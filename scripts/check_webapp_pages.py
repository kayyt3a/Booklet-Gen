"""What the web app's pages must keep doing, now that they are the shop front.

`check_webapp_security.py` covers auth, CSRF and session handling. This covers
the front end those pages present: that a signed-out visitor gets the landing
page and a signed-in one gets the form, that the static assets are actually
served, and that no page reaches out to another host.

Run: python scripts/check_webapp_pages.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = Path(tempfile.mkdtemp(prefix="folio-pages-"))
os.environ["FOLIO_DB"] = str(_tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(_tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "p" * 40
os.environ.pop("DATABASE_URL", None)

from booklet_gen.webapp import create_app                        # noqa: E402
from booklet_gen.webapp import db                                # noqa: E402
from booklet_gen.programs import PROGRAMS                        # noqa: E402

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


app = create_app()
client = app.test_client()


def csrf(path: str) -> str:
    m = re.search(rb'name="csrf_token" value="([^"]+)"', client.get(path).data)
    return m.group(1).decode() if m else ""


# ---------------------------------------------------------------------------
print("\nSigned out, the front page sells the product")
print("-" * 62)
# It used to be the generate form's template with the form hidden, so a
# prospective customer's first impression was a heading and a Sign up button on
# an otherwise empty page.
home = client.get("/")
body = home.data
check(home.status_code == 200, "the front page loads", str(home.status_code))
check(b"Practice booklets your kid will actually finish" in body,
      "it leads with what the product is")
check(b'id="program"' not in body and b"Generate booklet" not in body,
      "and does not show the generate form to someone who cannot use it")
for p in PROGRAMS.values():
    check(p.label.encode() in body, f"it names the {p.label} booklet")
check(b"sample-page.png" in body,
      "it shows a real page out of a booklet, not just a description")
check(b"No credit card" in body, "it says what signing up costs")

sample = client.get("/static/img/sample-page.png")
check(sample.status_code == 200 and len(sample.data) > 20_000,
      "and that sample page is actually served",
      f"{sample.status_code}, {len(sample.data)} bytes")


# ---------------------------------------------------------------------------
print("\nSigned in, the front page is the form")
print("-" * 62)
client.post("/signup", data={"email": "pages@test.com",
                             "password": "correct-horse-battery",
                             "csrf_token": csrf("/signup")})
with client.session_transaction() as s:
    signed_in = "user_id" in s
check(signed_in, "the fixture account signed in")

home = client.get("/")
body = home.data
check(b'id="program"' in body and b"Generate booklet" in body,
      "the generate form is there")
check(b"Practice booklets your kid will actually finish" not in body,
      "and the sales pitch is not, because they have already bought in")
for field in (b'id="year"', b'id="student_name"', b'id="term_plan"',
              b'name="csrf_token"'):
    check(field in body, f"the form still carries {field.decode()}")


# ---------------------------------------------------------------------------
print("\nStatic assets")
print("-" * 62)
for path, kind in (("/static/css/style.css", "the stylesheet"),
                   ("/static/favicon.svg", "the favicon")):
    r = client.get(path)
    check(r.status_code == 200 and len(r.data) > 200, f"{kind} is served",
          f"{r.status_code}, {len(r.data)} bytes")

css = client.get("/static/css/style.css").data.decode()
# These were hardcoded three times in the pills and a fourth time inline on the
# delete button, while --green sat declared and unused.
for token in ("--paper", "--orange-d", "--green-tint", "--amber-tint",
              "--danger-tint"):
    check(token in css, f"{token} is defined")
check(css.count("#a02020") <= 1,
      "the danger red is named once, not repeated at each use site")


# ---------------------------------------------------------------------------
print("\nEvery page is self-contained")
print("-" * 62)
# No CDN font, script or stylesheet. A booklet generator for children should
# not be telling a third party who is reading its pages, and an external asset
# is also one more thing that can be slow or gone when a parent first visits.
OFFSITE = re.compile(
    rb'(?:src|href)\s*=\s*["\'](?!/|\{\{|#|data:|mailto:)[a-zA-Z]+:', re.I)
pages = ["/", "/library", "/account", "/login", "/signup"]
for path in pages:
    data = client.get(path, follow_redirects=True).data
    hits = OFFSITE.findall(data)
    check(not hits, f"{path} loads nothing from another host", str(hits[:2]))

check("@import" not in css and "url(http" not in css,
      "and the stylesheet pulls in nothing either")


# ---------------------------------------------------------------------------
print("\nThe pages a signed-in user moves between still work")
print("-" * 62)
uid = db.get_user_by_email("pages@test.com")["id"]
db.create_job("pages-job", uid, "Academic Accelerate - Year 5 - Mathematics - Ella",
              units=1)
db.finish_job("pages-job", path=str(_tmp / "output" / "x.pdf"))
db.save_job_file("pages-job", uid, "folio.pdf", "application/pdf", b"%PDF fake")

lib = client.get("/library")
check(lib.status_code == 200 and b"Ella" in lib.data,
      "the library lists a finished booklet")
acct = client.get("/account")
check(acct.status_code == 200 and b"Booklets generated" in acct.data,
      "the account page shows usage")
prog = client.get("/progress/pages-job")
check(prog.status_code == 200 and b"bar" in prog.data,
      "the progress page renders")
dl = client.get("/download/pages-job")
check(dl.status_code == 200 and dl.data.startswith(b"%PDF"),
      "and the finished booklet still downloads")


# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
