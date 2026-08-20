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
# A second account with nothing in it, because the empty states are pages too.
NEW_EMAIL = "layout-new@test.com"

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
    db.create_user(NEW_EMAIL, PASSWORD)
    db.grant_credits(uid, 4, "fixture", "layout-check")
    for i, label in enumerate((
            "Academic Accelerate - Year 5 - Mathematics - Ella",
            "NAPLAN Practice - Year 5 - Ella",
            "Academic Accelerate - Year 3 - English - Noah")):
        job = f"layout-{i}"
        db.create_job(job, uid, label, units=1)
        db.finish_job(job, path=str(_tmp / "output" / f"{job}.pdf"))
        db.save_job_file(job, uid, "folio.pdf", "application/pdf", b"%PDF x")
    # A plan is always ten weeks (programs.TERM_PLAN_WEEKS), so every plan
    # ends on a two-digit week chip. The ladder is written out here rather
    # than planned, because planning it would need a live model.
    db.create_plan(uid, "Ella", "accelerate", "Mathematics", "Year 5", 10,
                   [{"week": n, "focus": f"Week {n} focus topic"}
                    for n in range(1, 11)])

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


def ink_boxes(page, selector: str) -> list[dict]:
    """Where an element actually puts ink.

    A block paragraph's own box runs the full width of its container even when
    its text is centred and 300px wide, so measuring element boxes reports an
    overlap that nobody can see. For anything without a background of its own,
    this measures the text run instead, via a Range over the element's
    contents. Elements that paint a background (a button) keep their own box,
    because there the whole slab is visible.
    """
    return page.eval_on_selector_all(selector, """els => els.map(e => {
        const bg = getComputedStyle(e).backgroundColor;
        const painted = bg && bg !== 'transparent' &&
                        !/rgba\\(0,\\s*0,\\s*0,\\s*0\\)/.test(bg);
        let r = e.getBoundingClientRect();
        if (!painted) {
            const range = document.createRange();
            range.selectNodeContents(e);
            const rr = range.getBoundingClientRect();
            if (rr.width > 0) { r = rr; }
        }
        return {x: r.x, y: r.y, w: r.width, h: r.height,
                right: r.right, bottom: r.bottom,
                text: (e.textContent || '').trim().slice(0, 24)};
    })""")


