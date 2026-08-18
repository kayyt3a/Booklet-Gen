"""Checks that a study plan week carries over from the week before it.

A standalone booklet used to be an island: `jobs.py` called run_program with
nothing but program, year, name and subject, so generating one in July and
another in August produced two week-1 booklets that had never heard of each
other. Spelling and times tables could only live inside a ten-booklet term
plan, because each depends on the booklet before it.

A study plan is the memory that was missing. It also must NOT auto-advance: a
tutor generating week 1 for a fifth student has to get week 1 again.

    PYTHONPATH=. python scripts/check_study_plans.py
"""
import os
import tempfile

os.environ["FOLIO_DB"] = os.path.join(tempfile.mkdtemp(), "plans.db")
os.environ["FOLIO_FILE_RETENTION"] = "4"
os.environ["FOLIO_PLAN_WEEK_RETENTION"] = "3"

from booklet_gen import jobs                       # noqa: E402
from booklet_gen.webapp import db                  # noqa: E402

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


LADDER = [{"week": w, "focus": f"skill {w}", "difficulty": "easy",
           "revision": w >= 9} for w in range(1, 11)]


class FakePipeline:
    """Records what the job runner asked for, so the wiring can be checked
    without a Gemini key. Generation itself is exercised by the other
    booklet checks; what matters here is what gets carried in and recorded."""

    def __init__(self):
        self.calls = []
        self.standalone = []

    def run_plan_week(self, program, year, name, **kw):
        self.calls.append({"program": program, "year": year, "name": name, **kw})
        from booklet_gen.schemas import (BookletData, SpellingList,
                                         SubtopicOutput, TablesList)
        week = kw["week"]
        return BookletData(
            subject=kw.get("subject") or "Mathematics", year_level=year,
            student_name=name,
            sections=[SubtopicOutput(topic=f"T{week}",
                                     subtopic=f"taught in week {week}",
                                     questions=[])],
            spelling_list=SpellingList(words=[f"w{week}a", f"w{week}b"]),
            tables_list=TablesList(table=week + 1),
        )

    def run_program(self, program, year, name, **kw):
        self.standalone.append({"program": program, "year": year, "name": name, **kw})
        from booklet_gen.schemas import BookletData
        return BookletData(subject="Mathematics", year_level=year,
                           student_name=name, sections=[])


db.init_db()
user = db.create_user("plans@example.com", "password123")
other = db.create_user("other@example.com", "password123")

print("\nA PLAN IS A ROW THE CUSTOMER PICKS, NOT A GUESS FROM THE NAME")

sam = db.create_plan(user, "Sam", "accelerate", "Mathematics", "Year 5", 10, LADDER)
ava = db.create_plan(user, "Ava", "accelerate", "Mathematics", "Year 5", 10, LADDER)
assert sam != ava, "two students collapsed into one plan"
ok("two students with the same program, year and subject get separate plans")

assert db.get_plan(sam, other) is None, (
    "another account's plan was readable, so a forged plan_id in a POST body "
    "would reach someone else's student")
assert db.get_plan(sam, user) is not None
ok("a plan is scoped to the account that owns it")

print("\nTHE WEEK IS THE ONE ASKED FOR, NEVER THE NEXT ONE")

pipeline = FakePipeline()
for plan_id in (sam, ava):
    jobs._run_plan_week(pipeline, {"plan_week": 1}, plan_id, f"job-{plan_id}-w1")
assert [c["week"] for c in pipeline.calls] == [1, 1], (
    "generating week 1 for a second student advanced the week: a tutor "
    f"running five students would get weeks 1..5, got {pipeline.calls}")
ok("week 1 for a second student is still week 1")

print("\nWEEK N BUILDS ON WHAT WEEK N-1 ACTUALLY TAUGHT")

pipeline = FakePipeline()
jobs._run_plan_week(pipeline, {"plan_week": 1}, sam, "sam-w1")
first = pipeline.calls[-1]
assert first["prev_focus"] is None, f"week 1 recapped something: {first}"
assert first["prev_spelling_words"] is None, "week 1 tested words never set"
ok("week 1 has nothing before it, so it recaps and tests nothing")

