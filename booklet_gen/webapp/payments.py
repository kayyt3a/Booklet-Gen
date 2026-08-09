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
        # A paid page may not imply files are kept for ever. Read live so the
        # pricing page, the library and support all quote the one number.
        retention=db.FILE_RETENTION_PER_USER,
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

    intent = getattr(session, "payment_intent", None)
    intent_id = getattr(intent, "id", intent)
    db.record_payment_and_credit(
        session.id, user_id, product.key, product.units,
        getattr(session, "amount_total", None),
        getattr(session, "currency", None),
        str(intent_id) if intent_id else None,
    )
    if intent_id:
        # Fulfilment may have happened on an earlier delivery, before the
        # intent was stored at all. A refund can only be traced back to this
        # payment through the intent, so make sure it is on the row.
        db.attach_payment_intent(session.id, str(intent_id))
    customer = getattr(session, "customer", None)
    if customer and not user["stripe_customer_id"]:
        db.set_stripe_customer(user_id, str(customer))
    return user_id


class UnknownPayment(Exception):
    """A money-back event that names no FolioAI payment we can find."""


def _field(obj, key: str, default=None):
    """One field off a Stripe event object.

    stripe.StripeObject looks like a dict and indexes like one, but it has no
    .get: the attribute lookup falls through to __getattr__, which raises
    AttributeError for the name "get". Using .get here reads fine, passes any
    test written with plain dictionaries, and then throws on every real
    webhook Stripe sends.
    """
    try:
        return obj[key]
    except (KeyError, IndexError, TypeError, AttributeError):
        return default


def _payment_for_intent(payment_intent_id: str) -> dict:
    """The payment row a charge belongs to, asking Stripe only if we must."""
    payment = db.find_payment(payment_intent_id=payment_intent_id)
    if payment is not None:
        return payment
    # Payments recorded before the intent was stored have nothing to match on.
    # Stripe can still say which checkout session produced the intent, and
    # once it has, the row is repaired so this costs nothing next time.
    sessions = _stripe().checkout.Session.list(
        payment_intent=payment_intent_id, limit=1)
    for session in getattr(sessions, "data", []) or []:
        found = db.find_payment(session_id=session.id)
        if found is not None:
            db.attach_payment_intent(session.id, payment_intent_id)
            return found
    raise UnknownPayment(payment_intent_id)


def reverse_for_money_back(payment_intent_id: str, reversed_total: int,
                           status: str, reason: str, reference: str) -> int:
    """Claw back credits for a payment Stripe has taken the money back on."""
    payment = _payment_for_intent(payment_intent_id)
    removed = db.reverse_payment_credits(
        payment["checkout_session_id"], reversed_total, status, reason, reference)
    if removed:
        log.warning(
            "reversed %s booklet credits on user=%s payment=%s (%s)",
            removed, payment["user_id"], payment["checkout_session_id"], status)
    return removed


def _units_to_reverse(payment: dict, amount: int | None,
                      amount_returned: int | None) -> int:
    """How many of a pack's credits a partial money-back covers.

    Proportional and rounded to nearest, so a full refund takes the whole
    pack, half a ten-pack takes five, and a small goodwill refund on top of a
    delivered booklet takes nothing. Rounding up instead would let a five
    percent gesture cost a customer a whole booklet.
    """
    units = int(payment["units"])
    if not amount or amount <= 0:
        return units
    returned = max(0, min(int(amount_returned or 0), int(amount)))
    if returned >= int(amount):
        return units
    return int((units * returned + int(amount) // 2) // int(amount))


def handle_money_back(event) -> None:
    """Apply a refund or dispute to the credits its payment granted."""
    kind = event["type"]
    obj = event["data"]["object"]
    intent_id = _field(obj, "payment_intent")
    if not intent_id:
        raise UnknownPayment("event carries no payment intent")
    intent_id = str(intent_id)

    if kind == "charge.refunded":
        payment = _payment_for_intent(intent_id)
        units = _units_to_reverse(
            payment, _field(obj, "amount"), _field(obj, "amount_refunded"))
        status = "refunded" if units >= int(payment["units"]) else "partially_refunded"
        reverse_for_money_back(
            intent_id, units, status,
            f"Stripe refund on {payment['product_key']}",
            f"refund:{_field(obj, 'id') or intent_id}:{units}")
        return

    if kind == "charge.dispute.created":
        # The money is already gone from the account when this arrives, and a
        # chargeback is for the whole charge, so the whole pack comes back.
        payment = _payment_for_intent(intent_id)
        reverse_for_money_back(
            intent_id, int(payment["units"]), "disputed",
            f"Stripe chargeback on {payment['product_key']}",
            f"dispute:{_field(obj, 'id') or intent_id}")
        return

    if kind == "charge.dispute.closed":
        # Deliberately no automatic restore. If the dispute was won the money
        # stayed, and whether to hand the credits back to someone who filed a
        # chargeback is a judgement for a person, using the audited admin
        # adjustment. Logged so it is not silently forgotten.
        payment = _payment_for_intent(intent_id)
        log.warning(
            "dispute closed as %s on user=%s payment=%s: credits stay reversed "
            "until an admin adjustment says otherwise",
            _field(obj, "status"), payment["user_id"], payment["checkout_session_id"])


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
        "charge.refunded",
        "charge.dispute.created",
        "charge.dispute.closed",
    }:
        try:
            handle_money_back(event)
        except UnknownPayment:
            # A charge we have no record of. Nothing to reverse, and no retry
            # can produce one, so do not spend Stripe's retry budget on it.
            # Logged loudly because the alternative reading is that a payment
            # of ours failed to record, which is worth a look.
            log.exception("money-back event names no known payment, event=%s",
                          event["id"])
            return {"received": True, "reversed": False}, 200
        except Exception:
            # The database or Stripe was unavailable. Ask for a retry: unlike
            # fulfilment, dropping this one leaves credits with someone whose
            # money has already gone back.
            log.exception("credit reversal failed, will retry, event=%s",
                          event["id"])
            return {"received": True, "reversed": False}, 500
        return {"received": True}, 200

    if event["type"] in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }:
        try:
            fulfil_checkout(event["data"]["object"]["id"])
        except ValueError:
            # Permanent: no usable user reference, a deleted account, an
            # unknown product, or a price that is not ours. Retrying cannot
            # change any of those. Stripe retries a 5xx for about three days
            # and then disables the endpoint, which would silently stop
            # fulfilling everyone else's real purchases, so this is an
            # operational problem to chase from the log rather than something
            # to hand back to Stripe.
            log.exception("Stripe fulfilment permanently failed, event=%s",
                          event["id"])
            return {"received": True, "fulfilled": False}, 200
        except Exception:
            # Transient: the database or network was unavailable. Ask Stripe
            # to try again, which is exactly what a 5xx means to it.
            log.exception("Stripe fulfilment failed, will retry, event=%s",
                          event["id"])
            return {"received": True, "fulfilled": False}, 500
    return {"received": True}, 200
