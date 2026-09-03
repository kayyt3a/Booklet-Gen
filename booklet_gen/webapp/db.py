"""Persistent data for accounts, queued generation, files, and purchases."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from ..dbpool import advisory_lock, get_pool, is_postgres

log = logging.getLogger(__name__)

# Booklets a new account starts with. Written once: the value appears at the
# signup grant and in two backfill migrations, and three copies of a number
# that decides what a customer gets for free is three chances to disagree.
#
# The ledger is idempotent on a "welcome:<user_id>" reference, so raising this
# does not re-grant to accounts that already have their welcome credit. Only
# new signups see the new number.
WELCOME_CREDITS = 2
DB_PATH = Path(os.environ.get("FOLIO_DB", "folio.db"))
FILE_RETENTION_PER_USER = int(os.environ.get("FOLIO_FILE_RETENTION", "20"))
# Kept per plan, not per account. A tutor running fifteen students wants the
# newest few weeks of each of them; counting all of it against one per-account
# cap would let one busy student evict another student's weeks, which is the
# inconsistency study plans exist to remove. Loose booklets that belong to no
# plan are still capped per account by the number above.
PLAN_WEEK_RETENTION = int(os.environ.get("FOLIO_PLAN_WEEK_RETENTION", "3"))
_SCHEMA_LOCK_KEY = 72_461_001


def _q(sql: str) -> str:
    return sql.replace("?", "%s") if is_postgres() else sql


@contextmanager
def _cursor(transaction: bool = False):
    """Yield name-addressable rows, optionally inside one real transaction."""
    if is_postgres():
        from psycopg.rows import dict_row
        with get_pool().connection() as conn:
            if transaction:
                with conn.transaction():
                    with conn.cursor(row_factory=dict_row) as cur:
                        yield cur
            else:
                with conn.cursor(row_factory=dict_row) as cur:
                    yield cur
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        if transaction:
            conn.execute("BEGIN IMMEDIATE")
        yield conn.cursor()
        conn.commit()
    finally:
        conn.close()


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT NOT NULL,
    created_at          BIGINT NOT NULL,
    email_verified      BOOLEAN NOT NULL DEFAULT TRUE,
    password_changed_at BIGINT NOT NULL DEFAULT 0,
    stripe_customer_id  TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id             TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status         TEXT NOT NULL,
    label          TEXT,
    error          TEXT,
    internal_error TEXT,
    path           TEXT,
    dir            TEXT,
    created_at     BIGINT NOT NULL,
    started_at     BIGINT,
    completed_at   BIGINT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    units          INTEGER NOT NULL DEFAULT 1,
    credit_units   INTEGER NOT NULL DEFAULT 0,
    request_json   TEXT,
    heartbeat_at   BIGINT
);
CREATE INDEX IF NOT EXISTS jobs_user_created_idx ON jobs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs (status, created_at);
CREATE TABLE IF NOT EXISTS job_files (
    job_id       TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    filename     TEXT NOT NULL,
    mimetype     TEXT NOT NULL,
    data         BYTEA NOT NULL,
    storage_key  TEXT,
    bytes        INTEGER NOT NULL,
    created_at   BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_ledger (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    reference  TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(user_id, reference)
);
CREATE INDEX IF NOT EXISTS credits_user_created_idx
    ON credit_ledger (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS payments (
    checkout_session_id TEXT PRIMARY KEY,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_key          TEXT NOT NULL,
    units                INTEGER NOT NULL,
    amount_total         INTEGER,
    currency             TEXT,
    status               TEXT NOT NULL,
    payment_intent_id    TEXT,
    reversed_units       INTEGER NOT NULL DEFAULT 0,
    created_at           BIGINT NOT NULL,
    updated_at           BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_user_created_idx
    ON payments (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS rate_limits (
    bucket_key     TEXT PRIMARY KEY,
    window_started BIGINT NOT NULL,
    hits           INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_name TEXT PRIMARY KEY,
    started_at  BIGINT NOT NULL,
    heartbeat_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS booklet_feedback (
    job_id       TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating       INTEGER NOT NULL,
    question_ref TEXT,
    comment      TEXT,
    created_at   BIGINT NOT NULL,
    updated_at   BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_created_idx
    ON booklet_feedback (created_at DESC);
CREATE TABLE IF NOT EXISTS study_plans (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    student_name TEXT NOT NULL,
    program      TEXT NOT NULL,
    subject      TEXT,
    year_level   TEXT NOT NULL,
    total_weeks  INTEGER NOT NULL,
    ladder_json  TEXT NOT NULL,
    created_at   BIGINT NOT NULL,
    archived_at  BIGINT
);
CREATE INDEX IF NOT EXISTS study_plans_user_idx
    ON study_plans (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS study_plan_weeks (
    plan_id       INTEGER NOT NULL REFERENCES study_plans(id) ON DELETE CASCADE,
    week          INTEGER NOT NULL,
    job_id        TEXT,
    taught        TEXT,
    spelling_json TEXT,
    tables_table  INTEGER,
    generated_at  BIGINT NOT NULL,
    PRIMARY KEY (plan_id, week)
);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT NOT NULL,
    created_at          INTEGER NOT NULL,
    email_verified      INTEGER NOT NULL DEFAULT 1,
    password_changed_at INTEGER NOT NULL DEFAULT 0,
    stripe_customer_id  TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id             TEXT PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    status         TEXT NOT NULL,
    label          TEXT,
    error          TEXT,
    internal_error TEXT,
    path           TEXT,
    dir            TEXT,
    created_at     INTEGER NOT NULL,
    started_at     INTEGER,
    completed_at   INTEGER,
    attempts       INTEGER NOT NULL DEFAULT 0,
    units          INTEGER NOT NULL DEFAULT 1,
    credit_units   INTEGER NOT NULL DEFAULT 0,
    request_json   TEXT,
    heartbeat_at   INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS jobs_user_created_idx ON jobs (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs (status, created_at);
CREATE TABLE IF NOT EXISTS job_files (
    job_id       TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    mimetype     TEXT NOT NULL,
    data         BLOB NOT NULL,
    storage_key  TEXT,
    bytes        INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS credit_ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    delta      INTEGER NOT NULL,
    reason     TEXT NOT NULL,
    reference  TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, reference),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS credits_user_created_idx
    ON credit_ledger (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS payments (
    checkout_session_id TEXT PRIMARY KEY,
    user_id              INTEGER NOT NULL,
    product_key          TEXT NOT NULL,
    units                INTEGER NOT NULL,
    amount_total         INTEGER,
    currency             TEXT,
    status               TEXT NOT NULL,
    payment_intent_id    TEXT,
    reversed_units       INTEGER NOT NULL DEFAULT 0,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS payments_user_created_idx
    ON payments (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS rate_limits (
    bucket_key     TEXT PRIMARY KEY,
    window_started INTEGER NOT NULL,
    hits           INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_name  TEXT PRIMARY KEY,
    started_at   INTEGER NOT NULL,
    heartbeat_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS booklet_feedback (
    job_id       TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    rating       INTEGER NOT NULL,
    question_ref TEXT,
    comment      TEXT,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS feedback_created_idx
    ON booklet_feedback (created_at DESC);
CREATE TABLE IF NOT EXISTS study_plans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    student_name TEXT NOT NULL,
    program      TEXT NOT NULL,
    subject      TEXT,
    year_level   TEXT NOT NULL,
    total_weeks  INTEGER NOT NULL,
    ladder_json  TEXT NOT NULL,
    created_at   INTEGER NOT NULL,
    archived_at  INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS study_plans_user_idx
    ON study_plans (user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS study_plan_weeks (
    plan_id       INTEGER NOT NULL,
    week          INTEGER NOT NULL,
    job_id        TEXT,
    taught        TEXT,
    spelling_json TEXT,
    tables_table  INTEGER,
    generated_at  INTEGER NOT NULL,
    PRIMARY KEY (plan_id, week),
    FOREIGN KEY(plan_id) REFERENCES study_plans(id)
);
"""


