"""robots.txt, sitemap.xml, canonical URLs and structured data.

Why this file exists: the founder searched "folio ai tutors you" and the site
did not come up at all, only the direct link worked. Two separate problems
were behind that, and this fixes both without touching anything that reads a
session, a credit balance, or a Stripe object.

1. Nothing told a crawler what to index. There was no robots.txt and no
   sitemap.xml, so a search engine that found the site at all had no signal
   for which of its ~25 routes are the two or three pages that actually
   explain what FolioAI is, and which are per-account pages that only ever
   render a login redirect for a crawler anyway (`/library`, `/account`,
   `/admin`, and so on). Crawl budget for a small, brand-new site is not
   unlimited, so pointing it at the home page, pricing, and the policy pages
   is what actually gets those pages seen and ranked.
2. There was no structured data, so even a query containing the exact brand
   name had nothing to key an Organization-style result off. `Organization`
   JSON-LD is the standard signal for "this domain is the entity named X".

Both `robots.txt` and `sitemap.xml` are generated, not static files, because
the base URL is only known at request time (or from FOLIO_PUBLIC_URL) and
because the disallow list and the sitemap should be built from one list of
routes each, not typed out twice and left to drift.
"""
from __future__ import annotations

import os

from flask import Blueprint, Response, request, url_for

from .public import _business

bp = Blueprint("seo", __name__)

# Every prefix below is a route that is either @login_required (so an
# anonymous crawler only ever receives a redirect to /login and there is
# nothing there worth indexing) or, for /verify and /reset-password, carries a
# single-use token in the URL path itself that a crawl must never touch.
# Keeping this list next to the route modules it mirrors, rather than
# reconstructing it from Flask's url_map, is deliberate: it is meant to be
# read and checked by a human against views.py, auth.py, admin.py and
# payments.py, not generated in a way nobody re-reads.
DISALLOWED_PREFIXES = (
    "/account",         # views.py: usage stats and deletion for one account
    "/admin",           # admin.py: the owner support console
    "/cancel",          # views.py: POST-only, stops one account's booklet
    "/checkout",        # payments.py: checkout and checkout/success
    "/download",        # views.py: one account's generated file
    "/feedback",        # views.py: a rating form scoped to one account's job
    "/generate",        # views.py: POST-only, spends a credit
    "/library",         # views.py: this account's booklets
    "/plans",           # views.py: this account's study plans
    "/progress",        # views.py: this account's job status page
    "/reset-password",  # auth.py: carries a single-use token in the path
    "/retry",           # views.py: POST-only, spends a credit
    "/status",          # views.py: this account's job status, polled by JS
    "/stripe",          # payments.py: the webhook, not a page
    "/verify",          # auth.py: carries a single-use token in the path
)

# The pages worth a search engine's attention: the ones that explain what
# FolioAI is and cost nothing to render for a visitor who is not signed in.
# (endpoint, changefreq, priority)
PUBLIC_PAGES = (
    ("views.index", "weekly", "1.0"),
    ("payments.pricing", "monthly", "0.8"),
    ("public.support", "yearly", "0.3"),
    ("public.privacy", "yearly", "0.3"),
    ("public.terms", "yearly", "0.3"),
)


def public_base_url() -> str:
    """The externally-reachable origin for this deployment, no trailing slash.

    Reuses FOLIO_PUBLIC_URL, the same variable payments.py already reads to
    build Stripe's success and cancel URLs (see DEPLOY.md). Adding a second
    "what is our real domain" variable would be one more thing to forget on
    Render; this one is already required there. Locally, or if it is ever
    unset on a real deploy, the current request's own host is a safer default
    than a hard-coded hostname, because it is never wrong for the request that
    is actually being served.
    """
    configured = (os.environ.get("FOLIO_PUBLIC_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return request.host_url.rstrip("/")


def public_url(endpoint: str, **values) -> str:
    return public_base_url() + url_for(endpoint, **values)


def organization_data() -> dict:
    """Organization JSON-LD, sitewide. Keeps `public.py`'s business identity
    as the one source for the name and support email, so this can never say
    something the Privacy, Terms and Support pages do not."""
    business = _business()
    return {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": business["name"],
        "url": public_base_url() + "/",
        "logo": public_base_url() + url_for("static", filename="img/brand/icon-512.png"),
        "description": (
            "FolioAI generates printable practice booklets for Years 1 to 10 "
            "in Australia: a mini-lesson, a worked example, practice "
            "questions, a cumulative Final Challenge, and a verified answer "
            "key."
        ),
        "email": business["email"],
    }


@bp.route("/robots.txt")
def robots_txt():
    lines = ["User-agent: *"]
    lines += [f"Disallow: {prefix}" for prefix in DISALLOWED_PREFIXES]
    lines += ["", f"Sitemap: {public_url('seo.sitemap_xml')}"]
    return Response("\n".join(lines) + "\n", mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap_xml():
    entries = "\n".join(
        "  <url>\n"
        f"    <loc>{public_url(endpoint)}</loc>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        "  </url>"
        for endpoint, changefreq, priority in PUBLIC_PAGES
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )
    return Response(body, mimetype="application/xml")


def init_seo(app) -> None:
    """Expose the URL and structured-data helpers to every template."""
    app.jinja_env.globals["public_url"] = public_url
    app.jinja_env.globals["public_base_url"] = public_base_url
    app.jinja_env.globals["organization_data"] = organization_data
