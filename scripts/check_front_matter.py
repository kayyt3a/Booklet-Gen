"""Checks the page addressed to the adult says only what the code does.

A booklet that arrives as a PDF has to explain itself. Nobody hands the parent
a covering note, and until now the only instructions in the whole document were
one line on each part band. So there is a page for the adult after the contents
saying what the parts are, how long each runs, how to mark it, and what
generated it.

Front matter is where a product lies to its customer, so this file is mostly
about what the page may not say.

  * The times on it are the times the bands print. Two numbers for one part,
    a page apart, is worse than neither.
  * It lists the parts this booklet has and no others.
  * It does not claim teacher review, curriculum accreditation or human
    authoring. None of those happened.
  * It promises a tick on every answer only when every answer has one, decided
    by the same function the cover and the key's colophon ask, so the three
    cannot disagree about the same key.
  * It says what did make the booklet, in plain words, on the same page.

    PYTHONPATH=. python scripts/check_front_matter.py
"""
import re
import sys
import tempfile
from pathlib import Path

import pymupdf

from booklet_gen.formatter import (every_answer_checked, how_to_rows,
                                   render_exam_pdf, render_pdf)
from booklet_gen.schemas import (BookletData, ExamPaper, ExamSection, Question,
                                 SubtopicOutput, SubtopicTeaching,
                                 ValidatedQuestion, WorkedExample)
from booklet_gen.timing import booklet_timing

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


def vq(text, answer="42", working="42", difficulty="medium", verified=True,
       marks=None):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty, marks=marks),
        verified=verified)


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


def booklet(all_verified=True):
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(topic=t, subtopic=s, teaching=TEACHING,
                                 questions=questions(i, 6),
                                 homework_questions=questions(i + 5, 5),
                                 estimated_minutes=10)
                  for i, (t, s) in enumerate(
                      [("Fractions", "Comparing fractions"),
                       ("Fractions", "Adding fractions"),
                       ("Volume", "Volume of a prism")])],
        recap_questions=questions(9, 3),
        challenge_questions=questions(4, 2, verified=all_verified),
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


tmp = Path(tempfile.mkdtemp(prefix="folio-front-"))
CLEAN = booklet()
PARTIAL = booklet(all_verified=False)
CLEAN_PDF = render_pdf(CLEAN, tmp / "clean.pdf")
PARTIAL_PDF = render_pdf(PARTIAL, tmp / "partial.pdf")

HEADING = "How to use this booklet"


def page_texts(path):
    doc = pymupdf.open(path)
    out = [" ".join(page.get_text().split()) for page in doc]
    doc.close()
    return out


def how_to_page(path):
    for i, text in enumerate(page_texts(path)):
        if HEADING in text:
            return i, text
    return None, ""


print("\nTHE PAGE IS THERE, EARLY, AND IT IS ONE PAGE")

for name, path in (("all checked", CLEAN_PDF), ("one unchecked", PARTIAL_PDF)):
    index, text = how_to_page(path)
    check(index is not None,
          f"{name}: the booklet explains itself to the adult on page "
          f"{(index or 0) + 1}",
          f"{name}: no page of the booklet says how to use it. The only "
          "instructions in the document are then one line on each part band, "
          "and nothing at all about what the key's tick means")
    if index is None:
        continue
    check(index <= 2,
          f"{name}: and it is in the front matter, not somewhere in the work",
          f"{name}: it is on page {index + 1}, past the point where the "
          "student has already started")
    later = [t for t in page_texts(path)[index + 1:] if HEADING in t]
    check(not later,
          f"{name}: it fits on one page",
          f"{name}: it runs onto a second page")

if how_to_page(CLEAN_PDF)[0] is None:
    print("\nNo page to check. Everything below reads that page.")
    print(f"\n{len(_failed)} FRONT MATTER CHECKS FAILED")
    sys.exit(1)

print("\nTHE TIMES ON IT ARE THE TIMES THE BANDS PRINT")

# Read back off the page rather than off the code that wrote it, and compared
# with the estimate printed on the part's own band. A parent planning a week
# around "about 41 min" who then finds "about 55 min" on the band has been
# given two numbers for one thing.
index, front = how_to_page(CLEAN_PDF)
body = " ".join(page_texts(CLEAN_PDF)[index + 1:])
listed = dict(re.findall(r"([A-Z][A-Za-z\- ]+?) ABOUT (\d+) MIN", front))
rows = how_to_rows(CLEAN, booklet_timing(CLEAN))

check(len(listed) == len([r for r in rows if r[1]]),
      f"every part with an estimate carries it here: {sorted(listed)}",
      f"the page lists {sorted(listed)} against the "
      f"{[r[0] for r in rows if r[1]]} that have one")

mismatched = []
for part, minutes in listed.items():
    part = part.strip()
    on_band = re.search(re.escape(part) + r".{0,220}?[Aa]bout (\d+) min", body)
    if not on_band:
        mismatched.append((part, "no band with an estimate found"))
    elif on_band.group(1) != minutes:
        mismatched.append((part, f"front matter {minutes}, band "
                                 f"{on_band.group(1)}"))
