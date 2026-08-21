"""Checks the booklet's contents page, and that its page numbers are real.

Every workbook a parent has bought opens with a contents. The booklet has
always known its own parts, its topics and the page each of them starts on,
and printed none of it: page 1 was the cover and page 2 was the first question,
so a tutor mid-session could only find the homework by flicking, and a parent
had no way to see how much of what they paid for was in front of them.

The page numbers are the whole risk. A contents whose numbers are one out is
worse than no contents at all, because it is a promise the document breaks in
front of the reader, and there are three ways to get them wrong:

  * guessing them from the story instead of the layout,
  * measuring them on a build that did not have the contents page in it, so
    every number is one short,
  * and measuring them before the blank verso that keeps the answer key off
    the back of a page the child wrote on, so everything past the student half
    is one short.

So nothing here trusts the number printed. Each listed heading is found in the
PDF by the type it is set in, and the page it was found on is compared with the
page the contents claims. The two fixtures are chosen to cover the third case:
one paginates with a blank verso before the key and one without.

    PYTHONPATH=. python scripts/check_contents_page.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf

from booklet_gen.formatter import (ANSWERS_KEY, CONTENTS_MIN_ROWS,
                                   _ANSWERS_LABEL, contents_rows, render_pdf)
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


def vq(text, answer="42", working="42", difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty), verified=True)


def teaching(marker):
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the amount of space inside a three "
                          "dimensional object, and it is measured in cubic "
                          "units."],
        key_points=["Volume equals length times width times height.",
                    "Write your answer in cubic units."],
        worked_example=WorkedExample(
            question=f"The {marker} box is 5 cm by 3 cm by 4 cm. What is its "
                     "volume?",
            steps=["Multiply the length by the width: 5 x 3 = 15.",
                   "Multiply that result by the height: 15 x 4 = 60."],
            answer="60 cubic centimetres"))


SHORT = ["What is {} x 7?", "What is {} + 68?", "What is 480 - {}?",
         "Round {} to the nearest hundred.", "What is {} divided by 4?"]


def questions(seed, n):
    out = []
    for j in range(n):
        k = seed * 7 + j
        if j % 4 == 3:
            out.append(vq(f"Explain how you know {300 + k} is larger than "
                          f"{290 + k}.", answer="It has more hundreds."))
        else:
            out.append(vq(SHORT[k % len(SHORT)].format(24 + k * 3),
                          answer=str(7 * (24 + k * 3)), difficulty="easy"))
    return out


def long_booklet():
    """Three topics across six subtopics, every part present."""
    subs = [("Fractions", "Comparing fractions", "Rosella"),
            ("Fractions", "Adding fractions", "Quokka"),
            ("Volume", "Volume of a prism", "Numbat"),
            ("Volume", "Capacity and litres", "Bilby"),
            ("Number and Place Value", "Four-digit numbers", "Wombat"),
            ("Number and Place Value", "Rounding to hundreds", "Bandicoot")]
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(topic=t, subtopic=s, teaching=teaching(m),
                                 questions=questions(i, 7),
                                 homework_questions=questions(i + 10, 6),
                                 estimated_minutes=10)
                  for i, (t, s, m) in enumerate(subs)],
        recap_questions=questions(99, 4), challenge_questions=questions(50, 3),
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


def two_subject_booklet():
    """Shorter, two subjects, and it paginates with a blank verso before the
    key: the case where every page past the student half moves down one."""
    return BookletData(
        subject="Numeracy and Literacy", year_level="Year 3",
        student_name="Kieran", program_label="NAPLAN Practice",
        sections=[
            SubtopicOutput(topic="Number", subtopic="Place value",
                           subject="Mathematics", teaching=teaching("Galah"),
                           questions=questions(3, 5),
                           homework_questions=questions(4, 4)),
            SubtopicOutput(topic="Number", subtopic="Money problems",
                           subject="Mathematics", teaching=None,
                           questions=questions(5, 4),
                           homework_questions=questions(6, 3)),
            SubtopicOutput(topic="Language Conventions", subtopic="Apostrophes",
                           subject="English", teaching=teaching("Magpie"),
                           questions=[vq("Write the plural of 'box'.",
                                         answer="boxes"),
                                      vq("Correct: the dog wagged it's tail.",
                                         answer="its")],
                           homework_questions=[vq("Write the plural of 'city'.",
                                                  answer="cities")])],
        recap_questions=questions(7, 3), recap_minutes=5,
        classwork_minutes=45, homework_minutes=60, total_minutes=110)


def tiny_booklet():
    """One topic, no homework, no warm-up: too little to want a contents."""
    return BookletData(
        subject="General Abilities", year_level="Year 6",
        student_name="Priya", program_label="Scholarships",
        sections=[SubtopicOutput(topic="Abstract Reasoning",
                                 subtopic="Shape sequences", teaching=None,
                                 questions=[vq("Which shape comes next?",
                                               answer="A")])],
        classwork_minutes=20, total_minutes=20)


tmp = Path(tempfile.mkdtemp(prefix="folio-contents-"))
BOOKS = {name: (data, render_pdf(data, tmp / f"{name}.pdf"))
         for name, data in (("long", long_booklet()),
                            ("two-subject", two_subject_booklet()))}

# ---------------------------------------------------------------------------
# Reading the printed contents back off the page
#
# The label and the figure are drawn as two strings on one baseline, so they
# are paired by the y they were drawn at rather than by anything the code that
# wrote them believes.
# ---------------------------------------------------------------------------


def lines_on(page):
    """[(y, x, size, font, text)] for every text line on a page."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                span = line["spans"][0]
                out.append((round(line["bbox"][1], 1), line["bbox"][0],
                            round(span["size"], 1), span["font"], text))
    return out


