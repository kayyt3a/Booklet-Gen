"""Checks the practice bank's schema, on both backends, and on old databases.

The bank is two schemas, one per backend, maintained by hand. Every way that
arrangement fails quietly is in this file:

  1. the two declarations drift, so a column exists locally and not in
     production, and the failure only appears the first time a live student
     presses the arrow,
  2. `CREATE TABLE IF NOT EXISTS` does exactly nothing on a table that is
     already there, so a column added in a later release never reaches the one
     database that matters. This is the trap the migration lists exist for, and
     it is measured here by upgrading a database that already has the tables,
  3. an index named in a later release never reaching an existing database
     either, which turns the draw from an index range scan into a table scan on
     the pool that also serves checkout,
  4. the UNIQUE (template_id, variant_key) constraint being declared but not
     enforced. It is the first of the three anti-repetition layers and the only
     one that makes an exact duplicate question impossible rather than merely
     unlikely,
  5. the practice engine reaching anywhere near the money tables,
  6. account deletion. Both directions are defects: leaving a student's answer
     history behind after they deleted their account breaks what the Privacy
     page promises, and aborting the deletion because the practice tables are
     absent is worse still, because then the customer cannot leave at all.

Everything is measured off a real SQLite database that this script creates and
upgrades, except the Postgres half, which has no database to talk to here and
is therefore measured off the declarations themselves.

    PYTHONPATH=. python scripts/check_practice_bank_schema.py
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import sqlite3
import tempfile
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("FLASK_SECRET_KEY", "practice-schema-check-secret-1234567")
_TMP = Path(tempfile.mkdtemp(prefix="folio-practice-schema-"))
os.environ["FOLIO_DB"] = str(_TMP / "folio.db")

from booklet_gen.practice import fixtures, store  # noqa: E402
from booklet_gen.practice.models import SeenEvent  # noqa: E402
from booklet_gen.webapp import db  # noqa: E402

PASSED = 0
TOTAL = 0


def check(good: bool, label: str, detail: str = "") -> None:
    global PASSED, TOTAL
    TOTAL += 1
    PASSED += bool(good)
    print(f"{'ok  ' if good else '*** FAIL ***':<14}{label}")
    if not good and detail:
        print(f"{'':<14}{detail[:500]}")


# A build without the bank has no store functions at all. Reached through these
# shims so such a build reports every behaviour it gets wrong instead of
# stopping on an AttributeError at line one.
def _init() -> str:
    initialiser = getattr(store, "init_practice_db", None)
    if initialiser is None:
        return "no init_practice_db"
    try:
        initialiser()
        return ""
    except Exception as exc:                                      # noqa: BLE001
        return str(exc)


def columns_of(path: Path, table: str) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def indexes_of(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}


def declared_columns(schema_sql: str) -> dict[str, list[str]]:
    """Column names per table, read out of a schema declaration.

    Used for the Postgres half, which cannot be measured against a live
    database from here.
    """
    tables: dict[str, list[str]] = {}
    for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", schema_sql,
            re.S):
        names = []
        for line in match.group(2).splitlines():
            line = line.strip().rstrip(",")
            if not line:
                continue
            first = line.split()[0]
            if first.upper() in {"UNIQUE", "PRIMARY", "FOREIGN", "CONSTRAINT",
                                 "CHECK"}:
                continue
            names.append(first)
        tables[match.group(1)] = names
    return tables


# --------------------------------------------------------------------------
print("== a fresh database gets every table, column and index ==")

fresh = _TMP / "fresh.db"
db.DB_PATH = fresh
db.init_db()
failure = _init()
check(not failure, "init_practice_db runs on a database that has just been "
                   "created", failure)

pg_tables = declared_columns(getattr(store, "_PG_SCHEMA", ""))
sqlite_tables = declared_columns(getattr(store, "_SQLITE_SCHEMA", ""))
expected_tables = set(getattr(store, "TABLES", ()))
check(expected_tables and set(sqlite_tables) == expected_tables,
      f"all {len(expected_tables)} practice tables are declared "
      f"({sorted(expected_tables)})")

for table, declared in sorted(sqlite_tables.items()):
    live = columns_of(fresh, table)
    check(live == set(declared),
          f"{table} has its {len(declared)} columns",
          f"declared but missing: {sorted(set(declared) - live)}; "
          f"present but undeclared: {sorted(live - set(declared))}")

live_indexes = indexes_of(fresh)
for statement in getattr(store, "_INDEXES", ()):
    name = statement.split("IF NOT EXISTS ")[1].split()[0]
    check(name in live_indexes,
          f"index {name} exists",
          "without it the draw scans the table on every arrow press, on the "
          "same connection pool that serves checkout")

# --------------------------------------------------------------------------
print("\n== the two backends declare the same bank ==")

check(set(pg_tables) == set(sqlite_tables),
      "both schemas declare the same tables",
      f"Postgres only: {sorted(set(pg_tables) - set(sqlite_tables))}; "
      f"SQLite only: {sorted(set(sqlite_tables) - set(pg_tables))}")
for table in sorted(set(pg_tables) & set(sqlite_tables)):
    check(pg_tables[table] == sqlite_tables[table],
          f"{table} is column for column identical on both backends",
          f"Postgres {pg_tables[table]} vs SQLite {sqlite_tables[table]}. A "
          "column that exists on one backend and not the other fails for the "
          "first live student and never once locally")

# Every column a later deploy could add has to be in BOTH migration lists, or
# it reaches a new database and never an existing one. The columns that cannot
# be added to a populated table (NOT NULL with no default) are excluded, since
# no migration could deliver them anyway.
pg_migrations = " | ".join(getattr(store, "_PG_MIGRATIONS", ()))
sqlite_migrations = getattr(store, "_SQLITE_MIGRATIONS", {})


def addable(schema_sql: str, table: str) -> list[str]:
    body = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\n\);",
                     schema_sql, re.S)
    out = []
    for line in (body.group(1).splitlines() if body else []):
        line = line.strip().rstrip(",")
        if not line:
            continue
        first, rest = line.split()[0], line.upper()
        if first.upper() in {"UNIQUE", "PRIMARY", "FOREIGN", "CONSTRAINT",
                             "CHECK"}:
            continue
        if "PRIMARY KEY" in rest:
            continue
        if "NOT NULL" in rest and "DEFAULT" not in rest:
            continue
        out.append(first)
    return out


for table in sorted(sqlite_tables):
    for column in addable(getattr(store, "_SQLITE_SCHEMA", ""), table):
        check(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} "
              in pg_migrations,
              f"Postgres can add {table}.{column} to a database that already "
              f"exists")
        check(column in sqlite_migrations.get(table, {}),
              f"SQLite can add {table}.{column} to a database that already "
              f"exists")

# --------------------------------------------------------------------------
print("\n== an existing database is upgraded, not skipped over ==")

# The exact production shape: users and jobs already there, no practice tables,
# and one practice table half-built by an older release that is missing the
# columns added since. CREATE TABLE IF NOT EXISTS does nothing to that table,
# so only the migration list can rescue it.
legacy = _TMP / "legacy.db"
with sqlite3.connect(legacy) as conn:
    conn.execute("""CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, created_at INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE jobs (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, status TEXT NOT NULL,
        label TEXT, created_at INTEGER NOT NULL)""")
    conn.execute("INSERT INTO users (email,password_hash,created_at) "
                 "VALUES ('old@example.com','x',1)")
    conn.execute("""CREATE TABLE practice_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        template_id TEXT NOT NULL, subject TEXT NOT NULL,
        subtopic_id TEXT NOT NULL, calculator TEXT NOT NULL,
        difficulty TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
        working TEXT NOT NULL, params_json TEXT NOT NULL,
        check_json TEXT NOT NULL, variant_key TEXT NOT NULL,
        shuffle_key REAL NOT NULL, verified_by TEXT NOT NULL,
        syllabus_version TEXT NOT NULL, created_at INTEGER NOT NULL,
        UNIQUE (template_id, variant_key))""")
    conn.execute("""INSERT INTO practice_items
        (template_id,subject,subtopic_id,calculator,difficulty,question,answer,
         working,params_json,check_json,variant_key,shuffle_key,verified_by,
         syllabus_version,created_at)
        VALUES ('old-t','methods','methods.functions.linear','free','easy',
                'Old question','7','w','{}','{}','old-v',0.5,'sympy:x','old',1)""")

db.DB_PATH = legacy
db.init_db()
failure = _init()
check(not failure, "init_practice_db upgrades a database that already has "
                   "users, jobs and a half-built practice table", failure)
legacy_columns = columns_of(legacy, "practice_items")
for column in ("status", "marks", "verifier_notes"):
    check(column in legacy_columns,
          f"practice_items.{column} reached the existing table",
          "CREATE TABLE IF NOT EXISTS did nothing here, so only the migration "
          "list could have added it. Without it every draw fails on the live "
          "database and passes on every fresh one")
check(indexes_of(legacy) >= {"practice_items_draw_idx",
                             "practice_seen_recent_idx"},
      "and the indexes reached it too",
      f"{sorted(indexes_of(legacy))}")
with sqlite3.connect(legacy) as conn:
    kept = conn.execute(
        "SELECT question, status FROM practice_items WHERE variant_key='old-v'"
    ).fetchone()
check(kept == ("Old question", "live"),
      f"the row already banked is intact and defaulted, not rewritten ({kept})")

_init()
_init()          # a redeploy runs it twice more
with sqlite3.connect(legacy) as conn:
    still = conn.execute("SELECT COUNT(*) FROM practice_items").fetchone()[0]
check(still == 1,
      f"two more boots change nothing ({still} item(s) still banked)")

# --------------------------------------------------------------------------
print("\n== a duplicate question is refused by the database itself ==")

db.DB_PATH = _TMP / "unique.db"
db.init_db()
_init()
bank = fixtures.seed_bank(templates_per_subtopic=1, items_per_template=1)
first = store.get_item(bank.item_ids[0])
again = store.insert_item(
    template_id=first.template_id, subject=first.subject,
    subtopic_id=first.subtopic_id, calculator=first.calculator,
    difficulty=first.difficulty, question="A different wording entirely",
    answer=first.answer, working=first.working, params_json=first.params_json,
    check_json=first.check_json, variant_key=first.variant_key,
    shuffle_key=0.99, verified_by=first.verified_by)
check(again is None,
      "re-banking a variant that is already there stores nothing",
      "this is the layer that makes an exact duplicate question impossible "
      "rather than merely unlikely")
check(len(store.live_items([first.subtopic_id])) == 1,
      f"and the bank still holds one item, not two "
      f"({len(store.live_items([first.subtopic_id]))})")

raw_rejected = False
try:
    with sqlite3.connect(db.DB_PATH) as conn:
        conn.execute(
            """INSERT INTO practice_items
               (template_id,subject,subtopic_id,calculator,difficulty,question,
                answer,working,params_json,check_json,variant_key,shuffle_key,
                verified_by,syllabus_version,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (first.template_id, first.subject, first.subtopic_id,
             first.calculator, first.difficulty, "Straight past the code",
             first.answer, first.working, first.params_json, first.check_json,
             first.variant_key, 0.5, first.verified_by, "x", 1))
