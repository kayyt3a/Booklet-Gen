from __future__ import annotations

import logging
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
from .timing import booklet_timing


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
        "closing": ParagraphStyle(
            "closing", parent=base["Normal"], fontName=FONT_REGULAR,
            fontSize=10.5, leading=15, alignment=TA_CENTER,
            textColor=colors.HexColor("#1F3A5F"),
        ),
    }


import re

# A fraction is digits/digits, but not part of a date (15/07/2025), a decimal
# (1/2.5), or a negative. Trailing punctuation is fine and must stay matched:
# an earlier lookahead of (?![0-9./]) silently skipped every fraction that
# ended a sentence, so "1/2 + 1/4." rendered with only the first one styled.
_FRACTION_RE = re.compile(r"(?<![\d./\-])(\d{1,4})/(\d{1,4})(?![\d/]|\.\d)")

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_FRACTION_SLASH = "⁄"


def _prettify_fractions(text: str) -> str:
    """Turn "3/4" into "³⁄₄" using real Unicode glyphs.

    These sit on the normal baseline, so unlike ReportLab's <sup>/<sub>
    markup they cannot collide with the lines above and below. The old
    approach shifted digits outside the line box, and at the leading these
    styles use that produced visibly overlapping text in the answer key.

    Requires a Unicode font: Helvetica has no superscript glyphs and would
    render black boxes, so fall back to plain "3/4" when registration failed.
    """
    if not _UNICODE_FONT:
        return text

    def repl(m: re.Match) -> str:
        num, den = m.group(1), m.group(2)
        if int(den) == 0:
            return m.group(0)
        return (num.translate(_SUPERSCRIPT) + _FRACTION_SLASH
                + den.translate(_SUBSCRIPT))
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
_X_MULT_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + _MULT_OPERAND + r")\s*[xX]\s*(?="
    + _MULT_OPERAND + r"(?![A-Za-z0-9]))")

# "40 cm x 20 cm", "3 units x 2 units": a unit word, then x, then a number.
_X_MULT_UNIT_RE = re.compile(
    r"\b(cm|mm|km|m|litres?|units?|cubes?|blocks?)\s+[xX]\s+(?=[0-9])", re.IGNORECASE)

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
    text = _X_MULT_RE.sub(rf"\1 {MULTIPLY} ", text)
    text = _SLASH_DIV_RE.sub(f" {DIVIDE} ", text)
    text = _WORD_DIV_RE.sub(f" {DIVIDE} ", text)

    def unit_word(m: re.Match) -> str:
        digit = "3" if m.group(1).lower() == "cubic" else "2"
        return _UNIT_WORDS[m.group(2).lower()] + _SUPER_23[digit]

    text = _POWER_WORD_RE.sub(unit_word, text)
    text = _UNIT_POWER_FLAT_RE.sub(lambda m: m.group(1) + _SUPER_23[m.group(2)], text)
    return text


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


MAX_IMG_WIDTH = 7.5 * cm
MAX_IMG_HEIGHT = 4.8 * cm
WE_IMG_WIDTH = 6 * cm
WE_IMG_HEIGHT = 4 * cm


def _make_image(path: str | None, max_w=MAX_IMG_WIDTH, max_h=MAX_IMG_HEIGHT):
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        reader = ImageReader(str(p))
        iw, ih = reader.getSize()
        scale = min(max_w / iw, max_h / ih, 1.0)
        return Image(str(p), width=iw * scale, height=ih * scale)
    except Exception:
        return None