def _sqlite_add_columns(conn, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db() -> None:
    now = int(time.time())
    if is_postgres():
        with advisory_lock(_SCHEMA_LOCK_KEY) as conn:
            conn.execute(_PG_SCHEMA)
            migrations = (
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT TRUE",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_changed_at BIGINT NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS units INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS internal_error TEXT",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS started_at BIGINT",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS completed_at BIGINT",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS credit_units INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS request_json TEXT",
                "ALTER TABLE job_files ADD COLUMN IF NOT EXISTS storage_key TEXT",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_intent_id TEXT",
                "ALTER TABLE payments ADD COLUMN IF NOT EXISTS reversed_units INTEGER NOT NULL DEFAULT 0",
                "CREATE INDEX IF NOT EXISTS payments_intent_idx ON payments (payment_intent_id)",
                # Which plan week this job is, so file retention can keep the
                # newest weeks per plan rather than per account. Nullable: a
                # one-off booklet belongs to no plan and never will.
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS plan_id INTEGER",
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS plan_week INTEGER",
                "CREATE INDEX IF NOT EXISTS jobs_plan_idx ON jobs (plan_id)",
                # When this job last proved it was still alive. NULL on every
                # row that predates the column and on every job that never
                # beat, which is why the created_at timeout stays as the
                # backstop rather than being replaced by this.
                "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS heartbeat_at BIGINT",
            )
            for statement in migrations:
                conn.execute(statement)
            conn.execute(
                """INSERT INTO credit_ledger
                   (user_id, delta, reason, reference, created_at)
                   SELECT id, %s, 'welcome credit', 'welcome:' || id::text, %s
                   FROM users ON CONFLICT (user_id, reference) DO NOTHING""",
                (WELCOME_CREDITS, now),
            )
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        conn.executescript(_SQLITE_SCHEMA)
        _sqlite_add_columns(conn, "users", {
            "email_verified": "INTEGER NOT NULL DEFAULT 1",
            "password_changed_at": "INTEGER NOT NULL DEFAULT 0",
            "stripe_customer_id": "TEXT",
        })
        _sqlite_add_columns(conn, "jobs", {
            "units": "INTEGER NOT NULL DEFAULT 1",
            "internal_error": "TEXT",
            "started_at": "INTEGER",
            "completed_at": "INTEGER",
            "attempts": "INTEGER NOT NULL DEFAULT 0",
            "credit_units": "INTEGER NOT NULL DEFAULT 0",
            "request_json": "TEXT",
            "plan_id": "INTEGER",
            "plan_week": "INTEGER",
            "heartbeat_at": "INTEGER",
        })
        conn.execute("CREATE INDEX IF NOT EXISTS jobs_plan_idx ON jobs (plan_id)")
        _sqlite_add_columns(conn, "job_files", {"storage_key": "TEXT"})
        _sqlite_add_columns(conn, "payments", {
            "payment_intent_id": "TEXT",
            "reversed_units": "INTEGER NOT NULL DEFAULT 0",
        })
        conn.execute("CREATE INDEX IF NOT EXISTS payments_intent_idx "
                     "ON payments (payment_intent_id)")
        conn.execute(
            """INSERT OR IGNORE INTO credit_ledger
               (user_id, delta, reason, reference, created_at)
               SELECT id, ?, 'welcome credit', 'welcome:' || id, ? FROM users""",
            (WELCOME_CREDITS, now),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- users ----------

def create_user(email: str, password: str, *, email_verified: bool = True) -> int:
    email = email.strip().lower()
    password_hash = generate_password_hash(password)
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute(
                """INSERT INTO users
                   (email, password_hash, created_at, email_verified, password_changed_at)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (email, password_hash, now, bool(email_verified), now),
            )
            user_id = int(cur.fetchone()["id"])
        else:
            cur.execute(
                """INSERT INTO users
                   (email, password_hash, created_at, email_verified, password_changed_at)
                   VALUES (?,?,?,?,?)""",
                (email, password_hash, now, int(email_verified), now),
            )
            user_id = int(cur.lastrowid)
        cur.execute(
            _q("""INSERT INTO credit_ledger
                (user_id, delta, reason, reference, created_at)
                VALUES (?,?,?,?,?)"""),
            (user_id, WELCOME_CREDITS, "welcome credit", f"welcome:{user_id}", now),
        )
    return user_id


def get_user_by_email(email: str):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM users WHERE email=?"), (email.strip().lower(),))
        return cur.fetchone()


def health_check() -> bool:
    with _cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone() is not None


def record_worker_heartbeat(worker_name: str = "generation", *,
                            started_at: int | None = None,
                            now: int | None = None) -> None:
    """Persist one low-frequency liveness signal from a generation worker."""
    name = str(worker_name).strip()[:80]
    if not name:
        raise ValueError("Worker name cannot be empty.")
    heartbeat_at = int(time.time()) if now is None else int(now)
    worker_started_at = heartbeat_at if started_at is None else int(started_at)
    with _cursor() as cur:
        if is_postgres():
            cur.execute(
                """INSERT INTO worker_heartbeats
                   (worker_name,started_at,heartbeat_at) VALUES (%s,%s,%s)
                   ON CONFLICT (worker_name) DO UPDATE SET
                   started_at=EXCLUDED.started_at,
                   heartbeat_at=EXCLUDED.heartbeat_at""",
                (name, worker_started_at, heartbeat_at),
            )
        else:
            cur.execute(
                """INSERT INTO worker_heartbeats
                   (worker_name,started_at,heartbeat_at) VALUES (?,?,?)
                   ON CONFLICT (worker_name) DO UPDATE SET
                   started_at=excluded.started_at,
                   heartbeat_at=excluded.heartbeat_at""",
                (name, worker_started_at, heartbeat_at),
            )


def get_worker_heartbeat(worker_name: str = "generation") -> dict | None:
    with _cursor() as cur:
        cur.execute(
            _q("SELECT * FROM worker_heartbeats WHERE worker_name=?"),
            (str(worker_name).strip()[:80],),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def worker_status(max_age_seconds: int = 120, *,
                  worker_name: str = "generation",
                  now: int | None = None) -> dict:
    """Return non-secret liveness details suitable for health and admin views."""
    checked_at = int(time.time()) if now is None else int(now)
    row = get_worker_heartbeat(worker_name)
    if row is None:
        return {
            "status": "never_seen",
            "heartbeat_at": None,
            "started_at": None,
            "age_seconds": None,
        }
    heartbeat_at = int(row["heartbeat_at"])
    age = max(0, checked_at - heartbeat_at)
    return {
        "status": "healthy" if age <= max(1, int(max_age_seconds)) else "stale",
        "heartbeat_at": heartbeat_at,
        "started_at": int(row["started_at"]),
        "age_seconds": age,
    }


def get_user(user_id: int):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM users WHERE id=?"), (user_id,))
        return cur.fetchone()


def verify_login(email: str, password: str):
    user = get_user_by_email(email)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


def mark_email_verified(user_id: int) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE users SET email_verified=? WHERE id=?"),
            (True if is_postgres() else 1, user_id),
        )


def update_password(user_id: int, password: str) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE users SET password_hash=?, password_changed_at=? WHERE id=?"),
            (generate_password_hash(password), int(time.time()), user_id),
        )


def set_stripe_customer(user_id: int, customer_id: str) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE users SET stripe_customer_id=? WHERE id=?"),
            (customer_id, user_id),
        )


def rate_limit_hit(bucket_key: str, limit: int, window_seconds: int) -> bool:
    """Count one sensitive action in a database-backed fixed window."""
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute(
                """INSERT INTO rate_limits (bucket_key,window_started,hits)
                   VALUES (%s,%s,0) ON CONFLICT (bucket_key) DO NOTHING""",
                (bucket_key, now),
            )
            cur.execute(
                "SELECT * FROM rate_limits WHERE bucket_key=%s FOR UPDATE",
                (bucket_key,),
            )
        else:
            cur.execute(
                "SELECT * FROM rate_limits WHERE bucket_key=?",
                (bucket_key,),
            )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                _q("""INSERT INTO rate_limits
                    (bucket_key,window_started,hits) VALUES (?,?,1)"""),
                (bucket_key, now),
            )
            return True
        started = int(row["window_started"])
        hits = int(row["hits"])
        if now - started >= int(window_seconds):
            cur.execute(
                _q("""UPDATE rate_limits SET window_started=?,hits=1
                    WHERE bucket_key=?"""),
                (now, bucket_key),
            )
            return True
        hits += 1
        cur.execute(
            _q("UPDATE rate_limits SET hits=? WHERE bucket_key=?"),
            (hits, bucket_key),
        )
        return hits <= int(limit)


# ---------- credits and payments ----------

def _scalar(cur) -> int:
    row = cur.fetchone()
    if row is None:
        return 0
    return int((row["n"] if is_postgres() else row[0]) or 0)


def credit_balance(user_id: int) -> int:
    with _cursor() as cur:
        cur.execute(
            _q("SELECT COALESCE(SUM(delta),0) AS n FROM credit_ledger WHERE user_id=?"),
            (user_id,),
        )
        return _scalar(cur)


def adjust_credits(user_id: int, delta: int, reason: str, reference: str) -> bool:
    """Move a balance by `delta`, once per reference. True if it moved.

    The ledger has always held negative entries (a queued job reserves its
    credits with one), but nothing outside job reservation could write one, so
    a refunded or charged-back purchase left its credits behind. This is the
    single writer for a correction in either direction, and every entry keeps
    the reason and reference that produced it.

    A balance is allowed to go below zero. A customer who bought ten booklets,
    generated all ten, and then charged the payment back has taken ten
    booklets they did not pay for, and zeroing them out would quietly write
    that off; the negative balance is the debt, and enqueue_job will not start
    new work until it is settled.
    """
    delta = int(delta)
    if delta == 0:
        raise ValueError("A credit adjustment must be non-zero.")
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        return _adjust_credits_in(cur, user_id, delta, reason, reference, now)


def _adjust_credits_in(cur, user_id: int, delta: int, reason: str,
                       reference: str, now: int) -> bool:
    """The ledger write itself, for callers already holding a transaction."""
    if is_postgres():
        cur.execute(
            """INSERT INTO credit_ledger
               (user_id, delta, reason, reference, created_at)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (user_id, reference) DO NOTHING RETURNING id""",
            (user_id, int(delta), reason, reference, now),
        )
        return cur.fetchone() is not None
    cur.execute(
        """INSERT OR IGNORE INTO credit_ledger
           (user_id, delta, reason, reference, created_at)
           VALUES (?,?,?,?,?)""",
        (user_id, int(delta), reason, reference, now),
    )
    return bool(cur.rowcount)


def grant_credits(user_id: int, units: int, reason: str, reference: str) -> bool:
    """Add credits. Kept positive-only so no existing caller can subtract."""
    if int(units) <= 0:
        raise ValueError("Credit grants must be positive.")
    return adjust_credits(user_id, int(units), reason, reference)


def record_payment_and_credit(session_id: str, user_id: int, product_key: str,
                              units: int, amount_total: int | None,
                              currency: str | None,
                              payment_intent_id: str | None = None) -> bool:
    """Fulfil one Stripe checkout exactly once, even under concurrent webhooks.

    The payment intent is stored because a later refund or chargeback arrives
    as a charge event, which names the intent and never the checkout session.
    Without it there is no way back from "this money was taken back" to "these
    credits were granted" that does not cost another call to Stripe.
    """
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute(
                """INSERT INTO payments
                   (checkout_session_id,user_id,product_key,units,amount_total,
                    currency,status,payment_intent_id,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,'paid',%s,%s,%s)
                   ON CONFLICT (checkout_session_id) DO NOTHING
                   RETURNING checkout_session_id""",
                (session_id, user_id, product_key, units, amount_total,
                 currency, payment_intent_id, now, now),
            )
            inserted = cur.fetchone() is not None
        else:
            cur.execute(
                """INSERT OR IGNORE INTO payments
                   (checkout_session_id,user_id,product_key,units,amount_total,
                    currency,status,payment_intent_id,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,'paid',?,?,?)""",
                (session_id, user_id, product_key, units, amount_total,
                 currency, payment_intent_id, now, now),
            )
            inserted = bool(cur.rowcount)
        if not inserted:
            return False
        cur.execute(
            _q("""INSERT INTO credit_ledger
                (user_id,delta,reason,reference,created_at) VALUES (?,?,?,?,?)"""),
            (user_id, int(units), f"Stripe purchase: {product_key}",
             f"payment:{session_id}", now),
        )
        return True


def list_payments(user_id: int, limit: int = 50) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            _q("SELECT * FROM payments WHERE user_id=? ORDER BY created_at DESC LIMIT ?"),
            (user_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def find_payment(*, session_id: str | None = None,
                 payment_intent_id: str | None = None) -> dict | None:
    """The payment row for a checkout session or a Stripe payment intent."""
    if session_id:
        column, value = "checkout_session_id", session_id
    elif payment_intent_id:
        column, value = "payment_intent_id", payment_intent_id
    else:
        return None
    with _cursor() as cur:
        cur.execute(
            _q(f"""SELECT * FROM payments WHERE {column}=?
                   ORDER BY created_at LIMIT 1"""),
            (value,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def attach_payment_intent(session_id: str, payment_intent_id: str) -> None:
    """Backfill the intent on a payment recorded before it was stored."""
    with _cursor() as cur:
        cur.execute(
            _q("""UPDATE payments SET payment_intent_id=?, updated_at=?
                  WHERE checkout_session_id=? AND payment_intent_id IS NULL"""),
            (payment_intent_id, int(time.time()), session_id),
        )


def reverse_payment_credits(session_id: str, reversed_total: int,
                            status: str, reason: str, reference: str) -> int:
    """Take back credits from a payment whose money went back. Units removed.

    `reversed_total` is the total that should stand reversed for this payment,
    not an increment, because Stripe redelivers webhooks and a partial refund
    can be followed by another one. Only the difference is written, so a
    repeated delivery of the same event does nothing.

    Reversal only ever goes up. Restoring credits after a dispute is won is
    deliberately not automatic: it is a decision about a customer who filed a
    chargeback, and it belongs to a person, through the audited admin
    adjustment, not to a webhook.
    """
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute(
                "SELECT * FROM payments WHERE checkout_session_id=%s FOR UPDATE",
                (session_id,),
            )
        else:
            cur.execute(
                "SELECT * FROM payments WHERE checkout_session_id=?", (session_id,),
            )
        row = cur.fetchone()
        if row is None:
            return 0
        payment = dict(row)
        units = int(payment["units"])
        already = int(payment.get("reversed_units") or 0)
        target = max(0, min(int(reversed_total), units))
        # Status still moves on a full-value dispute that a refund had already
        # covered, so the row says what happened even when no credit moves.
        if target <= already:
            if status and status != payment["status"]:
                cur.execute(
                    _q("""UPDATE payments SET status=?, updated_at=?
                          WHERE checkout_session_id=?"""),
                    (status, now, session_id),
                )
            return 0
        delta = target - already
        wrote = _adjust_credits_in(
            cur, int(payment["user_id"]), -delta, reason, reference, now)
        if not wrote:
            # The same reference has already been applied. Leave the counter
            # alone rather than record a reversal that never hit the ledger.
            return 0
        cur.execute(
            _q("""UPDATE payments SET reversed_units=?, status=?, updated_at=?
                  WHERE checkout_session_id=?"""),
            (target, status or payment["status"], now, session_id),
        )
        return delta


# ---------- jobs ----------

def create_job(job_id: str, user_id: int, label: str, units: int = 1) -> None:
    """Legacy immediate job creation retained for checks and local fixtures."""
    now = int(time.time())
    with _cursor() as cur:
        cur.execute(
            _q("""INSERT INTO jobs
                (id,user_id,status,label,created_at,started_at,attempts,units)
                VALUES (?,?,?,?,?,?,?,?)"""),
            (job_id, user_id, "running", label, now, now, 1, max(1, int(units))),
        )


def enqueue_job(job_id: str, user_id: int, label: str, units: int,
                request_data: dict, reserve_credits: bool,
                daily_limit: int | None = None,
                global_daily_limit: int | None = None,
                plan_id: int | None = None,
                plan_week: int | None = None) -> bool:
    """Atomically reserve credits and create a queued generation job.

    The abuse caps are counted here, inside the same transaction as the
    insert, and not only in the caller. views._quota_allows reads the counts
    on a plain cursor and then enqueues separately, so concurrent requests all
    saw the same pre-insert total: twelve simultaneous posts cleared a limit of
    three. That caller stays, because it produces the specific message a
    customer should see, but it is a courtesy check rather than the guard.

    The global ceiling matters most. It is the only thing bounding how much
    Gemini spend a single day can produce, and during the launch window
    DEPLOY.md recommends running with payments off, where no credit reservation
    stands in the way either.
    """
    now = int(time.time())
    units = max(1, int(units))
    since = now - 86400
    with _cursor(transaction=True) as cur:
        if reserve_credits:
            if is_postgres():
                cur.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user_id,))
            cur.execute(
                _q("SELECT COALESCE(SUM(delta),0) AS n FROM credit_ledger WHERE user_id=?"),
                (user_id,),
            )
            if _scalar(cur) < units:
                return False
        if daily_limit is not None:
            cur.execute(
                _q("""SELECT COALESCE(SUM(units),0) AS n FROM jobs
                    WHERE user_id=? AND created_at>=?"""),
                (user_id, since),
            )
            if _scalar(cur) + units > daily_limit:
                return False
        if global_daily_limit is not None:
            cur.execute(
                _q("SELECT COALESCE(SUM(units),0) AS n FROM jobs WHERE created_at>=?"),
                (since,),
            )
            if _scalar(cur) + units > global_daily_limit:
                return False
        cur.execute(
            _q("""INSERT INTO jobs
                (id,user_id,status,label,created_at,units,credit_units,
                 request_json,plan_id,plan_week)
                VALUES (?,?,?,?,?,?,?,?,?,?)"""),
            (job_id, user_id, "queued", label, now, units,
             units if reserve_credits else 0,
             json.dumps(request_data, separators=(",", ":")),
             plan_id, plan_week),
        )
        if reserve_credits:
            cur.execute(
                _q("""INSERT INTO credit_ledger
                    (user_id,delta,reason,reference,created_at) VALUES (?,?,?,?,?)"""),
                (user_id, -units, "generation reserved", f"job:{job_id}:reserve", now),
            )
        return True


# ---------- per-job heartbeat ----------
#
# The worker heartbeat above says whether a generating *process* exists. This
# says whether one particular job is still being generated, which is a
# different question and the one a customer's spinner depends on.
#
# Why it has to exist: the only way to tell a slow job from a dead one used to
# be its age, so FOLIO_JOB_TIMEOUT had to be set high enough for the slowest
# legitimate work (a ten-week term plan) and ended up at 45 minutes. A job
# whose thread died after ten seconds still showed a spinner for the rest of
# those 45 minutes with the credit spent.
#
# Why it beats from its own thread rather than between pipeline stages: a
# single Gemini call is bounded by llm/gemini.py's per-attempt timeout and
# retry deadline, not by anything shorter, so a beat that only ticked between
# stages would go quiet during exactly the slow generation it is meant to
# distinguish from a dead one, and the sweep would then kill live work. Killing
# a live job is worse than the bug being fixed, so the beat is deliberately
# independent of what the job is doing.

JOB_HEARTBEAT_SECONDS = max(
    0, int(os.environ.get("FOLIO_JOB_HEARTBEAT_SECONDS", "30")))


def beat_job(job_id: str, now: int | None = None) -> bool:
    """Stamp one job as still alive. False once it is no longer running.

    The status condition is what lets the pump below stop by itself: when the
    job finishes, fails or is cancelled, this stops matching and the thread
    exits. Nothing has to remember to turn it off, which matters because the
    settling can happen in another process entirely.
    """
    stamp = int(time.time()) if now is None else int(now)
    with _cursor() as cur:
        cur.execute(
            _q("UPDATE jobs SET heartbeat_at=? WHERE id=? AND status='running'"),
            (stamp, job_id),
        )
        return (cur.rowcount or 0) > 0


def start_job_heartbeat(job_id: str, interval_seconds: int | None = None):
    """Beat for `job_id` until it stops running. Returns a stop Event.

    A daemon thread, so it dies with the process. That is the point: when a
    Render deploy or an idle spin-down takes the web service down mid-job, the
    beats stop at the same instant the generation does, and the sweep can tell.
    """
    stop = threading.Event()
    interval = (JOB_HEARTBEAT_SECONDS if interval_seconds is None
                else int(interval_seconds))
    if interval <= 0:
        return stop

    def _pump() -> None:
        while not stop.wait(interval):
            try:
                if not beat_job(job_id):
                    return
            except Exception as exc:
                # A missed beat must not stop generation, and one blip must not
                # end the pump: the sweep threshold has room for many misses.
                log.warning("could not beat for job %s: %s", job_id, exc)

    threading.Thread(target=_pump, name=f"job-heartbeat-{job_id[:8]}",
                     daemon=True).start()
    return stop


def _claim_where(where_sql: str, params: tuple, heartbeat: bool = True):
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute(
                f"""SELECT id FROM jobs WHERE {where_sql}
                    ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                params,
            )
        else:
            cur.execute(
                f"SELECT id FROM jobs WHERE {where_sql} ORDER BY created_at LIMIT 1",
                params,
            )
        picked = cur.fetchone()
        if picked is None:
            return None
        job_id = picked["id"] if is_postgres() else picked[0]
        # The first beat, written by the claim itself so there is no window in
        # which a running job looks like one that never beat. Left NULL when
        # nothing is going to beat for this job, because a heartbeat that is
        # stamped once and never advances would have the sweep kill live work
        # ten minutes later. With it NULL the job falls back to the created_at
        # backstop, which is exactly the behaviour before any of this.
        pumping = heartbeat and JOB_HEARTBEAT_SECONDS > 0
        cur.execute(
            _q("""UPDATE jobs SET status='running', started_at=?, heartbeat_at=?,
                attempts=attempts+1, error=NULL, internal_error=NULL WHERE id=?"""),
            (now, now if pumping else None, job_id),
        )
        cur.execute(_q("SELECT * FROM jobs WHERE id=?"), (job_id,))
        claimed = dict(cur.fetchone())
    # After the claim commits, and here rather than in the caller, because
    # every way a job starts generating goes through this function: the inline
    # thread in the web service (views._dispatch_job -> jobs.run_job_by_id ->
    # claim_job) and the separate worker process (worker.main ->
    # claim_next_job). One hook covers both, and no caller can forget it.
    if heartbeat:
        start_job_heartbeat(job_id)
    return claimed


def claim_next_job(heartbeat: bool = True):
    return _claim_where("status='queued'", (), heartbeat)


def claim_job(job_id: str, heartbeat: bool = True):
    return _claim_where(_q("status='queued' AND id=?"), (job_id,), heartbeat)


def finish_job(job_id: str, *, path: str = None, dir: str = None) -> bool:
    """Mark a job done. False when it had already been settled.

    The status guard is load-bearing, not defensive. `fail_stale_running_jobs`
    runs in the web process and refunds any job older than FOLIO_JOB_TIMEOUT,
    but it cannot tell the worker to stop, and in production it is a different
    dyno entirely. Without the guard a slow job was refunded by the sweep and
    then flipped to 'done' by the worker minutes later, so the customer kept
    the credits and the booklet. A ten-week term plan is both the job most
    likely to run long and the A$39 one.

    Losing the output of a job that overran is the correct trade: the credits
    are already back, so the customer can simply generate again.
    """
    now = int(time.time())
    with _cursor() as cur:
        cur.execute(
            _q("""UPDATE jobs SET status='done', path=?, dir=?, error=NULL,
                internal_error=NULL, completed_at=?
                WHERE id=? AND status IN ('queued','running')"""),
            (path, dir, now, job_id),
        )
        return (cur.rowcount or 0) > 0


def _refund_row(cur, row, now: int) -> None:
    units = int(row["credit_units"] or 0)
    if units <= 0:
        return
    if is_postgres():
        cur.execute(
            """INSERT INTO credit_ledger
               (user_id,delta,reason,reference,created_at)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (user_id,reference) DO NOTHING""",
            (row["user_id"], units, "failed generation refund",
             f"job:{row['id']}:refund", now),
        )
    else:
        cur.execute(
            """INSERT OR IGNORE INTO credit_ledger
               (user_id,delta,reason,reference,created_at) VALUES (?,?,?,?,?)""",
            (row["user_id"], units, "failed generation refund",
             f"job:{row['id']}:refund", now),
        )


def fail_job(job_id: str, error: str,
             public_message: str = "Generation failed. Your booklet credit was returned. Please try again.") -> None:
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        cur.execute(_q("SELECT * FROM jobs WHERE id=?"), (job_id,))
        row = cur.fetchone()
        if row is None:
            return
        cur.execute(
            _q("""UPDATE jobs SET status='error', error=?, internal_error=?,
                completed_at=? WHERE id=?"""),
            (public_message[:500], str(error)[:2000], now, job_id),
        )
        _refund_row(cur, row, now)


# What a customer sees on a booklet they stopped themselves. A constant
# because both the cancel route and the library template read it: the row is an
# ordinary settled-and-refunded 'error', and this is the only thing that
# distinguishes "you stopped this" from "this broke".
CANCELLED_MESSAGE = (
    "You cancelled this booklet before it finished. Your booklet credit was "
    "returned."
)


def fail_job_if_running(job_id: str, error: str) -> bool:
    """Settle a queued or running job as failed and refund it. Once.

    Every guard here is load-bearing against a job that is genuinely still
    generating somewhere else:

    * The row is locked FOR UPDATE on Postgres, where the customer's cancel and
      the worker's finish_job run in different processes against the same row.
      Without it both transactions read 'running', and the update below (which
      is a plain UPDATE ... WHERE id=?) would blindly overwrite a 'done' the
      worker had just committed, refunding a booklet that was delivered.
    * The UPDATE repeats the status condition, so even without row locking a
      job that settled between the read and the write is left alone.
    * `_refund_row` only runs when this call is the one that moved the status,
      so a double-clicked cancel refunds nothing the second time. The ledger's
      unique reference would catch it anyway; this stops it a step earlier.
    """
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute("SELECT * FROM jobs WHERE id=%s FOR UPDATE", (job_id,))
        else:
            cur.execute("SELECT * FROM jobs WHERE id=?", (job_id,))
        row = cur.fetchone()
        if row is None or row["status"] not in {"queued", "running"}:
            return False
        cur.execute(
            _q("""UPDATE jobs SET status='error', error=?, internal_error=?,
                completed_at=? WHERE id=? AND status IN ('queued','running')"""),
            (str(error)[:500], str(error)[:2000], now, job_id),
        )
        if (cur.rowcount or 0) <= 0:
            return False
        _refund_row(cur, row, now)
        return True


def fail_stale_running_jobs(max_age_seconds: int,
                            heartbeat_max_age_seconds: int | None = None) -> int:
    """Settle and refund jobs that cannot still be generating. Count settled.

    Two independent tests, because they catch different failures:

    * `max_age_seconds` against `created_at` is the backstop. It is the only
      thing that catches a job that never beat at all: one queued for a worker
      that never arrived, or a row written before the heartbeat column existed.
      It has to be generous enough for the slowest legitimate work, which is
      why it is 45 minutes.
    * `heartbeat_max_age_seconds` against `heartbeat_at` is the fast path. A
      job that has beaten and then stopped is dead, whatever its age, so this
      can be minutes without endangering a slow but living job. Rows with a
      NULL heartbeat are deliberately untouched by it.

    The age test still applies to beating jobs too, so the absolute ceiling on
    a single job is unchanged. It is the only protection left against a job
    that hangs while its heartbeat thread carries on beating, and a customer
    who is watching a genuinely long job can now stop it themselves.
    """
    now = int(time.time())
    cutoff = now - int(max_age_seconds)
    sql = """SELECT * FROM jobs WHERE status IN ('queued','running')
             AND created_at < ?"""
    params: tuple = (cutoff,)
    if heartbeat_max_age_seconds:
        sql = """SELECT * FROM jobs WHERE status IN ('queued','running')
                 AND (created_at < ?
                      OR (status='running' AND heartbeat_at IS NOT NULL
                          AND heartbeat_at < ?))"""
        params = (cutoff, now - int(heartbeat_max_age_seconds))
    with _cursor(transaction=True) as cur:
        cur.execute(_q(sql), params)
        rows = list(cur.fetchall())
        settled = 0
        for row in rows:
            message = (
                "Generation stopped before it finished. Your booklet credit "
                "was returned. Please try again."
            )
            # Same status guard as fail_job_if_running, for the same reason:
            # the row was read without a lock, so a worker in another process
            # may have committed 'done' in between. An unguarded UPDATE would
            # overwrite that and refund a booklet that was delivered.
            cur.execute(
                _q("""UPDATE jobs SET status='error', error=?, internal_error=?,
                    completed_at=? WHERE id=? AND status IN ('queued','running')"""),
                (message, "Worker stopped or timed out.", now, row["id"]),
            )
            if (cur.rowcount or 0) <= 0:
                continue
            _refund_row(cur, row, now)
            settled += 1
        return settled


def get_job(job_id: str):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM jobs WHERE id=?"), (job_id,))
        return cur.fetchone()


def list_jobs(user_id: int, limit: int = 50) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT j.id,j.status,j.label,j.error,j.created_at,
                       j.started_at,j.completed_at,j.heartbeat_at,j.units,
                       j.request_json,f.filename,f.bytes,f.storage_key
                FROM jobs j LEFT JOIN job_files f ON f.job_id=j.id
                WHERE j.user_id=? ORDER BY j.created_at DESC LIMIT ?"""),
            (user_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]


def list_recent_jobs(limit: int = 100) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT j.*,u.email,f.filename,f.bytes
                FROM jobs j JOIN users u ON u.id=j.user_id
                LEFT JOIN job_files f ON f.job_id=j.id
                ORDER BY j.created_at DESC LIMIT ?"""),
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def operations_summary(window_seconds: int = 86400, *,
                       heartbeat_max_age_seconds: int = 120,
                       worker_name: str = "generation",
                       now: int | None = None) -> dict:
    """Aggregate queue and generation health without returning customer data."""
    checked_at = int(time.time()) if now is None else int(now)
    since = checked_at - max(60, int(window_seconds))
    with _cursor() as cur:
        cur.execute(
            """SELECT status,created_at FROM jobs
               WHERE status IN ('queued','running')"""
        )
        active = [dict(row) for row in cur.fetchall()]
        cur.execute(
            _q("""SELECT status,created_at,started_at,completed_at,units
                FROM jobs WHERE created_at>=?"""),
            (since,),
        )
        recent = [dict(row) for row in cur.fetchall()]

    queued = [row for row in active if row["status"] == "queued"]
    running = [row for row in active if row["status"] == "running"]
    settled = [row for row in recent if row["status"] in {"done", "error"}]
    completed = [row for row in settled if row["status"] == "done"]
    failed = [row for row in settled if row["status"] == "error"]
    durations = sorted(
        int(row["completed_at"]) - int(row["started_at"])
        for row in settled
        if row["started_at"] is not None
        and row["completed_at"] is not None
        and int(row["completed_at"]) >= int(row["started_at"])
    )
    settled_count = len(settled)
    average_duration = (
        round(sum(durations) / len(durations)) if durations else None
    )
    p95_duration = None
    if durations:
        p95_index = max(0, (95 * len(durations) + 99) // 100 - 1)
        p95_duration = durations[p95_index]

    return {
        "checked_at": checked_at,
        "worker": worker_status(
            heartbeat_max_age_seconds,
            worker_name=worker_name,
            now=checked_at,
        ),
        "queue": {
            "queued": len(queued),
            "running": len(running),
            "oldest_queued_age_seconds": (
                max(0, checked_at - min(int(row["created_at"]) for row in queued))
                if queued else None
            ),
        },
        "recent": {
            "window_seconds": max(60, int(window_seconds)),
            "jobs_started": len(recent),
            "booklet_units_started": sum(int(row["units"] or 0) for row in recent),
            "completed": len(completed),
            "failed": len(failed),
            "success_rate_percent": (
                round(100 * len(completed) / settled_count, 1)
                if settled_count else None
            ),
            "failure_rate_percent": (
                round(100 * len(failed) / settled_count, 1)
                if settled_count else None
            ),
            "average_duration_seconds": average_duration,
            "p95_duration_seconds": p95_duration,
            "last_failure_at": max(
                (int(row["completed_at"]) for row in failed
                 if row["completed_at"] is not None),
                default=None,
            ),
        },
    }


# ---------- booklet feedback ----------
#
# One row per booklet, keyed by job_id, so a customer rates a booklet rather
# than accumulating votes on it. Re-rating updates the row: a parent who marks
# a booklet 4 stars on download and drops it to 2 after actually teaching from
# it is giving us the better number, not a second opinion.

RATING_MIN = 1
RATING_MAX = 5
COMMENT_MAX = 1000
QUESTION_REF_MAX = 60


def save_feedback(job_id: str, user_id: int, rating: int,
                  question_ref: str = "", comment: str = "") -> None:
    rating = int(rating)
    if not RATING_MIN <= rating <= RATING_MAX:
        raise ValueError(f"rating must be {RATING_MIN} to {RATING_MAX}")
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        cur.execute(
            _q("""INSERT INTO booklet_feedback
                    (job_id,user_id,rating,question_ref,comment,
                     created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT (job_id) DO UPDATE SET
                    rating=excluded.rating,
                    question_ref=excluded.question_ref,
                    comment=excluded.comment,
                    updated_at=excluded.updated_at"""),
            (job_id, user_id, rating,
             (question_ref or "")[:QUESTION_REF_MAX].strip() or None,
             (comment or "")[:COMMENT_MAX].strip() or None, now, now),
        )


def get_feedback(job_id: str):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM booklet_feedback WHERE job_id=?"), (job_id,))
        return cur.fetchone()


def feedback_for_jobs(job_ids: list[str]) -> dict[str, int]:
    """Ratings for a page of library rows, in one query rather than N."""
    if not job_ids:
        return {}
    holes = ",".join("?" for _ in job_ids)
    with _cursor() as cur:
        cur.execute(
            _q(f"SELECT job_id,rating FROM booklet_feedback WHERE job_id IN ({holes})"),
            tuple(job_ids),
        )
        return {row["job_id"]: int(row["rating"]) for row in cur.fetchall()}


def list_recent_feedback(limit: int = 200) -> list[dict]:
    """Newest feedback for the support console.

    Deliberately returns no customer email and no job label. The label carries
    whatever name the parent typed for their child, and SUPPORT_PLAYBOOK.md
    forbids putting that in the support log. What triage actually needs is the
    year and subject, which `request_json` carries.
    """
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT b.job_id,b.rating,b.question_ref,b.comment,
                       b.created_at,b.updated_at,j.request_json
                FROM booklet_feedback b JOIN jobs j ON j.id=b.job_id
                ORDER BY b.updated_at DESC LIMIT ?"""),
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]


def feedback_summary() -> dict:
    counts = {rating: 0 for rating in range(RATING_MIN, RATING_MAX + 1)}
    with _cursor() as cur:
        cur.execute("SELECT rating,COUNT(*) AS n FROM booklet_feedback GROUP BY rating")
        for row in cur.fetchall():
            rating = int(row["rating"])
            if rating in counts:
                counts[rating] = int(row["n"])
    total = sum(counts.values())
    return {
        "counts": counts,
        "total": total,
        # Under a few dozen ratings this average is noise. It is here to watch
        # a trend over hundreds, not to decide anything during the beta.
        "average": round(
            sum(rating * n for rating, n in counts.items()) / total, 2
        ) if total else None,
    }


# ---------- deliverable storage ----------

def save_job_file(job_id: str, user_id: int, filename: str,
                  mimetype: str, data: bytes) -> None:
    storage_key = None
    stored_data = data
    old_storage_keys: list[str] = []
    try:
        from . import storage
        if storage.enabled():
            storage_key = f"{user_id}/{job_id}/{filename}"
            storage.upload(storage_key, data, mimetype)
            stored_data = b""
    except Exception as exc:
        storage_key = None
        stored_data = data
        log.warning("object storage upload failed; using database fallback: %s", exc)

    payload = memoryview(stored_data) if is_postgres() else sqlite3.Binary(stored_data)
    with _cursor(transaction=True) as cur:
        cur.execute(_q("DELETE FROM job_files WHERE job_id=?"), (job_id,))
        cur.execute(
            _q("""INSERT INTO job_files
                (job_id,filename,mimetype,data,storage_key,bytes,created_at)
                VALUES (?,?,?,?,?,?,?)"""),
            (job_id, filename, mimetype, payload, storage_key,
             len(data), int(time.time())),
        )
        # Two separate caps, because a plan week and a one-off booklet are not
        # competing for the same shelf. Counting them together let a tutor's
        # busiest student push another student's weeks out, so a plan that
        # promised the last three weeks quietly held one.
        cur.execute(_q("SELECT plan_id FROM jobs WHERE id=?"), (job_id,))
        row = cur.fetchone()
        plan_id = row["plan_id"] if row else None
        cur.execute(
            _q("""SELECT f.job_id,f.storage_key FROM job_files f
                JOIN jobs j ON j.id=f.job_id
                WHERE j.user_id=? AND j.plan_id IS NULL
                ORDER BY f.created_at DESC LIMIT 1000 OFFSET ?"""),
            (user_id, FILE_RETENTION_PER_USER),
        )
        old_rows = list(cur.fetchall())
        if plan_id is not None:
            # Only this plan can have gained a file, so only this plan needs
            # trimming.
            # Newest first, ties broken toward the later week. Two weeks
            # generated in the same second are ordered arbitrarily otherwise,
            # and "keep the last three" then decides by insertion order, which
            # can throw away week 5 and keep week 1.
            cur.execute(
                _q("""SELECT f.job_id,f.storage_key FROM job_files f
                    JOIN jobs j ON j.id=f.job_id WHERE j.plan_id=?
                    ORDER BY f.created_at DESC, j.plan_week DESC
                    LIMIT 1000 OFFSET ?"""),
                (plan_id, PLAN_WEEK_RETENTION),
            )
            old_rows.extend(cur.fetchall())
        old_ids = [row["job_id"] for row in old_rows]
        old_storage_keys = [row["storage_key"] for row in old_rows if row["storage_key"]]
        for old_id in old_ids:
            cur.execute(_q("DELETE FROM job_files WHERE job_id=?"), (old_id,))
    if old_storage_keys:
        try:
            from . import storage
            storage.delete(old_storage_keys)
        except Exception as exc:
            log.warning("could not trim old stored files: %s", exc)


def get_job_file(job_id: str):
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT filename,mimetype,data,storage_key
                FROM job_files WHERE job_id=?"""),
            (job_id,),
        )
        return cur.fetchone()


