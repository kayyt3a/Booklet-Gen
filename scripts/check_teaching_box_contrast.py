"""Checks the box the child reads does not look like the box they write in.

A mini-lesson prints two boxes. "Paulio shows you first" is a worked example:
every value is filled in, the child reads it and writes nothing. "Now let's try
one together" is guided: it has gaps in it, and the child is meant to write in
those gaps. They are functionally opposite.

They used to be identical. Same tint, same border weight, same border colour,
same width, same mascot, stacked two millimetres apart so the pair read as one
slab with a line across it. A child looking at the page cannot see which one is
theirs, and the most common way that fails is the worst one: they write in
neither, because the whole block looks like something to read.

The worked example keeps the tint, because a filled panel is what "read this"
looks like. The guided box is turned inside out instead of merely retinted:
white paper inside, a dashed rule around it, and a heavy solid rule down the
left edge. That is deliberate. Nearly every one of these booklets is printed on
a mono home printer, and a second tint is a colour difference: two pale fills
that are eight grey levels apart are the same box in grey. A dash pattern is
geometry and survives any printer, and the left rule is dark enough to rank
against the other box's hairline. So this file measures the difference in
GREYSCALE, off the rendered page, and does not accept the fills alone as the
answer.

    PYTHONPATH=. python scripts/check_teaching_box_contrast.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from reportlab.lib.units import cm

from booklet_gen.formatter import (PENCIL_GLYPH, TEACHING_BOX_GAP, render_pdf)
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


# Short on purpose: one intro sentence, one key point, a two-step worked
# example and one guided example, so both boxes land on the same page and the
# gap between them can be measured rather than inferred.
def booklet(year="Year 5"):
    return BookletData(
        subject="Mathematics", year_level=year, student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(
            topic="Volume", subtopic="Volume of a prism",
            teaching=SubtopicTeaching(
                intro_paragraphs=["Volume is the space inside a solid."],
                key_points=["Length times width times height."],
                worked_example=WorkedExample(
                    question="A box is 5 cm by 3 cm by 4 cm. What is its "
                             "volume?",
                    steps=["Multiply the length by the width: 5 x 3 = 15.",
                           "Multiply that by the height: 15 x 4 = 60."],
                    answer="60 cubic centimetres"),
                guided_examples=[WorkedExample(
                    question="Find the volume of a prism 2 cm by 2 cm by 3 cm.",
                    steps=["Multiply the length and width: [[4]].",
                           "Multiply by the height: [[12]]."],
                    answer="[[12]] cubic cm")]),
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


tmp = Path(tempfile.mkdtemp(prefix="folio-boxes-"))
out = tmp / "boxes.pdf"
render_pdf(booklet(), out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
DPI = 300
S = DPI / 72
MEASURE = 10 * cm


def find_box(document, needle):
    """(page index, rect) of the teaching box whose label contains `needle`.

    Found through the label rather than the fill, so this keeps working
    whatever either box is filled with, which is the thing under test.
    """
    for i, page in enumerate(document):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(s["text"] for s in line["spans"])
                if needle not in text:
                    continue
                boxes = [d["rect"] for d in page.get_drawings()
                         if d["rect"].width > MEASURE
                         and d["rect"].y0 <= line["bbox"][1] + 2
                         and d["rect"].y1 >= line["bbox"][3] - 2]
                if boxes:
                    return i, min(boxes, key=lambda r: r.height)
    return None, None


read_page, read_box = find_box(doc, "shows you first")
write_page, write_box = find_box(doc, "try one together")

check(read_box is not None and write_box is not None,
      "both teaching boxes were found on the page",
      f"a teaching box is missing from the render: worked={read_box}, "
      "guided={write_box}. Everything below measures those two boxes")
if read_box is None or write_box is None:
    print(f"\n{len(_failed)} TEACHING BOX CHECKS FAILED")
    sys.exit(1)

print("\nTHEY DO NOT TOUCH")

check(read_page == write_page,
      f"both boxes print on page {read_page + 1} in this fixture, so the gap "
      "between them is the gap a reader sees",
      f"the two boxes landed on pages {read_page + 1} and {write_page + 1}, so "
      "this fixture no longer measures the gap. Shorten it until they share a "
      "page")
gap = write_box.y0 - read_box.y1
check(gap >= 0.4 * cm,
      f"there is {gap / cm:.2f}cm between the reading box and the writing box "
      f"(the formatter sets {TEACHING_BOX_GAP / cm:.2f}cm)",
      f"only {gap / cm:.2f}cm separates the two boxes. At that distance two "
      "boxes of the same width read as one block with a rule across it, and "
      "the child never sees that the second one is a different kind of thing")

print("\nTHEY ARE TOLD APART IN GREYSCALE, NOT BY COLOUR")

pix = doc[read_page].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def band(rect, y0, y1, x0=None, x1=None):
    return grey[int(y0 * S):int(y1 * S),
                int((rect.x0 if x0 is None else x0) * S):
                int((rect.x1 if x1 is None else x1) * S)]


def interior(rect):
    """The paper inside a box. The median is the fill: type is a minority."""
    return int(np.median(band(rect, rect.y0 + 3, rect.y1 - 3,
                              rect.x0 + 4, rect.x1 - 4)))


read_fill, write_fill = interior(read_box), interior(write_box)
check(read_fill <= 250 and write_fill >= 252,
      f"the reading box is a tinted panel ({read_fill}/255) and the writing "
      f"box is white paper ({write_fill}/255)",
      f"the two fills print at {read_fill}/255 and {write_fill}/255 in grey. "
      "The reading box has to stay a panel and the writing box has to stay "
      "paper, or the only difference left is the border")


def dash_runs(rect):
    """Runs of ink along a box's top border: 1 if solid, many if dashed."""
    strip = band(rect, rect.y0 - 1.2, rect.y0 + 1.2,
                 rect.x0 + 6, rect.x1 - 6).min(axis=0) < 200
    return int(np.sum(strip[1:] & ~strip[:-1]) + (1 if strip[0] else 0))


