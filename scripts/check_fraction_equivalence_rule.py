"""Checks the maths lesson prompt teaches the equivalence RULE, not just the fact.

The shipped sample on the landing page (a Year 5 comparing-fractions lesson)
told the student "multiplying the top and bottom by the same number does not
change what a fraction is worth" and stopped there. That is true, and it is
not usable: it describes a property, not an action. A student staring at 3/4
and 5/8 mid-question needs the sentence they act on, not the sentence that
explains why acting on it is safe.

    PYTHONPATH=. python scripts/check_fraction_equivalence_rule.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents._shared import PROMPT_DIR

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nTHE MATHS LESSON PROMPT DEMANDS AN ACTIONABLE RULE, NOT JUST A FACT")

body = (PROMPT_DIR / "intro_writer_maths.txt").read_text(encoding="utf-8")

assert "whatever you do to the bottom" in body.lower(), (
    "the prompt no longer tells the writer to phrase equivalence as an "
    "instruction the student can act on. Every fraction lesson goes back to "
    "stating the fact ('multiplying top and bottom by the same number does "
    "not change the value') and leaving the student to work out what to DO "
    "with that on their own")
ok("the prompt requires the 'whatever you do to the bottom, you do to the "
   "top' rule, not just the equivalence fact")

assert "unlike denominators" in body.lower() or "common denominator" in body.lower(), (
    "the rule is not tied to the subtopics it actually matters for "
    "(comparing, ordering, adding or subtracting unlike-denominator "
    "fractions), so a reviewer has no way to tell it was meant to fire there")
ok("the rule is scoped to the subtopics where a common denominator is needed")

print(f"\nALL {_passed} FRACTION EQUIVALENCE PROMPT CHECKS PASSED")
sys.exit(0)
