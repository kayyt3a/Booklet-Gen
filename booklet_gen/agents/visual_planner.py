"""One batched visual-planning call for a subtopic's final question set."""
from __future__ import annotations

import json
import logging
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from ..schemas import Question
from ..visual_policy import (PRIORITIES, VISUAL_KINDS, deterministic_priority,
                             stronger_priority, student_safe_spec)
from ._shared import extract_json, load_prompt

log = logging.getLogger(__name__)


class VisualPlanItem(BaseModel):
    item_index: int
    priority: Literal["required", "strong", "helpful", "text-only"] = "text-only"
    visual_kind: Literal["diagram", "scene", "image", "none"] = "none"
    diagram_spec: Optional[dict] = None
    scene_spec: Optional[dict] = None
    image_query: Optional[str] = None
    reason: str = ""


class VisualPlan(BaseModel):
    plans: list[VisualPlanItem] = Field(default_factory=list)


class VisualPlannerAgent:
    """Plans all visuals in one subtopic in exactly one LLM call.

    The user payload intentionally contains no proposed answer or working.
    They are irrelevant to representation choice and would let the planner
    accidentally encode an answer into a student-facing figure.
    """

    def __init__(self, client):
        self._client = client
        try:
            self._system = load_prompt("visual_planner.txt")
        except OSError:
            # Keeps older installations and isolated deterministic fixtures
            # usable while the planner prompt is deployed with the package.
            self._system = (
                "Choose instructionally useful visuals for student questions. "
                "Return JSON with a plans list. Use the supplied item_index. "
                "Never calculate or expose an unknown value."
            )

    def plan(self, subject: str, year_level: str, topic: str, subtopic: str,
             questions: list[Question]) -> list[VisualPlanItem]:
        if not questions:
            return []
        items = []
        for i, q in enumerate(questions):
            # Question text is the complete pedagogical input. Existing specs
            # are preserved deterministically when the plan is merged, so the
            # model does not need them and cannot copy a derived value from a
            # generator-authored spec.
            item = {
                "item_index": i,
                "question": q.question,
            }
            items.append(item)
        user = (
            f"Subject: {subject}\nYear level: {year_level}\n"
            f"Topic: {topic}\nSubtopic: {subtopic}\n\n"
            "Plan the most instructionally useful visual for each final "
            "student question in this single batch. Preserve an existing "
            "sound spec. Return one plan per item_index.\n\n"
            + json.dumps({"items": items}, ensure_ascii=False)
        )
        try:
            raw = self._client.complete(
                self._system, user, tier="strong", temperature=0.0,
            )
            parsed = VisualPlan.model_validate(extract_json(raw))
        except Exception as exc:
            # Visual planning is an enhancement, not a reason to lose a whole
            # subtopic. Deterministic floors below still enforce figure
            # dependencies and retain every existing generator spec.
            log.warning("visual_planner.failed", extra={"error": str(exc)[:200]})
            parsed = VisualPlan()

        by_index = {p.item_index: p for p in parsed.plans
                    if 0 <= p.item_index < len(questions)}
        out = []
        for i, q in enumerate(questions):
            item = by_index.get(i) or VisualPlanItem(item_index=i)
            floor = deterministic_priority(q.question, subject, subtopic)
            item.priority = stronger_priority(item.priority, floor)
            if item.visual_kind not in VISUAL_KINDS:
                item.visual_kind = "none"
            out.append(item)
        return out


def _usable_diagram_spec(spec: dict | None, question_text: str) -> dict | None:
    """Return a reconciled spec only when the real renderer accepts it."""
    if not isinstance(spec, dict):
        return None
    from .consistency import reconcile_diagram_spec
    from ..visuals.diagrams import SUPPORTED_TYPES, render_diagram

    if spec.get("type") not in SUPPORTED_TYPES:
        return None
    clean, _ = reconcile_diagram_spec(spec, question_text)
    clean = student_safe_spec(clean, mode="student")
    return clean if render_diagram(clean) is not None else None


def apply_visual_plan(questions: list[Question], plans: list[VisualPlanItem]) -> None:
    """Apply a plan while preserving only an existing supported visual."""
    by_index = {p.item_index: p for p in plans}
    for i, q in enumerate(questions):
        p = by_index.get(i)
        if p is None:
            q.visual_priority = stronger_priority(
                q.visual_priority,
                deterministic_priority(q.question),
            )
            continue
        q.visual_priority = p.priority
        q.visual_reason = p.reason.strip() or None
        # A malformed generator spec used to block a sound planner fallback,
        # then fail at render time and leave the question text-only. Preserve
        # existing work only after its type contract is known to be usable.
        if q.diagram_spec:
            q.diagram_spec = _usable_diagram_spec(q.diagram_spec, q.question)
        if q.scene_spec:
            from ..visuals.scene_specs import validate_scene_spec
            if not validate_scene_spec(q.scene_spec):
                q.scene_spec = None
        # Normalise a model conflict deterministically. Formal diagrams take
        # precedence over contextual scenes, which take precedence over an
        # editorial image query disabled in clean-room production.
        if q.diagram_spec:
            q.scene_spec = None
            q.image_query = None
        elif q.scene_spec:
            q.image_query = None
        if q.diagram_spec or q.scene_spec or q.image_query:
            continue
        if p.visual_kind == "diagram" and p.diagram_spec:
            q.diagram_spec = _usable_diagram_spec(p.diagram_spec, q.question)
        elif p.visual_kind == "scene" and p.scene_spec:
            from ..visuals.scene_specs import validate_scene_spec
            if validate_scene_spec(p.scene_spec):
                q.scene_spec = p.scene_spec
        elif p.visual_kind == "image" and p.image_query:
            q.image_query = p.image_query.strip() or None
