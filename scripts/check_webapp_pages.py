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
from booklet_gen.webapp.commerce import products                 # noqa: E402
from booklet_gen.webapp.public import _business                  # noqa: E402
from booklet_gen.programs import (                               # noqa: E402
    EXAM_PROGRAMS,
    PROGRAMS,
    customer_programs,
)

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
# The header wordmark is marked up as FOLIO <em>AI</em> so the logo can tint
# the "AI", so a raw substring search for "FOLIOAI" no longer finds it and
# would pass or fail for the wrong reason. Collapse the tags in the brand link
# and check what a customer actually reads.
_brand = re.search(rb'<a class="brand"[^>]*>(.*?)</a>', body, re.S)
_brand_text = " ".join(re.sub(rb"<[^>]+>", b" ", _brand.group(1)).decode().split())
check(_brand_text.startswith("FOLIO AI") and b"FolioAI writes" in body,
      "the customer-facing product name is FolioAI", _brand_text)
check(b"Practice booklets your kid will actually finish" in body,
      "it leads with what the product is")
check(b'name="program"' not in body and b"Generate booklet" not in body,
      "and does not show the generate form to someone who cannot use it")
offered = customer_programs()
withheld = {k: p for k, p in PROGRAMS.items() if k not in offered}
for p in offered.values():
    check(p.label.encode() in body, f"it names the {p.label} booklet")
# Scholarships and Methods Exam are held back from the customer menu until each
# has its own original-authoring guide and a reviewed sample set. Advertising a
# product the site will then refuse to generate is the failure this guards.
for p in withheld.values():
    check(p.label.encode() not in body,
          f"and does not advertise {p.label}, which is not on sale yet")

# The hero eyebrow said "Years 1 to 10, plus WACE exams" in hand-written copy
# while methods_exam sat outside DEFAULT_WEB_PROGRAMS, so the headline named
# the one product the site would refuse to sell. It is now driven from
# customer_programs(), which is what this pair of checks pins: silent when the
# exam program is withheld, and back when it is offered.
check((b"WACE" in body) == bool(EXAM_PROGRAMS & set(offered)),
      "it advertises WACE exams only when an exam program is on sale",
      f"offered {sorted(offered)}, WACE named: {b'WACE' in body}")
_saved_allowlist = os.environ.get("FOLIO_WEB_PROGRAM_ALLOWLIST")
os.environ["FOLIO_WEB_PROGRAM_ALLOWLIST"] = "naplan,accelerate,methods_exam"
try:
    with_exams = client.get("/").data
finally:
    if _saved_allowlist is None:
        os.environ.pop("FOLIO_WEB_PROGRAM_ALLOWLIST", None)
    else:
        os.environ["FOLIO_WEB_PROGRAM_ALLOWLIST"] = _saved_allowlist
check(b"WACE" in with_exams,
      "and does advertise them once the exam program is allowlisted")

# The hero promised "practice questions sized for an hour". Only the class work
# is trimmed to CLASSWORK_CAP_MINUTES; the booklet also carries a week of
# homework and a Final Challenge, and one real Year 5 Maths booklet printed
# "about 245 minutes" on its cover. A parent who bought an hour and printed
# four is a refund. So the page may never call something an hour without
# saying, right there, that the hour is the class work.
_hero = body.decode()
_hour_claims = [_hero[max(0, m.start() - 110):m.end() + 110]
                for m in re.finditer(r"\bhour\b", _hero)]
check(bool(_hour_claims)
      and all("class work" in c.lower() for c in _hour_claims),
      "an hour is only ever claimed for the class work, not the booklet",
      f"{len(_hour_claims)} mentions of an hour")
check(b"for the rest of the week" in body,
      "and the page says the homework runs on past that hour")

check(b"sample-page.png" in body,
      "it shows a real page out of a booklet, not just a description")
check(b"No credit card" in body, "it says what signing up costs")


# ---------------------------------------------------------------------------
print("\nThe landing page says what a booklet costs, before the fold")
print("-" * 62)
# "First booklet free" appeared above the fold and no number did, so a parent
# deciding whether this was worth an account had to leave the page for Pricing
# to find out whether the second booklet was five dollars or fifty. The figures
# come from commerce.products(), never typed into the template, because a typed
# price drifts the day FOLIO_PRICE_SINGLE_AUD changes and then the shop front
# quotes something Checkout does not charge.
catalog = products()
_hero_html = body.decode().split('<section class="coverBand"', 1)[0]
check(f"A${catalog['single'].display_price}" in _hero_html,
      "the price of one booklet is in the hero, above everything else",
      f"looking for A${catalog['single'].display_price}")
for key, p in catalog.items():
    check(f"A${p.display_price}".encode() in body,
          f"the page quotes the catalogue price of the {p.label}",
          f"A${p.display_price}")
# Nothing on the page may name a price the catalogue does not sell.
_quoted = set(re.findall(r"A\$\s*([0-9]+(?:\.[0-9]{2})?)", body.decode()))
_real = {p.display_price for p in catalog.values()}
check(_quoted and _quoted <= _real,
      "and quotes no figure the catalogue does not carry",
      f"page says {sorted(_quoted)}, catalogue holds {sorted(_real)}")
