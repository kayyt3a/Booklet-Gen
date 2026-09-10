"""Checks the stored-file caps, measured by saving files and counting them back.

Every generated PDF goes into the database unless object storage is configured,
so these two caps are the main thing between the product and its disk budget. A
Supabase project ran out of disk IO with the loose cap at twenty: the database
became too slow to answer a query, and the site went down with it.

The caps are now three loose booklets per account and two weeks per plan, and
both are read from an environment variable so that moving to a paid instance is
a dashboard change rather than a deploy.

Two is the floor for a plan rather than one, and that is a curriculum
constraint, not a storage one. Week N tests the spelling list and the times
table SET IN WEEK N-1 (agents/spelling, agents/tables), so a tutor marking this
week needs last week's page in front of them. One week would break the only two
routines in the product that span booklets.

Everything below is measured by saving real files through db.save_job_file and
counting what survives, never by reading the constant back. The constant is
what the check is about; asserting it against itself would pass with the trim
deleted entirely.

The other half is the promise on the page. Four customer-facing pages quote a
retention number, and a page that states a number the code does not enforce is
how a customer finds out by losing a booklet, so those are asserted to quote
the constant rather than a literal.

    PYTHONPATH=. python scripts/check_file_retention.py
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("DATABASE_URL", None)
os.environ["FOLIO_DB"] = str(Path(tempfile.mkdtemp()) / "retention.sqlite")
logging.disable(logging.CRITICAL)

from booklet_gen.webapp import db                                 # noqa: E402

PASSED = 0
TOTAL = 0
ROOT = Path(__file__).resolve().parent.parent


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


db.init_db()
user_id = db.create_user("retention@example.com", "not-a-real-password")
if not isinstance(user_id, int):
    user_id = db.get_user_by_email("retention@example.com")["id"]


# Files are saved with a whole-second `created_at`, and this script saves
# several inside one second, so without help every timestamp would tie and
# "the newest three" would have nothing to sort by. A real account generates
# booklets minutes or hours apart, so each save here is backdated to put it in
# a distinct second. That is what makes the ordering assertions below about the
# trim rather than about SQLite's row order.
#
# The tie itself is a real if narrow gap: two one-off booklets finishing in the
# same second are trimmed in an arbitrary order, and the one deleted could be
# the newer. Plan weeks already carry a `plan_week DESC` tiebreak for exactly
# this reason, which covers the case that actually happens in volume. Closing
# it properly for loose booklets needs a sub-second or monotonic column, which
# is a schema migration and not worth running against a database that is
# already short of disk.
_clock = [0]


def save(label: str, *, plan_id=None, week=None) -> str:
    """Store one file the way a finished generation job does."""
    job_id = f"job-{label}"
    db.enqueue_job(job_id, user_id, label, 1, {}, False,
                   plan_id=plan_id, plan_week=week)
    db.save_job_file(job_id, user_id, f"{label}.pdf", "application/pdf",
                     b"%PDF-1.4 pretend booklet")
    _clock[0] += 1
    with db._cursor(transaction=True) as cur:
        cur.execute(db._q("UPDATE job_files SET created_at=? WHERE job_id=?"),
                    (1_700_000_000 + _clock[0], job_id))
    return job_id


def stored_for_plan(plan_id) -> list[str]:
    with db._cursor() as cur:
        cur.execute(db._q("""SELECT f.job_id FROM job_files f
                             JOIN jobs j ON j.id=f.job_id
                             WHERE j.plan_id=?
                             ORDER BY f.created_at DESC"""), (plan_id,))
        return [row["job_id"] for row in cur.fetchall()]


def stored_loose() -> list[str]:
    with db._cursor() as cur:
        cur.execute(db._q("""SELECT f.job_id FROM job_files f
                             JOIN jobs j ON j.id=f.job_id
                             WHERE j.user_id=? AND j.plan_id IS NULL
                             ORDER BY f.created_at DESC"""), (user_id,))
        return [row["job_id"] for row in cur.fetchall()]


print("\n== the caps are what the customer was told ==")

check(db.FILE_RETENTION_PER_USER == 3,
      f"a customer keeps {db.FILE_RETENTION_PER_USER} loose booklets",
      "the loose cap moved. Four pages quote this number to customers, and "
      "twenty of them in the database is what exhausted the disk IO budget")
check(db.PLAN_WEEK_RETENTION == 2,
      f"a plan keeps {db.PLAN_WEEK_RETENTION} weeks",
      "the plan cap moved. Below two, week N cannot test the spelling list "
      "set in week N-1, which is the one thing study plans exist to do")

print("\n== saving more loose booklets than the cap keeps only the newest ==")

# One more than the cap, so the trim has to fire exactly once.
saved = [save(f"loose-{i}") for i in range(db.FILE_RETENTION_PER_USER + 3)]
kept = stored_loose()
check(len(kept) == db.FILE_RETENTION_PER_USER,
      f"{len(kept)} file(s) stored after saving {len(saved)}",
      f"expected {db.FILE_RETENTION_PER_USER}. The trim is not firing, so the "
      "database grows without limit and the disk budget goes again")
check(set(kept) == set(saved[-db.FILE_RETENTION_PER_USER:]),
      "the ones kept are the most recently saved",
      f"kept {sorted(kept)}, expected the newest "
      f"{db.FILE_RETENTION_PER_USER}. A customer who generates a booklet and "
      "finds an older one downloadable instead has lost the one they wanted")

print("\n== a plan keeps its own newest weeks, counted per plan ==")

ladder = [{"week": w, "focus": f"w{w}"} for w in range(1, 11)]
plan_a = db.create_plan(user_id, "Ava", "accelerate", "Mathematics",
                        "Year 5", 10, ladder)
plan_b = db.create_plan(user_id, "Ben", "accelerate", "Mathematics",
                        "Year 3", 10, ladder)
for week in range(1, 6):
    save(f"a-week-{week}", plan_id=plan_a, week=week)
kept_a = stored_for_plan(plan_a)
check(len(kept_a) == db.PLAN_WEEK_RETENTION,
      f"plan A kept {len(kept_a)} week(s) after generating 5",
      f"expected {db.PLAN_WEEK_RETENTION}")
check(kept_a == ["job-a-week-5", "job-a-week-4"][:db.PLAN_WEEK_RETENTION],
      "and they are the last week and the one before it",
      f"kept {kept_a}. A tutor marking this week needs last week's page, "
      "because this week tests the spelling and tables set in it")

for week in range(1, 4):
    save(f"b-week-{week}", plan_id=plan_b, week=week)
check(len(stored_for_plan(plan_a)) == db.PLAN_WEEK_RETENTION,
      "generating for a second student does not evict the first student's weeks",
      "one busy student is pushing another student's weeks out, which is the "
      "per-account counting that study plans exist to avoid")

print("\n== a plan week and a loose booklet do not compete ==")

loose_before = len(stored_loose())
for week in range(4, 8):
    save(f"b-week-{week}", plan_id=plan_b, week=week)
check(len(stored_loose()) == loose_before,
      f"the account still has its {loose_before} loose booklet(s) after four "
      "more plan weeks",
      "plan weeks are being counted against the loose cap, so a tutor "
      "generating a term wipes the one-off booklets they bought separately")

print("\n== raising the cap is instant, and lowering it needs the script ==")

original = db.FILE_RETENTION_PER_USER
try:
    db.FILE_RETENTION_PER_USER = 6
    save("after-raise")
    check(len(stored_loose()) == 4,
          "raising the cap stops deleting immediately (kept 4 of 6 allowed)",
          "the trim ignores the constant, so the environment variable does "
          "not actually control anything")

    db.FILE_RETENTION_PER_USER = 2
    lowered = len(stored_loose())
    check(lowered == 4,
          f"lowering the cap alone deletes nothing yet ({lowered} still stored)",
          "this is the behaviour trim_stored_files.py exists for; if it "
          "changed, that script may now be unnecessary or wrong")

    sys.path.insert(0, str(ROOT / "scripts"))
    import trim_stored_files                                      # noqa: E402
    rows, _ = trim_stored_files.plan_the_trim()
    check(len(rows) == lowered - 2,
          f"the trim script finds the {len(rows)} file(s) beyond the new cap",
          "a lowered cap cannot be applied to an account that has stopped "
          "generating, so the promise on the page stays false for them")
    trim_stored_files.apply_the_trim(rows)
    check(len(stored_loose()) == 2,
          "and applying it brings the account within cap",
          "the script reports work it does not actually do")
    check(trim_stored_files.plan_the_trim()[0] == [],
          "running it again finds nothing, so it is safe to repeat",
          "the script is not idempotent and a second run would delete files "
          "that are within cap")
finally:
    db.FILE_RETENTION_PER_USER = original

print("\n== the pages quote the constant, not a number of their own ==")

for name, variable in (
    ("library.html", "retention"),
    ("pricing.html", "retention"),
    ("support.html", "retention"),
    ("plans.html", "plan_week_retention"),
):
    text = (ROOT / "booklet_gen" / "webapp" / "templates" / name).read_text(
        encoding="utf-8")
    check("{{ " + variable + " }}" in text,
          f"{name} renders {{{{ {variable} }}}} rather than a literal",
          "this page states a retention number of its own, so raising the cap "
          "on a bigger Supabase plan leaves the page lying to customers")

views = (ROOT / "booklet_gen" / "webapp" / "views.py").read_text(encoding="utf-8")
check("plan_week_retention=db.PLAN_WEEK_RETENTION" in views,
      "the plans page is passed the constant the trim uses",
      "the new hint on the plans page will render blank, which is worse than "
      "saying nothing because it looks like a bug")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
