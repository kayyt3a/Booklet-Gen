"""Checks the maths on the page is set as mathematics, not as source code.

Five real booklets (Years 1, 3, 5, 7 and 9 Mathematics) were generated from
production and read by a tutor. The Year 9 one printed:

    Simplify the expression: (x³ × x^4) ÷ x²
    Volume of cylinder = π * 3^2 × 10 = 3.14 × 9 × 10 = 282.6 cm³.
    (f²)^3 = f^(2 × 3) = f^6.
    Simplify the expression: b^9 / b²

That is 70 caret indices against 40 real superscripts, thirteen lines carrying
both notations at once, and a formula that reads as Python. A parent cannot
mark that, and a tutor reading it decides the booklet was not written by
anyone who does mathematics. It sits in the maths itself, which is the one
part of the product a customer is paying for.

The fix is deterministic and lives at render time, the same shape as _dedash:
correct in the prompt, verified on the page. This check is the "on the page"
half. It renders a booklet per year level built out of lines taken verbatim
from those five PDFs, then reads the rendered pages back and requires:

  * no caret index anywhere, at any year level;
  * no asterisk used as multiplication, and no bare "sqrt(" or "->";
  * never two index notations inside one expression;
  * "3 x 4" in Year 1 and Year 3 is multiplication, and "3x + 4" in Year 9 is
    not. One rule has to be right in both booklets, because one product
    prints both, and getting it wrong turns an equation into a wrong answer;
  * a spaced slash divides, an unspaced one does not, so fractions, dates and
    rates come through exactly as written;
  * every superscript glyph the normaliser can emit exists in all four
    embedded DejaVu faces and is drawn as a small raised glyph rather than as
    a missing-glyph box;
  * an index that has no Unicode superscript spelling, set with ReportLab
    markup instead, lands at the same height and size as one that does.

    PYTHONPATH=. python scripts/check_math_notation.py
"""
import re
import sys
import tempfile
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTFont
from matplotlib import font_manager
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate

from booklet_gen import formatter as F
from booklet_gen import mathnotation as N
from booklet_gen.formatter import _escape, _make_styles, _register_fonts, render_pdf
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


def check(cond, msg, detail=""):
    if cond:
        ok(msg)
    else:
        bad(f"{msg}{(': ' + detail) if detail else ''}")


_register_fonts()
tmp = Path(tempfile.mkdtemp(prefix="notation-"))

# ---------------------------------------------------------------------------
# The rule for x, proved at both ends of the product
#
# This is the dangerous half of the change. "3 x 4" in a Year 3 booklet is a
# multiplication; "3x + 4" in a Year 9 booklet is a term and an unknown. A
# naive rule corrupts algebra, and a corrupted equation is a wrong answer
# printed under a verification tick.
# ---------------------------------------------------------------------------
print("\nx MULTIPLIES IN THE PRIMARY BOOKLET AND STAYS AN UNKNOWN IN THE SENIOR ONE")

X_CASES = [
    # Year 1 to Year 5: x between two operands is a multiplication sign.
    ("What is 3 x 4?", "What is 3 × 4?", "Year 1 arrays"),
    ("5x3 = 15", "5 × 3 = 15", "no spaces, still two numbers"),
    ("Volume = length x width x height", "Volume = length × width × height",
     "the formula a Year 5 is taught the words of"),
    ("Perimeter = 2 x (length + width)", "Perimeter = 2 × (length + width)",
     "a bracket is an operand too"),
    ("The box is 40 cm x 20 cm.", "The box is 40 cm × 20 cm.", "measurements"),
    # Year 7 to Year 10: every one of these must come through untouched.
    ("Solve for x: 3x + 4 = 19", "Solve for x: 3x + 4 = 19", "a term and an unknown"),
    ("5x = 45", "5x = 45", "the shipped Year 5 pre-algebra line"),
    ("Expand 2(x + 3) and collect like terms.",
     "Expand 2(x + 3) and collect like terms.", "x beside a bracket"),
    ("Find the value of x when y = mx + c.",
     "Find the value of x when y = mx + c.", "x named in prose"),
    ("The x-axis crosses the y-axis at the origin.",
     "The x-axis crosses the y-axis at the origin.", "x in a hyphenated word"),
    ("Six boxes of matches", "Six boxes of matches", "x inside ordinary words"),
    ("Simplify x × x × x.", "Simplify x × x × x.", "x times itself is left alone"),
]
for raw, want, why in X_CASES:
    got = _escape(raw)
    if got != want:
        bad(f"the x rule broke {why}: {raw!r} became {got!r}, wanted {want!r}")
