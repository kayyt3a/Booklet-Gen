"""Checks the running head and foot say where in the booklet the reader is.

Every page from 2 to 21 used to carry the identical line "Academic Accelerate |
Year 3 | Kieran" and a bare page number. That tells the reader three things
they knew when they bought it and nothing about where they are. A tutor mid
session could not find their place, and a parent flipping to the back could not
see where the answers begin.

Four properties, each of them something a reader would notice.

  * The foot gives a position, not a number. "Page 3 of 21" also says the print
    job came out whole, which "Page 3" cannot.
  * The head names the part the page belongs to, at the right, and the rule
    under it takes that part's colour. Checked against the part bands actually
    drawn in the PDF rather than against the map the head was built from, so
    the head cannot pass by agreeing with itself.
  * The chrome stays inside CHROME_MARGIN. That constant is 1.6cm because the
    unprintable band of common home printers reaches 12.7mm (HP DeskJet) and
    14.0mm (Epson EcoTank): printing at actual size below it clips the page
    number, and printing to fit rescales every ruled line the child writes on.
  * The cover carries none of it.

    PYTHONPATH=. python scripts/check_running_chrome.py
"""
import re
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen.formatter import (CHROME_MARGIN, EXAM_SECTION_INK,
                                   PAGE_MARGIN, part_ink, render_exam_pdf,
                                   render_pdf)
from booklet_gen.schemas import (BookletData, ExamPaper, ExamSection, Question,
                                 SubtopicOutput, SubtopicTeaching,
                                 ValidatedQuestion, WorkedExample)

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


def vq(text, answer="42", working="42", difficulty="medium", marks=None):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty, marks=marks), verified=True)


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Volume is the space inside a solid, in cubic units."],
    key_points=["Length times width times height."],
    worked_example=WorkedExample(question="A box is 5 cm by 3 cm by 4 cm.",
                                 steps=["5 x 3 = 15.", "15 x 4 = 60."],
                                 answer="60 cubic centimetres"))
SHORT = ["What is {} x 7?", "What is {} + 68?", "What is 480 - {}?",
         "Round {} to the nearest hundred."]


def questions(seed, n):
    return [vq(SHORT[(seed + j) % len(SHORT)].format(24 + j * 3 + seed),
               answer=str(7 * (24 + j * 3 + seed)),
               difficulty="easy" if j % 2 else "medium") for j in range(n)]


booklet = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    program_label="Academic Accelerate",
    sections=[SubtopicOutput(topic=t, subtopic=s, teaching=TEACHING,
                             questions=questions(i, 6),
                             homework_questions=questions(i + 5, 5),
                             estimated_minutes=10)
              for i, (t, s) in enumerate([("Fractions", "Comparing fractions"),
                                          ("Fractions", "Adding fractions"),
                                          ("Volume", "Volume of a prism")])],
    recap_questions=questions(9, 3), challenge_questions=questions(4, 2),
    recap_minutes=6, classwork_minutes=60, homework_minutes=105,
    challenge_minutes=18, total_minutes=170)

paper = ExamPaper(
    subject="Mathematics Methods", year_level="Year 12", student_name="Alex",
    unit="Units 3 and 4", reading_minutes=10, working_minutes=100,
    sections=[ExamSection(name="Section One: Calculator-free",
                          description="Answer all questions.",
                          working_minutes=50,
                          questions=[vq("Differentiate y = 3x^2 + 2x.",
                                        answer="6x + 2", marks=3),
                                     vq("Integrate 2x dx from 0 to 3.",
                                        answer="9", marks=4)]),
              ExamSection(name="Section Two: Calculator-assumed",
                          description="Show your working.", working_minutes=50,
                          questions=[vq("A population grows at 4% a year.",
                                        answer="about 1480", marks=6)])])

tmp = Path(tempfile.mkdtemp(prefix="folio-chrome-"))
BOOKLET = render_pdf(booklet, tmp / "booklet.pdf")
EXAM = render_exam_pdf(paper, tmp / "exam.pdf")

HEAD_BAND = 55          # everything drawn above this is running head
FOOT_BAND = 780         # everything below this is the foot


def lines_on(page):
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                out.append((line["bbox"], round(line["spans"][0]["size"], 1),
                            line["spans"][0].get("color", 0), text))
    return out


