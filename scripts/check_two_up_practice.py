"""Checks short practice questions are set two to a row, and stay correct there.

Twelve consecutive pages of a maths booklet used to be the same page. Every one
of them was the same unit repeated four or five times: a bold number, one line
of question, a working panel, an answer rule. Nothing changed size, weight,
width or position from one page to the next, so a parent flipping the printed
stack had no landmarks in it at all, and every page was half empty sideways
while the booklet ran long.

So a question short enough to be set at half measure is set at half measure,
two to a row, each cell carrying its own working panel. Which questions qualify
is decided by the booklet's own content: one line of text in the narrow column,
and a small working entitlement. A word problem, a question with a picture, one
that wants ruled lines for a written answer and one that wants room to think in
all keep the full measure. That is what makes the rhythm differ from booklet to
booklet by itself, rather than being a pattern stamped on every page.

Four things have to hold, and every one of them is a defect a customer would
see if it broke.

  * Two-up rows are actually produced, and NOT produced for a booklet whose
    questions are all long. A layout that never fires is not a layout, and one
    that fires on everything squashes word problems into a column.
  * Numbering stays in reading order: left cell, right cell, next row down. A
    child told to do question 7 has to find question 7 where 7 should be.
  * The answer key's page references still point at the page the question is
    printed on. The key says "(p11)" and whoever is marking turns to page 11.
  * A 5mm square is 5mm in a narrow column too. The grid is an exercise-book
    grid: a child counting squares to line a column addition up has to be
    counting the same square they counted on the page before, so the panel
    holds fewer squares in a narrow cell rather than smaller ones. And the two
    panels of a row are the same height and start on the same line, or the row
    looks ragged and half-finished.

    PYTHONPATH=. python scripts/check_two_up_practice.py
"""
import re
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.units import cm

from booklet_gen import formatter as F
from booklet_gen.formatter import (TWO_UP_COLUMN, _make_styles, render_pdf,
                                   two_up_eligible, two_up_rows)
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


def vq(text, answer="42", working="Multiply it out.", difficulty="easy",
       **kw):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True, **kw)


STYLES = _make_styles()

print("\nWHAT QUALIFIES IS DECIDED BY THE QUESTION, NOT BY A PATTERN")

CASES = [
    (vq("What is 28 x 3?"), True,
     "a one-line sum with a small working area is what the narrow column is "
     "for"),
    (vq("Round 4 728 to the nearest hundred."), True, "as above"),
    (vq("A bus holds 48 passengers. Seven buses leave the depot full, and at "
        "the first stop 38 people get off. How many are still aboard?"), False,
     "a word problem wraps to three lines in an 8cm column, which is a "
     "question squashed rather than a question laid out"),
    (vq("What is 28 x 3?", difficulty="hard"), False,
     "a question tagged hard is entitled to 3.2cm of working, and a child "
     "given room to think in should not have it taken away to save paper"),
    (vq("Explain how you know 2 059 is larger than 1 999.",
        answer="It has 2 thousands, and 2 beats 1, so it is larger."), False,
     "a written answer gets full ruled lines, and half-measure ruled lines "
     "are half a sentence each"),
    (vq("Sketch the graph of y = 2x."), False,
     "a drawing needs clear space, and half of it is not space"),
    (vq("a) What is 6 x 7? b) What is 6 x 8?"), False,
     "two parts means two answer rules, which is a taller block than the "
     "narrow column is for"),
]
for question, want, why in CASES:
    got = two_up_eligible(STYLES, question, 1, "Mathematics")
    if got != want:
        bad(f"{question.question.question[:40]!r} eligible={got}, wanted "
            f"{want}: {why}")
ok("a short sum qualifies; a word problem, a hard question, a written answer, "
   "a drawing and a two-part question do not")

# A question with a picture under it cannot share a row: the picture is sized
# for the full measure and there is nothing sensible to do with it at half.
with_picture = vq("What is the area of this rectangle?")
with_picture.image_path = str(Path(F.ASSET_DIR) / "cover_background.jpg")
assert F.image_is_usable(with_picture.image_path), "fixture image missing"
check(not two_up_eligible(STYLES, with_picture, 1, "Mathematics"),
      "a question carrying a diagram keeps the full measure",
      "a question with a diagram was put in a half-measure cell. The picture "
      "is drawn at up to 7.5cm wide and the column is 8.2cm, so it fills the "
      "cell and the working panel is pushed off the row")

print("\nROWS ARE BUILT IN READING ORDER")

check(two_up_rows([True, True, True, True]) == [[0, 1], [2, 3]],
      "four short questions make two rows of two",
      f"four short questions made {two_up_rows([True] * 4)}")
