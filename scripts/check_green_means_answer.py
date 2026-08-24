"""Checks the booklet's green is spent on one thing: the answer.

Colour that carries meaning can carry one meaning. #146B2C was carrying three.
It was the ink on "Answer: 60 cm3" in a worked example, the ink on the tick
that says an answer was verified, and the ink on the "(about 16 min)" estimate
printed beside every subtopic heading. On one page a parent could see a green
time badge and a green "Answer: 73" within a few centimetres, with nothing in
common between them, which is how a colour stops being a signal and becomes
decoration.

The estimate is metadata. It moved to the grey the booklet already uses for
metadata, the same ink as the "TOPIC 1 OF 2" locator that sits an inch above it
on the same page. Green now says one thing, and this file measures what every
green mark in a rendered booklet actually says.

The green Marking Key band on an exam paper is deliberately left alone and
asserted below: it opens the answers, so it is the same meaning at the scale of
a part.

    PYTHONPATH=. python scripts/check_green_means_answer.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf

from booklet_gen.formatter import ANSWER_GREEN, META_GREY, render_pdf
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)

_passed = 0
_failed: list[str] = []


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg):
    _failed.append(msg)
    print("  FAIL:", msg)


def check(cond, good, consequence):
    if cond:
        ok(good)
    else:
        bad(consequence)
    return cond


def vq(text, answer="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=answer,
                          difficulty=difficulty), verified=True)


def section(topic, subtopic, marker):
    return SubtopicOutput(
        topic=topic, subtopic=subtopic,
        teaching=SubtopicTeaching(
            intro_paragraphs=["Volume is the space inside a solid."],
            key_points=["Length times width times height."],
            worked_example=WorkedExample(
                question=f"The {marker} box is 5 cm by 3 cm by 4 cm. What is "
                         "its volume?",
                steps=["Multiply the length by the width: 5 x 3 = 15."],
                answer="60 cubic centimetres"),
            guided_examples=[WorkedExample(
                question="Find the volume of a prism 2 cm by 2 cm by 3 cm.",
                steps=["Multiply the length and width: [[4]]."],
                answer="[[12]] cubic cm")]),
        questions=[vq(f"{marker} {j}: a box is {j + 2} cm by 2 cm by 3 cm. "
                      "What is its volume?", answer=str((j + 2) * 6))
                   for j in range(3)],
        homework_questions=[vq(f"{marker} homework {j}: calculate "
                               f"{j + 1}/12 + {j + 1}/12.", difficulty="easy")
                            for j in range(3)],
        estimated_minutes=16)


data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    program_label="Academic Accelerate",
    sections=[section("Volume", "Volume of a prism", "Numbat"),
              section("Fractions", "Comparing fractions", "Rosella")],
    recap_questions=[vq("Calculate 15 * 4 + 7.", answer="67",
                        difficulty="easy")],
    challenge_questions=[vq("A pool is 10 m by 5 m by 1.5 m. What is its "
                            "volume?", answer="75", difficulty="hard")],
    recap_minutes=6, classwork_minutes=40, homework_minutes=20,
    challenge_minutes=12, total_minutes=78)

out = Path(tempfile.mkdtemp(prefix="folio-green-")) / "green.pdf"
render_pdf(data, out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
GREEN = int(ANSWER_GREEN[1:], 16)
GREY = int(META_GREY[1:], 16)


def spans():
    for i, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        yield i, span, text


print("\nEVERY GREEN MARK ON THE PAGE IS AN ANSWER OR A VERIFICATION")

# The running head's right-hand slot on a key page is the third thing allowed
# to be green, and for the reason the exam paper's Marking Key band already is
# (see the note at the top of this file): it says "you are in the answers",
# which is the same meaning at the scale of a part rather than a fourth use of
# the colour. It is recognised by where it sits, not only by what it says: the
# head band above the type area. The word "Answers" anywhere in the body is
# still a stray.
HEAD_BAND = 55
green = [(i, s, t) for i, s, t in spans() if s["color"] == GREEN]
strays = [(f"page {i + 1}", t[:40]) for i, s, t in green
          if not (t.startswith("Answer:") or t in {"✓", "✔"}
                  or (t == "ANSWERS" and s["bbox"][3] < HEAD_BAND))]

check(green, "the booklet prints green marks at all",
      "nothing at all is printed in the answer green, so this file is "
      "measuring an empty set and would pass whatever happened to the palette")

check(not strays,
      f"all {len(green)} of them are an answer line or a verification tick",
      f"these green marks are neither an answer nor a verification: {strays}. "
      "A colour that means three things means none of them, and a green time "
      "estimate a few centimetres from a green answer is the page where a "
      "reader stops trusting the signal")

check(any(t.startswith("Answer:") for _, _, t in green)
      and any(t in {"✓", "✔"} for _, _, t in green),
      "and both of the things green is for are actually on the page: an "
      "answer line and a verification tick",
      "the booklet printed green marks of only one kind, so the assertion "
      "above is weaker than it looks")

print("\nTHE TIME ESTIMATE IS SET IN THE INK THE BOOKLET USES FOR METADATA")

badges = [(i, s, t) for i, s, t in spans() if t.startswith("(about ")
          and t.endswith("min)")]
check(badges,
      f"{len(badges)} subtopic time estimates printed",
      "no time estimate printed at all, so nothing below is measured")

wrong = [(f"page {i + 1}", t, f"#{s['color']:06X}") for i, s, t in badges
         if s["color"] != GREY]
check(not wrong,
      f"every one of them is set in {META_GREY}",
      f"these time estimates are not in the metadata grey: {wrong}. The "
      "estimate is a statement about the work, not part of it")

kickers = {s["color"] for _, s, t in spans() if t.startswith("TOPIC ")
           and " OF " in t}
check(kickers == {GREY},
      f"which is the same ink as the TOPIC locator on the same page "
      f"({META_GREY}), so the booklet has one metadata grey and not two",
      f"the TOPIC locator is set in {[f'#{c:06X}' for c in kickers]} against "
      f"the estimate's {META_GREY}. Two greys for two pieces of metadata on "
      "one page is the same mistake one step quieter")

print("\nAND THE EXAM PAPER'S MARKING KEY BAND KEEPS THE GREEN ON PURPOSE")

# The band that opens the marking key is the same meaning at the scale of a
# part: everything under it is answers. It is named here so that a later reader
# of this file knows it was considered and kept, rather than missed.
check(ANSWER_GREEN == "#146B2C",
      f"green is still {ANSWER_GREEN}, the ink the Marking Key band is drawn "
      "in on an exam paper",
      "the answer green moved. The exam paper's Marking Key band is drawn in "
      "it too, and scripts/check_part_colours_and_key_steps.py pins that band "
      "by its literal hex")

doc.close()

if _failed:
    print(f"\n{len(_failed)} GREEN MEANING CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} GREEN MEANING CHECKS PASSED")
sys.exit(0)
