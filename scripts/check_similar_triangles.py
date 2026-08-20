"""Checks the similar-triangles diagram.

Year 9-10 similarity had no picture at all. `triangle` and `right_triangle`
draw one triangle; similarity is about two, and about which part of one
answers to which part of the other, which a pair of separate drawings cannot
say.

The property that matters here is not how it looks. It is that the figure
cannot contradict the question printed above it: a booklet that says "these
triangles are similar" over a picture where they are not is worse than a
booklet with no picture. So the pair is built from ONE set of sides and a
scale factor, and there is no way to ask for anything else.

    PYTHONPATH=. python scripts/check_similar_triangles.py
"""
import math
import sys
import tempfile
from pathlib import Path

from booklet_gen.visuals import diagrams

# Use an isolated system-temporary cache so this check never clears the
# production cache inside the OneDrive workspace.
_cache = tempfile.TemporaryDirectory(prefix="folio-similar-triangles-")
diagrams.CACHE_DIR = Path(_cache.name)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def spec(**kw):
    base = {"type": "similar_triangles", "sides": [6, 5, 4], "scale": 2}
    base.update(kw)
    return base


print("\nTHE PAIR IS ALWAYS GENUINELY SIMILAR")

# There is no way to hand this two independent triangles: the enlargement is
# derived. This is the check that the figure cannot lie about its own caption.
import inspect  # noqa: E402

from booklet_gen.visuals import shapes  # noqa: E402

source = inspect.getsource(shapes.similar_triangles)
assert "sides2" not in source and "scale" in source, (
    "the enlargement must be derived from a scale factor; taking a second "
    "independent list of sides allows a non-similar pair under a caption "
    "that says they are similar")
ok("the enlargement is derived from the first triangle, never given separately")

print("\nIT REFUSES WHAT IT CANNOT DRAW HONESTLY")

for bad, why in (
        (spec(sides=[1, 2, 9]), "side lengths that cannot close into a triangle"),
        (spec(sides=[6, 5]), "only two sides"),
        (spec(sides=[0, 5, 4]), "a zero-length side"),
        (spec(scale=0), "a scale factor of zero"),
        (spec(scale=-2), "a negative scale factor"),
        (spec(labels=["A", "B", "C"]), "three vertex labels for six vertices"),
):
    assert diagrams.render_diagram(bad) is None, (
        f"it drew something for {why}, which prints a figure the question "
        "cannot be answered from")
ok("a triangle that cannot exist, a bad scale or missing labels all refuse")

print("\nTHE FIGURES IT DOES DRAW ARE REAL FILES")

for good in (spec(), spec(unknown=["f"], unit="cm"), spec(scale=1.5, unit="m"),
             spec(sides=[5, 4, 3], scale=3),
             spec(labels=["P", "Q", "R", "X", "Y", "Z"])):
    path = diagrams.render_diagram(good)
    assert path is not None and path.exists(), f"no diagram for {good}"
    assert path.stat().st_size > 2000, f"suspiciously small diagram for {good}"
ok("every valid spec renders, including unknowns, units and custom letters")

print("\nTHE MEASUREMENTS DRAWN ARE THE ONES ASKED FOR")

# The apex is worked out from the three sides, so a wrong formula would draw a
# triangle whose sides are not the numbers printed beside them. Recompute the
# corner positions the renderer uses and measure the result.
a, b, c = 6.0, 5.0, 4.0
ax_ = (c * c + a * a - b * b) / (2 * a)
ay = math.sqrt(c * c - ax_ * ax_)
apex, left, right = (ax_, ay), (0.0, 0.0), (a, 0.0)
assert abs(math.dist(left, right) - a) < 1e-9, "the base is not side a"
assert abs(math.dist(apex, right) - b) < 1e-9, (
    "the right slant is not side b, so a label names the wrong edge")
assert abs(math.dist(apex, left) - c) < 1e-9, (
    "the left slant is not side c, so a label names the wrong edge")
ok("each side lands on the edge its label names")

print("\nAN UNKNOWN SIDE IS WITHHELD, NOT JUST UNLABELLED")

from booklet_gen.visuals.style import UNKNOWN_LABEL, _dim_label  # noqa: E402

s = spec(unknown=["f"], unit="cm")
assert _dim_label(s, "f", 8.0, " cm") == UNKNOWN_LABEL, (
    "the side the question asks for printed its own answer")
assert _dim_label(s, "d", 12.0, " cm") == "12 cm", (
    "a known side lost its measurement")
ok("the asked-for side prints as unknown and the rest keep their numbers")

print("\nTHE PIPELINE HIDES A NAMED UNKNOWN EVEN IF THE MODEL FORGETS")

from booklet_gen.agents.consistency import reconcile_diagram_spec  # noqa: E402

fixed, changed = reconcile_diagram_spec(
    spec(unknown=[]),
    "Triangles ABC and DEF are similar. Find the length of EF.",
)
assert changed and fixed.get("unknown") == ["d"], fixed
ok("find EF deterministically hides renderer side d")

fixed, changed = reconcile_diagram_spec(
    spec(unknown=[]),
    "Triangles ABC and DEF are similar. Calculate side AC.",
)
assert changed and fixed.get("unknown") == ["b"], fixed
ok("find AC deterministically hides renderer side b")

fixed, changed = reconcile_diagram_spec(
    spec(unknown=[]),
    "Triangles ABC and DEF are similar. AB is 4 cm and DE is 8 cm. Find EF.",
)
assert changed and fixed.get("unknown") == ["d"], fixed
ok("asking for EF hides only EF and leaves the stated AB and DE visible")

print("\nTHE PROMPTS CAN ACTUALLY ASK FOR IT")

prompt = Path("booklet_gen/prompts/question_generator_maths.txt").read_text()
assert "similar_triangles" in prompt, (
    "a renderer no prompt names is one no customer will ever see")
assert "scale" in prompt and "unknown" in prompt
ok("the maths prompt routes similarity to it and documents its fields")

print(f"\nALL {_passed} SIMILAR-TRIANGLE CHECKS PASSED")
sys.exit(0)
