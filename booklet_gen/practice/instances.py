"""Turning one parameterised family into many concrete questions.

This is the module that makes the economics work. A language model is asked
once for a family, and this expands it into sixty distinct questions that are
each verified independently. Hundreds of calls become tens of thousands of
checked items, which is the only reason a bank deep enough to grind against is
affordable at all.

Two properties matter more than anything else here.

DETERMINISM. The same template and the same seed produce byte-identical
instances, every time, on any machine. That is what lets a check re-derive
every row in the bank from `template_id`, `params_json` and the seed and
confirm the stored question still matches. Without it, a template edited six
months from now silently rots every question it ever produced and nothing
notices.

ONE RENDER PASS. The question the student reads, the answer, the working and
the payload the verifier checks are all rendered from the SAME parameter dict
in the same pass. They cannot come from different numbers. A renderer that
prints "35.0 g" while its check payload says 3.50 gets that wrong once and
then ships it eight hundred times, and the round trip in `verify.admit` is the
only thing standing between that and a student's screen.

Nothing here calls `eval`. Placeholder expressions and constraints go through
SymPy's parser with an explicit symbol whitelist, because a template is
written by a language model and is therefore untrusted input.
"""
from __future__ import annotations

import hashlib
import itertools
import logging
import random
import re
from typing import Any, Iterable, Iterator, Optional

import sympy as sp
from sympy.parsing.sympy_parser import (convert_equals_signs,
                                        parse_expr, standard_transformations)

from .models import Instance, TemplateRow, canonical_json

log = logging.getLogger(__name__)

# How many parameter tuples we are willing to enumerate before switching to
# sampling. A family with more combinations than this is not more valuable
# than one with exactly this many, and enumerating them all would stall the
# filler on a template nobody asked for.
MAX_ENUMERATION = 200_000

# The floor a family has to clear to be worth banking. Six possible instances
# is a family a student exhausts in one sitting and then meets again all week.
MIN_SPACE = 40

DEFAULT_COUNT = 60
MAX_COUNT = 200

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")

# What a placeholder expression or a constraint is allowed to contain. Deliberately
# narrow: arithmetic over the declared parameters and a handful of named
# functions, and nothing else. Anything outside this is a template we refuse
# rather than a template we try to be clever about.
_SAFE_EXPR = re.compile(r"^[A-Za-z0-9_+\-*/(),.<>=!% ^]+$")

_ALLOWED_FUNCTIONS = {
    "gcd": sp.gcd, "lcm": sp.lcm, "abs": sp.Abs, "Abs": sp.Abs,
    "sqrt": sp.sqrt, "floor": sp.floor, "ceiling": sp.ceiling,
    "Rational": sp.Rational, "factorial": sp.factorial,
    "min": sp.Min, "max": sp.Max, "Min": sp.Min, "Max": sp.Max,
}

_TRANSFORMS = standard_transformations + (convert_equals_signs,)


class TemplateError(ValueError):
    """A template that cannot be expanded. The reason is the message.

    Raised rather than returned because every caller stores the reason against
    the template and moves on; there is no path that wants to continue with a
    half-expanded family.
    """


# ---------------------------------------------------------------------------
# The parameter space
# ---------------------------------------------------------------------------

def _values_for(name: str, spec: Any) -> list:
    """Every value one declared parameter may take.

    Accepts the two shapes a template may use: an explicit `choices` list, or
    an integer `range` with optional `step` and `exclude`. A float range is
    deliberately not supported. Floats invite questions whose answers depend on
    binary rounding, and a family that cannot state its own values exactly is a
    family whose verification is arguing with itself.
    """
    if isinstance(spec, (list, tuple)):
        spec = {"choices": list(spec)}
    if not isinstance(spec, dict):
        raise TemplateError(f"parameter {name!r} is not a specification")

    if "choices" in spec:
        choices = list(spec["choices"])
        if not choices:
            raise TemplateError(f"parameter {name!r} has no choices")
        return choices

    bounds = spec.get("range")
    if not (isinstance(bounds, (list, tuple)) and len(bounds) == 2):
        raise TemplateError(
            f"parameter {name!r} declares neither choices nor a two-element range")
    try:
        low, high = int(bounds[0]), int(bounds[1])
        step = int(spec.get("step", 1)) or 1
    except (TypeError, ValueError) as exc:
        raise TemplateError(f"parameter {name!r} has a non-integer range") from exc
    if high < low:
        low, high = high, low
    excluded = {int(x) for x in spec.get("exclude", ()) if _is_int(x)}
    values = [v for v in range(low, high + 1, abs(step)) if v not in excluded]
    if not values:
        raise TemplateError(f"parameter {name!r} excludes its whole range")
    return values


def _is_int(value) -> bool:
    try:
        int(value)
        return True
    except (TypeError, ValueError):
        return False


