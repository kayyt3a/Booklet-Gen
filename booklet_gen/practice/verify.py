"""The admission gate. Nothing enters the bank without passing it twice.

An instance is admitted only when BOTH of these hold:

  Gate 1, independent recomputation. The answer is recomputed from the
  structured `check` payload, never from the template's `answer_pattern`. This
  proves the answer is arithmetically right.

  Gate 2, text round trip. The problem is re-extracted from the RENDERED
  question string and must equal the `check` payload. This proves the student is
  being shown the problem that was solved. For the six kinds `agents/validator.py`
  already covers, its verdict on the printed text is required as well, because
  reading the printed text is all that validator ever does, so its agreement is
  a second independent statement that the question is readable and right.

Admission requires `verified AND conclusive`, never `verified` alone.
`ValidationResult.conclusive` exists because a partial match is not a pass, and
on senior material an inconclusive verdict means the item does not ship. The
tempting "fix" for a bank that fills slowly is to relax that to accept
`conclusive=False`. `scripts/check_practice_instance_verification.py` asserts
that specific relaxation fails.

THE MODEL'S ANSWER IS NEVER TRUSTED
-----------------------------------
`answer_pattern` is a cross-check and nothing more. Gate 1 computes the answer
itself and compares. A family whose author cannot state its own answer is a
family whose questions are wrong in ways the verifier may not always catch, so
one disagreement rejects the whole template rather than the one instance.

KINDS_FOR_SUBTOPIC IS AUTHORITATIVE
-----------------------------------
`senior_syllabus.py` marks all 42 Methods subtopics symbolic or numeric. That is
a claim about the topic. `KINDS_FOR_SUBTOPIC` below is the claim about the code
that exists, and where they disagree this one wins. A subtopic with an empty
tuple is not fillable no matter what `bankable()` says: the filler skips it with
`blocked_reason='no checker'` and the API reports it as not yet stocked rather
than serving a blank card. The coverage table is printed by check 7.3 on every
run, so the gap is a number in the output rather than a surprise in production.
"""
from __future__ import annotations

import math
import re

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor, implicit_multiplication_application, parse_expr,
    standard_transformations,
)

from .. import senior_syllabus as syllabus
from ..agents.validator import SympyValidator, ValidationResult
from ..schemas import Question
from . import chem
from .models import Instance

X = sp.Symbol("x")

# Deliberately a copy of the transformations `agents/validator.py` uses rather
# than an import of its private parser. verify.py has to read kinds that
# validator has no path for at all (binomial, roots, equivalence), and reaching
# into another module's private name to get half of what is needed would couple
# the practice engine to an internal of the booklet pipeline.
_TRANSFORMS = standard_transformations + (
    convert_xor, implicit_multiplication_application,
)
# In Methods a bare "e" is Euler's number, never a variable.
_LOCALS = {"e": sp.E, "E": sp.E, "pi": sp.pi}

_ANSWER_PREFIX = re.compile(
    r"^\s*(?:dy\s*/\s*dx|d\s*/\s*dx|f\s*'\s*\(\s*x\s*\)|g\s*'\s*\(\s*x\s*\)"
    r"|y\s*'|f\s*'|y|answer|value)\s*[:=]\s*", re.I)


def _parse(text: str):
    """Parse a maths expression the way a student writes it, or None."""
    if not text or not str(text).strip():
        return None
    body = str(text).strip()
    body = body.replace("^", "**").replace("×", "*").replace("·", "*")
    body = body.replace("−", "-").rstrip(".,;:")
    body = re.sub(r"\s*(?:with respect to\s*\w*|for\s+x|dx)\s*$", "", body, flags=re.I)
    body = re.sub(r"[+\-]\s*[cC]\s*$", "", body).strip()
    if not body:
        return None
    try:
        # `global_dict` pinned for the same reason as in instances.py: without
        # it parse_expr evaluates against globals with no `__builtins__` key,
        # so Python injects the real builtins and every one of them becomes
        # reachable from a printed question string. This parses text a language
        # model wrote.
        from .instances import _SAFE_GLOBALS, reject_hostile
        if reject_hostile(body):
            return None
        return parse_expr(body, transformations=_TRANSFORMS, local_dict=_LOCALS,
                          global_dict=dict(_SAFE_GLOBALS))
    except Exception:
        return None


