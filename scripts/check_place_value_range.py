"""Checks a four-digit number can be shown by place, not refused.

A shipped Year 4 booklet asked "What is the value of the digit 4 in 2456?"
and "Compare the value of 7 in 7201 and 1702", and neither carried a figure.
Place value is the one idea in primary maths that is genuinely spatial, and
the two questions about it in that booklet were bare lines of text.

The renderer refused them:

    if not (0 <= value <= 999):
        raise ValueError(f"place value blocks cover 0-999, got {value}")

The limit was real and the conclusion drawn from it was wrong. Base-ten blocks
genuinely stop working above 999: 2456 is twenty-four hundred-squares, about
2,400 drawn cells inside a 6cm box, which is a grey rectangle. But a classroom
does not stop teaching place value at four digits, it changes the picture. At
four digits it uses a place-value chart, columns headed and one digit in each,
which is also exactly what the question is asking about.

So there are two forms now and the number chooses between them. Nothing about
the 0-999 blocks changed.

WHAT WENT WRONG ON THE FIRST ATTEMPT, and the reason half this file measures
type rather than numbers: the chart was written with its columns headed in
words, and it printed "ThousandsHundreds Tens Ones" with the first two on top
of one another. At seven digits it was an illegible smear. The same defect the
column graph had, in a figure written the week it was fixed there: text drawn
at a size the legibility pass inflates, into a slot nothing measured. The
headings are measured against their own column now and swapped for the
abbreviations a classroom chart uses when the words will not fit.

    PYTHONPATH=. python scripts/check_place_value_range.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib                                                  # noqa: E402
matplotlib.use("Agg")

from booklet_gen.visuals import diagrams                           # noqa: E402
from booklet_gen.visuals.style import _Fonts                       # noqa: E402

PASSED = 0
TOTAL = 0
SCRATCH = Path(__file__).resolve().parent.parent / "output" / "_pv_check.png"


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


def rendered(value: int):
    """The PNG for this value, or None if the renderer refused it."""
    shutil.rmtree(diagrams.CACHE_DIR, ignore_errors=True)
    return diagrams.render_diagram({"type": "place_value", "value": value})


def drawn(value: int, scale: float = 1.0) -> dict:
    """The chart's headings as they were actually drawn."""
    import matplotlib.pyplot as plt
    SCRATCH.parent.mkdir(parents=True, exist_ok=True)
    captured = {}
    original = diagrams.save_figure

    def spy(fig, out, pad=0.08):
        axes = fig.axes[0]
        renderer = fig.canvas.get_renderer()
        texts = [t for t in axes.texts]
        # The headings sit above the boxes; the digits sit inside them.
        captured["headings"] = [t.get_text() for t in texts
                                if t.get_position()[1] > 1.0]
        captured["digits"] = [t.get_text() for t in texts
                              if t.get_position()[1] < 1.0]
        boxes = [t.get_window_extent(renderer=renderer) for t in texts
                 if t.get_position()[1] > 1.0]
        captured["widest"] = max(b.width for b in boxes) if boxes else 0.0
        captured["column"] = (axes.get_window_extent().width
                              / (len(captured["headings"]) + 0.2))
        original(fig, out, pad)

    diagrams.save_figure = spy
    # The chart is reached through the dispatcher's own renderer table, so a
    # rename there fails here rather than quietly measuring nothing.
    try:
        diagrams._RENDERERS["place_value"](
            {"type": "place_value", "value": value}, SCRATCH, _Fonts(scale))
    finally:
        diagrams.save_figure = original
    return captured


print("\n== the questions that shipped without a figure now get one ==")

# Both read off the shipped Year 4 booklet.
for value, label in ((2456, "the value of the digit 4 in 2456"),
                     (7201, "the value of 7 in 7201"),
                     (1702, "and in 1702")):
    check(rendered(value) is not None,
          f"{value}: {label}",
          f"the renderer still refuses {value}, so the question prints as a "
          "bare line of text and the one spatial idea in primary maths is "
          "taught without a picture")


print("\n== and the blocks below 999 are untouched ==")

for value in (0, 7, 40, 256, 999):
    check(rendered(value) is not None, f"{value} still draws",
          f"{value} was drawable before this change and is not now, which "
          "means the two forms are not routing on the number")

