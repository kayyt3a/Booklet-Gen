#!/usr/bin/env python3
"""Check that a verified mark means the answer was actually checked.

The failure this exists to prevent: the direct-computation path used to scan a
question for ANY window of tokens that sympified, and pass the question if any
of them equalled the answer. That is an existence check, not a verification,
and it blessed the two commonest wrong answers in primary maths (stopping one
term early, and quoting an operand as the result). Because the pipeline
short-circuited the LLM judge on a sympy pass, every one of those was final.

Both directions are tested, and both matter:

  REJECT  a wrong answer must not keep a check mark
  VERIFY  a correct answer must keep one (a validator that fails everything is
          just as useless, and it drives the regeneration loop into a wall)
  DEFER   a question the checker cannot read whole must say so, not guess. It
          then goes to the LLM judge, which is the only correct outcome for a
          word problem whose arithmetic is not written down.

Usage:  PYTHONPATH=. python scripts/check_answer_verification.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The checks below deliberately trigger warnings; the report is the output.
logging.disable(logging.CRITICAL)

from booklet_gen.agents.llm_judge import LLMJudgeValidator      # noqa: E402
from booklet_gen.agents.validator import (                      # noqa: E402
    SympyValidator, ValidationResult, answers_conflict)
from booklet_gen.pipeline import BookletPipeline                 # noqa: E402
from booklet_gen.schemas import Question                        # noqa: E402

VERIFY, REJECT, DEFER = "VERIFY", "REJECT", "DEFER"

# The three questions that were confirmed to return "verified" with a wrong or
# unchecked answer before this was fixed. None of them may ever be VERIFY
# again. Two of them must land on DEFER rather than REJECT, and that is the
# right outcome: sympy cannot read the arithmetic a word problem asks for, so
# claiming the answer is wrong would be as unfounded as claiming it is right.
# They go to the LLM judge, which can.
FALSE_POSITIVES = [
    ("Calculate 11/15 - 3/15 - 2/15.", "8/15"),
    ("Subtract 4/9 from 8/9.", "4/9"),
    ("Sam had 20 apples and gave away 5 + 3 of them. How many are left?", "8"),
]

# (question, answer, expectation, note)
CASES = [
    # ---- the three confirmed false positives -------------------------------
    ("Calculate 11/15 - 3/15 - 2/15.", "8/15", REJECT,
     "stopped one term early; 11/15 - 3/15 used to match as a window"),
    ("Subtract 4/9 from 8/9.", "4/9", DEFER,
     "used to match an operand; the value happens to be right, but nothing "
     "here checked the subtraction"),
    ("Sam had 20 apples and gave away 5 + 3 of them. How many are left?", "8", DEFER,
     "wrong (12), but the sum to do is not written down: the judge's call"),

    # ---- the same questions answered correctly -----------------------------
    ("Calculate 11/15 - 3/15 - 2/15.", "6/15", VERIFY, "correct, unsimplified"),
    ("Calculate 11/15 - 3/15 - 2/15.", "2/5", VERIFY, "correct, simplified"),

    # ---- plain computations that must keep their mark ----------------------
    ("Calculate 3/8 + 2/8.", "5/8", VERIFY, ""),
    ("Calculate 3/8 + 2/8. Give your answer in simplest form.", "5/8", VERIFY,
     "trailing instruction does not change the value"),
    ("What is 7 x 8?", "56", VERIFY, "x between numbers is multiplication"),
    ("Work out 45 - 18.", "27", VERIFY, ""),
    ("Calculate 2 + 3 x 4.", "14", VERIFY, "order of operations"),
    ("Simplify 4/8.", "1/2", VERIFY, ""),
    ("Find the total of 1,200 + 300.", "1500", VERIFY, "thousands separator"),
    ("Calculate 100 / 4.", "25", VERIFY, ""),
    ("Calculate 8 - 3.", "5 apples", VERIFY, "answer carrying a unit"),
    ("Calculate 12 x 5.", "60", VERIFY, ""),

    # ---- wrong answers to those same computations --------------------------
    ("Calculate 3/8 + 2/8.", "5/16", REJECT, "added the denominators"),
    ("What is 7 x 8?", "54", REJECT, ""),
    ("Work out 45 - 18.", "37", REJECT, "borrowing slip"),
    ("Calculate 2 + 3 x 4.", "20", REJECT, "left to right instead of BODMAS"),
    ("Calculate 12 x 5.", "17", REJECT, "added instead of multiplied"),

    # ---- equations ---------------------------------------------------------
    ("Solve for x: 2x + 3 = 11", "x = 4", VERIFY, "implicit multiplication"),
    ("Solve for x: 2x + 3 = 11", "x = 5", REJECT, ""),
    ("Solve 5n = 45", "n = 9", VERIFY, ""),
    ("Solve 5n = 45", "n = 8", REJECT, ""),
    ("Solve x^2 = 9", "x = 3 or x = -3", VERIFY, "two roots"),

    # ---- must DEFER: the sum a student must do is not written down ---------
    ("A bag has 3 red and 5 blue balls. Find P(red).", "3/8", DEFER,
     "the answer is not any computation printed in the question"),
    ("Tom has 5 apples and buys 3 + 2 more. How many now?", "10", DEFER,
     "correct: the printed sum is not the whole task"),
    ("There are 24 students. 1/3 play soccer. How many play soccer?", "8", DEFER,
     "correct: 1/3 is a quoted fraction, not a division to perform"),
    ("Calculate 3 + 4. Then double the result.", "14", DEFER,
     "correct: a step follows the printed sum"),
    ("Calculate 10 / 3. Round to 1 decimal place.", "3.3", DEFER,
     "correct: rounding changes the expected value"),
    ("Order these from smallest: 0.5, 1/4, 0.75", "1/4, 0.5, 0.75", DEFER,
     "correct: a list of values, not a sum"),
    ("Calculate 25% of 80.", "20", DEFER, "correct: percent is not parsed"),
    ("Round 3.7 to the nearest whole number.", "4", DEFER, "correct"),
    ("Calculate the perimeter of a rectangle 5 cm by 3 cm.", "16 cm", DEFER,
     "correct: no computation is printed"),
    ("Solve for x: 4x = 20", "x = 5 cm", DEFER,
     "correct: an answer we cannot parse is not a wrong answer"),
    ("Explain why 1/2 is bigger than 1/4.", "Halves are larger pieces.", DEFER,
     "prose answer"),
]

# (proposed answer, the judge's own answer, should the mark be withheld)
CONFLICT_CASES = [
    ("8/15", "6/15", True, "judge solved it differently and passed it anyway"),
    ("6/15", "2/5", False, "same value, written differently"),
    ("0.5", "1/2", False, "same value"),
    ("20", "20 apples", False, "same value with a unit"),
    ("75", "80", True, "the shipped Q63 defect"),
    ("Sydney", "Melbourne", False, "not numeric, not ours to call"),
    ("8", "unanswerable", False, "handled by the judge's own verdict, not here"),
    ("12", "", False, "no solve returned, nothing to compare"),
]


def _describe(result: ValidationResult) -> str:
    if result.verified and result.conclusive:
        return VERIFY
    if not result.verified and result.conclusive:
        return REJECT
    return DEFER


class _CountingJudge:
    """Stands in for the LLM judge so routing can be tested without an API."""

    def __init__(self, verdict: bool = True):
        self.calls = 0
        self.batches = 0
        self.seen: list[str] = []
        self._verdict = verdict

    def validate(self, subject, year_level, q, chunks=None):
        self.calls += 1
        self.seen.append(q.question)
        return ValidationResult(self._verdict, "stub judge")

    def validate_batch(self, subject, year_level, questions, chunks=None):
        self.batches += 1
        self.calls += 1
        self.seen.extend(q.question for q in questions)
        return [ValidationResult(self._verdict, "stub judge(batch)") for _ in questions]


class _StubClient:
    """Returns a canned judge response, so the judge's own logic is testable."""

    def __init__(self, payload: str):
        self.payload = payload

    def complete(self, system, user, tier="strong", temperature=0.0):
        return self.payload


