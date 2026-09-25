"""Checks a booklet does not throw away a third of a page to keep a box whole.

A shipped Year 3 booklet printed 24 questions across 14 pages and looked like
15 questions, because it averaged 4.8cm of empty page above the footer and one
page wasted 12.4cm of a 24.6cm column. The blank always sat immediately before
a mini-lesson or a part band.

THE CAUSE WAS A GUARD DOING ITS JOB. Three separate breaks all measured the
same thing: a heading run plus the whole lesson opening, INCLUDING the
worked-example box. That box is a Table, it cannot split, and it is several
centimetres tall. So when a page had room for everything except the box, the
break moved the lot and threw away everything still on the page.

It was written to stop a different defect: headings and lesson prose left
sitting above four or five centimetres of white when only the box moved. That
defect is real and it is the SMALLER one. The guard traded a bounded cost for
an unbounded one, and in a booklet whose worked examples run long it fired on
every subtopic.

So the breaks now measure the heading run plus the lesson PROSE, and the box
is allowed to travel to the next page alone. The prose fills whatever is left,
which is what a reader wants under a heading anyway, and the only thing thrown
away is what the box itself would not fit into.

WHAT THAT COULD BREAK, and what the second half of this file is for: a heading
at the very foot of a page with nothing under it. The ordinary orphan rule is
the floor on every one of these breaks, and it is asserted directly, by
measuring the bottom-most text on each page and refusing to find a heading
there.

Renders through the real formatter, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_page_fill.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf                                                     # noqa: E402

from booklet_gen.formatter import render_pdf                       # noqa: E402
from booklet_gen.schemas import (                                  # noqa: E402
    BookletData, Question, SubtopicOutput, SubtopicTeaching,
    ValidatedQuestion, WorkedExample)

PASSED = 0
TOTAL = 0
OUT = Path(tempfile.mkdtemp())

# The footer band, and the smallest type any heading is set in. Measured off a
# rendered booklet: topic 19pt, part band 18pt, subtopic 13pt, and the largest
# thing that is not a heading is "Now you try:" at 11pt.
FOOTER_TOP_PT = 780.0
HEADING_PT = 12.5

# Empty space above the footer, averaged over every content page but the last.
# The last is excluded because a booklet stops where it stops and there is
# nothing to pull up under its score box. Measured: 3.7cm before this fix and
# 1.8cm after, on the booklet below, so this separates them and is not a
# number chosen to be passed.
MEAN_DEAD_SPACE_CM = 2.5


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


def q(text, answer, working, difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty),
        verified=True, validator_notes="", retry_count=0)


# A worked example of the length the product actually produces. The box is the
# whole point of this file: a short one never forces the break that was losing
# the pages, so a check built on a two-line example would pass either way.
STEPS = [
    "Divide the tens: 3 into 7 goes 2 times with 1 left over, so write 2 "
    "above the 7 and carry the 1 across to make 14.",
    "Divide the ones: 3 into 14 goes 4 times because 3 times 4 is 12, which "
    "leaves 2 left over once you subtract.",
    "Write the 4 above the 4, giving a final result of 24 remainder 2.",
]
INTRO = ("When a number does not divide evenly, use multiplication facts to "
         "find the closest multiple that fits inside it. The leftover amount "
         "is called the remainder. Short division sets this out digit by "
         "digit from left to right.")
POINTS = ["Work from left to right, writing any leftover tens in front of the "
          "next digit.",
          "The final remainder must always be smaller than the number you are "
          "dividing by."]

PRACTICE = [
    q("Calculate 58 divided by 4.", "14 remainder 2", "4 into 18 is 4 r2."),
    q("At school camp, 65 badges are shared equally among 6 campers. How many "
      "each, and how many are left over?", "10 each, 5 left", "65 = 6x10 + 5"),
    q("Mia works out 47 divided by 3 and writes 14 remainder 5. Explain why "
      "this is wrong.", "15 remainder 2", "A remainder is under the divisor."),
    q("Calculate 95 divided by 7.", "13 remainder 4", "7 into 25 is 3 r4."),
]
HOMEWORK = [
    q("A secret number divided by 5 gives 16 remainder 3. Find it.", "83",
      "16 x 5 = 80, then 80 + 3 = 83."),
    q("A rectangle has an area of 20 square cm and a width of 4 cm. How long "
      "is it?", "5 cm", "20 divided by 4 = 5 cm."),
    q("Which is larger, 3/4 or 0.7?", "3/4", "3/4 is 0.75."),
]
CHALLENGE = [
    q("A garden bed is 6 m by 4 m. What is its area?", "24 square m",
      "6 x 4 = 24."),
    q("A class of 30 splits into teams of 4. How many teams, how many left?",
      "7 teams, 2 left", "30 = 4x7 + 2."),
    q("Ella reads 12 pages a day for 5 days. How many pages?", "60", "12x5"),
    q("A fence costs $8 a metre. How much for 9 metres?", "$72", "8x9 = 72."),
]


def booklet(year="Year 3"):
    def section(topic, name):
        return SubtopicOutput(
            topic=topic, subtopic=name, subject="Mathematics",
            teaching=SubtopicTeaching(
                intro_paragraphs=[INTRO], key_points=POINTS,
                worked_example=WorkedExample(
                    question="Calculate 74 divided by 3.", steps=STEPS,
                    answer="24 remainder 2")),
            questions=PRACTICE, homework_questions=HOMEWORK,
            estimated_minutes=14)

    return BookletData(
        subject="Mathematics", year_level=year, student_name="Sam",
        program_label="Academic Accelerate",
        sections=[section("Number and Place Value", "Division with remainders"),
                  section("Measurement and Geometry", "Perimeter and area"),
                  section("Statistics and Probability", "Column graphs")],
        recap_questions=PRACTICE, challenge_questions=CHALLENGE,
        recap_minutes=5, challenge_minutes=4, classwork_minutes=43,
        homework_minutes=13, total_minutes=65)


def measure(name, year="Year 3"):
    path = OUT / f"{name}.pdf"
    render_pdf(booklet(year), path)
    doc = pymupdf.open(path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        head = "\n".join(text.split("\n")[:6])
        if "ANSWERS" in head:
            break
        if not any(b in head for b in ("WARM-UP RECAP", "CLASS WORK",
                                       "HOMEWORK", "FINAL CHALLENGE")):
            continue
        low = 0.0
        bottom_size = 0.0
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if not span["text"].strip():
                        continue
                    y1 = span["bbox"][3]
                    if y1 >= FOOTER_TOP_PT:
                        continue
                    if y1 > low:
                        low, bottom_size = y1, span["size"]
                    elif abs(y1 - low) < 1.0:
                        bottom_size = max(bottom_size, span["size"])
        for drawing in page.get_drawings():
            if drawing["rect"].y1 < FOOTER_TOP_PT:
                low = max(low, drawing["rect"].y1)
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if rect.y1 < FOOTER_TOP_PT:
                    low = max(low, rect.y1)
        pages.append({"n": i + 1, "gap_cm": (FOOTER_TOP_PT - low) / 72 * 2.54,
                      "bottom_pt": bottom_size})
    return pages, doc.page_count


print("\n== a booklet does not print a third of itself blank ==")

pages, total = measure("fill")
# Not the last: a booklet stops where it stops and nothing can be pulled up
# under its score box.
body = pages[:-1]
mean = sum(p["gap_cm"] for p in body) / len(body)
worst = max(body, key=lambda p: p["gap_cm"])
check(mean <= MEAN_DEAD_SPACE_CM,
      f"{mean:.2f}cm empty on average across {len(body)} pages "
      f"(worst p{worst['n']} at {worst['gap_cm']:.1f}cm)",
      f"{mean:.2f}cm a page against a ceiling of {MEAN_DEAD_SPACE_CM}cm. The "
      "column is 24.6cm, so this is the share of every page that prints "
      "blank, and it is why a booklet of 24 questions reads like 15")

check(worst["gap_cm"] <= 6.0,
      f"and no single page throws away more than {worst['gap_cm']:.1f}cm",
      f"p{worst['n']} wastes {worst['gap_cm']:.1f}cm. A page that stops a "
      "third of the way up reads as a fault, whatever the average says")


print("\n== and no heading is left standing at the foot of a page ==")

# The regression the change above could cause, and the reason the old break
# existed. Measured off the type size: topic headings are set at 19pt, part
# bands at 18pt and subtopic headings at 13pt, and the largest thing that is
# not a heading is "Now you try:" at 11pt.
for page in pages:
    check(page["bottom_pt"] < HEADING_PT,
          f"p{page['n']} ends in {page['bottom_pt']:.0f}pt type",
          f"p{page['n']} ends with a {page['bottom_pt']:.0f}pt heading and "
          "nothing under it. A heading is a promise about what comes next, "
          "and this is the defect the old break existed to prevent")


print("\n== the fill does not come from a longer booklet ==")

# The cheap way to make every page look full is to spread the same content
# over more pages, which costs the customer paper and proves nothing.
check(total <= 16,
      f"the whole booklet is {total} pages",
      f"{total} pages. Filling pages by adding pages is not filling pages")


print("\n== it holds at the other year levels too ==")

for year in ("Year 1", "Year 6", "Year 9"):
    pages, total = measure(f"fill-{year.replace(' ', '')}", year)
    body = pages[:-1] or pages
    mean = sum(p["gap_cm"] for p in body) / len(body)
    check(mean <= MEAN_DEAD_SPACE_CM + 0.75,
          f"{year}: {mean:.2f}cm a page across {len(body)} pages",
          f"{mean:.2f}cm a page. The year bands change how many questions sit "
          "between one lesson and the next, so a fix that only holds for the "
          "year it was measured on has not found the cause")
    orphans = [p["n"] for p in pages if p["bottom_pt"] >= HEADING_PT]
    check(not orphans,
          f"        and no stranded heading on any page",
          f"headings left at the foot of page(s) {orphans}")


print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
