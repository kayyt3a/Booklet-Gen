"""Checks the answer key is set as a designed page, not dumped down a column.

The key used to run across the full 539pt measure of an A4 page while the
longest thing it ever printed was "3. Answer: 10/12 = 5/6" and two lines of
working. The median line ended around the middle of the sheet, so half of
every key page was blank, over six or seven pages. That is what a printout of
a variable looks like, and it is the last thing a parent reads before deciding
whether fifty dollars bought a product or an export.

The key now runs in two columns of its own page template. Four properties came
out of that, and every one of them is something the person marking would see if
it broke.

  * The key is two columns and the BODY IS NOT. The body's pages are worked on
    by a child with a pencil: a working panel needs the full measure, a reading
    passage needs it, and a question set in an 8cm column with a grid under it
    is unusable. The key is the only part of the booklet whose measure changed,
    and a regression that two-columned the student pages has to fail loudly
    rather than quietly ship.
  * The verified tick and the "(p9)" back-reference sit in their own
    right-aligned strip, so they form a column that can be scanned down. They
    used to trail whatever the answer happened to be, which put them at a
    different x on every line; a parent looking for the one answer that was not
    checked had to read all sixty rather than run an eye down an edge.
  * A wrapped answer hangs. Its second line used to start flush with the
    question number, so the run-on of answer 5 sat in the same column as the
    number of answer 6, and the tick got pushed onto the following line, which
    is what broke the column above.
  * The key's pages actually carry text. This is the measurement the whole
    change was made against, so it is asserted as a floor rather than left as a
    claim in a commit message.

Everything here is measured off a rendered PDF with pymupdf. The column
geometry is found by looking for the emptiest vertical band on the page rather
than by importing the formatter's constants, because a check that reads the
constants it is checking proves only that the file parses.

    PYTHONPATH=. python scripts/check_answer_key_layout.py
"""
import re
import statistics
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen import formatter as F
from booklet_gen.formatter import PAGE_MARGIN, _register_fonts, render_pdf
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
# A booklet with the shapes the key has to cope with: a recap, guided examples
# (which put a bordered box inside a column), several subtopics of class work,
# homework, a Final Challenge, and one deliberately long answer that cannot fit
# a column on one line.
# ---------------------------------------------------------------------------

def vq(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


def teaching(n_guided):
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the amount of space inside a three "
                          "dimensional object. Multiply length, width and "
                          "height, and answer in cubic units."],
        key_points=["Volume equals length times width times height.",
                    "Write your answer in cubic units, like cm^3."],
        worked_example=WorkedExample(
            question="A box is 5 cm by 3 cm by 4 cm. What is its volume?",
            steps=["Multiply the length by the width: 5 x 3 = 15.",
                   "Multiply that result by the height: 15 x 4 = 60."],
            answer="60 cubic centimetres"),
        guided_examples=[WorkedExample(
            question=f"Find the volume of a prism {k + 2} cm by 2 cm by 3 cm.",
            steps=["Multiply the length and width.", "Multiply by the height."],
            answer="36 cubic cm") for k in range(n_guided)])


TOPICS = [("Fractions", "Comparing fractions"), ("Fractions", "Adding fractions"),
          ("Volume", "Volume of a prism"), ("Volume", "Capacity and litres"),
          ("Number and Place Value", "Four-digit numbers and ordering")]
sections = []
for i, (topic, subtopic) in enumerate(TOPICS):
    sections.append(SubtopicOutput(
        topic=topic, subtopic=subtopic, teaching=teaching(1 + i % 2),
        questions=[vq(f"Question {i}.{j}: A box is {j + 2} cm long, 2 cm wide "
                      "and 3 cm high. What is its volume in cubic centimetres?",
                      answer=str((j + 2) * 6),
                      working=f"Volume = length x width x height. Volume = "
                              f"{j + 2} x 2 x 3. The volume is {(j + 2) * 6}.")
                   for j in range(4)],
        homework_questions=[
            vq(f"Homework {i}.{j}: Calculate {j + 1}/12 + {j + 1}/12.",
               answer=f"{2 * (j + 1)}/12", difficulty="easy",
               working="Add the numerators over the same denominator. The "
                       "total is the sum.") for j in range(5)],
        estimated_minutes=10))

# The answer this check's hanging-indent property is measured on. It is the
# shape of a real comprehension or ordering answer: a sentence, not a number,
# and far too long for one line of an 8cm column.
LONG_ANSWER = ("1 299, then 1 562, then 1 840, because the hundreds digit "
               "decides once the thousands are equal")
sections[-1].questions.append(vq(
    "Order these numbers from smallest to largest: 1 562, 1 299, 1 840.",
    answer=LONG_ANSWER,
    working="Compare thousands: all have 1. Compare hundreds: 2, 5 and 8."))

