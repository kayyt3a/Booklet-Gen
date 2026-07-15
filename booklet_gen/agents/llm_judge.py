from __future__ import annotations

import logging
from pydantic import BaseModel, ValidationError

from ..llm import LLMClient
from ..schemas import Question
from ._shared import load_prompt, extract_json
from .validator import ValidationResult

log = logging.getLogger(__name__)


class _JudgeResponse(BaseModel):
    verified: bool
    reason: str = ""


class LLMJudgeValidator:
    """A separate LLM call in a fresh context grades each question.

    Fresh context = we build a new system/user pair per call and never share
    state with the generator. The API call itself is stateless, so the judge
    is grading someone else's work rather than self-checking.
    """

    def __init__(self, client: LLMClient):
        self._client = client
        self._system = load_prompt("validator_llm_judge.txt")

    def validate(
        self,
        subject: str,
        year_level: str,
        q: Question,
        reference_chunks: list[str] | None = None,
    ) -> ValidationResult:
        user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Question: {q.question}\n"
            f"Proposed answer: {q.answer}\n"
            f"Proposed working: {q.working}"
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            user += (
                "\n\nReference material for grounding (real textbook/exam excerpts "
                "at this level — cross-check answers against these when relevant):\n\n"
                + joined
            )
        try:
            raw = self._client.complete(self._system, user, tier="strong", temperature=0.0)
            data = extract_json(raw)
            resp = _JudgeResponse.model_validate(data)
            log.info(
                "llm_judge.result",
                extra={"subject": subject, "verified": resp.verified, "reason": resp.reason[:200]},
            )
            return ValidationResult(verified=resp.verified, notes=f"llm-judge: {resp.reason}")
        except (ValueError, ValidationError) as e:
            log.warning("llm_judge.parse_error", extra={"error": str(e)[:200]})
            return ValidationResult(False, f"llm-judge parse error: {e}")
        except Exception as e:
            log.warning("llm_judge.error", extra={"error": str(e)[:200]})
            return ValidationResult(False, f"llm-judge error: {e}")
