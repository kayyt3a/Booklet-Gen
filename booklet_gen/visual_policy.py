"""Deterministic policy around the LLM visual planner.

The planner chooses a useful representation. This module owns the rules that
cannot be left to a model: a question that depends on a figure must receive a
real rendered file, student algorithms must never print their answers, and
coverage is measured from render results rather than optimistic JSON specs.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable


PRIORITIES = ("required", "strong", "helpful", "text-only")
VISUAL_KINDS = ("diagram", "scene", "image", "none")

# These questions cannot be answered as written without the visual.
_FIGURE_DEPENDENT = re.compile(
    r"\b(?:in the (?:diagram|figure|graph|chart|table|picture)|"
    r"from the (?:diagram|figure|graph|chart|table|picture)|"
    r"this (?:diagram|figure|graph|chart|shape|solid|object|pattern)|"
    r"the (?:diagram|figure|graph|chart|shape|solid|object|pattern) (?:above|"
    r"below)|"
    r"(?:the|this) (?:diagram|figure|graph|chart|shape|solid|object|pattern|"
    r"picture) (?:shown|pictured|illustrated)(?: (?:above|below))?|"
    r"(?:shown|pictured|illustrated) (?:in|on|by) (?:the|this) "
    r"(?:diagram|figure|graph|chart|table|picture|shape|solid|object|pattern))\b",
    re.IGNORECASE,
)

# The representation is the content, not decoration. A bare sentence cannot
# faithfully ask these question families.
_INHERENTLY_VISUAL = re.compile(
    r"\b(?:similar triangles?|scale drawing|transformation|reflection|"
    r"rotation|translation|symmetr(?:y|ical)|net of|angle|coordinates?|"
    r"number line|pictograph|bar graph|line graph|scatter plot|histogram|"
    r"stem.and.leaf|venn diagram|two.way table|frequency table|spinner|"
    r"clock face|read (?:the |this )?(?:ruler|scale|thermometer|measuring "
    r"jug)|column (?:addition|subtraction)|long multiplication|short "
    r"division|place.value blocks?)\b",
    re.IGNORECASE,
)

_STRONGLY_HELPED = re.compile(
    r"\b(?:shadow|tree|flagpole|ladder|ramp|building|shelf|bookshelf|ribbon|"
    r"race track|recipe|ratio|proportion|fraction|decimal|percentage|area|"
    r"perimeter|volume|capacity|probability|array|equal groups?|timeline|"
    r"life cycle|circuit|forces?|food chain|map|floor plan)\b",
    re.IGNORECASE,
)

_STUDENT_ALGORITHMS = frozenset({
    "column_arithmetic", "long_multiplication", "short_division",
})

_CALCULATION = re.compile(
    r"\b(?:calculate|work\s+out|evaluate)\s+"
    r"([0-9][0-9,]*)\s*([+\-*x×÷/])\s*([0-9][0-9,]*)\b",
    re.IGNORECASE,
)
_NUMBER_LINE_BUILD = re.compile(
    r"\b(?:mark|place|plot|show|locate)\b.{0,100}\bnumber\s+line\b"
    r"|\bnumber\s+line\b.{0,100}\b(?:mark|place|plot)\b"
    r"|\bwhere\s+is\b.{0,80}\blocated\b",
    re.IGNORECASE,
)
_SIMPLE_FRACTION = re.compile(r"\b(\d{1,2})\s*[/⁄]\s*(\d{1,2})\b")


@dataclass(frozen=True)
class Coverage:
    total: int
    rendered: int
    eligible: int
    eligible_rendered: int
    required: int
    required_rendered: int

    @property
    def eligible_rate(self) -> float:
        return self.eligible_rendered / self.eligible if self.eligible else 1.0

    @property
    def required_rate(self) -> float:
        return self.required_rendered / self.required if self.required else 1.0


@dataclass(frozen=True)
class CoveragePolicyResult:
    target: int
    rendered: int
    shortfall: int

    @property
    def met(self) -> bool:
        return self.shortfall == 0


def deterministic_priority(question_text: str, subject: str = "",
                           subtopic: str = "") -> str:
    """Return the non-negotiable visual floor for one question.

    This is deliberately conservative. The LLM can elevate a question above
    this floor, but it cannot call an explicitly figure-dependent item
    text-only.
    """
    text = " ".join((question_text or "", subtopic or ""))
    if _FIGURE_DEPENDENT.search(text) or _INHERENTLY_VISUAL.search(text):
        return "required"
    if _STRONGLY_HELPED.search(text):
        return "strong"
    return "text-only"


def stronger_priority(planned: str | None, floor: str) -> str:
    """Combine a planner decision with the deterministic safety floor."""
    rank = {"text-only": 0, "helpful": 1, "strong": 2, "required": 3}
    planned = planned if planned in rank else "text-only"
    floor = floor if floor in rank else "text-only"
    return planned if rank[planned] >= rank[floor] else floor


def deterministic_diagram_spec(question_text: str, subject: str = "",
                               subtopic: str = "") -> dict | None:
    """Build safe formal diagrams for question forms with one exact parse.

    This fallback reads only the printed question. It never sees the answer or
    working, and it handles only layouts whose operands or fraction are stated
    explicitly. Ambiguous word problems remain the visual planner's job.
    """
    text = question_text or ""
    maths = (subject or "").strip().lower() in {
        "mathematics", "maths", "math", "",
    }
    if not maths:
        return None
    match = _CALCULATION.search(text)
    if match:
        top = int(match.group(1).replace(",", ""))
        bottom = int(match.group(3).replace(",", ""))
        operation = match.group(2).lower()
        if operation in {"+", "-"} and max(top, bottom) >= 10:
            return {
                "type": "column_arithmetic", "top": top, "bottom": bottom,
                "operation": operation, "show_answer": False,
            }
        if operation in {"÷", "/"} and top >= 10 and 2 <= bottom <= 12:
            return {
                "type": "short_division", "dividend": top,
                "divisor": bottom, "show_answer": False,
            }
        if operation in {"*", "x", "×"} and top >= 10 and 1 <= bottom <= 99:
            return {
                "type": "long_multiplication", "top": top,
                "bottom": bottom, "show_answer": False,
            }

    fraction = _SIMPLE_FRACTION.search(text)
    if "number line" in text.lower() and fraction and _NUMBER_LINE_BUILD.search(text):
        denominator = int(fraction.group(2))
        if 2 <= denominator <= 20:
            return {
                "type": "number_line", "from": 0, "to": 1,
                "divisions": denominator, "mark_at": [], "label_at": [],
            }
    return None


def student_safe_spec(spec: dict | None, mode: str = "student") -> dict | None:
    """Copy a spec and remove answer-revealing student algorithm settings."""
    if not isinstance(spec, dict):
        return spec
    out = dict(spec)
    if mode == "student" and str(out.get("type", "")).lower() in _STUDENT_ALGORITHMS:
        out["show_answer"] = False
    return out


def requires_rendered_visual(question) -> bool:
    return getattr(question, "visual_priority", "text-only") == "required"


def rendered_visual_coverage(items: Iterable) -> Coverage:
    """Measure coverage after rendering, from image_path rather than specs.

    Accepts ValidatedQuestion objects or objects exposing a `question` and
    `image_path` attribute. It is suitable for a section, challenge or exam.
    """
    total = rendered = eligible = eligible_rendered = required = required_rendered = 0
    for item in items:
        q = getattr(item, "question", item)
        path = getattr(item, "image_path", None)
        has_render = bool(path)
        priority = getattr(q, "visual_priority", "text-only")
        total += 1
        rendered += int(has_render)
        if priority != "text-only":
            eligible += 1
            eligible_rendered += int(has_render)
        if priority == "required":
            required += 1
            required_rendered += int(has_render)
    return Coverage(total, rendered, eligible, eligible_rendered,
                    required, required_rendered)


def visual_coverage_policy(items: Iterable) -> CoveragePolicyResult:
    """Evaluate the minimum useful-render policy for one final set.

    Required items need a render, strong items need at least half, and helpful
    items retain the established one-third floor with two where available.
    The calculation uses actual image paths, never optimistic specs.
    """
    rows = list(items)
    counts = {priority: 0 for priority in PRIORITIES}
    renders = {priority: 0 for priority in PRIORITIES}
    for item in rows:
        q = getattr(item, "question", item)
        priority = getattr(q, "visual_priority", "text-only")
        if priority not in counts:
            priority = "text-only"
        counts[priority] += 1
        if priority != "text-only" and bool(getattr(item, "image_path", None)):
            renders[priority] += 1
    strong_target = math.ceil(counts["strong"] / 2)
    helpful_target = 0
    if counts["helpful"]:
        helpful_target = min(
            counts["helpful"], max(2, math.ceil(counts["helpful"] / 3)),
        )
    target = counts["required"] + strong_target + helpful_target
    satisfied = (
        min(renders["required"], counts["required"])
        + min(renders["strong"], strong_target)
        + min(renders["helpful"], helpful_target)
    )
    return CoveragePolicyResult(
        target=target,
        rendered=satisfied,
        shortfall=max(0, target - satisfied),
    )
