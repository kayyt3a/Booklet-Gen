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
    BaseDocTemplate, Frame, FrameBreak, NextPageTemplate, PageTemplate,
    Paragraph, Spacer, PageBreak, CondPageBreak, KeepTogether, Table,
    TableStyle, Image,
)
from reportlab.platypus.flowables import Flowable
from reportlab.lib.utils import ImageReader

from .schemas import BookletData, ExamPaper, ValidatedQuestion, WorkedExample
from .timing import booklet_timing, homework_session_plan
from .visuals.cover import CoverSpec, render_cover, variant_for


PAGE_MARGIN = 2.0 * cm

# ---------------------------------------------------------------------------
# The answer key's measure
#
# The key used to be set across the full 539pt of the page and the median
# answer line ended at 300pt, so half of every key page was blank, over six
# pages. A line of ten words across a measure built for thirty is what a
# printed list of answers looks like when nobody chose a measure for it.
#
# Two columns fixes both halves of that: the measure suits the line length the
# key actually produces, and the key comes down from about six pages to three,
# which is three fewer sheets a customer prints per booklet.
KEY_COLUMN_GAP = 0.8 * cm
KEY_COLUMN_WIDTH = (A4[0] - 2 * PAGE_MARGIN - KEY_COLUMN_GAP) / 2
# The right-aligned strip holding the tick and the "(p9)" back-reference.
_KEY_MARK_CM = 1.45
# How far a wrapped answer hangs, so its later lines line up under the answer
# rather than under the question number. Set at the width of "12. ", which
# leaves the number alone in the margin and the answer in one block: a deeper
# hang would look tidier on a full page and leaves too little measure in a
# column for a comprehension answer, which is the longest thing in the key.
_KEY_HANG_CM = 0.85

# Where the running header and the page number sit, measured from the sheet
# edge. They used to sit at 1.2cm, which put the descender of "Page" 11.3mm
# from the edge, inside the unprintable band of common home printers (HP
# DeskJet 12.7mm, Epson EcoTank 14.0mm). Printing at actual size clipped or
# dropped the page number, and the answer key's "(p8)" back-references are
# useless without it; printing to fit instead rescaled the whole sheet to about
# 94 percent and quietly shrank every ruled line the child writes on. 1.6cm
# clears both. It also moves the header out from under a corner staple.
CHROME_MARGIN = 1.6 * cm

# The four parts of the booklet, each with its own colour, used for the band in
# the body and the matching heading in the answer key. Named rather than
# repeated as literals: Homework and the Final Challenge were both #8B1E3F in
# four separate places, so at a flip-through the routine practice and the
# graded cumulative test were the same block of colour and only the words told
# them apart.
#
# The Final Challenge is bronze, not another red. It reads as the trophy at the
# end, it ties to the brand's orange rather than introducing a new hue, and it
# is the one choice here that also separates from Homework in GREYSCALE
# (relative luminance 0.15 against maroon's 0.07), which matters because most
# of these are printed on a home mono printer.
PART_RECAP = "#6b7280"
PART_CLASSWORK = "#1F3A5F"
PART_HOMEWORK = "#8B1E3F"
PART_CHALLENGE = "#9A5B0E"

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
# The heading face. A booklet set entirely in one sans reads as a document
# somebody exported, because that is what an unstyled export looks like; every
# printed workbook a parent has paid for pairs a display face for its headings
# with a text face for its body. Serif here also matches the website, whose
# headings are already a serif, so the page a parent buys on and the page they
# print are recognisably the same product.
FONT_DISPLAY = "Helvetica-Bold"
FONT_DISPLAY_REGULAR = "Helvetica"
FONT_ITALIC = "Helvetica-Oblique"

_UNICODE_FONT = False


def _register_fonts() -> None:
    """Register DejaVu Sans with ReportLab. Falls back to Helvetica silently."""
    global FONT_REGULAR, FONT_BOLD, FONT_ITALIC, _UNICODE_FONT
    global FONT_DISPLAY, FONT_DISPLAY_REGULAR
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
            "DejaVuSerif": "DejaVu Serif",
            "DejaVuSerif-Bold": "DejaVu Serif:bold",
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
        registerFontFamily(
            "DejaVuSerif", normal="DejaVuSerif", bold="DejaVuSerif-Bold",
            italic="DejaVuSerif", boldItalic="DejaVuSerif-Bold",
        )
        FONT_REGULAR, FONT_BOLD, FONT_ITALIC = (
            "DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique")
        # Both serif faces ship in fonts-dejavu-core, the package the Dockerfile
        # already installs, so this needs nothing new in production. If the
        # lookup fails the whole block falls through to Helvetica together and
        # the booklet still prints.
        FONT_DISPLAY, FONT_DISPLAY_REGULAR = "DejaVuSerif-Bold", "DejaVuSerif"
        _UNICODE_FONT = True
    except Exception as e:
        log.info("formatter.font_fallback", extra={"reason": str(e)[:200]})


