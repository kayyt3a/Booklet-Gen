"""Persistent data for accounts, queued generation, files, and purchases."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from ..dbpool import advisory_lock, get_pool, is_postgres

log = logging.getLogger(__name__)
DB_PATH = Path(os.environ.get("FOLIO_DB", "folio.db"))
FILE_RETENTION_PER_USER = int(os.environ.get("FOLIO_FILE_RETENTION", "20"))
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
    request_json   TEXT
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
            )
            for statement in migrations:
                conn.execute(statement)
            conn.execute(
                """INSERT INTO credit_ledger
                   (user_id, delta, reason, reference, created_at)
                   SELECT id, 1, 'welcome credit', 'welcome:' || id::text, %s
                   FROM users ON CONFLICT (user_id, reference) DO NOTHING""",
                (now,),
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
        })
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
               SELECT id, 1, 'welcome credit', 'welcome:' || id, ? FROM users""",
            (now,),
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
            (user_id, 1, "welcome credit", f"welcome:{user_id}", now),
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
                global_daily_limit: int | None = None) -> bool:
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
                (id,user_id,status,label,created_at,units,credit_units,request_json)
                VALUES (?,?,?,?,?,?,?,?)"""),
            (job_id, user_id, "queued", label, now, units,
             units if reserve_credits else 0,
             json.dumps(request_data, separators=(",", ":"))),
        )
        if reserve_credits:
            cur.execute(
                _q("""INSERT INTO credit_ledger
                    (user_id,delta,reason,reference,created_at) VALUES (?,?,?,?,?)"""),
                (user_id, -units, "generation reserved", f"job:{job_id}:reserve", now),
            )
        return True


def _claim_where(where_sql: str, params: tuple):
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
        cur.execute(
            _q("""UPDATE jobs SET status='running', started_at=?,
                attempts=attempts+1, error=NULL, internal_error=NULL WHERE id=?"""),
            (now, job_id),
        )
        cur.execute(_q("SELECT * FROM jobs WHERE id=?"), (job_id,))
        return dict(cur.fetchone())


def claim_next_job():
    return _claim_where("status='queued'", ())


def claim_job(job_id: str):
    return _claim_where(_q("status='queued' AND id=?"), (job_id,))


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


def fail_job_if_running(job_id: str, error: str) -> bool:
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        cur.execute(
            _q("SELECT * FROM jobs WHERE id=? AND status IN ('queued','running')"),
            (job_id,),
        )
        row = cur.fetchone()
        if row is None:
            return False
        cur.execute(
            _q("""UPDATE jobs SET status='error', error=?, internal_error=?,
                completed_at=? WHERE id=?"""),
            (str(error)[:500], str(error)[:2000], now, job_id),
        )
        _refund_row(cur, row, now)
        return True


def fail_stale_running_jobs(max_age_seconds: int) -> int:
    cutoff = int(time.time()) - int(max_age_seconds)
    now = int(time.time())
    with _cursor(transaction=True) as cur:
        cur.execute(
            _q("""SELECT * FROM jobs WHERE status IN ('queued','running')
                AND created_at < ?"""),
            (cutoff,),
        )
        rows = list(cur.fetchall())
        for row in rows:
            message = (
                "Generation stopped before it finished. Your booklet credit "
                "was returned. Please try again."
            )
            cur.execute(
                _q("""UPDATE jobs SET status='error', error=?, internal_error=?,
                    completed_at=? WHERE id=?"""),
                (message, "Worker stopped or timed out.", now, row["id"]),
            )
            _refund_row(cur, row, now)
        return len(rows)


def get_job(job_id: str):
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM jobs WHERE id=?"), (job_id,))
        return cur.fetchone()


def list_jobs(user_id: int, limit: int = 50) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT j.id,j.status,j.label,j.error,j.created_at,
                       j.started_at,j.completed_at,j.units,j.request_json,
                       f.filename,f.bytes,f.storage_key
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
        cur.execute(
            _q("""SELECT f.job_id,f.storage_key FROM job_files f
                JOIN jobs j ON j.id=f.job_id WHERE j.user_id=?
                ORDER BY f.created_at DESC LIMIT 1000 OFFSET ?"""),
            (user_id, FILE_RETENTION_PER_USER),
        )
        old_rows = list(cur.fetchall())
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
        cur.execute(_q("DELETE FROM credit_ledger WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM payments WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM jobs WHERE user_id=?"), (user_id,))
        cur.execute(_q("DELETE FROM users WHERE id=?"), (user_id,))
    if storage_keys:
        try:
            from . import storage
            storage.delete(storage_keys)
        except Exception as exc:
            log.warning("could not delete account objects from storage: %s", exc)
    return leftovers