def _strip_answer(answer: str) -> str:
    return _ANSWER_PREFIX.sub("", (answer or "").strip())


def _same(a, b) -> bool:
    """Whether two expressions are the same function of x."""
    if a is None or b is None:
        return False
    try:
        if sp.simplify(a - b) == 0:
            return True
    except Exception:
        pass
    try:
        for probe in (0.37, 1.53, 2.71, 4.19):
            if abs(complex((a - b).subs(X, probe))) > 1e-7:
                return False
        return True
    except Exception:
        return False


# ------------------------------------------------------------------- kinds

# Kinds handed straight to the existing SympyValidator, whose verdict on the
# printed text is required as part of gate 2.
VALIDATOR_KINDS = (
    "derivative", "derivative_at", "integral_indefinite", "integral_definite",
    "solve_equation", "direct_computation",
)
# Kinds settled by deterministic routines in this module. Still SymPy, still no
# new dependency, but validator.py has no path for them.
METHODS_ONLY_KINDS = (
    "expression_equivalence", "roots", "function_value", "binomial", "normal",
)
METHODS_KINDS = VALIDATOR_KINDS + METHODS_ONLY_KINDS
CHEMISTRY_KINDS = chem.KINDS

KINDS = tuple(sorted(set(METHODS_KINDS) | set(CHEMISTRY_KINDS)))


def verified_by(kind: str) -> str:
    """The `verified_by` stamp for a bank row, e.g. 'sympy:derivative'.

    Names which checker settled the item, so check 7.4 can assert off the bank
    that nothing was admitted by anything but a deterministic routine.
    """
    if kind in METHODS_KINDS:
        return f"sympy:{kind}"
    if kind in CHEMISTRY_KINDS:
        return f"chem:{kind}"
    raise ValueError(f"{kind!r} is not a verify kind")


# Every stamp a live bank row is allowed to carry. Anything else means an item
# reached a Year 12 on the strength of a language model's opinion.
VERIFIED_BY_ALLOWLIST = frozenset(verified_by(k) for k in KINDS)


# --------------------------------------------------------- Methods: gate one

def _recompute_methods(kind: str, check: dict, answer: str) -> ValidationResult:
    given = _parse(_strip_answer(answer))

    if kind in ("derivative", "derivative_at"):
        fn = _parse(check.get("function"))
        if fn is None:
            return ValidationResult(False, "the check payload states no function")
        wanted = sp.diff(fn, X)
        if kind == "derivative_at":
            at = _parse(check.get("at"))
            if at is None or given is None:
                return ValidationResult(False, "the point or the answer will not parse")
            value = sp.simplify(wanted.subs(X, at))
            if _same(sp.nsimplify(value), given):
                return ValidationResult(True, f"f'({at}) = {value}")
            return ValidationResult(
                False, f"f'({at}) is {value}, but the answer says {given}")
        if _same(wanted, given):
            return ValidationResult(True, f"d/dx({fn}) = {sp.simplify(wanted)}")
        return ValidationResult(
            False, f"the derivative of {fn} is {sp.simplify(wanted)}, "
                   f"but the answer says {given}")

    if kind == "integral_indefinite":
        integrand = _parse(check.get("integrand"))
        if integrand is None or given is None:
            return ValidationResult(False, "the integrand or the answer will not parse")
        # Differentiating the stated antiderivative must return the integrand.
        # More robust than comparing antiderivatives, and it makes "+ c" moot.
        if _same(sp.diff(given, X), integrand):
            return ValidationResult(True, f"d/dx(answer) = {integrand}")
        return ValidationResult(
            False, f"differentiating the answer gives {sp.simplify(sp.diff(given, X))}, "
                   f"not the integrand {integrand}")

    if kind == "integral_definite":
        integrand = _parse(check.get("integrand"))
        lo, hi = _parse(check.get("lower")), _parse(check.get("upper"))
        if integrand is None or lo is None or hi is None or given is None:
            return ValidationResult(False, "the integral or the answer will not parse")
        value = sp.integrate(integrand, (X, lo, hi))
        if value.has(sp.Integral):
            return ValidationResult(
                False, "sympy could not evaluate this definite integral, so no "
                       "answer to it can be called checked", conclusive=False)
        if _same(sp.simplify(value), given):
            return ValidationResult(True, f"definite integral = {sp.simplify(value)}")
        return ValidationResult(
            False, f"the integral is {sp.simplify(value)}, but the answer says {given}")

    if kind in ("solve_equation", "roots"):
        var = sp.Symbol(check.get("variable", "x"))
        if kind == "roots":
            lhs, rhs = _parse(check.get("polynomial")), sp.Integer(0)
        else:
            lhs, rhs = _parse(check.get("lhs")), _parse(check.get("rhs"))
        if lhs is None or rhs is None:
            return ValidationResult(False, "the equation will not parse")
        wanted = set(sp.solve(sp.Eq(lhs, rhs), var))
        stated = _answer_roots(answer, var)
        if not wanted:
            return ValidationResult(False, "the equation has no solution to state")
        if stated is None:
            return ValidationResult(
                False, f"the answer {answer!r} does not state roots as "
                       f"'{var} = ...'")
        if _root_sets_match(wanted, stated):
            return ValidationResult(True, f"solution set {sorted(map(str, wanted))}")
        return ValidationResult(
            False, f"the solutions are {sorted(map(str, wanted))}, but the "
                   f"answer states {sorted(map(str, stated))}")

    if kind in ("direct_computation", "expression_equivalence"):
        expression = _parse(check.get("expression"))
        if expression is None or given is None:
            return ValidationResult(False, "the expression or the answer will not parse")
        if _same(expression, given):
            return ValidationResult(True, f"{expression} = {sp.simplify(given)}")
        return ValidationResult(
            False, f"{expression} simplifies to {sp.simplify(expression)}, "
                   f"but the answer says {given}")

    if kind == "function_value":
        fn, at = _parse(check.get("function")), _parse(check.get("at"))
        if fn is None or at is None or given is None:
            return ValidationResult(False, "the function, point or answer will not parse")
        value = sp.simplify(fn.subs(X, at))
        if _same(sp.nsimplify(value), given):
            return ValidationResult(True, f"f({at}) = {value}")
        return ValidationResult(
            False, f"f({at}) is {value}, but the answer says {given}")

    if kind in ("binomial", "normal"):
        return _recompute_distribution(kind, check, answer)

    return ValidationResult(False, f"{kind!r} has no recomputation routine",
                            conclusive=False)


