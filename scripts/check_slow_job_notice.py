"""Check the progress page says something true about a booklet taking a while.

The second half of the founder's afternoon: the first booklet appeared frozen,
the page told him nothing either way, so he generated a second one and spent a
second credit. The page had exactly one thing to say for the whole run, "This
usually takes a couple of minutes", which stops being true at minute twenty and
was still saying it at minute forty.

What the page may honestly say is narrow, and this file guards the boundary
from both sides:

  * it may say how long it has been going, that this is longer than usual, and
    that the booklet can be cancelled for its credit back,
  * it may NOT invent a percentage, a fraction, an ETA or a "nearly done",
    because the pipeline reports no stage boundaries and every one of those
    would be made up. A bar that reaches 90 percent and stops is worse than no
    bar at all.

A term plan is ten booklets in one job, so it is legitimately slow and must not
be called slow at the same moment a single booklet is.

    PYTHONPATH=. python scripts/check_slow_job_notice.py
"""
from __future__ import annotations

import os
import re
import tempfile
import time
import uuid
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
tmp = Path(tempfile.mkdtemp(prefix="folio-slownotice-"))
os.environ["FOLIO_DB"] = str(tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "slow-notice-check-secret-key-value-12345"

from booklet_gen.webapp import create_app, db, views  # noqa: E402

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
REQUEST = {"program": "accelerate", "year": "Year 5", "subject": "Mathematics",
           "name": "Sam", "is_term": False, "is_exam": False}


def token(path: str) -> str:
    page = client.get(path).data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


client.post("/signup", data={"email": "waiting@example.com",
                            "password": "password123",
                            "csrf_token": token("/signup")})
user = int(db.get_user_by_email("waiting@example.com")["id"])
db.grant_credits(user, 100, reason="test", reference="slow-seed")
with client.session_transaction() as browser_session:
    browser_session["user_id"] = user


def start_job(*, units: int = 1, minutes_ago: int = 0) -> str:
    job_id = uuid.uuid4().hex
    label = "Term plan" if units > 1 else "Academic Accelerate - Year 5 - Sam"
    assert db.enqueue_job(job_id, user, label, units,
                          dict(REQUEST, is_term=units > 1), True)
    assert db.claim_job(job_id) is not None
    if minutes_ago:
        stamp = int(time.time()) - minutes_ago * 60
        with db._cursor() as cur:
            cur.execute(
                db._q("UPDATE jobs SET created_at=?, started_at=? WHERE id=?"),
                (stamp, stamp, job_id))
        # Still alive, just slow. Without this the heartbeat sweep would
        # (correctly) settle it and there would be nothing to narrate.
        if getattr(db, "beat_job", None):
            db.beat_job(job_id)
    return job_id


def status_of(job_id: str) -> dict:
    return client.get(f"/status/{job_id}").get_json()


# --------------------------------------------------------------------------
print("== a booklet that has just started is described as usual ==")
fresh = start_job()
payload = status_of(fresh)
check(payload.get("slow") is False,
      f"a booklet a few seconds old is not called slow ({payload})",
      "crying wolf on every normal booklet would make the notice worthless")
page = client.get(f"/progress/{fresh}").data.decode()
check("usually takes a couple of minutes" in page,
      "and the page still says what usually happens")
check("taking longer than usual" not in
      page.split("id=\"slowNotice\"")[0],
      "with no slow notice above it")

# --------------------------------------------------------------------------
print("\n== after long enough, the page stops claiming a couple of minutes ==")
stuck = start_job(minutes_ago=30)
payload = status_of(stuck)
check(payload.get("slow") is True,
      f"thirty minutes in, the server says so ({payload})",
      "this is the state the founder sat in front of with no information")
check(payload.get("running_seconds", 0) >= 30 * 60,
      f"and reports how long it has been going "
      f"({payload.get('running_seconds')}s)")

page = client.get(f"/progress/{stuck}").data.decode()
check("taking longer than usual" in page,
      "the page says it is taking longer than usual on first load",
      "the customer coming back to the tab must not have to wait for a poll")
check("your booklet credit comes straight back" in page,
      "and points at the way out, which returns the credit")
check(f"/cancel/{stuck}" in page, "with the cancel control actually on it")

# --------------------------------------------------------------------------
print("\n== nothing on the page is invented ==")
# The pipeline reports no progress, so a number here could only be made up.
forbidden = [r"% complete", r"percent complete", r"almost done",
             r"nearly done", r"minutes remaining", r"estimated", r"\beta\b",
             r"halfway", r"\d\d%", r"step \d of"]
lowered = page.lower()
found = [pattern for pattern in forbidden if re.search(pattern, lowered)]
check(not found, "no percentage, no ETA and no fake milestone",
      f"found: {found}")
check("running_seconds" in payload and set(payload) <= {
          "status", "slow", "running_seconds", "error", "download_url"},
      f"and /status returns only facts it can actually know: {sorted(payload)}")

# --------------------------------------------------------------------------
print("\n== My booklets says it there too ==")
page = client.get("/library").data.decode()
check("taking longer than usual" in page,
      "the page the founder was actually looking at carries the same note")
row = page.split(f"/progress/{stuck}")[0]
check("Building" in row, "beside the Building pill, not instead of it")

# --------------------------------------------------------------------------
print("\n== a term plan is ten booklets, so it is allowed to take longer ==")
# Ten booklets in one job. Calling it slow at the same minute a single booklet
# is called slow would be crying wolf on the product's most expensive order.
# Read through getattr so a build with no notice at all reports every line
# below rather than stopping on an AttributeError.
slow_after = getattr(views, "_slow_after_seconds", lambda _units: 0)
single_threshold = slow_after(1)
term_threshold = slow_after(10)
check(term_threshold > single_threshold,
      f"a ten-week plan is given longer than a single booklet "
      f"({term_threshold}s against {single_threshold}s)")
check(term_threshold < views.JOB_TIMEOUT_SECONDS,
      f"but still inside the timeout ({term_threshold}s against "
      f"{views.JOB_TIMEOUT_SECONDS}s), so the notice appears before the job "
      f"is settled rather than instead of it")

term = start_job(units=10, minutes_ago=10)
check(status_of(term).get("slow") is False,
      f"a term plan ten minutes in is not called slow ({status_of(term)})")
term_slow = start_job(units=10, minutes_ago=35)
check(status_of(term_slow).get("slow") is True,
      f"the same plan thirty-five minutes in is ({status_of(term_slow)})")

# --------------------------------------------------------------------------
print("\n== a settled booklet is never narrated as slow ==")
done_job = start_job(minutes_ago=30)
db.finish_job(done_job, path=str(tmp / "x.pdf"))
payload = status_of(done_job)
check(payload["status"] == "done" and "slow" not in payload,
      f"a finished booklet carries no slow flag ({payload})")

failed = start_job(minutes_ago=30)
db.fail_job_if_running(failed, "something broke")
payload = status_of(failed)
check(payload["status"] == "error" and "slow" not in payload,
      f"nor does a failed one ({payload})")

# --------------------------------------------------------------------------
print("\n== the threshold is tunable without a code change ==")
notice_seconds = getattr(views, "SLOW_NOTICE_SECONDS", None)
check(isinstance(notice_seconds, int) and notice_seconds > 0,
      f"FOLIO_SLOW_JOB_NOTICE backs it ({notice_seconds}s)")
check(isinstance(notice_seconds, int) and notice_seconds >= 300,
      f"and it is minutes rather than seconds, so an ordinary booklet never "
      f"trips it ({notice_seconds}s)")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
