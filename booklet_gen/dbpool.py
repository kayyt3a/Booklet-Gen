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


def close_pool() -> None:
    """Close the pool. Mainly for tests."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
