"""Apply the current retention caps to files that are already stored.

The save-time trim in `db.save_job_file` only runs when an account saves its
NEXT file. That is the right place for it, but it means LOWERING a cap does
nothing to an account that has stopped generating: drop the loose-booklet cap
from twenty to three and a dormant account keeps all twenty until it comes back
and generates a twenty-first. When the reason for lowering the cap is that the
database has run out of disk, "until they come back" is not a plan.

So this applies the caps as they stand right now, everywhere, once:

    FILE_RETENTION_PER_USER   newest loose booklets kept per account
    PLAN_WEEK_RETENTION       newest weeks kept per study plan

Both are read from the same constants the app enforces, so this script has no
opinion of its own and cannot drift from the running product. Change the
environment variable, run this, and the stored files match the promise on the
page.

It is safe to run repeatedly and does nothing when everything is already within
cap. RAISING a cap makes it a no-op, which is the honest behaviour: a file
deleted under a lower cap is gone, and this cannot invent it back.

    PYTHONPATH=. python scripts/trim_stored_files.py              # report only
    PYTHONPATH=. python scripts/trim_stored_files.py --apply      # delete

Reports by default. Deleting a customer's booklet is not something a script
should do because it was run with no arguments.

A NOTE ON DISK IO, WHICH IS PROBABLY WHY YOU ARE HERE. This deletes rows, and
in Postgres a DELETE writes a log record per row and leaves the space to be
reclaimed later, so trimming a very large table costs real IO at exactly the
moment there is none to spare. That is the price of choosing WHICH files to
keep. If the intention is to remove every stored file rather than trim to a
cap, `TRUNCATE TABLE job_files` releases the disk immediately and almost for
free, and is the better tool for that job.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.webapp import db                                 # noqa: E402


def _rows_beyond_cap(cur, sql: str, params: tuple) -> list[dict]:
    cur.execute(db._q(sql), params)
    return [dict(row) for row in cur.fetchall()]


def plan_the_trim() -> tuple[list[dict], dict[str, int]]:
    """Every stored file that is beyond its cap, without deleting anything.

    Two queries rather than one per account: the window function ranks each
    file within its own account or plan, so this stays a single pass whatever
    the number of customers. The ordering matches `save_job_file` exactly,
    including the plan_week tiebreak, or this script and the app would disagree
    about which week is the newest when two were generated in the same second.
    """
    loose_sql = """
        SELECT job_id, storage_key, user_id, filename FROM (
            SELECT f.job_id, f.storage_key, j.user_id, f.filename,
                   ROW_NUMBER() OVER (PARTITION BY j.user_id
                                      ORDER BY f.created_at DESC) AS rank
            FROM job_files f JOIN jobs j ON j.id = f.job_id
            WHERE j.plan_id IS NULL
        ) ranked WHERE rank > ?"""
    plan_sql = """
        SELECT job_id, storage_key, plan_id, filename FROM (
            SELECT f.job_id, f.storage_key, j.plan_id, f.filename,
                   ROW_NUMBER() OVER (PARTITION BY j.plan_id
                                      ORDER BY f.created_at DESC,
                                               j.plan_week DESC) AS rank
            FROM job_files f JOIN jobs j ON j.id = f.job_id
            WHERE j.plan_id IS NOT NULL
        ) ranked WHERE rank > ?"""
    with db._cursor() as cur:
        loose = _rows_beyond_cap(cur, loose_sql, (db.FILE_RETENTION_PER_USER,))
        weeks = _rows_beyond_cap(cur, plan_sql, (db.PLAN_WEEK_RETENTION,))
    counts = {"loose booklets": len(loose), "plan weeks": len(weeks)}
    return loose + weeks, counts


def apply_the_trim(rows: list[dict]) -> int:
    """Delete the rows, and the objects any of them point at."""
    deleted = 0
    with db._cursor(transaction=True) as cur:
        for row in rows:
            cur.execute(db._q("DELETE FROM job_files WHERE job_id=?"),
                        (row["job_id"],))
            deleted += 1
    keys = [row["storage_key"] for row in rows if row.get("storage_key")]
    if keys:
        # Object storage is not in the transaction, so this happens after the
        # database is settled. An orphaned object costs storage; a row pointing
        # at a deleted object costs a customer a broken download.
        try:
            from booklet_gen.webapp import storage
            storage.delete(keys)
            print(f"  removed {len(keys)} object(s) from the storage bucket")
        except Exception as exc:
            print(f"  could not remove {len(keys)} storage object(s): {exc}")
            print("  the database rows are gone; these objects are now orphans")
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trim stored booklet files to the current retention caps.")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without this, only report")
    args = parser.parse_args()

    print(f"caps in force: {db.FILE_RETENTION_PER_USER} loose booklet(s) per "
          f"account, {db.PLAN_WEEK_RETENTION} week(s) per plan")
    print(f"database: {'Postgres' if db.is_postgres() else f'SQLite at {db.DB_PATH}'}")

    try:
        rows, counts = plan_the_trim()
    except Exception as exc:
        # Deliberately not calling init_db() to create the schema. This script
        # is most useful pointed at a database that is already struggling, and
        # init_db fires roughly thirty DDL statements per run; adding those to
        # a database with no disk budget left is how the outage started.
        print(f"could not read job_files: {exc}")
        print("Is DATABASE_URL pointing at the right database, and has the app "
              "created its schema there yet?")
        return 1
    for label, count in counts.items():
        print(f"  beyond cap: {count} {label}")
    if not rows:
        print("nothing to do: every stored file is within its cap")
        return 0

    if not args.apply:
        print(f"\n{len(rows)} file(s) WOULD be deleted. Re-run with --apply to "
              "delete them.")
        print("This is not reversible: the file is gone and the booklet cannot "
              "be downloaded again.")
        return 0

    print(f"\ndeleting {len(rows)} file(s)...")
    deleted = apply_the_trim(rows)
    print(f"deleted {deleted} file(s)")
    remaining, _ = plan_the_trim()
    if remaining:
        print(f"WARNING: {len(remaining)} file(s) are still beyond cap")
        return 1
    print("every stored file is now within its cap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
