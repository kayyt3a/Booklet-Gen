"""Folio web application (Flask).

A parent-facing app: sign up, pick Type / Year / Subject from dropdowns, and
download a generated booklet or a whole term plan. Free to use.

Run locally:
    export FLASK_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
    export GEMINI_API_KEY=...
    python -m booklet_gen.webapp

Serve in production with gunicorn (see Dockerfile):
    gunicorn "booklet_gen.webapp:create_app()"

FLASK_SECRET_KEY is not optional in a real deployment: it signs the session
cookie, so anyone who knows it can mint a cookie for any account. See
`_resolve_secret_key` below.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template

from ..dbpool import is_postgres
from .db import init_db
from .security import init_csrf

log = logging.getLogger(__name__)

# Values that have appeared in this repo, its docs or its example env file, so
# they are effectively public. Treated as "no key set" rather than as a key.
_PUBLISHED_SECRETS = frozenset({
    "dev-insecure-change-me",
    "change-me-to-a-long-random-string",
    "change-me",
    "changeme",
    "dev",
    "development",
    "secret",
    "test",
    "folio",
})

_SECRET_KEY_HELP = """
FLASK_SECRET_KEY is missing or set to a publicly known placeholder, and
DATABASE_URL is set, so this is a real deployment carrying real accounts.

Refusing to start. The session cookie is signed with this key: with a known
key anyone can forge a cookie for any user id and read every account's
booklets, which carry students' names.

Fix it on the host (Render: Dashboard > the folio service > Environment):

    FLASK_SECRET_KEY = {suggestion}

Then redeploy. Everyone currently logged in will be logged out once, which is
the point: any cookie signed with the old key stops working.
""".strip()


def _resolve_secret_key() -> str:
    """The session signing key, or a hard failure when running for real.

    A deployment that never set FLASK_SECRET_KEY used to fall back silently to
    a default that is committed to this repository. Nothing broke, nothing
    logged, and every session cookie was forgeable. So:

    * A real key is used as given.
    * No key, or a known placeholder, plus DATABASE_URL set (which means a
      real deployment with a real accounts database) raises at startup.
    * Otherwise this is a local checkout, so a random per-process key is
      generated. Logins do not survive a restart locally, which is a nudge to
      set the variable, and is far better than a shared known secret.
    """
    key = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
    if key and key.lower() not in _PUBLISHED_SECRETS:
        if len(key) < 32:
            log.warning(
                "FLASK_SECRET_KEY is only %d characters. Use at least 32 "
                "random characters.", len(key),
            )
        return key

    if is_postgres():
        raise RuntimeError(
            _SECRET_KEY_HELP.format(suggestion=secrets.token_urlsafe(48))
        )

    log.warning(
        "FLASK_SECRET_KEY is not set. Using a random key for this process "
        "only: logins will not survive a restart and will not work across "
        "multiple workers. Set FLASK_SECRET_KEY in your .env for a stable "
        "local session."
    )
    return secrets.token_urlsafe(48)


def _cookies_should_be_secure() -> bool:
    """Mark the session cookie Secure on a real deployment.

    Render serves the app over HTTPS, so this costs nothing there. It is off
    locally because http://127.0.0.1:5000 would otherwise never receive the
    cookie. FOLIO_COOKIE_SECURE overrides either way.
    """
    override = (os.environ.get("FOLIO_COOKIE_SECURE") or "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return is_postgres()


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = _resolve_secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        # Lax still sends the cookie on a top-level GET navigation, so links
        # into Folio keep working, but not on a cross-site form POST. That is
        # defence in depth behind the CSRF token, not a replacement for it.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_cookies_should_be_secure(),
    )
    app.config["OUTPUT_DIR"] = Path(os.environ.get("FOLIO_OUTPUT", "output"))
    app.config["OUTPUT_DIR"].mkdir(parents=True, exist_ok=True)

    init_db()
    init_csrf(app)

    # A deploy or an idle spin-down kills in-flight generation threads while
    # the row still says "running". Nothing would ever clear those, so the
    # user watches a spinner for ever. Settle them at boot.
    try:
        from .views import JOB_TIMEOUT_SECONDS
        from . import db as _db
        stale = _db.fail_stale_running_jobs(JOB_TIMEOUT_SECONDS)
        if stale:
            log.warning("marked %d stale running job(s) as failed at boot", stale)
    except Exception as e:  # never let housekeeping stop the app booting
        log.warning("stale job sweep failed at boot: %s", e)

    @app.errorhandler(400)
    def _bad_request(e):
        # A rejected CSRF token is usually just an expired session, so say so
        # in the app's own styling rather than showing a bare browser error.
        return render_template(
            "error.html",
            heading="That request could not be accepted",
            detail=getattr(e, "description", "The request was not valid."),
        ), 400

    @app.template_filter("timeago")
    def _timeago(ts) -> str:
        """Render a unix timestamp as a short relative time."""
        try:
            delta = int(time.time()) - int(ts)
        except (TypeError, ValueError):
            return ""
        if delta < 60:
            return "just now"
        if delta < 3600:
            n = delta // 60
            return f"{n} minute{'s' if n != 1 else ''} ago"
        if delta < 86400:
            n = delta // 3600
            return f"{n} hour{'s' if n != 1 else ''} ago"
        n = delta // 86400
        if n < 30:
            return f"{n} day{'s' if n != 1 else ''} ago"
        return datetime.fromtimestamp(int(ts)).strftime("%d %b %Y")

    from .auth import bp as auth_bp
    from .views import bp as views_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
