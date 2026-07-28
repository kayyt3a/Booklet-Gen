"""Folio web application (Flask).

A parent-facing app: sign up, pick Type / Year / Subject from dropdowns, and
download a generated booklet or a whole term plan. Free to use.

Run locally:
    export FLASK_SECRET_KEY=dev  GEMINI_API_KEY=...
    python -m booklet_gen.webapp

Serve in production with gunicorn (see Dockerfile):
    gunicorn "booklet_gen.webapp:create_app()"
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from flask import Flask

from .db import init_db


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-change-me")
    app.config["OUTPUT_DIR"] = Path(os.environ.get("FOLIO_OUTPUT", "output"))
    app.config["OUTPUT_DIR"].mkdir(parents=True, exist_ok=True)

    init_db()

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
