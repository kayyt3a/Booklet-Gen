"""Checks an angle's label does not sit on top of the arc it names.

A Year 6 booklet printed a three-angle figure at a point. One label read
"120°" and the one beside it read "145", and the degree symbol had not been
dropped: it was drawn exactly on the arc stroke, where it reads as part of the
circle. The child is left comparing a figure that appears to mix degrees with
bare numbers, in a question whose whole method is that the three add to 360.

THE RULE WAS ABOUT THE WRONG PART OF THE LABEL. Labels were centred at a fixed
radius, a set distance past the arc:

    r  = 0.30 + 0.055 * i      # the arc
    rl = 0.52 + 0.055 * i      # the label's CENTRE

That is a promise about the middle of the text and not about its edge. A label
sitting above or below the vertex approaches the arc with its line height,
which is small. One sitting to the left or right approaches with half its
WIDTH, and "145째" is wide enough to reach back over the arc it was measured
from. Both labels in that figure obeyed the rule. Only one of them cleared.

So the extent of the text along its own radius is measured and added: the
half-width where the label lies horizontally, the half-height where it lies
vertically, and the blend of the two in between.

This is the third figure in this product to print text on top of something
already drawn there, after the column graph's category labels and the
place-value chart's column headings. The shape is always the same: a position
computed from a constant, and nothing measuring what the text actually
occupies once it is set.

    PYTHONPATH=. python scripts/check_angle_labels.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib                                                  # noqa: E402
matplotlib.use("Agg")

from booklet_gen.visuals import shapes                             # noqa: E402
from booklet_gen.visuals.style import _Fonts                       # noqa: E402

PASSED = 0
TOTAL = 0
SCRATCH = Path(__file__).resolve().parent.parent / "output" / "_angle_check.png"


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


def box_distance_from_vertex(box) -> float:
    """How close this label's nearest corner comes to the vertex at (0, 0).

    The vertex is outside the label box in every figure here, so this is the
    ordinary point-to-rectangle distance. Measuring the nearest EDGE rather
    than the centre is the entire point: the centre always cleared, and the
    edge is what overlapped the arc.
    """
    dx = max(box.x0, 0.0, -box.x1)
    dy = max(box.y0, 0.0, -box.y1)
    return math.hypot(dx, dy)


def measure(spec: dict, scale: float = 1.0) -> list[dict]:
    """Every label on this figure, with the arc it names and how clear it is."""
    SCRATCH.parent.mkdir(parents=True, exist_ok=True)
    captured: list[dict] = []
    original = shapes._finish

    def spy(fig, ax, out, pad=0.08):
        ax.set_aspect("equal")
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        for i, text in enumerate(ax.texts):
            body = text.get_text()
            if not body or body.startswith("not to scale"):
                continue
            box = text.get_window_extent(renderer=renderer).transformed(
                ax.transData.inverted())
            captured.append({
                "text": body,
                "clear": box_distance_from_vertex(box),
                # The arcs this renderer draws, smallest first, so a label can
                # be checked against the one it names.
                "index": i,
            })
        original(fig, ax, out, pad)

    shapes._finish = spy
    try:
        shapes.angle(spec, SCRATCH, _Fonts(scale))
    finally:
        shapes._finish = original
    return captured


# What each label has to clear, worked out here rather than taken from the
# renderer, so the two have to agree.
#
# A right angle is not marked with an arc at all: it gets the square a child
# is taught to recognise, drawn at a fixed 0.16. Reading every index off the
# arc formula reported the 90 degree label as overlapping an arc that branch
# never draws, which is a check inventing a defect rather than finding one.
def mark_radius(spec: dict, i: int) -> float:
    angles = spec.get("angles") or [spec.get("degrees", 45)]
    if abs(float(angles[i]) - 90) < 0.01:
        return 0.16
    return 0.30 + 0.055 * i


# Read off page 9 of the shipped Year 6 booklet.
SHIPPED = {"type": "angle", "base": "point", "angles": [120, 145, 95],
           "labels": ["120°", "145°", "y"]}


print("\n== the figure that shipped ==")

for scale in (1.0, 1.3, 1.6):
    labels = measure(SHIPPED, scale)
    check(len(labels) == 3, f"{scale}x: three labels drawn",
          f"got {[x['text'] for x in labels]}; nothing below is measuring the "
          "real figure")
    for i, label in enumerate(labels):
        gap = label["clear"] - mark_radius(SHIPPED, i)
        check(gap > 0,
              f"        {label['text']!r} clears its arc by {gap:.3f}",
              f"{label['text']!r} overlaps its own arc by {-gap:.3f}. This is "
              "the defect as shipped: the degree symbol is drawn on the arc "
              "stroke and reads as part of the circle")


print("\n== a label is pushed out only as far as it has to be ==")

# The cheap way to clear an arc is to fling every label to the edge of the
# figure, which breaks the thing a label is for: saying which angle it names.
far = measure(SHIPPED)
for i, label in enumerate(far):
    check(label["clear"] < mark_radius(SHIPPED, i) + 0.55,
          f"{label['text']!r} sits {label['clear']:.2f} from the vertex, "
          f"against its mark at {mark_radius(SHIPPED, i):.2f}",
          f"{label['text']!r} is {label['clear']:.2f} out. A label parked far "
          "from its arc no longer says which angle it belongs to, which is "
          "worse than the overlap it was moved to avoid")


print("\n== and it holds for the shapes the booklets actually ask for ==")

CASES = [
    ({"type": "angle", "base": "line", "angles": [58, 74, 48],
      "labels": ["58°", "74°", "a"]}, "three on a straight line"),
    ({"type": "angle", "base": "point", "angles": [130, 95, 75, 60],
      "labels": ["130°", "95°", "75°", "x"]}, "four at a point"),
    ({"type": "angle", "base": "line", "angles": [90, 30, 30, 30],
      "labels": ["90°", "a", "a", "a"]}, "a right angle and three equal"),
    ({"type": "angle", "base": "open", "angles": [45]}, "a single angle"),
    ({"type": "angle", "base": "point", "angles": [120, 120, 120],
      "labels": ["120°", "120°", "120°"]}, "three wide labels"),
]
for spec, label in CASES:
    labels = measure(spec)
    overlaps = [x["text"] for i, x in enumerate(labels)
                if x["clear"] - mark_radius(spec, i) <= 0]
    check(not overlaps, f"{label}: all {len(labels)} label(s) clear",
          f"{overlaps} sit on their own arcs")


print("\n== a right angle's label clears its square, not an arc ==")

# The right angle is marked with a square at 0.16 rather than an arc, and its
# label was measured against a radius that branch never sets. A fixed offset
# hid that; measuring does not.
square = measure({"type": "angle", "base": "line", "angles": [90, 90],
                  "labels": ["90°", "b"]})
named = ", ".join(repr(x["text"]) for x in square)
check(all(x["clear"] > 0.16 for x in square),
      f"both labels clear the 0.16 square ({named})",
      f"{[(x['text'], round(x['clear'], 3)) for x in square]} against a square "
      "drawn at 0.16")


SCRATCH.unlink(missing_ok=True)
print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