def _symbols(names: Iterable[str]) -> dict:
    table = dict(_ALLOWED_FUNCTIONS)
    for name in names:
        table[name] = sp.Symbol(name)
    return table


def _split_top(text: str, op: str) -> Optional[tuple[str, str]]:
    """Split on `op` outside any bracket, or None. Used for comparisons."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and text.startswith(op, i):
            return text[:i], text[i + len(op):]
    return None


def _as_relational(text: str) -> str:
    """Rewrite `a != b` as `Ne(a, b)`, and `==` likewise.

    Necessary because SymPy symbols use Python's comparison protocol for
    equality: `Symbol('a') != Symbol('b')` evaluates to the plain bool True at
    parse time rather than to an unevaluated `Ne`, so a constraint written the
    obvious way collapses to a constant before any parameters are substituted.
    Left unhandled this rejects every tuple in the space and the family looks
    empty, which is a confusing way for a correct template to fail.

    The ordering matters: `!=` is checked first because scanning for `==` in
    `a != b` would otherwise find nothing and fall through.
    """
    for op, fn in (("!=", "Ne"), ("==", "Eq")):
        parts = _split_top(text, op)
        if parts:
            return f"{fn}({parts[0].strip()}, {parts[1].strip()})"
    return text


def _parse_safely(text: str, table: dict):
    if not _SAFE_EXPR.match(text or ""):
        raise TemplateError(f"expression {text!r} contains something unexpected")
    try:
        return parse_expr(_as_relational(text), local_dict=table,
                          transformations=_TRANSFORMS, evaluate=True)
    except Exception as exc:                                   # noqa: BLE001
        raise TemplateError(f"expression {text!r} does not parse: {exc}") from exc


def _satisfies(constraints: Iterable, table: dict, values: dict) -> bool:
    for raw in constraints or ():
        expr = _parse_safely(str(raw), table)
        if isinstance(expr, bool):
            # A constraint that reduced to a constant before substitution says
            # nothing about this tuple, and silently accepting it would let a
            # meaningless constraint pass for a real one.
            if expr:
                continue
            return False
        try:
            verdict = expr.subs(values)
        except Exception:                                      # noqa: BLE001
            return False
        if verdict in (sp.true, True):
            continue
        if verdict in (sp.false, False):
            return False
        # A constraint that will not reduce to a truth value has not been
        # satisfied. Treating "cannot tell" as "fine" is how a family whose
        # constraints are nonsense ends up producing questions with a zero
        # denominator in them.
        return False
    return True


def space_size(template: TemplateRow) -> int:
    """How many distinct instances this family can produce, up to the cap.

    Counted properly, with the constraints applied, because a family declaring
    three parameters over wide ranges can still be reduced to nothing by one
    constraint, and the difference decides whether it is worth an LLM call.
    """
    return sum(1 for _ in _tuples(template))


def _tuples(template: TemplateRow) -> Iterator[dict]:
    names = list((template.params or {}).keys())
    if not names:
        raise TemplateError("the template declares no parameters, so every "
                            "instance of it would be the same question")
    pools = [_values_for(n, template.params[n]) for n in names]

    total = 1
    for pool in pools:
        total *= len(pool)
        if total > MAX_ENUMERATION:
            break

    table = _symbols(names)
    constraints = list(template.constraints or ())

    if total <= MAX_ENUMERATION:
        for combo in itertools.product(*pools):
            values = dict(zip(names, combo))
            if _satisfies(constraints, table, values):
                yield values
        return

    # Too large to sweep. Sample without replacement against a seen set, which
    # keeps the guarantee that every instance is distinct while giving up only
    # the guarantee that we looked at all of them.
    rng = random.Random(f"{template.id}:space")
    seen: set = set()
    misses = 0
    while misses < 4000 and len(seen) < MAX_ENUMERATION:
        combo = tuple(rng.choice(pool) for pool in pools)
        if combo in seen:
            misses += 1
            continue
        seen.add(combo)
        values = dict(zip(names, combo))
        if _satisfies(constraints, table, values):
            yield values
        else:
            misses += 1


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _format(value) -> str:
    """Print a computed value the way a person writes it.

    SymPy hands back `6` as `Integer(6)` and `6.0` for anything that touched a
    float. A question reading "the derivative is 6.0x" looks like output from a
    program, which is exactly the impression this product is trying not to
    give.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, sp.Basic):
        if value.is_Integer:
            return str(int(value))
        if value.is_Rational and not value.is_Integer:
            return str(sp.nsimplify(value))
        if value.is_Float:
            as_float = float(value)
            return (str(int(as_float)) if as_float.is_integer()
                    else repr(round(as_float, 10)))
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(round(value, 10))
    return str(value)


_SIGN_FIXES = (
    (re.compile(r"\+\s*-\s*"), " - "),
    (re.compile(r"-\s*-\s*"), " + "),
    (re.compile(r"\s{2,}"), " "),
)


