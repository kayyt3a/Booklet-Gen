"""Data layer for the Folio web app: users and jobs.

Two backends behind one API:

* **Postgres** when DATABASE_URL is set. This is what deployments use, so
  accounts and job history survive restarts and redeploys.
* **SQLite** otherwise, for a local checkout with no services to run.

Passwords are hashed with werkzeug in both cases; no plaintext is ever stored.
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from werkzeug.security import generate_password_hash, check_password_hash

from ..dbpool import advisory_lock, get_pool, is_postgres

DB_PATH = Path(os.environ.get("FOLIO_DB", "folio.db"))

# Arbitrary, just needs to be distinct from other advisory-lock keys in this
# codebase (see rag/store.py, which uses a different one for its own schema).
_SCHEMA_LOCK_KEY = 72_461_001


def _q(sql: str) -> str:
    """SQLite uses ? placeholders, psycopg uses %s. Author SQL with ?."""
    return sql.replace("?", "%s") if is_postgres() else sql


@contextmanager
def _cursor():
    """Yield a cursor whose rows are addressable by column name on either
    backend. Commits on clean exit."""
    if is_postgres():
        from psycopg.rows import dict_row
        with get_pool().connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                yield cur
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    status     TEXT NOT NULL,
    label      TEXT,
    error      TEXT,
    path       TEXT,
    dir        TEXT,
    created_at BIGINT NOT NULL,
    units      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS jobs_user_created_idx ON jobs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
CREATE TABLE IF NOT EXISTS job_files (
    job_id     TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    mimetype   TEXT NOT NULL,
    data       BYTEA NOT NULL,
    bytes      INTEGER NOT NULL,
    created_at BIGINT NOT NULL
);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    status     TEXT NOT NULL,
    label      TEXT,
    error      TEXT,
    path       TEXT,
    dir        TEXT,
    created_at INTEGER NOT NULL,
    units      INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS jobs_user_created_idx ON jobs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
CREATE TABLE IF NOT EXISTS job_files (
    job_id     TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    mimetype   TEXT NOT NULL,
    data       BLOB NOT NULL,
    bytes      INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
"""


def init_db() -> None:
    if is_postgres():
        # Concurrent gunicorn workers can boot at the same instant against a
        # fresh database; serialize schema creation so only one actually races.
        with advisory_lock(_SCHEMA_LOCK_KEY) as conn:
            conn.execute(_PG_SCHEMA)
            # Existing deployments predate the units column (see the abuse
            # guard below). Backfilled to 1, which is what every old row was.
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS units"
                " INTEGER NOT NULL DEFAULT 1"
            )
    else:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        try:
            conn.executescript(_SQLITE_SCHEMA)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
            if "units" not in cols:
                conn.execute(
                    "ALTER TABLE jobs ADD COLUMN units INTEGER NOT NULL DEFAULT 1")
            conn.commit()
        finally:
            conn.close()


# ---------- users ----------

def create_user(email: str, password: str) -> int:
    email = email.strip().lower()
    pw = generate_password_hash(password)
    now = int(time.time())
    with _cursor() as cur:
        if is_postgres():
            cur.execute(
                "INSERT INTO users (email, password_hash, created_at)"
                " VALUES (%s,%s,%s) RETURNING id",
                (email, pw, now),
            )
            return int(cur.fetchone()["id"])
        cur.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
            (email, pw, now),
        )
        return int(cur.lastrowid)


def get_user_by_email(email: str):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM users WHERE email=?"), (email.strip().lower(),))
        return cur.fetchone()


def get_user(user_id: int):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM users WHERE id=?"), (user_id,))
        return cur.fetchone()


def verify_login(email: str, password: str):
    user = get_user_by_email(email)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


# ---------- jobs ----------

def create_job(job_id: str, user_id: int, label: str, units: int = 1) -> None:
    """Record a new job. `units` is how many booklets it will produce, which
    is what the abuse guard counts: a term plan is one job but ten booklets
    and ten booklets' worth of API spend."""
    with _cursor() as cur:
        cur.execute(
            _q("INSERT INTO jobs (id, user_id, status, label, created_at, units)"
               " VALUES (?,?,?,?,?,?)"),
            (job_id, user_id, "running", label, int(time.time()), max(1, int(units))),
        )


def finish_job(job_id: str, *, path: str = None, dir: str = None) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE jobs SET status='done', path=?, dir=?, error=NULL WHERE id=?"),
            (path, dir, job_id),
        )


def fail_job(job_id: str, error: str) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE jobs SET status='error', error=? WHERE id=?"),
            (error[:500], job_id),
        )


def fail_job_if_running(job_id: str, error: str) -> bool:
    """Fail a job only while it is still running.

    Used by the watchdog so a job that finished a moment before the timeout
    is not retroactively marked failed.
    """
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE jobs SET status='error', error=?"
               " WHERE id=? AND status='running'"),
            (error[:500], job_id),
        )
        return bool(cur.rowcount)


def fail_stale_running_jobs(max_age_seconds: int) -> int:
    """Settle jobs whose worker thread is gone.

    Generation runs in a background thread, so a redeploy, a crash or a free
    tier spin-down takes the thread with it and leaves the row saying
    "running" for ever. Anything older than the timeout could not still be
    alive: the process that owned it no longer exists.
    """
    cutoff = int(time.time()) - int(max_age_seconds)
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE jobs SET status='error', error=?"
               " WHERE status='running' AND created_at < ?"),
            ("Generation stopped before it finished. This usually means the "
             "server restarted mid-run. Please try again.", cutoff),
        )
        return int(cur.rowcount or 0)