# ---------- quotas and account data ----------

def booklets_started_last_24h(user_id: int) -> int:
    since = int(time.time()) - 86400
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT COALESCE(SUM(units),0) AS n FROM jobs
                WHERE user_id=? AND created_at>=?"""),
            (user_id, since),
        )
        return _scalar(cur)


def booklets_started_globally_last_24h() -> int:
    since = int(time.time()) - 86400
    with _cursor() as cur:
        cur.execute(
            _q("SELECT COALESCE(SUM(units),0) AS n FROM jobs WHERE created_at>=?"),
            (since,),
        )
        return _scalar(cur)


def export_account(user_id: int) -> dict:
    user = get_user(user_id)
    if user is None:
        return {}
    jobs = list_jobs(user_id, limit=10_000)
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT job_id,rating,question_ref,comment,created_at,updated_at
                FROM booklet_feedback WHERE user_id=? ORDER BY created_at"""),
            (user_id,),
        )
        feedback = [dict(row) for row in cur.fetchall()]
    return {
        "account": {
            "id": int(user["id"]),
            "email": user["email"],
            "email_verified": bool(user["email_verified"]),
            "created_at": int(user["created_at"]),
            "booklet_credits": credit_balance(user_id),
            "stripe_customer_id": user["stripe_customer_id"],
        },
        "booklets": [{
            "id": job["id"], "label": job["label"], "status": job["status"],
            "created_at": int(job["created_at"]), "filename": job["filename"],
            "bytes": job["bytes"], "error": job["error"],
        } for job in jobs],
        "payments": list_payments(user_id, limit=10_000),
        "feedback": feedback,
        "exported_at": int(time.time()),
    }


