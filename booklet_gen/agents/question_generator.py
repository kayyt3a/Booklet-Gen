from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import QuestionSet, Subtopic
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


class QuestionGeneratorAgent:
    def __init__(self, client: LLMClient, max_retries: int = 3, questions_per_subtopic: int = 5):
        self._client = client
        self._max_retries = max_retries
        self._n = questions_per_subtopic
        self._system = load_prompt("question_generator_maths.txt")

    def generate(self, year_level: str, topic: str, subtopic: Subtopic) -> QuestionSet:
        base_user = (
            f"Year level: {year_level}\n"
            f"Topic: {topic}\n"
            f"Subtopic: {subtopic.name}\n"
            f"Target difficulty: {subtopic.difficulty_hint}\n"
            f"Question types: {', '.join(subtopic.question_types) or 'any suitable'}\n"
            f"Generate exactly {self._n} questions."
        )
        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = base_user if not error_feedback else (
                f"{base_user}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object matching the schema."
            )
            log.info(
                "question_generator.attempt",
                extra={"attempt": attempt, "subtopic": subtopic.name},
            )
            raw = self._client.complete(self._system, user, tier="strong", temperature=0.6)
            try:
                data = extract_json(raw)
                qs = QuestionSet.model_validate(data)
                if not qs.questions:
                    raise ValueError("empty questions array")
                log.info(
                    "question_generator.success",
                    extra={"attempt": attempt, "subtopic": subtopic.name, "count": len(qs.questions)},
                )
                return qs
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning(
                    "question_generator.retry",
                    extra={"attempt": attempt, "subtopic": subtopic.name, "error": error_feedback[:300]},
                )
        raise RuntimeError(
            f"Question generator failed for {subtopic.name!r} after {self._max_retries} attempts: {error_feedback}"
        )