data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    program_label="Academic Accelerate", sections=sections,
    recap_questions=[vq("Calculate 15 * 4 + 7.", answer="67", difficulty="easy"),
                     vq("If 5x = 45, what is x?", answer="x = 9",
                        difficulty="easy")],
    challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its volume "
                            "in cubic metres?", answer="75", difficulty="hard",
                            working="Volume = 10 x 5 x 1.5. 50 x 1.5 = 75.")],
    recap_minutes=6, classwork_minutes=60, homework_minutes=105,
    challenge_minutes=18, total_minutes=170)

_register_fonts()
out = Path(tempfile.mkdtemp(prefix="folio-key-")) / "key.pdf"
render_pdf(data, out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
# The first page of the key PROPER, found by the banner it opens with. The
# half-title in front of it also carries the words "Worked Solutions", and it
# is neither set in two columns nor full of answers: counting it as a key page
# measured a designed landmark against a density floor written for pages of
# answers, and put its centred legend tick into the column of marking ticks.
KEY_START = next(i for i, p in enumerate(doc)
                 if "Answers & Worked Solutions" in p.get_text())
LEFT, RIGHT = PAGE_MARGIN, A4[0] - PAGE_MARGIN
MEASURE = RIGHT - LEFT
TYPE_AREA = MEASURE * (A4[1] - 2 * PAGE_MARGIN)


def body_lines(page):
    """Text lines inside the type area, so the running header and the page
    number are not mistaken for content."""
    height = page.rect.height
    out_ = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            if not "".join(s["text"] for s in line["spans"]).strip():
                continue
            _, y0, _, y1 = line["bbox"]
            if y0 < PAGE_MARGIN - 6 or y1 > height - PAGE_MARGIN + 6:
                continue
            out_.append(line)
    return out_


def spans(first=0, last=None):
    for i in range(first, doc.page_count if last is None else last):
        for block in doc[i].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip():
                        yield i, span


def gutter(lines):
    """The emptiest vertical band on a page: (x, crossings, n_left, n_right).

    A page set in one column has text running across every x in the middle of
    the measure, so the best band it can offer still has lines through it and
    nothing at all starting to its right. A page set in two has a band no line
    crosses with roughly half the page's lines on either side of it.
    """
    best = None
    for x in range(int(LEFT + 80), int(RIGHT - 80)):
        crossings = sum(1 for ln in lines
                        if ln["bbox"][0] < x - 1 and ln["bbox"][2] > x + 1)
        left = sum(1 for ln in lines if ln["bbox"][2] <= x)
        right = sum(1 for ln in lines if ln["bbox"][0] >= x)
        score = (crossings, -min(left, right))
        if best is None or score < best[0]:
            best = (score, (x, crossings, left, right))
    return best[1]


# A page counts as two-column when a band it splits cleanly has a real share of
# its lines on both sides. Three crossings are tolerated because the first key
# page carries a full-width banner ("Answers & Worked Solutions" and the line
# telling the marker what the tick means) above the columns.
def two_column(lines) -> bool:
    x, crossings, left, right = gutter(lines)
    return crossings <= 3 and min(left, right) >= 20


def column_of(x0) -> str:
    return "right" if x0 > (LEFT + RIGHT) / 2 else "left"


print("\nTHE KEY RUNS IN TWO COLUMNS")

key_pages = {i: body_lines(doc[i]) for i in range(KEY_START, doc.page_count)}
# A key that has run out of answers is allowed to end short, so the last page
# is not required to fill two columns. Every page carrying a full page's worth
# of lines is.
full = {i: ls for i, ls in key_pages.items() if len(ls) >= 30}
check(len(full) >= 2,
      f"{len(full)} key pages carry a full page of answers, so there is "
      "something to measure",
      f"only {len(full)} of {len(key_pages)} key pages have 30 lines on them. "
      "This fixture is no longer generating enough of a key to tell a "
      "one-column layout from a two-column one, so the measurements below "
      "prove nothing")

single = [i + 1 for i, ls in full.items() if not two_column(ls)]
check(not single,
      f"key pages {[i + 1 for i in full]} are each set in two columns",
      f"key pages {single} are set in one column again. The key's longest "
      "line is an answer, a tick and a page number; across the full 539pt "
      "measure that leaves half of every page blank and adds three sheets to "
      "what the customer prints")

print("\nAND THE PAGES THE CHILD WORKS ON ARE NOT")

# The catastrophic direction. A question set in an 8cm column has no room for
# a working panel, a column subtraction or a reading passage, and the child
# cannot write in it.
body = {i: body_lines(doc[i]) for i in range(1, KEY_START)}
columned = [i + 1 for i, ls in body.items() if ls and two_column(ls)]
check(not columned,
      f"all {len(body)} question pages keep the full measure",
      f"question pages {columned} were split into columns. The child works on "
      "these pages: a working panel, a column subtraction and a reading "
      "passage all need the full measure, and an 8cm column cannot be written "
      "in")

print("\nTHE TICKS AND PAGE REFERENCES FORM A COLUMN")

refs, ticks = {}, {}
for _, span in spans(KEY_START):
    text = span["text"].strip()
    if re.fullmatch(r"\(p\d+\)", text):
        refs.setdefault(column_of(span["bbox"][0]), []).append(span["bbox"][2])
    if "✓" in text:
        ticks.setdefault(column_of(span["bbox"][0]), []).append(span["bbox"][0])

n_refs = sum(len(v) for v in refs.values())
check(n_refs >= 20,
      f"{n_refs} answers carry a page reference back to the question",
      f"only {n_refs} page references were found in the key. Either the "
      "back-references are gone, in which case marking sixty answers means "
      "hunting for every question by hand, or they are no longer being "
      "extracted and nothing below is being measured")

# Right-aligned means one right edge, whatever the reference says. "(p9)" and
# "(p13)" are different widths and must still end at the same x.
for side, xs in sorted(refs.items()):
    if len(xs) < 10:
        continue
    spread = max(xs) - min(xs)
    check(spread <= 1.5,
          f"the {side} column's {len(xs)} page references share one right edge "
          f"(within {spread:.2f}pt)",
          f"the {side} column's page references end anywhere across "
          f"{spread:.1f}pt. They trail whatever the answer happened to say, so "
          "there is no edge to run an eye down and the marker reads every line")

for side, xs in sorted(ticks.items()):
    if len(xs) < 10:
        continue
    spread = max(xs) - min(xs)
    # Not zero: the tick sits left of the reference, and "(p9)" is a digit
    # narrower than "(p13)". A few points of step is the reference's width,
    # not scatter.
    check(spread <= 10,
          f"the {side} column's {len(xs)} verified ticks stand within "
          f"{spread:.1f}pt of one another",
          f"the {side} column's ticks are scattered over {spread:.1f}pt of "
          "measure. The tick is the only thing on the page that says an answer "
          "was checked, and an unticked answer is only visible at a glance if "
          "the ticked ones line up")

print("\nA WRAPPED ANSWER HANGS UNDER THE ANSWER, NOT UNDER THE NUMBER")

# Every line of an answer is set in the bold sans; the working under it is
# regular and the headings around it are the serif. So the continuation lines
# of an answer are the bold spans immediately below a "N. Answer:" span in the
# same column. Read off the module rather than imported by name: the font
# names are only settled once _register_fonts has run.
bold = [(page, s) for page, s in spans(KEY_START) if s["font"] == F.FONT_BOLD]
wraps = []
for page, span in bold:
    if not re.match(r"^\d+\.\s*Answer:", span["text"].strip()):
        continue
    for other_page, other in bold:
        if other_page != page or other is span:
            continue
        if column_of(other["bbox"][0]) != column_of(span["bbox"][0]):
            continue
        if -1 <= other["bbox"][1] - span["bbox"][3] <= 4:
            wraps.append((round(span["bbox"][0], 1), round(other["bbox"][0], 1),
                          other["text"].strip()[:40]))

check(wraps,
      f"{len(wraps)} answer(s) in the key wrap onto a second line",
      "no answer in the key wraps at all, so the hanging indent is untested. "
      "The fixture's long answer stopped being long enough for a column, and "
      "this property is being asserted against nothing")

flush = [w for w in wraps if w[1] - w[0] < 15]
hang = min((w[1] - w[0] for w in wraps), default=0.0)
check(bool(wraps) and not flush,
      "and every one of them is indented past its question number by at least "
      f"{hang:.0f}pt",
      f"these answers start their second line flush with the question number: "
      f"{flush[:3]} (first-line x, wrapped x, text). The run-on of one answer "
      "then sits in the same column as the number of the next, and the tick "
      "is pushed onto the line below, which is what breaks the column of "
      "ticks above")

print("\nTHE KEY'S PAGES CARRY TEXT")

# The measurement the change was made against. The single-column key averaged
# 16 percent of its type area in text and never once reached 21; two columns
# take the same answers to about 30. The floor is set below what the layout
# achieves and well above anything one column managed, so it catches a
# regression to a sparse key without failing on a key that happens to hold
# more worked-example boxes than answers.
DENSITY_FLOOR = 0.22


def density(lines) -> float:
    return sum((ln["bbox"][2] - ln["bbox"][0]) * (ln["bbox"][3] - ln["bbox"][1])
               for ln in lines) / TYPE_AREA


# The last key page is where the answers run out, and a page that ends short
# because there is nothing left to print is not the defect.
measured = [density(ls) for i, ls in sorted(key_pages.items())
            if i < max(key_pages)]
mean = statistics.mean(measured) if measured else 0.0
check(mean >= DENSITY_FLOOR,
      f"the key's pages average {mean:.0%} of the type area in text, above "
      f"the {DENSITY_FLOOR:.0%} floor",
      f"the key's pages average {mean:.0%} of the type area in text "
      f"({[round(d, 3) for d in measured]}), under a {DENSITY_FLOOR:.0%} "
      "floor. That is the sparse single-column key again: half of every sheet "
      "blank, more sheets to print, and a key that reads as an export rather "
      "than as part of the booklet")

doc.close()

if _failed:
    print(f"\n{len(_failed)} ANSWER KEY LAYOUT CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} ANSWER KEY LAYOUT CHECKS PASSED")
sys.exit(0)