check(two_up_rows([True, False, True, True]) == [[0], [1], [2, 3]],
      "a long question between two short ones breaks the pairing rather than "
      "reordering round it",
      f"got {two_up_rows([True, False, True, True])}. Pairing across a long "
      "question would print question 3 above question 2")
check(two_up_rows([True, True, True], breaks=[1]) == [[0], [1, 2]],
      "a question a session band is printed above starts its own row",
      f"got {two_up_rows([True, True, True], breaks=[1])}. The band would be "
      "printed between the two halves of a row already begun")

# ---------------------------------------------------------------------------
# The fixture
#
# Deliberately mixed, because that is the condition the whole thing exists for:
# a page has to be able to run a two-up row, then a full-width word problem,
# then another two-up row. Every question text and every answer is unique, so a
# question found on a page is not a guess.
# ---------------------------------------------------------------------------

SHORT = "What is {} x 7?"
LONG = ("A bus holds {} passengers. Seven buses leave the depot full, and at "
        "the first stop 38 people get off. How many passengers are still on "
        "the buses altogether?")


def run(seed, n):
    out = []
    for j in range(n):
        k = seed * 13 + j
        if j % 5 == 4:
            out.append(vq(LONG.format(100 + k), answer=str(9000 + k),
                          difficulty="hard"))
        elif j % 5 == 3:
            out.append(vq(f"Explain how you know {300 + k} is larger than "
                          f"{290 + k}.",
                          answer="It has more hundreds, and a hundred is "
                                 "worth more than ten ones.",
                          difficulty="medium"))
        else:
            out.append(vq(SHORT.format(20 + k), answer=str(7 * (20 + k))))
    return out


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Line the digits up by place value and work one column "
                      "at a time, starting from the ones."],
    key_points=["Line the digits up.", "Work one column at a time."],
    worked_example=WorkedExample(
        question="What is 2 385 + 1 947?",
        steps=["Add the ones.", "Carry into the tens."], answer="4 332"))

SUBTOPICS = [("Number and Place Value", "Four-digit numbers"),
             ("Number and Place Value", "Rounding to hundreds"),
             ("Addition and Subtraction", "Column addition"),
             ("Measurement", "Perimeter of rectangles")]


def booklet(long_only=False):
    sections = []
    for i, (topic, subtopic) in enumerate(SUBTOPICS):
        def wordy(base):
            return [vq(LONG.format(base + j), answer=str(8000 + base + j),
                       difficulty="hard") for j in range(6)]
        qs = wordy(500 + i * 10) if long_only else run(i, 8)
        hw = wordy(700 + i * 10) if long_only else run(i + 20, 6)
        sections.append(SubtopicOutput(
            topic=topic, subtopic=subtopic, teaching=TEACHING, questions=qs,
            homework_questions=hw, estimated_minutes=10))
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=(wordy(900) if long_only else run(40, 3)),
        challenge_questions=(wordy(950) if long_only else run(50, 3)),
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


tmp = Path(tempfile.mkdtemp(prefix="folio-twoup-"))
out = tmp / "twoup.pdf"
render_pdf(booklet(), out)
print(f"\nrendered {out}")

INK = tuple(round(int(F._PANEL_INK[i:i + 2], 16) / 255, 3) for i in (1, 3, 5))
doc = pymupdf.open(out)
KEY_START = next(i for i, p in enumerate(doc) if "Worked Solutions" in p.get_text())


def panels(page):
    """The working panels drawn on this page, as (rect, [grid strokes])."""
    rects, strokes = [], []
    for d in page.get_drawings():
        colour = d.get("color")
        if not colour or max(abs(c - i) for c, i in zip(colour, INK)) > 0.01:
            continue
        kinds = {item[0] for item in d["items"]}
        (rects if "re" in kinds else strokes).append(d)
    return [(r["rect"], [s["rect"] for s in strokes
                         if r["rect"].y0 - 1 <= s["rect"].y0
                         and s["rect"].y1 <= r["rect"].y1 + 1
                         and r["rect"].x0 - 1 <= s["rect"].x0
                         and s["rect"].x1 <= r["rect"].x1 + 1])
            for r in rects]


def number_above(page, rect):
    """The printed question number sitting directly above this panel."""
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            x0, _, _, y1 = line["bbox"]
            if not (0 <= rect.y0 - y1 < 16):
                continue
            if abs(x0 - rect.x0) > 12:
                continue
            m = re.match(r"(\d+)\.", text)
            if m:
                return int(m.group(1))
    return None


NARROW = [(i, r, s) for i in range(1, KEY_START)
          for r, s in panels(doc[i])
          if abs(r.width - TWO_UP_COLUMN) < 4]
