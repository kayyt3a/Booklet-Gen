from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer,
    PageBreak, KeepTogether, Table, TableStyle, Image,
)
from reportlab.lib.utils import ImageReader

from .schemas import BookletData, ValidatedQuestion, WorkedExample


PAGE_MARGIN = 2.0 * cm


def _make_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=26, leading=30, alignment=TA_CENTER, spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", parent=base["Normal"], fontName="Helvetica",
            fontSize=13, alignment=TA_CENTER, textColor=colors.HexColor("#555555"),
            spaceAfter=4,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, alignment=TA_CENTER, textColor=colors.HexColor("#888888"),
        ),
        "wordmark": ParagraphStyle(
            "wordmark", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=12, alignment=TA_CENTER, textColor=colors.HexColor("#1F3A5F"),
            spaceAfter=4,
        ),
        "subject_band": ParagraphStyle(
            "subject_band", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=15, leading=19, spaceBefore=10, spaceAfter=10,
            textColor=colors.white, backColor=colors.HexColor("#1F3A5F"),
            borderPadding=(6, 8, 6, 8), alignment=TA_CENTER,
        ),
        "topic": ParagraphStyle(
            "topic", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=18, leading=22, spaceBefore=6, spaceAfter=8,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "subtopic": ParagraphStyle(
            "subtopic", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, spaceBefore=4, spaceAfter=6,
            textColor=colors.HexColor("#333333"),
        ),
        "intro_para": ParagraphStyle(
            "intro_para", parent=base["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=14, alignment=TA_LEFT, spaceAfter=5,
        ),
        "key_point": ParagraphStyle(
            "key_point", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=13, leftIndent=14, bulletIndent=2,
            spaceAfter=2,
        ),
        "we_label": ParagraphStyle(
            "we_label", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, textColor=colors.HexColor("#1F3A5F"),
            spaceAfter=3,
        ),
        "we_question": ParagraphStyle(
            "we_question", parent=base["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=14, spaceAfter=6,
        ),
        "we_step": ParagraphStyle(
            "we_step", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=13, leftIndent=12, spaceAfter=2,
        ),
        "we_answer": ParagraphStyle(
            "we_answer", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10.5, leading=14, spaceBefore=4,
            textColor=colors.HexColor("#1B8A3A"),
        ),
        "practice_label": ParagraphStyle(
            "practice_label", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, spaceBefore=8, spaceAfter=6,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "question": ParagraphStyle(
            "question", parent=base["Normal"], fontName="Helvetica",
            fontSize=11, leading=15, alignment=TA_LEFT,
        ),
        "answer": ParagraphStyle(
            "answer", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11, leading=14,
        ),
        "working": ParagraphStyle(
            "working", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=13, textColor=colors.HexColor("#333333"),
            leftIndent=12,
        ),
        "answers_heading": ParagraphStyle(
            "answers_heading", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=12,
            textColor=colors.HexColor("#1F3A5F"),
        ),
        "challenge_heading": ParagraphStyle(
            "challenge_heading", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=22, leading=26, alignment=TA_CENTER, spaceAfter=6,
            textColor=colors.HexColor("#8B1E3F"),
        ),
        "challenge_blurb": ParagraphStyle(
            "challenge_blurb", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=11, leading=14, alignment=TA_CENTER, spaceAfter=14,
            textColor=colors.HexColor("#555555"),
        ),
        "footer_note": ParagraphStyle(
            "footer_note", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=9, textColor=colors.HexColor("#888888"), alignment=TA_CENTER,
        ),
    }


import re

_FRACTION_RE = re.compile(r"(?<![0-9./\-])(\d{1,4})/(\d{1,4})(?![0-9./])")


def _prettify_fractions(text: str) -> str:
    """Turn "3/4" into "<sup>3</sup>/<sub>4</sub>" so it reads as a real fraction.

    Reportlab's <sup>/<sub> markup uses the current font's baseline shift and
    auto-shrinks the digit — safe with Helvetica which has no dedicated
    superscript/subscript glyphs (Unicode chars ⁰¹²³ etc render as black
    boxes in Helvetica).

    The negative lookbehind/lookahead avoid dates (15/07/2025), decimal-ish
    tokens, and negatives.
    """
    def repl(m: re.Match) -> str:
        num, den = m.group(1), m.group(2)
        if int(den) == 0:
            return m.group(0)
        return f"<sup>{num}</sup>/<sub>{den}</sub>"
    return _FRACTION_RE.sub(repl, text)


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


def _escape(text: str) -> str:
    return _prettify_fractions(
        _dedash(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


MAX_IMG_WIDTH = 10 * cm
MAX_IMG_HEIGHT = 7 * cm
WE_IMG_WIDTH = 8 * cm
WE_IMG_HEIGHT = 5.5 * cm


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


def _worked_example_flowable(styles, we: WorkedExample):
    """Return a bordered box containing the worked example."""
    inner = [
        Paragraph("Worked example", styles["we_label"]),
        Paragraph(_escape(we.question), styles["we_question"]),
    ]
    img = _make_image(we.image_path, max_w=WE_IMG_WIDTH, max_h=WE_IMG_HEIGHT)
    if img is not None:
        inner.append(Spacer(1, 0.15 * cm))
        inner.append(img)
        inner.append(Spacer(1, 0.15 * cm))
    for i, step in enumerate(we.steps, 1):
        inner.append(Paragraph(f"<b>{i}.</b> {_escape(step)}", styles["we_step"]))
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


def _question_block(styles, q_num: int, vq: ValidatedQuestion):
    symbol_html = f' <font color="#1B8A3A"><b>✓</b></font>' if vq.verified else ""
    block = [
        Paragraph(
            f"<b>{q_num}.</b> {_escape(vq.question.question)}{symbol_html}",
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
    block.append(Spacer(1, 0.9 * cm))
    return KeepTogether(block)


ASSET_DIR = Path(__file__).resolve().parent / "assets"


def cover_background_path() -> str | None:
    """Resolve the cover background image. Override with the env var
    FOLIO_COVER_BACKGROUND, otherwise use booklet_gen/assets/cover_background.png
    if present. Returns None when no background is configured (plain cover)."""
    env = os.environ.get("FOLIO_COVER_BACKGROUND")
    if env and Path(env).exists():
        return env
    default = ASSET_DIR / "cover_background.png"
    return str(default) if default.exists() else None


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
    canvas.setFont("Helvetica", 9)
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


def render_pdf(data: BookletData, out_path: Path) -> Path:
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
    story.append(Paragraph(date.today().strftime("%d %B %Y"), styles["meta"]))
    if data.total_minutes:
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"Estimated time: about {data.total_minutes} minutes. "
            "Take breaks whenever you need to.",
            styles["meta"],
        ))
    story.append(Spacer(1, 4 * cm))
    section_subjects = {(s.subject or data.subject).strip().lower() for s in data.sections}
    only_maths = section_subjects == {"mathematics"}
    verify_note = (
        "Questions marked with a check mark have been symbolically verified."
        if only_maths else
        "Questions marked with a check mark have been checked for accuracy."
    )
    story.append(Paragraph(verify_note, styles["footer_note"]))
    story.append(PageBreak())

    # Body: per subtopic - heading, lesson, worked example, practice questions.
    # Multi-subject (program) booklets get a subject band whenever the subject
    # changes, so Numeracy and Literacy sections read as distinct parts.
    multi_subject = len({(s.subject or "") for s in data.sections if s.subject}) > 1
    q_num = 0
    current_topic = None
    current_subject = None
    for section in data.sections:
        if multi_subject and section.subject and section.subject != current_subject:
            story.append(Paragraph(_escape(section.subject), styles["subject_band"]))
            current_subject = section.subject
            current_topic = None  # restart topic grouping under the new subject
        if section.topic != current_topic:
            story.append(Paragraph(_escape(section.topic), styles["topic"]))
            current_topic = section.topic
        time_badge = (
            f'  <font size=9 color="#1B8A3A">(about {section.estimated_minutes} min)</font>'
            if section.estimated_minutes else ""
        )
        story.append(Paragraph(_escape(section.subtopic) + time_badge, styles["subtopic"]))

        if section.teaching is not None:
            for para in section.teaching.intro_paragraphs:
                story.append(Paragraph(_escape(para), styles["intro_para"]))
            if section.teaching.key_points:
                story.append(Spacer(1, 0.15 * cm))
                for kp in section.teaching.key_points:
                    story.append(Paragraph(
                        f"• {_escape(kp)}", styles["key_point"],
                    ))
            story.append(Spacer(1, 0.3 * cm))
            story.append(_worked_example_flowable(styles, section.teaching.worked_example))
            story.append(Spacer(1, 0.35 * cm))
            story.append(Paragraph("Now you try:", styles["practice_label"]))

        for vq in section.questions:
            q_num += 1
            story.append(_question_block(styles, q_num, vq))

    # Final Challenge section
    if data.challenge_questions:
        story.append(PageBreak())
        story.append(Spacer(1, 1 * cm))
        story.append(Paragraph("Final Challenge", styles["challenge_heading"]))
        challenge_time = (
            f" (about {data.challenge_minutes} min)" if data.challenge_minutes else ""
        )
        story.append(Paragraph(
            "Let's see how well you know the content. Questions from across everything "
            f"you just practised.{challenge_time}",
            styles["challenge_blurb"],
        ))
        for vq in data.challenge_questions:
            q_num += 1
            story.append(_question_block(styles, q_num, vq))

    # Answers section (practice + challenge)
    story.append(PageBreak())
    story.append(Paragraph("Answers &amp; Worked Solutions", styles["answers_heading"]))

    q_num = 0
    current_topic = None
    current_subject = None
    for section in data.sections:
        if multi_subject and section.subject and section.subject != current_subject:
            story.append(Paragraph(_escape(section.subject), styles["subject_band"]))
            current_subject = section.subject
            current_topic = None
        if section.topic != current_topic:
            story.append(Paragraph(_escape(section.topic), styles["topic"]))
            current_topic = section.topic
        story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
        for vq in section.questions:
            q_num += 1
            story.append(_answer_block(styles, q_num, vq))

    if data.challenge_questions:
        story.append(Paragraph("Final Challenge", styles["topic"]))
        for vq in data.challenge_questions:
            q_num += 1
            story.append(_answer_block(styles, q_num, vq))

    doc.build(story)
    return out_path


def _answer_block(styles, q_num: int, vq: ValidatedQuestion):
    symbol_html = (
        f' <font color="#1B8A3A"><b>✓ verified</b></font>' if vq.verified else ""
    )
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
