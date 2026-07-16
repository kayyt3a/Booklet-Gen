from __future__ import annotations

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


def _escape(text: str) -> str:
    return _prettify_fractions(
        text.replace("&", "&amp;")
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


def _draw_page_chrome(canvas, doc):
    canvas.saveState()
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
        title=f"{data.subject} Practice Booklet",
        author="Folio",
    )
    doc._header_text = f"{data.subject} — {data.year_level} — {data.student_name}"

    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height, id="body",
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_draw_page_chrome)])

    styles = _make_styles()
    story = []

    # Cover
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph("FOLIO", styles["wordmark"]))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph(f"{data.subject}", styles["title"]))
    story.append(Paragraph(f"Practice Booklet &amp; Early Preparation — {data.year_level}", styles["subtitle"]))
    story.append(Spacer(1, 1.5 * cm))
    story.append(Paragraph(f"Prepared for <b>{_escape(data.student_name)}</b>", styles["subtitle"]))
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(date.today().strftime("%d %B %Y"), styles["meta"]))
    story.append(Spacer(1, 4 * cm))
    is_maths = data.subject.strip().lower() in {"mathematics", "maths", "math"}
    verify_note = (
        "Questions marked with a check mark have been symbolically verified."
        if is_maths else
        "Questions marked with a check mark have been reviewed by an independent grader."
    )
    story.append(Paragraph(verify_note, styles["footer_note"]))
    story.append(PageBreak())

    # Body: per subtopic — heading, lesson, worked example, practice questions
    q_num = 0
    current_topic = None
    for section in data.sections:
        if section.topic != current_topic:
            story.append(Paragraph(_escape(section.topic), styles["topic"]))
            current_topic = section.topic
        story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))

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
        story.append(Paragraph(
            "Let's see how well you know the content — questions from across everything you just practised.",
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
    for section in data.sections:
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
