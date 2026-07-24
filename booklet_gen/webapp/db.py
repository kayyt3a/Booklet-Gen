"""Tiny SQLite data layer for the Folio web app: users and jobs.

Deliberately dependency-free (stdlib sqlite3 + werkzeug password hashing).
Fine for a single-process launch; swap for Postgres when you outgrow it.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = Path(os.environ.get("FOLIO_DB", "folio.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    with _connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,            -- running | done | error
                label TEXT,
                error TEXT,
                path TEXT,                       -- single-booklet output
                dir TEXT,                        -- term-plan output folder
                created_at INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )


# ---------- users ----------

def create_user(email: str, password: str) -> int:
    email = email.strip().lower()
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
            (email, generate_password_hash(password), int(time.time())),
        )
        return int(cur.lastrowid)


def get_user_by_email(email: str) -> Optional[sqlite3.Row]:
    with _connect() as c:
        return c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with _connect() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def verify_login(email: str, password: str) -> Optional[sqlite3.Row]:
    user = get_user_by_email(email)
    if user and check_password_hash(user["password_hash"], password):
        return user
    return None


# ---------- jobs ----------

def create_job(job_id: str, user_id: int, label: str) -> None:
    with _connect() as c:
        c.execute(
            "INSERT INTO jobs (id, user_id, status, label, created_at) VALUES (?,?,?,?,?)",
            (job_id, user_id, "running", label, int(time.time())),
        )


def finish_job(job_id: str, *, path: str = None, dir: str = None) -> None:
    with _connect() as c:
        c.execute("UPDATE jobs SET status='done', path=?, dir=? WHERE id=?", (path, dir, job_id))


def fail_job(job_id: str, error: str) -> None:
    with _connect() as c:
        c.execute("UPDATE jobs SET status='error', error=? WHERE id=?", (error[:500], job_id))


def get_job(job_id: str) -> Optional[sqlite3.Row]:
    with _connect() as c:
        return c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def jobs_started_last_24h(user_id: int) -> int:
    """Count jobs a user has started in the last 24h, for a simple abuse guard.
    Every job counts once regardless of type, so a term plan (heavier) counts
    the same as a single booklet - kept simple on purpose."""
    since = int(time.time()) - 86400
    with _connect() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE user_id=? AND created_at>=?",
            (user_id, since),
        ).fetchone()
        return row[0] if row else 0
