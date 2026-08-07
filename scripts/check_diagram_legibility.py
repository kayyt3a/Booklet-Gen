#!/usr/bin/env python3
"""Check that diagram text is still readable once it is printed on paper.

A diagram is not drawn at page size. Every renderer here works in its own
pixel or inch space, and the formatter then scales the whole picture bodily
into a fixed box on the page. Anything written inside the picture shrinks by
that same factor, so a font size chosen while looking at the PNG says nothing
about what a parent gets out of the printer.

The compare composite got this wrong. It wrote its sub-diagram labels at a
fixed 26px onto a canvas whose width depends on how many parts the sub-figures
have, and the formatter then squeezed that canvas into a 6cm box. Two bar
models landed the label at 7.7pt; a nine-part bar beside a three-part bar
landed it at 4.5pt; a four-way compare reached 2.6pt. The shipped Year 5
fractions booklet has "1/2" and "2/4" printed under two bar models at 7.7pt,
which is smaller than the page numbers.

So this check does not assert that a label exists, or that some constant in
the source has some value. It renders the picture, reads the PNG back off
disk, puts it through the real formatter sizing code, and asserts the point
size the label actually prints at.

Usage:  PYTHONPATH=. python scripts/check_diagram_legibility.py
"""
from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.disable(logging.CRITICAL)

from PIL import Image as PILImage, ImageFont                     # noqa: E402

from booklet_gen import formatter                                # noqa: E402
from booklet_gen.visuals import diagrams                         # noqa: E402

PASSED = 0
TOTAL = 0

# The floor a child has to read at. Booklet body text is around 10pt.
MIN_PT = 9.0


def check(good: bool, label: str, detail: str = "") -> None:
    global PASSED, TOTAL
    TOTAL += 1
    PASSED += bool(good)
    print(f"{'ok  ' if good else '*** FAIL ***':<14}{label}")
    if not good and detail:
        print(f"{'':<14}{detail[:300]}")


def bar(parts: int, shaded: int) -> dict:
    return {"type": "bar_model", "parts": parts, "shaded": shaded}


def pie(slices: int, shaded: int) -> dict:
    return {"type": "circle_slices", "slices": slices, "shaded": shaded}


def compare(*items) -> dict:
    return {"type": "compare",
            "items": [{"label": lab, "spec": sp} for lab, sp in items]}


# Each case is (name, spec). The first is the composite printed on page 5 of
# the shipped Year 5 booklet; the rest widen the canvas, which is the axis
# that made the labels shrink.
CASES = [
    ("two bar models (the shipped page 5 figure)",
     compare(("1/2", bar(2, 1)), ("2/4", bar(4, 2)))),
    ("a nine-part bar beside a three-part bar",
     compare(("3/9", bar(9, 3)), ("1/3", bar(3, 1)))),
    ("two circles",
     compare(("1/2", pie(2, 1)), ("3/6", pie(6, 3)))),
    ("four bar models, the widest composite allowed",
     compare(("1/2", bar(2, 1)), ("2/4", bar(4, 2)),
             ("3/6", bar(6, 3)), ("4/8", bar(8, 4)))),
    ("mixed figures of different heights",
     compare(("A", pie(4, 1)), ("B", bar(8, 3)))),
    ("labels longer than the figure under them",
     compare(("three quarters", bar(4, 3)), ("one half", bar(2, 1)))),
]