# The shape, not just that something rendered: below 1000 there must be no
# chart, because base-ten blocks are the right picture there and the chart
# would be a regression dressed as a fix.
blocks = drawn(256)
check(not blocks.get("headings"),
      "256 draws blocks, with no chart headings on it",
      f"got headings {blocks.get('headings')}. A three-digit number is a "
      "blocks question and always was")


print("\n== the chart heads every column, and legibly ==")

for value, expect in ((2456, 4), (45678, 5), (1234567, 7)):
    chart = drawn(value)
    check(len(chart["headings"]) == expect
          and len(chart["digits"]) == expect,
          f"{value:,}: {expect} columns, {expect} headings, {expect} digits",
          f"got {len(chart['headings'])} headings and "
          f"{len(chart['digits'])} digits. A chart with a digit in an unheaded "
          "column is a row of numbers, which is the question restated rather "
          "than a picture of it")
    check("".join(chart["digits"]) == str(value),
          f"        and they read {''.join(chart['digits'])}",
          f"the digits read {''.join(chart['digits'])!r} against a value of "
          f"{value}. A chart showing a different number than the question asks "
          "about is worse than no chart")


print("\n== no heading is printed on top of the one beside it ==")

# The defect the first attempt shipped, measured at the type sizes that
# actually print. The legibility pass only ever makes text BIGGER, so a fix
# that holds at the base size and fails once the type is scaled up fixes
# nothing, and the range it reaches has to be established rather than assumed.
#
# It was assumed first, at 2x, and that is a size this figure never reaches:
# seven columns of "HTh" overrun at 2x and the pass settles at 1.27x, so the
# assertion was reporting a state nothing can produce. The cure for a guess is
# not a smaller guess, so the settled scale is measured below and asserted to
# stay inside the range the headings are then checked across. If some later
# change makes this figure demand more, the first of those two fails and says
# so, instead of the headings quietly colliding on a page.
SCALE_CEILING = 1.5


def settles_at(value: int) -> float:
    """The scale the real legibility pass stops at for this figure."""
    from PIL import Image
    scale = 1.0
    for _ in range(6):
        fonts = _Fonts(scale)
        diagrams._RENDERERS["place_value"](
            {"type": "place_value", "value": value}, SCRATCH, fonts)
        with Image.open(SCRATCH) as img:
            png_w, png_h = img.size
        shortfall = fonts.shortfall(png_w, png_h)
        if shortfall <= 1.001:
            return scale
        nxt = min(scale * shortfall, 4.0)
        if nxt <= scale * 1.001:
            return scale
        scale = nxt
    return scale


for value in (2456, 45678, 456789, 1234567):
    settled = settles_at(value)
    check(settled <= SCALE_CEILING,
          f"{value:,}: the legibility pass settles at {settled:.2f}x",
          f"it settles at {settled:.2f}x, past the {SCALE_CEILING}x the "
          "headings are checked across below, so this figure is now printing "
          "at a size nothing here has measured")
    for scale in (1.0, settled, SCALE_CEILING):
        chart = drawn(value, scale)
        clear = chart["column"] - chart["widest"]
        check(clear > 0,
              f"        {scale:.2f}x: {clear:.0f}px of clear space per column",
              f"the widest heading overruns its column by {-clear:.0f}px at "
              f"{scale:.2f}x. This printed 'ThousandsHundreds Tens Ones' with "
              "two headings on top of each other")


print("\n== past seven digits it says so rather than drawing nonsense ==")

# A ceiling is still a ceiling. What must not happen is the silent refusal
# that started all this, so this asserts the refusal is reasoned: eight digits
# needs a column the figure has no room to head, and that is a different thing
# from four digits being unsupported.
check(rendered(12_345_678) is None,
      "eight digits is refused rather than drawn unlabelled",
      "an eighth column has no heading in the table, so it would print a "
      "digit nobody can name the place of")
check(rendered(-5) is None,
      "and a negative number is refused",
      "place value blocks and charts both show a count of units, and there is "
      "no picture of minus five to draw")


SCRATCH.unlink(missing_ok=True)
shutil.rmtree(diagrams.CACHE_DIR, ignore_errors=True)
print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
