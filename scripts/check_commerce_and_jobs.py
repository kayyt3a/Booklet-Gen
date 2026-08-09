"""Check FolioAI credits, queue settlement, payment idempotency, and recovery."""
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
tmp = Path(tempfile.mkdtemp(prefix="folio-commerce-"))
os.environ["FOLIO_DB"] = str(tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "commerce-check-secret-key-value-123456789"
os.environ.pop("STRIPE_SECRET_KEY", None)
os.environ.pop("STRIPE_WEBHOOK_SECRET", None)
os.environ.pop("STRIPE_PRICE_SINGLE", None)
os.environ.pop("STRIPE_PRICE_TERM", None)

from booklet_gen.webapp import create_app  # noqa: E402
from booklet_gen.webapp import db, mailer, payments, views  # noqa: E402
from booklet_gen.programs import WEB_PROGRAM_ALLOWLIST_ENV  # noqa: E402


def passed(label: str) -> None:
    print(f"  PASS  {label}")


app = create_app()
client = app.test_client()


def token(path: str) -> str:
    page = client.get(path).data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


client.post(
    "/signup",
    data={"email": "buyer@example.com", "password": "password123",
          "csrf_token": token("/signup")},
)
user_id = int(db.get_user_by_email("buyer@example.com")["id"])
assert db.credit_balance(user_id) == 1
passed("a new account receives exactly one welcome credit")

request_data = {
    "program": "accelerate", "year": "Year 5", "subject": "Mathematics",
    "topic": None, "name": "Ari", "is_term": False, "is_exam": False,
}
first = uuid.uuid4().hex
assert db.enqueue_job(first, user_id, "First", 1, request_data, True)
assert db.credit_balance(user_id) == 0
assert not db.enqueue_job(uuid.uuid4().hex, user_id, "Blocked", 1,
                          request_data, True)
passed("job creation atomically reserves credits and rejects overspend")

claimed = db.claim_next_job()
assert claimed and claimed["id"] == first and claimed["status"] == "running"
assert json.loads(claimed["request_json"])["name"] == "Ari"
passed("the durable worker queue claims the complete stored request")

db.fail_job(first, "provider unavailable")
assert db.credit_balance(user_id) == 1
db.fail_job(first, "duplicate failure callback")
assert db.credit_balance(user_id) == 1
passed("a failed generation refunds its credit exactly once")

retry = uuid.uuid4().hex
assert db.enqueue_job(retry, user_id, "Retry", 1, request_data, True)
assert db.claim_job(retry)["status"] == "running"
db.finish_job(retry, path="placeholder.pdf")
assert db.credit_balance(user_id) == 0
passed("a successful generation settles without returning the spent credit")

assert db.record_payment_and_credit(
    "cs_test_once", user_id, "term", 10, 3900, "aud",
)
assert not db.record_payment_and_credit(
    "cs_test_once", user_id, "term", 10, 3900, "aud",
)
assert db.credit_balance(user_id) == 10
assert len(db.list_payments(user_id)) == 1
passed("replayed Stripe fulfilment grants one purchase exactly once")

os.environ["STRIPE_SECRET_KEY"] = "sk_test_local-check"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_local-check"
os.environ["STRIPE_PRICE_SINGLE"] = "price_single_check"
os.environ["STRIPE_PRICE_TERM"] = "price_term_check"
checkout_session = SimpleNamespace(
    id="cs_test_verified",
    payment_status="paid",
    client_reference_id=str(user_id),
    metadata={"product_key": "single"},
    line_items=SimpleNamespace(data=[SimpleNamespace(
        price=SimpleNamespace(id="price_single_check"),
    )]),
    amount_total=790,
    currency="aud",
    customer="cus_test_verified",
)
fake_stripe = SimpleNamespace(
    checkout=SimpleNamespace(Session=SimpleNamespace(
        retrieve=lambda _session_id, expand: checkout_session,
    )),
)
real_stripe_loader = payments._stripe
payments._stripe = lambda: fake_stripe
try:
    before_verified = db.credit_balance(user_id)
    assert payments.fulfil_checkout("cs_test_verified") == user_id
    assert payments.fulfil_checkout("cs_test_verified") == user_id
    assert db.credit_balance(user_id) == before_verified + 1
    assert db.get_user(user_id)["stripe_customer_id"] == "cus_test_verified"
finally:
    payments._stripe = real_stripe_loader
passed("server-side fulfilment verifies the price and remains idempotent")

term = uuid.uuid4().hex
term_request = dict(request_data, is_term=True)
assert db.enqueue_job(term, user_id, "Term", 10, term_request, True)
assert db.credit_balance(user_id) == 1
db.fail_job_if_running(term, "worker stopped")
assert db.credit_balance(user_id) == 11
passed("a 10-week plan reserves and refunds all 10 units together")

before_retry_count = len(db.list_jobs(user_id))
response = client.post(
    f"/retry/{term}",
    data={"csrf_token": token("/library")},
    follow_redirects=True,
)
assert response.status_code == 200
assert b"reached today" in response.data
assert len(db.list_jobs(user_id)) == before_retry_count
passed("customer retries cannot bypass the daily booklet abuse guard")

# The cap has to hold when the requests arrive together, not only when they
# arrive one at a time. views._quota_allows counts on a plain cursor and then
# enqueues in a separate transaction, so concurrent posts all read the same
# pre-insert total and every one of them was admitted.
import threading  # noqa: E402

race_user = db.create_user("race@test.com", "correct-horse-battery")
race_results: list[bool] = []
race_lock = threading.Lock()
RACE_LIMIT, RACE_POSTS = 3, 12


def _race_enqueue(n: int) -> None:
    ok = db.enqueue_job(f"race-{n}", race_user, "Race", 1, {"program": "accelerate"},
                        False, daily_limit=RACE_LIMIT, global_daily_limit=10_000)
    with race_lock:
        race_results.append(ok)


threads = [threading.Thread(target=_race_enqueue, args=(n,))
           for n in range(RACE_POSTS)]
for t in threads:
    t.start()
for t in threads:
    t.join()
assert sum(race_results) == RACE_LIMIT, (
    f"{sum(race_results)} of {RACE_POSTS} concurrent requests cleared a "
    f"limit of {RACE_LIMIT}")
assert db.booklets_started_last_24h(race_user) == RACE_LIMIT
passed("the daily cap holds when requests arrive concurrently")

# A slow job that the stale sweep has already failed and refunded must not be
# resurrected by the worker finishing later. It used to be: the customer kept
# the refunded credits and the booklet, which for a term plan is A$39 of
# product given away, self-serve and repeatable.
stale_user = db.create_user("stale@test.com", "correct-horse-battery")
db.grant_credits(stale_user, 10, reason="test", reference="stale-seed")
stale_start = db.credit_balance(stale_user)
assert db.enqueue_job("stale-job", stale_user, "Term plan", 10,
                      {"program": "accelerate", "is_term": True}, True)
assert db.credit_balance(stale_user) == stale_start - 10
db.claim_job("stale-job")
with db._cursor() as _cur:
    _cur.execute(db._q("UPDATE jobs SET created_at=? WHERE id=?"),
                 (int(time.time()) - 99_999, "stale-job"))
assert db.fail_stale_running_jobs(2700) == 1
assert db.credit_balance(stale_user) == stale_start, "the sweep should refund"
assert db.finish_job("stale-job", path="/tmp/whatever.pdf") is False, (
    "finish_job must refuse a job that was already settled and refunded")
assert db.get_job("stale-job")["status"] == "error"
passed("a refunded stale job is not resurrected by the worker finishing late")

import stripe  # noqa: E402

webhook_calls = []
real_fulfil = payments.fulfil_checkout
payments.fulfil_checkout = lambda session_id: webhook_calls.append(session_id)
try:
    payload = (
        '{"id":"evt_check","object":"event",'
        '"type":"checkout.session.completed",'
        '"data":{"object":{"id":"cs_webhook_check"}}}'
    )
    timestamp = int(time.time())
    signature = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{payload}",
        os.environ["STRIPE_WEBHOOK_SECRET"],
    )
    header = f"t={timestamp},v1={signature}"
    response = client.post(
        "/stripe/webhook", data=payload, content_type="application/json",
        headers={"Stripe-Signature": header},
    )
    assert response.status_code == 200, response.data
    assert webhook_calls == ["cs_webhook_check"]
    assert client.post(
        "/stripe/webhook", data=payload, content_type="application/json",
    ).status_code == 400

    # A permanent failure must not be handed back to Stripe as a 5xx. Stripe
    # retries a 5xx for about three days and then disables the endpoint, which
    # would silently stop fulfilling everyone else's real purchases. A price
    # mismatch or a deleted account can never succeed on retry.
    def _permanent(_session_id):
        raise ValueError("Checkout price does not match the FolioAI product.")

    payments.fulfil_checkout = _permanent
    assert client.post(
        "/stripe/webhook", data=payload, content_type="application/json",
        headers={"Stripe-Signature": header},
    ).status_code == 200, "a permanent fulfilment failure must not ask Stripe to retry"

    # A transient failure still must, because retrying is exactly the fix.
    def _transient(_session_id):
        raise ConnectionError("database unavailable")

    payments.fulfil_checkout = _transient
    assert client.post(
        "/stripe/webhook", data=payload, content_type="application/json",
        headers={"Stripe-Signature": header},
    ).status_code == 500, "a transient fulfilment failure must ask Stripe to retry"
