"""Folio web application (Flask).

A parent-facing app: sign up, buy credits, pick Type / Year / Subject from
dropdowns, and download a generated booklet or a whole term plan.

Run locally:
    export FLASK_SECRET_KEY=dev  GEMINI_API_KEY=...
    python -m booklet_gen.webapp

Serve in production with gunicorn (see Dockerfile):
    gunicorn "booklet_gen.webapp:create_app()"
"""
from __future__ import annotations

import os
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

    from .auth import bp as auth_bp
    from .views import bp as views_bp
    from .billing import bp as billing_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(views_bp)
    app.register_blueprint(billing_bp)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False)
