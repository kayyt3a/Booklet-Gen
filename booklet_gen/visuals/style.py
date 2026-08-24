"""Drawing constants and the legibility machinery every renderer shares.

Split out of `diagrams.py` so the renderer modules (`shapes`, `data`,
`literacy`) can import what they need without importing the dispatcher that
imports them. `diagrams.py` re-exports everything here, so the old names still
resolve and check scripts that reach for `diagrams.DPI` keep working.

The one idea worth understanding before adding a renderer is `_Fonts`. Every
figure is drawn large and then scaled bodily into a small box on the page by
the formatter, so the size a label is written at says nothing about the size it
prints at. A figure authored 4.9 inches wide and placed 2.4 inches wide loses
half of everything written on it, including the measurements the question turns
on. Renderers therefore ask for sizes through `f.label(...)` and `f.note(...)`
rather than writing `fontsize=11`, because 11 is a size on a canvas nobody
looks at. Recording the request lets the dispatcher measure the finished PNG
and re-draw larger if anything lands under the floor.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

SHADE_COLOR = "#1F3A5F"
SHADE_ALPHA = 0.55
LINE_COLOR = "#1F3A5F"
LINE_WIDTH = 1.8
# A second ink, for the one thing on a figure that must not be confused with
# the figure itself: a mirror line, a height that is not a side, a line of best
# fit, the highlighted part of a sentence.
ACCENT_COLOR = "#C05621"

UNKNOWN_LABEL = "?"

DPI = 180

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

    One instance per render, so concurrent subtopics cannot collide.
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


# Regular polygons a primary booklet names, by side count. Shared because both
# the "name this shape" figure and the symmetry figure draw from it, and a
# shape one of them can draw and the other cannot is a bug waiting to happen.
_SHAPE_SIDES = {
    "triangle": 3, "quadrilateral": 4, "square": 4, "rectangle": 4,
    "rhombus": 4, "trapezium": 4, "parallelogram": 4,
    "pentagon": 5, "hexagon": 6, "heptagon": 7, "octagon": 8,
    "nonagon": 9, "decagon": 10,
}


def _pretty_num(x: float) -> str:
    """Render numbers without gratuitous decimals: 4.0 -> "4", 3.5 -> "3.5"."""
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:g}"


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


def _unit_suffix(spec: dict) -> str:
    unit = str(spec.get("unit", "") or "").strip()
    return f" {unit}" if unit else ""


# Every figure in the product is saved with a TRANSPARENT background, and this
# is the one place that decides it.
#
# A diagram used to save on an opaque white canvas. On a plain question page
# nobody could tell. Inside the worked example, whose panel is tinted, the
# figure printed as a hard-edged white rectangle with a visible seam on all
# four sides: the single most obvious "assembled from parts" mark in the whole
# booklet, and the guided box is white while the worked example is tinted, so
# the same figure meets both.
#
# The alternative was to pass the containing box's fill through to the figure's
# facecolor. That is worse, and the reason is the cache. Diagrams are cached on
# a hash of their spec, and the same spec is drawn both inside the tinted box
# and on a white question page. A background that depends on where the figure
# lands has to be part of the cache key, or the first caller decides what every
# later caller gets, and the seam comes back inverted. Transparent needs no
# context, so one cached file is correct on any background, now and for any
# background this product grows later.
#
# Only the figure and axes patches go transparent. A white patch a renderer
# drew ON PURPOSE, like the face of a solid or the card behind a label, is an
# artist and keeps its fill.
def _set_notation(fig) -> None:
    """Last look over the text on a finished figure. See labels.py.

    The real pass runs on the spec, before anything is drawn, because several
    figures lay themselves out by measuring their own text. This is the
    backstop for a string a renderer built from something other than a spec
    string, and for whatever renderer somebody adds next. It cannot raise: a
    notation tidy-up is never worth losing a picture over.

    A label already in mathtext is left alone. Rewriting the inside of a
    `$...$` run can turn a formula matplotlib parses into one it does not, and
    a parse error at draw time takes the whole figure with it.
    """
    from .labels import label_text

    try:
        from matplotlib.text import Text

        for artist in fig.findobj(Text):
            raw = artist.get_text()
            if not raw or "$" in raw:
                continue
            fixed = label_text(raw)
            if fixed != raw:
                artist.set_text(fixed)
    except Exception:                       # pragma: no cover
        log.info("diagram.notation_sweep_skipped")


def save_figure(fig, out, pad: float = 0.08) -> None:
    """Save a finished figure: tight bounds, transparent background, closed."""
    import matplotlib.pyplot as plt

    _set_notation(fig)
    fig.savefig(out, bbox_inches="tight", pad_inches=pad, transparent=True)
    plt.close(fig)


def _finish(fig, ax, out, pad: float = 0.08) -> None:
    """Every renderer ends the same way: equal aspect, no axes, tight save."""
    ax.set_aspect("equal")
    ax.axis("off")
    save_figure(fig, out, pad)


def _pixel_axes(width_in: float, height_in: float):
    """A figure whose data coordinates are pixels, with the axes filling it.

    For a figure whose content IS text, the width of a label is not known
    until it has been drawn. Working in pixels lets a label be measured with
    `get_window_extent` and then placed, instead of guessed at from a
    character count, which is how "the old dog" and "she" end up the same
    width on a diagram of sentence parts.
    """
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(width_in, height_in), dpi=DPI)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, width_in * DPI)
    ax.set_ylim(0, height_in * DPI)
    ax.set_aspect("auto")
    ax.axis("off")
    return fig, ax


def _measure(fig, artists) -> list[float]:
    """Width in pixels of each already-added text artist."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    return [a.get_window_extent(renderer).width for a in artists]


