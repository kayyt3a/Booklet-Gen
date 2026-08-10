"""Checks for the booklet feedback channel.

Feedback is the only path by which a defect a parent noticed reaches the
person who can fix it, so the guards that matter are the ones that keep it
honest and private: you may only rate a booklet you own and that actually
finished, a booklet holds one rating rather than a pile of votes, a child's
name never reaches the support log, and deleting an account takes the
feedback with it.

Runs on Flask's test client with a throwaway SQLite file, so it needs no
Gemini key and no database.

    PYTHONPATH=. python scripts/check_feedback.py
"""
import json
import os
import re
import shutil
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="folio-feedback-")
os.environ.pop("DATABASE_URL", None)          # exercise the SQLite path
os.environ["FOLIO_DB"] = os.path.join(_TMP, "folio.db")
os.environ["FOLIO_OUTPUT"] = os.path.join(_TMP, "output")
os.environ["FLASK_SECRET_KEY"] = "a-real-key-for-this-check-only-not-a-placeholder"
os.environ["FOLIO_ADMIN_EMAILS"] = "owner@folio.test"

from booklet_gen.webapp import create_app, admin, db, views  # noqa: E402

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def token_from(client, path):
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


def make_booklet(job_id, user_id, label, name="Student", *,
                 status="done", program="accelerate", year="Year 5",
                 subject="Mathematics"):
    """A finished booklet with the request that produced it, as generate() stores it."""
    db.create_job(job_id, user_id, label)
    args = dict(program=program, year=year, subject=subject, topic=None,
                name=name, is_term=False, is_exam=False)
    with db._cursor(transaction=True) as cur:
        cur.execute(db._q("UPDATE jobs SET request_json=? WHERE id=?"),
                    (json.dumps(args), job_id))
    if status == "done":
        db.finish_job(job_id, path=os.path.join(_TMP, f"{job_id}.pdf"))
    else:
        db.fail_job(job_id, "generation failed")


def rate(client, job_id, rating, comment="", question_ref=""):
    return client.post(f"/feedback/{job_id}", data={
        "rating": rating, "comment": comment, "question_ref": question_ref,
        "csrf_token": token_from(client, f"/feedback/{job_id}"),
    }, follow_redirects=True)


app = create_app()
app.config.update(TESTING=True)

owner = app.test_client()
signup(owner, "owner@folio.test")
owner_id = db.get_user_by_email("owner@folio.test")["id"]

parent = app.test_client()
signup(parent, "parent@folio.test")
parent_id = db.get_user_by_email("parent@folio.test")["id"]

stranger = app.test_client()
signup(stranger, "stranger@folio.test")
stranger_id = db.get_user_by_email("stranger@folio.test")["id"]

make_booklet("job-good", parent_id, "Academic Accelerate - Year 5 - Mathematics - Ella",
             name="Ella")
make_booklet("job-second", parent_id, "NAPLAN Practice - Year 3 - Sam", name="Sam",
             year="Year 3", program="naplan", subject=None)
make_booklet("job-failed", parent_id, "Failed booklet", name="Ella", status="error")
make_booklet("job-stranger", stranger_id, "Someone else's booklet", name="Bo")

print("\nACCESS")

resp = app.test_client().get("/feedback/job-good")
assert resp.status_code in (301, 302), "anonymous visitors can reach the rating form"
assert "/login" in resp.headers.get("Location", ""), resp.headers.get("Location")
ok("rating a booklet requires a login")

assert parent.get("/feedback/job-stranger").status_code == 404
assert rate(stranger, "job-good", 1).status_code == 404 or \
    db.get_feedback("job-good") is None
ok("another account's booklet cannot be opened or rated")

assert parent.get("/feedback/job-failed").status_code == 404
ok("a booklet that failed cannot be rated, there is nothing to judge")

assert parent.get("/feedback/no-such-job").status_code == 404
ok("an unknown job id is a 404, not a crash")

no_csrf = parent.post("/feedback/job-good", data={"rating": "5"})
assert no_csrf.status_code == 400, no_csrf.status_code
assert db.get_feedback("job-good") is None
ok("a rating POST without a CSRF token is rejected")

print("\nRATING RANGE")

for bad in ("0", "6", "-1", "abc", "", "3.5", "99999999999999999999"):
    rate(parent, "job-good", bad)
    assert db.get_feedback("job-good") is None, f"rating {bad!r} was accepted"
ok("0, 6, negative, fractional, huge and non-numeric ratings are all refused")

for good in (1, 2, 3, 4, 5):
    rate(parent, "job-good", good)
    row = db.get_feedback("job-good")
    assert row is not None and int(row["rating"]) == good, good
ok("every rating from 1 to 5 is accepted")

try:
    db.save_feedback("job-second", parent_id, 9)
except ValueError:
    ok("db.save_feedback refuses an out-of-range rating even when called directly")
else:
    raise AssertionError("db.save_feedback stored a rating of 9")

print("\nONE RATING PER BOOKLET")


def feedback_rows(job_id):
    with db._cursor() as cur:
        cur.execute(db._q("SELECT * FROM booklet_feedback WHERE job_id=?"), (job_id,))
        return [dict(r) for r in cur.fetchall()]


rate(parent, "job-good", 2, comment="first thoughts")
first = feedback_rows("job-good")
assert len(first) == 1, first

# Backdate the row before re-rating. Comparing the two wall-clock stamps
# instead would pass whether or not created_at is preserved, because both
# ratings land inside the same second.
with db._cursor(transaction=True) as cur:
    cur.execute(db._q("UPDATE booklet_feedback SET created_at=?,updated_at=? "
                      "WHERE job_id=?"), (1_000, 1_000, "job-good"))