ok("x multiplies between two operands and is an unknown everywhere else")

# ---------------------------------------------------------------------------
# Indices, division and the rest of the source-code notation
# ---------------------------------------------------------------------------
print("\nINDICES ARE RAISED, DIVISION HAS ONE SIGN, AND NOTHING READS AS CODE")

NOTATION_CASES = [
    # Verbatim from the Year 9 booklet.
    ("Simplify the expression: (x³ × x^4) ÷ x²",
     "Simplify the expression: (x³ × x⁴) ÷ x²",
     "the two-notations-in-one-expression line"),
    ("Volume of cylinder = π * 3^2 × 10 = 3.14 × 9 × 10 = 282.6 cm³.",
     "Volume of cylinder = π × 3² × 10 = 3.14 × 9 × 10 = 282.6 cm³.",
     "pi times a power, written as Python"),
    ("Simplify the expression: b^9 / b²", "Simplify the expression: b⁹ ÷ b²",
     "an index law written with a slash"),
    ("x^a × x^b = x^(a+b).", "xᵃ × xᵇ = xᵃ⁺ᵇ.", "the index law itself"),
    ("x^a ÷ x^b = x^(a-b).", "xᵃ ÷ xᵇ = xᵃ⁻ᵇ.", "letter indices and a bracket"),
    ("The result is c^12.", "The result is c¹².",
     "a two digit index at the end of a sentence keeps the full stop down"),
    ("Divide by denominator: e^9 / e³ = e^(9-3).", "Divide by denominator: e⁹ ÷ e³ = e⁹⁻³.",
     "a whole worked step"),
    ("2. Answer: 1.413 × 10^-4 cm³", "2. Answer: 1.413 × 10⁻⁴ cm³",
     "a negative index in standard form"),
    ("Simplify (4a^3b^-2) / (2a^-1b^4).", "Simplify (4a³b⁻²) ÷ (2a⁻¹b⁴).",
     "indices inside brackets, divided"),
    ("d = sqrt(3^2 + 4^2) = sqrt(9 + 16) = 5",
     "d = √(3² + 4²) = √(9 + 16) = 5", "a function call is not a square root"),
    ("C = π * d -> 62.8 = 3.14 × d", "C = π × d → 62.8 = 3.14 × d",
     "an ASCII arrow in a Year 7 formula"),
    ("A = π * r²", "A = π × r²", "the circle area formula"),
    # Division: the spaced slash divides, the unspaced one never does.
    ("8000 / 2 = 4000", "8000 ÷ 2 = 4000", "arithmetic division"),
    ("Write 3/4 as a decimal.", "Write 3⁄4 as a decimal.",
     "an unspaced slash is a fraction and keeps its fraction slash"),
    ("Shade 3 / 4 of the pizza.", "Shade 3 / 4 of the pizza.",
     "a quantity, not a sum"),
    ("The race was on 15/07/2025.", "The race was on 15/07/2025.", "a date"),
    ("He ran at 8 m / s for 200 m.", "He ran at 8 m / s for 200 m.", "a rate"),
    ("Speed was 60 km / h.", "Speed was 60 km / h.", "a two letter unit"),
    ("Circle true/false and/or explain.", "Circle true/false and/or explain.",
     "words either side of a slash"),
    ("Is it yes / no, his / her?", "Is it yes / no, his / her?",
     "spaced words are still words"),
    ("Visit https://folioai.com.au/help", "Visit https://folioai.com.au/help",
     "a URL"),
]
for raw, want, why in NOTATION_CASES:
    got = _escape(raw)
    if got != want:
        bad(f"{why}: {raw!r} became {got!r}, wanted {want!r}")
