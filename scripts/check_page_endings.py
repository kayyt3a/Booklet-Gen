"""Checks no page of the booklet ends in a way that reads as a printing fault.

A generated booklet gives itself away at the foot of the page. Three separate
faults were measured on one twenty-one page fixture, and between them they left
4.8cm, 7.2cm, 14.8cm and 12.0cm of unexplained white at four page feet.

  * The mini-lesson's worked example is a single bordered box. A box cannot
    split, so when it does not fit it moves whole to the next page and leaves
    the topic heading, the subtopic heading, the intro paragraph and the key
    points sitting alone above five centimetres of nothing. The child turns
    over to find the example that was being introduced.
  * A heading with nothing under it. "Class Work", "Number and Place Value"
    and "Four-digit numbers and ordering" all three at the foot of a key page
    with no answer beneath any of them, and "Now you try:" as the last thing on
    a page with question 1 overleaf. A heading is a promise about what follows;
    at the foot of a page it is a broken one.
  * A part band with nothing under it. The full-width reversed "Class Work"
    band, its subtitle, and then nine and a half centimetres of blank paper: a
    part announces itself across the whole measure and the page stops. The band
    is laid down before the mini-lesson under it asks for a page of its own, so
    the lesson left and the band stayed. A band and the run it opens are
    measured and moved as one.
  * A page that legitimately ends short. Homework will not start with less than
    7cm left and the Final Challenge will not start with less than 9cm, because
    a part that begins three lines before a page turn is worse than one that
    begins on a fresh page. Those rules are right and stay, and the room they
    give up is at present left as white paper rather than filled with anything.
    All this file asserts about it is that the two constants have not been
    weakened, which is the cheap way to make a whitespace measurement look
    better without removing any dead space.

    PYTHONPATH=. python scripts/check_page_endings.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph
from reportlab.platypus.flowables import _listWrapOn

from booklet_gen.formatter import (BODY_WIDTH, HOMEWORK_MIN_START_CM,
                                   PAGE_MARGIN, PART_CHALLENGE, PART_CLASSWORK,
                                   PART_HOMEWORK, PART_RECAP,
                                   _CHALLENGE_MIN_START_CM, _make_styles,
                                   _question_block, _unwrap, orphan_break,
                                   render_pdf, stack_height)
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
# The fixture
#
# Deliberately awkward: the subtopics differ in how many steps their worked
# example carries and how many guided examples follow it, so the lessons are
# different heights and the page boundaries fall in different places under
# them. That is the condition the strand needs. One subtopic has no class work
# at all, which is the shape the hour cap produces, and its lesson is reprinted
# down in Homework where its practice went.
# ---------------------------------------------------------------------------

def vq(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


SUBTOPICS = [
    # (topic, subtopic, marker, worked-example steps, guided examples)
    ("Fractions", "Comparing fractions", "Rosella", 2, 1),
    ("Fractions", "Adding fractions", "Quokka", 3, 2),
    ("Volume", "Volume of a prism", "Numbat", 5, 1),
    ("Volume", "Capacity and litres", "Bilby", 4, 2),
    ("Number and Place Value", "Four-digit numbers and ordering", "Wombat", 3, 1),
    ("Number and Place Value", "Rounding to the nearest hundred", "Bandicoot", 4, 1),
]
STEPS = ["Multiply the length by the width: 5 x 3 = 15.",
         "Multiply that result by the height: 15 x 4 = 60.",
         "State the volume in cubic units: 60 cm^3.",
         "Check it against an estimate: 5 x 3 x 4 is about 60.",
         "Write the unit down, because a number alone is not a volume."]


def teaching(marker, n_steps, n_guided):
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the amount of space inside a three "
                          "dimensional object. To find the volume of a "
                          "rectangular prism you multiply its length, width "
                          "and height together, and the answer is always in "
                          "cubic units."],
        key_points=["Volume equals length times width times height.",
                    "Write your answer in cubic units, like cm^3.",
                    "Check the unit before you write the answer down."],
        worked_example=WorkedExample(
            question=f"The {marker} box is 5 cm by 3 cm by 4 cm. What is its "
                     "volume?",
            steps=STEPS[:n_steps], answer="60 cubic centimetres"),
        guided_examples=[WorkedExample(
            question=f"Find the volume of a prism {k + 2} cm by 2 cm by 3 cm.",
            steps=["Multiply the length and width.", "Multiply by the height."],
            answer="36 cubic cm") for k in range(n_guided)])


def booklet():
    sections = []
    for i, (topic, subtopic, marker, steps, guided) in enumerate(SUBTOPICS):
        sections.append(SubtopicOutput(
            topic=topic, subtopic=subtopic,
            teaching=teaching(marker, steps, guided),
            # The last subtopic did not fit the hour, so it has no class work
            # and its lesson is reprinted in Homework.
            questions=[] if i == len(SUBTOPICS) - 1 else [
                vq(f"Question {i}.{j}: A box is {j + 2} cm long, 2 cm wide and "
                   "3 cm high. What is its volume in cubic centimetres?",
                   answer=str((j + 2) * 6),
                   working="Volume = length x width x height. The volume is "
                           f"{(j + 2) * 6}.") for j in range(4)],
            homework_questions=[
                vq(f"Homework {i}.{j}: Calculate {j + 1}/12 + {j + 1}/12.",
                   answer=f"{2 * (j + 1)}/12", difficulty="easy",
                   working="Add the numerators over the same denominator.")
                for j in range(5)],
            estimated_minutes=10))
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=[vq("Calculate 15 * 4 + 7.", answer="67",
                            difficulty="easy"),
                         vq("If 5x = 45, what is x?", answer="x = 9",
                            difficulty="easy")],
        challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its "
                                "volume in cubic metres?", answer="75",
                                difficulty="hard",
                                working="Volume = 10 x 5 x 1.5 = 75.")],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


out = Path(tempfile.mkdtemp(prefix="folio-endings-")) / "endings.pdf"
render_pdf(booklet(), out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
PAGES = [page.get_text() for page in doc]
KEY_START = next(i for i, t in enumerate(PAGES) if "Worked Solutions" in t)
BODY = range(1, KEY_START)


def page_of(needle, first=1, last=None):
    for i in range(first, len(PAGES) if last is None else last):
        if needle in " ".join(PAGES[i].split()):
            return i
    return None


print("\nA MINI-LESSON ARRIVES WITH ITS WORKED EXAMPLE")

# The worked-example question carries a marker word unique to its subtopic and
# printed nowhere else in the booklet, so the page it landed on is not a guess.
stranded = []
for topic, subtopic, marker, _, _ in SUBTOPICS:
    box = page_of(f"The {marker} box")
    if box is None:
        stranded.append((subtopic, "the worked example was not printed at all"))
        continue
    page = " ".join(PAGES[box].split())
    missing = [name for name, text in
               (("its subtopic heading", subtopic),
                ("its intro paragraph", "Volume is the amount of space"),
                ("its key points", "Volume equals length times width"))
               if text not in page]
    if missing:
        stranded.append((subtopic, f"page {box + 1} has the box but not "
                                   + ", ".join(missing)))

check(not stranded,
      f"all {len(SUBTOPICS)} mini-lessons print their heading, intro, key "
      "points and worked example on one page",
      f"these lessons were split from their own worked example: {stranded}. "
      "The box cannot break, so it moves whole to the next page and leaves "
      "the headings and the bullets above about five centimetres of white. "
      "The child reads an introduction to an example that is not there")

print("\nNO HEADING IS LEFT AT THE FOOT OF A PAGE WITH NOTHING UNDER IT")

# Every heading in the booklet is set in the display serif: topics, subtopics,
# the coloured part bands and the key's own headings. "Now you try:" is a sans
# label but makes the same promise, so it is named here rather than inferred.
# A heading being the lowest thing in its column is the whole defect: there is
# nothing under it, and the reader turns the page to find out what it named.
PROMISES = {"Now you try:"}
GUTTER = (A4[0] / 2 - 10, A4[0] / 2 + 10)


def lowest_lines(document, index, key_start):
    """The bottom text line of each column on this page.

    The body runs in one column, the answer key in two, so the key is split at
    the gutter: a heading at the foot of the left column with the answers
    continuing at the top of the right is the same orphan as one at the foot
    of a full-width page, and only shows up if the columns are measured apart.
    """
    page = document[index]
    height = page.rect.height
    columns = {}
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            x0, y0, _, y1 = line["bbox"]
            if y0 < PAGE_MARGIN - 6 or y1 > height - PAGE_MARGIN + 6:
                continue
            key = ("right" if x0 > GUTTER[1] else "left") \
                if index >= key_start else "page"
            if key not in columns or y1 > columns[key][0]:
                columns[key] = (y1, line["spans"][0]["font"], text)
    return columns


def stranded_headings(document, key_start):
    """Every heading that is the last thing in its column."""
    out = []
    for i in range(1, len(document)):
        for column, (_, font, text) in lowest_lines(document, i,
                                                    key_start).items():
            if "Serif" in font or text in PROMISES:
                out.append((f"page {i + 1}", column, text[:44]))
    return out


orphans = stranded_headings(doc, KEY_START)

check(not orphans,
      f"no heading is the last thing in its column, across all "
      f"{len(PAGES) - 1} printed pages",
      f"these headings are the last thing in their column, with nothing "
      f"under them: {orphans}. A heading is a promise about what follows it; "
      "at the foot of a page it is a broken one, and on a key page three of "
      "them stacked with no answer beneath reads as a page that failed to "
      "print")

# The Homework part band is placed before anything under it can ask for a page
# break of its own, because a break below the band would leave the band itself
# at the foot of a page. So the one break in front of the band has to cover the
# whole opening run: the band, the first session band, the topic opener, the
# subtopic heading and the first row of questions.
#
# A flat seven centimetres does not cover it. This fixture is tuned so Class
# Work finishes with roughly that much of a page left: with the break measured
# the subtopic heading arrives with its questions, and with a flat seven it
# prints at the foot of the page with nothing under it. The class-work count is
# what moves the boundary, so it is the thing varied here, and a run of them is
# rendered rather than one, because a single count only proves one alignment.


def homework_boundary_booklet(classwork_per_subtopic):
    sections = []
    for i, (topic, subtopic, marker, steps, guided) in enumerate(SUBTOPICS[:4]):
        sections.append(SubtopicOutput(
            topic=topic, subtopic=subtopic,
            teaching=teaching(marker, steps, guided),
            questions=[
                vq(f"Question {i}.{j}: A box is {j + 2} cm long, 2 cm wide and "
                   "3 cm high. What is its volume in cubic centimetres?")
                for j in range(classwork_per_subtopic)],
            # Short and easy, so the first row of Homework is two questions
            # side by side and taller than a two and a half centimetre
            # allowance, which is exactly the case a flat allowance misses.
            homework_questions=[
                vq(f"Calculate {j + 1}/12 + {j + 1}/12.", difficulty="easy")
                for j in range(5)],
            estimated_minutes=10))
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=[vq("Calculate 15 * 4 + 7.", difficulty="easy")],
        challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its "
                                "volume in cubic metres?", difficulty="hard")],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


boundary = []
for n in (2, 3, 5):
    path = out.parent / f"homework-boundary-{n}.pdf"
    render_pdf(homework_boundary_booklet(n), path)
    other = pymupdf.open(path)
    start = next(i for i, page in enumerate(other)
                 if "Worked Solutions" in page.get_text())
    boundary += [(n,) + o for o in stranded_headings(other, start)]
    other.close()

check(not boundary,
      "and none is stranded where Homework starts, across three booklets whose "
      "Class Work ends at different points down the page",
      f"these headings were left at the foot of a page where Homework begins: "
      f"{boundary} (class work per subtopic, page, column, text). The one "
      "break in front of the Homework band has to cover everything down to the "
      "first question, because nothing below the band can ask for a break "
      "without stranding the band itself")

print("\nNO PART BAND IS THE LAST THING ON ITS PAGE")

# The worst-looking page in the booklet was page 2 of a Year 5 maths fixture:
# the Warm-up finished, the full-width reversed "Class Work" band printed with
# its subtitle, and then nine and a half centimetres of blank paper. A part
# announced itself across the whole measure and the page stopped.
#
# The band is a heading like any other, but the strand check above cannot see
# it: the lowest line of a band is its sans subtitle, not the serif title, so
# the band reads as ordinary text at the foot of a page. It is found here by
# the ink it is drawn in instead, which is also the only thing that knows where
# the band ENDS.
#
# The cause was two rules colliding. The band was laid down first, and the
# mini-lesson underneath it then asked for a page of its own so it would arrive
# with its worked example. The lesson went and the band stayed. So the break in
# front of a band has to cover the run the band opens: the band, the session
# band where there is one, the topic opener, the subtopic heading and the first
# block of content, measured as one.

PART_INKS = {"Warm-up Recap": PART_RECAP, "Class Work": PART_CLASSWORK,
             "Homework": PART_HOMEWORK, "Final Challenge": PART_CHALLENGE}
MEASURE = A4[0] - 2 * PAGE_MARGIN


def part_bands(document):
    """Every part band drawn in the booklet: (page index, rect, part name)."""
    inks = {name: tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
            for name, c in PART_INKS.items()}
    found = []
    for i, page in enumerate(document):
        for d in page.get_drawings():
            fill = d.get("fill")
            if not fill or d["rect"].width < 0.9 * MEASURE \
                    or d["rect"].height < 1.5 * cm:
                continue
            for name, ink in inks.items():
                if max(abs(a - b) for a, b in zip(fill, ink)) < 0.01:
                    found.append((i, d["rect"], name))
    return found


def stranded_bands(document):
    """Every part band with nothing printed under it on its own page."""
    out = []
    for i, rect, name in part_bands(document):
        page = document[i]
        floor = page.rect.height - PAGE_MARGIN + 6
        under = [line for block in page.get_text("dict")["blocks"]
                 for line in block.get("lines", [])
                 if "".join(s["text"] for s in line["spans"]).strip()
                 and line["bbox"][1] > rect.y1 - 1 and line["bbox"][3] < floor]
        if not under:
            gap = (page.rect.height - PAGE_MARGIN - rect.y1) / cm
            out.append((f"page {i + 1}", name, f"{gap:.1f}cm blank under it"))
    return out


bands = [(0,) + b for b in stranded_bands(doc)]
for n in (2, 3, 5):
    other = pymupdf.open(out.parent / f"homework-boundary-{n}.pdf")
    bands += [(n,) + b for b in stranded_bands(other)]
    other.close()

# The Warm-up is what pushes the Class Work band down the page, so its length is
# what is varied here, the same way the class-work count is varied above for the
# Homework boundary. Six recap questions is the count that reproduces the
# founder's page in this fixture: the recap fills two thirds of page 2, the
# mini-lesson under the Class Work band needs more than the third that is left,
# and with the band placed before the lesson's break the lesson goes to page 3
# and leaves the band above 9.9cm of nothing.
for n in (2, 4, 6):
    data = booklet()
    data.recap_questions = [
        vq(f"Calculate {12 + j} x 4 + {j}.", answer=str(48 + 5 * j),
           difficulty="easy") for j in range(n)]
    path = out.parent / f"recap-{n}.pdf"
    render_pdf(data, path)
    other = pymupdf.open(path)
    bands += [(f"recap {n}",) + b for b in stranded_bands(other)]
    other.close()

check(not bands,
      "no part band is the last thing on its page, across seven booklets whose "
      "parts begin at different points down the page",
      f"these part bands printed with nothing under them: {bands} (fixture, "
      "page, part, blank). A part that announces itself across the full "
      "measure and then leaves the rest of the sheet empty is the most "
      "unfinished-looking page in the booklet. The break in front of a band "
      "has to cover the whole run the band opens, or the run moves to a fresh "
      "page and the band stays behind")

print("\nA HEADING RESERVES WHAT THE BLOCK UNDER IT INSISTS ON, NOT MORE")

# The room kept free in front of a heading has to be what the block underneath
# will actually insist on. A question is a KeepTogether, and the frame decides
# whether one fits by wrapping its contents unbounded; the working panel inside
# answers that with its floor rather than its full height, because the panel
# gives when a page runs out and stops at three quarters of its room.
#
# So a heading measured against the panel's full height reserves up to a
# centimetre the question is never going to ask for, and moves itself and its
# question to a fresh page over space that was there all along. That is where
# the three and four centimetre tails at the foot of the Homework pages came
# from. Nothing here shrinks a panel further than the frame was already
# entitled to shrink it; the 75% floor is untouched and check_working_panels
# guards it.
#
# The number is checked against ReportLab's own measurement of the block rather
# than against the formatter's, so this cannot pass by both sides agreeing on
# the same mistake.

styles = _make_styles()
probe = _question_block(styles, 1, vq("What is 24 x 7?", answer="168"),
                        None, 0.0, 1, "Mathematics")
heading = Paragraph("Subtracting with regrouping", styles["subtopic"])
_, insists = _listWrapOn(probe._content, BODY_WIDTH, Canvas("/dev/null"))
reserved = orphan_break([heading], follow=[probe]).height
head_h = stack_height([heading])
give = stack_height(_unwrap([probe])) - insists

check(reserved >= head_h + insists,
      f"the break in front of a heading keeps the whole block under it: "
      f"{reserved / cm:.2f}cm reserved for {(head_h + insists) / cm:.2f}cm",
      f"only {reserved / cm:.2f}cm is reserved where the heading and the block "
      f"under it need {(head_h + insists) / cm:.2f}cm. The heading will print "
      "at the foot of a page and its question overleaf")

check(reserved <= head_h + insists + 0.3 * cm,
      f"and reserves no more than that: {reserved / cm:.2f}cm against "
      f"{(head_h + insists) / cm:.2f}cm insisted on",
      f"{reserved / cm:.2f}cm is reserved where the block only insists on "
      f"{(head_h + insists) / cm:.2f}cm. The extra is the working panel's give, "
      "which the panel hands back at the foot of a page anyway. Reserving it "
      "sends the heading and its question to a fresh page and leaves that much "
      "white behind for nothing")

check(give >= 0.4 * cm,
      f"and the give the panel hands back is real: {give / cm:.2f}cm on one "
      "medium question",
      f"the panel only gives {give / cm:.2f}cm, so the two checks above are "
      "measuring nothing. Either the shrink floor moved or the question used "
      "here stopped having a panel that shrinks")

print("\nTHE RULES THAT LEAVE A PAGE SHORT ARE STILL IN FORCE")

# Homework will not start with less than 7cm of page left and the Final
# Challenge will not start with less than 9cm. Those rules are deliberate and
# they stay: a part that begins three lines before a page turn is worse than one
# that begins on a fresh page.
#
# What they leave behind is up to 7cm and 9cm of blank paper at a page foot, and
# that space is at present ACCEPTED rather than filled. A self-assessment strip
# ("How did that go? Got it / Nearly / Go over this again") used to sit in it,
# and the assertions that pinned it to the page were here. The strip was removed
# at the founder's direction: he did not want it in the booklet. So the
# assertions are gone with it rather than softened into something that passes
# whatever happens.
#
# This one stays, and it is the reason the section is still here. The cheap way
# to make a whitespace measurement look better is to weaken the constants that
# create the gap, which does not remove dead space, it moves it to the top of
# the next page where it is a part starting three lines before a turn. If the
# foot space is to be closed it has to be closed by the page packing tighter.
check(HOMEWORK_MIN_START_CM >= 7.0 and _CHALLENGE_MIN_START_CM >= 9.0,
      f"Homework still refuses to start with under {HOMEWORK_MIN_START_CM}cm "
      f"left, and the Final Challenge under {_CHALLENGE_MIN_START_CM}cm",
      f"the minimum start rules were weakened to {HOMEWORK_MIN_START_CM}cm and "
      f"{_CHALLENGE_MIN_START_CM}cm. That does not remove dead space, it moves "
      "it: a part that starts three lines before a page turn is worse than one "
      "that starts on a fresh page")

doc.close()

if _failed:
    print(f"\n{len(_failed)} PAGE ENDING CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} PAGE ENDING CHECKS PASSED")
sys.exit(0)
