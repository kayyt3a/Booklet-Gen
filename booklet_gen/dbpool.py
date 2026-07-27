"""Shared Postgres connection handling.

One database backs both halves of Folio: the web app's accounts and jobs
(`webapp/db.py`) and the RAG vector store (`rag/store.py`). Keeping the
connection logic here means a single DATABASE_URL and one pool to tune.

Set DATABASE_URL to a normal Postgres URL, e.g.
    postgresql://user:pass@host/dbname
Managed providers (Neon, Supabase, Render) hand you one directly. When it is
unset, Folio falls back to local storage: SQLite for accounts and an on-disk
Chroma store for RAG, which is what a local dev checkout wants.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

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


def close_pool() -> None:
    """Close the pool. Mainly for tests."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