ok(f"{len(NOTATION_CASES)} lines taken from the five real booklets set correctly")

# The one index Unicode cannot spell: it has to be markup, not a caret and not
# an approximation with the letter x standing in for a times sign.
mixed = _escape("(f²)^3 = f^(2 × 3) = f^6.")
check("^" not in mixed and "super" in mixed and "ˣ" not in mixed,
      "an index containing a times sign is set with markup, not left as a caret",
      mixed)


# ---------------------------------------------------------------------------
# A booklet per year level, rendered, then read back off the page
# ---------------------------------------------------------------------------
def vq(text, answer="", working=""):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer or "42",
                          working=working or "See the working above."),
        verified=True)


def booklet(year, topic, subtopic, intro, points, we_q, we_steps, we_ans, qs):
    return BookletData(
        subject="Mathematics", year_level=year, student_name="Kieran",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(
            topic=topic, subtopic=subtopic,
            teaching=SubtopicTeaching(
                intro_paragraphs=intro, key_points=points,
                worked_example=WorkedExample(question=we_q, steps=we_steps,
                                             answer=we_ans)),
            questions=[vq(*q) if isinstance(q, tuple) else vq(q) for q in qs])])


# Every line below is either verbatim from one of the five booklets or the
# same shape as one, carets and asterisks included, so the fixtures carry the
# defect the way production produced it.
BOOKLETS = {
    "Year 1": booklet(
        "Year 1", "Number and Algebra", "Groups of the same size",
        ["Two groups of 5 is the same as 5 x 2."],
        ["Count the groups, then count how many are in each group."],
        "There are 3 rows of 4 stickers. How many stickers?",
        ["Count the rows: 3 rows.", "3 x 4 = 12."], "12 stickers",
        ["How many are 2 x 5?", "There are 4 rows of 5 apples. What is 4 x 5?",
         "Share 20 apples between 4 baskets. 20 / 4 = ?"]),
    "Year 3": booklet(
        "Year 3", "Number and Algebra", "Multiplication facts",
        ["An array shows 6 x 3 as six rows of three."],
        ["A number times 1 stays the same: 7 x 1 = 7."],
        "What is 8 x 3?", ["8 x 3 is the same as 8 + 8 + 8.", "8 x 3 = 24."],
        "24",
        ["What is 6 x 7?", "Work out 48 / 6.", "A tray holds 4 x 5 muffins."]),
    "Year 5": booklet(
        "Year 5", "Measurement", "Volume of rectangular prisms",
        ["Volume is length x width x height, measured in cubic centimetres."],
        ["Multiply all three dimensions: 4 * 3 * 2 = 24 cm3."],
        "Find the volume of a 5 cm x 4 cm x 2 cm box.",
        ["Multiply the first two: 5 x 4 = 20.", "20 * 2 = 40 cm^3."],
        "40 cm3",
        ["A tank is 40 cm x 20 cm x 10 cm. Find its volume in cm^3.",
         "Calculate 15 * 4 + 7.", "Shade 3/4 of the grid."]),
    "Year 7": booklet(
        "Year 7", "Number and Algebra", "Index notation and factors",
        ["An index tells you how many times to multiply the base by itself, "
         "so 2^5 means 2 x 2 x 2 x 2 x 2."],
        ["The circumference of a circle is C = π * d.",
         "The area of a circle is A = π * r^2."],
        "Write 36 as a product of prime factors in index form.",
        ["Factors are 2 x 2 x 3 x 3.", "That is 2^2 x 3^2."], "2^2 × 3^2",
        ["Expand 3^4 into a multiplication expression.",
         "Write 40 as a product of primes in index form.",
         "C = π * d -> 62.8 = 3.14 × d. Find d."]),
    "Year 9": booklet(
        "Year 9", "Number and Algebra", "Index laws",
        ["An index law is a shortcut for multiplying or dividing powers of "
         "the same base."],
        ["Multiply terms with the same base by adding their indices: "
         "x^a × x^b = x^(a+b).",
         "Divide terms with the same base by subtracting their indices: "
         "x^a ÷ x^b = x^(a-b).",
         "Raise a power to another power by multiplying the indices: "
         "(x^a)^b = x^(a × b).",
         "Any non-zero variable raised to the power of zero equals one: "
         "x^0 = 1."],
        "Simplify the expression: (x³ × x^4) ÷ x²",
        ["Add the indices in the bracket: 3 + 4 = 7.",
         "Rewrite the expression with the new numerator: x^7 ÷ x².",
         "Subtract the indices: 7 - 2 = 5."], "x^5",
        [("1. Simplify the expression: a^4 × a³", "a^7",
          "Use the multiplication rule: x^a × x^b = x^(a+b). The result is a^7."),
         ("2. Simplify the expression: b^9 / b²", "b^7",
          "Use the division rule: x^a / x^b = x^(a-b). The result is b^7."),
         ("3. Simplify the expression: (f²)^3 × f^4", "f^10",
          "(f²)^3 = f^(2 × 3) = f^6. Multiply the terms: f^6 × f^4 = f^(6+4)."),
         ("4. Solve for x: 3x + 4 = 19", "x = 5",
          "Subtract 4 from both sides, then divide by 3. 5x = 45 works the same way."),
         ("5. A cylinder has radius 3 cm and height 10 cm. Find its volume.",
          "282.6 cm³",
          "Volume of cylinder = π * 3^2 × 10 = 3.14 × 9 × 10 = 282.6 cm³."),
         ("6. Find the distance between (1, 2) and (4, 6).", "5 units",
          "d = sqrt((4 - 1)^2 + (6 - 2)^2) = sqrt(3^2 + 4^2) = sqrt(25) = 5")]),
}