def delete_account(user_id: int) -> list:
    storage_keys: list[str] = []
    with _cursor(transaction=True) as cur:
        cur.execute(_q("SELECT path,dir FROM jobs WHERE user_id=?"), (user_id,))
        rows = list(cur.fetchall())
        leftovers = [(row["path"], row["dir"]) for row in rows]
        cur.execute(
            _q("""SELECT storage_key FROM job_files WHERE job_id IN
                (SELECT id FROM jobs WHERE user_id=?)"""),
            (user_id,),
        )
        storage_keys = [row["storage_key"] for row in cur.fetchall() if row["storage_key"]]
        cur.execute(
            _q("DELETE FROM job_files WHERE job_id IN (SELECT id FROM jobs WHERE user_id=?)"),
            (user_id,),
        )
        # Feedback goes with the account. Keeping an anonymised copy would be
        # operationally useful, but Privacy and the deletion flash both promise
        # everything goes, and a parent finding their typed comment still on
        # file after deleting would have been misled.
        cur.execute(_q("DELETE FROM booklet_feedback WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM credit_ledger WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM payments WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM jobs WHERE user_id=?"), (user_id,))
        # Practice history is per user and outlives any session, so it has to
        # go explicitly. It is deleted rather than left to ON DELETE CASCADE
        # because SQLite never enables PRAGMA foreign_keys here, so the cascade
        # only fires on one of the two backends. Swallows a missing table and
        # nothing else: see practice/store.py.
        from ..practice import store as practice_store
        practice_store.delete_user_practice_data(cur, user_id)
        cur.execute(_q("DELETE FROM users WHERE id=?"), (user_id,))
    if storage_keys:
        try:
            from . import storage
            storage.delete(storage_keys)
        except Exception as exc:
            log.warning("could not delete account objects from storage: %s", exc)
    return leftovers


