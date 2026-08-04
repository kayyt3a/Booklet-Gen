from __future__ import annotations

import io
import logging
import math
import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer,
    PageBreak, CondPageBreak, KeepTogether, Table, TableStyle, Image,
)
from reportlab.platypus.flowables import Flowable
from reportlab.lib.utils import ImageReader

from .schemas import BookletData, ExamPaper, ValidatedQuestion, WorkedExample
from .timing import booklet_timing, homework_session_plan


PAGE_MARGIN = 2.0 * cm

log = logging.getLogger(__name__)


# Font family. Helvetica is one of ReportLab's built-in Type 1 fonts: always
# available, but it carries no Unicode beyond Latin-1, so superscripts and
# fraction glyphs render as black boxes. DejaVu Sans ships with matplotlib
# (already a hard dependency for diagrams), covers the full Unicode range we
# need, and reads warmer and rounder on the page than Helvetica does, which
# matters for a booklet a primary-school student has to sit in front of.
#
# Registration is best-effort: if anything about the matplotlib font bundle
# changes, we fall back to Helvetica and the booklet still renders.
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

_UNICODE_FONT = False


def _register_fonts() -> None:
    """Register DejaVu Sans with ReportLab. Falls back to Helvetica silently."""
    global FONT_REGULAR, FONT_BOLD, FONT_ITALIC, _UNICODE_FONT
    if _UNICODE_FONT:
        return
    try:
        from matplotlib import font_manager
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase.pdfmetrics import registerFontFamily

        faces = {
            "DejaVuSans": "DejaVu Sans",
            "DejaVuSans-Bold": "DejaVu Sans:bold",
            "DejaVuSans-Oblique": "DejaVu Sans:italic",
            "DejaVuSans-BoldOblique": "DejaVu Sans:bold:italic",
        }
        for name, query in faces.items():
            prop = font_manager.FontProperties()
            parts = query.split(":")
            prop.set_family(parts[0])
            if "bold" in parts:
                prop.set_weight("bold")
            if "italic" in parts:
                prop.set_style("oblique")
            path = font_manager.findfont(prop, fallback_to_default=False)
            pdfmetrics.registerFont(TTFont(name, path))

        registerFontFamily(
            "DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
            italic="DejaVuSans-Oblique", boldItalic="DejaVuSans-BoldOblique",
        )
        FONT_REGULAR, FONT_BOLD, FONT_ITALIC = (
            "DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique")
        _UNICODE_FONT = True
    except Exception as e:
        log.info("formatter.font_fallback", extra={"reason": str(e)[:200]})


