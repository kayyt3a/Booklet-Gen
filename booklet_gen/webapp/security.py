"""Small, dependency-free security helpers for the Folio web app.

Two things live here:

* **CSRF protection.** A synchroniser token kept in the signed session and
  echoed back in every state-changing request. `/generate` spends the owner's
  Gemini budget and burns the victim's daily quota, so a page on another site
  must not be able to trigger it just because the victim is logged in. This is
  deliberately a few dozen lines rather than a new framework dependency.
* **Redirect target validation.** The login form carries a `next` parameter,
  and an unvalidated one turns the login page into an open redirect that a
  phishing link can point at another site.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from urllib.parse import urlparse

from flask import abort, request, session

log = logging.getLogger(__name__)

CSRF_SESSION_KEY = "_csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

# Everything else (POST, PUT, PATCH, DELETE) has to carry a token.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def csrf_token() -> str:
    """The current session's CSRF token, minted on first use.

    Exposed to Jinja as `csrf_token()` so templates can drop it into a hidden
    field. Reading it marks the session as modified, which is what causes the
    cookie to be set on a visitor who has not logged in yet.
    """
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field() -> str:
    """The hidden input, ready to paste into a form."""
    from markupsafe import Markup
    return Markup(
        f'<input type="hidden" name="{CSRF_FORM_FIELD}" value="{csrf_token()}">'
    )


def _submitted_token() -> str:
    return (request.form.get(CSRF_FORM_FIELD)
            or request.headers.get(CSRF_HEADER)
            or "")


def init_csrf(app) -> None:
    """Require a valid token on every state-changing request."""
    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["csrf_field"] = csrf_field

    @app.before_request
    def _verify_csrf():
        if request.method in _SAFE_METHODS:
            return None
        expected = session.get(CSRF_SESSION_KEY) or ""
        submitted = _submitted_token()
        if not expected or not submitted or not hmac.compare_digest(
                str(expected), str(submitted)):
            log.warning(
                "csrf.rejected path=%s origin=%s referer=%s",
                request.path,
                request.headers.get("Origin", "-"),
                request.headers.get("Referer", "-"),
            )
            abort(400, description=(
                "Your session expired or the request did not come from Folio. "
                "Go back, reload the page, and try again."
            ))
        return None


def rotate_csrf_token() -> str:
    """Mint a fresh token. Called when the session identity changes."""
    session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_SESSION_KEY]


def safe_redirect_target(target: str | None, fallback: str) -> str:
    """Return `target` only if it is a same-site relative path.

    Anything absolute, protocol-relative (`//evil.example`), backslash-prefixed
    (browsers normalise `/\\evil.example` to a protocol-relative URL), or
    carrying a newline gets replaced by the fallback.
    """
    if not target:
        return fallback
    candidate = target.strip()
    if not candidate or any(c in candidate for c in ("\r", "\n", "\t", "\x00")):
        return fallback
    if not candidate.startswith("/"):
        return fallback
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return fallback
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return fallback
    return candidate
