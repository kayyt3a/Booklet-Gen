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
from ..pipeline import MIN_CLASSWORK_TOPICS, MIN_NOW_YOU_TRY
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


def _managed_payments_wanted() -> bool:
    """Whether to let Stripe be the merchant of record for this sale.

    Off unless asked for. See the note at the call site: it costs 3.5% per
    transaction and requires a tax code on every product.
    """
    return (os.environ.get("FOLIO_STRIPE_MANAGED_PAYMENTS") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


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
        # Read live from the pipeline for the same reason. A floor advertised
        # on the page that the generator does not enforce is a promise to a
        # paying customer that nothing keeps, and these two numbers have to
        # move together or not at all.
        min_topics=MIN_CLASSWORK_TOPICS,
        min_practice=MIN_NOW_YOU_TRY,
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
        # Stripe turns Managed Payments on by default for new accounts, which
        # makes Stripe the merchant of record, adds 3.5% per transaction on top
        # of the normal fee, and refuses any line item whose product carries no
        # tax code. FolioAI's products carry none, so every live checkout
        # failed with a 500:
        #
        #   Invalid line_items[0]: the product tax code is missing ... Product
        #   tax code is required for Managed Payments, which is enabled by
        #   default on your account.
        #
        # There is no off switch in the dashboard: that settings page is a
        # sign-up flow, not a toggle. So it is declined per session, which is
        # the mechanism Stripe's own error message points at. Declining here
        # rather than relying on a dashboard default also means Stripe changing
        # that default again cannot silently take checkout down.
        #
        # This is a pricing and tax decision, not a technical one. 3.5% on a
        # five dollar booklet is most of the margin, and being your own
        # merchant of record is the arrangement the Terms and the GST position
        # were written for. Set FOLIO_STRIPE_MANAGED_PAYMENTS=1 to opt in, and
        # give both products a tax code first or checkout will fail again.
        "managed_payments": {"enabled": _managed_payments_wanted()},
    }
    customer_id = (g.user["stripe_customer_id"] or "").strip()
    if customer_id:
        values["customer"] = customer_id
    else:
        _create_customer(values, g.user["email"])
    try:
        session = stripe.checkout.Session.create(**values)
    except Exception as exc:
        # A stored customer id this Stripe account has never heard of. The
        # first live launch is where this bites: every account that tested
        # checkout carries a TEST mode cus_..., Stripe rejects it outright
        # under a live key, and the customer gets a 500 on the one page whose
        # whole job is taking their money. It also happens whenever a customer
        # is deleted in the Stripe dashboard.
        #
        # The id is worthless either way, so forget it and let Stripe make a
        # new one from the email address, which is what an account with no id
        # already does. Falling back rather than raising means one stale row
        # costs a moment, not a sale.
        if not (customer_id and _customer_is_unknown(exc)):
            raise
        log.warning("stored Stripe customer %s is unknown to this account; "
                    "creating a new one", customer_id)
        db.set_stripe_customer(int(g.user["id"]), None)
        values.pop("customer", None)
        _create_customer(values, g.user["email"])
        session = stripe.checkout.Session.create(**values)
    return redirect(session.url, code=303)


def _create_customer(values: dict, email: str) -> None:
    """Have Stripe make a fresh customer for this checkout."""
    values["customer_email"] = email
    values["customer_creation"] = "always"


def _customer_is_unknown(exc: Exception) -> bool:
    """Whether Stripe refused the request because the customer is not there.

    Matched on the message rather than the type: stripe raises the same
    InvalidRequestError for a bad price, a bad currency and half a dozen other
    things, and retrying those without the customer would send the customer
    round the same failure twice. `param` is the reliable half of the message
    and the text is the backstop.
    """
    if getattr(exc, "param", None) == "customer":
        return True
    message = str(getattr(exc, "user_message", None) or exc)
    return "No such customer" in message


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

    product_key = _field(_field(session, "metadata", {}) or {}, "product_key", "")
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
