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
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CACHE_DIR = Path("output/diagrams")
SHADE_COLOR = "#1F3A5F"
SHADE_ALPHA = 0.55
LINE_COLOR = "#1F3A5F"
LINE_WIDTH = 1.8


UNKNOWN_LABEL = "?"

DPI = 180

# Every figure here is drawn large and then scaled bodily into a small box on
# the page by the formatter, so the size a label is written at says nothing
# about the size it prints at. A figure authored at 4.9 inches wide and placed
# 2.4 inches wide loses half of everything written on it, including the
# measurements the question turns on.
#
# DIAGRAM_PRINT_BOX_PT is the *smallest* box the formatter scales a diagram
# into (WE_IMG_WIDTH x WE_IMG_HEIGHT in formatter.py, 6cm x 4cm). Question
# figures get a larger box and so come out larger still. Sizing for the
# smaller box therefore covers both.
# scripts/check_diagram_legibility.py asserts these two stay in agreement.
DIAGRAM_PRINT_BOX_PT = (6 * 72 / 2.54, 4 * 72 / 2.54)
# Body text in the booklet is around 10pt. A measurement written on a figure
# is read at a glance, not in a paragraph, so 9pt is a floor rather than a
# target. The "not to scale" caption is a footnote and may sit lower.
MIN_DIAGRAM_LABEL_PT = 9.0
MIN_DIAGRAM_NOTE_PT = 7.0
# The compare composite draws its own labels with Pillow rather than
# matplotlib, so it applies the same floor through its own layout.
MIN_COMPARE_LABEL_PT = MIN_DIAGRAM_LABEL_PT
# A figure that cannot be made legible without swamping the drawing is
# stopped here rather than growing without bound.
_MAX_FONT_SCALE = 4.0
# Clear the floor by a little, so rounding cannot drop text back under it.
_FLOOR_MARGIN = 1.03


class _Fonts:
    """The font sizes one figure uses, and the floor each must clear in print.

    Renderers ask for sizes through this rather than writing `fontsize=11`,
    because 11 is a size on a canvas nobody looks at. Recording the request
    lets render_diagram measure the finished PNG, work out what the formatter
    will scale it by, and re-draw at a larger size if anything lands under its
    floor. One instance per render, so concurrent subtopics cannot collide.
    """

    def __init__(self, scale: float = 1.0) -> None:
        self.scale = scale
        self.used: list[tuple[float, float]] = []   # (size drawn, floor in print)

    def label(self, base: float = 11.0) -> float:
        """A measurement or value the child has to read."""
        return self._add(base, MIN_DIAGRAM_LABEL_PT)

    def note(self, base: float = 7.0) -> float:
        """Secondary caption text."""
        return self._add(base, MIN_DIAGRAM_NOTE_PT)

    def _add(self, base: float, floor: float) -> float:
        pt = base * self.scale
        self.used.append((pt, floor))
        return pt

    def shortfall(self, png_w: int, png_h: int) -> float:
        """How many times too small the worst text is once printed.

        1.0 or less means everything clears its floor. The scale factor is
        exactly the one formatter._make_image applies: the figure DPI cancels,
        leaving points on the page per point in the figure.

        Aims a little over the floor rather than exactly at it. Landing on the
        boundary makes the floor a coin toss decided by rounding, and a floor
        that is sometimes missed is not a floor.
        """
        if not self.used:
            return 1.0
        box_w, box_h = DIAGRAM_PRINT_BOX_PT
        px_to_pt = min(box_w / png_w, box_h / png_h, 1.0)
        worst = 1.0
        for pt, floor in self.used:
            printed = pt * DPI / 72 * px_to_pt
            if printed > 0:
                worst = max(worst, floor * _FLOOR_MARGIN / printed)
        return worst


# Where the search for a compare label size starts and stops. The ceiling only
# binds on a composite so wide that no font size can be both legible and in
# proportion; growing without bound there would push the figures themselves
# down to nothing.
_COMPARE_LABEL_PX_MIN = 26
_COMPARE_LABEL_PX_MAX = 160


