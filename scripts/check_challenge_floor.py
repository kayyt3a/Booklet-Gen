"""Checks the Final Challenge is worth the page it is printed on.

A Year 3 booklet shipped with this as its entire Final Challenge, on a page of
its own, under a heading promising "questions that mix everything together":

    1. The mark shows 3/4 on the number line. What fraction with a denominator
       of 8 is at this same point?

    ...
    Final Challenge        ______ / 1

The year band sets four. Nothing malfunctioned: the model was asked for four,
the seven guards on a challenge question ran, three were dropped for reasons
each of them was right about, and one printed. Every guard in the pipeline can
say no, and none of them can say "that is not enough".

THREE THINGS WERE MISSING, and the cheap one comes first.

ASK FOR MORE THAN PRINTS. The Final Challenge is one call for the whole
booklet and its spares are graded inside the same batched judge call, so
headroom costs output tokens and no round trip. It is also the smallest set in
the booklet and the likeliest to be dropped from, because its questions are
cumulative, multi-step and the longest thing in it, which is most of what the
guards look for. Asking for exactly what prints was the mistake.

TOP UP ONCE. If the headroom is not enough, a second call is a small fraction
of what the booklet already cost.

THEN STOP. A section that still cannot fill itself is not printed. The
formatter already drops the heading, the contents line, the timing and the
score row when the list is empty, so the booklet reads as finished rather than
as faulty. Losing a section costs less than looking broken.

Runs against a fake generator, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_challenge_floor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import pipeline as pipeline_module                   # noqa: E402
from booklet_gen.pipeline import BookletPipeline                      # noqa: E402
from booklet_gen.schemas import Question, QuestionSet                 # noqa: E402
from booklet_gen.timing import session_plan                           # noqa: E402

# Read rather than imported, so this file still RUNS against the pipeline as
# it shipped and reports the defect instead of an ImportError. A check that
# cannot execute against the old behaviour has not measured anything about the
# new one. Zero is the honest reading of a pipeline with no headroom in it.
CHALLENGE_HEADROOM = getattr(pipeline_module, "CHALLENGE_HEADROOM", 0)

PASSED = 0
TOTAL = 0


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


COVERED = [("Number and Place Value", "Multiplication facts and division"),
           ("Measurement and Geometry", "Perimeter and area"),
           ("Statistics and Probability", "Column graphs")]


class FakeChallenger:
    """Returns as many questions as it is asked for, and remembers each ask.

    `reject_per_batch` is how many of each batch the guards will throw out,
    which is the thing being simulated: the shipped booklet lost three of its
    four, and the question is what the pipeline does about that rather than
    whether the guards were right.
    """

    def __init__(self, reject_per_batch: int = 0):
        self.asks: list[int] = []
        self.reject_per_batch = reject_per_batch
        self._serial = 0

    def generate(self, subject, year_level, covered, n_questions, chunks=None):
        self.asks.append(n_questions)
        out = []
        for i in range(n_questions):
            self._serial += 1
            doomed = i < self.reject_per_batch
            out.append(Question(
                question=("DROPME " if doomed else "")
                         + f"Question {self._serial} about {subject}.",
                answer=str(self._serial), working="Working.",
                difficulty="hard"))
        return QuestionSet(questions=out)


def build(reject_per_batch=0, year="Year 3", alone=True):
    """Run the real _build_challenge against a fake model and fake guards."""
    pipe = BookletPipeline.__new__(BookletPipeline)
    pipe._n_challenge = 5           # the constructor default; the band binds
    challenger = FakeChallenger(reject_per_batch)
    pipe._challenger = challenger
    pipe._validate_many = lambda subject, yl, qs, chunks=None, **k: [
        type("R", (), {"verified": True, "notes": ""})() for _ in qs]
    pipe._plan_question_visuals = lambda *a, **k: None
    pipe._resolve_visual = lambda q: (None, None)
    pipe._reasoning_reject = lambda subject, q: False
    pipe._orphan_figure = lambda text, path: None
    pipe._absurd_quantity = lambda text: None
    pipe._impossible_constraints = lambda q: None
    pipe._trusted = lambda q, verified: verified
    # The one guard under our control. Standing in for all seven: what matters
    # is that a question was refused, not which gate refused it.
    pipe._self_answering = lambda q: ("planted" if "DROPME" in q.question
                                      else None)
    try:
        got = pipe._build_challenge("Mathematics", year, COVERED, None, None,
                                    alone=alone)
    except TypeError:
        # The pipeline as it shipped had no opinion about merging, so there is
        # no argument to pass it. Same reason as the headroom above: this file
        # has to be able to run against the old behaviour to be evidence about
        # the new one.
        got = pipe._build_challenge("Mathematics", year, COVERED, None, None)
    return got, challenger


BAND = session_plan("Year 3").challenge_questions


print("\n== the generator is asked for more than the booklet prints ==")

got, challenger = build(reject_per_batch=0)
check(challenger.asks and challenger.asks[0] > BAND,
      f"Year 3 prints {BAND} and asks for {challenger.asks[0]}",
      f"asked for {challenger.asks}. Asking for exactly what prints means "
      "every guard that fires comes straight off the printed count, which is "
      "how a four question section shipped holding one")
check(challenger.asks[0] == BAND + CHALLENGE_HEADROOM,
      f"the spare is the declared headroom of {CHALLENGE_HEADROOM}",
      f"asked for {challenger.asks[0]}, expected {BAND + CHALLENGE_HEADROOM}")


print("\n== and the spares are not printed when they are not needed ==")

check(len(got) == BAND,
      f"nothing rejected: exactly {BAND} print, not {challenger.asks[0]}",
      f"printed {len(got)}. The headroom is insurance, not a longer section: "
      "the year band sized this part and printing the spares lengthens a "
      "booklet that was already measured")
check(len(challenger.asks) == 1,
      "and the model is called once",
      f"called {len(challenger.asks)} times with nothing to recover from, "
      "which is a second call on every booklet in the product")


print("\n== the shipped failure, replayed ==")

# Three of four dropped is what happened. With headroom the same guard rate
# leaves a section rather than a stub.
got, challenger = build(reject_per_batch=3)
floor = (BAND + 1) // 2
check(len(got) >= floor,
      f"three dropped per batch: {len(got)} questions print, floor is {floor}",
      f"printed {len(got)}. This is the shipped defect: the guards were right "
      "and the section was still not worth its page")
check(len(got) != 1,
      "and never the single question that shipped",
      "one question under a heading promising that everything is mixed "
      "together, with a score box reading / 1, is the most visible fault in "
      "the booklet")


print("\n== a section that falls short is topped up once ==")

# Five of each batch rejected. The first pass cannot reach the floor, so a
# second call has to happen, and one is enough here.
got, challenger = build(reject_per_batch=5)
check(len(challenger.asks) == 2,
      f"short after the first pass: called again ({challenger.asks})",
      f"called {len(challenger.asks)} time(s). The booklet is already paid "
      "for and one more call is a small fraction of what it cost, so giving "
      "up without trying spends the customer's money to save ours")
check(len(got) >= floor,
      f"and the top-up gets it to {len(got)}",
      f"still {len(got)} after a second call")
check(len(set(q.question.question for q in got)) == len(got),
      "with no question repeated between the two passes",
      f"{[q.question.question for q in got]}: the same question twice in a "
      "four question capstone is worse than three questions")


print("\n== a section that still cannot fill itself is not printed ==")

# Every question rejected, both times.
got, challenger = build(reject_per_batch=99)
check(got == [],
      "nothing survives either pass: the section is dropped, not stubbed",
      f"returned {len(got)} question(s). The formatter omits the heading, the "
      "contents line, the timing and the score row when this list is empty, "
      "so an absent section reads as finished and a stub reads as broken")
check(len(challenger.asks) == 2,
      "and it gave up after two passes, not more",
      f"made {len(challenger.asks)} calls. A model that has failed twice on "
      "the same request is not going to succeed on the fifth, and the "
      "customer is waiting for a booklet the whole time")


print("\n== a merged section is judged as the section, not as its halves ==")

# A NAPLAN booklet runs this once for numeracy and once for literacy and
# merges the two into one Final Challenge. A short half is not a short
# section, and throwing it away costs the customer questions to fix an
# appearance the merge has already fixed.
got, challenger = build(reject_per_batch=5, alone=False)
check(got,
      f"merging: {len(got)} question(s) are handed back rather than dropped",
      "the numeracy half was discarded for being short on its own, so a "
      "NAPLAN Final Challenge loses half its questions and nothing says why")


print("\n== the floor tracks the year band ==")

for year in ("Year 1", "Year 3", "Year 6", "Year 9"):
    band = session_plan(year).challenge_questions
    got, challenger = build(reject_per_batch=0, year=year)
    check(len(got) == band,
          f"{year}: prints its band of {band}",
          f"printed {len(got)} against a band of {band}")
    check(challenger.asks[0] == band + CHALLENGE_HEADROOM,
          f"        and asks for {band + CHALLENGE_HEADROOM}",
          f"asked for {challenger.asks[0]}")


print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
