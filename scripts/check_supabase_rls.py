"""Checks no table this app owns can be read through Supabase's public API.

On 27 September 2026 Supabase emailed a CRITICAL security issue:

    Table publicly accessible. Anyone with your project URL can read, edit,
    and delete all data in this table because Row-Level Security is not
    enabled.    rls_disabled_in_public

Supabase serves every table in the `public` schema over a REST API, and that
API authenticates with the project's ANON key, which Supabase treats as
public: it is designed to be shipped inside browser code. With row level
security off, that key reads and writes every row. For this app that is every
customer's email and password hash, the credit ledger (a free booklet for
anyone who edits a number), every payment, and every stored PDF.

Nothing in this codebase had ever turned row level security on, and nothing
in it uses that API either: the app talks to Postgres directly as the role
that created its tables. So the fix is to enable RLS with NO policies at all.
The REST API's roles then see nothing, and a table's owner is not subject to
its own RLS, so the app sees exactly what it did before.

EXCEPT UNDER ONE WORD. `FORCE ROW LEVEL SECURITY` applies the policies to the
owner as well, there are no policies, and an owner with no policy granting it
anything reads zero rows. Every login fails and every booklet disappears, with
no error anywhere, because an empty result is not an error. This file measures
that directly rather than trusting the absence of the word.

WHY THIS NEEDS A REAL DATABASE. The property is enforced by Postgres, not by
Python, so nothing short of Postgres can check it. It builds the situation
Supabase ships:

  * an app role that owns its tables and is NOT a superuser. Superusers
    bypass RLS unconditionally, so testing as one passes whatever the code
    does, which is how a check like this lies
  * `anon` and `authenticated`, granted everything on new tables by default
    privilege, exactly as Supabase grants them

then runs the real init_db() and asks each role what it can see.

Needs a Postgres it may create roles in, as a superuser URL:

    FOLIO_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:55432/postgres \\
        PYTHONPATH=. python scripts/check_supabase_rls.py

NEVER point it at production. It creates and drops a database of its own.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSED = 0
TOTAL = 0

ADMIN_URL = os.environ.get("FOLIO_TEST_PG_ADMIN_URL")
TEST_DB = "folio_rls_check"
APP_ROLE = "folio_rls_app"


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


if not ADMIN_URL:
    print("FOLIO_TEST_PG_ADMIN_URL is not set, so this cannot run.\n"
          "It needs a throwaway Postgres it can create roles in. See the "
          "docstring. Never production.")
    raise SystemExit(2)

import psycopg                                                     # noqa: E402

if any(h in ADMIN_URL for h in ("supabase", "pooler", "render.com", "neon")):
    print("Refusing: that URL looks like a hosted database. This check creates "
          "and drops roles and a database of its own.")
    raise SystemExit(2)

admin = psycopg.connect(ADMIN_URL, autocommit=True)


def reset() -> None:
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    for role in (APP_ROLE, "anon", "authenticated", "folio_rls_other"):
        admin.execute(f"DROP ROLE IF EXISTS {role}")


reset()
# The Supabase shape. NOSUPERUSER on the app role is the whole test: a
# superuser bypasses RLS whatever the tables say.
admin.execute(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS")
admin.execute("CREATE ROLE anon NOLOGIN")
admin.execute("CREATE ROLE authenticated NOLOGIN")
admin.execute(f"GRANT anon, authenticated TO {APP_ROLE}")
admin.execute(f"CREATE DATABASE {TEST_DB} OWNER {APP_ROLE}")

base = ADMIN_URL.rsplit("/", 1)[0]
app_url = (base.replace("://postgres@", f"://{APP_ROLE}@")
           .replace("://postgres:", f"://{APP_ROLE}:") + f"/{TEST_DB}")
setup = psycopg.connect(base + f"/{TEST_DB}", autocommit=True)
setup.execute(f"GRANT ALL ON SCHEMA public TO {APP_ROLE}")
setup.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated")
# What Supabase does to every project: whatever the app creates in public is
# granted in full to the REST API's roles. RLS is then the ONLY thing between
# the anon key and the data.
setup.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {APP_ROLE} IN SCHEMA public "
              "GRANT ALL ON TABLES TO anon, authenticated")
setup.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {APP_ROLE} IN SCHEMA public "
              "GRANT ALL ON SEQUENCES TO anon, authenticated")

os.environ["DATABASE_URL"] = app_url
from booklet_gen.webapp import db                                  # noqa: E402

db.init_db()

app = psycopg.connect(app_url, autocommit=True)
tables = [r[0] for r in app.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
    "ORDER BY tablename").fetchall()]


print(f"\n== every table the app creates is behind RLS ({len(tables)}) ==")

check(len(tables) >= 10,
      f"init_db created {len(tables)} tables: {', '.join(tables)}",
      "the schema did not build, so nothing below is measuring the product")
exposed = [t for t, on in app.execute(
    "SELECT relname, relrowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r'").fetchall() if not on]
check(not exposed,
      "none of them is left with row level security off",
      f"{exposed} are readable through the REST API with the anon key. This "
      "is the table Supabase emailed about")

forced = [t for t, on in app.execute(
    "SELECT relname, relforcerowsecurity FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'r'").fetchall() if on]
check(not forced,
      "and none of them FORCES it on the owner",
      f"{forced} force RLS on their owner with no policy granting it anything. "
      "The app reads zero rows from them, every login fails, and no error "
      "is raised anywhere because an empty result is not an error")


print("\n== the app still sees everything it wrote ==")

user_id = db.create_user("parent@example.com", "correct horse battery")
if not isinstance(user_id, int):
    user_id = db.get_user_by_email("parent@example.com")["id"]
got = db.get_user_by_email("parent@example.com")
check(got is not None and got["id"] == user_id,
      "a new account is written and read back through the app's own code",
      "the app cannot read its own users table, which on the live site is "
      "every customer failing to log in")
check(bool(got and got.get("password_hash")),
      "including its password hash, which login needs",
      "the row came back without the column login checks against")


print("\n== and the public API sees nothing ==")

for role in ("anon", "authenticated"):
    app.execute(f"SET ROLE {role}")
    try:
        rows = app.execute("SELECT email, password_hash FROM users").fetchall()
        check(rows == [],
              f"as {role}: SELECT on users returns {len(rows)} row(s)",
              f"{role} read {len(rows)} account(s) including password hashes. "
              "That is what anyone holding the project's anon key could do")
        changed = app.execute(
            "UPDATE credit_ledger SET delta = 999").rowcount
        check(changed == 0,
              f"as {role}: UPDATE on the credit ledger touches {changed} row(s)",
              f"{role} rewrote {changed} credit entries: free booklets for "
              "anyone who edits a number")
        gone = app.execute("DELETE FROM users").rowcount
        check(gone == 0,
              f"as {role}: DELETE on users removes {gone} row(s)",
              f"{role} deleted {gone} account(s)")
    finally:
        app.execute("RESET ROLE")

check(db.get_user_by_email("parent@example.com") is not None,
      "and the account is still there after all of that",
      "the REST API's roles destroyed data the app owns")


print("\n== why FORCE is refused, measured rather than asserted ==")

# The same app role, the same no-policy setup, one word different. If this
# ever stops showing the owner losing its rows, the reason given above for
# refusing FORCE is wrong and should be rewritten, not trusted.
app.execute("CREATE TABLE force_demo (id int)")
app.execute("INSERT INTO force_demo VALUES (1), (2), (3)")
app.execute("ALTER TABLE force_demo ENABLE ROW LEVEL SECURITY")
plain = app.execute("SELECT count(*) FROM force_demo").fetchone()[0]
app.execute("ALTER TABLE force_demo FORCE ROW LEVEL SECURITY")
forced_count = app.execute("SELECT count(*) FROM force_demo").fetchone()[0]
app.execute("DROP TABLE force_demo")
check(plain == 3 and forced_count == 0,
      f"owner sees {plain} rows with RLS on, {forced_count} once it is FORCED",
      f"read {plain} then {forced_count}. The claim that FORCE locks the app "
      "out of its own tables no longer holds on this Postgres; recheck it")


print("\n== it does not take the site down on anything it does not own ==")

# A table someone made by hand in the Supabase dashboard is owned by another
# role, and ALTER TABLE on it fails. Raising there would stop the app booting,
# which turns a hardening step into an outage.
admin.execute("CREATE ROLE folio_rls_other NOLOGIN")
setup.execute("GRANT ALL ON SCHEMA public TO folio_rls_other")
setup.execute("SET ROLE folio_rls_other")
setup.execute("CREATE TABLE made_in_the_dashboard (id int)")
setup.execute("RESET ROLE")
try:
    db.init_db()
    booted = True
except Exception as e:                                             # pragma: no cover
    booted = False
    print(f"                {e}")
check(booted,
      "init_db still boots with a table in public it does not own",
      "the app refuses to start because of a table it never created")


print("\n== and it holds on every boot, not just the first ==")

try:
    db.init_db()
    again = True
except Exception:                                                  # pragma: no cover
    again = False
check(again, "a second boot over an already protected schema is a no-op",
      "enabling RLS is not idempotent here, so the second deploy crashes")


app.close()
setup.close()
from booklet_gen import dbpool                                     # noqa: E402
dbpool.close_pool()
reset()
admin.close()
print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