WIDE = [(i, r) for i in range(1, KEY_START) for r, _ in panels(doc[i])
        if r.width > TWO_UP_COLUMN + 4]

print("\nTHE BOOKLET ACTUALLY RUNS TWO TO A ROW, AND NOT FOR EVERYTHING")

check(len(NARROW) >= 12,
      f"{len(NARROW)} questions are set at half measure, {len(WIDE)} across "
      "the page",
      f"only {len(NARROW)} questions were set at half measure. Every page is "
      "then the same page: one bold number, one line, one panel, an answer "
      "rule, four or five times over, for the length of the booklet")

check(len(WIDE) >= 8,
      "the word problems and written answers kept the full measure",
      f"only {len(WIDE)} questions kept the full measure. A word problem in an "
      "8cm column wraps to three lines and a written answer gets half a "
      "sentence per rule; the narrow column is for short questions only")

# Rows: two narrow panels at the same height on the same page.
rows = []
for i in range(1, KEY_START):
    narrow = sorted([(r, s) for _, r, s in NARROW if _ == i],
                    key=lambda p: (round(p[0].y0, 1), p[0].x0))
    for a, b in zip(narrow, narrow[1:]):
        if abs(a[0].y0 - b[0].y0) < 2 and a[0].x0 < b[0].x0:
            rows.append((i, a, b))
check(len(rows) >= 6,
      f"{len(rows)} two-up rows across the question pages",
      f"only {len(rows)} rows carry two panels side by side. Narrow panels "
      "that never pair are one column of half-width boxes, which is worse "
      "than the full measure it replaced")

# A booklet whose questions are all word problems must get none of this.
plain = tmp / "long.pdf"
render_pdf(booklet(long_only=True), plain)
pdoc = pymupdf.open(plain)
pkey = next(i for i, p in enumerate(pdoc) if "Worked Solutions" in p.get_text())
plain_narrow = sum(1 for i in range(1, pkey) for r, _ in panels(pdoc[i])
                   if abs(r.width - TWO_UP_COLUMN) < 4)
pdoc.close()
check(plain_narrow == 0,
      "a booklet of nothing but word problems gets no two-up rows at all, so "
      "the rhythm is the content's and not a pattern laid over it",
      f"{plain_narrow} panels were set at half measure in a booklet whose "
      "questions are all multi-sentence word problems. The layout is supposed "
      "to follow what the questions need")

print("\nNUMBERING STAYS IN READING ORDER")

misordered = []
for page, (left, _), (right, _) in rows:
    ln, rn = number_above(doc[page], left), number_above(doc[page], right)
    if ln is None or rn is None or rn != ln + 1:
        misordered.append((page + 1, ln, rn))
check(not misordered,
      f"every one of the {len(rows)} rows reads left number then the next one "
      "to its right",
      f"these rows are numbered out of order: {misordered[:4]} (page, left, "
      "right). A child told to do question 7 looks for it where 7 should be, "
      "and a tutor marking down the key reads the numbers in printed order")

# And down the page as a whole: the numbers under one heading run 1, 2, 3...
for i in range(1, KEY_START):
    page = doc[i]
    seen = [(r.y0, r.x0, number_above(page, r)) for r, _ in panels(page)]
    seen = [n for _, _, n in sorted(seen) if n is not None]
    # Not consecutive: a written-response question draws ruled lines and no
    # panel, so its number is not in this list. Strictly increasing, except
    # where a new subtopic restarts the numbering at 1.
    if not all(b > a or b == 1 for a, b in zip(seen, seen[1:])):
        bad(f"page {i + 1} prints question numbers {seen}, which do not run in "
            "order down the page")
        break
else:
    ok("and the numbers run in order down every question page")

print("\nTHE ANSWER KEY STILL POINTS AT THE RIGHT PAGE")

# Every answer in the fixture is unique, so the question a key line refers to
# is found by its answer and then looked for on the page the key names.
answers = {}
for section in booklet().sections:
    for q in list(section.questions) + list(section.homework_questions):
        answers[q.question.answer] = q.question.question
for q in booklet().recap_questions + booklet().challenge_questions:
    answers[q.question.answer] = q.question.question

def norm(text):
    """Letters and digits only, lowercased.

    The page prints "540 × 7" where the fixture wrote "540 x 7", because the
    formatter sets a real multiplication sign. Folding the two together and
    then keeping only letters and digits makes the match about which question
    this is rather than about how it was typeset.
    """
    return re.sub(r"[^0-9a-z]", "", text.lower().replace("×", "x"))


