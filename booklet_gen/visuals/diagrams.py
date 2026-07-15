"""Matplotlib-rendered diagrams for maths questions.

Called from the pipeline once the generator has returned a Question with a
diagram_spec. Returns a path to a PNG, or None on any failure (in which
case the question just renders without an image — never blocks the booklet).

Supported spec types
--------------------
{ "type": "circle_slices", "slices": 4, "shaded": 1 }
    A circle divided into equal wedges by straight lines through the centre.
    User's example — "circle sliced into 4 portions" — becomes two lines
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
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CACHE_DIR = Path("output/diagrams")
SHADE_COLOR = "#1F3A5F"
SHADE_ALPHA = 0.55
LINE_COLOR = "#1F3A5F"
LINE_WIDTH = 1.8


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
        renderer(spec, out)
        return out
    except Exception as e:
        log.warning("diagram.render_failed", extra={"type": kind, "error": str(e)[:200]})
        return None


# ---- individual renderers ----

def _circle_slices(spec: dict, out: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Wedge, Circle

    slices = int(spec.get("slices", 4))
    shaded = int(spec.get("shaded", 0))
    if slices < 2 or slices > 24:
        raise ValueError(f"slices must be 2-24, got {slices}")
    shaded = max(0, min(shaded, slices))

    fig, ax = plt.subplots(figsize=(2.4, 2.4), dpi=180)
    # Shaded wedges — wedges are drawn CCW from the theta1 angle.
    wedge_angle = 360.0 / slices
    for i in range(slices):
        # Start each wedge at 90° so the "first" slice sits at 12 o'clock.
        t1 = 90 - (i + 1) * wedge_angle
        t2 = 90 - i * wedge_angle
        if i < shaded:
            ax.add_patch(Wedge((0, 0), 1.0, t1, t2,
                               facecolor=SHADE_COLOR, alpha=SHADE_ALPHA,
                               edgecolor="none"))
    # Straight line dividers (radii) — always visible over the shading.
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


def _bar_model(spec: dict, out: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    parts = int(spec.get("parts", 4))
    shaded = int(spec.get("shaded", 0))
    if parts < 1 or parts > 30:
        raise ValueError(f"parts must be 1-30, got {parts}")
    shaded = max(0, min(shaded, parts))

    width_per = 0.4
    total_w = parts * width_per
    fig, ax = plt.subplots(figsize=(min(6.0, total_w + 0.5), 0.9), dpi=180)
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


def _number_line(spec: dict, out: Path) -> None:
    import matplotlib.pyplot as plt
    lo = float(spec.get("from", 0))
    hi = float(spec.get("to", 1))
    divisions = int(spec.get("divisions", 4))
    marks = spec.get("mark_at") or []
    labels = spec.get("label_at") or []
    if hi <= lo or divisions < 1 or divisions > 40:
        raise ValueError("invalid number_line spec")

    fig, ax = plt.subplots(figsize=(5.5, 1.0), dpi=180)
    ax.plot([lo, hi], [0, 0], color=LINE_COLOR, linewidth=LINE_WIDTH)
    step = (hi - lo) / divisions
    for i in range(divisions + 1):
        x = lo + i * step
        ax.plot([x, x], [-0.08, 0.08], color=LINE_COLOR, linewidth=LINE_WIDTH)
        # Endpoint labels
        if i == 0 or i == divisions:
            ax.text(x, -0.25, _pretty_num(x), ha="center", va="top", fontsize=10)
    # Highlighted marks with labels
    for i, m in enumerate(marks):
        mx = float(m)
        ax.plot([mx], [0], marker="o", markersize=9,
                color=SHADE_COLOR, markeredgecolor=LINE_COLOR)
        if i < len(labels):
            ax.text(mx, 0.22, str(labels[i]), ha="center", va="bottom",
                    fontsize=10, color=LINE_COLOR)
    ax.set_xlim(lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05)
    ax.set_ylim(-0.5, 0.5)
    ax.axis("off")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


def _rectangle(spec: dict, out: Path) -> None:
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
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=180)
    ax.add_patch(Rectangle((0, 0), length, width,
                           facecolor="white",
                           edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    # Labels
    unit_s = f" {unit}" if unit else ""
    ax.text(length / 2, -width * 0.08, f"{_pretty_num(length)}{unit_s}",
            ha="center", va="top", fontsize=11)
    ax.text(-length * 0.05, width / 2, f"{_pretty_num(width)}{unit_s}",
            ha="right", va="center", fontsize=11, rotation=90)
    ax.set_xlim(-length * 0.15, length * 1.05)
    ax.set_ylim(-width * 0.18, width * 1.08)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.savefig(out, bbox_inches="tight", pad_inches=0.1)
    plt.close(fig)


def _l_shape(spec: dict, out: Path) -> None:
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
    fig, ax = plt.subplots(figsize=(OL * scale + 1.4, OW * scale + 1.2), dpi=180)
    ax.add_patch(Polygon(pts, closed=True, facecolor="white",
                         edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
    us = f" {unit}" if unit else ""
    # Label each side. Coords chosen so labels don't overlap the shape.
    def txt(x, y, s, **kw):
        ax.text(x, y, s, fontsize=10, **kw)
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


def _pretty_num(x: float) -> str:
    """Render numbers without gratuitous decimals — 4.0 -> "4", 3.5 -> "3.5"."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}"


_RENDERERS = {
    "circle_slices": _circle_slices,
    "bar_model": _bar_model,
    "number_line": _number_line,
    "rectangle": _rectangle,
    "l_shape": _l_shape,
}
