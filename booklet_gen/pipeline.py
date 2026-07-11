from __future__ import annotations

import logging
from typing import Optional

from .agents.llm_judge import LLMJudgeValidator
from .agents.outline_parser import OutlineParserAgent
from .agents.question_generator import QuestionGeneratorAgent
from .agents.validator import SympyValidator, ValidationResult
from .config import Config, load_config
from .llm import get_client
from .schemas import BookletData, Question, SubtopicOutput, ValidatedQuestion

log = logging.getLogger(__name__)


class BookletPipeline:
    def __init__(
        self,
        config: Optional[Config] = None,
        questions_per_subtopic: int = 5,
        max_generation_attempts: int = 2,
        client=None,
    ):
        self._config = config or load_config()
        self._client = client or get_client(self._config)
        self._parser = OutlineParserAgent(self._client, self._config.max_retries)
        self._generator = QuestionGeneratorAgent(
            self._client,
            max_retries=self._config.max_retries,
            questions_per_subtopic=questions_per_subtopic,
        )
        self._sympy = SympyValidator()
        self._judge = LLMJudgeValidator(self._client)
        self._max_generation_attempts = max_generation_attempts

    def run(self, description: str, student_name: str) -> BookletData:
        log.info("pipeline.start", extra={"description": description})
        outline = self._parser.parse(description)
        log.info(
            "pipeline.outline",
            extra={"subject": outline.subject, "year_level": outline.year_level,
                   "topics": len(outline.topics)},
        )

        sections: list[SubtopicOutput] = []
        for topic in outline.topics:
            for subtopic in topic.subtopics:
                validated = self._generate_and_validate(
                    outline.subject, outline.year_level, topic.name, subtopic,
                )
                total = len(validated)
                failed = sum(1 for v in validated if not v.verified)
                failure_rate = failed / total if total else 0.0
                log.info(
                    "pipeline.subtopic.done",
                    extra={"subject": outline.subject, "topic": topic.name,
                           "subtopic": subtopic.name, "total": total, "failed": failed,
                           "failure_rate": failure_rate},
                )
                sections.append(SubtopicOutput(
                    topic=topic.name,
                    subtopic=subtopic.name,
                    questions=validated,
                    failure_rate=failure_rate,
                ))

        return BookletData(
            subject=outline.subject,
            year_level=outline.year_level,
            student_name=student_name,
            sections=sections,
        )

    def _validate(self, subject: str, year_level: str, q: Question) -> ValidationResult:
        """Route to the right validator for the subject.

        - Mathematics: try sympy first. If sympy can't verify (no verifiable
          pattern detected), fall back to LLM-judge so we still get a signal.
        - Science / English: LLM-judge in a fresh context.
        """
        key = subject.strip().lower()
        if key in {"mathematics", "maths", "math"}:
            result = self._sympy.validate(q)
            if result.verified:
                return result
            # Only trust a sympy failure when it's a definitive equation
            # substitution failure — that path knows exactly what it disproved.
            # Everything else (no pattern found, no matching computation) is a
            # soft failure where sympy just couldn't parse the setup (common for
            # word problems), so fall back to the LLM-judge.
            if result.notes and "substitution" in result.notes:
                return result
            fallback = self._judge.validate(subject, year_level, q)
            fallback.notes = f"sympy inconclusive; {fallback.notes}"
            return fallback
        return self._judge.validate(subject, year_level, q)

    def _generate_and_validate(self, subject, year_level, topic_name, subtopic) -> list[ValidatedQuestion]:
        results: list[ValidatedQuestion] = []
        qs = self._generator.generate(subject, year_level, topic_name, subtopic)
        for q in qs.questions:
            result = self._validate(subject, year_level, q)
            retry_count = 0
            while not result.verified and retry_count < self._max_generation_attempts - 1:
                retry_count += 1
                log.info(
                    "pipeline.regenerate_single",
                    extra={"subject": subject, "subtopic": subtopic.name,
                           "retry": retry_count, "reason": result.notes},
                )
                fresh = self._generator.generate(subject, year_level, topic_name, subtopic)
                q = fresh.questions[0]
                result = self._validate(subject, year_level, q)
            results.append(ValidatedQuestion(
                question=q,
                verified=result.verified,
                validator_notes=result.notes,
                retry_count=retry_count,
            ))
        return results
