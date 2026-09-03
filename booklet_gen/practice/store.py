"""Every SQL statement the practice engine runs.

The bank is the only part of the engine that touches a database, and it is
deliberately the only part that knows any SQL. The factory (templates,
instances, verify, filler) hands it verified rows; the grind (the web views)
asks it for questions. Neither writes a statement of its own, so there is one
place to look when a draw is slow or a student sees a repeat.

WHY THIS IMPORTS THREE PRIVATE HELPERS FROM webapp.db
-----------------------------------------------------
`_cursor`, `_q` and `_sqlite_add_columns` are imported rather than
reimplemented. Two connection paths would mean two answers to "am I inside a
transaction", and `_cursor` already carries the SQLite WAL pragma and the
Postgres `dict_row` factory that every row-reading function here assumes. A
second, near-identical copy of that logic is how the two halves of one database
drift apart.

Nothing here names `credit_ledger`, `payments` or `jobs`. The practice engine
has no business near the money tables, and `scripts/check_practice_bank_schema.py`
asserts that by reading this module's source.

The schema is declared twice, once per backend, exactly as `webapp/db.py` does
it, with migration lists so a column added later reaches a database that
already exists. `CREATE TABLE IF NOT EXISTS` does nothing at all on a table
that is already there, which is the trap the migration lists exist to close.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence

from .. import senior_syllabus
from ..dbpool import advisory_lock, is_postgres
from ..webapp import db as accounts_db          # for DB_PATH, which checks repoint
from ..webapp.db import _cursor, _q, _sqlite_add_columns
from .models import DrawResult, ItemRow, SeenEvent, TemplateRow, canonical_json

log = logging.getLogger(__name__)

# Not webapp.db._SCHEMA_LOCK_KEY (72_461_001). Sharing that key would make the
# practice DDL wait on the account DDL and vice versa for no reason, and worse,
# would hide a deadlock the day one of them starts calling the other.
_PRACTICE_SCHEMA_LOCK_KEY = 72_461_002

# How far back the draw looks to avoid handing out the same question family
# twice in quick succession. Five is roughly one screenful of grinding.
SPACING_WINDOW = 5

# The widest scope in the syllabus is 42 subtopics. This is the ceiling on the
# bind parameters one draw will expand, and `draw` raises rather than
# truncating: a subject that outgrows it must fail loudly, not silently serve a
# subset of what the student asked for.
MAX_SCOPE_IDS = 200

# How many candidates to over-fetch per requested item, so the spacing filter
# has something to choose between.
OVERFETCH = 4

TABLES = (
    "practice_templates",
    "practice_items",
    "practice_sessions",
    "practice_seen",
    "practice_scope_demand",
    "practice_generation_log",
    "practice_node_state",
)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_templates (
    id                  TEXT PRIMARY KEY,
    subject             TEXT NOT NULL,
    subtopic_id         TEXT NOT NULL,
    verify_kind         TEXT NOT NULL,
    calculator          TEXT NOT NULL,
    difficulty          TEXT NOT NULL,
    marks               INTEGER,
    question_pattern    TEXT NOT NULL,
    answer_pattern      TEXT NOT NULL,
    working_pattern     TEXT NOT NULL,
    params_json         TEXT NOT NULL,
    constraints_json    TEXT NOT NULL,
    check_pattern_json  TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'live',
    reject_reason       TEXT,
    instances_made      INTEGER NOT NULL DEFAULT 0,
    instances_verified  INTEGER NOT NULL DEFAULT 0,
    model               TEXT,
    prompt_version      TEXT NOT NULL,
    syllabus_version    TEXT NOT NULL,
    created_at          BIGINT NOT NULL,
    retired_at          BIGINT
);
CREATE INDEX IF NOT EXISTS practice_templates_node_idx
    ON practice_templates (subtopic_id, status);
CREATE TABLE IF NOT EXISTS practice_items (
    id               BIGSERIAL PRIMARY KEY,
    template_id      TEXT NOT NULL REFERENCES practice_templates(id) ON DELETE CASCADE,
    subject          TEXT NOT NULL,
    subtopic_id      TEXT NOT NULL,
    calculator       TEXT NOT NULL,
    difficulty       TEXT NOT NULL,
    marks            INTEGER,
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL,
    working          TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    check_json       TEXT NOT NULL,
    variant_key      TEXT NOT NULL,
    shuffle_key      DOUBLE PRECISION NOT NULL,
    verified_by      TEXT NOT NULL,
    verifier_notes   TEXT,
    syllabus_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'live',
    created_at       BIGINT NOT NULL,
    UNIQUE (template_id, variant_key)
);
CREATE INDEX IF NOT EXISTS practice_items_draw_idx
    ON practice_items (subtopic_id, status, shuffle_key);
CREATE INDEX IF NOT EXISTS practice_items_template_idx
    ON practice_items (template_id);
CREATE TABLE IF NOT EXISTS practice_sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject      TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    scope_label  TEXT NOT NULL,
    calculator   TEXT,
    served       INTEGER NOT NULL DEFAULT 0,
    answered     INTEGER NOT NULL DEFAULT 0,
    correct      INTEGER NOT NULL DEFAULT 0,
    created_at   BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS practice_sessions_user_idx
    ON practice_sessions (user_id, last_seen_at DESC);
CREATE TABLE IF NOT EXISTS practice_seen (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id        BIGINT NOT NULL REFERENCES practice_items(id) ON DELETE CASCADE,
    subtopic_id    TEXT NOT NULL,
    template_id    TEXT NOT NULL,
    outcome        TEXT,
    times_seen     INTEGER NOT NULL DEFAULT 1,
    first_seen_at  BIGINT NOT NULL,
    last_seen_at   BIGINT NOT NULL,
    PRIMARY KEY (user_id, item_id)
);
CREATE INDEX IF NOT EXISTS practice_seen_user_node_idx
    ON practice_seen (user_id, subtopic_id);
CREATE INDEX IF NOT EXISTS practice_seen_recent_idx
    ON practice_seen (user_id, last_seen_at DESC);
CREATE TABLE IF NOT EXISTS practice_scope_demand (
    subtopic_id       TEXT PRIMARY KEY,
    requests          INTEGER NOT NULL DEFAULT 0,
    dry_requests      INTEGER NOT NULL DEFAULT 0,
    last_requested_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS practice_generation_log (
    day                 TEXT NOT NULL,
    subtopic_id         TEXT NOT NULL,
    calls               INTEGER NOT NULL DEFAULT 0,
    templates_kept      INTEGER NOT NULL DEFAULT 0,
    templates_rejected  INTEGER NOT NULL DEFAULT 0,
    items_kept          INTEGER NOT NULL DEFAULT 0,
    items_discarded     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, subtopic_id)
);
CREATE TABLE IF NOT EXISTS practice_node_state (
    subtopic_id           TEXT PRIMARY KEY,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    blocked_reason        TEXT,
    blocked_at            BIGINT,
    last_filled_at        BIGINT
);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_templates (
    id                  TEXT PRIMARY KEY,
    subject             TEXT NOT NULL,
    subtopic_id         TEXT NOT NULL,
    verify_kind         TEXT NOT NULL,
    calculator          TEXT NOT NULL,
    difficulty          TEXT NOT NULL,
    marks               INTEGER,
    question_pattern    TEXT NOT NULL,
    answer_pattern      TEXT NOT NULL,
    working_pattern     TEXT NOT NULL,
    params_json         TEXT NOT NULL,
    constraints_json    TEXT NOT NULL,
    check_pattern_json  TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'live',
    reject_reason       TEXT,
    instances_made      INTEGER NOT NULL DEFAULT 0,
    instances_verified  INTEGER NOT NULL DEFAULT 0,
    model               TEXT,
    prompt_version      TEXT NOT NULL,
    syllabus_version    TEXT NOT NULL,
    created_at          INTEGER NOT NULL,
    retired_at          INTEGER
);
CREATE INDEX IF NOT EXISTS practice_templates_node_idx
    ON practice_templates (subtopic_id, status);
CREATE TABLE IF NOT EXISTS practice_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id      TEXT NOT NULL,
    subject          TEXT NOT NULL,
    subtopic_id      TEXT NOT NULL,
    calculator       TEXT NOT NULL,
    difficulty       TEXT NOT NULL,
    marks            INTEGER,
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL,
    working          TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    check_json       TEXT NOT NULL,
    variant_key      TEXT NOT NULL,
    shuffle_key      REAL NOT NULL,
    verified_by      TEXT NOT NULL,
    verifier_notes   TEXT,
    syllabus_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'live',
    created_at       INTEGER NOT NULL,
    UNIQUE (template_id, variant_key),
    FOREIGN KEY(template_id) REFERENCES practice_templates(id)
);
CREATE INDEX IF NOT EXISTS practice_items_draw_idx
    ON practice_items (subtopic_id, status, shuffle_key);
CREATE INDEX IF NOT EXISTS practice_items_template_idx
    ON practice_items (template_id);
CREATE TABLE IF NOT EXISTS practice_sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL,
    subject      TEXT NOT NULL,
    scope_id     TEXT NOT NULL,
    scope_label  TEXT NOT NULL,
    calculator   TEXT,
    served       INTEGER NOT NULL DEFAULT 0,
    answered     INTEGER NOT NULL DEFAULT 0,
    correct      INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS practice_sessions_user_idx
    ON practice_sessions (user_id, last_seen_at DESC);
CREATE TABLE IF NOT EXISTS practice_seen (
    user_id        INTEGER NOT NULL,
    item_id        INTEGER NOT NULL,
    subtopic_id    TEXT NOT NULL,
    template_id    TEXT NOT NULL,
    outcome        TEXT,
    times_seen     INTEGER NOT NULL DEFAULT 1,
    first_seen_at  INTEGER NOT NULL,
    last_seen_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, item_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(item_id) REFERENCES practice_items(id)
);
CREATE INDEX IF NOT EXISTS practice_seen_user_node_idx
    ON practice_seen (user_id, subtopic_id);
CREATE INDEX IF NOT EXISTS practice_seen_recent_idx
    ON practice_seen (user_id, last_seen_at DESC);
CREATE TABLE IF NOT EXISTS practice_scope_demand (
    subtopic_id       TEXT PRIMARY KEY,
    requests          INTEGER NOT NULL DEFAULT 0,
    dry_requests      INTEGER NOT NULL DEFAULT 0,
    last_requested_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS practice_generation_log (
    day                 TEXT NOT NULL,
    subtopic_id         TEXT NOT NULL,
    calls               INTEGER NOT NULL DEFAULT 0,
    templates_kept      INTEGER NOT NULL DEFAULT 0,
    templates_rejected  INTEGER NOT NULL DEFAULT 0,
    items_kept          INTEGER NOT NULL DEFAULT 0,
    items_discarded     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, subtopic_id)
);
CREATE TABLE IF NOT EXISTS practice_node_state (
    subtopic_id           TEXT PRIMARY KEY,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    blocked_reason        TEXT,
    blocked_at            INTEGER,
    last_filled_at        INTEGER
);
"""

