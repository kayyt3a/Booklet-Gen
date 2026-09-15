"""Checks that a stale Stripe customer id cannot break the checkout page.

FolioAI's first live launch failed on the first click. The account had tested
checkout in Stripe test mode, so `users.stripe_customer_id` held a test-mode
`cus_...`, and `checkout()` passed it to a live API call:

    stripe._error.InvalidRequestError: No such customer: 'cus_V4tqYrpZj0I4gl';
    a similar object exists in test mode, but a live mode key was used to make
    this request.

    "POST /checkout/single HTTP/1.1" 500

A 500 on the one page whose whole job is taking money, for every account that
had ever tested a purchase. The id is worthless in live mode, and an account
carrying NO id already works fine: Stripe makes a customer from the email
address. So the fix is to forget the id and do exactly that.

Going live is not the only way in. Deleting a customer in the Stripe dashboard
strips the id's meaning the same way, and so does moving to a different Stripe
account.

The retry is deliberately narrow. `InvalidRequestError` is also what Stripe
raises for a bad price, a bad currency and much else; retrying those without
the customer would walk the customer into the same failure twice and log it as
a customer problem. So only a fault that names the customer is retried, and
everything else is raised as before.

    PYTHONPATH=. python scripts/check_stripe_customer_fallback.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("DATABASE_URL", None)
os.environ["FOLIO_DB"] = str(Path(tempfile.mkdtemp()) / "stripe.sqlite")
os.environ.update({
    "STRIPE_SECRET_KEY": "sk_live_fake_for_this_check",
    "STRIPE_WEBHOOK_SECRET": "whsec_fake_for_this_check",
    "STRIPE_PRICE_SINGLE": "price_single_live",
    "STRIPE_PRICE_TERM": "price_term_live",
    "FOLIO_PUBLIC_URL": "https://folioaitutorsyou.com",
})
logging.disable(logging.CRITICAL)

from booklet_gen.webapp import db, payments, security             # noqa: E402

PASSED = 0
TOTAL = 0


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


class StripeError(Exception):
    """Stands in for stripe.InvalidRequestError, which carries a `param`."""

    def __init__(self, message: str, param: str | None = None):
        super().__init__(message)
        self.param = param
        self.user_message = message


# The exact message from the live launch, and the shapes it takes elsewhere.
LIVE_LAUNCH = StripeError(
    "Request req_7Mt9ya0r6dJCKO: No such customer: 'cus_V4tqYrpZj0I4gl'; a "
    "similar object exists in test mode, but a live mode key was used to make "
    "this request.", param="customer")
DELETED_CUSTOMER = StripeError("No such customer: 'cus_gone'", param="customer")
UNPARAMED = StripeError("No such customer: 'cus_gone'")
BAD_PRICE = StripeError("No such price: 'price_wrong'", param="line_items[0][price]")
CARD_DECLINED = StripeError("Your card was declined.", param=None)


print("\n== the fault is recognised, and only when it is that fault ==")

for exc, label in ((LIVE_LAUNCH, "the message from the real live launch"),
                   (DELETED_CUSTOMER, "a customer deleted in the dashboard"),
                   (UNPARAMED, "the same fault with no param set")):
    check(payments._customer_is_unknown(exc), f"recognised: {label}",
          "the checkout page will 500 instead of recovering, on the one page "
          "whose job is taking money")

for exc, label in ((BAD_PRICE, "a price that does not exist"),
                   (CARD_DECLINED, "a declined card")):
    check(not payments._customer_is_unknown(exc), f"not retried: {label}",
          "an unrelated fault is being retried without the customer, which "
          "sends the customer into the same failure a second time and files it "
          "under the wrong cause")


print("\n== a stale id is dropped and the sale goes through ==")

db.init_db()
user_id = db.create_user("stale@example.com", "not-a-real-password")
if not isinstance(user_id, int):
    user_id = db.get_user_by_email("stale@example.com")["id"]
db.set_stripe_customer(user_id, "cus_V4tqYrpZj0I4gl")

calls: list[dict] = []


class FakeSession:
    url = "https://checkout.stripe.com/c/pay/fake"

    @staticmethod
    def create(**values):
        calls.append(values)
        # First attempt carries the stale customer and is refused, exactly as
        # the live Stripe API refused it.
        if values.get("customer") == "cus_V4tqYrpZj0I4gl":
            raise LIVE_LAUNCH
        return FakeSession()


class FakeStripe:
    class checkout:
        Session = FakeSession


payments._stripe = lambda: FakeStripe

from booklet_gen.webapp import create_app                          # noqa: E402

app = create_app()
app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False,
                  SECRET_KEY="check", SESSION_COOKIE_SECURE=False)

# The app verifies its own CSRF token against the session, so the test has to
# agree with itself rather than switch the protection off. Disabling it would
# also stop this check noticing if checkout ever lost its token.
CSRF = "token-for-this-check"


def post_checkout(client, product="single"):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session[security.CSRF_SESSION_KEY] = CSRF
    return client.post(f"/checkout/{product}", data={"csrf_token": CSRF})

with app.test_client() as client:
    response = post_checkout(client)

check(response.status_code == 303,
      f"the checkout answered {response.status_code}, a redirect to Stripe",
      "the customer got an error page rather than a payment page. This is the "
      "exact 500 the live launch produced")
check("checkout.stripe.com" in (response.headers.get("Location") or ""),
      "and it points at Stripe's hosted payment page",
      f"redirected to {response.headers.get('Location')!r} instead")
check(len(calls) == 2,
      f"Stripe was called twice: once with the stale id, once without",
      f"called {len(calls)} time(s). One call means the retry never happened; "
      "more means it is looping")
if len(calls) == 2:
    check("customer" not in calls[1],
          "the retry does not carry the dead customer id",
          "the same unusable id was sent again, so the retry cannot succeed")
    check(calls[1].get("customer_email") == "stale@example.com"
          and calls[1].get("customer_creation") == "always",
          "the retry asks Stripe to make a customer from the email address",
          "without these Stripe has no way to create the customer, and the "
          "purchase is not attached to anyone")

print("\n== and the dead id is not left in the database ==")

row = db.get_user(user_id)
check(not (row["stripe_customer_id"] or ""),
      "the stale customer id was cleared",
      f"still {row['stripe_customer_id']!r}. Every future checkout by this "
      "account pays the cost of the failed first attempt again")

print("\n== a healthy account is untouched ==")

calls.clear()
db.set_stripe_customer(user_id, "cus_live_and_real")
with app.test_client() as client:
    response = post_checkout(client)
check(response.status_code == 303 and len(calls) == 1,
      "a valid customer id is used once, with no retry",
      f"status {response.status_code} after {len(calls)} call(s): a working "
      "account is being sent through the failure path")
check(calls and calls[0].get("customer") == "cus_live_and_real",
      "and the returning customer keeps their Stripe record",
      "the stored customer is being discarded, so a customer's saved cards "
      "and receipt history detach from them on every purchase")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