def head_of(page):
    return [(b, s, c, t) for b, s, c, t in lines_on(page) if b[3] < HEAD_BAND]


def foot_of(page):
    return [(b, s, c, t) for b, s, c, t in lines_on(page) if b[1] > FOOT_BAND]


def rgb(colour_int):
    return ((colour_int >> 16) & 255, (colour_int >> 8) & 255, colour_int & 255)


def hex_rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


print("\nTHE FOOT GIVES A POSITION, NOT A NUMBER")

for name, path in (("booklet", BOOKLET), ("exam", EXAM)):
    doc = pymupdf.open(path)
    total = len(doc)
    wrong = []
    for i, page in enumerate(doc[1:], start=2):
        feet = [t for _, _, _, t in foot_of(page) if t.startswith("Page ")]
        if feet != [f"Page {i} of {total}"]:
            wrong.append((i, feet))
    doc.close()
    check(not wrong,
          f"{name}: every page from 2 to {total} is numbered "
          f"\"Page n of {total}\"",
          f"{name}: these feet are wrong: {wrong[:4]}. A page number with no "
          "total is a number; with a total it is a position, and it is also "
          "how a reader knows the print job came out whole")

print("\nTHE HEAD NAMES THE PART THE PAGE IS IN")

# Derived from the PDF, not from the formatter's map: the part a page belongs
# to is the last part band printed on or before it. The bands are found by
# their 18pt display type, and the answer key by its own 20pt heading, which
# is the only part of the booklet that opens without a band.
PART_BAND_PT = 18.0
ANSWERS_PT = 20.0
KNOWN = {"Warm-up Recap", "Class Work", "Homework", "Final Challenge",
         "Spelling Test", "Times Tables Test"} \
    | {s.name for s in paper.sections} | {"Marking Key"}


def parts_by_page(path):
    """{page number: the part it belongs to}, read off the printed bands."""
    doc = pymupdf.open(path)
    marks = []
    for i, page in enumerate(doc, start=1):
        for bbox, size, _, text in lines_on(page):
            if abs(size - PART_BAND_PT) < 0.6 and text in KNOWN:
                marks.append((i, bbox[1], text))
            elif abs(size - ANSWERS_PT) < 0.6 and "Worked Solutions" in text:
                marks.append((i, bbox[1], "Answers"))
    total = len(doc)
    doc.close()
    marks.sort()
    out = {}
    for j, (page, _, name) in enumerate(marks):
        last = marks[j + 1][0] if j + 1 < len(marks) else total
        for p in range(page, last + 1):
            out[p] = name
    return out


def exam_ink(name: str) -> str:
    return part_ink("Answers") if name == "Marking Key" else EXAM_SECTION_INK


for name, path, inks in (("booklet", BOOKLET, part_ink),
                         ("exam", EXAM, exam_ink)):
    doc = pymupdf.open(path)
    belongs = parts_by_page(path)
    wrong, uncoloured = [], []
    for i, page in enumerate(doc, start=1):
        want = belongs.get(i)
        slots = [(b, c, t) for b, s, c, t in head_of(page)
                 if abs(s - 8.5) < 0.4]
        if want is None:
            continue
        if [t for _, _, t in slots] != [want.upper()]:
            wrong.append((i, [t for _, _, t in slots], want.upper()))
            continue
        # And it is set in the part's own colour, the same one its band and
        # its chip on the contents page are drawn in.
        ink = hex_rgb(inks(want))
        got = rgb(slots[0][1])
        if max(abs(a - b) for a, b in zip(ink, got)) > 3:
            uncoloured.append((i, want, got, ink))
    doc.close()
    check(not wrong,
          f"{name}: the head names the right part on all "
          f"{len(belongs)} pages that are inside one",
          f"{name}: the head names the wrong part on these pages "
          f"(page, printed, expected): {wrong[:5]}. A running head that names "
          "the wrong part is worse than one that names nothing")
    check(not uncoloured,
          f"{name}: and it is set in that part's own colour",
          f"{name}: these slots are not in the part's colour "
          f"(page, part, drawn, expected): {uncoloured[:4]}")

print("\nAND THE RULE UNDER IT CARRIES THE SAME COLOUR")