def rendered_label_pt(spec: dict, max_w: float, max_h: float):
    """(point size the label prints at, png size, font px) for one spec.

    The font size is taken from the draw call itself, by watching
    ImageFont.truetype, rather than from anything the layout code reports
    about itself. The scale is taken from the formatter: _make_image is the
    function that actually decides how big the picture lands on the page, so
    if that sizing ever changes this check moves with it.
    """
    sizes: list[int] = []
    original = ImageFont.truetype

    def spy(font, size=10, *args, **kwargs):
        sizes.append(size)
        return original(font, size, *args, **kwargs)

    # render_diagram returns the cached PNG without drawing anything, so the
    # cache has to go before every measurement or the second box measures
    # nothing at all.
    for stale in diagrams.CACHE_DIR.glob("*.png"):
        stale.unlink()
    ImageFont.truetype = spy
    try:
        path = diagrams.render_diagram(dict(spec))
    finally:
        ImageFont.truetype = original
    if path is None or not path.exists():
        return None, None, None
    png_w, png_h = PILImage.open(path).size
    flowable = formatter._make_image(str(path), max_w=max_w, max_h=max_h)
    if flowable is None:
        return None, (png_w, png_h), None
    # Points per pixel, straight off the flowable the page will draw.
    scale = flowable.drawWidth / png_w
    font_px = sizes[-1] if sizes else None
    if font_px is None:
        return None, (png_w, png_h), None
    return font_px * scale, (png_w, png_h), font_px


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="folio-legibility-"))
    old_cache = diagrams.CACHE_DIR
    diagrams.CACHE_DIR = tmp        # never read a cached PNG from an old run
    try:
        print("== the box the composite is sized for matches the formatter ==")
        assumed_w, assumed_h = diagrams.COMPARE_PRINT_BOX_PT
        # The worked-example box is the smaller of the two the formatter uses,
        # so sizing for it covers the question box as well. If the formatter
        # ever shrinks a box below what diagrams.py assumes, every label size
        # computed from that assumption is optimistic, and this fails.
        check(assumed_w <= formatter.WE_IMG_WIDTH + 0.01
              and assumed_h <= formatter.WE_IMG_HEIGHT + 0.01
              and assumed_w <= formatter.MAX_IMG_WIDTH + 0.01
              and assumed_h <= formatter.MAX_IMG_HEIGHT + 0.01,
              "diagrams.COMPARE_PRINT_BOX_PT is no bigger than any print box",
              f"assumed {assumed_w:.1f}x{assumed_h:.1f}pt, formatter smallest "
              f"{formatter.WE_IMG_WIDTH:.1f}x{formatter.WE_IMG_HEIGHT:.1f}pt")
        check(diagrams.MIN_COMPARE_LABEL_PT >= MIN_PT,
              f"the label floor in diagrams.py is at least {MIN_PT}pt",
              f"got {diagrams.MIN_COMPARE_LABEL_PT}")

        for box_name, max_w, max_h in [
            ("worked example box", formatter.WE_IMG_WIDTH, formatter.WE_IMG_HEIGHT),
            ("question box", formatter.MAX_IMG_WIDTH, formatter.MAX_IMG_HEIGHT),
        ]:
            print(f"\n== compare labels in the {box_name} "
                  f"({max_w / 28.35:.1f}cm x {max_h / 28.35:.1f}cm) ==")
            for name, spec in CASES:
                pt, png, font_px = rendered_label_pt(spec, max_w, max_h)
                if pt is None:
                    check(False, name, f"did not render (png={png})")
                    continue
                check(pt >= MIN_PT,
                      f"{name}: prints at {pt:.1f}pt",
                      f"{pt:.2f}pt is below the {MIN_PT}pt floor "
                      f"(png {png[0]}x{png[1]}px, font {font_px}px)")

        print("\n== the composite still fits and still says what it should ==")
        for name, spec in CASES:
            path = diagrams.render_diagram(dict(spec))
            if path is None:
                check(False, f"{name}: renders")
                continue
            flow = formatter._make_image(str(path),
                                         max_w=formatter.WE_IMG_WIDTH,
                                         max_h=formatter.WE_IMG_HEIGHT)
            check(flow is not None
                  and flow.drawWidth <= formatter.WE_IMG_WIDTH + 0.01
                  and flow.drawHeight <= formatter.WE_IMG_HEIGHT + 0.01,
                  f"{name}: still fits the print box",
                  f"{flow.drawWidth:.1f}x{flow.drawHeight:.1f}pt" if flow else "no image")

        # A label the layout never drew would pass a size assertion trivially.
        print("\n== the labels are actually on the canvas ==")
        path = diagrams.render_diagram(
            compare(("1/2", bar(2, 1)), ("2/4", bar(4, 2))))
        img = PILImage.open(path).convert("RGB")
        w, h = img.size
        # The bottom band is label only: no sub-figure is pasted there.
        band = img.crop((0, h - 40, w, h)).convert("L")
        ink = sum(band.histogram()[:128])
        check(ink > 50, "ink is present in the label band under the figures",
              f"{ink} dark pixels in the bottom 40 rows")
    finally:
        diagrams.CACHE_DIR = old_cache

    print(f"\n{PASSED}/{TOTAL} behaved as expected")
    return 0 if PASSED == TOTAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
