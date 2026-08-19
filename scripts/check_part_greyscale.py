"""Checks the four parts of the booklet stay distinct on a mono home printer.

The Warm-up Recap, Class Work, Homework and the Final Challenge each get a
full-width band in their own colour, and until now the colour was the only thing
telling them apart. Most of these booklets are printed at home in black and
white, and measured off a 300dpi greyscale rendering the four came out at 56,
74, 107 and 113 out of 255. Class Work and Homework were eighteen points apart,
the Warm-up and the Final Challenge six. Eighteen out of 255 is nothing once
printer dot gain has closed it and six is not a difference at all, so a printed
booklet had two tones and not four, and the collapsed pair was the one a student
navigates between every session: the work done in the lesson and the work taken
home.

Two things fix it and this file asserts both, because either alone is a half
measure.

  * The luminances are spread deliberately. Every pair of bands is at least
    _PART_GREY_FLOOR apart once the page is converted to grey, measured off the
    rendered PDF rather than computed from the hex, and every band still carries
    white type at AA contrast. There is not much room to work in, because white
    type needs a dark ground, so the spread comes out around 27 points a step
    and cannot be pushed much further.
  * The meaning is no longer carried by tone at all. Each band prints a
    reversed-out notch marker at its left, one notch for the Warm-up through
    four for the Final Challenge, and its position in words at its right.
    Notches are the absence of ink, so they survive any conversion whatever, and
    they are readable at the distance a parent flips a printed stack from, which
    is further than eight point type works at.

    PYTHONPATH=. python scripts/check_part_greyscale.py
"""
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from reportlab.lib.units import cm

from booklet_gen import formatter as F
from booklet_gen.formatter import (PART_CHALLENGE, PART_CLASSWORK,
                                   PART_HOMEWORK, PART_RECAP,
                                   _PART_GREY_FLOOR, render_pdf)
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


PARTS = {"Warm-up Recap": PART_RECAP, "Class Work": PART_CLASSWORK,
         "Homework": PART_HOMEWORK, "Final Challenge": PART_CHALLENGE}
ORDER = list(PARTS)


def vq(text, answer="42", difficulty="easy"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer,
                          working="Work it through.", difficulty=difficulty),
        verified=True)


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Line the digits up by place value and work one column "
                      "at a time."],
    key_points=["Line the digits up.", "Work one column at a time."],
    worked_example=WorkedExample(question="What is 2 385 + 1 947?",
                                 steps=["Add the ones.", "Carry into the tens."],
                                 answer="4 332"))


def booklet(recap=True, challenge=True):
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(
            topic="Number", subtopic="Multiplying", teaching=TEACHING,
            questions=[vq(f"What is {30 + j} x 7?", answer=str(7 * (30 + j)))
                       for j in range(8)],
            homework_questions=[vq(f"Double {200 + j}.",
                                   answer=str(2 * (200 + j)))
                                for j in range(6)],
            estimated_minutes=10)],
        recap_questions=[vq("What is 15 x 4?", answer="60")] if recap else [],
        challenge_questions=([vq("What is 25 x 8?", answer="200")]
                             if challenge else []),
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


tmp = Path(tempfile.mkdtemp(prefix="folio-parts-"))
out = tmp / "parts.pdf"
render_pdf(booklet(), out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
MEASURE = 21 * cm - 2 * F.PAGE_MARGIN
INKS = {name: tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
        for name, c in PARTS.items()}

bands = {}
for i, page in enumerate(doc):
    for d in page.get_drawings():
        fill = d.get("fill")
        if not fill or d["rect"].width < 0.9 * MEASURE \
                or d["rect"].height < 1.5 * cm:
            continue
        for name, ink in INKS.items():
            if name not in bands and max(abs(a - b)
                                         for a, b in zip(fill, ink)) < 0.01:
                bands[name] = (i, d["rect"])

print("\nEVERY PART BAND IS ON THE PAGE")

check(set(bands) == set(PARTS),
      f"all four bands are drawn: {[b[0] + 1 for b in bands.values()]}",
      f"only found bands for {sorted(bands)}. The rest of this measures bands "
      "that have to exist first")

print("\nAND THEY PRINT AS FOUR DIFFERENT GREYS, NOT TWO")


def band_grey(name):
    """The band's tone at 300dpi in grey, sampled off the solid left edge.

    Measured off the rendered page rather than computed from the hex, because
    what matters is what comes out of the converter a printer driver actually
    runs, not what a luminance formula predicts. The sample is taken inside the
    band's left padding, which carries no type and no notch.
    """
    i, rect = bands[name]
    clip = pymupdf.Rect(rect.x0 + 3, rect.y0 + 3, rect.x0 + 9, rect.y0 + 9)
    pix = doc[i].get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, clip=clip)
    return int(np.median(np.frombuffer(pix.samples, dtype=np.uint8)))


greys = {name: band_grey(name) for name in bands}
print("   " + ", ".join(f"{n}: {g}/255" for n, g in greys.items()))

close = [(a, b, abs(greys[a] - greys[b]))
         for i, a in enumerate(ORDER) for b in ORDER[i + 1:]
         if a in greys and b in greys and abs(greys[a] - greys[b]) < _PART_GREY_FLOOR]
