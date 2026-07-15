from __future__ import annotations

import logging
from typing import Optional

from .agents.challenge_generator import ChallengeGeneratorAgent
from .agents.intro_writer import IntroWriterAgent
from .agents.llm_judge import LLMJudgeValidator
from .agents.outline_parser import OutlineParserAgent
from .agents.question_generator import QuestionGeneratorAgent
from .agents.validator import SympyValidator, ValidationResult
from .config import Config, load_config
from .llm import get_client
from .rag import Retriever
from .schemas import (
    BookletData, Question, SubtopicOutput, SubtopicTeaching,
    ValidatedQuestion,
)

log = logging.getLogger(__name__)


class BookletPipeline:
    def __init__(
        self,
        config: Optional[Config] = None,
        questions_per_subtopic: int = 5,
        challenge_questions: int = 5,
        max_generation_attempts: int = 2,
        client=None,
        retriever: Optional[Retriever] = None,
    ):
        self._config = config or load_config()
        self._client = client or get_client(self._config)
        self._parser = OutlineParserAgent(self._client, self._config.max_retries)
        self._intro = IntroWriterAgent(self._client, self._config.max_retries)
        self._generator = QuestionGeneratorAgent(
            self._client,
            max_retries=self._config.max_retries,
            questions_per_subtopic=questions_per_subtopic,
        )
        self._challenger = ChallengeGeneratorAgent(self._client, self._config.max_retries)
        self._sympy = SympyValidator()
        self._judge = LLMJudgeValidator(self._client)
        self._max_generation_attempts = max_generation_attempts
        self._n_challenge = challenge_questions
        self._retriever = retriever if retriever is not None else Retriever()

    def run(self, description: str, student_name: str) -> BookletData:
        log.info("pipeline.start", extra={"description": description})
        outline = self._parser.parse(description)
        log.info(
            "pipeline.outline",
            extra={"subject": outline.subject, "year_level": outline.year_level,
                   "topics": len(outline.topics)},
        )

        sections: list[SubtopicOutput] = []
        covered: list[tuple[str, str]] = []
        rag_pool: list[str] = []

        for topic in outline.topics:
            for subtopic in topic.subtopics:
                chunks = self._retrieve(outline.subject, outline.year_level,
                                        topic.name, subtopic.name)
                rag_pool.extend(chunks)
                teaching = self._make_teaching(
                    outline.subject, outline.year_level, topic.name, subtopic, chunks,
                )
                validated = self._generate_and_validate(
                    outline.subject, outline.year_level, topic.name, subtopic, chunks,
                )
                total = len(validated)
                failed = sum(1 for v in validated if not v.verified)
                failure_rate = failed / total if total else 0.0
                log.info(
                    "pipeline.subtopic.done",
                    extra={"subject": outline.subject, "topic": topic.name,
                           "subtopic": subtopic.name, "total": total, "failed": failed,
                           "failure_rate": failure_rate,
                           "rag_chunks": len(chunks)},
                )
                sections.append(SubtopicOutput(
                    topic=topic.name,
                    subtopic=subtopic.name,
                    teaching=teaching,
                    questions=validated,
                    failure_rate=failure_rate,
                ))
                covered.append((topic.name, subtopic.name))

        challenge = self._build_challenge(
            outline.subject, outline.year_level, covered, rag_pool,
        )

        return BookletData(
            subject=outline.subject,
            year_level=outline.year_level,
            student_name=student_name,
            sections=sections,
            challenge_questions=challenge,
        )

    # ---------- RAG ----------

    def _retrieve(self, subject, year_level, topic, subtopic) -> list[str]:
        try:
            hits = self._retriever.retrieve(subject, year_level, topic, subtopic)
        except Exception as e:
            log.warning("pipeline.retrieve_failed", extra={"error": str(e)[:200]})
            return []
        if hits:
            log.info(
                "pipeline.retrieved",
                extra={"subject": subject, "subtopic": subtopic, "count": len(hits),
                       "sources": [h.source for h in hits]},
            )
        return [h.text for h in hits]

    # ---------- Teaching ----------

    def _make_teaching(
        self, subject, year_level, topic_name, subtopic, reference_chunks,
    ) -> SubtopicTeaching | None:
        try:
            teaching = self._intro.write(
                subject, year_level, topic_name, subtopic, reference_chunks,
            )
        except Exception as e:
            # Soft failure — booklet still renders without the mini-lesson.
            log.warning(
                "pipeline.intro_failed",
                extra={"subject": subject, "subtopic": subtopic.name, "error": str(e)[:200]},
            )
            return None
        # Render diagram for the worked example if requested.
        spec = teaching.worked_example.diagram_spec
        if spec:
            from .visuals import render_diagram
            try:
                path = render_diagram(spec)
            except Exception as e:
                log.warning("pipeline.worked_example_diagram_failed",
                            extra={"error": str(e)[:200]})
                path = None
            if path:
                log.info("pipeline.worked_example_diagram",
                         extra={"type": spec.get("type")})
                teaching.worked_example.image_path = str(path)
        return teaching

    # ---------- Validation ----------

    def _validate(
        self, subject: str, year_level: str, q: Question,
        reference_chunks: list[str] | None = None,
    ) -> ValidationResult:
        key = subject.strip().lower()
        if key in {"mathematics", "maths", "math"}:
            result = self._sympy.validate(q)
            if result.verified:
                return result
            if result.notes and "substitution" in result.notes:
                return result
            fallback = self._judge.validate(subject, year_level, q, reference_chunks)
            fallback.notes = f"sympy inconclusive; {fallback.notes}"
            return fallback
        return self._judge.validate(subject, year_level, q, reference_chunks)

    def _generate_and_validate(
        self, subject, year_level, topic_name, subtopic, reference_chunks,
    ) -> list[ValidatedQuestion]:
        results: list[ValidatedQuestion] = []
        qs = self._generator.generate(subject, year_level, topic_name, subtopic, reference_chunks)
        for q in qs.questions:
            result = self._validate(subject, year_level, q, reference_chunks)
            retry_count = 0
            while not result.verified and retry_count < self._max_generation_attempts - 1:
                retry_count += 1
                log.info(
                    "pipeline.regenerate_single",
                    extra={"subject": subject, "subtopic": subtopic.name,
                           "retry": retry_count, "reason": result.notes},
                )
                fresh = self._generator.generate(
                    subject, year_level, topic_name, subtopic, reference_chunks,
                )
                q = fresh.questions[0]
                result = self._validate(subject, year_level, q, reference_chunks)
            image_path, image_attr = self._resolve_visual(q)
            results.append(ValidatedQuestion(
                question=q,
                verified=result.verified,
                validator_notes=result.notes,
                retry_count=retry_count,
                image_path=str(image_path) if image_path else None,
                image_attribution=image_attr,
            ))
        return results

    # ---------- Challenge ----------

    def _build_challenge(
        self, subject, year_level, covered, reference_chunks,
    ) -> list[ValidatedQuestion]:
        if not covered or self._n_challenge <= 0:
            return []
        try:
            qs = self._challenger.generate(
                subject, year_level, covered, self._n_challenge, reference_chunks,
            )
        except Exception as e:
            log.warning("pipeline.challenge_failed", extra={"error": str(e)[:200]})
            return []
        results: list[ValidatedQuestion] = []
        for q in qs.questions:
            result = self._validate(subject, year_level, q, reference_chunks)
            retry_count = 0
            # For the challenge set we tolerate the LLM's first attempt more —
            # regeneration would cost another full-set call. Just record the
            # verification status.
            image_path, image_attr = self._resolve_visual(q)
            results.append(ValidatedQuestion(
                question=q,
                verified=result.verified,
                validator_notes=result.notes,
                retry_count=retry_count,
                image_path=str(image_path) if image_path else None,
                image_attribution=image_attr,
            ))
        return results

    # ---------- Visuals ----------

    def _resolve_visual(self, q):
        """Return (path, attribution) for whichever optional visual the LLM asked for."""
        if q.diagram_spec:
            from .visuals import render_diagram
            path = render_diagram(q.diagram_spec)
            if path:
                log.info("pipeline.diagram", extra={"type": q.diagram_spec.get("type")})
                return path, None
        if q.image_query:
            from .visuals import fetch_image
            path, attr = fetch_image(q.image_query)
            if path:
                log.info("pipeline.image", extra={"query": q.image_query, "attr": attr})
                return path, attr
            log.info("pipeline.image_missed", extra={"query": q.image_query})
        return None, None