def _px(points: float) -> float:
    """Points of type into pixels of canvas.

    Gaps on a text-driven figure are expressed through this rather than as
    pixel constants. The legibility pass re-draws a figure with its fonts
    scaled up when the text would print too small, so a layout built from
    fixed pixel gaps holds still while the type inside it grows, and the rows
    run into each other. That is not hypothetical: the first version of
    `text_structure` printed its stage names on top of its own title.
    """
    return points * DPI / 72.0


def _width_budget(size_pt: float) -> float:
    """Widest a text-driven figure may be for `size_pt` to survive printing.

    A wide figure is scaled bodily into the print box, and its text with it.
    Where the canvas is built out of the type, the two cancel exactly:
    doubling the font doubles the canvas, so the printed size never moves and
    the legibility pass loops until it gives up. `sentence_parts` hit that
    head on, printing a nine word sentence at 6.1pt however big the type was
    drawn.

    The fix is to cap the canvas rather than the type. Because the cap scales
    with the type, any wrapping decided against it is stable, and the printed
    size lands just over the floor on the first pass.
    """
    budget_pt = size_pt * DIAGRAM_PRINT_BOX_PT[0] / (MIN_DIAGRAM_LABEL_PT
                                                     * _FLOOR_MARGIN)
    return budget_pt / 72.0 * DPI


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


__all__ = [
    "log", "SHADE_COLOR", "SHADE_ALPHA", "LINE_COLOR", "LINE_WIDTH",
    "ACCENT_COLOR", "UNKNOWN_LABEL", "DPI", "DIAGRAM_PRINT_BOX_PT",
    "MIN_DIAGRAM_LABEL_PT", "MIN_DIAGRAM_NOTE_PT", "MIN_COMPARE_LABEL_PT",
    "_MAX_FONT_SCALE", "_FLOOR_MARGIN", "_Fonts", "_COMPARE_LABEL_PX_MIN",
    "_COMPARE_LABEL_PX_MAX", "_pretty_num", "_dim_label", "_side_rotation",
    "_scale_note", "_unit_suffix", "_finish", "_label_font", "_SHAPE_SIDES",
    "_pixel_axes", "_measure", "_px", "_width_budget", "_set_notation",
    "Optional",
]