# ---------- study plans ----------
#
# A plan is one student's term: the ladder of weekly skills, planned once up
# front, plus a record of what each generated week actually taught. It exists
# so a standalone booklet can carry over from the week before it.
#
# Deliberately not keyed on the student's name. A tutor generating week 1 for
# five students needs five separate plans, and a parent who types "Sammy" one
# week and "Sam" the next must not silently start a second term. The plan is
# a row the customer picks, so neither can happen.


def create_plan(user_id: int, student_name: str, program: str,
                subject: str | None, year_level: str, total_weeks: int,
                ladder: list[dict]) -> int:
    now = int(time.time())
    payload = json.dumps(ladder, separators=(",", ":"))
    with _cursor(transaction=True) as cur:
        if is_postgres():
            cur.execute(
                """INSERT INTO study_plans
                   (user_id,student_name,program,subject,year_level,
                    total_weeks,ladder_json,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user_id, student_name, program, subject, year_level,
                 total_weeks, payload, now),
            )
            return int(cur.fetchone()["id"])
        cur.execute(
            """INSERT INTO study_plans
               (user_id,student_name,program,subject,year_level,
                total_weeks,ladder_json,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, student_name, program, subject, year_level,
             total_weeks, payload, now),
        )
        return int(cur.lastrowid)


