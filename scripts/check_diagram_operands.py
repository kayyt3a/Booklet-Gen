"""Checks that a figure never draws a different sum from the question above it.

A customer's Year 6 booklet printed this, on the page a child works on:

    2. Calculate 512 x 24.
                              0
                          x   0
                          -------

The model emitted a `long_multiplication` spec without `top` and `bottom`, and
the renderer read them with `spec.get("top", 0)`. Two missing operands became
two zeros, and the page carried a tidy, confident, completely wrong diagram.

This is the worst shape a visual defect can take. A blank space is obviously
blank and a student reads the question instead. A well-drawn figure of the
wrong sum is trusted over the text, because the figure looks like the part the
teacher prepared.

Two things now stop it, and both are checked here:

  * the renderer REFUSES a spec with no operands, so a broken spec produces no
    figure rather than a wrong one
  * the pipeline RECOVERS the operands from the question text first, so the
    student gets the figure that was intended instead of losing it

The second is what keeps the first from being a downgrade.

    PYTHONPATH=. python scripts/check_diagram_operands.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import visual_policy as vp                      # noqa: E402
from booklet_gen.visuals import diagrams                         # noqa: E402

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


# The cache is keyed on the spec, so a figure drawn by an earlier run would be
# handed back without the renderer being asked again, and every assertion below
# would be measuring the old behaviour.
shutil.rmtree(diagrams.CACHE_DIR, ignore_errors=True)

print("\n== a spec with no operands draws nothing at all ==")

BROKEN = [
    ({"type": "long_multiplication", "show_answer": False},
     "the spec from the customer's booklet"),
    ({"type": "long_multiplication", "top": 512},
     "half a multiplication"),
    ({"type": "long_multiplication", "top": 512, "bottom": None},
     "an explicit null operand"),
    ({"type": "long_multiplication", "top": 512, "bottom": ""},
     "an empty string operand"),
    ({"type": "column_arithmetic", "operation": "+"},
     "a sum with nothing to add"),
    ({"type": "column_arithmetic", "top": 4528, "operation": "-"},
     "half a subtraction"),
]
for spec, label in BROKEN:
    check(diagrams.render_diagram(dict(spec)) is None,
          f"refused: {label}",
          f"{spec} drew a figure. Whatever numbers it used are not the ones in "
          "the question, and the student will believe the picture")

print("\n== a complete spec still draws, exactly as before ==")

WORKING = [
    ({"type": "long_multiplication", "top": 512, "bottom": 24,
      "show_answer": False}, "512 x 24, the real question"),
    ({"type": "long_multiplication", "top": 409, "bottom": 57,
      "show_answer": False}, "409 x 57"),
    ({"type": "column_arithmetic", "top": 4528, "bottom": 2694,
      "operation": "+", "show_answer": False}, "4528 + 2694"),
    ({"type": "short_division", "dividend": 736, "divisor": 4,
      "show_answer": False}, "736 divided by 4"),
]
for spec, label in WORKING:
    check(diagrams.render_diagram(dict(spec)) is not None,
          f"drew: {label}",
          "the guard is too tight and is now dropping figures the booklet "
          "needs, which is a different way of making the page worse")

print("\n== the operands are recovered from the question, not lost ==")

# Refusing alone would turn a wrong figure into a missing one. The numbers are
# in the question text, so the intended figure is recoverable.
for text, wanted in (("Calculate 512 x 24.", (512, 24)),
                     ("Calculate 409 × 57.", (409, 57)),
                     ("Work out 4528 + 2694.", None)):
    repaired = vp.repair_spec_from_text(
        {"type": "long_multiplication", "show_answer": False}, text)
    if wanted is None:
        check(vp.spec_is_incomplete(repaired),
              f"an addition is not passed off as a multiplication: {text!r}",
              "a recovered spec of a different kind overruled the model's own "
              "choice of figure")
        continue
    check((repaired.get("top"), repaired.get("bottom")) == wanted,
          f"{text!r} recovered {wanted}",
          f"got {(repaired.get('top'), repaired.get('bottom'))}: the figure is "
          "dropped when it did not have to be")

print("\n== and the recovered spec actually renders ==")

recovered = vp.repair_spec_from_text(
    {"type": "long_multiplication", "show_answer": False}, "Calculate 512 x 24.")
check(diagrams.render_diagram(dict(recovered)) is not None,
      "the repaired 512 x 24 spec draws a figure",
      "the repair produces something the renderer still refuses, so the page "
      "loses the diagram anyway")

print("\n== recovery never overrules a deliberate choice ==")

check(vp.repair_spec_from_text({"type": "bar_model"},
                               "Calculate 512 x 24.") == {"type": "bar_model"},
      "a bar model beside a multiplication is left alone",
      "any spec missing a key would be rewritten into a long multiplication, "
      "so a model that chose a different representation is overruled")

kept = {"type": "long_multiplication", "top": 7, "bottom": 3}
check(vp.repair_spec_from_text(kept, "Calculate 512 x 24.") == kept,
      "a spec that already has its operands is untouched",
      "the question text overwrote numbers the model set on purpose, which "
      "breaks any question whose figure is deliberately a smaller example")

check(vp.repair_spec_from_text(
        {"type": "long_multiplication"}, "Explain why regrouping works.")
      == {"type": "long_multiplication"},
      "a question with no numbers in it recovers nothing",
      "numbers were invented for a question that does not contain any")

print("\n== the defaulting pattern is gone from the algorithm renderers ==")

# The specific line that caused this. Worth asserting on, because it is a
# natural thing to write and reads as harmless.
import inspect  # noqa: E402

for name in ("_long_multiplication", "_column_arithmetic"):
    src = inspect.getsource(getattr(diagrams, name))
    check('spec.get("top", 0)' not in src and 'spec.get("bottom", 0)' not in src,
          f"{name} does not default a missing operand to zero",
          "this is the exact line that printed 0 x 0 under 512 x 24")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