# Every column a later deploy could add to a table that already exists: the
# nullable ones and the ones with a default. A NOT NULL column with no default
# cannot be added to a populated table on either backend, so those are
# deliberately absent rather than forgotten. Declared per backend from one
# reading of the schemas above, and check_practice_bank_schema.py asserts the
# two lists cover the same columns as each other and as the schemas.
_SQLITE_MIGRATIONS: dict[str, dict[str, str]] = {
    "practice_templates": {
        "marks": "INTEGER",
        "status": "TEXT NOT NULL DEFAULT 'live'",
        "reject_reason": "TEXT",
        "instances_made": "INTEGER NOT NULL DEFAULT 0",
        "instances_verified": "INTEGER NOT NULL DEFAULT 0",
        "model": "TEXT",
        "retired_at": "INTEGER",
    },
    "practice_items": {
        "marks": "INTEGER",
        "verifier_notes": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'live'",
    },
    "practice_sessions": {
        "calculator": "TEXT",
        "served": "INTEGER NOT NULL DEFAULT 0",
        "answered": "INTEGER NOT NULL DEFAULT 0",
        "correct": "INTEGER NOT NULL DEFAULT 0",
    },
    "practice_seen": {
        "outcome": "TEXT",
        "times_seen": "INTEGER NOT NULL DEFAULT 1",
    },
    "practice_scope_demand": {
        "requests": "INTEGER NOT NULL DEFAULT 0",
        "dry_requests": "INTEGER NOT NULL DEFAULT 0",
    },
    "practice_generation_log": {
        "calls": "INTEGER NOT NULL DEFAULT 0",
        "templates_kept": "INTEGER NOT NULL DEFAULT 0",
        "templates_rejected": "INTEGER NOT NULL DEFAULT 0",
        "items_kept": "INTEGER NOT NULL DEFAULT 0",
        "items_discarded": "INTEGER NOT NULL DEFAULT 0",
    },
    "practice_node_state": {
        "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
        "blocked_reason": "TEXT",
        "blocked_at": "INTEGER",
        "last_filled_at": "INTEGER",
    },
}