def _plan_row(row) -> dict:
    plan = dict(row)
    try:
        plan["ladder"] = json.loads(plan.get("ladder_json") or "[]")
    except (TypeError, ValueError):
        plan["ladder"] = []
    return plan


def get_plan(plan_id: int, user_id: int | None = None) -> dict | None:
    """One plan. Pass `user_id` to refuse another account's plan outright,
    so a forged id in a POST body cannot reach someone else's student."""
    sql = "SELECT * FROM study_plans WHERE id=?"
    params: tuple = (plan_id,)
    if user_id is not None:
        sql += " AND user_id=?"
        params = (plan_id, user_id)
    with _cursor() as cur:
        cur.execute(_q(sql), params)
        row = cur.fetchone()
        return _plan_row(row) if row else None


def list_plans(user_id: int, include_archived: bool = False) -> list[dict]:
    """A user's plans, newest first, each with the weeks already generated."""
    sql = "SELECT * FROM study_plans WHERE user_id=?"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY created_at DESC"
    with _cursor() as cur:
        cur.execute(_q(sql), (user_id,))
        plans = [_plan_row(row) for row in cur.fetchall()]
        if not plans:
            return []
        cur.execute(
            _q("""SELECT w.plan_id, w.week FROM study_plan_weeks w
                JOIN study_plans p ON p.id=w.plan_id
                WHERE p.user_id=? ORDER BY w.week"""),
            (user_id,),
        )
        done: dict[int, list[int]] = {}
        for row in cur.fetchall():
            done.setdefault(int(row["plan_id"]), []).append(int(row["week"]))
    for plan in plans:
        weeks_done = done.get(int(plan["id"]), [])
        plan["weeks_done"] = weeks_done
        # The lowest week not yet generated, so the dropdown opens on the work
        # the student has left rather than on week 1 with "(already generated)"
        # beside it. Lowest rather than highest-plus-one: someone who skipped
        # week 3 is offered week 3 back, not pushed further past it.
        total = int(plan.get("total_weeks") or 0)
        remaining = [w for w in range(1, total + 1) if w not in set(weeks_done)]
        plan["next_week"] = remaining[0] if remaining else total
    return plans


