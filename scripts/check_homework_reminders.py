"""Checks the reminder beside the homework is the lesson's own words, or absent.

Homework is worked days later, alone, and pages away from the lesson. A
subtopic taught in the session deliberately does not reprint its mini-lesson
down there, which is right, and it leaves the child with nothing in front of
them at the one moment they are on their own with it. A printed workbook puts a
"Remember" beside the questions at exactly that point.

The risk in a callout is that the formatter starts writing. It must not: this
booklet's teaching is generated, checked and paid for as teaching, and a tip
composed in the layout code is content nobody validated, printed in the voice
of content that was. So every assertion here is about provenance.

  * Every line in a reminder is a key point of that subtopic, character for
    character.
  * It is printed only where the data carries it: a subtopic with no teaching,
    no key points, or points too long to be a reminder gets nothing rather than
    filler, and a subtopic whose whole mini-lesson was reprinted in Homework
    does not get the same lines twice on one page.
  * It never carries an answer to a question underneath it.
  * And it arrives on the page its questions are on, not at the foot of the one
    before.

    PYTHONPATH=. python scripts/check_homework_reminders.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf

from booklet_gen.formatter import (_REMINDER_LABEL, _REMINDER_MAX_CHARS,
                                   reminder_points, render_pdf)
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


def vq(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


def teaching(points):
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the space inside a solid."],
        key_points=list(points),
        worked_example=WorkedExample(question="A box is 5 cm by 3 cm by 4 cm.",
                                     steps=["5 x 3 = 15.", "15 x 4 = 60."],
                                     answer="60 cubic centimetres"))


LONG_POINT = ("Whenever you are asked for a volume, remember that you must "
              "first multiply the length by the width and only then multiply "
              "the result by the height, and write the unit down at the end.")
assert len(LONG_POINT) > _REMINDER_MAX_CHARS

# Five subtopics, each a different shape of the same data, so both the printing
# and the not-printing are exercised in one booklet.
SECTIONS = [
    # (subtopic, key points, has class work)
    ("Volume of a prism", ["Volume equals length times width times height.",
                           "Write your answer in cubic units, like cm^3.",
                           "Check the unit before you write it down."], True),
    ("Capacity and litres", ["1000 mL is one litre."], True),
    ("Rounding to hundreds", [], True),                     # no key points
    ("Ordering numbers", [LONG_POINT], True),               # too long to use
    ("Adding fractions", ["Add the numerators, keep the denominator."], False),
]


def booklet():
    sections = []
    for i, (name, points, taught) in enumerate(SECTIONS):
        sections.append(SubtopicOutput(
            topic="Number and Measurement", subtopic=name,
            teaching=teaching(points) if points or i == 2 else None,
            questions=[vq(f"{name} class {j}: what is {j + 2} x 7?")
                       for j in range(4)] if taught else [],
            homework_questions=[
                vq(f"{name} homework {j}: what is {j + 3} x 9?",
                   answer=f"ANSWER{i}{j}", difficulty="easy")
                for j in range(4)],
            estimated_minutes=10))
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=[vq("Calculate 15 x 4 + 7.", difficulty="easy")],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        total_minutes=170)


data = booklet()
path = render_pdf(data, Path(tempfile.mkdtemp(prefix="folio-remind-")) / "b.pdf")
doc = pymupdf.open(path)
PAGES = [" ".join(page.get_text().split()) for page in doc]
print(f"\nrendered {path}")

print("\nA REMINDER IS PRINTED WHERE THE DATA CARRIES ONE, AND NOWHERE ELSE")

WANT = {s.subtopic: reminder_points(s) for s in data.sections}
printed = sum(p.count(_REMINDER_LABEL) for p in PAGES)
check(printed == sum(1 for v in WANT.values() if v),
      f"{printed} reminders printed, one for each of the "
      f"{sum(1 for v in WANT.values() if v)} subtopics whose key points can "
      "carry one",
      f"{printed} reminders printed against "
      f"{sum(1 for v in WANT.values() if v)} subtopics that have usable key "
      "points")

for name, points in WANT.items():
    reason = {
        "Rounding to hundreds": "it has no key points at all",
        "Ordering numbers": "its only key point is a paragraph, not a "
                            "reminder",
        "Adding fractions": "its whole mini-lesson is reprinted in Homework",
    }.get(name)
    if reason:
        check(not points,
              f"{name!r} gets none, because {reason}",
              f"{name!r} was given a reminder although {reason}. Filler in the "
              "place a workbook puts a rule is worse than white space: it "
              "teaches the reader to skip the device")

print("\nAND EVERY LINE OF IT IS THE SUBTOPIC'S OWN KEY POINT")

# Character for character, against the data the booklet was built from. This is
# the assertion the whole item stands on: the formatter must not be the author
# of anything a child reads as teaching.
invented = []
for i, page in enumerate(doc):
    text = PAGES[i]
    if _REMINDER_LABEL not in text:
        continue
    after = text.split(_REMINDER_LABEL, 1)[1]
    for section in data.sections:
        for point in reminder_points(section):
            if point[:40] in after:
                break
        else:
            continue
        # This page's reminder belongs to this subtopic. Every bullet on it has
        # to be one of that subtopic's own points.
        for point in reminder_points(section):
            if point[:40] not in after:
                invented.append((i + 1, section.subtopic, point[:40]))
        break
check(not invented,
      "every reminder prints its own subtopic's key points, unchanged",
      f"these reminders do not match the subtopic's key points: {invented}. "
      "A tip written in the layout code is content nobody validated, printed "
      "in the voice of content that was")

print("\nIT NEVER CARRIES AN ANSWER TO THE QUESTIONS UNDER IT")

leaks = []
for i, page in enumerate(doc):
    if _REMINDER_LABEL not in PAGES[i]:
        continue
    strip = PAGES[i].split(_REMINDER_LABEL, 1)[1][:400]
    for section in data.sections:
        for hq in section.homework_questions:
            if hq.question.answer and hq.question.answer in strip:
                leaks.append((i + 1, hq.question.answer))
check(not leaks,
      "no reminder prints an answer to a question beneath it",
      f"these answers were printed in a reminder: {leaks}")

print("\nAND IT ARRIVES ON THE PAGE ITS QUESTIONS ARE ON")

# A reminder at the foot of a page with the questions overleaf is the same
# stranded-heading defect the rest of the booklet spent a round removing, in a
# device whose entire purpose is to be beside the work.
stranded = []
for i, page in enumerate(doc):
    if _REMINDER_LABEL not in PAGES[i]:
        continue
    for section in data.sections:
        points = reminder_points(section)
        if not points or points[0][:40] not in PAGES[i]:
            continue
        first = section.homework_questions[0].question.question[:30]
        if first not in PAGES[i]:
            stranded.append((i + 1, section.subtopic))
check(not stranded,
      "every reminder shares its page with the first question it is about",
      f"these reminders printed with their questions overleaf: {stranded}")

doc.close()

if _failed:
    print(f"\n{len(_failed)} HOMEWORK REMINDER CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} HOMEWORK REMINDER CHECKS PASSED")
sys.exit(0)