def _dim_label(spec: dict, key: str, value: float, unit_suffix: str) -> str:
    """The text drawn against one side: the measurement, or "?" if unknown.

    The value stays in the spec because the shape still has to be drawn to
    roughly the right proportions. Only the label is withheld.
    """
    raw = spec.get("unknown") or []
    if isinstance(raw, str):
        raw = [raw]
    if key in {str(k).strip().lower() for k in raw}:
        return UNKNOWN_LABEL
    return f"{_pretty_num(value)}{unit_suffix}"


def _side_rotation(label: str) -> int:
    """Measurements read up the side; a lone "?" reads better upright."""
    return 0 if label == UNKNOWN_LABEL else 90


def _scale_note(ax, spec: dict, f: _Fonts) -> None:
    """Caption a figure whose unknown side is still drawn in proportion.

    Hiding the label stops the number being printed, but the shape is drawn
    to scale, so a child with a ruler can still read the answer off the
    page. Every textbook says "not to scale" for exactly this reason.
    """
    if not (spec.get("unknown") or []):
        return
    ax.annotate("Diagram not to scale", xy=(0.5, -0.06),
                xycoords="axes fraction", ha="center", va="top",
                fontsize=f.note(7), color=LINE_COLOR, alpha=0.75)


def _cache_path(spec: dict) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        json.dumps(spec, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return CACHE_DIR / f"{key}.png"


def render_diagram(spec: dict) -> Optional[Path]:
    """Dispatch on spec['type']. Returns a PNG path or None on any error."""
    if not isinstance(spec, dict):
        return None
    kind = spec.get("type")
    if not kind:
        return None
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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05, transparent=False)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def _pretty_num(x: float) -> str:
    """Render numbers without gratuitous decimals: 4.0 -> "4", 3.5 -> "3.5"."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}"


def _label_font(size_px: int):
    """A bold sans face at `size_px`, wherever the font happens to live.

    The formatter finds DejaVu Sans through matplotlib's font manager rather
    than by path, because matplotlib bundles the font and is already a hard
    dependency. Do the same here: an absolute /usr/share path is a Linux-only
    assumption, and the booklet is generated on Windows too, where it would
    silently drop to Pillow's bitmap default and print labels smaller still.
    """
    from PIL import ImageFont

    try:
        from matplotlib import font_manager

        prop = font_manager.FontProperties()
        prop.set_family("DejaVu Sans")
        prop.set_weight("bold")
        return ImageFont.truetype(
            font_manager.findfont(prop, fallback_to_default=False), size_px)
    except Exception:
        pass
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size_px)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size_px)
    except Exception:
        return ImageFont.load_default()


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05, transparent=False)
    plt.close(fig)


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

    fig, ax = plt.subplots(figsize=(0.42 * cols + 0.6, 0.42 * rows + 0.6), dpi=DPI)
    for r in range(rows):
        for c in range(cols):
            ax.add_patch(Circle((c, -r), 0.32, facecolor=SHADE_COLOR,
                                alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                                linewidth=LINE_WIDTH * 0.8))
    ax.set_xlim(-0.6, cols - 0.4)
    ax.set_ylim(-rows + 0.4, 0.6)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05, transparent=False)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05, transparent=False)
    plt.close(fig)


# Regular polygons a primary booklet names, by side count.
_SHAPE_SIDES = {
    "triangle": 3, "quadrilateral": 4, "square": 4, "rectangle": 4,
    "rhombus": 4, "trapezium": 4, "parallelogram": 4,
    "pentagon": 5, "hexagon": 6, "heptagon": 7, "octagon": 8,
    "nonagon": 9, "decagon": 10,
}


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05, transparent=False)
    plt.close(fig)


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
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05, transparent=False)
    plt.close(fig)


_RENDERERS = {
    "circle_slices": _circle_slices,
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
}
