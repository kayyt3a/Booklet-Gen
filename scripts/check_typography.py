"""Checks the booklet sets its headings in a display face, not one sans.

Every heading and every line of body text used to be DejaVu Sans. One family
throughout is what an unstyled export looks like, and it is the loudest thing
telling a parent that the booklet they printed was generated rather than
published. Headings now carry a serif; body text stays sans, because that is
what is comfortable to read at 10pt on a page a child works through.

Two things this has to keep true:

  * the serif has to be a font that actually exists in production. It comes
    from fonts-dejavu-core, which the Dockerfile already installs, and if the
    lookup ever fails everything falls back to Helvetica together rather than
    printing half the booklet in a font that was never found.
  * the cover keeps its own sans title, which is what the founder's reference
    mockups show. A serif heading rule applied blindly would have changed it.

    PYTHONPATH=. python scripts/check_typography.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf

from booklet_gen import formatter as F
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nTHE DISPLAY FACE IS REAL AND SEPARATE FROM THE BODY FACE")

F._register_fonts()
assert F._UNICODE_FONT, (
    "font registration fell back to Helvetica, so this run cannot say "
    "anything about the fonts a customer gets")
assert F.FONT_DISPLAY != F.FONT_BOLD, (
    f"headings and body bold are the same face ({F.FONT_DISPLAY}), which is "
    "the single-typeface look this change exists to remove")
assert "Serif" in F.FONT_DISPLAY, f"the display face is not a serif: {F.FONT_DISPLAY}"
assert "Serif" not in F.FONT_REGULAR, (
    f"body text became a serif ({F.FONT_REGULAR}); it stays sans, which is "
    "what reads comfortably at 10pt for a child working through a page")
ok("headings are a serif, body text is a sans, and they are different fonts")

# The Dockerfile installs fonts-dejavu-core. Both faces must come out of that
# package or production silently loses the heading font.
from matplotlib import font_manager  # noqa: E402

for family, weight in (("DejaVu Serif", "normal"), ("DejaVu Serif", "bold"),
                       ("DejaVu Sans", "normal")):
    prop = font_manager.FontProperties(family=family, weight=weight)
    found = font_manager.findfont(prop, fallback_to_default=False)
    assert Path(found).exists(), f"{family} {weight} is missing: {found}"
ok("both serif faces and the sans resolve to real files on disk")

print("\nTHE STYLES ACTUALLY USE IT")

styles = F._make_styles()
for name in ("topic", "subtopic", "part_band", "key_part", "answers_heading"):
    assert styles[name].fontName == F.FONT_DISPLAY, (
        f"the {name!r} heading is still set in {styles[name].fontName}")
ok("topic, subtopic, part bands and the key's headings are all display")

for name in ("intro_para", "question", "key_point", "working"):
    assert styles[name].fontName in (F.FONT_REGULAR, F.FONT_BOLD), (
        f"{name!r} is set in {styles[name].fontName}, but body copy stays sans")
ok("body copy, questions and working stay in the sans")

# A scale only works if its steps are far enough apart to read as ranks.
assert styles["topic"].fontSize - styles["subtopic"].fontSize >= 4, (
    f"topic {styles['topic'].fontSize} and subtopic "
    f"{styles['subtopic'].fontSize} are too close to read as different levels")
assert styles["subtopic"].fontSize > styles["question"].fontSize, (
    "a subtopic heading is no larger than the questions under it")
ok("the heading sizes are far enough apart to rank")

print("\nBOTH FONTS ARE EMBEDDED IN THE PDF A CUSTOMER GETS")

vq = ValidatedQuestion(question=Question(question="Which is larger: 41 or 38?",
                                         answer="41", working="4 tens beats 3."),
                       verified=True)
data = BookletData(
    subject="Mathematics", year_level="Year 3", student_name="Kieran",
    program_label="Academic Accelerate",
    sections=[SubtopicOutput(
        topic="Number and Place Value", subtopic="Comparing numbers",
        teaching=SubtopicTeaching(
            intro_paragraphs=["Look at the largest place value first."],
            key_points=["Compare left to right."],
            worked_example=WorkedExample(question="Which is larger, 62 or 59?",
                                         steps=["Compare the tens."],
                                         answer="62")),
        questions=[vq])])
out = Path(tempfile.mkdtemp()) / "typo.pdf"
F.render_pdf(data, out)
def drawn_fonts(page) -> set:
    """The fonts this page actually sets text in.

    Not page.get_fonts(): that reads the resource dictionary, and ReportLab
    shares one across the document, so every page lists the serif whether or
    not a single glyph on it is set in one. Reading the spans is the only way
    to tell what a reader would see.
    """
    return {span["font"]
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line["spans"]}


doc = pymupdf.open(out)
fonts = set().union(*(drawn_fonts(page) for page in doc))
cover_fonts = drawn_fonts(doc[0])
doc.close()

assert any("Serif" in f for f in fonts), (
    f"no serif was embedded, so no heading is set in one: {sorted(fonts)}")
assert any("Sans" in f for f in fonts), f"no sans embedded: {sorted(fonts)}"
ok("a rendered booklet embeds both the serif and the sans")

print("\nTHE COVER KEEPS ITS OWN TITLE FACE")

assert not any("Serif" in f for f in cover_fonts), (
    "the cover picked up the serif. Its title is a bold sans in the founder's "
    f"reference mockups and is drawn from FONT_BOLD: {sorted(cover_fonts)}")
ok("the cover is untouched by the heading change")

print(f"\nALL {_passed} TYPOGRAPHY CHECKS PASSED")
sys.exit(0)
