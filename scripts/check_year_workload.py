"""Checks that a younger student is set less work than an older one.

Five booklets generated from production on 21 August 2026 (Years 1, 3, 5, 7
and 9 Mathematics) were measured off the page:

    Year 1   warm-up 5,  class work 52,  total 105 min,  21 pages
    Year 3   5 / 57 / 110, 24 pages       Year 5   6 / 55 / 110, 22 pages
    Year 7   4 / 57 / 110, 22 pages       Year 9   4 / 55 / 105, 22 pages

Every one of them taught four subtopics and set sixteen homework questions. A
six year old was issued the same workload as a fourteen year old, including a
fifty-two minute continuous block of written maths. A parent of a Year 1 knows
that is wrong at a glance, and a booklet that is wrong about something that
obvious puts every other page of it in doubt, including the pages that are
right.

Nothing in the generator knew the student's age. Class Work was filled up to
one flat hour for everybody, and the homework, recap and challenge counts were
constructor defaults. `timing.session_plan` now sets a budget per year band and
the pipeline builds to it.

Three properties, and each is a way the fault comes back.

  * A Year 1 booklet is MATERIALLY shorter than a Year 9 one: fewer minutes of
    class work, fewer minutes in total, fewer questions, fewer pages. Floors,
    not just an ordering, because one minute of difference would satisfy an
    ordering and still hand a six year old an hour of maths.
  * A Year 9 booklet is NOT shortened. The cheap way to pass the first property
    is to shrink everybody, which fixes nothing and takes work away from the
    students who can do it. Year 9 keeps its hour.
  * Nothing that a credit buys is traded away to get there. The pricing page
    renders MIN_CLASSWORK_TOPICS and MIN_NOW_YOU_TRY into a promise of at least
    three topics and at least four questions under every mini-lesson. Those
    hold at every year: a shorter session is bought with a lower cap and fewer
    subtopics inside it, never with a narrower booklet or thinner practice.

No API key is needed or used: every call is served by a stub client. The stub
is calibrated against the shipped booklets, so a subtopic here costs about the
fourteen minutes one really costs, and the Year 9 numbers this file measures
are the Year 9 numbers the customer received.

    PYTHONPATH=. python scripts/check_year_workload.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The fitter logs a warning every time a floor holds a session over its cap,
# which is the expected outcome for lower primary and not a failure.
logging.disable(logging.CRITICAL)

import pypdf                                                     # noqa: E402

from booklet_gen.formatter import render_pdf                     # noqa: E402
from booklet_gen.pipeline import (BookletPipeline,               # noqa: E402
                                  CLASSWORK_CAP_MINUTES,
                                  MIN_CLASSWORK_SUBTOPICS,
                                  MIN_CLASSWORK_TOPICS,
                                  MIN_NOW_YOU_TRY)
from booklet_gen.timing import booklet_timing, session_plan      # noqa: E402

_passed = 0
_failed: list[str] = []


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg: str) -> None:
    _failed.append(msg)
    print("  FAIL:", msg)


def check(good: bool, msg: str) -> None:
    (ok if good else bad)(msg)


# ---------------------------------------------------------------------------
# A stub model, calibrated against the shipped booklets
#
# The mini-lesson is the shape the intro writer really produces at Year 1: a
# short paragraph, two key points, a worked example of three steps and one
# guided example. The question set opens with two short easy questions and
# continues with longer medium ones. Together a subtopic costs about fourteen
# minutes, which is what one cost on the page.
# ---------------------------------------------------------------------------

LESSON = json.dumps({
    "intro_paragraphs": [
        "To find half of a shape or a group, you must split it into two equal "
        "parts. Both parts must be exactly the same size or amount."],
    "key_points": ["Whatever you do to one side, you must do to the other.",
                   "Two halves make one whole."],
    "mnemonic": "Split, then check",
    "worked_example": {
        "question": "Find half of 8 counters.",
        "steps": ["Draw two circles to show the two equal parts.",
                  "Share the 8 counters one by one into the circles.",
                  "Count how many counters are in one circle."],
        "answer": "Half of 8 counters is 4 counters.", "diagram_spec": None},
    "guided_examples": [{
        "question": "Find half of 12 marbles.",
        "steps": ["Draw two groups to share the marbles into.",
                  "Place one marble into each group until all 12 are shared.",
                  "Count one group to get the answer."],
        "answer": "6", "diagram_spec": None}],
})

# Four topics, seven subtopics: more than any session can teach, which is what
# the outline parser really returns. What the booklet ends up with is therefore
# decided by the cap fitter, exactly as it is in production, and not by the
# fixture.
OUTLINE = json.dumps({
    "subject": "Mathematics", "year_level": "YEAR",
    "topics": [
        {"name": "Number and Place Value", "subtopics": [
            {"name": "Counting and ordering numbers",
             "difficulty_hint": "medium", "question_types": ["computation"]},
            {"name": "Partitioning numbers",
             "difficulty_hint": "medium", "question_types": ["word problem"]}]},
        {"name": "Addition and Subtraction", "subtopics": [
            {"name": "Adding and subtracting within 20",
             "difficulty_hint": "medium", "question_types": ["computation"]},
            {"name": "Missing number problems",
             "difficulty_hint": "hard", "question_types": ["word problem"]}]},
        {"name": "Fractions and Money", "subtopics": [
            {"name": "Halves of shapes and collections",
             "difficulty_hint": "medium", "question_types": ["word problem"]}]},
        {"name": "Measurement and Geometry", "subtopics": [
            {"name": "Classifying 2D shapes",
             "difficulty_hint": "medium", "question_types": ["short answer"]},
            {"name": "Comparing lengths",
             "difficulty_hint": "medium", "question_types": ["comparison"]}]},
    ],
})


def _question_payload(user: str) -> str:
    """Exactly as many questions as the caller asked for, and no more."""
    m = re.search(r"Generate exactly (\d+) questions", user)
    n = int(m.group(1)) if m else 6
    sub = re.search(r"Subtopic: (.*)", user)
    tag = (sub.group(1) if sub else "x")[:28]
    out = []
    for i in range(1, n + 1):
        if i <= 2:
            out.append({"question": f"{tag} {i}. Kieran counted 14 birds on a "
                                    f"fence. Six flew away. How many are left?",
                        "answer": "8", "working": "14 - 6 = 8",
                        "difficulty": "easy"})
        else:
            out.append({"question": f"{tag} {i}. A box holds 24 pencils. The "
                                    f"teacher shares them equally between four "
                                    f"tables. How many pencils does each table "
                                    f"get?",
                        "answer": "6", "working": "24 / 4 = 6",
                        "difficulty": "medium"})
    return json.dumps({"questions": out})


def _judge_payload(user: str) -> str:
    """Pass everything, echoing each proposed answer back to itself."""
    answers = re.findall(r"^Proposed answer: (.*)$", user, re.MULTILINE)
    return json.dumps({"results": [
        {"index": i, "solved_answer": a, "verified": True, "reason": "stub"}
        for i, a in enumerate(answers)]})


def _challenge_payload(user: str) -> str:
    m = re.search(r"exactly (\d+) cumulative", user)
    n = int(m.group(1)) if m else 5
    return json.dumps({"questions": [
        {"question": f"Challenge {i}. A rectangle is {10 + i} cm long and 5 cm "
                     f"wide. Work out its perimeter, then explain how you know "
                     f"your answer is right.",
         "answer": f"{2 * (15 + i)} cm", "working": f"2 x ({10 + i} + 5)",
         "difficulty": "hard"}
        for i in range(1, n + 1)]})


class StubClient:
    def __init__(self, year: str) -> None:
        self.year = year

    def complete(self, system, user, tier="strong", temperature=0.0):
        if system.startswith("You convert a short natural-language description"):
            return OUTLINE.replace("YEAR", self.year)
        if "writing a mini-lesson" in system:
            return LESSON
        if system.startswith("You are an independent grader"):
            return _judge_payload(user)
        if "FINAL CHALLENGE" in system:
            return _challenge_payload(user)
        return _question_payload(user)


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


def build(year: str, out_dir: Path) -> dict:
    """Generate one Mathematics booklet for this year and measure it."""
    pipeline = BookletPipeline(client=StubClient(year), retriever=NoRetriever(),
                               max_workers=1)
    data = pipeline.run_program("accelerate", year, "Kieran", subject="Maths")
    t = booklet_timing(data)
    pdf = render_pdf(data, out_dir / f"{year.replace(' ', '').lower()}.pdf")
    taught = [s for s in data.sections if s.questions]
    return {
        "year": year,
        "data": data,
        "taught": len(taught),
        "topics": len({s.topic for s in taught}),
        "per_subtopic": [len(s.questions) for s in taught],
        "classwork_questions": sum(len(s.questions) for s in data.sections),
        "homework_questions": sum(len(s.homework_questions)
                                  for s in data.sections),
        "challenge": len(data.challenge_questions),
        "recap": len(data.recap_questions),
        "classwork_minutes": t["classwork_minutes"] or 0,
        "total_minutes": t["total_minutes"],
        "pages": len(pypdf.PdfReader(str(pdf)).pages),
    }


def questions(b: dict) -> int:
    return (b["classwork_questions"] + b["homework_questions"]
            + b["challenge"] + b["recap"])


# ---------------------------------------------------------------------------
print("\nTHE BUDGET RISES WITH THE YEAR AND NEVER PASSES THE TUTOR'S HOUR")

plans = {y: session_plan(f"Year {y}") for y in range(1, 11)}
caps = [plans[y].classwork_cap_minutes for y in range(1, 11)]
check(all(a <= b for a, b in zip(caps, caps[1:])),
      f"the class work cap never falls as the year rises: {caps}")
check(caps[0] < caps[-1],
      f"and a Year 1 session is capped well below a Year 10 one "
      f"({caps[0]} min against {caps[-1]} min)")
check(max(caps) <= CLASSWORK_CAP_MINUTES,
      "no year band can book more than the tutor's hour, whatever it asks for "
      f"(highest cap {max(caps)} min, ceiling {CLASSWORK_CAP_MINUTES} min)")
check(plans[9].classwork_cap_minutes == CLASSWORK_CAP_MINUTES,
      "Year 9 still gets the whole hour it was already getting")
check(session_plan("").classwork_cap_minutes == CLASSWORK_CAP_MINUTES
      and session_plan("Year 4 something odd").classwork_cap_minutes == 40,
      "a year level nobody can parse falls back to the hour rather than "
      "guessing a shorter one")

homework = [plans[y].homework_per_subtopic for y in range(1, 11)]
check(all(a <= b for a, b in zip(homework, homework[1:]))
      and homework[0] < homework[-1],
      f"homework per subtopic rises with the year too: {homework}")

print("\nNO FLOOR THE PRICING PAGE SELLS IS LOWERED TO GET THERE")
check(all(p.min_topics >= MIN_CLASSWORK_TOPICS for p in plans.values()),
      f"every year still covers at least {MIN_CLASSWORK_TOPICS} topics, which "
      "is what the pricing page promises a credit buys")
check(all(p.min_subtopics >= MIN_CLASSWORK_SUBTOPICS for p in plans.values()),
      f"and still teaches at least {MIN_CLASSWORK_SUBTOPICS} subtopics")

# ---------------------------------------------------------------------------
print("\nWHAT A BOOKLET ACTUALLY CONTAINS, YEAR BY YEAR")

tmp = Path(tempfile.mkdtemp(prefix="folio-year-workload-"))
booklets = {y: build(f"Year {y}", tmp) for y in (1, 3, 5, 7, 9)}

print(f"    {'year':<9}{'taught':>7}{'class q':>9}{'hwk q':>7}{'chal':>6}"
      f"{'class min':>11}{'total min':>11}{'pages':>7}")
for y, b in booklets.items():
    print(f"    {b['year']:<9}{b['taught']:>7}{b['classwork_questions']:>9}"
          f"{b['homework_questions']:>7}{b['challenge']:>6}"
          f"{b['classwork_minutes']:>11}{b['total_minutes']:>11}"
          f"{b['pages']:>7}")

y1, y9 = booklets[1], booklets[9]

# The floors below are set well inside what the change actually produces
# (14 minutes of class work, 40 minutes in total, 17 questions and 5 pages when
# this was written), so ordinary drift in question length cannot trip them and
# a return to one-size-fits-all cannot slip past them.
print("\nA YEAR 1 BOOKLET IS MATERIALLY SHORTER THAN A YEAR 9 ONE")
gap = y9["classwork_minutes"] - y1["classwork_minutes"]
check(gap >= 8,
      f"the Year 1 class work block is {gap} min shorter than the Year 9 one "
      f"({y1['classwork_minutes']} against {y9['classwork_minutes']}), so a "
      "six year old is not sat down for a fourteen year old's lesson")
total_gap = y9["total_minutes"] - y1["total_minutes"]
check(total_gap >= 25,
      f"the Year 1 booklet is {total_gap} min shorter in total "
      f"({y1['total_minutes']} against {y9['total_minutes']})")
q_gap = questions(y9) - questions(y1)
check(q_gap >= 10,
      f"and sets {q_gap} fewer questions ({questions(y1)} against "
      f"{questions(y9)}), so the work itself is smaller and not just the "
      "estimate printed over it")
page_gap = y9["pages"] - y1["pages"]
check(page_gap >= 3,
      f"the Year 1 PDF is {page_gap} pages shorter ({y1['pages']} against "
      f"{y9['pages']}): the difference is visible before anything is read")
check(y1["taught"] < y9["taught"],
      f"the Year 1 session teaches fewer subtopics "
      f"({y1['taught']} against {y9['taught']}), which is where the minutes go")

print("\nAND THE WORKLOAD RISES ALL THE WAY UP, NOT JUST AT THE ENDS")
order = [booklets[y] for y in (1, 3, 5, 7, 9)]
check(all(a["total_minutes"] <= b["total_minutes"]
          for a, b in zip(order, order[1:])),
      "no year is set more work than the year above it: "
      + ", ".join(f"{b['year']} {b['total_minutes']} min" for b in order))
check(all(questions(a) <= questions(b) for a, b in zip(order, order[1:])),
      "and no year is set more questions than the year above it: "
      + ", ".join(f"{b['year']} {questions(b)}" for b in order))

print("\nTHE OLDER STUDENT IS NOT SHORT-CHANGED TO MAKE THAT TRUE")
check(y9["classwork_minutes"] >= 50,
      f"Year 9 class work still fills the session ({y9['classwork_minutes']} "
      f"min of a {CLASSWORK_CAP_MINUTES} min hour), so the gap above was not "
      "won by shrinking everybody")
check(y9["taught"] >= 4,
      f"Year 9 still teaches {y9['taught']} subtopics")
check(y9["homework_questions"] >= 12,
      f"and still gets a week of homework ({y9['homework_questions']} "
      "questions)")

print("\nEVERY YEAR KEEPS WHAT A CREDIT BUYS")
for y, b in booklets.items():
    check(b["topics"] >= MIN_CLASSWORK_TOPICS,
          f"{b['year']} covers {b['topics']} topics, the promise is "
          f"{MIN_CLASSWORK_TOPICS}")
    check(b["taught"] >= MIN_CLASSWORK_SUBTOPICS,
          f"{b['year']} teaches {b['taught']} subtopics, the floor is "
          f"{MIN_CLASSWORK_SUBTOPICS}")
    check(all(n >= MIN_NOW_YOU_TRY for n in b["per_subtopic"]),
          f"{b['year']} keeps {MIN_NOW_YOU_TRY} questions under every "
          f"mini-lesson: {b['per_subtopic']}")
    check(b["challenge"] >= 3 and b["recap"] >= 3,
          f"{b['year']} still has a warm-up ({b['recap']}) and a Final "
          f"Challenge ({b['challenge']})")

print("\nAND THE COVER STILL TELLS THE TRUTH ABOUT WHAT IS INSIDE")
for y, b in booklets.items():
    data = b["data"]
    printed = booklet_timing(data)
    check(printed["total_minutes"] == b["total_minutes"],
          f"{b['year']} prints the total measured off its own pages "
          f"({b['total_minutes']} min)")
    # A shorter cap must not be reached by printing a smaller number over the
    # same work. The estimate is recomputed from the finished booklet, so an
    # overrun shows: what must never happen is class work costing more than the
    # measured content says.
    measured = sum(printed["section_raw"])
    check(abs(measured - printed["classwork_raw"]) < 0.01,
          f"{b['year']} class work minutes are the sum of its sections, not a "
          "target copied onto the page")

# ---------------------------------------------------------------------------
print(f"\nPDFs written to {tmp}")
print(f"\n{_passed} passed, {len(_failed)} failed")
if _failed:
    for f in _failed:
        print("  -", f)
    sys.exit(1)