_PG_MIGRATIONS = (
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS marks INTEGER",
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'live'",
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS reject_reason TEXT",
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS instances_made INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS instances_verified INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS model TEXT",
    "ALTER TABLE practice_templates ADD COLUMN IF NOT EXISTS retired_at BIGINT",
    "ALTER TABLE practice_items ADD COLUMN IF NOT EXISTS marks INTEGER",
    "ALTER TABLE practice_items ADD COLUMN IF NOT EXISTS verifier_notes TEXT",
    "ALTER TABLE practice_items ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'live'",
    "ALTER TABLE practice_sessions ADD COLUMN IF NOT EXISTS calculator TEXT",
    "ALTER TABLE practice_sessions ADD COLUMN IF NOT EXISTS served INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_sessions ADD COLUMN IF NOT EXISTS answered INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_sessions ADD COLUMN IF NOT EXISTS correct INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_seen ADD COLUMN IF NOT EXISTS outcome TEXT",
    "ALTER TABLE practice_seen ADD COLUMN IF NOT EXISTS times_seen INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE practice_scope_demand ADD COLUMN IF NOT EXISTS requests INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_scope_demand ADD COLUMN IF NOT EXISTS dry_requests INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_generation_log ADD COLUMN IF NOT EXISTS calls INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_generation_log ADD COLUMN IF NOT EXISTS templates_kept INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_generation_log ADD COLUMN IF NOT EXISTS templates_rejected INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_generation_log ADD COLUMN IF NOT EXISTS items_kept INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_generation_log ADD COLUMN IF NOT EXISTS items_discarded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_node_state ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE practice_node_state ADD COLUMN IF NOT EXISTS blocked_reason TEXT",
    "ALTER TABLE practice_node_state ADD COLUMN IF NOT EXISTS blocked_at BIGINT",
    "ALTER TABLE practice_node_state ADD COLUMN IF NOT EXISTS last_filled_at BIGINT",
)

# The indexes, repeated outside the schema strings, because a database that
# already has the tables never re-runs a CREATE TABLE and would therefore never
# gain an index added in a later release. Identical text on both backends.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS practice_templates_node_idx"
    " ON practice_templates (subtopic_id, status)",
    "CREATE INDEX IF NOT EXISTS practice_items_draw_idx"
    " ON practice_items (subtopic_id, status, shuffle_key)",
    "CREATE INDEX IF NOT EXISTS practice_items_template_idx"
    " ON practice_items (template_id)",
    "CREATE INDEX IF NOT EXISTS practice_sessions_user_idx"
    " ON practice_sessions (user_id, last_seen_at DESC)",
    "CREATE INDEX IF NOT EXISTS practice_seen_user_node_idx"
    " ON practice_seen (user_id, subtopic_id)",
    "CREATE INDEX IF NOT EXISTS practice_seen_recent_idx"
    " ON practice_seen (user_id, last_seen_at DESC)",
)


def init_practice_db() -> None:
    """Create or upgrade the practice tables. Safe to run on every boot.

    `webapp.db.init_db()` must have run first: practice_sessions and
    practice_seen reference users(id), and on Postgres a foreign key to a table
    that does not exist yet is an error rather than a warning.
    """
    if is_postgres():
        with advisory_lock(_PRACTICE_SCHEMA_LOCK_KEY) as conn:
            conn.execute(_PG_SCHEMA)
            for statement in _PG_MIGRATIONS + _INDEXES:
                conn.execute(statement)
        return

    conn = sqlite3.connect(accounts_db.DB_PATH, timeout=30)
    try:
        conn.executescript(_SQLITE_SCHEMA)
        for table, columns in _SQLITE_MIGRATIONS.items():
            _sqlite_add_columns(conn, table, columns)
        for statement in _INDEXES:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def delete_user_practice_data(cur, user_id: int) -> None:
    """Remove one account's practice history, on a cursor already in a
    transaction. Called from webapp.db.delete_account.

    The only failure this swallows is the practice tables not existing, which
    is a real state: a process that has never run `init_practice_db` still has
    to be able to delete an account. A customer who cannot delete their account
    is a worse defect than the orphaned rows this is here to prevent.

    On Postgres the swallow has to happen inside a savepoint. A failed
    statement poisons the whole transaction there, so catching the error
    without one would leave the surrounding DELETE FROM users unable to run
    either, which is exactly the outcome the guard exists to avoid.
    """
    if is_postgres():
        try:
            with cur.connection.transaction():
                _delete_practice_rows(cur, user_id)
        except Exception as exc:                                  # noqa: BLE001
            if not _is_missing_table(exc):
                raise
            log.info("practice tables absent, nothing to delete for user %s",
                     user_id)
        return
    try:
        _delete_practice_rows(cur, user_id)
    except sqlite3.OperationalError as exc:
        if not _is_missing_table(exc):
            raise
        log.info("practice tables absent, nothing to delete for user %s", user_id)


def _delete_practice_rows(cur, user_id: int) -> None:
    cur.execute(_q("DELETE FROM practice_seen WHERE user_id=?"), (user_id,))
    cur.execute(_q("DELETE FROM practice_sessions WHERE user_id=?"), (user_id,))


