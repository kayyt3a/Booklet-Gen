"""Checks Paulio is set into the teaching box rather than dropped on it.

Paulio is the one piece of character in a printed booklet, and in the taught
boxes he was pasted into the corner rather than laid out with the type. Three
things were measured on a Year 5 fixture, and each of them is what a parent
means when they say a page looks assembled rather than designed.

  * His feet cleared the cap-height of the line below by 2.6pt, nine tenths of
    a millimetre. At that distance the ink of the bear and the ink of the first
    word are one mark on a printed page: the mascot looks like it is standing
    on the sentence.
  * The box stepped left three times. The mascot sat at the box's left padding,
    the label was pushed 1.25cm in to clear him, and the question line went
    back to the padding, under his feet. Three left edges inside one bordered
    box is the thing that reads as a sticker: nothing lines up with anything.
    Everything the box says now hangs off the LABEL's edge, and the mascot is
    the only element outside it, which is what a marginal figure is.
  * One pose, at one size, in the same corner, about ten times a booklet. The
    fix is not variety for its own sake: the pose now says which box it is.
    "Paulio shows you first" gets him presenting, "Now let's try one together"
    gets him at a desk with a pencil, because that box is the one the child
    writes in. Two poses that mean something, both from artwork the product
    already ships.

The vertical centring of the label against the mascot is asserted here too. It
was NOT broken when this was written (it measured centred to a fifth of a
point, because the header row was already VALIGN MIDDLE), and it is pinned so
that the padding added under the lockup cannot quietly push the label off
centre later.

    PYTHONPATH=. python scripts/check_mascot_lockup.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from reportlab.lib.units import cm

from booklet_gen.formatter import (PAULIO_ICON_SIZE, _PAULIO_GUIDED_ICON_PATH,
                                   _PAULIO_ICON_PATH, render_pdf)
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
# The fixture: a Year 5 booklet, which is inside the range Paulio narrates, so
# both taught boxes carry him. One subtopic's worked example holds a diagram,
# because the figure has to keep the same left edge as the type around it or
# the box is stepping again one element lower down.
# ---------------------------------------------------------------------------

def vq(text, answer="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=answer,
                          difficulty=difficulty), verified=True)


def teaching(marker, diagram=None):
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the amount of space inside a three "
                          "dimensional object."],
        key_points=["Volume equals length times width times height."],
        worked_example=WorkedExample(
            question=f"The {marker} box is 5 cm by 3 cm by 4 cm. What is its "
                     "volume?",
            steps=["Multiply the length by the width: 5 x 3 = 15.",
                   "Multiply that result by the height: 15 x 4 = 60."],
            answer="60 cubic centimetres",
            image_path=str(diagram) if diagram else None),
        guided_examples=[WorkedExample(
            question="Find the volume of a prism 2 cm by 2 cm by 3 cm.",
            steps=["Multiply the length and width: [[4]].",
                   "Multiply by the height: [[12]]."],
            answer="[[12]] cubic cm")])


def booklet(diagram):
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(
            topic="Volume", subtopic="Volume of a prism",
            teaching=teaching("Numbat", diagram),
            questions=[vq(f"Question {j}: a box is {j + 2} cm by 2 cm by 3 cm. "
                          "What is its volume?") for j in range(3)],
            homework_questions=[vq(f"Homework {j}: calculate {j + 1}/12 + "
                                   f"{j + 1}/12.", difficulty="easy")
                                for j in range(3)],
            estimated_minutes=16)],
        recap_questions=[vq("Calculate 15 * 4 + 7.", difficulty="easy")],
        challenge_questions=[vq("A pool is 10 m by 5 m by 1.5 m. What is its "
                                "volume?", difficulty="hard")],
        recap_minutes=6, classwork_minutes=40, homework_minutes=20,
        challenge_minutes=12, total_minutes=78)


from booklet_gen.visuals.diagrams import render_diagram      # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="folio-mascot-"))
diagram = render_diagram({"type": "cuboid", "length": 5, "width": 3,
                          "height": 4, "unit": "cm"})
out = tmp / "mascot.pdf"
render_pdf(booklet(diagram), out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
DPI = 600
S = DPI / 72

# Every page that carries a mascot, with that page rasterised once in grey so
# the ink can be measured rather than the bounding boxes trusted. A PNG's box
# includes its transparent margin; the bear's feet are where the ink stops.
LOCKUPS = []
for i, page in enumerate(doc):
    for info in page.get_image_info(hashes=True):
        rect = pymupdf.Rect(info["bbox"])
        if abs(rect.width - PAULIO_ICON_SIZE) > 6 and \
                abs(rect.height - PAULIO_ICON_SIZE) > 6:
            continue        # the finish-page mascot and the cover, not a lockup
        LOCKUPS.append((i, rect, info.get("digest")))

check(len(LOCKUPS) >= 2,
      f"{len(LOCKUPS)} taught boxes in the booklet carry Paulio",
      f"only {len(LOCKUPS)} mascot found in the taught boxes. The rest of this "
      "file measures those lockups, so with none of them it is measuring "
      "nothing")

GREY = {}


def grey(index):
    if index not in GREY:
        pix = doc[index].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
        GREY[index] = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width)
    return GREY[index]


def ink_rows(index, rect, threshold=170):
    """(top, bottom) of the ink inside `rect`, in points, or None."""
    g = grey(index)
    sub = g[int(rect.y0 * S):int(rect.y1 * S), int(rect.x0 * S):int(rect.x1 * S)]
    rows = np.where((sub < threshold).any(axis=1))[0]
    if not len(rows):
        return None
    return rect.y0 + rows.min() / S, rect.y0 + rows.max() / S


def lines_of(index):
    out = []
    for block in doc[index].get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                out.append((line["bbox"], text, line["spans"][0]))
    return out


print("\nHIS FEET DO NOT STAND ON THE LINE BELOW HIM")

# 0.2cm is the pad now set under the lockup. The floor asserted is 0.15cm,
# just over half a millimetre of it, because the question below can only ever
# start LOWER than the pad puts it: this is a floor, not a target.
CLEARANCE_FLOOR = 0.15 * cm
tight = []
for index, rect, _ in LOCKUPS:
    feet = ink_rows(index, rect)
    if feet is None:
        continue
    below = [b for b, t, s in lines_of(index) if b[1] > rect.y1 - 4
             and b[0] < rect.x0 + 14 * cm]
    if not below:
        continue
    top = min(b[1] for b in below)
    # The text bbox's top is the font's ascender line, which for DejaVu sits
    # above the capitals. The ink is measured instead, in the band the line
    # occupies, so this is the distance a reader actually sees.
    band = pymupdf.Rect(rect.x0, feet[1] + 0.5, rect.x0 + 14 * cm, top + 14)
    line_ink = ink_rows(index, band, threshold=128)
    gap = (line_ink[0] - feet[1]) if line_ink else 99.0
    if gap < CLEARANCE_FLOOR:
        tight.append((f"page {index + 1}", f"{gap / cm * 10:.2f}mm"))

check(not tight,
      f"the mascot clears the line under him by at least "
      f"{CLEARANCE_FLOOR / cm * 10:.1f}mm in every taught box",
      f"the mascot's feet come within {tight} of the ink of the line below. "
      "Under a millimetre the two are one mark on a printed page, and the "
      "bear reads as standing on the sentence rather than introducing it")

print("\nTHE BOX HAS ONE LEFT EDGE, AND THE MASCOT HANGS OUTSIDE IT")

steps = []
for index, rect, _ in LOCKUPS:
    label = min((b for b, t, s in lines_of(index)
                 if abs(b[1] - rect.y0) < PAULIO_ICON_SIZE and b[0] > rect.x1 - 2),
                key=lambda b: b[1], default=None)
    if label is None:
        steps.append((f"page {index + 1}", "no label found beside the mascot"))
        continue
    edge = label[0]
    # Everything printed inside the box, below the lockup. The box is the
    # drawn rectangle the mascot sits in.
    boxes = [d["rect"] for d in doc[index].get_drawings()
             if d["rect"].width > 10 * cm and d["rect"].y0 <= rect.y0 + 2
             and d["rect"].y1 >= rect.y1 - 2]
    if not boxes:
        steps.append((f"page {index + 1}", "the mascot is not inside any box"))
        continue
    box = max(boxes, key=lambda r: r.height)
    outside = sorted({round(b[0], 1) for b, t, s in lines_of(index)
                      if b[1] > rect.y1 - 4 and b[3] < box.y1 + 2
                      and b[0] < edge - 0.6})
    if outside:
        steps.append((f"page {index + 1}", f"lines start at {outside} against "
                                           f"the label's {edge:.1f}"))
    figures = [pymupdf.Rect(i["bbox"]) for i in doc[index].get_image_info()
               if i["bbox"][1] > rect.y1 - 4 and i["bbox"][3] < box.y1 + 2]
    if any(f.x0 < edge - 0.6 for f in figures):
        steps.append((f"page {index + 1}", "a figure inside the box starts "
                                           "left of the label's edge"))

check(not steps,
      f"every line and figure in the taught boxes starts at the label's edge, "
      f"across {len(LOCKUPS)} boxes",
      f"the taught box steps left below the lockup: {steps}. The mascot at the "
      "padding, the label indented to clear him and the question back at the "
      "padding is three left edges in one bordered box, and it is what makes "
      "the whole lockup read as dropped on rather than set in")

print("\nTHE LABEL IS CENTRED ON THE MASCOT, NOT HUNG OFF HIS BELLY")

off = []
for index, rect, _ in LOCKUPS:
    feet = ink_rows(index, rect)
    label = min((b for b, t, s in lines_of(index)
                 if abs(b[1] - rect.y0) < PAULIO_ICON_SIZE and b[0] > rect.x1 - 2),
                key=lambda b: b[1], default=None)
    if feet is None or label is None:
        continue
    ink = ink_rows(index, pymupdf.Rect(label[0], label[1] - 2, label[2],
                                       label[3] + 2), threshold=128)
    if ink is None:
        continue
    drift = (ink[0] + ink[1]) / 2 - (feet[0] + feet[1]) / 2
    if abs(drift) > 2.0:
        off.append((f"page {index + 1}", f"{drift:.1f}pt"))

check(not off,
      "the label's ink is centred on the mascot's ink in every lockup",
      f"the label sits off the mascot's centre by {off}. A label hung off the "
      "top or the bottom of a figure reads as two things that happen to be "
      "next to each other")

print("\nTHE POSE SAYS WHICH BOX IT IS")

check(_PAULIO_ICON_PATH.exists() and _PAULIO_GUIDED_ICON_PATH.exists(),
      "both poses exist on disk, in the artwork the product already ships",
      f"a pose is missing: {_PAULIO_ICON_PATH} / {_PAULIO_GUIDED_ICON_PATH}. "
      "The booklet falls back to no mascot at all in that box")

check(_PAULIO_ICON_PATH != _PAULIO_GUIDED_ICON_PATH,
      "the worked example and the guided box are drawn from different artwork",
      "both taught boxes use the same pose. The same figure at the same size "
      "in the same corner ten times a booklet is a sticker, and the child gets "
      "no signal from it about which box is which")

poses = {d for _, _, d in LOCKUPS if d}
check(len(poses) >= 2,
      f"{len(poses)} distinct poses are actually embedded in the rendered "
      f"booklet across {len(LOCKUPS)} lockups",
      f"only {len(poses)} distinct pose is embedded in the PDF across "
      f"{len(LOCKUPS)} lockups, so the two boxes print identical mascots "
      "however the source is configured")

print("\nAND NONE OF IT REACHES A SECONDARY BOOKLET")

# Paulio does not narrate above Year 6, so no lockup, no indent, and nothing
# above hangs on him being there.
senior = booklet(None)
senior.year_level = "Year 9"
senior_path = tmp / "senior.pdf"
render_pdf(senior, senior_path)
other = pymupdf.open(senior_path)
icons = [i for page in other for i in page.get_image_info()
         if abs(i["bbox"][2] - i["bbox"][0] - PAULIO_ICON_SIZE) < 6]
other.close()
check(not icons,
      "a Year 9 booklet still prints its worked examples with no mascot at all",
      f"{len(icons)} mascot icons printed in a Year 9 booklet. A bear cub "
      "introducing the worked example is the booklet a tutor shows a paying "
      "parent")

doc.close()

if _failed:
    print(f"\n{len(_failed)} MASCOT LOCKUP CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} MASCOT LOCKUP CHECKS PASSED")
sys.exit(0)
