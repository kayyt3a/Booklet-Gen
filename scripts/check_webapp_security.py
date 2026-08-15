"""Security checks for the FolioAI web app: sessions, CSRF, redirects, quotas,
stuck jobs, account deletion.

Every check proves the negative case as well as the positive one: a forged
cookie is rejected, a cross-site POST is rejected, an off-site `next` is
refused. Runs entirely on Flask's test client with a throwaway SQLite file, so
it needs no Gemini key and no database.

    PYTHONPATH=. python scripts/check_webapp_security.py
"""
import os
import re
import shutil
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="folio-seccheck-")
os.environ.pop("DATABASE_URL", None)          # exercise the SQLite path
os.environ["FOLIO_DB"] = os.path.join(_TMP, "folio.db")
os.environ["FOLIO_OUTPUT"] = os.path.join(_TMP, "output")
os.environ["FLASK_SECRET_KEY"] = "a-real-key-for-this-check-only-not-a-placeholder"

from flask.sessions import SecureCookieSessionInterface  # noqa: E402

from booklet_gen.webapp import create_app, _resolve_secret_key  # noqa: E402
from booklet_gen.webapp import db, views  # noqa: E402

PUBLISHED_DEFAULT = "dev-insecure-change-me"
_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def token_from(client, path):
    """Pull the CSRF token out of a rendered form."""
    html = client.get(path).data.decode()
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, f"no CSRF token rendered on {path}"
    return m.group(1)


def signup(client, email, password="password123"):
    return client.post("/signup",
                       data={"email": email, "password": password,
                             "csrf_token": token_from(client, "/signup")},
                       environ_base={"REMOTE_ADDR": email},
                       follow_redirects=True)


def forge_cookie(secret, payload):
    class Fake:
        secret_key = secret
        config = {"SECRET_KEY_FALLBACKS": None}
    return SecureCookieSessionInterface().get_signing_serializer(Fake()).dumps(payload)


def backdate_job(job_id, seconds):
    with db._cursor() as cur:
        cur.execute(db._q("UPDATE jobs SET created_at=? WHERE id=?"),
                    (int(time.time()) - seconds, job_id))


# ---------------------------------------------------------------- 1. secret key
print("\n1. session signing key")

saved_key = os.environ["FLASK_SECRET_KEY"]
try:
    os.environ["DATABASE_URL"] = "postgresql://user:pw@example.invalid/folio"

    for bad, describe in ((None, "unset"),
                          (PUBLISHED_DEFAULT, "the published default"),
                          ("change-me-to-a-long-random-string", "the .env example value"),
                          ("   ", "blank")):
        if bad is None:
            os.environ.pop("FLASK_SECRET_KEY", None)
        else:
            os.environ["FLASK_SECRET_KEY"] = bad
        try:
            _resolve_secret_key()
        except RuntimeError as e:
            assert "Refusing to start" in str(e)
            ok(f"a real deployment refuses to boot when the key is {describe}")
        else:
            sys.exit(f"FAIL: booted with a {describe} key while DATABASE_URL is set")

    os.environ["FLASK_SECRET_KEY"] = saved_key
    assert _resolve_secret_key() == saved_key
    ok("a real key is accepted unchanged")
finally:
    os.environ.pop("DATABASE_URL", None)
    os.environ["FLASK_SECRET_KEY"] = saved_key

# Local checkout with no key: boots, but on a random per-process key.
os.environ.pop("FLASK_SECRET_KEY", None)
k1, k2 = _resolve_secret_key(), _resolve_secret_key()
assert k1 != k2 and k1 != PUBLISHED_DEFAULT and len(k1) >= 32
ok("a local checkout gets a random key instead of the published one")
os.environ["FLASK_SECRET_KEY"] = saved_key

app = create_app()
app.config["OUTPUT_DIR"].mkdir(parents=True, exist_ok=True)
assert app.config["SECRET_KEY"] == saved_key

# The victim: a real account with a real booklet.
victim = app.test_client()
signup(victim, "victim@test.com")
vid = db.get_user_by_email("victim@test.com")["id"]
db.create_job("victim-job", vid, "Academic Accelerate - Year 5 - Mathematics")
db.save_job_file("victim-job", vid, "v.pdf", "application/pdf", b"%PDF student name")
db.finish_job("victim-job", path="/nonexistent")

