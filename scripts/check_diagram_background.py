"""Checks a diagram takes the colour of whatever it is printed on.

A matplotlib figure used to be saved on an opaque white canvas. On a plain
question page that is invisible. Inside the worked example, whose panel is
tinted #F4F7FB, it printed as a hard-edged white rectangle with a seam on all
four sides. Nothing else in the booklet has a seam like that, and it was the
clearest "assembled from parts" mark in the document: a page that otherwise
looks published, with a screenshot dropped into the middle of it.

The figures are saved with a transparent background instead, which is the only
one of the two available fixes that is safe with a CACHE. Diagrams are cached
on a hash of their spec, and the same spec is drawn both inside the tinted box
and on a white question page. Had the background been taken from the container
instead, the first caller would have decided what every later caller got, and
the seam would have come back inverted: a pale blue rectangle on white paper.
That is the case this file renders, both placements of one spec in one booklet,
and it asserts they are the same cached file AND that each one matches what it
sits on.

The cache key carries a render version for the same reason. A spec says nothing
about how it was drawn, so a warm cache would have gone on serving figures with
white rectangles baked into them long after the code stopped making them, on
exactly the deployed instance a paying customer generates from.

    PYTHONPATH=. python scripts/check_diagram_background.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image as PILImage
from reportlab.lib.units import cm

from booklet_gen.formatter import WE_FILL, render_pdf
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)
from booklet_gen.visuals import diagrams as D

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


SPEC = {"type": "cuboid", "length": 5, "width": 3, "height": 4, "unit": "cm"}

print("\nTHE FIGURE ITSELF CARRIES NO BACKGROUND OF ITS OWN")

png = D.render_diagram(SPEC)
check(png is not None and Path(png).exists(),
      f"the diagram rendered: {png}",
      "the diagram did not render at all, so nothing below is measuring "
      "anything")

with PILImage.open(png) as im:
    mode, size = im.mode, im.size
    rgba = im.convert("RGBA")
    alpha = np.array(rgba.getchannel("A"))
corners = np.concatenate([alpha[:6, :6].ravel(), alpha[:6, -6:].ravel(),
                          alpha[-6:, :6].ravel(), alpha[-6:, -6:].ravel()])
check(mode == "RGBA" and int(corners.max()) == 0,
      f"it is saved {mode} with its corners fully transparent, so one cached "
      "file is correct on any background",
      f"the diagram is saved {mode} with corner alpha up to "
      f"{int(corners.max())}/255. An opaque canvas is a white rectangle "
      "wherever the paper is not white")

print("\nTHE CACHE KEY KNOWS HOW THE FIGURE WAS DRAWN, NOT JUST WHAT OF")

first = D._cache_path(SPEC)
version = D.RENDER_VERSION
try:
    D.RENDER_VERSION = version + 1
    bumped = D._cache_path(SPEC)
finally:
    D.RENDER_VERSION = version
check(first != bumped,
      "bumping the render version moves the cache path, so a warm disk cannot "
      "serve figures drawn under the old rules",
      "the cache path is the same before and after a render version bump. A "
      "deployed instance with figures already on disk keeps serving them "
      "forever, and a fix to how diagrams are drawn never reaches the "
      "customers who already have a cache")

other = dict(SPEC, length=6)
check(D._cache_path(other) != first,
      "and two different specs still land on two different files",
      "two different specs share a cache path, so one diagram is served for "
      "another question")

print("\nBOTH PLACEMENTS OF ONE SPEC ARE THE SAME FILE, AND NEITHER SEAMS")


def vq(text, answer="42", difficulty="medium", image=None):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=answer,
                          difficulty=difficulty), verified=True,
        image_path=str(image) if image else None)


# The same figure twice: once inside the tinted worked-example panel, once on
# the white paper of a question page. One spec, one cache entry, two
# backgrounds. That is the case a context-dependent background gets wrong.
data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    program_label="Academic Accelerate",
    sections=[SubtopicOutput(
        topic="Volume", subtopic="Volume of a prism",
        teaching=SubtopicTeaching(
            intro_paragraphs=["Volume is the space inside a solid."],
            key_points=["Length times width times height."],
            worked_example=WorkedExample(
                question="The box is 5 cm by 3 cm by 4 cm. What is its volume?",
                steps=["Multiply the length by the width: 5 x 3 = 15.",
                       "Multiply that by the height: 15 x 4 = 60."],
                answer="60 cubic centimetres", image_path=str(png))),
        questions=[vq("Question 1: what is the volume of this box?",
                      image=png)]
        + [vq(f"Question {j}: a box is {j + 1} cm by 2 cm by 3 cm. What is "
              "its volume?") for j in range(2, 4)],
        homework_questions=[vq(f"Homework {j}: calculate {j + 1}/12 + "
                               f"{j + 1}/12.", difficulty="easy")
                            for j in range(3)],
        estimated_minutes=16)],
    recap_questions=[vq("Calculate 15 * 4 + 7.", difficulty="easy")],
    challenge_questions=[vq("A pool is 10 m by 5 m by 1.5 m. What is its "
                            "volume?", difficulty="hard")],
    recap_minutes=6, classwork_minutes=40, homework_minutes=20,
    challenge_minutes=12, total_minutes=78)

out = Path(tempfile.mkdtemp(prefix="folio-figbg-")) / "figures.pdf"
render_pdf(data, out)
print(f"  rendered {out}")

doc = pymupdf.open(out)
DPI = 300
S = DPI / 72
figures = []
for i, page in enumerate(doc):
    for info in page.get_image_info(hashes=True):
        rect = pymupdf.Rect(info["bbox"])
        # Matched by the source PNG's own pixel dimensions, so the mascots
        # printed elsewhere in the booklet cannot be mistaken for the figure.
        if (info["width"], info["height"]) == size:
            figures.append((i, rect, info["digest"]))

check(len(figures) >= 2,
      f"the same diagram is printed {len(figures)} times in the booklet",
      f"only {len(figures)} large figures printed, so the two placements this "
      "file exists to compare are not both on the page")

check(len({d for _, _, d in figures}) == 1,
      "and all of them are one cached file, embedded once",
      f"the placements resolved to {len({d for _, _, d in figures})} different "
      "images for one spec. Either the cache key grew a context it should not "
      "have, or the same figure is being drawn twice per booklet")

GREY = {}


def page_rgb(index):
    if index not in GREY:
        pix = doc[index].get_pixmap(dpi=DPI)
        GREY[index] = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, 3)
    return GREY[index]


def patch(index, x0, y0, x1, y1):
    a = page_rgb(index)
    return a[int(y0 * S):int(y1 * S), int(x0 * S):int(x1 * S)].reshape(-1, 3)


def corner_colour(index, rect, inset):
    """The median colour of the four corners of a figure, `inset` pt inside.

    The corners of a tight bounding box are padding, so this is the figure's
    own background. The median is used because a stray label can reach into
    one corner and must not be allowed to decide the answer.
    """
    size = 3.0
    got = []
    for x, y in ((rect.x0 + inset, rect.y0 + inset),
                 (rect.x1 - inset - size, rect.y0 + inset),
                 (rect.x0 + inset, rect.y1 - inset - size),
                 (rect.x1 - inset - size, rect.y1 - inset - size)):
        got.append(patch(index, x, y, x + size, y + size))
    return np.median(np.concatenate(got), axis=0)


def outside_colour(index, rect):
    """What the figure is sitting ON: the paper just left and right of it."""
    return np.median(np.concatenate([
        patch(index, rect.x0 - 6, rect.y0 + 4, rect.x0 - 2, rect.y1 - 4),
        patch(index, rect.x1 + 2, rect.y0 + 4, rect.x1 + 6, rect.y1 - 4)]),
        axis=0)


seams = []
for index, rect, _ in figures:
    inside = corner_colour(index, rect, 2.0)
    around = outside_colour(index, rect)
    drift = float(np.max(np.abs(inside - around)))
    if drift > 3:
        seams.append((f"page {index + 1}", f"figure {list(map(int, inside))} "
                                           f"on paper {list(map(int, around))}",
                      f"{drift:.0f} levels"))

check(not seams,
      f"every one of the {len(figures)} figures matches the paper it is "
      "printed on, in the tinted panel and on the white question page alike",
      f"these figures print on a background of their own: {seams}. A white "
      "rectangle with a hard edge inside a tinted panel is the most obvious "
      "sign in the booklet that the page was assembled rather than laid out")

# And the panel really is tinted, or the assertion above passes by everything
# being white.
tinted = [(i, r) for i, r, _ in figures
          if abs(float(outside_colour(i, r)[2])
                 - int(WE_FILL[5:7], 16)) < 4
          and float(outside_colour(i, r)[0]) < 250]
check(tinted,
      f"and one of them really is inside the tinted panel ({WE_FILL}), so the "
      "match above is a match to something",
      "no figure landed on the tinted worked-example panel, so this fixture "
      "cannot see the seam it exists for. Every figure is on white paper and "
      "the check passes for the wrong reason")

doc.close()

if _failed:
    print(f"\n{len(_failed)} DIAGRAM BACKGROUND CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} DIAGRAM BACKGROUND CHECKS PASSED")
sys.exit(0)