def _answer_roots(answer: str, var: sp.Symbol):
    """The set of roots an answer states as 'x = 2 or x = 3', else None."""
    text = (answer or "").strip()
    pieces = re.split(r"\s*(?:,|\bor\b|;)\s*", text)
    roots = set()
    for piece in pieces:
        m = re.match(rf"^\s*{re.escape(str(var))}\s*=\s*(.+)$", piece.strip())
        if not m:
            return None
        value = _parse(m.group(1))
        if value is None:
            return None
        roots.add(sp.nsimplify(value))
    return roots or None


def _root_sets_match(wanted, stated) -> bool:
    if len(wanted) != len(stated):
        return False
    remaining = list(stated)
    for root in wanted:
        for i, candidate in enumerate(remaining):
            try:
                if sp.simplify(root - candidate) == 0:
                    remaining.pop(i)
                    break
            except Exception:
                continue
        else:
            return False
    return True


def _binomial_probability(n: int, p, k: int, mode: str):
    def one(i):
        return sp.binomial(n, i) * p ** i * (1 - p) ** (n - i)
    if mode == "exactly":
        return one(k)
    if mode == "at_most":
        return sum(one(i) for i in range(0, k + 1))
    if mode == "at_least":
        return sum(one(i) for i in range(k, n + 1))
    raise ValueError(f"binomial mode {mode!r} is not one of exactly, at_most, at_least")


def _normal_probability(mu, sigma, mode: str, bound, upper=None):
    def below(value):
        return (1 + sp.erf((value - mu) / (sigma * sp.sqrt(2)))) / 2
    if mode == "less_than":
        return below(bound)
    if mode == "greater_than":
        return 1 - below(bound)
    if mode == "between":
        return below(upper) - below(bound)
    raise ValueError(f"normal mode {mode!r} is not one of less_than, greater_than, between")