attacker = app.test_client()
cookie_name = app.config["SESSION_COOKIE_NAME"]
attacker.set_cookie(cookie_name, forge_cookie(PUBLISHED_DEFAULT, {"user_id": vid}),
                    domain="localhost")
r = attacker.get("/library")
assert r.status_code == 302 and "/login" in r.headers["Location"], r.status_code
assert b"Academic Accelerate" not in r.data
assert attacker.get("/download/victim-job").status_code == 302
ok("a cookie forged with the published default key is rejected")

# Sanity: the same forgery against the real key would work, i.e. the check
# above is testing signature verification and not something incidental.
attacker2 = app.test_client()
attacker2.set_cookie(cookie_name, forge_cookie(saved_key, {"user_id": vid}),
                     domain="localhost")
assert b"Academic Accelerate" in attacker2.get("/library").data
ok("(control) a correctly signed cookie does reach the library")

assert app.config["SESSION_COOKIE_HTTPONLY"] is True
assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
ok("session cookie is HttpOnly and SameSite=Lax")

# --------------------------------------------------------------- 2. open redirect
print("\n2. login redirect target")

signup(app.test_client(), "redir@test.com")

OFFSITE = ["https://evil.example/steal", "//evil.example/steal",
           "/\\evil.example/steal", "http:/evil.example",
           "\\\\evil.example/steal", "javascript:alert(1)",
           "https:evil.example", "  https://evil.example"]


def login_next(target):
    c = app.test_client()
    tok = token_from(c, "/login")
    r = c.post(f"/login?next={target}",
               data={"email": "redir@test.com", "password": "password123",
                     "csrf_token": tok})
    assert r.status_code == 302, r.status_code
    return r


for target in OFFSITE:
    dest = login_next(target).headers.get("Location", "")
    assert "evil.example" not in dest and dest == "/", \
        f"open redirect via {target!r} -> {dest}"
ok(f"{len(OFFSITE)} off-site next values all land on / instead")

# A next carrying CRLF must not be able to add a response header.
r = login_next("/library%0d%0aSet-Cookie:+stolen=1")
assert "stolen" not in r.headers.get("Set-Cookie", "")
assert all("stolen" not in v for k, v in r.headers if k.lower() != "location")
assert "\r" not in r.headers["Location"] and "\n" not in r.headers["Location"]
ok("a next carrying CRLF cannot inject a response header")

c = app.test_client()
tok = token_from(c, "/login")
r = c.post("/login?next=/library",
           data={"email": "redir@test.com", "password": "password123",
                 "csrf_token": tok})
assert r.headers["Location"] == "/library", r.headers.get("Location")
ok("a same-site relative next is still honoured")

# ----------------------------------------------------------------------- 3. CSRF
print("\n3. CSRF on state-changing routes")

user = app.test_client()
signup(user, "csrf@test.com")
uid = db.get_user_by_email("csrf@test.com")["id"]

views._run_job = lambda job_id, a: None   # do not call Gemini in a check script

FORM = {"program": "accelerate", "year": "Year 5", "subject": "Mathematics",
        "student_name": "Sam"}

r = user.post("/generate", data=dict(FORM))
assert r.status_code == 400, f"no-token POST /generate returned {r.status_code}"
assert b"did not come from FolioAI" in r.data, "no explanation shown to the user"
ok("POST /generate with no token is rejected (400) and explained")

r = user.post("/generate", data=dict(FORM, csrf_token="not-the-right-token"),
              headers={"Origin": "https://evil.example",
                       "Referer": "https://evil.example/attack.html"})
assert r.status_code == 400, r.status_code
ok("a cross-site POST carrying a guessed token is rejected")

# A token minted for a different session must not work either.
other = app.test_client()
signup(other, "csrf2@test.com")
stolen = token_from(other, "/")
r = user.post("/generate", data=dict(FORM, csrf_token=stolen))
assert r.status_code == 400, r.status_code
ok("another session's token is rejected")

