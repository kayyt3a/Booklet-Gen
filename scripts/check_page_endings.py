"""Checks no page of the booklet ends in a way that reads as a printing fault.

A generated booklet gives itself away at the foot of the page. Three separate
faults were measured on one twenty-one page fixture, and between them they left
4.8cm, 7.2cm, 14.8cm and 12.0cm of unexplained white at four page feet.

  * The mini-lesson's worked example is a single bordered box. A box cannot
    split, so when it does not fit it moves whole to the next page and leaves
    the topic heading, the subtopic heading, the intro paragraph and the key
    points sitting alone above five centimetres of nothing. The child turns
    over to find the example that was being introduced.
  * A heading with nothing under it. "Class Work", "Number and Place Value"
    and "Four-digit numbers and ordering" all three at the foot of a key page
    with no answer beneath any of them, and "Now you try:" as the last thing on
    a page with question 1 overleaf. A heading is a promise about what follows;
    at the foot of a page it is a broken one.
  * A page that legitimately ends short. Homework will not start with less than
    7cm left and the Final Challenge will not start with less than 9cm, because
    a part that begins three lines before a page turn is worse than one that
    begins on a fresh page. Those rules are right and stay. What was wrong was
    leaving the room they create as blank paper, which reads as a fault rather
    than as a decision.

    PYTHONPATH=. python scripts/check_page_endings.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen.formatter import (HOMEWORK_MIN_START_CM, PAGE_MARGIN,
                                   _CHALLENGE_MIN_START_CM,
                                   _CHECKPOINT_PROMPT, render_pdf)
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


# ---------------------------------------------------------------------------
# The fixture
#
# Deliberately awkward: the subtopics differ in how many steps their worked
# example carries and how many guided examples follow it, so the lessons are
# different heights and the page boundaries fall in different places under
# them. That is the condition the strand needs. One subtopic has no class work
# at all, which is the shape the hour cap produces, and its lesson is reprinted
# down in Homework where its practice went.
# ---------------------------------------------------------------------------

def vq(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


SUBTOPICS = [
    # (topic, subtopic, marker, worked-example steps, guided examples)
    ("Fractions", "Comparing fractions", "Rosella", 2, 1),
    ("Fractions", "Adding fractions", "Quokka", 3, 2),
    ("Volume", "Volume of a prism", "Numbat", 5, 1),
    ("Volume", "Capacity and litres", "Bilby", 4, 2),
    ("Number and Place Value", "Four-digit numbers and ordering", "Wombat", 3, 1),
    ("Number and Place Value", "Rounding to the nearest hundred", "Bandicoot", 4, 1),
]
STEPS = ["Multiply the length by the width: 5 x 3 = 15.",
         "Multiply that result by the height: 15 x 4 = 60.",
         "State the volume in cubic units: 60 cm^3.",
         "Check it against an estimate: 5 x 3 x 4 is about 60.",
         "Write the unit down, because a number alone is not a volume."]


def teaching(marker, n_steps, n_guided):
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the amount of space inside a three "
                          "dimensional object. To find the volume of a "
                          "rectangular prism you multiply its length, width "
                          "and height together, and the answer is always in "
                          "cubic units."],
        key_points=["Volume equals length times width times height.",
                    "Write your answer in cubic units, like cm^3.",
                    "Check the unit before you write the answer down."],
        worked_example=WorkedExample(
            question=f"The {marker} box is 5 cm by 3 cm by 4 cm. What is its "
                     "volume?",
            steps=STEPS[:n_steps], answer="60 cubic centimetres"),
        guided_examples=[WorkedExample(
            question=f"Find the volume of a prism {k + 2} cm by 2 cm by 3 cm.",
            steps=["Multiply the length and width.", "Multiply by the height."],
            answer="36 cubic cm") for k in range(n_guided)])


def booklet():
    sections = []
    for i, (topic, subtopic, marker, steps, guided) in enumerate(SUBTOPICS):
        sections.append(SubtopicOutput(
            topic=topic, subtopic=subtopic,
            teaching=teaching(marker, steps, guided),
            # The last subtopic did not fit the hour, so it has no class work
            # and its lesson is reprinted in Homework.
            questions=[] if i == len(SUBTOPICS) - 1 else [
                vq(f"Question {i}.{j}: A box is {j + 2} cm long, 2 cm wide and "
                   "3 cm high. What is its volume in cubic centimetres?",
                   answer=str((j + 2) * 6),
                   working="Volume = length x width x height. The volume is "
                           f"{(j + 2) * 6}.") for j in range(4)],
            homework_questions=[
                vq(f"Homework {i}.{j}: Calculate {j + 1}/12 + {j + 1}/12.",
                   answer=f"{2 * (j + 1)}/12", difficulty="easy",
                   working="Add the numerators over the same denominator.")
                for j in range(5)],
            estimated_minutes=10))
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=[vq("Calculate 15 * 4 + 7.", answer="67",
                            difficulty="easy"),
                         vq("If 5x = 45, what is x?", answer="x = 9",
                            difficulty="easy")],
        challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its "
                                "volume in cubic metres?", answer="75",
                                difficulty="hard",
                                working="Volume = 10 x 5 x 1.5 = 75.")],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


out = Path(tempfile.mkdtemp(prefix="folio-endings-")) / "endings.pdf"
render_pdf(booklet(), out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
PAGES = [page.get_text() for page in doc]
KEY_START = next(i for i, t in enumerate(PAGES) if "Worked Solutions" in t)
BODY = range(1, KEY_START)


def page_of(needle, first=1, last=None):
    for i in range(first, len(PAGES) if last is None else last):
        if needle in " ".join(PAGES[i].split()):
            return i
    return None


print("\nA MINI-LESSON ARRIVES WITH ITS WORKED EXAMPLE")

# The worked-example question carries a marker word unique to its subtopic and
# printed nowhere else in the booklet, so the page it landed on is not a guess.
stranded = []
for topic, subtopic, marker, _, _ in SUBTOPICS:
    box = page_of(f"The {marker} box")
    if box is None:
        stranded.append((subtopic, "the worked example was not printed at all"))
        continue
    page = " ".join(PAGES[box].split())
    missing = [name for name, text in
               (("its subtopic heading", subtopic),
                ("its intro paragraph", "Volume is the amount of space"),
                ("its key points", "Volume equals length times width"))
               if text not in page]
    if missing:
        stranded.append((subtopic, f"page {box + 1} has the box but not "
                                   + ", ".join(missing)))

check(not stranded,
      f"all {len(SUBTOPICS)} mini-lessons print their heading, intro, key "
      "points and worked example on one page",
      f"these lessons were split from their own worked example: {stranded}. "
      "The box cannot break, so it moves whole to the next page and leaves "
      "the headings and the bullets above about five centimetres of white. "
      "The child reads an introduction to an example that is not there")

print("\nNO HEADING IS LEFT AT THE FOOT OF A PAGE WITH NOTHING UNDER IT")

# Every heading in the booklet is set in the display serif: topics, subtopics,
# the coloured part bands and the key's own headings. "Now you try:" is a sans
# label but makes the same promise, so it is named here rather than inferred.
# A heading being the lowest thing in its column is the whole defect: there is
# nothing under it, and the reader turns the page to find out what it named.
PROMISES = {"Now you try:"}
GUTTER = (A4[0] / 2 - 10, A4[0] / 2 + 10)


def lowest_lines(index):
    """The bottom text line of each column on this page.

    The body runs in one column, the answer key in two, so the key is split at
    the gutter: a heading at the foot of the left column with the answers
    continuing at the top of the right is the same orphan as one at the foot
    of a full-width page, and only shows up if the columns are measured apart.
    """
    page = doc[index]
    height = page.rect.height
    columns = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            x0, y0, _, y1 = line["bbox"]
            if y0 < PAGE_MARGIN - 6 or y1 > height - PAGE_MARGIN + 6:
                continue
            key = ("right" if x0 > GUTTER[1] else "left") \
                if index >= KEY_START else "page"
            if key not in columns or y1 > columns[key][0]:
                columns[key] = (y1, line["spans"][0]["font"], text)
    return columns


orphans = []
for i in range(1, len(PAGES)):
    for column, (_, font, text) in lowest_lines(i).items():
        if "Serif" in font or text in PROMISES:
            orphans.append((f"page {i + 1}", column, text[:44]))

check(not orphans,
      f"no heading is the last thing in its column, across all "
      f"{len(PAGES) - 1} printed pages",
      f"these headings are the last thing in their column, with nothing "
      f"under them: {orphans}. A heading is a promise about what follows it; "
      "at the foot of a page it is a broken one, and on a key page three of "
      "them stacked with no answer beneath reads as a page that failed to "
      "print")

print("\nA PAGE LEFT SHORT ON PURPOSE ENDS ON SOMETHING, NOT ON NOTHING")

# The two rules that create the gap. They are deliberate and they stay: a part
# that begins three lines before a page turn is worse than one that begins on
# a fresh page. This check exists to make sure the whitespace was dealt with by
# filling the hole and not by quietly deleting the rules that create it, which
# would be the easy way to make the measurements look better.
check(HOMEWORK_MIN_START_CM >= 7.0 and _CHALLENGE_MIN_START_CM >= 9.0,
      f"Homework still refuses to start with under {HOMEWORK_MIN_START_CM}cm "
      f"left, and the Final Challenge under {_CHALLENGE_MIN_START_CM}cm",
      f"the minimum start rules were weakened to {HOMEWORK_MIN_START_CM}cm and "
      f"{_CHALLENGE_MIN_START_CM}cm. That does not remove dead space, it moves "
      "it: a part that starts three lines before a page turn is worse than one "
      "that starts on a fresh page")

strips = [i for i, t in enumerate(PAGES) if _CHECKPOINT_PROMPT in t]
check(strips,
      f"the checkpoint strip is printed on pages {[i + 1 for i in strips]}",
      f"no page carries the checkpoint strip. {HOMEWORK_MIN_START_CM}cm and "
      f"{_CHALLENGE_MIN_START_CM}cm of page foot are given up by the minimum "
      "start rules and left as blank paper, which a reader cannot tell from a "
      "page that failed to print")

# It is a device for filling a hole, not a page furniture element. On every
# page is a tic; nowhere near a hole is useless.
check(len(strips) <= max(1, (KEY_START - 1) // 3),
      f"it appears on {len(strips)} of the {KEY_START - 1} question pages, "
      "which is where the holes are and nowhere else",
      f"it appears on {len(strips)} of {KEY_START - 1} question pages. It is "
      "there to fill a page foot given up on purpose, and printed on every "
      "page it stops being a checkpoint and becomes wallpaper")

check(all(i < KEY_START for i in strips),
      "and never in the answer key, which nobody works through",
      f"the checkpoint strip reached the answer key: pages "
      f"{[i + 1 for i in strips if i >= KEY_START]}. It asks the child how the "
      "work went, and the key is read by whoever is marking")

# The Homework part band is the biggest of the holes, at up to seven
# centimetres. The page before it is the one to look at.
homework_page = page_of("Do these through the week")
check(homework_page is not None and homework_page - 1 in strips,
      f"the page Class Work ends on, the one before Homework starts on page "
      f"{(homework_page or 0) + 1}, carries the strip, so the room Homework "
      "gave up reads as designed",
      f"Homework starts on page {(homework_page or 0) + 1} and the page before "
      "it ends in blank paper. That is the HOMEWORK_MIN_START_CM hole, the "
      "largest one in the booklet, and the one a parent is most likely to read "
      "as a misprint")

HEIGHT = doc[0].rect.height
for i in strips:
    page = doc[i]
    boxes = [d["rect"] for d in page.get_drawings()
             if d["rect"].width > 0.5 * (A4[0] - 2 * PAGE_MARGIN)
             and 0.6 * cm < d["rect"].height < 2.0 * cm]
    if not check(boxes,
                 f"page {i + 1}: the strip is drawn as a box, not just words",
                 f"page {i + 1} has the checkpoint text but no box round it. "
                 "Three loose words at the foot of a page read as something "
                 "left behind rather than as a designed element"):
        continue
    strip = max(boxes, key=lambda r: r.y1)
    foot = HEIGHT - PAGE_MARGIN - strip.y1
    check(abs(foot) < 0.6 * cm,
          f"page {i + 1}: it sits on the bottom margin, {foot / cm:.2f}cm off",
          f"page {i + 1}: the strip's bottom edge is {foot / cm:.1f}cm above "
          "the bottom margin, so there is still a band of blank paper under "
          "it. It is meant to be what the page ends on")

if strips:
    print("\nAND IT COSTS ALMOST NO INK")

    # A booklet is printed at home, often more than once. The strip earns its
    # place by being nearly free: one hairline box, four short words and three
    # empty squares.
    page = doc[strips[0]]
    strip = max((d["rect"] for d in page.get_drawings()
                 if d["rect"].width > 0.5 * (A4[0] - 2 * PAGE_MARGIN)
                 and 0.6 * cm < d["rect"].height < 2.0 * cm),
                key=lambda r: r.y1)
    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, clip=strip)
    coverage = (255.0 - sum(pix.samples) / len(pix.samples)) / 255.0
    check(coverage <= 0.05,
          f"the strip covers {coverage:.1%} of its own area in ink",
          f"the strip covers {coverage:.1%} of its area in ink. It is filling "
          "a hole on a page a parent prints at home; it is not worth a "
          "cartridge")

doc.close()

if _failed:
    print(f"\n{len(_failed)} PAGE ENDING CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} PAGE ENDING CHECKS PASSED")
sys.exit(0)
