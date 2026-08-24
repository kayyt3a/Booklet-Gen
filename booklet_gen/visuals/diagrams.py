"""Matplotlib-rendered diagrams for maths questions.

Called from the pipeline once the generator has returned a Question with a
diagram_spec. Returns a path to a PNG, or None on any failure (in which
case the question just renders without an image, never blocks the booklet).

Supported spec types
--------------------
{ "type": "circle_slices", "slices": 4, "shaded": 1 }
    A circle divided into equal wedges by straight lines through the centre.
    User's example, "circle sliced into 4 portions", becomes two lines
    (horizontal + vertical), giving four quadrants with `shaded` shaded.

{ "type": "bar_model", "parts": 8, "shaded": 5 }
    A horizontal rectangle split into equal vertical strips, first N shaded.

{ "type": "number_line", "from": 0, "to": 1, "divisions": 4,
  "mark_at": [3/4], "label_at": ["3/4"] }
    A number line from `from` to `to`, split into `divisions` equal ticks.
    Optional highlighted marks with labels.

{ "type": "rectangle", "length": 8, "width": 3, "unit": "cm" }
    A single rectangle with labelled sides.

{ "type": "l_shape", "outer_length": 12, "outer_width": 10,
  "cut_length": 4, "cut_width": 3, "unit": "m" }
    An L-shape formed by a rectangle with one corner cut out. All sides
    labelled with dimensions, so the shape is unambiguous.

{ "type": "cuboid", "length": 5, "width": 3, "height": 4, "unit": "cm" }
    A rectangular prism (cuboid) drawn as a textbook-style 3D solid: an
    oblique projection with solid visible edges and dashed hidden edges,
    with length, width, and height each labelled. Use this for volume and
    surface-area questions about boxes, cubes (length=width=height), and
    rectangular prisms. This is the go-to for "find the volume" questions.

{ "type": "cylinder", "radius": 3, "height": 8, "unit": "cm" }
    An upright cylinder with an elliptical top, a solid front base and a
    dashed hidden back base, labelled with radius and height. Use for
    volume/surface-area of cylinders (typically upper primary and beyond).

{ "type": "column_arithmetic", "top": 4528, "bottom": 2694,
  "operation": "+", "show_answer": false }
    The vertical algorithm as a child is actually taught to write it: digits
    stacked in place-value columns, the operator to the left, a rule under
    the bottom number, and the small carried or borrowed digits in their own
    row above. `show_answer` prints the result under the rule; leave it false
    for a question and set it true for a worked example.

    Written as prose ("5 + 3 = 8, 2 + 6 = 8...") the algorithm is unreadable:
    it runs right to left, the carries are invisible, and the last line of
    the working disagrees with the answer, which is also how these questions
    ended up shipping without a verified tick. Column arithmetic is the one
    topic in primary maths whose entire content is the layout.

Hiding the answer
-----------------
`rectangle`, `cuboid` and `cylinder` accept an optional "unknown" list naming
dimensions whose label must not be printed:

{ "type": "cuboid", "length": 4, "width": 3, "height": 2,
  "unknown": ["height"] }
    Draws the box to shape but writes "?" where the height label would go.

A find-the-missing-dimension question ("the base is 4 by 3 blocks, how many
layers high?") still needs a number to draw the solid, but printing that
number on the drawing hands over the answer. The "?" is how a textbook poses
it, and the diagram stays worth having. `agents.consistency` sets this key
from the question text; the model is not asked to.

{ "type": "compare", "items": [
    {"label": "A", "spec": {...}},
    {"label": "B", "spec": {...}}
  ] }
    A composite figure showing 2-4 sub-diagrams side by side, each with a
    label underneath. Use this for "which is bigger?" style comparison
    questions. Sub-specs may be any of the other types.

Everything else
---------------
The rest of the library lives in three sibling modules, registered into
`_RENDERERS` at the bottom of this file. Each renderer's own docstring names
the curriculum subtopic it exists for.

    shapes.py    angle, triangle, right_triangle, similar_triangles,
                 parallelogram, trapezium,
                 circle, grid_area, symmetry, ruler, jug, scale_dial, money,
                 shape_3d, net, parallel_lines, part_whole, factor_tree
    data.py      bar_chart, picture_graph, tally, dot_plot, scatter, box_plot,
                 stem_leaf, spinner, tree_diagram, venn, coordinate_plane
    literacy.py  sentence_parts, text_structure, narrative_arc, word_web

The prompts are the other half of this: a renderer no prompt routes a subtopic
to will never be asked for, so adding one here without naming it in
`prompts/question_generator_*.txt` changes nothing a customer sees.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Optional

# The drawing constants and the legibility machinery now live in `style`, so
# the renderer modules below can share them without importing this dispatcher,
# which imports them. Re-exported here because callers and check scripts reach
# for `diagrams.DPI`, `diagrams.CACHE_DIR` and friends.
from .labels import label_text, normalise_spec                    # noqa: F401
from .style import (                                              # noqa: F401
    ACCENT_COLOR,
    DIAGRAM_PRINT_BOX_PT,
    DPI,
    LINE_COLOR,
    LINE_WIDTH,
    MIN_COMPARE_LABEL_PT,
    MIN_DIAGRAM_LABEL_PT,
    MIN_DIAGRAM_NOTE_PT,
    SHADE_ALPHA,
    SHADE_COLOR,
    UNKNOWN_LABEL,
    _COMPARE_LABEL_PX_MAX,
    _COMPARE_LABEL_PX_MIN,
    _dim_label,
    _FLOOR_MARGIN,
    _Fonts,
    _label_font,
    _MAX_FONT_SCALE,
    _pretty_num,
    _scale_note,
    _SHAPE_SIDES,
    _side_rotation,
    log,
    save_figure,
)

CACHE_DIR = Path("output/diagrams")

# The cache is keyed on the spec, and the spec alone says nothing about HOW the
# figure was drawn. So the drawing itself is versioned, and the version is part
# of the key. Without it a deployed instance with a warm cache keeps serving
# figures drawn under the old rules for as long as the disk lives: after the
# background went transparent, every diagram already on disk still had a white
# rectangle baked into it, and the seam this was meant to remove would have
# survived in exactly the booklets a paying customer had already generated.
#
# Bump this whenever a change alters what a renderer PUTS ON THE PAGE for an
# unchanged spec. It is also the hook a context-dependent background would hang
# off, if this ever stops being able to be context-free.
RENDER_VERSION = 3


def _cache_path(spec: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        json.dumps({"v": RENDER_VERSION, "spec": spec},
                   sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return CACHE_DIR / f"{key}.png"


def render_diagram(spec: dict) -> Optional[Path]:
    """Dispatch on spec['type']. Returns a PNG path or None on any error."""
    if not isinstance(spec, dict):
        return None
    kind = spec.get("type")
    if not kind:
        return None
    # The model writes its labels the way it writes everything else, in source
    # code: "cm^2", "3*4", "sqrt(16)". Every other string in the booklet is put
    # right by formatter._escape and these never went near it, so a figure
    # printed a caret under a paragraph that printed an index. See labels.py.
    #
    # Before the cache key, not after: "cm^2" and "cm²" draw the same picture
    # and should be the same file.
    spec = normalise_spec(spec)
    out = _cache_path(spec)
    if out.exists():
        return out
    try:
        renderer = _RENDERERS.get(kind)
        if renderer is None:
            log.info("diagram.unknown_type", extra={"type": kind})
            return None
        # Import matplotlib lazily so unused installs pay no import cost.
        import matplotlib
        matplotlib.use("Agg")
        _draw_legibly(renderer, spec, out, kind)
        return out
    except Exception as e:
        log.warning("diagram.render_failed", extra={"type": kind, "error": str(e)[:200]})
        return None


def _draw_legibly(renderer, spec: dict, out: Path, kind: str) -> None:
    """Draw the figure, then redraw it larger if its text prints too small.

    Sizing the text up front is not enough: matplotlib saves with a tight
    bounding box, so a bigger label makes a bigger canvas, which the formatter
    then scales down harder. The only honest measure is the finished PNG, so
    that is what this measures, and it re-draws until the worst text on the
    figure clears its floor.
    """
    from PIL import Image as PILImage

    scale = 1.0
    for _ in range(6):
        fonts = _Fonts(scale)
        renderer(spec, out, fonts)
        if not fonts.used:
            return               # nothing written on it, nothing to read
        with PILImage.open(out) as img:
            png_w, png_h = img.size
        shortfall = fonts.shortfall(png_w, png_h)
        if shortfall <= 1.001:
            return
        nxt = min(scale * shortfall, _MAX_FONT_SCALE)
        if nxt <= scale * 1.001:
            log.info("diagram.label_size_capped",
                     extra={"type": kind, "scale": round(scale, 2)})
            return
        scale = nxt


# ---- individual renderers ----

def _circle_slices(spec: dict, out: Path, f: _Fonts) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Wedge, Circle

    slices = int(spec.get("slices", 4))
    shaded = int(spec.get("shaded", 0))
    if slices < 2 or slices > 24:
        raise ValueError(f"slices must be 2-24, got {slices}")
    shaded = max(0, min(shaded, slices))

    fig, ax = plt.subplots(figsize=(2.4, 2.4), dpi=DPI)
    # Shaded wedges: wedges are drawn CCW from the theta1 angle.
    wedge_angle = 360.0 / slices
    for i in range(slices):
        # Start each wedge at 90° so the "first" slice sits at 12 o'clock.
        t1 = 90 - (i + 1) * wedge_angle
        t2 = 90 - i * wedge_angle
        if i < shaded:
            ax.add_patch(Wedge((0, 0), 1.0, t1, t2,
                               facecolor=SHADE_COLOR, alpha=SHADE_ALPHA,
                               edgecolor="none"))
    # Straight line dividers (radii), always visible over the shading.
    for i in range(slices):
        angle = math.radians(90 - i * wedge_angle)
        x, y = math.cos(angle), math.sin(angle)
        ax.plot([-x if slices % 2 == 0 and i < slices // 2 else 0,
                 x if slices % 2 == 0 and i < slices // 2 else x],
                [-y if slices % 2 == 0 and i < slices // 2 else 0,
                 y if slices % 2 == 0 and i < slices // 2 else y],
                color=LINE_COLOR, linewidth=LINE_WIDTH)
    # Outer circle
    ax.add_patch(Circle((0, 0), 1.0, fill=False,
                        edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


def _bar_model(spec: dict, out: Path, f: _Fonts) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    parts = int(spec.get("parts", 4))
    shaded = int(spec.get("shaded", 0))
    if parts < 1 or parts > 30:
        raise ValueError(f"parts must be 1-30, got {parts}")
    shaded = max(0, min(shaded, parts))

    width_per = 0.4
    total_w = parts * width_per
    fig, ax = plt.subplots(figsize=(min(6.0, total_w + 0.5), 0.9), dpi=DPI)
    for i in range(parts):
        x = i * width_per
        face = SHADE_COLOR if i < shaded else "white"
        alpha = SHADE_ALPHA if i < shaded else 1.0
        ax.add_patch(Rectangle((x, 0), width_per, 0.5,
                               facecolor=face, alpha=alpha,
                               edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    ax.set_xlim(-0.05, total_w + 0.05)
    ax.set_ylim(-0.1, 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


_PLAIN_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_PLAIN_FRACTION = re.compile(r"^(-?\d+)\s*[/⁄]\s*(\d+)$")
_MIXED_NUMBER = re.compile(r"^(-?\d+)\s+(\d+)\s*[/⁄]\s*(\d+)$")


def _label_value(text: str) -> Optional[float]:
    """The value a mark's label states, or None if it does not state one.

    Only a label that is *entirely* a number counts: "347", "3.5", "3/4",
    "1 1/2". Anything with words in it ("about 350", "x") is describing the
    point rather than naming it, and is left alone.
    """
    s = str(text).strip()
    if _PLAIN_NUMBER.match(s):
        return float(s)
    m = _MIXED_NUMBER.match(s)
    if m and float(m.group(3)):
        whole = float(m.group(1))
        part = float(m.group(2)) / float(m.group(3))
        return whole - part if whole < 0 else whole + part
    m = _PLAIN_FRACTION.match(s)
    if m and float(m.group(2)):
        return float(m.group(1)) / float(m.group(2))
    return None


def _mark_label(text: str, mark: float) -> str:
    """The text to print above a highlighted point, corrected if it lies.

    A number line's mark is placed by its value, so the position is the fact
    and the label is a claim about it. When the two disagree the figure
    contradicts itself, and the child is asked to believe that the point
    sitting between two ticks is a value it plainly is not.

    This is not hypothetical. A Year 5 booklet teaching "round 347 to the
    nearest 100" drew a line from 300 to 400, put the dot at 347, and wrote
    "300" over it: the model labelled the mark with the answer instead of the
    number being rounded. A prompt cannot be relied on to stop that, but the
    spec contradicts itself in a way that is plain to read, so the position
    wins and the label is set to the value actually marked.
    """
    stated = _label_value(text)
    if stated is None:
        return str(text)
    if abs(stated - mark) <= max(1e-9, abs(mark) * 1e-9):
        return str(text)
    log.info("diagram.mark_label_corrected",
             extra={"claimed": str(text)[:20], "marked": mark})
    return _pretty_num(mark)


def _number_line(spec: dict, out: Path, f: _Fonts) -> None:
    import matplotlib.pyplot as plt
    lo = float(spec.get("from", 0))
    hi = float(spec.get("to", 1))
    divisions = int(spec.get("divisions", 4))
    marks = spec.get("mark_at") or []
    labels = spec.get("label_at") or []
    if hi <= lo or divisions < 1 or divisions > 40:
        raise ValueError("invalid number_line spec")

    fig, ax = plt.subplots(figsize=(5.5, 1.0), dpi=DPI)
    ax.plot([lo, hi], [0, 0], color=LINE_COLOR, linewidth=LINE_WIDTH)
    step = (hi - lo) / divisions
    for i in range(divisions + 1):
        x = lo + i * step
        ax.plot([x, x], [-0.08, 0.08], color=LINE_COLOR, linewidth=LINE_WIDTH)
        # Endpoint labels
        if i == 0 or i == divisions:
            ax.text(x, -0.25, _pretty_num(x), ha="center", va="top", fontsize=f.label(10))
    # Highlighted marks with labels
    for i, m in enumerate(marks):
        mx = float(m)
        # A point off the end of the line is clipped away by the axes limits,
        # leaving its label floating over nothing. Drop the pair instead.
        if mx < lo or mx > hi:
            log.info("diagram.mark_off_the_line",
                     extra={"mark": mx, "from": lo, "to": hi})
            continue
        ax.plot([mx], [0], marker="o", markersize=9,
                color=SHADE_COLOR, markeredgecolor=LINE_COLOR)
        if i < len(labels):
            ax.text(mx, 0.22, _mark_label(labels[i], mx), ha="center", va="bottom",
                    fontsize=f.label(10), color=LINE_COLOR)
    ax.set_xlim(lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05)
    ax.set_ylim(-0.5, 0.5)
    ax.axis("off")
    save_figure(fig, out, 0.05)


def _rectangle(spec: dict, out: Path, f: _Fonts) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    length = float(spec.get("length", 8))
    width = float(spec.get("width", 3))
    unit = str(spec.get("unit", ""))
    if length <= 0 or width <= 0 or length > 100 or width > 100:
        raise ValueError("rectangle dimensions out of range")

    # Scale so the longer side is roughly 3 inches on paper.
    scale = 3.0 / max(length, width)
    fig_w = length * scale + 1.2
    fig_h = width * scale + 1.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=DPI)
    ax.add_patch(Rectangle((0, 0), length, width,
                           facecolor="white",
                           edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    # Labels
    unit_s = f" {unit}" if unit else ""
    ax.text(length / 2, -width * 0.08, _dim_label(spec, "length", length, unit_s),
            ha="center", va="top", fontsize=f.label(11))
    side = _dim_label(spec, "width", width, unit_s)
    ax.text(-length * 0.05, width / 2, side,
            ha="right", va="center", fontsize=f.label(11), rotation=_side_rotation(side))
    ax.set_xlim(-length * 0.15, length * 1.05)
    ax.set_ylim(-width * 0.18, width * 1.08)
    ax.set_aspect("equal")
    ax.axis("off")
    _scale_note(ax, spec, f)
    save_figure(fig, out, 0.1)


def _l_shape(spec: dict, out: Path, f: _Fonts) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    OL = float(spec.get("outer_length", 12))
    OW = float(spec.get("outer_width", 10))
    CL = float(spec.get("cut_length", 4))
    CW = float(spec.get("cut_width", 3))
    unit = str(spec.get("unit", ""))
    if not (0 < CL < OL and 0 < CW < OW):
        raise ValueError("cut dimensions must be strictly inside outer")

    # L-shape polygon: rectangle OLxOW with top-right corner CLxCW removed.
    pts = [(0, 0), (OL, 0), (OL, OW - CW),
           (OL - CL, OW - CW), (OL - CL, OW), (0, OW)]

    scale = 3.5 / max(OL, OW)
    fig, ax = plt.subplots(figsize=(OL * scale + 1.4, OW * scale + 1.2), dpi=DPI)
    ax.add_patch(Polygon(pts, closed=True, facecolor="white",
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    us = f" {unit}" if unit else ""
    # Label each side. Coords chosen so labels don't overlap the shape.
    def txt(x, y, s, **kw):
        ax.text(x, y, s, fontsize=f.label(10), **kw)
    txt(OL / 2, -OW * 0.06, f"{_pretty_num(OL)}{us}", ha="center", va="top")             # bottom
    txt(OL + OL * 0.02, (OW - CW) / 2, f"{_pretty_num(OW - CW)}{us}",
        ha="left", va="center")                                                          # right lower
    txt(OL - CL / 2, OW - CW - OW * 0.03, f"{_pretty_num(CL)}{us}",
        ha="center", va="top")                                                           # cut horizontal
    txt(OL - CL - OL * 0.02, OW - CW / 2, f"{_pretty_num(CW)}{us}",
        ha="right", va="center")                                                         # cut vertical
    txt((OL - CL) / 2, OW + OW * 0.02, f"{_pretty_num(OL - CL)}{us}",
        ha="center", va="bottom")                                                        # top
    txt(-OL * 0.02, OW / 2, f"{_pretty_num(OW)}{us}",
        ha="right", va="center", rotation=90)                                            # left
    ax.set_xlim(-OL * 0.18, OL * 1.15)
    ax.set_ylim(-OW * 0.15, OW * 1.12)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.1)


def _cuboid(spec: dict, out: Path, f: _Fonts) -> None:
    """A rectangular prism in oblique (cabinet) projection.

    length -> width across the front (x), height -> up the front (y),
    width  -> depth receding to the back. Visible edges solid, the three
    edges meeting the hidden back-bottom corner dashed. Each dimension is
    labelled once.
    """
    import matplotlib.pyplot as plt

    L = float(spec.get("length", 5))   # front width (x)
    H = float(spec.get("height", 4))   # front height (y)
    W = float(spec.get("width", 3))    # depth (receding)
    unit = str(spec.get("unit", ""))
    for name, v in (("length", L), ("height", H), ("width", W)):
        if v <= 0 or v > 100:
            raise ValueError(f"cuboid {name} out of range: {v}")

    # Cabinet projection: depth is halved and receding at ~40 degrees.
    theta = math.radians(40)
    k = 0.5
    ox, oy = W * k * math.cos(theta), W * k * math.sin(theta)

    # Front face corners.
    A = (0.0, 0.0)          # front bottom-left
    B = (L, 0.0)            # front bottom-right
    C = (L, H)             # front top-right
    D = (0.0, H)           # front top-left
    # Back face corners (front + offset).
    A2 = (A[0] + ox, A[1] + oy)
    B2 = (B[0] + ox, B[1] + oy)
    C2 = (C[0] + ox, C[1] + oy)
    D2 = (D[0] + ox, D[1] + oy)

    span = max(L + ox, H + oy)
    scale = 3.2 / span
    fig, ax = plt.subplots(figsize=(span * scale + 1.4, span * scale + 1.2), dpi=DPI)

    def line(p, q, dashed=False):
        ax.plot([p[0], q[0]], [p[1], q[1]], color=LINE_COLOR,
                linewidth=LINE_WIDTH,
                linestyle=(0, (4, 3)) if dashed else "solid")

    # Solid visible edges: front face, top face, right face.
    for p, q in [(A, B), (B, C), (C, D), (D, A),      # front
                 (D, D2), (C, C2), (B, B2),           # receding (visible)
                 (D2, C2), (C2, B2)]:                  # back top + back right
        line(p, q)
    # Hidden edges (dashed): the back-bottom-left corner A2 and its edges.
    for p, q in [(A, A2), (A2, B2), (A2, D2)]:
        line(p, q, dashed=True)

    us = f" {unit}" if unit else ""
    # length: front bottom edge A-B, below.
    ax.text((A[0] + B[0]) / 2, -H * 0.07, _dim_label(spec, "length", L, us),
            ha="center", va="top", fontsize=f.label(11))
    # height: front left edge A-D, to the left.
    side = _dim_label(spec, "height", H, us)
    ax.text(-L * 0.05, H / 2, side,
            ha="right", va="center", fontsize=f.label(11), rotation=_side_rotation(side))
    # width (depth): receding edge B-B2, offset to the lower right.
    ax.text((B[0] + B2[0]) / 2 + L * 0.03, (B[1] + B2[1]) / 2 - H * 0.02,
            _dim_label(spec, "width", W, us), ha="left", va="center", fontsize=f.label(11))

    ax.set_xlim(-L * 0.2, L + ox + L * 0.12)
    ax.set_ylim(-H * 0.18, H + oy + H * 0.1)
    ax.set_aspect("equal")
    ax.axis("off")
    _scale_note(ax, spec, f)
    save_figure(fig, out, 0.1)


def _cylinder(spec: dict, out: Path, f: _Fonts) -> None:
    """An upright cylinder: elliptical top, solid front base, dashed back base."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse, Arc

    r = float(spec.get("radius", 3))
    H = float(spec.get("height", 8))
    unit = str(spec.get("unit", ""))
    if r <= 0 or r > 100 or H <= 0 or H > 100:
        raise ValueError("cylinder radius/height out of range")

    ell_h = r * 0.5            # visual half-height of the perspective ellipse
    scale = 3.0 / max(2 * r, H + 2 * ell_h)
    fig, ax = plt.subplots(figsize=(2 * r * scale + 1.4, (H + 2 * ell_h) * scale + 1.0),
                           dpi=DPI)

    cx = 0.0
    top_y = H
    bot_y = 0.0

    # Vertical sides.
    ax.plot([cx - r, cx - r], [bot_y, top_y], color=LINE_COLOR, linewidth=LINE_WIDTH)
    ax.plot([cx + r, cx + r], [bot_y, top_y], color=LINE_COLOR, linewidth=LINE_WIDTH)
    # Top: full ellipse.
    ax.add_patch(Ellipse((cx, top_y), 2 * r, 2 * ell_h, fill=False,
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    # Bottom: front half solid, back half dashed.
    ax.add_patch(Arc((cx, bot_y), 2 * r, 2 * ell_h, theta1=180, theta2=360,
                     edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))          # front
    ax.add_patch(Arc((cx, bot_y), 2 * r, 2 * ell_h, theta1=0, theta2=180,
                     edgecolor=LINE_COLOR, linewidth=LINE_WIDTH,
                     linestyle=(0, (4, 3))))                                # hidden back

    us = f" {unit}" if unit else ""
    # radius: from centre of top ellipse out to the rim.
    ax.plot([cx, cx + r], [top_y, top_y], color=SHADE_COLOR,
            linewidth=LINE_WIDTH, linestyle=(0, (2, 2)))
    # Clear of the rim, not across it: at legible size this label sat on top
    # of the ellipse outline and both became hard to read.
    ax.text(cx + r / 2, top_y + ell_h * 1.12,
            f"r = {_dim_label(spec, 'radius', r, us)}",
            ha="center", va="bottom", fontsize=f.label(10), color=LINE_COLOR)
    # height: down the right side.
    side = _dim_label(spec, "height", H, us)
    ax.text(cx + r + r * 0.12, H / 2, side,
            ha="left", va="center", fontsize=f.label(11), rotation=_side_rotation(side))

    ax.set_xlim(cx - r * 1.5, cx + r * 1.7)
    ax.set_ylim(-ell_h * 2.2, top_y + ell_h * 2.2)
    ax.set_aspect("equal")
    ax.axis("off")
    _scale_note(ax, spec, f)
    save_figure(fig, out, 0.1)


def _compare(spec: dict, out: Path, f: _Fonts) -> None:
    """Render 2-4 sub-diagrams side by side, each with a label underneath.

    We render each sub-spec through the normal render_diagram path (so caching
    and validation apply), then compose them into a single PNG with Pillow.
    """
    from PIL import Image as PILImage, ImageDraw

    items = spec.get("items") or []
    if not (2 <= len(items) <= 4):
        raise ValueError(f"compare needs 2-4 items, got {len(items)}")

    tiles: list[tuple[PILImage.Image, str]] = []
    for i, item in enumerate(items):
        sub_spec = item.get("spec")
        label = str(item.get("label") or chr(ord("A") + i))
        if not isinstance(sub_spec, dict):
            raise ValueError(f"compare item {i} missing 'spec' dict")
        if sub_spec.get("type") == "compare":
            raise ValueError("compare items cannot themselves be compare")
        sub_path = render_diagram(sub_spec)
        if sub_path is None:
            raise ValueError(f"compare item {i} ({sub_spec.get('type')}) failed to render")
        tiles.append((PILImage.open(sub_path).convert("RGBA"), label))

    # Normalise heights so tiles sit on the same baseline.
    target_h = max(img.height for img, _ in tiles)
    resized: list[tuple[PILImage.Image, str]] = []
    for img, label in tiles:
        if img.height != target_h:
            ratio = target_h / img.height
            img = img.resize((int(img.width * ratio), target_h), PILImage.LANCZOS)
        resized.append((img, label))

    pad = 20
    box_w, box_h = DIAGRAM_PRINT_BOX_PT
    ruler = ImageDraw.Draw(PILImage.new("RGBA", (1, 1)))

    def layout(font_px: int):
        """Geometry for one candidate label size, plus the resulting scale.

        `scale` is exactly what formatter._make_image will apply: points per
        pixel once the composite is fitted into the print box. The label
        therefore prints at font_px * scale points.
        """
        font = _label_font(font_px)
        widths = []
        for img, label in resized:
            bbox = ruler.textbbox((0, 0), label, font=font)
            # A label wider than its own figure would otherwise run into the
            # neighbouring one, so the column, not the figure, sets the width.
            widths.append(max(img.width, (bbox[2] - bbox[0]) + 8))
        gap = max(40, int(font_px * 0.8))
        total_w = pad * 2 + sum(widths) + gap * (len(resized) - 1)
        total_h = pad * 2 + target_h + font_px + 18
        scale = min(box_w / total_w, box_h / total_h, 1.0)
        return font, widths, total_w, total_h, scale

    # Grow the label until it clears the floor at print size. Raising it
    # widens the columns and deepens the label band, which shrinks the scale
    # again, so this iterates; it converges because the floor is small next to
    # the print box, and the ceiling stops it either way.
    font_px = _COMPARE_LABEL_PX_MIN
    font, widths, total_w, total_h, scale = layout(font_px)
    for _ in range(8):
        needed = math.ceil(MIN_COMPARE_LABEL_PT / scale)
        if needed <= font_px:
            break
        nxt = min(needed, _COMPARE_LABEL_PX_MAX)
        if nxt <= font_px:
            log.info("diagram.compare_label_capped",
                     extra={"items": len(resized), "font_px": font_px})
            break
        font_px = nxt
        font, widths, total_w, total_h, scale = layout(font_px)

    canvas = PILImage.new("RGBA", (total_w, total_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    x = pad
    for (img, label), col_w in zip(resized, widths):
        canvas.paste(img, (x + (col_w - img.width) // 2, pad), img)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((x + col_w / 2 - tw / 2, pad + target_h + 6),
                  label, fill=(31, 58, 95, 255), font=font)
        x += col_w + max(40, int(font_px * 0.8))

    canvas.save(out, "PNG")


def _clock(spec: dict, out: Path, f: _Fonts) -> None:
    """An analogue clock face showing one time.

    A booklet taught clock reading with no clock on the page, so its questions
    had to describe the picture in words: "what time is shown on a clock where
    the short hand is at 3 and the long hand is at 2". That is not reading a
    clock, it is decoding a sentence about one, and it is the whole skill
    missing. A child learning the hour hand has to see the hour hand.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    hour = int(spec.get("hour", 12)) % 12
    minute = int(spec.get("minute", 0)) % 60

    fig, ax = plt.subplots(figsize=(2.6, 2.6), dpi=DPI)
    ax.add_patch(Circle((0, 0), 1.0, fill=False,
                        edgecolor=LINE_COLOR, linewidth=LINE_WIDTH * 1.4))

    for tick in range(60):
        angle = math.radians(90 - tick * 6)
        inner = 0.90 if tick % 5 else 0.84
        ax.plot([inner * math.cos(angle), 0.97 * math.cos(angle)],
                [inner * math.sin(angle), 0.97 * math.sin(angle)],
                color=LINE_COLOR,
                linewidth=LINE_WIDTH * (0.9 if tick % 5 == 0 else 0.4))

    # The numerals are the thing being read, so they get label size, not note.
    for n in range(1, 13):
        angle = math.radians(90 - n * 30)
        ax.text(0.72 * math.cos(angle), 0.72 * math.sin(angle), str(n),
                ha="center", va="center", fontsize=f.label(13),
                color=LINE_COLOR)

    # Minute hand first so the hour hand sits on top where they overlap.
    minute_angle = math.radians(90 - minute * 6)
    ax.plot([0, 0.80 * math.cos(minute_angle)], [0, 0.80 * math.sin(minute_angle)],
            color=LINE_COLOR, linewidth=LINE_WIDTH * 1.6,
            solid_capstyle="round")
    # The hour hand creeps between numerals, which is what makes "just past
    # seven" legible. Drawing it exactly on the numeral teaches the wrong thing.
    hour_angle = math.radians(90 - (hour + minute / 60.0) * 30)
    ax.plot([0, 0.52 * math.cos(hour_angle)], [0, 0.52 * math.sin(hour_angle)],
            color=LINE_COLOR, linewidth=LINE_WIDTH * 2.8,
            solid_capstyle="round")
    ax.add_patch(Circle((0, 0), 0.045, facecolor=LINE_COLOR, edgecolor="none"))

    ax.set_xlim(-1.12, 1.12)
    ax.set_ylim(-1.12, 1.12)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


def _array(spec: dict, out: Path, f: _Fonts) -> None:
    """Rows by columns of counters: the picture multiplication is built on.

    "Arrays and equal groups" is a named Year 2 to 4 subtopic and there was no
    way to draw one, so a booklet taught arrays entirely in prose.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    rows = int(spec.get("rows", 3))
    cols = int(spec.get("columns", spec.get("cols", 4)))
    if not (1 <= rows <= 12 and 1 <= cols <= 12):
        raise ValueError(f"an array must be 1-12 by 1-12, got {rows}x{cols}")
    # "Shaded" turns the array into a fraction of a collection: three quarters
    # of twelve counters is a picture, and it is a different picture from three
    # quarters of one circle. Both are on the Year 2 curriculum and only the
    # circle could be drawn.
    shaded = int(spec.get("shaded", rows * cols))
    if not (0 <= shaded <= rows * cols):
        raise ValueError(f"cannot shade {shaded} of {rows * cols} counters")

    fig, ax = plt.subplots(figsize=(0.42 * cols + 0.6, 0.42 * rows + 0.6), dpi=DPI)
    for r in range(rows):
        for c in range(cols):
            filled = r * cols + c < shaded
            ax.add_patch(Circle((c, -r), 0.32,
                                facecolor=SHADE_COLOR if filled else "white",
                                alpha=SHADE_ALPHA if filled else 1.0,
                                edgecolor=LINE_COLOR,
                                linewidth=LINE_WIDTH * 0.8))
    ax.set_xlim(-0.6, cols - 0.4)
    ax.set_ylim(-rows + 0.4, 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


def _groups(spec: dict, out: Path, f: _Fonts) -> None:
    """Equal groups of counters, for sharing and division.

    Distinct from an array on purpose: sharing twelve stickers among three
    friends is three rings of four, not a three by four grid, and a child who
    is shown the grid is being taught a different idea.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Ellipse

    groups = int(spec.get("groups", 3))
    each = int(spec.get("each", 4))
    if not (1 <= groups <= 8 and 1 <= each <= 12):
        raise ValueError(f"groups must be 1-8 of 1-12, got {groups} of {each}")

    per_row = min(each, 4)
    rows = math.ceil(each / per_row)
    # The counters in a group span (per_row - 1) * 0.55 plus a radius each
    # side. The ring has to be centred on that, not on the cell.
    span = (per_row - 1) * 0.55
    gw = span + 1.05
    fig, ax = plt.subplots(figsize=(gw * groups + 0.4, rows * 0.55 + 0.9), dpi=DPI)

    for g in range(groups):
        x0 = g * gw
        ax.add_patch(Ellipse((x0 + span / 2, -(rows - 1) * 0.55 / 2),
                             span + 0.75, rows * 0.55 + 0.45,
                             fill=False, edgecolor=LINE_COLOR,
                             linewidth=LINE_WIDTH, linestyle="--"))
        for i in range(each):
            r, c = divmod(i, per_row)
            ax.add_patch(Circle((x0 + c * 0.55, -r * 0.55), 0.2,
                                facecolor=SHADE_COLOR, alpha=SHADE_ALPHA,
                                edgecolor=LINE_COLOR, linewidth=LINE_WIDTH * 0.7))
    ax.set_xlim(-0.55, gw * groups - 0.1)
    ax.set_ylim(-(rows - 1) * 0.55 - 0.55, 0.55)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


def _shape(spec: dict, out: Path, f: _Fonts) -> None:
    """One or more named flat shapes, optionally labelled underneath.

    A booklet taught "a triangle has 3 sides, a pentagon has 5 sides" and drew
    none of them, leaving most of the page blank. Naming shapes is a looking
    task before it is a counting one.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, RegularPolygon, Rectangle

    names = spec.get("shapes") or [spec.get("shape", "triangle")]
    # Three is the most that fits across 7.5cm with a readable name under
    # each. A fourth forces the whole figure to be scaled down on the page,
    # the legibility pass then enlarges the text to clear its floor, and the
    # names collide. Ask for two figures instead of a crowded one.
    names = [str(n).strip().lower() for n in names][:3]
    if not names:
        raise ValueError("a shape diagram needs at least one shape")
    show_labels = bool(spec.get("label", True))

    # The figure is kept near the 7.5cm the formatter allows, so the page
    # barely scales it and the legibility pass barely enlarges the text.
    # Drawing it wide and letting the page shrink it is what ran the names
    # into each other: the shapes shrink, the words do not.
    step = 1.5
    fig, ax = plt.subplots(figsize=(1.32 * len(names) + 0.25, 1.75), dpi=DPI)
    for i, name in enumerate(names):
        cx = i * step
        if name == "circle":
            ax.add_patch(Circle((cx, 0), 0.55, facecolor=SHADE_COLOR,
                                alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                                linewidth=LINE_WIDTH))
        elif name == "rectangle":
            ax.add_patch(Rectangle((cx - 0.65, -0.4), 1.3, 0.8,
                                   facecolor=SHADE_COLOR, alpha=SHADE_ALPHA,
                                   edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        else:
            sides = _SHAPE_SIDES.get(name)
            if sides is None:
                raise ValueError(f"unknown shape {name!r}")
            # Square and triangle read wrong resting on a vertex.
            rotation = math.pi / 4 if sides == 4 else 0.0
            ax.add_patch(RegularPolygon((cx, 0), sides, radius=0.6,
                                        orientation=rotation,
                                        facecolor=SHADE_COLOR, alpha=SHADE_ALPHA,
                                        edgecolor=LINE_COLOR,
                                        linewidth=LINE_WIDTH))
        if show_labels:
            ax.text(cx, -0.85, name, ha="center", va="top",
                    fontsize=f.label(11), color=LINE_COLOR)
    ax.set_xlim(-step / 2 - 0.1, step * (len(names) - 1) + step / 2 + 0.1)
    ax.set_ylim(-1.35 if show_labels else -0.8, 0.8)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


def _place_value(spec: dict, out: Path, f: _Fonts) -> None:
    """Hundreds, tens and ones as blocks, the standard classroom picture.

    "Three-digit numbers" was taught with no picture of a three-digit number.
    Place value is the one idea in early primary that is genuinely spatial.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    value = int(spec.get("value", 0))
    if not (0 <= value <= 999):
        raise ValueError(f"place value blocks cover 0-999, got {value}")
    hundreds, rest = divmod(value, 100)
    tens, ones = divmod(rest, 10)

    fig, ax = plt.subplots(figsize=(5.4, 2.3), dpi=DPI)
    u = 0.16
    x = 0.0

    def block(x0, y0, w, h):
        ax.add_patch(Rectangle((x0, y0), w, h, facecolor=SHADE_COLOR,
                               alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                               linewidth=LINE_WIDTH * 0.7))

    for _ in range(hundreds):
        for r in range(10):
            for c in range(10):
                block(x + c * u, -r * u, u, u)
        x += 10 * u + 0.35
    for _ in range(tens):
        for r in range(10):
            block(x, -r * u, u, u)
        x += u + 0.14
    if tens:
        x += 0.2
    for i in range(ones):
        block(x + i * (u + 0.06), 0, u, u)

    if spec.get("label", True):
        parts = []
        if hundreds:
            parts.append(f"{hundreds} hundred{'s' if hundreds > 1 else ''}")
        if tens:
            parts.append(f"{tens} ten{'s' if tens > 1 else ''}")
        if ones:
            parts.append(f"{ones} one{'s' if ones > 1 else ''}")
        if parts:
            ax.text(0, -10 * u - 0.22, ", ".join(parts), ha="left", va="top",
                    fontsize=f.label(11), color=LINE_COLOR)

    ax.set_xlim(-0.2, max(x + ones * (u + 0.06), 1.0) + 0.2)
    ax.set_ylim(-10 * u - 0.72, 0.3)
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, 0.05)


from . import data as _data_figures            # noqa: E402
from . import literacy as _literacy_figures    # noqa: E402
from . import shapes as _shape_figures         # noqa: E402

def _column_arithmetic(spec: dict, out: Path, f: _Fonts) -> None:
    """The vertical algorithm, laid out the way a child is taught to write it.

    Digits sit in place-value columns in a monospaced face so the columns
    line up by construction rather than by luck, the operator sits to the
    left of the bottom row, and a rule runs under it. Carries (addition) and
    borrows (subtraction) are worked out here in Python and printed small
    above the columns they belong to, which is the whole thing the child is
    supposed to be learning and the one part prose working cannot show.
    """
    import matplotlib.pyplot as plt

    top = int(spec.get("top", 0))
    bottom = int(spec.get("bottom", 0))
    op = str(spec.get("operation", "+")).strip()
    if op not in {"+", "-"}:
        raise ValueError(f"column arithmetic supports + and -, got {op!r}")
    if top < 0 or bottom < 0:
        raise ValueError("column arithmetic needs non-negative numbers")
    if op == "-" and bottom > top:
        raise ValueError("column subtraction needs the larger number on top")
    total = top + bottom if op == "+" else top - bottom
    if max(top, bottom, total) > 9_999_999:
        raise ValueError("column arithmetic is drawn up to 7 digits")

    width = max(len(str(top)), len(str(bottom)), len(str(total)))
    t_digits = str(top).rjust(width)
    b_digits = str(bottom).rjust(width)
    a_digits = str(total).rjust(width)

    # Work the carries/borrows out column by column, right to left, exactly
    # as the child does. marks[i] is what is written above column i.
    marks = [""] * width
    if op == "+":
        carry = 0
        for i in range(width - 1, -1, -1):
            a = int(t_digits[i]) if t_digits[i] != " " else 0
            b = int(b_digits[i]) if b_digits[i] != " " else 0
            s = a + b + carry
            carry = 1 if s >= 10 else 0
            if carry and i > 0:
                marks[i - 1] = "1"
    else:
        digits = [int(c) if c != " " else 0 for c in t_digits]
        for i in range(width - 1, -1, -1):
            b = int(b_digits[i]) if b_digits[i] != " " else 0
            if digits[i] < b:
                j = i - 1
                while j >= 0 and digits[j] == 0:
                    digits[j] = 9
                    marks[j] = "9"
                    j -= 1
                if j >= 0:
                    digits[j] -= 1
                    marks[j] = str(digits[j])
                digits[i] += 10
                marks[i] = str(digits[i])
            digits[i] -= b

    show = bool(spec.get("show_answer", False))
    # One data unit per column, and the figure sized at a fixed inches-per-unit,
    # so the digits sit about a digit-width apart at every number length. Tying
    # the figure size to the column count instead lets the spacing drift with
    # the number of digits, and four loose digits stop reading as one number.
    col = 1.0
    x0 = col
    y_mark, y_top, y_bot = 3.0, 2.0, 1.0
    rule_y = 0.5
    ans_y = rule_y - 0.75
    span_x = width + 1.7
    span_y = (y_mark + 0.6) - (ans_y - 0.55 if show else rule_y - 0.3)
    fig, ax = plt.subplots(figsize=(0.30 * span_x, 0.30 * span_y), dpi=DPI)

    digit_pt = f.label(15.0)
    mark_pt = f.note(9.0)
    mono = {"family": "DejaVu Sans Mono"}

    for i in range(width):
        x = x0 + i * col
        # The carries and borrows ARE the exercise. Printing them on a question
        # hands the student the only part they were asked to do: "6015 - 2759"
        # with 5 9 10 15 already written above it is a subtraction with the
        # subtraction taken out. They belong to the worked example, where the
        # point is to show the method, and nowhere else.
        if marks[i] and show:
            ax.text(x, y_mark, marks[i], ha="center", va="center",
                    fontsize=mark_pt, color=ACCENT_COLOR, **mono)
        if t_digits[i] != " ":
            ax.text(x, y_top, t_digits[i], ha="center", va="center",
                    fontsize=digit_pt, color=LINE_COLOR, **mono)
        if b_digits[i] != " ":
            ax.text(x, y_bot, b_digits[i], ha="center", va="center",
                    fontsize=digit_pt, color=LINE_COLOR, **mono)

    ax.text(x0 - col * 1.15, y_bot, op, ha="center", va="center",
            fontsize=digit_pt, color=LINE_COLOR, **mono)
    ax.plot([x0 - col * 1.55, x0 + (width - 0.45) * col], [rule_y, rule_y],
            color=LINE_COLOR, linewidth=LINE_WIDTH)

    if show:
        for i in range(width):
            if a_digits[i] != " ":
                ax.text(x0 + i * col, ans_y, a_digits[i], ha="center",
                        va="center", fontsize=digit_pt, color=LINE_COLOR,
                        **mono)

    ax.set_xlim(x0 - col * 1.9, x0 + (width - 0.3) * col)
    # The top of the view is the carry row whether or not anything is printed
    # in it, so a question keeps the blank line the student writes their own
    # regroups on. Cropping to y_top here would save a few millimetres and take
    # away the only place the working goes.
    ax.set_ylim(ans_y - 0.55 if show else rule_y - 0.3, y_mark + 0.6)
    ax.axis("off")
    save_figure(fig, out, 0.06)


# The multiplication sign the rest of the booklet normalises everything to
# (see formatter._normalise_notation). The figure has to agree with the prose
# beside it, or the page teaches two symbols for one operation.
MULTIPLY_GLYPH = "×"


def _long_multiplication(spec: dict, out: Path, f: _Fonts) -> None:
    """Long multiplication, laid out the way a child is taught to write it.

    Same principle as `_column_arithmetic`: the method IS the layout, so prose
    cannot teach it. Written out as "243 x 2 = 486, then 243 x 10 = 2430" the
    student never sees the two things that actually trip them up, which are
    the digit-by-digit carrying and the zero that holds the tens column open.

    So this draws what goes on the page: one carry row per multiplier digit,
    the earlier row struck through the way a child crosses their carries out
    when they finish one digit and start the next, a zero placeholder printed
    in the accent colour on the second partial product, and the two partial
    products added under a second rule.
    """
    import matplotlib.pyplot as plt

    top = int(spec.get("top", 0))
    bottom = int(spec.get("bottom", 0))
    if top < 0 or bottom < 0:
        raise ValueError("long multiplication needs non-negative numbers")
    b_str = str(bottom)
    if not 1 <= len(b_str) <= 2:
        # Three multiplier digits means three partial products and three carry
        # rows, which stops fitting the box and stops being the Year 5-6 method
        # this exists to teach.
        raise ValueError("long multiplication is drawn for a 1 or 2 digit multiplier")
    if top > 9999 or top * bottom > 9_999_999:
        raise ValueError("long multiplication is drawn up to 7 digits of product")

    t_str = str(top)
    total = top * bottom

    # One partial product per multiplier digit, right to left, each with the
    # carries it generates and the place-value zeros that hold its column.
    partials, carry_rows = [], []
    for j, ch in enumerate(reversed(b_str)):
        d = int(ch)
        marks = [""] * len(t_str)
        carry = 0
        for i in range(len(t_str) - 1, -1, -1):
            p = int(t_str[i]) * d + carry
            carry = p // 10
            if carry and i > 0:
                marks[i - 1] = str(carry)
        partials.append(str(top * d) + "0" * j if d or j else "0")
        carry_rows.append(marks)

    width = max(len(str(total)), len(t_str), len(b_str),
                max(len(p) for p in partials))
    show = bool(spec.get("show_answer", False))
    multi = len(partials) > 1

    col = 1.0
    x0 = col
    # Carry rows stack upward, most recently written on top, exactly as they
    # end up on paper: you cross out the ones row when you move to the tens.
    y_marks = [3.0 + 0.85 * j for j in range(len(carry_rows))]
    y_top, y_bot = 2.0, 1.0
    rule1_y = 0.5
    y_partials = [rule1_y - 0.75 - 1.0 * k for k in range(len(partials))]
    rule2_y = y_partials[-1] - 0.5
    ans_y = rule2_y - 0.75

    bottom_y = (ans_y - 0.55) if show else (rule1_y - 0.3)
    top_y = (max(y_marks) + 0.6) if show else (y_marks[0] + 0.6)
    span_x = width + 2.2
    fig, ax = plt.subplots(figsize=(0.30 * span_x, 0.30 * (top_y - bottom_y)),
                           dpi=DPI)

    digit_pt = f.label(15.0)
    mark_pt = f.note(9.0)
    mono = {"family": "DejaVu Sans Mono"}

    def place(s: str, y: float, colour=LINE_COLOR, size=None, accent_zeros=0):
        """Right-align a number across the place-value columns."""
        padded = s.rjust(width)
        for i, ch in enumerate(padded):
            if ch == " ":
                continue
            # The placeholder zeros are the whole point of the second row, so
            # they are the one thing on it printed in the accent colour.
            is_pad = accent_zeros and i >= width - accent_zeros
            ax.text(x0 + i * col, y, ch, ha="center", va="center",
                    fontsize=size or digit_pt,
                    color=ACCENT_COLOR if is_pad else colour, **mono)

    # Carries and partial products ARE the exercise, so a question shows none
    # of them: the same rule `_column_arithmetic` follows, for the same reason.
    if show:
        # `marks` is indexed against the top number, but every row on the
        # figure is right-aligned to the width of the PRODUCT, which is wider.
        # Without this offset the carries drift left of the digits they came
        # from and hang over the partial products instead: a carry above the
        # wrong column is the error a child copies straight into their book.
        pad = width - len(t_str)
        for j, marks in enumerate(carry_rows):
            y = y_marks[j]
            struck = j < len(carry_rows) - 1
            for i, m in enumerate(marks):
                if not m:
                    continue
                x = x0 + (pad + i) * col
                ax.text(x, y, m, ha="center", va="center", fontsize=mark_pt,
                        color=ACCENT_COLOR, **mono)
                if struck:
                    # Crossed out, because that is what the child does to the
                    # ones-row carries before starting the tens row.
                    ax.plot([x - 0.3, x + 0.3], [y - 0.18, y + 0.18],
                            color=ACCENT_COLOR, linewidth=LINE_WIDTH * 0.6)

    place(t_str, y_top)
    place(b_str, y_bot)
    ax.text(x0 - col * 1.15, y_bot, MULTIPLY_GLYPH, ha="center", va="center",
            fontsize=digit_pt, color=LINE_COLOR, **mono)
    ax.plot([x0 - col * 1.55, x0 + (width - 0.45) * col], [rule1_y, rule1_y],
            color=LINE_COLOR, linewidth=LINE_WIDTH)

    if show:
        for k, p in enumerate(partials):
            place(p, y_partials[k], accent_zeros=k)
        if multi:
            ax.text(x0 - col * 1.15, y_partials[-1], "+", ha="center",
                    va="center", fontsize=digit_pt, color=LINE_COLOR, **mono)
            ax.plot([x0 - col * 1.55, x0 + (width - 0.45) * col],
                    [rule2_y, rule2_y], color=LINE_COLOR, linewidth=LINE_WIDTH)
            place(str(total), ans_y)

    ax.set_xlim(x0 - col * 1.9, x0 + (width - 0.3) * col)
    ax.set_ylim(bottom_y, top_y)
    ax.axis("off")
    save_figure(fig, out, 0.06)


def _short_division(spec: dict, out: Path, f: _Fonts) -> None:
    """Short division ("the bus stop"), with the carried remainders in place.

    The quotient sits above the vinculum, digit over digit, and each remainder
    is written small in front of the next digit of the dividend, which is the
    entire method: 746 / 3 is "7 divides by 3 twice, carry the 1 in front of
    the 4; 14 divides four times, carry the 2 in front of the 6". Described in
    sentences none of that lands, because the carried digit has nowhere to go.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    dividend = int(spec.get("dividend", 0))
    divisor = int(spec.get("divisor", 0))
    if dividend < 0:
        raise ValueError("short division needs a non-negative dividend")
    if not 2 <= divisor <= 12:
        # Short division is the single-digit-divisor method (extended to 11 and
        # 12, which primary students learn as tables). A two-digit divisor is
        # long division, a different algorithm with a different layout.
        raise ValueError("short division is drawn for divisors 2 to 12")
    if dividend > 9_999_999:
        raise ValueError("short division is drawn up to 7 digits")

    d_str = str(dividend)
    # The quotient digit over each dividend digit, and the remainder carried
    # in front of the next one: exactly the two things the child writes.
    q_digits, carried = [], [""] * len(d_str)
    carry = 0
    for i, ch in enumerate(d_str):
        cur = carry * 10 + int(ch)
        q_digits.append(str(cur // divisor))
        carry = cur % divisor
        if i + 1 < len(d_str) and carry:
            carried[i + 1] = str(carry)
    remainder = carry

    show = bool(spec.get("show_answer", False))

    col = 1.2
    x0 = col
    y_quot, y_div = 1.5, 0.0
    rule_y = 0.78
    n = len(d_str)
    right_x = x0 + (n - 0.4) * col

    fig, ax = plt.subplots(figsize=(0.34 * (n + 3.2), 0.34 * 3.0), dpi=DPI)
    digit_pt = f.label(15.0)
    mark_pt = f.note(9.0)
    mono = {"family": "DejaVu Sans Mono"}

    for i, ch in enumerate(d_str):
        x = x0 + i * col
        ax.text(x, y_div, ch, ha="center", va="center", fontsize=digit_pt,
                color=LINE_COLOR, **mono)
        if show:
            # A leading zero in the quotient is not written: 146 / 3 starts at
            # the 14, and printing "048" is not what goes on the page.
            if not (i == 0 and q_digits[0] == "0"):
                ax.text(x, y_quot, q_digits[i], ha="center", va="center",
                        fontsize=digit_pt, color=LINE_COLOR, **mono)
            if carried[i]:
                # Small, and tucked up in front of the digit it joins, which is
                # what makes "14" out of a carried 1 and a 4.
                ax.text(x - 0.42 * col, y_div + 0.30, carried[i], ha="center",
                        va="center", fontsize=mark_pt, color=ACCENT_COLOR,
                        **mono)

    if show and remainder:
        ax.text(right_x + 0.35 * col, y_quot, f"r {remainder}", ha="left",
                va="center", fontsize=digit_pt, color=LINE_COLOR, **mono)

    ax.text(x0 - col * 1.5, y_div, str(divisor), ha="center", va="center",
            fontsize=digit_pt, color=LINE_COLOR, **mono)
    # The bus stop itself: an arc down the left of the dividend and the
    # vinculum over the top, drawn rather than set as a ")" glyph so it
    # actually reaches the full height of the digits at any font scale.
    arc_x0 = x0 - col * 0.72
    t = np.linspace(0, 1, 40)
    ax.plot(arc_x0 - 0.17 * col * np.sin(np.pi * t),
            y_div - 0.5 + t * (rule_y - y_div + 0.5),
            color=LINE_COLOR, linewidth=LINE_WIDTH)
    ax.plot([arc_x0, right_x], [rule_y, rule_y],
            color=LINE_COLOR, linewidth=LINE_WIDTH)

    ax.set_xlim(x0 - col * 2.2, right_x + col * 1.5)
    ax.set_ylim(y_div - 0.8, y_quot + 0.65)
    ax.axis("off")
    save_figure(fig, out, 0.06)


_RENDERERS = {
    "circle_slices": _circle_slices,
    "column_arithmetic": _column_arithmetic,
    "long_multiplication": _long_multiplication,
    "short_division": _short_division,
    "bar_model": _bar_model,
    "number_line": _number_line,
    "rectangle": _rectangle,
    "l_shape": _l_shape,
    "cuboid": _cuboid,
    "cylinder": _cylinder,
    "compare": _compare,
    "clock": _clock,
    "array": _array,
    "groups": _groups,
    "shape": _shape,
    "place_value": _place_value,
    # Geometry, measurement and number pictures.
    **_shape_figures.RENDERERS,
    # Statistics, chance and the Cartesian plane.
    **_data_figures.RENDERERS,
    # English: how language is put together.
    **_literacy_figures.RENDERERS,
}

# Two modules must never claim the same type name: the later import would win
# silently and a subtopic would get somebody else's picture.
_claimed = [set(_shape_figures.RENDERERS), set(_data_figures.RENDERERS),
            set(_literacy_figures.RENDERERS)]
for _i, _a in enumerate(_claimed):
    for _b in _claimed[_i + 1:]:
        if _a & _b:
            raise RuntimeError(f"duplicate diagram types: {sorted(_a & _b)}")

SUPPORTED_TYPES = frozenset(_RENDERERS)
