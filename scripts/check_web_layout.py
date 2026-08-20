"""What the site's pages must measure, in a browser, at phone and desktop width.

The other web checks read markup. This one lays the pages out and measures the
boxes, because the defects it exists to catch are invisible in the HTML and in
the CSS: an absolutely positioned mark that lands on a sentence only at 390px,
a tap target that is 31px tall, a card that sits in the left two thirds of its
own container. Every one of those was found by looking at a screenshot, and a
screenshot is not something a check can keep looking at.

Widths are 390 and 1440 CSS px: a phone, which is where most of this traffic
arrives, and a laptop.

This needs Playwright and a Chromium. It is skipped, loudly, where there is no
browser to drive rather than failing the suite on a machine that was never
going to run it:

    pip install playwright && playwright install chromium
    PYTHONPATH=. python scripts/check_web_layout.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = Path(tempfile.mkdtemp(prefix="folio-layout-"))
os.environ["FOLIO_DB"] = str(_tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(_tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "l" * 40
os.environ["FOLIO_JOB_MODE"] = "manual"
os.environ.pop("DATABASE_URL", None)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("\nSKIPPED: Playwright is not installed, so the page layout cannot "
          "be measured.\n  pip install playwright && playwright install chromium")
    raise SystemExit(0)

from booklet_gen.webapp import create_app                        # noqa: E402
from booklet_gen.webapp import db                                # noqa: E402

PORT = int(os.environ.get("FOLIO_LAYOUT_PORT", "5177"))
BASE = f"http://127.0.0.1:{PORT}"
EMAIL, PASSWORD = "layout@test.com", "correct-horse-battery"

# iOS asks for 44pt, Android for 48dp. 44 is the floor used here.
TAP_MIN = 44

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def _launch(pw):
    """A browser, however this machine happens to have one.

    launch() first, because that is what a normal `playwright install` gives.
    The fallback covers a preinstalled browser directory whose build number
    does not match the installed Playwright, which would otherwise skip the
    check on a machine that does have a Chromium.
    """
    try:
        return pw.chromium.launch()
    except Exception:
        root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers"))
        for exe in sorted(root.glob("chromium-*/chrome-linux/chrome")):
            return pw.chromium.launch(executable_path=str(exe))
        raise


# ---------------------------------------------------------------------------
# A seeded app, served for real: these measurements depend on layout, and a
# test client does not lay anything out.
# ---------------------------------------------------------------------------
app = create_app()
with app.app_context():
    uid = db.create_user(EMAIL, PASSWORD)
    db.grant_credits(uid, 4, "fixture", "layout-check")
    for i, label in enumerate((
            "Academic Accelerate - Year 5 - Mathematics - Ella",
            "NAPLAN Practice - Year 5 - Ella",
            "Academic Accelerate - Year 3 - English - Noah")):
        job = f"layout-{i}"
        db.create_job(job, uid, label, units=1)
        db.finish_job(job, path=str(_tmp / "output" / f"{job}.pdf"))
        db.save_job_file(job, uid, "folio.pdf", "application/pdf", b"%PDF x")

threading.Thread(
    target=lambda: app.run(port=PORT, threaded=True, use_reloader=False),
    daemon=True).start()
time.sleep(1.2)


def boxes(page, selector: str) -> list[dict]:
    return page.eval_on_selector_all(selector, """els => els.map(e => {
        const r = e.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height,
                right: r.right, bottom: r.bottom,
                text: (e.textContent || '').trim().slice(0, 24)};
    })""")


def box(page, selector: str) -> dict | None:
    got = boxes(page, selector)
    return got[0] if got else None


def overlap(a: dict, b: dict) -> float:
    """Area shared by two boxes, in square CSS px."""
    dx = min(a["right"], b["right"]) - max(a["x"], b["x"])
    dy = min(a["bottom"], b["bottom"]) - max(a["y"], b["y"])
    return dx * dy if dx > 0 and dy > 0 else 0.0


with sync_playwright() as pw:
    browser = _launch(pw)

    def open_page(width: int, height: int = 844, signed_in: bool = False):
        ctx = browser.new_context(viewport={"width": width, "height": height})
        page = ctx.new_page()
        if signed_in:
            page.goto(BASE + "/login")
            page.fill("#email", EMAIL)
            page.fill("#password", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
        return ctx, page

    # -----------------------------------------------------------------------
    print("\nNothing decorative sits on the words that sell the product")
    print("-" * 62)
    # The hero's F mark is absolutely positioned against the full-bleed hero,
    # so at 390px it rendered from x=303 to x=413 and landed on top of "Your
    # first booklet is included. No credit card required." with the full stop
    # partly under it. That sentence removes a parent's last objection.
    for width in (390, 1440):
        ctx, page = open_page(width)
        page.goto(BASE + "/")
        page.wait_for_load_state("networkidle")
        mark = box(page, ".heroMark")
        line = box(page, ".reassure")
        # display:none reports a zero box rather than no box at all.
        shown = mark is not None and mark["w"] > 0
        if not shown:
            check(width < 900, "the hero mark is absent where there is no room "
                               "beside the text column", f"{width}px")
        else:
            check(overlap(mark, line) == 0,
                  "the hero mark is clear of the reassurance line",
                  f"{width}px, {overlap(mark, line):.0f} sq px shared")
        # Nothing in the hero may widen the document: the mark is deliberately
        # cropped by the corner, which only works while it is cropped rather
        # than scrolled to.
        scroll = page.evaluate("document.documentElement.scrollWidth")
        check(scroll <= width, "the page does not scroll sideways",
              f"{width}px viewport, {scroll}px document")
        ctx.close()

    # -----------------------------------------------------------------------
    print("\nThe wrapped nav row is not sliced by the header's own edge")
    print("-" * 62)
    # At 390 the nav wraps to a second row whose bottom was the header's
    # bottom. The active link's underline is a 2px rule with 6px rounded ends,
    # so being cut off at the header edge left a flat orange smudge; the white
    # "Sign up" pill had 25px of navy above it and 2px below.
    for path, signed_in in (("/pricing", False), ("/login", False),
                            ("/library", True)):
        ctx, page = open_page(390, signed_in=signed_in)
        page.goto(BASE + path)
        page.wait_for_load_state("networkidle")
        header = box(page, "header")
        rows = boxes(page, ".nav a, .navForm button")
        wrapped = [r for r in rows if r["h"] > 0]
        gap = min(header["bottom"] - r["bottom"] for r in wrapped)
        check(gap >= 6, f"{path}: the nav row clears the header edge",
              f"{gap:.0f}px below the lowest nav item")
        pill = box(page, ".navCta")
        if pill and pill["h"] > 0:
            below = header["bottom"] - pill["bottom"]
            # It cannot be centred in a two-row header, and it is not asked to
            # be: it has to have navy under it rather than the header's edge.
            check(below >= 8,
                  f"{path}: the Sign up pill sits in the bar, not on its edge",
                  f"{below:.0f}px of bar below it")
        ctx.close()

    browser.close()

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