def archive_plan(plan_id: int, user_id: int) -> bool:
    with _cursor(transaction=True) as cur:
        cur.execute(
            _q("""UPDATE study_plans SET archived_at=?
                WHERE id=? AND user_id=? AND archived_at IS NULL"""),
            (int(time.time()), plan_id, user_id),
        )
        return cur.rowcount > 0


def get_plan_week(plan_id: int, week: int) -> dict | None:
    with _cursor() as cur:
        cur.execute(
            _q("SELECT * FROM study_plan_weeks WHERE plan_id=? AND week=?"),
            (plan_id, week),
        )
        row = cur.fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            record["spelling_words"] = json.loads(record.get("spelling_json") or "[]")
        except (TypeError, ValueError):
            record["spelling_words"] = []
        return record


def record_plan_week(plan_id: int, week: int, job_id: str | None,
                     taught: str | None, spelling_words: list[str] | None,
                     tables_table: int | None) -> None:
    """What week `week` actually taught, for the week after it to build on.

    Written after generation rather than from the ladder, because the outline
    parser chooses the real subtopics and the hour cap can drop some of them.
    Upserted, so regenerating a week replaces its record instead of failing.
    """
    now = int(time.time())
    words = json.dumps(list(spelling_words or []), separators=(",", ":"))
    with _cursor(transaction=True) as cur:
        cur.execute(_q("DELETE FROM study_plan_weeks WHERE plan_id=? AND week=?"),
                    (plan_id, week))
        cur.execute(
            _q("""INSERT INTO study_plan_weeks
                (plan_id,week,job_id,taught,spelling_json,tables_table,generated_at)
                VALUES (?,?,?,?,?,?,?)"""),
            (plan_id, week, job_id, taught, words, tables_table, now),
        )


def plan_history(plan_id: int) -> dict:
    """Everything the next week of this plan needs to follow on.

    `words_set` and `tables_set` span the whole plan, not just last week, so a
    new week cannot re-set spelling words or a times table the student has
    already had. `previous` is the week immediately before, which is the only
    one a recap or a test may draw from.
    """
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT week,taught,spelling_json,tables_table
                FROM study_plan_weeks WHERE plan_id=? ORDER BY week"""),
            (plan_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    words: list[str] = []
    tables: list[int] = []
    for row in rows:
        try:
            words.extend(json.loads(row.get("spelling_json") or "[]"))
        except (TypeError, ValueError):
            pass
        if row.get("tables_table") is not None:
            tables.append(int(row["tables_table"]))
    return {"weeks": rows, "words_set": words, "tables_set": tables}