print("\nNO PAGE OF ANY YEAR LEVEL CARRIES SOURCE CODE")

SUPERS = set(N.SUPERSCRIPTS)
pages_by_year = {}
for year, data in BOOKLETS.items():
    out = render_pdf(data, tmp / f"{year.replace(' ', '')}.pdf")
    doc = pymupdf.open(out)
    pages = [p.get_text() for p in doc]
    pages_by_year[year] = pages
    body = "\n".join(pages)
    for page_no, page in enumerate(pages, 1):
        for line in page.split("\n"):
            if "^" in line:
                bad(f"{year} page {page_no} prints a caret index: {line!r}")
            if "*" in line:
                bad(f"{year} page {page_no} prints an asterisk: {line!r}")
            if "sqrt(" in line.lower():
                bad(f"{year} page {page_no} prints a function call: {line!r}")
            if "->" in line:
                bad(f"{year} page {page_no} prints an ASCII arrow: {line!r}")
            if "^" in line and any(c in line for c in SUPERS):
                bad(f"{year} page {page_no} mixes two index notations in one "
                    f"line: {line!r}")
    check(bool(body.strip()), f"{year} rendered pages to read", str(len(pages)))
ok("no caret, asterisk, sqrt( or -> survives to any page of any year level")

y9 = "\n".join(pages_by_year["Year 9"])
y1 = "\n".join(pages_by_year["Year 1"])
y3 = "\n".join(pages_by_year["Year 3"])
y5 = "\n".join(pages_by_year["Year 5"])
y7 = "\n".join(pages_by_year["Year 7"])

check("x³ × x⁴" in y9,
      "the line that carried both notations now carries one",
      [l for l in y9.split("\n") if "Simplify the expression" in l][:1])
check("3x + 4 = 19" in y9 and "5x = 45" in y9,
      "the algebra in the same booklet still has its unknown",
      [l for l in y9.split("\n") if "3x" in l][:1])