before = db.booklets_started_last_24h(uid)
r = user.post("/generate", data=dict(FORM, csrf_token=token_from(user, "/")))
assert r.status_code == 302 and "/progress/" in r.headers["Location"], r.status_code
assert db.booklets_started_last_24h(uid) == before + 1
progress = user.get(r.headers["Location"])
assert progress.status_code == 200 and b"Paulio is preparing your booklet" in progress.data
ok("the real form, with its token, still works end to end")

r = app.test_client().post("/login", data={"email": "csrf@test.com",
                                           "password": "password123"})
assert r.status_code == 400, r.status_code
ok("POST /login with no token is rejected, so the guard is global")

# ------------------------------------------------------------------- 4. quotas
print("\n4. abuse guard counts booklets, not requests")

quota = app.test_client()
signup(quota, "quota@test.com")
qid = db.get_user_by_email("quota@test.com")["id"]

r = quota.post("/generate", data=dict(FORM, term_plan="on",
                                      csrf_token=token_from(quota, "/")))
assert r.status_code == 302 and "/progress/" in r.headers["Location"]
counted = db.booklets_started_last_24h(qid)
assert counted == views.TERM_WEEKS, f"term plan counted as {counted}, not {views.TERM_WEEKS}"
ok(f"one term plan counts as {views.TERM_WEEKS} booklets, not 1")

# There is no per-account daily cap. A customer with credits may generate as
# much as they have paid for, on the same day, which is the whole point of
# having bought it. The guard that remains is the instance ceiling, tested
# below, and the credit reservation, tested in check_commerce_and_jobs.py.
assert not hasattr(views, "DAILY_BOOKLET_LIMIT"), (
    "a per-account daily cap is back. Credits are the entitlement: a cap "
    "rations work the customer has already bought, and makes a tutoring firm "
    "on one account impossible")
r = quota.post("/generate", data=dict(FORM, term_plan="on",
                                      csrf_token=token_from(quota, "/")),
               follow_redirects=True)
assert "try again tomorrow" not in r.data.decode().lower(), \
    "a second term plan on the same day was refused"
assert db.booklets_started_last_24h(qid) == views.TERM_WEEKS * 2
ok("a second term plan the same day is allowed, because credits are the limit")

saved_global = views.GLOBAL_DAILY_BOOKLET_LIMIT
views.GLOBAL_DAILY_BOOKLET_LIMIT = 1
try:
    fresh = app.test_client()
    signup(fresh, "newaccount@test.com")
    r = fresh.post("/generate", data=dict(FORM, csrf_token=token_from(fresh, "/")),
                   follow_redirects=True)
    assert "overall generation limit" in r.data.decode(), \
        "global ceiling did not stop a brand new account"
    ok("a brand new account cannot get past the global daily ceiling")
finally:
    views.GLOBAL_DAILY_BOOKLET_LIMIT = saved_global

# --------------------------------------------------------------- 5. stuck jobs
print("\n5. jobs that die silently")

stuck = app.test_client()
signup(stuck, "stuck@test.com")
sid = db.get_user_by_email("stuck@test.com")["id"]

db.create_job("stuck-1", sid, "NAPLAN Practice - Year 3")
assert stuck.get("/status/stuck-1").get_json()["status"] == "running"
backdate_job("stuck-1", views.JOB_TIMEOUT_SECONDS + 60)
payload = stuck.get("/status/stuck-1").get_json()
assert payload["status"] == "error", payload
assert "restarted" in payload["error"]
ok("a job past the timeout reports an error instead of spinning for ever")

db.create_job("stuck-2", sid, "Scholarship - Year 6")
backdate_job("stuck-2", views.JOB_TIMEOUT_SECONDS + 60)
db.create_job("stuck-3", sid, "Scholarship - Year 7")   # young, must survive
assert db.fail_stale_running_jobs(views.JOB_TIMEOUT_SECONDS) == 1
assert db.get_job("stuck-2")["status"] == "error"
assert db.get_job("stuck-3")["status"] == "running"
ok("the boot sweep fails only jobs older than the timeout")

assert db.fail_job_if_running("stuck-2", "second attempt") is False
ok("the watchdog will not re-fail a job that already settled")