def _make_styles():
    _register_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=FONT_BOLD,
            fontSize=26, leading=30, alignment=TA_CENTER, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=13, alignment=TA_CENTER, textColor=colors.HexColor("#555555"),
            spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor("#888888"),
        ),
        "wordmark": ParagraphStyle(
            "wordmark", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=12, alignment=TA_CENTER, textColor=colors.HexColor("#1F3A5F"),
            spaceAfter=4,
        ),
        "subject_band": ParagraphStyle(
            "subject_band", parent=base["Heading1"], fontName=FONT_BOLD,
            fontSize=15, leading=19, spaceBefore=10, spaceAfter=10,
            textColor=colors.white, backColor=colors.HexColor("#1F3A5F"),
            borderPadding=(6, 8, 6, 8), alignment=TA_CENTER,
        ),
        "part_band": ParagraphStyle(
            "part_band", parent=base["Heading1"], fontName=FONT_BOLD,
            fontSize=17, leading=20, textColor=colors.white, alignment=TA_CENTER,
        ),
        "part_band_sub": ParagraphStyle(
            "part_band_sub", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, leading=13, textColor=colors.HexColor("#F4F7FB"),
            alignment=TA_CENTER, spaceBefore=2,
        ),
        "mnemonic": ParagraphStyle(
            "mnemonic", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=12, leading=15, textColor=colors.HexColor("#8B1E3F"),
            spaceBefore=4, spaceAfter=4,
        ),
        "topic": ParagraphStyle(
            "topic", parent=base["Heading1"], fontName=FONT_BOLD,
            fontSize=18, leading=22, spaceBefore=6, spaceAfter=8,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "subtopic": ParagraphStyle(
            "subtopic", parent=base["Heading2"], fontName=FONT_BOLD,
            fontSize=13.5, leading=17, spaceBefore=12, spaceAfter=7,
            textColor=colors.HexColor("#333333"),
        ),
        "intro_para": ParagraphStyle(
            "intro_para", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, leading=14, alignment=TA_LEFT, spaceAfter=6,
        ),
        "key_point": ParagraphStyle(
            "key_point", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, leading=13.5, leftIndent=14, bulletIndent=2,
            spaceAfter=3,
        ),
        "we_label": ParagraphStyle(
            "we_label", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=10, leading=13, textColor=colors.HexColor("#1F3A5F"),
            spaceAfter=3,
        ),
        "we_question": ParagraphStyle(
            "we_question", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, leading=14, spaceAfter=6,
        ),
        # The thing the task is about, set apart from the task itself: indented
        # both sides and italic, so a child can see at a glance where the
        # instruction stops and the material starts.
        "we_specimen": ParagraphStyle(
            "we_specimen", parent=base["Normal"], fontName=FONT_ITALIC,
            fontSize=10, leading=15, leftIndent=14, rightIndent=10,
            textColor=colors.HexColor("#1F3A5F"), spaceAfter=2,
        ),
        "question_specimen": ParagraphStyle(
            "question_specimen", parent=base["Normal"], fontName=FONT_ITALIC,
            fontSize=10.5, leading=15, leftIndent=16, rightIndent=10,
            textColor=colors.HexColor("#1F3A5F"), spaceBefore=3, spaceAfter=3,
        ),
        "we_step": ParagraphStyle(
            "we_step", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=9.5, leading=13, leftIndent=12, spaceAfter=3,
        ),
        "we_answer": ParagraphStyle(
            "we_answer", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=10.5, leading=15, spaceBefore=7,
            textColor=colors.HexColor("#1B8A3A"),
        ),
        "practice_label": ParagraphStyle(
            "practice_label", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=11, leading=14, spaceBefore=8, spaceAfter=6,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "question": ParagraphStyle(
            "question", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10.5, leading=14.5, alignment=TA_LEFT,
        ),
        "answer": ParagraphStyle(
            "answer", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=10.5, leading=14.5,
        ),
        # Marks printed in the right margin of an exam question.
        "exam_marks": ParagraphStyle(
            "exam_marks", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=10, leading=14, alignment=TA_RIGHT,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "working": ParagraphStyle(
            "working", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=9.5, leading=13, textColor=colors.HexColor("#333333"),
            leftIndent=12,
        ),
        "answers_heading": ParagraphStyle(
            "answers_heading", parent=base["Heading1"], fontName=FONT_BOLD,
            fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=12,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "challenge_heading": ParagraphStyle(
            "challenge_heading", parent=base["Heading1"], fontName=FONT_BOLD,
            fontSize=22, leading=26, alignment=TA_CENTER, spaceAfter=6,
            textColor=colors.HexColor("#8B1E3F"),
        ),
        "challenge_blurb": ParagraphStyle(
            "challenge_blurb", parent=base["Normal"], fontName=FONT_ITALIC,
            fontSize=11, leading=14, alignment=TA_CENTER, spaceAfter=14,
            textColor=colors.HexColor("#555555"),
        ),
        "footer_note": ParagraphStyle(
            "footer_note", parent=base["Normal"], fontName=FONT_ITALIC,
            fontSize=9, textColor=colors.HexColor("#888888"), alignment=TA_CENTER,
        ),
        "footer_note_left": ParagraphStyle(
            "footer_note_left", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=9, leading=12, textColor=colors.HexColor("#555555"),
            alignment=TA_LEFT,
        ),
        "closing": ParagraphStyle(
            "closing", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10.5, leading=15, alignment=TA_CENTER,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        # Reading passages. Deliberately unlike the worked-example box (blue)
        # and the session band (pink) so a child can tell at a glance that this
        # block is something to read rather than something to do.
        "passage_label": ParagraphStyle(
            "passage_label", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=9, leading=12, textColor=colors.HexColor("#A9793F"),
            spaceAfter=4,
        ),
        "passage_title": ParagraphStyle(
            "passage_title", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=12.5, leading=16, textColor=colors.HexColor("#6B4A1F"),
            spaceAfter=5,
        ),
        "passage_para": ParagraphStyle(
            "passage_para", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10.5, leading=15.5, alignment=TA_LEFT, spaceAfter=6,
        ),
        "spelling_word": ParagraphStyle(
            "spelling_word", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=11, leading=16,
        ),
        "spelling_num": ParagraphStyle(
            "spelling_num", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=11, leading=16, alignment=TA_RIGHT,
            textColor=colors.HexColor("#666666"),
        ),
    }


import re

# A fraction is digits/digits, but not part of a date (15/07/2025), a decimal
# (1/2.5), or a negative. Trailing punctuation is fine and must stay matched:
# an earlier lookahead of (?![0-9./]) silently skipped every fraction that
# ended a sentence, so "1/2 + 1/4." rendered with only the first one styled.
_FRACTION_RE = re.compile(r"(?<![\d./\-])(\d{1,4})/(\d{1,4})(?![\d/]|\.\d)")

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_FRACTION_SLASH = "⁄"


def _prettify_fractions(text: str) -> str:
    """Turn "3/4" into "3⁄4", a fraction slash between full size digits.

    The slash does the work. It leans further than an ordinary solidus and
    reads as a fraction rather than as a division, and being an ordinary
    character on the normal baseline it cannot collide with the lines above
    and below. ReportLab's <sup>/<sub> markup shifts digits outside the line
    box and produced visibly overlapping text in the answer key, which is why
    that approach was abandoned.

    The digits are deliberately NOT superscripted and subscripted. That was
    tried and it is what a Year 5 reads it at that matters: the Unicode
    superscript and subscript digits are about 56 percent the height of a
    normal digit in DejaVu Sans, so at the 9.5pt worked-example size a
    denominator printed at the visual equivalent of 5.3pt. In a booklet whose
    first topic is comparing fractions, the two numbers the child has to
    compare were the smallest thing on the page, and a two digit denominator
    was a smudge.

    Requires a Unicode font: Helvetica has no fraction slash and would render
    a black box, so fall back to plain "3/4" when registration failed.
    """
    if not _UNICODE_FONT:
        return text

    def repl(m: re.Match) -> str:
        num, den = m.group(1), m.group(2)
        if int(den) == 0:
            return m.group(0)
        return num + _FRACTION_SLASH + den
    return _FRACTION_RE.sub(repl, text)


# Models write units inconsistently: the question text says "cm²" but the
# worked solution often says "cm^2". Normalise to the real glyph.
_CARET_POWER_RE = re.compile(r"(?<=[A-Za-z])\^([23])\b")


def _tidy_units(text: str) -> str:
    if not _UNICODE_FONT:
        return text
    return _CARET_POWER_RE.sub(lambda m: m.group(1).translate(_SUPERSCRIPT), text)


# ---------------------------------------------------------------------------
# Notation normalisation
#
# The generator is not consistent with itself, and a booklet is where that
# shows. One real Year 5 booklet printed "15 * 4 + 7", then "5x = 45", then
# "1 x 3 = 3" within two pages: three meanings across two symbols, for a child
# who is still learning what the symbols mean. Volume appeared as "cubic
# centimetres", "cubic cm" and "cm^3" in the same document.
#
# None of that can be fixed reliably in a prompt, so it is fixed here, at render
# time, deterministically. After this pass the booklet uses exactly one symbol
# per operation:
#     x  is always an unknown            *  never appears
#     ×  is always multiplication        ÷  is always division
#     cm³ is always volume               cm² is always area
# ---------------------------------------------------------------------------

MULTIPLY = "×"
DIVIDE = "÷"

# "15 * 4", "2 * (7 + 4)", "5 * side". Emphasis asterisks (*like this*) have a
# space on the outside, so neither lookaround matches and they survive.
_STAR_MULT_RE = re.compile(r"(?<=[0-9A-Za-z\)])\s*\*\s*(?=[0-9A-Za-z\(])")

# "1 x 3", "5x3", "40 x 20 x 10", "l x w x h". Both sides must be a number or a
# single letter that is not itself x, so the unknown in "5x = 45", "solve for x"
# and any ordinary word containing an x are all left alone.
_MULT_OPERAND = r"(?:[0-9]+(?:\.[0-9]+)?|[a-wyzA-WYZ])"
# An opening bracket counts as a right-hand operand too. Without it the rule a
# child is taught the formula from, "2 x (length + width)", kept its letter x
# three lines above "length × width" in the same box.
_X_MULT_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + _MULT_OPERAND + r")\s*[xX]\s*(?=(?:"
    + _MULT_OPERAND + r"(?![A-Za-z0-9])|\())")

# "40 cm x 20 cm", "3 units x 2 units": a unit word, then x, then a number.
_X_MULT_UNIT_RE = re.compile(
    r"\b(cm|mm|km|m|litres?|units?|cubes?|blocks?)\s+[xX]\s+(?=[0-9])", re.IGNORECASE)

# "length x width x height": an x between two dimension words is always a
# multiplication sign. Scoped to this closed list so no ordinary sentence with
# an "x" in it is touched.
_DIMENSION = r"(?:length|width|height|depth|breadth|base|side)"
_X_MULT_DIM_RE = re.compile(
    r"\b(" + _DIMENSION + r")\s+[xX]\s+(?=" + _DIMENSION + r"\b)", re.IGNORECASE)

# "8000 / 2 = 4000". A slash with a space beside it is a division sign; a slash
# with no spaces ("3/4") is a fraction and is left for _prettify_fractions. The
# trailing lookahead keeps "3 / 4 of the pizza" a quantity rather than a sum.
_SLASH_DIV_RE = re.compile(
    r"(?<=[0-9])(?:\s+/\s*|\s*/\s+)(?=[0-9])(?![0-9]{0,3}\s+of\b)")

# "8000 divided by 2" in the middle of an expression.
_WORD_DIV_RE = re.compile(r"(?<=[0-9])\s+divided by\s+(?=[0-9])", re.IGNORECASE)

_UNIT_WORDS = {
    "centimetre": "cm", "centimetres": "cm", "centimeter": "cm", "centimeters": "cm",
    "cm": "cm",
    "millimetre": "mm", "millimetres": "mm", "millimeter": "mm", "millimeters": "mm",
    "mm": "mm",
    "metre": "m", "metres": "m", "meter": "m", "meters": "m", "m": "m",
    "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km",
    "km": "km",
}
_POWER_WORD_RE = re.compile(
    r"\b(cubic|square)\s+(" + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)

# "cm3", "cm^3", "m2" written flat. No space is allowed before the digit, or
# "a rope 2 m 3 cm long" would turn into cubic metres.
_UNIT_POWER_FLAT_RE = re.compile(r"\b(cm|mm|km|m)\^?([23])\b")

# Both superscripts, the multiplication sign and the division sign are Latin-1,
# so they render in Helvetica too and need no Unicode-font guard.
_SUPER_23 = {"2": "²", "3": "³"}


def _normalise_notation(text: str) -> str:
    text = _STAR_MULT_RE.sub(f" {MULTIPLY} ", text)
    text = _X_MULT_UNIT_RE.sub(lambda m: f"{m.group(1)} {MULTIPLY} ", text)
    text = _X_MULT_DIM_RE.sub(lambda m: f"{m.group(1)} {MULTIPLY} ", text)
    text = _X_MULT_RE.sub(rf"\1 {MULTIPLY} ", text)
    text = _SLASH_DIV_RE.sub(f" {DIVIDE} ", text)
    text = _WORD_DIV_RE.sub(f" {DIVIDE} ", text)

    def unit_word(m: re.Match) -> str:
        digit = "3" if m.group(1).lower() == "cubic" else "2"
        return _UNIT_WORDS[m.group(2).lower()] + _SUPER_23[digit]

    text = _POWER_WORD_RE.sub(unit_word, text)
    text = _UNIT_POWER_FLAT_RE.sub(lambda m: m.group(1) + _SUPER_23[m.group(2)], text)
    return text


# ---------------------------------------------------------------------------
# Answer-key presentation
#
# Three defects a tutor found by marking a real booklet at the kitchen table.
#
# 1. The key contradicted the booklet's own marking standard. Page 4 teaches
#    "check if your answer needs simplifying", and then the key gave Q31 as
#    4/10, Q32 as 6/15 and Q35 as 8/10 while Q60, which asked for a simplified
#    answer, was duly simplified. A child who does the taught thing is marked
#    wrong by the key. We do not overwrite what the model returned, because the
#    unsimplified form is often the honest end of the working; we print both,
#    "6/15 = 2/5", so either form marks correct.
#
# 2. The key dropped the units the lesson insists on: page 7 teaches "always
#    cubed, write cm3", and the key then gave bare "30", "120", "96", "56".
#    When the question names the unit it wants, a bare number gets it back.
#
# 3. The Final Challenge solutions were formatted unlike every other solution:
#    one operation per line for 55 questions, then dense prose for the last
#    five. Solutions are split to one step per line throughout.
# ---------------------------------------------------------------------------

# A bare fraction inside an answer string. Same shape as _FRACTION_RE, kept
# separate because this one runs before escaping and on the raw model answer.
_ANSWER_FRACTION_RE = re.compile(r"(?<![\d./\-])(\d{1,4})/(\d{1,4})(?![\d/]|\.\d)")


def _gcd(a: int, b: int) -> int:
    while b:
        a, b = b, a % b
    return a


# "Find an equivalent fraction for 1/2 by multiplying by 3" wants 3/6 and
# nothing else, so the key must not go on to offer 1/2 back.
_EQUIVALENT_ASK_RE = re.compile(r"\bequivalent\b", re.IGNORECASE)


def simplify_fractions_in_answer(answer: str, question: str = "") -> str:
    """Show a fraction answer in lowest terms as well as as given.

    "6/15" -> "6/15 = 2/5"; "4/10 of a metre" -> "4/10 of a metre (4/10 = 2/5)";
    an answer already in lowest terms comes back untouched.
    """
    if _EQUIVALENT_ASK_RE.search(question or ""):
        return (answer or "").strip()
    text = (answer or "").strip()
    if not text:
        return text
    reduced: list[tuple[str, str]] = []
    for m in _ANSWER_FRACTION_RE.finditer(text):
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0 or num == 0:
            continue
        g = _gcd(num, den)
        if g == 1:
            continue
        a, b = num // g, den // g
        pair = (m.group(0), str(a) if b == 1 else f"{a}/{b}")
        if pair not in reduced:
            reduced.append(pair)
    if not reduced:
        return text
    if len(reduced) == 1 and text.endswith(reduced[0][0]):
        return f"{text} = {reduced[0][1]}"
    pairs = ", ".join(f"{orig} = {simp}" for orig, simp in reduced)
    return f"{text} ({pairs})"


# Units, as the question itself writes them. Longest first so "metres" is not
# consumed by "m". Matched against text that has already been through
# _normalise_notation, so "cubic centimetres" is already "cm3" by this point.
_POWER_UNIT_PAT = r"(?:cm|mm|km|m)[²³]"
_CAPACITY_PAT = r"(?:millilitres|millilitre|litres|litre|mL|ml)"
_LINEAR_WORDS = ["centimetres", "centimetre", "millimetres", "millimetre",
                 "kilometres", "kilometre", "metres", "metre",
                 "cm", "mm", "km", "m"]
_LINEAR_PAT = "(?:" + "|".join(_LINEAR_WORDS) + ")"
_ANY_UNIT_PAT = f"(?:{_POWER_UNIT_PAT}|{_CAPACITY_PAT}|{_LINEAR_PAT})"

_ASK_IN_UNIT_RE = re.compile(r"\bin\s+(" + _ANY_UNIT_PAT + r")\b")
_ASK_HOW_MANY_RE = re.compile(
    r"\bhow many\s+(?:more\s+|extra\s+|whole\s+|full\s+)?(" + _ANY_UNIT_PAT + r")\b",
    re.IGNORECASE)
# A measurement stated in the question: "6 cm", "1.5 metres".
_STATED_LINEAR_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(" + _LINEAR_PAT + r")\b(?![²³])")

_VOLUME_ASK_RE = re.compile(r"\b(volume|capacity)\b", re.IGNORECASE)
_LENGTH_ASK_RE = re.compile(
    r"\b(height|length|width|depth|breadth|perimeter|side length|edge length)\b",
    re.IGNORECASE)

_BARE_NUMBER_RE = re.compile(r"-?\d{1,15}(?:\.\d+)?")

_LINEAR_SYMBOL = {"centimetres": "cm", "centimetre": "cm", "cm": "cm",
                  "millimetres": "mm", "millimetre": "mm", "mm": "mm",
                  "kilometres": "km", "kilometre": "km", "km": "km",
                  "metres": "m", "metre": "m", "m": "m"}


def _final_ask(text: str) -> str:
    """The last sentence of a question: the bit that says what to hand back."""
    parts = [p for p in re.split(r"(?<=[.?!])\s+", text.strip()) if p.strip()]
    return parts[-1] if parts else text


def _stated_linear_unit(text: str) -> str | None:
    """The linear unit the question measures in, if it uses exactly one."""
    found = {_LINEAR_SYMBOL[u.lower()] for u in _STATED_LINEAR_RE.findall(text)}
    return found.pop() if len(found) == 1 else None


def answer_unit(question_text: str) -> str | None:
    """The unit a numeric answer to this question should carry, or None.

    Deliberately conservative. It echoes back a unit the question itself names,
    and only infers one when the closing sentence asks for a volume or a
    dimension and every measurement in the question uses the same unit. If it
    cannot tell, it says nothing rather than inventing a unit.
    """
    text = _normalise_notation(question_text or "")
    ask = _final_ask(text)
    if re.search(r"\bper\b|/", ask):
        # A rate ("litres per minute", "m/s") is a compound unit and guessing
        # half of it is worse than printing none.
        return None
    for rx in (_ASK_IN_UNIT_RE, _ASK_HOW_MANY_RE):
        m = rx.search(ask)
        if m:
            return m.group(1)
    vol = _VOLUME_ASK_RE.search(ask)
    length = _LENGTH_ASK_RE.search(ask)
    # Whichever the closing sentence mentions last is what it is asking for:
    # "so the depth increases by 1 metre, what will the new total volume be?"
    # is a volume question, not a depth one.
    if vol and (not length or vol.start() > length.start()):
        unit = _stated_linear_unit(text)
        return f"{unit}³" if unit else None
    if length:
        return _stated_linear_unit(text)
    return None


def answer_with_unit(answer: str, question_text: str) -> str:
    """Put the question's unit back on a bare numeric answer."""
    text = (answer or "").strip()
    if not _BARE_NUMBER_RE.fullmatch(text):
        return text
    unit = answer_unit(question_text)
    return f"{text} {unit}" if unit else text


def key_answer(question) -> str:
    """The answer exactly as the answer key should print it."""
    text = getattr(question, "question", "") or ""
    answer = answer_with_unit(getattr(question, "answer", "") or "", text)
    return simplify_fractions_in_answer(answer, text)


# One step per line. Models write the same solution as newline-separated
# operations for most questions and as a single dense paragraph for the Final
# Challenge; splitting on sentence and clause boundaries makes both look the
# same on the page.
_SOLUTION_SPLIT_RE = re.compile(r"(?<=[.:;])\s+(?=[A-Z0-9(¹²³⁴⁵⁶⁷⁸⁹])")


def solution_lines(working: str) -> list[str]:
    lines: list[str] = []
    for raw in (working or "").splitlines():
        for part in _SOLUTION_SPLIT_RE.split(raw.strip()):
            part = _strip_step_prefix(part.strip())
            if part:
                lines.append(part)
    return lines


_EM_DASH = re.compile(r"\s*—\s*")
_EN_RANGE = re.compile(r"(?<=\d)\s*–\s*(?=\d)")
_EN_DASH = re.compile(r"\s*–\s*")


def _dedash(text: str) -> str:
    """Remove em/en dashes from generated text.

    Em dashes read as an AI tell and look less professional in a printed
    booklet, so we replace them deterministically no matter what the model
    produces: em dash -> comma (its usual parenthetical/break role), en dash
    between digits -> "to" (a range), other en dashes -> comma. Doubled or
    stranded punctuation left by the swap is then tidied up.
    """
    text = _EM_DASH.sub(", ", text)
    text = _EN_RANGE.sub(" to ", text)
    text = _EN_DASH.sub(", ", text)
    text = re.sub(r",\s*,", ", ", text)             # collapse doubled commas
    text = re.sub(r"\s+,", ",", text)               # no space before comma
    text = re.sub(r",\s*([.!?;:])", r"\1", text)    # drop comma before other punctuation
    text = re.sub(r"([.!?;:])\s*,\s*", r"\1 ", text)  # drop comma after sentence punctuation
    return text.strip()


# Models habitually open each worked-example step with "Step 1:", which is
# pure repetition once the step sits in a numbered list. Strip it so the
# student reads the maths instead of the scaffolding.
_STEP_PREFIX_RE = re.compile(r"^\s*Step\s*\d+\s*[:.\)-]\s*", re.IGNORECASE)


def _strip_step_prefix(text: str) -> str:
    stripped = _STEP_PREFIX_RE.sub("", text, count=1)
    return stripped or text


def _escape(text: str) -> str:
    return _prettify_fractions(_tidy_units(_normalise_notation(
        _dedash(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )))


# ---------------------------------------------------------------------------
# Mini-lesson prose
#
# Two presentation defects the owner found in a Year 3 English booklet.
#
# 1. A lesson wrote: "like saying the dog runs instead of the dog run". The two
#    specimens run straight into the sentence, so a seven year old reads eleven
#    words of grammar rather than two examples. Quoting them makes them look
#    like the objects they are.
#
# 2. The term being taught was set in the same weight as everything around it.
#    A child skimming back for "what was a synonym again?" has nothing to find.
#
# Both are done here rather than in the prompt because the prompt cannot be
# made to comply reliably, and because the fix has to hold for text that was
# generated before the prompt changed. Both are also deliberately narrow: they
# fire on shapes where the boundaries are unambiguous and do nothing at all
# otherwise. See `quote_inline_examples` and `lesson_terms` for the limits.
# ---------------------------------------------------------------------------

# "saying the dog runs instead of the dog run", "writing cm3 rather than cm".
# The trigger verb and the separator are both required: "instead of" on its own
# appears in ordinary sentences ("use a comma instead of a full stop") where the
# left operand has no clear start, and quoting half a clause is worse than
# quoting nothing.
_INLINE_EXAMPLE_RE = re.compile(
    r"\b(saying|writing|say|write)\s+"
    r"([^,.;:!?\"()]{2,60}?)\s+"
    r"(instead of|rather than)\s+"
    r"([^,.;:!?\"()]{2,60}?)"
    r"(?=\s*[,.;:!?]|\s*$)",
    re.IGNORECASE,
)

_MAX_EXAMPLE_WORDS = 6


def quote_inline_examples(text: str) -> str:
    """Put quotation marks round two specimens compared inside a sentence.

    "like saying the dog runs instead of the dog run"
      -> like saying "the dog runs" instead of "the dog run"

    Leaves the sentence alone when either specimen is longer than six words, is
    empty, or already carries quotation marks: those are the cases where the
    boundaries are guesswork.
    """
    def repl(m: re.Match) -> str:
        left, sep, right = m.group(2).strip(), m.group(3), m.group(4).strip()
        for part in (left, right):
            if not part or len(part.split()) > _MAX_EXAMPLE_WORDS:
                return m.group(0)
        return f'{m.group(1)} "{left}" {sep} "{right}"'

    return _INLINE_EXAMPLE_RE.sub(repl, text or "")


# The lesson writer marks a newly introduced term as **alliteration** on first
# use, and that is the only inline markup in the system. It has to be asterisks
# rather than tags: `_escape` turns a model-emitted <b> into a literal &lt;b&gt;,
# whereas asterisks survive escaping, because the multiplication normaliser
# above deliberately spares emphasis markers.
#
# Two runs of two or more asterisks, wrapped round something that is not just
# whitespace. Written to tolerate the model's mistakes (***term***, **a **b**)
# rather than to reject them: this text goes straight into a ReportLab
# Paragraph, and malformed markup there raises and takes the booklet down with
# it, so every input has to leave valid output.
_BOLD_MARKUP_RE = re.compile(r"\*{2,}(\S(?:.*?\S)?)\*{2,}", re.DOTALL)
_STRAY_STARS_RE = re.compile(r"\*{2,}")


def apply_bold_markup(escaped: str) -> str:
    """Turn **term** into a bold run, in text that has already been escaped.

    Must run after escaping, or the tags it inserts are escaped in turn. Any
    asterisk run left over is dropped rather than printed: unmatched markup is
    the model's typo, and a stray ** on a Year 3 page is noise. Single
    asterisks are left alone, because by this point a lone * is either an
    emphasis marker or part of an expression the normaliser chose not to touch.
    """
    text = _BOLD_MARKUP_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped or "")
    # A group can still contain an odd inner run ("**a **b**"): strip those too,
    # so no asterisk reaches the page and no tag is left half open.
    return _STRAY_STARS_RE.sub("", text)


def _lesson_html(text: str) -> str:
    """Escape a line of mini-lesson prose and apply the lesson treatments."""
    return apply_bold_markup(_escape(quote_inline_examples(text)))


MAX_IMG_WIDTH = 7.5 * cm
MAX_IMG_HEIGHT = 4.8 * cm
WE_IMG_WIDTH = 6 * cm
WE_IMG_HEIGHT = 4 * cm


def _image_reader(path: str | None):
    """An ImageReader for a path that resolves to a readable picture, else None.

    Shared so the page layout and the credits page cannot disagree about which
    pictures the booklet contains.
    """
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return ImageReader(str(p))
    except Exception:
        return None


def image_is_usable(path: str | None) -> bool:
    """Whether this path will actually print."""
    return _image_reader(path) is not None


def _make_image(path: str | None, max_w=MAX_IMG_WIDTH, max_h=MAX_IMG_HEIGHT):
    reader = _image_reader(path)
    if reader is None:
        return None
    try:
        iw, ih = reader.getSize()
        scale = min(max_w / iw, max_h / ih, 1.0)
        return Image(str(path), width=iw * scale, height=ih * scale)
    except Exception:
        return None


def _lesson_flowables(styles, t) -> list:
    """The mini-lesson body: prose, mnemonic, key points, worked examples.

    Extracted so a subtopic the hour could not fit can carry its lesson down
    into Homework, where its practice went. Without that the booklet asks for
    work on a skill it never explains.
    """
    out = []
    for para in t.intro_paragraphs:
        out.append(Paragraph(_lesson_html(para), styles["intro_para"]))
    if t.mnemonic:
        out.append(Paragraph(f"Remember: {_lesson_html(t.mnemonic)}",
                             styles["mnemonic"]))
    if t.key_points:
        out.append(Spacer(1, 0.15 * cm))
        for kp in t.key_points:
            out.append(Paragraph(f"• {_lesson_html(kp)}", styles["key_point"]))
    out.append(Spacer(1, 0.3 * cm))
    out.append(_worked_example_flowable(styles, t.worked_example,
                                        "Watch first (worked example)"))
    for ge in t.guided_examples:
        out.append(Spacer(1, 0.2 * cm))
        out.append(_worked_example_flowable(styles, ge,
                                            "Let's do this one together"))
    return out


# A worked example arrives as one string carrying both the instruction and the
# thing to work on: 'Read the story below. Who are the main characters? "Leo
# and his sister Mia loved to visit their grandma's farm."' Printed as one
# paragraph that reads as a wall, and the child cannot see where the task ends
# and the material begins. Split on the quoted specimen and set it apart.
_SPECIMEN_RE = re.compile(r'["“]([^"”]{12,})["”]\s*\.?\s*$')


def split_instruction_and_specimen(text: str) -> tuple[str, str | None]:
    """Separate a trailing quoted specimen from the instruction before it.

    Returns (instruction, specimen or None). Only splits when the quoted run is
    long enough to be material rather than a single quoted word, and when
    something is left in front of it: '"the dog run" is wrong' is a sentence
    about a specimen, not an instruction followed by one.
    """
    m = _SPECIMEN_RE.search(text or "")
    if not m:
        return (text or "").strip(), None
    instruction = text[:m.start()].strip()
    specimen = m.group(1).strip()
    if not instruction or len(specimen.split()) < 3:
        return (text or "").strip(), None
    return instruction, specimen


def _worked_example_flowable(styles, we: WorkedExample, label: str = "Worked example"):
    """Return a bordered box containing a worked example. `label` distinguishes
    the "I do" worked example from the "we do" guided ones."""
    # Worked examples are lesson content, so they get the same treatment: a
    # **term** the model marked up there becomes bold rather than printing its
    # asterisks, and a stray run of asterisks is dropped.
    instruction, specimen = split_instruction_and_specimen(we.question)
    inner = [
        Paragraph(label, styles["we_label"]),
        Paragraph(apply_bold_markup(_escape(instruction)), styles["we_question"]),
    ]
    if specimen:
        # Set apart, indented and quoted, so the task and the thing the task is
        # about are not one run-on paragraph.
        inner.append(Spacer(1, 0.2 * cm))
        inner.append(Paragraph(f'"{_escape(specimen)}"', styles["we_specimen"]))
        inner.append(Spacer(1, 0.2 * cm))
    img = _make_image(we.image_path, max_w=WE_IMG_WIDTH, max_h=WE_IMG_HEIGHT)
    if img is not None:
        inner.append(Spacer(1, 0.15 * cm))
        inner.append(img)
        inner.append(Spacer(1, 0.15 * cm))
    for i, step in enumerate(we.steps, 1):
        inner.append(Paragraph(
            f"<b>{i}.</b> {apply_bold_markup(_escape(_strip_step_prefix(step)))}",
            styles["we_step"]))
    inner.append(Paragraph(f"Answer: {_escape(we.answer)}", styles["we_answer"]))

    tbl = Table([[inner]], colWidths=[A4[0] - 2 * PAGE_MARGIN - 0.4 * cm])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#B7C3D4")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Reading passages
#
# A shipped Year 3 English booklet asked "Referring to the passage above..."
# with the passage printed below the question, and printed the same passage
# again for the next question. The generator now hands the reading over as its
# own object, and everything about where it lands is decided here: the passage
# is laid out once, before the first question that refers to it, and it cannot
# be separated from that question by a page break.
# ---------------------------------------------------------------------------


def section_passages(section) -> dict:
    """{passage id: Passage} for a section. Empty when the field is absent."""
    return {p.id: p for p in (getattr(section, "passages", None) or []) if p.id}


def passage_groups(questions, passages: dict) -> list[tuple[object, list]]:
    """Questions in printing order as [(Passage or None, [question, ...])].

    Every question that names a passage is moved into that passage's group, at
    the point the passage is first referred to, so the passage is printed once
    and every question about it follows it. Questions with no passage, or with
    a passage id the section does not define, keep their place and their group
    is keyed None.
    """
    order: list[str | None] = []
    buckets: dict[str | None, list] = {}
    for vq in questions:
        pid = getattr(getattr(vq, "question", None), "passage_id", None)
        key = pid if pid in passages else None
        if key is None:
            # Ungrouped questions must not be merged with each other across a
            # passage boundary, or a question written after the reading would
            # jump above it. Each run of them is its own group.
            if not order or order[-1] is not None:
                order.append(None)
                buckets[len(order) - 1] = []
            buckets[len(order) - 1].append(vq)
        else:
            if key not in buckets:
                order.append(key)
                buckets[key] = []
            buckets[key].append(vq)
    out = []
    for i, key in enumerate(order):
        bucket = buckets[i] if key is None else buckets[key]
        out.append((passages[key] if key is not None else None, bucket))
    return out


def ordered_questions(questions, passages: dict) -> list:
    """The flat question order the booklet prints, after passage grouping.

    The answer key and the homework session plan both number questions, so both
    have to walk the same order the page does.
    """
    return [vq for _, group in passage_groups(questions, passages) for vq in group]


def _passage_flowable(styles, passage):
    """The reading itself: a tinted, ruled box holding a title and paragraphs."""
    inner = [Paragraph("READ THIS", styles["passage_label"])]
    if getattr(passage, "title", None):
        inner.append(Paragraph(_escape(passage.title), styles["passage_title"]))
    for para in (getattr(passage, "paragraphs", None) or []):
        inner.append(Paragraph(_escape(para), styles["passage_para"]))
    tbl = Table([[inner]], colWidths=[A4[0] - 2 * PAGE_MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FDF8EF")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#D9C9A8")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#A9793F")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return tbl


def _part_band(styles, text: str, bg_hex: str, subtitle: str = ""):
    """A full-width coloured divider for a major part (Recap / Class Work /
    Homework), so the two halves of the booklet read as distinct sections."""
    cells = [Paragraph(text, styles["part_band"])]
    if subtitle:
        cells.append(Paragraph(subtitle, styles["part_band_sub"]))
    tbl = Table([[cells]], colWidths=[A4[0] - 2 * PAGE_MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg_hex)),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return tbl


# Blank space left under a question for the student to work in.
#
# This used to be a flat gap, so "what is 28 x 3?" and a three-part volume
# problem got exactly the same room: the one-liners wasted half a page and the
# multi-step questions had nowhere to work. Scale it by difficulty, and give
# multi-part questions room per part, the way the exam renderer scales by marks.
_DIFFICULTY_SPACE_CM = {"easy": 1.6, "medium": 2.2, "hard": 3.2}

# The Warm-up Recap is written entirely in easy questions, so it inherited the
# smallest allowance and a child got roughly one line to work "15 x 4 + 7" in
# while every question from the Class Work on had room to think. Warm-up
# questions are still arithmetic that wants a couple of lines of working, so
# they get a floor of their own rather than the easy rate.
_RECAP_MIN_SPACE_CM = 2.2

# "a)", "(b)", "c." at a word boundary: the model's usual way of numbering parts.
_PART_MARKER_RE = re.compile(r"(?:^|\s)\(?([a-e])[\).]\s")

# One question should never swallow a whole page.
_MAX_WORKING_SPACE_CM = 8.0

# Height of one ruled "Answer:" line, and the least working room a question may
# be squeezed down to at the foot of a page.
_ANSWER_LINE_CM = 0.75
_MIN_WORKING_SPACE_CM = 0.9

BODY_HEIGHT = A4[1] - 2 * PAGE_MARGIN

# Room needed at the foot of a page for the Homework part to start there rather
# than on a fresh page: the band, a heading and a question or two. Raise this to
# BODY_HEIGHT / cm to go back to Homework always starting on its own page.
HOMEWORK_MIN_START_CM = 7.0


def part_labels(text: str) -> list[str]:
    """The part markers in a question, in order: ["a", "b"] for "a) ... b) ...".

    Shared with timing.py, which charges for each part.
    """
    seen: list[str] = []
    for m in _PART_MARKER_RE.findall(text or ""):
        if m not in seen:
            seen.append(m)
    return seen


# Questions that want prose, a diagram or a demonstration rather than a value.
# An "Answer: ____" rule under one of these tells the child to compress an
# explanation onto a single line, so they do not get one.
_EXTENDED_RESPONSE_RE = re.compile(
    r"\b(explain|describe|justify|discuss|prove|show that|show why|show your working|"
    r"draw|sketch|shade|colour|color|plot|label|construct|write (?:a|an|one|two|"
    r"three|four|five|\d+) (?:short |more )?"
    r"(?:paragraphs?|sentences?|lines?|stor(?:y|ies))|in your own words)\b",
    re.IGNORECASE)

# Of those, the ones that want a picture. These need clear space: ruling a
# space someone has to sketch in is worse than leaving it blank. "Show your
# working" belongs here too, because working is laid out down the page rather
# than along a line.
_DRAWN_RESPONSE_RE = re.compile(
    r"\b(draw|sketch|shade|colour|color|plot|label|construct|show your working)\b",
    re.IGNORECASE)

# "Write two sentences", "write a short paragraph": the question says how much
# it wants, so give exactly that much room.
_WRITTEN_AMOUNT_RE = re.compile(
    r"\bwrite (a|an|one|two|three|four|five|\d+) (?:short |more )?"
    r"(paragraphs?|sentences?|lines?|stor(?:y|ies))\b", re.IGNORECASE)

_AMOUNT_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
                 "five": 5}

# Ruled lines one of each is worth. A sentence from a primary writer runs to
# more than one line more often than not.
_RULES_PER = {"line": 1, "sentence": 2, "paragraph": 5, "story": 8}

# What a bare "Explain how you know" gets when the question names no amount.
_DEFAULT_WRITTEN_RULES = 4
_MAX_WRITTEN_RULES = 8

# A question whose own model answer runs to a sentence wants a sentence back,
# whether or not it says "explain". Most comprehension questions are phrased
# "What can you infer about...", "Why does the writer end it that way", and
# none of those match a verb list; they were getting a single "Answer:" rule at
# the foot of a gap sized for arithmetic working.
#
# The key is the honest measure of how much writing the question wants, and it
# separates the two subjects cleanly: across a Year 5 sample every maths answer
# came in at six words or fewer, every comprehension answer at eight or more.
_ANSWER_WORDS_FOR_RULES = 8
_WORDS_PER_RULE = 6.0


def answer_line_labels(question) -> list[str]:
    """Labels for the ruled answer lines under a question, empty for none.

    A question that gets writing lines does not also get an "Answer:" rule
    beneath them. Two kinds of ruling under one question asks the child to
    write the answer twice, and the lines already say where to write.
    """
    text = getattr(question, "question", "") or ""
    if _EXTENDED_RESPONSE_RE.search(text) or written_response_rules(question):
        return []
    parts = part_labels(text)
    if len(parts) >= 2:
        return [f"{p}) Answer:" for p in parts]
    return ["Answer:"]


def written_response_rules(question) -> int:
    """Blank ruled lines for a question that wants prose, 0 for one that does not.

    An extended response used to get no rule of any kind, on the reasoning that
    an explanation should not be squashed onto a single "Answer:" line. That
    reasoning is right and the result was wrong: the longest questions in the
    booklet ended up with the least structure on the page, a silent gap of
    white that reads as a printing fault, and a child with nothing telling them
    where to start writing or how much is wanted. Ruled lines say both.

    A drawing question still gets clear space, and so does "show your working".
    """
    text = getattr(question, "question", "") or ""
    if _DRAWN_RESPONSE_RE.search(text):
        return 0
    if _EXTENDED_RESPONSE_RE.search(text):
        m = _WRITTEN_AMOUNT_RE.search(text)
        if m:
            amount, unit = m.group(1).lower(), m.group(2).lower().rstrip("s")
            unit = {"storie": "story"}.get(unit, unit)
            n = _AMOUNT_WORDS.get(amount) or (int(amount) if amount.isdigit() else 1)
            rules = n * _RULES_PER.get(unit, 2)
        else:
            rules = _DEFAULT_WRITTEN_RULES
        return max(2, min(rules, _MAX_WRITTEN_RULES))
    # Not phrased as an extended response, but the key answers it in prose.
    words = len((getattr(question, "answer", "") or "").split())
    if words < _ANSWER_WORDS_FOR_RULES:
        return 0
    return max(2, min(math.ceil(words / _WORDS_PER_RULE), _MAX_WRITTEN_RULES))


def _working_space_cm(question, floor_cm: float = 0.0) -> float:
    # A prose answer is sized by the lines it is given, not by its difficulty
    # tag: "Explain how you know" is tagged easy as often as hard, and the
    # rules are what the child writes on either way.
    rules = written_response_rules(question)
    if rules:
        return min(rules * _ANSWER_LINE_CM + 0.3, _MAX_WORKING_SPACE_CM)
    base = max(floor_cm, _DIFFICULTY_SPACE_CM.get(
        (question.difficulty or "medium").strip().lower(), 2.2))
    parts = len(part_labels(question.question))
    if parts >= 2:
        base += parts * 1.1
    return min(base, _MAX_WORKING_SPACE_CM)


class WorkingSpace(Flowable):
    """The blank area under a question, with its ruled answer line(s).

    Two jobs, and they are the same job. It draws the "Answer: _____" rule the
    booklet had nowhere for, and it is the piece that gives when a page runs
    out: it reports its full height when there is room and shrinks toward
    `min_height` when there is not.

    Why it has to shrink: the question used to be a KeepTogether wrapping a
    fixed Spacer, so a question whose blank space did not fit was moved whole to
    the next page and the current page was abandoned, two thirds empty, in one
    real booklet. KeepTogether measures its contents with an unbounded height
    (0xfffffff), so reporting the *minimum* during measurement makes the
    keep-together decision on the small size, and the real height is then
    negotiated against the space actually left on the page.
    """

    def __init__(self, height: float, labels=(), min_height: float | None = None,
                 rules: int = 0):
        super().__init__()
        self.labels = list(labels)
        # Unlabelled ruled lines for a prose answer. Unlike the working space
        # around them these do not shrink: a question that asks for two
        # sentences has to still offer two sentences of ruling at the foot of a
        # page, or the child meets the same question with half the room.
        self.rules = max(0, int(rules))
        self.answers_height = _ANSWER_LINE_CM * cm * len(self.labels)
        self.height = height + self.answers_height
        floor = _MIN_WORKING_SPACE_CM * cm + self.answers_height
        if self.rules:
            floor = self.height
        self.min_height = min(self.height, min_height if min_height is not None else floor)
        self.width = 0

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        if availHeight > BODY_HEIGHT:
            # Being measured, not placed (KeepTogether/_listWrapOn). Quote the
            # minimum so the question is not bounced to the next page over
            # blank space that is allowed to shrink.
            h = self.min_height
        else:
            h = max(self.min_height, min(self.height, availHeight))
        self._h = h
        return availWidth, h

    def draw(self):
        if not self.labels and not self.rules:
            return
        c = self.canv
        c.saveState()
        c.setFont(FONT_REGULAR, 9.5)
        c.setFillColor(colors.HexColor("#333333"))
        c.setStrokeColor(colors.HexColor("#9AA6B8"))
        c.setLineWidth(0.6)
        line_h = _ANSWER_LINE_CM * cm
        for i, label in enumerate(reversed(self.labels)):
            # Offset so the rule does not sit tight against the next question.
            y = i * line_h + 0.42 * cm
            c.drawString(0, y, label)
            x0 = c.stringWidth(label, FONT_REGULAR, 9.5) + 6
            c.line(x0, y - 2.5, self.width, y - 2.5)
        # Full-width rules for a written answer, drawn from the top of the
        # block down, so the child starts writing where the question ends
        # rather than at the bottom of a gap.
        for i in range(self.rules):
            y = self._h - 0.42 * cm - i * line_h
            c.line(0, y, self.width, y)
        c.restoreState()


class PageMarker(Flowable):
    """Zero-size flowable that records the page its question landed on.

    The answer key wants to say "17. Answer: 30 cm³ (p8)", and nothing knows
    what page 8 is until the document has been laid out. So the booklet is
    built twice: the first build is thrown away and only fills this map, the
    second prints the references. It is placed first inside the question's
    KeepTogether, so it reports the page the question starts on.
    """

    width = 0
    height = 0

    def __init__(self, page_map: dict, key: int):
        super().__init__()
        self._page_map = page_map
        self._key = key

    def wrap(self, availWidth, availHeight):
        return 0, 0

    def draw(self):
        self._page_map[self._key] = self.canv.getPageNumber()


def question_numbering(data: BookletData) -> dict:
    """{running index: the number actually printed beside the question}.

    Questions are numbered from 1 again at each reading and at each subtopic,
    so "question 3" means the third question about the story in front of you
    rather than the sixty-third thing in the booklet. A booklet-wide running
    number is easier to implement and worse to sit in front of.

    The running index does not go away: it stays as the identity of a question,
    because two questions can now both print as "3" and the page-reference map
    and the answer key both need to tell them apart. Computed once, here, and
    read by the body and the key alike, so the two cannot drift apart.
    """
    out: dict = {}
    n = 0

    def run(questions, passages=None, reset_per_passage=False):
        nonlocal n
        d = 0
        if not reset_per_passage:
            for vq in questions:
                n += 1
                d += 1
                out[n] = d
            return
        for passage, group in passage_groups(questions, passages or {}):
            if passage is not None:
                d = 0          # a new reading starts its questions at 1
            for vq in group:
                n += 1
                d += 1
                out[n] = d

    if data.recap_questions:
        run(data.recap_questions)
    for s in data.sections:
        if s.questions:
            run(s.questions, section_passages(s), reset_per_passage=True)
    for s in data.sections:
        if s.homework_questions:
            run(s.homework_questions, section_passages(s),
                reset_per_passage=True)
    if data.challenge_questions:
        run(data.challenge_questions)
    return out


def _question_flowables(styles, q_num: int, vq: ValidatedQuestion,
                        page_map: dict | None = None,
                        space_floor_cm: float = 0.0,
                        display_num: int | None = None) -> list:
    """The flowables of one numbered question, before they are bound together.

    Returned as a flat list rather than a KeepTogether because a passage binds
    its first question to itself, and a KeepTogether inside a KeepTogether does
    not measure: the inner one reports 0xffffff by design, so the outer one
    concludes it is five kilometres tall and moves to a new page every time.
    That is what pushed a reading, and the page it should have shared, apart.

    No verification mark here. Every question in a generated booklet carried a
    green tick, which to a child reading an unattempted page says "correct", and
    which, being on every question, said nothing at all. Verification is shown
    where it means something: beside the answer in the key.
    """
    block = []
    if page_map is not None:
        block.append(PageMarker(page_map, q_num))
    # The sentence or passage a question works on is set apart from the
    # instruction, the same way the worked examples do it: a question and the
    # material it is about should not read as one paragraph.
    instruction, specimen = split_instruction_and_specimen(vq.question.question)
    shown = q_num if display_num is None else display_num
    block.append(
        Paragraph(
            f"<b>{shown}.</b> {_escape(instruction)}",
            styles["question"],
        ),
    )
    if specimen:
        block.append(Paragraph(f'"{_escape(specimen)}"',
                               styles["question_specimen"]))
    img = _make_image(vq.image_path)
    if img is not None:
        block.append(Spacer(1, 0.3 * cm))
        block.append(img)
        # No credit line under the picture. A licence string under every image
        # reads as clutter on a child's worksheet; the attributions are still
        # printed, gathered on a references page at the very back.
    block.append(WorkingSpace(
        _working_space_cm(vq.question, space_floor_cm) * cm,
        answer_line_labels(vq.question),
        rules=written_response_rules(vq.question),
    ))
    return block


def _question_block(styles, q_num: int, vq: ValidatedQuestion,
                    page_map: dict | None = None, space_floor_cm: float = 0.0,
                    display_num: int | None = None):
    """One numbered question and its working space, kept on one page."""
    return KeepTogether(_question_flowables(styles, q_num, vq, page_map,
                                            space_floor_cm, display_num))


def _passage_question_block(styles, passage, q_flowables: list):
    """A reading and the first question about it, bound onto one page."""
    return KeepTogether([_passage_flowable(styles, passage),
                         Spacer(1, 0.35 * cm)] + q_flowables)


ASSET_DIR = Path(__file__).resolve().parent / "assets"


def cover_background_path() -> str | None:
    """Resolve the cover background image. Override with the env var
    FOLIO_COVER_BACKGROUND, otherwise use booklet_gen/assets/cover_background.png
    if present. Returns None when no background is configured (plain cover)."""
    env = os.environ.get("FOLIO_COVER_BACKGROUND")
    if env and Path(env).exists():
        return env
    # Prefer the JPEG: the PNG is the higher-quality source, but it is 1.4MB
    # and rides along in every booklet, which dominated the output file size.
    # The JPEG is visually equivalent for a soft-gradient background at a
    # tenth of the size.
    for name in ("cover_background.jpg", "cover_background.png"):
        path = ASSET_DIR / name
        if path.exists():
            return str(path)
    return None


def _draw_page_chrome(canvas, doc):
    canvas.saveState()
    # Page 1 is the cover. When a background image is configured, draw it full
    # bleed and skip the running header/footer so the design stays clean.
    if doc.page == 1 and getattr(doc, "_cover_bg", None):
        try:
            canvas.drawImage(
                doc._cover_bg, 0, 0, width=A4[0], height=A4[1],
                preserveAspectRatio=False, mask="auto",
            )
        except Exception:
            pass
        canvas.restoreState()
        return
    # Exam papers use a plain cover with no background; still keep the running
    # header off it, the way a real examination front page looks.
    if doc.page == 1 and getattr(doc, "_plain_cover", False):
        canvas.restoreState()
        return
    canvas.setFont(FONT_REGULAR, 9)
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.drawRightString(
        A4[0] - PAGE_MARGIN, 1.2 * cm, f"Page {doc.page}",
    )
    header = getattr(doc, "_header_text", "")
    if header:
        canvas.drawString(PAGE_MARGIN, A4[1] - 1.2 * cm, header)
        canvas.setStrokeColor(colors.HexColor("#DDDDDD"))
        canvas.line(PAGE_MARGIN, A4[1] - 1.35 * cm, A4[0] - PAGE_MARGIN, A4[1] - 1.35 * cm)
    canvas.restoreState()


def _closing_note(styles, data: BookletData):
    """A short sign-off after the last question, addressed to the student."""
    name = _escape(data.student_name or "").strip()
    opening = f"That is the end of the booklet, {name}." if name \
        else "That is the end of the booklet."
    tail = ("Go through it with whoever is working with you, check your answers "
            "against the key at the back, and mark anything you want to go over "
            "again.")
    tbl = Table([[Paragraph(f"{opening} Well done for getting through it. {tail}",
                            styles["closing"])]],
                colWidths=[A4[0] - 2 * PAGE_MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F7FB")),
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#B7C3D4")),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return KeepTogether([tbl])


def part_counts(data: BookletData) -> list[tuple[str, int]]:
    """(part name, question count) for every part that has questions."""
    rows = []
    spelling = spelling_words(getattr(data, "spelling_test", None))
    if spelling:
        rows.append(("Spelling Test", len(spelling)))
    if data.recap_questions:
        rows.append(("Warm-up Recap", len(data.recap_questions)))
    cw = sum(len(s.questions) for s in data.sections)
    if cw:
        rows.append(("Class Work", cw))
    hw = sum(len(s.homework_questions) for s in data.sections)
    if hw:
        rows.append(("Homework", hw))
    if data.challenge_questions:
        rows.append(("Final Challenge", len(data.challenge_questions)))
    return rows


def _score_card(styles, data: BookletData):
    """Somewhere to write a mark.

    A tutor following a student across a ten week term plan had nothing to
    write a total on, so there was nothing to compare week to week. Printed in
    both copies: the marking happens on the child's copy, and the tutor's copy
    needs the same box to transcribe into.
    """
    rows = part_counts(data)
    total = sum(n for _, n in rows)
    # Laid out across the page rather than down it: a column of one line per
    # part orphaned itself onto a page of its own.
    names = [Paragraph(f"<b>{_escape(n)}</b>", styles["footer_note_left"])
             for n, _ in rows] + [Paragraph("<b>Total</b>", styles["footer_note_left"])]
    marks = [Paragraph(f"______ / {n}", styles["question"]) for _, n in rows]
    marks.append(Paragraph(f"<b>______ / {total}</b>", styles["question"]))
    width = A4[0] - 2 * PAGE_MARGIN
    col = width / max(1, len(names))
    tbl = Table([names, marks], colWidths=[col] * len(names))
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#B7C3D4")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D5DDE8")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F7FB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    caption = Paragraph(
        "<b>Score</b>      Marked by: ____________________      "
        "Date: ______________", styles["footer_note_left"])
    return KeepTogether([caption, Spacer(1, 0.1 * cm), tbl])


def _session_band(styles, index: int, of: int, minutes: int, count: int):
    """A day marker inside the Homework part.

    Thirty-five questions billed as one number "across the week" is not a plan,
    it is a pile. This splits the pile into sittings a parent can point at.

    The band counts the sitting's questions rather than naming a numbered span.
    Question numbers restart at each subtopic and each reading, so "questions 17
    to 27" named numbers that appear nowhere on the page. Where a sitting stops
    is shown by where the next band starts; what the band has to say is how much
    work is in it.
    """
    span = "1 question" if count == 1 else f"{count} questions"
    text = (f"<b>Session {index} of {of}</b>  |  {span}  |  about {minutes} min"
            "  |  Date: __________")
    tbl = Table([[Paragraph(text, styles["question"])]],
                colWidths=[A4[0] - 2 * PAGE_MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1E7EB")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor("#8B1E3F")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Spelling
#
# Spelling runs across a term, so it appears twice in a booklet and the two
# appearances are not the same thing.
#
#   The LIST is at the back: twenty words to learn for next week, printed in
#   full, because learning them is the point.
#
#   The TEST is at the front and is a dictation on the previous week's list:
#   twelve numbered ruled spaces and not one word printed anywhere on the page.
#   The adult calls the words out; printing them beside the lines would be a
#   spelling test you can copy from.
#
# The booklet is worked through by a parent or tutor sitting with the child, so
# there is no second document to hide anything in. The twelve words the adult
# calls out are therefore printed in the answer key at the very back: it is the
# furthest page in the booklet from the test page at the front, so the adult can
# hold the back open while the child works on the front. The alternative, a
# footer on the test page naming which words to use, sits directly under the
# child's pencil, and the alternative of relying on last week's booklet fails
# the moment last week's booklet is not on the table.
# ---------------------------------------------------------------------------

SPELLING_COLUMNS = 2
_SPELLING_ROW_CM = 1.3

# Blank spaces on the test page when nothing says otherwise. Twelve of the
# previous week's twenty words is the product's shape, and the page has to be
# printable before the generator has chosen which twelve.
SPELLING_TEST_SPACES = 12


def spelling_words(obj) -> list[str]:
    """The words on a SpellingList or SpellingTest, or [] when absent."""
    return [w for w in (getattr(obj, "words", None) or []) if str(w).strip()]


def spelling_test_spaces(test) -> int:
    """How many ruled spaces the test page prints, 0 when there is no test."""
    if test is None:
        return 0
    return len(spelling_words(test)) or SPELLING_TEST_SPACES


def _spelling_grid(styles, cells: list, ruled: bool):
    """A numbered grid, filled down each column: 1 to 10 left, 11 to 20 right.

    `cells` are the flowables to sit beside each number. When `ruled` is set a
    rule is drawn under each numbered cell, which is what turns the grid into
    something a child writes on.
    """
    count = len(cells)
    columns = SPELLING_COLUMNS if count > 6 else 1
    rows = (count + columns - 1) // columns
    body_w = A4[0] - 2 * PAGE_MARGIN
    num_w = 1.0 * cm
    line_w = body_w / columns - num_w
    data, style = [], [
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for r in range(rows):
        row = []
        for c in range(columns):
            i = c * rows + r
            if i < count:
                row.append(Paragraph(f"{i + 1}.", styles["spelling_num"]))
                row.append(cells[i])
                if ruled:
                    style.append(("LINEBELOW", (2 * c + 1, r), (2 * c + 1, r),
                                  0.6, colors.HexColor("#9AA6B8")))
            else:
                row.extend(["", ""])
        data.append(row)
    tbl = Table(data, colWidths=[num_w, line_w] * columns,
                rowHeights=[_SPELLING_ROW_CM * cm] * rows)
    tbl.setStyle(TableStyle(style))
    return tbl


def _spelling_test_block(styles, test, minutes: int | None = None):
    """The dictation page: numbered ruled spaces and no words at all.

    Nothing here names the words or where they came from. The page a child sits
    over during a spelling test is the one place in the booklet that has to be
    free of clues.
    """
    spaces = spelling_test_spaces(test)
    if not spaces:
        return []
    band = _part_band(
        styles, "Spelling Test", "#3F6B5E",
        f"{spaces} words. Someone will read each word out loud. "
        "Write it on the line beside its number."
        + (f" About {minutes} min." if minutes else ""))
    grid = _spelling_grid(styles, [Paragraph("", styles["spelling_word"])
                                   for _ in range(spaces)], ruled=True)
    return [band, Spacer(1, 0.45 * cm), grid, Spacer(1, 0.5 * cm)]


def _spelling_list_block(styles, spelling_list):
    """The words to learn for next week, printed in full."""
    words = spelling_words(spelling_list)
    if not words:
        return []
    band = _part_band(
        styles, "Spelling List", "#3F6B5E",
        f"{len(words)} words to learn for next week. "
        "You will be tested on these in your next booklet.")
    grid = _spelling_grid(
        styles,
        [Paragraph(_escape(str(w)), styles["spelling_word"]) for w in words],
        ruled=False)
    return [KeepTogether([band, Spacer(1, 0.45 * cm), grid])]


def _spelling_key_block(styles, test):
    """The words to call out, printed in the key at the back.

    The furthest page in the booklet from the test page at the front, which is
    the point: the adult reads from here while the child works up the front.
    """
    words = spelling_words(test)
    if not words:
        return []
    from_week = getattr(test, "from_week", None)
    # The size of the list these were drawn from is not carried here, and the
    # sentence used to assert it was twenty. It said "10 of the twenty words"
    # over a list of ten, in a booklet whose own spelling list is ten long.
    # Say only what the data supports.
    n = len(words)
    word_s = "word" if n == 1 else "words"
    source = (f"These are {n} {word_s} from the list set in week {from_week}. "
              if from_week else
              f"These are {n} {word_s} from last week's list. ")
    numbered = ", ".join(f"{i}. {_escape(str(w))}" for i, w in enumerate(words, 1))
    return [KeepTogether([
        Paragraph("Spelling Test", styles["topic"]),
        Paragraph(source + "Read them out one at a time, in this order. They "
                  "are deliberately not printed on the test page.",
                  styles["challenge_blurb"]),
        Paragraph(numbered, styles["answer"]),
        Spacer(1, 0.4 * cm),
    ])]


# Key for the page number of the last page the student writes on, recorded by
# the probe build into the same map the question PageMarkers use.
LAST_STUDENT_PAGE = "last_student_page"


def _booklet_story(styles, data: BookletData, times: dict, *,
                   cover_bg: str | None,
                   page_map: dict | None, page_refs: dict | None,
                   blank_before_key: bool = False) -> list:
    """Build the whole story for the booklet.

    Called twice. On the first call `page_map` is an empty dict that the
    PageMarkers fill in as the throwaway build lays the questions out; on the
    second it is `page_refs`, and the answer key can say which page each
    question is on.
    """
    # Always present, even when nobody reads it: the markers are zero-height,
    # and keeping them in both builds means the two builds lay out from an
    # identical story, so the page each question landed on in the throwaway
    # build is the page it lands on in the real one.
    page_map = {} if page_map is None else page_map
    story: list = []

    # Cover - lead with the product line (program) when present, otherwise the
    # subject. The secondary line carries the subject(s) and year level. With a
    # background image the text is pushed down to sit in the clear centre zone.
    story.append(Spacer(1, 6.5 * cm if cover_bg else 3 * cm))
    story.append(Paragraph("FOLIO", styles["wordmark"]))
    story.append(Spacer(1, 0.6 * cm))
    headline = data.program_label or data.subject
    story.append(Paragraph(_escape(headline), styles["title"]))
    secondary = data.subject if data.program_label else "Practice Booklet and Early Preparation"
    if secondary:
        story.append(Paragraph(f"{_escape(secondary)}  |  {data.year_level}", styles["subtitle"]))
    else:
        story.append(Paragraph(data.year_level, styles["subtitle"]))
    story.append(Spacer(1, 1.5 * cm))
    story.append(Paragraph(f"Prepared for <b>{_escape(data.student_name)}</b>", styles["subtitle"]))
    story.append(Spacer(1, 0.4 * cm))
    if data.week_number and data.total_weeks:
        wk_line = f"Week {data.week_number} of {data.total_weeks}"
        if data.week_focus:
            wk_line += f"  |  {_escape(data.week_focus)}"
        story.append(Paragraph(wk_line, styles["meta"]))
        story.append(Spacer(1, 0.15 * cm))
    story.append(Paragraph(date.today().strftime("%d %B %Y"), styles["meta"]))
    if times["total_minutes"]:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"Estimated time: about {times['total_minutes']} minutes. "
            "Take breaks whenever you need to.",
            styles["meta"],
        ))
    story.append(Spacer(1, 4 * cm))
    # One booklet, worked through together. The verification mark now lives
    # beside the answers in the key rather than beside unattempted questions.
    section_subjects = {(s.subject or data.subject).strip().lower()
                        for s in data.sections}
    only_maths = section_subjects == {"mathematics"}
    # Two claims that were not true of the booklet they were printed on.
    #
    # "show your working" went on every cover, including English booklets,
    # where there is no working to show.
    #
    # "symbolically verified" went on every all-maths cover regardless of what
    # actually ran. Only questions SymPy can decide are proved symbolically;
    # everything else, which is most of a primary booklet ("Round 468 to the
    # nearest hundred", "Explain his mistake"), is checked by the LLM judge,
    # the same one English uses. Claiming an algebra engine stood behind
    # "explain his mistake" is the kind of thing a sceptical tutor screenshots,
    # and it devalues the mark on the answers where it is earned. Until the
    # mark distinguishes the two, the cover claims only what is true of all of
    # them.
    story.append(Paragraph(
        "Work through it in order"
        + (" and show your working." if only_maths else ".")
        + " Every answer in the key at the back has been checked for accuracy.",
        styles["footer_note"],
    ))
    story.append(PageBreak())

    multi_subject = len({(s.subject or "") for s in data.sections if s.subject}) > 1

    # `n` is the running index, the identity of a question, used for the page
    # map and to line the key up with the body. `nums` turns it into the number
    # actually printed, which restarts at each reading and each subtopic.
    counter = {"n": 0}
    nums = question_numbering(data)

    def shown(n: int) -> int:
        return nums.get(n, n)

    # A clear line between one question and the next. It goes between the
    # blocks rather than inside them: a spaceBefore on the question paragraph
    # lets ReportLab split the KeepTogether, which separates a question from
    # the working space that belongs to it.
    Q_GAP = 0.3 * cm

    def render_questions(qs, space_floor_cm: float = 0.0):
        for i, vq in enumerate(qs):
            counter["n"] += 1
            if i:
                story.append(Spacer(1, Q_GAP))
            story.append(_question_block(styles, counter["n"], vq, page_map,
                                         space_floor_cm, shown(counter["n"])))

    def render_passage_questions(section, qs, space_floor_cm: float = 0.0):
        """Questions grouped under their reading, passage first.

        The passage and the first question that refers to it are bound into one
        KeepTogether, so "Referring to the passage above" can never point at a
        passage on the following page.
        """
        first = True
        for passage, group in passage_groups(qs, section_passages(section)):
            for i, vq in enumerate(group):
                counter["n"] += 1
                if not first:
                    story.append(Spacer(1, Q_GAP))
                first = False
                if passage is not None and i == 0:
                    # Built from loose flowables, never by wrapping the
                    # finished block: a KeepTogether inside a KeepTogether
                    # measures as 0xffffff and breaks the page every time.
                    block = _passage_question_block(
                        styles, passage,
                        _question_flowables(styles, counter["n"], vq, page_map,
                                            space_floor_cm,
                                            shown(counter["n"])))
                else:
                    block = _question_block(styles, counter["n"], vq, page_map,
                                            space_floor_cm,
                                            shown(counter["n"]))
                story.append(block)

    def subject_topic_headers(section, state):
        if multi_subject and section.subject and section.subject != state["subject"]:
            story.append(Paragraph(_escape(section.subject), styles["subject_band"]))
            state["subject"] = section.subject
            state["topic"] = None
        if section.topic != state["topic"]:
            story.append(Paragraph(_escape(section.topic), styles["topic"]))
            state["topic"] = section.topic

    # ---- Spelling Test (dictation on last week's list, before anything else) ----
    story.extend(_spelling_test_block(styles, getattr(data, "spelling_test", None),
                                      times.get("spelling_minutes")))

    # ---- Warm-up Recap ----
    if data.recap_questions:
        sub = f"Quick revision to warm up. About {times['recap_minutes']} min." \
            if times["recap_minutes"] else "Quick revision to warm up."
        story.append(_part_band(styles, "Warm-up Recap", "#6b7280", sub))
        story.append(Spacer(1, 0.3 * cm))
        render_questions(data.recap_questions, _RECAP_MIN_SPACE_CM)

    # ---- Class Work (lesson + guided + now-you-try) ----
    cw_sub = f"Do this in your lesson. About {times['classwork_minutes']} min." \
        if times["classwork_minutes"] else "Do this in your lesson."
    story.append(_part_band(styles, "Class Work", "#1F3A5F", cw_sub))
    story.append(Spacer(1, 0.3 * cm))
    state = {"subject": None, "topic": None}
    for si, section in enumerate(data.sections):
        # A subtopic the hour could not fit has had its practice moved to
        # Homework, and its lesson goes with it. Printing the lesson here and
        # counting it as free is what put an hour's session at "about 100 min":
        # a mini-lesson with its worked and guided examples is about twelve
        # minutes of teaching whether or not practice follows it.
        if not section.questions:
            continue
        subject_topic_headers(section, state)
        time_badge = (
            f'  <font size=9 color="#1B8A3A">'
            f'(about {times["section_minutes"][si]} min)</font>'
        )
        story.append(Paragraph(_escape(section.subtopic) + time_badge, styles["subtopic"]))

        t = section.teaching
        if t is not None:
            story.extend(_lesson_flowables(styles, t))
            if section.questions:
                # Only when something follows it. A subtopic the hour could not
                # fit keeps its lesson but has had its practice moved to
                # Homework, and "Now you try:" over an empty space, immediately
                # under the next heading, is what that used to print.
                story.append(Spacer(1, 0.35 * cm))
                story.append(Paragraph("Now you try:", styles["practice_label"]))

        render_passage_questions(section, section.questions)

    # ---- Homework (repetition through the week) + Final Challenge ----
    has_homework = any(s.homework_questions for s in data.sections)
    if has_homework or data.challenge_questions:
        # Not an unconditional break. Class Work used to end wherever it ended
        # and throw the rest of the page away: one real booklet finished the
        # section two questions into a page and left the other two thirds
        # blank. The coloured band is divider enough, so only break when there
        # is too little room left to be worth starting Homework here.
        story.append(CondPageBreak(HOMEWORK_MIN_START_CM * cm))
        sessions = homework_session_plan(data)
        hw_sub = ("Do these through the week to lock it in. "
                  f"About {times['homework_minutes']} min.") \
            if times["homework_minutes"] else "Do these through the week to lock it in."
        if sessions:
            hw_sub = ("Do these through the week to lock it in. "
                      f"Split into {len(sessions)} sessions, "
                      f"about {times['homework_minutes']} min in total.")
        story.append(_part_band(styles, "Homework", "#8B1E3F", hw_sub))
        story.append(Spacer(1, 0.3 * cm))

        # Session boundaries are indices into the flat homework list, so a
        # session may start part way through a subtopic. When it starts on the
        # first question of a subtopic the band goes above that heading, not
        # between the heading and its questions.
        starts = {s["start"]: (i + 1, s) for i, s in enumerate(sessions)}

        def session_band_for(flat_index: int):
            hit = starts.get(flat_index)
            if not hit:
                return None
            i, s = hit
            return _session_band(styles, i, len(sessions), s["minutes"],
                                 s["count"])

        flat = 0
        state = {"subject": None, "topic": None}
        for section in data.sections:
            if not section.homework_questions:
                continue
            # Build this subtopic's headings, then hold them back: if a session
            # starts here the band belongs above the heading, not between the
            # heading and its first question.
            mark = len(story)
            subject_topic_headers(section, state)
            story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
            # A subtopic that did not fit the session brings its mini-lesson
            # down here, so the teaching is not lost: nothing else in the
            # booklet explains the skill its homework asks for. Subtopics that
            # were taught in the session do not repeat their lesson.
            if not section.questions and section.teaching is not None:
                story.extend(_lesson_flowables(styles, section.teaching))
            headings = story[mark:]
            del story[mark:]

            j = 0
            for passage, group in passage_groups(section.homework_questions,
                                                 section_passages(section)):
                for i, vq in enumerate(group):
                    if j:
                        story.append(Spacer(1, Q_GAP))
                    band = session_band_for(flat)
                    if band is not None:
                        if flat:
                            # Do not leave a session band stranded at the foot of
                            # a page with its first question overleaf.
                            story.append(Spacer(1, 0.3 * cm))
                            story.append(CondPageBreak(3.5 * cm))
                        story.append(band)
                        story.append(Spacer(1, 0.25 * cm))
                    if j == 0:
                        story.extend(headings)
                    counter["n"] += 1
                    if passage is not None and i == 0:
                        # Homework is worked days later and pages away from the
                        # class work, so a passage used in both parts is printed
                        # again here rather than referred back to.
                        #
                        # Built from the loose flowables, never by wrapping the
                        # finished question block: a KeepTogether inside a
                        # KeepTogether reports 0xffffff for its height, so the
                        # outer one believes it can never fit and breaks the
                        # page every time, stranding the band above it.
                        block = _passage_question_block(
                            styles, passage,
                            _question_flowables(styles, counter["n"], vq,
                                                page_map, 0.0,
                                                shown(counter["n"])))
                    else:
                        block = _question_block(styles, counter["n"], vq,
                                                page_map, 0.0,
                                                shown(counter["n"]))
                    story.append(block)
                    flat += 1
                    j += 1

        if data.challenge_questions:
            story.append(Spacer(1, 0.4 * cm))
            story.append(Paragraph("Final Challenge", styles["challenge_heading"]))
            ct = (f" (about {times['challenge_minutes']} min)"
                  if times["challenge_minutes"] else "")
            story.append(Paragraph(
                "Now let's see how well you know it all. Questions from across "
                f"everything you practised.{ct}",
                styles["challenge_blurb"],
            ))
            render_questions(data.challenge_questions)

    # ---- Spelling List (words to learn for next week) ----
    spelling_block = _spelling_list_block(
        styles, getattr(data, "spelling_list", None))
    if spelling_block:
        story.append(Spacer(1, 0.5 * cm))
        story.extend(spelling_block)

    # ---- Closing ----
    # The booklet used to stop dead: the page after the last question was the
    # answer key. Say something to the student first, by name.
    story.append(Spacer(1, 0.6 * cm))
    story.append(_closing_note(styles, data))
    story.append(Spacer(1, 0.35 * cm))
    story.append(_score_card(styles, data))

    # ---- Answer key (same order: recap, class work, homework, challenge) ----
    #
    # The key must come off a different sheet of paper from the last page the
    # student writes on. Printed double-sided, an odd-numbered last student
    # page puts the key on the back of it, and the first thing on the key is
    # the spelling dictation list this booklet takes deliberate trouble to keep
    # out of the child's hands. Turning the sheet over handed it straight back.
    #
    # The probe build records where the student half ends; the real build adds
    # a blank verso when that page is odd. Says so on the page, so a parent
    # does not read a blank sheet as a printing fault.
    if page_map is not None:
        story.append(PageMarker(page_map, LAST_STUDENT_PAGE))
    if blank_before_key:
        story.append(PageBreak())
        story.append(Paragraph(
            "This page is intentionally blank, so the answers start on a new "
            "sheet of paper.", styles["footer_note"]))
    story.append(PageBreak())
    story.append(Paragraph("Answers &amp; Worked Solutions", styles["answers_heading"]))
    story.append(Paragraph(
        "For whoever is marking. Page numbers in brackets point back to the "
        "question.", styles["challenge_blurb"]))
    story.extend(_spelling_key_block(styles, getattr(data, "spelling_test", None)))
    acount = {"n": 0}

    def render_answers(qs):
        for vq in qs:
            acount["n"] += 1
            page = (page_refs or {}).get(acount["n"])
            # Numbered as the body numbered it. The running index still drives
            # the page lookup, because several questions now print as "3".
            story.append(_answer_block(styles, shown(acount["n"]), vq, page))

    if data.recap_questions:
        story.append(Paragraph("Warm-up Recap", styles["topic"]))
        render_answers(data.recap_questions)

    story.append(Paragraph("Class Work", styles["topic"]))
    state = {"subject": None, "topic": None}
    for section in data.sections:
        # A subtopic the hour cap moved out has no class work, and its answers
        # are printed under Homework below. Without this the key printed its
        # heading here with nothing underneath, and whoever was marking read
        # that as a missing page. The Homework loop has always had the
        # equivalent guard.
        if not section.questions:
            continue
        subject_topic_headers(section, state)
        story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
        # Grouping questions under their passage changes the printed order, so
        # the key has to be walked in the same order or every number after the
        # first passage points at the wrong question.
        render_answers(ordered_questions(section.questions,
                                         section_passages(section)))

    if has_homework:
        story.append(Paragraph("Homework", styles["topic"]))
        state = {"subject": None, "topic": None}
        for section in data.sections:
            if not section.homework_questions:
                continue
            subject_topic_headers(section, state)
            story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
            render_answers(ordered_questions(section.homework_questions,
                                             section_passages(section)))

    if data.challenge_questions:
        story.append(Paragraph("Final Challenge", styles["topic"]))
        render_answers(data.challenge_questions)

    story.extend(_image_credits_block(styles, data))
    return story


def all_questions(data: BookletData) -> list:
    """Every ValidatedQuestion in the booklet, in printed order."""
    out = list(data.recap_questions)
    for s in data.sections:
        out.extend(s.questions)
    for s in data.sections:
        out.extend(s.homework_questions)
    out.extend(data.challenge_questions)
    return out


def image_credits(data: BookletData) -> list[str]:
    """Attributions for the pictures actually printed, deduplicated, in order.

    Only images that made it onto a page: a question can carry an attribution
    from a lookup whose file never resolved, and crediting a picture the
    booklet does not contain is worse than crediting nothing. So this asks the
    same question the layout asks, rather than trusting `image_path` to be set,
    which a failed download leaves behind anyway.
    """
    seen, out = set(), []
    for vq in all_questions(data):
        credit = (getattr(vq, "image_attribution", None) or "").strip()
        if not image_is_usable(vq.image_path) or not credit or credit in seen:
            continue
        seen.add(credit)
        out.append(credit)
    return out


def _image_credits_block(styles, data: BookletData) -> list:
    """The picture credits, gathered on their own page at the very back.

    They used to sit under each image, where a licence string on a Year 3
    worksheet reads as clutter. They still have to be printed: these are
    Wikimedia Commons photographs and the licences require attribution.
    """
    credits = image_credits(data)
    if not credits:
        return []
    out = [PageBreak(),
           Paragraph("Picture Credits", styles["answers_heading"]),
           Paragraph(
               "The photographs in this booklet come from Wikimedia Commons "
               "and are used under their respective licences.",
               styles["challenge_blurb"])]
    for c in credits:
        out.append(Paragraph(f"• {_escape(c)}", styles["key_point"]))
    return out


def _booklet_doc(target, data: BookletData):
    doc = BaseDocTemplate(
        target,
        pagesize=A4,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        title=f"{data.program_label or data.subject} Practice Booklet",
        author="Folio",
    )
    _head = data.program_label or data.subject
    doc._header_text = f"{_head}  |  {data.year_level}  |  {data.student_name}"
    doc._cover_bg = cover_background_path()
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame,
                                       onPage=_draw_page_chrome)])
    return doc


def render_pdf(data: BookletData, out_path: Path) -> Path:
    """Render the booklet, answer key included.

    One document. A Folio booklet is worked through by a parent or tutor sitting
    with the child, so the key belongs at the back of the same booklet rather
    than in a second file. What the key must not do is leak into the pages the
    child works on: the verification marks live beside the answers, and the
    spelling dictation words are printed only here.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    styles = _make_styles()
    # Times are recomputed here rather than read off the BookletData. The
    # pipeline's numbers count questions only; these count what is actually
    # printed, including the mini-lesson, the worked example and every guided
    # example the student has to work through. See timing.py.
    times = booklet_timing(data)
    cover_bg = cover_background_path()

    # Throwaway build purely to find out which page each question landed on.
    # Everything that affects that pagination sits before the answer key's
    # PageBreak, so the map is the same in the real build.
    page_refs: dict = {}
    probe = _booklet_doc(io.BytesIO(), data)
    probe.build(_booklet_story(
        styles, data, times,
        cover_bg=cover_bg, page_map=page_refs, page_refs=None))

    # An odd last student page means the key would print on its reverse. The
    # blank verso shifts every key page by one, but nothing references a key
    # page, so the map built above still holds.
    blank_before_key = page_refs.get(LAST_STUDENT_PAGE, 0) % 2 == 1

    doc = _booklet_doc(str(out_path), data)
    doc.build(_booklet_story(
        styles, data, times,
        cover_bg=cover_bg, page_map=None, page_refs=page_refs,
        blank_before_key=blank_before_key))
    return out_path



def _exam_question_block(styles, q_num: int, vq: ValidatedQuestion, body_width: float):
    """An exam question: number and text on the left, marks in the right margin,
    then ruled working space sized to the marks awarded."""
    marks = vq.question.marks or 0
    mark_label = f"({marks} mark{'s' if marks != 1 else ''})" if marks else ""
    # Exam questions carry (a)/(b) parts on their own lines, so honour the
    # newlines the generator emits instead of running them together.
    text = _escape(vq.question.question).replace("\n", "<br/>")
    row = Table(
        [[Paragraph(f"<b>{q_num}.</b>&nbsp;&nbsp;{text}", styles["question"]),
          Paragraph(mark_label, styles["exam_marks"])]],
        colWidths=[body_width - 2.6 * cm, 2.6 * cm],
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    block = [row]

    img = _make_image(vq.image_path)
    if img is not None:
        block.append(Spacer(1, 0.3 * cm))
        block.append(img)

    # Working space: roughly proportional to the marks, within sane bounds.
    space = min(max(marks, 1) * 0.9, 7.0)
    block.append(Spacer(1, space * cm))
    return KeepTogether(block)


def render_exam_pdf(paper: ExamPaper, out_path: Path) -> Path:
    """Render a practice ATAR examination paper.

    Deliberately separate from render_pdf: an exam has no teaching content, its
    questions carry marks, and it needs a formal instructions cover and a
    marking key rather than a friendly answer key.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        title=f"{paper.subject} Practice Examination",
        author="Folio",
    )
    doc._header_text = f"{paper.subject}  |  {paper.year_level}  |  {paper.student_name}"
    # Exams use a plain cover: a decorative background undercuts the look.
    doc._cover_bg = None
    doc._plain_cover = True

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_draw_page_chrome)])

    styles = _make_styles()
    body_width = A4[0] - 2 * PAGE_MARGIN
    story = []

    # ---- Cover: formal exam front page ----
    story.append(Spacer(1, 1.2 * cm))
    story.append(Paragraph("FOLIO", styles["wordmark"]))
    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph("Practice Examination", styles["subtitle"]))
    story.append(Paragraph(_escape(paper.subject), styles["title"]))
    if paper.unit:
        story.append(Paragraph(_escape(paper.unit), styles["subtitle"]))
    story.append(Spacer(1, 1.0 * cm))
    story.append(Paragraph(f"Prepared for <b>{_escape(paper.student_name)}</b>",
                           styles["subtitle"]))
    story.append(Spacer(1, 1.0 * cm))

    total_time = paper.reading_minutes + paper.working_minutes
    info = [
        ["Reading time", f"{paper.reading_minutes} minutes"],
        ["Working time", f"{paper.working_minutes} minutes"],
        ["Total time", f"{total_time} minutes"],
        ["Total marks", str(paper.total_marks)],
    ]
    for s in paper.sections:
        pct = round(100 * s.total_marks / paper.total_marks) if paper.total_marks else 0
        info.append([s.name, f"{s.total_marks} marks ({pct}%)"])
    tbl = Table(info, colWidths=[body_width * 0.45, body_width * 0.55])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1c2434")),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#DDDDDD")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)

    if paper.materials:
        story.append(Spacer(1, 0.8 * cm))
        story.append(Paragraph("Material required for this examination", styles["subtopic"]))
        for line in paper.materials:
            story.append(Paragraph(f"• {_escape(line)}", styles["key_point"]))

    story.append(Spacer(1, 0.8 * cm))
    story.append(Paragraph(
        "This is a practice paper generated by Folio. Questions marked with a "
        "check mark in the marking key have been symbolically verified.",
        styles["footer_note"],
    ))
    story.append(PageBreak())

    # ---- Sections ----
    counter = {"n": 0}
    for section in paper.sections:
        sub = f"{section.total_marks} marks"
        if section.working_minutes:
            sub += f"  |  suggested working time {section.working_minutes} minutes"
        story.append(_part_band(styles, section.name, "#1F3A5F", sub))
        if section.description:
            story.append(Spacer(1, 0.25 * cm))
            story.append(Paragraph(_escape(section.description), styles["intro_para"]))
        story.append(Spacer(1, 0.4 * cm))
        for vq in section.questions:
            counter["n"] += 1
            story.append(_exam_question_block(styles, counter["n"], vq, body_width))
        story.append(PageBreak())

    # ---- Marking key ----
    story.append(_part_band(styles, "Marking Key", "#1B8A3A",
                            "Solutions and mark allocations"))
    story.append(Spacer(1, 0.4 * cm))
    counter["n"] = 0
    for section in paper.sections:
        story.append(Paragraph(_escape(section.name), styles["topic"]))
        for vq in section.questions:
            counter["n"] += 1
            story.append(_answer_block(styles, counter["n"], vq, tidy_answer=False))

    doc.build(story)
    return out_path


