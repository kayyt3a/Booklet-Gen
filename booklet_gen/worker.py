"""FolioAI background worker backed by the shared Postgres jobs table."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import time

from .jobs import execute_claimed_job
from .dbpool import is_postgres
from .webapp import db

log = logging.getLogger(__name__)
_stopping = False


def _stop(_signum, _frame) -> None:
    global _stopping
    _stopping = True
    log.info("worker will stop after the current job")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="Process at most one queued job, then exit")
    args = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    require_postgres = (os.environ.get("FOLIO_REQUIRE_POSTGRES") or "").strip().lower()
    if require_postgres in {"1", "true", "yes", "on"} and not is_postgres():
        log.error("FOLIO_REQUIRE_POSTGRES is enabled but DATABASE_URL is missing")
        return 2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    db.init_db()
    timeout = int(os.environ.get("FOLIO_JOB_TIMEOUT", "2700"))
    recovered = db.fail_stale_running_jobs(timeout)
    if recovered:
        log.warning("settled %d stale queued or running jobs", recovered)

    poll_seconds = max(0.5, float(os.environ.get("FOLIO_WORKER_POLL_SECONDS", "2")))
    # The web service enqueues but never generates, so if this loop is not
    # running a customer's booklet sits at "generating" for ever and the site
    # looks like it is working. The heartbeat is how the web service knows the
    # difference. db had the writer and the reader already; nothing called
    # either, so the table stayed empty and the outage stayed invisible.
    started_at = int(time.time())
    db.record_worker_heartbeat(started_at=started_at)
    while not _stopping:
        try:
            db.record_worker_heartbeat(started_at=started_at)
        except Exception:
            # A missed beat must not stop the worker generating. The reader
            # treats a stale beat as down, which is the safe direction.
            log.exception("could not record the worker heartbeat")
        try:
            job = db.claim_next_job()
        except Exception:
            log.exception("could not poll the generation queue")
            if args.once:
                return 1
            time.sleep(min(10.0, max(2.0, poll_seconds)))
            continue
        if job is None:
            if args.once:
                return 0
            time.sleep(poll_seconds)
            continue
        execute_claimed_job(job)
        if args.once:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