def printed_contents(doc):
    """[(label, page number)] down the contents page, in printed order.

    A list and not a mapping: a topic is listed once under Class Work and again
    under Homework with a different page beside it, and a mapping would keep
    only the second and silently check half the page.
    """
    rows = []
    by_y: dict = {}
    for y, x, size, font, text in lines_on(doc[1]):
        if y < 100:                      # the running head
            continue
        by_y.setdefault(round(y), []).append((x, text))
    for y in sorted(by_y):
        items = sorted(by_y[y])
        if len(items) < 2 or not items[-1][1].strip(". ").isdigit():
            continue
        # The leader dots are drawn as their own string between the label and
        # the figure.
        label = " ".join(t for _, t in items[:-1]).strip(" .")
        rows.append((label, int(items[-1][1])))
    return rows


def heading_pages(doc, text: str, size: float, tol: float = 0.6) -> list:
    """Pages carrying `text` set at `size` point, which is what tells a part
    band (18pt) from the same words on the contents page (11pt) and from the
    answer key's own heading (16pt)."""
    out = []
    for i, page in enumerate(doc):
        for _, _, s, _, t in lines_on(page):
            if t == text and abs(s - size) <= tol:
                out.append(i + 1)
                break
    return out


PART_BAND_PT = 18.0
TOPIC_OPENER_PT = 19.0
# The half-title that opens the answer key, which is what the contents points
# at: it is where the answers section begins, and it is the page a reader
# recognises when they flip to the back. The key's own banner ("Answers &
# Worked Solutions", with the ampersand) is on the page behind it.
ANSWERS_HEADING_PT = 25.0
ANSWERS_HEADING = "Answers and Worked Solutions"

print("\nTHE CONTENTS PAGE IS THERE, AND IT IS PAGE 2")

for name, (data, path) in BOOKS.items():
    doc = pymupdf.open(path)
    heads = [t for _, _, _, _, t in lines_on(doc[1])]
    check("Contents" in heads,
          f"{name}: the contents is the page straight after the cover",
          f"{name}: page 2 does not carry the contents heading. It reads "
          f"{heads[:4]}. The cover hands straight over to the work, which is "
          "what a document somebody exported looks like")
    # One page, not two: a contents that turns over has stopped being a map.
    check("Contents" not in " ".join(
        t for _, _, _, _, t in lines_on(doc[2])),
        f"{name}: it fits on one page",
        f"{name}: the contents runs onto page 3")
    doc.close()

print("\nEVERY PAGE NUMBER IN IT IS THE PAGE THAT HEADING IS ON")