# A tint of the part's colour rather than the colour itself: at full strength
# across the whole measure it is a heavier line than a running head wants, and
# the slot's own type carries the hue at full strength. What must hold is that
# the rule is recognisably that part's hue and not the flat grey it replaced.
doc = pymupdf.open(BOOKLET)
belongs = parts_by_page(BOOKLET)
rules, flat = {}, []
for i, page in enumerate(doc, start=1):
    for d in page.get_drawings():
        r = d["rect"]
        if (d.get("color") and r.height < 1.5 and r.width > 400
                and r.y0 < HEAD_BAND + 10):
            rules[i] = (d["color"], d.get("width"))
for page, part in belongs.items():
    if page not in rules:
        flat.append((page, part, "no rule drawn"))
        continue
    colour, width = rules[page]
    got = tuple(round(c * 255) for c in colour)
    ink = hex_rgb(part_ink(part))
    grey = max(got) - min(got) < 8
    # The tint has to keep the part's hue: the channel ordering of the ink is
    # what tells navy from bronze from the grey the rule used to be.
    if grey or sorted(range(3), key=lambda k: got[k]) != sorted(
            range(3), key=lambda k: ink[k]):
        flat.append((page, part, got))
    elif abs((width or 0) - 1.0) > 0.01:
        flat.append((page, part, f"{width}pt"))
doc.close()
check(not flat,
      f"the head rule takes the part's hue on all {len(belongs)} pages, at 1pt",
      f"these head rules are the old flat grey, missing, or the wrong weight: "
      f"{flat[:5]}. The rule is the part's colour at a tint; a grey one says "
      "nothing about where the reader is")

print("\nNOTHING SITS INSIDE A HOME PRINTER'S UNPRINTABLE BAND")

# CHROME_MARGIN is 1.6cm because 1.2cm put the descender of "Page" 11.3mm from
# the sheet edge, inside the band an HP DeskJet (12.7mm) and an Epson EcoTank
# (14.0mm) cannot print in. That band is the FOOT of the sheet, where the feed
# rollers are; the same printers take about 3mm at the head. So the floor here
# is measured on the foot, on the ink and not on the baseline the text was
# asked for, and the head is asked for something different: that it clears the
# top edge comfortably and stays out of the type area, because chrome that
# reaches into the body collides with the part band at the top of a page.
FOOT_FLOOR_CM = 1.5
HEAD_FLOOR_CM = 1.0
for name, path in (("booklet", BOOKLET), ("exam", EXAM)):
    doc = pymupdf.open(path)
    low, high = [], []
    for i, page in enumerate(doc, start=1):
        for bbox, _, _, text in foot_of(page):
            gap = (A4[1] - bbox[3]) / cm
            if gap < FOOT_FLOOR_CM:
                low.append((i, text[:20], round(gap, 2)))
        for bbox, _, _, text in head_of(page):
            if bbox[1] / cm < HEAD_FLOOR_CM or bbox[3] > PAGE_MARGIN:
                high.append((i, text[:20], round(bbox[1] / cm, 2),
                             round(bbox[3] / cm, 2)))
    doc.close()
    check(not low,
          f"{name}: every line at the foot keeps {FOOT_FLOOR_CM}cm of the "
          "sheet edge, descenders included",
          f"{name}: this chrome is inside the unprintable band: {low[:4]}. "
          "Printed at actual size it is clipped; printed to fit, the whole "
          "sheet is rescaled and every ruled line the child writes on shrinks "
          "with it")
    check(not high,
          f"{name}: and the running head sits between the top edge and the "
          "type area",
          f"{name}: this head ink is too close to the edge or reaches into "
          f"the body: {high[:4]} (page, text, top cm, bottom cm)")

print("\nTHE COVER CARRIES NONE OF IT")

for name, path in (("booklet", BOOKLET), ("exam", EXAM)):
    doc = pymupdf.open(path)
    cover = " ".join(t for _, _, _, t in head_of(doc[0]) + foot_of(doc[0]))
    doc.close()
    check(not re.search(r"Page \d", cover),
          f"{name}: page 1 is one composition, with no running head or foot "
          "over it",
          f"{name}: the cover carries chrome: {cover[:60]!r}")

if _failed:
    print(f"\n{len(_failed)} RUNNING CHROME CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} RUNNING CHROME CHECKS PASSED")
sys.exit(0)
