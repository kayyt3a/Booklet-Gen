"""Checks that the reading budget in the maths prompts reaches the page.

Correct in the prompt, verified on the page. That is the rule this codebase
already keeps for the house style (`_dedash`), for guided examples
(`consistency.seal_guided_example`) and for notation (`mathnotation`). Reading
load had the first half and not the second.

The maths prompts set a word budget per year band, because measurement showed a
Year 1 booklet shipping a 39-word question and Year 1 carrying about seventy
per cent of Year 7's reading load. At Year 1 the maths is one addition and the
question is the obstacle: a child who can add 10 and 6 fails on decoding, and
neither they nor their parent can tell which of the two things went wrong.
Nothing in the pipeline measured the budget, so a model that ignored it
produced the same booklet as before with nothing failing anywhere.

What is pinned here:

  * The numbers enforced ARE the numbers asked for. Both prompt files are
    parsed and compared against the table in `agents/reading_load.py`, so the
    two cannot drift apart, and the word counter agrees with the prompt's own
    worked example of a 16-word question rewritten to 9.
  * A question over the budget does not reach the page. Measured off a
    rendered PDF, not off the data structure, because the page is what the
    child reads.
  * The floor holds. A model that breaches wholesale must not empty the
    booklet: the pricing page promises four questions under every mini-lesson,
    so what cannot be dropped is kept and logged at warning level rather than
    quietly cut. This is the honest half of the design, and it is asserted
    against, not left to a comment.
  * A question that FITS is never dropped, and English is never touched: a
    comprehension question is read beside a passage that is hundreds of words
    by design, and the English prompt budgets the passage instead.
  * Validation stays batched. Enforcement that cost an API call a question
    would trade the project's main cost lever for a word count, so the judge
    calls are counted.

No API key is needed or used: every call is served by the stub clients below.

    PYTHONPATH=. python scripts/check_reading_load.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf                                                    # noqa: E402

from booklet_gen.agents import reading_load                     # noqa: E402
from booklet_gen.agents._shared import PROMPT_DIR               # noqa: E402
from booklet_gen.formatter import render_pdf                    # noqa: E402
from booklet_gen.pipeline import (BookletPipeline,              # noqa: E402
                                  MIN_NOW_YOU_TRY)
from booklet_gen.schemas import Question                        # noqa: E402

_passed = 0
_failed: list[str] = []


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg: str) -> None:
    _failed.append(msg)
    print("  FAIL:", msg)


def check(good: bool, msg: str, why: str = "") -> None:
    """Assert `msg`. `why` is what the customer receives when it is false."""
    if good:
        ok(msg)
    else:
        bad(f"{msg}. {why}" if why else msg)


# The pipeline's own log is where an unfixable breach is surfaced, so it is
# captured rather than silenced. Detached from the root logger so the expected
# warnings (the cap fitter's, among others) do not print over this file's
# output.
class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def events(self, name: str) -> list[logging.LogRecord]:
        return [r for r in self.records if r.msg == name]

    def clear(self) -> None:
        self.records.clear()


CAPTURED = Capture()
_pkg = logging.getLogger("booklet_gen")
_pkg.addHandler(CAPTURED)
_pkg.setLevel(logging.INFO)
_pkg.propagate = False


# ---------------------------------------------------------------------------
print("\nTHE ENFORCED NUMBERS ARE THE NUMBERS THE PROMPTS ASK FOR")

practice_prompt = (PROMPT_DIR / "question_generator_maths.txt").read_text(
    encoding="utf-8")
challenge_prompt = (PROMPT_DIR / "challenge_generator_maths.txt").read_text(
    encoding="utf-8")

# "Years 1-2: ... AT MOST 12 WORDS, averaging 8."
asked_practice = {
    int(top): (int(most), int(avg)) for _, top, most, avg in
    re.findall(r"Years (\d+)-(\d+)[^\n]*?AT MOST (\d+) WORDS, averaging (\d+)",
               practice_prompt)}
# "AT MOST 15 words a question in Years 1-2, 30 words in Years 3-4, ..."
asked_challenge = {
    int(top): int(most) for most, _, top in
    re.findall(r"(\d+) words?(?: a question)? in Years (\d+)-(\d+)",
               challenge_prompt)}

check(len(asked_practice) == 5 and len(asked_challenge) == 5,
      f"both maths prompts still state a per-year budget "
      f"({len(asked_practice)} practice bands, {len(asked_challenge)} "
      f"challenge bands)",
      "The prompt no longer asks for one, so this file is comparing the code "
      "against nothing and every assertion below is vacuous")

enforced_practice = {top: (most, avg) for (top, most), (_, avg) in
                     zip(reading_load.PRACTICE_MAX_WORDS,
                         reading_load.PRACTICE_AVERAGE_WORDS)}
enforced_challenge = dict(reading_load.CHALLENGE_MAX_WORDS)
check(enforced_practice == asked_practice,
      f"practice budgets match the prompt: "
      f"{ {k: v[0] for k, v in enforced_practice.items()} } words by top year",
      f"The prompt asks for {asked_practice} and the code enforces "
      f"{enforced_practice}. Two sets of numbers, so the booklet is written to "
      f"one rule and marked against another")
check(enforced_challenge == asked_challenge,
      f"Final Challenge budgets match its prompt: {enforced_challenge}",
      f"The prompt asks for {asked_challenge} and the code enforces "
      f"{enforced_challenge}")

# The prompt counts its own example: 16 words, rewritten to 9. A counter that
# disagreed with the prompt's author would be enforcing a different rule from
# the one the model was given.
LONG_EXAMPLE = ("A basket has 10 apples and 6 oranges. How many pieces of "
                "fruit are there altogether?")
SHORT_EXAMPLE = "10 apples and 6 oranges. How many fruit altogether?"
check(reading_load.word_count(LONG_EXAMPLE) == 16
      and reading_load.word_count(SHORT_EXAMPLE) == 9,
      f"the word counter reads the prompt's own example as "
      f"{reading_load.word_count(LONG_EXAMPLE)} words and its rewrite as "
      f"{reading_load.word_count(SHORT_EXAMPLE)}, exactly as the prompt does",
      "The counter and the prompt disagree about what a word is, so the "
      "budget enforced is not the budget requested")

print("\nTHE BUDGET APPLIES TO MATHS, AND ONLY TO MATHS")
for subject, expected in (("Mathematics", True), ("Maths", True),
                          ("English", False), ("Reasoning", False)):
    check(reading_load.applies(subject) is expected,
          f"{subject}: reading budget applies = {expected}",
          "An English comprehension question is read beside a passage that is "
          "hundreds of words by design, and its prompt budgets the passage "
          "instead. Holding it to a maths question's word count would delete "
          "the comprehension half of a NAPLAN booklet")

wordy_english = Question(
    question=("According to the third paragraph, why do the scientists at "
              "Exmouth photograph the underside of each whale's tail rather "
              "than counting the whales they can see from the headland?"),
    answer="Every tail is different.", working="stated in paragraph three")
kept, dropped = reading_load.hold_to_budget([wordy_english], "Year 3",
                                            "English", keep_at_least=0)
check(len(kept) == 1 and not dropped,
      f"a {reading_load.word_count(wordy_english.question)} word comprehension "
      f"question at Year 3 is left alone",
      "A comprehension question was dropped for being longer than a Year 3 "
      "arithmetic question, which deletes the reading half of the product")


# ---------------------------------------------------------------------------
# A stub model that ignores the budget, which is the whole point: a model that
# kept it would leave nothing to enforce.
# ---------------------------------------------------------------------------

LONG_MARK = "wheelbarrow"      # only ever appears in an over-budget question
SHORT_MARK = "marbles"         # only ever appears in a question that fits


def long_question(tag: str, i: int) -> dict:
    return {
        "question": (f"{tag} {i}. On Saturday morning Mia and her older "
                     f"brother went out to the garden shed with a heavy "
                     f"wheelbarrow, and after a while they had carefully "
                     f"counted 24 red apples, and then a little later they "
                     f"also counted 13 more green apples from the second "
                     f"tree by the fence. How many apples did they count "
                     f"altogether?"),
        "answer": "37", "working": "24 + 13 = 37", "difficulty": "medium"}


def short_question(tag: str, i: int) -> dict:
    """Nine words including the tag, so it fits even the Year 1 budget.

    The count is deliberately clear of 24: a question whose own answer is
    printed in it is dropped by another guard entirely
    (`question_states_its_answer`), and a fixture losing questions to a second
    guard would make the numbers below unreadable.
    """
    return {"question": f"{tag} {i}. How many marbles in {i + 1} boxes of 24?",
            "answer": str(24 * (i + 1)),
            "working": f"24 x {i + 1} = {24 * (i + 1)}",
            "difficulty": "easy"}


def outline(year: str) -> dict:
    return {"subject": "Mathematics", "year_level": year, "topics": [
        {"name": "Number and Algebra", "subtopics": [
            {"name": "Addition", "difficulty_hint": "easy",
             "question_types": ["computation"]}]},
        {"name": "Measurement and Geometry", "subtopics": [
            {"name": "Length", "difficulty_hint": "easy",
             "question_types": ["short answer"]}]},
        {"name": "Statistics and Probability", "subtopics": [
            {"name": "Graphs", "difficulty_hint": "easy",
             "question_types": ["short answer"]}]}]}
# One-word subtopics on purpose. The stub tags every question with its subtopic
# so the booklet-wide dedupe cannot empty nine sections out of ten, and at Year
# 1 the whole question has twelve words to live in, tag included.


LESSON = json.dumps({
    "intro_paragraphs": ["Add the ones first, then the tens."],
    "key_points": ["Line up the columns."],
    "worked_example": {"question": "What is 24 + 13?",
                       "steps": ["Add the ones.", "Add the tens."],
                       "answer": "37"},
    "guided_examples": [],
})


class StubClient:
    """`n_long` of every generated set breaches the budget."""

    def __init__(self, year: str, n_long: int, all_long: bool = False) -> None:
        self.year = year
        self.n_long = n_long
        self.all_long = all_long
        self.judge_calls = 0
        # Every question SET asked for, the Final Challenge included. The
        # judge is meant to be called once per set, never once per question.
        self.set_calls = 0

    def complete(self, system, user, tier="strong", temperature=0.0):
        if system.startswith("You convert a short natural-language description"):
            return json.dumps(outline(self.year))
        if "writing a mini-lesson" in system:
            return LESSON
        if system.startswith("You are an independent grader"):
            self.judge_calls += 1
            answers = re.findall(r"^Proposed answer: (.*)$", user, re.MULTILINE)
            return json.dumps({"results": [
                {"index": i, "solved_answer": a, "verified": True,
                 "reason": "stub"} for i, a in enumerate(answers)]})
        if "FINAL CHALLENGE" in system:
            self.set_calls += 1
            m = re.search(r"exactly (\d+) cumulative", user)
            n = int(m.group(1)) if m else 3
            return json.dumps({"questions": [
                short_question("Challenge", i) for i in range(1, n + 1)]})
        self.set_calls += 1
        m = re.search(r"Generate exactly (\d+) questions", user)
        n = int(m.group(1)) if m else 6
        sub = re.search(r"Subtopic: (.*)", user)
        tag = (sub.group(1) if sub else "x")[:22]
        if "Warm-up Recap" in user:
            # A short tag of its own: the recap's "subtopic" is a paragraph of
            # instructions, and truncating that into the question text would
            # blow the budget on the fixture rather than on what it measures.
            return json.dumps({"questions": [short_question("Recap", i)
                                             for i in range(1, n + 1)]})
        long_n = n if self.all_long else min(self.n_long, n)
        out = [short_question(tag, i) for i in range(1, n - long_n + 1)]
        out += [long_question(tag, i) for i in range(n - long_n + 1, n + 1)]
        return json.dumps({"questions": out})


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


def every_question(data):
    return [*data.recap_questions, *data.challenge_questions,
            *(vq for s in data.sections
              for vq in (*s.questions, *s.homework_questions))]


SAMPLE_LONG = long_question("Addition", 1)["question"]
SAMPLE_SHORT = short_question("Addition", 1)["question"]

print("\nTHE FIXTURE REALLY DOES BREACH THE BUDGET")
for year, budget in (("Year 1", 12), ("Year 5", 35)):
    check(reading_load.word_count(SAMPLE_LONG) > budget,
          f"{year}: the long question is "
          f"{reading_load.word_count(SAMPLE_LONG)} words against a {budget} "
          f"word budget",
          "The fixture is inside the budget, so nothing below is measuring "
          "enforcement")
    check(reading_load.word_count(SAMPLE_SHORT) <= budget,
          f"{year}: the short one is "
          f"{reading_load.word_count(SAMPLE_SHORT)} words and fits")

print("\nAN OVER-LONG QUESTION DOES NOT REACH THE PAGE")
# Year 5: eight questions a subtopic, four of them the classwork floor, so a
# set with two stragglers in it has the room to lose both.
CAPTURED.clear()
client = StubClient("Year 5", n_long=2)
pipeline = BookletPipeline(client=client, retriever=NoRetriever(), max_workers=1)
data = pipeline.run_program("accelerate", "Year 5", "Kieran", subject="Maths")

over = [(reading_load.word_count(vq.question.question),
         vq.question.question[:60]) for vq in every_question(data)
        if reading_load.over_budget(vq.question.question, "Year 5",
                                    vq.question.subject or "Mathematics")]
check(not over,
      f"none of the {len(every_question(data))} questions in the booklet is "
      f"over the 35 word Year 5 budget",
      f"{len(over)} are, the longest at {max(over)[0] if over else 0} words: "
      f"{over[:1]}. The prompt asked for a reading age and the booklet "
      f"ignored it, which is where this started")

kept_short = sum(1 for vq in every_question(data)
                 if SHORT_MARK in vq.question.question)
check(kept_short >= MIN_NOW_YOU_TRY * 2,
      f"and the {kept_short} questions that FIT were all kept",
      "The enforcement is thinning the booklet rather than holding it to a "
      "reading age: a question inside the budget must never be dropped")

taught = [s for s in data.sections if s.questions]
thin = [(s.subtopic, len(s.questions)) for s in taught
        if len(s.questions) < MIN_NOW_YOU_TRY]
check(not thin,
      f"every taught subtopic still has at least {MIN_NOW_YOU_TRY} questions "
      f"under its mini-lesson ({[len(s.questions) for s in taught]})",
      f"{thin} fell through the floor the pricing page promises")

homework = sum(len(s.homework_questions) for s in data.sections)
check(homework > 0,
      f"and {homework} homework questions survived the drop",
      "Dropping two stragglers emptied Homework, so the budget is being paid "
      "for with the week's practice")

out = Path(tempfile.mkdtemp(prefix="folio-reading-")) / "booklet.pdf"
render_pdf(data, out)
text = "\n".join(page.extract_text() or ""
                 for page in pypdf.PdfReader(str(out)).pages).lower()
print(f"  rendered {out}")
check(LONG_MARK not in text,
      f'the word "{LONG_MARK}", which only ever appears in an over-budget '
      f"question, is nowhere in the printed PDF",
      f'"{LONG_MARK}" is on the page. The question was dropped from the data '
      f"and printed anyway, or it was never dropped at all")
check(SHORT_MARK in text,
      f'and "{SHORT_MARK}", which only appears in questions that fit, is',
      "The PDF has no fixture questions in it at all, so the assertion above "
      "passed on an empty page")

print("\nVALIDATION IS STILL BATCHED, ONE CALL A SET")
n_sets = client.set_calls
check(client.judge_calls <= n_sets,
      f"{client.judge_calls} judge calls for {n_sets} generated sets",
      f"{client.judge_calls} judge calls against {n_sets} sets means the "
      f"enforcement pass is grading question by question, which trades the "
      f"project's main lever on API cost for a word count")

print("\nDROPPING IS A REPAIR, NOT A DIET")
# The rule, on its own, before it is measured through a booklet: either every
# offender in a set can go, or none does. Taking the worst two out of six
# offenders leaves a child with four questions they still cannot read AND a
# parent with a thinner booklet, which is the reading load unchanged and the
# practice gone.
SIX = [Question(question=long_question("Addition", i)["question"],
                answer="37", working="24 + 13 = 37") for i in range(1, 7)]
FITS = [Question(question=short_question("Addition", i)["question"],
                 answer=str(24 * (i + 1)), working="x") for i in range(1, 7)]

kept, dropped = reading_load.hold_to_budget(
    FITS[:4] + SIX[:2], "Year 5", "Mathematics", keep_at_least=4)
check(len(kept) == 4 and len(dropped) == 2
      and all(SHORT_MARK in q.question for q in kept),
      "two stragglers in a set of six go, and the four that fit stay",
      f"{len(dropped)} dropped and {len(kept)} kept. The set could be made "
      f"readable by dropping only the questions that were unreadable, which "
      f"is the case this exists for")

kept, dropped = reading_load.hold_to_budget(
    FITS[:1] + SIX[:5], "Year 5", "Mathematics", keep_at_least=4)
check(len(kept) == 6 and not dropped,
      "but five offenders in a set of six are all kept, because only two of "
      "them could have gone",
      f"{len(dropped)} were dropped, leaving a set that is smaller and still "
      f"unreadable. The child gains nothing and loses the practice, which is "
      f"a worse booklet than the one the fault produced")

print("\nA WHOLESALE BREACH IS SURFACED, NOT HIDDEN AND NOT PAID FOR")
# Year 1 is where this was measured on the real booklets, and the hardest
# case: every question over the budget. Nothing can go without leaving the
# same unreadable booklet minus its homework, so the booklet is kept whole and
# the log is what gets it looked at. The alternative is quietly halving a six
# year old's booklet and calling the reading load fixed.
CAPTURED.clear()
client = StubClient("Year 1", n_long=0, all_long=True)
pipeline = BookletPipeline(client=client, retriever=NoRetriever(), max_workers=1)
data = pipeline.run_program("accelerate", "Year 1", "Sam", subject="Maths")

taught = [s for s in data.sections if s.questions]
check(bool(taught) and all(len(s.questions) >= MIN_NOW_YOU_TRY
                           for s in taught),
      f"the booklet still teaches {len(taught)} subtopic(s) with "
      f"{[len(s.questions) for s in taught]} questions under them",
      "A model that ignores the budget wholesale emptied the booklet. A "
      "mini-lesson with nothing under it is a defect this codebase has "
      "already paid for once")

homework = sum(len(s.homework_questions) for s in data.sections)
check(homework > 0,
      f"and its {homework} homework questions are all still there",
      "A six year old's homework was deleted to make a reading budget look "
      "kept. The questions left behind are exactly as long as they were")

dropped = CAPTURED.events("pipeline.drop_over_long_question")
surfaced = CAPTURED.events("pipeline.reading_load_over_budget")
check(not dropped,
      "nothing was dropped, because dropping could not have made this booklet "
      "readable",
      f"{len(dropped)} questions were cut out of a set where every question "
      f"breaches the budget. That is a smaller booklet, not a readable one")
check(bool(surfaced),
      f"and {len(surfaced)} warnings name what could not be",
      "The booklet goes out with questions a six year old cannot read and "
      "nothing anywhere says so. Support cannot answer a complaint about a "
      "booklet that reported no fault")
if surfaced:
    r = surfaced[0]
    check(all(hasattr(r, f) for f in ("where", "budget", "longest",
                                      "kept_over_budget")),
          f'the warning says where ({getattr(r, "where", "?")}), how long '
          f'({getattr(r, "longest", "?")} words), against what budget '
          f'({getattr(r, "budget", "?")}) and how many '
          f'({getattr(r, "kept_over_budget", "?")})',
          "The warning does not say which subtopic or how far over, so acting "
          "on it means regenerating the whole booklet to find out")

print(f"\n{_passed} passed, {len(_failed)} failed")
if _failed:
    for f in _failed:
        print("  -", f)
    sys.exit(1)