def _routing_pipeline(judge):
    """A Pipeline with only the validation collaborators wired up.

    Built without __init__ on purpose: constructing a real one needs an API
    key, and validation routing does not.
    """
    from booklet_gen.agents.reasoning_validator import ReasoningValidator
    p = object.__new__(BookletPipeline)
    p._sympy = SympyValidator()
    p._reasoning = ReasoningValidator()
    p._judge = judge
    return p


def main() -> int:
    validator = SympyValidator()
    passed = total = 0

    print("== the three confirmed false positives ==")
    for question, answer in FALSE_POSITIVES:
        total += 1
        result = validator.validate(Question(question=question, answer=answer, working=""))
        good = not result.verified
        passed += good
        print(f"{'ok  ' if good else '*** FAIL ***':<14}not verified={not result.verified!s:<6}"
              f"{question[:48]:<50} ans={answer}")
        if not good:
            print(f"{'':<14}{(result.notes or '')[:110]}")

    print("\n== sympy validator ==")
    for question, answer, expected, note in CASES:
        total += 1
        result = validator.validate(Question(question=question, answer=answer, working=""))
        got = _describe(result)
        good = got == expected
        passed += good
        print(f"{'ok  ' if good else '*** FAIL ***':<14}{expected:<7}got={got:<7}"
              f"{question[:44]:<46} ans={answer[:16]:<18}{note}")
        if not good:
            print(f"{'':<14}{(result.notes or '')[:110]}")

    print("\n== judge held to its own solve ==")
    for proposed, solved, should_withhold, note in CONFLICT_CASES:
        total += 1
        got = answers_conflict(proposed, solved)
        good = got == should_withhold
        passed += good
        print(f"{'ok  ' if good else '*** FAIL ***':<14}"
              f"answer={proposed[:12]:<14}judge solved={solved[:12]:<14}"
              f"withhold={got!s:<6}{note}")

    # A judge that passes an answer it did not itself reach must not hand out
    # a check mark. This is the 63-of-63 booklet's failure mode in miniature.
    total += 1
    judge = LLMJudgeValidator(_StubClient(
        '{"solved_answer": "6/15", "verified": true, "reason": "looks right"}'))
    verdict = judge.validate(
        "Mathematics", "Year 5",
        Question(question="Calculate 11/15 - 3/15 - 2/15.", answer="8/15", working=""))
    good = not verdict.verified
    passed += good
    print(f"\n{'ok  ' if good else '*** FAIL ***':<14}"
          f"judge said verified but solved it as 6/15 -> mark withheld={not verdict.verified}")
    if not good:
        print(f"{'':<14}{verdict.notes}")

    # And it must still pass an answer it did reach.
    total += 1
    judge = LLMJudgeValidator(_StubClient(
        '{"solved_answer": "2/5", "verified": true, "reason": "correct"}'))
    verdict = judge.validate(
        "Mathematics", "Year 5",
        Question(question="Calculate 11/15 - 3/15 - 2/15.", answer="6/15", working=""))
    good = verdict.verified
    passed += good
    print(f"{'ok  ' if good else '*** FAIL ***':<14}"
          f"judge solved 2/5 and passed 6/15 (same value) -> verified={verdict.verified}")

    print("\n== pipeline routing ==")
    routing = [
        # (question, answer, judge verdict, expect final verified, expect judge called, note)
        ("Calculate 11/15 - 3/15 - 2/15.", "8/15", True, False, False,
         "a proven-wrong answer is final: the judge cannot overturn sympy"),
        ("Calculate 3/8 + 2/8.", "5/8", False, True, False,
         "a proven-right answer is final and costs no API call"),
        ("Calculate 3 + 4. Then double the result.", "14", True, True, True,
         "partial match: judge decides, and here it verifies"),
        ("Calculate 3 + 4. Then double the result.", "14", False, False, True,
         "partial match: judge decides, and here it rejects"),
        ("A bag has 3 red and 5 blue balls. Find P(red).", "3/8", True, True, True,
         "nothing to check symbolically, so the judge grades it"),
    ]
    for question, answer, verdict, want_verified, want_judged, note in routing:
        total += 1
        judge = _CountingJudge(verdict)
        pipe = _routing_pipeline(judge)
        out = pipe._validate_many(
            "Mathematics", "Year 5",
            [Question(question=question, answer=answer, working="")])
        good = out[0].verified == want_verified and (judge.calls > 0) == want_judged
        passed += good
        print(f"{'ok  ' if good else '*** FAIL ***':<14}"
              f"verified={out[0].verified!s:<6}judged={judge.calls > 0!s:<6}"
              f"{question[:40]:<42}{note}")
        if not good:
            print(f"{'':<14}{(out[0].notes or '')[:110]}")

    # Batching is a standing project decision: one judge call per subtopic, not
    # one per question. Regressing it would quietly multiply the API bill.
    total += 1
    judge = _CountingJudge(True)
    pipe = _routing_pipeline(judge)
    batch = [
        Question(question="A bag has 3 red and 5 blue balls. Find P(red).",
                 answer="3/8", working=""),
        Question(question="Round 3.7 to the nearest whole number.", answer="4", working=""),
        Question(question="Calculate 25% of 80.", answer="20", working=""),
        Question(question="Calculate 3/8 + 2/8.", answer="5/8", working=""),
    ]
    out = pipe._validate_many("Mathematics", "Year 5", batch)
    good = judge.batches == 1 and judge.calls == 1 and len(judge.seen) == 3
    passed += good
    print(f"{'ok  ' if good else '*** FAIL ***':<14}"
          f"3 of 4 questions went to the judge in {judge.batches} batched call")

    # The whole point of the exercise: a set is no longer 100 percent verified.
    total += 1
    judge = _CountingJudge(True)
    pipe = _routing_pipeline(judge)
    mixed = [
        Question(question="Calculate 11/15 - 3/15 - 2/15.", answer="8/15", working=""),
        Question(question="Calculate 3/8 + 2/8.", answer="5/8", working=""),
        Question(question="What is 7 x 8?", answer="54", working=""),
        Question(question="A bag has 3 red and 5 blue balls. Find P(red).",
                 answer="3/8", working=""),
    ]
    out = pipe._validate_many("Mathematics", "Year 5", mixed)
    verified = sum(1 for r in out if r.verified)
    good = verified == 2
    passed += good
    print(f"{'ok  ' if good else '*** FAIL ***':<14}"
          f"{verified} of {len(mixed)} verified with a rubber-stamping judge "
          "(2 wrong answers refused)")

    # The working-consistency guard in agents/consistency.py is the last word,
    # and stays that way: a symbolic proof of the answer does not excuse an
    # answer key whose own working says something else. These two checks exist
    # so the layers are known to compose rather than override each other.
    print("\n== composition with the working-consistency guard ==")
    composition = [
        ("6/15", "11/15 - 3/15 = 8/15. Then 8/15 - 2/15 = 6/15.", True,
         "sympy verified and the working agrees: mark kept"),
        ("6/15", "8/15 - 2/15 = 5/15. Wait, recalculating: 6/15.", False,
         "sympy verified but the working second-guesses itself: mark withheld"),
    ]
    for answer, working, want, note in composition:
        total += 1
        q = Question(question="Calculate 11/15 - 3/15 - 2/15.", answer=answer, working=working)
        symbolic = SympyValidator().validate(q)
        final = BookletPipeline._trusted(q, symbolic.verified)
        good = symbolic.verified and final == want
        passed += good
        print(f"{'ok  ' if good else '*** FAIL ***':<14}"
              f"sympy={symbolic.verified!s:<6}final={final!s:<6}{note}")

    print(f"\n{passed}/{total} behaved as expected")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
