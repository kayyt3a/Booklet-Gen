"""Folio-owned contextual scenes composed from deterministic line art."""
from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Callable

from .scene_components import (
    INK, MID, PALE, SceneCanvas, draw_arrow, draw_building, draw_container,
    draw_dimension, draw_flagpole, draw_garden, draw_generic_item, draw_ground,
    draw_ladder, draw_price_tag, draw_ribbon, draw_right_angle, draw_scoreboard,
    draw_shape, draw_shelf, draw_track, draw_tree, draw_wall,
)
from .scene_specs import (SUPPORTED_SCENE_TEMPLATES, canonical_scene_json,
                          normalise_scene_spec, visible_labels)
from .style import (ACCENT_COLOR, DPI, LINE_COLOR, LINE_WIDTH, _FLOOR_MARGIN,
                    _Fonts, _MAX_FONT_SCALE)

log = logging.getLogger(__name__)

CACHE_DIR = Path("output/scenes")
RENDERER_CACHE_VERSION = 3


_PROFILE_BOX_PT = {
    "student": (12 * 72 / 2.54, 6.2 * 72 / 2.54),
    "teaching": (9 * 72 / 2.54, 5 * 72 / 2.54),
}


def _cache_path(spec: dict, profile: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "renderer": RENDERER_CACHE_VERSION,
        "profile": profile,
        "scene": json.loads(canonical_scene_json(spec)),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    key = hashlib.sha1(payload.encode("ascii")).hexdigest()[:16]
    return CACHE_DIR / f"scene-{key}.png"


def _figure(width=5.6, height=2.8):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(width, height), dpi=DPI)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def _save(fig, ax, out: Path):
    import matplotlib.pyplot as plt

    fig.savefig(out, dpi=DPI, bbox_inches="tight", pad_inches=0.08,
                facecolor="white")
    plt.close(fig)


def _label(value, unit="") -> str:
    if value is None:
        return "x"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value} {unit}".strip()


def _caption_unknown(ax, meta: SceneCanvas, spec: dict, f: _Fonts):
    if spec.get("unknown") and spec.get("template") in {
            "shadow_similarity", "ladder_wall", "ribbon_measure",
            "race_progress", "garden"}:
        meta.text(ax, 5, 0.12, "Diagram not to scale", fontsize=f.note(8),
                  color=LINE_COLOR)