# Rendered, not written out: move the price and the page has to move with it.
_saved_price = os.environ.get("FOLIO_PRICE_SINGLE_AUD")
os.environ["FOLIO_PRICE_SINGLE_AUD"] = "6.50"
try:
    _repriced = client.get("/").data
finally:
    if _saved_price is None:
        os.environ.pop("FOLIO_PRICE_SINGLE_AUD", None)
    else:
        os.environ["FOLIO_PRICE_SINGLE_AUD"] = _saved_price
check(b"A$6.50" in _repriced and b"A$5.00" not in _repriced,
      "and the figure follows the catalogue when the price changes",
      "repriced to 6.50")

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
check(b'name="program"' in body and b"Generate booklet" in body,
      "the generate form is there")
check(b"Practice booklets your kid will actually finish" not in body,
      "and the sales pitch is not, because they have already bought in")
for field in (b'id="year"', b'id="student_name"', b'id="term_plan"',
              b'name="csrf_token"'):
    check(field in body, f"the form still carries {field.decode()}")
for key, p in offered.items():
    check(f'id="program_{key}"'.encode() in body and p.blurb.encode() in body,
          f"the form explains the {p.label} choice")
for key in withheld:
    check(f'id="program_{key}"'.encode() not in body,
          f"the form offers no way to pick {PROGRAMS[key].label}")
check(b"progressively planned booklets" in body,
      "the term-plan choice explains what it delivers")


# ---------------------------------------------------------------------------
print("\nStatic assets")
print("-" * 62)
r = client.get("/static/css/style.css")
check(r.status_code == 200 and len(r.data) > 200, "the stylesheet is served",
      f"{r.status_code}, {len(r.data)} bytes")

# Every icon and preview image the page declares must actually resolve. This
# used to name /static/favicon.svg outright, so moving the brand to the real
# artwork left the check pointing at a deleted file: it caught the breakage,
# but it would have gone on asserting one hard-coded path for ever. Reading the
# hrefs out of the rendered head means the check follows the markup.
_declared = re.findall(rb'<link rel="(?:icon|apple-touch-icon)"[^>]*?href="([^"]+)"',
                       body, re.S)
_declared += re.findall(rb'<meta property="og:image"[^>]*?content="([^"]+)"', body, re.S)
check(len(_declared) >= 3, "the page declares tab icons and a share image",
      f"{len(_declared)} declared")
