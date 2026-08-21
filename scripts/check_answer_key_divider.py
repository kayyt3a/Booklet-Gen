"""Checks the answer key opens on a designed page instead of just starting.

The key used to begin the way a second file begins: the last page the child
wrote on, then a page of answers. Nothing said the half of the booklet the
adult uses had started, and "where do the answers begin" was a question you
answered by flicking.

The half-title answers it, and it also replaces a device that existed for the
same reason. No page of the key may print on the reverse of a page the student
wrote on, because the first thing in the key is the spelling dictation list the
booklet takes deliberate trouble to keep out of the child's hands, and turning
the sheet over handed it straight back. That used to be solved by inserting a
sheet that said it was intentionally blank, which is a page the customer paid
to print and reads as a fault. The divider takes that page and says something
on it: student half ends on N, divider on N+1, answers from N+2, which is never
on N's sheet whatever parity N has.

So this file checks the page exists and reads as a landmark, that it carries
the legend for the mark used behind it, that it tells the truth about that
mark, and that the sheet invariant holds for every parity. The parity is the
part that has to be tested rather than argued: the fixtures below are built to
end their student half on both an odd and an even page.

    PYTHONPATH=. python scripts/check_answer_key_divider.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4

from booklet_gen.formatter import every_answer_checked, render_pdf
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


def vq(text, answer="42", working="42", difficulty="medium", verified=True):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=verified)


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Volume is the space inside a solid, in cubic units."],
    key_points=["Length times width times height."],
    worked_example=WorkedExample(question="A box is 5 cm by 3 cm by 4 cm.",
                                 steps=["5 x 3 = 15.", "15 x 4 = 60."],
                                 answer="60 cubic centimetres"))
SHORT = ["What is {} x 7?", "What is {} + 68?", "What is 480 - {}?",
         "Round {} to the nearest hundred."]


def questions(seed, n, verified=True):
    return [vq(SHORT[(seed + j) % len(SHORT)].format(24 + j * 3 + seed),
               answer=str(7 * (24 + j * 3 + seed)),
               difficulty="easy" if j % 2 else "medium", verified=verified)
            for j in range(n)]


def booklet(size: int, all_verified=True):
    """`size` moves the page the student half ends on, and so its parity."""
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(topic=t, subtopic=s, teaching=TEACHING,
                                 questions=questions(i, size),
                                 homework_questions=questions(i + 5, size),
                                 estimated_minutes=10)
                  for i, (t, s) in enumerate(
                      [("Fractions", "Comparing fractions"),
                       ("Fractions", "Adding fractions"),
                       ("Volume", "Volume of a prism")])],
        recap_questions=questions(9, 3),
        challenge_questions=questions(4, 2, verified=all_verified),
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


DIVIDER_TITLE = "Answers and Worked Solutions"
BANNER = "Answers & Worked Solutions"
TICK = "✓"

tmp = Path(tempfile.mkdtemp(prefix="folio-divider-"))
BOOKS = {}
for size in (4, 5, 6, 7):
    data = booklet(size)
    BOOKS[size] = (data, render_pdf(data, tmp / f"b{size}.pdf"))


def pages_of(path):
    doc = pymupdf.open(path)
    out = [page.get_text() for page in doc]
    doc.close()
    return out


def positions(path):
    """(divider page, first answers page, last student page), 1-based."""
    pages = pages_of(path)
    divider = next((i + 1 for i, t in enumerate(pages) if DIVIDER_TITLE in t),
                   None)
    answers = next(i + 1 for i, t in enumerate(pages) if BANNER in t)
    return divider, answers, (divider or answers) - 1


print("\nTHE KEY OPENS ON A PAGE OF ITS OWN")

missing_divider = [size for size, (_, path) in BOOKS.items()
                   if positions(path)[0] is None]
if not check(not missing_divider,
             f"all {len(BOOKS)} booklets open their key on a half-title",
             f"these booklets have no divider in front of the key: "
             f"{missing_divider}. The key begins the way a second file begins, "
             "and nothing in the booklet says the half the adult uses has "
             "started"):
    print(f"\n{len(_failed)} KEY DIVIDER CHECKS FAILED")
    sys.exit(1)

for size, (data, path) in BOOKS.items():
    pages = pages_of(path)
    divider, answers, _ = positions(path)
    page = " ".join(pages[divider - 1].split())
    check(answers == divider + 1,
          f"{size}q: the half-title is page {divider} and the answers begin on "
          f"{answers}",
          f"{size}q: the divider is on {divider} and the answers on {answers}. "
          "The divider is the page that opens the key and belongs directly in "
          "front of it")
    missing = [w for w in ("FOLIO", "AI", DIVIDER_TITLE, "whoever is marking")
               if w not in page]
    check(not missing,
          f"{size}q: it carries the wordmark, the title and who the rest is for",
          f"{size}q: the divider is missing {missing}. A half-title with only "
          "a heading on it is a heading on an empty page")

print("\nAND IT SAYS WHAT THE MARK BEHIND IT MEANS")

for size, (data, path) in BOOKS.items():
    pages = pages_of(path)
    divider, answers, _ = positions(path)
    page = " ".join(pages[divider - 1].split())
    check(page.count(TICK) == 1,
          f"{size}q: it prints the mark itself, once, as a legend",
          f"{size}q: the divider prints {page.count(TICK)} ticks. The reader "
          "has to see the mark to learn it; more than one and it is not a "
          "legend")

# And the legend tells the truth about the key behind it. The same function the
# cover, the front matter and the key's colophon ask decides which sentence
# prints, so a booklet with one unchecked answer cannot promise otherwise here.
CHECKED = "Every answer in this key has been checked"
PARTIAL = "A tick beside an answer means that answer was checked"
for name, data in (("all checked", booklet(4)),
                   ("one unchecked", booklet(4, all_verified=False))):
    path = render_pdf(data, tmp / f"{name.replace(' ', '-')}.pdf")
    pages = pages_of(path)
    divider = next(i for i, t in enumerate(pages) if DIVIDER_TITLE in t)
    page = " ".join(pages[divider].split())
    absolute = CHECKED in page
    check(absolute == every_answer_checked(data) and (PARTIAL in page) != absolute,
          f"{name}: the divider claims exactly what the key behind it shows",
          f"{name}: every_answer_checked is {every_answer_checked(data)} and "
          f"the divider says {'every answer is checked' if absolute else 'only that a tick marks one'}. "
          "The one page a marker reads before they start marking cannot be the "
          "page that overstates the key")

print("\nNO PAGE OF THE KEY IS THE BACK OF A PAGE THE STUDENT WROTE ON")

# Printed double-sided, sheet n carries pages 2n-1 and 2n. Four fixtures, whose
# student halves end on different pages, so both parities are covered: the
# invariant is measured rather than reasoned about.
def sheet(page_number: int) -> int:
    return (page_number + 1) // 2


parities = set()
for size, (data, path) in BOOKS.items():
    divider, answers, last = positions(path)
    parities.add(last % 2)
    check(sheet(answers) > sheet(last),
          f"{size}q: the student half ends on page {last} (sheet "
          f"{sheet(last)}) and the answers start on {answers} (sheet "
          f"{sheet(answers)})",
          f"{size}q: page {last} and page {answers} are both on sheet "
          f"{sheet(last)}. Turning over the last page the child wrote on shows "
          "them the answers, and on a booklet with a spelling test it shows "
          "them the words they are about to be tested on")

check(parities == {0, 1},
      "and both parities of last student page are covered by the fixtures",
      f"every fixture ends its student half on the same parity ({parities}), "
      "so the case the blank verso used to exist for is not being tested")

print("\nAND NO SHEET IS SPENT ON A PAGE THAT SAYS IT IS BLANK")

for size, (data, path) in BOOKS.items():
    blanks = [i + 1 for i, t in enumerate(pages_of(path))
              if "intentionally blank" in t]
    check(not blanks,
          f"{size}q: nothing in the booklet is a blank page",
          f"{size}q: blank pages at {blanks}. The divider does the job that "
          "page was inserted for, and a customer who prints a blank sheet "
          "reads it as a fault whatever it says on it")

if _failed:
    print(f"\n{len(_failed)} KEY DIVIDER CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} KEY DIVIDER CHECKS PASSED")
sys.exit(0)