check("xᵃ × xᵇ = xᵃ⁺ᵇ" in y9, "the index law prints as an index law")
check("π × 3² × 10" in y9, "the cylinder working is arithmetic, not Python")
check("3 × 4" in y1 and "4 × 5" in y1,
      "Year 1 multiplies with a times sign")
check("6 × 7" in y3 and "48 ÷ 6" in y3,
      "Year 3 multiplies and divides with the right signs")
check("length × width × height" in y5 and "cm³" in y5,
      "Year 5 keeps one symbol for volume and one for multiplication")
check("A = π × r²" in y7 and "C = π × d" in y7,
      "the Year 7 circle formulas are formulas")
check("3⁄4" in y5, "an unspaced fraction is untouched by the division rule")


# ---------------------------------------------------------------------------
# The glyphs are real, in every face and at every size
#
# A superscript that is not in the embedded font prints as a box, which is
# worse than the caret it replaced. The fonts come from fonts-dejavu-core,
# which the Dockerfile installs.
# ---------------------------------------------------------------------------
print("\nEVERY SUPERSCRIPT GLYPH EXISTS IN EVERY EMBEDDED FACE")

assert F._UNICODE_FONT, ("font registration fell back to Helvetica, so this "
                         "run says nothing about the glyphs a customer gets")
FACES = [("DejaVu Sans", "normal"), ("DejaVu Sans", "bold"),
         ("DejaVu Serif", "normal"), ("DejaVu Serif", "bold")]
for family, weight in FACES:
    path = font_manager.findfont(
        font_manager.FontProperties(family=family, weight=weight),
        fallback_to_default=False)
    cmap = set()
    for table in TTFont(path)["cmap"].tables:
        cmap |= set(table.cmap)
    missing = [ch for ch in sorted(SUPERS | {N.ROOT, N.MULTIPLY, N.DIVIDE, "→"})
               if ord(ch) not in cmap]
    check(not missing, f"{family} {weight} has every glyph the normaliser emits",
          "".join(missing))

# And the numbers the markup fallback uses are the ones measured off those
# glyphs, so an index set either way lands in the same place.
path = font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans"),
                             fallback_to_default=False)
font = TTFont(path)
glyphs = font.getGlyphSet()
upm = font["head"].unitsPerEm
cmap = {}
for table in font["cmap"].tables:
    cmap.update(table.cmap)
from fontTools.pens.boundsPen import BoundsPen  # noqa: E402


def ink(ch):
    pen = BoundsPen(glyphs)
    glyphs[cmap[ord(ch)]].draw(pen)
    return pen.bounds  # (xMin, yMin, xMax, yMax) in font units


_, sup_lo, _, sup_hi = ink("²")
_, _, _, full_hi = ink("2")
size_pct = round(100 * (sup_hi - sup_lo) / full_hi)
rise_pct = round(100 * sup_lo / upm)
check(abs(N.SUPER_SIZE_PCT - size_pct) <= 2,
      "the markup fallback is set at the size of the Unicode superscript",
      f"module says {N.SUPER_SIZE_PCT}%, the font says {size_pct}%")
check(abs(N.SUPER_RISE_PCT - rise_pct) <= 2,
      "and raised onto the same baseline",
      f"module says {N.SUPER_RISE_PCT}%, the font says {rise_pct}%")

print("\nSUPERSCRIPTS DRAW AS SMALL RAISED GLYPHS, NOT AS BOXES")

styles = _make_styles()
SIZES = sorted({round(styles[n].fontSize, 2)
                for n in ("question", "working", "key_point", "intro_para",
                          "subtopic", "topic")})
