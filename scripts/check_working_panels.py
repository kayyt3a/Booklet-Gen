"""Checks that a practice question is given a designed working area.

Every question in a printed booklet used to be a bold number, one line of
text, a blank gap, and a 0.6pt hairline labelled "Answer:". Five of those to a
page for eight pages. A parent paying fifty dollars for a booklet reads that
gap as an accident: nothing on the page says the space is an allocation, how
much working is expected in it, or where the working stops and the answer
starts. The booklet already knew how to do better, because an extended
response gets real ruled lines (`written_response_rules`) and those questions
look published while the maths ones look like leftover margin. The working
panel extends that same pattern to every question.

Four properties have to stay true, and each of them is a defect a customer
would see if it broke.

  * There is a panel at all, under every question that gets an "Answer:" rule.
  * Maths gets squared paper and writing gets ruled lines. Ruled lines at
    7.5mm pitch are no use for a column subtraction, and a grid under "write
    two sentences" is a child writing across squares.
  * The panel is feint. It has to be visible on a home printer in GREYSCALE,
    which is how most of these are printed, without competing with the
    question text, and it has to cost close to nothing in ink: a page of
    squares that drinks a cartridge is a page nobody prints twice.
  * The same question type gets the same room wherever it lands on the page.
    An inconsistent gap is forgivable because nobody can see what it was meant
    to be. An inconsistent panel is obvious: the grid stops early and the page
    looks misprinted. So the working area may be squeezed by at most a
    quarter, and a question that still does not fit moves to the next page.

    PYTHONPATH=. python scripts/check_working_panels.py
"""
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from reportlab.lib.units import cm

from booklet_gen import formatter as F
from booklet_gen.formatter import (WorkingSpace, _WORKING_SHRINK_FLOOR,
                                   render_pdf, working_panel)
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


def q(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


print("\nEACH SUBJECT GETS THE PANEL ITS WORKING NEEDS")

CASES = [
    ("Mathematics", "What is 28 x 3?", "grid",
     "arithmetic is laid out down and across the page, so it gets squares"),
    ("Mathematics Methods", "Differentiate y = 3x^2.", "grid",
     "senior maths is still maths"),
    ("Numeracy and Literacy", "What is 28 x 3?", "grid",
     "a NAPLAN booklet mixes both subjects, and a grid can be written on "
     "while 7.5mm rules cannot be calculated on"),
    ("English", "Write the plural of 'box'.", "rules",
     "writing goes along a line"),
    ("English", "Which word is a synonym for 'enormous'?", "rules", "as above"),
    ("Mathematics", "Draw a place value chart for 5 214.", "none",
     "ruling a space someone has to sketch in is worse than leaving it blank"),
    ("Mathematics", "Sketch the graph of y = 2x.", "none", "as above"),
]
for subject, text, want, why in CASES:
    got = working_panel(subject, Question(question=text, answer="", working=""))
    if got != want:
        bad(f"{subject}: {text!r} got a {got!r} panel, wanted {want!r}: {why}")
ok("maths gets squares, writing gets lines, and a drawing gets clear paper")

# An extended response already had ruled lines before any of this existed.
# That is the pattern being extended, so it must not be replaced by a grid.
prose = Question(question="Explain how you know 2 059 is larger than 1 999.",
                 answer="It has 2 thousands, and 2 beats 1.", working="")
assert working_panel("Mathematics", prose) == "rules", (
    "an explain question in a maths booklet was given squared paper. It "
    "earns full ruled lines from written_response_rules, and those lines are "
    "its panel; a grid over the top of them is two rulings for one answer")
ok("an extended response keeps the ruled lines it already had")

print("\nTHE WORKING AREA IS NEVER SQUEEZED BY MORE THAN A QUARTER")

for height_cm, labels in ((1.6, ["Answer:"]), (3.2, ["Answer:"]),
                          (2.2, ["a) Answer:", "b) Answer:"])):
    ws = WorkingSpace(height_cm * cm, labels, panel="grid")
    working_full = ws.height - ws.answers_height
    working_min = ws.min_height - ws.answers_height
    ratio = working_min / working_full
    assert ratio >= _WORKING_SHRINK_FLOOR - 1e-9, (
        f"a {height_cm}cm working area can be squeezed to {ratio:.0%} of "
        "itself. The same question type then prints with a visibly different "
        "panel depending on where it lands on the page")
    assert ws.min_height >= ws.answers_height, (
        "the answer rule's own strip was squeezed into, so the rule would be "
        "drawn on top of the grid")
ok(f"the working area keeps at least {_WORKING_SHRINK_FLOOR:.0%} of its room")

print("\nA RENDERED BOOKLET DRAWS THEM")

# Enough questions, in enough shapes, that some of them land at the foot of a
# page and have to be squeezed or moved. That is where the inconsistency this
# check exists for used to show up.
#
# The question TEXT varies in length, which is what moves the page boundaries
# around underneath them, and the difficulty tags vary, which is what each
# question's entitlement is computed from further down.
def filler(i):
    return " ".join(["work it out carefully"] * (i % 4))


DIFFICULTIES = ["easy", "medium", "hard"]
maths = [q(f"Question {i}: what is {i} x 37? {filler(i)}",
           difficulty=DIFFICULTIES[i % 3]) for i in range(21)]
english = [q(f"Item {i}: write the plural of 'box{i}'. {filler(i)}",
             difficulty=DIFFICULTIES[i % 3]) for i in range(9)]
teaching = SubtopicTeaching(
    intro_paragraphs=["Start at the biggest place value and work right."],
    key_points=["Left to right."],
    worked_example=WorkedExample(question="Which is larger, 3 048 or 3 084?",
                                 steps=["Compare the tens."], answer="3 084"))
data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    program_label="Academic Accelerate",
    sections=[
        SubtopicOutput(topic="Number and Place Value", subtopic="Ordering",
                       subject="Mathematics", teaching=teaching,
                       questions=maths[:12], homework_questions=maths[12:]),
        SubtopicOutput(topic="Reading and Vocabulary", subtopic="Plurals",
                       subject="English", teaching=teaching,
                       questions=english[:5], homework_questions=english[5:]),
    ])
