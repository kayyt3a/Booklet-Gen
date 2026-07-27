"""End-to-end check of the Postgres + pgvector backends against a real server."""
import os, random

os.environ["FOLIO_EMBED_DIM"] = "8"   # tiny vectors so the test is readable

from booklet_gen.dbpool import is_postgres, get_pool
from booklet_gen.webapp import db
from booklet_gen.rag.store import VectorStore

assert is_postgres(), "DATABASE_URL must be set for this test"
print("backend: postgres =", is_postgres())

# Clean slate.
with get_pool().connection() as c:
    c.execute("DROP TABLE IF EXISTS rag_chunks, jobs, users CASCADE")

# ---------- accounts ----------
db.init_db()
uid = db.create_user("Parent@Example.com ", "hunter2pass")
print("created user id:", uid)
assert db.get_user_by_email("parent@example.com") is not None, "lookup failed"
assert db.verify_login("parent@example.com", "hunter2pass"), "login failed"
assert not db.verify_login("parent@example.com", "wrong"), "bad password accepted"
u = db.get_user(uid)
assert u["email"] == "parent@example.com"
assert "hunter2pass" not in u["password_hash"], "password stored in plaintext"
print("accounts: signup, lookup, correct + incorrect login all OK")

# ---------- jobs + abuse guard ----------
assert db.jobs_started_last_24h(uid) == 0
for i in range(3):
    db.create_job(f"job{i}", uid, f"Test booklet {i}")
assert db.jobs_started_last_24h(uid) == 3, db.jobs_started_last_24h(uid)
db.finish_job("job0", path="/tmp/x.pdf")
assert db.get_job("job0")["status"] == "done"
db.fail_job("job1", "boom")
j1 = db.get_job("job1")
assert j1["status"] == "error" and j1["error"] == "boom"
print("jobs: create, finish, fail, and 24h count all OK")

# ---------- vectors ----------
store = VectorStore()


def vec(seed):
    r = random.Random(seed)
    return [r.random() for _ in range(8)]


target = vec(1)
store.add_chunks(
    ["fractions of a whole", "photosynthesis basics", "scope and sequence"],
    [target, vec(2), vec(3)],
    [
        {"source_id": "s1", "source": "maths.pdf", "subject": "Mathematics",
         "year_level": "Year 5", "topics": "NAPLAN", "ordinal": 0},
        {"source_id": "s1", "source": "sci.pdf", "subject": "Science",
         "year_level": "Year 5", "topics": "SCSA", "ordinal": 1},
        {"source_id": "s1", "source": "syll.pdf", "subject": "Mathematics",
         "year_level": "Any", "topics": "SCSA", "ordinal": 2},
    ],
    source_id="s1",
)
print("chunk count:", store.count())
assert store.count() == 3

# Exact-match query should rank the identical vector first.
hits = store.query(target, top_k=3, subject="Mathematics", year_levels=["Year 5", "Any"])
print("filtered hits:", [(h["text"][:22], round(h["distance"], 4)) for h in hits])
assert hits[0]["text"] == "fractions of a whole", "nearest neighbour wrong"
assert hits[0]["distance"] < 1e-6, "identical vector should have ~0 distance"
# The Science row must be excluded by the subject filter.
assert all(h["metadata"]["subject"] == "Mathematics" for h in hits), "subject filter leaked"
# The "Any" wildcard row must be included.
assert any(h["metadata"]["year_level"] == "Any" for h in hits), "wildcard year missing"
print("vectors: similarity order, subject filter, and 'Any' wildcard all OK")

# Re-ingest must overwrite, not duplicate.
store.add_chunks(["fractions of a whole v2"], [target],
                 [{"source_id": "s1", "source": "maths.pdf", "subject": "Mathematics",
                   "year_level": "Year 5", "topics": "NAPLAN", "ordinal": 0}],
                 source_id="s1")
print("count after re-ingest of 1 chunk:", store.count())
assert store.count() == 3, "re-ingest duplicated rows"

store.delete_by_source("s1")
assert store.count() == 0, "delete_by_source failed"
print("vectors: idempotent re-ingest and delete_by_source OK")

print("\nALL POSTGRES + PGVECTOR CHECKS PASSED")