SPECIMEN = "x" + "".join(sorted(SUPERS))
for size in SIZES:
    style = ParagraphStyle("probe", fontName=F.FONT_REGULAR, fontSize=size,
                           leading=size * 1.5)
    out = tmp / f"glyphs-{size}.pdf"
    SimpleDocTemplate(str(out)).build(
        [Paragraph(SPECIMEN, style),
         Paragraph("x" + N.super_markup("(2 × 3)"), style)])
    page = pymupdf.open(out)[0]
    drawn = "".join(sp["text"] for bl in page.get_text("dict")["blocks"]
                    for ln in bl.get("lines", []) for sp in ln["spans"])
    lost = [ch for ch in SUPERS if ch not in drawn]
    if lost:
        bad(f"at {size}pt the page did not draw: {''.join(lost)}")
        continue
    # A missing glyph in an embedded TrueType font draws .notdef, which is a
    # full height rectangle sitting on the baseline. A real superscript inks
    # only in the top half of the em, so measuring the ink is what separates
    # "rendered" from "rendered as a box".
    pix = page.get_pixmap(dpi=400)
    span = [sp for bl in page.get_text("dict")["blocks"]
            for ln in bl.get("lines", []) for sp in ln["spans"]
            if "⁴" in sp["text"]][0]
    scale = 400 / 72.0
    base_y = span["origin"][1]
    x0 = int((span["bbox"][0] + 0.6 * size) * scale)   # past the leading "x"
    x1 = int(span["bbox"][2] * scale)
    # Only this line's own box, or the ink of the paragraph below it is read
    # as part of the superscripts.
    y0 = max(0, int(span["bbox"][1] * scale) - 1)
    y1 = min(pix.height, int(span["bbox"][3] * scale) + 1)
    rows = [y for y in range(y0, y1)
            for x in range(x0, min(x1, pix.width))
            if pix.pixel(x, y)[0] < 128]
    if not rows:
        bad(f"at {size}pt nothing was inked where the superscripts should be")
        continue
    lo_em = (base_y - max(rows) / scale) / size    # bottom of the ink, in ems
    hi_em = (base_y - min(rows) / scale) / size    # top of the ink, in ems
    if lo_em < 0.15:
        bad(f"at {size}pt the superscripts ink down to the baseline "
            f"({lo_em:.2f} em), which is what a missing-glyph box looks like")
    if hi_em > 0.95:
        bad(f"at {size}pt the superscripts stand {hi_em:.2f} em above the "
            f"baseline, higher than the font's own superscript digits")
ok(f"every superscript draws as a raised glyph at {SIZES} pt")

# The two paths, side by side on one line: they must agree, or the booklet is
# back to two notations in one expression.
style = ParagraphStyle("probe", fontName=F.FONT_REGULAR, fontSize=10, leading=16)
out = tmp / "both-paths.pdf"
SimpleDocTemplate(str(out)).build(
    [Paragraph("x⁴ f" + N.super_markup("(2 × 3)"), style)])
page = pymupdf.open(out)[0]
spans = [sp for bl in page.get_text("dict")["blocks"]
         for ln in bl.get("lines", []) for sp in ln["spans"]]
raised = [sp for sp in spans if "2" in sp["text"] and sp["size"] < 10]
check(len(raised) == 1, "the markup index came out as its own raised run",
      str([(sp["text"], sp["size"]) for sp in spans]))
if raised:
    base = [sp for sp in spans if "x⁴" in sp["text"]][0]
    lift = base["origin"][1] - raised[0]["origin"][1]
    check(abs(raised[0]["size"] - 10 * N.SUPER_SIZE_PCT / 100) < 0.2,
          "set at the size the Unicode superscript is drawn at",
          f"{raised[0]['size']}pt")
    check(abs(lift - 10 * N.SUPER_RISE_PCT / 100) < 0.2,
          "and raised by the same amount", f"{lift:.2f}pt")
    check(raised[0]["bbox"][1] >= base["bbox"][1] - 0.1,
          "and kept inside the line box, clear of the line above",
          f"{raised[0]['bbox'][1]:.2f} vs {base['bbox'][1]:.2f}")


print()
if _failed:
    print(f"{len(_failed)} FAILED, {_passed} passed")
    for msg in _failed:
        print("  -", msg)
    sys.exit(1)
print(f"All {_passed} checks passed. PDFs in {tmp}")