def _is_missing_table(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        return "no such table" in str(exc).lower()
    try:
        from psycopg import errors as pg_errors
    except Exception:                                             # noqa: BLE001
        return False
    return isinstance(exc, pg_errors.UndefinedTable)


_fingerprint: Optional[str] = None


def syllabus_fingerprint() -> str:
    """What the syllabus looked like when a row was written.

    Computed from the tree rather than hand-maintained, because a version
    constant is a version somebody forgets to bump, and a bank stamped with a
    stale one cannot be told apart from a bank that is still current.
    """
    global _fingerprint
    if _fingerprint is None:
        triples = sorted(
            (sub.id, sub.verification, sub.calculator)
            for pool in senior_syllabus.SUBJECTS.values() for sub in pool
        )
        _fingerprint = hashlib.sha256(
            canonical_json(triples).encode("utf-8")).hexdigest()[:12]
    return _fingerprint


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _holes(n: int) -> str:
    return ",".join("?" for _ in range(n))


def _now(now: Optional[int] = None) -> int:
    return int(time.time()) if now is None else int(now)


def _utc_day(now: Optional[int] = None) -> str:
    stamp = datetime.fromtimestamp(_now(now), tz=timezone.utc)
    return stamp.strftime("%Y-%m-%d")


def _count(cur) -> int:
    row = cur.fetchone()
    if row is None:
        return 0
    return int((row["n"] if is_postgres() else row[0]) or 0)


def _calculator_values(calculator: Optional[str]) -> list[str]:
    """Which stored calculator flags satisfy a student's choice.

    A question marked 'either' is fair game in both sittings, so a calculator
    filter that only matched exactly would hide most of the bank.
    """
    choice = (calculator or "").strip().lower()
    if choice in ("free", "assumed"):
        return [choice, "either"]
    return []


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def save_template(template: TemplateRow) -> None:
    """Insert or replace one template family. Idempotent on the template id."""
    with _cursor() as cur:
        cur.execute(
            _q("""INSERT INTO practice_templates
                (id, subject, subtopic_id, verify_kind, calculator, difficulty,
                 marks, question_pattern, answer_pattern, working_pattern,
                 params_json, constraints_json, check_pattern_json, status,
                 reject_reason, instances_made, instances_verified, model,
                 prompt_version, syllabus_version, created_at, retired_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (id) DO UPDATE SET
                 status=excluded.status,
                 reject_reason=excluded.reject_reason,
                 instances_made=excluded.instances_made,
                 instances_verified=excluded.instances_verified,
                 model=excluded.model,
                 retired_at=excluded.retired_at"""),
            (template.id, template.subject, template.subtopic_id,
             template.verify_kind, template.calculator, template.difficulty,
             template.marks, template.question_pattern, template.answer_pattern,
             template.working_pattern, canonical_json(template.params),
             canonical_json(template.constraints),
             canonical_json(template.check_pattern), template.status,
             template.reject_reason, template.instances_made,
             template.instances_verified, template.model,
             template.prompt_version, template.syllabus_version,
             template.created_at, template.retired_at),
        )


def _template_row(row) -> TemplateRow:
    row = dict(row)
    return TemplateRow(
        id=row["id"], subject=row["subject"], subtopic_id=row["subtopic_id"],
        verify_kind=row["verify_kind"], calculator=row["calculator"],
        difficulty=row["difficulty"], question_pattern=row["question_pattern"],
        answer_pattern=row["answer_pattern"],
        working_pattern=row["working_pattern"],
        params=_loads(row["params_json"], {}),
        constraints=_loads(row["constraints_json"], []),
        check_pattern=_loads(row["check_pattern_json"], {}),
        prompt_version=row["prompt_version"],
        syllabus_version=row["syllabus_version"],
        created_at=int(row["created_at"]), marks=row["marks"],
        status=row["status"], reject_reason=row["reject_reason"],
        instances_made=int(row["instances_made"] or 0),
        instances_verified=int(row["instances_verified"] or 0),
        model=row["model"],
        retired_at=row["retired_at"],
    )


def _loads(raw, fallback):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def get_template(template_id: str) -> Optional[TemplateRow]:
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM practice_templates WHERE id=?"),
                    (template_id,))
        row = cur.fetchone()
        return _template_row(row) if row is not None else None


def live_templates(subtopic_ids: Sequence[str]) -> list[TemplateRow]:
    ids = [str(s) for s in subtopic_ids if s]
    if not ids:
        return []
    with _cursor() as cur:
        cur.execute(
            _q(f"""SELECT * FROM practice_templates
                   WHERE status='live' AND subtopic_id IN ({_holes(len(ids))})
                   ORDER BY created_at"""),
            tuple(ids),
        )
        return [_template_row(row) for row in cur.fetchall()]


def set_template_status(template_id: str, status: str,
                        reason: Optional[str] = None,
                        now: Optional[int] = None) -> None:
    """Retire or reject a family. A rejected template is never deleted.

    Its reason is the only record of what the model gets wrong on a subtopic,
    and the filler's blocking rule is measured on exactly that.
    """
    stamp = _now(now)
    with _cursor() as cur:
        cur.execute(
            _q("""UPDATE practice_templates
                  SET status=?, reject_reason=?, retired_at=?
                  WHERE id=?"""),
            (status, reason, stamp if status != "live" else None, template_id),
        )


def bump_template_counts(template_id: str, made: int = 0,
                         verified: int = 0) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("""UPDATE practice_templates
                  SET instances_made=instances_made+?,
                      instances_verified=instances_verified+?
                  WHERE id=?"""),
            (int(made), int(verified), template_id),
        )


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def insert_item(*, template_id: str, subject: str, subtopic_id: str,
                calculator: str, difficulty: str, question: str, answer: str,
                working: str, params_json: str, check_json: str,
                variant_key: str, shuffle_key: float, verified_by: str,
                marks: Optional[int] = None,
                verifier_notes: Optional[str] = None,
                syllabus_version: Optional[str] = None,
                status: str = "live",
                now: Optional[int] = None) -> Optional[int]:
    """Store one verified question. None when this variant is already banked.

    The duplicate case is reported rather than raised because it is ordinary:
    the filler expanding more instances from an existing template will
    regenerate variants it already made, and the UNIQUE constraint is what
    makes an exact repeat impossible at the database level rather than merely
    unlikely in code.
    """
    with _cursor() as cur:
        return _insert_item(
            cur, template_id=template_id, subject=subject,
            subtopic_id=subtopic_id, calculator=calculator,
            difficulty=difficulty, question=question, answer=answer,
            working=working, params_json=params_json, check_json=check_json,
            variant_key=variant_key, shuffle_key=shuffle_key,
            verified_by=verified_by, marks=marks,
            verifier_notes=verifier_notes, syllabus_version=syllabus_version,
            status=status, now=now)


