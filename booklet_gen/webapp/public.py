"""Public policy and customer-support pages."""
from __future__ import annotations

import os

from flask import Blueprint, render_template

bp = Blueprint("public", __name__)


def _business() -> dict[str, str]:
    return {
        "name": (os.environ.get("FOLIO_BUSINESS_NAME") or "FolioAI").strip(),
        "email": (os.environ.get("FOLIO_SUPPORT_EMAIL") or "support@example.com").strip(),
        "country": (os.environ.get("FOLIO_BUSINESS_COUNTRY") or "Australia").strip(),
        "number": (os.environ.get("FOLIO_BUSINESS_NUMBER") or "").strip(),
        "address": (os.environ.get("FOLIO_BUSINESS_ADDRESS") or "").strip(),
        "phone": (os.environ.get("FOLIO_SUPPORT_PHONE") or "").strip(),
    }


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html", business=_business())


@bp.route("/terms")
def terms():
    return render_template("terms.html", business=_business())


@bp.route("/support")
def support():
    # Support told customers FolioAI keeps "only your most recent generated
    # files" with no number, while pricing implied they were kept for ever.
    # Both now quote db.FILE_RETENTION_PER_USER, which is what the cleanup in
    # db.save_job_file actually enforces.
    from .db import FILE_RETENTION_PER_USER
    return render_template("support.html", business=_business(),
                           retention=FILE_RETENTION_PER_USER)
