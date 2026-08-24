"""Checks that "Now let's try one together" is something the child fills in.

It used to print a second fully worked example: the same demonstration twice,
with every value already on the page, so a section named "together" asked the
child for nothing. The guided example now carries [[values]] the child works
out, printed as ruled gaps, with the completed version in the answer key.

    PYTHONPATH=. python scripts/check_guided_blanks.py
"""
import re
import sys
from pathlib import Path

from booklet_gen import formatter as F
from booklet_gen.schemas import (
    BookletData, Question, SubtopicOutput, SubtopicTeaching, ValidatedQuestion,
    WorkedExample)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def texts(flowable) -> str:
    """Every Paragraph's source text inside a rendered box, joined."""
    found = []

    def walk(node):
        for attr in ("_cellvalues", "_content"):
            for row in getattr(node, attr, None) or []:
                for cell in (row if isinstance(row, list) else [row]):
                    for item in (cell if isinstance(cell, list) else [cell]):
                        walk(item)
        if hasattr(node, "text"):
            found.append(node.text)
    walk(flowable)
    return "\n".join(found)


STYLES = F._make_styles()

GUIDED = WorkedExample(
    question="Work out 27 x 3.",
    steps=["Multiply the ones: 7 x 3 = [[21]], write 1 and carry the [[2]].",
           "Multiply the tens: 2 x 3 = 6, add the carried 2 = [[8]]."],
    answer="[[81]]",
)

print("\nTHE GUIDED EXAMPLE IS A GAP TO FILL, NOT AN ANSWER TO READ")

box = texts(F._worked_example_flowable(STYLES, GUIDED, "Now let's try one "
                                       "together", guided=True))
for value in ("21", "81", "8"):
    assert f">{value}<" not in box and f" {value}." not in box, (
        f"the value {value} is still printed on the page the child writes on: "
        f"they read the answer instead of working it out.\n{box}")
assert "<u>" in box, f"no ruled gap was drawn at all:\n{box}"
assert box.count("<u>") == 4, f"expected 4 gaps (3 steps + answer):\n{box}"
ok("every [[value]] prints as a ruled gap, and none of the values leak")

# The method has to stay readable, or the child is guessing rather than
# working: only the results are hidden, never the instruction around them.
assert "Multiply the ones" in box and "write 1 and carry" in box, box
# The formatter normalises "x" to the multiplication glyph, so match that
# rather than what the model wrote.
assert "7 × 3" in box, "the numbers the question gave were blanked out too"
ok("the words and the given numbers stay, only the results are hidden")

assert "[[" not in box and "]]" not in box, f"raw markers reached the page:\n{box}"
ok("no raw [[ ]] markers reach the page")

print("\nTHE ANSWER KEY CARRIES THE COMPLETED VERSION")

key = texts(F._worked_example_flowable(STYLES, GUIDED, "Completed",
                                       guided=True, reveal=True))
for value in ("21", "81", "8"):
    assert value in key, f"{value} is missing from the key:\n{key}"
assert "<u>" not in key, f"the key printed gaps instead of answers:\n{key}"
assert "[[" not in key, f"raw markers reached the key:\n{key}"
ok("the key prints every value the page left blank")

print("\nTHE WORKED EXAMPLE ABOVE IT IS NEVER BLANKED")

# "I do" is the one complete model of the method on the page, and the practice
# that follows starts from it. Blanking it leaves the child nothing to copy.
demo = texts(F._worked_example_flowable(
    STYLES, WorkedExample(question="Work out 14 x 2.",
                          steps=["4 x 2 = [[8]]"], answer="[[28]]"),
    "Worked example"))
assert "8" in demo and "28" in demo, f"the I-do example was blanked:\n{demo}"
assert "<u>" not in demo, f"the I-do example drew a gap:\n{demo}"
assert "[[" not in demo, f"raw markers reached the I-do example:\n{demo}"
ok("a stray marker in the worked example prints its value, never a gap")

print("\nA MODEL THAT IGNORES THE MARKERS STILL GETS A BOX THAT ASKS SOMETHING")

# Left as-is this degrades silently back to the defect: a second complete
# demonstration under a heading that says "together".
bare = texts(F._worked_example_flowable(
    STYLES, WorkedExample(question="Work out 27 x 3.",
                          steps=["Multiply the ones: 7 x 3 = 21."],
                          answer="81"),
    "Now let's try one together", guided=True))
assert "<u>" in bare, (
    "a guided example with no [[ ]] printed as a finished demonstration, "
    f"which is the defect this whole change removes:\n{bare}")
