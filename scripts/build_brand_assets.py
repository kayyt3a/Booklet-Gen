"""Turn the supplied logo artwork into the web assets the site actually serves.

The source files under booklet_gen/webapp/static/img/brand/source/ are around a
megabyte each and violet. The site is navy, because --navy is the colour the
printed booklet sets its headings and Class Work band in, and a mark in another
colour stops matching the thing being sold. Serving a 1MB PNG as a favicon
would also make the logo the slowest asset on the page.

So this script does two jobs, and it is a script rather than a one-off edit
because the source art will be regenerated and the derivatives have to be
rebuildable without anyone remembering the numbers:

  1. Swing the violet onto navy. Only hues inside the violet band move. The
     near-navy backgrounds already sit around 221 and must stay put; the white
     page and the near-black letter carry almost no saturation, so they are
     untouched by construction rather than by masking.

  2. Emit each derivative at the size it is used at, not at source size.

Run:  PYTHONPATH=. python scripts/build_brand_assets.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image
import matplotlib.colors as mc

BRAND = Path("booklet_gen/webapp/static/img/brand")
SRC = BRAND / "source"

# The violet band, and where it lands. The span is compressed rather than
# flattened to one value so the artwork's own light-to-dark structure survives.
VIOLET_LO, VIOLET_HI = 232.0, 285.0
NAVY_LO, NAVY_HI = 206.0, 219.0


def navyfy(im: Image.Image, darken: float = 1.0) -> Image.Image:
    """Recolour violet to navy. `darken` < 1 dims only the recoloured pixels,
    which is what the light-background lockup needs: a pale blue that reads
    fine on dark is nearly invisible on cream."""
    rgba = im.convert("RGBA")
    a = np.asarray(rgba).astype(np.float32) / 255.0
    rgb, alpha = a[..., :3], a[..., 3:]
    hsv = mc.rgb_to_hsv(rgb)
    h = hsv[..., 0] * 360.0
    sel = (h >= VIOLET_LO) & (h <= VIOLET_HI)
    span = (NAVY_HI - NAVY_LO) / (VIOLET_HI - VIOLET_LO)
    hsv[..., 0] = np.where(sel, (NAVY_LO + (h - VIOLET_LO) * span) / 360.0, hsv[..., 0])
    hsv[..., 1] = np.where(sel, hsv[..., 1] * 0.62, hsv[..., 1])
    if darken != 1.0:
        hsv[..., 2] = np.where(sel, hsv[..., 2] * darken, hsv[..., 2])
    out = np.concatenate([mc.hsv_to_rgb(hsv), alpha], axis=-1)
    return Image.fromarray((out * 255).clip(0, 255).astype(np.uint8), "RGBA")


def trim_alpha(im: Image.Image, pad: int = 8) -> Image.Image:
    """Crop to the visible artwork. The mark arrives centred in a 1536x1024
    frame, so most of the file is empty and every later resize would be
    spending its pixels on nothing."""
    box = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box is None:
        return im
    l, t, r, b = box
    w, h = im.size
    return im.crop((max(0, l - pad), max(0, t - pad), min(w, r + pad), min(h, b + pad)))


def save(im: Image.Image, name: str, size, *, rgb_bg=None, colors: int = 0) -> None:
    """Write one derivative. `colors` palettises the result: these are flat
    illustrations with a gradient, so 128 entries is indistinguishable from
    truecolour at icon sizes and roughly a quarter of the bytes. An icon that
    costs 230KB is the slowest thing on the page it decorates."""
    out = im.copy()
    if isinstance(size, tuple):
        out.thumbnail(size, Image.LANCZOS)
    else:
        out = out.resize((size, size), Image.LANCZOS)
    if rgb_bg is not None:
        flat = Image.new("RGB", out.size, rgb_bg)
        flat.paste(out, mask=out.convert("RGBA").getchannel("A"))
        out = flat
    if colors:
        # Alpha survives quantisation only in RGBA->P with an explicit method.
        out = out.quantize(colors=colors, method=Image.Quantize.MEDIANCUT
                           if out.mode != "RGBA" else Image.Quantize.FASTOCTREE)
    path = BRAND / name
    out.save(path, optimize=True)
    print(f"  {name:28} {out.size[0]}x{out.size[1]:<5} {path.stat().st_size / 1024:6.1f} KB")


def main() -> int:
    if not SRC.is_dir():
        print(f"No source artwork at {SRC}", file=sys.stderr)
        return 1

    icon = SRC / "icon-square.png"
    mark = SRC / "mark.png"
    banner = SRC / "banner-dark.png"
    for f in (icon, mark, banner):
        if not f.exists():
            print(f"missing {f}", file=sys.stderr)
            return 1

    print("Building navy web assets from the supplied artwork")

    # App icon. Ships with its own tile, which is what a home-screen icon wants:
    # iOS square-crops and will not honour transparency.
    ic = navyfy(Image.open(icon))
    save(ic, "apple-touch-icon.png", 180, rgb_bg=(15, 27, 61), colors=128)
    save(ic, "icon-512.png", 512, rgb_bg=(15, 27, 61), colors=128)

    # Tab icons. PNG rather than a hand-drawn SVG: an SVG would be a second,
    # approximate copy of this artwork that has to be kept in sync by eye, and
    # the two drifted apart the moment the real files arrived.
    save(ic, "favicon-32.png", 32, rgb_bg=(15, 27, 61), colors=64)
    save(ic, "favicon-16.png", 16, rgb_bg=(15, 27, 61), colors=64)

    # The mark on transparency, for any surface. Served at 2x the size it is
    # displayed at so it stays sharp on a retina screen.
    mk = trim_alpha(navyfy(Image.open(mark)))
    save(mk, "mark-512.png", (512, 512), colors=128)
    save(mk, "mark-96.png", (96, 96), colors=128)

    # Social preview. 1200x630 is what the major crawlers read.
    bn = navyfy(Image.open(banner)).convert("RGB")
    w, h = bn.size
    target = 1200 / 630
    if w / h > target:
        new_w = int(h * target)
        bn = bn.crop(((w - new_w) // 2, 0, (w + new_w) // 2, h))
    else:
        new_h = int(w / target)
        bn = bn.crop((0, (h - new_h) // 2, w, (h + new_h) // 2))
    bn = bn.resize((1200, 630), Image.LANCZOS)
    bn.save(BRAND / "og-image.jpg", quality=86, optimize=True, progressive=True)
    print(f"  {'og-image.jpg':28} 1200x630  "
          f"{(BRAND / 'og-image.jpg').stat().st_size / 1024:6.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
