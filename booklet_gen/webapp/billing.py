"""Stripe checkout for credit packs, plus the webhook that grants credits.

Configuration (environment):
    STRIPE_SECRET_KEY        sk_test_... or sk_live_...
    STRIPE_WEBHOOK_SECRET    whsec_...  (from the Stripe webhook dashboard)
    PUBLIC_BASE_URL          https://yourdomain.com  (for redirect URLs)

If STRIPE_SECRET_KEY is unset the billing pages still render but checkout is
disabled, so the rest of the app runs without a Stripe account during dev.
"""
from __future__ import annotations

import os

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, g, abort,
    current_app,
)

from . import db
from .auth import login_required
from .pricing import CREDIT_PACKS, get_pack

bp = Blueprint("billing", __name__)


def _stripe():
    """Return the configured stripe module, or None if not set up."""
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        return None
    try:
        import stripe
    except ImportError:
        return None
    stripe.api_key = key
    return stripe


def _base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", request.host_url.rstrip("/"))


@bp.route("/billing")
@login_required
def billing():
    return render_template(
        "billing.html",
        packs=CREDIT_PACKS.values(),
        stripe_enabled=_stripe() is not None,
        credits=g.user["credits"],
    )


@bp.route("/checkout/<pack_key>", methods=["POST"])
@login_required
def checkout(pack_key: str):
    pack = get_pack(pack_key)
    if not pack:
        abort(404)
    stripe = _stripe()
    if stripe is None:
        flash("Payments are not configured yet. Set STRIPE_SECRET_KEY to enable checkout.")
        return redirect(url_for("billing.billing"))

    base = _base_url()
    sess = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{
            "price_data": {
                "currency": "aud",
                "product_data": {"name": f"Folio {pack.name}: {pack.credits} credits"},
                "unit_amount": pack.price_cents,
            },
            "quantity": 1,
        }],
        # Tie the purchase to the user + pack so the webhook can grant credits.
        client_reference_id=str(g.user["id"]),
        metadata={"user_id": str(g.user["id"]), "credits": str(pack.credits)},
        success_url=f"{base}{url_for('billing.success')}?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base}{url_for('billing.billing')}",
    )
    return redirect(sess.url, code=303)


@bp.route("/billing/success")
@login_required
def success():
    """Fallback credit grant on redirect, in case the webhook is delayed.
    Idempotent: grant_payment only grants once per Stripe session id."""
    stripe = _stripe()
    session_id = request.args.get("session_id")
    if stripe and session_id:
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            if sess.get("payment_status") == "paid":
                credits = int(sess["metadata"]["credits"])
                uid = int(sess["metadata"]["user_id"])
                db.grant_payment(uid, session_id, credits)
        except Exception:
            pass
    return render_template("success.html", credits=g.user["credits"])


@bp.route("/webhook", methods=["POST"])
def webhook():
    """Stripe calls this after a successful payment. Verifies the signature,
    then grants credits idempotently."""
    stripe = _stripe()
    if stripe is None:
        abort(503)
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        if secret:
            event = stripe.Webhook.construct_event(payload, sig, secret)
        else:
            event = stripe.Event.construct_from(request.get_json(force=True), stripe.api_key)
    except Exception:
        abort(400)

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        if sess.get("payment_status") == "paid":
            meta = sess.get("metadata") or {}
            try:
                db.grant_payment(int(meta["user_id"]), sess["id"], int(meta["credits"]))
            except (KeyError, ValueError):
                pass
    return "", 200
