"""Render fixed booklet fixtures and verify printed brand output."""
from __future__ import annotations

import re
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pypdf

from booklet_gen import formatter as F
from booklet_gen.schemas import (
    BookletData,
    ExamPaper,
    ExamSection,
    Question,
    SubtopicOutput,
    SubtopicTeaching,
    ValidatedQuestion,
    WorkedExample,
)

LEGACY_BRAND = re.compile(r"Folio(?:AI|\s+AI)", re.IGNORECASE)
WORDMARK = re.compile(r"\bFOLIO\b")


class FixedDate:
    @classmethod
    def today(cls) -> date:
        return date(2025, 1, 2)


def question(text: str, answer: str, *, marks: int | None = None) -> ValidatedQuestion:
    return ValidatedQuestion(
        question=Question(
            question=text,
            answer=answer,
            working=answer,
            difficulty="medium",
            marks=marks,
        ),
        verified=True,
    )


def standard_fixture() -> BookletData:
    teaching = SubtopicTeaching(
        intro_paragraphs=["A fixed example explains the calculation."],
        key_points=["Add the two quantities."],
        worked_example=WorkedExample(
            question="What is 4 plus 8?",
            steps=["Add 4 and 8."],
            answer="12",
        ),
    )
    return BookletData(
        subject="Mathematics",
        year_level="Year 6",
        student_name="Jordan Lee",
        sections=[SubtopicOutput(
            topic="Number",
            subtopic="Addition",
            teaching=teaching,
            questions=[question("What is 6 plus 7?", "13")],
            homework_questions=[question("What is 9 plus 5?", "14")],
            estimated_minutes=10,
        )],
        recap_questions=[question("What is 2 plus 3?", "5")],
        challenge_questions=[question("What is 10 plus 11?", "21")],
    )


def exam_fixture() -> ExamPaper:
    return ExamPaper(
        subject="Mathematics Methods",
        year_level="Year 12",
        student_name="Jordan Lee",
        unit="Unit 3",
        reading_minutes=5,
        sections=[ExamSection(
            name="Section One",
            questions=[question("Differentiate y = 3x squared.", "6x", marks=2)],
            working_minutes=10,
        )],
    )


def visible_text(reader: pypdf.PdfReader) -> str:
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def check_pdf(label: str, path: Path) -> list[str]:
    reader = pypdf.PdfReader(str(path))
    text = visible_text(reader)
    metadata = {key: str(value) for key, value in (reader.metadata or {}).items()}
    failures: list[str] = []

    if LEGACY_BRAND.search(text):
        failures.append(f"{label}: visible text contains a legacy brand")
    if not WORDMARK.search(text):
        failures.append(f"{label}: visible text has no Folio wordmark")
    for key, value in metadata.items():
        if LEGACY_BRAND.search(value):
            failures.append(f"{label}: {key} metadata contains a legacy brand")
    for key in ("/Author", "/Creator"):
        if metadata.get(key) != F.PRINTED_BRAND:
            failures.append(
                f"{label}: {key} metadata is {metadata.get(key)!r}, expected {F.PRINTED_BRAND!r}"
            )

    print(f"{label}: {len(reader.pages)} pages")
    print(f"  visible Folio wordmark: {bool(WORDMARK.search(text))}")
    print(f"  metadata: {metadata}")
    return failures


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="folio-printed-brand-") as tmpdir:
        tmp = Path(tmpdir)
        with patch.object(F, "date", FixedDate), patch.dict(
            F.os.environ, {"FOLIO_COVER_BACKGROUND": ""}, clear=False
        ):
            standard = F.render_pdf(standard_fixture(), tmp / "standard.pdf")
            exam = F.render_exam_pdf(exam_fixture(), tmp / "exam.pdf")

        failures = check_pdf("standard", standard)
        failures.extend(check_pdf("exam", exam))

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
