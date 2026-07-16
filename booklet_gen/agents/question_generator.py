from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import QuestionSet, Subtopic
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


_SUBJECT_PROMPT_FILES = {
    "mathematics": "question_generator_maths.txt",
    "maths": "question_generator_maths.txt",
    "math": "question_generator_maths.txt",
    "science": "question_generator_science.txt",
    "english": "question_generator_english.txt",
    "reasoning": "question_generator_reasoning.txt",
    "verbal reasoning": "question_generator_reasoning.txt",
    "quantitative reasoning": "question_generator_reasoning.txt",
}


def _prompt_file_for(subject: str) -> str:
    key = subject.strip().lower()
    if key not in _SUBJECT_PROMPT_FILES:
        raise ValueError(f"No question generator prompt configured for subject {subject!r}")
    return _SUBJECT_PROMPT_FILES[key]


class QuestionGeneratorAgent:
    def __init__(self, client: LLMClient, max_retries: int = 3, questions_per_subtopic: int = 5):
        self._client = client
        self._max_retries = max_retries
        self._n = questions_per_subtopic
        # Cache system prompts per subject so we don't reload on every subtopic.
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
        topic: str,
        subtopic: Subtopic,
        reference_chunks: list[str] | None = None,
    ) -> QuestionSet:
        system = self._system_prompt(subject)
        base_user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Topic: {topic}\n"
            f"Subtopic: {subtopic.name}\n"
            f"Target difficulty: {subtopic.difficulty_hint}\n"
            f"Question types: {', '.join(subtopic.question_types) or 'any suitable'}\n"
            f"Generate exactly {self._n} questions."
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            base_user += (
                "\n\nReference material (real textbook/exam excerpts at this level — "
                "use these to calibrate style, phrasing, and difficulty; do NOT copy "
                "questions verbatim):\n\n" + joined
            )
        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = base_user if not error_feedback else (
                f"{base_user}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object matching the schema."
            )
            log.info(
                "question_generator.attempt",
                extra={"attempt": attempt, "subject": subject, "subtopic": subtopic.name},
            )
            raw = self._client.complete(system, user, tier="strong", temperature=0.6)
            try:
                data = extract_json(raw)
                qs = QuestionSet.model_validate(data)
                if not qs.questions:
                    raise ValueError("empty questions array")
                log.info(
                    "question_generator.success",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name, "count": len(qs.questions)},
                )
                return qs
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning(
                    "question_generator.retry",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name, "error": error_feedback[:300]},
                )
        raise RuntimeError(
            f"Question generator failed for {subject}/{subtopic.name!r} after {self._max_retries} attempts: {error_feedback}"
        )
