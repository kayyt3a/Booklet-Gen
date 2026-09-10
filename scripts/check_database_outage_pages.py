"""Checks that a database outage cannot take down pages that need no database.

A customer opened FolioAI and got the branded 500 page with no stylesheet on
it, a broken image where the wordmark goes, and "Log in / Sign up" in the nav
despite being signed in. Every one of those details came from one line:

    @bp.before_app_request
    def load_user():
        uid = session.get("user_id")
        g.user = db.get_user(uid) if uid else None

Flask runs `before_app_request` before EVERY request, and it counts a static
file as a request. So that database query sat in front of the stylesheet and
the logo as well as the pages, and when the database stopped answering it took
down:

  * the landing page, the pricing page and the login form, none of which need
    a database at all
  * /static/css/style.css, which is why the error page was unstyled
  * /static/img/brand/mark-96.png, which is why the wordmark was a broken
    image icon

and it rendered the error page with `g.user` never assigned, which Jinja reads
as undefined and therefore falsy, which is why the nav offered "Log in" to
somebody who was already logged in. The whole screenshot is one exception in
one before-request hook.

Two rules now, and both are asserted below against a database that raises on
every call:

  * a static file is never asked who is requesting it
  * a failed lookup means "not signed in", not "no site"

FAILING TO LOGGED-OUT IS THE POINT, and the second half of this file is about
that. It must not become a way past authentication: nothing behind
`login_required` may open, and /healthz must still report the outage, because
that is what monitoring watches and a site that hides its own outage is worse
than one that shows it.

    PYTHONPATH=. python scripts/check_database_outage_pages.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("FOLIO_DB", str(Path(tempfile.mkdtemp()) / "check.sqlite"))
logging.disable(logging.CRITICAL)

from booklet_gen.webapp import create_app, db                     # noqa: E402

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


def dead(*_args, **_kwargs):
    """Every driver failure looks the same from here: a paused project, an
    exhausted disk IO budget, a full connection pool, a dropped socket."""
    raise RuntimeError("connection timed out")


app = create_app()
# Production error handling, not the test client's default of re-raising, or
# every assertion below would measure werkzeug rather than the app.
app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False,
                  SECRET_KEY="check", SESSION_COOKIE_SECURE=False)

_real_get_user = db.get_user
_real_health = db.health_check
db.get_user = dead

print("\n== with the database refusing every query, and a signed-in cookie ==")

with app.test_client() as client:
    with client.session_transaction() as session:
        # The state the customer was in: a session cookie from an earlier
        # visit, so load_user has a user id to look up and must try.
        session["user_id"] = 1

    OPEN_TO_EVERYONE = [
        ("/", "the landing page"),
        ("/pricing", "the pricing page"),
        ("/login", "the login form"),
    ]
    for path, label in OPEN_TO_EVERYONE:
        status = client.get(path).status_code
        check(status == 200, f"{label} still loads ({path})",
              f"got {status}. This page needs no database, and a customer "
              "arriving during a blip sees a broken product rather than a "
              "working one they cannot sign into")

    STATIC = [
        ("/static/css/style.css", "the stylesheet"),
        ("/static/img/brand/mark-96.png", "the logo"),
        ("/static/img/brand/favicon-32.png", "the favicon"),
    ]
    for path, label in STATIC:
        response = client.get(path)
        check(response.status_code == 200 and len(response.get_data()) > 0,
              f"{label} is served ({path})",
              f"got {response.status_code}. A stylesheet does not need to know "
              "who is asking for it, and losing it is what made the error page "
              "look like a broken website rather than a message")

    css = client.get("/static/css/style.css")
    check(len(css.get_data()) > 10_000,
          f"the stylesheet arrives whole ({len(css.get_data()):,} bytes)",
          "a truncated stylesheet renders as an unstyled page, which is the "
          "symptom this check exists for")

    print("\n== and the error page, when one is genuinely needed, is styled ==")

    # Any page that truly needs the database still fails. It must fail looking
    # like FolioAI, which means the stylesheet it links has to be reachable.
    error_page = client.get("/library", follow_redirects=True)
    body = error_page.get_data(as_text=True)
    check("style.css" in body,
          "a page rendered during the outage still links its stylesheet",
          "the page cannot be styled, so every failure looks like a crash")

print("\n== failing to logged-out is not a way past the login ==")

with app.test_client() as client:
    with client.session_transaction() as session:
        session["user_id"] = 1
    for path in ("/library", "/account"):
        response = client.get(path)
        location = response.headers.get("Location", "")
        check(response.status_code in (301, 302) and "login" in location,
              f"{path} sends the visitor to the login page",
              f"got {response.status_code} to {location!r}. A database failure "
              "must never be a way into an account: the lookup that proves who "
              "somebody is just failed, so nobody is anybody")

print("\n== and the outage is still reported where it is watched ==")

db.health_check = dead
with app.test_client() as client:
    response = client.get("/healthz")
    check(response.status_code == 503,
          f"/healthz answers {response.status_code}, not 200",
          "the app now hides its own outage, so an uptime monitor reports the "
          "site healthy while every signed-in customer is locked out. That is "
          "a worse failure than the one this change fixed")
db.health_check = _real_health

print("\n== a working database still signs people in ==")

db.get_user = _real_get_user
with app.test_client() as client:
    users = None
    try:
        db.create_user("outage-check@example.com", "not-a-real-password")
        users = db.get_user_by_email("outage-check@example.com")
    except Exception as exc:
        print(f"                (could not create a test user: {exc})")
    if users is not None:
        with client.session_transaction() as session:
            session["user_id"] = users["id"]
        body = client.get("/").get_data(as_text=True)
        check("signedIn" in body,
              "the nav shows the signed-in state when the lookup works",
              "the guard is swallowing successful lookups too, so every "
              "customer is permanently logged out")
        check(client.get("/library").status_code == 200,
              "and a signed-in page opens",
              "the change locked out the customers it was meant to protect")
    else:
        check(False, "a test user could be created to prove the happy path",
              "this check cannot tell whether signing in still works, which "
              "is the thing most at risk from a change like this")

print("\n== the fragile line is gone from before_app_request ==")

source = (Path(__file__).resolve().parent.parent / "booklet_gen" / "webapp"
          / "auth.py").read_text(encoding="utf-8")
hook = source.split("def load_user(")[1].split("\ndef ")[0]
check("try:" in hook and "except" in hook,
      "load_user handles a failing lookup instead of raising",
      "an unguarded query in this hook takes down every page and every static "
      "file, which is exactly the outage a customer photographed")
check('request.endpoint == "static"' in hook,
      "load_user skips static files entirely",
      "the stylesheet and the logo are back behind a database query, so an "
      "outage will look like a broken website again")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
