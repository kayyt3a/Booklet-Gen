from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import Subtopic, SubtopicTeaching
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


_SUBJECT_PROMPT_FILES = {
    "mathematics": "intro_writer_maths.txt",
    "maths": "intro_writer_maths.txt",
    "math": "intro_writer_maths.txt",
    "science": "intro_writer_science.txt",
    "english": "intro_writer_english.txt",
    "reasoning": "intro_writer_reasoning.txt",
    "verbal reasoning": "intro_writer_reasoning.txt",
    "quantitative reasoning": "intro_writer_reasoning.txt",
}


def _prompt_file_for(subject: str) -> str:
    key = subject.strip().lower()
    if key not in _SUBJECT_PROMPT_FILES:
        raise ValueError(f"No intro writer prompt configured for subject {subject!r}")
    return _SUBJECT_PROMPT_FILES[key]


class IntroWriterAgent:
    """Produces a mini-lesson (intro + worked example) for a subtopic."""

    def __init__(self, client: LLMClient, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries
        self._system_by_subject: dict[str, str] = {}

    def _system_prompt(self, subject: str) -> str:
        key = subject.strip().lower()
        if key not in self._system_by_subject:
            self._system_by_subject[key] = load_prompt(_prompt_file_for(subject))
        return self._system_by_subject[key]

    def write(
        self,
        subject: str,
        year_level: str,
        topic: str,
        subtopic: Subtopic,
        reference_chunks: list[str] | None = None,
    ) -> SubtopicTeaching:
        system = self._system_prompt(subject)
        base_user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Topic: {topic}\n"
            f"Subtopic: {subtopic.name}\n"
            f"Difficulty focus: {subtopic.difficulty_hint}\n"
            "Write the mini-lesson JSON now."
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            base_user += (
                "\n\nReference material (real textbook/exam excerpts at this level — "
                "use these to calibrate voice, examples, and difficulty; do NOT copy "
                "verbatim):\n\n" + joined
            )
        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = base_user if not error_feedback else (
                f"{base_user}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object matching the schema."
            )
            log.info(
                "intro_writer.attempt",
                extra={"attempt": attempt, "subject": subject, "subtopic": subtopic.name},
            )
            raw = self._client.complete(system, user, tier="strong", temperature=0.5)
            try:
                data = extract_json(raw)
                teaching = SubtopicTeaching.model_validate(data)
                log.info(
                    "intro_writer.success",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name,
                           "paragraphs": len(teaching.intro_paragraphs),
                           "has_diagram": teaching.worked_example.diagram_spec is not None},
                )
                return teaching
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning(
                    "intro_writer.retry",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name, "error": error_feedback[:300]},
                )
        raise RuntimeError(
            f"Intro writer failed for {subject}/{subtopic.name!r} after {self._max_retries} attempts: {error_feedback}"
        )