finally:
    payments.fulfil_checkout = real_fulfil
passed("the Stripe webhook is CSRF-exempt but rejects an invalid signature")
passed("permanent fulfilment failures do not burn Stripe's retry budget")

assert client.get("/healthz").status_code == 200
assert client.get("/pricing").status_code == 200
for path in ("/privacy", "/terms", "/support", "/forgot-password"):
    assert client.get(path).status_code == 200, path
passed("health, pricing, policy, support, and recovery routes are live")

os.environ["FOLIO_REQUIRE_EMAIL_VERIFICATION"] = "1"
email_tokens = {}
real_send_verification = mailer.send_verification
real_send_reset = mailer.send_password_reset


def page_token(test_client, path: str) -> str:
    page = test_client.get(path).data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def capture_verification(user) -> None:
    email_tokens["verify"] = mailer.make_token(
        "verify", {"uid": int(user["id"]), "email": user["email"]},
    )


def capture_reset(user) -> None:
    email_tokens["reset"] = mailer.make_token(
        "reset", {"uid": int(user["id"]),
                  "marker": str(user["password_hash"])[-16:]},
    )


mailer.send_verification = capture_verification
mailer.send_password_reset = capture_reset
verified_client = app.test_client()
try:
    response = verified_client.post(
        "/signup",
        data={"email": "verified@example.com", "password": "password123",
              "csrf_token": page_token(verified_client, "/signup")},
    )
    assert response.status_code == 200 and b"Check your email" in response.data
    with verified_client.session_transaction() as session_data:
        assert "user_id" not in session_data
    assert verified_client.get(
        f"/verify/{email_tokens['verify']}", follow_redirects=True,
    ).status_code == 200
    verified = db.get_user_by_email("verified@example.com")
    assert bool(verified["email_verified"])

    verified_client.post(
        "/forgot-password",
        data={"email": "verified@example.com",
              "csrf_token": page_token(verified_client, "/forgot-password")},
    )
    reset_path = f"/reset-password/{email_tokens['reset']}"
    response = verified_client.post(
        reset_path,
        data={"password": "new-password-456",
              "csrf_token": page_token(verified_client, reset_path)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert db.verify_login("verified@example.com", "new-password-456")
finally:
    mailer.send_verification = real_send_verification
    mailer.send_password_reset = real_send_reset
    os.environ.pop("FOLIO_REQUIRE_EMAIL_VERIFICATION", None)
passed("email verification and expiring password-reset tokens complete the account flows")

saved_mode = views.JOB_MODE
saved_global_limit = views.GLOBAL_DAILY_BOOKLET_LIMIT
views.JOB_MODE = "queue"
views.GLOBAL_DAILY_BOOKLET_LIMIT = 1000
# This case models a working queue deployment, so it needs a live worker.
# Without one the web service now refuses the order outright rather than
# queueing a booklet nothing will ever generate.
db.record_worker_heartbeat(started_at=int(time.time()))
request_client = app.test_client()
try:
    request_client.post(
        "/signup",
        data={"email": "request-check@example.com", "password": "password123",
              "csrf_token": page_token(request_client, "/signup")},
    )
    request_user = db.get_user_by_email("request-check@example.com")
    rejected = request_client.post(
        "/generate",
        data={"program": "accelerate", "year": "Year 11",
              "subject": "Mathematics", "student_name": "Kai",
              "csrf_token": page_token(request_client, "/")},
        follow_redirects=True,
    )
    assert b"available for Years 1 to 10" in rejected.data
    assert not db.list_jobs(request_user["id"])

    # Methods Exam is off the customer menu until it has its own authoring
    # guide, so the server must refuse it even when a form field names it.
    off_menu = request_client.post(
        "/generate",
        data={"program": "methods_exam", "year": "Year 11",
              "student_name": "Kai",
              "csrf_token": page_token(request_client, "/")},
        follow_redirects=True,
    )
    assert not db.list_jobs(request_user["id"])

    # With it explicitly allowlisted, an exam still costs one unit and ignores
    # term_plan, which is the credit arithmetic this case exists to pin down.
    os.environ[WEB_PROGRAM_ALLOWLIST_ENV] = "naplan,accelerate,methods_exam"
    accepted = request_client.post(
        "/generate",
        data={"program": "methods_exam", "year": "Year 11",
              "student_name": "Kai", "term_plan": "on",
              "csrf_token": page_token(request_client, "/")},
    )
    assert accepted.status_code == 302 and "/progress/" in accepted.headers["Location"]
    queued = db.list_jobs(request_user["id"])
    assert len(queued) == 1 and queued[0]["units"] == 1
    stored_request = json.loads(db.get_job(queued[0]["id"])["request_json"])
    assert stored_request["is_exam"] is True and stored_request["is_term"] is False
finally:
    views.JOB_MODE = saved_mode
    views.GLOBAL_DAILY_BOOKLET_LIMIT = saved_global_limit
    os.environ.pop(WEB_PROGRAM_ALLOWLIST_ENV, None)
passed("server validation keeps booklet years and exam credit costs honest")

# Queue mode with no worker behind it. The web service only enqueues, so a
# missing worker leaves every booklet sitting at "generating" for ever with the
# credit already spent. That is worse than an outage, because the site looks
# like it is working. db had a heartbeat writer and reader already and nothing
# called either, so the table stayed empty and the outage stayed invisible.
saved_job_mode = views.JOB_MODE
with client.session_transaction() as session:
    saved_session_user = session.get("user_id")
try:
    views.JOB_MODE = "queue"
    # A worker that has stopped beating, which is what a crashed or
    # unprovisioned worker looks like from the web service.
    stale_beat = int(time.time()) - (views.WORKER_HEARTBEAT_MAX_AGE + 600)
    db.record_worker_heartbeat(started_at=stale_beat, now=stale_beat)
    assert views.generation_is_available() == (False, "stale")
    health = client.get("/healthz")
    assert health.status_code == 503, health.status_code
    assert health.get_json()["worker"] == "stale"

    worker_user = db.create_user("noworker@test.com", "correct-horse-battery")
    db.grant_credits(worker_user, 5, reason="test", reference="worker-seed")
    before_balance = db.credit_balance(worker_user)
    before_jobs = len(db.list_jobs(worker_user))
    with client.session_transaction() as session:
        session["user_id"] = worker_user
    refused = client.post(
        "/generate",
        data={"program": "accelerate", "year": "Year 5",
              "subject": "Mathematics", "student_name": "Sam",
              "csrf_token": page_token(client, "/")},
        follow_redirects=True,
    )
    assert b"paused for maintenance" in refused.data
    assert len(db.list_jobs(worker_user)) == before_jobs, "no job may be queued"
    assert db.credit_balance(worker_user) == before_balance, "no credit may be spent"

    db.record_worker_heartbeat(started_at=int(time.time()))
    assert views.generation_is_available() == (True, "")
    assert client.get("/healthz").status_code == 200
finally:
    views.JOB_MODE = saved_job_mode
    # This block signs the shared client in as a throwaway account. Later
    # cases reuse that client and expect the original buyer.
    with client.session_transaction() as session:
        if saved_session_user is None:
            session.pop("user_id", None)
        else:
            session["user_id"] = saved_session_user
passed("queue mode refuses to take an order it has no worker to fill")

logout_token = token("/")
assert client.get("/logout").status_code == 405
assert client.post("/logout", data={"csrf_token": logout_token}).status_code == 302
assert client.get("/account").status_code == 302
passed("logout is a CSRF-protected POST and clears the account session")

print("\nALL COMMERCE AND QUEUE CHECKS PASSED")
