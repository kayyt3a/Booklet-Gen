"""FolioAI web application (Flask).

A parent-facing app: sign up, buy booklet credits, choose a program and year,
and download a generated booklet or a whole term plan.

Run locally:
    export FLASK_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
    export GEMINI_API_KEY=...
    python -m flask --app booklet_gen.webapp:create_app run

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
from datetime import timedelta
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

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


def _validate_runtime_config() -> None:
    require_postgres = (os.environ.get("FOLIO_REQUIRE_POSTGRES") or "").strip().lower()
    # Read the mode views actually runs on rather than parsing the variable a
    # second time. This used to default to "inline" here and "auto" in
    # views.py. Nothing behaved differently, because both files only ever
    # compare the value against "queue", but two defaults for one setting is a
    # trap for the next change, and it made "what mode is this deployment in?"
    # a question with two answers.
    from .views import JOB_MODE as queue_mode
    if (require_postgres in {"1", "true", "yes", "on"} or queue_mode == "queue") \
            and not is_postgres():
        raise RuntimeError(
            "This FolioAI configuration requires Postgres, but DATABASE_URL is "
            "missing. Queue mode cannot share a local SQLite file between the "
            "web and worker services."
        )


def create_app() -> Flask:
    _validate_runtime_config()
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = _resolve_secret_key()
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SESSION_COOKIE_NAME="folio_session",
        SESSION_COOKIE_HTTPONLY=True,
        # Lax still sends the cookie on a top-level GET navigation, so links
        # into FolioAI keep working, but not on a cross-site form POST. That is
        # defence in depth behind the CSRF token, not a replacement for it.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_cookies_should_be_secure(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
        PREFERRED_URL_SCHEME="https",
    )
    app.config["OUTPUT_DIR"] = Path(os.environ.get("FOLIO_OUTPUT", "output"))
    app.config["OUTPUT_DIR"].mkdir(parents=True, exist_ok=True)

    init_db()
    init_csrf(app)
    from .commerce import validate_commerce_config
    from .mailer import validate_mail_config
    validate_commerce_config()
    validate_mail_config()

    # A deploy or an idle spin-down kills in-flight generation threads while
    # the row still says "running". Nothing would ever clear those, so the
    # user watches a spinner for ever. Settle them at boot.
    #
    # Deliberately not "fail everything that says running", tempting as that
    # is here: in queue mode the worker is a separate service that this boot
    # knows nothing about, and its jobs are legitimately still generating. The
    # heartbeat is what tells the two apart, so this sweep uses the same rule
    # as every other one.
    try:
        from .views import JOB_HEARTBEAT_MAX_AGE, JOB_TIMEOUT_SECONDS
        from . import db as _db
        stale = _db.fail_stale_running_jobs(JOB_TIMEOUT_SECONDS,
                                            JOB_HEARTBEAT_MAX_AGE)
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

    @app.errorhandler(429)
    def _too_many_requests(e):
        return render_template(
            "error.html",
            heading="Please wait before trying again",
            detail=getattr(e, "description", "Too many requests were made."),
        ), 429

    @app.errorhandler(404)
    def _not_found(_e):
        return render_template(
            "error.html",
            heading="We could not find that page",
            detail="The link may be old, or the page may belong to another account.",
        ), 404

    @app.errorhandler(500)
    def _server_error(_e):
        return render_template(
            "error.html",
            heading="FolioAI hit an unexpected problem",
            detail=("Nothing else is needed from you right now. Try again, or "
                    "contact support if the problem continues."),
        ), 500

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

    # The covers a booklet is delivered with, available to every template.
    # They are the product's strongest asset and they appeared nowhere on the
    # site, so a visitor could not see what they would be handed until after
    # they had paid for it. See booklet_gen/webapp/covers.py.
    from .covers import SAMPLES as _cover_samples, cover_for

    app.jinja_env.globals["cover_samples"] = _cover_samples
    app.template_filter("cover_for")(cover_for)

    # Who is taking the money, available to every template rather than only to
    # the three pages public.py renders. The site footer needs it, and reading
    # it from the same place Terms, Privacy and Support read it is what stops
    # the footer stating an identity the legal pages contradict. Flask reapplies
    # the caller's own context over a processor's, so public.py's explicit
    # `business=` argument still wins on those three pages.
    from .public import _business

    app.context_processor(lambda: {"business": _business()})

    from .admin import bp as admin_bp, is_admin
    from .auth import bp as auth_bp
    from .payments import bp as payments_bp
    from .public import bp as public_bp
    from .seo import bp as seo_bp, init_seo
    from .views import bp as views_bp

    app.jinja_env.globals["is_admin"] = is_admin
    init_seo(app)
    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(payments_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(seo_bp)
    app.register_blueprint(views_bp)

    @app.get("/healthz")
    def _healthz():
        try:
            from . import db as _db
            if not _db.health_check():
                return {"status": "error"}, 503
            # In queue mode a healthy database is not a healthy service: the
            # web dyno only enqueues, so with no worker behind it every booklet
            # hangs. Report that here or the outage is invisible to monitoring.
            from .views import JOB_MODE, WORKER_HEARTBEAT_MAX_AGE
            if JOB_MODE == "queue":
                worker = _db.worker_status(WORKER_HEARTBEAT_MAX_AGE)
                if worker["status"] != "healthy":
                    return {"status": "degraded",
                            "worker": worker["status"]}, 503
                return {"status": "ok", "worker": "healthy"}, 200
            return {"status": "ok"}, 200
        except Exception:
            log.exception("health check failed")
            return {"status": "error"}, 503

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' "
            "'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; "
            # The checkout form posts to our own /checkout/<key>, but that
            # handler's whole job is a 303 to a Stripe-hosted page. Chrome
            # enforces form-action against that final redirect target, not
            # just the form's own action, so 'self' alone silently blocks
            # the one redirect this page exists to make.
            "form-action 'self' https://checkout.stripe.com",
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains",
            )
        if request.endpoint != "static":
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
