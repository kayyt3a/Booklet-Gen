"""Deterministic checks for Folio-owned contextual scene renderers."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image

from booklet_gen.visuals import scenes
from booklet_gen.visuals.scene_specs import (
    SUPPORTED_SCENE_TEMPLATES,
    normalise_scene_spec,
    validate_scene_spec,
    visible_labels,
)


passed = 0


def ok(message: str) -> None:
    global passed
    passed += 1
    print("ok ", message)


EXAMPLES = {
    "shadow_similarity": {
        "template": "shadow_similarity", "unit": "m",
        "objects": [
            {"id": "reference", "kind": "tree", "height": 6, "shadow": 4},
            {"id": "target", "kind": "tree", "height": None, "shadow": 10},
        ],
        "unknown": {"object_id": "target", "measure": "height", "symbol": "x"},
    },
    "ladder_wall": {
        "template": "ladder_wall", "unit": "m",
        "height": 3, "base": 4, "ladder": 5,
    },
    "shelves": {
        "template": "shelves",
        "shelves": [{"label": "Shelf A", "count": 12},
                    {"label": "Shelf B", "count": 8}],
    },
    "ribbon_measure": {
        "template": "ribbon_measure", "unit": "cm", "label": "Ribbon cuts",
        "segments": [{"value": 12, "label": "first"},
                     {"value": None, "label": "second"}],
        "unknown": {"measure": "segment", "symbol": "x"},
    },
    "race_progress": {
        "template": "race_progress", "unit": "m", "label": "500 m race",
        "segments": [{"value": 200, "label": "completed"},
                     {"value": 300, "label": "remaining"}],
        "total": 500,
    },
    "shopping": {
        "template": "shopping", "unit": "$",
        "items": [{"label": "Notebook", "kind": "book", "price": 3.5},
                  {"label": "Ball", "kind": "ball", "price": 6}],
    },
    "equal_groups_scene": {
        "template": "equal_groups_scene", "groups": 4, "each": 3,
        "kind": "ball",
    },
    "scoreboard": {
        "template": "scoreboard",
        "teams": [{"label": "Red", "value": 45},
                  {"label": "Blue", "value": 38}],
    },
    "garden": {
        "template": "garden", "unit": "m", "length": 8, "width": 5,
        "kind": "garden bed",
    },
    "timeline": {
        "template": "timeline", "unit": "min", "label": "Journey",
        "segments": [{"value": 15, "label": "walk"},
                     {"value": 25, "label": "bus"}],
    },
    "science_process": {
        "template": "science_process",
        "steps": ["Evaporation", "Condensation", "Rain"],
    },
    "force_scene": {
        "template": "force_scene", "object": "box",
        "forces": [{"direction": "right", "label": "push 8 N"},
                   {"direction": "left", "label": "friction 3 N"}],
    },
    "circuit": {
        "template": "circuit", "open": True,
        "components": ["battery", "switch", "lamp", "resistor"],
    },
    "particle_model": {
        "template": "particle_model", "states": ["solid", "liquid", "gas"],
    },
    "life_cycle": {
        "template": "life_cycle",
        "steps": ["Egg", "Larva", "Pupa", "Adult"],
    },
    "reasoning_sequence": {
        "template": "reasoning_sequence",
        "steps": [{"shape": "triangle", "count": 1, "rotation": 0},
                  {"shape": "triangle", "count": 2, "rotation": 90},
                  {"shape": "?", "count": 1, "rotation": 0}],
        "unknown": {"measure": "shape", "symbol": "?"},
    },
    "logic_grid": {
        "template": "logic_grid", "rows": ["Ava", "Luca"],
        "columns": ["Red", "Blue"],
        "marks": [{"row": 0, "column": 0, "mark": "x"},
                  {"row": 1, "column": 1, "mark": "o"}],
    },
}


assert set(EXAMPLES) == set(SUPPORTED_SCENE_TEMPLATES)
ok("every supported template has a representative check")

for name, spec in EXAMPLES.items():
    assert validate_scene_spec(spec), name
    clean = normalise_scene_spec(spec)
    assert clean["template"] == name
ok("every representative scene validates and canonicalises")

labels = visible_labels(EXAMPLES["shadow_similarity"])
assert "6 m" in labels and "4 m" in labels and "10 m" in labels and "x" in labels
assert "15 m" not in labels
ok("the tree-shadow scene exposes givens and x, never the calculated answer")

SAFE_TEXT_UNKNOWNS = {
    "science_process": {
        "template": "science_process",
        "steps": ["Evaporation", "?", "Rain"],
        "unknown": {"measure": "stage", "symbol": "?"},
    },
    "life_cycle": {
        "template": "life_cycle",
        "steps": ["Egg", "Larva", "?", "Adult"],
        "unknown": {"measure": "step", "symbol": "?"},
    },
    "particle_model": {
        "template": "particle_model",
        "states": ["solid", "?"],
        "unknown": {"measure": "state", "symbol": "?"},
    },
    "circuit_component": {
        "template": "circuit",
        "components": ["battery", "?", "lamp"],
        "unknown": {"measure": "component", "symbol": "?"},
    },
    "circuit_state": {
        "template": "circuit", "open": None,
        "components": ["battery", "switch", "lamp"],
        "unknown": {"measure": "state", "symbol": "?"},
    },
    "logic_grid": {
        "template": "logic_grid", "rows": ["Ava", "Luca"],
        "columns": ["Red", "Blue"],
        "marks": [{"row": 0, "column": 0, "mark": "x"}],
        "unknown": {"measure": "match", "symbol": "?"},
    },
}

for name, spec in SAFE_TEXT_UNKNOWNS.items():
    assert validate_scene_spec(spec), name
safe_labels = {name: visible_labels(spec)
               for name, spec in SAFE_TEXT_UNKNOWNS.items()}
assert "Condensation" not in safe_labels["science_process"]
assert "Pupa" not in safe_labels["life_cycle"]
assert "liquid" not in safe_labels["particle_model"]
assert "resistor" not in safe_labels["circuit_component"]
assert "open" not in safe_labels["circuit_state"]
assert "closed" not in safe_labels["circuit_state"]
assert "o" not in safe_labels["logic_grid"]
assert all("?" in labels for name, labels in safe_labels.items()
           if name != "logic_grid")
ok("textual scene families replace student answers with safe placeholders")

LEAKING_TEXT_UNKNOWNS = [
    {"template": "science_process",
     "steps": ["Evaporation", "Condensation", "Rain"],
     "unknown": {"measure": "stage", "symbol": "?"}},
    {"template": "science_process",
     "steps": ["Evaporation", "?", "Rain"]},
    {"template": "life_cycle",
     "steps": ["Egg", "Larva", "Pupa", "Adult"],
     "unknown": {"measure": "step", "symbol": "?"}},
    {"template": "particle_model", "states": ["solid", "liquid"],
     "unknown": {"measure": "state", "symbol": "?"}},
    {"template": "particle_model", "states": ["solid"],
     "unknown": {"measure": "particles", "symbol": "?"}},
    {"template": "circuit", "components": ["battery", "resistor", "lamp"],
     "unknown": {"measure": "component", "symbol": "?"}},
    {"template": "circuit", "open": True,
     "components": ["battery", "switch", "lamp"],
     "unknown": {"measure": "state", "symbol": "?"}},
    {"template": "circuit", "components": ["battery", "?", "lamp"]},
    {"template": "logic_grid", "rows": ["Ava", "Luca"],
     "columns": ["Red", "Blue"],
     "marks": [{"row": 0, "column": 1, "mark": "o"}],
     "unknown": {"measure": "match", "symbol": "?"}},
]
assert all(not validate_scene_spec(spec) for spec in LEAKING_TEXT_UNKNOWNS)
ok("scene validation refuses textual specs that would print the answer")

SAFE_DERIVED_UNKNOWNS = {
    "shelves_difference": {
        "template": "shelves",
        "shelves": [{"label": "Top", "count": 12},
                    {"label": "Bottom", "count": 8}],
        "unknown": {"measure": "difference", "symbol": "?"},
    },
    "shopping_total": {
        "template": "shopping",
        "items": [{"label": "Book", "price": 4},
                  {"label": "Pen", "price": 2}],
        "unknown": {"measure": "total", "symbol": "?"},
    },
    "groups_total": {
        "template": "equal_groups_scene", "groups": 4, "each": 3,
        "unknown": {"measure": "total", "symbol": "?"},
    },
    "garden_width": {
        "template": "garden", "unit": "m", "length": 8, "width": None,
        "unknown": {"measure": "width", "symbol": "x"},
    },
}
assert all(validate_scene_spec(spec)
           for spec in SAFE_DERIVED_UNKNOWNS.values())
ok("derived and explicitly hidden numeric unknowns remain supported")

LEAKING_NUMERIC_UNKNOWNS = [
    {"template": "shelves",
     "shelves": [{"label": "Top", "count": 12},
                 {"label": "Bottom", "count": 8}],
     "unknown": {"measure": "count", "symbol": "?"}},
    {"template": "shopping",
     "items": [{"label": "Book", "price": 4},
               {"label": "Pen", "price": 2}],
     "unknown": {"measure": "price", "symbol": "?"}},
    {"template": "scoreboard",
     "teams": [{"label": "Blue", "value": 24},
               {"label": "Gold", "value": 19}],
     "unknown": {"measure": "value", "symbol": "?"}},
    {"template": "equal_groups_scene", "groups": 4, "each": 3,
     "unknown": {"measure": "each", "symbol": "?"}},
    {"template": "ribbon_measure",
     "segments": [{"value": 12}, {"value": None}],
     "unknown": {"measure": "total", "symbol": "?"}},
    {"template": "ribbon_measure",
     "segments": [{"value": 12}, {"value": 8}],
     "unknown": {"measure": "segment", "symbol": "?"}},
    {"template": "garden", "length": 8, "width": 5,
     "unknown": {"measure": "width", "symbol": "x"}},
    {"template": "garden", "length": 8, "width": None,
     "unknown": {"measure": "area", "symbol": "x"}},
    {"template": "force_scene", "object": "box",
     "forces": [{"direction": "right", "label": "8 N"}],
     "unknown": {"measure": "direction", "symbol": "?"}},
    {"template": "circuit", "components": ["cell", "lamp"],
     "unknown": {"measure": "current", "symbol": "?"}},
    {"template": "reasoning_sequence",
     "steps": [{"shape": "circle", "count": 1, "rotation": 0},
               {"shape": "square", "count": 2, "rotation": 0},
               {"shape": "triangle", "count": 3, "rotation": 0}],
     "unknown": {"measure": "count", "symbol": "?"}},
    {"template": "reasoning_sequence",
     "steps": [{"shape": "circle", "count": 1, "rotation": 0},
               {"shape": "square", "count": 2, "rotation": 0},
               {"shape": "?", "count": 3, "rotation": 0}]},
]
assert all(not validate_scene_spec(spec)
           for spec in LEAKING_NUMERIC_UNKNOWNS)
ok("scene validation refuses numeric unknowns that remain visibly answered")

invalid = [
    {"template": "shadow_similarity", "unit": "m", "objects": [
        {"id": "a", "kind": "tree", "height": 6, "shadow": 4},
        {"id": "b", "kind": "tree", "height": 12, "shadow": 10},
    ]},
    {"template": "ladder_wall", "height": 3, "base": 4, "ladder": 8},
    {"template": "race_progress", "segments": [{"value": 6}, {"value": 7}],
     "total": 20},
    {"template": "circuit", "components": ["battery", "dragon"]},
    {"template": "logic_grid", "rows": ["A", "B"], "columns": ["X", "Y"],
     "marks": [{"row": 9, "column": 0, "mark": "x"}]},
]
assert all(not validate_scene_spec(spec) for spec in invalid)
ok("contradictory, impossible, and unsupported scenes are refused")

with tempfile.TemporaryDirectory(prefix="folio-scenes-") as raw:
    old = scenes.CACHE_DIR
    old_canvas = scenes.SceneCanvas
    recorded_canvases = []

    class RecordingCanvas(old_canvas):
        def __init__(self):
            super().__init__()
            recorded_canvases.append(self)

    scenes.CACHE_DIR = Path(raw)
    scenes.SceneCanvas = RecordingCanvas
    try:
        for name, spec in EXAMPLES.items():
            path = scenes.render_scene(spec)
            assert path is not None and path.exists(), name
            assert path.stat().st_size > 2500, name
            with Image.open(path) as image:
                assert image.width >= 500 and image.height >= 220, name
                grey = image.convert("L")
                lo, hi = grey.getextrema()
                assert hi - lo >= 100, (name, lo, hi)
        ok("every scene renders sharply with grayscale contrast")

        forbidden_print = {
            "science_process": {"Condensation"},
            "life_cycle": {"Pupa"},
            "particle_model": {"liquid"},
            "circuit_component": {"resistor"},
            "circuit_state": {"open", "closed"},
            "logic_grid": {"o"},
        }
        for name, spec in SAFE_TEXT_UNKNOWNS.items():
            before = len(recorded_canvases)
            path = scenes.render_scene(spec)
            assert path is not None and path.exists(), name
            assert path.stat().st_size > 2500, name
            canvases = recorded_canvases[before:]
            assert canvases, name
            printed = {label for canvas in canvases for label in canvas.labels}
            assert printed.isdisjoint(forbidden_print[name]), (name, printed)
        ok("safely hidden textual answers do not reach rendered labels")

        for spec in LEAKING_TEXT_UNKNOWNS:
            assert scenes.render_scene(spec) is None
        ok("no answer-leaking textual scene can reach the renderer")

        first = scenes.render_scene(EXAMPLES["shadow_similarity"])
        data1 = first.read_bytes()
        first.unlink()
        second = scenes.render_scene(EXAMPLES["shadow_similarity"])
        assert first == second and data1 == second.read_bytes()
        ok("scene output and cache paths are deterministic")

        assert scenes.render_scene(invalid[0]) is None
        ok("render_scene returns None instead of drawing a dishonest scene")
    finally:
        scenes.CACHE_DIR = old
        scenes.SceneCanvas = old_canvas

print(f"\n{passed} contextual-scene contracts passed")
