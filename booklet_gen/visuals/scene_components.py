"""Small deterministic drawing components for contextual booklet scenes."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from matplotlib.patches import (Arc, Circle, FancyArrowPatch, FancyBboxPatch,
                                Polygon, Rectangle)

from .style import ACCENT_COLOR, LINE_COLOR, LINE_WIDTH, SHADE_COLOR

PALE = "#E8F0FA"
MID = "#9DBBE8"
DARK = LINE_COLOR
WARM = "#E9C46A"
GREEN = "#6E8B74"
INK = "#172A46"


@dataclass
class SceneCanvas:
    """Metadata recorded while a scene is drawn for deterministic checks."""

    labels: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)

    def text(self, ax, x, y, value, *, fontsize=12, ha="center", va="center",
             color=INK, weight="normal", rotation=0, bbox=None):
        value = str(value)
        self.labels.append(value)
        return ax.text(x, y, value, fontsize=fontsize, ha=ha, va=va,
                       color=color, fontweight=weight, rotation=rotation,
                       bbox=bbox, zorder=20)

    def object(self, name: str) -> None:
        self.objects.append(name)


def draw_ground(ax, x0, x1, y=0.8):
    ax.plot([x0, x1], [y, y], color=INK, linewidth=LINE_WIDTH, zorder=5)
    for x in [x0 + (x1 - x0) * p for p in (0.08, 0.27, 0.52, 0.73, 0.91)]:
        ax.plot([x, x + 0.10], [y, y + 0.07], color=GREEN, linewidth=1)


def draw_dimension(ax, meta: SceneCanvas, p0, p1, label, *, offset=(0, 0),
                   fontsize=12, dashed=False):
    x0, y0 = p0[0] + offset[0], p0[1] + offset[1]
    x1, y1 = p1[0] + offset[0], p1[1] + offset[1]
    style = "<|-|>"
    arrow = FancyArrowPatch((x0, y0), (x1, y1), arrowstyle=style,
                            mutation_scale=10, linewidth=1.25,
                            linestyle="--" if dashed else "-", color=DARK,
                            zorder=12)
    ax.add_patch(arrow)
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    rotation = math.degrees(math.atan2(y1 - y0, x1 - x0))
    if abs(rotation) > 90:
        rotation += 180
    meta.text(ax, mx, my + (0.13 if abs(y1 - y0) < 0.2 else 0), label,
              fontsize=fontsize, rotation=rotation,
              bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5})


def draw_right_angle(ax, x, y, size=0.18):
    ax.plot([x, x + size, x + size], [y + size, y + size, y],
            color=ACCENT_COLOR, linewidth=1.4, zorder=15)


def draw_tree(ax, meta: SceneCanvas, x, ground, height, width=0.8):
    meta.object("tree")
    trunk_h = height * 0.42
    ax.add_patch(Rectangle((x - width * 0.10, ground), width * 0.20, trunk_h,
                           facecolor="#A97C50", edgecolor=INK, linewidth=1.1,
                           zorder=7))
    centres = [
        (x, ground + height * 0.73, width * 0.45),
        (x - width * 0.25, ground + height * 0.60, width * 0.34),
        (x + width * 0.25, ground + height * 0.60, width * 0.34),
        (x, ground + height * 0.91, width * 0.30),
    ]
    for cx, cy, radius in centres:
        ax.add_patch(Circle((cx, cy), radius, facecolor="#AFC5B2",
                            edgecolor=INK, linewidth=1.1, zorder=8))
    return {"base": (x, ground), "top": (x, ground + height)}


def draw_building(ax, meta: SceneCanvas, x, ground, height, width=1.1):
    meta.object("building")
    ax.add_patch(Rectangle((x - width / 2, ground), width, height,
                           facecolor=PALE, edgecolor=INK, linewidth=1.4, zorder=7))
    for row in range(2):
        for col in range(2):
            ax.add_patch(Rectangle((x - width * 0.32 + col * width * 0.38,
                                    ground + height * (0.25 + row * 0.33)),
                                   width * 0.20, height * 0.16,
                                   facecolor=MID, edgecolor=INK, linewidth=0.8,
                                   zorder=8))
    return {"base": (x, ground), "top": (x, ground + height)}


def draw_flagpole(ax, meta: SceneCanvas, x, ground, height, width=0.9):
    meta.object("flagpole")
    ax.plot([x, x], [ground, ground + height], color=INK, linewidth=2, zorder=8)
    ax.add_patch(Polygon([(x, ground + height), (x + width, ground + height - 0.18),
                          (x, ground + height - 0.38)], closed=True,
                         facecolor=MID, edgecolor=INK, linewidth=1, zorder=8))
    return {"base": (x, ground), "top": (x, ground + height)}


def draw_wall(ax, meta: SceneCanvas, x, ground, height):
    meta.object("wall")
    ax.add_patch(Rectangle((x - 0.18, ground), 0.36, height,
                           facecolor=PALE, edgecolor=INK, hatch="//",
                           linewidth=1.4, zorder=6))


def draw_ladder(ax, meta: SceneCanvas, base, top, rails=0.11):
    meta.object("ladder")
    x0, y0 = base
    x1, y1 = top
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1
    nx, ny = -dy / length * rails, dx / length * rails
    for sign in (-1, 1):
        ax.plot([x0 + sign * nx, x1 + sign * nx],
                [y0 + sign * ny, y1 + sign * ny], color=INK,
                linewidth=2.2, zorder=9)
    for fraction in [0.18, 0.34, 0.50, 0.66, 0.82]:
        cx, cy = x0 + dx * fraction, y0 + dy * fraction
        ax.plot([cx - nx, cx + nx], [cy - ny, cy + ny], color=INK,
                linewidth=1.3, zorder=9)


def draw_shelf(ax, meta: SceneCanvas, x, y, width, count, *, max_icons=16):
    meta.object("shelf")
    ax.add_patch(Rectangle((x, y), width, 0.12, facecolor=INK,
                           edgecolor=INK, zorder=8))
    shown = min(max(0, int(count)), max_icons)
    if shown:
        gap = width * 0.82 / shown
        for i in range(shown):
            bx = x + width * 0.08 + gap * i
            ax.add_patch(Rectangle((bx, y + 0.12), gap * 0.72,
                                   0.42 + 0.08 * (i % 3), facecolor=MID if i % 2 else PALE,
                                   edgecolor=INK, linewidth=0.8, zorder=7))


def draw_ribbon(ax, meta: SceneCanvas, x0, x1, y, cuts):
    meta.object("ribbon")
    ax.add_patch(FancyBboxPatch((x0, y - 0.18), x1 - x0, 0.36,
                                boxstyle="round,pad=0.02,rounding_size=0.12",
                                facecolor=MID, edgecolor=INK, linewidth=1.3,
                                zorder=7))
    for x in cuts:
        ax.plot([x, x], [y - 0.29, y + 0.29], color=ACCENT_COLOR,
                linestyle="--", linewidth=1.5, zorder=10)


def draw_track(ax, meta: SceneCanvas, x0, x1, y):
    meta.object("track")
    ax.add_patch(FancyBboxPatch((x0, y - 0.32), x1 - x0, 0.64,
                                boxstyle="round,pad=0.02,rounding_size=0.30",
                                facecolor="#F2E4D5", edgecolor=INK,
                                linewidth=1.4, zorder=6))
    ax.plot([x0 + 0.15, x1 - 0.15], [y, y], color="white", linewidth=1.2,
            linestyle="--", zorder=8)


def draw_price_tag(ax, meta: SceneCanvas, x, y, label, fontsize=12):
    meta.object("price tag")
    ax.add_patch(FancyBboxPatch((x - 0.42, y - 0.23), 0.84, 0.46,
                                boxstyle="round,pad=0.04,rounding_size=0.08",
                                facecolor="white", edgecolor=INK,
                                linewidth=1.1, zorder=10))
    meta.text(ax, x, y, label, fontsize=fontsize, weight="bold")


def draw_generic_item(ax, meta: SceneCanvas, x, y, kind="item", scale=1.0):
    meta.object(kind)
    if kind in {"ball", "orange", "apple", "fruit"}:
        ax.add_patch(Circle((x, y), 0.24 * scale, facecolor=WARM,
                            edgecolor=INK, linewidth=1.1, zorder=7))
        if kind != "ball":
            ax.plot([x, x + 0.08 * scale], [y + 0.22 * scale, y + 0.34 * scale],
                    color=GREEN, linewidth=1.4, zorder=8)
    elif kind in {"book", "notebook"}:
        ax.add_patch(Rectangle((x - 0.25 * scale, y - 0.18 * scale),
                               0.50 * scale, 0.36 * scale, facecolor=MID,
                               edgecolor=INK, linewidth=1.1, zorder=7))
        ax.plot([x - 0.17 * scale, x + 0.17 * scale], [y + 0.08 * scale] * 2,
                color="white", linewidth=1, zorder=8)
    elif kind in {"box", "packet", "carton"}:
        ax.add_patch(Rectangle((x - 0.25 * scale, y - 0.22 * scale),
                               0.50 * scale, 0.44 * scale, facecolor=PALE,
                               edgecolor=INK, linewidth=1.1, zorder=7))
        ax.plot([x - 0.25 * scale, x, x + 0.25 * scale],
                [y + 0.22 * scale, y + 0.32 * scale, y + 0.22 * scale],
                color=INK, linewidth=1, zorder=8)
    else:
        ax.add_patch(FancyBboxPatch((x - 0.24 * scale, y - 0.20 * scale),
                                    0.48 * scale, 0.40 * scale,
                                    boxstyle="round,pad=0.03,rounding_size=0.08",
                                    facecolor=PALE, edgecolor=INK,
                                    linewidth=1.1, zorder=7))


def draw_scoreboard(ax, meta: SceneCanvas, x, y, width, height):
    meta.object("scoreboard")
    ax.add_patch(FancyBboxPatch((x, y), width, height,
                                boxstyle="round,pad=0.04,rounding_size=0.12",
                                facecolor=INK, edgecolor=INK, linewidth=1.5,
                                zorder=6))


def draw_garden(ax, meta: SceneCanvas, x, y, width, height):
    meta.object("garden")
    ax.add_patch(Rectangle((x, y), width, height, facecolor="#DDE8D7",
                           edgecolor=INK, linewidth=1.5, hatch="..", zorder=6))
    for fx in [x, x + width]:
        for fy in [y, y + height]:
            ax.add_patch(Circle((fx, fy), 0.06, facecolor=INK, edgecolor=INK,
                                zorder=8))


def draw_container(ax, meta: SceneCanvas, x, y, width, height, level=0.55):
    meta.object("container")
    ax.add_patch(Rectangle((x, y), width, height, facecolor="white",
                           edgecolor=INK, linewidth=1.4, zorder=6))
    level = min(max(float(level), 0), 1)
    ax.add_patch(Rectangle((x + 0.03, y + 0.03), width - 0.06,
                           (height - 0.06) * level, facecolor=MID,
                           edgecolor="none", alpha=0.8, zorder=5))


def draw_arrow(ax, p0, p1, *, color=ACCENT_COLOR, width=1.5):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle="-|>", mutation_scale=13,
                                linewidth=width, color=color, zorder=12))


def draw_shape(ax, meta: SceneCanvas, x, y, shape, scale=0.28, rotation=0):
    meta.object(shape)
    if shape == "?":
        meta.text(ax, x, y, "?", fontsize=20, weight="bold")
        return
    if shape == "circle":
        patch = Circle((x, y), scale, facecolor=PALE, edgecolor=INK,
                       linewidth=1.4)
    else:
        sides = {"triangle": 3, "square": 4, "diamond": 4, "star": 5}.get(shape, 4)
        if shape == "star":
            pts = []
            for i in range(10):
                r = scale if i % 2 == 0 else scale * 0.45
                a = math.radians(rotation + 90 + i * 36)
                pts.append((x + r * math.cos(a), y + r * math.sin(a)))
        else:
            pts = [(x + scale * math.cos(math.radians(rotation + 90 + i * 360 / sides)),
                    y + scale * math.sin(math.radians(rotation + 90 + i * 360 / sides)))
                   for i in range(sides)]
        patch = Polygon(pts, closed=True, facecolor=PALE, edgecolor=INK,
                        linewidth=1.4)
    patch.set_zorder(7)
    ax.add_patch(patch)


__all__ = [name for name in globals() if name.startswith("draw_")] + [
    "SceneCanvas", "PALE", "MID", "DARK", "WARM", "GREEN", "INK",
]