def get_job(job_id: str):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM jobs WHERE id=?"), (job_id,))
        return cur.fetchone()


def list_jobs(user_id: int, limit: int = 50) -> list:
    """A user's generation history, newest first, with whether the file is
    still downloadable."""
    with _cursor() as cur:
        cur.execute(
            _q("""
            SELECT j.id, j.status, j.label, j.error, j.created_at,
                   f.filename, f.bytes
            FROM jobs j
            LEFT JOIN job_files f ON f.job_id = j.id
            WHERE j.user_id = ?
            ORDER BY j.created_at DESC
            LIMIT ?
            """),
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


# Keep the newest N deliverables per account. Booklet PDFs are megabytes and a
# free Postgres tier is ~500MB, so old blobs are dropped while the job rows
# stay, leaving the history intact with the download marked expired.
FILE_RETENTION_PER_USER = int(os.environ.get("FOLIO_FILE_RETENTION", "20"))


def save_job_file(job_id: str, user_id: int, filename: str,
                  mimetype: str, data: bytes) -> None:
    """Store the finished deliverable in the database so it survives restarts.

    Render's filesystem is ephemeral, so a PDF left only on disk disappears on
    the next deploy and the history page links to nothing.
    """
    payload = memoryview(data) if is_postgres() else sqlite3.Binary(data)
    with _cursor() as cur:
        cur.execute(
            _q("DELETE FROM job_files WHERE job_id=?"), (job_id,))
        cur.execute(
            _q("INSERT INTO job_files (job_id, filename, mimetype, data, bytes,"
               " created_at) VALUES (?,?,?,?,?,?)"),
            (job_id, filename, mimetype, payload, len(data), int(time.time())),
        )
        # Trim this user's older blobs.
        cur.execute(
            _q("""
            DELETE FROM job_files WHERE job_id IN (
                SELECT f.job_id FROM job_files f
                JOIN jobs j ON j.id = f.job_id
                WHERE j.user_id = ?
                ORDER BY f.created_at DESC
                LIMIT 1000 OFFSET ?
            )
            """),
            (user_id, FILE_RETENTION_PER_USER),
        )


def get_job_file(job_id: str):
    with _cursor() as cur:
        cur.execute(
            _q("SELECT filename, mimetype, data FROM job_files WHERE job_id=?"),
            (job_id,),
        )
        return cur.fetchone()


def _scalar(cur) -> int:
    row = cur.fetchone()
    if row is None:
        return 0
    # sqlite3.Row indexes positionally; the psycopg dict_row is by name.
    value = row["n"] if is_postgres() else row[0]
    return int(value or 0)


def booklets_started_last_24h(user_id: int) -> int:
    """Booklets (not jobs) a user has started in the last 24h.

    Counting jobs undercounted by a factor of ten for anyone ticking the term
    plan box, because that is one job row and ten generated booklets.
    """
    since = int(time.time()) - 86400
    with _cursor() as cur:
        cur.execute(
            _q("SELECT COALESCE(SUM(units),0) AS n FROM jobs"
               " WHERE user_id=? AND created_at>=?"),
            (user_id, since),
        )
        return _scalar(cur)


def booklets_started_globally_last_24h() -> int:
    """Booklets started by every account in the last 24h.

    Signup is free and unverified, so a per-account cap alone only costs an
    abuser the effort of making more accounts. This is the ceiling on the
    whole instance's API spend.
    """
    since = int(time.time()) - 86400
    with _cursor() as cur:
        cur.execute(
            _q("SELECT COALESCE(SUM(units),0) AS n FROM jobs WHERE created_at>=?"),
            (since,),
        )
        return _scalar(cur)


# ---------- account data: export and deletion ----------

def export_account(user_id: int) -> dict:
    """Everything this account holds, as plain data.

    The booklets themselves stay downloadable from the library; this is the
    record of what exists, which is what a data request actually asks for.
    """
    user = get_user(user_id)
    if user is None:
        return {}
    jobs = list_jobs(user_id, limit=10_000)
    return {
        "account": {
            "id": int(user["id"]),
            "email": user["email"],
            "created_at": int(user["created_at"]),
        },
        "booklets": [
            {
                "id": j["id"],
                "label": j["label"],
                "status": j["status"],
                "created_at": int(j["created_at"]),
                "filename": j["filename"],
                "bytes": j["bytes"],
                "error": j["error"],
            }
            for j in jobs
        ],
        "exported_at": int(time.time()),
    }


def delete_account(user_id: int) -> list:
    """Delete an account and everything stored for it.

    Returns the on-disk locations the deleted jobs referenced so the caller
    can clean up the (ephemeral) instance filesystem too. Ordering matters on
    SQLite, which does not enforce the cascade Postgres has.
    """
    with _cursor() as cur:
        cur.execute(
            _q("SELECT path, dir FROM jobs WHERE user_id=?"), (user_id,))
        rows = cur.fetchall()
        leftovers = [
            ((r["path"] if is_postgres() else r[0]),
             (r["dir"] if is_postgres() else r[1]))
            for r in rows
        ]
        cur.execute(
            _q("DELETE FROM job_files WHERE job_id IN"
               " (SELECT id FROM jobs WHERE user_id=?)"),
            (user_id,),
        )
        cur.execute(_q("DELETE FROM jobs WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM users WHERE id=?"), (user_id,))
    return leftovers
