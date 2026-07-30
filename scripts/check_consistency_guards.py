#!/usr/bin/env python3
"""Check the answer-consistency and diagram-reconciliation guards.

Every case here is drawn from a real generated booklet. The PASS cases are
genuine answer-key entries that must keep their verified mark; the REJECT
cases are defects that shipped. Both directions matter: a guard that rejects
good questions is as damaging as one that misses bad ones.

Usage:  python scripts/check_consistency_guards.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents.consistency import (          # noqa: E402
    answer_is_trustworthy, reconcile_diagram_spec)

# (answer, working, should_be_trusted)
ANSWER_CASES = [
    # Real entries from pages 19-25 that must keep their mark.
    ("1/2", "Divide by the greatest common divisor, which is 4.\n4 / 4 = 1\n8 / 4 = 2", True),
    ("1/3", "Divide the numerator and denominator by 3.\n3 / 3 = 1\n9 / 3 = 3", True),
    ("3/4", "Divide both by 3.\n9 / 3 = 3\n12 / 3 = 4", True),
    ("5/7", "The denominators are the same, so add the numerators.\n2 + 3 = 5\nKeep the denominator as 7.", True),
    ("8000", "Volume = 40 cm x 20 cm x 10 cm.\n40 x 20 = 800. 800 x 10 = 8000.", True),
    ("56", "Volume = 7 * 4 * 2\nVolume = 28 * 2\nVolume = 56", True),
    ("125", "150 = 6 * side2.\nside2 = 25.\nside = 5.\nVolume = 5^3 = 125.", True),
    ("x = 9", "Divide both sides by 5\nx = 45 / 5\nx = 9", True),
    ("3/8 of the pizza is left", "2/8 + 3/8 = 5/8. Then 8/8 - 5/8 = 3/8.", True),

    # Q63, page 25 of the shipped booklet: stated 75 over working reaching 80.
    ("75", "New volume = 5 x 4 x 4 = 80. Wait, recalculating: 60 + 20 = 80. "
           "Correction: 80 cubic metres.", False),
    # Self-correction phrases that a trailing \b silently disabled.
    ("20", "Correction: the answer is 20.", False),
    ("12", "Actually, it is 12.", False),
    ("6", "My mistake, the answer is 6.", False),
    # A fraction answer contradicted by its own working.
    ("5/8", "3/8 + 2/8 = 6/8. So the answer is 6/8.", False),
]

# (spec, question, expect_changed, note)
DIAGRAM_CASES = [
    ({"type": "cuboid", "length": 4, "width": 2, "height": 1, "unit": "cm"},
     "A rectangular tank is 40 cm long, 20 cm wide, and 10 cm high.",
     True, "page 9: drawing was a tenth of the stated size"),
    ({"type": "cuboid", "length": 4, "width": 2, "height": 1, "unit": "dm"},
     "A fish tank has a base 40 cm long and 20 cm wide, filled to a height of 10 cm.",
     True, "page 17: question in cm, drawing in dm"),
    ({"type": "cuboid", "length": 7, "width": 4, "height": 2, "unit": "m"},
     "A pool is a rectangular prism with a length of 7 m, a width of 4 m, and a depth of 2 m.",
     False, "already correct, must not be touched"),
    ({"type": "cuboid", "length": 100, "width": 50, "height": 20, "unit": "cm"},
     "A trough is 1 metre long, 50 cm wide, and 20 cm deep.",
     False, "mixed units: overriding would invent a 1m x 50m x 20m trough"),
    ({"type": "cuboid", "length": 3, "width": 3, "height": 3, "unit": "cm"},
     "A solid object is made by stacking cubes. How many cubes are in the stack?",
     False, "nothing stated, must not invent"),
]


def main() -> int:
    failures = 0

    print("Answer consistency")
    print("-" * 62)
    for answer, working, want in ANSWER_CASES:
        got, why = answer_is_trustworthy(answer, working)
        ok = got == want
        failures += not ok
        label = "trusted" if got else "rejected"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:8} {answer[:26]!r:30} {why or ''}")

    print("\nDiagram reconciliation")
    print("-" * 62)
    for spec, question, want, note in DIAGRAM_CASES:
        _, changed = reconcile_diagram_spec(dict(spec), question)
        ok = changed == want
        failures += not ok
        label = "corrected" if changed else "unchanged"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:10} {note}")

    total = len(ANSWER_CASES) + len(DIAGRAM_CASES)
    print(f"\n{total - failures}/{total} behaved as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
