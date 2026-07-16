from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import QuestionSet
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


_SUBJECT_PROMPT_FILES = {
    "mathematics": "challenge_generator_maths.txt",
    "maths": "challenge_generator_maths.txt",
    "math": "challenge_generator_maths.txt",
    "science": "challenge_generator_science.txt",
    "english": "challenge_generator_english.txt",
    "reasoning": "challenge_generator_reasoning.txt",
    "verbal reasoning": "challenge_generator_reasoning.txt",
    "quantitative reasoning": "challenge_generator_reasoning.txt",
}


def _prompt_file_for(subject: str) -> str:
    key = subject.strip().lower()
    if key not in _SUBJECT_PROMPT_FILES:
        raise ValueError(f"No challenge generator prompt configured for subject {subject!r}")
    return _SUBJECT_PROMPT_FILES[key]


class ChallengeGeneratorAgent:
    """Generates the cumulative harder end-of-booklet set."""

    def __init__(self, client: LLMClient, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries
        self._system_by_subject: dict[str, str] = {}

    def _system_prompt(self, subject: str) -> str:
        key = subject.strip().lower()
        if key not in self._system_by_subject:
            self._system_by_subject[key] = load_prompt(_prompt_file_for(subject))
        return self._system_by_subject[key]

    def generate(
        self,
        subject: str,
        year_level: str,
        covered: list[tuple[str, str]],
        n_questions: int,
        reference_chunks: list[str] | None = None,
    ) -> QuestionSet:
        """`covered` is a list of (topic, subtopic) pairs the student worked through."""
        system = self._system_prompt(subject)
        covered_lines = "\n".join(f"- {t} / {s}" for t, s in covered)
        base_user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Subtopics the student has just practiced:\n{covered_lines}\n\n"
            f"Generate exactly {n_questions} cumulative challenge questions."
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks[:6])
            base_user += (
                "\n\nReference material (real textbook/exam excerpts at this level — "
                "for calibration only; do NOT copy verbatim):\n\n" + joined
            )
        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = base_user if not error_feedback else (
                f"{base_user}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object matching the schema."
            )
            log.info(
                "challenge_generator.attempt",
                extra={"attempt": attempt, "subject": subject, "n": n_questions},
            )
            raw = self._client.complete(system, user, tier="strong", temperature=0.7)
            try:
                data = extract_json(raw)
                qs = QuestionSet.model_validate(data)
                if not qs.questions:
                    raise ValueError("empty questions array")
                log.info(
                    "challenge_generator.success",
                    extra={"attempt": attempt, "subject": subject,
                           "count": len(qs.questions)},
                )
                return qs
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning(
                    "challenge_generator.retry",
                    extra={"attempt": attempt, "subject": subject,
                           "error": error_feedback[:300]},
                )
        raise RuntimeError(
            f"Challenge generator failed for {subject} after {self._max_retries} attempts: {error_feedback}"
        )
