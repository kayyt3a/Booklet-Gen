"""Minimal owner support console for jobs and booklet-credit adjustments."""
from __future__ import annotations

import functools
import json
import os
import uuid

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db

bp = Blueprint("admin", __name__, url_prefix="/admin")


def is_admin(user=None) -> bool:
    user = g.user if user is None else user
    if user is None:
        return False
    allowed = {
        value.strip().lower()
        for value in (os.environ.get("FOLIO_ADMIN_EMAILS") or "").split(",")
        if value.strip()
    }
    return user["email"].strip().lower() in allowed


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            abort(404)
        return view(*args, **kwargs)
    return wrapped


@bp.route("")
@admin_required
def index():
    return render_template("admin.html", jobs=db.list_recent_jobs(100))


@bp.route("/credits", methods=["POST"])
@admin_required
def credits():
    email = (request.form.get("email") or "").strip().lower()
    reason = (request.form.get("reason") or "support adjustment").strip()[:120]
    try:
        units = int(request.form.get("units") or "0")
    except ValueError:
        units = 0
    user = db.get_user_by_email(email)
    if user is None or units < 1 or units > 100:
        flash("Enter an existing account and between 1 and 100 credits.")
    else:
        reference = f"admin:{g.user['id']}:{uuid.uuid4().hex}"
        db.grant_credits(user["id"], units, reason, reference)
        flash(f"Added {units} booklet credits to {email}.")
    return redirect(url_for("admin.index"))

@bp.route("/retry/<job_id>", methods=["POST"])
@admin_required
def retry(job_id: str):
    original = db.get_job(job_id)
    if original is None or original["status"] != "error" or not original["request_json"]:
        abort(404)
    try:
        args = json.loads(original["request_json"])
    except (TypeError, ValueError):
        abort(400)
    new_id = uuid.uuid4().hex
    db.enqueue_job(
        new_id, original["user_id"], original["label"], original["units"],
        args, reserve_credits=False,
    )
    from .views import _dispatch_job
    _dispatch_job(new_id, args)
    flash("A no-charge support retry was queued.")
    return redirect(url_for("admin.index"))