except sqlite3.IntegrityError:
    raw_rejected = True
check(raw_rejected,
      "and the constraint refuses it even from raw SQL that bypasses store.py",
      "declared but unenforced is the same as absent: the filler regenerates "
      "variants it has already made, every night, for ever")

# --------------------------------------------------------------------------
print("\n== the bank never reaches the money ==")

# Measured off the string literals that can reach a cursor, not off the raw
# source. Grepping the whole file also reads the module docstring, which names
# all three tables in the course of promising not to touch them, so the naive
# version failed against a module that was in fact perfectly clean. What
# matters is whether a money table can appear in a statement, and that means
# the literals.
tree = ast.parse(inspect.getsource(store))
docstrings = set()
for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                         ast.AsyncFunctionDef)):
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))
literals = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]

for table in ("credit_ledger", "payments", "jobs"):
    hits = [lit for lit in literals if table in lit]
    check(not hits,
          f"no statement in store.py names {table}",
          f"{hits[:1]} -- db.py holds the ledger, the payments and the refund "
          "path, and the practice engine has no business writing a statement "
          "against any of them")

# --------------------------------------------------------------------------
print("\n== deleting an account takes the practice history with it ==")

db.DB_PATH = _TMP / "deletion.db"
db.init_db()
_init()
student = fixtures.make_user("leaver@example.com")
other = fixtures.make_user("stayer@example.com")
bank = fixtures.seed_bank(templates_per_subtopic=2, items_per_template=4)
scope = bank.subtopic_ids[:1]
store.create_session(student, "Mathematics Methods", scope[0], "Linear")
store.create_session(other, "Mathematics Methods", scope[0], "Linear")
store.record_seen(student, [SeenEvent(item_id=i, outcome="got_it")
                            for i in bank.item_ids[:3]])