assert "81" not in bare, f"the answer is still printed:\n{bare}"
ok("a guided example with no markers still has its answer blanked")

print("\nTHE GAP IS SIZED TO THE ANSWER, WITHIN LIMITS")

wide = texts(F._worked_example_flowable(
    STYLES, WorkedExample(question="q", steps=["a = [[x]]"], answer="[[y]]"),
    "l", guided=True))
gap = re.search(r"<u>((?:&nbsp;)+)</u>", wide)
assert gap and gap.group(1).count("&nbsp;") == F._BLANK_MIN_CHARS, (
    "a one-character answer got a gap too small to write in")
long_answer = "[[" + "z" * 80 + "]]"
capped = texts(F._worked_example_flowable(
    STYLES, WorkedExample(question="q", steps=[f"a = {long_answer}"],
                          answer="b"),
    "l", guided=True))
widths = [m.count("&nbsp;") for m in re.findall(r"<u>((?:&nbsp;)+)</u>", capped)]
assert max(widths) == F._BLANK_MAX_CHARS, (
    f"an 80-character answer drew a gap {max(widths)} wide and would run off "
    "the page")
ok("gaps grow with the answer but stay within the page")

print("\nTHE BOOKLET'S ANSWER KEY INCLUDES THE COMPLETED GUIDED EXAMPLES")

vq = ValidatedQuestion(
    question=Question(question="What is 5 x 5?", answer="25", working="5x5=25"),
    verified=True)
data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic="Multiplication", subtopic="Two-digit multiplication",
        teaching=SubtopicTeaching(
            intro_paragraphs=["We multiply one digit at a time."],
            key_points=["Carry when the product is ten or more."],
            worked_example=WorkedExample(
                question="Work out 14 x 2.", steps=["4 x 2 = 8"], answer="28"),
            guided_examples=[GUIDED]),
        questions=[vq])])
# Rendered as a real PDF, because the key is assembled inside render_pdf and
# what matters is what actually lands on the page.
import tempfile  # noqa: E402

import fitz  # noqa: E402

out = Path(tempfile.mkdtemp()) / "b.pdf"
F.render_pdf(data, str(out))
doc = fitz.open(out)
pages = [page.get_text() for page in doc]
doc.close()
text = "\n".join(pages)

assert "try one together" in text.lower(), (
    "the answer key has no section for the guided examples, so nothing in the "
    "booklet says what belongs in the gaps")
assert "81" in text, "the completed value is nowhere in the booklet"
assert "[[" not in text, "raw markers reached the printed booklet"
ok("a rendered booklet's key names the section and carries its values")

# Whole-document text cannot tell the page the child writes on from the key at
# the back: the value appears in both, so a booklet that prints the answer in
# the lesson still reads as correct. Split on the key and check each side. This
# is the assertion that catches the guided box being rendered as a plain worked
# example again, which the joined text passes happily.
key_starts = [i for i, page in enumerate(pages)
              if "Answers & Worked Solutions" in page]
assert key_starts, f"no answer key page found in {len(pages)} pages"
# The guided box itself, not the pages it sits on. A bare search across the
# body reads any "21" as the leaked value, so this failed on the 21st of the
# month against a booklet that was rendering perfectly: the cover prints the
# date. Restricting to the page is not enough either, because the footer
# prints "Page 21" and a subtopic heading prints "about 21 min". The value has
# to be looked for in the box that would leak it and nowhere else.
boxes = []
for page in pages[1:key_starts[0]]:
    low = page.lower()
    start = low.find("try one together")
    if start == -1:
        continue
    # The box ends where the practice under it begins; failing that, at the
    # page footer, which is the last line and is never part of the box.
    end = low.find("now you try", start)
    boxes.append(page[start:end if end != -1
                      else page.rfind("\n", start) or len(page)])
assert boxes, "the guided example is not in the body of the booklet at all"
body = "\n".join(boxes)
answers = "\n".join(pages[key_starts[0]:])
for value in ("21", "81"):
    assert not re.search(rf"(?<!\d){value}(?!\d)", body), (
        f"{value} is printed on the page the child writes on: the guided "
        "example is being rendered as a finished demonstration again, which "
        "is exactly the defect this change removes")
assert "81" in answers, "the completed value never reaches the answer key"
ok("the values appear only in the key, never on the page the child fills in")

print(f"\nALL {_passed} GUIDED-BLANK CHECKS PASSED")
sys.exit(0)