check(not close,
      f"every pair of bands is at least {_PART_GREY_FLOOR}/255 apart in "
      f"greyscale, the closest being "
      f"{min((abs(greys[a] - greys[b]), a, b) for i, a in enumerate(ORDER) for b in ORDER[i + 1:])[0]}",
      f"these pairs print as the same grey: {close} (part, part, points apart "
      f"out of 255). The floor is {_PART_GREY_FLOOR}. A booklet printed at home "
      "in black and white then has fewer tones than it has parts, and the pair "
      "that collapses is the one a student navigates between")


def contrast_with_white(hex_colour):
    def f(c):
        c = int(hex_colour[c:c + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * f(1) + 0.7152 * f(3) + 0.0722 * f(5)
    return 1.05 / (lum + 0.05)


faint = [(n, round(contrast_with_white(c), 2)) for n, c in PARTS.items()
         if contrast_with_white(c) < 4.5]
check(not faint,
      "and every band still carries its white heading at AA contrast: "
      + ", ".join(f"{n} {contrast_with_white(c):.1f}:1"
                  for n, c in PARTS.items()),
      f"these bands no longer carry white type legibly: {faint}. Spreading the "
      "tones apart is not worth doing by making a band too light to reverse "
      "type out of")

print("\nAND THE MEANING DOES NOT DEPEND ON TONE AT ALL")

# The notches are white bars knocked out of the band, so on the page they are
# runs of paper inside a solid rectangle. Counted by looking along the band's
# own height, in the strip at its left where they are drawn.
notch_counts = {}
for name, (i, rect) in bands.items():
    strip = pymupdf.Rect(rect.x0, rect.y0 + rect.height / 2 - 2,
                         rect.x0 + F._BAND_MARGIN, rect.y0 + rect.height / 2 + 2)
    pix = doc[i].get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, clip=strip)
    row = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,
                                                             pix.width)
    light = np.median(row, axis=0) > 200
    runs = sum(1 for k in range(1, len(light))
               if light[k] and not light[k - 1]) + (1 if light[0] else 0)
    notch_counts[name] = runs

check(all(notch_counts.get(n) == i + 1 for i, n in enumerate(ORDER)),
      "each band carries its own count of reversed-out notches: "
      + ", ".join(f"{n} {notch_counts.get(n)}" for n in ORDER),
      f"the notch counts came out as {notch_counts}, wanted "
      f"{ {n: i + 1 for i, n in enumerate(ORDER)} }. The notches are the part "
      "of this that does not care what the printer does to colour, and they "
      "are the part a parent can read while flipping a stack of paper")

# Read out of the band's own rectangle, not the page: the Warm-up and Class
# Work bands usually land on the same sheet, and matching by page would let
# either one's locator stand in for the other's.
locators = {}
for name, (i, rect) in bands.items():
    for block in doc[i].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(sp["text"] for sp in line["spans"]).strip()
            x0, y0, _, y1 = line["bbox"]
            if not (rect.y0 <= y0 and y1 <= rect.y1 and rect.x0 <= x0):
                continue
            m = re.fullmatch(r"PART (\d+) OF (\d+)", text)
            if m:
                locators.setdefault(name, []).append(m.group(0))
check(all(locators.get(n) == [f"PART {i + 1} OF 4"]
          for i, n in enumerate(ORDER)),
      "and says where it is in words as well: "
      + ", ".join(f"{n} {locators.get(n)}" for n in ORDER),
      f"the part locators came out as {locators}. The notches say the same "
      "thing but have to be counted; the words are what makes them readable "
      "without a legend")

print("\nA BOOKLET WITH FEWER PARTS COUNTS ITS OWN")

# The count is of the parts this booklet actually has. A booklet with no
# Warm-up must not open on "Part 2 of 4" and leave a parent looking for Part 1.
short = tmp / "short.pdf"
render_pdf(booklet(recap=False, challenge=False), short)
sdoc = pymupdf.open(short)
stext = " ".join(" ".join(p.get_text().split()) for p in sdoc)
found = sorted(set(re.findall(r"PART \d+ OF \d+", stext)))
sdoc.close()
check(found == ["PART 1 OF 2", "PART 2 OF 2"],
      f"a booklet with only Class Work and Homework numbers them {found}",
      f"a booklet with only Class Work and Homework printed {found}. A parent "
      "reading 'Part 2 of 4' on the first band in the booklet goes looking for "
      "a Part 1 that was never generated")

print("\nAND THE NOTCHES COST NOTHING, BECAUSE THEY ARE NOT INK")

i, rect = bands["Final Challenge"]
full = doc[i].get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, clip=rect)
grey = np.frombuffer(full.samples, dtype=np.uint8)
coverage = (255.0 - grey.mean()) / 255.0
solid = 1.0 - band_grey("Final Challenge") / 255.0
check(coverage <= solid,
      f"the band with four notches covers {coverage:.0%} against {solid:.0%} "
      "for the same rectangle solid, so the marker is paper the band gave back",
      f"the notched band covers {coverage:.0%} against {solid:.0%} solid. The "
      "notches are supposed to be the absence of ink; anything that costs more "
      "than the plain band has been drawn the wrong way round")

doc.close()

if _failed:
    print(f"\n{len(_failed)} PART GREYSCALE CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} PART GREYSCALE CHECKS PASSED")
sys.exit(0)
