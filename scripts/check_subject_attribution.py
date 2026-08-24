"""Checks that every question in a booklet says which subject it belongs to.

A NAPLAN booklet is two subject engines merged into one document. Class Work
survives that merge with its labels intact, because a section is a
`SubtopicOutput` and that carries `subject`. The two parts that are NOT
sections do not:

  * the Warm-up Recap, built once per subject and merged into one flat list,
  * the Final Challenge, built the same way and printed under one heading.

`Question` had no subject field at all, so once those two lists were merged
nothing downstream could tell a numeracy item from a literacy one. That is not
an abstract tidiness problem. `_honest_subject_display` narrows the cover to
the subjects the finished booklet really contains, and it could only see
sections, so a booklet whose sections came out maths-only while the Final
Challenge still held five English items was covered "Mathematics" and printed
over English questions. The guard against a false cover was itself printing a
false cover, in the other direction.

Three properties, each a defect a customer would be handed:

  * `Question` carries the attribution, defaulted so that the exam generator,
    the free-text path and every check that builds one by hand are unaffected.
  * The pipeline stamps it, from the engine that ran rather than from anything
    a model said, on section questions, recap questions and challenge
    questions alike. A field nothing fills is worse than no field, because it
    reads as an answer.
  * The cover guard is answerable for the WHOLE booklet. It may still narrow,
    which is what it is for, but it may not narrow away a subject that is
    sitting in the Final Challenge.

No API key is needed or used: every call is served by the stub client below.

    PYTHONPATH=. python scripts/check_subject_attribution.py
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The cap fitter logs a warning whenever a floor holds a session over its cap.
# Expected in lower primary, not a failure.
logging.disable(logging.CRITICAL)

from booklet_gen.pipeline import BookletPipeline            # noqa: E402
from booklet_gen.programs import PROGRAMS                   # noqa: E402
from booklet_gen.schemas import (Question, SubtopicOutput,  # noqa: E402
                                 ValidatedQuestion)

_passed = 0
_failed: list[str] = []


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg: str) -> None:
    _failed.append(msg)
    print("  FAIL:", msg)


def subj(vq) -> str | None:
    """The attribution on a validated question, or None.

    Read defensively so this file still reports rather than crashes against a
    build where `Question` has no such field: a traceback is not a measurement.
    """
    return getattr(vq.question, "subject", None)


def check(good: bool, msg: str, why: str = "") -> None:
    """Assert `msg`. `why` is what the customer receives when it is false."""
    if good:
        ok(msg)
    else:
        bad(f"{msg}. {why}" if why else msg)


# ---------------------------------------------------------------------------
# A stub model shaped like the two halves of a NAPLAN booklet. Deliberately
# smaller than the one in check_naplan_both_subjects.py: nothing here is about
# the trimmer, so two topics a side is enough to produce a merged recap and a
# merged Final Challenge, which is what is being measured.
# ---------------------------------------------------------------------------

MATHS_OUTLINE = {
    "subject": "Mathematics", "year_level": "YEAR",
    "topics": [
        {"name": "Number and Algebra", "subtopics": [
            {"name": "Place value to 10 000", "difficulty_hint": "medium",
             "question_types": ["computation"]}]},
        {"name": "Measurement and Geometry", "subtopics": [
            {"name": "Perimeter of rectangles", "difficulty_hint": "medium",
             "question_types": ["short answer"]}]},
        {"name": "Statistics and Probability", "subtopics": [
            {"name": "Reading column graphs", "difficulty_hint": "easy",
             "question_types": ["short answer"]}]},
    ],
}

ENGLISH_OUTLINE = {
    "subject": "English", "year_level": "YEAR",
    "topics": [
        {"name": "Language Conventions", "subtopics": [
            {"name": "Commas in lists", "difficulty_hint": "medium",
             "question_types": ["short answer"]}]},
        {"name": "Vocabulary", "subtopics": [
            {"name": "Synonyms", "difficulty_hint": "medium",
             "question_types": ["short answer"]}]},
        {"name": "Punctuation", "subtopics": [
            {"name": "Apostrophes", "difficulty_hint": "easy",
             "question_types": ["short answer"]}]},
    ],
}

MATHS_LESSON = json.dumps({
    "intro_paragraphs": ["Each place is ten times the one on its right."],
    "key_points": ["Read the number from the left."],
    "worked_example": {"question": "What is the 7 worth in 27 508?",
                       "steps": ["Find its column."], "answer": "7 000"},
    "guided_examples": [{"question": "What is the 3 worth in 13 460?",
                         "steps": ["Find its column."], "answer": "3 000"}],
})

ENGLISH_LESSON = json.dumps({
    "intro_paragraphs": ["A comma separates the items in a list."],
    "key_points": ["No comma before the last item."],
    "worked_example": {"question": "Add the commas: we packed apples pears figs.",
                       "steps": ["Find the items."],
                       "answer": "We packed apples, pears and figs."},
    "guided_examples": [{"question": "Add the commas: Mia won the race.",
                         "steps": ["Find the clause."],
                         "answer": "Mia, who lives next door, won the race."}],
})


def maths_questions(n: int, tag: str) -> dict:
    return {"questions": [
        {"question": f"{tag} {i}. What is 24 x {i}?", "answer": str(24 * i),
         "working": f"24 x {i}", "difficulty": "easy"}
        for i in range(1, n + 1)]}


def english_questions(n: int, tag: str) -> dict:
    return {"questions": [
        {"question": f"{tag} {i}. Write the plural of 'box{i}'.",
         "answer": f"box{i}es", "working": "add es", "difficulty": "easy"}
        for i in range(1, n + 1)]}


def judge_payload(user: str) -> str:
    answers = re.findall(r"^Proposed answer: (.*)$", user, re.MULTILINE)
    return json.dumps({"results": [
        {"index": i, "solved_answer": a, "verified": True, "reason": "stub"}
        for i, a in enumerate(answers)]})


def challenge_payload(user: str) -> str:
    m = re.search(r"exactly (\d+) cumulative", user)
    n = int(m.group(1)) if m else 5
    maths = "Subject: Mathematics" in user
    kind = "numeracy" if maths else "literacy"
    return json.dumps({"questions": [
        {"question": f"Final Challenge {kind} {i}: work it out.",
         "answer": "42" if maths else "boxes", "working": "shown",
         "difficulty": "hard"}
        for i in range(1, n + 1)]})


class StubClient:
    def __init__(self, year: str) -> None:
        self.year = year

    def complete(self, system, user, tier="strong", temperature=0.0):
        if system.startswith("You convert a short natural-language description"):
            head = user.split("\n", 1)[0]
            body = ENGLISH_OUTLINE if "literacy" in head else MATHS_OUTLINE
            return json.dumps(body).replace("YEAR", self.year)
        if "writing a mini-lesson" in system:
            return (ENGLISH_LESSON
                    if system.startswith("You are a warm, expert English")
                    else MATHS_LESSON)
        if system.startswith("You are an independent grader"):
            return judge_payload(user)
        if "FINAL CHALLENGE" in system:
            return challenge_payload(user)
        m = re.search(r"Generate exactly (\d+) questions", user)
        n = int(m.group(1)) if m else 6
        sub = re.search(r"Subtopic: (.*)", user)
        tag = (sub.group(1) if sub else "x")[:24]
        if "Subject: English" in user:
            return json.dumps(english_questions(n, tag))
        return json.dumps(maths_questions(n, tag))


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


program = PROGRAMS["naplan"]
DECLARED = tuple(program.subjects)


# ---------------------------------------------------------------------------
print("\nA QUESTION CAN SAY WHICH SUBJECT IT BELONGS TO")

field = Question.model_fields.get("subject")
check(field is not None,
      "Question carries a `subject` field",
      "The Warm-up Recap and the Final Challenge are merged across subjects "
      "into flat lists with no section around them, so once they are merged "
      "nothing in the booklet, the answer key or the cover guard can say "
      "which half of a two-subject product a question came from")
if field is not None:
    check(field.default is None and not field.is_required(),
          "and it defaults to None rather than being required",
          "Question is also the exam generator's parse target and is built by "
          "hand in a dozen checks. A required field here breaks all of them "
          "and buys nothing: a single-subject booklet has nothing to attribute")

print("\nTHE PIPELINE FILLS IT IN, EVERYWHERE A QUESTION IS KEPT")

pipeline = BookletPipeline(client=StubClient("Year 5"), retriever=NoRetriever(),
                           max_workers=1)
data = pipeline.run_program("naplan", "Year 5", "Kieran")

parts = {
    "Warm-up Recap": list(data.recap_questions),
    "Final Challenge": list(data.challenge_questions),
    "Class Work": [vq for s in data.sections for vq in s.questions],
    "Homework": [vq for s in data.sections for vq in s.homework_questions],
}
for name, questions in parts.items():
    check(bool(questions), f"the booklet has a {name} to measure")
    unattributed = [vq.question.question[:40] for vq in questions
                    if not subj(vq)]
    check(not unattributed,
          f"every one of the {len(questions)} {name} questions is attributed "
          f"({dict(Counter(subj(vq) for vq in questions))})",
          f"{len(unattributed)} unattributed, e.g. {unattributed[:2]}. A field "
          f"nothing fills is worse than no field at all, because a reader "
          f"takes the empty value for an answer")
    stray = {subj(vq) for vq in questions} - set(DECLARED)
    check(not stray,
          f"and every {name} attribution is one of the declared subjects",
          f"{stray} is not a subject this program runs, so the attribution is "
          f"coming from the model rather than from the engine")

print("\nAND IT AGREES WITH THE SECTION IT SITS IN")
mismatched = [(s.subtopic, s.subject, subj(vq))
              for s in data.sections
              for vq in (*s.questions, *s.homework_questions)
              if subj(vq) != s.subject]
check(not mismatched,
      f"all {len(parts['Class Work']) + len(parts['Homework'])} section "
      f"questions match SubtopicOutput.subject",
      f"{mismatched[:2]} (subtopic, section subject, question subject). Two "
      f"records of the same fact that disagree are worse than one, because "
      f"whichever is read first is believed")

print("\nBOTH HALVES REACH THE MERGED WARM-UP AND FINAL CHALLENGE")
for name in ("Warm-up Recap", "Final Challenge"):
    got = Counter(subj(vq) for vq in parts[name])
    for subject in DECLARED:
        check(got[subject] > 0,
              f"the {name} carries {got[subject]} {subject} question(s)",
              f"The fixture generates both halves, so a {name} with no "
              f"{subject} in it means the attribution is being lost between "
              f"the engine and the booklet and the rest of this file is "
              f"measuring nothing")

print("\nTHE COVER GUARD IS ANSWERABLE FOR THE WHOLE BOOKLET")

params = inspect.signature(BookletPipeline._honest_subject_display).parameters
check("loose_questions" in params,
      "_honest_subject_display is given the questions that sit outside a "
      "section",
      "It can only see sections, so the Warm-up Recap and the Final Challenge "
      "are invisible to the one guard that decides what the cover claims")

if "loose_questions" in params:
    def cover(sections, loose=()):
        return BookletPipeline._honest_subject_display(
            program.subject_display, DECLARED, sections, loose_questions=loose)

    def vq(text, subject):
        return ValidatedQuestion(
            question=Question(question=text, answer="42", working="shown",
                              subject=subject), verified=True)

    MATHS_SECTION = SubtopicOutput(topic="Number and Algebra",
                                   subtopic="Place value",
                                   subject="Mathematics", questions=[])
    ENGLISH_SECTION = SubtopicOutput(topic="Language Conventions",
                                     subtopic="Commas",
                                     subject="English", questions=[])
    ENGLISH_CHALLENGE = [vq("Explain which word fits and why.", "English")]
    MATHS_CHALLENGE = [vq("A shop sells 24 pies a day. How many in a week?",
                          "Mathematics")]

    # The defect, exactly: sections are maths-only, the Final Challenge is not.
    got = cover([MATHS_SECTION], ENGLISH_CHALLENGE)
    check(got == program.subject_display,
          f'a booklet whose sections are maths-only but whose Final Challenge '
          f'holds English is covered "{got}"',
          f'It is covered "{got}", and the English questions in the Final '
          f'Challenge print underneath that word. The guard exists to stop the '
          f'cover naming a subject the booklet does not hold, and here it '
          f'removed a subject the booklet does hold')

    # It still narrows, which is the whole point of it.
    got = cover([MATHS_SECTION], MATHS_CHALLENGE)
    check(got != program.subject_display,
          f'a booklet with no English anywhere is narrowed to "{got}"',
          f'Counting the loose questions must not turn the guard off: this '
          f'booklet contains no literacy in any part of it and is still '
          f'covered "{got}"')

    got = cover([MATHS_SECTION, ENGLISH_SECTION],
                MATHS_CHALLENGE + ENGLISH_CHALLENGE)
    check(got == program.subject_display,
          f'a booklet holding both halves is left alone at "{got}"',
          "The guard is rewriting covers it should not touch, which replaces "
          "the product's own words with engine names on every booklet")

    # The old three-argument call is what check_naplan_both_subjects.py makes.
    got = BookletPipeline._honest_subject_display(
        program.subject_display, DECLARED, [MATHS_SECTION])
    check(got == "Mathematics",
          f'and a caller that passes no loose questions still gets "{got}"',
          "The new parameter changed the behaviour of the old call, so every "
          "existing caller now means something different from what it says")

print(f"\n{_passed} passed, {len(_failed)} failed")
if _failed:
    for f in _failed:
        print("  -", f)
    sys.exit(1)
