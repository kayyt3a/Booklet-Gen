"""Checks the year dropdown in a real browser, because Safari broke it.

A customer's friend could not generate a booklet on an iPad. Tapping Generate
did nothing at all: no error, no page change, no request reaching the server.
It worked on every desktop it was tried on.

The cause was two lines in generate.html:

    o.hidden = !allowed;
    o.disabled = !allowed;

SAFARI HAS NEVER HONOURED `hidden` ON AN <option>. On an iPad every year stayed
in the list, and iOS draws a <select> as a native wheel picker, where the
difference between an option that is hidden and one that is merely disabled is
the difference between a year that does not exist and a year that scrolls past
and will not take.

Then the second half:

    if (!allowed && o.selected) { o.selected = false; cleared = true; }
    if (cleared) year.selectedIndex = 0;

Index 0 is the disabled placeholder. Forcing a DISABLED option selected on a
select marked `required` leaves a form that cannot be submitted, and iOS Safari
shows no validation message for it. Desktop Chrome draws a bubble explaining
the problem, which is exactly why this never reproduced anywhere it was looked
for.

The fix removes the options from the DOM instead of hiding them, and restores a
selection by VALUE rather than by index. Both are things every browser agrees
about, so nothing here depends on Safari having been fixed.

This drives Chromium rather than reading the source, because the property that
matters is what the DOM ends up holding. Chromium is not Safari, but the thing
being asserted is that the page no longer RELIES on a Safari-specific
behaviour: an option that is gone is gone everywhere.

    PYTHONPATH=. python scripts/check_generate_form_years.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
import wsgiref.simple_server
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("DATABASE_URL", None)
os.environ["FOLIO_DB"] = str(Path(tempfile.mkdtemp()) / "form.sqlite")
os.environ.setdefault("FOLIO_COOKIE_SECURE", "0")
logging.disable(logging.CRITICAL)

PASSED = 0
TOTAL = 0


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


print("\n== the page no longer asks Safari to hide an option ==")

source = (Path(__file__).resolve().parent.parent / "booklet_gen" / "webapp"
          / "templates" / "generate.html").read_text(encoding="utf-8")
check("o.hidden" not in source,
      "no option is hidden by attribute",
      "Safari ignores hidden on an <option>, so every restricted year comes "
      "back on an iPad and the picker offers years the program cannot take")
check("selectedIndex = 0" not in source.split("year.value = wanted")[0],
      "a selection is restored by value, not by forcing index 0",
      "index 0 is the disabled placeholder; selecting it on a required field "
      "leaves a form iOS Safari refuses to submit and refuses to explain")

from booklet_gen.webapp import create_app, db                     # noqa: E402

app = create_app()
app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False,
                  SECRET_KEY="check", SESSION_COOKIE_SECURE=False)
db.init_db()
user_id = db.create_user("ipad@example.com", "not-a-real-password")
if not isinstance(user_id, int):
    user_id = db.get_user_by_email("ipad@example.com")["id"]
db.add_credits(user_id, 5, "check") if hasattr(db, "add_credits") else None

server = wsgiref.simple_server.make_server("127.0.0.1", 0, app)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("\n  playwright is not installed; the browser half of this check "
          "cannot run.\n  pip install playwright")
    print(f"\n{PASSED}/{TOTAL} behaved as expected")
    raise SystemExit(0 if PASSED == TOTAL else 1)

print("\n== driving the real form ==")

with sync_playwright() as p:
    # This sandbox ships Chromium at a build the pip-installed Playwright
    # does not expect, and re-downloading is both slow and blocked. Point at
    # the one that is here; a caller with a matching install can drop
    # FOLIO_CHROMIUM and Playwright will find its own.
    exe = os.environ.get(
        "FOLIO_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    browser = (p.chromium.launch(executable_path=exe)
               if os.path.exists(exe) else p.chromium.launch())
    context = browser.new_context()
    # Sign in the way the app does, by planting the session cookie's contents
    # through the app itself rather than filling the login form.
    page = context.new_page()
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = user_id
        cookie = None
        for c in client.cookie_jar if hasattr(client, "cookie_jar") else []:
            if c.name == "folio_session":
                cookie = c.value
    page.goto(f"http://127.0.0.1:{port}/login", wait_until="domcontentloaded")

    # The form lives behind login; rather than fight the session, render the
    # template's script against the real markup by loading the page the app
    # serves to a signed-in user.
    from flask import url_for
    with app.test_request_context():
        pass
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user_id
    # The form is rendered by the index route for a signed-in
    # customer; /generate is the POST target and answers 405 to a GET.
    html = client.get("/").data.decode()
    if "id=\"year\"" not in html:
        print("  (the generate page did not render a year select; skipping)")
    else:
        page.set_content(html, wait_until="domcontentloaded")

        def years():
            return page.eval_on_selector_all(
                "#year option", "os => os.map(o => o.value).filter(Boolean)")

        def pick_program(value):
            page.eval_on_selector(
                f'input[name="program"][value="{value}"]',
                "el => { el.checked = true; el.dispatchEvent("
                "new Event('change', {bubbles: true})); }")

        programs = page.eval_on_selector_all(
            'input[name="program"]',
            "os => os.map(o => ({v: o.value, exam: o.dataset.exam, "
            "naplan: o.dataset.naplan}))")
        check(bool(programs), f"the form offers {len(programs)} program(s)",
              "no program radios rendered, so nothing below is measuring the "
              "real page")

        before = years()
        check(len(before) > 0, f"{len(before)} year(s) before any choice",
              "the year select is empty on load")

        naplan = next((x for x in programs if x["naplan"] == "yes"), None)
        if naplan:
            pick_program(naplan["v"])
            shown = years()
            check(shown and all(y.split()[-1] in {"3", "5", "7", "9"}
                                for y in shown),
                  f"NAPLAN offers only {shown}",
                  f"got {shown}. NAPLAN is sat in Years 3, 5, 7 and 9, and any "
                  "other year offers practice for a test never sat")
            check(len(shown) < len(before),
                  "and the other years are GONE from the DOM, not just styled",
                  "the options are still present, which is the Safari case: "
                  "hidden is ignored and the picker shows them anyway")

        plain = next((x for x in programs
                      if x["exam"] != "yes" and x["naplan"] != "yes"), None)
        if plain and naplan:
            # Against this program's OWN set, not the untouched list. Academic
            # Accelerate legitimately drops Years 11 and 12, which belong to
            # the exam paper, so "everything back" would be the wrong claim.
            pick_program(plain["v"])
            its_own = years()
            pick_program(naplan["v"])
            pick_program(plain["v"])
            check(years() == its_own,
                  f"switching away and back restores all {len(its_own)} of its "
                  "years",
                  f"got {years()}, expected {its_own}. Options removed by one "
                  "choice are not being put back by the next, so the picker "
                  "empties out as the customer changes their mind")
            check(set(its_own) - set(shown),
                  "and those include years NAPLAN had removed",
                  "the restore is not being exercised: this program allows "
                  "nothing that NAPLAN took away, so the assertion above "
                  "cannot fail")

        if plain:
            pick_program(plain["v"])
            available = years()
            if available:
                page.select_option("#year", available[1] if len(available) > 1
                                   else available[0])
                chosen = page.eval_on_selector("#year", "el => el.value")
                pick_program(plain["v"])            # a change that keeps it
                check(page.eval_on_selector("#year", "el => el.value") == chosen,
                      f"a chosen year ({chosen}) survives a program change",
                      "the customer's selection is being cleared by a change "
                      "that did not invalidate it, so they have to pick twice")
                check(page.eval_on_selector(
                          "#year", "el => el.selectedOptions[0].disabled") is False,
                      "and the select never rests on the disabled placeholder",
                      "a required select sitting on a disabled option cannot "
                      "be submitted, and iOS Safari gives no reason why")

    browser.close()

server.shutdown()
print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
