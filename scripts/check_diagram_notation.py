"""Checks a diagram is lettered in mathematics, not in source code.

Every string in the booklet passes `formatter._escape`, which runs the
deterministic notation pass: `x^4` prints x⁴, `*` prints ×, `sqrt(` prints √,
`cm^2` prints cm². Diagram labels did not. They arrive in the model's
`diagram_spec`, go straight to matplotlib and are drawn exactly as written, so
a page whose prose read "cm²" carried a column graph whose axis read
"Area (cm^2)" two centimetres below it.

A figure is worse than a paragraph for this. It is where the eye goes, and one
caret in a picture on an otherwise typeset page is the "assembled from parts"
tell the whole notation job exists to remove.

The fix cannot reuse the formatter's pass unchanged, because that pass may emit
ReportLab `<super>` markup for an index Unicode cannot spell, and matplotlib
would draw the tag. So the diagram path takes a markup-free mode that sets what
it can as characters and leaves the rest exactly as written.

This file proves it by RENDERING, not by testing the string function. A string
test passing while the figure still prints a caret is the precise failure mode
here, so every claim below is settled on pixels:

  * a spec written in source code and the same spec written correctly render
    to the identical image, and both differ from what the raw spec drew before
    the fix (or the fixture is measuring nothing);
  * an index with no superscript spelling is left alone, pixel for pixel, and
    is NOT the ReportLab tag, which would print across the figure;
  * the backstop sweep catches a label a renderer wrote itself, not just one
    that came out of a spec;
  * every glyph the markup-free pass can emit exists in the face matplotlib
    actually draws with, so nothing swaps a caret for a box;
  * the render version moved, because a warm disk on the deployed instance
    would otherwise go on serving the figures with the carets in them.

    PYTHONPATH=. python scripts/check_diagram_notation.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image as PILImage

from booklet_gen import mathnotation as N
from booklet_gen.visuals import diagrams as D
from booklet_gen.visuals import labels as L
from booklet_gen.visuals import style as S

_passed = 0
_failed: list[str] = []


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg):
    _failed.append(msg)
    print("  FAIL:", msg)


def check(cond, good, consequence):
    if cond:
        ok(good)
    else:
        bad(consequence)
    return cond


def summary(mark, msg):
    """One line for a whole loop, but only when the loop found nothing."""
    if len(_failed) == mark:
        ok(msg)


TMP = Path(tempfile.mkdtemp(prefix="folio-fignote-"))
_CACHE = D.CACHE_DIR


def render(spec: dict, tag: str, bypass: bool = False):
    """Render one spec into its own cache directory.

    Its own directory because several of the comparisons below are between
    specs that hash to the SAME cache key, and a shared cache would hand the
    second call the first call's file and make the comparison vacuous.

    `bypass` turns the notation pass off at both the places it runs, which is
    how the "before" image is produced.
    """
    D.CACHE_DIR = TMP / tag
    spec_pass, sweep = D.normalise_spec, S._set_notation
    if bypass:
        D.normalise_spec = lambda s: s
        S._set_notation = lambda fig: None
    try:
        return D.render_diagram(spec)
    finally:
        D.normalise_spec, S._set_notation = spec_pass, sweep
        D.CACHE_DIR = _CACHE


def pixels(path):
    with PILImage.open(path) as im:
        return np.array(im.convert("RGBA"))


def differs(a, b) -> int:
    """How many pixels two renders disagree on. A size mismatch counts as all."""
    pa, pb = pixels(a), pixels(b)
    if pa.shape != pb.shape:
        return max(pa.size, pb.size)
    return int(np.count_nonzero(np.any(pa != pb, axis=-1)))


# ---------------------------------------------------------------------------
# The mode itself: characters only, never a tag
# ---------------------------------------------------------------------------
print("\nTHE MARKUP-FREE MODE EMITS CHARACTERS AND NEVER A TAG")

# Left: what a model actually wrote in a diagram_spec. Right: what the child
# has to see on the figure.
PLAIN_CASES = [
    ("Area (cm^2)", "Area (cm²)", "a column graph axis"),
    ("cm^2", "cm²", "a unit written flat"),
    ("Volume in cm3", "Volume in cm³", "a unit written flatter still"),
    ("3*4", "3 × 4", "an array label"),
    ("Array written as a*b", "Array written as a × b", "an axis caption"),
    ("b x h", "b × h", "the area formula on a triangle"),
    ("sqrt(16)", "√(16)", "a root written as a function call"),
    ("x^2", "x²", "an index on an axis"),
    ("a^3", "a³", "an index in a word web node"),
    ("8000 / 2", "8000 ÷ 2", "a spaced slash divides"),
    ("3/4", "3/4", "an unspaced slash is a fraction and stays one"),
    ("km/h", "km/h", "and a rate stays a rate"),
    ("Solve for x", "Solve for x", "the unknown keeps its letter"),
    ("cubic centimetres", "cm³", "the unit named in words"),
    # The whole point of the mode: no superscript spelling, so no rewrite.
    ("f^(2 × 3)", "f^(2 × 3)", "an index containing a multiplication sign"),
    ("x^1.5", "x^1.5", "a decimal index"),
    ("x^Q", "x^Q", "an uppercase index"),
    ("x^q", "x^q", "the one lowercase letter with no superscript form"),
]
mark = len(_failed)
for raw, want, why in PLAIN_CASES:
    got = L.label_text(raw)
    if got != want:
        bad(f"{why}: {raw!r} drew as {got!r}, wanted {want!r}")
    if got != L.label_text(got):
        bad(f"{why}: the pass is not idempotent, {got!r} became "
            f"{L.label_text(got)!r}. Every comparison below assumes the "
            "correctly written form is a fixed point")
    if "<" in got or "super" in got or "&" in got:
        bad(f"{why}: {raw!r} produced markup matplotlib would print as text: "
            f"{got!r}")
summary(mark, f"{len(PLAIN_CASES)} labels set as characters, with the "
        "unspellable indices left exactly as written")

# The same strings through the paragraph path DO get markup, so the two modes
# are really two modes and this file is not just describing the default.
check("<super" in N.normalise_notation("f^(2 × 3)")
      and "<super" not in N.normalise_plain("f^(2 × 3)"),
      "the paragraph path still raises that index with markup, so the "
      "markup-free mode is a mode and not the only behaviour",
      f"the two modes agree on f^(2 × 3): "
      f"{N.normalise_notation('f^(2 × 3)')!r}. Either the PDF lost its raised "
      "index or the diagram path is about to print a tag")

# A spec key the code dispatches on is not text and must not be rewritten.
check(L.normalise_spec({"type": "l_shape", "operation": "x",
                        "unknown": ["height"]})
      == {"type": "l_shape", "operation": "x", "unknown": ["height"]},
      "the keys the renderer dispatches on come through untouched",
      "a lookup token was rewritten by the notation pass, so the renderer, the "
      "operator or the withheld dimension is chosen by a string that no longer "
      "matches the list it is looked up in")


# ---------------------------------------------------------------------------
# The face matplotlib actually draws with
# ---------------------------------------------------------------------------
print("\nEVERY GLYPH THE PASS CAN EMIT EXISTS IN THE FACE MATPLOTLIB DRAWS WITH")

import matplotlib                                              # noqa: E402
matplotlib.use("Agg")
from fontTools.ttLib import TTFont                             # noqa: E402
from matplotlib import font_manager                            # noqa: E402

face = font_manager.findfont(
    font_manager.FontProperties(family=matplotlib.rcParams["font.family"]))
cmap = set()
for table in TTFont(face)["cmap"].tables:
    cmap |= set(table.cmap)
missing = sorted(ch for ch in N.PLAIN_GLYPHS if ord(ch) not in cmap)
check(not missing,
      f"{Path(face).name} carries all {len(N.PLAIN_GLYPHS)} of them",
      f"{Path(face).name} is missing {''.join(missing)!r}. Matplotlib draws a "
      "missing glyph as a hollow box, which is worse on a figure than the "
      "caret it replaced")


# ---------------------------------------------------------------------------
# Rendered: the source-code spec and the correct spec are one picture
# ---------------------------------------------------------------------------
print("\nA SPEC WRITTEN IN SOURCE CODE RENDERS AS THE SPEC WRITTEN CORRECTLY")

# Each pair is (raw as a model writes it, the same thing written properly).
# Every renderer here reaches matplotlib by a different route: axis labels and
# tick labels and a legend, a dimension label built from a unit, a title, an
# annotation over a mark, and the `compare` labels, which are drawn with
# Pillow rather than matplotlib and so are missed by any sweep of artists.
PAIRS = [
    ("bar_chart",
     {"type": "bar_chart", "categories": ["3*4", "sqrt(16)", "x^2"],
      "values": [3, 5, 2], "x_label": "Array written as a*b",
      "y_label": "Area (cm^2)"},
     {"type": "bar_chart", "categories": ["3 × 4", "√(16)", "x²"],
      "values": [3, 5, 2], "x_label": "Array written as a × b",
      "y_label": "Area (cm²)"}),
    ("bar_chart legend",
     {"type": "bar_chart", "categories": ["A", "B"],
      "series": [{"name": "Area in cm^2", "values": [3, 5]},
                 {"name": "Volume in cm3", "values": [2, 4]}]},
     {"type": "bar_chart", "categories": ["A", "B"],
      "series": [{"name": "Area in cm²", "values": [3, 5]},
                 {"name": "Volume in cm³", "values": [2, 4]}]}),
    ("rectangle unit",
     {"type": "rectangle", "length": 8, "width": 3, "unit": "cm^2"},
     {"type": "rectangle", "length": 8, "width": 3, "unit": "cm²"}),
    ("number_line label",
     {"type": "number_line", "from": 0, "to": 4, "divisions": 4,
      "mark_at": [2], "label_at": ["sqrt(16) / 2"]},
     {"type": "number_line", "from": 0, "to": 4, "divisions": 4,
      "mark_at": [2], "label_at": ["√(16) ÷ 2"]}),
    ("scatter axes",
     {"type": "scatter", "points": [[1, 1], [2, 4], [3, 9], [4, 16]],
      "x_label": "side (cm)", "y_label": "area (cm^2)"},
     {"type": "scatter", "points": [[1, 1], [2, 4], [3, 9], [4, 16]],
      "x_label": "side (cm)", "y_label": "area (cm²)"}),
    ("word_web nodes",
     {"type": "word_web", "centre": "Area",
      "words": ["cm^2", "3*4", "sqrt(16)", "b x h"]},
     {"type": "word_web", "centre": "Area",
      "words": ["cm²", "3 × 4", "√(16)", "b × h"]}),
    ("compare labels, drawn with Pillow",
     {"type": "compare", "items": [
         {"label": "A: 4*3 cm^2",
          "spec": {"type": "rectangle", "length": 4, "width": 3, "unit": "cm"}},
         {"label": "B: 6*2 cm^2",
          "spec": {"type": "rectangle", "length": 6, "width": 2, "unit": "cm"}}]},
     {"type": "compare", "items": [
         {"label": "A: 4 × 3 cm²",
          "spec": {"type": "rectangle", "length": 4, "width": 3, "unit": "cm"}},
         {"label": "B: 6 × 2 cm²",
          "spec": {"type": "rectangle", "length": 6, "width": 2, "unit": "cm"}}]}),
]

mark = len(_failed)
for i, (name, raw, proper) in enumerate(PAIRS):
    before = render(raw, f"{i}-before", bypass=True)
    after = render(raw, f"{i}-after")
    want = render(proper, f"{i}-want")
    if not (before and after and want):
        bad(f"{name}: a render returned nothing "
            f"(before={before}, after={after}, want={want}), so this pair "
            "proves nothing")
        continue
    # The guard that stops this pair passing for the wrong reason: if the raw
    # spec drew the same picture without the fix, the label under test is not
    # on the figure at all and the equality below is vacuous.
    drift = differs(before, want)
    if not drift:
        bad(f"{name}: the raw spec drew the correct picture even with the "
            f"notation pass off, so this fixture cannot see the defect. Its "
            f"label is not reaching the canvas")
        continue
    gap = differs(after, want)
    if gap:
        bad(f"{name}: the figure a model's spec draws still differs from the "
            f"correctly written one on {gap} pixels ({after} vs {want}). A "
            "caret or an asterisk is printed inside the figure")

summary(mark, f"all {len(PAIRS)} figures draw the model's notation as the "
        "booklet's notation, pixel for pixel")


# ---------------------------------------------------------------------------
# The index no font can spell: left alone, and certainly not tagged
# ---------------------------------------------------------------------------
print("\nAN INDEX WITH NO SUPERSCRIPT SPELLING IS LEFT ALONE, NOT TAGGED")

# Three indices no font can spell, and one that can, all in one figure. The
# fourth is there because the answer must not be "give up on the whole label
# set": one unspellable index must not turn the pass off for the ones beside it.
HARD = {"type": "word_web", "centre": "Index laws",
        "words": ["f^(2 × 3)", "x^1.5", "x^Q", "a^3"]}
hard_before = render(HARD, "hard-before", bypass=True)
hard_after = render(HARD, "hard-after")
# What the figure has to end up saying, drawn with the pass off so it is
# literally these characters and nothing else.
WANT = {"type": "word_web", "centre": "Index laws",
        "words": ["f^(2 × 3)", "x^1.5", "x^Q", "a³"]}
hard_want = render(WANT, "hard-want", bypass=True)
# And what the paragraph path would have handed matplotlib, spelled out.
TAGGED = {"type": "word_web", "centre": "Index laws",
          "words": [N.normalise_notation("f^(2 × 3)"),
                    N.normalise_notation("x^1.5"),
                    N.normalise_notation("x^Q"), "a³"]}
tagged = render(TAGGED, "hard-tagged")

if not all((hard_before, hard_after, hard_want, tagged)):
    bad(f"a render returned nothing: {hard_before}, {hard_after}, "
        f"{hard_want}, {tagged}")
else:
    check(differs(hard_after, tagged) > 0
          and pixels(tagged).shape[1] > pixels(hard_after).shape[1] * 1.5,
          f"the markup a paragraph would carry draws "
          f"{pixels(tagged).shape[1] / pixels(hard_after).shape[1]:.1f} times "
          "wider on the canvas, which is the tag being printed as text",
          "the ReportLab markup drew the same figure as the plain text, so "
          "this file cannot tell a tag from an index and the assertions below "
          "mean nothing")
    check(differs(hard_before, hard_want) > 0,
          "and the figure really does change when the pass runs, so this "
          "section is measuring something",
          "the raw spec already drew the wanted figure, so the fixture cannot "
          "see the defect")
    # "Left alone" has to mean literally unchanged, not "changed into
    # something else that also happens to have no caret".
    check(differs(hard_after, hard_want) == 0,
          "an index the font cannot spell comes through exactly as the model "
          "wrote it, caret and all, while a^3 beside it is still set as a³",
          f"the figure differs from the wanted one on "
          f"{differs(hard_after, hard_want)} pixels ({hard_after} vs "
          f"{hard_want}). Either an unspellable index was approximated, which "
          "makes an index law say something other than the index law, or one "
          "unspellable label turned the pass off for the labels beside it")


# ---------------------------------------------------------------------------
# The backstop: a label a renderer wrote itself, not one out of a spec
# ---------------------------------------------------------------------------
print("\nA LABEL A RENDERER WRITES ITSELF IS SWEPT TOO")


def _probe(text):
    """A renderer that ignores its spec, so only the save-time sweep can help."""
    def draw(spec, out, f):
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(2.2, 0.9), dpi=D.DPI)
        ax.text(0.5, 0.5, text, ha="center", va="center",
                fontsize=f.label(12), color=D.LINE_COLOR)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        S.save_figure(fig, out, 0.06)
    return draw


PROBE = {"type": "_notation_probe"}
try:
    D._RENDERERS["_notation_probe"] = _probe("Total area: 4*3 cm^2")
    swept = render(PROBE, "probe-swept")
    unswept = render(PROBE, "probe-unswept", bypass=True)
    D._RENDERERS["_notation_probe"] = _probe("Total area: 4 × 3 cm²")
    proper = render(PROBE, "probe-proper")
finally:
    D._RENDERERS.pop("_notation_probe", None)

if swept and unswept and proper:
    check(differs(unswept, proper) > 0,
          "the hard-coded label really does draw differently when untouched, "
          "so the sweep has something to do",
          "the probe draws the same either way, so this section is vacuous")
    check(differs(swept, proper) == 0,
          "text a renderer put on the canvas without going through a spec is "
          "normalised at save time as well",
          f"a label written straight onto the figure kept its source-code "
          f"notation ({differs(swept, proper)} pixels differ from the correct "
          "render). Every renderer added from here on has to remember to "
          "normalise by hand")
else:
    bad(f"the probe renders failed: {swept}, {unswept}, {proper}")


# ---------------------------------------------------------------------------
# The cache, which is what decides whether any of this reaches a customer
# ---------------------------------------------------------------------------
print("\nTHE WARM DISK CANNOT GO ON SERVING THE FIGURES WITH THE CARETS IN THEM")

SPEC = {"type": "rectangle", "length": 8, "width": 3, "unit": "cm"}
now = D.RENDER_VERSION
current = D._cache_path(SPEC)
try:
    D.RENDER_VERSION = 2                 # the version this change was made at
    previous = D._cache_path(SPEC)
finally:
    D.RENDER_VERSION = now

check(now > 2,
      f"the render version moved past the one the carets were drawn under "
      f"(now {now})",
      f"the render version is still {now}. Nothing about a spec says how it "
      "was drawn, so every diagram already on the deployed instance's disk "
      "keeps its caret for as long as that disk lives, in exactly the "
      "booklets the customers who have already generated go back to")
check(current != previous,
      f"and the bump moves the cache path ({previous.name} -> {current.name}), "
      "so the old PNGs are unreachable rather than merely out of date",
      "the cache path is unchanged across the version bump, so bumping it "
      "achieved nothing and the stale figures are still served")

check(D._cache_path(L.normalise_spec({"type": "rectangle", "length": 8,
                                      "width": 3, "unit": "cm^2"}))
      == D._cache_path(L.normalise_spec({"type": "rectangle", "length": 8,
                                         "width": 3, "unit": "cm²"})),
      "and a spec written either way lands on one cache entry, because it "
      "draws one picture",
      "two specs that draw the identical figure are cached as two files, so "
      "the disk fills with duplicates of the same diagram")

print(f"\nrenders under {TMP}")
if _failed:
    print(f"\n{len(_failed)} DIAGRAM NOTATION CHECKS FAILED")
    for msg in _failed:
        print("  -", msg)
    sys.exit(1)
shutil.rmtree(TMP, ignore_errors=True)
print(f"\nALL {_passed} DIAGRAM NOTATION CHECKS PASSED")
sys.exit(0)
