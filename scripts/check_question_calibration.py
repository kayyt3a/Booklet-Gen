"""Checks the maths prompts scale a question to the year and vary its shape.

Five real Mathematics booklets (Years 1, 3, 5, 7, 9) were generated from
production on 2026-08-21 and measured. Three things were wrong with the
questions, and all three trace back to what the prompts ask for.

  READING LOAD DID NOT MOVE WITH THE YEAR. Mean question length ran 86, 93,
  105, 116, 100 characters across Years 1 to 9: flat, and the Year 1 end of it
  far too heavy. A six year old was issued "Which is longer: a line that is 5
  paperclips long or a line that is 8 paperclips long?", eighteen words with
  two relative clauses. A child who can compare 5 and 8 still fails that, on
  decoding. Nothing in `question_generator_maths.txt` said a word about
  reading load, so the model wrote every year at its own default register.

  WORD PROBLEMS THINNED OUT AS THE YEAR ROSE. The share of questions set in a
  real situation fell from about 40% at Year 1 to 20% at Year 9, which is
  backwards: senior maths is where application matters and the papers the
  student will actually sit are heavily worded. Year 9 class work ran "Solve
  for x: 2(x + 4) = 16", "5(x - 3) = 2x + 3", "3(2x - 1) = 4(x + 1)",
  "2(3x + 5) = 4(x - 2)". The prompt said "mix straight computation, word
  problems, and explain-your-reasoning style", which is a wish, not a quota.

  SETS WERE FORMULAIC. The four above are one question asked four times. Year
  5 factors ran list-the-factors / list-the-multiples / list-the-factors /
  list-the-multiples. The prompt already said "vary the FORM, not just the
  numbers", so the fix is not to say it again: it is to name the shapes and
  count them.

WHAT THIS CAN AND CANNOT SHOW. A prompt is not a function, so this pins the
half that is deterministic: that the prompts DEMAND these properties in a form
a reader can check and a script can parse, that the numbers ramp with the year
instead of sitting flat, and that the questions which actually shipped are
measured as failures by the rule now written down. Whether the model complies
is a question only a generation run can answer, and this script says so out
loud rather than passing quietly. With GEMINI_API_KEY set and
FOLIO_CHECK_GENERATE=1 it makes two real generation calls, one Year 1 and one
Year 9, and measures them.

    PYTHONPATH=. python scripts/check_question_calibration.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents._shared import PROMPT_DIR  # noqa: E402

_passed = 0


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print("  ok:", msg)


def prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


PRACTICE = prompt("question_generator_maths.txt")
CHALLENGE = prompt("challenge_generator_maths.txt")
LESSON = prompt("intro_writer_maths.txt")

BANDS = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]


def words(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
print("\nTHE PROMPT NAMES A WORD BUDGET FOR EVERY YEAR BAND")

practice_budget = {
    (int(a), int(b)): int(n)
    for a, b, n in re.findall(
        r"Years (\d+)-(\d+)\b[^\n]*?AT MOST (\d+) WORDS", PRACTICE)
}
assert set(practice_budget) == set(BANDS), (
    "question_generator_maths.txt does not give a word budget for every year "
    f"band, only for {sorted(practice_budget)}. A band with no number gets the "
    "model's default register, which is what produced an eighteen word Year 1 "
    "question.")
ok(f"practice budgets, by band: {[practice_budget[b] for b in BANDS]}")

challenge_budget = {
    (int(a), int(b)): int(n)
    for n, a, b in re.findall(
        r"(\d+) words?(?: a question)? in Years (\d+)-(\d+)", CHALLENGE)
}
assert set(challenge_budget) == set(BANDS), (
    "challenge_generator_maths.txt sets no per-band budget, so the Final "
    "Challenge stays free to hand a Year 1 the thirty-four word question that "
    f"shipped. Found {sorted(challenge_budget)}")
ok(f"Final Challenge budgets, by band: {[challenge_budget[b] for b in BANDS]}")

for label, budget in (("practice", practice_budget),
                      ("Final Challenge", challenge_budget)):
    seq = [budget[b] for b in BANDS]
    assert seq == sorted(seq) and len(set(seq)) == len(seq), (
        f"the {label} budgets do not rise strictly with the year: {seq}. A flat "
        "table is the defect written down rather than fixed.")
    assert seq[-1] >= 4 * seq[0], (
        f"the {label} ramp is too shallow to change anything: {seq[0]} words at "
        f"Year 1 against {seq[-1]} at Year 9. The shipped booklets were within "
        "16 characters of each other end to end.")
ok("both budgets rise strictly with the year, and by a wide enough margin")

assert practice_budget[(1, 2)] <= 15, (
    f"a Year 1 budget of {practice_budget[(1, 2)]} words is not a Year 1 "
    "budget; a six year old reads a handful of words at a time")
ok(f"Years 1-2 are capped at {practice_budget[(1, 2)]} words a question")

# The cap alone lets a model write every question at the ceiling. An average
# is what actually moves the mean the founder measured.
averages = [int(n) for n in re.findall(r"averaging (\d+)", PRACTICE)]
assert len(averages) == len(BANDS) and averages == sorted(averages), (
    f"the prompt gives caps but no rising average: {averages}. A cap is a "
    "ceiling nobody has to approach, and the number that was measured flat "
    "was the mean.")
ok(f"a target average is set for each band too: {averages}")


# ---------------------------------------------------------------------------
print("\nTHE RULE BITES ON THE QUESTIONS THAT ACTUALLY SHIPPED")

# Verbatim from lleyton/kieran booklets generated 2026-08-21.
SHIPPED_YEAR_1 = [
    "Which is longer: a line that is 5 paperclips long or a line that is 8 "
    "paperclips long?",
    "A basket has 10 apples and 6 oranges. How many pieces of fruit are there "
    "altogether?",
    "A box contains 22 chocolates. If you eat half of them on Monday and half "
    "of the remaining chocolates on Tuesday, how many chocolates did you eat "
    "on Monday?",
]
over = [q for q in SHIPPED_YEAR_1 if words(q) > practice_budget[(1, 2)]]
assert len(over) == len(SHIPPED_YEAR_1), (
    "a question that shipped to a six year old still fits inside the new Year "
    "1 budget, so the budget is not tight enough to have changed anything: "
    f"{[q for q in SHIPPED_YEAR_1 if q not in over]}")
ok(f"all {len(over)} sampled Year 1 questions blow the new budget "
   f"({[words(q) for q in SHIPPED_YEAR_1]} words against "
   f"{practice_budget[(1, 2)]})")

REWRITTEN = ["Which line is longer, A or B?",
             "10 apples and 6 oranges. How many fruit altogether?"]
assert all(words(q) <= practice_budget[(1, 2)] for q in REWRITTEN), (
    "the budget is so tight that a reasonable Year 1 question cannot be "
    "written inside it")
ok("and a plain rewrite of the same maths fits inside it")

SHIPPED_YEAR_1_CHALLENGE = (
    "Elias has a box of blocks. He counts 4 groups of 10 blocks and 7 extra "
    "blocks. He then subtracts 12 blocks to build a tower. How many blocks "
    "are left in the box?")
assert words(SHIPPED_YEAR_1_CHALLENGE) > challenge_budget[(1, 2)], \
    "the Final Challenge budget does not catch the Year 1 question that shipped"
ok(f"the Year 1 Final Challenge question that shipped "
   f"({words(SHIPPED_YEAR_1_CHALLENGE)} words) now breaches its budget")


# ---------------------------------------------------------------------------
print("\nTHE REASON IS GIVEN, NOT JUST THE NUMBER")

# A bare table invites the model to pad a Year 1 question up to the ceiling
# with an extra clause. The prompts have to say why the ceiling is there, and
# in particular that the acceleration rule does not touch it: the whole product
# writes a year above, and a model applying that to the prose lands exactly
# where these booklets landed.
for name, body in (("question_generator_maths.txt", PRACTICE),
                   ("challenge_generator_maths.txt", CHALLENGE),
                   ("intro_writer_maths.txt", LESSON)):
    assert re.search(r"reading age", body), (
        f"{name} sets a length but never says the acceleration rule stops at "
        "the maths. A model lifting the content a year lifts the sentences "
        "with it.")
ok("all three maths writers are told acceleration never lifts the reading age")

assert re.search(r"comprehension test|reading test", PRACTICE), (
    "the practice prompt does not name the failure mode, so a long question "
    "still reads to the model as a rich question")
ok("the practice prompt names what an over-long question does to a child")

# The escape hatch for the youngest years: a situation too long to state is a
# situation to draw. Without it the budget just deletes context from Year 1.
assert re.search(r"situation to DRAW", PRACTICE), (
    "nothing tells the youngest bands to picture a situation they have no "
    "words to describe, so the budget will strip Year 1 down to bare sums")
ok("Years 1-2 are told to draw a situation rather than describe it")


# ---------------------------------------------------------------------------
print("\nWHAT ONLY A GENERATION RUN CAN SETTLE")

if not (os.environ.get("GEMINI_API_KEY") and
        os.environ.get("FOLIO_CHECK_GENERATE")):
    print("  SKIP: whether the model OBEYS these budgets cannot be checked "
          "from the prompt text.")
    print("        Everything above is a statement about what the prompts ask "
          "for, not about")
    print("        what comes back. To measure it, set GEMINI_API_KEY and "
          "FOLIO_CHECK_GENERATE=1")
    print("        and re-run: this makes two live question-generator calls "
          "(Year 1 and Year 9)")
    print("        and reports the mean question length of each.")
else:
    from booklet_gen.agents.question_generator import QuestionGeneratorAgent
    from booklet_gen.llm import get_client
    from booklet_gen.schemas import Subtopic

    agent = QuestionGeneratorAgent(get_client(), questions_per_subtopic=6)
    measured = {}
    for year, topic, sub in (
            ("Year 1", "Addition and Subtraction",
             Subtopic(name="Adding and subtracting within 20")),
            ("Year 9", "Algebraic Expansion and Factorisation",
             Subtopic(name="Solving linear equations involving brackets"))):
        qs = agent.generate("Mathematics", year, topic, sub)
        lens = [words(q.question) for q in qs.questions]
        measured[year] = sum(lens) / len(lens)
        print(f"  {year}: {len(lens)} questions, mean {measured[year]:.1f} "
              f"words, longest {max(lens)}")
    assert measured["Year 1"] < measured["Year 9"], (
        "a live run still writes Year 1 at Year 9 length, so the budgets are "
        "written down but not obeyed")
    ok("a live run writes Year 1 shorter than Year 9")

print(f"\n{_passed} CALIBRATION CHECKS PASSED")
sys.exit(0)
