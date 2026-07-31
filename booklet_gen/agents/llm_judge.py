from __future__ import annotations

import logging
from pydantic import BaseModel, ValidationError

from ..llm import LLMClient
from ..schemas import Question
from ._shared import load_prompt, extract_json
from .validator import ValidationResult, answers_conflict

log = logging.getLogger(__name__)

# Asking for the judge's own answer first, in the JSON it emits, is the whole
# mechanism: the model has to commit to a solution before it can write a
# verdict, so the verdict becomes a comparison rather than an impression.
_RESPONSE_SHAPE = (
    '{"results": [{"index": 0, "solved_answer": "...", "verified": true, '
    '"reason": "..."}, ...]}'
)


class _JudgeResponse(BaseModel):
    verified: bool
    reason: str = ""
    solved_answer: str = ""


def _cross_check(q: Question, verified: bool, solved: str, reason: str) -> ValidationResult:
    """Hold the judge to its own solve.

    A judge that works out one answer and then passes a different one has not
    graded anything, it has agreed. Where both are plain numbers we can settle
    that here for free, and withhold the mark.
    """
    if verified and answers_conflict(q.answer, solved):
        log.warning(
            "llm_judge.self_contradiction",
            extra={"proposed": (q.answer or "")[:40], "solved": (solved or "")[:40]},
        )
        return ValidationResult(
            False,
            f"llm-judge solved this as {solved.strip()} but passed the answer "
            f"{(q.answer or '').strip()}; mark withheld",
        )
    return ValidationResult(verified, reason)


class LLMJudgeValidator:
    """A separate LLM call in a fresh context grades each question.

    Fresh context = we build a new system/user pair per call and never share
    state with the generator. The API call itself is stateless, so the judge
    is grading someone else's work rather than self-checking.

    The judge is asked to solve each question itself before grading it, and its
    solution is compared against the proposed answer here in code. Without
    that, "verified" only ever meant the answer looked plausible.
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
            f"Proposed working: {q.working}\n\n"
            "Solve the question yourself first, then grade the proposed answer "
            "against your own solution."
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            user += (
                "\n\nReference material for grounding (real textbook/exam excerpts "
                "at this level, cross-check answers against these when relevant):\n\n"
                + joined
            )
        try:
            raw = self._client.complete(self._system, user, tier="strong", temperature=0.0)
            data = extract_json(raw)
            resp = _JudgeResponse.model_validate(data)
            result = _cross_check(
                q, resp.verified, resp.solved_answer, f"llm-judge: {resp.reason}",
            )
            log.info(
                "llm_judge.result",
                extra={"subject": subject, "verified": result.verified,
                       "solved": resp.solved_answer[:60],
                       "reason": (result.notes or "")[:200]},
            )
            return result
        except (ValueError, ValidationError) as e:
            log.warning("llm_judge.parse_error", extra={"error": str(e)[:200]})
            return ValidationResult(False, f"llm-judge parse error: {e}")
        except Exception as e:
            log.warning("llm_judge.error", extra={"error": str(e)[:200]})
            return ValidationResult(False, f"llm-judge error: {e}")

    def validate_batch(
        self,
        subject: str,
        year_level: str,
        questions: list[Question],
        reference_chunks: list[str] | None = None,
    ) -> list[ValidationResult] | None:
        """Grade a whole set of questions in ONE call, cutting API usage from N
        calls to 1 for a subtopic. Returns a list aligned to `questions`, or
        None if the batch call failed (so the caller can fall back to per-
        question grading)."""
        if not questions:
            return []
        blocks = []
        for i, q in enumerate(questions):
            blocks.append(
                f"[Question {i}]\n"
                f"Question: {q.question}\n"
                f"Proposed answer: {q.answer}\n"
                f"Proposed working: {q.working}"
            )
        user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n\n"
            "Grade EACH question below independently, to the same standard as a "
            "single question. For each one: solve it yourself from the question "
            "text, record your own answer in solved_answer, and only then decide "
            "whether the proposed answer is correct and appropriate for the year "
            "level. Grading a batch is not permission to skim it, and passing "
            "every question in the batch means you have stopped checking.\n\n"
            + "\n\n".join(blocks)
            + "\n\nReturn ONLY a JSON object of this exact shape, with one entry per "
            "question index above:\n"
            + _RESPONSE_SHAPE
        )
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            user += (
                "\n\nReference material for grounding (real textbook/exam excerpts "
                "at this level, cross-check answers against these when relevant):\n\n"
                + joined
            )
        try:
            raw = self._client.complete(self._system, user, tier="strong", temperature=0.0)
            data = extract_json(raw)
            by_index: dict[int, ValidationResult] = {}
            for item in data.get("results", []):
                try:
                    idx = int(item["index"])
                except (KeyError, ValueError, TypeError):
                    continue
                if not 0 <= idx < len(questions):
                    continue
                by_index[idx] = _cross_check(
                    questions[idx],
                    bool(item.get("verified")),
                    str(item.get("solved_answer", "")),
                    f"llm-judge(batch): {str(item.get('reason', ''))[:200]}",
                )
            # Align to input order. A missing verdict is treated as unverified
            # (safe: it just skips the check mark / triggers a regeneration).
            out = [
                by_index.get(i, ValidationResult(False, "llm-judge(batch): no verdict"))
                for i in range(len(questions))
            ]
            passed = sum(1 for r in out if r.verified)
            log.info("llm_judge.batch",
                     extra={"subject": subject, "count": len(questions),
                            "verified": passed})
            if len(out) >= 5 and passed == len(out):
                # Not an error, but the prompt tells the judge a clean sweep is
                # a warning sign, so it belongs in the logs rather than passing
                # unremarked.
                log.warning("llm_judge.batch_all_passed",
                            extra={"subject": subject, "count": len(out)})
            return out
        except Exception as e:
            log.warning("llm_judge.batch_error", extra={"error": str(e)[:200]})
            return None
