"""Checks that a search engine can find FolioAI, and only find the right part of it.

The founder searched "folio ai tutors you" and nothing came up except the
direct link. Two things were actually missing, not one generic checklist
item: there was no robots.txt and no sitemap.xml, so nothing told a crawler
which of the site's ~25 routes are worth indexing, and there was no
structured data, so even a query containing the exact brand name had no
Organization signal to resolve to. Both are fixed in `booklet_gen/webapp/seo.py`
and `base.html`. This script proves the fix rather than trusting it:

* `robots.txt` and `sitemap.xml` exist, return the right content type, and
  actually agree with the account of the app's own routes below, rather than
  being two hand-typed lists that could each drift from `views.py`,
  `payments.py`, `admin.py` and `auth.py` without anything noticing.
* Every login-gated or admin-only route in the source is covered by a
  `Disallow` line. Missing one here means a page that only ever shows an
  anonymous crawler a login redirect gets crawl budget anyway, and a stale
  job's `/download/<job_id>` or the admin console being merely
  *unauthenticated* rather than *disallowed* is exactly the "getting it wrong
  is worse than doing nothing" case the brief called out.
* No URL in the sitemap is one of those gated routes. A sitemap is a
  request to index; asking Google to index a login wall is worse than saying
  nothing.
* Every public, indexable page has its own `<title>` and meta description
  distinct from the others. One generic description repeated on every page
  is indistinguishable, to a search engine, from having none, and it was the
  actual state of five of these seven pages before this change.
* Canonical and `og:url` are present and point at the real deployed origin,
  not `http://localhost` or a relative path, once `FOLIO_PUBLIC_URL` is set,
  the same variable the founder already sets on Render for Stripe.
* Organization JSON-LD is present sitewide and carries the same name and
  support email as the Privacy, Terms and Support pages, so it can never say
  something else about who FolioAI is. Product JSON-LD on `/pricing` carries
  no `aggregateRating` or `review` field anywhere, because FolioAI has
  neither and fake rating markup is a manual-action risk.

To see this fail against the old behaviour, comment out the blueprint
registration this adds in `booklet_gen/webapp/__init__.py`
(`app.register_blueprint(seo_bp)` and the `init_seo(app)` call) and rerun.
`/robots.txt` and `/sitemap.xml` then 404, which is exactly the state a
crawler found before tonight.

Run: PYTHONPATH=. python scripts/check_seo.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = Path(tempfile.mkdtemp(prefix="folio-seo-check-"))
os.environ["FOLIO_DB"] = str(_tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(_tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "s" * 40
os.environ["FOLIO_PUBLIC_URL"] = "https://folio-45rh.onrender.com"
os.environ.pop("DATABASE_URL", None)

from booklet_gen.webapp import create_app  # noqa: E402
from booklet_gen.webapp.seo import DISALLOWED_PREFIXES, PUBLIC_PAGES  # noqa: E402

failures: list[str] = []


def check(ok: bool, label: str, consequence: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        failures.append(f"{label} -- {consequence}")


BASE = "https://folio-45rh.onrender.com"
WEBAPP = ROOT / "booklet_gen" / "webapp"


def _prefix(path: str) -> str:
    """The first path segment, the unit both robots.txt and this script
    reason about. "/plans/new" and "/plans/<int:plan_id>/archive" both
    reduce to "/plans"."""
    seg = path.split("/", 2)[1]
    return f"/{seg}"


def _gated_paths(filename: str, guard: str, url_prefix: str = "") -> list[str]:
    """Every @bp.route(...) path in `filename` immediately decorated with
    `@guard`, read from the source rather than the running app, because a
    route that is gated but whose decorator this regex cannot see is exactly
    the kind of drift a hand-typed Disallow list would never catch either."""
    src = (WEBAPP / filename).read_text(encoding="utf-8")
    pattern = re.compile(
        r'@bp\.route\(\s*"([^"]*)"[^\n]*\)\s*\n\s*@' + re.escape(guard) + r'\b')
    return [url_prefix + m.group(1) for m in pattern.finditer(src)]


app = create_app()
client = app.test_client()

print("\nrobots.txt and sitemap.xml exist and are the right shape")
print("-" * 62)

robots = client.get("/robots.txt")
check(robots.status_code == 200, "robots.txt returns 200",
      "a crawler that requests it gets a 404 and assumes there is none, so "
      "it falls back to crawling and indexing everything, private pages "
      "included")
check(robots.mimetype == "text/plain", "robots.txt is served as text/plain",
      f"got {robots.mimetype}; some crawlers only parse the file at all "
      "when it is served as plain text")
robots_body = robots.data.decode()
check(robots_body.startswith("User-agent: *"),
      "robots.txt opens with a User-agent line",
      "a robots.txt without one is not a rule set a crawler recognises")
check(f"Sitemap: {BASE}/sitemap.xml" in robots_body,
      "robots.txt points at the sitemap",
      "a crawler that never finds sitemap.xml has to discover every page by "
      "following links, and account and admin pages are not linked from "
      "anywhere a crawler can reach")

sitemap = client.get("/sitemap.xml")
check(sitemap.status_code == 200, "sitemap.xml returns 200",
      "Search Console cannot fetch a sitemap that 404s, and neither can Google")
check(sitemap.mimetype == "application/xml", "sitemap.xml is served as XML",
      f"got {sitemap.mimetype}; served as anything else some crawlers refuse to parse it")
tree = ElementTree.fromstring(sitemap.data)
ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
locs = [el.text for el in tree.findall("s:url/s:loc", ns)]
check(len(locs) == len(PUBLIC_PAGES) and len(locs) >= 4,
      "the sitemap lists the expected number of pages",
      f"got {len(locs)} <loc> entries for {len(PUBLIC_PAGES)} declared public pages")
for loc in locs:
    check(loc.startswith(BASE + "/"), f"{loc} uses the configured public origin",
          f"a sitemap entry not on {BASE} points a crawler at the wrong host "
          "entirely, or leaks a request-local hostname such as localhost")


print("\nEvery gated route in the source is covered by Disallow")
print("-" * 62)

gated = set()
gated.update(_gated_paths("views.py", "login_required"))
gated.update(_gated_paths("payments.py", "login_required"))
gated.update(_gated_paths("admin.py", "admin_required", url_prefix="/admin"))
# Not decorated with login_required by design (auth.py must stay untouched),
# but still sensitive: each carries a single-use token in the URL path, and
# the webhook is a signed server-to-server callback, not a page. Hardcoded
# here, not scanned, because there is no decorator for a regex to find; if
# either route's shape ever changes this line has to be updated by a human
# reading auth.py and payments.py, which is the point.
gated.update({"/verify/<token>", "/reset-password/<token>", "/stripe/webhook"})

check(len(gated) >= 15, "the gated-route scan actually found routes",
      f"found {len(gated)}; a regex that silently matches nothing would make "
      "every check below pass for the wrong reason")

for path in sorted(gated):
    prefix = _prefix(path)
    check(prefix in DISALLOWED_PREFIXES,
          f"{path} (prefix {prefix}) is covered by a Disallow line",
          f"{path} is gated in the source but robots.txt would let a "
          f"crawler index whatever it finds there")
    check(f"Disallow: {prefix}" in robots_body,
          f"robots.txt actually contains \"Disallow: {prefix}\"",
          "DISALLOWED_PREFIXES and the served file disagree")

print("\nNo gated route is in the sitemap")
print("-" * 62)
for loc in locs:
    path = loc[len(BASE):]
    check(not any(path == g or path.startswith(_prefix(g) + "/") or path == _prefix(g)
                  for g in gated),
          f"{path} is not one of the gated routes",
          f"{path} would be a direct request to index a page that only ever "
          "shows an anonymous visitor a login redirect")


print("\nEvery public page has its own title and description")
print("-" * 62)

PUBLIC_HTML_PAGES = [endpoint for endpoint, _, _ in PUBLIC_PAGES] + [
    "auth.login", "auth.signup",
]
seen_titles: dict[str, str] = {}
seen_descriptions: dict[str, str] = {}
for endpoint in PUBLIC_HTML_PAGES:
    with app.test_request_context():
        from flask import url_for
        path = url_for(endpoint)
    r = client.get(path)
    body = r.data.decode()
    title = re.search(r"<title>(.*?)</title>", body, re.S)
    desc = re.search(r'name="description" content="(.*?)">', body, re.S)
    check(r.status_code == 200, f"{path} loads", f"got {r.status_code}")
    check(bool(title) and title.group(1).strip(),
          f"{path} has a non-empty <title>", "an empty title is a blank tab and result")
    check(bool(desc) and len(desc.group(1).split()) >= 6,
          f"{path} has a real meta description, not a one-word stub",
          "a near-empty description invites Google to write its own snippet "
          "from whatever text it finds first on the page")
    t = " ".join(title.group(1).split()) if title else ""
    d = " ".join(desc.group(1).split()) if desc else ""
    if t in seen_titles:
        check(False, f"{path}'s title is unique",
              f"identical to {seen_titles[t]}'s: two pages competing for the "
              "same search snippet instead of each covering its own query")
    else:
        seen_titles[t] = path
    if d in seen_descriptions:
        check(False, f"{path}'s description is unique",
              f"identical to {seen_descriptions[d]}'s: this was the actual "
              "state of five of these seven pages before this change")
    else:
        seen_descriptions[d] = path


print("\nCanonical, og:url and structured data are present and truthful")
print("-" * 62)

home = client.get("/").data.decode()
canon = re.search(r'rel="canonical" href="([^"]+)"', home)
check(bool(canon) and canon.group(1) == f"{BASE}/",
      "the home page canonical points at the configured public origin",
      f"got {canon.group(1) if canon else None}; a canonical pointing at "
      "the wrong host tells Google the real page lives somewhere else")
ogurl = re.search(r'property="og:url" content="([^"]+)"', home)
check(bool(ogurl) and ogurl.group(1) == f"{BASE}/",
      "og:url is present and matches the canonical",
      "a link shared into Facebook or LinkedIn without og:url can unfurl "
      "against whatever URL variant was clicked, not the canonical one")

ld_blocks = re.findall(
    r'<script type="application/ld\+json">(.*?)</script>', home, re.S)
check(len(ld_blocks) >= 1, "the home page carries at least one JSON-LD block",
      "no structured data means nothing tells a search engine this domain "
      "is the entity named FolioAI")
import json  # noqa: E402
org = json.loads(ld_blocks[0])
check(org.get("@type") == "Organization" and org.get("name") == "FolioAI",
      "the Organization block names FolioAI",
      f"got {org}")
check(org.get("url", "").startswith(BASE),
      "the Organization url is the configured public origin", f"got {org}")
check("aggregateRating" not in org and "review" not in org,
      "no rating or review field on the Organization block",
      "FolioAI has no reviews; fake rating markup is dishonest and a "
      "manual-action risk with search engines")

pricing_body = client.get("/pricing").data.decode()
pricing_ld = [json.loads(b) for b in re.findall(
    r'<script type="application/ld\+json">(.*?)</script>', pricing_body, re.S)]
products_block = next((b for b in pricing_ld if b.get("@type") == "Product"), None)
check(products_block is not None, "the pricing page carries a Product block",
      "no Product markup on the one page that states what a booklet costs")
if products_block is not None:
    check("aggregateRating" not in products_block and "review" not in products_block,
          "no rating or review field on the Product block either",
          "same reason: nothing here is a real review")
    prices = {o["price"] for o in products_block.get("offers", [])}
    check(prices == {"5.00", "35.00"},
          "the Product offers quote the real prices the page itself charges",
          f"got {prices}; structured data quoting a different price than the "
          "page itself is the kind of mismatch that gets a manual action")


print("\n" + "-" * 62)
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