read_runs, write_runs = dash_runs(read_box), dash_runs(write_box)
check(read_runs <= 2 and write_runs >= 15,
      f"the reading box is bordered by a solid rule ({read_runs} run of ink "
      f"across the top) and the writing box by a dashed one ({write_runs} "
      "runs). A dash pattern is geometry, so it survives any printer",
      f"the top borders measure {read_runs} and {write_runs} runs of ink. If "
      "both are solid the boxes differ only in a pale fill, and eight grey "
      "levels apart is the same box on a mono printer")


def edge_ink(rect):
    """The darkest ink in the two points either side of a box's left edge."""
    return int(band(rect, rect.y0 + 4, rect.y1 - 4,
                    rect.x0 - 2, rect.x0 + 2).min())


read_edge, write_edge = edge_ink(read_box), edge_ink(write_box)
check(write_edge <= 110 and read_edge - write_edge >= 60,
      f"the writing box carries a heavy spine down its left edge "
      f"({write_edge}/255) against the reading box's hairline ({read_edge}"
      "/255), a separation of "
      f"{read_edge - write_edge} grey levels",
      f"the left edges print at {read_edge}/255 and {write_edge}/255, "
      f"{read_edge - write_edge} levels apart. The spine is the cue that "
      "carries at arm's length, before any of the type is read")

print("\nTHE LABEL SAYS WHICH ONE THE CHILD WRITES IN")

labels = {}
for page in doc:
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            for key in ("shows you first", "try one together", "Completed"):
                if key in text:
                    labels.setdefault(key, text)

check(PENCIL_GLYPH in labels.get("try one together", ""),
      f"the guided label opens with a pencil: {labels.get('try one together')!r}",
      f"the guided label is {labels.get('try one together')!r}, with no pencil "
      "on it. The border tells a parent the two boxes are different; the "
      "pencil tells a seven year old which one to pick up a pencil for")

check(PENCIL_GLYPH not in labels.get("shows you first", ""),
      "and the worked example carries none, because nothing in it is written",
      f"the worked example is labelled {labels.get('shows you first')!r}. A "
      "pencil on the box that is already finished points the child at the "
      "wrong box")

check(PENCIL_GLYPH not in labels.get("Completed", "Completed"),
      "and neither does the completed copy in the answer key",
      "the answer key's completed copy carries a pencil. There is nothing "
      "left to write in it: it is the version with the gaps filled")

print("\nAND IT ALL WORKS WITH NO MASCOT IN THE BOX")

# Paulio stops at Year 6. Above it the two boxes carry no icon at all, so the
# treatment is the only thing distinguishing them and it has to stand alone.
senior_path = tmp / "senior.pdf"
render_pdf(booklet("Year 9"), senior_path)
senior = pymupdf.open(senior_path)
s_read_page, s_read = find_box(senior, "Watch first")
s_write_page, s_write = find_box(senior, "do this one together")
if s_read is None or s_write is None:
    bad(f"a teaching box is missing from the Year 9 render: {s_read} {s_write}")
else:
    pix = senior[s_write_page].get_pixmap(dpi=DPI, colorspace=pymupdf.csGRAY)
    grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,
                                                              pix.width)
    runs, edge = dash_runs(s_write), edge_ink(s_write)
    check(runs >= 15 and edge <= 110,
          f"a Year 9 booklet's guided box is dashed ({runs} runs) and spined "
          f"({edge}/255) with no mascot anywhere near it",
          f"the Year 9 guided box measures {runs} runs and {edge}/255 at the "
          "edge. Above Year 6 there is no mascot in either box, so the "
          "treatment is the only thing telling them apart")
senior.close()

doc.close()

if _failed:
    print(f"\n{len(_failed)} TEACHING BOX CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} TEACHING BOX CHECKS PASSED")
sys.exit(0)
