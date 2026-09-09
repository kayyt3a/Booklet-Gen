"""Checks that no figure is invented from a spec that does not describe one.

A customer's Year 6 booklet printed "0 x 0" under a question reading
"Calculate 512 x 24": the model sent a long multiplication spec with no
operands and the renderer read them as `spec.get("top", 0)`. That was fixed in
the two algorithm renderers. Sweeping the other forty-nine found the same shape
of defect nearly everywhere. Rendering a bare `{"type": ...}` used to produce a
finished, labelled figure for twenty-five of them:

    angle (45 degrees)          groups (3 of 4)         rectangle (8 by 3)
    array (3 by 4)              jug (1000 mL, empty)    right_triangle (3-4-5)
    bar_model (4 parts)         l_shape (12/10/4/3)     ruler (7 cm)
    circle_slices (4)           net (a cube)            scale_dial (1000 g)
    clock (12:00)               number_line (0 to 1)    shape (a triangle)
    cuboid (5 by 3 by 4)        parallelogram (8 by 4)  shape_3d (a cube)
    cylinder (r 3, h 8)         place_value (0)         similar_triangles
    factor_tree (36)            grid_area (6 by 4)      symmetry (a rectangle)
                                                        trapezium (5/9/4)

None of those numbers came from the question. A child asked to find an area
does not measure the page, they read the labels, so a defaulted dimension is
not a cosmetic blemish: it is a different question, asked confidently. The
3-4-5 triangle is the worst of them, because it is a real Pythagorean triple
and survives a glance from an adult.

`visuals/requirements.py` now states what each figure cannot be drawn without,
and `render_diagram` enforces it centrally. The assertions below are the
invariant rather than the table: NO renderer may draw from a bare type, so a
renderer added later with a plausible default is caught here.

The counterweight matters as much. Refusing turns a wrong figure into a missing
one, which is its own way of making the booklet worse, so this also renders
every diagram example written into the prompts. That is what the model is
taught to copy, and it caught four types whose examples the renderer had always
rejected: every tally, picture graph, stem and leaf plot and scatterplot built
to the shape of the example was being dropped silently.

    PYTHONPATH=. python scripts/check_diagram_requirements.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.visuals import diagrams                          # noqa: E402
from booklet_gen.visuals.requirements import (                    # noqa: E402
    REQUIRED_KEYS, missing_requirements,
)

PASSED = 0
TOTAL = 0


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


def draws(spec: dict) -> bool:
    return diagrams.render_diagram(dict(spec)) is not None


# The cache is keyed on the spec, so a figure drawn by an earlier run would be
# handed back without the renderer being asked, and every assertion below would
# be measuring the old behaviour.
shutil.rmtree(diagrams.CACHE_DIR, ignore_errors=True)

print("\n== no renderer draws a figure from a bare type ==")

invented = sorted(k for k in diagrams.SUPPORTED_TYPES if draws({"type": k}))
check(not invented,
      f"all {len(diagrams.SUPPORTED_TYPES)} renderers refuse an empty spec",
      f"these drew something out of nothing: {invented}. Every number on those "
      "figures is a default argument, and the student will read them as the "
      "numbers in the question")

print("\n== a spec that lost one measurement is refused, not filled in ==")

HALF_SPECS = [
    ({"type": "rectangle", "length": 12, "unit": "cm"},
     "a rectangle with no width", "an 8 cm width invented from nowhere"),
    ({"type": "cuboid", "length": 6, "width": 4, "unit": "cm"},
     "a box with no height", "a 4 cm height invented, and volume is a product "
     "of all three"),
    ({"type": "cylinder", "radius": 5, "unit": "cm"},
     "a cylinder with no height", "an 8 cm height invented"),
    ({"type": "right_triangle", "a": 8, "unit": "cm"},
     "a right triangle with one side", "3-4-5 invented, which looks correct "
     "because it is a real triple"),
    ({"type": "trapezium", "top": 6, "height": 4},
     "a trapezium with no bottom", "a 9 unit parallel side invented"),
    ({"type": "parallelogram", "base": 10},
     "a parallelogram with no height", "a height of 4 invented, so the area is "
     "wrong by whatever the real height was"),
    ({"type": "jug", "capacity": 500, "unit": "mL"},
     "a jug with no level in it", "an empty jug drawn for a question about "
     "how much it holds"),
    ({"type": "scale_dial", "max": 2000, "unit": "g"},
     "a scale with no reading", "a needle at zero under 'what mass is shown'"),
    ({"type": "clock", "minute": 30},
     "a time with no hour", "12:30 drawn for whatever time was meant"),
    ({"type": "number_line", "from": 300, "to": 400},
     "a number line with no divisions", "four ticks assumed on a line that "
     "may be marked in tens"),
    ({"type": "array", "rows": 4},
     "an array with no columns", "a 4 by 4 array for 4 by 7"),
    ({"type": "angle", "base": "line"},
     "an angle figure with no angles", "45 degrees drawn on a straight line"),
    ({"type": "place_value"},
     "place value blocks with no number", "zero blocks for a three-digit "
     "number"),
]
for spec, label, consequence in HALF_SPECS:
    check(not draws(spec), f"refused: {label}", consequence)

print("\n== a complete spec still draws ==")

COMPLETE = [
    ({"type": "rectangle", "length": 12, "width": 5, "unit": "cm"}, "rectangle"),
    ({"type": "cuboid", "length": 6, "width": 4, "height": 3, "unit": "cm"}, "cuboid"),
    ({"type": "cylinder", "radius": 5, "height": 9, "unit": "cm"}, "cylinder"),
    ({"type": "right_triangle", "a": 8, "b": 6, "c": 10, "unit": "cm",
      "unknown": ["c"]}, "right triangle"),
    ({"type": "clock", "hour": 7, "minute": 20}, "clock"),
    ({"type": "array", "rows": 4, "columns": 7}, "array"),
    ({"type": "number_line", "from": 300, "to": 400, "divisions": 10,
      "mark_at": [347], "label_at": ["347"]}, "number line"),
    ({"type": "place_value", "value": 342}, "place value blocks"),
    ({"type": "jug", "capacity": 1000, "level": 650, "unit": "mL"}, "jug"),
]
for spec, label in COMPLETE:
    check(draws(spec), f"drew: {label}",
          "the requirement is too tight and is dropping figures the booklet "
          "needs, which is a different way of making the page worse")

print("\n== zero is a measurement, not an absence ==")

# The distinction the whole table turns on. An empty jug and an unshaded bar
# are real figures a question asks for; a missing key is not.
for spec, label in (
    ({"type": "jug", "capacity": 1000, "level": 0, "unit": "mL"},
     "a jug drawn empty on purpose"),
    ({"type": "place_value", "value": 0}, "place value blocks for zero"),
    ({"type": "bar_model", "parts": 8, "shaded": 0},
     "a bar with nothing shaded yet"),
    ({"type": "scale_dial", "max": 1000, "value": 0, "unit": "g"},
     "a scale reading zero"),
):
    check(draws(spec), f"drew: {label}",
          "a legitimate zero was treated as a missing key, so the question "
          "that needs an empty figure cannot have one")

print("\n== genuinely optional keys keep their defaults ==")

for spec, label in (
    ({"type": "circle_slices", "slices": 6}, "a circle with no shading asked for"),
    ({"type": "rectangle", "length": 4, "width": 3}, "a rectangle with no unit"),
    ({"type": "column_arithmetic", "top": 4528, "bottom": 2694, "operation": "+"},
     "column arithmetic with show_answer left out"),
    ({"type": "grid_area", "width": 6, "height": 4}, "a grid with no corner cut"),
    ({"type": "shape", "shapes": ["pentagon"]}, "a shape with label left out"),
):
    check(draws(spec), f"drew: {label}",
          "an optional key was made mandatory, so a perfectly good spec now "
          "produces no figure")

print("\n== a net asks for the dimensions its own solid needs ==")

check(draws({"type": "net", "solid": "cube", "edge": 4, "unit": "cm"}),
      "a cube net needs only its edge",
      "the prism's three dimensions were demanded of a cube, refusing a spec "
      "that is completely well formed")
check(not draws({"type": "net", "solid": "rectangular prism", "length": 5,
                 "width": 3, "unit": "cm"}),
      "refused: a prism net with no height",
      "a depth of 2 invented, and surface area is a sum over all six faces")
check(not draws({"type": "net", "edge": 4}),
      "refused: a net that does not say which solid",
      "a cube drawn for whatever solid the question was about")

print("\n== the guard runs before the cache, not after ==")

# A figure drawn under the old behaviour may still be sitting on disk. Serving
# it would put the invented numbers back on the page of the next booklet.
stale = {"type": "rectangle", "length": 12, "unit": "cm"}
cached = diagrams._cache_path(diagrams.normalise_spec(stale))
cached.write_bytes(b"\x89PNG\r\n\x1a\n" + b"stale figure from an older release")
check(not draws(stale),
      "an incomplete spec is refused even with a figure already cached",
      "a wrong figure drawn by a previous release is still being served, so "
      "the fix does not reach any instance with a warm cache")
cached.unlink(missing_ok=True)

print("\n== every diagram example in the prompts renders ==")


def _objects(text: str):
    """Every balanced {...} that starts a spec, wherever it sits in the file."""
    for i in range(len(text)):
        if not text.startswith('{"type"', i):
            continue
        depth, j, in_string, escaped = 0, i, False, False
        while j < len(text):
            ch = text[j]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    break
            j += 1


prompts = Path(__file__).resolve().parent.parent / "booklet_gen" / "prompts"
examples, refused = 0, []
for path in sorted(prompts.glob("*.txt")):
    for raw in _objects(path.read_text(encoding="utf-8")):
        try:
            spec = json.loads(raw)
        except json.JSONDecodeError:
            continue          # prose describing a shape rather than a spec
        if spec.get("type") not in diagrams.SUPPORTED_TYPES:
            continue
        examples += 1
        if not draws(spec):
            refused.append(f"{path.name}: {raw[:80]}")

check(examples >= 100, f"found {examples} worked specs across the prompts",
      "the examples stopped being found, so this section is asserting nothing")
check(not refused,
      f"all {examples} of them draw a figure",
      "the model is being shown a spec the renderer will always reject, so "
      "every figure it builds to that shape is dropped in silence:\n                "
      + "\n                ".join(refused))

print("\n== the table itself stays honest ==")

unknown = sorted(set(REQUIRED_KEYS) - set(diagrams.SUPPORTED_TYPES))
check(not unknown, "every type in the table is a type that exists",
      f"{unknown} are named as requirements for renderers that are not "
      "registered, so the entry protects nothing")
check(missing_requirements({"type": "rectangle", "length": 4, "width": ""})
      == ["width"],
      "an empty string counts as a missing measurement",
      "a spec carrying \"\" would satisfy the requirement and then be read as "
      "a number by the renderer")
check(missing_requirements({"type": "not_a_diagram"}) == [],
      "an unknown type is left to the dispatcher to reject",
      "the requirements check started reporting on types it knows nothing "
      "about")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
