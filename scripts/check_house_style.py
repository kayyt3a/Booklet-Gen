"""Checks the inside of the booklet belongs to the same product as the cover.

The cover was designed to a system (booklet_gen/assets/COVER_DESIGN_SYSTEM.md):
deep navy, one accent blue, layered page-fold waves, the Folio mark behaving as
a publisher's imprint. The interior shared none of it. A customer opened the
file, saw a designed cover, and then twenty pages that could have come out of
any office. Two products, one purchase.

Three things cross over, and this file checks each of them on a rendered PDF
rather than in the source.

  * The lines the child writes on are ruled in the cover's own accent blue,
    which is what an exercise book looks like, and imported from the cover
    module rather than retyped, so the two cannot drift apart.
  * The wordmark sits at the foot of every page opposite the page number, where
    a publisher's imprint goes.
  * The cover's wave motif closes the front matter pages, drawn by the cover's
    own routine.

And each of them is checked for the two ways decoration costs a customer money:
it must survive a greyscale printer, and it must not drink ink.

    PYTHONPATH=. python scripts/check_house_style.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen.formatter import (ACCENT_BLUE, CHROME_MARGIN, PAGE_MARGIN,
                                   PART_CHALLENGE, PART_CLASSWORK,
                                   PART_HOMEWORK, PART_RECAP, render_pdf)
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)
from booklet_gen.visuals import cover as C

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


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Volume is the space inside a solid, in cubic units."],
    key_points=["Length times width times height."],
    worked_example=WorkedExample(question="A box is 5 cm by 3 cm by 4 cm.",
                                 steps=["5 x 3 = 15.", "15 x 4 = 60."],
                                 answer="60 cubic centimetres"))


def questions(seed, n):
    out = []
    for j in range(n):
        if j % 3 == 2:
            out.append(vq(f"Explain how you know {300 + seed + j} is larger "
                          f"than {290 + seed + j}.", answer="More hundreds."))
        else:
            out.append(vq(f"What is {24 + seed + j * 3} x 7?",
                          answer=str(7 * (24 + seed + j * 3)),
                          difficulty="easy"))
    return out


data = BookletData(
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

path = render_pdf(data, Path(tempfile.mkdtemp(prefix="folio-style-")) / "b.pdf")
doc = pymupdf.open(path)
print(f"\nrendered {path}")


def hex_rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


ACCENT = hex_rgb(ACCENT_BLUE)

print("\nTHE BOOKLET'S ACCENT IS THE COVER'S ACCENT")

check(ACCENT_BLUE == C.ACCENT_HEX,
      f"the interior rules and the cover both use {C.ACCENT_HEX}, from one "
      "definition",
      f"the interior uses {ACCENT_BLUE} and the cover {C.ACCENT_HEX}. Two "
      "blues that were meant to be one is how a product stops looking like a "
      "product")

print("\nTHE LINES THE CHILD WRITES ON ARE RULED IN IT")

# Every 0.6pt hairline in the booklet except the one that closes a topic
# opener. That one is drawn in its part's own colour on purpose: it is the foot
# of a landmark, not a line anybody writes on, and check_topic_openers asserts
# the colour it is in.
PART_INKS = {hex_rgb(h) for h in (PART_RECAP, PART_CLASSWORK, PART_HOMEWORK,
                                  PART_CHALLENGE)}
rules = []
for i, page in enumerate(doc):
    for d in page.get_drawings():
        w = d.get("width") or 0
        if not (0.5 < w < 0.7 and d["rect"].height < 1 and d.get("color")):
            continue
        if any(max(abs(a - b) for a, b in zip(d["color"], ink)) < 0.01
               for ink in PART_INKS):
            continue
        rules.append((i + 1, d))
accented = [(p, d) for p, d in rules
            if max(abs(a - b) for a, b in zip(d["color"], ACCENT)) < 0.01]
check(len(rules) >= 20,
      f"{len(rules)} answer and response rules are drawn in the booklet",
      f"only {len(rules)} rules found, so nothing below is measured")
check(len(accented) == len(rules),
      f"and all {len(accented)} of them are the cover's blue",
      f"only {len(accented)} of {len(rules)} rules are in the accent; the "
      f"rest are drawn in something else. The line a child writes on is the "
      "most repeated mark in the booklet and the cheapest place to carry the "
      "brand")

print("\nAND THEY SURVIVE A GREYSCALE PRINTER")

# Most of these booklets are printed at home in black and white. A rule that
# vanishes there is worse than the grey it replaced, and one that prints darker
# than the question text competes with it.
page_no, rule = (accented or rules)[len(accented or rules) // 2]
page = doc[page_no - 1]
pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
s = 300 / 72
band = grey[int(rule["rect"].y0 * s) - 2:int(rule["rect"].y1 * s) + 3,
            int(rule["rect"].x0 * s) + 4:int(rule["rect"].x1 * s) - 4]
darkest = int(band.min())
check(darkest <= 175,
      f"a ruled line prints at {darkest}/255 in greyscale, no lighter than the "
      "grey it replaced (about 164)",
      f"the rule prints at {darkest}/255 in greyscale. A child ruling their "
      "answer under a line they cannot see is writing on blank paper")
check(darkest >= 90,
      f"and no darker: {darkest}/255 against the question text above it",
      f"the rule prints at {darkest}/255, dark enough to compete with the "
      "question text. It is a guide, not content")

print("\nTHE WORDMARK IS AT THE FOOT OF EVERY PAGE, OPPOSITE THE NUMBER")

FOOT = A4[1] - CHROME_MARGIN - 12
missing, misplaced, uncoloured = [], [], []
for i, page in enumerate(doc):
    spans = [s for b in page.get_text("dict")["blocks"]
             for l in b.get("lines", []) for s in l["spans"]
             if s["bbox"][1] > FOOT]
    folio = [s for s in spans if s["text"].strip() == "FOLIO"]
    ai = [s for s in spans if s["text"].strip() == "AI"]
    if i == 0:
        # The cover is one composition and carries no chrome at all; the
        # wordmark up there is part of the cover's own publisher lockup.
        continue
    if not folio or not ai:
        missing.append(i + 1)
        continue
    if abs(folio[0]["bbox"][0] - PAGE_MARGIN) > 2:
        misplaced.append((i + 1, round(folio[0]["bbox"][0], 1)))
    if abs(ai[0]["color"] - int(ACCENT_BLUE[1:], 16)) > 0:
        uncoloured.append((i + 1, f"#{ai[0]['color']:06X}"))
check(not missing,
      f"all {len(doc) - 1} pages after the cover carry the wordmark at the "
      "foot",
      f"these pages carry no wordmark: {missing}. It is where a publisher's "
      "imprint goes, and the one mark on an interior page that says who made "
      "the booklet")
check(not misplaced,
      "and it is set at the left margin, opposite the page number",
      f"these are not at the margin: {misplaced}")
check(not uncoloured,
      "with its AI in the cover's accent, the way the cover sets it",
      f"these print the AI in another colour: {uncoloured}")

print("\nTHE COVER'S WAVE CLOSES THE FRONT MATTER")

TONES = [tuple(round(c, 3) for c in (col.red, col.green, col.blue))
         for col, _ in C.INTERIOR_WAVES]


def waves_on(page):
    out = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill:
            continue
        rounded = tuple(round(c, 3) for c in fill)
        if any(max(abs(a - b) for a, b in zip(rounded, t)) < 0.01
               for t in TONES) and d["rect"].width > 300:
            out.append(d)
    return out


front = [i for i, page in enumerate(doc)
         if {"Contents", "How to use this booklet"} & set(
             page.get_text().splitlines())]
check(front, "the booklet has front matter to close",
      "no front matter page found, so nothing below is measured")
bare = [i + 1 for i in front if not waves_on(doc[i])]
check(not bare,
      f"both front matter pages end on the cover's wave motif "
      f"(pages {[i + 1 for i in front]})",
      f"these front matter pages end in nothing: {bare}. A designed short page "
      "with a third of a sheet of white under it reads as a page that failed, "
      "not one that finished")

print("\nIT IS HELD INSIDE THE TYPE AREA, AND IT COSTS ALMOST NO INK")

# Never bled to the trim. A home printer cannot print to the edge, and a reader
# who picks "fit to page" to keep the artwork rescales the whole sheet, which
# shrinks every ruled line the child writes on. This is the same rule
# CHROME_MARGIN exists for, one step further out.
outside = []
for i in front:
    for d in waves_on(doc[i]):
        r = d["rect"]
        if (r.x0 < PAGE_MARGIN - 1 or r.x1 > A4[0] - PAGE_MARGIN + 1
                or r.y1 > A4[1] - PAGE_MARGIN + 1):
            outside.append((i + 1, [round(v, 1) for v in (r.x0, r.y0, r.x1,
                                                          r.y1)]))
check(not outside,
      "the band stops at the type area on every side",
      f"these waves run outside the margins: {outside}. Bled artwork on an "
      "interior page is either clipped by the printer or rescales the sheet")

worst = 0.0
for i in front:
    bands = waves_on(doc[i])
    if not bands:
        continue
    box = bands[0]["rect"]
    for d in bands[1:]:
        box |= d["rect"]
    pix = doc[i].get_pixmap(dpi=150, colorspace=pymupdf.csGRAY, clip=box)
    band = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,
                                                              pix.width)
    worst = max(worst, (255.0 - band.mean()) / 255.0)
check(worst <= 0.12,
      f"and the densest wave band covers {worst:.1%} of itself in ink, inside "
      "the 12% budget",
      f"the wave band costs {worst:.1%} coverage. These booklets are printed "
      "at home, often twice; decoration is not worth a cartridge")

doc.close()

if _failed:
    print(f"\n{len(_failed)} HOUSE STYLE CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} HOUSE STYLE CHECKS PASSED")
sys.exit(0)