_ITEM_COLUMNS = """(template_id, subject, subtopic_id, calculator, difficulty,
                    marks, question, answer, working, params_json, check_json,
                    variant_key, shuffle_key, verified_by, verifier_notes,
                    syllabus_version, status, created_at)"""


def _insert_item(cur, *, template_id: str, subject: str, subtopic_id: str,
                 calculator: str, difficulty: str, question: str, answer: str,
                 working: str, params_json: str, check_json: str,
                 variant_key: str, shuffle_key: float, verified_by: str,
                 marks: Optional[int] = None,
                 verifier_notes: Optional[str] = None,
                 syllabus_version: Optional[str] = None,
                 status: str = "live",
                 now: Optional[int] = None) -> Optional[int]:
    """The insert itself, for callers already holding a cursor."""
    values = (template_id, subject, subtopic_id, calculator, difficulty, marks,
              question, answer, working, params_json, check_json, variant_key,
              float(shuffle_key), verified_by, verifier_notes,
              syllabus_version or syllabus_fingerprint(), status, _now(now))
    if is_postgres():
        cur.execute(
            f"""INSERT INTO practice_items {_ITEM_COLUMNS}
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (template_id, variant_key) DO NOTHING
                RETURNING id""",
            values,
        )
        row = cur.fetchone()
        return int(row["id"]) if row is not None else None
    cur.execute(
        f"""INSERT OR IGNORE INTO practice_items {_ITEM_COLUMNS}
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        values,
    )
    return int(cur.lastrowid) if cur.rowcount else None


def bulk_insert_items(records: Iterable[dict], now: Optional[int] = None
                      ) -> list[int]:
    """Bank a run of items in one transaction. The ids that were new.

    One transaction rather than one per item because the filler stores about
    sixty at a time and a seeded bank is thousands, and on SQLite `_cursor`
    opens, commits and closes a connection per call. Same statement, same
    duplicate handling, just not paid for once per question.
    """
    stored: list[int] = []
    with _cursor(transaction=True) as cur:
        for record in records:
            new_id = _insert_item(cur, now=now, **record)
            if new_id is not None:
                stored.append(new_id)
    return stored


def add_items(template: TemplateRow, instances: Iterable, verified_by: str,
              verifier_notes: Optional[str] = None,
              now: Optional[int] = None) -> int:
    """Bank a run of admitted instances from one template. Count actually stored.

    Takes `models.Instance` records, which already carry the variant and
    shuffle keys, so the factory never has to know the column list.
    """
    records = [{
        "template_id": template.id, "subject": template.subject,
        "subtopic_id": template.subtopic_id, "calculator": template.calculator,
        "difficulty": template.difficulty, "marks": template.marks,
        "question": instance.question, "answer": instance.answer,
        "working": instance.working, "params_json": instance.params_json,
        "check_json": instance.check_json, "variant_key": instance.variant_key,
        "shuffle_key": instance.shuffle_key, "verified_by": verified_by,
        "verifier_notes": verifier_notes,
        "syllabus_version": template.syllabus_version,
    } for instance in instances]
    return len(bulk_insert_items(records, now=now))


def _item_row(row) -> ItemRow:
    row = dict(row)
    return ItemRow(
        id=int(row["id"]), template_id=row["template_id"],
        subject=row["subject"], subtopic_id=row["subtopic_id"],
        calculator=row["calculator"], difficulty=row["difficulty"],
        question=row["question"], answer=row["answer"], working=row["working"],
        params_json=row["params_json"], check_json=row["check_json"],
        variant_key=row["variant_key"], shuffle_key=float(row["shuffle_key"]),
        verified_by=row["verified_by"],
        syllabus_version=row["syllabus_version"],
        created_at=int(row["created_at"]), marks=row["marks"],
        verifier_notes=row["verifier_notes"], status=row["status"],
    )


def get_item(item_id: int) -> Optional[ItemRow]:
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM practice_items WHERE id=?"), (int(item_id),))
        row = cur.fetchone()
        return _item_row(row) if row is not None else None


def live_items(subtopic_ids: Sequence[str], limit: int = 1000) -> list[ItemRow]:
    """Every live item in a scope. For the filler and for offline re-derivation."""
    ids = [str(s) for s in subtopic_ids if s]
    if not ids:
        return []
    with _cursor() as cur:
        cur.execute(
            _q(f"""SELECT * FROM practice_items
                   WHERE status='live' AND subtopic_id IN ({_holes(len(ids))})
                   ORDER BY id LIMIT ?"""),
            tuple(ids) + (int(limit),),
        )
        return [_item_row(row) for row in cur.fetchall()]


def bank_depth(subtopic_ids: Optional[Sequence[str]] = None) -> dict[str, int]:
    """Live items per subtopic. The filler's first and cheapest question."""
    sql = ("SELECT subtopic_id, COUNT(*) AS n FROM practice_items "
           "WHERE status='live'")
    params: tuple = ()
    if subtopic_ids is not None:
        ids = [str(s) for s in subtopic_ids if s]
        if not ids:
            return {}
        sql += f" AND subtopic_id IN ({_holes(len(ids))})"
        params = tuple(ids)
    sql += " GROUP BY subtopic_id"
    with _cursor() as cur:
        cur.execute(_q(sql), params)
        return {row["subtopic_id"]: int(row["n"] if is_postgres() else row[1])
                for row in cur.fetchall()}


def item_counts_by_template(subtopic_id: str) -> dict[str, int]:
    """How many variants each family has already yielded.

    The filler compares this against a template's parameter space to decide
    whether a deficit can be closed by expanding more instances, which costs no
    LLM call at all.
    """
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT template_id, COUNT(*) AS n FROM practice_items
                  WHERE subtopic_id=? GROUP BY template_id"""),
            (subtopic_id,),
        )
        return {row["template_id"]: int(row["n"] if is_postgres() else row[1])
                for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(user_id: int, subject: str, scope_id: str,
                   scope_label: str, calculator: Optional[str] = None,
                   now: Optional[int] = None) -> str:
    """Start one grinding session. Stores what was picked, not what it resolved to.

    Freezing the resolved leaf list here would freeze the session against a
    syllabus that later gains a subtopic, so a student who left a tab open
    would keep being served the old scope for ever.
    """
    stamp = _now(now)
    session_id = uuid.uuid4().hex
    with _cursor() as cur:
        cur.execute(
            _q("""INSERT INTO practice_sessions
                (id,user_id,subject,scope_id,scope_label,calculator,
                 created_at,last_seen_at)
                VALUES (?,?,?,?,?,?,?,?)"""),
            (session_id, int(user_id), subject, scope_id, scope_label,
             calculator or None, stamp, stamp),
        )
    return session_id


def get_session(session_id: str, user_id: int) -> Optional[dict]:
    """One session, and only for the account that owns it.

    The user id is a required argument rather than an optional filter so no
    caller can forget it. Another student's session id must read as absent,
    not as somebody else's questions.
    """
    with _cursor() as cur:
        cur.execute(
            _q("SELECT * FROM practice_sessions WHERE id=? AND user_id=?"),
            (session_id, int(user_id)),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None


def touch_session(session_id: str, *, served: int = 0, answered: int = 0,
                  correct: int = 0, now: Optional[int] = None) -> None:
    with _cursor() as cur:
        cur.execute(
            _q("""UPDATE practice_sessions
                  SET served=served+?, answered=answered+?, correct=correct+?,
                      last_seen_at=?
                  WHERE id=?"""),
            (int(served), int(answered), int(correct), _now(now), session_id),
        )


def recent_sessions(user_id: int, limit: int = 10) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT * FROM practice_sessions WHERE user_id=?
                  ORDER BY last_seen_at DESC LIMIT ?"""),
            (int(user_id), int(limit)),
        )
        return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# The draw
