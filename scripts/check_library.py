"""Check the booklet history + durable file storage on whichever backend is set."""
import io, os, sys, zipfile

from booklet_gen.dbpool import is_postgres, get_pool
from booklet_gen.webapp import create_app
from booklet_gen.webapp import db

print("backend:", "postgres" if is_postgres() else "sqlite")

if is_postgres():
    with get_pool().connection() as c:
        c.execute("DROP TABLE IF EXISTS job_files, jobs, users CASCADE")

app = create_app()
c = app.test_client()

# signup
assert c.post("/signup", data={"email": "lib@test.com", "password": "password123"},
              follow_redirects=True).status_code == 200
uid = db.get_user_by_email("lib@test.com")["id"]

# empty state
r = c.get("/library")
assert r.status_code == 200, r.status_code
assert b"not generated anything yet" in r.data, "empty state missing"
print("empty library renders")

# a finished job with an archived file
db.create_job("j1", uid, "Academic Accelerate - Year 5 - Mathematics")
pdf = b"%PDF-1.4 fake booklet bytes"
db.save_job_file("j1", uid, "accelerate-year-5.pdf", "application/pdf", pdf)
db.finish_job("j1", path="/nonexistent/gone.pdf")   # simulate the disk copy being wiped

# a failed job, and one still running
db.create_job("j2", uid, "NAPLAN Practice - Year 3")
db.fail_job("j2", "quota exceeded")
db.create_job("j3", uid, "Methods Exam - Year 12")

r = c.get("/library")
html = r.data.decode()
assert "Academic Accelerate - Year 5" in html, "done job missing"
assert "NAPLAN Practice - Year 3" in html, "failed job missing"
assert "Methods Exam - Year 12" in html, "running job missing"
assert "Failed" in html and "In progress" in html and "Download" in html
print("library lists done / failed / running correctly")

# download must come from the DB even though the disk path is gone
r = c.get("/download/j1")
assert r.status_code == 200, f"download failed: {r.status_code}"
assert r.data == pdf, "wrong bytes returned"
assert "accelerate-year-5.pdf" in r.headers.get("Content-Disposition", "")
print("download served from database after the on-disk copy vanished")

# another user must not reach it
c2 = app.test_client()
c2.post("/signup", data={"email": "other@test.com", "password": "password123"},
        follow_redirects=True)
assert c2.get("/download/j1").status_code == 404, "LEAK: other user downloaded the file"
assert "Academic Accelerate" not in c2.get("/library").data.decode(), "LEAK: history visible"
print("other accounts cannot see or download it")

# retention trims old blobs but keeps history rows
db.FILE_RETENTION_PER_USER = 3
for n in range(6):
    jid = f"r{n}"
    db.create_job(jid, uid, f"Retention test {n}")
    db.save_job_file(jid, uid, f"r{n}.pdf", "application/pdf", b"x" * 100)
    db.finish_job(jid, path=f"/tmp/{jid}.pdf")
kept = [j for j in db.list_jobs(uid) if j["label"].startswith("Retention") and j["filename"]]
rows = [j for j in db.list_jobs(uid) if j["label"].startswith("Retention")]
print(f"retention: {len(rows)} history rows, {len(kept)} still downloadable")
assert len(rows) == 6, "history rows were deleted"
assert len(kept) <= 3, f"retention did not trim ({len(kept)} kept)"
print("retention trims files but preserves history")

print("\nALL LIBRARY CHECKS PASSED")