check(not mismatched,
      "and each one matches the estimate on that part's own band",
      f"these parts are given two different times: {mismatched}. The front "
      "matter reads the same booklet_timing call the bands do, so a "
      "difference means one of them stopped")

print("\nIT LISTS THE PARTS THIS BOOKLET HAS, AND NO OTHERS")

absent = [name for name in ("Spelling Test", "Times Tables Test")
          if name in front]
check(not absent,
      "a booklet with no spelling and no tables test does not explain them",
      f"the page explains {absent}, which this booklet does not contain. "
      "Instructions for a part that is not there send the adult looking for "
      "a page that does not exist")
for part in ("Warm-up Recap", "Class Work", "Homework", "Final Challenge"):
    if part not in front:
        bad(f"{part} is in the booklet and not on the page that explains it")
        break
else:
    ok("and all four of the parts it does have are explained")

print("\nIT CLAIMS NOTHING THAT DID NOT HAPPEN")

# Written as claims and not as words: the page does say "No teacher has
# reviewed it", which is the opposite of a claim of teacher review and has to
# stay sayable.
FORBIDDEN = [
    r"reviewed by (?:a|our) teacher", r"teacher[- ]reviewed",
    r"written by (?:a )?(?:teacher|expert|tutor)", r"expert[- ]written",
    r"curriculum[- ]aligned", r"accredit", r"endorsed", r"approved by",
    r"qualified teacher", r"marked by (?:a|our) teacher",
]
claims = [p for p in FORBIDDEN if re.search(p, front, re.IGNORECASE)]
check(not claims,
      "no teacher review, no accreditation, no human authoring is claimed",
      f"the page makes these claims, none of which is true of this product: "
      f"{claims}. A parent who works that out later has been sold something "
      "quietly")

check("by machine" in front and "No teacher has reviewed it" in front,
      "and it says plainly what did write and check it",
      "the page does not say the booklet is written and checked by machine, "
      "or does not say that no teacher reviewed it. Saying nothing is not the "
      "same as being honest: this is the one page where a parent decides what "
      "the product is")

check("—" not in front and "–" not in front,
      "and it carries no em or en dashes",
      "the front matter carries a dash the house style does not use")

print("\nTHE TICK IS PROMISED ONLY WHERE THE KEY EARNS IT")

CHECKED = "Every answer in this key has been checked"
PARTIAL_LINE = "A tick beside an answer means that answer was checked"

for name, data, path in (("all checked", CLEAN, CLEAN_PDF),
                         ("one unchecked", PARTIAL, PARTIAL_PDF)):
    _, page = how_to_page(path)
    absolute = CHECKED in page
    hedged = PARTIAL_LINE in page
    said = "every answer is checked" if absolute else "only that a tick marks one"
    check(absolute == every_answer_checked(data) and hedged != absolute,
          f"{name}: the page promises "
          f"{'every answer checked' if absolute else 'a tick where it was'}, "
          "which is what the key behind it shows",
          f"{name}: every_answer_checked is {every_answer_checked(data)} and "
          f"the page says {said}. A parent told in the product's own notation "
          "that the front of the booklet is false does not have to find a "
          "wrong answer to want their money back")
    # And the colophon at the end of the key agrees, because both ask the same
    # function. Two pages of the same booklet disagreeing about the same key is
    # the defect this pair exists to prevent.
    tail = " ".join(page_texts(path)[-3:])
    check((CHECKED in tail) == absolute,
          f"{name}: and the colophon at the back of the key says the same",
          f"{name}: the front matter and the key's colophon disagree about "
          "whether every answer was checked")

print("\nAN EXAM PAPER GETS NEITHER PAGE")

# Decided, not overlooked. A practice examination is a different document: it
# opens with a formal instructions page carrying reading time, working time,
# marks and materials, which is what a candidate expects to see and where the
# equivalent information already is. A contents page and a page of advice
# addressed to a parent would both undercut it.
paper = ExamPaper(
    subject="Mathematics Methods", year_level="Year 12", student_name="Alex",
    unit="Units 3 and 4", reading_minutes=10, working_minutes=100,
    materials=["Pens, pencils, eraser"],
    sections=[ExamSection(name="Section One: Calculator-free",
                          working_minutes=50,
                          questions=[vq("Differentiate y = 3x^2.",
                                        answer="6x", marks=3)])])
exam_text = page_texts(render_exam_pdf(paper, tmp / "exam.pdf"))
check(not any(HEADING in t for t in exam_text)
      and not any("Contents" in t.split() for t in exam_text),
      "the exam paper keeps its formal front page and gets neither the "
      "contents nor the how-to",
      "an examination paper was given booklet front matter. It opens with "
      "reading time, working time, marks and materials, which is what a "
      "candidate expects and where that information already is")
check("Reading time" in exam_text[0] and "Total marks" in exam_text[0],
      "and that formal front page is still intact",
      "the exam's instructions page lost its reading time or its mark total")

if _failed:
    print(f"\n{len(_failed)} FRONT MATTER CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} FRONT MATTER CHECKS PASSED")
sys.exit(0)