def _make_styles():
    _register_fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=FONT_DISPLAY,
            fontSize=26, leading=30, alignment=TA_CENTER, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=13, alignment=TA_CENTER, textColor=colors.HexColor("#555555"),
            spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor("#5F5F5F"),
        ),
        "wordmark": ParagraphStyle(
            "wordmark", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=12, alignment=TA_CENTER, textColor=colors.HexColor("#1F3A5F"),
            spaceAfter=4,
        ),
        "subject_band": ParagraphStyle(
            "subject_band", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=15, leading=19, spaceBefore=10, spaceAfter=10,
            textColor=colors.white, backColor=colors.HexColor("#1F3A5F"),
            borderPadding=(6, 8, 6, 8), alignment=TA_CENTER,
        ),
        "part_band": ParagraphStyle(
            "part_band", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=18, leading=22, textColor=colors.white, alignment=TA_CENTER,
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
            "topic", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=19, leading=23, spaceBefore=14, spaceAfter=2,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        # The four parts of the answer key. In the body each of these gets a
        # full-width coloured band, and the key used to set them in "topic",
        # the same style as the topic name inside them, so "Class Work" and
        # "Fractions" were typographically identical and whoever was marking
        # could not see where one part stopped and the next began. The key
        # reuses the body's bands instead.
        "key_part": ParagraphStyle(
            "key_part", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=16, leading=20, spaceBefore=0, spaceAfter=0,
        ),
        # The topic and subtopic inside the key. Separate from the body's,
        # because the key is set in two columns: the body's 19pt topic wrapped
        # to three lines in an 8cm measure, and because the part heading above
        # them is now a reversed-out tab, these have to sit clearly below it.
        "key_topic": ParagraphStyle(
            "key_topic", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=12.5, leading=16, spaceBefore=8, spaceAfter=1,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "key_subtopic": ParagraphStyle(
            "key_subtopic", parent=base["Heading2"], fontName=FONT_DISPLAY,
            fontSize=10.5, leading=14, spaceBefore=4, spaceAfter=4,
            textColor=colors.HexColor("#2A3F73"),
        ),
        "subtopic": ParagraphStyle(
            "subtopic", parent=base["Heading2"], fontName=FONT_DISPLAY,
            fontSize=13, leading=17, spaceBefore=10, spaceAfter=6,
            textColor=colors.HexColor("#2A3F73"),
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
        # The specimen: the sentence the child has to decode, edit or correct.
        # Set upright, not italic. Italics are the one typographic feature every
        # dyslexia guideline names to avoid, because the slant degrades letter
        # shape recognition, and this was the text in the booklet needing the
        # most careful character by character reading: "She ate a apple for
        # snack", "its cold outside said Sam", "Let's eat Grandad". The indent,
        # the colour and the quote marks already separate it from the
        # instruction three times over.
        "we_specimen": ParagraphStyle(
            "we_specimen", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, leading=15, leftIndent=14, rightIndent=10,
            textColor=colors.HexColor("#1F3A5F"), spaceAfter=2,
        ),
        "question_specimen": ParagraphStyle(
            "question_specimen", parent=base["Normal"], fontName=FONT_REGULAR,
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
            textColor=colors.HexColor("#146B2C"),
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
        # The answer key's own answer line. Hanging: a wrapped answer used to
        # start its second line flush with the question number, so the run-on
        # of one answer sat in the same column as the number of the next.
        "key_answer": ParagraphStyle(
            "key_answer", parent=base["Normal"], fontName=FONT_BOLD,
            fontSize=10, leading=13, leftIndent=_KEY_HANG_CM * cm,
            firstLineIndent=-_KEY_HANG_CM * cm,
        ),
        # The tick and the page reference, in their own right-aligned column so
        # they line up down the page and a missing tick is visible at a glance.
        "key_mark": ParagraphStyle(
            "key_mark", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10, leading=13, alignment=TA_RIGHT,
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
            "answers_heading", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=12,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "challenge_heading": ParagraphStyle(
            "challenge_heading", parent=base["Heading1"], fontName=FONT_DISPLAY,
            fontSize=22, leading=26, alignment=TA_CENTER, spaceAfter=6,
            textColor=colors.HexColor(PART_CHALLENGE),
        ),
        "challenge_blurb": ParagraphStyle(
            "challenge_blurb", parent=base["Normal"], fontName=FONT_ITALIC,
            fontSize=11, leading=14, alignment=TA_CENTER, spaceAfter=14,
            textColor=colors.HexColor("#555555"),
        ),
        "footer_note": ParagraphStyle(
            "footer_note", parent=base["Normal"], fontName=FONT_ITALIC,
            fontSize=9, textColor=colors.HexColor("#5F5F5F"), alignment=TA_CENTER,
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
            fontSize=9, leading=12, textColor=colors.HexColor("#7A5424"),
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

# Markdown emphasis, which models emit constantly and which must never reach a
# printed page. Stripped before any notation rule runs, because _STAR_MULT_RE
# below would otherwise read the markers as multiplication: a real booklet
# printed "multiply the numerator and the denominator by the × same × number"
# in the highlighted box the whole topic is named after.
#
# An emphasis marker hugs its text (no space on the inside) and is free on the
# outside, which is exactly the opposite of a multiplication asterisk. That
# asymmetry is what separates them, and it is why "4 * 3" is untouched.
# Single asterisks only. A **double** pair is deliberate markup that
# apply_bold_markup turns into a real bold run later, and it has to survive
# this step intact.
_EMPHASIS_RE = re.compile(
    r"(?<![\w*])\*(?!\*)(?=\S)([^*\n]{1,80}?)(?<=\S)\*(?!\*)(?![\w])")


def _strip_emphasis(text: str) -> str:
    return _EMPHASIS_RE.sub(r"\1", text)


# "15 * 4", "2 * (7 + 4)", "5 * side". Emphasis asterisks are already gone by
# the time this runs, so the greedy \s* either side is safe.
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


# A step number with nothing after it: "1.", "2)", "3". The Final Challenge is
# the one place the model numbers its own working, and splitting on sentence
# ends then stranded every numeral on a line of its own, so an answer that read
# as three lines everywhere else read as nine here and looked like a rendering
# fault. Every other part of the key carries no numbers at all, one step per
# line, so these are dropped rather than rejoined: putting "1." back on the
# front of one line and nothing on the next numbers a third of the working.
_BARE_ENUMERATOR_RE = re.compile(r"^\d{1,2}\s*[.)]?$")


def solution_lines(working: str) -> list[str]:
    """The working, as the lines the answer key prints it on.

    One line per STEP, where a step is a clause and the arithmetic that
    finishes it. Splitting on every sentence boundary instead, which is what
    this used to do, turned a three-clause solution into six lines of four
    words:

        Compare thousands:
        all have 1.
        Compare hundreds:
        2, 5 and 8.
        Order:
        1,299 < 1,562 < 1,840.

    Half of that is a label with its value on the next line. Set down the page
    like that, over six pages, the key reads as debug output rather than as
    something written for the adult marking it, and the columns of four words
    are what made the key look machine-produced at a glance.

    So a clause is joined to the arithmetic it introduces, and a new line
    starts only once the current one has resolved into an "=" step. Working
    with no equals sign in it at all is prose, and prose is one wrapped
    paragraph. The model's own newlines are always honoured: when it lays a
    method out one operation per line, that is the method's shape.
    """
    lines: list[str] = []
    for raw in (working or "").splitlines():
        current = ""
        for part in _SOLUTION_SPLIT_RE.split(raw.strip()):
            part = _strip_step_prefix(part.strip())
            if not part or _BARE_ENUMERATOR_RE.match(part):
                continue
            if current and "=" in current:
                lines.append(current)
                current = part
            else:
                current = f"{current} {part}".strip()
        if current:
            lines.append(current)
    return lines


_EM_DASH = re.compile(r"\s*—\s*")
_EN_RANGE = re.compile(r"(?<=\d)\s*–\s*(?=\d)")
_EN_DASH = re.compile(r"\s*–\s*")


# A shipped Year 5 booklet asked "A athlete runs 3/20 of a kilometre...". The
# prompt can be told to get a/an right, and now is (_shared.py), but a prompt
# is never a guarantee: this is the deterministic backstop, on the same
# footing as _dedash below. "Correct in the prompt, verified on the page" is
# the pattern; this is the "verified on the page" half.
#
# Deliberately narrow. Getting a/an right in general needs the SOUND of the
# next word, not its spelling ("a university", "an hour"), which a fixed word
# list cannot cover completely. So this only fixes what it can prove: a fixed
# list of the exceptions that actually turn up in a maths/English tutoring
# vocabulary, applied on top of the reliable default (vowel letter -> "an").
# A next token that starts with a digit is left alone entirely: whether "8"
# reads as "an eight" or "180" reads as "a hundred and eighty" depends on the
# whole number, not the leading digit, and a wrong guess there would be a new
# bug, not a fix for this one.
_ARTICLE_RE = re.compile(r"\b([Aa]n?)\s+([A-Za-z][A-Za-z'-]*)")

# Vowel-letter word that is actually a consonant SOUND ("yoo", "w"), so "a"
# is correct despite the spelling.
_A_NOT_AN = frozenset({
    "university", "universities", "universal", "uniform", "uniforms",
    "unique", "unicorn", "unicorns", "union", "unions", "unit", "units",
    "united", "use", "used", "useful", "user", "users", "usual", "usually",
    "utensil", "utensils", "utility", "one", "once", "european",
    "europe", "euro", "euros", "eucalyptus", "ewe", "ewes",
})
# Consonant-letter word with a silent leading sound, so "an" is correct
# despite the spelling. Kept short and Australian-English safe (herb keeps
# its "h" sound here, unlike US English).
_AN_NOT_A = frozenset({
    "hour", "hours", "hourly", "honest", "honestly", "honesty",
    "honour", "honours", "honourable", "heir", "heirs",
})


def _correct_article(article: str, word: str) -> str:
    # A hyphenated word ("one-way", "university-level") is pronounced off its
    # first part, so that is what both the exception lookup and the default
    # vowel-letter rule have to test, not the string as a whole.
    head = word.lower().split("-", 1)[0]
    if head in _A_NOT_AN:
        wants_an = False
    elif head in _AN_NOT_A:
        wants_an = True
    else:
        wants_an = head[0] in "aeiou"
    an_form = "An" if article[0].isupper() else "an"
    a_form = "A" if article[0].isupper() else "a"
    return an_form if wants_an else a_form


def _fix_articles(text: str) -> str:
    """Correct a/an before the next word, deterministically. See note above."""
    def fix(m: "re.Match[str]") -> str:
        return _correct_article(m.group(1), m.group(2)) + " " + m.group(2)
    return _ARTICLE_RE.sub(fix, text)


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
        _strip_emphasis(_fix_articles(_dedash(text)))
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


# "Now let's try one together" used to print a second fully worked example, so
# the child read an answer instead of reaching one and the section taught by
# demonstration twice over. The guided example now arrives with the values the
# child is meant to work out wrapped in [[ ]], and the same string renders two
# ways: a ruled gap on the page they write on, the value itself in the answer
# key. One source of truth, so a blank and its answer cannot disagree.
# The [[value]] convention lives in blanks.py, because the LLM judge needs it
# too and cannot import this module. Aliased to the private names the rest of
# this file and the check scripts already use.
from .blanks import (BLANK_RE as _BLANK_RE, MAX_CHARS as _BLANK_MAX_CHARS,
                     MIN_CHARS as _BLANK_MIN_CHARS, PAD as _BLANK_PAD,
                     blank_out, fill_in, has_blanks, strip_markers)


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


# Paulio, the study-buddy mascot, narrates the worked examples in primary
# booklets and stays out of the secondary ones. A bear cub explaining
# calculus to a Year 10 sitting a Methods practice paper reads as
# patronising, and that is the booklet a tutor shows a paying parent. The
# split is at the end of primary school, which is also where the printed
# product changes character.
_PAULIO_MAX_YEAR = 6

_YEAR_DIGITS_RE = re.compile(r"\d+")

# Neutral first, so an unparseable year level gets the labels that are safe
# at every age rather than the ones that are only safe at some.
_WE_LABEL = "Watch first (worked example)"
_GE_LABEL = "Let's do this one together"
_WE_LABEL_PAULIO = "Paulio shows you first"
_GE_LABEL_PAULIO = "Now let's try one together"


# The icon that opens Paulio's worked examples. He narrates the lesson in the
# labels already ("Paulio shows you first"), but until now nothing in the
# printed booklet showed him: a parent reading the PDF cold has no way to know
# that name belongs to a character at all. This is his one appearance per
# taught box, reusing the same asset the website already ships rather than a
# second copy of the artwork living in two places.
_PAULIO_ICON_PATH = (
    Path(__file__).resolve().parent / "webapp" / "static" / "img" / "paulio"
    / "paulio-guide-right.png"
)
PAULIO_ICON_SIZE = 1.1 * cm


def paulio_teaches(year_level: str | None) -> bool:
    """Whether Paulio narrates the worked examples at this year level.

    Pre-primary and kindergarten carry no digit and are below the cut, so
    they are named rather than left to the digit parse. Anything else
    unrecognised falls through to False: the neutral labels read fine to a
    seven year old, while the Paulio ones do not read fine to a sixteen year
    old, so an unknown year should fail towards neutral.
    """
    text = (year_level or "").strip().lower()
    if not text:
        return False
    if any(w in text for w in ("pre-primary", "pre primary", "kindergarten",
                               "prep", "foundation")):
        return True
    m = _YEAR_DIGITS_RE.search(text)
    return bool(m) and int(m.group()) <= _PAULIO_MAX_YEAR


def _lesson_flowables(styles, t, year_level: str | None = None) -> list:
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
    paulio = paulio_teaches(year_level)
    out.append(_worked_example_flowable(
        styles, t.worked_example,
        _WE_LABEL_PAULIO if paulio else _WE_LABEL, paulio=paulio))
    for ge in t.guided_examples:
        out.append(Spacer(1, 0.2 * cm))
        out.append(_worked_example_flowable(
            styles, ge, _GE_LABEL_PAULIO if paulio else _GE_LABEL, paulio=paulio,
            guided=True))
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


def _worked_example_flowable(styles, we: WorkedExample, label: str = "Worked example",
                             paulio: bool = False, guided: bool = False,
                             reveal: bool = False, width: float | None = None):
    """Return a bordered box containing a worked example. `label` distinguishes
    the "I do" worked example from the "we do" guided ones. `paulio` puts his
    icon beside the label, for the same year levels he narrates in.

    `guided` marks the "we do" box, where the [[values]] are left as gaps for
    the child to fill. `reveal` prints those values instead, for the answer
    key. The "I do" example is never blanked: it is the one complete model of
    the method on the page, and the practice that follows starts from it.
    """
    # Worked examples are lesson content, so they get the same treatment: a
    # **term** the model marked up there becomes bold rather than printing its
    # asterisks, and a stray run of asterisks is dropped.
    instruction, specimen = split_instruction_and_specimen(we.question)
    # The box is normally the width of the page. In the answer key it is the
    # width of a key column, because the key is set in two.
    box_w = (width if width is not None else A4[0] - 2 * PAGE_MARGIN) - 0.4 * cm
    inner_w = box_w - 20
    label_para = Paragraph(label, styles["we_label"])
    icon = _make_image(str(_PAULIO_ICON_PATH), max_w=PAULIO_ICON_SIZE,
                       max_h=PAULIO_ICON_SIZE) if paulio else None
    if icon is not None:
        icon_col = PAULIO_ICON_SIZE + 0.15 * cm
        header = Table([[icon, label_para]],
                       colWidths=[icon_col, inner_w - icon_col])
        header.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        inner = [header]
    else:
        inner = [label_para]
    # The question states the task, so a [[ ]] the model put here would blank
    # out part of what is being asked. Strip the markers and keep the words.
    inner.append(Paragraph(
        _BLANK_RE.sub(r"\1", apply_bold_markup(_escape(instruction))),
        styles["we_question"]))
    if specimen:
        # Set apart, indented and quoted, so the task and the thing the task is
        # about are not one run-on paragraph.
        inner.append(Spacer(1, 0.2 * cm))
        # The specimen is the sentence the example works on, so a [[ ]] in it
        # is a gap the student fills when this box is guided, and just words
        # when it is the finished "I do" example above. It used to be neither:
        # the markers printed raw, because only the instruction was handled.
        spec_html = apply_bold_markup(_escape(specimen))
        if reveal:
            spec_html = fill_in(spec_html)
        elif guided:
            spec_html = blank_out(spec_html)
        else:
            spec_html = strip_markers(spec_html)
        inner.append(Paragraph(f'"{spec_html}"', styles["we_specimen"]))
        inner.append(Spacer(1, 0.2 * cm))
    img = _make_image(we.image_path, max_w=WE_IMG_WIDTH, max_h=WE_IMG_HEIGHT)
    if img is not None:
        inner.append(Spacer(1, 0.15 * cm))
        inner.append(img)
        inner.append(Spacer(1, 0.15 * cm))
    # A guided example the model returned with no [[ ]] at all would print as a
    # second demonstration, which is the thing this section stopped being. The
    # answer is blanked regardless, so the box always asks for something.
    gaps = guided and not reveal
    marked = guided and any(has_blanks(s) for s in [*we.steps, we.answer])
    if gaps and not marked:
        log.warning("guided example has no [[blanks]]; blanking its answer only")

    def render(text: str) -> str:
        escaped = apply_bold_markup(_escape(text))
        if reveal:
            return fill_in(escaped)
        return blank_out(escaped) if guided else _BLANK_RE.sub(r"\1", escaped)

    for i, step in enumerate(we.steps, 1):
        inner.append(Paragraph(f"<b>{i}.</b> {render(_strip_step_prefix(step))}",
                               styles["we_step"]))
    if gaps and not marked:
        answer_html = f"<u>{'&nbsp;' * _BLANK_MAX_CHARS}</u>"
    else:
        answer_html = render(we.answer)
    inner.append(Paragraph(f"Answer: {answer_html}", styles["we_answer"]))

    tbl = Table([[inner]], colWidths=[box_w])
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


def _key_part_heading(styles, text: str, hex_colour: str,
                      width: float | None = None):
    """A part divider inside the answer key: Warm-up, Class Work, Homework,
    Final Challenge.

    Reversed out of a short tab in the part's own colour, with a rule under it
    across the measure. It used to be coloured TEXT at 21pt over a topic
    heading at 19pt, both navy: two points apart and the same shape, so
    "Class Work" and "Number and Place Value" read as the same rank and the
    marker could not see where one part stopped. In the body those two ranks
    are a full colour band against a plain heading, and the key was
    contradicting the hierarchy the body had already taught.

    A tab rather than a full-width band. A reversed-out band four times over
    costs about five plain pages' worth of ink; a tab is the width of the
    words, which is a tenth of that, and it still reads as a different order of
    thing from a heading in text.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth
    width = width if width is not None else A4[0] - 2 * PAGE_MARGIN
    style = ParagraphStyle("key_part_" + text.replace(" ", ""),
                           parent=styles["key_part"], textColor=colors.white)
    label = _escape(text)
    tab_w = min(width, stringWidth(label, style.fontName, style.fontSize) + 18)
    tab = Table([[Paragraph(label, style)]], colWidths=[tab_w], hAlign="LEFT")
    tab.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(hex_colour)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    rule = Table([[""]], colWidths=[width], rowHeights=[2.5], hAlign="LEFT")
    rule.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(hex_colour)),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return KeepTogether([tab, rule, Spacer(1, 0.25 * cm)])


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

# Height of one ruled "Answer:" line.
_ANSWER_LINE_CM = 0.75

# How far a question's working area may be squeezed at the foot of a page,
# as a fraction of the room the same question gets anywhere else.
#
# This used to be a flat 0.9cm floor, which meant a question tagged "hard" got
# 3.2cm of room in the middle of a page and 0.9cm at the foot of one: the same
# question type, a third of the space, decided by where it happened to land. An
# inconsistent blank gap is forgivable because nobody can see what it was meant
# to be. An inconsistent PANEL is not: the grid stops early and the page looks
# misprinted. So the squeeze is capped at a quarter of the working area, and a
# question that still does not fit moves to the next page instead.
_WORKING_SHRINK_FLOOR = 0.75

BODY_HEIGHT = A4[1] - 2 * PAGE_MARGIN
BODY_WIDTH = A4[0] - 2 * PAGE_MARGIN

# A CondPageBreak taller than the frame it is in breaks the page and then finds
# the fresh page too short as well, so it can throw a sheet away for nothing.
# Nothing this file asks for is allowed past four fifths of the type area.
_MAX_COND_BREAK = 0.8 * BODY_HEIGHT


def stack_height(flowables, width: float = BODY_WIDTH) -> float:
    """How tall this run of flowables will actually be, asked of the flowables.

    Used to size the CondPageBreak in front of a mini-lesson. The height of a
    lesson is not a constant that can be guessed at: the worked-example box
    varies by several centimetres with the number of steps the model wrote and
    whether the subtopic carries a diagram, and a guessed constant is either
    too small to prevent the strand or so large it throws away a page under
    every heading. So each flowable is asked to wrap itself, exactly as the
    document will ask it during the build.

    A KeepTogether reports 0xffffff for its height rather than a real one, so
    anything that answers with a number that large is skipped rather than
    allowed to poison the total.
    """
    total = 0.0
    prev_after = 0.0
    for f in flowables:
        try:
            _, h = f.wrap(width, BODY_HEIGHT)
            before, after = f.getSpaceBefore(), f.getSpaceAfter()
        except Exception:
            continue
        if h > BODY_HEIGHT:
            continue
        # The frame collapses the gap between two flowables to the larger of
        # the pair rather than adding them. Adding them would overstate a
        # lesson by a couple of centimetres and throw away a page for it.
        total += max(prev_after, before) + h
        prev_after = after
    return total


def _lesson_opening(flowables: list) -> list:
    """The part of a mini-lesson that has to land on one page with its heading.

    Everything up to and including the worked-example box, which is the only
    Table a lesson contains. Past that point come the guided examples, and
    those are separate boxes that can perfectly well start on the next page.
    """
    for i, f in enumerate(flowables):
        if isinstance(f, Table):
            return flowables[:i + 1]
    return flowables


# What has to fit under a heading for the heading to be worth printing here.
# Enough for the first line or two of whatever follows: an answer and its
# working in the key, a question and the top of its working panel in the body.
# Not enough for the whole block, deliberately. A heading is a promise about
# what comes next, and one line of it kept is enough to keep the promise; a
# rule that demanded the whole block would move a heading to a fresh page every
# time a question happened to be tall.
_ORPHAN_MIN_CM = 2.5


def orphan_break(headings: list, width: float = BODY_WIDTH,
                 follow_cm: float = _ORPHAN_MIN_CM) -> CondPageBreak:
    """A break that stops this run of headings printing with nothing under it.

    Sized for the headings themselves, measured, plus room for the start of
    what they introduce. One break in front of a whole run rather than one per
    heading: two breaks in a row can strand the heading between them, when the
    first finds room for a topic heading and the second then moves the subtopic
    heading underneath it to the next page.

    `width` is the measure the headings will be set in, which is a key column
    rather than the page for anything in the answer key. Used inside a column
    frame, a CondPageBreak moves to the next column and only then to the next
    page, which is the behaviour wanted in both places.
    """
    return CondPageBreak(min(stack_height(headings, width) + follow_cm * cm,
                             _MAX_COND_BREAK))


def _lesson_cond_break(headings: list, lesson: list) -> list:
    """The break that stops a heading being stranded above its worked example.

    The worked-example box is one Table and cannot split, so when it does not
    fit it moves whole to the next page and leaves the topic heading, the
    subtopic heading, the intro paragraph and the key points sitting above four
    or five centimetres of white. Measured, not guessed: see stack_height.

    Measured as ONE stack rather than as two added together. The frame sets the
    gap between the last heading and the first line of the lesson to the larger
    of the heading's spaceAfter and the paragraph's spaceBefore, and measuring
    the two runs separately loses that junction entirely: 6pt, about 0.21cm,
    which is enough to leave a lesson believing it fits with 9.83cm left when
    it needs 9.89cm, and the worked-example box then moves to the next page on
    its own. Small enough to hide until something repaginates the booklet.
    """
    needed = min(stack_height(headings + _lesson_opening(lesson)),
                 _MAX_COND_BREAK)
    return [CondPageBreak(needed)]


# Room needed at the foot of a page for the Homework part to start there rather
# than on a fresh page: the band, a heading and a question or two. Raise this to
# BODY_HEIGHT / cm to go back to Homework always starting on its own page.
HOMEWORK_MIN_START_CM = 7.0

# The same, for the Final Challenge: its band, blurb and first question need
# room, or the band strands itself at the foot of a page.
_CHALLENGE_MIN_START_CM = 9.0


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


# ---------------------------------------------------------------------------
# The working panel
#
# A practice question used to print as a number, a line of text, a gap, and a
# hairline labelled "Answer:" at the bottom of it. Five of those to a page for
# eight pages. The gap read as an accident rather than as an allocation, which
# is the loudest thing in the booklet saying nobody designed this.
#
# The booklet already knew how to do better: an extended-response question gets
# real ruled lines (`written_response_rules`), and those questions look like a
# published workbook while the maths ones look like a leftover margin. So the
# panel extends that pattern to every question rather than inventing a second
# mechanism: maths gets the 5mm squared paper of a school exercise book,
# writing gets ruled lines at the same pitch as the extended-response rules,
# and the "Answer:" rule sits inside the panel's bottom edge.
#
# Feint on purpose. #E4E9F0 at 0.25pt is about a tenth of the weight of the
# answer rule, so it shows the child where the working goes without competing
# with the question text, and it survives a mono home printer: a light grey
# rules better in greyscale than a colour tint does, because it is already
# nothing but luminance.
_PANEL_INK = "#E4E9F0"
_PANEL_WEIGHT = 0.25
# Squared paper, at the pitch every Australian primary exercise book uses.
_PANEL_GRID_CM = 0.5
# Writing lines, at the pitch the extended-response rules already use, so a
# child writing three lines here and four lines there writes the same size.
_PANEL_RULE_CM = _ANSWER_LINE_CM

# Below this there is no working area left to rule, only the answer line, and a
# panel two squares tall reads as a rendering fault rather than as a design.
_PANEL_MIN_CM = 1.0

# Subjects whose working is arithmetic laid out down and across the page rather
# than sentences along a line. Matched as substrings, so "Mathematics Methods"
# and NAPLAN's combined "Numeracy and Literacy" both land on squared paper: a
# grid can be written on, whereas ruled lines at 7.5mm are no use for a column
# subtraction, so a mixed booklet fails towards the grid.
_GRID_SUBJECTS = ("math", "numeracy", "quantitative", "reasoning")


def working_panel(subject: str | None, question) -> str:
    """Which working panel goes under this question: grid, rules or none.

    "none" is for a question that asks for a drawing: ruling a space someone
    has to sketch in is worse than leaving it blank, which is the same reason
    `written_response_rules` returns 0 for them.
    """
    text = getattr(question, "question", "") or ""
    if _DRAWN_RESPONSE_RE.search(text):
        return "none"
    # A question that already earns full ruled lines has its panel: they are
    # drawn across the whole allocation and there is no "Answer:" rule under
    # them. This is the pattern the rest of this is extending.
    if written_response_rules(question):
        return "rules"
    s = (subject or "").strip().lower()
    return "grid" if any(k in s for k in _GRID_SUBJECTS) else "rules"


class WorkingSpace(Flowable):
    """The working panel under a question, with its ruled answer line(s).

    Two jobs, and they are the same job. It draws the panel and the
    "Answer: _____" rule the booklet had nowhere for, and it is the piece that
    gives when a page runs out: it reports its full height when there is room
    and shrinks toward `min_height` when there is not.

    Why it has to shrink: the question used to be a KeepTogether wrapping a
    fixed Spacer, so a question whose blank space did not fit was moved whole to
    the next page and the current page was abandoned, two thirds empty, in one
    real booklet. KeepTogether measures its contents with an unbounded height
    (0xfffffff), so reporting the *minimum* during measurement makes the
    keep-together decision on the small size, and the real height is then
    negotiated against the space actually left on the page.
    """

    def __init__(self, height: float, labels=(), min_height: float | None = None,
                 rules: int = 0, panel: str = "none"):
        super().__init__()
        self.labels = list(labels)
        # Unlabelled ruled lines for a prose answer. Unlike the working space
        # around them these do not shrink: a question that asks for two
        # sentences has to still offer two sentences of ruling at the foot of a
        # page, or the child meets the same question with half the room.
        self.rules = max(0, int(rules))
        self.panel = panel
        self.answers_height = _ANSWER_LINE_CM * cm * len(self.labels)
        self.height = height + self.answers_height
        # Only the working area gives; the answer rule under it is a fixed
        # strip and cannot be shrunk into.
        floor = self.answers_height + _WORKING_SHRINK_FLOOR * height
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

    def _draw_panel(self, c):
        """The feint working area: squared paper for maths, lines for writing.

        Drawn from the top of the answer strip upwards, so the panel's bottom
        edge is the answer rule and the child can see the whole allocation as
        one block rather than as a gap that happens to end in a hairline.
        """
        top = self._h
        band = self.answers_height
        if top - band < _PANEL_MIN_CM * cm:
            return
        c.saveState()
        c.setStrokeColor(colors.HexColor(_PANEL_INK))
        c.setLineWidth(_PANEL_WEIGHT)
        # The outline is what turns the ruling into an allocation: it says the
        # room stops here, which is the thing a bare gap could never say.
        c.rect(0, 0, self.width, top, stroke=1, fill=0)
        if self.panel == "grid":
            pitch = _PANEL_GRID_CM * cm
            y = band + pitch
            while y < top - 1:
                c.line(0, y, self.width, y)
                y += pitch
            x = pitch
            while x < self.width - 1:
                c.line(x, band, x, top)
                x += pitch
        else:
            pitch = _PANEL_RULE_CM * cm
            y = top - pitch
            while y > band + 1:
                c.line(0, y, self.width, y)
                y -= pitch
        c.restoreState()

    def draw(self):
        if not self.labels and not self.rules:
            return
        c = self.canv
        if self.labels and self.panel in ("grid", "rules"):
            self._draw_panel(c)
        c.saveState()
        c.setFont(FONT_REGULAR, 9.5)
        c.setFillColor(colors.HexColor("#333333"))
        c.setStrokeColor(colors.HexColor("#9AA6B8"))
        c.setLineWidth(0.6)
        line_h = _ANSWER_LINE_CM * cm
        # Inset from the panel's edges, so the label and its rule sit inside
        # the working area rather than crossing its outline.
        pad = 5 if self.panel in ("grid", "rules") else 0
        for i, label in enumerate(reversed(self.labels)):
            # Offset so the rule does not sit tight against the next question.
            y = i * line_h + 0.42 * cm
            c.drawString(pad, y, label)
            x0 = pad + c.stringWidth(label, FONT_REGULAR, 9.5) + 6
            c.line(x0, y - 2.5, self.width - pad, y - 2.5)
        # Full-width rules for a written answer, drawn from the top of the
        # block down, so the child starts writing where the question ends
        # rather than at the bottom of a gap.
        for i in range(self.rules):
            y = self._h - 0.42 * cm - i * line_h
            c.line(0, y, self.width, y)
        c.restoreState()


# The page foot left short on purpose
#
# HOMEWORK_MIN_START_CM and _CHALLENGE_MIN_START_CM hand back up to seven and
# nine centimetres at a page foot, because a part that begins three lines before
# a page turn is worse than one that begins on a fresh page. That room is
# currently left as white paper.
#
# A self-assessment strip once filled it ("How did that go? Got it / Nearly /
# Go over this again"). It was removed at the founder's direction: he did not
# want it on the page, and a device nobody asked for is not worth the two lines
# it adds to every part boundary. Do not reinstate it. If the foot space is to
# be closed, it should be closed by the page packing tighter rather than by an
# element invented to sit in the hole.
# ---------------------------------------------------------------------------


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
                        display_num: int | None = None,
                        subject: str | None = None,
                        working_cm: float | None = None,
                        fixed_working: bool = False) -> list:
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
    # A cloze question carries its missing word as [[word]], so the sentence
    # and the word it is missing come from one string and cannot disagree. The
    # gap is drawn here and the word itself only ever appears in the key.
    # `blank_out` leaves text without markers alone, so every other subject and
    # question type is unaffected.
    block.append(
        Paragraph(
            f"<b>{shown}.</b> {blank_out(_escape(instruction))}",
            styles["question"],
        ),
    )
    if specimen:
        block.append(Paragraph(f'"{blank_out(_escape(specimen))}"',
                               styles["question_specimen"]))
    img = _make_image(vq.image_path)
    if img is not None:
        block.append(Spacer(1, 0.3 * cm))
        block.append(img)
        # No credit line under the picture. A licence string under every image
        # reads as clutter on a child's worksheet; the attributions are still
        # printed, gathered on a references page at the very back.
    # `working_cm` is set only by the two-up grid, where both cells of a row
    # are given the taller of the pair's entitlements so the row's two panels
    # end on the same line. `fixed_working` stops the panel shrinking inside a
    # table cell: a row cannot split anyway, so there is nothing to gain by
    # giving, and a cell measured with an unbounded height would otherwise
    # quote its squeezed minimum and print the whole grid a quarter short.
    height = (_working_space_cm(vq.question, space_floor_cm)
              if working_cm is None else working_cm) * cm
    space = WorkingSpace(
        height,
        answer_line_labels(vq.question),
        min_height=height + _ANSWER_LINE_CM * cm * len(
            answer_line_labels(vq.question)) if fixed_working else None,
        rules=written_response_rules(vq.question),
        panel=working_panel(subject, vq.question),
    )
    block.append(space)
    return block


# ---------------------------------------------------------------------------
# The two-up practice grid
#
# A booklet of short arithmetic used to print one question per row for twelve
# consecutive pages: bold number, one line of text, a panel, an answer rule,
# four or five to a page, nothing changing size, weight, width or position from
# one page to the next. A parent flipping the printed stack has no landmarks in
# it, and the pages are half empty sideways while the booklet runs long.
#
# So a question short enough to be set at half measure is set at half measure,
# two to a row, each with its own panel. Which questions qualify is decided by
# the booklet's own content rather than by a fixed pattern, so the rhythm
# differs from booklet to booklet by itself: a page that runs a two-up row, then
# a full-width word problem, then another two-up row reads as laid out rather
# than as stamped.
_TWO_UP_GAP = 0.6 * cm
TWO_UP_COLUMN = (BODY_WIDTH - _TWO_UP_GAP) / 2

# The tallest working area a question may claim and still be set two-up. The
# `easy` tier is 1.6cm and the Warm-up floor is 2.2cm; anything above that is a
# question that wants room to think in, and room to think in wants the measure.
_TWO_UP_MAX_WORKING_CM = 2.2


def two_up_eligible(styles, vq: ValidatedQuestion, display_num: int,
                    subject: str | None = None, space_floor_cm: float = 0.0,
                    width: float | None = None) -> bool:
    """Whether this question can be set at half measure beside another.

    Two conditions, and both are about what the question needs rather than
    about what would fit: its text has to sit on ONE line in the narrow column,
    and its working area has to be small. A question that wraps to two lines at
    half measure has been squeezed rather than laid out, and a word problem or
    anything wanting real working stays across the page.

    Everything that carries something other than a line of text and a panel is
    out: a picture, a reading passage, ruled lines for a written answer, a
    drawing space, or parts a) and b) with a rule each.
    """
    width = TWO_UP_COLUMN if width is None else width
    q = getattr(vq, "question", None)
    if q is None:
        return False
    if image_is_usable(getattr(vq, "image_path", None)):
        return False
    if getattr(q, "passage_id", None):
        return False
    if written_response_rules(q):
        return False
    if working_panel(subject, q) == "none":
        return False
    # Exactly one "Answer:" rule. Parts a) and b) get one each, and they also
    # earn extra height, so they belong across the measure.
    if len(answer_line_labels(q)) != 1:
        return False
    if _working_space_cm(q, space_floor_cm) > _TWO_UP_MAX_WORKING_CM + 1e-6:
        return False
    instruction, specimen = split_instruction_and_specimen(q.question)
    if specimen:
        return False
    style = styles["question"]
    para = Paragraph(f"<b>{display_num}.</b> {blank_out(_escape(instruction))}",
                     style)
    try:
        _, h = para.wrap(width, BODY_HEIGHT)
    except Exception:
        return False
    return h <= style.leading + 0.5


def two_up_rows(eligible: list[bool], breaks=()) -> list[list[int]]:
    """Group question positions into printed rows, in reading order.

    Rows of two where two adjacent questions both qualify, rows of one
    otherwise. Reading order is left cell then right cell then the next row
    down, which is the order the numbering, the page map and the answer key all
    already walk, so none of them has to change.

    `breaks` are positions that must start a row of their own: a homework
    question that a session band is printed above cannot be the right-hand half
    of a row begun before the band.
    """
    rows: list[list[int]] = []
    i = 0
    while i < len(eligible):
        if (eligible[i] and i + 1 < len(eligible) and eligible[i + 1]
                and (i + 1) not in set(breaks)):
            rows.append([i, i + 1])
            i += 2
        else:
            rows.append([i])
            i += 1
    return rows


def _two_up_block(styles, cells: list, page_map: dict | None = None):
    """One row of two short questions, each with its own working panel.

    The two panels are given the same height, the taller of the pair's
    entitlements, and the cells are top aligned, so the row ends on one line
    rather than stepping down in the middle.

    The panel's grid is NOT scaled to the column. A 5mm square is 5mm wherever
    it sits, because that is the whole point of an exercise-book grid: a child
    counting squares to line up a column addition has to be counting the same
    square they counted on the page before. A narrow column simply holds fewer
    of them. WorkingSpace draws from an absolute pitch, so this comes for free
    and the check pins it there.
    """
    height = max(_working_space_cm(vq.question, floor)
                 for _, _, vq, _, floor in cells)
    built = [_question_flowables(styles, n, vq, page_map, floor, display,
                                 subject, working_cm=height, fixed_working=True)
             for n, display, vq, subject, floor in cells]
    row = [built[0], "", built[1] if len(built) > 1 else ""]
    tbl = Table([row], colWidths=[TWO_UP_COLUMN, _TWO_UP_GAP, TWO_UP_COLUMN],
                hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tbl


def _question_block(styles, q_num: int, vq: ValidatedQuestion,
                    page_map: dict | None = None, space_floor_cm: float = 0.0,
                    display_num: int | None = None,
                    subject: str | None = None):
    """One numbered question and its working space, kept on one page."""
    return KeepTogether(_question_flowables(styles, q_num, vq, page_map,
                                            space_floor_cm, display_num,
                                            subject))


def _passage_question_block(styles, passage, q_flowables: list):
    """A reading and the first question about it, bound onto one page."""
    return KeepTogether([_passage_flowable(styles, passage),
                         Spacer(1, 0.35 * cm)] + q_flowables)


ASSET_DIR = Path(__file__).resolve().parent / "assets"


def cover_background_path() -> str | None:
    """An optional full-bleed image that replaces the drawn cover entirely.

    The cover is now drawn on the canvas (booklet_gen/visuals/cover.py) so it
    can carry the subject, year, topic and week. This is the escape hatch and
    nothing more: it is off unless FOLIO_COVER_BACKGROUND points at a file that
    exists. Dropping a cover_background.png into assets/ no longer changes
    anything, deliberately, because the old file was still sitting there and
    would otherwise have silently overridden the new design on every install.
    """
    env = os.environ.get("FOLIO_COVER_BACKGROUND")
    if env and Path(env).exists():
        return env
    return None


# Booklet-type wording for the pill on the cover. The design system allows
# "Practice Booklet", "Practice Exam", "Revision Booklet", "Weekly Practice"
# and "Assessment Preparation"; a booklet only ever earns one of them from what
# it actually contains.
def cover_pill(data: BookletData) -> str:
    focus = (data.week_focus or "").lower()
    if data.week_number and data.total_weeks:
        if "revis" in focus:
            return "Revision Booklet"
        return "Weekly Practice"
    return "Practice Booklet"


def cover_topic(data: BookletData) -> str:
    """The topic line: the topics this booklet actually teaches.

    Two at most. A Year 5 booklet with four subtopics across two topics reads
    as "Fractions and Volume"; listing all four ran off the trim edge.
    """
    seen = []
    for s in data.sections:
        t = (s.topic or "").strip()
        if t and t not in seen:
            seen.append(t)
    if not seen:
        return ""
    if len(seen) == 1:
        return seen[0]
    if len(seen) == 2:
        return f"{seen[0]} and {seen[1]}"
    return f"{seen[0]}, {seen[1]} and more"


def cover_spec(data: BookletData, times: dict | None = None) -> CoverSpec:
    """Everything the cover renderer needs, as finished strings.

    Deterministic: render_pdf builds the document twice and both builds have to
    lay out identically.
    """
    times = times or {}
    subject = (data.subject or "").strip()
    program = (data.program_label or "").strip()
    # "Year 6" over "Mathematics", the way both reference covers read. The
    # product line is the small line above the pill, where it behaves like a
    # series name rather than displacing what the child is actually studying.
    title_lines = [t for t in (data.year_level, subject) if t]

    week = ""
    if data.week_number and data.total_weeks:
        week = f"{data.week_number} of {data.total_weeks}"
        if data.week_focus:
            week += f"  |  {data.week_focus}"

    meta = [date.today().strftime("%d %B %Y")]
    if times.get("total_minutes"):
        meta.append(f"Estimated time: about {times['total_minutes']} minutes. "
                    "Take breaks whenever you need to.")

    return CoverSpec(
        title_lines=title_lines,
        pill=cover_pill(data),
        eyebrow=program,
        subject=subject,
        topic=cover_topic(data),
        student_name=(data.student_name or "").strip(),
        week=week,
        # DIFFICULTY is in the design system's field list but BookletData has
        # no source for it, so it prints only if one is ever added.
        difficulty=str(getattr(data, "difficulty", "") or ""),
        meta_lines=meta,
        footer_note=cover_footer_note(data),
        variant=variant_for(subject, program, data.year_level or "",
                            cover_topic(data)),
        font_regular=FONT_REGULAR, font_bold=FONT_BOLD,
        background_image=cover_background_path() or "",
    )


def cover_footer_note(data: BookletData) -> str:
    """The one sentence on the cover that has to be true of the key behind it.

    Two claims that were not true of the booklet they were printed on.

    "show your working" went on every cover, including English booklets, where
    there is no working to show.

    "symbolically verified" went on every all-maths cover regardless of what
    actually ran. Only questions SymPy can decide are proved symbolically;
    everything else, which is most of a primary booklet ("Round 468 to the
    nearest hundred", "Explain his mistake"), is checked by the LLM judge, the
    same one English uses. Claiming an algebra engine stood behind "explain his
    mistake" is the kind of thing a sceptical tutor screenshots, and it
    devalues the mark on the answers where it is earned. Until the mark
    distinguishes the two, the cover claims only what is true of all of them.

    The claim has to match the key it points at. A real booklet said "every
    answer has been checked for accuracy" on page 1 and then printed ten
    answers out of ninety-nine with no tick beside them, which tells a parent
    in the product's own notation that the cover is false. Being told that is
    worse than never claiming it: they do not have to find a wrong answer to
    want their money back.
    """
    section_subjects = {(s.subject or data.subject).strip().lower()
                        for s in data.sections}
    only_maths = section_subjects == {"mathematics"}
    every_answer_checked = all(vq.verified for vq in all_questions(data))
    return (
        "Work through it in order"
        + (" and show your working." if only_maths else ".")
        + (" Every answer in the key at the back has been checked."
           if every_answer_checked else
           " In the key at the back, a tick marks an answer that has been"
           " checked."))


def _draw_page_chrome(canvas, doc):
    if doc.page == 1:
        # Declares the document's language to a screen reader and to any
        # procurement checklist that looks. ReportLab has no doc-level setter
        # for it, so it goes on the catalog from the first page.
        canvas.setCatalogEntry("Lang", "en-AU")
    canvas.saveState()
    # Page 1 is the cover, drawn edge to edge on the canvas by
    # booklet_gen/visuals/cover.py, with no running header or footer over it.
    # The story contributes nothing to page 1 but the page break, so the whole
    # front page is one composition rather than text floating on a picture.
    if doc.page == 1 and getattr(doc, "_cover", None) is not None:
        try:
            render_cover(canvas, doc._cover)
        except Exception as e:
            # A cover that fails to draw must not cost the customer the
            # booklet: fall back to a blank front page.
            log.warning("formatter.cover_failed", extra={"reason": str(e)[:300]})
        canvas.restoreState()
        return
    # Exam papers use a plain cover with no background; still keep the running
    # header off it, the way a real examination front page looks.
    if doc.page == 1 and getattr(doc, "_plain_cover", False):
        canvas.restoreState()
        return
    canvas.setFont(FONT_REGULAR, 9)
    canvas.setFillColor(colors.HexColor("#5F5F5F"))
    canvas.drawRightString(
        A4[0] - PAGE_MARGIN, CHROME_MARGIN, f"Page {doc.page}",
    )
    header = getattr(doc, "_header_text", "")
    if header:
        canvas.drawString(PAGE_MARGIN, A4[1] - CHROME_MARGIN, header)
        canvas.setStrokeColor(colors.HexColor("#DDDDDD"))
        canvas.line(PAGE_MARGIN, A4[1] - CHROME_MARGIN - 0.15 * cm,
                    A4[0] - PAGE_MARGIN, A4[1] - CHROME_MARGIN - 0.15 * cm)
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
    from .agents.tables import test_questions
    tables = test_questions(getattr(data, "tables_test", None))
    if tables:
        rows.append(("Times Tables Test", len(tables)))
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
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(PART_HOMEWORK)),
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


# ---------------------------------------------------------------------------
# Times tables. Same weekly shape as spelling and rendered with the same grid
# metrics, but the cell carries the question rather than being left blank: a
# spelling test is dictated aloud, so its page must hold no clues, while a
# tables test is read off the page and every fact has to be printed.
# ---------------------------------------------------------------------------

TABLES_COLUMNS = 2
_TABLES_ROW_CM = 1.15
_TABLES_BAND = "#7A4E8F"


def _tables_grid(styles, items: list, ruled: bool):
    """A numbered grid of `table x multiplier`, filled down each column.

    With `ruled` set, each row ends in a rule for the product, which is what
    makes it a test rather than a list to read.
    """
    count = len(items)
    columns = TABLES_COLUMNS if count > 6 else 1
    rows = (count + columns - 1) // columns
    body_w = A4[0] - 2 * PAGE_MARGIN
    num_w = 0.95 * cm
    q_w = 2.5 * cm
    rest_w = body_w / columns - num_w - q_w
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
                table, mult, product = items[i]
                row.append(Paragraph(f"{i + 1}.", styles["spelling_num"]))
                row.append(Paragraph(f"{table} &times; {mult} =",
                                     styles["spelling_word"]))
                row.append(Paragraph("" if ruled else f"<b>{product}</b>",
                                     styles["spelling_word"]))
                if ruled:
                    style.append(("LINEBELOW", (3 * c + 2, r), (3 * c + 2, r),
                                  0.6, colors.HexColor("#9AA6B8")))
            else:
                row.extend(["", "", ""])
        data.append(row)
    tbl = Table(data, colWidths=[num_w, q_w, rest_w] * columns,
                rowHeights=[_TABLES_ROW_CM * cm] * rows)
    tbl.setStyle(TableStyle(style))
    return tbl


def _tables_test_block(styles, test, minutes: int | None = None):
    """The recall drill on last week's table, at the front of the booklet."""
    from .agents.tables import test_questions
    items = test_questions(test)
    if not items:
        return []
    table = items[0][0]
    from_week = getattr(test, "from_week", None)
    source = (f"the {table} times table set in week {from_week}"
              if from_week else f"the {table} times table set last week")
    band = _part_band(
        styles, "Times Tables Test", _TABLES_BAND,
        f"All 12 facts from {source}, in mixed order. Write the answer on "
        "each line. No working, no counting up: these are meant to be known."
        + (f" About {minutes} min." if minutes else ""))
    grid = _tables_grid(styles, items, ruled=True)
    return [KeepTogether([band, Spacer(1, 0.45 * cm), grid]),
            Spacer(1, 0.5 * cm)]


def _tables_list_block(styles, tables_list):
    """The table to memorise before the next booklet, printed in full."""
    from .agents.tables import facts
    table = getattr(tables_list, "table", 0) or 0
    if not table:
        return []
    band = _part_band(
        styles, "Times Table to Learn", _TABLES_BAND,
        f"The {table} times table. Learn it before your next booklet: you "
        "will be tested on all 12 facts, in mixed order.")
    grid = _tables_grid(styles, facts(table), ruled=False)
    return [KeepTogether([band, Spacer(1, 0.45 * cm), grid])]


def _tables_key_block(styles, test):
    """The marker's copy of the shuffled test, in the order it was asked."""
    from .agents.tables import test_questions
    items = test_questions(test)
    if not items:
        return []
    table = items[0][0]
    from_week = getattr(test, "from_week", None)
    source = (f"set in week {from_week}" if from_week else "set last week")
    answers = ", ".join(f"{i}. {product}"
                        for i, (_, _, product) in enumerate(items, 1))
    return [KeepTogether([
        Paragraph("Times Tables Test", styles["key_topic"]),
        Paragraph(f"The {table} times table, {source}. Answers in the order "
                  "the questions are printed on the test page.",
                  styles["challenge_blurb"]),
        Spacer(1, 0.2 * cm),
        Paragraph(_escape(answers), styles["spelling_word"]),
        Spacer(1, 0.4 * cm),
    ])]


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
        Paragraph("Spelling Test", styles["key_topic"]),
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

    # Cover. Every mark on page 1 is drawn on the canvas by
    # _draw_page_chrome -> booklet_gen/visuals/cover.py, so the story
    # contributes nothing to it but the break that ends it. The copy the cover
    # prints, including the sentence about what the answer key has and has not
    # checked, is assembled by cover_spec()/cover_footer_note() above.
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

    def plan(qs, subject, floor, start_n, breaks=()):
        """A run of questions turned into printed rows.

        Returns the cells, one per question in reading order, and the rows: a
        row is one position or two, and two only when both questions are short
        enough to be set at half measure. Numbering is assigned here, before
        anything is laid out, so a two-up row cannot renumber anything: the
        left cell is always the lower number and the right cell the next one.
        """
        cells = [(start_n + k + 1, shown(start_n + k + 1), vq, subject, floor)
                 for k, vq in enumerate(qs)]
        return cells, two_up_rows(
            [two_up_eligible(styles, vq, display, subj, fl)
             for _, display, vq, subj, fl in cells], breaks)

    def question_row(cells, row):
        """One printed row: two questions side by side, or one full measure."""
        if len(row) == 2:
            return _two_up_block(styles, [cells[i] for i in row], page_map)
        n, display, vq, subject, floor = cells[row[0]]
        return _question_block(styles, n, vq, page_map, floor, display, subject)

    def render_questions(qs, space_floor_cm: float = 0.0):
        # The Warm-up and the Final Challenge belong to the booklet rather than
        # to a subtopic, so the booklet's own subject decides their panel.
        cells, rows = plan(qs, data.subject, space_floor_cm, counter["n"])
        for i, row in enumerate(rows):
            if i:
                story.append(Spacer(1, Q_GAP))
            story.append(question_row(cells, row))
        counter["n"] += len(qs)

    def render_passage_questions(section, qs, space_floor_cm: float = 0.0):
        """Questions grouped under their reading, passage first.

        The passage and the first question that refers to it are bound into one
        KeepTogether, so "Referring to the passage above" can never point at a
        passage on the following page.
        """
        subject = section.subject or data.subject
        first = True
        for passage, group in passage_groups(qs, section_passages(section)):
            cells, _ = plan(group, subject, space_floor_cm, counter["n"])
            counter["n"] += len(group)
            head = 1 if passage is not None else 0
            if head:
                n, display, vq, subj, floor = cells[0]
                # Built from loose flowables, never by wrapping the finished
                # block: a KeepTogether inside a KeepTogether measures as
                # 0xffffff and breaks the page every time.
                story.append(_passage_question_block(
                    styles, passage,
                    _question_flowables(styles, n, vq, page_map, floor,
                                        display, subj)))
                first = False
            rest = cells[head:]
            rows = two_up_rows([two_up_eligible(styles, vq, display, subj, fl)
                                for _, display, vq, subj, fl in rest])
            for row in rows:
                if not first:
                    story.append(Spacer(1, Q_GAP))
                first = False
                story.append(question_row(rest, row))

    def subject_topic_headers(section, state, key: bool = False,
                              out: list | None = None):
        """The subject band and topic heading, when either has just changed.

        `key` picks the answer key's smaller pair: the key is set in two
        columns, and the body's 19pt topic wraps to three lines in an 8cm
        measure. `out` lets the key collect its headings somewhere other than
        the story, so they can be held back until there is something to print
        under them.
        """
        dest = story if out is None else out
        if multi_subject and section.subject and section.subject != state["subject"]:
            dest.append(Paragraph(_escape(section.subject), styles["subject_band"]))
            state["subject"] = section.subject
            state["topic"] = None
        if section.topic != state["topic"]:
            dest.append(Paragraph(_escape(section.topic),
                                  styles["key_topic" if key else "topic"]))
            state["topic"] = section.topic

    # ---- Spelling Test (dictation on last week's list, before anything else) ----
    story.extend(_spelling_test_block(styles, getattr(data, "spelling_test", None),
                                      times.get("spelling_minutes")))

    # ---- Times Tables Test (recall drill on last week's table) ----
    # Also at the front, and before the recap: it is the one part of the
    # booklet that has to be answered cold, so anything the student reads
    # first is a chance to warm up on facts they were supposed to have known.
    story.extend(_tables_test_block(styles, getattr(data, "tables_test", None),
                                    times.get("tables_minutes")))

    # ---- Warm-up Recap ----
    if data.recap_questions:
        sub = f"Quick revision to warm up. About {times['recap_minutes']} min." \
            if times["recap_minutes"] else "Quick revision to warm up."
        band = _part_band(styles, "Warm-up Recap", PART_RECAP, sub)
        story.append(orphan_break([band]))
        story.append(band)
        story.append(Spacer(1, 0.3 * cm))
        render_questions(data.recap_questions, _RECAP_MIN_SPACE_CM)

    # ---- Class Work (lesson + guided + now-you-try) ----
    cw_sub = f"Do this in your lesson. About {times['classwork_minutes']} min." \
        if times["classwork_minutes"] else "Do this in your lesson."
    band = _part_band(styles, "Class Work", PART_CLASSWORK, cw_sub)
    story.append(orphan_break([band]))
    story.append(band)
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
        # Built before anything is appended, so the run can be measured and a
        # break asked for in front of it. Otherwise the worked-example box, one
        # unsplittable Table, moves to the next page on its own and leaves the
        # headings, the intro and the key points above five centimetres of
        # white.
        mark = len(story)
        subject_topic_headers(section, state)
        time_badge = (
            f'  <font size=9 color="#146B2C">'
            f'(about {times["section_minutes"][si]} min)</font>'
        )
        story.append(Paragraph(_escape(section.subtopic) + time_badge, styles["subtopic"]))

        t = section.teaching
        if t is not None:
            lesson = _lesson_flowables(styles, t, data.year_level)
            story[mark:mark] = _lesson_cond_break(story[mark:], lesson)
            story.extend(lesson)
            if section.questions:
                # Only when something follows it. A subtopic the hour could not
                # fit keeps its lesson but has had its practice moved to
                # Homework, and "Now you try:" over an empty space, immediately
                # under the next heading, is what that used to print.
                story.append(Spacer(1, 0.35 * cm))
                label = Paragraph("Now you try:", styles["practice_label"])
                # "Now you try:" as the last thing on a page, with question 1
                # overleaf, is an instruction pointing at blank paper.
                story.append(orphan_break([label]))
                story.append(label)
        else:
            # No lesson to measure, so nothing has asked for room under these
            # headings yet. A subtopic name at the foot of a page with its
            # first question on the next is the same defect in miniature.
            story.insert(mark, orphan_break(story[mark:]))

        render_passage_questions(section, section.questions)

    # ---- Homework (repetition through the week) + Final Challenge ----
    has_homework = any(s.homework_questions for s in data.sections)
    if has_homework or data.challenge_questions:
        # Not an unconditional break. Class Work used to end wherever it ended
        # and throw the rest of the page away: one real booklet finished the
        # section two questions into a page and left the other two thirds
        # blank. The coloured band is divider enough, so only break when there
        # is too little room left to be worth starting Homework here.
        # Homework will not begin with less than HOMEWORK_MIN_START_CM left,
        # so up to seven centimetres of the page Class Work finished on is
        # given up. The strip fills it when there is a gap and draws nothing
        # when there is not.
        story.append(CondPageBreak(HOMEWORK_MIN_START_CM * cm))
        sessions = homework_session_plan(data)
        # The number on this band has to be the number the page underneath it
        # adds up to. It used to be the whole homework half, Final Challenge and
        # all, printed over four sittings of 31, 31, 31 and 29 min: a parent who
        # planned around "179 min in total" was 57 minutes out. The sitting
        # bands are what a parent counts, so the total is their sum, and the
        # Final Challenge keeps its own estimate on its own band below.
        hw_sub = ("Do these through the week to lock it in. "
                  f"About {times['homework_only_minutes']} min.") \
            if times["homework_only_minutes"] \
            else "Do these through the week to lock it in."
        if sessions:
            hw_sub = ("Do these through the week to lock it in. "
                      f"Split into {len(sessions)} sessions, about "
                      f"{sum(s['minutes'] for s in sessions)} min in total.")
        if data.challenge_questions and times["challenge_minutes"]:
            hw_sub += (" The Final Challenge at the end adds about "
                       f"{times['challenge_minutes']} min.")
        story.append(_part_band(styles, "Homework", PART_HOMEWORK, hw_sub))
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
            head_only = story[mark:]
            # A subtopic that did not fit the session brings its mini-lesson
            # down here, so the teaching is not lost: nothing else in the
            # booklet explains the skill its homework asks for. Subtopics that
            # were taught in the session do not repeat their lesson.
            lesson = []
            if not section.questions and section.teaching is not None:
                lesson = _lesson_flowables(styles, section.teaching,
                                           data.year_level)
                story.extend(lesson)
            headings = story[mark:]
            del story[mark:]
            # How much has to be left on the page for these headings to be
            # worth printing here: the headings themselves, and either the
            # worked-example box they introduce or the first line or two of the
            # first question. The break itself is placed further down, once it
            # is known whether a session band goes above them, because two
            # breaks in a row would strand whatever sits between them.
            # One stack, not two added: measuring them separately drops the
            # gap the frame puts between the last heading and the first line
            # under it, which is enough to strand a worked-example box.
            headings_need = (stack_height(head_only + _lesson_opening(lesson))
                             if lesson
                             else stack_height(head_only) + _ORPHAN_MIN_CM * cm)

            # Flattened into printed order first, so the two-up decision can
            # look at the question after this one. A question that a session
            # band is printed above has to start its own row, or the band would
            # land between the two halves of a row already begun.
            ordered = [(passage, i, vq)
                       for passage, group in passage_groups(
                           section.homework_questions,
                           section_passages(section))
                       for i, vq in enumerate(group)]
            subject = section.subject or data.subject
            cells, _ = plan([vq for _, _, vq in ordered], subject, 0.0,
                            counter["n"])
            counter["n"] += len(ordered)
            eligible = [
                # The first question under a reading is bound to that reading
                # and printed across the measure with it.
                not (p is not None and i == 0)
                and two_up_eligible(styles, vq, display, subj, fl)
                for (p, i, _), (_, display, vq, subj, fl) in zip(ordered, cells)]
            rows = two_up_rows(eligible,
                               [k for k in range(len(ordered))
                                if (flat + k) in starts])

            j = 0
            for row in rows:
                passage, i, vq = ordered[row[0]]
                if j:
                    story.append(Spacer(1, Q_GAP))
                band = session_band_for(flat)
                if band is not None:
                    # The session starts part way through this subtopic, so
                    # the heading was printed pages back under an earlier
                    # session. Without this the child sits down on
                    # Wednesday to "Session 2 of 2" followed by "2. Write
                    # 0.305 in words", with no sign of what the work is
                    # about or where question one went.
                    cont = Paragraph(
                        _escape(f"{section.subtopic} (continued)"),
                        styles["subtopic"]) if j else None
                    if flat:
                        # Do not leave a session band stranded at the foot
                        # of a page with its first question overleaf, and
                        # do not strand the heading that goes under it
                        # either. One break covers the band, the heading
                        # and the start of what follows.
                        need = stack_height([band]) + 0.25 * cm + (
                            stack_height([cont]) + _ORPHAN_MIN_CM * cm
                            if cont is not None else headings_need)
                        story.append(Spacer(1, 0.3 * cm))
                        story.append(CondPageBreak(
                            min(max(need, 3.5 * cm), _MAX_COND_BREAK)))
                    # The first band of all needs no break of its own: the
                    # Homework part band is directly above it and has
                    # already guaranteed HOMEWORK_MIN_START_CM of room. A
                    # second break here would break the page between that
                    # band and this one, which is the worse strand.
                    story.append(band)
                    story.append(Spacer(1, 0.25 * cm))
                    if cont is not None:
                        story.append(cont)
                elif j == 0:
                    story.append(CondPageBreak(
                        min(headings_need, _MAX_COND_BREAK)))
                if j == 0:
                    story.extend(headings)
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
                    n, display, _, subj, floor = cells[row[0]]
                    block = _passage_question_block(
                        styles, passage,
                        _question_flowables(styles, n, vq, page_map, floor,
                                            display, subj))
                else:
                    block = question_row(cells, row)
                story.append(block)
                flat += len(row)
                j += len(row)

        if data.challenge_questions:
            # The Final Challenge is a scored part of the booklet, the same as
            # the Warm-up, Class Work and Homework, and it is the one the
            # product is sold on. It used to arrive as a centred heading a
            # centimetre below the last homework question, so after twenty
            # questions the thing called the challenge appeared squashed at the
            # foot of the page it had been working down. It gets the same band
            # every other part gets, and a page of its own to arrive on.
            story.append(Spacer(1, 0.4 * cm))
            story.append(CondPageBreak(_CHALLENGE_MIN_START_CM * cm))
            ct = (f" About {times['challenge_minutes']} min."
                  if times["challenge_minutes"] else "")
            story.append(_part_band(
                styles, "Final Challenge", PART_CHALLENGE,
                "You have done the hard part. These last questions mix "
                f"everything together. Nothing new, just all at once.{ct}"))
            story.append(Spacer(1, 0.3 * cm))
            render_questions(data.challenge_questions)

    # ---- Spelling List (words to learn for next week) ----
    spelling_block = _spelling_list_block(
        styles, getattr(data, "spelling_list", None))
    if spelling_block:
        story.append(Spacer(1, 0.5 * cm))
        story.extend(spelling_block)

    # ---- Times Table to Learn (for next week's test) ----
    tables_block = _tables_list_block(
        styles, getattr(data, "tables_list", None))
    if tables_block:
        story.append(Spacer(1, 0.5 * cm))
        story.extend(tables_block)

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
    # The key is set in two columns from here to the end, and the answers
    # heading spans them: the first key page uses a template whose top frame is
    # full width, every page after it is two plain columns. "*" restarts the
    # cycle at the second entry, so "key" repeats for the rest of the booklet.
    story.append(NextPageTemplate(["key_open", "*", "key"]))
    story.append(PageBreak())
    story.append(Paragraph("Answers &amp; Worked Solutions", styles["answers_heading"]))
    story.append(Paragraph(
        "For whoever is marking. A tick means the answer was checked. Page "
        "numbers in brackets point back to the question.",
        styles["challenge_blurb"]))
    # Nothing else belongs in the full-width banner: without this the next
    # flowable would be laid across both columns before the columns start.
    story.append(FrameBreak())
    story.extend(_spelling_key_block(styles, getattr(data, "spelling_test", None)))
    story.extend(_tables_key_block(styles, getattr(data, "tables_test", None)))
    acount = {"n": 0}

    # Headings in the key are held back until there is something to put under
    # them, then laid down as one run behind a single break. A key page ended
    # with "Class Work", "Number and Place Value" and "Four-digit numbers and
    # ordering" stacked at the foot of a column with no answer under any of
    # them, which reads to whoever is marking as a page that failed to print.
    # Holding them back also means one break covers the whole stack: a break
    # per heading can strand the heading above it.
    pending: list = []

    def lay_headings():
        """Put the waiting headings down, with room under them or overleaf."""
        if not pending:
            return
        story.append(orphan_break(pending, KEY_COLUMN_WIDTH))
        story.extend(pending)
        pending.clear()

    def render_answers(qs):
        for vq in qs:
            lay_headings()
            acount["n"] += 1
            page = (page_refs or {}).get(acount["n"])
            # Numbered as the body numbered it. The running index still drives
            # the page lookup, because several questions now print as "3".
            story.append(_answer_block(styles, shown(acount["n"]), vq, page,
                                       width=KEY_COLUMN_WIDTH))

    def render_section_answers(section, questions):
        """Answers for one subtopic, with each reading named above its group.

        Numbering restarts at 1 under every passage, exactly as the student
        page numbers it. Flattening the groups printed two runs of "1" to "5"
        under a single subtopic heading with nothing between them, so whoever
        was marking beside the student had no way to tell which reading the
        second run belonged to and marked against the wrong one.
        """
        groups = passage_groups(questions, section_passages(section))
        for passage, qs in groups:
            if passage is not None and len(groups) > 1:
                title = getattr(passage, "title", None)
                pending.append(Paragraph(
                    _escape(f"Questions on '{title}'" if title
                            else "Questions on the next reading"),
                    styles["passage_label"]))
            render_answers(qs)

    if data.recap_questions:
        pending.append(_key_part_heading(styles, "Warm-up Recap", PART_RECAP,
                                         KEY_COLUMN_WIDTH))
        render_answers(data.recap_questions)

    # The gaps in "let's try one together" are the only thing in the booklet a
    # child writes into that the key would otherwise not cover, so they come
    # first: whoever is sitting with them needs the filled-in version before
    # the independent practice, not after it.
    guided = [(s, ge) for s in data.sections
              for ge in ((s.teaching.guided_examples if s.teaching else []) or [])]
    if guided:
        pending.append(_key_part_heading(styles, "Let's try one together",
                                         PART_CLASSWORK, KEY_COLUMN_WIDTH))
        state = {"subject": None, "topic": None}
        for section, ge in guided:
            subject_topic_headers(section, state, key=True, out=pending)
            pending.append(Paragraph(_escape(section.subtopic),
                                     styles["key_subtopic"]))
            # The completed box is the content these headings introduce, so it
            # decides where they can go: measured, because a box with five
            # steps in it is centimetres taller than one with two.
            box = _worked_example_flowable(
                styles, ge, "Completed", guided=True, reveal=True,
                width=KEY_COLUMN_WIDTH)
            story.append(orphan_break(pending + [box], KEY_COLUMN_WIDTH,
                                      follow_cm=0.0))
            story.extend(pending)
            pending.clear()
            story.append(box)
            story.append(Spacer(1, 0.2 * cm))

    pending.append(_key_part_heading(styles, "Class Work", PART_CLASSWORK,
                                     KEY_COLUMN_WIDTH))
    state = {"subject": None, "topic": None}
    for section in data.sections:
        # A subtopic the hour cap moved out has no class work, and its answers
        # are printed under Homework below. Without this the key printed its
        # heading here with nothing underneath, and whoever was marking read
        # that as a missing page. The Homework loop has always had the
        # equivalent guard.
        if not section.questions:
            continue
        subject_topic_headers(section, state, key=True, out=pending)
        pending.append(Paragraph(_escape(section.subtopic), styles["key_subtopic"]))
        # Grouping questions under their passage changes the printed order, so
        # the key has to be walked in the same order or every number after the
        # first passage points at the wrong question.
        render_section_answers(section, section.questions)

    if has_homework:
        pending.append(_key_part_heading(styles, "Homework", PART_HOMEWORK,
                                         KEY_COLUMN_WIDTH))
        state = {"subject": None, "topic": None}
        for section in data.sections:
            if not section.homework_questions:
                continue
            subject_topic_headers(section, state, key=True, out=pending)
            pending.append(Paragraph(_escape(section.subtopic),
                                     styles["key_subtopic"]))
            render_section_answers(section, section.homework_questions)

    if data.challenge_questions:
        pending.append(_key_part_heading(styles, "Final Challenge",
                                         PART_CHALLENGE, KEY_COLUMN_WIDTH))
        render_answers(data.challenge_questions)

    # A part with no answers under it still names itself: a key that silently
    # omits "Final Challenge" reads as a key with a section missing.
    lay_headings()
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
    # Back to one column: the key is set in two, and a page of attributions is
    # neither a key nor something anybody reads down a column.
    out = [NextPageTemplate("main"), PageBreak(),
           Paragraph("Picture Credits", styles["answers_heading"]),
           Paragraph(
               "The photographs in this booklet come from Wikimedia Commons "
               "and are used under their respective licences.",
               styles["challenge_blurb"])]
    for c in credits:
        out.append(Paragraph(f"• {_escape(c)}", styles["key_point"]))
    return out


def booklet_title(data: BookletData) -> str:
    """What the file calls itself: in a browser tab, a print queue, Properties.

    Every booklet used to be titled "Academic Accelerate Practice Booklet",
    which is the same string for a Year 1 English booklet and a Year 10 maths
    one, so a tutor with four of them open could not tell which tab was which.
    """
    parts = [data.program_label or data.subject, data.year_level]
    if data.program_label and data.subject and data.subject != data.program_label:
        parts.insert(1, data.subject)
    if data.student_name:
        parts.append(data.student_name)
    return " - ".join(p for p in parts if p)


def _booklet_doc(target, data: BookletData, times: dict | None = None):
    doc = BaseDocTemplate(
        target,
        pagesize=A4,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        title=booklet_title(data),
        author="FolioAI",
        # ReportLab writes its own literal "(unspecified)" into these when they
        # are left off, and a parent sees that in the Properties dialog of the
        # thing they paid for.
        subject=f"{data.subject} practice, {data.year_level}",
        creator="FolioAI",
    )
    _head = data.program_label or data.subject
    doc._header_text = f"{_head}  |  {data.year_level}  |  {data.student_name}"
    # Built once per document and read by the page-1 canvas callback. Pure
    # data, so the probe build and the real build draw an identical cover.
    doc._cover = cover_spec(data, times)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame,
                                       onPage=_draw_page_chrome)]
                         + _key_templates(doc))
    return doc


# How much of the first key page the full-width banner takes: the "Answers &
# Worked Solutions" heading and the line under it telling the marker what the
# tick and the page references mean.
_KEY_BANNER_CM = 2.9


def _key_templates(doc) -> list:
    """The answer key's page templates: two columns, and a banner on page one.

    The body is untouched. Only the key changes measure, because only the key
    has the problem: its lines are short (an answer, a tick, a page number, and
    a line or two of working) and across the full width of an A4 page the
    typical line used half the measure and left the other half blank, over six
    pages. Two columns fit the lines the key actually produces and take it from
    about six pages to three.
    """
    def columns(height, bottom):
        w = (doc.width - KEY_COLUMN_GAP) / 2
        return [Frame(doc.leftMargin, bottom, w, height, id="key_left",
                      leftPadding=0, rightPadding=0),
                Frame(doc.leftMargin + w + KEY_COLUMN_GAP, bottom, w, height,
                      id="key_right", leftPadding=0, rightPadding=0)]

    banner_h = _KEY_BANNER_CM * cm
    open_frames = [Frame(doc.leftMargin, doc.bottomMargin + doc.height - banner_h,
                         doc.width, banner_h, id="key_banner",
                         leftPadding=0, rightPadding=0)]
    open_frames += columns(doc.height - banner_h, doc.bottomMargin)
    return [
        PageTemplate(id="key_open", frames=open_frames, onPage=_draw_page_chrome),
        PageTemplate(id="key", frames=columns(doc.height, doc.bottomMargin),
                     onPage=_draw_page_chrome),
    ]


def render_pdf(data: BookletData, out_path: Path) -> Path:
    """Render the booklet, answer key included.

    One document. A FolioAI booklet is worked through by a parent or tutor sitting
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

    # Throwaway build purely to find out which page each question landed on.
    # Everything that affects that pagination sits before the answer key's
    # PageBreak, so the map is the same in the real build.
    page_refs: dict = {}
    probe = _booklet_doc(io.BytesIO(), data, times)
    probe.build(_booklet_story(
        styles, data, times, page_map=page_refs, page_refs=None))

    # An odd last student page means the key would print on its reverse. The
    # blank verso shifts every key page by one, but nothing references a key
    # page, so the map built above still holds.
    blank_before_key = page_refs.get(LAST_STUDENT_PAGE, 0) % 2 == 1

    doc = _booklet_doc(str(out_path), data, times)
    doc.build(_booklet_story(
        styles, data, times, page_map=None, page_refs=page_refs,
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
        author="FolioAI",
    )
    doc._header_text = f"{paper.subject}  |  {paper.year_level}  |  {paper.student_name}"
    # Exams use a plain cover: the booklet cover design undercuts the look of a
    # formal examination front page, so page 1 stays a text page with no
    # running header.
    doc._cover = None
    doc._plain_cover = True

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_draw_page_chrome)])

    styles = _make_styles()
    body_width = A4[0] - 2 * PAGE_MARGIN
    story = []

    # ---- Cover: formal exam front page ----
    story.append(Spacer(1, 1.2 * cm))
    story.append(Paragraph("FOLIOAI", styles["wordmark"]))
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
        "This is a practice paper generated by FolioAI. Questions marked with a "
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
    story.append(_part_band(styles, "Marking Key", "#146B2C",
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
                  tidy_answer: bool = True, width: float | None = None):
    """One numbered answer, its verification mark, and its working.

    Laid out as a row rather than as one run of text, for two reasons a person
    marking would notice.

    The answer used to lose its hanging indent when it wrapped: the second line
    started flush with the question number, so at a glance "1299, 1562, 1840"
    and the number of the NEXT question sat in the same column. It now hangs
    under the word "Answer", the way a reference list does.

    The tick and the page reference used to trail whatever the answer happened
    to be, which put them at a different x on every line, and a wrapped answer
    pushed them onto the line below. They are set in their own right-aligned
    column, so the ticks form a column that can be scanned down rather than
    read one by one, and a missing one is visible instantly.
    """
    width = width if width is not None else A4[0] - 2 * PAGE_MARGIN
    # The only place a verification mark belongs: beside a worked answer, where
    # it tells the person marking that this solution was checked. The check
    # glyph is outside Latin-1, so fall back to the word when we fell back to
    # Helvetica. The key's own intro line says what the tick means.
    mark = "✓" if _UNICODE_FONT else "checked"
    symbol_html = f'<font color="#146B2C"><b>{mark}</b></font>' if vq.verified else ""
    # Marking 63 questions spread over 18 pages means constant flipping, so the
    # key says where the question was.
    page_html = (f'<font size=8.5 color="#5F5F5F">(p{page})</font>'
                 if page else "")
    # Booklet keys restore the unit the question asked for and show a fraction
    # in lowest terms. An exam marking key does neither: senior answers carry
    # compound units and exact forms that must be reproduced as marked.
    answer = key_answer(vq.question) if tidy_answer else (vq.question.answer or "")
    # A cloze answer is the word the gap was hiding, so the key prints it
    # plainly. `strip_markers` is the backstop for a model that wraps the
    # answer field as well as the sentence: the key must never show a customer
    # the machinery, and "[[melancholy]]" beside question 4 is exactly that.
    row = Table(
        [[Paragraph(f"<b>{q_num}.</b> Answer: {strip_markers(_escape(answer))}",
                    styles["key_answer"]),
          Paragraph(f"{symbol_html} {page_html}".strip(), styles["key_mark"])]],
        colWidths=[width - _KEY_MARK_CM * cm, _KEY_MARK_CM * cm], hAlign="LEFT",
    )
    row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    block = [row]
    for line in solution_lines(vq.question.working):
        block.append(Paragraph(strip_markers(_escape(line)), styles["working"]))
    block.append(Spacer(1, 0.3 * cm))
    return KeepTogether(block)
