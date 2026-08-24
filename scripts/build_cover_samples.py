"""Render the booklet covers the website shows, and the share card built on one.

The cover is the best thing the product makes and, until this script existed,
the only way to see one was to pay for a booklet: static/img/ shipped eleven
mascot poses and no covers at all. A visitor deciding whether FolioAI is worth
an account could see a mascot, a page of worked division, and nothing of the
object they would actually be handed.

So the site now shows real covers. Real, in the strict sense: each one below is
a configuration a customer can genuinely buy today (see programs.py), rendered
by the same booklet_gen.visuals.cover code that draws the cover of the PDF they
would receive. Nothing here is a mockup, and none of it is drawn by hand, so a
change to the cover design lands on the marketing pages by re-running this
rather than by someone remembering to redraw a screenshot.

Two things are deliberately left off these covers that a delivered booklet
carries: the generation date and the estimated time. Both are true of one
booklet on one day, and a marketing asset that says "20 August 2026" is stale
by September. The estimated time is worse than stale: it is a claim about
length, and a claim about length on the landing page has to agree with the
landing page, which is a coupling nobody would remember to maintain.

Sizes are 2x the CSS width each image is displayed at, matching the reasoning
in build_paulio_assets.py, and the output is palettised because the artwork is
flat vector fills: a truecolour A4 render is around 400KB, the palettised one
around a tenth of that, with nothing visible lost.

Run:  PYTHONPATH=. python scripts/build_cover_samples.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.visuals.cover import (  # noqa: E402
    ACCENT, NAVY, BLUE_PALE, CoverSpec, render_cover, variant_for)
from booklet_gen.webapp.covers import SAMPLES  # noqa: E402

STATIC = Path("booklet_gen/webapp/static/img")
COVERS = STATIC / "covers"
BRAND = STATIC / "brand"

# 2x the largest CSS width each is displayed at. HERO_PX serves the landing
# page's sample band (about 300 CSS px in the fan, 380 for the front cover);
# THUMB_PX serves a library row, where the cover stands 78 CSS px tall.
HERO_PX = 760
THUMB_PX = 130

FOOTER = ("Work through it in order. In the key at the back, a tick marks an "
          "answer that has been checked.")


def spec_for(s) -> CoverSpec:
    """The CoverSpec for one entry in booklet_gen/webapp/covers.py.

    The covers the site shows are defined next to the code that displays them,
    not here, so a template can never point at an image this script does not
    build.
    """
    return CoverSpec(
        title_lines=[s.year, s.subject],
        pill=s.pill,
        eyebrow=s.program.upper(),
        subject=s.subject,
        topic=s.topic,
        student_name=s.name,
        week=s.week,
        meta_lines=[],
        footer_note=FOOTER,
        variant=variant_for(s.subject, s.program, s.year, s.topic),
    )


def render_png(spec: CoverSpec, width_px: int, tmp: Path) -> Image.Image:
    """One cover, drawn as a PDF page and rasterised at the width asked for."""
    import pymupdf

    pdf = tmp / "cover.pdf"
    c = canvas.Canvas(str(pdf), pagesize=A4)
    render_cover(c, spec)
    c.showPage()
    c.save()
    doc = pymupdf.open(str(pdf))
    page = doc[0]
    zoom = width_px / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return im


def save(im: Image.Image, path: Path, colors: int = 128) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).save(
        path, optimize=True)
    print(f"  {path.name:34} {im.size[0]}x{im.size[1]:<5} "
          f"{path.stat().st_size / 1024:6.1f} KB")


def build_share_card(front: Image.Image) -> None:
    """The 1200x630 card a link to FolioAI unfurls into.

    It used to be the logo on navy: correct dimensions, right colours, and no
    statement of what the product is. That image is the first thing about nine
    thousand people in a parents' group see, and a wordmark alone asks them to
    click to find out what it even sells. This one says what it is and shows
    one, which is the same argument as putting covers on the landing page.

    Drawn with ReportLab rather than PIL on purpose: Helvetica is built into
    the PDF spec, so this renders identically on the founder's Windows machine
    and on a Linux build box, whereas a PIL version would depend on whichever
    TrueType files happen to be installed.
    """
    import pymupdf

    W, H = 1200, 630
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shot = tmp / "front.png"
        front.save(shot)

        pdf = tmp / "og.pdf"
        c = canvas.Canvas(str(pdf), pagesize=(W, H))
        c.setFillColor(NAVY)
        c.rect(0, 0, W, H, stroke=0, fill=1)

        # A single pale sweep, the same "turning page" idea the covers use,
        # so the card belongs to the same family rather than being a banner.
        c.setFillColor(HexColor("#0B2266"))
        p = c.beginPath()
        p.moveTo(W, 0)
        p.lineTo(W, H)
        p.curveTo(W * 0.62, H * 0.86, W * 0.58, H * 0.22, W * 0.42, 0)
        p.close()
        c.drawPath(p, stroke=0, fill=1)

        # The publisher lockup, which is the cover's lockup at 30/24 scale
        # rather than a second arrangement of the same words. cover.py's
        # render_cover sets FOLIO at 24pt bold, the accent "AI" one word space
        # after it, and "practice booklets" in the same bold face at 11.5pt on
        # a baseline 19pt below. Those three ratios are what is reproduced
        # here; the tagline used to be regular weight at a size of its own.
        mark = BRAND / "mark-512.png"
        if mark.is_file():
            c.drawImage(str(mark), 72, H - 150, width=64, height=64,
                        mask="auto", preserveAspectRatio=True)
        wm = 30.0
        scale = wm / 24.0
        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", wm)
        c.drawString(150, H - 116, "FOLIO")
        c.setFillColor(ACCENT)
        c.drawString(150 + c.stringWidth("FOLIO", "Helvetica-Bold", wm) + 7 * scale,
                     H - 116, "AI")
        c.setFillColor(BLUE_PALE)
        c.setFont("Helvetica-Bold", 11.5 * scale)
        c.drawString(150 + scale, H - 116 - 19 * scale, "practice booklets")

        c.setFillColor(HexColor("#FFFFFF"))
        c.setFont("Helvetica-Bold", 46)
        c.drawString(72, 358, "Practice booklets your kid")
        c.drawString(72, 302, "will actually finish.")

        c.setFillColor(HexColor("#C8DBFD"))
        c.setFont("Helvetica", 21)
        c.drawString(72, 244, "Printable PDFs for Years 1 to 10, written in")
        c.drawString(72, 214, "about two minutes, with a checked answer key.")
        c.setFont("Helvetica-Bold", 19)
        c.drawString(72, 154, "First booklet free.  No credit card.")

        # The cover itself, standing at the right, tilted a few degrees so it
        # reads as an object on a desk rather than a screenshot pasted in.
        ch = 470.0
        cw = ch * front.width / front.height
        c.saveState()
        c.translate(W - cw / 2 - 132, H / 2 - 8)
        c.rotate(-5)
        c.setFillColor(HexColor("#04113A"))
        c.roundRect(-cw / 2 + 9, -ch / 2 - 11, cw, ch, 6, stroke=0, fill=1)
        c.drawImage(str(shot), -cw / 2, -ch / 2, width=cw, height=ch,
                    mask=None)
        c.restoreState()
        c.showPage()
        c.save()

        doc = pymupdf.open(str(pdf))
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()

    im = im.resize((W, H), Image.LANCZOS)
    out = BRAND / "og-image.jpg"
    im.save(out, quality=86, optimize=True, progressive=True)
    print(f"  {out.name:34} {W}x{H}   {out.stat().st_size / 1024:6.1f} KB")


def main() -> int:
    print("Rendering the covers the website shows")
    front = None
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for s in SAMPLES:
            big = render_png(spec_for(s), HERO_PX, tmp)
            save(big, COVERS / f"{s.slug}.png")
            thumb = big.resize(
                (THUMB_PX, round(big.height * THUMB_PX / big.width)),
                Image.LANCZOS)
            save(thumb, COVERS / f"{s.slug}-thumb.png", colors=64)
            if front is None:
                front = big
    print("Rendering the share card")
    build_share_card(front)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
