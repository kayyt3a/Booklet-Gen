"""What each figure cannot be drawn without.

A customer's Year 6 booklet printed a long multiplication frame containing
"0 x 0" under a question reading "Calculate 512 x 24". The model had emitted a
spec with no operands in it, and the renderer read them as `spec.get("top", 0)`.
Two missing numbers became two zeros, and the page carried a tidy, confident
diagram of a different sum.

That was fixed in the two algorithm renderers, and then a sweep of the other
forty-nine found the same shape of defect nearly everywhere: twenty-five of
them drew a complete figure from `{"type": ...}` and nothing else. A rectangle
with no dimensions came out as a labelled 8 cm by 3 cm. A cuboid came out 5 by
3 by 4. A right triangle came out 3-4-5, which is worse than an arbitrary
default because it is a Pythagorean triple and so survives a glance. A child
reading "find the area" measures nothing; they read the numbers off the
picture, and the numbers were invented by a default argument.

So the requirement is stated once, here, and enforced centrally in
`render_diagram` rather than in fifty renderers. Each entry lists the groups of
keys a figure of that type cannot be honest without. A group is a set of
alternative spellings, and at least one of them must carry a value.

Keys are absent from this table on purpose when their default is a real
choice rather than a guess: `unit` (unitless is a legitimate figure),
`show_answer` (false is what a question wants), `label` (labelled is the norm),
`shaded` (nothing shaded is what "shade three quarters" needs), and
`grid_area`'s `cut_width` (no cut is a plain rectangle).

The counterpart to refusing is recovery: `visual_policy.repair_spec_from_text`
puts operands back from the question text where it can, so the common case
becomes the right figure rather than no figure.

`scripts/check_diagram_requirements.py` asserts the stronger invariant this
table exists to serve: NO renderer draws anything from a bare `{"type": ...}`.
A renderer added later with a plausible-looking default is caught there rather
than in a customer's booklet.
"""
from __future__ import annotations

# type -> tuple of requirement groups; each group is a tuple of acceptable key
# spellings, one of which must be present.
REQUIRED_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    # The vertical algorithms. These are the ones that shipped wrong.
    "column_arithmetic": (("top",), ("bottom",)),
    "long_multiplication": (("top",), ("bottom",)),
    "short_division": (("dividend",), ("divisor",)),

    # Fractions and number.
    "circle_slices": (("slices",),),
    "bar_model": (("parts",),),
    # A line's endpoints are the whole scale a student reads a position
    # against, so 0 to 1 is not a safe stand-in for a line from 300 to 400.
    "number_line": (("from",), ("to",), ("divisions",)),
    "place_value": (("value",),),
    "factor_tree": (("value", "number"),),
    "array": (("rows",), ("columns", "cols")),
    "groups": (("groups",), ("each",)),

    # Measurement and geometry. Every one of these prints its dimensions as
    # labels, which is exactly what makes an invented default dangerous.
    "rectangle": (("length",), ("width",)),
    "l_shape": (("outer_length",), ("outer_width",),
                ("cut_length",), ("cut_width",)),
    "grid_area": (("width",), ("height",)),
    "parallelogram": (("base",), ("height",)),
    "trapezium": (("top", "a"), ("bottom", "b"), ("height",)),
    "right_triangle": (("a", "base"), ("b", "height")),
    "similar_triangles": (("sides",), ("scale",)),
    "cuboid": (("length",), ("width",), ("height",)),
    "cylinder": (("radius",), ("height",)),
    "angle": (("angles", "degrees"),),
    "symmetry": (("shape",),),
    "shape": (("shapes", "shape"),),
    "shape_3d": (("solids", "solid"),),

    # Reading an instrument. The needle position IS the answer, so a default
    # of zero silently rewrites the question.
    "ruler": (("length",),),
    "jug": (("capacity",), ("level",)),
    "scale_dial": (("max",), ("value",)),
    "clock": (("hour",),),

    # Composite.
    "compare": (("items",),),
}


def _net_requirements(spec: dict) -> tuple[tuple[str, ...], ...]:
    """A net's dimensions depend on which solid it unfolds.

    A cube is one edge; a rectangular prism is three different faces. Asking
    for the prism's three from a cube spec would refuse a figure that is
    perfectly well specified.
    """
    solid = str(spec.get("solid", "")).strip().lower().replace("_", " ")
    if solid == "rectangular prism":
        return (("solid",), ("length",), ("width",), ("height",))
    return (("solid",), ("edge", "length"))


# Types whose requirements cannot be read off the spec's type alone.
CONDITIONAL = {"net": _net_requirements}


def _is_absent(value) -> bool:
    """Whether a spec carries nothing usable under a key.

    An empty string and an empty list are absences that happen to be typed.
    Zero and False are not: an empty jug and an unshaded bar are real figures.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def missing_requirements(spec: dict | None) -> list[str]:
    """The names a figure of this type needs and this spec does not carry.

    Empty means the spec is complete enough to draw honestly. It says nothing
    about whether the numbers are *right*, only that they came from the model
    rather than from a default argument.
    """
    if not isinstance(spec, dict):
        return []
    kind = str(spec.get("type", "")).strip()
    if kind in CONDITIONAL:
        groups = CONDITIONAL[kind](spec)
    else:
        groups = REQUIRED_KEYS.get(kind, ())
    missing = []
    for group in groups:
        if all(_is_absent(spec.get(key)) for key in group):
            missing.append(" or ".join(group))
    return missing
