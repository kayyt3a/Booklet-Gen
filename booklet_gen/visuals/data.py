"""Statistics, chance and the Cartesian plane.

The whole Statistics and Probability strand runs from Year 1 to Year 10 and
none of it could be drawn, so a booklet teaching column graphs printed the
data as a sentence and asked the child to picture the graph. Reading a graph
is the skill; describing one in prose tests something else entirely.

Unlike `shapes`, most figures here keep their axes, so they do not go through
`_finish`. Tick labels are sized through `f.label(...)` like everything else,
because a tick a child has to read off is a measurement.
"""
from __future__ import annotations

import math
from pathlib import Path

from .style import (
    ACCENT_COLOR,
    LINE_COLOR,
    LINE_WIDTH,
    SHADE_ALPHA,
    SHADE_COLOR,
    _finish,
    _Fonts,
    _pretty_num,
)

# A second and third fill, so a grouped column graph or a two-set Venn can be
# told apart in black and white as well as in colour. Chosen for contrast in
# value, not just in hue, because most of these print on a mono printer.
SERIES_FILLS = (SHADE_COLOR, "#7FA8CC", "#C05621", "#9BB7A0")


def _save(fig, out: Path) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(out, bbox_inches="tight", pad_inches=0.10, transparent=False)
    plt.close(fig)


def _frame(ax, f: _Fonts, x_label: str = "", y_label: str = "") -> None:
    """The plain two-spine frame every chart here uses."""
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(LINE_COLOR)
        ax.spines[side].set_linewidth(LINE_WIDTH * 0.8)
    ax.tick_params(colors=LINE_COLOR, labelsize=f.label(9), length=3, width=1.0)
    if x_label:
        ax.set_xlabel(x_label, fontsize=f.label(9.5), color=LINE_COLOR)
    if y_label:
        ax.set_ylabel(y_label, fontsize=f.label(9.5), color=LINE_COLOR)


def _series(spec: dict) -> list[tuple[str, list[float]]]:
    """Read either the one-series shorthand or the full series list."""
    raw = spec.get("series")
    if raw:
        out = []
        for s in raw:
            if not isinstance(s, dict) or "values" not in s:
                raise ValueError("each series needs a values list")
            out.append((str(s.get("name", "")), [float(v) for v in s["values"]]))
        return out
    values = spec.get("values")
    if values is None:
        raise ValueError("a chart needs values")
    return [("", [float(v) for v in values])]


# ---------------------------------------------------------------------------
# Data displays
# ---------------------------------------------------------------------------

def bar_chart(spec: dict, out: Path, f: _Fonts) -> None:
    """A column graph, single or side by side (Years 3-4, then 5-6).

    Side-by-side columns are a named Year 5 subtopic, and comparing two groups
    is the only reason the display exists, so more than one series is a
    first-class case rather than an extra.
    """
    import matplotlib.pyplot as plt

    categories = [str(c) for c in (spec.get("categories") or [])]
    series = _series(spec)
    if not (2 <= len(categories) <= 8):
        raise ValueError(f"a column graph wants 2-8 categories, got {len(categories)}")
    if not (1 <= len(series) <= 3):
        raise ValueError(f"a column graph wants 1-3 series, got {len(series)}")
    for name, values in series:
        if len(values) != len(categories):
            raise ValueError(
                f"series {name!r} has {len(values)} values for "
                f"{len(categories)} categories")
        if any(v < 0 for v in values):
            raise ValueError("a column graph cannot show a negative count")

    # Capped at the width of the print box. A figure authored wider than the
    # box is scaled down bodily, and the legibility pass then has to blow the
    # tick labels back up, so the words end up large and the columns small.
    fig, ax = plt.subplots(
        figsize=(min(3.0, 0.34 * len(categories) * len(series) + 1.3), 1.85),
        dpi=180)
    n = len(series)
    width = 0.8 / n
    xs = list(range(len(categories)))
    for i, (name, values) in enumerate(series):
        offs = [x - 0.4 + width * (i + 0.5) for x in xs]
        ax.bar(offs, values, width=width * 0.9, color=SERIES_FILLS[i % 4],
               alpha=SHADE_ALPHA + 0.25, edgecolor=LINE_COLOR,
               linewidth=LINE_WIDTH * 0.7, label=name or None)
    ax.set_xticks(xs)
    ax.set_xticklabels(categories)
    _frame(ax, f, str(spec.get("x_label", "")), str(spec.get("y_label", "")))
    if n > 1 and any(name for name, _ in series):
        # Above the plot, not inside it. Matplotlib's "best" location happily
        # parks the legend on top of the tallest column, which is the one value
        # the question is most likely to be about.
        ax.legend(fontsize=f.label(8.5), frameon=False, ncol=n,
                  loc="lower center", bbox_to_anchor=(0.5, 1.0))
    ax.set_ylim(0, max(max(v) for _, v in series) * 1.12 or 1)
    _save(fig, out)