# ---------------------------------------------------------------------------

def draw(user_id: int, subtopic_ids: Sequence[str], limit: int = 10, *,
         exclude_ids: Sequence[int] = (), calculator: Optional[str] = None
         ) -> DrawResult:
    """The arrow's supply: the next `limit` questions for this student.

    Three anti-repetition layers meet here. The bank's UNIQUE constraint has
    already made an exact duplicate unstorable; `shuffle_key` has already
    divorced the serving order from the parameter sweep order that produced the
    items; and this function does the third, filtering candidates so a student
    is never handed two questions from the same family in one batch or within
    SPACING_WINDOW of the last one they saw.

    The filtering is Python rather than SQL because `DISTINCT ON` is Postgres
    only and this app also runs on SQLite, and one draw that behaves
    identically on both backends is worth more than one clever statement.

    When the scope is too narrow to satisfy the rule the result says
    `spacing='relaxed'` instead of pretending, and when the unseen stock is
    exhausted it repeats the oldest items with `dry=True`. It never widens the
    scope: a student who chose Antidifferentiation and is quietly fed
    confidence intervals has been lied to by the one feature whose entire
    promise is the scope.
    """
    ids = list(dict.fromkeys(str(s) for s in subtopic_ids if s))
    if not ids:
        return DrawResult(unstocked=True)
    if len(ids) > MAX_SCOPE_IDS:
        raise ValueError(
            f"a practice scope of {len(ids)} subtopics exceeds the "
            f"{MAX_SCOPE_IDS} this draw will expand as bind parameters. "
            "Truncating would serve a subset of what the student asked for "
            "without saying so, so this refuses instead.")
    limit = max(1, min(int(limit), 50))
    excluded = [int(i) for i in exclude_ids][:200]
    calc = _calculator_values(calculator)

    scope_sql = f"i.subtopic_id IN ({_holes(len(ids))})"
    scope_params: list = list(ids)
    if calc:
        scope_sql += f" AND i.calculator IN ({_holes(len(calc))})"
        scope_params += calc

    with _cursor() as cur:
        # Is anything at all banked for this scope? Distinguishes "you have
        # seen everything" from "nothing has been generated yet", which the
        # student reads as two very different sentences.
        cur.execute(
            _q(f"SELECT COUNT(*) AS n FROM practice_items i "
               f"WHERE i.status='live' AND {scope_sql}"),
            tuple(scope_params),
        )
        stocked = _count(cur)
        if not stocked:
            return DrawResult(unstocked=True)

        unseen_sql = ("NOT EXISTS (SELECT 1 FROM practice_seen s "
                      "WHERE s.user_id=? AND s.item_id=i.id)")
        cur.execute(
            _q(f"""SELECT COUNT(*) AS n FROM practice_items i
                   WHERE i.status='live' AND {scope_sql} AND {unseen_sql}"""),
            tuple(scope_params) + (int(user_id),),
        )
        remaining_unseen = _count(cur)

        # How many distinct families have live stock here. Below four, the
        # one-per-template rule cannot be met and saying so is better than
        # quietly returning three questions when ten were asked for.
        cur.execute(
            _q(f"""SELECT COUNT(DISTINCT i.template_id) AS n
                   FROM practice_items i
                   WHERE i.status='live' AND {scope_sql}"""),
            tuple(scope_params),
        )
        family_count = _count(cur)

        candidate_sql = (f"SELECT i.* FROM practice_items i "
                         f"WHERE i.status='live' AND {scope_sql} AND {unseen_sql}")
        candidate_params = tuple(scope_params) + (int(user_id),)
        if excluded:
            candidate_sql += f" AND i.id NOT IN ({_holes(len(excluded))})"
            candidate_params += tuple(excluded)
        candidate_sql += " ORDER BY i.shuffle_key LIMIT ?"
        want = limit * OVERFETCH
        cur.execute(_q(candidate_sql), candidate_params + (want,))
        candidates = [_item_row(row) for row in cur.fetchall()]

        # The families this student saw most recently, inside this scope only.
        # A template they last met in another topic is irrelevant here.
        cur.execute(
            _q(f"""SELECT template_id FROM practice_seen
                   WHERE user_id=? AND subtopic_id IN ({_holes(len(ids))})
                   ORDER BY last_seen_at DESC, item_id DESC LIMIT ?"""),
            (int(user_id),) + tuple(ids) + (SPACING_WINDOW,),
        )
        recent = [row["template_id"] if is_postgres() else row[0]
                  for row in cur.fetchall()]

        picked, spacing = _apply_spacing(candidates, recent, limit,
                                         family_count)

        repeats: set[int] = set()
        exhausted = len(candidates) < want
        if len(picked) < limit and exhausted:
            # Every unseen item in the scope is either in this batch or in the
            # browser's buffer. Going round again is the honest fallback, and
            # oldest first so the student meets what they have most forgotten.
            taken = {item.id for item in picked} | set(excluded)
            repeat_sql = (f"SELECT i.* FROM practice_items i "
                          f"JOIN practice_seen s ON s.item_id=i.id "
                          f"WHERE s.user_id=? AND i.status='live' AND {scope_sql}")
            repeat_params: tuple = (int(user_id),) + tuple(scope_params)
            if taken:
                repeat_sql += f" AND i.id NOT IN ({_holes(len(taken))})"
                repeat_params += tuple(sorted(taken))
            repeat_sql += " ORDER BY s.last_seen_at ASC, i.id ASC LIMIT ?"
            cur.execute(_q(repeat_sql), repeat_params + (limit - len(picked),))
            for row in cur.fetchall():
                item = _item_row(row)
                picked.append(item)
                repeats.add(item.id)

    return DrawResult(items=picked, remaining_unseen=remaining_unseen,
                      dry=bool(repeats), repeats=frozenset(repeats),
                      spacing=spacing, unstocked=False)


