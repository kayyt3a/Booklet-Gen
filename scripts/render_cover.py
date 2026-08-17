"""Render just the cover, for design iteration.

The cover used to be reachable only by generating a whole booklet, questions,
lesson, answer key and all, which is slow and (outside of scripts/check_
cover_design.py's hand-built fixtures) needs a live Gemini key. The cover
itself needs none of that: render_cover() draws from a plain CoverSpec, no
BookletData, no LLM call.

    python scripts/render_cover.py --subject Mathematics --year "Year 6" \\
        --topic "Fractions and Decimals" --name "Kieran Tran" --out cover.pdf

    # also drop a PNG next to it, to look at without opening a PDF viewer
    python scripts/render_cover.py --subject Science --year "Year 9" --png

Add --variant to override the automatic light_blue/dark_navy/white/warm
choice; leave it off to see what a real booklet with these fields would get.
"""
import argparse
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from booklet_gen.visuals.cover import CoverSpec, VARIANTS, render_cover, variant_for


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subject", default="Mathematics")
    ap.add_argument("--year", default="Year 6")
    ap.add_argument("--topic", default="")
    ap.add_argument("--name", default="Kieran Tran")
    ap.add_argument("--program", default="Academic Accelerate",
                    help="Product line shown as the small eyebrow, e.g. "
                         "'Academic Accelerate', 'Scholarships', 'NAPLAN Practice'.")
    ap.add_argument("--pill", default="Practice Booklet")
    ap.add_argument("--week", default="", help="e.g. '4 of 10' or '4 of 10  |  Persuasive devices'")
    ap.add_argument("--difficulty", default="")
    ap.add_argument("--variant", choices=sorted(VARIANTS), default="",
                    help="Override the automatic family choice.")
    ap.add_argument("--out", default="output/cover_preview.pdf")
    ap.add_argument("--png", nargs="?", const="output/cover_preview.png", default=None,
                    help="Also save a PNG. Give a path, or omit the value for "
                         "output/cover_preview.png.")
    args = ap.parse_args()

    variant = args.variant or variant_for(args.subject, args.program, args.year, args.topic)
    spec = CoverSpec(
        title_lines=[args.year, args.subject],
        pill=args.pill,
        eyebrow=args.program.upper(),
        subject=args.subject,
        topic=args.topic,
        student_name=args.name,
        week=args.week,
        difficulty=args.difficulty,
        meta_lines=[date.today().strftime("%d %B %Y"),
                   "Estimated time: about 45 minutes. Take breaks whenever you need to."],
        footer_note="Work through it in order. In the key at the back, a tick "
                    "marks an answer that has been checked.",
        variant=variant,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(out), pagesize=A4)
    render_cover(c, spec)
    c.showPage()
    c.save()
    print(f"variant: {variant}")
    print(f"wrote {out}")

    if args.png:
        import fitz  # PyMuPDF
        doc = fitz.open(str(out))
        png_path = Path(args.png)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        doc[0].get_pixmap(dpi=150).save(str(png_path))
        doc.close()
        print(f"wrote {png_path}")


if __name__ == "__main__":
    main()
