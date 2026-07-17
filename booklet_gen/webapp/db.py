"""Tiny SQLite data layer for the Folio web app: users, credits, jobs.

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

# New accounts start with a few free credits so a parent can try it.
SIGNUP_FREE_CREDITS = int(os.environ.get("FOLIO_SIGNUP_CREDITS", "2"))


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
                credits INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                stripe_session TEXT UNIQUE,      -- idempotency: one grant per session
                credits INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )


# ---------- users ----------

def create_user(email: str, password: str) -> int:
    email = email.strip().lower()
    with _connect() as c:
        cur = c.execute(
            "INSERT INTO users (email, password_hash, credits, created_at) VALUES (?,?,?,?)",
            (email, generate_password_hash(password), SIGNUP_FREE_CREDITS, int(time.time())),
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


# ---------- credits ----------

def get_credits(user_id: int) -> int:
    u = get_user(user_id)
    return int(u["credits"]) if u else 0


def spend_credits(user_id: int, amount: int) -> bool:
    """Atomically deduct `amount` credits. Returns False if insufficient."""
    with _connect() as c:
        cur = c.execute(
            "UPDATE users SET credits = credits - ? WHERE id=? AND credits >= ?",
            (amount, user_id, amount),
        )
        return cur.rowcount == 1


def add_credits(user_id: int, amount: int) -> None:
    with _connect() as c:
        c.execute("UPDATE users SET credits = credits + ? WHERE id=?", (amount, user_id))


def refund_credits(user_id: int, amount: int) -> None:
    add_credits(user_id, amount)


# ---------- payments (idempotent credit grants) ----------

def grant_payment(user_id: int, stripe_session: str, credits: int) -> bool:
    """Record a Stripe payment and add credits, exactly once per session id.
    Returns True if this call granted the credits, False if already granted."""
    with _connect() as c:
        try:
            c.execute(
                "INSERT INTO payments (user_id, stripe_session, credits, created_at) VALUES (?,?,?,?)",
                (user_id, stripe_session, credits, int(time.time())),
            )
        except sqlite3.IntegrityError:
            return False  # already processed this session
        c.execute("UPDATE users SET credits = credits + ? WHERE id=?", (credits, user_id))
        return True


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
