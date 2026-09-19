"""Checks that a column graph's category labels do not run into each other.

A Year 3 booklet shipped a column graph whose x axis read, as one unbroken
string:

    ApplesBananasBerriesMelon

The question above it was "Jia claims that more than half of the 50 students
chose bananas. Is she correct?", so reading the bananas column is the whole
task, and the label naming it was fused to the two either side of it.

Matplotlib centres a tick label on its tick and never checks whether the label
beside it is already occupying that space. Nothing else measured it either, so
four ordinary fruit names were enough to break the figure and no log line
anywhere said so.

WHY THE FIGURE IS NOT JUST DRAWN WIDER, which is the obvious fix and the wrong
one. A figure wider than the print box is scaled down bodily by the formatter,
and the legibility pass then scales the type back up by that same factor to
hold the 9pt floor. The two cancel: the words keep the same share of the
printed width however large the canvas is drawn. Horizontal room is fixed by
the print box and the floor together, so the labels are what has to give.

They give in the order a child can afford. A label with a space in it is
wrapped onto two lines, which costs nothing to read. Only a label that is one
long word is slanted, because slanted text is harder for a seven year old than
upright text and should be the last resort rather than the house style.

    PYTHONPATH=. python scripts/check_bar_chart_labels.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib                                                  # noqa: E402
matplotlib.use("Agg")

from booklet_gen.visuals import data                               # noqa: E402
from booklet_gen.visuals.style import _Fonts                       # noqa: E402

PASSED = 0
TOTAL = 0
SCRATCH = Path(__file__).resolve().parent.parent / "output" / "_bar_check.png"


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


def draw(spec: dict, scale: float = 1.0) -> dict:
    """Render one column graph and report the labels as they were drawn.

    The renderer closes its figure on the way out, so the measurement is taken
    by intercepting the save. Measuring anything else would be measuring a
    figure nobody prints.

    Each label is measured twice: once as drawn, and once with its slant taken
    off. The second is what `clearance` needs, and setting the rotation to zero
    to take it is the plainest way to get a label's own width and height
    without doing trigonometry on a bounding box.
    """
    SCRATCH.parent.mkdir(parents=True, exist_ok=True)
    captured = {}
    original = data._save

    def spy(fig, out):
        renderer = fig.canvas.get_renderer()
        axes = fig.axes[0]
        labels = [t for t in axes.get_xticklabels() if t.get_text()]
        captured["texts"] = [t.get_text() for t in labels]
        captured["rotations"] = [t.get_rotation() for t in labels]
        upright = []
        for t in labels:
            was = t.get_rotation()
            t.set_rotation(0)
            box = t.get_window_extent(renderer=renderer)
            upright.append((box.width, box.height))
            t.set_rotation(was)
        captured["sizes"] = upright
        captured["slot"] = (axes.get_window_extent().width
                            / max(len(labels), 1))
        captured["yticks"] = [t for t in axes.get_yticks()
                              if axes.get_ylim()[0] <= t <= axes.get_ylim()[1]]
        original(fig, out)

    data._save = spy
    try:
        data.bar_chart(spec, SCRATCH, _Fonts(scale))
    finally:
        data._save = original
    return captured


def clearance(drawn: dict) -> float:
    """Pixels of clear space between the closest pair of labels.

    Negative means they touch. Two cases, because a slanted label and an
    upright one are crowded in different directions:

    UPRIGHT labels share one row, centred on their own tick. The room each has
    is the column width, and a label wider than that runs into its neighbour.

    SLANTED labels do not share a row at all. Each is anchored at its own tick
    and runs back under the column to its left, so consecutive labels sit on
    parallel lines. Their axis-aligned bounding boxes overlap heavily and
    always will, which is why comparing those boxes reports a collision on a
    figure whose words are plainly separate. What actually has to clear is the
    PERPENDICULAR gap between those parallel lines, which is the column width
    times the sine of the slant, against the height of one line of type.
    """
    if not drawn["sizes"]:
        return 0.0
    slot = drawn["slot"]
    tilt = max(drawn["rotations"])
    if tilt == 0:
        return slot - max(w for w, _ in drawn["sizes"])
    gap = slot * math.sin(math.radians(tilt))
    return gap - max(h for _, h in drawn["sizes"])


# The spec from the shipped page, read off the printed figure.
SHIPPED = {"type": "bar_chart",
           "categories": ["Apples", "Bananas", "Berries", "Melon"],
           "values": [12, 16, 8, 14], "y_label": "Students"}


print("\n== the shipped Year 3 graph ==")

drawn = draw(SHIPPED)
gap = clearance(drawn)
check(gap > 0.0,
      f"the four category labels are clear of each other "
      f"(closest pair {gap:.0f}px apart)",
      f"the closest pair overlap by {-gap:.0f}px. This is the defect as "
      "shipped: the axis reads ApplesBananasBerriesMelon and the column the "
      "question asks about cannot be identified")

# Tested at the size the legibility pass actually settles on as well as at
# 1.0, because the pass only ever makes the type BIGGER, and a fix that holds
# at the base size and fails once the labels are scaled up fixes nothing that
# gets printed.
for scale in (1.2, 1.5, 2.0):
    gap = clearance(draw(SHIPPED, scale))
    check(gap > 0.0,
          f"still clear with the type scaled {scale}x ({gap:.0f}px)",
          f"overlap by {-gap:.0f}px at {scale}x. The legibility pass raises "
          "the font until the smallest text clears 9pt in print, so this is "
          "the size that reaches the page, not the base size")


print("\n== a label is only slanted when it has to be ==")

# Slanted text is harder for a seven year old than upright text. It is the
# last resort, not the house style, so these must come out level.
UPRIGHT = [
    ({"type": "bar_chart", "categories": ["Red", "Blue", "Green"],
      "values": [4, 7, 2], "y_label": "Votes"}, "three short colours"),
    ({"type": "bar_chart", "categories": ["Mon", "Tue", "Wed"],
      "values": [3, 5, 4], "y_label": "Books"}, "three weekdays"),
    ({"type": "bar_chart", "categories": ["Cats", "Dogs"],
      "values": [6, 9], "y_label": "Pets"}, "two short words"),
]
for spec, label in UPRIGHT:
    drawn = draw(spec)
    check(all(r == 0 for r in drawn["rotations"]),
          f"{label} stay level",
          f"rotated to {drawn['rotations']}. These fit upright, and slanting "
          "them makes every column graph in the product harder to read to fix "
          "a problem this figure does not have")


print("\n== a multi-word label wraps before anything is slanted ==")

WRAPS = [
    ({"type": "bar_chart", "categories": ["Ice cream", "Hot chips", "Meat pie"],
      "values": [9, 5, 11], "y_label": "Children"}, "canteen choices", True),
    # Three words apiece. Breaking at a space is not enough to get "Walked to
    # school" inside a third of the axis, so this one is expected to need the
    # slant as well, and is here to prove the fallback still fires once the
    # cheaper remedy has been spent rather than being skipped because
    # something was already tried.
    ({"type": "bar_chart",
      "categories": ["Walked to school", "Came by car", "Rode a bike"],
      "values": [11, 14, 7], "y_label": "Students"}, "how they travelled",
     False),
]
for spec, label, fits_upright in WRAPS:
    drawn = draw(spec)
    if fits_upright:
        check(all("\n" in t for t in drawn["texts"]),
              f"{label} break at the space",
              f"got {drawn['texts']}. A label with a space in it has a free "
              "way to get narrower, and taking it means the words stay "
              "upright")
        check(all(r == 0 for r in drawn["rotations"]),
              "and the wrap alone was enough, so nothing is slanted",
              f"rotated to {drawn['rotations']}: these fit upright once "
              "wrapped, and slanting them as well spends a cost the figure "
              "does not owe")
    else:
        check(all(r > 0 for r in drawn["rotations"]),
              f"{label} need the slant as well",
              "these do not fit upright even wrapped, so the fallback has to "
              "fire; if it does not, the wrap is being treated as a remedy "
              "that always works")
        check(all("\n" not in t for t in drawn["texts"]),
              "and the wrap is taken back off before they are slanted",
              f"got {drawn['texts']}. A wrapped label is narrower and twice "
              "as TALL, and height is the measurement a slanted row is short "
              "of, so keeping both leaves each label's second line in the "
              "one beside it")
    check(clearance(drawn) > 0.0,
          f"and the labels end up {clearance(drawn):.0f}px clear",
          "the labels still touch after wrapping and slanting, so neither "
          "remedy is reaching a figure that needs both")


print("\n== the value axis stays readable ==")

# Making room under the axis for slanted labels takes it from the plot, and
# matplotlib answers a shorter plot by thinning out the ticks. A graph marked
# 0 and 10 and nothing between is one a child cannot read a 14 off, which is
# the only thing these questions ever ask.
for spec, label in ((SHIPPED, "the shipped graph"),
                    ({"type": "bar_chart",
                      "categories": ["Jan", "Feb", "Mar", "Apr", "May",
                                     "Jun", "Jul", "Aug"],
                      "values": [3, 5, 2, 8, 6, 4, 7, 5],
                      "y_label": "Rainy days"}, "eight months")):
    ticks = draw(spec)["yticks"]
    check(len(ticks) >= 4,
          f"{label}: {len(ticks)} marks on the value axis ({ticks})",
          f"only {ticks}. The question is 'how many chose X', so the answer is "
          "read off this axis, and a bar landing between two distant marks "
          "cannot be read at all")
    check(all(float(t).is_integer() for t in ticks),
          "and every one of them is a whole number",
          f"got {ticks}. These are counts of children, and half a child on "
          "the axis of a Year 3 graph is a mistake the child has to ignore")


SCRATCH.unlink(missing_ok=True)
print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