def _recompute_distribution(kind: str, check: dict, answer: str) -> ValidationResult:
    printed = chem.parse_number(answer)
    if printed is None:
        return ValidationResult(False, f"the answer {answer!r} states no number")
    try:
        dp, sf = chem.rounding_of(check)
        if kind == "binomial":
            exact = _binomial_probability(
                int(check["n"]), sp.Rational(str(check["p"])),
                int(check["k"]), check["mode"])
        else:
            exact = _normal_probability(
                sp.Rational(str(check["mu"])), sp.Rational(str(check["sigma"])),
                check["mode"], sp.Rational(str(check["bound"])),
                None if check.get("upper") is None else sp.Rational(str(check["upper"])))
        value = float(sp.N(exact, 30))
    except (KeyError, ValueError, TypeError, chem.ChemError) as exc:
        return ValidationResult(False, f"the {kind} payload is unusable: {exc}")
    if chem.numbers_agree(printed, value, dp=dp, sf=sf):
        return ValidationResult(True, f"{kind} probability {value:.10g}")
    return ValidationResult(
        False, f"the {kind} probability is {value:.10g}, but the answer says {printed}")


# --------------------------------------------------------- Methods: gate two
#
# Each reads the rendered question and rebuilds the check payload. The shapes
# are dictated by prompts/practice_template_methods.txt, and are the shapes
# `agents/validator.py` is keyword-gated on, so a template that renders into
# something these cannot read is also a template that validator cannot check.

_FUNC_DEF = re.compile(
    r"(?:\by\b|\bf\s*\(\s*x\s*\)|\bg\s*\(\s*x\s*\))\s*=\s*([^,.;?]+)", re.I)
_DERIV_AT = re.compile(r"f\s*'\s*\(\s*(-?[\d./]+)\s*\)")
_INTEGRAND = re.compile(
    r"(?:∫|integrate|integral of|antiderivative of|anti-derivative of|primitive of)"
    r"\s*(.+?)\s*(?:\bdx\b|with respect to\s*x|$)", re.I | re.S)
_LIMITS = re.compile(r"(?:from|between)\s+(-?[\d./]+)\s+(?:to|and)\s+(-?[\d./]+)", re.I)
_DERIV_CUE = ("differentiate", "derivative", "dy/dx", "d/dx", "f'(",
              "gradient function")
_INTEGRAL_CUE = ("integrate", "integral", "antiderivative", "anti-derivative",
                 "primitive", "∫")
_SOLVE = re.compile(r"solve\s+(?:the equation\s+)?(.+?)\s*(?:for\s+\w+)?\s*[.?]?\s*$",
                    re.I)
_EQUIV_CUE = re.compile(
    r"(?:expand and simplify|expand|factorise|factorize|simplify)\s+(.+?)\s*[.?]\s*$",
    re.I)
_COMPUTE_CUE = re.compile(r"(?:calculate|evaluate|compute|work out)\s+(.+?)\s*[.?]\s*$",
                          re.I)
_FUNC_AT = re.compile(r"\bf\s*\(\s*(-?[\d./]+)\s*\)", re.I)
_BINOMIAL = re.compile(
    r"X\s*~\s*B\s*\(\s*n\s*=\s*(\d+)\s*,\s*p\s*=\s*([\d.]+)\s*\)", re.I)
_NORMAL = re.compile(
    r"X\s*~\s*N\s*\(\s*mu\s*=\s*(-?[\d.]+)\s*,\s*sigma\s*=\s*([\d.]+)\s*\)", re.I)
_P_EQ = re.compile(r"P\s*\(\s*X\s*=\s*(\d+)\s*\)")
_P_LE = re.compile(r"P\s*\(\s*X\s*(?:<=|≤)\s*(\d+)\s*\)")
_P_GE = re.compile(r"P\s*\(\s*X\s*(?:>=|≥)\s*(\d+)\s*\)")
_P_LT = re.compile(r"P\s*\(\s*X\s*<\s*(-?[\d.]+)\s*\)")
_P_GT = re.compile(r"P\s*\(\s*X\s*>\s*(-?[\d.]+)\s*\)")
_P_BETWEEN = re.compile(r"P\s*\(\s*(-?[\d.]+)\s*<\s*X\s*<\s*(-?[\d.]+)\s*\)")
_DP = re.compile(r"(\d+)\s*decimal places?", re.I)