out = Path(tempfile.mkdtemp(prefix="folio-panels-")) / "panels.pdf"
render_pdf(data, out)
print(f"  rendered {out}")

INK = tuple(round(int(F._PANEL_INK[i:i + 2], 16) / 255, 3) for i in (1, 3, 5))
# The darker ink the "Answer:" rule itself is stroked in.
RULE_INK = tuple(round(int("#9AA6B8"[i:i + 2], 16) / 255, 3) for i in (1, 3, 5))


def panel_paths(page):
    """(rects, strokes) drawn in the panel's feint ink on this page."""
    rects, strokes = [], []
    for d in page.get_drawings():
        colour = d.get("color")
        if not colour or max(abs(c - i) for c, i in zip(colour, INK)) > 0.01:
            continue
        kinds = {item[0] for item in d["items"]}
        (rects if "re" in kinds else strokes).append(d)
    return rects, strokes


doc = pymupdf.open(out)
key_page = next(i for i, p in enumerate(doc) if "Worked Solutions" in p.get_text())
body = list(doc)[1:key_page]

panels = [(i + 2, r) for i, page in enumerate(body)
          for r in panel_paths(page)[0]]
squared = [(n, r) for n, r in panels
           if any(s["rect"].width < 1 and r["rect"].y0 <= s["rect"].y0 <= r["rect"].y1
                  for s in panel_paths(body[n - 2])[1])]
assert len(panels) >= 20, (
    f"only {len(panels)} working panels in the whole booklet. A question with "
    "no panel is back to a bare gap with a hairline under it")
ok(f"{len(panels)} questions carry a drawn working panel")

for page in body:
    for d in panel_paths(page)[0] + panel_paths(page)[1]:
        assert d["width"] <= 0.26, (
            f"a panel line is {d['width']}pt. The panel has to stay lighter "
            "than the answer rule, or the ruling competes with the question")
ok("every panel line is drawn at 0.25pt")

# Squares on the maths pages, lines on the English ones. Told apart by whether
# any stroke in the panel is vertical.
maths_pages, writing_pages = [], []
for n, page in enumerate(body, start=2):
    rects, strokes = panel_paths(page)
    if not rects:
        continue
    vertical = [s for s in strokes if s["rect"].width < 1]
    (maths_pages if vertical else writing_pages).append(n)
assert maths_pages, "no page carries a squared panel, so maths lost its grid"
assert writing_pages, (
    "no page carries a ruled panel, so the English questions were given "
    "squared paper to write sentences on")
ok(f"squares on pages {maths_pages[:4]}..., ruled lines on {writing_pages[:4]}...")

print("\nTHE PANEL IS THE SAME SIZE WHEREVER THE QUESTION LANDS")

# Each panel is matched back to the question printed directly above it, and
# measured against what that question is entitled to anywhere on the page.
# Matching by question rather than comparing panels to each other is the point:
# an easy question and a hard one are MEANT to differ, and the defect is the
# same question getting different room in two places.
BAND = F._ANSWER_LINE_CM * cm            # the answer rule's own strip
tagged = {f"{kind} {i}": vq.question
          for kind, group in (("Question", maths), ("Item", english))
          for i, vq in enumerate(group)}
