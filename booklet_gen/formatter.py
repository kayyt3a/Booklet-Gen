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
    PageBreak, KeepTogether, Table, TableStyle,
)

from .schemas import BookletData


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
        "footer_note": ParagraphStyle(
            "footer_note", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=9, textColor=colors.HexColor("#888888"), alignment=TA_CENTER,
        ),
    }


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


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
        author="Booklet-Gen",
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
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph(f"{data.subject}", styles["title"]))
    story.append(Paragraph(f"Practice Booklet — {data.year_level}", styles["subtitle"]))
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

    # Questions by topic/subtopic
    q_num = 0
    numbering: list[tuple[int, str, str]] = []  # (q_num, verified_symbol, answer)
    current_topic = None
    for section in data.sections:
        if section.topic != current_topic:
            story.append(Paragraph(_escape(section.topic), styles["topic"]))
            current_topic = section.topic
        story.append(Paragraph(_escape(section.subtopic), styles["subtopic"]))
        for vq in section.questions:
            q_num += 1
            symbol = "✓" if vq.verified else ""
            numbering.append((q_num, symbol, vq.question.answer))
            symbol_html = f' <font color="#1B8A3A"><b>✓</b></font>' if vq.verified else ""
            block = [
                Paragraph(
                    f"<b>{q_num}.</b> {_escape(vq.question.question)}{symbol_html}",
                    styles["question"],
                ),
                Spacer(1, 0.9 * cm),
            ]
            story.append(KeepTogether(block))

    # Answers section
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
            symbol_html = f' <font color="#1B8A3A"><b>✓ verified</b></font>' if vq.verified else ""
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
            story.append(KeepTogether(block))

    doc.build(story)
    return out_path