def _extract_methods(kind: str, text: str) -> dict | None:
    low = (text or "").lower()

    if kind in ("derivative", "derivative_at"):
        if not any(cue in low for cue in _DERIV_CUE):
            return None
        m = _FUNC_DEF.search(text)
        if not m:
            return None
        out = {"function": m.group(1).strip()}
        at = _DERIV_AT.search(text)
        if kind == "derivative_at":
            if not at:
                return None
            out["at"] = at.group(1)
        elif at:
            # A question naming a point is a derivative_at question. Checking it
            # as a plain derivative would mark a number against an expression.
            return None
        return out

    if kind in ("integral_indefinite", "integral_definite"):
        if not any(cue in low for cue in _INTEGRAL_CUE):
            return None
        m = _INTEGRAND.search(text)
        if not m:
            return None
        raw = re.sub(r"^(?:the|of)\s+", "", m.group(1).strip(), flags=re.I)
        raw = _LIMITS.sub("", raw).strip()
        limits = _LIMITS.search(text)
        if kind == "integral_definite":
            if not limits:
                return None
            return {"integrand": raw, "lower": limits.group(1),
                    "upper": limits.group(2)}
        if limits:
            return None
        return {"integrand": raw}

    if kind == "solve_equation":
        m = _SOLVE.search(text.strip())
        if not m:
            return None
        body = m.group(1).strip().rstrip(".")
        if body.count("=") != 1:
            return None
        lhs, rhs = body.split("=")
        variable = "x"
        named = re.search(r"for\s+([a-zA-Z])\b", text)
        if named:
            variable = named.group(1)
        return {"lhs": lhs.strip(), "rhs": rhs.strip(), "variable": variable}

    if kind == "roots":
        m = _SOLVE.search(text.strip())
        if not m:
            return None
        body = m.group(1).strip().rstrip(".")
        if body.count("=") != 1:
            return None
        lhs, rhs = body.split("=")
        if _parse(rhs) is None or sp.simplify(_parse(rhs)) != 0:
            return None
        variable = "x"
        named = re.search(r"for\s+([a-zA-Z])\b", text)
        if named:
            variable = named.group(1)
        return {"polynomial": lhs.strip(), "variable": variable}

    if kind == "direct_computation":
        m = _COMPUTE_CUE.search(text.strip())
        if not m:
            return None
        return {"expression": m.group(1).strip()}

    if kind == "expression_equivalence":
        m = _EQUIV_CUE.search(text.strip())
        if not m:
            return None
        return {"expression": m.group(1).strip()}

    if kind == "function_value":
        m = _FUNC_DEF.search(text)
        at = _FUNC_AT.search(text)
        if not m or not at:
            return None
        return {"function": m.group(1).strip(), "at": at.group(1)}

    if kind == "binomial":
        m = _BINOMIAL.search(text)
        if not m:
            return None
        out = {"n": int(m.group(1)), "p": m.group(2)}
        for pattern, mode in ((_P_EQ, "exactly"), (_P_LE, "at_most"),
                              (_P_GE, "at_least")):
            hit = pattern.search(text)
            if hit:
                out["k"] = int(hit.group(1))
                out["mode"] = mode
                break
        else:
            return None
        dp = _DP.search(text)
        if dp:
            out["dp"] = int(dp.group(1))
        return out

    if kind == "normal":
        m = _NORMAL.search(text)
        if not m:
            return None
        out = {"mu": m.group(1), "sigma": m.group(2)}
        between = _P_BETWEEN.search(text)
        if between:
            out.update({"mode": "between", "bound": between.group(1),
                        "upper": between.group(2)})
        else:
            less, more = _P_LT.search(text), _P_GT.search(text)
            if less:
                out.update({"mode": "less_than", "bound": less.group(1)})
            elif more:
                out.update({"mode": "greater_than", "bound": more.group(1)})
            else:
                return None
        dp = _DP.search(text)
        if dp:
            out["dp"] = int(dp.group(1))
        return out

    return None


_SYMBOLIC_FIELDS = {
    "function", "integrand", "lhs", "rhs", "polynomial", "expression",
    "lower", "upper", "at",
}