def _answer_block(styles, q_num: int, vq: ValidatedQuestion, page: int | None = None,
                  tidy_answer: bool = True):
    # The only place a verification mark belongs: beside a worked answer, where
    # it tells the person marking that this solution was checked. The check
    # glyph is outside Latin-1, so drop it when we fell back to Helvetica.
    mark = "✓ verified" if _UNICODE_FONT else "verified"
    symbol_html = f' <font color="#1B8A3A"><b>{mark}</b></font>' if vq.verified else ""
    # Marking 63 questions spread over 18 pages means constant flipping, so the
    # key says where the question was.
    page_html = (f' <font size=9 color="#888888">(p{page})</font>'
                 if page else "")
    # Booklet keys restore the unit the question asked for and show a fraction
    # in lowest terms. An exam marking key does neither: senior answers carry
    # compound units and exact forms that must be reproduced as marked.
    answer = key_answer(vq.question) if tidy_answer else (vq.question.answer or "")
    block = [
        Paragraph(
            f"<b>{q_num}.</b> Answer: {_escape(answer)}{symbol_html}{page_html}",
            styles["answer"],
        ),
    ]
    for line in solution_lines(vq.question.working):
        block.append(Paragraph(_escape(line), styles["working"]))
    block.append(Spacer(1, 0.35 * cm))
    return KeepTogether(block)
