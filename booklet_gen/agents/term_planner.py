from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import TermPlan, TermWeek
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


class TermPlannerAgent:
    """Plans a term as a list of weekly topic foci with a difficulty ramp.

    Falls back to a simple deterministic ramp if the model call fails, so a
    term plan never dies on a bad planner response.
    """

    def __init__(self, client: LLMClient, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries
        self._system = load_prompt("term_planner.txt")

    def plan(
        self,
        program_label: str,
        subject: str,
        year_level: str,
        weeks: int,
        topic_hint: str | None = None,
    ) -> TermPlan:
        user = (
            f"Booklet type: {program_label}\n"
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Number of weeks: {weeks}\n"
        )
        if topic_hint:
            user += f"Focus the term around: {topic_hint}\n"
        user += f"\nProduce exactly {weeks} weeks."

        error = ""
        for attempt in range(1, self._max_retries + 1):
            msg = user if not error else (
                f"{user}\n\nYour previous attempt failed:\n{error}\nReturn corrected JSON."
            )
            try:
                raw = self._client.complete(self._system, msg, tier="strong", temperature=0.4)
                plan = TermPlan.model_validate(extract_json(raw))
                plan = self._normalise(plan, weeks)
                log.info("term_planner.success",
                         extra={"weeks": len(plan.weeks), "attempt": attempt})
                return plan
            except (ValueError, ValidationError) as e:
                error = str(e)
                log.warning("term_planner.retry", extra={"attempt": attempt, "error": error[:200]})
            except Exception as e:  # client/network failure: no point retrying the same call
                log.warning("term_planner.client_error", extra={"error": str(e)[:200]})
                break

        log.warning("term_planner.fallback")
        return self._fallback(subject, weeks, topic_hint)

    @staticmethod
    def _normalise(plan: TermPlan, weeks: int) -> TermPlan:
        """Trim/pad to exactly `weeks` and renumber 1..weeks."""
        ws = plan.weeks[:weeks]
        while len(ws) < weeks:
            ws.append(TermWeek(week=len(ws) + 1, focus="revision and mixed practice",
                               difficulty="hard", revision=True))
        for i, w in enumerate(ws, 1):
            w.week = i
        return TermPlan(weeks=ws)

    @staticmethod
    def _fallback(subject: str, weeks: int, topic_hint: str | None) -> TermPlan:
        base = topic_hint or f"{subject} core skills"
        out: list[TermWeek] = []
        for i in range(1, weeks + 1):
            if i > weeks - 2:
                out.append(TermWeek(week=i, focus="revision and mixed practice",
                                    difficulty="hard", revision=True))
            else:
                diff = "easy" if i <= weeks / 3 else ("medium" if i <= 2 * weeks / 3 else "hard")
                out.append(TermWeek(week=i, focus=f"{base} (part {i})", difficulty=diff))
        return TermPlan(weeks=out)
