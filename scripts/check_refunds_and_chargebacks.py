"""Check that money going back takes the booklet credits back with it.

The hole this closes: the Stripe webhook handled only the two events that
grant credits. There was no charge.refunded and no dispute handling, and
record_payment_and_credit wrote status='paid' with nothing in the codebase
ever updating it. So a customer could buy the ten-pack, generate all ten
booklets, file a chargeback, and keep everything. The admin console clamped
credit adjustments to a positive 1 to 100, so there was no way to correct it
by hand either, and SUPPORT_PLAYBOOK.md told the operator not to edit the
database until an audited tool existed.

Everything here drives the real webhook route with a real Stripe signature and
reads the real ledger afterwards. Stripe itself is never called: the only
outbound lookup, the checkout session behind a payment intent, is stubbed.

Usage:  PYTHONPATH=. python scripts/check_refunds_and_chargebacks.py
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parent.parent
os.environ.pop("DATABASE_URL", None)
tmp = Path(tempfile.mkdtemp(prefix="folio-refunds-"))
os.environ["FOLIO_DB"] = str(tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "refund-check-secret-key-value-123456789"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_refund-check"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_refund-check"
os.environ["STRIPE_PRICE_SINGLE"] = "price_single_check"
os.environ["STRIPE_PRICE_TERM"] = "price_term_check"

import stripe                                                    # noqa: E402

from booklet_gen.webapp import create_app                        # noqa: E402
from booklet_gen.webapp import db, payments                      # noqa: E402

PASSED = 0
TOTAL = 0


def check(good: bool, label: str, detail: str = "") -> None:
    global PASSED, TOTAL
    TOTAL += 1
    PASSED += bool(good)
    print(f"{'ok  ' if good else '*** FAIL ***':<14}{label}")
    if not good and detail:
        print(f"{'':<14}{detail[:300]}")


app = create_app()
client = app.test_client()


def csrf(path: str) -> str:
    page = client.get(path).data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


# The one outbound Stripe call in this path: which checkout session produced a
# payment intent. Only reached for payment rows recorded before the intent was
# stored on them.
_sessions_by_intent: dict[str, str] = {}


def _fake_session_list(**kwargs):
    session_id = _sessions_by_intent.get(kwargs.get("payment_intent"))
    return SimpleNamespace(data=[SimpleNamespace(id=session_id)] if session_id else [])


stripe.checkout.Session.list = _fake_session_list


def post_event(kind: str, obj: dict, *, sign: bool = True):
    """Deliver one signed Stripe event to the real webhook route."""
    payload = json.dumps({
        "id": f"evt_{uuid.uuid4().hex[:12]}", "object": "event",
        "type": kind, "data": {"object": obj},
    })
    headers = {}
    if sign:
        timestamp = int(time.time())
        signature = stripe.WebhookSignature._compute_signature(
            f"{timestamp}.{payload}", os.environ["STRIPE_WEBHOOK_SECRET"])
        headers["Stripe-Signature"] = f"t={timestamp},v1={signature}"
    return client.post("/stripe/webhook", data=payload,
                       content_type="application/json", headers=headers)


def new_buyer(email: str) -> int:
    client.post("/signup", data={
        "email": email, "password": "password123", "csrf_token": csrf("/signup")})
    return int(db.get_user_by_email(email)["id"])


def sell(user_id: int, units: int, cents: int, *, intent: str | None,
         product: str = "term") -> str:
    """Record a completed purchase the way fulfilment does, and return its id."""
    session_id = f"cs_{uuid.uuid4().hex[:16]}"
    db.record_payment_and_credit(
        session_id, user_id, product, units, cents, "aud", intent)
    return session_id


def status_of(session_id: str) -> str:
    return db.find_payment(session_id=session_id)["status"]


def reversed_of(session_id: str) -> int:
    return int(db.find_payment(session_id=session_id)["reversed_units"] or 0)


# --------------------------------------------------------------------------
print("== a chargeback on a fully used ten-pack ==")
# The exact scenario: buy ten, generate ten, charge back, keep everything.
buyer = new_buyer("chargeback@example.com")
welcome = db.credit_balance(buyer)
session = sell(buyer, 10, 3900, intent="pi_chargeback")
check(db.credit_balance(buyer) == welcome + 10,
      f"the ten-pack grants ten credits (balance {db.credit_balance(buyer)})")

spent = uuid.uuid4().hex
db.enqueue_job(spent, buyer, "Term plan", 10,
               {"program": "accelerate", "is_term": True}, True)
db.claim_job(spent)
db.finish_job(spent, path="term.pdf")
check(db.credit_balance(buyer) == welcome,
      "all ten are generated and spent")

response = post_event("charge.dispute.created", {
    "id": "dp_1", "payment_intent": "pi_chargeback",
    "amount": 3900, "status": "needs_response"})
check(response.status_code == 200, f"the dispute webhook is accepted ({response.status_code})")
check(db.credit_balance(buyer) == welcome - 10,
      f"the chargeback leaves the balance at {db.credit_balance(buyer)}, "
      "the ten booklets taken and not paid for",
      "credits were not clawed back")
check(status_of(session) == "disputed",
      f"the payment row now reads {status_of(session)!r}, not 'paid'")

blocked = db.enqueue_job(uuid.uuid4().hex, buyer, "Another", 1,
                         {"program": "accelerate"}, True)
check(blocked is False,
      "a negative balance stops the account generating anything else")

# Stripe redelivers webhooks. Doing it twice must not double the reversal.
post_event("charge.dispute.created", {
    "id": "dp_1", "payment_intent": "pi_chargeback",
    "amount": 3900, "status": "needs_response"})
check(db.credit_balance(buyer) == welcome - 10,
      f"a redelivered dispute changes nothing (balance {db.credit_balance(buyer)})")

# --------------------------------------------------------------------------
print("\n== a full refund ==")
refunded = new_buyer("refund@example.com")
base = db.credit_balance(refunded)
session = sell(refunded, 10, 3900, intent="pi_refund")
post_event("charge.refunded", {
    "id": "ch_refund", "payment_intent": "pi_refund",
    "amount": 3900, "amount_refunded": 3900, "refunded": True})
check(db.credit_balance(refunded) == base,
      f"a full refund takes the whole pack back (balance {db.credit_balance(refunded)})")
check(status_of(session) == "refunded",
      f"the payment row reads {status_of(session)!r}")

# --------------------------------------------------------------------------
print("\n== partial refunds are proportional and cumulative ==")
partial = new_buyer("partial@example.com")
base = db.credit_balance(partial)
session = sell(partial, 10, 3900, intent="pi_partial")
post_event("charge.refunded", {
    "id": "ch_partial", "payment_intent": "pi_partial",
    "amount": 3900, "amount_refunded": 1170, "refunded": False})
check(db.credit_balance(partial) == base + 7,
      f"refunding 30 percent of a ten-pack takes three "
      f"(balance {db.credit_balance(partial)}, expected {base + 7})")
check(status_of(session) == "partially_refunded",
      f"the payment row reads {status_of(session)!r}")

post_event("charge.refunded", {
    "id": "ch_partial", "payment_intent": "pi_partial",
    "amount": 3900, "amount_refunded": 3900, "refunded": True})
check(db.credit_balance(partial) == base,
      f"refunding the rest takes only the remaining seven "
      f"(balance {db.credit_balance(partial)}, expected {base})")
check(reversed_of(session) == 10,
      f"ten of ten stand reversed, counted once ({reversed_of(session)})")

# A goodwill gesture on a single booklet must not cost the booklet.
goodwill = new_buyer("goodwill@example.com")
base = db.credit_balance(goodwill)
session = sell(goodwill, 1, 900, intent="pi_goodwill", product="single")
post_event("charge.refunded", {
    "id": "ch_goodwill", "payment_intent": "pi_goodwill",
    "amount": 900, "amount_refunded": 90, "refunded": False})
check(db.credit_balance(goodwill) == base + 1,
      f"a ten percent goodwill refund takes no credit "
      f"(balance {db.credit_balance(goodwill)}, expected {base + 1})")

# --------------------------------------------------------------------------
print("\n== a dispute after a partial refund takes only the remainder ==")
both = new_buyer("both@example.com")
base = db.credit_balance(both)
session = sell(both, 10, 3900, intent="pi_both")
post_event("charge.refunded", {
    "id": "ch_both", "payment_intent": "pi_both",
    "amount": 3900, "amount_refunded": 780, "refunded": False})
check(db.credit_balance(both) == base + 8,
      f"20 percent refunded takes two (balance {db.credit_balance(both)})")
post_event("charge.dispute.created", {
    "id": "dp_both", "payment_intent": "pi_both", "amount": 3900})
check(db.credit_balance(both) == base,
      f"the chargeback takes the other eight, not another ten "
      f"(balance {db.credit_balance(both)}, expected {base})")
check(reversed_of(session) == 10, f"reversed total is 10 ({reversed_of(session)})")

# --------------------------------------------------------------------------
print("\n== a dispute closing does not quietly hand the credits back ==")
before = db.credit_balance(both)
response = post_event("charge.dispute.closed", {
    "id": "dp_both", "payment_intent": "pi_both", "status": "won"})
check(response.status_code == 200, "the closing event is accepted")
check(db.credit_balance(both) == before,
      "winning a dispute does not automatically restore credits; that is a "
      "decision for a person through the audited adjustment")

# --------------------------------------------------------------------------
print("\n== a payment recorded before intents were stored ==")
legacy = new_buyer("legacy@example.com")
base = db.credit_balance(legacy)
session = sell(legacy, 10, 3900, intent=None)
check(db.find_payment(session_id=session)["payment_intent_id"] is None,
      "the legacy row starts with no payment intent")
_sessions_by_intent["pi_legacy"] = session
post_event("charge.refunded", {
    "id": "ch_legacy", "payment_intent": "pi_legacy",
    "amount": 3900, "amount_refunded": 3900, "refunded": True})
check(db.credit_balance(legacy) == base,
      f"it is still found and reversed (balance {db.credit_balance(legacy)})")
check(db.find_payment(session_id=session)["payment_intent_id"] == "pi_legacy",
      "and the intent is written back so the next lookup is local")

# --------------------------------------------------------------------------
print("\n== events we cannot act on, and events we must retry ==")
response = post_event("charge.refunded", {
    "id": "ch_stranger", "payment_intent": "pi_not_ours",
    "amount": 500, "amount_refunded": 500, "refunded": True})
check(response.status_code == 200,
      f"a charge that is not ours is accepted, not retried forever "
      f"({response.status_code})",
      "a 5xx here would burn Stripe's retry budget and then disable the "
      "endpoint, which stops fulfilling everyone else's real purchases")

response = post_event("charge.refunded", {
    "id": "ch_nointent", "amount": 500, "amount_refunded": 500})
check(response.status_code == 200,
      f"an event with no payment intent is accepted ({response.status_code})")

real_reverse = db.reverse_payment_credits
try:
    def _unavailable(*args, **kwargs):
        raise ConnectionError("database unavailable")

    db.reverse_payment_credits = _unavailable
    response = post_event("charge.refunded", {
        "id": "ch_retry", "payment_intent": "pi_refund",
        "amount": 3900, "amount_refunded": 3900, "refunded": True})
    check(response.status_code == 500,
          f"a database outage asks Stripe to retry ({response.status_code})",
          "dropping this one leaves credits with someone whose money went back")
finally:
    db.reverse_payment_credits = real_reverse

response = post_event("charge.refunded", {
    "id": "ch_unsigned", "payment_intent": "pi_refund",
    "amount": 3900, "amount_refunded": 3900}, sign=False)
check(response.status_code == 400,
      f"an unsigned reversal is rejected ({response.status_code})",
      "anyone could otherwise strip any account's credits")

# --------------------------------------------------------------------------
print("\n== the ledger says who did what ==")
with db._cursor() as cur:
    cur.execute(db._q(
        "SELECT delta,reason,reference FROM credit_ledger WHERE user_id=? "
        "AND delta<0 ORDER BY id"), (both,))
    entries = [dict(row) for row in cur.fetchall()]
check(any(e["reference"].startswith("refund:") for e in entries)
      and any(e["reference"].startswith("dispute:") for e in entries),
      f"each reversal is a ledger entry naming its cause: "
      f"{[e['reference'] for e in entries]}")
check(all(e["reason"] for e in entries),
      "and every one of them carries a reason")

# --------------------------------------------------------------------------
print("\n== the audited manual adjustment ==")
# The webhook cannot judge every case: a dispute we won, a refund issued
# outside Stripe, a correction to an earlier mistake. Before this there was no
# way to remove a credit at all, so SUPPORT_PLAYBOOK.md had to tell the
# operator not to touch the database.
os.environ["FOLIO_ADMIN_EMAILS"] = "owner@example.com"
owner = new_buyer("owner@example.com")
client.post("/logout", data={"csrf_token": csrf("/account")})
client.post("/login", data={"email": "owner@example.com",
                            "password": "password123",
                            "csrf_token": csrf("/login")})
check(client.get("/admin").status_code == 200, "the owner reaches the console")


def adjust(email: str, units, reason: str):
    return client.post("/admin/credits", data={
        "email": email, "units": units, "reason": reason,
        "csrf_token": csrf("/admin")}, follow_redirects=True)


target = "chargeback@example.com"
target_id = int(db.get_user_by_email(target)["id"])
before = db.credit_balance(target_id)
adjust(target, -5, "wrote off dispute dp_1 after review")
check(db.credit_balance(target_id) == before - 5,
      f"a negative adjustment removes credits "
      f"(balance {db.credit_balance(target_id)}, was {before})",
      "the console clamped to a positive 1 to 100, so this was impossible")

before = db.credit_balance(target_id)
adjust(target, -5, "support adjustment")
check(db.credit_balance(target_id) == before,
      "a removal with only the placeholder reason is refused")
adjust(target, -5, "short")
check(db.credit_balance(target_id) == before,
      "and so is a removal with a reason too thin to audit")

adjust(target, -101, "way past the cap on a slipped key")
check(db.credit_balance(target_id) == before,
      "an adjustment past the cap is refused in the negative direction too")
adjust(target, 0, "nothing at all")
check(db.credit_balance(target_id) == before, "a zero adjustment is refused")
adjust("nobody@example.com", -5, "an account that does not exist")
check(db.credit_balance(target_id) == before,
      "an unknown account is refused")

adjust(target, 3, "goodwill after the review")
check(db.credit_balance(target_id) == before + 3,
      f"adding still works (balance {db.credit_balance(target_id)})")

with db._cursor() as cur:
    cur.execute(db._q(
        "SELECT delta,reason,reference FROM credit_ledger WHERE user_id=? "
        "AND reference LIKE 'admin:%' ORDER BY id"), (target_id,))
    admin_entries = [dict(row) for row in cur.fetchall()]
check(len(admin_entries) == 2
      and any(e["delta"] == -5 for e in admin_entries)
      and all(f"admin:{owner}:" in e["reference"] for e in admin_entries)
      and all("owner@example.com" in e["reason"] for e in admin_entries),
      f"every adjustment names the admin who made it and why: "
      f"{[(e['delta'], e['reason']) for e in admin_entries]}")

# The console is the only way in, and it is not open to customers.
client.post("/logout", data={"csrf_token": csrf("/account")})
client.post("/login", data={"email": target, "password": "password123",
                            "csrf_token": csrf("/login")})
check(client.get("/admin").status_code == 404,
      "a customer cannot see the console")
victim = int(db.get_user_by_email("goodwill@example.com")["id"])
before_self = db.credit_balance(target_id)
before_victim = db.credit_balance(victim)
# A token from a page they can reach, so this tests the admin gate and not
# only the CSRF layer sitting in front of it.
response = client.post("/admin/credits", data={
    "email": "goodwill@example.com", "units": -50,
    "reason": "stripping someone else's account",
    "csrf_token": csrf("/account")}, follow_redirects=True)
check(response.status_code == 404, f"and cannot post to it ({response.status_code})")
check(db.credit_balance(victim) == before_victim
      and db.credit_balance(target_id) == before_self,
      "no balance moved")

# --------------------------------------------------------------------------
print("\n== upgrading a database that predates the reversal columns ==")
# The deployed database already exists, so a new column only arrives through
# the migration list. Creating its index alongside the CREATE TABLE looks
# right and works perfectly on an empty database, then fails on every real one
# with "column payment_intent_id does not exist", because CREATE TABLE IF NOT
# EXISTS does nothing when the table is already there. init_db raises, and the
# app does not start.
import sqlite3                                                   # noqa: E402

legacy_db = tmp / "legacy.db"
with sqlite3.connect(legacy_db) as conn:
    conn.execute("""CREATE TABLE payments (
        checkout_session_id TEXT PRIMARY KEY,
        user_id             INTEGER NOT NULL,
        product_key         TEXT NOT NULL,
        units               INTEGER NOT NULL,
        amount_total        INTEGER,
        currency            TEXT,
        status              TEXT NOT NULL,
        created_at          INTEGER NOT NULL,
        updated_at          INTEGER NOT NULL)""")
    conn.execute(
        "INSERT INTO payments VALUES ('cs_old',1,'term',10,3900,'aud','paid',1,1)")

original_path = db.DB_PATH
try:
    db.DB_PATH = legacy_db
    started = True
    try:
        db.init_db()
        db.init_db()          # a redeploy runs it again
    except Exception as exc:                    # noqa: BLE001
        started = False
        check(False, "init_db upgrades an existing database", str(exc))
    if started:
        check(True, "init_db upgrades an existing database, twice over")
        with sqlite3.connect(legacy_db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(payments)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(payments)")}
            kept = conn.execute(
                "SELECT units,reversed_units FROM payments "
                "WHERE checkout_session_id='cs_old'").fetchone()
        check({"payment_intent_id", "reversed_units"} <= columns,
              f"the new columns arrive on the existing table ({sorted(columns)})")
        check("payments_intent_idx" in indexes,
              f"and so does the index the refund lookup needs ({sorted(indexes)})")
        check(kept == (10, 0),
              f"the payment already in the table is intact and unreversed ({kept})")
finally:
    db.DB_PATH = original_path

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
