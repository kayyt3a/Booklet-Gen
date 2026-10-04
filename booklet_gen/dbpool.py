"""Shared Postgres connection handling.

One database backs both halves of FolioAI: the web app's accounts and jobs
(`webapp/db.py`) and the RAG vector store (`rag/store.py`). Keeping the
connection logic here means a single DATABASE_URL and one pool to tune.

Set DATABASE_URL to a normal Postgres URL, e.g.
    postgresql://user:pass@host/dbname
Managed providers (Neon, Supabase, Render) hand you one directly. When it is
unset, FolioAI falls back to local storage: SQLite for accounts and an on-disk
Chroma store for RAG, which is what a local dev checkout wants.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Optional

from dotenv import load_dotenv

# Load .env here rather than relying on some other module having done it
# first. config.py also calls this, but scripts that only touch the database
# or the vector store (rag_status.py, ingest_folder.py) never import config,
# and would silently fall back to local storage while DATABASE_URL sat
# correctly in .env. Repeat calls are cheap and do not override real
# environment variables, so a shell-set DATABASE_URL still wins.
load_dotenv()

log = logging.getLogger(__name__)

_pool = None
_pool_lock = threading.Lock()


def database_url() -> Optional[str]:
    """The configured Postgres URL, or None when running on local storage."""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return None
    # Some providers still hand out the legacy postgres:// scheme.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def is_postgres() -> bool:
    return database_url() is not None


def get_pool():
    """Lazily build a shared connection pool. Import of psycopg is deferred so
    a local SQLite/Chroma checkout does not need the dependency installed."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        url = database_url()
        if not url:
            raise RuntimeError("DATABASE_URL is not set")
        from psycopg_pool import ConnectionPool
        # Managed free tiers cap connections tightly, and gunicorn runs
        # 2 workers x 4 threads, so keep this small.
        _pool = ConnectionPool(
            url, min_size=1,
            max_size=int(os.environ.get("FOLIO_DB_POOL_MAX", "5")),
            kwargs={"autocommit": True},
            open=True,
        )
        log.info("db.pool_opened", extra={"max_size": _pool.max_size})
        return _pool


@contextmanager
def advisory_lock(key: int):
    """Serialize a block of DDL across every process and thread using this
    database, keyed by an arbitrary int agreed on by both callers.

    `CREATE TABLE/INDEX/EXTENSION IF NOT EXISTS` still races when two sessions
    run it at the same instant: both can see "does not exist" and both
    attempt the create, and the loser gets a duplicate-key error against a
    system catalog rather than a friendly "already exists". This is exactly
    what happened the first time this app booted two gunicorn workers against
    a fresh database. A session-level advisory lock makes the second caller
    simply wait instead of racing.
    """
    with get_pool().connection() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            yield conn
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


# Every table this app owns in the public schema, not yet behind row level
# security. Owned by current_user because ALTER TABLE needs ownership, and a
# table someone made by hand in the dashboard would otherwise fail the
# statement and take the whole boot down with it.
_UNPROTECTED_TABLES_SQL = """
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public'
      AND tableowner = current_user
      AND NOT rowsecurity
"""


def lock_public_tables(conn) -> list[str]:
    """Put every table this app owns behind row level security.

    Supabase serves every table in the `public` schema over a REST API at
    https://<project>.supabase.co/rest/v1/, and that API authenticates with
    the project's ANON key, which Supabase treats as public: it is meant to be
    shipped inside browser code. With row level security off, that key reads,
    edits and deletes every row. For this app that meant every customer's
    email and password hash, the credit ledger (free booklets for anyone who
    edits it), payments, and every stored PDF. Supabase flagged it as critical
    on 27 September 2026. Nothing in this codebase had ever turned RLS on.

    THIS APP NEVER USES THAT API. It talks to Postgres directly through
    DATABASE_URL as the role that created the tables, and a table's owner is
    not subject to its own row level security. So turning it on with NO
    policies at all is exactly right: the REST API's anon and authenticated
    roles see nothing, and the app sees everything it did before.

    NOT `FORCE ROW LEVEL SECURITY`. That one clause is the difference between
    this fix and a total outage. FORCE applies the policies to the owner as
    well, there are no policies, and an owner with no policy granting it
    anything reads zero rows: every login fails and every booklet is gone,
    with no error anywhere, because an empty result is not an error.
    scripts/check_supabase_rls.py refuses the word.

    Run on every boot rather than once, and over every owned table rather than
    a list of names, because the defect was never one table: it was that
    creating a table in this schema made it public by default. A table added
    next year is covered at the next restart without anyone remembering to.
    """
    locked = []
    for (table,) in conn.execute(_UNPROTECTED_TABLES_SQL).fetchall():
        conn.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        locked.append(table)
    if locked:
        log.warning("db.rls_enabled", extra={"tables": ", ".join(locked)})
    return locked


def close_pool() -> None:
    """Close the pool. Mainly for tests."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
