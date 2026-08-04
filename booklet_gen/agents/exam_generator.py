"""Generates one section of an exam paper (calculator-free or -assumed).

Unlike the booklet question generator this works a whole section at a time and
targets a mark total rather than a question count, because an exam is specified
by marks and time, not by "5 questions".
"""
from __future__ import annotations

import logging

from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import ExamQuestionDraft
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)

_PROMPT_FILES = {
    "mathematics methods": "exam_generator_methods.txt",
    "methods": "exam_generator_methods.txt",
}


def _prompt_file_for(subject: str) -> str:
    key = subject.strip().lower()
    if key not in _PROMPT_FILES:
        raise ValueError(f"No exam generator prompt configured for subject {subject!r}")
    return _PROMPT_FILES[key]


class ExamGeneratorAgent:
    def __init__(self, client: LLMClient, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries
        self._system_by_subject: dict[str, str] = {}

    def _system_prompt(self, subject: str) -> str:
        key = subject.strip().lower()
        if key not in self._system_by_subject:
            self._system_by_subject[key] = load_prompt(_prompt_file_for(subject))
        return self._system_by_subject[key]

    def generate_section(
        self,
        subject: str,
        year_level: str,
        section_name: str,
        calculator_allowed: bool,
        target_marks: int,
        topic_focus: str | None = None,
        reference_chunks: list[str] | None = None,
    ) -> ExamQuestionDraft:
        system = self._system_prompt(subject)
        mode = "CALCULATOR-ASSUMED" if calculator_allowed else "CALCULATOR-FREE"
        base_user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Section: {section_name}\n"
            f"Calculator status: {mode}\n"
            f"Target total marks for this section: {target_marks}\n"
        )
        if topic_focus:
            base_user += (
                f"Weight the section towards: {topic_focus} (still include a "
                "spread of other syllabus areas).\n"
            )
        base_user += (
            "Write a set of questions whose marks sum to approximately the "
            "target. Order them from shorter to longer, as a real paper does."
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            base_user += (
                "\n\nReference material (real WACE exam excerpts and marking keys "
                "at this level, use these to calibrate style, phrasing, mark "
                "allocation, and difficulty; do NOT copy questions verbatim):\n\n"
                + joined
            )

        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = base_user if not error_feedback else (
                f"{base_user}\n\nYour previous attempt failed validation:\n"
                f"{error_feedback}\nReturn a corrected JSON object matching the schema."
            )
            log.info("exam_generator.attempt",
                     extra={"attempt": attempt, "section": section_name,
                            "target_marks": target_marks})
            raw = self._client.complete(system, user, tier="strong", temperature=0.6)
            try:
                draft = ExamQuestionDraft.model_validate(extract_json(raw))
                if not draft.questions:
                    raise ValueError("empty questions array")
                missing = [q.question[:40] for q in draft.questions if not q.marks]
                if missing:
                    raise ValueError(f"questions missing a marks value: {missing}")
                log.info("exam_generator.success",
                         extra={"attempt": attempt, "section": section_name,
                                "count": len(draft.questions),
                                "marks": sum(q.marks or 0 for q in draft.questions)})
                return draft
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning("exam_generator.retry",
                            extra={"attempt": attempt, "section": section_name,
                                   "error": error_feedback[:300]})
        raise RuntimeError(
            f"Exam generator failed for {section_name!r} after "
            f"{self._max_retries} attempts: {error_feedback}"
        )