for _u in _declared:
    _path = _u.decode()
    _path = _path[_path.index("/static"):] if "/static" in _path else _path
    r = client.get(_path)
    check(r.status_code == 200 and len(r.data) > 200,
          f"{_path.rsplit('/', 1)[-1]} is served",
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
# The canonical link is a deliberate exception: it is required to be an
# absolute, self-referencing URL (booklet_gen/webapp/seo.py, base.html), and
# it is metadata about this page's own address, not a resource fetched from
# anywhere. Nothing else on the page is allowed to be absolute, so only this
# one tag is stripped before the offsite scan runs.
CANONICAL_LINK = re.compile(rb'<link rel="canonical"[^>]*>')
pages = [
    "/", "/library", "/account", "/login", "/signup", "/pricing",
    "/support", "/privacy", "/terms", "/forgot-password",
    "/verify/resend",
]
for path in pages:
    data = client.get(path, follow_redirects=True).data
    hits = OFFSITE.findall(CANONICAL_LINK.sub(b"", data))
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

# ---------------------------------------------------------------------------
print("\nA row in My booklets says its state once, and offers a way on")
print("-" * 62)
# Two kinds of failed row. The first can be retried; the second cannot, because
# it has no stored request. That second kind is not hypothetical: request_json
# arrived as a later column (see db.init_db's migrations), so every job already
# in the database when it landed carries NULL, and db.create_job, which the
# fixtures and the older code path use, never writes one either. Its action
# slot used to hold a second, identical red "Failed" pill beside the one in the
# row's own meta line: right-aligned on a laptop, left-aligned on a phone, and
# saying nothing the row had not already said, under an error message that ends
# "Please try again".
db.enqueue_job("pages-retryable", uid, "NAPLAN Practice - Year 5 - Ella", 1,
               {"program": "naplan", "year": "Year 5", "name": "Ella"},
               reserve_credits=False)
db.fail_job("pages-retryable", "the model timed out")
db.create_job("pages-legacy", uid,
              "Academic Accelerate - Year 3 - English - Noah", units=1)
db.fail_job("pages-legacy", "the model timed out")
check(db.get_job("pages-legacy")["request_json"] is None,
      "a job created without a stored request really has none",
      "which is what a pre-migration row looks like")

_rows = re.findall(r'<li class="jobItem">(.*?)</li>',
                   client.get("/library").data.decode(), re.S)
check(len(_rows) == 3, "the library renders every row", f"{len(_rows)} rows")
for _row in _rows:
    _title = re.search(r'class="jobTitle">([^<]*)', _row).group(1).strip()
    _pills = re.findall(r'<span class="pill [^"]*">([^<]*)</span>', _row)
    check(len(_pills) == 1, f"{_title[:34]}: one status pill, not two",
          f"pills: {[p.strip() for p in _pills]}")
_legacy = next(r for r in _rows if "Noah" in r)
check("Try again" not in _legacy and "/retry/" not in _legacy,
      "a row with no stored request offers no retry, which would 404")
check('href="/"' in _legacy,
      "but it does offer a way on, rather than a dead red pill")
acct = client.get("/account")
check(acct.status_code == 200 and b"Booklets generated" in acct.data,
      "the account page shows usage")
check(b"<details class=\"dangerZone\">" in acct.data,
      "account deletion is available without dominating the page")
pricing = client.get("/pricing")
check(pricing.status_code == 200 and b"pay-as-you-go pricing" in pricing.data,
      "the pricing page explains the credit packs")
check(b"Start with a free booklet" not in pricing.data and
      b"Purchases coming soon" in pricing.data,
      "a signed-in user sees payment availability honestly")
for path, phrase in (("/support", b"How can we help"),
                     ("/privacy", b"Privacy policy"),
                     ("/terms", b"Terms of use"),
                     ("/forgot-password", b"Reset your password")):
    page = client.get(path)
    check(page.status_code == 200 and phrase in page.data,
          f"{path} renders its customer page")
terms = client.get("/terms").data
support = client.get("/support").data
privacy = client.get("/privacy").data
check(b"within 14 days" in terms and b"replacement credit" in terms,
      "the terms state the voluntary quality remedy")
check(b"two business days" in support and b"not usable" in support,
      "support gives a response target and quality path")
check(b"first name or nickname only" in privacy and b"accounting records" in privacy,
      "privacy copy minimises child data and explains residual legal records")

# ---------------------------------------------------------------------------
print("\nEvery page carries the identity of the business taking the money")
print("-" * 62)
# The footer used to be a trading name and three links, which is thin for an
# Australian parent about to enter a card. It now carries what the legal pages
# already state, from the same source they read: public._business(). The point
# of checking it here is that the two can never disagree, and that nothing in
# the footer is invented.


def footer_of(path: str) -> str:
    m = re.search(r"<footer>(.*?)</footer>", client.get(path).data.decode(), re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", m.group(1)).split()) if m else ""


_biz = _business()
for path in ("/", "/pricing", "/support", "/login"):
    foot = footer_of(path)
    check(_biz["name"] in foot and _biz["email"] in foot
          and _biz["country"] in foot,
          f"{path}: the footer names the business, its country and its email",
          foot[:90])
# The same address the legal pages give, not a second one nobody monitors.
for path in ("/support", "/privacy", "/terms"):
    page = client.get(path).data.decode()
    addresses = set(re.findall(r"mailto:([^\"'>\s]+)", page))
    check(addresses == {_biz["email"]},
          f"{path} gives the one contact address the footer gives",
          f"page offers {sorted(addresses)}")
# Nothing invented: with no ABN and no postal address configured, the footer
# states neither, and does not leave the separators of an empty one behind.
_ABN = "ABN 12 345 678 901"
_ADDR = "PO Box 9, Perth WA 6000"
check(_ABN not in footer_of("/") and _ADDR not in footer_of("/"),
      "with no business number configured, the footer states none")
_saved = {k: os.environ.get(k) for k in
          ("FOLIO_BUSINESS_NUMBER", "FOLIO_BUSINESS_ADDRESS")}
os.environ["FOLIO_BUSINESS_NUMBER"] = _ABN
os.environ["FOLIO_BUSINESS_ADDRESS"] = _ADDR
try:
    _identified = footer_of("/")
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
check(_ABN in _identified and _ADDR in _identified,
      "and prints both the moment the deployment sets them", _identified[:120])

# ---------------------------------------------------------------------------
print("\nEvery page tells the same truth about how long files are kept")
print("-" * 62)
# Pricing promised "Saved in My booklets for later download" on a page taking
# money, while support said FolioAI "keeps only your most recent generated
# files" with no number, and the library quoted a number neither of them did.
# db.save_job_file keeps the newest FILE_RETENTION_PER_USER files per account
# and clears the rest, so that is the number all three must print. The value is
# monkeypatched to something no other page would say by accident, which also
# proves the pages read it live rather than hard-coding today's default.
_real_retention = db.FILE_RETENTION_PER_USER
db.FILE_RETENTION_PER_USER = 7
try:
    retention_pages = {p: client.get(p).data for p in
                       ("/pricing", "/support", "/library")}
finally:
    db.FILE_RETENTION_PER_USER = _real_retention
for path, page in retention_pages.items():
    check(b"7 most recent" in page,
          f"{path} states the real number of files kept", str(b"7 most recent" in page))
check(b"Saved in My booklets for later download" not in retention_pages["/pricing"],
      "and pricing no longer implies a booklet is kept for ever")
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