def tidy_signs(text: str) -> str:
    """Turn "+ -6x" into "- 6x" on the page the student reads.

    A parameter allowed to go negative renders straight into the pattern's own
    "+", and no examiner writes "y = 9x^6 + -6x". The template prompt asks for
    ranges that avoid it, but a prompt rule with nothing enforcing it is a rule
    the model follows most of the time, and "most of the time" over thirty
    thousand questions is thousands of them. This is the deterministic half of
    that pair, in the same spirit as the em dash stripper in the formatter.

    Applied only to the strings a person reads. The check payload keeps its
    machine form, because "+ -6*x" is what SymPy parses and tidying it would
    change what is being verified rather than how it looks.
    """
    out = text or ""
    for pattern, replacement in _SIGN_FIXES:
        out = pattern.sub(replacement, out)
    return out.strip()


def render(pattern: str, values: dict, table: Optional[dict] = None) -> str:
    """One pattern, with every `{expression}` replaced by its value.

    Placeholders may hold arithmetic over the parameters, not just parameter
    names, because "{a*n}x^{n-1}" is how a derivative family states its own
    answer and splitting that into more declared parameters would only move the
    arithmetic somewhere less readable.
    """
    table = table or _symbols(values.keys())

    def one(match: re.Match) -> str:
        body = match.group(1).strip()
        if body in values:                       # the common case, no parsing
            return _format(values[body])
        expr = _parse_safely(body, table)
        try:
            return _format(expr.subs(values))
        except Exception as exc:                                # noqa: BLE001
            raise TemplateError(
                f"placeholder {{{body}}} could not be evaluated: {exc}") from exc

    return _PLACEHOLDER.sub(one, pattern or "")


def render_payload(pattern: Any, values: dict, table: Optional[dict] = None):
    """The check payload, rendered from the same values as the question.

    Walks the structure rather than only its top level, because a payload like
    {"reagents": [{"formula": "{acid}", "moles": "{n}"}]} is ordinary and
    rendering only the outer strings would leave markers in the part the
    verifier reads.
    """
    table = table or _symbols(values.keys())
    if isinstance(pattern, str):
        return render(pattern, values, table)
    if isinstance(pattern, dict):
        return {k: render_payload(v, values, table) for k, v in pattern.items()}
    if isinstance(pattern, (list, tuple)):
        return [render_payload(v, values, table) for v in pattern]
    return pattern


def variant_key(values: dict) -> str:
    return hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------

def expand(template: TemplateRow, count: int = DEFAULT_COUNT, seed: int = 0,
           skip: Optional[set] = None) -> list[Instance]:
    """`count` distinct concrete questions from one family.

    Sweeps the parameter space, shuffles it, and takes the first `count`. That
    order matters: sampling with replacement would return `count` draws that
    mostly differ, while sweeping and then shuffling returns `count` that
    provably differ. The shuffle is what stops the bank being filled in
    parameter order, a=1 then a=2 then a=3, which no amount of clever serving
    can disguise afterwards.

    `skip` carries variant keys already in the bank, so a second night's
    top-up on the same template extends its stock instead of colliding with
    it.
    """
    count = max(1, min(int(count), MAX_COUNT))
    rng = random.Random(f"{template.id}:{seed}")
    skip = set(skip or ())

    pool = [v for v in _tuples(template) if variant_key(v) not in skip]
    if not pool:
        raise TemplateError(
            "every parameter combination this family allows is already in the "
            "bank, so it has nothing left to give")
    rng.shuffle(pool)

    names = list(template.params.keys())
    table = _symbols(names)
    out: list[Instance] = []
    failures: list[str] = []

    for values in pool:
        if len(out) >= count:
            break
        try:
            # One dict, one pass. The question the student reads and the
            # payload the verifier checks are rendered from the same numbers,
            # which is the property the round-trip gate depends on.
            question = tidy_signs(render(template.question_pattern, values, table))
            answer = tidy_signs(render(template.answer_pattern, values, table))
            working = tidy_signs(render(template.working_pattern, values, table))
            check = render_payload(template.check_pattern, values, table)
        except TemplateError as exc:
            failures.append(str(exc))
            if len(failures) > 20:
                raise TemplateError(
                    f"{len(failures)} of this family's instances would not "
                    f"render: {failures[0]}")
            continue

        if "{" in question or "{" in answer:
            failures.append("a marker survived rendering")
            continue

        out.append(Instance(
            template_id=template.id,
            params=dict(values),
            question=question,
            answer=answer,
            working=working,
            check=dict(check) if isinstance(check, dict) else {"value": check},
            variant_key=variant_key(values),
            shuffle_key=rng.random(),
        ))

    if failures:
        log.info("practice.instances.partial",
                 extra={"template": template.id, "failed": len(failures),
                        "made": len(out)})
    if not out:
        raise TemplateError(
            f"not one instance of this family rendered: {failures[:1]}")
    return out