def _worked_example_flowable(styles, we: WorkedExample, label: str = "Worked example"):
    """Return a bordered box containing a worked example. `label` distinguishes
    the "I do" worked example from the "we do" guided ones."""
    inner = [
        Paragraph(label, styles["we_label"]),
        Paragraph(_escape(we.question), styles["we_question"]),
    ]
    img = _make_image(we.image_path, max_w=WE_IMG_WIDTH, max_h=WE_IMG_HEIGHT)
    if img is not None:
        inner.append(Spacer(1, 0.15 * cm))
        inner.append(img)
        inner.append(Spacer(1, 0.15 * cm))
    for i, step in enumerate(we.steps, 1):
        inner.append(Paragraph(f"<b>{i}.</b> {_escape(_strip_step_prefix(step))}",
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
_DIFFICULTY_SPACE_CM = {"easy": 1.2, "medium": 2.2, "hard": 3.2}

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
    r"draw|sketch|shade|colour|color|plot|label|construct|write a (?:short )?"
    r"(?:paragraph|sentence|story)|in your own words)\b", re.IGNORECASE)


def answer_line_labels(question) -> list[str]:
    """Labels for the ruled answer lines under a question, empty for none."""
    text = getattr(question, "question", "") or ""
    if _EXTENDED_RESPONSE_RE.search(text):
        return []
    parts = part_labels(text)
    if len(parts) >= 2:
        return [f"{p}) Answer:" for p in parts]
    return ["Answer:"]


def _working_space_cm(question) -> float:
    base = _DIFFICULTY_SPACE_CM.get(
        (question.difficulty or "medium").strip().lower(), 2.2)
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

    def __init__(self, height: float, labels=(), min_height: float | None = None):
        super().__init__()
        self.labels = list(labels)
        self.answers_height = _ANSWER_LINE_CM * cm * len(self.labels)
        self.height = height + self.answers_height
        floor = _MIN_WORKING_SPACE_CM * cm + self.answers_height
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
        if not self.labels:
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
        c.restoreState()


def _question_block(styles, q_num: int, vq: ValidatedQuestion):
    """One numbered question plus its working space.

    No verification mark here. Every question in a generated booklet carried a
    green tick, which to a child reading an unattempted page says "correct", and
    which, being on every question, said nothing at all. Verification is shown
    where it means something: beside the answer in the key.
    """
    block = [
        Paragraph(
            f"<b>{q_num}.</b> {_escape(vq.question.question)}",
            styles["question"],
        ),
    ]
    img = _make_image(vq.image_path)
    if img is not None:
        block.append(Spacer(1, 0.3 * cm))
        block.append(img)
        if vq.image_attribution:
            block.append(Paragraph(
                f"<i>Image: {_escape(vq.image_attribution)}</i>",
                styles["footer_note"],
            ))
    block.append(WorkingSpace(
        _working_space_cm(vq.question) * cm,
        answer_line_labels(vq.question),
    ))
    return KeepTogether(block)


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


def _closing_note(styles, data: BookletData, include_answers: bool):
    """A short sign-off after the last question, addressed to the student."""
    name = _escape(data.student_name or "").strip()
    opening = f"That is the end of the booklet, {name}." if name \
        else "That is the end of the booklet."
    tail = ("Check your answers against the key at the back, and mark anything "
            "you want to go over again." if include_answers else
            "Bring it to your next session so you can go through it with your "
            "tutor, and mark anything you want to go over again.")
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


def student_copy_path(out_path: Path) -> Path:
    """Sibling path for the answer-free copy: booklet.pdf -> booklet-student.pdf."""
    out_path = Path(out_path)
    return out_path.with_name(f"{out_path.stem}-student{out_path.suffix or '.pdf'}")


def render_booklet_pair(data: BookletData, out_path: Path) -> tuple[Path, Path]:
    """Render both copies of a booklet.

    Returns (tutor_copy, student_copy). The tutor copy keeps the given path and
    the full answer key; the student copy is the same booklet with the key
    removed, which is the copy you can actually hand to a child.
    """
    tutor = render_pdf(data, out_path, include_answers=True)
    student = render_pdf(data, student_copy_path(out_path), include_answers=False)
    return tutor, student


def render_pdf(data: BookletData, out_path: Path, *,
               include_answers: bool = True) -> Path:
    """Render a booklet.

    `include_answers=False` produces the student copy: the same booklet with no
    answer key bound into the back of it. Without this a tutoring firm cannot
    hand a booklet to a student at all, because the worked solution to every
    question is a few pages further on in the same document.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN, bottomMargin=PAGE_MARGIN,
        title=f"{data.program_label or data.subject} Practice Booklet",
        author="Folio",
    )
    _head = data.program_label or data.subject
    doc._header_text = f"{_head}  |  {data.year_level}  |  {data.student_name}"
    doc._cover_bg = cover_background_path()

    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height, id="body",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_draw_page_chrome)])

    styles = _make_styles()
    story = []

    # Times are recomputed here rather than read off the BookletData. The
    # pipeline's numbers count questions only; these count what is actually
    # printed, including the mini-lesson, the worked example and every guided
    # example the student has to work through. See timing.py.
    times = booklet_timing(data)

    # Cover - lead with the product line (program) when present, otherwise the
    # subject. The secondary line carries the subject(s) and year level. With a
    # background image the text is pushed down to sit in the clear centre zone.
    story.append(Spacer(1, 6.5 * cm if doc._cover_bg else 3 * cm))
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
    if include_answers:
        # Only the tutor copy has a key to point at, and the mark now lives
        # beside the answers rather than beside the unattempted questions.
        section_subjects = {(s.subject or data.subject).strip().lower()
                            for s in data.sections}
        only_maths = section_subjects == {"mathematics"}
        story.append(Paragraph(
            "Tutor copy. The answer key at the back marks each answer that has "
            + ("been symbolically verified." if only_maths
               else "been checked for accuracy."),
            styles["footer_note"],
        ))
    else:
        story.append(Paragraph(
            "Student copy. Work through it in order and show your working.",
            styles["footer_note"],
        ))
    story.append(PageBreak())

    multi_subject = len({(s.subject or "") for s in data.sections if s.subject}) > 1

    # A single running question number across the whole booklet, so the answer
    # key lines up no matter which part a question is in.
    counter = {"n": 0}

    def render_questions(qs):
        for vq in qs:
            counter["n"] += 1
            story.append(_question_block(styles, counter["n"], vq))

    def subject_topic_headers(section, state):
        if multi_subject and section.subject and section.subject != state["subject"]:
            story.append(Paragraph(_escape(section.subject), styles["subject_band"]))
            state["subject"] = section.subject
            state["topic"] = None
        if section.topic != state["topic"]:
            story.append(Paragraph(_escape(section.topic), styles["topic"]))
            state["topic"] = section.topic

    # ---- Warm-up Recap ----
    if data.recap_questions:
        sub = f"Quick revision to warm up. About {times['recap_minutes']} min." \
            if times["recap_minutes"] else "Quick revision to warm up."
        story.append(_part_band(styles, "Warm-up Recap", "#6b7280", sub))
        story.append(Spacer(1, 0.3 * cm))
        render_questions(data.recap_questions)

    # ---- Class Work (lesson + guided + now-you-try) ----
    cw_sub = f"Do this in your lesson. About {times['classwork_minutes']} min." \
        if times["classwork_minutes"] else "Do this in your lesson."
    story.append(_part_band(styles, "Class Work", "#1F3A5F", cw_sub))
    story.append(Spacer(1, 0.3 * cm))
    state = {"subject": None, "topic": None}
    for si, section in enumerate(data.sections):
        subject_topic_headers(section, state)
        time_badge = (
            f'  <font size=9 color="#1B8A3A">'
            f'(about {times["section_minutes"][si]} min)</font>'
        )
        story.append(Paragraph(_escape(section.subtopic) + time_badge, styles["subtopic"]))

        t = section.teaching
        if t is not None:
            for para in t.intro_paragraphs:
                story.append(Paragraph(_escape(para), styles["intro_para"]))
            if t.mnemonic:
                story.append(Paragraph(f"Remember: {_escape(t.mnemonic)}", styles["mnemonic"]))
            if t.key_points:
                story.append(Spacer(1, 0.15 * cm))
                for kp in t.key_points:
                    story.append(Paragraph(f"• {_escape(kp)}", styles["key_point"]))
            story.append(Spacer(1, 0.3 * cm))
            story.append(_worked_example_flowable(styles, t.worked_example, "Watch first (worked example)"))
            for i, ge in enumerate(t.guided_examples, 1):
                story.append(Spacer(1, 0.2 * cm))
                story.append(_worked_example_flowable(styles, ge, "Let's do this one together"))
            story.append(Spacer(1, 0.35 * cm))
            story.append(Paragraph("Now you try:", styles["practice_label"]))

        render_questions(section.questions)

    # ---- Homework (repetition through the week) + Final Challenge ----
    has_homework = any(s.homework_questions for s in data.sections)
    if has_homework or data.challenge_questions:
        # Not an unconditional break. Class Work used to end wherever it ended
        # and throw the rest of the page away: one real booklet finished the
        # section two questions into a page and left the other two thirds
        # blank. The coloured band is divider enough, so only break when there
        # is too little room left to be worth starting Homework here.
        story.append(CondPageBreak(HOMEWORK_MIN_START_CM * cm))
        hw_sub = ("Do these through the week to lock it in. "
                  f"About {times['homework_minutes']} min.") \
            if times["homework_minutes"] else "Do these through the week to lock it in."
        story.append(_part_band(styles, "Homework", "#8B1E3F", hw_sub))
        story.append(Spacer(1, 0.3 * cm))
        state = {"subject": None, "topic": None}
        for section in data.sections:
            if not section.homework_questions:
                continue
            subject_topic_headers(section, state)
            story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
            render_questions(section.homework_questions)

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

    # ---- Closing ----
    # The booklet used to stop dead: the page after the last question was the
    # answer key. Say something to the student first, by name.
    story.append(Spacer(1, 0.6 * cm))
    story.append(_closing_note(styles, data, include_answers))

    if not include_answers:
        doc.build(story)
        return out_path

    # ---- Answer key (same order: recap, class work, homework, challenge) ----
    story.append(PageBreak())
    story.append(Paragraph("Answers &amp; Worked Solutions", styles["answers_heading"]))
    story.append(Paragraph(
        "Tutor copy only. The student copy of this booklet ends after the last "
        "question.", styles["challenge_blurb"]))
    acount = {"n": 0}

    def render_answers(qs):
        for vq in qs:
            acount["n"] += 1
            story.append(_answer_block(styles, acount["n"], vq))

    if data.recap_questions:
        story.append(Paragraph("Warm-up Recap", styles["topic"]))
        render_answers(data.recap_questions)

    story.append(Paragraph("Class Work", styles["topic"]))
    state = {"subject": None, "topic": None}
    for section in data.sections:
        subject_topic_headers(section, state)
        story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
        render_answers(section.questions)

    if has_homework:
        story.append(Paragraph("Homework", styles["topic"]))
        state = {"subject": None, "topic": None}
        for section in data.sections:
            if not section.homework_questions:
                continue
            subject_topic_headers(section, state)
            story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
            render_answers(section.homework_questions)

    if data.challenge_questions:
        story.append(Paragraph("Final Challenge", styles["topic"]))
        render_answers(data.challenge_questions)

    doc.build(story)
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
            story.append(_answer_block(styles, counter["n"], vq))

    doc.build(story)
    return out_path


def _answer_block(styles, q_num: int, vq: ValidatedQuestion):
    # The only place a verification mark belongs: beside a worked answer, where
    # it tells the person marking that this solution was checked. The check
    # glyph is outside Latin-1, so drop it when we fell back to Helvetica.
    mark = "✓ verified" if _UNICODE_FONT else "verified"
    symbol_html = f' <font color="#1B8A3A"><b>{mark}</b></font>' if vq.verified else ""
    block = [
        Paragraph(
            f"<b>{q_num}.</b> Answer: {_escape(vq.question.answer)}{symbol_html}",
            styles["answer"],
        ),
    ]
    for line in vq.question.working.splitlines():
        line = line.strip()
        if line:
            block.append(Paragraph(_escape(line), styles["working"]))
    block.append(Spacer(1, 0.35 * cm))
    return KeepTogether(block)