def _methods_payloads_agree(stored: dict, read: dict) -> tuple[bool, str]:
    """Compare stored against re-extracted, symbolically where that is the point.

    "3*x**2 + 5*x" and "3x^2 + 5x" are the same problem written two ways, and a
    string comparison would reject every well-formed template. Everything that
    is not maths is compared as text or as a number.
    """
    for key, value in stored.items():
        if key == "kind":
            continue
        if key not in read:
            return False, f"the question never states {key!r}"
        if key in _SYMBOLIC_FIELDS:
            left, right = _parse(str(value)), _parse(str(read[key]))
            if left is None or right is None:
                return False, f"{key} does not parse on one side of the round trip"
            if not _same(left, right):
                return False, (f"the question states {key} as {read[key]!r}, but the "
                               f"answer was computed from {value!r}")
        elif chem._normalise(value) != chem._normalise(read[key]):
            return False, (f"the question states {key}={read[key]!r} but the answer "
                           f"was computed from {key}={value!r}")
    return True, "the printed question states the problem that was solved"


# --------------------------------------------------------------- the gate

_validator = SympyValidator()


def admit(instance: Instance) -> ValidationResult:
    """Whether this instance may enter the bank. Both gates, or nothing.

    Returns the existing `ValidationResult`, so the notes that explain a
    rejection are stored on the template and readable by a human deciding
    whether the subtopic is worth another try.
    """
    check = dict(instance.check or {})
    kind = check.get("kind")
    if kind not in KINDS:
        return ValidationResult(
            False, f"{kind!r} is not a verify kind, so nothing checked this item")

    try:
        gate1 = (_recompute_methods(kind, check, instance.answer)
                 if kind in METHODS_KINDS
                 else _recompute_chemistry(kind, check, instance.answer))
    except Exception as exc:                      # a parse quirk proves nothing
        return ValidationResult(
            False, f"gate 1 could not run: {type(exc).__name__}: {exc}",
            conclusive=False)
    if not gate1.verified:
        return gate1
    if not gate1.conclusive:
        # Not a failure of the answer, but not a proof either. On senior
        # material that is the same outcome: the item does not ship.
        return gate1

    try:
        read = (_extract_methods(kind, instance.question) if kind in METHODS_KINDS
                else chem.extract(kind, instance.question))
    except Exception as exc:
        return ValidationResult(
            False, f"gate 2 could not read the question: {type(exc).__name__}: {exc}",
            conclusive=False)
    if read is None:
        return ValidationResult(
            False, "the rendered question does not state the problem in a form "
                   f"the {kind} checker can read back, so nothing confirms the "
                   "student is being shown what was solved", conclusive=False)
    agree, note = (_methods_payloads_agree(check, read) if kind in METHODS_KINDS
                   else chem.payloads_agree(
                       {k: v for k, v in check.items() if k != "kind"}, read))
    if not agree:
        return ValidationResult(False, f"round trip failed: {note}")

    if kind in VALIDATOR_KINDS:
        verdict = _validator.validate(Question(
            question=instance.question, answer=instance.answer,
            working=instance.working or ""))
        if not (verdict.verified and verdict.conclusive):
            return ValidationResult(
                verdict.verified,
                f"the shipped validator could not settle the printed question: "
                f"{verdict.notes}", conclusive=verdict.conclusive)

    return ValidationResult(True, f"{gate1.notes}; {note}")


def _recompute_chemistry(kind: str, check: dict, answer: str) -> ValidationResult:
    payload = {k: v for k, v in check.items() if k != "kind"}
    try:
        computed = chem.solve(kind, payload)
    except chem.ChemError as exc:
        return ValidationResult(False, f"the chemistry does not work: {exc}")

    if kind == "balance_equation":
        stated = _stated_coefficients(answer, payload["equation"])
        if stated is None:
            return ValidationResult(
                False, f"the answer {answer!r} is not the same equation with "
                       "coefficients in front of the same species")
        if stated == list(computed):
            return ValidationResult(True, f"coefficients {computed}")
        return ValidationResult(
            False, f"the equation balances as {computed}, but the answer states "
                   f"{stated}")

    if isinstance(computed, str):
        # A formula or a species name. Compared as written, after removing the
        # spacing and the state symbols a student may or may not include.
        stated = re.sub(r"\s+|\((?:s|l|g|aq)\)", "", (answer or "").strip(), flags=re.I)
        if stated == re.sub(r"\s+", "", computed):
            return ValidationResult(True, f"{kind} = {computed}")
        return ValidationResult(
            False, f"the answer is {computed!r}, but the question's answer says "
                   f"{stated!r}")

    printed = chem.parse_number(answer)
    if printed is None:
        return ValidationResult(False, f"the answer {answer!r} states no number")
    try:
        dp, sf = chem.rounding_of(payload)
    except chem.ChemError as exc:
        return ValidationResult(False, str(exc))
    if chem.numbers_agree(printed, float(computed), dp=dp, sf=sf):
        return ValidationResult(True, f"{kind} = {float(computed):.10g}")
    return ValidationResult(
        False, f"{kind} computes to {float(computed):.10g}, but the answer says "
               f"{printed}")


