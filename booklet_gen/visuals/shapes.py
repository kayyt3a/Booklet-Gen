"""Geometry, measurement and number figures.

Every renderer here answers a subtopic the Australian curriculum names and the
booklet previously had to teach in prose. The comment above each one says which
one, because a renderer nobody routes a subtopic to is dead weight that still
has to be kept legible.

Figures are authored close to the size they print at (the worked-example box is
6cm x 4cm, about 2.4in x 1.6in). Drawing one four inches wide and letting the
page shrink it is what makes labels illegible: the shapes shrink, the words do
not, and the legibility pass then has to blow the words back up until they
swamp the drawing.
"""
from __future__ import annotations

import math
from pathlib import Path

from .style import (
    ACCENT_COLOR,
    DPI,
    LINE_COLOR,
    LINE_WIDTH,
    SHADE_ALPHA,
    SHADE_COLOR,
    UNKNOWN_LABEL,
    _dim_label,
    _finish,
    _Fonts,
    _measure,
    _pixel_axes,
    _pretty_num,
    _px,
    _scale_note,
    _SHAPE_SIDES,
    _side_rotation,
    _unit_suffix,
)


def _right_angle_mark(ax, vertex, u, v, size: float) -> None:
    """The little square that says "this really is 90 degrees".

    `u` and `v` are unit vectors along the two arms. Without it a perpendicular
    height reads as just another line, and a right-angled triangle drawn at a
    slight angle on a home printer reads as scalene.
    """
    vx, vy = vertex
    p1 = (vx + u[0] * size, vy + u[1] * size)
    p2 = (vx + (u[0] + v[0]) * size, vy + (u[1] + v[1]) * size)
    p3 = (vx + v[0] * size, vy + v[1] * size)
    ax.plot([p1[0], p2[0], p3[0]], [p1[1], p2[1], p3[1]],
            color=LINE_COLOR, linewidth=LINE_WIDTH * 0.7)


def _unit(dx: float, dy: float) -> tuple[float, float]:
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


# ---------------------------------------------------------------------------
# Angles: "angles as turns and comparison to a right angle" (Years 3-4),
# "angles on a straight line, at a point and in a triangle" (Years 5-6).
# ---------------------------------------------------------------------------

