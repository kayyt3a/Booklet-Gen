"""Turn the supplied Paulio artwork into the sizes the site actually displays.

The source files under booklet_gen/webapp/static/img/paulio/source/ are
around 1300px on their long edge and 1.5-2.2MB each. MASCOT_GUIDE.md sets the
site's own display sizes: standard poses render 160-280 CSS px wide, desk
scenes 260-420 CSS px wide. Shipping the source file for either would make
Paulio the heaviest thing on every page he appears on, by a wide margin over
everything else the site loads.

So each pose is emitted at 2x its largest intended CSS width, which is sharp
on a retina screen and still a fraction of the source size. This is a script
rather than a one-off resize, on the same reasoning as build_brand_assets.py:
the source art can change and the derivatives have to be rebuildable without
anyone re-deriving the numbers.

No recolouring here, unlike the brand mark: the artwork already ships in the
guide's navy/cream/orange palette, so there is nothing to correct.

Each resized image is then palettised to 256 colours. The line art's pencil-
texture shading resists plain lossless compression (a truecolour PNG at the
right pixel size still ran 400-900KB), but the actual palette is small and
mostly flat, so 256 entries is visually indistinguishable from truecolour
here and cuts most files to under 100KB.

Run:  PYTHONPATH=. python scripts/build_paulio_assets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

PAULIO = Path("booklet_gen/webapp/static/img/paulio")
SRC = PAULIO / "source"

# 2x the largest CSS width MASCOT_GUIDE.md gives each category, so the image
# stays sharp on a retina screen without shipping source resolution nobody
# can see the benefit of.
STANDARD_PX = 560   # standard poses: 160-280 CSS px
DESK_PX = 840        # desk scenes: 260-420 CSS px

# Every production file MASCOT_GUIDE.md's asset table names, and which size
# band each belongs to. Anything not listed here is left alone rather than
# silently skipped, so a new pose added to source/ without a decision about
# its size fails loudly instead of shipping unoptimised or not shipping.
SIZES = {
    "paulio-welcome.png": STANDARD_PX,
    "paulio-welcome-left.png": STANDARD_PX,
    "paulio-guide-right.png": STANDARD_PX,
    "paulio-library.png": STANDARD_PX,
    "paulio-library-left.png": STANDARD_PX,
    "paulio-billing.png": STANDARD_PX,
    "paulio-success.png": STANDARD_PX,
    "paulio-hanging.png": STANDARD_PX,
    "paulio-signature.png": STANDARD_PX,
    "paulio-working.png": DESK_PX,
    "paulio-working-left.png": DESK_PX,
    "paulio-working-right.png": DESK_PX,
}


def trim_alpha(im: Image.Image, pad: int = 6) -> Image.Image:
    """Crop to the visible artwork, so the resize spends its pixels on
    Paulio rather than on the transparent margin around him."""
    box = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box is None:
        return im
    l, t, r, b = box
    w, h = im.size
    return im.crop((max(0, l - pad), max(0, t - pad), min(w, r + pad), min(h, b + pad)))


def build_one(name: str, target_w: int) -> None:
    src_path = SRC / name
    im = Image.open(src_path).convert("RGBA")
    im = trim_alpha(im)
    if im.width > target_w:
        target_h = round(im.height * target_w / im.width)
        im = im.resize((target_w, target_h), Image.LANCZOS)
    # FASTOCTREE, not the default: it is the quantizer that keeps alpha
    # rather than flattening it onto a background colour.
    im = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    out_path = PAULIO / name
    im.save(out_path, optimize=True)
    src_kb = src_path.stat().st_size / 1024
    out_kb = out_path.stat().st_size / 1024
    print(f"  {name:28} {im.size[0]}x{im.size[1]:<5} "
          f"{src_kb:7.0f} KB -> {out_kb:6.1f} KB")


def main() -> int:
    if not SRC.is_dir():
        print(f"No source artwork at {SRC}", file=sys.stderr)
        return 1

    missing = [n for n in SIZES if not (SRC / n).exists()]
    if missing:
        print("Missing from source/:", ", ".join(missing), file=sys.stderr)
        return 1

    extra = [f.name for f in SRC.glob("*.png") if f.name not in SIZES]
    if extra:
        print("In source/ but not in SIZES (add a decision, don't skip it "
              "silently):", ", ".join(extra), file=sys.stderr)
        return 1

    print("Building web-sized Paulio assets from the supplied artwork")
    for name, target_w in SIZES.items():
        build_one(name, target_w)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