def _stated_coefficients(answer: str, equation: str) -> list[int] | None:
    """The coefficients an answer puts in front of the skeleton's species."""
    try:
        left, right = chem.split_equation(answer)
        want_left, want_right = chem.split_equation(equation)
    except chem.ChemError:
        return None
    if len(left) != len(want_left) or len(right) != len(want_right):
        return None
    out: list[int] = []
    for stated, wanted in zip(left + right, want_left + want_right):
        coefficient, formula = chem.strip_coefficient(stated)
        if formula.replace(" ", "") != wanted.replace(" ", ""):
            return None
        out.append(coefficient)
    return out


# ------------------------------------------------------------- the coverage
#
# What the code can actually check, subtopic by subtopic. Where this disagrees
# with `senior_syllabus.Subtopic.verification`, this wins: that field is a claim
# about the topic, this one is a claim about a routine that exists and is
# tested. An empty tuple means the filler skips the subtopic entirely.

KINDS_FOR_SUBTOPIC: dict[str, tuple[str, ...]] = {
    # --- Mathematics Methods -------------------------------------------------
    "methods.functions.linear": ("solve_equation",),
    "methods.functions.quadratic": ("roots", "expression_equivalence"),
    "methods.functions.inverse-proportion": ("function_value",),
    "methods.functions.powers-polynomials": ("roots", "function_value",
                                             "expression_equivalence"),
    # Describing a transformation is prose. Nothing here can settle it.
    "methods.functions.transformations": (),
    # No kind reads "state the centre and radius of this circle" off a
    # printed question, so this is recorded as unfilled rather than left
    # out of the table, where it would look like an oversight.
    "methods.functions.circles": (),
    "methods.functions.notation": ("function_value",),
    # A triangle is a picture. Neither the sine rule nor the ambiguous case can
    # be read back off a sentence by anything in this module.
    "methods.trigonometry.sine-cosine-rules": (),
    "methods.trigonometry.radians": (),
    "methods.trigonometry.functions": (),
    "methods.probability.combinations": (),
    "methods.probability.events-sets": (),
    "methods.probability.conditional": (),
    # Index laws are the one Methods topic that is routinely set as a bare
    # numeric computation ("Evaluate 2^5 * 2^-3"), which is exactly the shape
    # direct_computation reads.
    "methods.exponentials.index-laws": ("expression_equivalence",
                                        "direct_computation"),
    "methods.exponentials.functions": ("solve_equation", "function_value"),
    "methods.sequences.arithmetic": (),
    "methods.sequences.geometric": (),
    "methods.sequences.recursion": (),
    "methods.calculus.rates-of-change": (),
    # First principles is still a derivative, and the printed question still
    # says "differentiate y = ...", so the checker settles the answer even
    # though it cannot see whether the limit definition was used.
    "methods.calculus.first-principles": ("derivative",),
    "methods.calculus.polynomial-derivatives": ("derivative", "derivative_at"),
    "methods.calculus.tangents-stationary": ("derivative_at",),
    "methods.calculus.antidifferentiation": ("integral_indefinite",),
    "methods.calculus.exponential-derivatives": ("derivative", "derivative_at"),
    "methods.calculus.trig-derivatives": ("derivative", "derivative_at"),
    "methods.calculus.chain-rule": ("derivative",),
    "methods.calculus.product-quotient": ("derivative",),
    "methods.calculus.second-derivative": (),
    "methods.calculus.optimisation": (),
    "methods.calculus.definite-integrals": ("integral_definite",),
    # Area needs the region described in prose, which no extractor can read.
    "methods.calculus.area": (),
    "methods.calculus.motion": (),
    "methods.calculus.increments": (),
    "methods.calculus.log-derivatives": ("derivative", "integral_indefinite"),
    "methods.exponentials.log-laws": ("expression_equivalence", "solve_equation"),
    "methods.exponentials.log-graphs": ("function_value",),
    "methods.statistics.discrete": (),
    "methods.statistics.bernoulli": (),
    "methods.statistics.binomial": ("binomial",),
    "methods.statistics.continuous": ("integral_definite",),
    "methods.statistics.normal": ("normal",),
    "methods.statistics.sample-proportions": (),
    "methods.statistics.confidence-intervals": (),

    # --- Chemistry -----------------------------------------------------------
    "chemistry.atomic.structure": (),
    "chemistry.atomic.electron-configuration": (),
    "chemistry.atomic.periodic-trends": (),
    "chemistry.bonding.ionic": (),
    "chemistry.bonding.covalent": (),
    "chemistry.bonding.metallic": (),
    "chemistry.bonding.intermolecular": (),
    "chemistry.stoichiometry.equations": ("balance_equation",),
    "chemistry.stoichiometry.mole": ("molar_mass", "percent_composition",
                                     "empirical_formula", "moles_mass"),
    "chemistry.stoichiometry.mass-calculations": ("moles_mass",),
    "chemistry.stoichiometry.limiting": ("limiting_reagent",),
    "chemistry.energy.enthalpy": (),
    "chemistry.energy.bond-energy": (),
    # No kinds: the Unit 2 gas content is qualitative in WACE Chemistry.
    # There is no PV = nRT and no molar volume in the syllabus, so the
    # gas_laws checker would have verified arithmetic no student is asked.
    "chemistry.gases.laws": (),
    "chemistry.solutions.concentration": ("concentration_dilution",),
    "chemistry.solutions.solubility": (),
    "chemistry.solutions.volumetric": ("titration",),
    "chemistry.rates.collision-theory": (),
    "chemistry.equilibrium.constant": ("equilibrium_kc",),
    # An ICE table is a different routine from writing K, and there is not one.
    "chemistry.equilibrium.calculations": (),
    "chemistry.equilibrium.le-chatelier": (),
    "chemistry.acids.conjugate-pairs": (),
    "chemistry.acids.ph-strong": ("ph_strong",),
    "chemistry.acids.weak": ("ph_weak",),
    "chemistry.acids.buffers": (),
    "chemistry.acids.titration-curves": (),
    "chemistry.redox.oxidation-numbers": (),
    # Half-equations balance electrons as well as atoms, and the balancer
    # conserves charge but has no notion of an electron as a species.
    "chemistry.redox.half-equations": (),
    "chemistry.redox.galvanic": (),
    "chemistry.redox.electrolysis": (),
    "chemistry.organic.nomenclature": (),
    "chemistry.organic.isomers": (),
    "chemistry.organic.reactions": (),
    "chemistry.organic.pathways": (),
    "chemistry.synthesis.yield": (),
    "chemistry.synthesis.design": (),
}