store.record_seen(other, [SeenEvent(item_id=bank.item_ids[0])])
check(store.seen_count(student, scope) == 3,
      "the leaving student has a history to delete")

db.delete_account(student)
check(store.seen_count(student, scope) == 0,
      f"their answer history is gone "
      f"({store.seen_count(student, scope)} row(s) left)",
      "the Privacy page and the deletion flash both promise everything goes, "
      "and what is stored here is every question they were shown and whether "
      "they got it right")
check(not store.recent_sessions(student),
      "their sessions are gone too")
check(store.seen_count(other, scope) == 1 and len(store.recent_sessions(other)) == 1,
      "the account that stayed is untouched")
check(len(store.live_items(scope)) == 8,
      f"and the bank itself is intact ({len(store.live_items(scope))} items), "
      "because questions belong to the product and not to one student")

# The guard that matters more than either: a process that has never run
# init_practice_db must still be able to delete an account.
db.DB_PATH = _TMP / "no-practice.db"
db.init_db()
stranded = db.create_user("stranded@example.com", "password123")
with sqlite3.connect(db.DB_PATH) as conn:
    for table in getattr(store, "TABLES", ()):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
try:
    db.delete_account(stranded)
    deleted, failure = db.get_user(stranded) is None, ""
except Exception as exc:                                          # noqa: BLE001
    deleted, failure = False, str(exc)
check(deleted,
      "an account still deletes on a database with no practice tables at all",
      f"{failure} -- a customer who cannot delete their account is a worse "
      "defect than the orphaned rows the delete was added to prevent")
with sqlite3.connect(db.DB_PATH) as conn:
    left = conn.execute("SELECT COUNT(*) FROM credit_ledger").fetchone()[0]
check(left == 0,
      f"and the rest of the deletion still committed ({left} ledger row(s) "
      f"left)",
      "swallowing the missing table without a savepoint would leave the whole "
      "transaction unable to commit, which is exactly what the guard is for")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