def picture_graph(spec: dict, out: Path, f: _Fonts) -> None:
    """A pictograph with a key (Years 1-2, tally marks and picture graphs).

    The key is the whole idea: one symbol standing for more than one thing is a
    six year old's first taste of scale. A count that is not a whole number of
    symbols is refused rather than rounded, because a half symbol the renderer
    invented would make the key a lie.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    rows = spec.get("rows") or []
    per = float(spec.get("each", spec.get("key_value", 1)))
    if not (2 <= len(rows) <= 6):
        raise ValueError(f"a picture graph wants 2-6 rows, got {len(rows)}")
    if per <= 0:
        raise ValueError("each symbol must stand for a positive amount")

    counts: list[tuple[str, int]] = []
    for r in rows:
        label = str(r.get("label", ""))
        count = float(r.get("count", 0))
        symbols = count / per
        if abs(symbols - round(symbols)) > 1e-9:
            raise ValueError(
                f"{label!r} has {_pretty_num(count)}, which is not a whole "
                f"number of symbols when each stands for {_pretty_num(per)}")
        if not (0 <= symbols <= 12):
            raise ValueError(f"{label!r} needs {symbols} symbols; keep it 0-12")
        counts.append((label, int(round(symbols))))

    widest = max(n for _, n in counts) or 1
    fig, ax = plt.subplots(figsize=(0.34 * widest + 1.5,
                                    0.40 * len(counts) + 0.75), dpi=180)
    for i, (label, n) in enumerate(counts):
        y = -i * 0.5
        ax.text(-0.30, y, label, ha="right", va="center",
                fontsize=f.label(10), color=LINE_COLOR)
        for j in range(n):
            ax.add_patch(Circle((j * 0.42, y), 0.15, facecolor=SHADE_COLOR,
                                alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                                linewidth=LINE_WIDTH * 0.7))
    key_y = -len(counts) * 0.5 - 0.15
    ax.add_patch(Circle((0, key_y), 0.15, facecolor=SHADE_COLOR,
                        alpha=SHADE_ALPHA, edgecolor=LINE_COLOR,
                        linewidth=LINE_WIDTH * 0.7))
    ax.text(0.26, key_y, f"= {_pretty_num(per)} {spec.get('unit', '')}".strip(),
            ha="left", va="center", fontsize=f.label(9.5), color=LINE_COLOR)
    ax.set_xlim(-2.0, max(widest * 0.42, 1.6) + 0.3)
    ax.set_ylim(key_y - 0.35, 0.35)
    _finish(fig, ax, out)


def tally(spec: dict, out: Path, f: _Fonts) -> None:
    """Tally marks in fives (Years 1-2).

    Four uprights and a diagonal through them. Written as "||||" in a sentence
    it is just a string of pipes; drawn, it is the reason tallies group in
    fives at all.
    """
    import matplotlib.pyplot as plt

    rows = spec.get("rows") or []
    if not (2 <= len(rows) <= 6):
        raise ValueError(f"a tally chart wants 2-6 rows, got {len(rows)}")

    parsed = []
    for r in rows:
        count = int(r.get("count", 0))
        if not (0 <= count <= 25):
            raise ValueError(f"a tally of {count} is unreadable; keep it 0-25")
        parsed.append((str(r.get("label", "")), count))

    widest = max(c for _, c in parsed) or 1
    groups = math.ceil(widest / 5)
    fig, ax = plt.subplots(figsize=(0.52 * groups + 1.5,
                                    0.36 * len(parsed) + 0.5), dpi=180)
    for i, (label, count) in enumerate(parsed):
        y = -i * 0.5
        ax.text(-0.22, y, label, ha="right", va="center",
                fontsize=f.label(10), color=LINE_COLOR)
        x = 0.0
        full, rest = divmod(count, 5)
        for _ in range(full):
            for k in range(4):
                ax.plot([x + k * 0.09, x + k * 0.09], [y - 0.16, y + 0.16],
                        color=LINE_COLOR, linewidth=LINE_WIDTH * 0.9)
            ax.plot([x - 0.03, x + 0.30], [y - 0.17, y + 0.17],
                    color=LINE_COLOR, linewidth=LINE_WIDTH * 0.9)
            x += 0.52
        for k in range(rest):
            ax.plot([x + k * 0.09, x + k * 0.09], [y - 0.16, y + 0.16],
                    color=LINE_COLOR, linewidth=LINE_WIDTH * 0.9)
    ax.set_xlim(-1.7, groups * 0.52 + 0.15)
    ax.set_ylim(-len(parsed) * 0.5 + 0.2, 0.3)
    _finish(fig, ax, out)


def dot_plot(spec: dict, out: Path, f: _Fonts) -> None:
    """Stacked dots over a number line (Years 5-6, mean, median and mode).

    Mode is a shape on a dot plot and an argument in a sentence. The point of
    the display is that the tallest stack is visible before it is counted.
    """
    import matplotlib.pyplot as plt

    values = [float(v) for v in (spec.get("values") or [])]
    if not (3 <= len(values) <= 40):
        raise ValueError(f"a dot plot wants 3-40 values, got {len(values)}")
    lo = math.floor(min(values))
    hi = math.ceil(max(values))
    if hi - lo > 20:
        raise ValueError("a dot plot spanning more than 20 steps is unreadable")

    stacks: dict[float, int] = {}
    fig, ax = plt.subplots(figsize=(0.32 * (hi - lo + 1) + 0.9, 1.7), dpi=180)
    for v in sorted(values):
        stacks[v] = stacks.get(v, 0) + 1
        ax.plot([v], [stacks[v]], marker="o", markersize=6,
                color=SHADE_COLOR, markeredgecolor=LINE_COLOR)
    ax.plot([lo - 0.5, hi + 0.5], [0, 0], color=LINE_COLOR, linewidth=LINE_WIDTH)
    for x in range(lo, hi + 1):
        ax.plot([x, x], [-0.18, 0], color=LINE_COLOR, linewidth=LINE_WIDTH * 0.7)
        ax.text(x, -0.32, _pretty_num(x), ha="center", va="top",
                fontsize=f.label(9), color=LINE_COLOR)
    if spec.get("x_label"):
        ax.text((lo + hi) / 2, -1.05, str(spec["x_label"]), ha="center", va="top",
                fontsize=f.label(9.5), color=LINE_COLOR)
    ax.set_xlim(lo - 0.8, hi + 0.8)
    ax.set_ylim(-1.5 if spec.get("x_label") else -0.9, max(stacks.values()) + 0.8)
    ax.set_aspect("auto")
    ax.axis("off")
    _save(fig, out)


def scatter(spec: dict, out: Path, f: _Fonts) -> None:
    """A scatterplot, optionally with a line of best fit (Years 9-10).

    Bivariate data is about the shape of a cloud of points. There is no way to
    ask whether an association is positive, negative or absent without drawing
    the cloud.
    """
    import matplotlib.pyplot as plt

    points = spec.get("points") or []
    if not (4 <= len(points) <= 40):
        raise ValueError(f"a scatterplot wants 4-40 points, got {len(points)}")
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]

    fig, ax = plt.subplots(figsize=(2.7, 2.0), dpi=180)
    ax.scatter(xs, ys, s=26, color=SHADE_COLOR, alpha=0.85,
               edgecolors=LINE_COLOR, linewidths=0.8, zorder=3)
    if spec.get("line_of_best_fit"):
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs)
        if denom > 0:
            m = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
            x0, x1 = min(xs), max(xs)
            ax.plot([x0, x1], [my + m * (x0 - mx), my + m * (x1 - mx)],
                    color=ACCENT_COLOR, linewidth=LINE_WIDTH, zorder=2)
    _frame(ax, f, str(spec.get("x_label", "")), str(spec.get("y_label", "")))
    ax.grid(True, color=LINE_COLOR, alpha=0.15, linewidth=0.6)
    ax.set_axisbelow(True)
    _save(fig, out)


def box_plot(spec: dict, out: Path, f: _Fonts) -> None:
    """One or two five-number summaries (Year 10, comparing distributions).

    The values are given, not computed from data, because a box plot in a
    question is usually the stimulus rather than the answer. They must be in
    order: a box plot whose quartiles cross is not a hard question, it is an
    impossible one.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plots = spec.get("plots")
    if not plots:
        plots = [{k: spec.get(k) for k in
                  ("min", "q1", "median", "q3", "max")} | {"label": spec.get("label", "")}]
    if not (1 <= len(plots) <= 3):
        raise ValueError(f"1-3 box plots, got {len(plots)}")

    parsed = []
    for p in plots:
        try:
            five = [float(p[k]) for k in ("min", "q1", "median", "q3", "max")]
        except (KeyError, TypeError):
            raise ValueError("a box plot needs min, q1, median, q3 and max")
        if any(five[i] > five[i + 1] for i in range(4)):
            raise ValueError(f"five-number summary out of order: {five}")
        parsed.append((str(p.get("label", "")), five))

    lo = min(v[0] for _, v in parsed)
    hi = max(v[4] for _, v in parsed)
    if hi <= lo:
        raise ValueError("a box plot needs a spread")
    fig, ax = plt.subplots(figsize=(3.0, 0.62 * len(parsed) + 1.0), dpi=180)
    for i, (label, (mn, q1, med, q3, mx)) in enumerate(parsed):
        y = -i * 1.0
        ax.add_patch(Rectangle((q1, y - 0.26), q3 - q1, 0.52,
                               facecolor=SERIES_FILLS[i % 4], alpha=SHADE_ALPHA,
                               edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        ax.plot([med, med], [y - 0.26, y + 0.26], color=LINE_COLOR,
                linewidth=LINE_WIDTH * 1.5)
        for a, b in ((mn, q1), (q3, mx)):
            ax.plot([a, b], [y, y], color=LINE_COLOR, linewidth=LINE_WIDTH * 0.9)
        for w in (mn, mx):
            ax.plot([w, w], [y - 0.17, y + 0.17], color=LINE_COLOR,
                    linewidth=LINE_WIDTH * 0.9)
        if label:
            ax.text(lo - (hi - lo) * 0.04, y, label, ha="right", va="center",
                    fontsize=f.label(9.5), color=LINE_COLOR)
    ax.set_yticks([])
    ax.set_ylim(-len(parsed) + 0.4, 0.55)
    ax.set_xlim(lo - (hi - lo) * 0.08, hi + (hi - lo) * 0.06)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(LINE_COLOR)
    ax.tick_params(colors=LINE_COLOR, labelsize=f.label(9))
    if spec.get("x_label"):
        ax.set_xlabel(str(spec["x_label"]), fontsize=f.label(9.5), color=LINE_COLOR)
    _save(fig, out)


def stem_leaf(spec: dict, out: Path, f: _Fonts) -> None:
    """A stem and leaf plot (Years 7-8).

    Built from the data, not transcribed from the spec, so the display cannot
    disagree with the numbers the question quotes.
    """
    import matplotlib.pyplot as plt

    values = [int(v) for v in (spec.get("values") or [])]
    if not (5 <= len(values) <= 40):
        raise ValueError(f"a stem and leaf plot wants 5-40 values, got {len(values)}")
    if any(v < 0 or v > 999 for v in values):
        raise ValueError("stem and leaf here covers 0 to 999")

    stems: dict[int, list[int]] = {}
    for v in sorted(values):
        stems.setdefault(v // 10, []).append(v % 10)
    order = list(range(min(stems), max(stems) + 1))
    if len(order) > 12:
        raise ValueError("more than 12 stems will not fit")

    fig, ax = plt.subplots(figsize=(2.5, 0.30 * len(order) + 0.85), dpi=180)
    ax.text(-0.12, 0.55, "Stem", ha="right", va="center",
            fontsize=f.label(9.5), color=LINE_COLOR)
    ax.text(0.16, 0.55, "Leaf", ha="left", va="center",
            fontsize=f.label(9.5), color=LINE_COLOR)
    for i, s in enumerate(order):
        y = -i * 0.42
        ax.text(-0.12, y, str(s), ha="right", va="center",
                fontsize=f.label(10.5), color=LINE_COLOR)
        ax.text(0.16, y, " ".join(str(d) for d in stems.get(s, [])),
                ha="left", va="center", fontsize=f.label(10.5), color=LINE_COLOR)
    ax.plot([0.02, 0.02], [0.30, -len(order) * 0.42 + 0.18],
            color=LINE_COLOR, linewidth=LINE_WIDTH)
    ax.text(0.16, -len(order) * 0.42 - 0.10,
            f"Key: {order[0]} | {stems.get(order[0], [0])[0]} "
            f"= {order[0] * 10 + stems.get(order[0], [0])[0]}",
            ha="left", va="top", fontsize=f.label(9), color=LINE_COLOR)
    ax.set_xlim(-0.9, 1.9)
    ax.set_ylim(-len(order) * 0.42 - 0.55, 0.85)
    ax.set_aspect("auto")
    ax.axis("off")
    _save(fig, out)


# ---------------------------------------------------------------------------
# Chance
# ---------------------------------------------------------------------------

def spinner(spec: dict, out: Path, f: _Fonts) -> None:
    """A spinner divided into named sectors (Years 3-6, chance).

    Equal sectors or weighted ones. Chance language ("likely", "unlikely") and
    chance as a fraction both come off looking at how much of the circle each
    outcome takes, which is a thing you look at.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, Wedge

    sectors = spec.get("sectors") or []
    if isinstance(sectors, dict):
        sectors = [{"label": k, "size": v} for k, v in sectors.items()]
    if not (2 <= len(sectors) <= 8):
        raise ValueError(f"a spinner wants 2-8 sectors, got {len(sectors)}")
    labels, sizes = [], []
    for s in sectors:
        if isinstance(s, str):
            labels.append(s)
            sizes.append(1.0)
        else:
            labels.append(str(s.get("label", "")))
            sizes.append(float(s.get("size", 1)))
    if any(v <= 0 for v in sizes):
        raise ValueError("every sector must have a positive size")
    total = sum(sizes)

    fig, ax = plt.subplots(figsize=(2.5, 2.5), dpi=180)
    start = 90.0
    for i, (label, size) in enumerate(zip(labels, sizes)):
        sweep = 360.0 * size / total
        end = start - sweep
        ax.add_patch(Wedge((0, 0), 1.0, end, start,
                           facecolor=SERIES_FILLS[i % 4], alpha=SHADE_ALPHA,
                           edgecolor=LINE_COLOR, linewidth=LINE_WIDTH))
        a = math.radians(start - sweep / 2)
        ax.text(0.62 * math.cos(a), 0.62 * math.sin(a), label,
                ha="center", va="center", fontsize=f.label(10),
                color=LINE_COLOR)
        start = end
    ax.add_patch(Circle((0, 0), 1.0, fill=False, edgecolor=LINE_COLOR,
                        linewidth=LINE_WIDTH * 1.2))
    # The pointer, so it reads as a spinner and not a pie chart.
    ax.plot([0, 0], [0, 0.78], color=ACCENT_COLOR, linewidth=LINE_WIDTH * 2.0,
            solid_capstyle="round", zorder=5)
    ax.add_patch(Circle((0, 0), 0.07, facecolor=ACCENT_COLOR, edgecolor="none",
                        zorder=6))
    ax.set_xlim(-1.15, 1.15)
    ax.set_ylim(-1.15, 1.15)
    _finish(fig, ax, out)


def tree_diagram(spec: dict, out: Path, f: _Fonts) -> None:
    """A one or two stage probability tree (Years 7-8, then Year 10).

    Branch labels are the probabilities; the leaf labels are the outcomes.
    Sample space and conditional probability are both taught off this picture,
    and "list the sample space" asked in prose gets a list back rather than a
    structure.
    """
    import matplotlib.pyplot as plt

    stages = spec.get("stages") or []
    if not (1 <= len(stages) <= 2):
        raise ValueError(f"a tree here has 1 or 2 stages, got {len(stages)}")
    branches = []
    for st in stages:
        opts = st.get("branches") or st.get("options") or []
        if not (2 <= len(opts) <= 3):
            raise ValueError("each stage needs 2-3 branches")
        branches.append([(str(o.get("label", "")), str(o.get("p", "")))
                         if isinstance(o, dict) else (str(o), "")
                         for o in opts])

    first = branches[0]
    second = branches[1] if len(branches) > 1 else None
    leaves = len(first) * (len(second) if second else 1)
    fig, ax = plt.subplots(figsize=(1.55 * (len(branches) + 0.9),
                                    0.44 * leaves + 0.6), dpi=180)

    def node_text(x, y, text, ha="left"):
        if text:
            ax.text(x, y, text, ha=ha, va="center", fontsize=f.label(10),
                    color=LINE_COLOR)

    span = leaves - 1
    first_ys = []
    per = leaves / len(first)
    for i in range(len(first)):
        first_ys.append(span / 2 - (i * per + (per - 1) / 2))

    for i, ((label, p), y) in enumerate(zip(first, first_ys)):
        ax.plot([0.10, 1.0], [0, y], color=LINE_COLOR, linewidth=LINE_WIDTH * 0.9)
        if p:
            ax.text(0.52, y / 2 + 0.16, p, ha="center", va="bottom",
                    fontsize=f.label(9), color=ACCENT_COLOR)
        node_text(1.08, y, label)
        if second:
            for j, (label2, p2) in enumerate(second):
                y2 = y + (len(second) - 1) / 2 - j
                ax.plot([1.48, 2.34], [y, y2], color=LINE_COLOR,
                        linewidth=LINE_WIDTH * 0.9)
                if p2:
                    ax.text(1.90, (y + y2) / 2 + 0.16, p2, ha="center",
                            va="bottom", fontsize=f.label(9), color=ACCENT_COLOR)
                node_text(2.42, y2, label2)
    ax.plot([0.10], [0], marker="o", markersize=5, color=LINE_COLOR)
    ax.set_xlim(-0.05, (3.35 if second else 1.9))
    ax.set_ylim(-span / 2 - 0.9, span / 2 + 0.9)
    ax.set_aspect("auto")
    ax.axis("off")
    _save(fig, out)


def venn(spec: dict, out: Path, f: _Fonts) -> None:
    """A two or three set Venn diagram (Years 9-10, and comparing two texts).

    Region contents are given by position: "a" is left only, "b" is right only,
    "ab" is the overlap, "none" sits outside both. For three sets the regions
    are a, b, c, ab, ac, bc, abc and none. Counts or short words both work,
    which is why English uses the same renderer to compare two texts.
    """
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle

    sets = spec.get("sets") or []
    sets = [str(s) for s in sets]
    if len(sets) not in (2, 3):
        raise ValueError(f"a Venn here takes 2 or 3 sets, got {len(sets)}")
    regions = {str(k).strip().lower(): str(v)
               for k, v in (spec.get("regions") or {}).items()}
    valid = ({"a", "b", "ab", "none"} if len(sets) == 2
             else {"a", "b", "c", "ab", "ac", "bc", "abc", "none"})
    unknown = set(regions) - valid
    if unknown:
        raise ValueError(f"unknown Venn regions {sorted(unknown)}; "
                         f"expected {sorted(valid)}")

    if len(sets) == 2:
        centres = [(-0.42, 0.0), (0.42, 0.0)]
        r = 0.78
        spots = {"a": (-0.78, 0.0), "b": (0.78, 0.0), "ab": (0.0, 0.0),
                 "none": (-1.30, -1.14)}
        names = {0: (-0.72, 0.92), 1: (0.72, 0.92)}
        fig, ax = plt.subplots(figsize=(2.9, 2.3), dpi=180)
    else:
        centres = [(-0.44, 0.26), (0.44, 0.26), (0.0, -0.48)]
        r = 0.80
        spots = {"a": (-0.80, 0.52), "b": (0.80, 0.52), "c": (0.0, -0.94),
                 "ab": (0.0, 0.62), "ac": (-0.46, -0.26), "bc": (0.46, -0.26),
                 "abc": (0.0, 0.02), "none": (-1.30, -1.64)}
        names = {0: (-0.92, 1.14), 1: (0.92, 1.14), 2: (0.0, -1.42)}
        fig, ax = plt.subplots(figsize=(2.7, 2.7), dpi=180)

    # A count that belongs to neither set has to sit inside something, or it
    # reads as a stray number under the picture rather than as part of it. The
    # rectangle is the universal set, which is what makes "how many altogether"
    # answerable from the diagram.
    if regions.get("none"):
        from matplotlib.patches import Rectangle

        box_bottom = -1.30 if len(sets) == 2 else -1.80
        ax.add_patch(Rectangle((-1.48, box_bottom), 2.96, 1.38 - box_bottom,
                               fill=False, edgecolor=LINE_COLOR,
                               linewidth=LINE_WIDTH * 0.8))
    for i, (cx, cy) in enumerate(centres):
        ax.add_patch(Circle((cx, cy), r, facecolor=SERIES_FILLS[i % 4],
                            alpha=0.28, edgecolor=LINE_COLOR,
                            linewidth=LINE_WIDTH))
    for i, name in enumerate(sets):
        x, y = names[i]
        ax.text(x, y, name, ha="center", va="center", fontsize=f.label(10),
                color=LINE_COLOR)
    for key, text in regions.items():
        if not text:
            continue
        x, y = spots[key]
        ax.text(x, y, text, ha="center", va="center", fontsize=f.label(10),
                color=LINE_COLOR)
    pad = 1.45 if len(sets) == 2 else 1.95
    ax.set_xlim(-1.55, 1.55)
    ax.set_ylim(-pad, 1.45)
    _finish(fig, ax, out)


# ---------------------------------------------------------------------------
# The Cartesian plane
# ---------------------------------------------------------------------------

def coordinate_plane(spec: dict, out: Path, f: _Fonts) -> None:
    """Axes with plotted points, straight lines and parabolas.

    One renderer covers the Cartesian plane (Years 5-6), plotting and reading
    linear relationships (7-8), gradient and intercept, simultaneous equations
    and quadratics (9-10). Every one of those was previously taught by
    describing a graph.

    lines:  [{"m": 2, "c": -1}] or [{"points": [[0,1],[3,7]]}]
    curves: [{"a": 1, "b": 0, "c": -4}] for y = ax^2 + bx + c
    """
    import matplotlib.pyplot as plt

    x_range = spec.get("x_range") or [-5, 5]
    y_range = spec.get("y_range") or x_range
    x0, x1 = float(x_range[0]), float(x_range[1])
    y0, y1 = float(y_range[0]), float(y_range[1])
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        raise ValueError("axis ranges must increase")
    if x1 - x0 > 40 or y1 - y0 > 40:
        raise ValueError("an axis spanning more than 40 units is unreadable "
                         "at booklet size")

    points = spec.get("points") or []
    labels = spec.get("labels") or []
    lines = spec.get("lines") or []
    curves = spec.get("curves") or []
    if not (points or lines or curves):
        raise ValueError("an empty grid asks nothing; plot a point, a line or "
                         "a curve")

    # Size the figure to land inside the print box without being scaled down.
    # A square grid authored 2.6in tall is 6.6cm, gets squeezed into a 4.8cm
    # box, and the legibility pass then has to enlarge every number to keep it
    # readable, so the numbers end up a fifth of a grid square tall and a point
    # label lands on the axis it was plotted beside. Fitting the box up front
    # is what keeps the type in proportion to the grid.
    height_in, width_in = 1.85, 2.90
    if width_in * (y1 - y0) / (x1 - x0) <= height_in:
        height_in = width_in * (y1 - y0) / (x1 - x0)
    else:
        width_in = height_in * (x1 - x0) / (y1 - y0)
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=180)

    def tick_step(span: float) -> int:
        """How often to number the axis so the numbers do not run together.

        A 2.6 inch axis over ten units gives each unit a quarter of an inch,
        and "-5" at the 9pt floor is wider than that, so numbering every unit
        printed "-5-4-3-2-1" as one smear. The gridlines still mark every unit;
        only the numbering thins out.
        """
        if span <= 6:
            return 1
        if span <= 16:
            return 2
        return 5

    step_x = tick_step(x1 - x0)
    step_y = tick_step(y1 - y0)
    for x in range(int(math.ceil(x0)), int(math.floor(x1)) + 1):
        ax.plot([x, x], [y0, y1], color=LINE_COLOR, alpha=0.16, linewidth=0.6)
    for y in range(int(math.ceil(y0)), int(math.floor(y1)) + 1):
        ax.plot([x0, x1], [y, y], color=LINE_COLOR, alpha=0.16, linewidth=0.6)
    ax.plot([x0, x1], [0, 0], color=LINE_COLOR, linewidth=LINE_WIDTH)
    ax.plot([0, 0], [y0, y1], color=LINE_COLOR, linewidth=LINE_WIDTH)
    # Numbered ticks land on multiples of the step, so a thinned axis reads
    # 2, 4, 6 rather than whatever happens to fall first from the left edge.
    def numbered(lo: float, hi: float, step: int) -> range:
        first = int(math.ceil(lo / step)) * step
        return range(first, int(math.floor(hi)) + 1, step)

    for x in numbered(x0, x1, step_x):
        if x:
            ax.text(x, -(y1 - y0) * 0.035, str(x), ha="center", va="top",
                    fontsize=f.label(8.5), color=LINE_COLOR)
    for y in numbered(y0, y1, step_y):
        if y:
            ax.text(-(x1 - x0) * 0.02, y, str(y), ha="right", va="center",
                    fontsize=f.label(8.5), color=LINE_COLOR)

    def plot_fn(fn, colour):
        n = 160
        xs, ys = [], []
        for i in range(n + 1):
            x = x0 + (x1 - x0) * i / n
            y = fn(x)
            if y0 - (y1 - y0) <= y <= y1 + (y1 - y0):
                xs.append(x)
                ys.append(y)
            else:
                if xs:
                    ax.plot(xs, ys, color=colour, linewidth=LINE_WIDTH)
                xs, ys = [], []
        if xs:
            ax.plot(xs, ys, color=colour, linewidth=LINE_WIDTH)

    for i, ln in enumerate(lines):
        colour = SERIES_FILLS[i % 4] if i else ACCENT_COLOR
        if "points" in ln:
            (px0, py0), (px1, py1) = ln["points"][0], ln["points"][1]
            if abs(float(px1) - float(px0)) < 1e-9:
                ax.plot([float(px0), float(px0)], [y0, y1], color=colour,
                        linewidth=LINE_WIDTH)
                continue
            m = (float(py1) - float(py0)) / (float(px1) - float(px0))
            c = float(py0) - m * float(px0)
        else:
            m, c = float(ln.get("m", 1)), float(ln.get("c", 0))
        plot_fn(lambda x, m=m, c=c: m * x + c, colour)
    for i, cv in enumerate(curves):
        a, b, c = float(cv.get("a", 1)), float(cv.get("b", 0)), float(cv.get("c", 0))
        if abs(a) < 1e-12:
            raise ValueError("a parabola needs a non-zero a; use a line instead")
        plot_fn(lambda x, a=a, b=b, c=c: a * x * x + b * x + c,
                SERIES_FILLS[i % 4] if i else ACCENT_COLOR)

    for i, p in enumerate(points):
        px, py = float(p[0]), float(p[1])
        ax.plot([px], [py], marker="o", markersize=7, color=SHADE_COLOR,
                markeredgecolor=LINE_COLOR, zorder=5)
        if i < len(labels) and labels[i]:
            # The label goes on the side of the point facing AWAY from the
            # origin. Always placing it up and to the right puts the label for
            # a third-quadrant point on top of the axis numbering, which is
            # how a point at (-3, -2) came to print as "B2" against the x axis.
            right, up = px < 0, py < 0
            ax.text(px + (x1 - x0) * (-0.03 if right else 0.03),
                    py + (y1 - y0) * (-0.035 if up else 0.035),
                    str(labels[i]),
                    ha="right" if right else "left",
                    va="top" if up else "bottom",
                    fontsize=f.label(10), color=LINE_COLOR, zorder=6)

    ax.set_xlim(x0 - (x1 - x0) * 0.06, x1 + (x1 - x0) * 0.06)
    ax.set_ylim(y0 - (y1 - y0) * 0.10, y1 + (y1 - y0) * 0.06)
    ax.set_aspect("auto")
    ax.axis("off")
    _save(fig, out)


RENDERERS = {
    "bar_chart": bar_chart,
    "picture_graph": picture_graph,
    "tally": tally,
    "dot_plot": dot_plot,
    "scatter": scatter,
    "box_plot": box_plot,
    "stem_leaf": stem_leaf,
    "spinner": spinner,
    "tree_diagram": tree_diagram,
    "venn": venn,
    "coordinate_plane": coordinate_plane,
}
