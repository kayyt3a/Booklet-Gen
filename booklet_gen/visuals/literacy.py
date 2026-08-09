"""Figures for English.

English had no diagram support at all, on the reasoning that a passage is
words and words are not a picture. That is true of the passage and false of
almost everything taught about it. The shape of a narrative, the parts of a
sentence, the relationship between two texts and the family around a root word
are all structures, and a structure explained only in prose is the one thing a
child cannot hold in their head while also reading the prose.

Nothing here illustrates a story. These are diagrams of how language is put
together, which is what the Language and Literacy strands actually assess.
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
    _Fonts,
    _finish,
    _measure,
    _pixel_axes,
    _px,
    _width_budget,
)






def _wrap(slots: list[float], gap: float, budget: float) -> list[list[int]]:
    """Greedily pack slot widths into lines no wider than `budget`."""
    lines: list[list[int]] = [[]]
    used = 0.0
    for i, w in enumerate(slots):
        extra = w + (gap if lines[-1] else 0.0)
        if lines[-1] and used + extra > budget:
            lines.append([i])
            used = w
        else:
            lines[-1].append(i)
            used += extra
    return lines


def _save(fig, out: Path) -> None:
    import matplotlib.pyplot as plt

    fig.savefig(out, bbox_inches="tight", pad_inches=0.10, transparent=False)
    plt.close(fig)


# ---------------------------------------------------------------------------

def sentence_parts(spec: dict, out: Path, f: _Fonts) -> None:
    """A sentence with its parts underlined and named (Years 3-10 grammar).

    Subject and verb, main and subordinate clause, noun group and verb group:
    all of it is taught by pointing at part of a sentence, and pointing is
    exactly what prose cannot do. The sentence stays whole and readable, with
    a bracket under each named part, so the child sees the part in place
    rather than quoted out of it.
    """
    parts = spec.get("parts") or []
    if not (2 <= len(parts) <= 5):
        raise ValueError(f"name 2-5 parts of the sentence, got {len(parts)}")
    texts, roles = [], []
    for p in parts:
        if isinstance(p, str):
            texts.append(p)
            roles.append("")
        else:
            texts.append(str(p.get("text", "")))
            roles.append(str(p.get("role", "")))
    if any(not t.strip() for t in texts):
        raise ValueError("every part needs some words in it")
    if not any(r.strip() for r in roles):
        raise ValueError("a sentence-parts figure with no roles names nothing")

    size = f.label(11)
    role_size = f.label(8.5)
    # Every gap below is a multiple of the type size, so the whole figure grows
    # together when the legibility pass enlarges the text.
    gap = _px(size) * 0.30
    drop = _px(size) * 0.42          # words down to the bracket
    tick = _px(size) * 0.26          # the turned-up ends of the bracket
    line_h = _px(size) * 1.05 + drop + tick + _px(role_size) * 1.9
    fig, ax = _pixel_axes(3.0, line_h / DPI)

    # Draw once off-canvas to measure, then place. Two passes, because a
    # bracket has to span exactly the words it names.
    word_artists = [ax.text(0, -50000, t, fontsize=size, color=LINE_COLOR,
                            ha="left", va="baseline") for t in texts]
    role_artists = [ax.text(0, -50000, r, fontsize=role_size, color=ACCENT_COLOR,
                            ha="center", va="top") for r in roles]
    word_w = _measure(fig, word_artists)
    role_w = _measure(fig, role_artists)

    # Each part must be at least as wide as the role name under it, or the
    # names collide while the words they label sit far apart.
    slots = [max(w, rw + _px(role_size) * 0.5) for w, rw in zip(word_w, role_w)]
    # A sentence too long to print legibly on one line wraps, the way a
    # sentence in a book does. It still reads as a sentence; drawn on one line
    # at any font size it would print at about six point.
    lines = _wrap(slots, gap, _width_budget(size))
    widths = [sum(slots[i] for i in ln) + gap * (len(ln) - 1) for ln in lines]

    width_in = (max(widths) + _px(size) * 0.6) / DPI
    height_in = line_h * len(lines) / DPI
    fig.set_size_inches(width_in, height_in)
    px_w, px_h = width_in * DPI, height_in * DPI
    ax.set_xlim(0, px_w)
    ax.set_ylim(0, px_h)

    for row, line in enumerate(lines):
        baseline = px_h - row * line_h - _px(size) * 1.05
        x = (px_w - widths[row]) / 2
        for i in line:
            slot = slots[i]
            word_artists[i].set_position((x + (slot - word_w[i]) / 2, baseline))
            # A line under the words with a tick turned up at each end.
            y = baseline - drop
            ax.plot([x, x + slot], [y, y], color=ACCENT_COLOR,
                    linewidth=LINE_WIDTH * 0.9)
            for xe in (x, x + slot):
                ax.plot([xe, xe], [y, y + tick], color=ACCENT_COLOR,
                        linewidth=LINE_WIDTH * 0.9)
            role_artists[i].set_position((x + slot / 2, y - tick * 0.6))
            x += slot + gap
    _save(fig, out)


def text_structure(spec: dict, out: Path, f: _Fonts) -> None:
    """The stages of a text type, in order (Years 3-10 text structure).

    Narrative, information report, procedure, persuasive text and the
    analytical paragraph are all defined by their stages and the order those
    stages come in. A list of stages in a paragraph of prose is the same
    information with the ordering thrown away.

    Laid out as a numbered column with an arrow running down it, rather than
    boxes across the page: the print box is 7.5cm wide, and "Complication"
    does not fit inside a quarter of that at a size a child can read.
    """
    stages = spec.get("stages") or []
    if not (2 <= len(stages) <= 6):
        raise ValueError(f"a text structure has 2-6 stages, got {len(stages)}")
    names, notes = [], []
    for s in stages:
        if isinstance(s, str):
            names.append(s)
            notes.append("")
        else:
            names.append(str(s.get("name", "")))
            notes.append(str(s.get("note", "")))
    if any(not n.strip() for n in names):
        raise ValueError("every stage needs a name")

    title = str(spec.get("title", "") or "")
    name_size = f.label(10.5)
    # The stage names are the content and carry the label floor. The glosses
    # beside them are a parenthetical, and they take the caption floor for a
    # concrete reason: this figure's canvas is built entirely out of its own
    # type, so growing the font grows the picture by the same factor and the
    # printed size never moves. Everything on it therefore prints in a fixed
    # ratio to the largest text, and demanding that a four-word gloss match a
    # heading is demanding they be the same size. The gloss is a caption, so
    # it is sized and floored as one.
    note_size = f.note(9)
    # Row pitch, disc radius and the gap after the title all come off the type
    # size, so enlarging the text moves the rows apart with it.
    row = _px(name_size) * 2.1
    radius = _px(name_size) * 0.62
    title_gap = _px(name_size) * 2.0 if title else 0.0
    height_in = (row * len(stages) + title_gap + _px(name_size) * 0.9) / DPI
    fig, ax = _pixel_axes(3.0, height_in)

    name_artists = [ax.text(0, -5000, n, fontsize=name_size, color=LINE_COLOR,
                            ha="left", va="center") for n in names]
    note_artists = [ax.text(0, -5000, n, fontsize=note_size, color=ACCENT_COLOR,
                            ha="left", va="center") for n in notes]
    name_w = _measure(fig, name_artists)
    note_w = _measure(fig, note_artists)

    left = radius * 2.6
    col = max(name_w) + _px(name_size) * 0.8
    wide = left + col + (max(note_w) if any(notes) else 0) + _px(name_size) * 0.5
    # Notes beside the names is the layout that reads best, but only while the
    # figure stays inside the width its type can survive being scaled into.
    # Past that the note drops under its name and the rows get taller, which
    # costs a little elegance and keeps the words readable.
    budget = _width_budget(name_size)
    stacked = any(notes) and wide > budget
    if stacked:
        row = _px(name_size) * 3.0
        height_in = (row * len(stages) + title_gap + _px(name_size) * 0.9) / DPI
        wide = max(left + max(name_w), left + max(note_w)) + _px(name_size) * 0.5
    width_in = min(wide, budget) / DPI if not stacked else wide / DPI
    fig.set_size_inches(width_in, height_in)
    px_w = width_in * DPI
    ax.set_xlim(0, px_w)
    ax.set_ylim(0, height_in * DPI)

    top = height_in * DPI - _px(name_size) * 0.45
    if title:
        ax.text(px_w / 2, top, title, ha="center", va="top",
                fontsize=f.label(11), color=LINE_COLOR)
        top -= title_gap

    for i, (name, note, note_artist) in enumerate(zip(names, notes, note_artists)):
        y = top - row / 2 - i * row
        ax.add_patch(_disc(ax, radius, y, radius))
        ax.text(radius, y, str(i + 1), ha="center", va="center",
                fontsize=f.note(8.5), color="white", zorder=4)
        name_artists[i].set_position(
            (left, y + (_px(name_size) * 0.62 if stacked else 0)))
        if note and stacked:
            note_artist.set_position((left, y - _px(name_size) * 0.72))
        elif note:
            note_artist.set_position((left + col, y))
        else:
            note_artist.remove()
        if i < len(names) - 1:
            ax.annotate("", xy=(radius, y - row + radius * 1.15),
                        xytext=(radius, y - radius * 1.15),
                        arrowprops=dict(arrowstyle="-|>", color=LINE_COLOR,
                                        linewidth=LINE_WIDTH * 0.8,
                                        shrinkA=0, shrinkB=0))
    _save(fig, out)


def _disc(ax, x: float, y: float, r: float):
    from matplotlib.patches import Circle

    return Circle((x, y), r, facecolor=SHADE_COLOR, edgecolor="none", zorder=3)


def narrative_arc(spec: dict, out: Path, f: _Fonts) -> None:
    """The story mountain: rising action, climax, falling action (Years 3-6).

    Where the tension sits is a shape, and the point of teaching it is that a
    child can see their own draft has no peak. Labels are the child's own
    stages, so the same curve serves a plot they are planning and one they are
    analysing.
    """
    import matplotlib.pyplot as plt

    labels = spec.get("stages") or spec.get("labels") or []
    labels = [str(s.get("name", s) if isinstance(s, dict) else s) for s in labels]
    if not (3 <= len(labels) <= 5):
        raise ValueError(f"a story arc wants 3-5 labelled points, got {len(labels)}")

    fig, ax = plt.subplots(figsize=(2.95, 1.95), dpi=DPI)
    n = 240
    xs = [i / n for i in range(n + 1)]
    # A skewed hump: the climax sits about two thirds along, which is what
    # makes it a story arc rather than a symmetrical hill.
    peak = 0.66

    def curve(x: float) -> float:
        return math.sin(math.pi * (x ** (math.log(0.5) / math.log(peak))))

    ax.plot(xs, [curve(x) for x in xs], color=LINE_COLOR, linewidth=LINE_WIDTH * 1.3)

    at = [i / (len(labels) - 1) for i in range(len(labels))]
    for i, (x, text) in enumerate(zip(at, labels)):
        y = curve(x)
        ax.plot([x], [y], marker="o", markersize=6, color=ACCENT_COLOR,
                markeredgecolor=LINE_COLOR, zorder=4)
        # The two ends hang below the curve, where there is open space. Every
        # interior label goes above it, on one of two heights taken in turn.
        # "Rising action" and "Falling action" are each about a third of the
        # printed width, so three of them in a row overlap horizontally
        # whatever you do; putting neighbours on different heights is what
        # stops the overlap being a collision.
        if i in (0, len(labels) - 1):
            ax.text(x, y - 0.12, text, ha="left" if i == 0 else "right",
                    va="top", fontsize=f.label(9), color=LINE_COLOR)
        else:
            # Interior labels sit on rows ABOVE the whole curve, with a leader
            # down to their point. Two reasons for the rows: a label placed
            # just above its own point lands under the curve wherever the
            # curve is still climbing, which is most of the left half; and
            # "Rising action" and "Falling action" are each over half the
            # width of the plot, so any two of them sharing a row touch. There
            # are at most three interior points, so three rows means no two
            # labels are ever on the same one. The climax takes the top row,
            # which also reads as the shape of the story.
            row = (1.10, 1.78, 1.44)[(i - 1) % 3]
            ax.plot([x, x], [y + 0.04, row - 0.03], color=LINE_COLOR,
                    linewidth=0.8, alpha=0.5)
            ax.text(x, row, text, ha="center", va="bottom",
                    fontsize=f.label(9), color=LINE_COLOR)
    ax.set_xlim(-0.18, 1.18)
    ax.set_ylim(-0.55, 2.12)
    ax.set_aspect("auto")
    ax.axis("off")
    _save(fig, out)


def word_web(spec: dict, out: Path, f: _Fonts) -> None:
    """A word in the middle with related words around it (Years 3-8).

    Synonyms and antonyms, a root and the words built on it, a prefix and what
    it does to each word it joins. All of these are one-to-many, and a
    one-to-many relationship written as a comma-separated list reads as a
    sequence, which is the wrong shape.
    """
    import matplotlib.pyplot as plt

    centre = str(spec.get("centre", spec.get("word", "")) or "").strip()
    around = [str(w) for w in (spec.get("around") or spec.get("words") or [])]
    if not centre:
        raise ValueError("a word web needs a word in the middle")
    if not (3 <= len(around) <= 6):
        raise ValueError(f"a word web wants 3-6 outer words, got {len(around)}")

    fig, ax = plt.subplots(figsize=(2.95, 2.2), dpi=DPI)
    # An ellipse rather than a circle, because the print box is wider than it
    # is tall and words are wider than they are tall.
    rx, ry = 1.52, 0.86
    for i, word in enumerate(around):
        a = math.radians(90 + 360 * i / len(around))
        x, y = rx * math.cos(a), ry * math.sin(a)
        ax.plot([x * 0.30, x * 0.80], [y * 0.34, y * 0.80],
                color=LINE_COLOR, linewidth=LINE_WIDTH * 0.8, alpha=0.7)
        ax.text(x, y, word, ha="center", va="center", fontsize=f.label(10),
                color=LINE_COLOR,
                bbox=dict(boxstyle="round,pad=0.28", facecolor="white",
                          edgecolor=LINE_COLOR, linewidth=LINE_WIDTH * 0.7))
    ax.text(0, 0, centre, ha="center", va="center", fontsize=f.label(11.5),
            color="white",
            bbox=dict(boxstyle="round,pad=0.34", facecolor=SHADE_COLOR,
                      edgecolor=LINE_COLOR, linewidth=LINE_WIDTH * 0.8))
    ax.set_xlim(-rx - 0.55, rx + 0.55)
    ax.set_ylim(-ry - 0.42, ry + 0.42)
    ax.set_aspect("auto")
    ax.axis("off")
    _save(fig, out)


RENDERERS = {
    "sentence_parts": sentence_parts,
    "text_structure": text_structure,
    "narrative_arc": narrative_arc,
    "word_web": word_web,
}

__all__ = ["RENDERERS", "sentence_parts", "text_structure", "narrative_arc",
           "word_web", "SHADE_ALPHA", "_finish"]
