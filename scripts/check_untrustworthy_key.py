"""Checks a question whose answer key cannot be trusted is not printed at all.

A Year 4 booklet shipped this, in the answer key, under a question about the
perimeter of four joined garden plots:

    Answer: 128 m
    The plots are side-by-side. If 4 plots of 12 m by 8 m are joined, the new
    rectangle is 48 m long and 8 m wide. The perimeter is 2 x (48 + 8) = 112 m.
    Alternatively, if they are joined along the 8 m side, the rectangle is
    12 m long and 32 m wide. The perimeter is 2 x (12 + 32) = 88 m.
    Assuming the standard arrangement of 48 m by 8 m: 112 m.
    Let us re-calculate for the most common interpretation of 'side-by-side'...

Three separate faults, and the third is the one that matters.

THE STATED ANSWER APPEARS NOWHERE. Every line of working says 112 or 88. The
answer says 128. `working_contradicts_answer` caught that.

THE MODEL IS THINKING OUT LOUD in the document a parent marks from.
`_SELF_CORRECTION` was supposed to catch that and did not: it held the phrase
"let me recalculate", and the booklet said "Let us re-calculate". Wrong
pronoun, and a hyphen. Neither is a fact about whether a model changed its
mind, and both are optional now. The auditor had its OWN copy of this rule,
which is precisely how the defect reached a customer: two detectors for one
rule drift, and the one that drifts is the one that ships. There is one now,
here, and the auditor imports it.

AND THE RESPONSE WAS TO WITHHOLD A TICK. That is the fault this file is named
after. The guard fired, correctly, and its entire effect was that the answer
printed without a check mark beside it. The question still went out. A parent
marking from that page marks a correct child wrong, and a missing tick does
not tell them which of the three numbers to believe, or even that there is a
disagreement to notice.

So it is a gate now, beside the other seven, and the question is dropped. One
question fewer is a rounding error against an answer key a parent cannot
trust.

WHAT MUST NOT BE DROPPED, and the second half of this file: working that
offers a second METHOD for the same answer. "Alternatively, count the squares
in each row" is good teaching and belongs in a key. What separates it from the
defect above is not the word, it is whether the answer is one the working
actually reaches.

Runs against a fake generator, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_untrustworthy_key.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from booklet_gen.agents.consistency import (                       # noqa: E402
    answer_is_trustworthy, answer_is_unprintable)
from booklet_gen.pipeline import BookletPipeline                  # noqa: E402
from booklet_gen.schemas import Question, QuestionSet             # noqa: E402

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


# Read off page 14 of the shipped booklet.
SHIPPED_WORKING = (
    "The plots are side-by-side. If 4 plots of 12 m by 8 m are joined, the "
    "new rectangle is 48 m long (4 x 12) and 8 m wide. The perimeter is "
    "2 x (48 + 8) = 2 x 56 = 112 m. Alternatively, if they are joined along "
    "the 8 m side, the rectangle is 12 m long and 32 m wide (4 x 8). The "
    "perimeter is 2 x (12 + 32) = 2 x 44 = 88 m. Assuming the standard "
    "arrangement of 48 m by 8 m: 48 + 48 + 8 + 8 = 112 m. Let us re-calculate "
    "for the most common interpretation of 'side-by-side': 48 m length, 8 m "
    "width.")


print("\n== the phrase that shipped is caught ==")

ok, why = answer_is_trustworthy("128 m", SHIPPED_WORKING)
check(not ok, f"the shipped key is refused ({why})",
      "this exact working went out to a customer with the answer 128 m above "
      "it and every line of it concluding 112 m")

# The pronoun and the hyphen, one at a time, because the shipped phrase got
# past the old pattern on both counts at once and fixing either alone would
# have looked like a fix.
for phrase in ("Let us re-calculate the perimeter.",
               "Let us recalculate the perimeter.",
               "Let me re-calculate the perimeter.",
               "Let me recalculate the perimeter.",
               "Re-checking the working, the answer is 5.",
               "Let us re-examine this.",
               "On second thought, the answer is 5.",
               "Actually, that gives 5."):
    ok, _ = answer_is_trustworthy("7", f"3 + 3 = 7. {phrase}")
    check(not ok, f"{phrase!r}",
          "the old pattern held only 'let me recalculate', so the pronoun and "
          "the hyphen each let a booklet through on their own")


print("\n== a noisy signal unticks; only a certain one drops ==")

# THE MOST IMPORTANT ASSERTIONS IN THIS FILE. The first version of the gate
# dropped on everything that cost a question its tick, and it emptied the
# practice set of every fraction subtopic in the test suite. An answer key is
# allowed to lose its tick on a guess; it is not allowed to lose the question.
UNTICK_BUT_KEEP = [
    ("3/16", "1 + 2 = 3",
     "a fraction whose working adds the numerators and never writes the "
     "denominator, because the denominator is in the question"),
    ("7222", "8 + 4 = 12 (write 2, carry 1). 2 + 9 + 1 = 12 (write 2, carry "
             "1). 5 + 6 + 1 = 12 (write 2, carry 1). 4 + 2 + 1 = 7.",
     "column addition, which builds its result one digit at a time and never "
     "states it"),
]
for answer, working, label in UNTICK_BUT_KEEP:
    bad, why = answer_is_unprintable(answer, working)
    check(not bad, f"kept: {label}",
          f"dropped as {why!r}. This is correct work. Escalating the "
          "contradiction signal from 'no tick' to 'no question' costs the "
          "customer most of a primary maths booklet")
    ok, _ = answer_is_trustworthy(answer, working)
    check(not ok,
          "        and it still loses its tick, so the doubt is not hidden",
          "the question is kept AND ticked, so the signal has been thrown "
          "away rather than downgraded")

# The certain one, both directions, so the split is asserted and not assumed.
bad, why = answer_is_unprintable("128 m", SHIPPED_WORKING)
check(bad, f"dropped: the shipped key ({why})",
      "the one case where there is nothing to lose: no correct answer key "
      "narrates a change of mind, so this must clear the higher bar")


print("\n== a second METHOD is not a second reading ==")

# Good teaching. If these are refused, the guard is costing the customer
# questions to fix a problem they do not have, which is the more expensive
# failure of the two.
KEEP = [
    ("12 cm2", "Count the squares: 4 rows of 3 is 12 cm2. Alternatively, "
     "multiply the two side lengths: 4 x 3 = 12 cm2.",
     "two routes to the same answer"),
    ("20", "5 x 4 = 20. Alternatively, add 5 four times: 5 + 5 + 5 + 5 = 20.",
     "repeated addition beside multiplication"),
    ("0.75", "3/4 means 3 divided by 4, which is 0.75. Another way to see it "
     "is that 3/4 is 75/100, and 75/100 is 0.75.",
     "an equivalence explained twice"),
]
for answer, working, label in KEEP:
    ok, why = answer_is_trustworthy(answer, working)
    check(ok, f"kept: {label}",
          f"refused as {why!r}. 'Alternatively' in a key is usually a second "
          "method, which belongs there. What marks the defect is an answer the "
          "working never reaches, not the word itself")


print("\n== and the pipeline DROPS it rather than unticking it ==")


class FakeGenerator:
    """Returns one sound question and one whose key contradicts itself."""

    def __init__(self):
        self.calls = 0

    def generate(self, subject, year_level, topic, subtopic, chunks=None,
                 teaching=None, **kw):
        self.calls += 1
        good = [Question(question=f"Calculate {n} x 4.", answer=str(n * 4),
                         working=f"{n} x 4 = {n * 4}.", difficulty="medium")
                for n in range(2, 9)]
        bad = Question(
            question="Four plots each 12 m by 8 m are joined in a row. What "
                     "is the perimeter?",
            answer="128 m", working=SHIPPED_WORKING, difficulty="hard")
        return QuestionSet(questions=[*good[:3], bad, *good[3:]])


def run_practice():
    from booklet_gen.pipeline import _SeenQuestions
    from booklet_gen.schemas import Subtopic
    pipe = BookletPipeline.__new__(BookletPipeline)
    pipe._n_classwork = 4
    pipe._n_homework = 3
    pipe._generator = FakeGenerator()
    pipe._validate_many = lambda s, y, qs, c=None, **k: [
        type("R", (), {"verified": True, "notes": ""})() for _ in qs]
    pipe._plan_question_visuals = lambda *a, **k: None
    pipe._resolve_visual = lambda q: (None, None)
    pipe._reasoning_reject = lambda s, q: False
    pipe._orphan_figure = lambda t, p: None
    pipe._absurd_quantity = lambda t: None
    pipe._impossible_constraints = lambda q: None
    pipe._self_answering = lambda q: None
    return BookletPipeline._generate_and_validate(
        pipe, "Mathematics", "Year 4", "Measurement",
        Subtopic(name="Perimeter", difficulty_hint="medium"), [],
        seen=_SeenQuestions())


kept = run_practice()
texts = [vq.question.question for vq in kept]
check(not any("Four plots" in t for t in texts),
      f"the contradicting question is gone from all {len(kept)} kept",
      f"it is still in the booklet: {[t[:40] for t in texts]}. Withholding "
      "its tick was the whole of the old response, and the question printed")
check(kept,
      f"and {len(kept)} sound question(s) survive beside it",
      "the gate took the whole set with it, which is worse than the defect")

# The backstop behind the gate, for the paths the gate does not run on. It has
# to keep working, not be replaced by it.
class _Q:
    answer = "128 m"
    working = SHIPPED_WORKING


check(BookletPipeline._trusted(_Q(), True) is False,
      "and _trusted still refuses the mark if one reaches it anyway",
      "the exam paper builds its marking key down a path the gates do not "
      "run on, so removing the backstop would uncover it")


print("\n== the auditor and the pipeline share one detector ==")

import audit_booklet                                              # noqa: E402

check(audit_booklet.DELIBERATION is not None,
      "the auditor has a deliberation pattern",
      "it could not import the pipeline's, so it is checking nothing")

# The drift itself, asserted. Not "do they both match this string", which two
# copies would also pass, but that the auditor's pattern is BUILT from the
# pipeline's.
src = (Path(__file__).resolve().parent / "audit_booklet.py").read_text(
    encoding="utf-8")
check("_SELF_CORRECTION" in src and "from booklet_gen.agents.consistency"
      in src,
      "and it is built from the pipeline's own, not written again",
      "the auditor holds a second copy of this rule. That is how the defect "
      "reached a customer: the pipeline's copy said 'let me recalculate', the "
      "booklet said 'Let us re-calculate', and only the auditor noticed")

for phrase in ("Let us re-calculate", "Alternatively", "On second thought"):
    check(bool(audit_booklet.DELIBERATION.search(f"3 + 3 = 7. {phrase}, 6.")),
          f"        the shared pattern sees {phrase!r}",
          "a phrase the pipeline drops on is one the auditor must be able to "
          "report on an already printed booklet")


print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
