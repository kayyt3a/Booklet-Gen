"""Checks a booklet still prints when one of its parts is missing.

Three of the four parts are optional, and which ones a booklet has is decided
by what the generator produced, not by the customer:

  * the Warm-up Recap is skipped when there is nothing to revise,
  * the Final Challenge is skipped when no cumulative questions survived
    validation,
  * and Homework is empty whenever every subtopic's validated questions fit
    inside the class-work count, which happens on a short subject and on any
    run where validation dropped enough that nothing spilled over.

The last of those did not print a booklet with a missing part. It printed no
booklet at all. The Homework band was built inside
`if has_homework or data.challenge_questions:`, so a booklet with no homework
and a Final Challenge asked for the band's "PART n OF m" locator, "Homework"
was not in the part list because the booklet has no homework, and `.index()`
raised ValueError out of the middle of render_pdf. The customer had paid,
waited for the generation, and got a failed job and no file.

So this file renders every combination of the optional parts and asserts two
things about each: the render finishes, and the bands actually drawn are the
parts that have questions in them. The second half matters because the cheap
way to stop the crash is to print the Homework band anyway, which puts a
full-width reversed band across the page with nothing under it but the next
part's band, the exact fault check_page_endings exists to catch.

    PYTHONPATH=. python scripts/check_optional_parts.py
"""
import itertools
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen.formatter import (PAGE_MARGIN, PART_CHALLENGE, PART_CLASSWORK,
                                   PART_HOMEWORK, PART_RECAP, part_counts,
                                   render_pdf)
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


def vq(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Start at the biggest place value and work right."],
    key_points=["Compare the digits from the left."],
    worked_example=WorkedExample(question="Which is larger, 3 048 or 3 084?",
                                 steps=["Compare the tens."], answer="3 084"))


def booklet(recap: bool, homework: bool, challenge: bool) -> BookletData:
    sections = [
        SubtopicOutput(
            topic="Number and Place Value", subtopic=name,
            teaching=TEACHING,
            questions=[vq(f"{name} {j}: what is {j} x 37?") for j in range(4)],
            homework_questions=[vq(f"{name} homework {j}: what is {j} + 68?",
                                   difficulty="easy") for j in range(4)]
            if homework else [],
            estimated_minutes=10)
        for name in ("Ordering", "Rounding")]
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=[vq("Calculate 15 x 4 + 7.", difficulty="easy")]
        if recap else [],
        challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its "
                                "volume?", difficulty="hard")]
        if challenge else [],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


PART_INKS = {"Warm-up Recap": PART_RECAP, "Class Work": PART_CLASSWORK,
             "Homework": PART_HOMEWORK, "Final Challenge": PART_CHALLENGE}
MEASURE = A4[0] - 2 * PAGE_MARGIN


def bands_drawn(path) -> set:
    """The part bands actually printed, found by the ink they are filled in."""
    inks = {name: tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
            for name, c in PART_INKS.items()}
    found = set()
    doc = pymupdf.open(path)
    for page in doc:
        for d in page.get_drawings():
            fill = d.get("fill")
            if not fill or d["rect"].width < 0.9 * MEASURE \
                    or d["rect"].height < 1.5 * cm:
                continue
            for name, ink in inks.items():
                if max(abs(a - b) for a, b in zip(fill, ink)) < 0.01:
                    found.add(name)
    doc.close()
    return found


print("\nEVERY COMBINATION OF THE OPTIONAL PARTS RENDERS")

tmp = Path(tempfile.mkdtemp(prefix="folio-parts-"))
crashed, mismatched = [], []
for recap, homework, challenge in itertools.product((True, False), repeat=3):
    tag = f"recap={recap} homework={homework} challenge={challenge}"
    data = booklet(recap, homework, challenge)
    path = tmp / f"{int(recap)}{int(homework)}{int(challenge)}.pdf"
    try:
        render_pdf(data, path)
    except Exception as e:
        crashed.append((tag, f"{type(e).__name__}: {e}"))
        continue
    # What the booklet says it holds, from the same function the score card on
    # the finish page counts out of, so the bands and the mark total agree.
    want = {name for name, count in part_counts(data) if count} & set(PART_INKS)
    got = bands_drawn(path)
    if got != want:
        mismatched.append((tag, f"drew {sorted(got)}, has questions in "
                                f"{sorted(want)}"))

if crashed:
    bad(f"these booklets did not render at all: {crashed}. A part the "
        "generator happened not to fill took the whole file down, and the "
        "customer who paid for it got a failed job")
else:
    ok("all eight combinations of Warm-up, Homework and Final Challenge render")

if mismatched:
    bad(f"the bands printed do not match the parts that have questions: "
        f"{mismatched}. A full-width reversed band introducing nothing is the "
        "most unfinished-looking page in the booklet, and a part with "
        "questions and no band leaves the student no way to see where it "
        "started")
else:
    ok("every part with questions in it gets a band, and no part without any "
       "gets one")

print("\nTHE PART LOCATORS COUNT THE PARTS THIS BOOKLET ACTUALLY HAS")

# "PART 2 OF 3" on the band is what makes the parts countable at a flip
# through. It has to be computed from the parts present, so a booklet with no
# homework does not announce its challenge as part 4 of 4 with part 3 missing.
missing = []
for recap, homework, challenge in itertools.product((True, False), repeat=3):
    data = booklet(recap, homework, challenge)
    path = tmp / f"{int(recap)}{int(homework)}{int(challenge)}.pdf"
    if not path.exists():
        continue
    want = {name for name, count in part_counts(data) if count} & set(PART_INKS)
    doc = pymupdf.open(path)
    text = " ".join(" ".join(p.get_text().split()) for p in doc)
    doc.close()
    for i in range(1, len(want) + 1):
        if f"PART {i} OF {len(want)}" not in text:
            missing.append((f"recap={recap} homework={homework} "
                            f"challenge={challenge}", f"PART {i} OF {len(want)}"))

if missing:
    bad(f"these part locators were not printed: {missing}. The bands are "
        "numbered against a count of the parts this booklet has, so a gap in "
        "the run means a band was printed for a part with nothing in it or "
        "counted against the wrong total")
else:
    ok("each booklet's bands run 1..n of n with no gap")

if _failed:
    print(f"\n{len(_failed)} OPTIONAL PART CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} OPTIONAL PART CHECKS PASSED")
sys.exit(0)