for name, (data, path) in BOOKS.items():
    doc = pymupdf.open(path)
    printed = printed_contents(doc)
    rows = contents_rows(data) + [("part", _ANSWERS_LABEL, ANSWERS_KEY)]
    if not check([label for _, label, _ in rows] == [l for l, _ in printed],
                 f"{name}: the contents lists exactly the booklet's "
                 f"{len(rows)} destinations, in printed order",
                 f"{name}: the contents lists {[l for l, _ in printed]} where "
                 f"the booklet holds {[label for _, label, _ in rows]}"):
        doc.close()
        continue
    wrong = []
    part_start = {}
    for (level, label, key), (_, claimed) in zip(rows, printed):
        if label == _ANSWERS_LABEL:
            actual = heading_pages(doc, ANSWERS_HEADING, ANSWERS_HEADING_PT)
        elif level == "part":
            actual = heading_pages(doc, label, PART_BAND_PT)
            part_start[label] = claimed
        else:
            # A topic is opened once in Class Work and again in Homework, so
            # the occurrence that counts is the first one at or after the part
            # this row sits under.
            floor = part_start.get(key[1], 0)
            actual = [p for p in heading_pages(doc, label, TOPIC_OPENER_PT)
                      if p >= floor]
        if not actual:
            wrong.append((label, "never printed as a heading anywhere"))
        elif claimed != actual[0]:
            wrong.append((label, f"contents says {claimed}, printed on "
                                 f"{actual[0]}"))
    check(not wrong,
          f"{name}: every one of the {len(rows)} page numbers is the page that "
          "heading actually prints on",
          f"{name}: these page numbers point at the wrong page: {wrong}. A "
          "contents that is one out is worse than no contents: it is a "
          "promise the booklet breaks in front of the reader on the first "
          "page they use it")
    doc.close()

print("\nAND THE ANSWER KEY'S NUMBER IS THE PAGE ITS DIVIDER IS ON")

# This section used to assert that one fixture paginated with a blank verso
# before the key, because that blank page shifted every number past the student
# half down by one and the answer key was the only thing the contents listed on
# the far side of it. There is no blank verso any more: the key's half-title
# took its job, and it takes the page directly after the student half in every
# booklet, so nothing shifts. What is left to assert is that the contents
# points at the half-title rather than at the page of answers behind it, which
# is the page a reader recognises when they flip to the back.
for name, (data, path) in BOOKS.items():
    doc = pymupdf.open(path)
    printed = dict(printed_contents(doc))
    divider = heading_pages(doc, ANSWERS_HEADING, ANSWERS_HEADING_PT)
    banner = [i + 1 for i, page in enumerate(doc)
              if "Answers & Worked Solutions" in page.get_text()]
    doc.close()
    check(len(divider) == 1 and banner and banner[0] == divider[0] + 1
          and printed.get(_ANSWERS_LABEL) == divider[0],
          f"{name}: the contents sends the reader to the half-title on page "
          f"{divider[0] if divider else '?'}, with the answers behind it",
          f"{name}: the divider is on {divider}, the answers begin on "
          f"{banner}, and the contents says "
          f"{printed.get(_ANSWERS_LABEL)}")

print("\nTHE NUMBERS ONLY EVER GO FORWARDS")

for name, (data, path) in BOOKS.items():
    doc = pymupdf.open(path)
    order = [n for _, n in printed_contents(doc)]
    doc.close()
    check(order == sorted(order) and min(order) >= 3,
          f"{name}: the listed pages run {order[0]} to {order[-1]} in order",
          f"{name}: the listed pages are {order}. A contents that goes "
          "backwards, or points at the cover or at itself, is reporting the "
          "story order rather than the printed order")

print("\nA BOOKLET TOO SMALL TO NEED ONE DOES NOT PAY A SHEET FOR IT")

# A contents listing three destinations costs a sheet of paper and tells the
# reader nothing they could not get by turning the page. The floor is on the
# number of places to go, not on the page count, because the page count is not
# known until the document has been laid out and the contents is part of what
# lays it out.
tiny = tiny_booklet()
rows = contents_rows(tiny)
check(len(rows) < CONTENTS_MIN_ROWS,
      f"a one-topic booklet has only {len(rows)} destinations, under the "
      f"{CONTENTS_MIN_ROWS} a contents is printed for",
      f"the tiny fixture has {len(rows)} destinations, so it is no longer "
      "testing the floor")
path = render_pdf(tiny, tmp / "tiny.pdf")
doc = pymupdf.open(path)
first = " ".join(doc[1].get_text().split())
doc.close()
check("Contents" not in first,
      "and it prints no contents page: the cover hands straight over to the "
      "work",
      f"a booklet with {len(rows)} destinations still spent a sheet on a "
      f"contents page: {first[:80]}")

if _failed:
    print(f"\n{len(_failed)} CONTENTS PAGE CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} CONTENTS PAGE CHECKS PASSED")
sys.exit(0)
