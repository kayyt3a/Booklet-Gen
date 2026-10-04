"""Checks a subtopic taught in the lesson is also practised during the week.

A Year 6 booklet spent seventeen minutes of Class Work on "Angles on a
straight line, at a point and in a triangle", and set NO angle question for
homework. Not a short set: none at all. Its Homework held six questions
against a Year 6 band of twelve, and all six were on the other two subtopics.

TWO CAUSES, AND THE SECOND IS THE ONE THAT MADE IT A ZERO.

THE ASK HAD NO HEADROOM. A subtopic was generated with exactly
`n_classwork + n_homework` questions and the guards drop about one in three,
so the shortfall came straight out of the printed booklet. This is the same
fault the Final Challenge had and it is fixed the same way, and for the same
price: one subtopic is one generation call and one batched judge call however
many questions come back, so a spare costs output tokens and no round trip.

CLASS WORK TOOK ITS SHARE OFF THE FRONT. The split is `validated[:cut]` for
Class Work and `validated[cut:]` for Homework, so a set that came back at four
gave Class Work its four and Homework the remainder, which is nothing. The
subtraction never had an opinion about the second number reaching zero.

Class Work now hands one back rather than let that happen, down to a floor of
three, because "four questions under every mini-lesson" is a promise about the
lesson and "practised again during the week" is the promise the whole booklet
is built on. Recomputed through `_passage_safe_split` rather than decremented,
because the cut has to keep landing on a passage boundary: an English
comprehension question moved across it is a question about a reading that is
not on the page.

Runs against a fake generator, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_homework_every_topic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import pipeline as pipeline_module                # noqa: E402
from booklet_gen.pipeline import BookletPipeline, _SeenQuestions   # noqa: E402
from booklet_gen.schemas import (                                  # noqa: E402
    Passage, Question, QuestionSet, Subtopic)
from booklet_gen.timing import session_plan                        # noqa: E402

# Read rather than imported, so this file still runs against the pipeline as
# it shipped and reports the defect instead of an ImportError.
PRACTICE_HEADROOM = getattr(pipeline_module, "PRACTICE_HEADROOM", 0)
CLASSWORK_MIN = getattr(pipeline_module, "CLASSWORK_MIN_QUESTIONS", 0)

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


class FakeGenerator:
    """Returns questions, remembers the ask, and drops a set number of them.

    `survive` is how many of each batch get past the guards, which is the
    thing being simulated. The Year 6 booklet's angle subtopic had four
    survive out of eight asked.
    """

    def __init__(self, survive: int | None = None, passages: int = 0):
        self.asks: list[int] = []
        self.survive = survive
        self.passages = passages

    def generate(self, subject, year_level, topic, subtopic, chunks=None,
                 teaching=None, classwork_count=None, passage_quota=None,
                 count=None, **kw):
        self.asks.append(count)
        kept = count if self.survive is None else min(self.survive, count)
        questions, passage_list = [], []
        for i in range(count):
            # Everything past `survive` is marked for the planted guard below.
            doomed = i >= kept
            pid = None
            if self.passages and i < self.passages * 2:
                pid = f"p{i // 2}"
            questions.append(Question(
                question=("DROPME " if doomed else "") + f"Question {i} on "
                         f"{subtopic.name}?",
                answer=str(i), working=f"The answer is {i}.",
                difficulty="medium", passage_id=pid))
        for k in range(self.passages):
            passage_list.append(Passage(id=f"p{k}", title=f"Reading {k}",
                                        text="A short reading passage."))
        return QuestionSet(questions=questions, passages=passage_list)


def build(survive=None, year="Year 6", passages=0):
    """Run the real subtopic path against a fake model and a planted guard."""
    plan = session_plan(year)
    pipe = BookletPipeline.__new__(BookletPipeline)
    pipe._n_classwork = 4
    pipe._n_homework = plan.homework_per_subtopic
    generator = FakeGenerator(survive, passages)
    pipe._generator = generator
    pipe._validate_many = lambda s, y, qs, c=None, **k: [
        type("R", (), {"verified": True, "notes": ""})() for _ in qs]
    pipe._plan_question_visuals = lambda *a, **k: None
    pipe._resolve_visual = lambda q: (None, None)
    pipe._reasoning_reject = lambda s, q: False
    pipe._orphan_figure = lambda t, p: None
    pipe._absurd_quantity = lambda t: None
    pipe._impossible_constraints = lambda q: None
    pipe._untrustworthy_key = lambda q: None
    pipe._self_answering = lambda q: ("planted" if "DROPME" in q.question
                                      else None)
    # THE REAL METHOD, not a copy of what it does. The first version of this
    # file re-implemented the Class Work / Homework split here and then
    # asserted against its own copy, so it passed whatever the pipeline did:
    # 21 of 22 green against the very code that shipped the defect. A check
    # that re-implements the thing it is checking has measured nothing.
    pipe._retrieve = lambda *a, **k: []
    pipe._intro = None
    pipe._write_teaching = lambda *a, **k: None
    pipe._drop_orphan_examples = lambda teaching, name: teaching
    section, _ = BookletPipeline._process_subtopic(
        pipe, "Mathematics", year, "Measurement",
        Subtopic(name="Angles on a straight line", difficulty_hint="medium"),
        seen=_SeenQuestions(), use_rag=False)
    return {"asked": generator.asks[0] if generator.asks else 0,
            "classwork": section.questions,
            "homework": section.homework_questions}


BAND = session_plan("Year 6")


print("\n== the ask carries headroom over what prints ==")

got = build()
check(got["asked"] > 4 + BAND.homework_per_subtopic,
      f"Year 6 prints 4 + {BAND.homework_per_subtopic} and asks for "
      f"{got['asked']}",
      f"asked for {got['asked']}. Asking for exactly what prints means every "
      "guard that fires comes off the printed count, and the guards drop "
      "about one question in three")
check(got["asked"] == 4 + BAND.homework_per_subtopic + PRACTICE_HEADROOM,
      f"the spare is the declared headroom of {PRACTICE_HEADROOM}",
      f"asked for {got['asked']}")


print("\n== and the spares do not lengthen the booklet ==")

check(len(got["classwork"]) == 4,
      f"nothing dropped: Class Work is {len(got['classwork'])}, its band",
      f"printed {len(got['classwork'])}. The headroom is insurance, not a "
      "longer lesson")
check(len(got["homework"]) == BAND.homework_per_subtopic,
      f"and Homework is {len(got['homework'])}, its band",
      f"printed {len(got['homework'])} against a band of "
      f"{BAND.homework_per_subtopic}")


print("\n== the shipped failure, replayed ==")

# Four survived out of the eight the old code asked for. Class Work took four
# and Homework got nothing.
short = build(survive=4)
check(short["homework"],
      f"four survive: Homework still gets {len(short['homework'])} question(s)",
      "Homework got nothing, which is the shipped defect: the child works "
      "through the subtopic in the lesson and never sees it again")
check(len(short["classwork"]) >= CLASSWORK_MIN,
      f"and Class Work keeps {len(short['classwork'])}, at or above its floor "
      f"of {CLASSWORK_MIN}",
      f"Class Work fell to {len(short['classwork'])}. Handing one back to "
      "Homework must not empty the lesson to do it")


print("\n== Class Work is not raided when it does not have to be ==")

for survive, label in ((8, "a full set"), (7, "one short"), (6, "two short")):
    plenty = build(survive=survive)
    check(len(plenty["classwork"]) == 4,
          f"{label}: Class Work keeps its full {len(plenty['classwork'])}",
          f"Class Work fell to {len(plenty['classwork'])} with "
          f"{survive} surviving. The yield is for the case where Homework "
          "would otherwise get nothing, and nothing else")
    check(plenty["homework"],
          f"        and Homework gets {len(plenty['homework'])}",
          "Homework is empty with questions to spare")


print("\n== a set too small to share is left alone ==")

# Below the Class Work floor there is nothing to hand back, and taking from a
# lesson that is already short would trade one defect for a worse one.
tiny = build(survive=2)
check(len(tiny["classwork"]) == 2 and not tiny["homework"],
      f"two survive: Class Work keeps both, Homework gets none",
      f"Class Work {len(tiny['classwork'])}, Homework "
      f"{len(tiny['homework'])}. Two questions is already under the floor, "
      "and splitting them leaves a mini-lesson with one question under it")


print("\n== the cut still lands on a passage boundary ==")

# English only. A comprehension question moved across the cut is a question
# about a reading that is not on the page, which is why the yield recomputes
# the split rather than subtracting one from it.
essay = build(survive=5, passages=3)
homework_pids = {vq.question.passage_id for vq in essay["homework"]
                 if vq.question.passage_id}
classwork_pids = {vq.question.passage_id for vq in essay["classwork"]
                  if vq.question.passage_id}
check(not (homework_pids & classwork_pids),
      f"no passage is split across the cut (Class Work {sorted(classwork_pids)}, "
      f"Homework {sorted(homework_pids)})",
      "a passage's questions are on both sides of the Class Work boundary, so "
      "one half asks about a reading printed in the other half of the booklet")


print("\n== it holds across the year bands ==")

for year in ("Year 1", "Year 3", "Year 6", "Year 9"):
    plan = session_plan(year)
    full = build(year=year)
    check(full["asked"] == 4 + plan.homework_per_subtopic + PRACTICE_HEADROOM,
          f"{year}: asks for {full['asked']}",
          f"asked {full['asked']} against a band of 4 + "
          f"{plan.homework_per_subtopic}")
    starved = build(survive=4, year=year)
    check(starved["homework"],
          f"        and a short set still leaves "
          f"{len(starved['homework'])} for the week",
          "Homework is empty at this year level")


print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