def _apply_spacing(candidates: list[ItemRow], recent: Sequence[str],
                   limit: int, family_count: int) -> tuple[list[ItemRow], str]:
    """Choose from the over-fetched candidates without repeating a family.

    Two passes. The strict one takes at most one item per template and skips
    any family seen in the last SPACING_WINDOW items. If that cannot fill the
    batch, the relaxed pass takes what is left, still refusing to place two
    items from one family next to each other, and the caller reports
    `spacing='relaxed'` so the interface can say the scope is thin rather than
    implying variety it does not have.
    """
    blocked = set(recent)
    picked: list[ItemRow] = []
    used: set[str] = set()
    for item in candidates:
        if len(picked) >= limit:
            break
        if item.template_id in used or item.template_id in blocked:
            continue
        picked.append(item)
        used.add(item.template_id)

    relaxed_added = 0
    if len(picked) < limit:
        chosen = {item.id for item in picked}
        for item in candidates:
            if len(picked) >= limit:
                break
            if item.id in chosen:
                continue
            if picked and picked[-1].template_id == item.template_id:
                continue
            picked.append(item)
            chosen.add(item.id)
            relaxed_added += 1

    # A batch that ran short because the bank is nearly empty still honoured
    # the rule, so it stays 'strict'. Only actually breaking the rule, or a
    # scope too thin to hold it in the first place, is reported as relaxed.
    spacing = "relaxed" if (relaxed_added or family_count < 4) else "strict"
    return picked, spacing


# ---------------------------------------------------------------------------
# Seen state
# ---------------------------------------------------------------------------

def record_seen(user_id: int, events: Iterable[SeenEvent],
                now: Optional[int] = None) -> int:
    """Record that this student was shown these items. Count of rows touched.

    Idempotent on purpose, in two senses. The composite primary key means a
    replayed batch updates a row rather than adding one, and `times_seen` only
    advances when the incoming sighting is genuinely newer than the one on
    file, so a batch re-sent after a flaky connection changes nothing at all.
    An outcome that arrives later still lands, because a student who answers a
    question after it was recorded as served is telling us something new.
    """
    stamp = _now(now)
    valid = [e for e in events if isinstance(e, SeenEvent) and e.valid()]
    if not valid:
        return 0
    item_ids = list(dict.fromkeys(int(e.item_id) for e in valid))[:200]
    with _cursor(transaction=True) as cur:
        cur.execute(
            _q(f"""SELECT id, subtopic_id, template_id FROM practice_items
                   WHERE id IN ({_holes(len(item_ids))})"""),
            tuple(item_ids),
        )
        known = {int(dict(row)["id"]): dict(row) for row in cur.fetchall()}
        touched = 0
        for event in valid:
            meta = known.get(int(event.item_id))
            if meta is None:
                # An id the bank has never held. Recording it would put a row
                # in a student's history pointing at nothing.
                continue
            seen_at = int(event.at) if event.at else stamp
            cur.execute(
                _q("""INSERT INTO practice_seen
                      (user_id,item_id,subtopic_id,template_id,outcome,
                       times_seen,first_seen_at,last_seen_at)
                      VALUES (?,?,?,?,?,1,?,?)
                      ON CONFLICT (user_id,item_id) DO UPDATE SET
                        times_seen = practice_seen.times_seen + CASE
                          WHEN excluded.last_seen_at > practice_seen.last_seen_at
                          THEN 1 ELSE 0 END,
                        outcome = COALESCE(excluded.outcome,
                                           practice_seen.outcome),
                        last_seen_at = CASE
                          WHEN excluded.last_seen_at > practice_seen.last_seen_at
                          THEN excluded.last_seen_at
                          ELSE practice_seen.last_seen_at END"""),
                (int(user_id), int(event.item_id), meta["subtopic_id"],
                 meta["template_id"], event.outcome, seen_at, seen_at),
            )
            touched += 1
        return touched


def seen_count(user_id: int, subtopic_ids: Sequence[str]) -> int:
    ids = [str(s) for s in subtopic_ids if s]
    if not ids:
        return 0
    with _cursor() as cur:
        cur.execute(
            _q(f"""SELECT COUNT(*) AS n FROM practice_seen
                   WHERE user_id=? AND subtopic_id IN ({_holes(len(ids))})"""),
            (int(user_id),) + tuple(ids),
        )
        return _count(cur)


def reset_seen(user_id: int, subtopic_ids: Sequence[str]) -> int:
    """Forget this student's history for a scope. Rows removed.

    Going round again is a button the student presses, never something the
    draw does on their behalf.
    """
    ids = [str(s) for s in subtopic_ids if s]
    if not ids:
        return 0
    with _cursor() as cur:
        cur.execute(
            _q(f"""DELETE FROM practice_seen
                   WHERE user_id=? AND subtopic_id IN ({_holes(len(ids))})"""),
            (int(user_id),) + tuple(ids),
        )
        return int(cur.rowcount or 0)


# ---------------------------------------------------------------------------
# Demand, budget and node state
# ---------------------------------------------------------------------------