PAGE_TEXT = [norm(p.get_text()) for p in doc]
refs, wrong = 0, []
for i in range(KEY_START, len(doc)):
    page = doc[i]
    marks = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            marks.append((line["bbox"][1], line["bbox"][0], text))
    for y, x, text in marks:
        m = re.match(r"\d+\. Answer: (\S+)", text)
        if not m or m.group(1) not in answers:
            continue
        near = [t for yy, xx, t in marks
                if abs(yy - y) < 6 and xx > x and "(p" in t]
        if not near:
            continue
        target = int(re.search(r"\(p(\d+)\)", near[0]).group(1))
        refs += 1
        wanted = norm(answers[m.group(1)])
        if wanted not in PAGE_TEXT[target - 1]:
            wrong.append((m.group(1), target))
check(refs >= 20 and not wrong,
      f"all {refs} page references in the key land on the page the question is "
      "printed on",
      f"{len(wrong)} of {refs} page references point at the wrong page: "
      f"{wrong[:4]} (answer, page named). Whoever is marking turns to the page "
      "the key names and the question is not on it")

print("\nA 5MM SQUARE IS 5MM IN A NARROW COLUMN TOO")

pitches = []
for _, rect, strokes in NARROW:
    verticals = sorted(s.x0 for s in strokes if s.width < 1)
    pitches += [round(b - a, 2) for a, b in zip(verticals, verticals[1:])]
check(pitches and max(pitches) - min(pitches) < 1
      and abs(pitches[0] - F._PANEL_GRID_CM * cm) < 1,
      f"the grid in a half-measure panel is drawn at "
      f"{(pitches[0] if pitches else 0) / cm:.2f}cm, the same pitch as a "
      "full-measure one",
      f"the squares in a narrow panel are drawn at "
      f"{[round(p / cm, 2) for p in sorted(set(pitches))][:4]}cm rather than "
      f"{F._PANEL_GRID_CM}cm. Scaling the grid to the column defeats the "
      "point of squared paper: a child counting squares to line up a column "
      "addition is counting a different square on every page")

wide_pitches = []
for i in range(1, KEY_START):
    for rect, strokes in panels(doc[i]):
        if rect.width <= TWO_UP_COLUMN + 4:
            continue
        verticals = sorted(s.x0 for s in strokes if s.width < 1)
        wide_pitches += [round(b - a, 2) for a, b in zip(verticals, verticals[1:])]
check(bool(wide_pitches) and bool(pitches)
      and abs(max(wide_pitches) - max(pitches)) < 1,
      "and it matches the pitch of the full-measure panels beside it on the "
      "same page",
      f"narrow panels rule at {max(pitches or [0]) / cm:.2f}cm and "
      f"full-measure ones at {max(wide_pitches or [0]) / cm:.2f}cm. Two grids "
      "at two pitches in one booklet is worse than one grid at the wrong "
      "pitch")

print("\nA ROW IS LEVEL, TOP AND BOTTOM")

ragged = []
for page, (left, _), (right, _) in rows:
    if abs(left.y0 - right.y0) > 1.5:
        ragged.append((page + 1, "tops", round(abs(left.y0 - right.y0), 1)))
    if abs(left.height - right.height) > 1.5:
        ragged.append((page + 1, "heights",
                       round(abs(left.height - right.height), 1)))
check(not ragged,
      f"all {len(rows)} rows have both panels starting on the same line and "
      "the same height",
      f"these rows are ragged: {ragged[:4]} (page, what, points out). One "
      "panel taller than the one beside it, or starting lower, is the single "
      "thing that makes a two-column layout look like it was not meant")

print("\nAND THE PAGES PACK TIGHTER")

check(TWO_UP_COLUMN * 2 < F.BODY_WIDTH,
      f"the two columns are {TWO_UP_COLUMN / cm:.2f}cm each with a gutter "
      "between them, inside the type area",
      f"two columns of {TWO_UP_COLUMN / cm:.2f}cm do not fit the "
      f"{F.BODY_WIDTH / cm:.2f}cm measure, so a row runs into the margin")

# The narrow panels are real working area, not a token: a child still gets
# enough squares across the cell to set out a two-digit multiplication.
squares = min([len([s for s in strokes if s.width < 1])
               for _, _, strokes in NARROW] or [0])
check(squares >= 12,
      f"the narrowest panel still rules {squares} columns of squares across, "
      "which is room for a written multiplication",
      f"a half-measure panel rules only {squares} columns of squares. Below "
      "about twelve there is not room to set out a two-digit multiplication, "
      "and the panel is decoration rather than working space")

doc.close()

if _failed:
    print(f"\n{len(_failed)} TWO-UP PRACTICE CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} TWO-UP PRACTICE CHECKS PASSED")
sys.exit(0)
