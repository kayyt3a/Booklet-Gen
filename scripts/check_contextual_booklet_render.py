"""Render and inspect a booklet containing Folio-owned contextual scenes.

Run from the repository root:

    PYTHONPATH=. python scripts/check_contextual_booklet_render.py

Pass ``--output path.pdf`` to keep the representative booklet for visual QA.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from pypdf import PdfReader
from reportlab.platypus import KeepTogether

from booklet_gen.agents.consistency import (
    reconcile_diagram_spec,
)
from booklet_gen.formatter import (
    SCENE_IMG_HEIGHT,
    SCENE_IMG_WIDTH,
    WE_SCENE_IMG_HEIGHT,
    WE_SCENE_IMG_WIDTH,
    _lesson_flowables,
    _make_styles,
    answer_line_labels,
    image_credits,
    render_pdf,
)
from booklet_gen.schemas import (
    BookletData,
    Question,
    SubtopicOutput,
    SubtopicTeaching,
    ValidatedQuestion,
    WorkedExample,
)
from booklet_gen.visuals.scenes import render_scene
from booklet_gen.visuals import render_diagram
from booklet_gen.visual_policy import deterministic_diagram_spec


SHADOW = {
    "template": "shadow_similarity",
    "unit": "m",
    "objects": [
        {"id": "reference", "kind": "tree", "height": 6, "shadow": 4},
        {"id": "target", "kind": "tree", "height": None, "shadow": 10},
    ],
    "unknown": {"object_id": "target", "measure": "height", "symbol": "x"},
}

GARDEN = {
    "template": "garden",
    "unit": "m",
    "length": 8,
    "width": 5,
    "kind": "garden bed",
}

GROUPS = {
    "template": "equal_groups_scene",
    "groups": 4,
    "each": 3,
    "kind": "ball",
}


def _scene_path(spec: dict, profile: str = "student") -> str:
    path = render_scene(spec, profile=profile)
    assert path is not None and path.exists(), spec["template"]
    return str(path)


def _question(text: str, answer: str, working: str, spec: dict) -> ValidatedQuestion:
    return ValidatedQuestion(
        question=Question(
            question=text,
            answer=answer,
            working=working,
            scene_spec=spec,
            visual_priority="required",
            visual_reason="the context carries measured or counted givens",
        ),
        verified=True,
        image_path=_scene_path(spec),
    )


def _diagram_question(text: str, answer: str, working: str) -> ValidatedQuestion:
    spec = deterministic_diagram_spec(text, "Mathematics", "Written algorithms")
    assert spec is not None
    path = render_diagram(spec)
    assert path is not None and path.exists()
    return ValidatedQuestion(
        question=Question(
            question=text,
            answer=answer,
            working=working,
            diagram_spec=spec,
            visual_priority="required",
        ),
        verified=True,
        image_path=str(path),
    )


def _booklet() -> BookletData:
    model = WorkedExample(
        question=(
            "Tree shadows model: A 6 m tree casts a 4 m shadow. At the same "
            "time, another tree casts a 10 m shadow. Find its height x."
        ),
        steps=[
            "Match height to shadow: 6/4 = x/10.",
            "Multiply both sides by 10: x = 60/4.",
            "The second tree is 15 m tall.",
        ],
        answer="15 m",
        scene_spec=SHADOW,
        image_path=_scene_path(SHADOW, profile="teaching"),
    )
    teaching = SubtopicTeaching(
        intro_paragraphs=[
            "A contextual diagram can carry exact measurements while the "
            "question asks you to connect them."
        ],
        key_points=[
            "Use matching measures in the same order.",
            "Treat x as the unknown and do not read an answer from the picture.",
        ],
        worked_example=model,
        guided_examples=[_guided_number_line()],
    )
    questions = [
        _question(
            "Tree shadows practice: A 6 m tree casts a 4 m shadow. A second "
            "tree casts a 10 m shadow. Find the second tree's height x.",
            "15 m",
            "6/4 = x/10, so x = 60/4 = 15 m.",
            SHADOW,
        ),
        _question(
            "Garden plan practice: A rectangular garden bed is 8 m long and "
            "5 m wide. Find its area.",
            "40 square metres",
            "Area = 8 x 5 = 40 square metres.",
            GARDEN,
        ),
        _question(
            "Equal groups practice: Four trays each hold 3 balls. How many "
            "balls are there altogether?",
            "12",
            "4 x 3 = 12 balls.",
            GROUPS,
        ),
        _diagram_question(
            "Calculate 694 ÷ 5.",
            "138 remainder 4",
            "5 goes into 694 one hundred and thirty-eight times with 4 left.",
        ),
    ]
    section = SubtopicOutput(
        topic="Visual problem solving",
        subtopic="Read useful diagrams and scenes",
        teaching=teaching,
        questions=questions,
        estimated_minutes=25,
    )
    return BookletData(
        subject="Mathematics",
        year_level="Year 7",
        student_name="Sample Student",
        sections=[section],
        challenge_questions=[ValidatedQuestion(
            question=Question(
                question=(
                    "Five garden plots challenge: A garden has 4 rectangular "
                    "plots. A new plot is added. What is the total area?"
                ),
                answer="36 square metres",
                working="Add the areas of the five plots.",
                difficulty="hard",
            ),
            verified=True,
        )],
        program_label="Academic Accelerate",
        total_minutes=25,
        classwork_minutes=25,
        homework_minutes=0,
    )


def _guided_number_line() -> WorkedExample:
    source = {
        "type": "number_line", "from": 0, "to": 1, "divisions": 3,
        "mark_at": [1 / 3], "label_at": ["1/3"],
    }
    safe, changed = reconcile_diagram_spec(
        source,
        "Mark the position of 1/3 on a number line between 0 and 1.",
        mode="student",
    )
    assert changed and safe["mark_at"] == []
    path = render_diagram(safe)
    assert path is not None and path.exists()
    answer_path = render_diagram(source)
    assert answer_path is not None and answer_path.exists()
    return WorkedExample(
        question="Mark the position of 1/3 on a number line between 0 and 1.",
        steps=[
            "Split the line into [[3]] equal parts.",
            "Place the mark after [[1]] jump.",
        ],
        answer="[[1/3]]",
        diagram_spec=safe,
        image_path=str(path),
        answer_image_path=str(answer_path),
    )


def _inspect(pdf_path: Path) -> None:
    pdf = PdfReader(str(pdf_path))
    assert len(pdf.pages) >= 4, "expected cover, lesson, practice and key pages"
    all_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Tree shadows practice" in all_text
    assert "Garden plan practice" in all_text
    assert "Equal groups practice" in all_text
    assert "Calculate 694 ÷ 5" in all_text
    assert "Image credits" not in all_text
    assert "Homework" not in all_text, (
        "a challenge-only booklet must not print an empty Homework section"
    )

    challenge_page = next(
        page for page in pdf.pages
        if "Five garden plots challenge" in (page.extract_text() or "")
    )
    assert "Answer:" in (challenge_page.extract_text() or ""), (
        "the noun 'plot' was mistaken for a draw instruction"
    )

    scene_pages = 0
    for page in pdf.pages:
        text = page.extract_text() or ""
        if not any(label in text for label in (
            "Tree shadows model",
            "Tree shadows practice",
            "Garden plan practice",
            "Equal groups practice",
            "Calculate 694 ÷ 5",
        )):
            continue
        assert page.images, f"context scene missing from page containing {text[:60]!r}"
        scene_pages += 1
    assert scene_pages >= 3, scene_pages

    assert SCENE_IMG_WIDTH > WE_SCENE_IMG_WIDTH
    assert SCENE_IMG_HEIGHT > WE_SCENE_IMG_HEIGHT


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = _booklet()
    garden_question = Question(
        question=(
            "A garden has 4 rectangular plots. A new plot is added. "
            "What is the total area?"
        ),
        answer="36 square metres",
        working="Add the five plot areas.",
    )
    assert answer_line_labels(garden_question) == ["Answer:"]
    draw_question = Question(
        question="Plot (3, 4) on the coordinate grid.",
        answer="Point at (3, 4)",
        working="Move 3 right and 4 up.",
    )
    assert answer_line_labels(draw_question) == []
    lesson_flowables = _lesson_flowables(
        _make_styles(), data.sections[0].teaching, data.year_level,
    )
    assert any(isinstance(item, KeepTogether) for item in lesson_flowables), (
        "lesson key points can split one bullet onto the following page"
    )
    model = data.sections[0].teaching.worked_example
    assert image_credits(data) == []
    model.image_attribution = "Licensed teaching image"
    assert image_credits(data) == ["Licensed teaching image"]
    model.image_attribution = None
    if args.output:
        target = args.output.resolve()
        render_pdf(data, target)
        _inspect(target)
        print(f"Contextual booklet render passed: {target}")
        return

    with tempfile.TemporaryDirectory(prefix="folio-contextual-booklet-") as raw:
        target = Path(raw) / "contextual-visual-system-demo.pdf"
        render_pdf(data, target)
        _inspect(target)
        print("Contextual booklet render passed")


if __name__ == "__main__":
    main()
