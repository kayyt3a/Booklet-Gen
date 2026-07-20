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
        "part_band": ParagraphStyle(
            "part_band", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=17, leading=20, textColor=colors.white, alignment=TA_CENTER,
        ),
        "part_band_sub": ParagraphStyle(
            "part_band_sub", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=13, textColor=colors.HexColor("#F4F7FB"),
            alignment=TA_CENTER, spaceBefore=2,
        ),
        "mnemonic": ParagraphStyle(
            "mnemonic", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=12, leading=15, textColor=colors.HexColor("#8B1E3F"),
            spaceBefore=4, spaceAfter=4,
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
    if data.week_number and data.total_weeks:
        wk_line = f"Week {data.week_number} of {data.total_weeks}"
        if data.week_focus:
            wk_line += f"  |  {_escape(data.week_focus)}"
        story.append(Paragraph(wk_line, styles["meta"]))
        story.append(Spacer(1, 0.15 * cm))
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
        sub = f"Quick revision to warm up. About {data.recap_minutes} min." if data.recap_minutes \
            else "Quick revision to warm up."
        story.append(_part_band(styles, "Warm-up Recap", "#6b7280", sub))
        story.append(Spacer(1, 0.3 * cm))
        render_questions(data.recap_questions)

    # ---- Class Work (lesson + guided + now-you-try) ----
    cw_sub = f"Do this in your lesson. About {data.classwork_minutes} min." if data.classwork_minutes \
        else "Do this in your lesson."
    story.append(_part_band(styles, "Class Work", "#1F3A5F", cw_sub))
    story.append(Spacer(1, 0.3 * cm))
    state = {"subject": None, "topic": None}
    for section in data.sections:
        subject_topic_headers(section, state)
        time_badge = (
            f'  <font size=9 color="#1B8A3A">(about {section.estimated_minutes} min)</font>'
            if section.estimated_minutes else ""
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
        story.append(PageBreak())
        hw_sub = f"Do these through the week to lock it in. About {data.homework_minutes} min." \
            if data.homework_minutes else "Do these through the week to lock it in."
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
            ct = f" (about {data.challenge_minutes} min)" if data.challenge_minutes else ""
            story.append(Paragraph(
                "Now let's see how well you know it all. Questions from across "
                f"everything you practised.{ct}",
                styles["challenge_blurb"],
            ))
            render_questions(data.challenge_questions)

    # ---- Answer key (same order: recap, class work, homework, challenge) ----
    story.append(PageBreak())
    story.append(Paragraph("Answers &amp; Worked Solutions", styles["answers_heading"]))
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