jobs._run_plan_week(pipeline, {"plan_week": 2}, sam, "sam-w2")
second = pipeline.calls[-1]
assert second["week"] == 2, second
# Recorded from the booklet, not from the ladder: the outline parser picks the
# real subtopics and the hour cap can drop some of them.
assert second["prev_focus"] == "taught in week 1", (
    "week 2 did not recap what week 1 actually taught, so the carry-over this "
    f"whole feature exists for is missing: {second}")
assert second["prev_spelling_words"] == ["w1a", "w1b"], (
    f"week 2's spelling test is not on week 1's list: {second}")
assert second["prev_spelling_week"] == 1, second
assert second["prev_table"] == 2, f"week 2 does not test week 1's table: {second}"
ok("week 2 recaps week 1's real subtopics and tests its list and table")

assert sorted(second["words_already_set"]) == ["w1a", "w1b"], second
assert second["tables_already_set"] == [2], second
ok("what has already been set is carried, so no week repeats another")

print("\nA GAP IN THE PLAN CARRIES NOTHING RATHER THAN REACHING BACK")

# Week 4 with week 3 never generated. Recapping week 1 here would ask about a
# booklet printed a month ago and skipped since.
jobs._run_plan_week(pipeline, {"plan_week": 4}, sam, "sam-w4")
fourth = pipeline.calls[-1]
assert fourth["week"] == 4, fourth
assert fourth["prev_focus"] is None, (
    "week 4 recapped an earlier week even though week 3 was never generated: "
    f"{fourth}")
assert sorted(fourth["words_already_set"]) == ["w1a", "w1b", "w2a", "w2b"], (
    "the whole plan's words should still be excluded from a new list")
ok("a skipped week means no recap, but nothing already set is re-set")

print("\nA STANDALONE BOOKLET IS STILL AN ISLAND, ON PURPOSE")

pipeline = FakePipeline()
jobs._run_plan_week(pipeline, {"program": "accelerate", "year": "Year 5",
                               "name": "Nobody", "plan_week": 3}, 999_999, "gone")
assert pipeline.standalone and not pipeline.calls, (
    "a job naming a deleted plan should still produce the booklet the "
    "customer paid for, without carry-over")
ok("a job whose plan was deleted still generates, carrying nothing")

print("\nEACH PLAN KEEPS ITS OWN WEEKS")

def store(job_id, plan_id=None, week=None):
    db.enqueue_job(job_id, user, job_id, 1, {"x": 1}, reserve_credits=False,
                   plan_id=plan_id, plan_week=week)
    db.save_job_file(job_id, user, f"{job_id}.pdf", "application/pdf", b"PDF")


for w in range(1, 6):
    store(f"s{w}", sam, w)
for w in range(1, 6):
    store(f"a{w}", ava, w)
for i in range(1, 7):
    store(f"loose{i}")

with db._cursor() as cur:
    cur.execute("""SELECT j.plan_id, j.plan_week FROM job_files f
                   JOIN jobs j ON j.id=f.job_id""")
    rows = [(r["plan_id"], r["plan_week"]) for r in cur.fetchall()]

sam_weeks = sorted(w for p, w in rows if p == sam)
ava_weeks = sorted(w for p, w in rows if p == ava)
loose = [1 for p, _ in rows if p is None]
assert sam_weeks == [3, 4, 5], (
    f"a plan should keep the current week and the previous two, got {sam_weeks}")
assert ava_weeks == [3, 4, 5], (
    "one student's weeks were evicted by another student's, which is what a "
    f"single per-account cap does: {ava_weeks}")
assert len(loose) == 4, (
    f"loose booklets should still be capped per account, kept {len(loose)}")
ok("every plan keeps its newest three weeks, and cannot evict another plan's")

print("\nTHE PAGE OFFERS THE WORK THAT IS LEFT")

plans = {p["student_name"]: p for p in db.list_plans(user)}
assert plans["Sam"]["weeks_done"] == [1, 2, 4], plans["Sam"]["weeks_done"]
assert plans["Sam"]["next_week"] == 3, (
    "the week dropdown should open on the lowest week not yet generated, so a "
    f"skipped week is offered back rather than passed over: {plans['Sam']}")
ok("a plan reports which weeks are done and which to offer next")

print(f"\nALL {_passed} STUDY-PLAN CHECKS PASSED")