def angle(spec: dict, out: Path, f: _Fonts) -> None:
    """One or more angles at a shared vertex, arcs marked and labelled.

    base "open"  a single angle standing alone
    base "line"  angles filling a straight line, so they must total 180
    base "point" angles filling a full turn, so they must total 360

    The totals are enforced rather than trusted. A figure claiming three angles
    on a straight line that sum to 200 is not a hard question, it is an
    impossible one, and the child who adds them up correctly is told they are
    wrong by the answer key.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc

    raw = spec.get("angles")
    if raw is None:
        raw = [spec.get("degrees", 45)]
    if isinstance(raw, (int, float)):
        raw = [raw]
    values = [float(a) for a in raw]
    base = str(spec.get("base", "open")).strip().lower()
    if base not in {"open", "line", "point"}:
        raise ValueError(f"angle base must be open, line or point, got {base!r}")
    if not (1 <= len(values) <= 6):
        raise ValueError(f"an angle figure takes 1-6 angles, got {len(values)}")
    if any(v <= 0 or v >= 360 for v in values):
        raise ValueError(f"angles must be between 0 and 360, got {values}")
    total = sum(values)
    if base == "line" and abs(total - 180) > 0.01:
        raise ValueError(f"angles on a straight line must total 180, got {total}")
    if base == "point" and abs(total - 360) > 0.01:
        raise ValueError(f"angles at a point must total 360, got {total}")
    if base == "open" and len(values) != 1:
        raise ValueError("an open angle figure shows exactly one angle")
    if base == "open" and total > 350:
        raise ValueError(f"a single open angle must be under 350, got {total}")

    labels = spec.get("labels") or []
    if isinstance(labels, str):
        labels = [labels]

    fig, ax = plt.subplots(figsize=(2.9, 2.5), dpi=180)
    arm = 1.0

    def ray(deg: float) -> None:
        a = math.radians(deg)
        ax.plot([0, arm * math.cos(a)], [0, arm * math.sin(a)],
                color=LINE_COLOR, linewidth=LINE_WIDTH, solid_capstyle="round")

    start = 0.0
    ray(start)
    for i, v in enumerate(values):
        end = start + v
        ray(end)
        mid = start + v / 2
        # A right angle gets the square, not an arc: that is how a child is
        # taught to recognise one on sight.
        if abs(v - 90) < 0.01:
            a0, a1 = math.radians(start), math.radians(end)
            _right_angle_mark(ax, (0, 0),
                              (math.cos(a0), math.sin(a0)),
                              (math.cos(a1), math.sin(a1)), 0.16)
        else:
            r = 0.30 + 0.055 * i      # nested arcs stay apart when angles are small
            ax.add_patch(Arc((0, 0), 2 * r, 2 * r, theta1=start, theta2=end,
                             edgecolor=LINE_COLOR, linewidth=LINE_WIDTH * 0.8))
        text = str(labels[i]) if i < len(labels) else f"{_pretty_num(v)}°"
        if text:
            rl = 0.52 + 0.055 * i
            am = math.radians(mid)
            ax.text(rl * math.cos(am), rl * math.sin(am), text,
                    ha="center", va="center", fontsize=f.label(11),
                    color=LINE_COLOR)
        start = end

    ax.add_patch(plt.Circle((0, 0), 0.035, facecolor=LINE_COLOR, edgecolor="none"))
    ax.set_xlim(-1.2 if base != "open" else -0.3, 1.2)
    ax.set_ylim(-1.2 if base == "point" else -0.3, 1.2)
    # A figure drawn to scale hands over an angle the question is asking for,
    # exactly as a drawn-to-scale length would.
    _scale_note(ax, spec, f)
    _finish(fig, ax, out)


# ---------------------------------------------------------------------------
# Triangles: "area of rectangles, triangles and parallelograms" (Years 5-6).
# ---------------------------------------------------------------------------

def triangle(spec: dict, out: Path, f: _Fonts) -> None:
    """A triangle with its base and perpendicular height, or its three sides.

    The perpendicular height is the whole difficulty of triangle area: a child
    who has only ever seen the slant side measured will use it. It is drawn
    dashed, in the second ink, with a right-angle mark where it meets the base,
    because it is a construction line and not a side of the shape.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    b = float(spec.get("base", 8))
    h = float(spec.get("height", 5)) if spec.get("height") is not None else None
    sides = spec.get("sides")
    if b <= 0 or b > 200:
        raise ValueError(f"triangle base out of range: {b}")
    if h is not None and (h <= 0 or h > 200):
        raise ValueError(f"triangle height out of range: {h}")
    if h is None and not sides:
        raise ValueError("a triangle needs a height or a set of side lengths")
    if sides is not None and len(sides) != 3:
        raise ValueError(f"a triangle has 3 sides, got {len(sides)}")

    # Where the apex sits along the base. Straight above the middle reads as
    # isosceles; a little off centre reads as a general triangle, which is what
    # keeps a child from assuming the two slant sides are equal.
    apex_at = float(spec.get("apex", 0.34))
    apex_at = min(0.9, max(0.1, apex_at))
    hh = h if h is not None else b * 0.6
    pts = [(0.0, 0.0), (b, 0.0), (b * apex_at, hh)]

    scale = 2.0 / max(b, hh)
    fig, ax = plt.subplots(figsize=(b * scale + 1.0, hh * scale + 0.9), dpi=180)
    ax.add_patch(Polygon(pts, closed=True, facecolor=SHADE_COLOR,
                         alpha=SHADE_ALPHA * 0.45,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    us = _unit_suffix(spec)

    if h is not None:
        foot = (b * apex_at, 0.0)
        ax.plot([foot[0], pts[2][0]], [0, hh], color=ACCENT_COLOR,
                linewidth=LINE_WIDTH, linestyle=(0, (4, 3)))
        _right_angle_mark(ax, foot, (1, 0), (0, 1), min(b, hh) * 0.09)
        label = _dim_label(spec, "height", h, us)
        ax.text(foot[0] + b * 0.035, hh / 2, label, ha="left", va="center",
                fontsize=f.label(10.5), color=ACCENT_COLOR)
    ax.text(b / 2, -hh * 0.07, _dim_label(spec, "base", b, us),
            ha="center", va="top", fontsize=f.label(11))

    if sides:
        edges = [((pts[1], pts[2]), "right"), ((pts[2], pts[0]), "left")]
        # sides[0] is the base, already drawn; the other two go on the slants.
        for (p, q), side in zip(edges, ("right", "left")):
            idx = 1 if side == "right" else 2
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            off = b * 0.06 if side == "right" else -b * 0.06
            ax.text(mx + off, my, f"{_pretty_num(float(sides[idx]))}{us}",
                    ha="left" if side == "right" else "right", va="center",
                    fontsize=f.label(10.5))

    ax.set_xlim(-b * 0.22, b * 1.22)
    ax.set_ylim(-hh * 0.24, hh * 1.14)
    _scale_note(ax, spec, f)
    _finish(fig, ax, out)


def right_triangle(spec: dict, out: Path, f: _Fonts) -> None:
    """A right-angled triangle: Pythagoras (Years 7-8) and trigonometry (9-10).

    Legs `a` along the bottom and `b` up the left, hypotenuse `c` between them,
    right angle at the origin. Any of the three may be listed in "unknown", in
    which case its label becomes "?" and the figure is captioned not to scale,
    because otherwise a child with a ruler reads the answer straight off the
    page instead of using the theorem the page is teaching.

    "angle" marks the acute angle at the bottom-right vertex, which is the one
    a trigonometry question names.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Arc, Polygon

    a = float(spec.get("a", spec.get("base", 4)))
    b = float(spec.get("b", spec.get("height", 3)))
    c = spec.get("c", spec.get("hypotenuse"))
    c = float(c) if c is not None else math.hypot(a, b)
    if not (0 < a <= 500 and 0 < b <= 500 and 0 < c <= 1000):
        raise ValueError(f"right triangle sides out of range: {a}, {b}, {c}")

    scale = 2.0 / max(a, b)
    fig, ax = plt.subplots(figsize=(a * scale + 1.1, b * scale + 1.0), dpi=180)
    pts = [(0.0, 0.0), (a, 0.0), (0.0, b)]
    ax.add_patch(Polygon(pts, closed=True, facecolor=SHADE_COLOR,
                         alpha=SHADE_ALPHA * 0.45,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    _right_angle_mark(ax, (0, 0), (1, 0), (0, 1), min(a, b) * 0.11)

    us = _unit_suffix(spec)
    ax.text(a / 2, -b * 0.07, _dim_label(spec, "a", a, us),
            ha="center", va="top", fontsize=f.label(11))
    side = _dim_label(spec, "b", b, us)
    ax.text(-a * 0.05, b / 2, side, ha="right", va="center",
            fontsize=f.label(11), rotation=_side_rotation(side))
    # The hypotenuse label sits outside the shape, level with the midpoint of
    # the slope, rather than written along it: at print size a rotated label on
    # the hypotenuse collides with the line it is naming.
    ax.text(a * 0.56, b * 0.56, _dim_label(spec, "c", c, us),
            ha="left", va="bottom", fontsize=f.label(11))

    marked = spec.get("angle")
    if marked is not None:
        theta = math.degrees(math.atan2(b, a))
        r = min(a, b) * 0.30
        ax.add_patch(Arc((a, 0), 2 * r, 2 * r, theta1=180 - theta, theta2=180,
                         edgecolor=ACCENT_COLOR, linewidth=LINE_WIDTH * 0.9))
        text = (f"{_pretty_num(float(marked))}°"
                if isinstance(marked, (int, float)) else str(marked))
        ax.text(a - r * 1.35, b * 0.10 + r * 0.28, text, ha="right", va="bottom",
                fontsize=f.label(10.5), color=ACCENT_COLOR)

    ax.set_xlim(-a * 0.24, a * 1.14)
    ax.set_ylim(-b * 0.22, b * 1.16)
    _scale_note(ax, spec, f)
    _finish(fig, ax, out)


def parallelogram(spec: dict, out: Path, f: _Fonts) -> None:
    """Base, perpendicular height and optional slant side (Years 5-6 area).

    Kept apart from `triangle` on purpose. The mistake a parallelogram invites
    is multiplying base by the slant side, so the slant side is labelled only
    when the question gives it, and the height is always drawn as the dashed
    construction line it is.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    b = float(spec.get("base", 8))
    h = float(spec.get("height", 4))
    if not (0 < b <= 200 and 0 < h <= 200):
        raise ValueError(f"parallelogram base/height out of range: {b}, {h}")
    lean = b * 0.28

    pts = [(0.0, 0.0), (b, 0.0), (b + lean, h), (lean, h)]
    scale = 2.0 / max(b + lean, h)
    fig, ax = plt.subplots(figsize=((b + lean) * scale + 1.1, h * scale + 1.0),
                           dpi=180)
    ax.add_patch(Polygon(pts, closed=True, facecolor=SHADE_COLOR,
                         alpha=SHADE_ALPHA * 0.45,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    us = _unit_suffix(spec)
    # Height dropped from the top-left corner onto the base.
    ax.plot([lean, lean], [h, 0], color=ACCENT_COLOR, linewidth=LINE_WIDTH,
            linestyle=(0, (4, 3)))
    _right_angle_mark(ax, (lean, 0), (1, 0), (0, 1), min(b, h) * 0.10)
    ax.text(lean + b * 0.03, h / 2, _dim_label(spec, "height", h, us),
            ha="left", va="center", fontsize=f.label(10.5), color=ACCENT_COLOR)
    ax.text(b / 2, -h * 0.09, _dim_label(spec, "base", b, us),
            ha="center", va="top", fontsize=f.label(11))
    if spec.get("side") is not None:
        s = float(spec["side"])
        ax.text(b + lean * 0.62, h * 0.5, f"{_pretty_num(s)}{us}",
                ha="left", va="center", fontsize=f.label(10.5))

    ax.set_xlim(-b * 0.16, b + lean + b * 0.28)
    ax.set_ylim(-h * 0.28, h * 1.16)
    _scale_note(ax, spec, f)
    _finish(fig, ax, out)


def trapezium(spec: dict, out: Path, f: _Fonts) -> None:
    """The two parallel sides and the height between them (Years 7-8 area)."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    top = float(spec.get("top", spec.get("a", 5)))
    bottom = float(spec.get("bottom", spec.get("b", 9)))
    h = float(spec.get("height", 4))
    if not (0 < top <= 200 and 0 < bottom <= 200 and 0 < h <= 200):
        raise ValueError(f"trapezium dimensions out of range: {top}, {bottom}, {h}")
    if abs(top - bottom) < 1e-9:
        raise ValueError("a trapezium's parallel sides must differ; "
                         "equal sides make it a parallelogram")

    inset = (bottom - top) / 2
    pts = [(0.0, 0.0), (bottom, 0.0), (bottom - inset, h), (inset, h)]
    span = max(bottom, top, h)
    scale = 2.0 / span
    fig, ax = plt.subplots(figsize=(bottom * scale + 1.1, h * scale + 1.0), dpi=180)
    ax.add_patch(Polygon(pts, closed=True, facecolor=SHADE_COLOR,
                         alpha=SHADE_ALPHA * 0.45,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    us = _unit_suffix(spec)
    hx = bottom * 0.5
    ax.plot([hx, hx], [0, h], color=ACCENT_COLOR, linewidth=LINE_WIDTH,
            linestyle=(0, (4, 3)))
    _right_angle_mark(ax, (hx, 0), (1, 0), (0, 1), min(bottom, h) * 0.09)
    ax.text(hx + bottom * 0.03, h / 2, _dim_label(spec, "height", h, us),
            ha="left", va="center", fontsize=f.label(10.5), color=ACCENT_COLOR)
    ax.text(bottom / 2, h + h * 0.06, _dim_label(spec, "top", top, us),
            ha="center", va="bottom", fontsize=f.label(11))
    ax.text(bottom / 2, -h * 0.09, _dim_label(spec, "bottom", bottom, us),
            ha="center", va="top", fontsize=f.label(11))

    ax.set_xlim(-bottom * 0.14, bottom * 1.14)
    ax.set_ylim(-h * 0.30, h * 1.34)
    _scale_note(ax, spec, f)
    _finish(fig, ax, out)


def circle(spec: dict, out: Path, f: _Fonts) -> None:
    """A circle with its radius or diameter drawn and labelled (Years 7-8).

    Distinct from `circle_slices`, which is a fraction picture with no
    measurement on it. Circumference and area questions need the line the
    formula uses to be visible, and need it named, because half the error rate
    in that topic is using a diameter where the formula wants a radius.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle as MplCircle

    r = spec.get("radius")
    d = spec.get("diameter")
    if r is None and d is None:
        raise ValueError("a circle needs a radius or a diameter")
    if r is not None and d is not None:
        raise ValueError("give a circle a radius or a diameter, not both")
    show_diameter = r is None
    value = float(d) if show_diameter else float(r)
    if not (0 < value <= 1000):
        raise ValueError(f"circle measurement out of range: {value}")

    us = _unit_suffix(spec)
    fig, ax = plt.subplots(figsize=(2.5, 2.5), dpi=180)
    ax.add_patch(MplCircle((0, 0), 1.0, facecolor=SHADE_COLOR,
                           alpha=SHADE_ALPHA * 0.35,
                           edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    ax.add_patch(MplCircle((0, 0), 0.035, facecolor=LINE_COLOR, edgecolor="none"))
    key = "diameter" if show_diameter else "radius"
    text = _dim_label(spec, key, value, us)
    if show_diameter:
        ax.plot([-1, 1], [0, 0], color=ACCENT_COLOR, linewidth=LINE_WIDTH)
        ax.text(0, 0.08, f"d = {text}", ha="center", va="bottom",
                fontsize=f.label(11), color=ACCENT_COLOR)
    else:
        ax.plot([0, 1], [0, 0], color=ACCENT_COLOR, linewidth=LINE_WIDTH)
        ax.text(0.5, 0.08, f"r = {text}", ha="center", va="bottom",
                fontsize=f.label(11), color=ACCENT_COLOR)
    ax.set_xlim(-1.18, 1.18)
    ax.set_ylim(-1.18, 1.30)
    _scale_note(ax, spec, f)
    _finish(fig, ax, out)


# ---------------------------------------------------------------------------
# Measurement pictures
# ---------------------------------------------------------------------------

def grid_area(spec: dict, out: Path, f: _Fonts) -> None:
    """A shape on squared paper, for "area by counting squares" (Years 3-4).

    Before area is a formula it is a count, and the count needs squares to
    count. Optionally a corner is cut out, which is how the same picture
    carries composite area later.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    w = int(spec.get("width", 6))
    h = int(spec.get("height", 4))
    cw = int(spec.get("cut_width", 0) or 0)
    ch = int(spec.get("cut_height", 0) or 0)
    if not (1 <= w <= 14 and 1 <= h <= 14):
        raise ValueError(f"a counting grid must be 1-14 by 1-14, got {w}x{h}")
    if cw or ch:
        if not (0 < cw < w and 0 < ch < h):
            raise ValueError("the cut must be strictly inside the rectangle")

    fig, ax = plt.subplots(figsize=(0.30 * w + 0.5, 0.30 * h + 0.5), dpi=180)
    if cw and ch:
        pts = [(0, 0), (w, 0), (w, h - ch), (w - cw, h - ch), (w - cw, h), (0, h)]
    else:
        pts = [(0, 0), (w, 0), (w, h), (0, h)]
    ax.add_patch(Polygon(pts, closed=True, facecolor=SHADE_COLOR,
                         alpha=SHADE_ALPHA * 0.40, edgecolor="none"))
    # The faint squares are the point of the figure, so they are drawn over the
    # fill, not under it.
    for x in range(w + 1):
        ax.plot([x, x], [0, h], color=LINE_COLOR, linewidth=0.6, alpha=0.55)
    for y in range(h + 1):
        ax.plot([0, w], [y, y], color=LINE_COLOR, linewidth=0.6, alpha=0.55)
    ax.add_patch(Polygon(pts, closed=True, fill=False,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH * 1.5))
    ax.set_xlim(-0.35, w + 0.35)
    ax.set_ylim(-0.35, h + 0.35)
    _finish(fig, ax, out)


def symmetry(spec: dict, out: Path, f: _Fonts) -> None:
    """A shape with a mirror line drawn through it (Years 3-4 symmetry).

    Two questions come off the same picture: "is the dashed line a line of
    symmetry?" with the line shown, and "where would the line of symmetry go?"
    with `show_mirror` false. Neither can be asked in words.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle as MplCircle, Polygon, Rectangle, RegularPolygon

    name = str(spec.get("shape", "rectangle")).strip().lower()
    mirror = str(spec.get("mirror", "vertical")).strip().lower()
    if mirror not in {"vertical", "horizontal", "diagonal"}:
        raise ValueError(f"mirror must be vertical, horizontal or diagonal, got {mirror!r}")
    show = bool(spec.get("show_mirror", True))

    fig, ax = plt.subplots(figsize=(2.3, 2.3), dpi=180)
    if name == "circle":
        ax.add_patch(MplCircle((0, 0), 0.85, facecolor=SHADE_COLOR,
                               alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                               linewidth=LINE_WIDTH))
    elif name == "rectangle":
        ax.add_patch(Rectangle((-0.95, -0.55), 1.9, 1.1, facecolor=SHADE_COLOR,
                               alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                               linewidth=LINE_WIDTH))
    elif name in {"arrow", "kite"}:
        pts = ([(0, 0.9), (0.55, 0.1), (0.22, 0.1), (0.22, -0.9),
                (-0.22, -0.9), (-0.22, 0.1), (-0.55, 0.1)] if name == "arrow"
               else [(0, 0.95), (0.6, 0.1), (0, -0.95), (-0.6, 0.1)])
        ax.add_patch(Polygon(pts, closed=True, facecolor=SHADE_COLOR,
                             alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                             linewidth=LINE_WIDTH))
    else:
        sides = _SHAPE_SIDES.get(name)
        if sides is None:
            raise ValueError(f"unknown shape {name!r}")
        ax.add_patch(RegularPolygon((0, 0), sides, radius=0.88,
                                    orientation=math.pi / 4 if sides == 4 else 0.0,
                                    facecolor=SHADE_COLOR, alpha=SHADE_ALPHA,
                                    edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    if show:
        ends = {"vertical": ((0, -1.15), (0, 1.15)),
                "horizontal": ((-1.15, 0), (1.15, 0)),
                "diagonal": ((-1.05, -1.05), (1.05, 1.05))}[mirror]
        ax.plot([ends[0][0], ends[1][0]], [ends[0][1], ends[1][1]],
                color=ACCENT_COLOR, linewidth=LINE_WIDTH,
                linestyle=(0, (5, 3)))
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-1.25, 1.25)
    _finish(fig, ax, out)


def ruler(spec: dict, out: Path, f: _Fonts) -> None:
    """An object laid against a ruler (Years 1-4, formal units for length).

    Measuring is a reading-off skill, and it cannot be practised on a page that
    only ever states the length in words. The object starts at zero, because
    starting it elsewhere is a different and much later skill.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    length = float(spec.get("length", 7))
    max_cm = int(spec.get("max", 0) or 0) or int(math.ceil(length) + 2)
    unit = str(spec.get("unit", "cm"))
    if not (0 < length <= max_cm) or max_cm > 30:
        raise ValueError(f"ruler length {length} does not fit a {max_cm} {unit} ruler")

    fig, ax = plt.subplots(figsize=(0.22 * max_cm + 0.5, 1.5), dpi=180)
    # The object.
    ax.add_patch(Rectangle((0, 0.55), length, 0.42, facecolor=SHADE_COLOR,
                           alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                           linewidth=LINE_WIDTH))
    if spec.get("object"):
        ax.text(length / 2, 1.06, str(spec["object"]), ha="center", va="bottom",
                fontsize=f.label(10.5), color=LINE_COLOR)
    # The ruler.
    ax.add_patch(Rectangle((0, -0.62), max_cm, 0.62, facecolor="white",
                           edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    for i in range(max_cm * 2 + 1):
        x = i / 2
        tall = (i % 2 == 0)
        ax.plot([x, x], [0, -0.20 if tall else -0.11],
                color=LINE_COLOR, linewidth=LINE_WIDTH * (0.8 if tall else 0.5))
        if tall:
            ax.text(x, -0.26, str(i // 2), ha="center", va="top",
                    fontsize=f.label(8.5), color=LINE_COLOR)
    ax.text(max_cm / 2, -0.80, unit, ha="center", va="top",
            fontsize=f.label(9), color=LINE_COLOR)
    ax.set_xlim(-0.5, max_cm + 0.5)
    ax.set_ylim(-1.15, 1.45)
    ax.set_aspect("auto")
    ax.axis("off")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.08, transparent=False)
    plt.close(fig)


def jug(spec: dict, out: Path, f: _Fonts) -> None:
    """A measuring jug with a liquid level (Years 1-4, capacity).

    Capacity is taught with a jug in every classroom in the country and was
    taught here with a paragraph.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon, Rectangle

    capacity = float(spec.get("capacity", 1000))
    level = float(spec.get("level", 0))
    unit = str(spec.get("unit", "mL"))
    divisions = int(spec.get("divisions", 10))
    if capacity <= 0 or not (0 <= level <= capacity):
        raise ValueError(f"jug level {level} outside 0 to {capacity}")
    if not (2 <= divisions <= 20):
        raise ValueError(f"a jug needs 2-20 graduations, got {divisions}")

    fig, ax = plt.subplots(figsize=(1.9, 2.5), dpi=180)
    w, h = 1.0, 2.0
    frac = level / capacity
    if frac > 0:
        ax.add_patch(Rectangle((0, 0), w, h * frac, facecolor=SHADE_COLOR,
                               alpha=SHADE_ALPHA, edgecolor="none"))
        ax.plot([0, w], [h * frac, h * frac], color=LINE_COLOR,
                linewidth=LINE_WIDTH)
    # Body: an open-topped jug, so the outline is three sides plus a spout.
    ax.plot([0, 0, w, w], [h, 0, 0, h], color=LINE_COLOR,
            linewidth=LINE_WIDTH * 1.3, solid_capstyle="round")
    ax.add_patch(Polygon([(w, h), (w + 0.22, h - 0.06), (w, h - 0.22)],
                         closed=True, fill=False, edgecolor=LINE_COLOR,
                         linewidth=LINE_WIDTH))
    for i in range(divisions + 1):
        y = h * i / divisions
        major = (i % max(1, divisions // 5) == 0)
        ax.plot([0, w * (0.30 if major else 0.18)], [y, y],
                color=LINE_COLOR, linewidth=LINE_WIDTH * 0.6)
        if major:
            ax.text(-0.06, y, _pretty_num(capacity * i / divisions),
                    ha="right", va="center", fontsize=f.label(8.5),
                    color=LINE_COLOR)
    ax.text(w / 2, -0.14, unit, ha="center", va="top",
            fontsize=f.label(9.5), color=LINE_COLOR)
    ax.set_xlim(-0.85, w + 0.42)
    ax.set_ylim(-0.5, h + 0.18)
    _finish(fig, ax, out)


def scale_dial(spec: dict, out: Path, f: _Fonts) -> None:
    """A kitchen-scale dial with a needle (Years 1-4, mass).

    Same argument as the jug and the clock: mass is read off a dial, and a
    booklet that only ever states the mass in words never asks the child to
    read one.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle as MplCircle

    maximum = float(spec.get("max", 1000))
    value = float(spec.get("value", 0))
    unit = str(spec.get("unit", "g"))
    divisions = int(spec.get("divisions", 10))
    if maximum <= 0 or not (0 <= value <= maximum):
        raise ValueError(f"dial value {value} outside 0 to {maximum}")
    if not (2 <= divisions <= 20):
        raise ValueError(f"a dial needs 2-20 graduations, got {divisions}")

    fig, ax = plt.subplots(figsize=(2.5, 2.5), dpi=180)
    ax.add_patch(MplCircle((0, 0), 1.0, fill=False, edgecolor=LINE_COLOR,
                           linewidth=LINE_WIDTH * 1.4))
    # A full turn is the full scale, starting at the top and going clockwise,
    # which is how a spring scale actually reads.
    for i in range(divisions + 1):
        if i == divisions:
            continue                     # the last tick lands on the first
        a = math.radians(90 - 360 * i / divisions)
        ax.plot([0.86 * math.cos(a), 0.97 * math.cos(a)],
                [0.86 * math.sin(a), 0.97 * math.sin(a)],
                color=LINE_COLOR, linewidth=LINE_WIDTH * 0.9)
        ax.text(0.68 * math.cos(a), 0.68 * math.sin(a),
                _pretty_num(maximum * i / divisions), ha="center", va="center",
                fontsize=f.label(9), color=LINE_COLOR)
    a = math.radians(90 - 360 * value / maximum)
    ax.plot([0, 0.60 * math.cos(a)], [0, 0.60 * math.sin(a)],
            color=ACCENT_COLOR, linewidth=LINE_WIDTH * 2.4,
            solid_capstyle="round")
    ax.add_patch(MplCircle((0, 0), 0.05, facecolor=LINE_COLOR, edgecolor="none"))
    ax.text(0, -1.14, unit, ha="center", va="top", fontsize=f.label(9.5),
            color=LINE_COLOR)
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.42, 1.15)
    _finish(fig, ax, out)


# Australian coins and notes, by value in cents. Notes are rectangles with the
# denomination on them; coins are discs. Nothing here imitates the artwork on
# real currency, which would be both pointless at this size and a reproduction
# question nobody needs.
_COIN_CENTS = {5: "5c", 10: "10c", 20: "20c", 50: "50c", 100: "$1", 200: "$2"}
_NOTE_CENTS = {500: "$5", 1000: "$10", 2000: "$20", 5000: "$50", 10000: "$100"}
# Real diameters in millimetres. Australian coins are not sized in order of
# value: the 50c is the largest and the $2 is the smallest, which is exactly
# the thing a child gets caught by, so drawing them in value order would teach
# the wrong cue.
_COIN_MM = {5: 19.4, 10: 23.6, 20: 28.5, 50: 31.5, 100: 25.0, 200: 20.5}


def money(spec: dict, out: Path, f: _Fonts) -> None:
    """Australian coins and notes (Years 1-4, money and simple change).

    "Money and simple change" is a named subtopic and there was no way to show
    money, so every money question had to list the coins in a sentence, which
    turns counting coins into reading comprehension.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle as MplCircle, FancyBboxPatch

    items = spec.get("items") or []
    if isinstance(items, (int, float, str)):
        items = [items]
    if not (1 <= len(items) <= 8):
        raise ValueError(f"a money figure shows 1-8 items, got {len(items)}")

    parsed: list[tuple[int, str, bool]] = []       # (cents, label, is_note)
    for raw in items:
        cents = _money_cents(raw)
        if cents in _COIN_CENTS:
            parsed.append((cents, _COIN_CENTS[cents], False))
        elif cents in _NOTE_CENTS:
            parsed.append((cents, _NOTE_CENTS[cents], True))
        else:
            raise ValueError(f"{raw!r} is not an Australian coin or note")

    # Coins are drawn in proportion to the real thing, so size is a usable cue
    # rather than a misleading one. Notes are all one size here; the real ones
    # differ only in length, which does not survive being 1cm wide on paper.
    fig, ax = plt.subplots(figsize=(0.70 * len(parsed) + 0.35, 1.10), dpi=180)
    x = 0.0
    for cents, label, is_note in parsed:
        if is_note:
            w, h = 0.92, 0.46
            ax.add_patch(FancyBboxPatch((x, -h / 2), w, h,
                                        boxstyle="round,pad=0.0,rounding_size=0.05",
                                        facecolor=SHADE_COLOR, alpha=SHADE_ALPHA * 0.5,
                                        edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
            ax.text(x + w / 2, 0, label, ha="center", va="center",
                    fontsize=f.label(11), color=LINE_COLOR)
            x += w + 0.14
        else:
            r = 0.34 * _COIN_MM[cents] / _COIN_MM[50]
            ax.add_patch(MplCircle((x + r, 0), r, facecolor=SHADE_COLOR,
                                   alpha=SHADE_ALPHA * 0.5, edgecolor=LINE_COLOR,
                                   linewidth=LINE_WIDTH))
            ax.text(x + r, 0, label, ha="center", va="center",
                    fontsize=f.label(10), color=LINE_COLOR)
            x += 2 * r + 0.12
    ax.set_xlim(-0.12, x)
    ax.set_ylim(-0.42, 0.42)
    _finish(fig, ax, out)


def _money_cents(raw) -> int:
    """Read "$2", "50c", 200 or 2.0 as a whole number of cents."""
    if isinstance(raw, (int, float)):
        # A bare number is cents when it is one of the coin values, dollars
        # otherwise. 2 means two dollars; 20 means twenty cents.
        n = float(raw)
        if abs(n - round(n)) < 1e-9 and int(round(n)) in _COIN_CENTS:
            return int(round(n))
        return int(round(n * 100))
    s = str(raw).strip().lower().replace(" ", "")
    if s.endswith("c"):
        return int(round(float(s[:-1])))
    if s.startswith("$"):
        return int(round(float(s[1:]) * 100))
    return int(round(float(s) * 100))


# ---------------------------------------------------------------------------
# Solids and nets
# ---------------------------------------------------------------------------

_SOLID_NAMES = ("cube", "rectangular prism", "sphere", "cone", "cylinder",
                "square pyramid", "triangular prism")


def _draw_solid(ax, name: str, cx: float) -> None:
    """One named solid, drawn in oblique projection around (cx, 0)."""
    from matplotlib.patches import Arc, Ellipse, Polygon

    def line(p, q, dashed=False):
        ax.plot([p[0], q[0]], [p[1], q[1]], color=LINE_COLOR,
                linewidth=LINE_WIDTH,
                linestyle=(0, (3, 2.5)) if dashed else "solid")

    ox, oy = 0.26, 0.22                     # the receding offset
    if name in {"cube", "rectangular prism"}:
        w, h = (0.62, 0.62) if name == "cube" else (0.86, 0.50)
        x0, y0 = cx - w / 2 - ox / 2, -h / 2 - oy / 2
        A, B = (x0, y0), (x0 + w, y0)
        C, D = (x0 + w, y0 + h), (x0, y0 + h)
        A2, B2 = (A[0] + ox, A[1] + oy), (B[0] + ox, B[1] + oy)
        C2, D2 = (C[0] + ox, C[1] + oy), (D[0] + ox, D[1] + oy)
        for p, q in [(A, B), (B, C), (C, D), (D, A), (D, D2), (C, C2),
                     (B, B2), (D2, C2), (C2, B2)]:
            line(p, q)
        for p, q in [(A, A2), (A2, B2), (A2, D2)]:
            line(p, q, dashed=True)
    elif name == "sphere":
        from matplotlib.patches import Circle as MplCircle
        ax.add_patch(MplCircle((cx, 0), 0.52, fill=False,
                               edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        # The equator is what stops a sphere reading as a flat circle.
        ax.add_patch(Arc((cx, 0), 1.04, 0.40, theta1=180, theta2=360,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        ax.add_patch(Arc((cx, 0), 1.04, 0.40, theta1=0, theta2=180,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH,
                         linestyle=(0, (3, 2.5))))
    elif name == "cone":
        r, h = 0.44, 0.90
        apex = (cx, h / 2)
        line(apex, (cx - r, -h / 2))
        line(apex, (cx + r, -h / 2))
        ax.add_patch(Arc((cx, -h / 2), 2 * r, 2 * r * 0.34,
                         theta1=180, theta2=360,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        ax.add_patch(Arc((cx, -h / 2), 2 * r, 2 * r * 0.34, theta1=0, theta2=180,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH,
                         linestyle=(0, (3, 2.5))))
    elif name == "cylinder":
        r, h = 0.40, 0.86
        ell = r * 0.34
        line((cx - r, -h / 2), (cx - r, h / 2))
        line((cx + r, -h / 2), (cx + r, h / 2))
        ax.add_patch(Ellipse((cx, h / 2), 2 * r, 2 * ell, fill=False,
                             edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        ax.add_patch(Arc((cx, -h / 2), 2 * r, 2 * ell, theta1=180, theta2=360,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        ax.add_patch(Arc((cx, -h / 2), 2 * r, 2 * ell, theta1=0, theta2=180,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH,
                         linestyle=(0, (3, 2.5))))
    elif name == "square pyramid":
        w, h = 0.66, 0.80
        x0, y0 = cx - w / 2 - ox / 2, -h / 2
        A, B = (x0, y0), (x0 + w, y0)
        A2, B2 = (A[0] + ox, A[1] + oy), (B[0] + ox, B[1] + oy)
        apex = (cx + ox / 2, h / 2)
        for p, q in [(A, B), (B, B2), (B2, A2), (A, apex), (B, apex), (B2, apex)]:
            line(p, q)
        for p, q in [(A, A2), (A2, apex)]:
            line(p, q, dashed=True)
    elif name == "triangular prism":
        w, h, d = 0.62, 0.66, 0.30
        left, right = cx - w / 2 - d / 2, cx + w / 2 - d / 2
        A, B, T = (left, -h / 2), (right, -h / 2), ((left + right) / 2, h / 2)
        A2 = (A[0] + d, A[1] + d * 0.72)
        B2 = (B[0] + d, B[1] + d * 0.72)
        T2 = (T[0] + d, T[1] + d * 0.72)
        for p, q in [(A, B), (B, T), (T, A), (B, B2), (T, T2), (B2, T2)]:
            line(p, q)
        for p, q in [(A, A2), (A2, B2), (A2, T2)]:
            line(p, q, dashed=True)
    else:
        raise ValueError(f"unknown solid {name!r}")


def shape_3d(spec: dict, out: Path, f: _Fonts) -> None:
    """Up to three named solids side by side (Years 1-2 naming, 7-8 prisms).

    "Naming and sorting two and three dimensional shapes" is a Year 1 subtopic.
    The flat half could be drawn; the solid half could not, so a booklet asking
    a six year old to name a cone had to describe one instead.
    """
    import matplotlib.pyplot as plt

    names = spec.get("solids") or [spec.get("solid", "cube")]
    names = [str(n).strip().lower().replace("_", " ") for n in names][:3]
    if not names:
        raise ValueError("a solids figure needs at least one solid")
    for n in names:
        if n not in _SOLID_NAMES:
            raise ValueError(f"unknown solid {n!r}")
    show_labels = bool(spec.get("label", True))

    step = 1.45
    fig, ax = plt.subplots(figsize=(1.28 * len(names) + 0.25, 1.75), dpi=180)
    for i, name in enumerate(names):
        cx = i * step
        _draw_solid(ax, name, cx)
        if show_labels:
            # Two words do not fit under a 1.28in column at legible size, so
            # they stack rather than run into the neighbouring solid.
            ax.text(cx, -0.72, name.replace(" ", "\n"), ha="center", va="top",
                    fontsize=f.label(10), color=LINE_COLOR, linespacing=1.05)
    ax.set_xlim(-step / 2 - 0.1, step * (len(names) - 1) + step / 2 + 0.1)
    ax.set_ylim(-1.55 if show_labels else -0.72, 0.72)
    _finish(fig, ax, out)


def net(spec: dict, out: Path, f: _Fonts) -> None:
    """The unfolded net of a cube or rectangular prism (Years 7-8, surface area).

    Surface area is where a child either sees six faces or does not. A net is
    the picture that makes the six visible, and it is the one figure that makes
    "why six?" answerable without the teacher present.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    solid = str(spec.get("solid", "cube")).strip().lower().replace("_", " ")
    us = _unit_suffix(spec)
    if solid == "cube":
        e = float(spec.get("edge", spec.get("length", 4)))
        L = W = H = e
    elif solid == "rectangular prism":
        L = float(spec.get("length", 5))
        W = float(spec.get("width", 3))
        H = float(spec.get("height", 2))
    else:
        raise ValueError(f"no net for {solid!r}; use cube or rectangular prism")
    if not all(0 < v <= 100 for v in (L, W, H)):
        raise ValueError(f"net dimensions out of range: {L}, {W}, {H}")

    # Unfolded as a cross: a band of four faces going around the solid (front,
    # right, back, left), with the top and bottom hinged off the FRONT face.
    # Hinging them off the second face instead makes a picture that does not
    # fold up into the box it claims to be, which is worse than no picture: the
    # child who tries it is right and the booklet is wrong.
    faces = [
        (0.0, 0.0, L, H),                 # front
        (L, 0.0, W, H),                   # right
        (L + W, 0.0, L, H),               # back
        (L + W + L, 0.0, W, H),           # left
        (0.0, H, L, W),                   # top
        (0.0, -W, L, W),                  # bottom
    ]
    total_w = 2 * L + 2 * W
    total_h = H + 2 * W
    scale = 2.1 / max(total_w, total_h)
    fig, ax = plt.subplots(figsize=(total_w * scale + 0.85, total_h * scale + 0.75),
                           dpi=180)
    for x, y, w, h in faces:
        ax.add_patch(Rectangle((x, y), w, h, facecolor=SHADE_COLOR,
                               alpha=SHADE_ALPHA * 0.35, edgecolor=LINE_COLOR,
                               linewidth=LINE_WIDTH))
    # Label one edge of each distinct length, not every edge: six faces with
    # every side written on them is unreadable at 6cm wide.
    ax.text(L / 2, -W - total_h * 0.03, f"{_pretty_num(L)}{us}",
            ha="center", va="top", fontsize=f.label(10))
    ax.text(-total_w * 0.015, H + W / 2, f"{_pretty_num(W)}{us}",
            ha="right", va="center", fontsize=f.label(10))
    ax.text(-total_w * 0.015, H / 2, f"{_pretty_num(H)}{us}",
            ha="right", va="center", fontsize=f.label(10))
    ax.set_xlim(-total_w * 0.18, total_w * 1.03)
    ax.set_ylim(-W - total_h * 0.20, H + W + total_h * 0.06)
    _finish(fig, ax, out)


def parallel_lines(spec: dict, out: Path, f: _Fonts) -> None:
    """Two parallel lines cut by a transversal (Years 7-8, angle relationships).

    The eight angles are numbered so a question can name them: 1 to 4
    anticlockwise from the upper right at the top intersection, 5 to 8 the same
    at the bottom one. Give `labels` as a map from that number to the text.
    Corresponding, alternate and co-interior angles are a looking skill, and
    there is no way to ask about them without the figure.
    """
    import matplotlib.pyplot as plt

    theta = float(spec.get("angle", 60))
    if not (20 <= theta <= 160) or abs(theta - 90) < 1:
        raise ValueError(f"transversal angle must be 20-160 and not 90, got {theta}")
    labels = {str(k): str(v) for k, v in (spec.get("labels") or {}).items()}
    if not labels:
        raise ValueError("label at least one of the eight angles, or the figure "
                         "asks nothing")
    for k in labels:
        if k not in {str(i) for i in range(1, 9)}:
            raise ValueError(f"angle positions are 1-8, got {k!r}")

    fig, ax = plt.subplots(figsize=(2.9, 2.6), dpi=180)
    y_top, y_bot = 0.55, -0.55
    half = 1.15
    for y in (y_top, y_bot):
        ax.plot([-half, half], [y, y], color=LINE_COLOR, linewidth=LINE_WIDTH)
        # Arrowheads are how a diagram says "these are parallel".
        for xa, d in ((half, 1), (-half, -1)):
            ax.plot([xa - 0.10 * d, xa], [y + 0.07, y], color=LINE_COLOR,
                    linewidth=LINE_WIDTH * 0.8)
            ax.plot([xa - 0.10 * d, xa], [y - 0.07, y], color=LINE_COLOR,
                    linewidth=LINE_WIDTH * 0.8)

    t = math.radians(theta)
    # The transversal passes through the origin; x = y / tan(theta).
    def x_at(y: float) -> float:
        return y / math.tan(t)
    reach = 0.95
    ax.plot([x_at(-reach), x_at(reach)], [-reach, reach],
            color=ACCENT_COLOR, linewidth=LINE_WIDTH)

    # Each numbered position is a direction from its intersection, bisecting
    # the wedge it names. Far enough out that the label clears the two lines
    # forming its wedge: a "118" sitting across the transversal is unreadable
    # and looks like it belongs to both angles at once.
    off = 0.46
    for n, text in labels.items():
        i = int(n)
        y_int = y_top if i <= 4 else y_bot
        quad = (i - 1) % 4          # 0 upper-right, 1 upper-left, 2 lower-left, 3 lower-right
        # The transversal leaves the intersection at theta and theta+180; the
        # parallel line leaves at 0 and 180. The label sits on the bisector of
        # the wedge it names, so it lands inside its own angle and not a
        # neighbour's.
        a_up, a_down = t, t + math.pi
        a_right, a_left = 0.0, math.pi
        mid = {0: (a_right + a_up) / 2,
               1: (a_up + a_left) / 2,
               2: (a_left + a_down) / 2,
               3: (a_down + 2 * math.pi) / 2}[quad]
        ax.text(x_at(y_int) + off * math.cos(mid), y_int + off * math.sin(mid),
                text, ha="center", va="center", fontsize=f.label(10.5),
                color=LINE_COLOR)
    ax.set_xlim(-1.35, 1.35)
    ax.set_ylim(-1.15, 1.15)
    _finish(fig, ax, out)


# ---------------------------------------------------------------------------
# Number pictures
# ---------------------------------------------------------------------------

def part_whole(spec: dict, out: Path, f: _Fonts) -> None:
    """A bar split into named parts under a brace for the whole.

    Part-part-whole is the Year 1 model for addition and subtraction, and the
    same picture carries ratio in Year 6 and percentage in Year 7. Unlike
    `bar_model`, whose parts are equal and anonymous, these parts are labelled
    and sized by value, so one of them can be the "?".
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    parts = spec.get("parts") or []
    if not (2 <= len(parts) <= 5):
        raise ValueError(f"a part-whole bar takes 2-5 parts, got {len(parts)}")
    labels = [str(p) for p in parts]
    weights = spec.get("weights")
    if weights:
        if len(weights) != len(parts):
            raise ValueError("weights must match parts")
        vals = [float(w) for w in weights]
    else:
        # Size a part by its own label where the label is a number, so 28 and 7
        # do not print the same width. An unknown part takes the average.
        nums: list[float | None] = []
        for lab in labels:
            try:
                nums.append(abs(float(lab.replace(",", ""))))
            except ValueError:
                nums.append(None)
        known = [n for n in nums if n]
        fill = (sum(known) / len(known)) if known else 1.0
        vals = [n if n else fill for n in nums]
    total = sum(vals) or 1.0
    # A sliver too thin to write in is worse than an out-of-proportion bar.
    vals = [max(v, total * 0.14) for v in vals]
    total = sum(vals)

    width = 3.0
    fig, ax = plt.subplots(figsize=(3.3, 1.5), dpi=180)
    x = 0.0
    for lab, v in zip(labels, vals):
        w = width * v / total
        ax.add_patch(Rectangle((x, 0), w, 0.55, facecolor=SHADE_COLOR,
                               alpha=SHADE_ALPHA * 0.45, edgecolor=LINE_COLOR,
                               linewidth=LINE_WIDTH))
        ax.text(x + w / 2, 0.275, lab, ha="center", va="center",
                fontsize=f.label(11), color=LINE_COLOR)
        x += w
    whole = spec.get("whole")
    if whole is not None:
        ax.plot([0, 0, width, width], [0.70, 0.82, 0.82, 0.70],
                color=LINE_COLOR, linewidth=LINE_WIDTH * 0.8)
        ax.text(width / 2, 0.90, str(whole), ha="center", va="bottom",
                fontsize=f.label(11), color=LINE_COLOR)
    ax.set_xlim(-0.1, width + 0.1)
    ax.set_ylim(-0.12, 1.25 if whole is not None else 0.70)
    ax.set_aspect("auto")
    ax.axis("off")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.08, transparent=False)
    plt.close(fig)


def factor_tree(spec: dict, out: Path, f: _Fonts) -> None:
    """The prime factorisation of a number, branched out (Years 7-8).

    The tree is computed here rather than taken from the spec, so the figure
    cannot contradict the arithmetic: the model gives the number, the renderer
    factorises it.
    """
    import matplotlib.pyplot as plt

    n = int(spec.get("value", spec.get("number", 36)))
    if not (4 <= n <= 10000):
        raise ValueError(f"a factor tree wants 4 to 10000, got {n}")

    def smallest_factor(m: int) -> int:
        i = 2
        while i * i <= m:
            if m % i == 0:
                return i
            i += 1
        return m

    if smallest_factor(n) == n:
        raise ValueError(f"{n} is prime, so it has no factor tree")

    # Lay the tree out as (value, depth, x). The prime branch always goes left,
    # so the picture reads down the right-hand spine, which is how it is taught.
    nodes: list[tuple[int, int, float]] = []
    edges: list[tuple[tuple[float, int], tuple[float, int]]] = []
    x, depth, current = 0.0, 0, n
    nodes.append((n, 0, 0.0))
    while smallest_factor(current) != current:
        p = smallest_factor(current)
        rest = current // p
        nodes.append((p, depth + 1, x - 0.62))
        nodes.append((rest, depth + 1, x + 0.62))
        edges.append(((x, depth), (x - 0.62, depth + 1)))
        edges.append(((x, depth), (x + 0.62, depth + 1)))
        x, depth, current = x + 0.62, depth + 1, rest

    fig, ax = plt.subplots(figsize=(0.95 * (depth + 1.4), 0.62 * (depth + 1.5)),
                           dpi=180)
    for (x0, d0), (x1, d1) in edges:
        ax.plot([x0, x1], [-d0 - 0.16, -d1 + 0.16], color=LINE_COLOR,
                linewidth=LINE_WIDTH * 0.8)
    for value, d, px in nodes:
        prime = smallest_factor(value) == value
        ax.text(px, -d, str(value), ha="center", va="center",
                fontsize=f.label(11),
                color=ACCENT_COLOR if prime else LINE_COLOR,
                bbox=dict(boxstyle="circle,pad=0.22" if prime else "square,pad=0.20",
                          facecolor="white",
                          edgecolor=ACCENT_COLOR if prime else LINE_COLOR,
                          linewidth=LINE_WIDTH * 0.7))
    xs = [px for _, _, px in nodes]
    ax.set_xlim(min(xs) - 0.55, max(xs) + 0.55)
    ax.set_ylim(-depth - 0.5, 0.5)
    ax.set_aspect("auto")
    ax.axis("off")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.08, transparent=False)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Algebra
# ---------------------------------------------------------------------------

def expand(spec: dict, out: Path, f: _Fonts) -> None:
    """The arcs joining each pair of terms in an expansion (Years 7-10).

    A Year 9 booklet taught expanding binomial products with the word FOIL, a
    five step list and no picture, so the one thing the mnemonic stands for,
    which term is multiplied by which, was the thing left in prose. This is the
    figure every teacher draws on the board: the expression written out once,
    with a looping line from each term in the first bracket to each term in the
    second.

    Arcs from the first term go above the expression and arcs from the second
    go below, so no two arcs cross, and nested arcs bow further out the further
    apart their terms are. That is what makes them traceable rather than a
    tangle.

    Covers the distributive law too: `left` with a single term draws 3(x + 4)
    with two arcs, which is the same picture one step earlier.
    """
    from matplotlib.patches import FancyArrowPatch

    left = [str(t).strip() for t in (spec.get("left") or []) if str(t).strip()]
    right = [str(t).strip() for t in (spec.get("right") or []) if str(t).strip()]
    if not (1 <= len(left) <= 2) or not (1 <= len(right) <= 3):
        raise ValueError(
            f"expand takes 1-2 terms on the left and 1-3 on the right, "
            f"got {len(left)} and {len(right)}")
    if len(left) * len(right) < 2:
        raise ValueError("an expansion with one product has nothing to trace")
    labels = [str(v) for v in (spec.get("labels") or [])]
    if labels and len(labels) != len(left) * len(right):
        raise ValueError(
            f"expand needs a label for every arc: {len(left) * len(right)} "
            f"arcs, {len(labels)} labels")

    size = f.label(13)
    arc_label_size = f.note(9)
    # Chunks are laid out one after another so each TERM's position is known.
    # Measuring the whole expression as one string would give its width and
    # nothing about where "x" or "+ 3" sits inside it, and the arcs have to
    # land on the terms.
    chunks: list[tuple[str, int | None]] = []      # (text, term index or None)
    def add_side(terms: list[str], offset: int) -> None:
        bracket = len(terms) > 1
        if bracket:
            chunks.append(("(", None))
        for i, term in enumerate(terms):
            if i:
                # A leading sign belongs to the operator, not the term, so the
                # arc lands on the number rather than halfway through " + ".
                sign, body = ("-", term[1:]) if term.startswith("-") else ("+", term.lstrip("+"))
                chunks.append((f" {sign} ", None))
                chunks.append((body.strip(), offset + i))
            else:
                chunks.append((term, offset + i))
        if bracket:
            chunks.append((")", None))
    add_side(left, 0)
    add_side(right, len(left))

    fig, ax = _pixel_axes(3.0, 2.0)
    artists = [ax.text(0, -50000, text, fontsize=size, color=LINE_COLOR,
                       ha="left", va="center") for text, _ in chunks]
    widths = _measure(fig, artists)

    total = sum(widths)
    bow = _px(size) * 0.55
    pad_x = _px(size) * 0.5
    # Arcs leaving the same term are stacked one clear step apart. Letting the
    # chord length set the bow, which is the obvious thing to do, spaces two
    # arcs by only a few pixels when their end points are close, and their
    # labels then overlap: "2x²" and "-6x" printed as "2x²6x". When there are
    # labels the step is a label height, so each one gets its own row.
    step = max(_px(size) * 0.42,
               _px(arc_label_size) * 1.35 if labels else 0.0)
    tallest = bow + step * (len(right) - 1)
    label_band = _px(arc_label_size) * 1.5 if labels else _px(size) * 0.18
    # Arcs from the second left term go below the expression, so the space
    # under it is only reserved when there is a second left term. A
    # distributive figure otherwise prints with an inch of nothing beneath it.
    has_below = len(left) > 1
    above_h = tallest + label_band + _px(size) * 0.62
    below_h = above_h if has_below else _px(size) * 0.75
    width_in = (total + pad_x * 2) / DPI
    height_in = (above_h + below_h) / DPI
    fig.set_size_inches(width_in, height_in)
    px_w, px_h = width_in * DPI, height_in * DPI
    ax.set_xlim(0, px_w)
    ax.set_ylim(0, px_h)

    mid_y = below_h
    centres: dict[int, float] = {}
    x = pad_x
    for (text, term), artist, w in zip(chunks, artists, widths):
        artist.set_position((x, mid_y))
        if term is not None:
            centres[term] = x + w / 2
        x += w
    top = mid_y + _px(size) * 0.60
    bottom = mid_y - _px(size) * 0.60

    for i in range(len(left)):
        above = (i == 0)
        anchor = top if above else bottom
        for j in range(len(right)):
            start = centres[i]
            end = centres[len(left) + j]
            # Each successive right term gets a taller arc, so arcs from one
            # term nest instead of tracing over each other.
            chord = max(1.0, abs(end - start))
            # matplotlib puts an arc3 control point one `rad` chord-length off
            # the chord, and a quadratic Bezier reaches half that, so the apex
            # sits at rad * chord / 2. Solving that for the height wanted is
            # the only way a label can be placed ON the arc rather than under
            # it, which is where the first version put every one of them. The
            # cap stops the shortest arc, where the two terms are neighbours,
            # curling into a loop that reads as a letter rather than a link.
            rad_mag = min(2 * (bow + step * j) / chord, 1.15)
            height = rad_mag * chord / 2
            rad = (-1 if above else 1) * rad_mag
            ax.add_patch(FancyArrowPatch(
                (start, anchor), (end, anchor),
                connectionstyle=f"arc3,rad={rad}",
                arrowstyle="-|>", mutation_scale=_px(size) * 0.26,
                linewidth=LINE_WIDTH * 0.8, color=ACCENT_COLOR,
                shrinkA=1, shrinkB=1, zorder=1))
            idx = i * len(right) + j
            if idx < len(labels):
                apex = anchor + (height if above else -height)
                # Sat on a white patch. Arcs leaving the same term fan out
                # from one point, so the apex of an inner arc lies directly
                # under the outer arc sweeping over it, and there is no
                # offset that clears both. Masking the line behind the label
                # is what a textbook does and the only thing that works here.
                ax.text((start + end) / 2,
                        apex + (1 if above else -1) * _px(arc_label_size) * 0.45,
                        labels[idx], ha="center",
                        va="bottom" if above else "top",
                        fontsize=arc_label_size, color=ACCENT_COLOR, zorder=3,
                        bbox=dict(boxstyle="round,pad=0.14", facecolor="white",
                                  edgecolor="none"))
    _save_pixel(fig, out)


def factor_pair(spec: dict, out: Path, f: _Fonts) -> None:
    """The factor diamond, for factorising a quadratic (Years 9-10).

    Product on top, sum underneath, the two numbers on the sides. A Year 9
    lesson defined factorising as "two numbers that multiply to give the
    constant term and add to give the coefficient of the middle term" and drew
    nothing, so the student had to hold four related numbers in their head with
    no place to put them. This is where they go, and it is the working the
    method actually needs.

    Not the expansion arcs run backwards: expanding is about which term meets
    which, and factorising is about a pair of numbers satisfying two conditions
    at once. Different question, different picture.

    Leave `factors` out and both sides print "?", which is the form a practice
    question takes.
    """
    import matplotlib.pyplot as plt

    if "product" not in spec or "sum" not in spec:
        raise ValueError("a factor diamond needs a product and a sum")
    product = float(spec["product"])
    total = float(spec["sum"])
    factors = spec.get("factors") or []
    if factors:
        if len(factors) != 2:
            raise ValueError(f"a factor pair is two numbers, got {len(factors)}")
        a, b = float(factors[0]), float(factors[1])
        # The whole point of the figure is that these four numbers agree. A
        # diamond whose sides do not multiply to its top teaches the method
        # wrong and marks a correct student incorrect.
        if abs(a * b - product) > 1e-9:
            raise ValueError(f"{a} x {b} is not {product}")
        if abs(a + b - total) > 1e-9:
            raise ValueError(f"{a} + {b} is not {total}")
        left, right = _pretty_num(a), _pretty_num(b)
    else:
        left = right = UNKNOWN_LABEL

    r = 1.0
    fig, ax = plt.subplots(figsize=(2.5, 2.5), dpi=DPI)
    ax.plot([0, r, 0, -r, 0], [r, 0, -r, 0, r], color=LINE_COLOR,
            linewidth=LINE_WIDTH)
    ax.plot([-r, r], [0, 0], color=LINE_COLOR, linewidth=LINE_WIDTH * 0.7)
    ax.plot([0, 0], [-r, r], color=LINE_COLOR, linewidth=LINE_WIDTH * 0.7)

    # Every cell centre sits on one of the two diagonals, so a number written
    # there is struck through by the line that makes the cell. Masking is the
    # only fix that keeps the number centred where it belongs.
    def cell(x: float, y: float, text: str, colour: str) -> None:
        ax.text(x, y, text, ha="center", va="center", fontsize=f.label(12),
                color=colour, zorder=3,
                bbox=dict(boxstyle="round,pad=0.16", facecolor="white",
                          edgecolor="none"))

    cell(0, r * 0.46, _pretty_num(product), LINE_COLOR)
    cell(0, -r * 0.46, _pretty_num(total), LINE_COLOR)
    cell(-r * 0.46, 0, left, ACCENT_COLOR)
    cell(r * 0.46, 0, right, ACCENT_COLOR)
    # The operators, not the sentence. "multiply to" printed wider than the
    # diamond it was describing; the symbols say the same thing in one glyph
    # and are what a textbook puts there.
    ax.text(0, r * 1.06, "×", ha="center", va="bottom",
            fontsize=f.label(11), color=LINE_COLOR)
    ax.text(0, -r * 1.06, "+", ha="center", va="top",
            fontsize=f.label(11), color=LINE_COLOR)
    ax.set_xlim(-r * 1.2, r * 1.2)
    ax.set_ylim(-r * 1.45, r * 1.45)
    _finish(fig, ax, out)


def _save_pixel(fig, out: Path) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(out, bbox_inches="tight", pad_inches=0.10, transparent=False)
    plt.close(fig)


RENDERERS = {
    "expand": expand,
    "factor_pair": factor_pair,
    "angle": angle,
    "triangle": triangle,
    "right_triangle": right_triangle,
    "parallelogram": parallelogram,
    "trapezium": trapezium,
    "circle": circle,
    "grid_area": grid_area,
    "symmetry": symmetry,
    "ruler": ruler,
    "jug": jug,
    "scale_dial": scale_dial,
    "money": money,
    "shape_3d": shape_3d,
    "net": net,
    "parallel_lines": parallel_lines,
    "part_whole": part_whole,
    "factor_tree": factor_tree,
}
