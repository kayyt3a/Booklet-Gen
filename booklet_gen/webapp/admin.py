"""Minimal owner support console for jobs and booklet-credit adjustments."""
from __future__ import annotations

import functools
import json
import logging
import os
import uuid

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db

log = logging.getLogger(__name__)
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


# The most an adjustment may move a balance in one go, either way. A refund
# of the largest pack is ten, so this is generous; it exists so a slipped key
# cannot strand an account at minus nine hundred.
MAX_ADJUSTMENT = 100
# A negative adjustment takes something a customer paid for, so the ledger
# entry has to say why in words a stranger could audit later. The default
# placeholder does not.
DEFAULT_REASON = "support adjustment"
MIN_REMOVAL_REASON = 8


@bp.route("/credits", methods=["POST"])
@admin_required
def credits():
    """Move a customer's balance either way, on the record.

    Removal used to be impossible: this clamped to a positive 1 to 100 and
    called grant_credits, which refuses anything else. So when a refund left
    unused credits behind, or a chargeback needed correcting by hand, there
    was nothing to do it with, and SUPPORT_PLAYBOOK.md had to tell the
    operator not to touch the database at all. The webhook now reverses
    automatically, but the manual path still has to exist for the cases a
    webhook cannot judge: a dispute we won, a refund issued outside Stripe, a
    correction to an earlier mistake.

    Audited means three things, all of them here: the ledger entry carries the
    reason and a reference naming the admin account that made it, a removal
    must state a reason rather than accept the placeholder, and the server log
    records who moved what for whom.
    """
    email = (request.form.get("email") or "").strip().lower()
    reason = (request.form.get("reason") or DEFAULT_REASON).strip()[:120]
    try:
        units = int(request.form.get("units") or "0")
    except ValueError:
        units = 0
    user = db.get_user_by_email(email)

    if user is None or units == 0 or abs(units) > MAX_ADJUSTMENT:
        flash(f"Enter an existing account and a non-zero adjustment between "
              f"-{MAX_ADJUSTMENT} and {MAX_ADJUSTMENT} credits.")
        return redirect(url_for("admin.index"))
    if units < 0 and (len(reason) < MIN_REMOVAL_REASON
                      or reason.lower() == DEFAULT_REASON):
        flash("Removing credits needs a specific reason for the record, such "
              "as the Stripe refund or dispute id.")
        return redirect(url_for("admin.index"))

    reference = f"admin:{g.user['id']}:{uuid.uuid4().hex}"
    db.adjust_credits(user["id"], units,
                      f"{reason} (admin {g.user['email']})", reference)
    balance = db.credit_balance(user["id"])
    log.warning("admin %s adjusted user=%s by %+d credits to %d: %s",
                g.user["email"], user["id"], units, balance, reason)
    flash(f"{'Added' if units > 0 else 'Removed'} {abs(units)} booklet "
          f"credits {'to' if units > 0 else 'from'} {email}. "
          f"Their balance is now {balance}.")
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