def _draw_shadow(spec, out, f, meta):
    fig, ax = _figure(5.8, 3.0)
    draw_ground(ax, 0.3, 9.7, 0.9)
    positions = [2.0, 6.6]
    display_heights = [2.5, 3.25]
    for index, (obj, x, height) in enumerate(zip(spec["objects"], positions,
                                                  display_heights)):
        kind = obj["kind"]
        if kind == "tree":
            anchors = draw_tree(ax, meta, x, 0.9, height, 0.85 if index == 0 else 1.0)
        elif kind == "building":
            anchors = draw_building(ax, meta, x, 0.9, height, 1.15)
        else:
            anchors = draw_flagpole(ax, meta, x, 0.9, height, 0.8)
        shadow_len = 1.65 if index == 0 else 2.15
        end = (x + shadow_len, 0.9)
        ax.plot([x, end[0]], [0.9, 0.9], color=INK, linewidth=4,
                alpha=0.42, solid_capstyle="round", zorder=4)
        ax.plot([anchors["top"][0], end[0]], [anchors["top"][1], end[1]],
                color=ACCENT_COLOR, linewidth=1.4, linestyle="--", zorder=9)
        draw_right_angle(ax, x, 0.9, 0.16)
        draw_dimension(ax, meta, (x, 0.9), anchors["top"],
                       _label(obj["height"], spec.get("unit", "")),
                       offset=(-0.52, 0), fontsize=f.label(11),
                       dashed=obj["height"] is None)
        draw_dimension(ax, meta, (x, 0.55), (end[0], 0.55),
                       _label(obj["shadow"], spec.get("unit", "")),
                       fontsize=f.label(11), dashed=obj["shadow"] is None)
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_ladder(spec, out, f, meta):
    fig, ax = _figure(5.5, 3.0)
    draw_ground(ax, 0.7, 9.3, 0.8)
    wall_x = 7.1
    draw_wall(ax, meta, wall_x, 0.8, 3.8)
    base, top = (2.1, 0.8), (wall_x - 0.18, 4.1)
    draw_ladder(ax, meta, base, top)
    draw_right_angle(ax, wall_x - 0.42, 0.8, 0.18)
    unit = spec.get("unit", "")
    draw_dimension(ax, meta, (wall_x + 0.38, 0.8), (wall_x + 0.38, 4.1),
                   _label(spec.get("height"), unit), fontsize=f.label(11),
                   dashed=spec.get("height") is None)
    draw_dimension(ax, meta, (2.1, 0.42), (wall_x - 0.18, 0.42),
                   _label(spec.get("base"), unit), fontsize=f.label(11),
                   dashed=spec.get("base") is None)
    draw_dimension(ax, meta, base, top, _label(spec.get("ladder"), unit),
                   offset=(-0.18, 0.15), fontsize=f.label(11),
                   dashed=spec.get("ladder") is None)
    if spec.get("angle") is not None:
        meta.text(ax, 2.55, 1.08, f"{spec['angle']} degrees", fontsize=f.label(10))
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_shelves(spec, out, f, meta):
    fig, ax = _figure(5.5, 3.0)
    rows = spec["shelves"]
    ys = [4.1 - i * (3.2 / max(1, len(rows) - 1)) for i in range(len(rows))]
    for row, y in zip(rows, ys):
        count = row.get("count", row.get("value", 0))
        draw_shelf(ax, meta, 2.2, y - 0.25, 6.5, count)
        meta.text(ax, 0.4, y, row["label"], ha="left", fontsize=f.label(10.5),
                  weight="bold")
        if "count" in row or "value" in row:
            meta.text(ax, 9.0, y, str(count), fontsize=f.label(11), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_ribbon_measure(spec, out, f, meta):
    fig, ax = _figure(5.8, 2.4)
    segments = spec["segments"]
    x0, x1, y = 0.8, 9.2, 2.6
    weights = [float(row["value"] or 1) for row in segments]
    total = sum(weights) or len(weights)
    cuts, boundaries = [], [x0]
    cursor = x0
    for weight in weights[:-1]:
        cursor += (x1 - x0) * weight / total
        cuts.append(cursor)
        boundaries.append(cursor)
    boundaries.append(x1)
    draw_ribbon(ax, meta, x0, x1, y, cuts)
    for index, row in enumerate(segments):
        label = _label(row["value"], spec.get("unit", ""))
        draw_dimension(ax, meta, (boundaries[index], 1.72),
                       (boundaries[index + 1], 1.72), label,
                       fontsize=f.label(10.5), dashed=row["value"] is None)
        if row.get("label"):
            meta.text(ax, (boundaries[index] + boundaries[index + 1]) / 2, 3.25,
                      row["label"], fontsize=f.note(9))
    if spec.get("label"):
        meta.text(ax, 5, 4.15, spec["label"], fontsize=f.label(11), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_race(spec, out, f, meta):
    fig, ax = _figure(5.8, 2.4)
    x0, x1, y = 0.7, 9.3, 2.35
    draw_track(ax, meta, x0, x1, y)
    segments = spec["segments"]
    weights = [float(row["value"] or 1) for row in segments]
    total = sum(weights) or len(weights)
    cursor = x0
    for index, (row, weight) in enumerate(zip(segments, weights)):
        next_x = cursor + (x1 - x0) * weight / total
        ax.plot([cursor, cursor], [y - 0.46, y + 0.46], color=INK, linewidth=1.1)
        draw_dimension(ax, meta, (cursor, 1.38), (next_x, 1.38),
                       _label(row["value"], spec.get("unit", "")),
                       fontsize=f.label(10), dashed=row["value"] is None)
        if row.get("label"):
            meta.text(ax, (cursor + next_x) / 2, 3.18, row["label"],
                      fontsize=f.note(9))
        if index < len(segments) - 1:
            ax.add_patch(__import__("matplotlib").patches.Circle(
                (next_x, y), 0.11, facecolor=ACCENT_COLOR, edgecolor=INK, zorder=10))
        cursor = next_x
    ax.plot([x1, x1], [y - 0.46, y + 0.46], color=INK, linewidth=1.1)
    if spec.get("label"):
        meta.text(ax, 5, 4.15, spec["label"], fontsize=f.label(11), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_shopping(spec, out, f, meta):
    fig, ax = _figure(5.8, 2.8)
    items = spec["items"]
    xs = [1.0 + i * 8.0 / max(1, len(items) - 1) for i in range(len(items))]
    for item, x in zip(items, xs):
        kind = item.get("kind", "item")
        draw_generic_item(ax, meta, x, 2.9, kind, 1.25)
        meta.text(ax, x, 4.2, item["label"], fontsize=f.note(9), weight="bold")
        if "price" in item:
            draw_price_tag(ax, meta, x, 1.55, f"${item['price']}", f.label(10.5))
        elif "value" in item:
            draw_price_tag(ax, meta, x, 1.55, str(item["value"]), f.label(10.5))
        if item.get("count") not in (None, 1):
            meta.text(ax, x + 0.38, 3.35, f"x{item['count']}", fontsize=f.label(10),
                      weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_groups(spec, out, f, meta):
    from matplotlib.patches import Ellipse

    fig, ax = _figure(5.5, 3.0)
    groups = spec["groups"]
    cols = min(4, groups)
    rows = math.ceil(groups / cols)
    kind = spec.get("kind", "counter")
    for index in range(groups):
        col, row = index % cols, index // cols
        cx = 1.35 + col * (7.3 / max(1, cols - 1))
        cy = 3.65 - row * (2.4 / max(1, rows - 1)) if rows > 1 else 2.6
        ax.add_patch(Ellipse((cx, cy), 1.65, 1.2, facecolor="white",
                             edgecolor=INK, linewidth=1.2, zorder=5))
        shown = min(spec["each"], 12)
        for j in range(shown):
            angle = 2 * math.pi * j / max(1, shown)
            draw_generic_item(ax, meta, cx + 0.52 * math.cos(angle),
                              cy + 0.36 * math.sin(angle), kind, 0.42)
    meta.text(ax, 5, 4.7, f"{groups} groups of {spec['each']}",
              fontsize=f.label(11), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_score(spec, out, f, meta):
    fig, ax = _figure(5.2, 3.0)
    teams = spec["teams"]
    draw_scoreboard(ax, meta, 1.25, 0.85, 7.5, 3.5)
    for index, team in enumerate(teams):
        y = 3.75 - index * (2.3 / max(1, len(teams) - 1))
        meta.text(ax, 2.0, y, team["label"], ha="left", fontsize=f.label(11),
                  color="white", weight="bold")
        value = team.get("value", team.get("count", "x"))
        meta.text(ax, 7.9, y, value, fontsize=f.label(13), color="white",
                  weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_garden_scene(spec, out, f, meta):
    fig, ax = _figure(5.5, 3.0)
    draw_garden(ax, meta, 2.0, 1.2, 6.2, 2.8)
    unit = spec.get("unit", "")
    if spec.get("length") is not None:
        draw_dimension(ax, meta, (2.0, 0.72), (8.2, 0.72),
                       _label(spec["length"], unit), fontsize=f.label(11))
    if spec.get("width") is not None:
        draw_dimension(ax, meta, (1.55, 1.2), (1.55, 4.0),
                       _label(spec["width"], unit), fontsize=f.label(11))
    meta.text(ax, 5.1, 2.6, spec.get("kind", "garden bed"),
              fontsize=f.note(10), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_timeline(spec, out, f, meta):
    fig, ax = _figure(5.8, 2.4)
    segments = spec["segments"]
    xs = [0.8 + i * 8.4 / len(segments) for i in range(len(segments) + 1)]
    ax.plot([xs[0], xs[-1]], [2.5, 2.5], color=INK, linewidth=2, zorder=5)
    for index, row in enumerate(segments):
        ax.plot([xs[index], xs[index]], [2.22, 2.78], color=INK, linewidth=1.3)
        meta.text(ax, xs[index], 3.35, row.get("label") or str(index + 1),
                  fontsize=f.note(9))
        draw_dimension(ax, meta, (xs[index], 1.65), (xs[index + 1], 1.65),
                       _label(row["value"], spec.get("unit", "")),
                       fontsize=f.label(10), dashed=row["value"] is None)
    ax.plot([xs[-1], xs[-1]], [2.22, 2.78], color=INK, linewidth=1.3)
    if spec.get("label"):
        meta.text(ax, 5, 4.35, spec["label"], fontsize=f.label(11), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_process(spec, out, f, meta, *, cycle=False):
    from matplotlib.patches import FancyBboxPatch

    fig, ax = _figure(5.8, 3.0)
    steps = spec["steps"]
    if cycle:
        centre, radius = (5, 2.55), 1.65
        positions = [(centre[0] + radius * math.cos(math.pi / 2 - i * 2 * math.pi / len(steps)),
                      centre[1] + radius * math.sin(math.pi / 2 - i * 2 * math.pi / len(steps)))
                     for i in range(len(steps))]
    else:
        cols = min(4, len(steps))
        rows = math.ceil(len(steps) / cols)
        positions = []
        for i in range(len(steps)):
            positions.append((1.35 + (i % cols) * 7.3 / max(1, cols - 1),
                              3.55 - (i // cols) * 2.0))
    box_width = min(2.45, 8.8 / min(4, len(steps)) - 0.28)
    for index, (step, (x, y)) in enumerate(zip(steps, positions)):
        ax.add_patch(FancyBboxPatch((x - box_width / 2, y - 0.42),
                                    box_width, 0.84,
                                    boxstyle="round,pad=0.05,rounding_size=0.12",
                                    facecolor=PALE, edgecolor=INK,
                                    linewidth=1.2, zorder=6))
        meta.text(ax, x, y, step, fontsize=f.label(9.5), weight="bold")
        if index < len(steps) - 1:
            nx, ny = positions[index + 1]
            dx, dy = nx - x, ny - y
            length = math.hypot(dx, dy) or 1
            ux, uy = dx / length, dy / length
            pad = box_width * 0.52
            draw_arrow(ax, (x + ux * pad, y + uy * 0.48),
                       (nx - ux * pad, ny - uy * 0.48))
    if cycle and len(steps) > 2:
        x, y = positions[-1]
        nx, ny = positions[0]
        dx, dy = nx - x, ny - y
        length = math.hypot(dx, dy) or 1
        ux, uy = dx / length, dy / length
        draw_arrow(ax, (x + ux * box_width * 0.52, y + uy * 0.48),
                   (nx - ux * box_width * 0.52, ny - uy * 0.48))
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_forces(spec, out, f, meta):
    from matplotlib.patches import Rectangle

    fig, ax = _figure(5.2, 3.0)
    ax.add_patch(Rectangle((4.05, 2.05), 1.9, 1.15, facecolor=PALE,
                           edgecolor=INK, linewidth=1.5, zorder=6))
    meta.object(spec.get("object", "box"))
    meta.text(ax, 5, 2.62, spec.get("object", "box"), fontsize=f.note(9),
              weight="bold")
    starts = {"up": (5, 3.3), "down": (5, 1.95),
              "left": (3.95, 2.62), "right": (6.05, 2.62)}
    ends = {"up": (5, 4.55), "down": (5, 0.7),
            "left": (1.8, 2.62), "right": (8.2, 2.62)}
    offsets = {"up": (0.3, 4.2), "down": (0.3, 1.0),
               "left": (2.6, 2.95), "right": (7.4, 2.95)}
    for force in spec["forces"]:
        direction = force["direction"]
        draw_arrow(ax, starts[direction], ends[direction])
        x, y = offsets[direction]
        meta.text(ax, x, y, force["label"], fontsize=f.label(10))
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_circuit(spec, out, f, meta):
    from matplotlib.patches import Circle

    fig, ax = _figure(5.5, 3.0)
    components = spec["components"]
    n = len(components)
    positions = []
    for i in range(n):
        if i < math.ceil(n / 2):
            positions.append((1.3 + i * 7.4 / max(1, math.ceil(n / 2) - 1), 3.7))
        else:
            j = i - math.ceil(n / 2)
            lower_n = n - math.ceil(n / 2)
            positions.append((8.7 - j * 7.4 / max(1, lower_n - 1), 1.3))
    for i, ((x, y), kind) in enumerate(zip(positions, components)):
        nx, ny = positions[(i + 1) % n]
        ax.plot([x, nx], [y, ny], color=INK, linewidth=1.5, zorder=4)
        if kind in {"x", "?"}:
            ax.add_patch(Circle((x, y), 0.36, facecolor="white", edgecolor=INK,
                                linewidth=1.4, linestyle="--", zorder=7))
            meta.text(ax, x, y, kind, fontsize=f.label(12), weight="bold")
        elif kind in {"lamp", "motor"}:
            ax.add_patch(Circle((x, y), 0.34, facecolor="white", edgecolor=INK,
                                linewidth=1.4, zorder=7))
            if kind == "lamp":
                ax.plot([x - 0.2, x + 0.2], [y - 0.2, y + 0.2], color=INK)
                ax.plot([x - 0.2, x + 0.2], [y + 0.2, y - 0.2], color=INK)
            else:
                meta.text(ax, x, y, "M", fontsize=f.label(9), weight="bold")
        elif kind in {"cell", "battery"}:
            ax.plot([x - 0.18, x - 0.18], [y - 0.32, y + 0.32], color=INK,
                    linewidth=1.2)
            ax.plot([x + 0.18, x + 0.18], [y - 0.48, y + 0.48], color=INK,
                    linewidth=2)
        elif kind == "switch":
            ax.add_patch(Circle((x - 0.25, y), 0.06, facecolor=INK))
            ax.add_patch(Circle((x + 0.25, y), 0.06, facecolor=INK))
            if spec.get("open") is None:
                meta.text(ax, x, y + 0.25,
                          (spec.get("unknown") or {}).get("symbol", "?"),
                          fontsize=f.label(11), weight="bold")
            else:
                end_y = y + 0.22 if spec.get("open") else y
                ax.plot([x - 0.2, x + 0.22], [y, end_y], color=INK, linewidth=1.7)
        else:
            ax.plot([x - 0.35, x - 0.2, x, x + 0.2, x + 0.35],
                    [y, y + 0.18, y - 0.18, y + 0.18, y], color=INK,
                    linewidth=1.3)
        meta.text(ax, x, y - 0.65, kind, fontsize=f.note(9))
        meta.object(kind)
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_particles(spec, out, f, meta):
    from matplotlib.patches import Circle, Rectangle

    fig, ax = _figure(5.7, 3.0)
    states = spec["states"]
    panel_w = 8.5 / len(states)
    for index, state in enumerate(states):
        x0 = 0.7 + index * panel_w
        ax.add_patch(Rectangle((x0, 1.0), panel_w - 0.35, 3.1,
                               facecolor="white", edgecolor=INK,
                               linewidth=1.3, zorder=5))
        meta.text(ax, x0 + (panel_w - 0.35) / 2, 4.48, state,
                  fontsize=f.label(10), weight="bold")
        if state in {"x", "?"}:
            points = []
            meta.text(ax, x0 + (panel_w - 0.35) / 2, 2.55, state,
                      fontsize=f.label(18), weight="bold")
        elif state == "solid":
            points = [(x0 + 0.45 + c * 0.45, 1.45 + r * 0.45)
                      for r in range(5) for c in range(max(2, int((panel_w - 0.7) / 0.45)))]
        elif state == "liquid":
            points = [(x0 + 0.45 + (i % 4) * max(0.38, (panel_w - 1.0) / 3),
                       1.35 + (i // 4) * 0.48 + (0.08 if i % 2 else 0)) for i in range(12)]
        else:
            points = [(x0 + 0.45 + (i * 0.73) % max(0.7, panel_w - 1.0),
                       1.4 + (i * 0.91) % 2.25) for i in range(9)]
        for px, py in points:
            ax.add_patch(Circle((px, py), 0.09, facecolor=MID, edgecolor=INK,
                                linewidth=0.6, zorder=7))
        meta.object(f"{state} particles")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_reasoning(spec, out, f, meta):
    from matplotlib.patches import Rectangle

    fig, ax = _figure(5.8, 2.5)
    steps = spec["steps"]
    width = 8.7 / len(steps)
    for index, step in enumerate(steps):
        x = 0.7 + index * width
        ax.add_patch(Rectangle((x, 1.1), width - 0.24, 2.9, facecolor="white",
                               edgecolor=INK, linewidth=1.1, zorder=5))
        count = step["count"]
        for j in range(count):
            cols = min(3, count)
            px = x + (width - 0.24) * (0.25 + 0.5 * (j % cols) / max(1, cols - 1))
            py = 2.55 + 0.62 * ((j // cols) - (count // cols) / 2)
            draw_shape(ax, meta, px, py, step["shape"], 0.23, step["rotation"])
        meta.text(ax, x + (width - 0.24) / 2, 0.72, str(index + 1),
                  fontsize=f.note(9))
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


def _draw_logic(spec, out, f, meta):
    fig, ax = _figure(4.8, 3.4)
    rows, columns = spec["rows"], spec["columns"]
    x0, y0 = 3.2, 0.8
    cell = min(0.72, 5.8 / max(len(rows), len(columns)))
    for i in range(len(rows) + 1):
        ax.plot([x0, x0 + len(columns) * cell], [y0 + i * cell] * 2,
                color=INK, linewidth=1)
    for j in range(len(columns) + 1):
        ax.plot([x0 + j * cell] * 2, [y0, y0 + len(rows) * cell],
                color=INK, linewidth=1)
    for i, row in enumerate(rows):
        meta.text(ax, x0 - 0.18, y0 + (len(rows) - i - 0.5) * cell, row,
                  ha="right", fontsize=f.label(9.5))
    for j, column in enumerate(columns):
        meta.text(ax, x0 + (j + 0.5) * cell, y0 + len(rows) * cell + 0.18,
                  column, va="bottom", rotation=45, fontsize=f.label(9.5))
    for mark in spec["marks"]:
        x = x0 + (mark["column"] + 0.5) * cell
        y = y0 + (len(rows) - mark["row"] - 0.5) * cell
        meta.text(ax, x, y, mark["mark"], fontsize=f.label(11), weight="bold")
    _caption_unknown(ax, meta, spec, f)
    _save(fig, ax, out)


_RENDERERS: dict[str, Callable] = {
    "shadow_similarity": _draw_shadow,
    "ladder_wall": _draw_ladder,
    "shelves": _draw_shelves,
    "ribbon_measure": _draw_ribbon_measure,
    "race_progress": _draw_race,
    "shopping": _draw_shopping,
    "equal_groups_scene": _draw_groups,
    "scoreboard": _draw_score,
    "garden": _draw_garden_scene,
    "timeline": _draw_timeline,
    "science_process": lambda s, o, f, m: _draw_process(s, o, f, m, cycle=False),
    "force_scene": _draw_forces,
    "circuit": _draw_circuit,
    "particle_model": _draw_particles,
    "life_cycle": lambda s, o, f, m: _draw_process(s, o, f, m, cycle=True),
    "reasoning_sequence": _draw_reasoning,
    "logic_grid": _draw_logic,
}

if set(_RENDERERS) != set(SUPPORTED_SCENE_TEMPLATES):
    raise RuntimeError("scene renderer and specification registries have drifted")


def _printed_shortfall(fonts: _Fonts, png_w: int, png_h: int,
                       profile: str) -> float:
    if not fonts.used:
        return 1.0
    box_w, box_h = _PROFILE_BOX_PT[profile]
    px_to_pt = min(box_w / png_w, box_h / png_h, 1.0)
    worst = 1.0
    for points, floor in fonts.used:
        printed = points * DPI / 72 * px_to_pt
        if printed > 0:
            worst = max(worst, floor * _FLOOR_MARGIN / printed)
    return worst


def _draw_legibly(renderer, spec: dict, out: Path, profile: str) -> None:
    from PIL import Image as PILImage

    scale = 1.0
    while True:
        fonts = _Fonts(scale)
        metadata = SceneCanvas()
        renderer(spec, out, fonts, metadata)
        with PILImage.open(out) as image:
            shortfall = _printed_shortfall(fonts, image.width, image.height,
                                            profile)
        if shortfall <= 1.0:
            return
        scale *= min(shortfall, 1.8)
        if scale > _MAX_FONT_SCALE + 1e-9:
            raise ValueError("scene labels cannot meet the print-size floor")


def render_scene(spec: dict, profile: str = "student") -> Path | None:
    """Validate and render one contextual scene, returning a cached PNG."""
    try:
        if profile not in _PROFILE_BOX_PT:
            raise ValueError(f"unsupported scene profile: {profile}")
        clean = normalise_scene_spec(spec)
        out = _cache_path(clean, profile)
        if out.exists():
            return out
        _draw_legibly(_RENDERERS[clean["template"]], clean, out, profile)
        return out
    except Exception as exc:
        log.warning("scene.render_failed", extra={
            "template": spec.get("template") if isinstance(spec, dict) else None,
            "error": str(exc)[:200],
        })
        return None


__all__ = [
    "CACHE_DIR", "RENDERER_CACHE_VERSION", "SUPPORTED_SCENE_TEMPLATES",
    "render_scene", "visible_labels",
]
