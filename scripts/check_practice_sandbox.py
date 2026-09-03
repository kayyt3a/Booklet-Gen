"""Checks that a template cannot run code on the machine that expands it.

A practice template is written by a language model and arrives as JSON with
five string fields in it, three of which are parsed as mathematics. That makes
this the only place in FolioAI where untrusted text is handed to an evaluator,
and the filler that does it runs unattended overnight with DATABASE_URL and the
Gemini key in its environment.

The code that shipped had a hole. `sympy.parse_expr` compiles the transformed
source and calls `eval` on it, and given no `global_dict` it builds one with
`from sympy import *`. That dict has no `__builtins__` key, so Python injects
the real builtins module, and every builtin becomes reachable from a string
made only of letters, digits and brackets, all of which the "arithmetic"
character whitelist allows. `open(1)` opened a real file descriptor. A
character class filters syntax; it was never going to filter meaning.

Three things now stand in the way, and this file exists so that removing any
one of them fails loudly rather than quietly:

  the character whitelist  syntax
  `reject_hostile`         dunders and attribute access
  `_SAFE_GLOBALS`          pinned globals with `__builtins__` empty

The last two matter independently. Pinning the globals does nothing against
`().__class__.__base__.__subclasses__()`, which starts from a literal already
in the expression and needs no builtins at all.

    PYTHONPATH=. python scripts/check_practice_sandbox.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sympy as sp                                              # noqa: E402

from booklet_gen.practice import instances, verify              # noqa: E402

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


# Anything that is not a SymPy object came out of a real Python evaluation.
def executed(value) -> bool:
    return value is not None and not isinstance(value, (sp.Basic, bool))


HOSTILE = [
    ("open(1)", "opens a file descriptor"),
    ("open('/etc/passwd')", "reads a file"),
    ("eval(1)", "reaches the evaluator"),
    ("exec(1)", "reaches the executor"),
    ("__import__('os')", "imports a module"),
    ("().__class__.__base__.__subclasses__()",
     "walks to every loaded class, the first step of a sandbox escape"),
    ("(1).__class__.__mro__", "walks the type hierarchy"),
    ("a.__globals__", "reaches a function's globals"),
    ("().__class__", "reaches an object's type"),
]

print("\n== a hostile expression never runs, in either parser ==")

for probe, what in HOSTILE:
    ran = False
    try:
        ran = executed(instances._parse_safely(probe, {}))
    except Exception:                                          # noqa: BLE001
        ran = False
    check(not ran, f"instances refuses {probe!r}",
          f"it {what}, on the unattended filler, from one line of a template "
          "a language model wrote")

    try:
        ran = executed(verify._parse(probe))
    except Exception:                                          # noqa: BLE001
        ran = False
    check(not ran, f"verify refuses {probe!r}",
          f"it {what}. This parser reads the PRINTED QUESTION, so the text "
          "does not even have to survive template validation to get here")

print("\n== the three guards are all still in place ==")

# Asserted individually, because each one covers what the others miss and a
# future reader removing "the redundant one" is exactly how this regresses.
check(instances._SAFE_GLOBALS.get("__builtins__") == {},
      "the parser's globals pin __builtins__ to an empty dict",
      "without this, parse_expr evaluates against the real builtins module and "
      "every one of them is reachable from an expression")

check(instances.reject_hostile("().__class__") is not None,
      "attribute access is refused by meaning, not only by character",
      "pinning the globals does not help here: the traversal starts from a "
      "literal in the expression and needs no builtins at all")

check(instances.reject_hostile("a.__globals__") is not None
      and instances.reject_hostile("x__y") is not None,
      "a double underscore anywhere is refused")

check(instances.reject_hostile("0.5") is None
      and instances.reject_hostile("gcd(a, b) == 1") is None,
      "and ordinary maths is not caught by any of it",
      "a guard that refuses real templates empties the bank instead of "
      "protecting it")

print("\n== the guard survives the route a real template takes ==")

# The hole was not reachable only through the private parser. `constraints` is
# never checked by the structural validator, and reaches the parser inside the
# sweep, so a crafted constraint executed while the template was still being
# measured for size.
from booklet_gen.practice.models import TemplateRow             # noqa: E402


def family(constraints) -> TemplateRow:
    return TemplateRow(
        id="t-probe", subject="methods",
        subtopic_id="methods.calculus.polynomial-derivatives",
        verify_kind="derivative", calculator="free", difficulty="medium",
        question_pattern="Differentiate y = {a}x^{n} with respect to x.",
        answer_pattern="dy/dx = {a*n}x^{n-1}", working_pattern="w",
        params={"a": {"range": [2, 9]}, "n": {"range": [2, 6]}},
        constraints=constraints, check_pattern={"kind": "derivative",
                                                "function": "{a}*x**{n}"},
        prompt_version="v1", syllabus_version="test", created_at=0)


for constraint in ("open(1)", "().__class__.__base__.__subclasses__()"):
    try:
        instances.space_size(family(["a != n", constraint]))
        survived = True
    except instances.TemplateError:
        survived = False
    except Exception:                                          # noqa: BLE001
        survived = False
    check(not survived,
          f"a template whose constraints hide {constraint!r} is refused",
          "constraints are not checked by the structural validator, so this "
          "runs while the template is still being measured, before the probe "
          "instance and before anything could reject it")

check(instances.space_size(family(["a != n"])) > 0,
      "and an honest constraint still measures a real space",
      "the guard is too tight and no family can be expanded at all")

print("\n== a badly scaled range cannot exhaust memory before it is judged ==")

# Not adversarial: a concentration written in the wrong units does this. The
# enumeration cap is checked against a running product AFTER every pool has
# been materialised, so one wide range allocated hundreds of megabytes before
# any check could apply.
try:
    instances.space_size(family(["a != n"]).__class__(
        **{**family(["a != n"]).__dict__,
           "params": {"n": {"range": [0, 5_000_000]}}, "constraints": []}))
    bounded = False
except instances.TemplateError:
    bounded = True

check(bounded,
      f"a parameter spanning millions is refused before its pool is built "
      f"(cap {instances.MAX_RANGE_SPAN})",
      "materialising it takes hundreds of megabytes and stalls the nightly "
      "filler on a template nobody wrote maliciously")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