# This used to assert the opposite: that a late finisher still ended up done,
# so a customer who waited was not punished for a slow job. That was a
# deliberate kindness, but it was written before credits existed and it never
# settled them. The watchdog refunds when it fails a job, so delivering the
# booklet afterwards handed over the product and the money back. On a ten-week
# term plan that is A$39, self-serve and repeatable.
#
# The credit is already returned, so the customer has lost time and nothing
# else, and the error text tells them to try again.
db.create_job("stuck-4", sid, "Late finisher")
db.fail_job_if_running("stuck-4", "timed out")
assert db.finish_job("stuck-4", path="/tmp/x.pdf") is False
assert db.get_job("stuck-4")["status"] == "error"
ok("a job that finishes after the watchdog refunded it is not resurrected")

zombie = app.test_client()
signup(zombie, "zombie@test.com")
zid = db.get_user_by_email("zombie@test.com")["id"]
db.create_job("stuck-5", zid, "Zombie on the library page")
backdate_job("stuck-5", views.JOB_TIMEOUT_SECONDS + 60)
html = zombie.get("/library").data.decode()
assert "In progress" not in html, "the library still offers a spinner for a dead job"
assert "Failed" in html
assert db.get_job("stuck-5")["status"] == "error"
ok("the library shows a dead job as failed, not as a spinner")

# ------------------------------------------------------- 6. export and deletion
print("\n6. account export and deletion")

owner = app.test_client()
signup(owner, "delete-me@test.com")
oid = db.get_user_by_email("delete-me@test.com")["id"]
pdf_path = app.config["OUTPUT_DIR"] / "delete-me.pdf"
pdf_path.write_bytes(b"%PDF student work")
db.create_job("del-1", oid, "Academic Accelerate - Year 4 - English")
db.save_job_file("del-1", oid, "d.pdf", "application/pdf", b"%PDF student work")
db.finish_job("del-1", path=str(pdf_path))

r = owner.get("/account/export")
assert r.status_code == 200 and r.mimetype == "application/json"
data = r.get_json()
assert data["account"]["email"] == "delete-me@test.com"
assert [b["id"] for b in data["booklets"]] == ["del-1"]
ok("export returns this account's details and booklet list")

assert app.test_client().get("/account/export").status_code == 302
ok("export requires a login")

r = owner.post("/account/delete",
               data={"password": "wrong-password",
                     "csrf_token": token_from(owner, "/account")},
               follow_redirects=True)
assert db.get_user(oid) is not None, "DELETED WITH THE WRONG PASSWORD"
assert "not correct" in r.data.decode()
ok("the wrong password deletes nothing")

r = owner.post("/account/delete", data={"password": "password123"})
assert r.status_code == 400, "delete accepted without a CSRF token"
assert db.get_user(oid) is not None
ok("deletion without a CSRF token is refused")

r = owner.post("/account/delete",
               data={"password": "password123",
                     "csrf_token": token_from(owner, "/account")},
               follow_redirects=True)
assert r.status_code == 200
assert db.get_user(oid) is None, "account row survived deletion"
assert db.get_job("del-1") is None, "job row survived deletion"
assert db.get_job_file("del-1") is None, "stored booklet survived deletion"
assert not pdf_path.exists(), "the file on disk survived deletion"
assert owner.get("/library").status_code == 302, "session survived deletion"
ok("deletion removes the account, its history, its files and its session")

assert db.get_user(vid) is not None and db.get_job("victim-job") is not None
ok("another account's data is untouched by that deletion")

# A job row pointing somewhere else on the filesystem must not be followed.
from pathlib import Path  # noqa: E402

elsewhere = Path(_TMP) / "not-ours.pdf"
elsewhere.write_bytes(b"someone else's file")
with app.test_request_context():
    views._remove_leftovers([(str(elsewhere), None),
                             (None, str(app.config["OUTPUT_DIR"])),
                             ("/etc/hostname", "/etc")])
assert elsewhere.exists(), "deletion escaped the output directory"
assert app.config["OUTPUT_DIR"].is_dir(), "deletion removed the whole output dir"
if os.name != "nt":
    assert Path("/etc/hostname").exists()
ok("deletion only removes files inside the output directory")

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\nALL {_passed} WEBAPP SECURITY CHECKS PASSED")
