"""Checks that a question reaches a student only on proof, never on assertion.

Everything else in the practice engine is plumbing. This is the part that
decides whether a Year 12 can trust what is on the screen three weeks before
an ATAR exam, and it rests on one rule: the answer a language model wrote down
is evidence of nothing. It is a cross-check and no more. What admits an
instance to the bank is a computer recomputing the answer from the question,
and then reading the question back to confirm it states the problem that was
solved.

Both gates are needed, and the second is the one people leave out.

  gate 1 catches a family whose answer is simply wrong
  gate 2 catches a family whose answer is right for a DIFFERENT question,
         which is what a renderer printing "35.0 g" over a payload saying
         3.50 produces: correct arithmetic, silently attached to the wrong
         numbers, shipped eight hundred times

There is a third thing this file is here to stop, and it is the most likely
way the engine actually degrades. Someone sees a discard rate of 100 percent
on a subtopic, decides verification is being fussy, and relaxes the gate to
accept an inconclusive verdict. An inconclusive verdict on senior material is
not a pass. The assertion below fails if that relaxation is ever made.

    PYTHONPATH=. python scripts/check_practice_instance_verification.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import senior_syllabus as S                    # noqa: E402
from booklet_gen.practice import instances, verify              # noqa: E402
from booklet_gen.practice.models import TemplateRow             # noqa: E402

PASSED = 0
TOTAL = 0


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


def template(**over) -> TemplateRow:
    base = dict(
        id="t-deriv", subject="methods",
        subtopic_id="methods.calculus.polynomial-derivatives",
        verify_kind="derivative", calculator="free", difficulty="medium",
        question_pattern="Differentiate y = {a}x^{n} + {b}x with respect to x.",
        answer_pattern="dy/dx = {a*n}x^{n-1} + {b}",
        working_pattern="Bring each index down and reduce it by one.",
        params={"a": {"range": [2, 9]}, "n": {"range": [2, 6]},
                "b": {"range": [1, 9]}},
        constraints=["a != b"],
        check_pattern={"kind": "derivative", "function": "{a}*x**{n} + {b}*x"},
        prompt_version="v1", syllabus_version="test",
        created_at=int(time.time()))
    base.update(over)
    return TemplateRow(**base)


def admitted(rows) -> int:
    return sum(1 for r in rows if r.verified and r.conclusive)


print("\n== a correct family is admitted, and the verifier says why ==")

good = instances.expand(template(), count=25, seed=7)
verdicts = [verify.admit(i) for i in good]
check(admitted(verdicts) == 25,
      f"{admitted(verdicts)} of 25 instances of a correct family admitted",
      "a family that is right and still cannot get through the gate means the "
      "bank fills from nothing and the feature ships empty")
check("=" in (verdicts[0].notes or ""),
      f"the verdict states the recomputation: {verdicts[0].notes[:70]}",
      "a pass with no working behind it is indistinguishable from a pass that "
      "checked nothing")

print("\n== the model's own answer is never what admits an instance ==")

# One term out. Everything else about this family is correct, including the
# question, so nothing but an independent recomputation can catch it.
wrong = instances.expand(
    template(id="t-wrong", answer_pattern="dy/dx = {a*n}x^{n-1} + {b+1}"),
    count=25, seed=7)
verdicts = [verify.admit(i) for i in wrong]
check(admitted(verdicts) == 0,
      f"a family whose stated answer is out by one term admits "
      f"{admitted(verdicts)} of 25",
      "the stored answer is being trusted, so whatever the model asserts "
      "reaches the student with a tick beside it")
check("but the answer says" in (verdicts[0].notes or ""),
      f"and the rejection names the discrepancy: {verdicts[0].notes[:80]}",
      "a rejection nobody can read is a subtopic nobody can repair")

# Wrong in a way that survives a careless glance: the right shape, the right
# leading term, an index that is one too high.
sneaky = instances.expand(
    template(id="t-sneaky", answer_pattern="dy/dx = {a*n}x^{n} + {b}"),
    count=15, seed=7)
check(admitted([verify.admit(i) for i in sneaky]) == 0,
      "a family with the right shape and a wrong index admits none",
      "this is the error a human proofreader misses, which is the whole "
      "reason the check is symbolic rather than a person reading samples")

print("\n== the question has to state the problem that was solved ==")

# Gate 2 alone. The answer is right for the payload, and the payload is right,
# but the printed question shows different numbers. Arithmetic cannot catch
# this; only reading the question back can.
mismatched = instances.expand(
    template(id="t-mismatch",
             question_pattern="Differentiate y = {a+1}x^{n} + {b}x with "
                              "respect to x."),
    count=15, seed=7)
verdicts = [verify.admit(i) for i in mismatched]
check(admitted(verdicts) == 0,
      f"a family whose question prints different numbers from its payload "
      f"admits {admitted(verdicts)} of 15",
      "the student is shown one question and marked against another, which is "
      "the failure that ships hundreds of times before anybody notices")

# A question the extractor cannot read at all is a question nothing verified.
opaque = instances.expand(
    template(id="t-opaque",
             question_pattern="Consider the function described above and "
                              "differentiate it. Use a={a}, n={n}, b={b}."),
    count=10, seed=7)
check(admitted([verify.admit(i) for i in opaque]) == 0,
      "a question the checker cannot read back is refused, not assumed right",
      "an unreadable question passing on gate 1 alone means the bank fills "
      "with items only half of which were ever checked")

print("\n== an inconclusive verdict is not a pass ==")

# The relaxation that will be tempting one day, when a subtopic shows a 100
# percent discard rate and somebody decides the gate is being fussy.
class _Inconclusive:
    verified = True
    conclusive = False
    notes = "could not settle this"


saved = verify._recompute_methods
try:
    verify._recompute_methods = lambda *a, **k: _Inconclusive()
    verdict = verify.admit(good[0])
    relaxed = bool(verdict.verified and verdict.conclusive)
finally:
    verify._recompute_methods = saved

check(not relaxed,
      "an instance the verifier could not settle is not admitted",
      "treating 'cannot tell' as 'fine' is how a wrong answer reaches a "
      "student three weeks before an ATAR exam with a tick beside it")

print("\n== every stored item can be re-derived from what was stored ==")

# The property that makes bank rot detectable. If a template is edited a year
# from now, this is what notices that the questions it produced no longer
# follow from it, instead of them sitting in the bank looking fine.
again = instances.expand(template(), count=25, seed=7)
identical = all(
    a.variant_key == b.variant_key and a.question == b.question
    and a.answer == b.answer and a.check == b.check
    for a, b in zip(good, again))
check(identical,
      "the same template and seed re-derive byte-identical instances",
      "without this, nothing can tell a question that was verified when it "
      "was made from one whose template has since changed under it")

check(len({i.variant_key for i in good}) == len(good),
      f"all {len(good)} instances of one family are distinct",
      "a family serving the same parameters twice wastes bank depth and puts "
      "a repeat in front of the student")

print("\n== a family too small to be worth an LLM call is refused ==")

tiny = template(id="t-tiny", params={"a": {"range": [2, 3]},
                                     "n": {"range": [2, 3]},
                                     "b": {"range": [1, 2]}},
                constraints=[])
check(instances.space_size(tiny) < instances.MIN_SPACE,
      f"a family with {instances.space_size(tiny)} combinations is under the "
      f"floor of {instances.MIN_SPACE}",
      "a family this small is exhausted in one sitting and then met again all "
      "week, which is the staleness the whole template design exists to avoid")

impossible = template(id="t-impossible", constraints=["a != a"])
check(instances.space_size(impossible) == 0,
      "a family whose constraints exclude everything reports an empty space",
      "expanding it would either loop or produce nothing while reporting "
      "success")

print("\n== coverage, measured rather than claimed ==")

# The syllabus says which topics are checkable in principle. This says which
# ones have code behind them today. The second number is the one that decides
# what a student can actually grind, and it is much smaller.
cov = verify.coverage()
for subject, key in S.SUBJECT_KEYS.items():
    row = cov[key]
    print(f"                {key:10} {row['fillable']:3} fillable of "
          f"{row['bankable']:3} bankable, {row['subtopics']:3} in the course")

total = sum(row["fillable"] for row in cov.values())
check(total >= 30,
      f"{total} subtopics across both subjects can actually be filled",
      "below thirty there is not enough breadth to sell a grind tool, and the "
      "picker would be mostly topics that serve nothing")

for key, row in cov.items():
    check(row["fillable"] <= row["bankable"] <= row["subtopics"],
          f"{key} coverage is internally consistent",
          "more subtopics are fillable than are bankable, so one of the two "
          "gates is not being applied")

unfillable = [s.id for s in S.METHODS if not verify.fillable(s.id)]
print(f"\n                {len(unfillable)} Methods subtopics have no checker "
      f"yet, for example: {', '.join(unfillable[:3])}")
check(all(S.subtopic(sid) is not None for sid in unfillable),
      "and each of them is still a real subtopic in the picker",
      "a topic the course contains has been dropped from the tree rather than "
      "reported as not yet stocked")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