def note_scope_demand(subtopic_ids: Sequence[str], dry: bool = False,
                      now: Optional[int] = None) -> None:
    """Record that a student asked for these subtopics, and whether we failed.

    Called when a session starts and again whenever a draw comes back dry, not
    on every arrow press: forty-two upserts per question would put the filler's
    bookkeeping on the request path it is meant to stay out of.
    """
    ids = [str(s) for s in subtopic_ids if s]
    if not ids:
        return
    stamp = _now(now)
    with _cursor(transaction=True) as cur:
        for sid in ids:
            cur.execute(
                _q("""INSERT INTO practice_scope_demand
                      (subtopic_id,requests,dry_requests,last_requested_at)
                      VALUES (?,1,?,?)
                      ON CONFLICT (subtopic_id) DO UPDATE SET
                        requests=practice_scope_demand.requests+1,
                        dry_requests=practice_scope_demand.dry_requests+?,
                        last_requested_at=excluded.last_requested_at"""),
                (sid, 1 if dry else 0, stamp, 1 if dry else 0),
            )


def scope_demand() -> dict[str, dict]:
    """What students actually grind, keyed by subtopic. The filler's priority."""
    with _cursor() as cur:
        cur.execute("SELECT * FROM practice_scope_demand")
        return {dict(row)["subtopic_id"]: dict(row) for row in cur.fetchall()}


def note_generation(subtopic_id: str, *, calls: int = 0,
                    templates_kept: int = 0, templates_rejected: int = 0,
                    items_kept: int = 0, items_discarded: int = 0,
                    now: Optional[int] = None) -> None:
    """Per-day, per-subtopic accounting, so last night's spend is one query."""
    day = _utc_day(now)
    with _cursor() as cur:
        cur.execute(
            _q("""INSERT INTO practice_generation_log
                  (day,subtopic_id,calls,templates_kept,templates_rejected,
                   items_kept,items_discarded)
                  VALUES (?,?,?,?,?,?,?)
                  ON CONFLICT (day,subtopic_id) DO UPDATE SET
                    calls=practice_generation_log.calls+excluded.calls,
                    templates_kept=practice_generation_log.templates_kept
                                   +excluded.templates_kept,
                    templates_rejected=practice_generation_log.templates_rejected
                                       +excluded.templates_rejected,
                    items_kept=practice_generation_log.items_kept
                               +excluded.items_kept,
                    items_discarded=practice_generation_log.items_discarded
                                    +excluded.items_discarded"""),
            (day, subtopic_id, int(calls), int(templates_kept),
             int(templates_rejected), int(items_kept), int(items_discarded)),
        )


def calls_today(now: Optional[int] = None) -> int:
    """LLM calls the filler has already spent today, read from the database.

    In the database and not in a process variable, because a cron that fires
    twice or a container that restarts would otherwise spend the daily cap
    twice over, and the cap is the only thing bounding an overnight bill.
    """
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT COALESCE(SUM(calls),0) AS n
                  FROM practice_generation_log WHERE day=?"""),
            (_utc_day(now),),
        )
        return _count(cur)


def generation_log(day: Optional[str] = None) -> list[dict]:
    with _cursor() as cur:
        cur.execute(
            _q("""SELECT * FROM practice_generation_log WHERE day=?
                  ORDER BY subtopic_id"""),
            (day or _utc_day(),),
        )
        return [dict(row) for row in cur.fetchall()]


def node_state(subtopic_id: str) -> dict:
    with _cursor() as cur:
        cur.execute(_q("SELECT * FROM practice_node_state WHERE subtopic_id=?"),
                    (subtopic_id,))
        row = cur.fetchone()
    if row is None:
        return {"subtopic_id": subtopic_id, "consecutive_failures": 0,
                "blocked_reason": None, "blocked_at": None,
                "last_filled_at": None}
    return dict(row)


def _upsert_node(subtopic_id: str, assignments: str, row: tuple) -> None:
    """One row per subtopic, created on first touch, then patched.

    `row` is what to insert when the subtopic has no state yet; `assignments`
    is what to change when it has. Both backends read `excluded.` the same way,
    so this is one statement rather than two.
    """
    with _cursor() as cur:
        cur.execute(
            _q(f"""INSERT INTO practice_node_state
                   (subtopic_id,consecutive_failures,blocked_reason,
                    blocked_at,last_filled_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT (subtopic_id) DO UPDATE SET {assignments}"""),
            row,
        )


def record_node_failure(subtopic_id: str) -> int:
    """Count one rejected template against a subtopic. The new run of failures."""
    _upsert_node(
        subtopic_id,
        "consecutive_failures=practice_node_state.consecutive_failures+1",
        (subtopic_id, 1, None, None, None))
    return int(node_state(subtopic_id)["consecutive_failures"] or 0)


def clear_node_failures(subtopic_id: str, now: Optional[int] = None) -> None:
    """A template landed, so the run of failures is over and the block lifts."""
    _upsert_node(
        subtopic_id,
        ("consecutive_failures=0, blocked_reason=NULL, blocked_at=NULL, "
         "last_filled_at=excluded.last_filled_at"),
        (subtopic_id, 0, None, None, _now(now)))


def block_node(subtopic_id: str, reason: str, now: Optional[int] = None) -> None:
    """Stop the filler spending budget on a subtopic it cannot do.

    A block is a row a human can read and clear, not a constant in the code,
    because the reason it was blocked is usually a prompt or a checker that
    somebody will fix.
    """
    _upsert_node(
        subtopic_id,
        "blocked_reason=excluded.blocked_reason, blocked_at=excluded.blocked_at",
        (subtopic_id, 0, reason, _now(now), None))


def unblock_node(subtopic_id: str) -> None:
    _upsert_node(
        subtopic_id,
        "blocked_reason=NULL, blocked_at=NULL, consecutive_failures=0",
        (subtopic_id, 0, None, None, None))


def blocked_nodes() -> dict[str, dict]:
    with _cursor() as cur:
        cur.execute("SELECT * FROM practice_node_state "
                    "WHERE blocked_reason IS NOT NULL")
        return {dict(row)["subtopic_id"]: dict(row) for row in cur.fetchall()}


def note_filled(subtopic_id: str, now: Optional[int] = None) -> None:
    _upsert_node(subtopic_id, "last_filled_at=excluded.last_filled_at",
                 (subtopic_id, 0, None, None, _now(now)))


def new_shuffle_key(rng: Optional[random.Random] = None) -> float:
    """A serving position that owes nothing to the parameters.

    This is the layer that stops "differentiate 2x^3" being followed by
    "differentiate 3x^3", which the uniqueness constraint and the spacing rule
    both consider perfectly distinct questions.
    """
    return (rng or random).random()
