"""Checks that a narrowed cover still speaks the product's own language.

`programs.py` says of itself that its names are the source of truth for cover
and menu labels. `Program.subject_display` is one opaque string for the whole
line ("Numeracy and Literacy"), which is fine while the booklet holds
everything the program declared. It stops being fine the moment the pipeline
has to narrow it.

`BookletPipeline._honest_subject_display` narrows the cover to the subjects the
finished booklet really contains, so that a booklet that lost a subject
upstream cannot go out still claiming it. With nothing mapping engine names to
product words, the only names it had to narrow WITH were the engines': a
NAPLAN booklet that came out numeracy-only was covered "Mathematics". Nothing
the customer bought is called that. They bought NAPLAN practice, chose it from
a menu that says numeracy and literacy, and are sitting a test that uses those
words on its own reports.

So the honesty guard was buying a true cover with the wrong vocabulary, and the
product's own naming file had no say in it.

Three properties:

  * Every product's declared subjects, put through its mapping, reproduce the
    cover line it already prints. That is what keeps the mapping in step with
    `subject_display` when a product is renamed: rename one and this fails
    rather than the two quietly disagreeing.
  * A narrowed cover uses the product's word. Checked for every program, not
    just NAPLAN, since the two single-subject products have their own phrasing
    too, and Academic Accelerate deliberately has none.
  * It holds end to end, through `run_program`, on a booklet whose literacy
    engine returned nothing. That is the situation the guard exists for, and
    the only one where any of this is visible to a customer.

No API key is needed or used: every call is served by the stub client below.

    PYTHONPATH=. python scripts/check_cover_vocabulary.py
"""
from __future__ import annotations

import inspect
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.disable(logging.CRITICAL)

from booklet_gen.pipeline import BookletPipeline               # noqa: E402
from booklet_gen.programs import (ACCELERATE_SUBJECTS,         # noqa: E402
                                  PROGRAMS, Program)
from booklet_gen.schemas import SubtopicOutput                 # noqa: E402

_passed = 0
_failed: list[str] = []


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg: str) -> None:
    _failed.append(msg)
    print("  FAIL:", msg)


def display_for(program, subject: str) -> str:
    """The product's word for one subject, read defensively.

    Falls back to what the product did before the mapping existed, so this
    file reports against a build that has none instead of stopping at a
    traceback on the first line that touches it.
    """
    fn = getattr(program, "display_for", None)
    return fn(subject) if fn else subject


def display_for_all(program, subjects) -> str:
    fn = getattr(program, "display_for_all", None)
    if fn is not None:
        return fn(subjects)
    return " and ".join(display_for(program, s) for s in subjects if s)


def check(good: bool, msg: str, why: str = "") -> None:
    """Assert `msg`. `why` is what the customer receives when it is false."""
    if good:
        ok(msg)
    else:
        bad(f"{msg}. {why}" if why else msg)


# ---------------------------------------------------------------------------
print("\nEVERY PRODUCT CAN NAME ONE SUBJECT IN ITS OWN WORDS")

check(hasattr(Program, "subject_display_by_subject")
      or "subject_display_by_subject" in getattr(Program, "__dataclass_fields__", {}),
      "Program carries a per-subject display mapping",
      "The cover line is one opaque string, so the only way to narrow it is "
      "to fall back on the subject engine names, and the product's own "
      "vocabulary is lost exactly when the cover is being corrected")

check(hasattr(Program, "display_for"),
      "and a `display_for` that falls back to the engine name",
      "Every caller then reaches into the mapping itself and each one decides "
      "for itself what a missing entry means")

for key, program in PROGRAMS.items():
    if program.pick_subject:
        # Accelerate has no line of its own: the parent picks the subject and
        # the cover prints the word they picked. The property worth holding is
        # that the word on the cover is the word on the menu.
        for subject in ACCELERATE_SUBJECTS:
            got = display_for(program, subject)
            check(got == subject,
                  f"{program.label}: the menu offers {subject!r} and the cover "
                  f"prints {got!r}",
                  f"The parent chose {subject!r} from the dropdown and the "
                  f"booklet came back headed {got!r}")
        continue
    line = display_for_all(program, program.subjects)
    check(line == program.subject_display,
          f'{program.label}: its {len(program.subjects)} subject(s) render as '
          f'"{line}", which is the cover it already prints',
          f'The mapping renders "{line}" while the cover prints '
          f'"{program.subject_display}". Two records of the product\'s name '
          f'that disagree, so renaming the product changes one of them and '
          f'the booklet says both')
    for subject in program.subjects:
        word = display_for(program, subject)
        check(word != subject or subject in program.subject_display,
              f"{program.label}: {subject} narrows to {word!r}",
              f"{subject} has no product word, so a booklet narrowed to it is "
              f"covered with the name of a subject ENGINE, which is an "
              f"internal term the customer has never seen")
        check(word in program.subject_display,
              f"and {word!r} is language this product already uses",
              f'{word!r} appears nowhere in "{program.subject_display}", so '
              f"the narrowed cover is in a vocabulary of its own")

print("\nTHE GUARD NARROWS INTO THAT VOCABULARY")

naplan = PROGRAMS["naplan"]
params = inspect.signature(BookletPipeline._honest_subject_display).parameters
check("display_by_subject" in params,
      "_honest_subject_display is given the product's words",
      "It has only the engine names to narrow with, so it prints them")