rate(parent, "job-good", 5, comment="better after teaching from it")
again = feedback_rows("job-good")
assert len(again) == 1, "re-rating created a second row instead of updating"
assert int(again[0]["rating"]) == 5
assert again[0]["comment"] == "better after teaching from it"
ok("re-rating updates the same row rather than stacking a second vote")

assert int(again[0]["created_at"]) == 1_000, \
    "re-rating rewrote created_at, losing when the booklet was first judged"
assert int(again[0]["updated_at"]) > 1_000, "updated_at did not move"
ok("re-rating keeps the original date and moves only the updated stamp")

print("\nTHE CHILD'S NAME NEVER REACHES THE LOG")

rate(parent, "job-good", 2,
     comment="Question 4 was far too hard for Ella and ELLA gave up.",
     question_ref="Q4 (Ella)")
row = db.get_feedback("job-good")
assert "Ella" not in row["comment"] and "ELLA" not in row["comment"], row["comment"]
assert "[name]" in row["comment"], row["comment"]
assert "Ella" not in (row["question_ref"] or ""), row["question_ref"]
ok("the student's name is stripped out of the comment and the question field")

rate(parent, "job-good", 3, comment="Umbrella was spelled wrong on page 2.")
row = db.get_feedback("job-good")
assert "Umbrella" in row["comment"], \
    f"whole-word matching failed, 'Umbrella' was mangled: {row['comment']}"
ok("a name inside a longer word is left alone")

rate(parent, "job-second", 4, comment="Sam loved it, though Q2 was odd.")
row = db.get_feedback("job-second")
assert "Sam" not in row["comment"], row["comment"]
ok("each booklet is scrubbed against its own student, not a shared list")

long_comment = "x" * (db.COMMENT_MAX + 500)
rate(parent, "job-second", 4, comment=long_comment)
assert len(db.get_feedback("job-second")["comment"]) <= db.COMMENT_MAX
ok(f"a comment longer than {db.COMMENT_MAX} characters is truncated, not rejected")

# The route truncates too, so this has to call the store directly or it would
# pass with the storage cap deleted.
db.save_feedback("job-second", parent_id, 3, comment="y" * (db.COMMENT_MAX + 500))
assert len(db.get_feedback("job-second")["comment"]) <= db.COMMENT_MAX
ok("the cap holds at the storage layer, not only in the form handler")

print("\nWHAT THE CUSTOMER SEES")

rate(parent, "job-good", 4)
html = parent.get("/library").data.decode()
assert "★★★★☆" in html, "the library does not show the rating already given"
assert f"/feedback/job-second" in html, "unrated booklets offer no way to rate them"
ok("the library shows an existing rating and offers one where there is none")

form = parent.get("/feedback/job-good").data.decode()
assert 'value="4"' in form and "checked" in form
ok("re-opening the form preselects the rating already given")

assert "do not include your child's name" in form.lower()
ok("the form asks for no child's name before the stripper has to enforce it")

progress = parent.get("/progress/job-good").data.decode()
assert "/feedback/job-good" in progress
ok("the done state points at the rating form")

print("\nEXPORT AND DELETION")

export = json.loads(parent.get("/account/export").data.decode())
assert "feedback" in export, "account export omits the customer's own feedback"
assert any(f["job_id"] == "job-good" for f in export["feedback"]), export["feedback"]
ok("a customer can export the feedback they wrote")

print("\nSUPPORT CONSOLE")

rate(parent, "job-good", 2,
     comment="The answer key for Q7 is wrong. Ella checked it twice.",
     question_ref="Q7")

console = owner.get("/admin").data.decode()
assert "Customer feedback" in console
# Everything below the heading. The jobs table above it is existing behaviour
# and does show the booklet label, which carries the name the parent typed.
panel = console.split("Customer feedback", 1)[1]
assert "The answer key for Q7 is wrong" in panel
ok("the owner can read customer feedback in the support console")

assert "parent@folio.test" not in panel, \
    "the feedback table leaks the customer's email address"
assert "Ella" not in panel, "a child's name reached the support console"
assert "[name]" in panel, "the redacted marker is missing, so nothing was stored"
ok("the feedback panel shows no customer email and no child's name")

assert "Year 5" in panel and "Year 3" in panel
ok("feedback is described by year and subject, which is what triage needs")

assert parent.get("/admin").status_code == 404
ok("a customer cannot read the feedback of other customers")

summary = db.feedback_summary()
assert summary["total"] == len(db.list_recent_feedback()), summary
assert summary["average"] is not None and 1 <= summary["average"] <= 5
ok("the summary counts and averages what is actually stored")

print("\nDELETION TAKES THE FEEDBACK TOO")

remaining_before = len(db.list_recent_feedback())
assert remaining_before >= 2
delete_token = token_from(parent, "/account")
parent.post("/account/delete",
            data={"password": "password123", "csrf_token": delete_token},
            follow_redirects=True)
assert db.get_user(parent_id) is None, "the account was not deleted"
assert db.get_feedback("job-good") is None and db.get_feedback("job-second") is None, \
    "deleting the account left its feedback behind"
with db._cursor() as cur:
    cur.execute(db._q("SELECT COUNT(*) AS n FROM booklet_feedback WHERE user_id=?"),
                (parent_id,))
    assert int(dict(cur.fetchone())["n"]) == 0
ok("deleting an account removes every rating and comment it wrote")

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\nALL {_passed} FEEDBACK CHECKS PASSED")
