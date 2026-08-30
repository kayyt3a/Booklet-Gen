"""Check that a dead booklet job is spotted in minutes, not in forty-five.

The 45 minute FOLIO_JOB_TIMEOUT was never a judgement about how long a job may
take. It existed because nothing could tell a slow job from a dead one, and a
ten-week term plan legitimately runs for tens of minutes, so the timeout had to
clear the slowest real work. A job whose thread died ten seconds in therefore
showed a spinner for the rest of those forty-five minutes with the credit
already spent, which is what happened to the founder.

A per-job heartbeat makes the two cases distinguishable. What this file pins
down, in the order the danger runs:

  1. the beat keeps ticking while the job is doing nothing observable, because
     one Gemini call can run for minutes and a beat that only ticked between
     pipeline stages would look dead during exactly the slow generation it is
     supposed to defend,
  2. a job that has stopped beating is failed AND REFUNDED quickly, with the
     balance checked and not just the status,
  3. a job that is still beating is left alone however long it has been going,
     because killing live work is worse than the bug being fixed,
  4. the created_at timeout still catches a job that never beat at all,
  5. the column arrives on a database that already exists, on both backends
     this app runs on.

    PYTHONPATH=. python scripts/check_job_heartbeat.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
tmp = Path(tempfile.mkdtemp(prefix="folio-heartbeat-"))
os.environ["FOLIO_DB"] = str(tmp / "folio.db")
os.environ["FOLIO_OUTPUT"] = str(tmp / "output")
os.environ["FLASK_SECRET_KEY"] = "heartbeat-check-secret-key-value-1234567"

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

# A build with no per-job heartbeat has no column, no pump and a one-argument
# sweep. Reached through these four shims so such a build reports every
# behaviour it gets wrong instead of stopping on an AttributeError at line one.
# Against a build that has them, every call goes straight through.
MISSING = "<not implemented>"
# 600 by default. None on a build that has no such setting at all.
HEARTBEAT_MAX_AGE = getattr(views, "JOB_HEARTBEAT_MAX_AGE", None)


def _claim(job_id: str, heartbeat: bool):
    try:
        return db.claim_job(job_id, heartbeat=heartbeat)
    except TypeError:
        return db.claim_job(job_id)


def beat_of(job_id: str):
    row = db.get_job(job_id)
    try:
        return row["heartbeat_at"]
    except (KeyError, IndexError):
        return MISSING


def beat(job_id: str) -> bool:
    writer = getattr(db, "beat_job", None)
    return bool(writer(job_id)) if writer else False


def sweep(max_age: int, heartbeat_max_age: int) -> int:
    try:
        return db.fail_stale_running_jobs(max_age, heartbeat_max_age)
    except TypeError:
        return db.fail_stale_running_jobs(max_age)


def token(path: str) -> str:
    page = client.get(path).data.decode()
    match = re.search(r'name="csrf_token" value="([^"]+)"', page)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


client.post("/signup", data={"email": "beat@example.com",
                            "password": "password123",
                            "csrf_token": token("/signup")})
user = int(db.get_user_by_email("beat@example.com")["id"])
db.grant_credits(user, 100, reason="test", reference="beat-seed")
with client.session_transaction() as browser_session:
    browser_session["user_id"] = user


def start_job(*, units: int = 1, claim: bool = True, heartbeat: bool = True) -> str:
    job_id = uuid.uuid4().hex
    assert db.enqueue_job(job_id, user, "Academic Accelerate - Year 5 - Sam",
                          units, REQUEST, True)
    if claim:
        assert _claim(job_id, heartbeat) is not None
    return job_id


def set_beat(job_id: str, seconds_ago: int) -> None:
    try:
        with db._cursor() as cur:
            cur.execute(db._q("UPDATE jobs SET heartbeat_at=? WHERE id=?"),
                        (int(time.time()) - seconds_ago, job_id))
    except Exception:
        pass          # no such column: a build without the heartbeat


def age_job(job_id: str, seconds_ago: int) -> None:
    with db._cursor() as cur:
        cur.execute(db._q("UPDATE jobs SET created_at=?, started_at=? WHERE id=?"),
                    (int(time.time()) - seconds_ago,
                     int(time.time()) - seconds_ago, job_id))


# --------------------------------------------------------------------------
print("== a job records that it is alive the moment it is claimed ==")
job = start_job()
check(beat_of(job) not in (None, MISSING),
      f"claiming stamps a first heartbeat ({beat_of(job)})",
      "without one there is a window where a running job looks like a job "
      "that never beat")
check(beat_of(job) not in (None, MISSING)
      and abs(int(beat_of(job)) - int(time.time())) < 5,
      "and the stamp is now, not the epoch")

# --------------------------------------------------------------------------
print("\n== the beat comes from its own thread, so a slow call still beats ==")
# This is the case that decides whether the whole idea is safe. Generation
# spends minutes inside one Gemini call and reports nothing while it does. The
# job below does nothing at all for three seconds, which stands in for that,
# and the beat still has to advance.
slow = start_job(heartbeat=False)   # no claim-time pump, so this one is mine
set_beat(slow, 120)
before_beat = beat_of(slow)
pump = getattr(db, "start_job_heartbeat", None)
stop = pump(slow, interval_seconds=1) if pump else None
blocking = threading.Event()
blocking.wait(3.2)          # the "LLM call": this thread reports nothing
after_beat = beat_of(slow)
check(MISSING not in (before_beat, after_beat) and before_beat is not None
      and after_beat is not None and int(after_beat) > int(before_beat),
      f"the heartbeat advanced ({before_beat} -> {after_beat}) while the job "
      f"itself did nothing observable",
      "a beat driven by pipeline stages would have gone quiet here, and the "
      "sweep would then kill jobs that were working perfectly well")
check(after_beat not in (None, MISSING)
      and time.time() - int(after_beat) < 2,
      f"and it is current (last beat {after_beat})")

# --------------------------------------------------------------------------
print("\n== the beat stops by itself when the job settles ==")
# Nothing has to remember to switch it off, which matters because the job can
# be settled from another process entirely.
db.finish_job(slow, path="done.pdf")
settled_at = beat_of(slow)
time.sleep(2.5)
check(beat_of(slow) == settled_at,
      "a settled job stops being beaten for")
check(getattr(db, "beat_job", None) is not None and db.beat_job(slow) is False,
      "beat_job reports False on a job that is no longer running, which is "
      "how the pump knows to exit")
check(pump is not None
      and not any(t.name.startswith(f"job-heartbeat-{slow[:8]}")
                  for t in threading.enumerate()),
      "and the thread is gone",
      f"threads still alive: {[t.name for t in threading.enumerate()]}")
if stop is not None:
    stop.set()

# --------------------------------------------------------------------------
print("\n== a job that stopped beating is failed and refunded in minutes ==")
before = db.credit_balance(user)
dead = start_job()
check(db.credit_balance(user) == before - 1, "the credit is spent")
# Eleven minutes since the last beat, but only eleven minutes old: nowhere
# near the 45 minute timeout, which is the whole point.
age_job(dead, 660)
set_beat(dead, 660)
check(HEARTBEAT_MAX_AGE is not None and HEARTBEAT_MAX_AGE <= 900,
      f"the heartbeat window is minutes, not the old 45 "
      f"({HEARTBEAT_MAX_AGE}s)")
check(660 < views.JOB_TIMEOUT_SECONDS,
      f"and this job is still well inside the old timeout "
      f"({views.JOB_TIMEOUT_SECONDS}s), so only the heartbeat can catch it")

settled = sweep(views.JOB_TIMEOUT_SECONDS, HEARTBEAT_MAX_AGE)
check(settled == 1, f"the sweep settles it ({settled} job(s))")
check(db.get_job(dead)["status"] == "error",
      f"the row stops saying running ({db.get_job(dead)['status']})")
check(db.credit_balance(user) == before,
      f"and the credit comes back (balance {db.credit_balance(user)}, "
      f"expected {before})",
      "settling a dead job without refunding it is the same bug in a "
      "different shirt")

# --------------------------------------------------------------------------
print("\n== the customer sees it, without waiting 45 minutes ==")
before = db.credit_balance(user)
watched = start_job()
age_job(watched, 700)
set_beat(watched, 700)
payload = client.get(f"/status/{watched}").get_json()
check(payload["status"] == "error",
      f"the progress page's own poll settles it ({payload})")
check("credit" in (payload.get("error") or "").lower(),
      f"and says the credit came back: {payload.get('error')!r}")
check(db.credit_balance(user) == before,
      f"which it did (balance {db.credit_balance(user)}, expected {before})")

before = db.credit_balance(user)
listed = start_job()
age_job(listed, 700)
set_beat(listed, 700)
page = client.get("/library").data.decode()
check(db.get_job(listed)["status"] == "error"
      and f"/progress/{listed}" not in page,
      "opening My booklets settles it, so the row stops saying Building",
      f"status {db.get_job(listed)['status']}")
check(db.credit_balance(user) == before,
      f"and that page refunded it too (balance {db.credit_balance(user)})")

# --------------------------------------------------------------------------
print("\n== a job that is still beating is never touched ==")
# The dangerous direction. A ten-week term plan runs for tens of minutes and
# is the A$39 one, so a sweep that kills it costs the customer real work.
before = db.credit_balance(user)
alive = uuid.uuid4().hex
assert db.enqueue_job(alive, user, "Term plan", 10, dict(REQUEST, is_term=True), True)
db.claim_job(alive)
age_job(alive, views.JOB_TIMEOUT_SECONDS - 120)   # 43 minutes in, still going
beat(alive)
settled = sweep(views.JOB_TIMEOUT_SECONDS, HEARTBEAT_MAX_AGE)
check(db.get_job(alive)["status"] == "running",
      f"a 43 minute term plan that beat a second ago keeps generating "
      f"({db.get_job(alive)['status']})",
      "killing live work is worse than the bug this replaces")
check(db.credit_balance(user) == before - 10,
      "and nothing was refunded out from under it")
check(client.get(f"/status/{alive}").get_json()["status"] == "running",
      "the progress poll leaves it alone as well")

# Only when it stops beating does it go, and then quickly.
set_beat(alive, (HEARTBEAT_MAX_AGE or 600) + 60)
sweep(views.JOB_TIMEOUT_SECONDS, HEARTBEAT_MAX_AGE)
check(db.get_job(alive)["status"] == "error"
      and db.credit_balance(user) == before,
      f"once it stops beating it is settled and all ten credits return "
      f"(balance {db.credit_balance(user)}, expected {before})")

# --------------------------------------------------------------------------
print("\n== a job that never beat still has the old timeout under it ==")
# Rows written before this column existed, jobs queued for a worker that never
# arrived, and any deployment running with the pump switched off.
before = db.credit_balance(user)
never = start_job(heartbeat=False)
check(beat_of(never) is None,
      "a job claimed with no pump behind it records no heartbeat",
      "a stamp that never advances would have the sweep kill live work")
settled = sweep(views.JOB_TIMEOUT_SECONDS, HEARTBEAT_MAX_AGE)
check(db.get_job(never)["status"] == "running",
      "so the heartbeat rule cannot touch it")
age_job(never, views.JOB_TIMEOUT_SECONDS + 60)
sweep(views.JOB_TIMEOUT_SECONDS, HEARTBEAT_MAX_AGE)
check(db.get_job(never)["status"] == "error" and db.credit_balance(user) == before,
      f"and the created_at backstop settles and refunds it "
      f"(balance {db.credit_balance(user)}, expected {before})")

before = db.credit_balance(user)
waiting = start_job(claim=False)
age_job(waiting, views.JOB_TIMEOUT_SECONDS + 60)
sweep(views.JOB_TIMEOUT_SECONDS, HEARTBEAT_MAX_AGE)
check(db.get_job(waiting)["status"] == "error" and db.credit_balance(user) == before,
      "a job left queued for a worker that never came is refunded too")

# --------------------------------------------------------------------------
print("\n== a restart settles what it killed, and nothing else ==")
# Boot cannot simply fail every running row: in queue mode the worker is a
# separate service and its jobs are legitimately still generating.
before = db.credit_balance(user)
killed = start_job()
set_beat(killed, (HEARTBEAT_MAX_AGE or 600) + 120)
survivor = start_job()
beat(survivor)
create_app()                      # the redeploy
check(db.get_job(killed)["status"] == "error",
      f"the job whose thread the restart killed is settled "
      f"({db.get_job(killed)['status']})")
check(db.get_job(survivor)["status"] == "running",
      f"a job still beating elsewhere is not "
      f"({db.get_job(survivor)['status']})",
      "a boot sweep that failed every running row would refund the worker's "
      "live jobs on every deploy")
check(db.credit_balance(user) == before - 1,
      f"exactly one of the two was refunded "
      f"(balance {db.credit_balance(user)}, expected {before - 1})")
db.fail_job_if_running(survivor, "tidy up")

# --------------------------------------------------------------------------
print("\n== the column reaches a database that already exists ==")
legacy = tmp / "legacy.db"
with sqlite3.connect(legacy) as conn:
    conn.execute("""CREATE TABLE jobs (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, status TEXT NOT NULL,
        label TEXT, error TEXT, path TEXT, dir TEXT,
        created_at INTEGER NOT NULL)""")
    conn.execute("INSERT INTO jobs (id,user_id,status,label,created_at) "
                 "VALUES ('old-job',1,'running','Old booklet',1)")

original = db.DB_PATH
try:
    db.DB_PATH = legacy
    db.init_db()
    db.init_db()                  # a redeploy runs it again
    with sqlite3.connect(legacy) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        kept = conn.execute(
            "SELECT status,heartbeat_at FROM jobs WHERE id='old-job'").fetchone()
    check("heartbeat_at" in columns,
          f"the migration adds it to an existing jobs table ({sorted(columns)})")
    check(kept == ("running", None),
          f"the row already there is intact, with no heartbeat invented for "
          f"it ({kept})")
except Exception as exc:                                        # noqa: BLE001
    check(False, "init_db upgrades an existing database", str(exc))
finally:
    db.DB_PATH = original

# Postgres gets the same column through the same list. There is no database to
# talk to here, so the statement itself is what gets checked.
import inspect  # noqa: E402

source = inspect.getsource(db.init_db)
check("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS heartbeat_at" in source,
      "and Postgres gets it from init_db's migration list, not only from "
      "CREATE TABLE, which does nothing on a database that already exists")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