MATHS_SECTION = SubtopicOutput(topic="Number and Algebra",
                               subtopic="Place value",
                               subject="Mathematics", questions=[])
ENGLISH_SECTION = SubtopicOutput(topic="Language Conventions",
                                 subtopic="Commas",
                                 subject="English", questions=[])

if "display_by_subject" in params:
    def cover(sections):
        return BookletPipeline._honest_subject_display(
            naplan.subject_display, naplan.subjects, sections,
            display_by_subject=getattr(naplan, "subject_display_by_subject",
                                       None))

    got = cover([MATHS_SECTION])
    check(got == "Numeracy",
          f'a numeracy-only NAPLAN booklet is covered "{got}"',
          f'It is covered "{got}". The cover is now true and in the wrong '
          f'language: a parent who bought NAPLAN practice is handed a booklet '
          f'headed with the name of an internal subject engine')
    got = cover([ENGLISH_SECTION])
    check(got == "Literacy",
          f'a literacy-only one is covered "{got}"',
          f'It is covered "{got}", which is not what this product calls its '
          f'English half anywhere else')
    got = cover([MATHS_SECTION, ENGLISH_SECTION])
    check(got == naplan.subject_display,
          f'and a whole one is left at "{got}"',
          "The guard is rewriting a cover it should not touch")

print("\nAND IT HOLDS THROUGH run_program, WITH THE LITERACY ENGINE DOWN")

MATHS_OUTLINE = {
    "subject": "Mathematics", "year_level": "Year 5",
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

LESSON = json.dumps({
    "intro_paragraphs": ["Each place is ten times the one on its right."],
    "key_points": ["Read the number from the left."],
    "worked_example": {"question": "What is the 7 worth in 27 508?",
                       "steps": ["Find its column."], "answer": "7 000"},
    "guided_examples": [],
})


class StubClient:
    def complete(self, system, user, tier="strong", temperature=0.0):
        if system.startswith("You convert a short natural-language description"):
            return json.dumps(MATHS_OUTLINE)
        if "writing a mini-lesson" in system:
            return LESSON
        if system.startswith("You are an independent grader"):
            answers = re.findall(r"^Proposed answer: (.*)$", user, re.MULTILINE)
            return json.dumps({"results": [
                {"index": i, "solved_answer": a, "verified": True,
                 "reason": "stub"} for i, a in enumerate(answers)]})
        if "FINAL CHALLENGE" in system:
            m = re.search(r"exactly (\d+) cumulative", user)
            n = int(m.group(1)) if m else 5
            return json.dumps({"questions": [
                {"question": f"Final Challenge {i}: work it out.",
                 "answer": "42", "working": "shown", "difficulty": "hard"}
                for i in range(1, n + 1)]})
        m = re.search(r"Generate exactly (\d+) questions", user)
        n = int(m.group(1)) if m else 6
        sub = re.search(r"Subtopic: (.*)", user)
        tag = (sub.group(1) if sub else "x")[:24]
        return json.dumps({"questions": [
            {"question": f"{tag} {i}. What is 24 x {i}?",
             "answer": str(24 * i), "working": f"24 x {i}",
             "difficulty": "easy"} for i in range(1, n + 1)]})


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


class LiteracyEngineDown(BookletPipeline):
    """A pipeline whose English half produces nothing at all.

    Not a contrived state: an engine that raises, an outline that comes back
    empty, or a validator that rejects a whole half all land here, and the
    guard was written for exactly this. Stubbing the stage rather than the
    model is the only way to reach it deterministically, since a model stub
    that returns good English gets a booklet with English in it.
    """

    def _generate_from_outline(self, outline, seen=None, **kw):
        if outline.subject == "English":
            return [], [], []
        return super()._generate_from_outline(outline, seen, **kw)

    def _build_recap(self, subject, *a, **kw):
        if subject == "English":
            return []
        return super()._build_recap(subject, *a, **kw)


down = LiteracyEngineDown(client=StubClient(), retriever=NoRetriever(),
                          max_workers=1)
data = down.run_program("naplan", "Year 5", "Kieran")
english_anywhere = [
    vq for vq in (*data.recap_questions, *data.challenge_questions,
                  *(q for s in data.sections
                    for q in (*s.questions, *s.homework_questions)))
    if getattr(vq.question, "subject", None) == "English"]
check(not english_anywhere and not [s for s in data.sections
                                    if s.subject == "English"],
      "the fixture really did produce a booklet with no literacy in it",
      "The fixture is not reaching the state under test, so the cover below "
      "is measuring nothing")
check(data.subject == "Numeracy",
      f'and run_program covers it "{data.subject}"',
      f'A parent who bought NAPLAN Practice receives a booklet headed '
      f'"{data.subject}". The cover is at least true, which is the guard '
      f"working, but it is written in a vocabulary this product does not use "
      f"on its menu, its pricing page or the test it prepares for")

whole = BookletPipeline(client=StubClient(), retriever=NoRetriever(),
                        max_workers=1)
data = whole.run_program("accelerate", "Year 5", "Kieran", subject="Maths")
check(data.subject == "Mathematics",
      f'and an Academic Accelerate maths booklet still covers "{data.subject}"',
      f'Routing the picked subject through the program changed the cover to '
      f'"{data.subject}". Accelerate maps nothing on purpose; its cover is the '
      f"word the parent chose from the menu")

print(f"\n{_passed} passed, {len(_failed)} failed")
if _failed:
    for f in _failed:
        print("  -", f)
    sys.exit(1)
