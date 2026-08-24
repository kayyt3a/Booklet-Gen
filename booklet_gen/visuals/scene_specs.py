"""Validation and canonicalisation for Folio-owned contextual scenes.

Scene specifications contain semantic facts only. The model never chooses
coordinates, colours, fonts, or image paths. This module keeps that boundary
small enough to audit before a scene reaches a child.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any


SCENE_VERSION = 1
SUPPORTED_SCENE_TEMPLATES = frozenset({
    "shadow_similarity",
    "ladder_wall",
    "shelves",
    "ribbon_measure",
    "race_progress",
    "shopping",
    "equal_groups_scene",
    "scoreboard",
    "garden",
    "timeline",
    "science_process",
    "force_scene",
    "circuit",
    "particle_model",
    "life_cycle",
    "reasoning_sequence",
    "logic_grid",
})

_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_ $%+*/=.,:;()?'\"#&/\-]+$")
_UNITS = frozenset({
    "", "mm", "cm", "m", "km", "mL", "L", "g", "kg", "s", "min",
    "h", "$", "c", "%", "degrees",
})


def _text(value: Any, *, name: str, limit: int = 42, empty: bool = False) -> str:
    value = str(value or "").strip()
    if not value and not empty:
        raise ValueError(f"{name} is required")
    if len(value) > limit:
        raise ValueError(f"{name} is too long")
    if value and not _SAFE_TEXT.fullmatch(value):
        raise ValueError(f"{name} contains unsupported characters")
    return value


def _number(value: Any, *, name: str, zero: bool = False,
            maximum: float = 1_000_000) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result) or result < (0 if zero else 1e-9):
        raise ValueError(f"{name} is out of range")
    if result > maximum:
        raise ValueError(f"{name} is out of range")
    return int(result) if result.is_integer() else result


def _maybe_number(value: Any, *, name: str, zero: bool = False):
    if value is None:
        return None
    return _number(value, name=name, zero=zero)


def _list(value: Any, *, name: str, low: int, high: int) -> list:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise ValueError(f"{name} must contain {low} to {high} items")
    return value


def _unknown(spec: dict, allowed: set[str]) -> dict | None:
    raw = spec.get("unknown")
    if raw in (None, {}, []):
        return None
    if not isinstance(raw, dict):
        raise ValueError("unknown must be an object")
    measure = _text(raw.get("measure"), name="unknown.measure", limit=24)
    if measure not in allowed:
        raise ValueError(f"unsupported unknown measure: {measure}")
    symbol = _text(raw.get("symbol") or "x", name="unknown.symbol", limit=1)
    if symbol not in {"x", "?"}:
        raise ValueError("unknown symbol must be x or ?")
    out = {"measure": measure, "symbol": symbol}
    if raw.get("object_id") is not None:
        out["object_id"] = _text(raw.get("object_id"), name="unknown.object_id",
                                  limit=24)
    return out


def _base(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("scene spec must be an object")
    template = _text(spec.get("template"), name="template", limit=32)
    if template not in SUPPORTED_SCENE_TEMPLATES:
        raise ValueError(f"unsupported scene template: {template}")
    version = int(spec.get("version", SCENE_VERSION))
    if version != SCENE_VERSION:
        raise ValueError(f"unsupported scene version: {version}")
    unit = _text(spec.get("unit", ""), name="unit", limit=8, empty=True)
    if unit not in _UNITS:
        raise ValueError(f"unsupported unit: {unit}")
    return {"template": template, "version": version, "unit": unit}


def _normalise_shadow(spec: dict, out: dict) -> None:
    objects = _list(spec.get("objects"), name="objects", low=2, high=2)
    seen = set()
    clean = []
    for index, raw in enumerate(objects):
        if not isinstance(raw, dict):
            raise ValueError("each shadow object must be an object")
        object_id = _text(raw.get("id") or f"object{index + 1}",
                          name="object id", limit=24)
        if object_id in seen:
            raise ValueError("scene object ids must be unique")
        seen.add(object_id)
        kind = _text(raw.get("kind") or "tree", name="object kind", limit=16)
        if kind not in {"tree", "building", "flagpole"}:
            raise ValueError(f"unsupported shadow object: {kind}")
        clean.append({
            "id": object_id,
            "kind": kind,
            "height": _maybe_number(raw.get("height"), name="height"),
            "shadow": _maybe_number(raw.get("shadow"), name="shadow"),
        })
    unknown = _unknown(spec, {"height", "shadow"})
    missing = [(obj["id"], key) for obj in clean for key in ("height", "shadow")
               if obj[key] is None]
    if len(missing) > 1:
        raise ValueError("shadow scene supports at most one unknown")
    if missing:
        if not unknown or (unknown.get("object_id"), unknown["measure"]) != missing[0]:
            raise ValueError("unknown must identify the missing shadow measure")
    elif unknown:
        raise ValueError("unknown measure must be null, never hidden as a solved value")
    if not missing:
        ratios = [float(obj["height"]) / float(obj["shadow"]) for obj in clean]
        if abs(ratios[0] - ratios[1]) > 1e-6:
            raise ValueError("shadow objects must form similar triangles")
    out.update(objects=clean, unknown=unknown)


def _normalise_ladder(spec: dict, out: dict) -> None:
    measures = {
        key: _maybe_number(spec.get(key), name=key)
        for key in ("height", "base", "ladder")
    }
    angle = _maybe_number(spec.get("angle"), name="angle")
    if angle is not None and not 0 < float(angle) < 90:
        raise ValueError("ladder angle must be acute")
    unknown = _unknown(spec, {"height", "base", "ladder", "angle"})
    missing = [key for key, value in measures.items() if value is None]
    if angle is None and unknown and unknown["measure"] == "angle":
        pass
    elif len(missing) > 1:
        raise ValueError("ladder scene needs at least two known side lengths")
    if missing and (not unknown or unknown["measure"] != missing[0]):
        raise ValueError("unknown must identify the missing ladder measure")
    if unknown and unknown["measure"] in measures and measures[unknown["measure"]] is not None:
        raise ValueError("unknown ladder measure must be null")
    if all(value is not None for value in measures.values()):
        if abs(float(measures["height"]) ** 2 + float(measures["base"]) ** 2
               - float(measures["ladder"]) ** 2) > 1e-5:
            raise ValueError("ladder sides do not form a right triangle")
    out.update(measures, angle=angle, unknown=unknown,
               object=_text(spec.get("object") or "ladder", name="object", limit=16))


def _normalise_named_items(spec: dict, out: dict, *, field: str,
                           allowed_unknowns: set[str],
                           low: int = 2, high: int = 8) -> None:
    rows = _list(spec.get(field), name=field, low=low, high=high)
    clean = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError(f"each {field} entry must be an object")
        entry = {
            "label": _text(raw.get("label") or str(index + 1),
                           name=f"{field} label", limit=26),
        }
        if raw.get("value") is not None:
            entry["value"] = _number(raw.get("value"), name=f"{field} value", zero=True)
        if raw.get("count") is not None:
            entry["count"] = int(_number(raw.get("count"), name=f"{field} count", zero=True,
                                         maximum=1000))
        if raw.get("price") is not None:
            entry["price"] = _number(raw.get("price"), name=f"{field} price", zero=True)
        if raw.get("kind") is not None:
            entry["kind"] = _text(raw.get("kind"), name=f"{field} kind", limit=18)
        clean.append(entry)
    out[field] = clean
    # Every item value above is printed. Only a relationship derived from
    # those visible givens may be unknown in this scene family.
    out["unknown"] = _unknown(spec, allowed_unknowns)


def _normalise_segments(spec: dict, out: dict, field: str = "segments") -> None:
    rows = _list(spec.get(field), name=field, low=2, high=10)
    clean = []
    missing = 0
    for index, raw in enumerate(rows):
        if isinstance(raw, dict):
            value = _maybe_number(raw.get("value"), name=f"{field} value", zero=True)
            label = _text(raw.get("label") or "", name=f"{field} label", limit=24,
                          empty=True)
        else:
            value = _maybe_number(raw, name=f"{field} value", zero=True)
            label = ""
        missing += value is None
        clean.append({"value": value, "label": label})
    if missing > 1:
        raise ValueError(f"{field} supports at most one unknown")
    out[field] = clean
    total = _maybe_number(spec.get("total"), name="total", zero=True)
    if total is not None and not missing:
        if abs(sum(float(row["value"]) for row in clean) - float(total)) > 1e-6:
            raise ValueError(f"{field} do not sum to total")
    out["total"] = total
    out["unknown"] = _unknown(spec, {"segment", "total", "remaining", "value"})
    if missing:
        if (not out["unknown"]
                or out["unknown"]["measure"] not in {"segment", "remaining", "value"}):
            raise ValueError(f"unknown must identify the missing {field} value")
    elif out["unknown"] and out["unknown"]["measure"] != "total":
        raise ValueError(f"a visible {field} value cannot also be unknown")


def _normalise_steps(spec: dict, out: dict, field: str = "steps") -> None:
    rows = _list(spec.get(field), name=field, low=2, high=8)
    clean = [_text(row.get("label") if isinstance(row, dict) else row,
                   name=f"{field} label", limit=28) for row in rows]
    unknown = _unknown(spec, {"stage", "step", "result"})
    hidden = [i for i, label in enumerate(clean) if label in {"x", "?"}]
    if unknown:
        if unknown["measure"] == "result":
            raise ValueError("a process result cannot be safely hidden in a step scene")
        if len(hidden) != 1 or clean[hidden[0]] != unknown["symbol"]:
            raise ValueError("a step unknown needs exactly one matching x or ? placeholder")
    elif hidden:
        raise ValueError("a step placeholder requires an unknown declaration")
    out[field] = clean
    out["unknown"] = unknown


def normalise_scene_spec(spec: dict) -> dict:
    """Return a canonical validated scene specification."""
    out = _base(spec)
    template = out["template"]
    if template == "shadow_similarity":
        _normalise_shadow(spec, out)
    elif template == "ladder_wall":
        _normalise_ladder(spec, out)
    elif template in {"shelves", "shopping", "scoreboard"}:
        field = {"shelves": "shelves", "shopping": "items", "scoreboard": "teams"}[template]
        allowed = {
            "shelves": {"total", "difference"},
            "shopping": {"total"},
            "scoreboard": {"total", "difference"},
        }[template]
        _normalise_named_items(spec, out, field=field,
                               allowed_unknowns=allowed)
    elif template == "equal_groups_scene":
        groups = int(_number(spec.get("groups"), name="groups", maximum=12))
        each = int(_number(spec.get("each"), name="each", zero=True, maximum=20))
        out.update(groups=groups, each=each,
                   kind=_text(spec.get("kind") or "counters", name="kind", limit=18),
                   unknown=_unknown(spec, {"total"}))
    elif template in {"ribbon_measure", "race_progress", "timeline"}:
        _normalise_segments(spec, out, "segments")
        out["label"] = _text(spec.get("label") or "", name="label", limit=30, empty=True)
    elif template == "garden":
        for key in ("length", "width", "path"):
            out[key] = _maybe_number(spec.get(key), name=key, zero=True)
        out["kind"] = _text(spec.get("kind") or "garden bed", name="kind", limit=22)
        unknown = _unknown(spec, {"length", "width", "area", "perimeter"})
        missing = [key for key in ("length", "width") if out[key] is None]
        if len(missing) > 1:
            raise ValueError("garden scene needs at least one known dimension")
        if missing:
            if not unknown or unknown["measure"] != missing[0]:
                raise ValueError("unknown must identify the missing garden dimension")
        elif unknown and unknown["measure"] in {"length", "width"}:
            raise ValueError("a visible garden dimension cannot also be unknown")
        out["unknown"] = unknown
    elif template in {"science_process", "life_cycle"}:
        _normalise_steps(spec, out, "steps")
    elif template == "force_scene":
        out["object"] = _text(spec.get("object") or "box", name="object", limit=20)
        forces = _list(spec.get("forces"), name="forces", low=1, high=6)
        clean = []
        for raw in forces:
            if not isinstance(raw, dict):
                raise ValueError("each force must be an object")
            direction = _text(raw.get("direction"), name="force direction", limit=8)
            if direction not in {"up", "down", "left", "right"}:
                raise ValueError("force direction is unsupported")
            clean.append({"direction": direction,
                          "label": _text(raw.get("label"), name="force label", limit=22)})
        out["forces"] = clean
        # Arrow directions and labels are all visible. This scene can ask only
        # for the resultant derived from those stated forces.
        out["unknown"] = _unknown(spec, {"resultant"})
    elif template == "circuit":
        components = _list(spec.get("components"), name="components", low=2, high=8)
        allowed = {"cell", "battery", "lamp", "switch", "resistor", "motor", "x", "?"}
        out["components"] = []
        for raw in components:
            kind = _text(raw.get("kind") if isinstance(raw, dict) else raw,
                         name="component", limit=16)
            if kind not in allowed:
                raise ValueError(f"unsupported circuit component: {kind}")
            out["components"].append(kind)
        out["open"] = None if "open" in spec and spec.get("open") is None \
            else bool(spec.get("open", False))
        unknown = _unknown(spec, {"component", "state"})
        hidden_components = [kind for kind in out["components"] if kind in {"x", "?"}]
        if unknown and unknown["measure"] == "component":
            if len(hidden_components) != 1 or hidden_components[0] != unknown["symbol"]:
                raise ValueError("a component unknown needs one matching x or ? placeholder")
        elif hidden_components:
            raise ValueError("a component placeholder requires a component unknown")
        if unknown and unknown["measure"] == "state":
            if out["open"] is not None or "switch" not in out["components"]:
                raise ValueError("a state unknown needs a switch with its state hidden")
        elif out["open"] is None:
            raise ValueError("a hidden switch state requires a state unknown")
        out["unknown"] = unknown
    elif template == "particle_model":
        states = _list(spec.get("states"), name="states", low=1, high=3)
        allowed = {"solid", "liquid", "gas", "x", "?"}
        out["states"] = []
        for state in states:
            value = _text(state, name="state", limit=8)
            if value not in allowed:
                raise ValueError(f"unsupported particle state: {value}")
            out["states"].append(value)
        unknown = _unknown(spec, {"state", "particles"})
        hidden = [state for state in out["states"] if state in {"x", "?"}]
        if unknown:
            if unknown["measure"] == "particles":
                raise ValueError("particle arrangements cannot be safely hidden by this template")
            if len(hidden) != 1 or hidden[0] != unknown["symbol"]:
                raise ValueError("a state unknown needs one matching x or ? placeholder")
        elif hidden:
            raise ValueError("a particle-state placeholder requires an unknown declaration")
        out["unknown"] = unknown
    elif template == "reasoning_sequence":
        steps = _list(spec.get("steps"), name="steps", low=3, high=8)
        allowed = {"circle", "square", "triangle", "star", "diamond", "?"}
        out["steps"] = []
        for raw in steps:
            if not isinstance(raw, dict):
                raise ValueError("each reasoning step must be an object")
            shape = _text(raw.get("shape"), name="shape", limit=10)
            if shape not in allowed:
                raise ValueError(f"unsupported reasoning shape: {shape}")
            out["steps"].append({
                "shape": shape,
                "count": int(_number(raw.get("count", 1), name="count", maximum=12)),
                "rotation": int(_number(raw.get("rotation", 0), name="rotation",
                                        zero=True, maximum=359)),
            })
        unknown = _unknown(spec, {"shape"})
        hidden = [step for step in out["steps"] if step["shape"] == "?"]
        if unknown:
            if unknown["symbol"] != "?" or len(hidden) != 1:
                raise ValueError("a shape unknown needs exactly one ? placeholder")
        elif hidden:
            raise ValueError("a reasoning placeholder requires a shape unknown")
        out["unknown"] = unknown
    elif template == "logic_grid":
        rows = _list(spec.get("rows"), name="rows", low=2, high=6)
        columns = _list(spec.get("columns"), name="columns", low=2, high=6)
        out["rows"] = [_text(x, name="row", limit=16) for x in rows]
        out["columns"] = [_text(x, name="column", limit=16) for x in columns]
        marks = spec.get("marks") or []
        if not isinstance(marks, list) or len(marks) > 36:
            raise ValueError("logic grid marks are invalid")
        clean = []
        for raw in marks:
            if not isinstance(raw, dict):
                raise ValueError("logic grid mark must be an object")
            row = int(_number(raw.get("row"), name="mark row", zero=True, maximum=5))
            col = int(_number(raw.get("column"), name="mark column", zero=True, maximum=5))
            mark = _text(raw.get("mark") or "x", name="mark", limit=1)
            if row >= len(out["rows"]) or col >= len(out["columns"]) or mark not in {"x", "o", "?"}:
                raise ValueError("logic grid mark is outside the grid")
            clean.append({"row": row, "column": col, "mark": mark})
        unknown = _unknown(spec, {"cell", "match"})
        if unknown and any(mark["mark"] == "o" for mark in clean):
            raise ValueError("a logic-grid unknown cannot include a confirmed match")
        out["marks"] = clean
        out["unknown"] = unknown
    else:  # pragma: no cover - SUPPORTED_SCENE_TEMPLATES and dispatch stay paired
        raise ValueError(f"no normaliser for {template}")
    return out


def canonical_scene_json(spec: dict) -> str:
    return json.dumps(normalise_scene_spec(spec), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True)


def visible_labels(spec: dict) -> list[str]:
    """Return labels the renderer is permitted to draw from a valid spec."""
    clean = normalise_scene_spec(spec)
    unit = clean.get("unit") or ""
    suffix = f" {unit}" if unit else ""
    labels: list[str] = []
    unknown = clean.get("unknown") or {}
    unknown_symbol = unknown.get("symbol", "x")

    def measure(value):
        if value is None:
            return unknown_symbol
        return f"{value}{suffix}"

    template = clean["template"]
    if template == "shadow_similarity":
        for obj in clean["objects"]:
            labels.extend([measure(obj["height"]), measure(obj["shadow"])])
    elif template == "ladder_wall":
        labels.extend(measure(clean[key]) for key in ("height", "base", "ladder"))
        if clean.get("angle") is not None:
            labels.append(f"{clean['angle']} degrees")
    elif template in {"shelves", "shopping", "scoreboard"}:
        field = {"shelves": "shelves", "shopping": "items", "scoreboard": "teams"}[template]
        for item in clean[field]:
            labels.append(item["label"])
            for key in ("value", "count", "price"):
                if key in item:
                    labels.append(measure(item[key]))
    elif template == "equal_groups_scene":
        labels.extend([str(clean["groups"]), str(clean["each"])])
    elif template in {"ribbon_measure", "race_progress", "timeline"}:
        labels.extend(measure(row["value"]) if row["value"] is not None
                      else unknown_symbol for row in clean["segments"])
        labels.extend(row["label"] for row in clean["segments"] if row["label"])
        if clean.get("label"):
            labels.append(clean["label"])
    elif template == "garden":
        labels.extend(measure(clean[key]) for key in ("length", "width")
                      if clean.get(key) is not None)
    elif template in {"science_process", "life_cycle"}:
        labels.extend(clean["steps"])
    elif template == "force_scene":
        labels.extend(force["label"] for force in clean["forces"])
    elif template == "circuit":
        labels.extend(clean["components"])
        if unknown.get("measure") == "state":
            labels.append(unknown_symbol)
    elif template == "particle_model":
        labels.extend(clean["states"])
    elif template == "reasoning_sequence":
        labels.extend(step["shape"] for step in clean["steps"] if step["shape"] == "?")
    elif template == "logic_grid":
        labels.extend(clean["rows"] + clean["columns"])
        labels.extend(mark["mark"] for mark in clean["marks"])
    if unknown and template in {
            "shadow_similarity", "ladder_wall", "ribbon_measure",
            "race_progress", "garden"}:
        labels.append("Diagram not to scale")
    return [str(label) for label in labels if str(label).strip()]


def validate_scene_spec(spec: dict) -> bool:
    try:
        normalise_scene_spec(spec)
        return True
    except (TypeError, ValueError):
        return False


__all__ = [
    "SCENE_VERSION", "SUPPORTED_SCENE_TEMPLATES", "canonical_scene_json",
    "normalise_scene_spec", "validate_scene_spec", "visible_labels",
]
