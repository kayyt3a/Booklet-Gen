"""Checks the guard on the one question shape that can have no answer at all.

The booklet now asks for a question per set that names no method: two facts are
given, and the student has to work out what to do with them.

    A rectangular paddock is to have an area of 420 m2, and there is 82 m of
    fencing to go round it. What size should it be?

Nothing in that says multiply, divide or factorise. It is the shape a real
paper is built from and the shape a worksheet generator never produces, because
every other shape names its own method in the first six words.

It also carries a failure the plainer shapes cannot. The two conditions have to
agree with each other, and whether they do is a fact about a quadratic rather
than about the story: the sides add to P/2 and multiply to A, so a rectangle
exists only when (P/2)^2 >= 4A. 420 and 82 give 20 by 21. 420 and 78 give
nothing whatsoever, and a child asked that question keeps trying, because a
question with no answer reads exactly like a hard one. It is the student who
perseveres who loses the most time to it.

Nothing already in the pipeline catches it. The arithmetic is sound, the units
are sound, the quantities are all plausible, and the judge would have to solve
the question itself to notice, which is the step a grader skips. So the check
is deterministic and sits with the other guards on model output.

The other half of a guard like this is what it must NOT do. Dropping questions
is expensive: the set is short, the answer key is written against the wording,
and a guard that fires on a sound question is worse than no guard. So most of
what follows is cases it has to leave alone.

    PYTHONPATH=. python scripts/check_application_questions.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents.consistency import (                      # noqa: E402
    impossible_shape_constraints,
)

PASSED = 0
TOTAL = 0
ROOT = Path(__file__).resolve().parent.parent


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


PADDOCK = ("A rectangular paddock is to have an area of {area} m2, and there "
           "is {fence} m of fencing to go round it. What size should it be?")

print("\n== a question whose two conditions cannot both hold is caught ==")

IMPOSSIBLE = [
    (PADDOCK.format(area=420, fence=78), "20 m by 19 m",
     "420 m2 inside 78 m of fence"),
    (PADDOCK.format(area=100, fence=38), "10 m by 9 m",
     "100 m2 inside 38 m of fence"),
    ("A rectangular pen has an area of 60 m2 and a perimeter of 28 m. "
     "What are its dimensions?", "6 m by 10 m",
     "60 m2 with a 28 m perimeter"),
    ("A rectangular courtyard has an area of 250 cm2 and 60 cm of edging "
     "around it. What are the side lengths?", "10 cm by 25 cm",
     "the same fault stated in centimetres"),
]
for question, answer, label in IMPOSSIBLE:
    reason = impossible_shape_constraints(question, answer)
    check(bool(reason), f"caught: {label}",
          "there is no rectangle with those two measurements, so this question "
          "ships and a student works at it until they give up")

reason = impossible_shape_constraints(PADDOCK.format(area=420, fence=78),
                                      "20 m by 19 m")
check(reason is not None and "add to 39" in reason and "420" in reason,
      "the reason names the sum and product that have no solution",
      f"got {reason!r}: the log line has to say what is wrong, or nobody "
      "reading it can tell a real fault from a guard misfiring")

print("\n== an answer that misses one of the two conditions is caught ==")

WRONG_ANSWERS = [
    ("15 m by 28 m", "right area, wrong perimeter"),
    ("10 m by 42 m", "right area, wrong perimeter again"),
    ("20 m by 20 m", "right perimeter is not even close, and the area is 400"),
    ("41 m by 1 m", "adds to 42, so neither condition holds"),
]
solvable = ("A rectangular pen has an area of 420 m2 and a perimeter of 82 m. "
            "What are its dimensions?")
for answer, label in WRONG_ANSWERS:
    check(bool(impossible_shape_constraints(solvable, answer)),
          f"caught: {label}",
          f"the key prints {answer} for a question whose real answer is "
          "20 m by 21 m, so a parent marking the page marks a correct child "
          "wrong")

print("\n== the question that works is left alone ==")

for question, answer, label in [
    (PADDOCK.format(area=420, fence=82), "20 m by 21 m",
     "the paddock question as it is meant to be written"),
    (solvable, "21 m by 20 m", "the same answer with the sides the other way"),
    (solvable, "The paddock should be 20 m by 21 m.",
     "an answer written as a sentence"),
    ("A rectangular sandpit has an area of 24 m2 and 20 m of edging. What are "
     "the side lengths?", "4 m by 6 m", "smaller numbers, same shape"),
]:
    check(impossible_shape_constraints(question, answer) is None,
          f"left alone: {label}",
          "a sound question is being dropped, which costs the set an item and "
          "is worse than having no guard")

print("\n== and so is everything it cannot read with certainty ==")

LEAVE_ALONE = [
    ("A farmer has 82 m of fencing. What is the largest rectangular area he "
     "can enclose?", "420.25 m2",
     "an optimisation question: the area is the answer, not a condition"),
    ("He has 60 m of fencing and a rectangular plot of 420 m2 to go round. "
     "How much more fencing does he need?", "22 m",
     "the same two numbers, where the fence is NOT the perimeter"),
    ("A rectangular court has an area of 420 m2. How long is it if it is 20 m "
     "wide?", "21 m", "only one condition given"),
    ("A rectangular room has an area of 12 m2 and 1400 cm of skirting board. "
     "What are its dimensions?", "3 m by 4 m",
     "an area in square metres beside a length in centimetres"),
    ("A paddock has an area of 420 m2 and 78 m of fencing. What size is it?",
     "20 m by 19 m", "no rectangle named, so the shape is not known"),
    ("Calculate 512 x 24.", "12288", "a plain computation"),
    ("", "", "empty text"),
]
for question, answer, label in LEAVE_ALONE:
    check(impossible_shape_constraints(question, answer) is None,
          f"left alone: {label}",
          "the guard fired on something it cannot actually judge, so it is now "
          "deleting questions on a guess")

print("\n== the pipeline drops what the guard catches ==")

from booklet_gen.pipeline import BookletPipeline                  # noqa: E402
from booklet_gen.schemas import Question                          # noqa: E402

broken = Question(question=PADDOCK.format(area=420, fence=78),
                  answer="20 m by 19 m", working="")
sound = Question(question=PADDOCK.format(area=420, fence=82),
                 answer="20 m by 21 m", working="")
check(bool(BookletPipeline._impossible_constraints(broken)),
      "the pipeline recognises the impossible question",
      "the guard exists but the pipeline never asks it anything")
check(BookletPipeline._impossible_constraints(sound) is None,
      "and keeps the one that works",
      "the pipeline is dropping the very question this shape was added for")

source = (ROOT / "booklet_gen" / "pipeline.py").read_text(encoding="utf-8")
check(source.count("self._impossible_constraints(q)") == 3,
      "all three question-selection paths ask it: practice, recap, challenge",
      f"found {source.count('self._impossible_constraints(q)')} of 3. A path "
      "that skips the guard still ships the question, and the Final Challenge "
      "is where the hardest application questions are meant to live")

print("\n== the prompts ask for the shape in the first place ==")

# The guard only matters if these questions get written. This half is asserted
# as present rather than measured on a page: what a model does with an
# instruction cannot be checked without generating a booklet, and that costs an
# API call per run. The half that IS deterministic is above.
prompts = ROOT / "booklet_gen" / "prompts"
for name in ("question_generator_maths.txt", "challenge_generator_maths.txt"):
    text = (prompts / name).read_text(encoding="utf-8")
    check("WORK OUT WHAT TO DO" in text or "NAMES NO METHOD" in text,
          f"{name} asks for a question that names no method",
          "the guard is protecting a question shape nothing ever produces")
    check("BACKWARDS" in text.upper() and "78" in text,
          f"{name} tells it to build the question from the answer",
          "without this the model writes the givens first and hopes, which is "
          "the exact habit that produces an unanswerable question")

print("\n== house style ==")

for name in ("question_generator_maths.txt", "challenge_generator_maths.txt"):
    text = (prompts / name).read_text(encoding="utf-8")
    check("—" not in text, f"no em dash in {name}",
          "an em dash in a prompt is copied into the booklet, and the "
          "formatter's stripper is a backstop rather than a licence")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