# Implemented, tested, and attached to no subtopic. `sig_figs` is a rule about
# how an answer is written rather than a topic anyone is taught, so no leaf of
# the tree is about it. It stays because a generator that produces the
# ambiguous trailing-zero form must be refused by something, and this is that
# something. Check 7.3 prints it as unattached rather than letting it look like
# coverage nobody can reach.
UNATTACHED_KINDS = tuple(
    k for k in KINDS
    if not any(k in kinds for kinds in KINDS_FOR_SUBTOPIC.values())
)


def kinds_for(subtopic_id: str) -> tuple[str, ...]:
    """The verify kinds a subtopic may be filled with. Empty means unfillable."""
    return KINDS_FOR_SUBTOPIC.get(subtopic_id, ())


def fillable(subtopic_id: str) -> bool:
    """Both gates: the syllabus says it is bankable AND a checker exists."""
    sub = syllabus.subtopic(subtopic_id)
    return bool(sub) and syllabus.bankable(sub) and bool(kinds_for(subtopic_id))


def coverage() -> dict[str, dict[str, int]]:
    """Per subject: how many subtopics exist, are bankable, and are fillable.

    Measured off the syllabus and this table together, so the number that gets
    printed is the number the filler will actually act on.
    """
    out: dict[str, dict[str, int]] = {}
    for subject, pool in syllabus.SUBJECTS.items():
        key = syllabus.SUBJECT_KEYS[subject]
        out[key] = {
            "subtopics": len(pool),
            "bankable": sum(1 for s in pool if syllabus.bankable(s)),
            "fillable": sum(1 for s in pool if fillable(s.id)),
        }
    return out
