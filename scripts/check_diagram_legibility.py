#!/usr/bin/env python3
"""Check that diagram text is still readable once it is printed on paper.

A diagram is not drawn at page size. Every renderer here works in its own
pixel or inch space, and the formatter then scales the whole picture bodily
into a fixed box on the page. Anything written inside the picture shrinks by
that same factor, so a font size chosen while looking at the PNG says nothing
about what a parent gets out of the printer.

Every renderer got this wrong, in the same way. The compare composite wrote
its sub-diagram labels at a fixed 26px onto a canvas whose width depends on
how many parts the sub-figures have: two bar models landed the label at
7.7pt, a nine-part bar beside a three-part bar at 4.5pt, a four-way compare
at 2.6pt. The matplotlib figures wrote theirs at a fixed 10pt or 11pt onto
figures up to 4.9 inches wide that print 2.4 inches wide: the L-shape
measurements landed at 4.9pt, the number line at 5.4pt, the cylinder at
4.8pt, the "not to scale" caption at 4.1pt.

Those numbers are the question. A Year 5 child working out the area of an
L-shaped garden has to read "12 m" off the drawing, and at 4.9pt on a home
printer they cannot.

So this check does not assert that a label exists, or that some constant in
the source has some value. It renders the picture, reads the PNG back off
disk, puts it through the real formatter sizing code, and asserts the point
size the text actually prints at.

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


# The single-figure renderers, with the specs the shipped Year 5 booklet used
# where it had one. These are matplotlib figures, so their text is measured
# differently from the compare composite's Pillow labels.
FIGURE_CASES = [
    ("rectangle 7cm x 3cm (page 7)",
     {"type": "rectangle", "length": 7, "width": 3, "unit": "cm"}),
    ("L-shape 12m x 10m (page 8)",
     {"type": "l_shape", "outer_length": 12, "outer_width": 10,
      "cut_length": 4, "cut_width": 3, "unit": "m"}),
    ("L-shape 10m x 8m (page 29)",
     {"type": "l_shape", "outer_length": 10, "outer_width": 8,
      "cut_length": 4, "cut_width": 3, "unit": "m"}),
    ("number line 300 to 400 (page 12)",
     {"type": "number_line", "from": 300, "to": 400, "divisions": 10,
      "mark_at": [347], "label_at": ["347"]}),
    ("number line 0 to 1 in quarters",
     {"type": "number_line", "from": 0, "to": 1, "divisions": 4,
      "mark_at": [0.75], "label_at": ["3/4"]}),
    ("cuboid 5x3x4",
     {"type": "cuboid", "length": 5, "width": 3, "height": 4, "unit": "cm"}),
    ("a long thin cuboid",
     {"type": "cuboid", "length": 20, "width": 4, "height": 3, "unit": "cm"}),
    ("cylinder r3 h8",
     {"type": "cylinder", "radius": 3, "height": 8, "unit": "cm"}),
    ("rectangle with a hidden side and its caption",
     {"type": "rectangle", "length": 6, "width": 4, "unit": "cm",
      "unknown": ["length"]}),
    ("cuboid with a hidden side and its caption",
     {"type": "cuboid", "length": 4, "width": 3, "height": 2,
      "unit": "blocks", "unknown": ["height"]}),
]


def rendered_figure_pt(spec: dict, max_w: float, max_h: float):
    """What a matplotlib figure's text prints at, in points.

    Returns (smallest measurement label, smallest caption or None, png size).
    Sizes come from the draw calls, not from what the sizing code claims about
    itself. Measurements go through Axes.text and the "not to scale" caption
    through Axes.annotate, which is how the two are told apart, and they carry
    different floors: a measurement is the question, a caption is a footnote.

    The figure is drawn more than once when the first attempt lands too small,
    so savefig marks the pass boundary and only the last pass counts, that
    being the one left on disk.
    """
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

    labels: list[float] = []
    notes: list[float] = []
    passes: list[tuple[list[float], list[float]]] = []
    o_text, o_annotate, o_save = Axes.text, Axes.annotate, Figure.savefig

    def spy_text(self, x, y, s, *args, **kwargs):
        if kwargs.get("fontsize"):
            labels.append(float(kwargs["fontsize"]))
        return o_text(self, x, y, s, *args, **kwargs)

    def spy_annotate(self, text, *args, **kwargs):
        if kwargs.get("fontsize"):
            notes.append(float(kwargs["fontsize"]))
        return o_annotate(self, text, *args, **kwargs)

    def spy_save(self, *args, **kwargs):
        result = o_save(self, *args, **kwargs)
        passes.append((list(labels), list(notes)))
        labels.clear()
        notes.clear()
        return result

    for stale in diagrams.CACHE_DIR.glob("*.png"):
        stale.unlink()
    Axes.text, Axes.annotate, Figure.savefig = spy_text, spy_annotate, spy_save
    try:
        path = diagrams.render_diagram(dict(spec))
    finally:
        Axes.text, Axes.annotate, Figure.savefig = o_text, o_annotate, o_save
    if path is None or not path.exists() or not passes:
        return None, None, None
    drawn_labels, drawn_notes = passes[-1]
    if not drawn_labels:
        return None, None, None
    png_w, png_h = PILImage.open(path).size
    flowable = formatter._make_image(str(path), max_w=max_w, max_h=max_h)
    if flowable is None:
        return None, None, (png_w, png_h)
    # A point in the figure is 1/72 inch, which at the figure's DPI is
    # DPI/72 pixels, which the formatter then places at drawWidth/png_w
    # points per pixel.
    per_pt = (diagrams.DPI / 72) * (flowable.drawWidth / png_w)
    smallest_note = min(drawn_notes) * per_pt if drawn_notes else None
    return min(drawn_labels) * per_pt, smallest_note, (png_w, png_h)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="folio-legibility-"))
    old_cache = diagrams.CACHE_DIR
    diagrams.CACHE_DIR = tmp        # never read a cached PNG from an old run
    try:
        print("== the box the composite is sized for matches the formatter ==")
        assumed_w, assumed_h = diagrams.DIAGRAM_PRINT_BOX_PT
        # The worked-example box is the smaller of the two the formatter uses,
        # so sizing for it covers the question box as well. If the formatter
        # ever shrinks a box below what diagrams.py assumes, every label size
        # computed from that assumption is optimistic, and this fails.
        check(assumed_w <= formatter.WE_IMG_WIDTH + 0.01
              and assumed_h <= formatter.WE_IMG_HEIGHT + 0.01
              and assumed_w <= formatter.MAX_IMG_WIDTH + 0.01
              and assumed_h <= formatter.MAX_IMG_HEIGHT + 0.01,
              "diagrams.DIAGRAM_PRINT_BOX_PT is no bigger than any print box",
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

        for box_name, max_w, max_h in [
            ("worked example box", formatter.WE_IMG_WIDTH, formatter.WE_IMG_HEIGHT),
            ("question box", formatter.MAX_IMG_WIDTH, formatter.MAX_IMG_HEIGHT),
        ]:
            print(f"\n== measurements on single figures in the {box_name} ==")
            for name, spec in FIGURE_CASES:
                pt, note_pt, png = rendered_figure_pt(spec, max_w, max_h)
                if pt is None:
                    check(False, name, f"did not render (png={png})")
                    continue
                extra = f", caption {note_pt:.1f}pt" if note_pt else ""
                check(pt >= MIN_PT,
                      f"{name}: measurements print at {pt:.1f}pt{extra}",
                      f"{pt:.2f}pt is below the {MIN_PT}pt floor "
                      f"(png {png[0]}x{png[1]}px)")
                if note_pt is not None:
                    check(note_pt >= diagrams.MIN_DIAGRAM_NOTE_PT,
                          f"{name}: the caption prints at {note_pt:.1f}pt",
                          f"{note_pt:.2f}pt is below the "
                          f"{diagrams.MIN_DIAGRAM_NOTE_PT}pt caption floor")

        print("\n== every figure still fits the print box ==")
        for name, spec in FIGURE_CASES:
            path = diagrams.render_diagram(dict(spec))
            flow = path and formatter._make_image(
                str(path), max_w=formatter.WE_IMG_WIDTH,
                max_h=formatter.WE_IMG_HEIGHT)
            check(flow is not None
                  and flow.drawWidth <= formatter.WE_IMG_WIDTH + 0.01
                  and flow.drawHeight <= formatter.WE_IMG_HEIGHT + 0.01,
                  f"{name}: fits",
                  f"{flow.drawWidth:.1f}x{flow.drawHeight:.1f}pt" if flow else "no image")

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