def text_boxes(page, selector: str) -> list[dict]:
    """The rectangle the characters occupy, whatever the element paints."""
    return page.eval_on_selector_all(selector, """els => els.map(e => {
        const range = document.createRange();
        range.selectNodeContents(e);
        const r = range.getBoundingClientRect();
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

    def open_page(width: int, height: int = 844, signed_in: bool = False,
                  email: str = EMAIL):
        ctx = browser.new_context(viewport={"width": width, "height": height})
        page = ctx.new_page()
        if signed_in:
            page.goto(BASE + "/login")
            page.fill("#email", email)
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

    # -----------------------------------------------------------------------
    print("\nAn empty library reads as words, not as words on a drawing")
    print("-" * 62)
    # The ghosted book and pencil are 130px line art in a panel about 340px
    # wide on a phone, so the sentence and the button printed straight over
    # them. A new account's first screen was its own copy tangled in wallpaper.
    for width in (390, 1440):
        ctx, page = open_page(width, signed_in=True, email=NEW_EMAIL)
        page.goto(BASE + "/library")
        page.wait_for_load_state("networkidle")
        motifs = [m for m in boxes(page, ".scatter .s") if m["w"] > 0]
        words = [w for w in ink_boxes(page, ".empty p, .empty .btn") if w["w"] > 0]
        check(bool(words), f"{width}px: the empty state still says something",
              f"{len(words)} text elements")
        worst = max((overlap(m, w) for m in motifs for w in words), default=0.0)
        check(worst == 0,
              f"{width}px: nothing readable sits on the ghosted motifs",
              f"{len(motifs)} motifs, {worst:.0f} sq px shared")
        ctx.close()

    # -----------------------------------------------------------------------
    print(f"\nEverything tappable is at least {TAP_MIN}px tall on a phone")
    print("-" * 62)
    for label, signed_in in (("signed out", False), ("signed in", True)):
        ctx, page = open_page(390, signed_in=signed_in)
        page.goto(BASE + "/pricing")
        page.wait_for_load_state("networkidle")
        for what, selector in (("nav", ".nav a, .navForm button"),
                               ("footer", "footer a")):
            targets = [t for t in boxes(page, selector) if t["w"] > 0]
            shortest = min(targets, key=lambda t: t["h"])
            check(shortest["h"] >= TAP_MIN,
                  f"{label}: every {what} target is finger-sized",
                  f"shortest is {shortest['h']:.0f}px ({shortest['text']})")
        # Equal boxes around unequal words leave unequal space between the
        # words, and space between the words is what the eye measures.
        # The ink, not the boxes: with every item flex:1 the boxes were
        # flush against each other and looked perfectly even, while the words
        # inside them were 25, 20, 11, 15 and 16px apart.
        nav = sorted((t for t in ink_boxes(page, ".nav a, .navForm button")
                      if t["w"] > 0), key=lambda t: t["x"])
        gaps = [round(b["x"] - a["right"], 1)
                for a, b in zip(nav, nav[1:]) if b["x"] >= a["right"] - 1]
        if len(gaps) >= 3:
            spread = max(gaps) - min(gaps)
            check(spread <= 4, f"{label}: the nav items are evenly spaced",
                  f"gaps {gaps}, spread {spread:.0f}px")
        ctx.close()

    # -----------------------------------------------------------------------
    print("\nOn a page someone came to use, the page comes first")
    print("-" * 62)
    # Paulio was 220-320px on Create, Study plans, My booklets and Account,
    # with the page title vertically centred against him. The result: a
    # library of five booklets whose first row began at y=790 in a 900px
    # window, and a one-form page whose first control sat 1173px down.
    for path in ("/", "/plans", "/library", "/account"):
        for width in (390, 1440):
            ctx, page = open_page(width, signed_in=True)
            page.goto(BASE + path)
            page.wait_for_load_state("networkidle")
            # Selectors name the row that has always been there, not the
            # modifier class this fix introduced, so the same measurements
            # run against the old markup.
            bear = box(page, ".paulioRow img")
            card = box(page, ".paulioRow")
            title = box(page, ".paulioRow h1")
            check(bear is not None and bear["w"] <= 120,
                  f"{path} at {width}: the mascot is a margin figure",
                  f"{bear['w']:.0f}px wide" if bear else "no mascot found")
            # Top-aligned, not floated in the middle of a tall bear.
            drop = (title["y"] - card["y"]) if title and card else 999
            check(drop <= 12,
                  f"{path} at {width}: the page title starts at the top of "
                  "its card", f"{drop:.0f}px down")
            ctx.close()

    ctx, page = open_page(1440, 900, signed_in=True)
    page.goto(BASE + "/library")
    page.wait_for_load_state("networkidle")
    first = box(page, ".jobItem")
    # 542 with the old header block, in a 900px window: the customer saw a
    # bear, a speech bubble and the top of one row. 450 leaves the mascot
    # room to exist and still puts two booklets on the first screen.
    check(first["y"] < 450, "a returning customer sees their booklets without "
                            "scrolling", f"first row at y={first['y']:.0f}")
    ctx.close()

    ctx, page = open_page(390, signed_in=True)
    page.goto(BASE + "/")
    page.wait_for_load_state("networkidle")
    control = box(page, ".programOption label")
    check(control["y"] < 844, "the create form's first choice is on the first "
                              "screen", f"y={control['y']:.0f} in an 844px viewport")
    ctx.close()

    # -----------------------------------------------------------------------
    print("\nThe last row of a ten-week plan looks like the other nine")
    print("-" * 62)
    for width in (390, 1440):
        ctx, page = open_page(width, signed_in=True)
        page.goto(BASE + "/plans")
        page.wait_for_load_state("networkidle")
        chips = boxes(page, ".weekNo")
        ink = text_boxes(page, ".weekNo")
        check(len(chips) == 10, f"{width}px: the plan lists ten weeks",
              f"{len(chips)} chips")
        widths = {round(c["w"], 1) for c in chips}
        check(len(widths) == 1, f"{width}px: every week chip is the same size",
              str(sorted(widths)))
        clear = min(min(i["x"] - c["x"], c["right"] - i["right"])
                    for c, i in zip(chips, ink))
        # The chip has to be visible around its own number. At 1.7em week 10
        # had 3.5px, so the numeral covered the chip.
        check(clear >= 5, f"{width}px: the number sits inside its chip",
              f"tightest is {clear:.1f}px")
        ctx.close()

    # -----------------------------------------------------------------------
    print("\nForm fields are one width, not a staircase")
    print("-" * 62)
    # On Study plans the three fields ran 288px, then the full 800px card,
    # then 288px again, because nothing set a select's width and each one took
    # whatever its container gave it. A dropdown's content is a fixed list, so
    # there is nothing about it that justifies a different width from the
    # dropdown above it.
    for path in ("/plans", "/"):
        for width in (390, 1440):
            ctx, page = open_page(width, signed_in=True)
            page.goto(BASE + path)
            page.wait_for_load_state("networkidle")
            # The create form hides the subject dropdown until a product that
            # has subjects is chosen, and a form with one dropdown in it
            # cannot be inconsistent with itself.
            # Set through the DOM rather than clicked: the radio itself is
            # visually hidden behind its label card, so a real click would
            # wait for an element that is never going to be visible.
            page.evaluate("""() => {
                const r = document.querySelector('#program_accelerate');
                if (r) { r.checked = true;
                         r.dispatchEvent(new Event('change', {bubbles:true})); }
            }""")
            page.wait_for_timeout(120)
            # .planGenerate's week picker is deliberately inline, beside its
            # own button, and is not one of the form's stacked fields.
            picks = [b for b in boxes(page, "form:not(.planGenerate) select")
                     if b["w"] > 0]
            widths = sorted({round(b["w"]) for b in picks})
            check(len(picks) >= 2 and len(widths) == 1,
                  f"{path} at {width}: every dropdown is the same width",
                  f"{len(picks)} dropdowns, widths {widths}")
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
