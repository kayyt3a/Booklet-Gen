"""The notation on a figure, set the way the notation beside it is set.

Every other string in the booklet passes `formatter._escape`, which runs the
deterministic notation pass in `mathnotation.py`: `x^4` becomes x⁴, `*` becomes
×, `sqrt(` becomes √, `cm^2` becomes cm². Diagram labels did not. They come out
of the model in the `diagram_spec` alongside the question, go straight to
matplotlib and are drawn exactly as written, so a booklet whose prose said
"cm²" printed a figure captioned "cm^2" two centimetres below it.

That is worse inside a figure than it is in a paragraph. A figure is what the
eye goes to, and one caret in a picture on an otherwise typeset page is the
"assembled from parts" tell the whole notation job existed to remove. It was
found on real specs: a column graph axis reading "Area (cm^2)", a word web with
"3*4" and "sqrt(16)" in its nodes, a rectangle whose unit was written "cm^2".

The pass is markup-free (`mathnotation.normalise_plain`). Matplotlib cannot
render ReportLab's `<super>` tag and would print it, so an index with no
Unicode superscript spelling is left exactly as the model wrote it. See
THE MARKUP-FREE MODE in `mathnotation.py` for why that is the better failure.

WHERE IT IS APPLIED
    On the SPEC, once, in `diagrams.render_diagram`, before the cache key is
    taken and before any renderer runs. Not at each of the hundred-odd
    `ax.text` calls, for three reasons:

      * it cannot be forgotten by the next renderer somebody adds;
      * several figures MEASURE their text to lay themselves out (the literacy
        figures place a node from `get_window_extent`, `compare` sizes its
        columns from `textbbox`), and text rewritten after it was measured
        sits in a box built for the other string;
      * `compare` draws its labels with Pillow, not matplotlib, so a sweep of
        matplotlib artists would miss them entirely.

    Taking the key off the normalised spec also means "cm^2" and "cm²" are one
    cache entry rather than two identical PNGs, which is what they draw as.

    `style.save_figure` sweeps the finished figure's text artists as a
    backstop, for a string a renderer built from something other than the
    spec. It is a second line, not the mechanism.

WHAT IS SKIPPED
    Keys whose value is a token the code dispatches on rather than text the
    child reads. `type` picks the renderer, `operation` picks the arithmetic
    sign, `unknown` names the dimensions whose label is withheld and is matched
    against a fixed word list. None of them is ever drawn as written, and a
    notation rule has no business anywhere near a lookup key.
"""
from __future__ import annotations

from ..mathnotation import normalise_plain

# See WHAT IS SKIPPED above.
_TOKEN_KEYS = frozenset({"type", "operation", "unknown"})


def label_text(text: str) -> str:
    """One drawn string, normalised. Anything but a string comes back as-is."""
    if not isinstance(text, str) or not text:
        return text
    try:
        return normalise_plain(text)
    except Exception:               # pragma: no cover - never lose a figure
        return text


def normalise_spec(value):
    """A copy of a diagram spec with every drawn string normalised.

    Walks dicts, lists and tuples, so the sub-specs of a `compare` composite
    and the per-series names of a column graph are reached as well as the top
    level. Numbers, booleans and None are returned untouched.
    """
    if isinstance(value, str):
        return label_text(value)
    if isinstance(value, dict):
        return {k: (v if k in _TOKEN_KEYS else normalise_spec(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [normalise_spec(v) for v in value]
    if isinstance(value, tuple):
        return tuple(normalise_spec(v) for v in value)
    return value
