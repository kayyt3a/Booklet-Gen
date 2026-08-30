"""Check that a customer can stop a stuck booklet and get the credit back.

What went wrong for the founder: two booklets sat on "Building" with the
credits already spent and nothing on the page could stop them. Generation runs
in a background thread inside the web service whenever no worker answers, a
Render restart or idle spin-down kills that thread without settling the row,
and the only recovery was FOLIO_JOB_TIMEOUT, which is 45 minutes. The one move
available to him was to spend a second credit on a second booklet.

So this exercises the cancel action end to end through the real routes, and it
asserts the balance every time, not only the job status. A cancel that flips a
row to 'error' without returning the credit is the same bug wearing a nicer
label.

The two orderings that matter, both checked below:

  cancel then finish  ->  finish_job's status guard refuses, the job stays
                          failed and refunded, and the booklet is not
                          downloadable. Credit back, no booklet.
  finish then cancel  ->  fail_job_if_running finds a settled row, refunds
                          nothing, and the booklet stays downloadable.
                          Booklet delivered, credit spent.

Never both. That is the property, and the concurrent section drives it with
real threads rather than trusting the reading.

    PYTHONPATH=. python scripts/check_job_cancel.py
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
tmp = Path(tempfile.mkdtemp(prefix="folio-cancel-"))
os.environ["FOLIO_DB"] = str(tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "cancel-check-secret-key-value-123456789"

from booklet_gen.webapp import create_app, db  # noqa: E402

PASSED = 0
TOTAL = 0


def check(good: bool, label: str, detail: str = "") -> None:
    global PASSED, TOTAL
    TOTAL += 1
    PASSED += bool(good)
    print(f"{'ok  ' if good else '*** FAIL ***':<14}{label}")
    if not good and detail:
        print(f"{'':<14}{detail[:400]}")


app = create_app()
client = app.test_client()
# Read through getattr so a build without the constant reports every failure
# below instead of stopping at the first AttributeError. The comparisons are
# still against the real constant whenever it exists.
CANCELLED = getattr(db, "CANCELLED_MESSAGE", "<db.CANCELLED_MESSAGE is missing>")
REQUEST = {"program": "accelerate", "year": "Year 5", "subject": "Mathematics",
           "name": "Sam", "is_term": False, "is_exam": False}


def token(test_client, path: str) -> str:
    page = test_client.get(path).data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def new_account(test_client, email: str, credits: int = 20) -> int:
    test_client.post("/signup", data={
        "email": email, "password": "password123",
        "csrf_token": token(test_client, "/signup")})
    user_id = int(db.get_user_by_email(email)["id"])
    db.grant_credits(user_id, credits, reason="test", reference=f"seed:{email}")
    return user_id


def start_job(user_id: int, *, units: int = 1, claim: bool = True) -> str:
    job_id = uuid.uuid4().hex
    assert db.enqueue_job(job_id, user_id, "Academic Accelerate - Year 5 - Sam",
                          units, REQUEST, True), "could not enqueue"
    if claim:
        assert db.claim_job(job_id) is not None
    return job_id


owner = new_account(client, "owner@example.com")

# --------------------------------------------------------------------------
print("== a running booklet can be cancelled, and the credit comes back ==")
before = db.credit_balance(owner)
job = start_job(owner)
check(db.credit_balance(owner) == before - 1,
      f"the job spent its credit (balance {db.credit_balance(owner)})")
check(db.get_job(job)["status"] == "running", "and it is running")

response = client.post(f"/cancel/{job}", data={
    "csrf_token": token(client, "/library")}, follow_redirects=True)
check(response.status_code == 200, f"the cancel is accepted ({response.status_code})")
check(db.get_job(job)["status"] == "error",
      f"the job is settled ({db.get_job(job)['status']})")
check(db.credit_balance(owner) == before,
      f"the credit is back (balance {db.credit_balance(owner)}, expected {before})",
      "a cancel that does not refund is the original bug with a button on it")
check(db.get_job(job)["error"] == CANCELLED,
      f"and the row says who stopped it: {db.get_job(job)['error']!r}")

# --------------------------------------------------------------------------
print("\n== a queued booklet, waiting on a worker, cancels the same way ==")
before = db.credit_balance(owner)
queued = start_job(owner, claim=False)
check(db.get_job(queued)["status"] == "queued", "the job is queued, never claimed")
client.post(f"/cancel/{queued}", data={"csrf_token": token(client, "/library")})
check(db.get_job(queued)["status"] == "error" and db.credit_balance(owner) == before,
      f"cancelled and refunded (balance {db.credit_balance(owner)}, expected {before})")

# --------------------------------------------------------------------------
print("\n== a term plan returns all ten credits, not one ==")
before = db.credit_balance(owner)
term = uuid.uuid4().hex
assert db.enqueue_job(term, owner, "Term plan", 10, dict(REQUEST, is_term=True), True)
check(db.credit_balance(owner) == before - 10,
      f"ten reserved (balance {db.credit_balance(owner)})")
db.claim_job(term)
client.post(f"/cancel/{term}", data={"csrf_token": token(client, "/library")})
check(db.credit_balance(owner) == before,
      f"ten returned (balance {db.credit_balance(owner)}, expected {before})")

# --------------------------------------------------------------------------
print("\n== a double-clicked cancel refunds exactly once ==")
before = db.credit_balance(owner)
twice = start_job(owner)
csrf = token(client, "/library")
client.post(f"/cancel/{twice}", data={"csrf_token": csrf})
client.post(f"/cancel/{twice}", data={"csrf_token": csrf})
client.post(f"/cancel/{twice}", data={"csrf_token": csrf})
check(db.credit_balance(owner) == before,
      f"three posts, one refund (balance {db.credit_balance(owner)}, "
      f"expected {before})",
      "a second refund would be free credits on a double click")

# --------------------------------------------------------------------------
print("\n== cancel then finish: credit back, no booklet ==")
before = db.credit_balance(owner)
first_cancel = start_job(owner)
client.post(f"/cancel/{first_cancel}", data={"csrf_token": token(client, "/library")})
late = db.finish_job(first_cancel, path=str(tmp / "late.pdf"))
check(late is False, "finish_job refuses a job that was already cancelled")
check(db.get_job(first_cancel)["status"] == "error",
      f"the job stays settled ({db.get_job(first_cancel)['status']})")
check(db.credit_balance(owner) == before,
      f"the customer keeps the refund (balance {db.credit_balance(owner)})")
check(client.get(f"/download/{first_cancel}").status_code == 404,
      "and cannot download the booklet they were refunded for",
      "refund plus booklet is the one outcome that must never happen")

# --------------------------------------------------------------------------
print("\n== finish then cancel: booklet delivered, credit stays spent ==")
before = db.credit_balance(owner)
first_finish = start_job(owner)
db.save_job_file(first_finish, owner, "booklet.pdf", "application/pdf", b"%PDF-1.4 ok")
check(db.finish_job(first_finish, path=str(tmp / "done.pdf")) is True,
      "the job completes normally")
response = client.post(f"/cancel/{first_finish}", data={
    "csrf_token": token(client, "/library")}, follow_redirects=True)
check(db.get_job(first_finish)["status"] == "done",
      f"a late cancel does not undo a finished booklet "
      f"({db.get_job(first_finish)['status']})")
check(db.credit_balance(owner) == before - 1,
      f"no refund for a booklet that was delivered "
      f"(balance {db.credit_balance(owner)}, expected {before - 1})",
      "refunding here would hand over the booklet and the credit")
check(b"finished just before your cancel" in response.data,
      "and the customer is told what happened rather than shown a silent no-op")
check(client.get(f"/download/{first_finish}").status_code == 200,
      "the booklet they paid for is still downloadable")

# --------------------------------------------------------------------------
print("\n== the two racing for real, many times over ==")
# Threads, not reasoning. The invariant is a single one: for every job,
# exactly one of (delivered, refunded) is true.
racer = new_account(app.test_client(), "racer@example.com", credits=200)
bad: list[str] = []
for round_number in range(30):
    balance_before = db.credit_balance(racer)
    job_id = start_job(racer)
    finished: list[bool] = []
    cancelled: list[bool] = []
    barrier = threading.Barrier(2)

    def do_finish(jid=job_id):
        barrier.wait()
        finished.append(db.finish_job(jid, path="race.pdf"))

    def do_cancel(jid=job_id):
        barrier.wait()
        cancelled.append(db.fail_job_if_running(jid, CANCELLED))

    threads = [threading.Thread(target=do_finish), threading.Thread(target=do_cancel)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    status = db.get_job(job_id)["status"]
    balance_after = db.credit_balance(racer)
    refunded = balance_after == balance_before
    delivered = status == "done"
    if finished[0] and cancelled[0]:
        bad.append(f"round {round_number}: both calls claimed the job")
    elif delivered and refunded:
        bad.append(f"round {round_number}: delivered AND refunded")
    elif not delivered and not refunded:
        bad.append(f"round {round_number}: neither delivered nor refunded "
                   f"(status {status}, balance {balance_after})")
check(not bad, "30 concurrent finish/cancel races each had exactly one winner",
      "; ".join(bad[:3]))

# --------------------------------------------------------------------------
print("\n== nobody else's booklet ==")
intruder_client = app.test_client()
intruder = new_account(intruder_client, "intruder@example.com")
victim_job = start_job(owner)
owner_before = db.credit_balance(owner)
intruder_before = db.credit_balance(intruder)
response = intruder_client.post(f"/cancel/{victim_job}", data={
    "csrf_token": token(intruder_client, "/library")})
check(response.status_code == 404,
      f"another account's cancel is a 404 ({response.status_code})")
check(db.get_job(victim_job)["status"] == "running",
      f"the job is untouched ({db.get_job(victim_job)['status']})")
check(db.credit_balance(owner) == owner_before
      and db.credit_balance(intruder) == intruder_before,
      "and no balance moved on either account")

anonymous = app.test_client()
response = anonymous.post(f"/cancel/{victim_job}", data={"csrf_token": "x"})
check(db.get_job(victim_job)["status"] == "running",
      f"a signed-out post cannot cancel it either ({response.status_code})")

# --------------------------------------------------------------------------
print("\n== a cancel has to be a deliberate POST from FolioAI ==")
check(client.get(f"/cancel/{victim_job}").status_code == 405,
      "GET is refused, so a prefetch or a crawler cannot destroy work")
response = client.post(f"/cancel/{victim_job}", data={})
check(response.status_code == 400,
      f"a POST with no CSRF token is refused ({response.status_code})")
response = client.post(f"/cancel/{victim_job}", data={"csrf_token": "forged"})
check(response.status_code == 400,
      f"and so is a forged one ({response.status_code})")
check(db.get_job(victim_job)["status"] == "running"
      and db.credit_balance(owner) == owner_before,
      "the cross-site attempts changed neither the job nor the balance")

# --------------------------------------------------------------------------
print("\n== the redirect afterwards cannot be pointed off-site ==")
response = client.post(f"/cancel/{victim_job}", data={
    "csrf_token": token(client, "/library"),
    "next": "https://evil.example/phish"})
check(response.status_code == 302
      and "evil.example" not in response.headers.get("Location", ""),
      f"an off-site next is replaced by My booklets "
      f"({response.headers.get('Location')})")
check(db.credit_balance(owner) == owner_before + 1,
      f"the cancel itself still worked (balance {db.credit_balance(owner)})")

# --------------------------------------------------------------------------
print("\n== the button is where the customer is actually looking ==")
building = start_job(owner)
library_page = client.get("/library").data.decode()
check(f"/cancel/{building}" in library_page,
      "My booklets offers Cancel on a Building row")
check('method="post"' in library_page, "as a form, not a link")
progress_page = client.get(f"/progress/{building}").data.decode()
check(f"/cancel/{building}" in progress_page,
      "and so does the progress page the customer waits on")

client.post(f"/cancel/{building}", data={"csrf_token": token(client, "/library")})
library_page = client.get("/library").data.decode()
check("Cancelled" in library_page,
      "a cancelled booklet reads Cancelled, not Failed",
      "the row is an ordinary refunded error row; only the word differs")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