squeezed = []
matched = 0
for n, panel in panels:
    page = body[n - 2]
    top = panel["rect"].y0
    above = " ".join(s["text"] for b in page.get_text("dict")["blocks"]
                     for l in b.get("lines", []) for s in l["spans"]
                     if 0 <= top - s["bbox"][3] < 14)
    tag = re.search(r"(?:Question|Item) \d+", above)
    if not tag or tag.group() not in tagged:
        continue
    matched += 1
    entitled = F._working_space_cm(tagged[tag.group()]) * cm + BAND
    got = panel["rect"].height
    if got < _WORKING_SHRINK_FLOOR * entitled - 0.5:
        squeezed.append((n, tag.group(), round(got / cm, 2),
                         round(entitled / cm, 2)))
assert matched >= 20, f"only {matched} panels could be matched to a question"
assert not squeezed, (
    "these questions were given a visibly smaller panel than the same "
    f"question gets elsewhere in the booklet: {squeezed[:4]} "
    "(page, question, drawn cm, entitled cm). A short gap is invisible; a "
    "grid that stops two squares early reads as a misprint")
ok(f"all {matched} panels are within the {_WORKING_SHRINK_FLOOR:.0%} floor of "
   "what their question is entitled to")

print("\nTHE ANSWER RULE SITS INSIDE THE PANEL")

# The answer rule is the darker 0.6pt hairline. It has to be inside the panel
# it belongs to, not floating under it: that is what makes the whole thing
# read as one allocation.
loose = []
for n, page in enumerate(body, start=2):
    rects, _ = panel_paths(page)
    # Identified by the answer rule's own stroke colour, not by weight alone.
    # A 0.6pt line is a common enough weight that other designed elements use
    # it too, and the topic opener closes with one across the measure; this
    # check is about where the ANSWER rule is, so it looks for that ink.
    hairlines = [d for d in page.get_drawings()
                 if (d.get("width") or 0) > 0.5 and (d.get("width") or 0) < 0.7
                 and d["rect"].height < 1
                 and d.get("color")
                 and max(abs(a - b) for a, b in
                         zip(d["color"], RULE_INK)) < 0.01]
    for d in hairlines:
        y = d["rect"].y0
        if not any(r["rect"].y0 <= y <= r["rect"].y1 for r in rects) and rects:
            loose.append((n, round(y, 1)))
assert not loose, (
    f"answer rules drawn outside any panel: {loose[:4]}. The rule and the "
    "working area have to read as one block, or the panel is just a box with "
    "a stray line under it")
ok("every answer rule is drawn inside its panel")

print("\nIT SURVIVES A GREYSCALE HOME PRINTER, AND COSTS ALMOST NO INK")

# The page with the most panel ink on it, rasterised at 300dpi in grey: this
# is what a mono laser or an inkjet in black-only mode is asked to print.
busiest = max(range(len(body)),
              key=lambda i: len(panel_paths(body[i])[0]) + len(panel_paths(body[i])[1]))
page = body[busiest]
rect = panel_paths(page)[0][0]["rect"]
pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def working_area(r):
    """The ruled part of the panel, above the answer rule's own strip.

    Sampled without the answer strip because the "Answer:" label and its 0.6pt
    rule live down there, and they are meant to be dark: the measurement here
    is of the guide ruling, not of the answer line.
    """
    s = 300 / 72
    return grey[int(r.y0 * s) + 2:int((r.y1 - BAND) * s) - 2,
                int(r.x0 * s) + 2:int(r.x1 * s) - 2]


inside = working_area(rect)
darkest = int(inside.min())
assert darkest <= 245, (
    f"the darkest thing inside a working panel is {darkest}/255 in greyscale. "
    "That is invisible on a home printer, so the child is back to a blank gap")
assert darkest >= 190, (
    f"the panel prints at {darkest}/255, which is dark enough to compete with "
    "the question text sitting above it. It is a guide, not content")
ok(f"the panel prints at {darkest}/255 in greyscale: visible, not competing")

text_dark = int(grey[int(rect.y0 * 300 / 72) - 30:int(rect.y0 * 300 / 72) - 5,
                     int(rect.x0 * 300 / 72):int(rect.x1 * 300 / 72)].min())
assert text_dark < darkest - 100, (
    f"the question text above the panel prints at {text_dark}/255 against the "
    f"panel's {darkest}/255. The two are too close to rank on the page")
ok(f"the question text above it prints at {text_dark}/255")

coverage = (255.0 - inside.mean()) / 255.0
assert coverage <= 0.01, (
    f"the panel covers {coverage:.1%} of its area in ink. A booklet is "
    "printed at home, often several times; a working area is not worth a "
    "cartridge")
ok(f"the panel costs {coverage:.2%} coverage, inside the 1% budget")

doc.close()

if _failed:
    print(f"\n{len(_failed)} WORKING PANEL CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} WORKING PANEL CHECKS PASSED")
sys.exit(0)
