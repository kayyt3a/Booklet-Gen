"""Stripe Checkout, signed webhook fulfilment, and customer purchase pages."""
from __future__ import annotations

import logging
import os

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import db
from .auth import login_required
from .commerce import (
    payments_enabled, products, stripe_secret_key, stripe_webhook_secret,
)
from .security import csrf_exempt
from .security import enforce_rate_limit

log = logging.getLogger(__name__)
bp = Blueprint("payments", __name__)


def _stripe():
    try:
        import stripe
    except ImportError as exc:
        raise RuntimeError("The Stripe Python package is not installed.") from exc
    stripe.api_key = stripe_secret_key()
    return stripe


def _public_url(endpoint: str, **values) -> str:
    configured = (os.environ.get("FOLIO_PUBLIC_URL") or "").strip().rstrip("/")
    path = url_for(endpoint, **values)
    return configured + path if configured else url_for(endpoint, _external=True, **values)


@bp.route("/pricing")
def pricing():
    return render_template(
        "pricing.html",
        products=products(),
        payments_enabled=payments_enabled(),
        credits=db.credit_balance(g.user["id"]) if g.user else None,
    )


@bp.route("/checkout/<product_key>", methods=["POST"])
@login_required
def checkout(product_key: str):
    enforce_rate_limit("checkout", 12, 900)
    catalog = products()
    product = catalog.get(product_key)
    if product is None:
        abort(404)
    if not payments_enabled():
        flash("Payments are not configured yet. No charge was made.")
        return redirect(url_for("payments.pricing"))

    stripe = _stripe()
    values = {
        "mode": "payment",
        "line_items": [{"price": product.price_id, "quantity": 1}],
        "client_reference_id": str(g.user["id"]),
        "metadata": {"product_key": product.key},
        "success_url": _public_url("payments.success")
                       + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": _public_url("payments.pricing") + "?cancelled=1",
        "allow_promotion_codes": True,
    }
    customer_id = (g.user["stripe_customer_id"] or "").strip()
    if customer_id:
        values["customer"] = customer_id
    else:
        values["customer_email"] = g.user["email"]
        values["customer_creation"] = "always"
    session = stripe.checkout.Session.create(**values)
    return redirect(session.url, code=303)


def fulfil_checkout(session_id: str) -> int | None:
    """Grant purchased credits exactly once after server-side verification."""
    if not payments_enabled():
        return None
    stripe = _stripe()
    session = stripe.checkout.Session.retrieve(
        session_id, expand=["line_items"],
    )
    if session.payment_status not in {"paid", "no_payment_required"}:
        return False
    try:
        user_id = int(session.client_reference_id)
    except (TypeError, ValueError):
        raise ValueError("Checkout session has no valid FolioAI user reference.")
    user = db.get_user(user_id)
    if user is None:
        raise ValueError("Checkout session belongs to an account that no longer exists.")

    product_key = (session.metadata or {}).get("product_key", "")
    product = products().get(product_key)
    if product is None:
        raise ValueError("Checkout session has an unknown FolioAI product.")
    line_items = list(getattr(session.line_items, "data", []) or [])
    price_ids = {getattr(getattr(item, "price", None), "id", None) for item in line_items}
    if product.price_id not in price_ids:
        raise ValueError("Checkout price does not match the FolioAI product.")

    db.record_payment_and_credit(
        session.id, user_id, product.key, product.units,
        getattr(session, "amount_total", None),
        getattr(session, "currency", None),
    )
    customer = getattr(session, "customer", None)
    if customer and not user["stripe_customer_id"]:
        db.set_stripe_customer(user_id, str(customer))
    return user_id


@bp.route("/checkout/success")
@login_required
def success():
    session_id = (request.args.get("session_id") or "").strip()
    if not session_id:
        return redirect(url_for("payments.pricing"))
    try:
        owner_id = fulfil_checkout(session_id)
    except Exception:
        log.exception("checkout fulfilment failed on success page")
        owner_id = None
    if owner_id != int(g.user["id"]):
        flash("That payment could not be confirmed for this account. No new "
              "charge was made here. Contact support if you have a receipt.")
        return redirect(url_for("payments.pricing"))
    return render_template(
        "payment_success.html",
        credits=db.credit_balance(g.user["id"]),
    )


@bp.route("/stripe/webhook", methods=["POST"])
@csrf_exempt
def webhook():
    if not payments_enabled():
        abort(503)
    stripe = _stripe()
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            request.get_data(cache=False), signature, stripe_webhook_secret(),
        )
    except (ValueError, getattr(
            getattr(stripe, "error", stripe),
            "SignatureVerificationError", ValueError)):
        abort(400)

    if event["type"] in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        try:
            fulfil_checkout(event["data"]["object"]["id"])
        except Exception:
            log.exception("Stripe fulfilment failed")
            return {"received": True, "fulfilled": False}, 500
    return {"received": True}, 200
