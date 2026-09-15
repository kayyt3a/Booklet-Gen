"""Checks that a one-word answer is not given five centimetres of squared paper.

Page 13 of a shipped Year 4 booklet held one question, a figure, a 5cm working
grid, and then 60% of the page empty:

    1. A set square has three angles. One is a right angle. The other two are
       smaller than a right angle. How many acute angles does the set square
       have?
                                  [ 5cm of squared paper ]
       Answer: ______

The answer is "2". The grid was sized from the question's difficulty tag and
its number of parts, and never from what the child actually writes, so every
choose-one question in the booklet was given the same allowance as a long
multiplication. Two costs, and the second is the expensive one:

  * a grid that size beside a one-word answer reads as a rendering fault, which
    is the loudest thing on a page saying nobody designed it
  * the wasted height pushed the rest of the subtopic onto a page of its own,
    and that page then printed nearly empty

ANSWER LENGTH IS THE WRONG SIGNAL, and it was the first thing tried.
"Calculate 573 x 46" answers in five digits and needs every square it is given.
What separates the two is whether the child COMPUTES or DECIDES. So the match
is on the question's task, confirmed against an answer short enough to prove
it, and the second half of this file is the questions that must keep their
grid.

    PYTHONPATH=. python scripts/check_working_space_fit.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import formatter as fmt                          # noqa: E402
from booklet_gen.schemas import Question                          # noqa: E402

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


def panel(question: str, answer: str, difficulty: str = "medium",
          floor: float = 0.0) -> float:
    return fmt._working_space_cm(
        Question(question=question, answer=answer, working="",
                 difficulty=difficulty), floor)


print("\n== a question answered by deciding gets a line, not a grid ==")

# Every one of these is from the shipped Year 4 booklet.
DECIDES = [
    ("Use a square corner to check the angle shown below. Is it acute or "
     "obtuse?", "acute", "the angle question, twice on one page"),
    ("Check the angle below with a square corner. Is it acute or obtuse?",
     "obtuse", "its near-duplicate"),
    ("Compare angle A and angle B using a square corner. Which angle is "
     "obtuse?", "B", "choosing between two figures"),
    ("A set square has three angles. One is a right angle. The other two are "
     "smaller than a right angle. How many acute angles does the set square "
     "have?", "2", "the page 13 question, answer 2"),
    ("Name the shape with five sides.", "pentagon", "naming a shape"),
    ("Is this solid a cube or a cylinder?", "cube", "choosing a solid"),
]
for question, answer, label in DECIDES:
    cm = panel(question, answer)
    check(cm <= fmt._PANEL_MIN_CM + 1e-6, f"{cm:.2f}cm: {label}",
          f"got {cm:.2f}cm for an answer of {answer!r}. That much blank grid "
          "beside a one-word answer is what made a shipped page print 60% "
          "empty")

print("\n== and a question answered by working keeps every square ==")

COMPUTES = [
    ("Calculate 573 × 46.", "26358", "hard", "long multiplication"),
    ("Calculate 4,528 + 2,694.", "7222", "medium", "column addition"),
    ("Convert the fraction 4/5 into a decimal.", "0.8", "medium",
     "a conversion with steps"),
    ("A paddock is 12 m long and 8 m wide. What is its perimeter?", "40 m",
     "medium", "a perimeter word problem"),
    ("What is the value of the digit 5 in the number 5241?", "5000", "easy",
     "place value"),
    ("Which is larger, 3/4 or 0.7? Show your working.", "3/4", "hard",
     "a comparison that asks for working"),
    ("How many acute angles are there if each of the 4 corners measures 45"
     " degrees and you must first halve each one?", "8", "hard",
     "an angle count that still needs arithmetic"),
]
for question, answer, difficulty, label in COMPUTES:
    cm = panel(question, answer, difficulty)
    check(cm > fmt._PANEL_MIN_CM + 1e-6, f"{cm:.2f}cm: {label}",
          f"got {cm:.2f}cm. This question needs written working, and a child "
          "given one line for it meets the same question with less room than "
          "the method takes")

print("\n== the shrink cannot override a section's own floor ==")

# The Warm-up Recap is written entirely in easy questions and sets its own
# minimum, because a recap question is arithmetic whatever its wording. A
# shrink that ignored that floor would undo the fix that exists for it.
cm = panel("Is it acute or obtuse?", "acute", "easy",
           floor=fmt._RECAP_MIN_SPACE_CM)
check(abs(cm - fmt._RECAP_MIN_SPACE_CM) < 1e-6,
      f"{cm:.2f}cm: a warm-up question keeps the recap floor",
      f"got {cm:.2f}cm against a floor of {fmt._RECAP_MIN_SPACE_CM}cm. The "
      "recap floor exists because a child got one line to work '15 x 4 + 7' "
      "in, and this must not bring that back")

print("\n== a prose answer is still sized by its ruled lines ==")

written = Question(
    question="Explain how you know the angle is obtuse. Use two sentences.",
    answer="Because it opens wider than a square corner.", working="",
    difficulty="medium")
rules = fmt.written_response_rules(written)
cm = fmt._working_space_cm(written)
check(rules > 0 and cm > fmt._PANEL_MIN_CM + 1e-6,
      f"{cm:.2f}cm across {rules} ruled line(s)",
      "an explain-your-answer question was shrunk to one line, so the child "
      "is asked for two sentences and given room for none")

print("\n== ambiguous wording is left alone rather than guessed at ==")

# The match has to be narrow. These read like choices but are not answerable
# in a word, and shrinking them would be worse than leaving them generous.
for question, answer, label in (
    ("Which of these numbers is larger, and by how much: 3,402 or 3,240?",
     "3,402 by 162", "a choice that also needs a subtraction"),
    ("Is 0.7 or 3/4 closer to 1? Show the working that proves it.",
     "3/4, because 1 - 3/4 = 0.25", "a choice needing proof"),
):
    cm = panel(question, answer, "hard")
    check(cm > fmt._PANEL_MIN_CM + 1e-6, f"{cm:.2f}cm: {label}",
          f"got {cm:.2f}cm. The match is too loose: it is shrinking questions "
          "whose answer only looks like a choice")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
