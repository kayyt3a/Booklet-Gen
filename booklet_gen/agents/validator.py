from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor, implicit_multiplication_application, parse_expr,
    standard_transformations,
)

from ..schemas import Question

log = logging.getLogger(__name__)

X = sp.Symbol("x")

# Richer parsing for calculus: students and LLMs write "3x^2", not "3*x**2",
# and "sin 2x" rather than "sin(2*x)". These transformations accept both.
_MATH_TRANSFORMS = standard_transformations + (
    convert_xor, implicit_multiplication_application,
)


@dataclass
class ValidationResult:
    verified: bool
    notes: Optional[str] = None


def _preprocess(expr: str) -> str:
    s = expr.strip()
    s = s.replace("^", "**")
    s = s.replace("×", "*").replace("·", "*").replace("÷", "/")
    s = s.replace("−", "-")  # unicode minus
    return s


def _parse_expr(s: str):
    return sp.sympify(_preprocess(s), rational=True)


def _safe_parse(s: str):
    """Return the parsed expression or None on any parse error."""
    try:
        return _parse_expr(s)
    except (sp.SympifyError, SyntaxError, TypeError, ValueError):
        return None


_EQUATION_ANSWER = re.compile(r"^\s*([a-zA-Z])\s*=\s*(.+)$")
_MULTI_ANSWER = re.compile(
    r"^\s*([a-zA-Z])\s*=\s*(.+?)\s*(?:,|or)\s*\1\s*=\s*(.+)$", re.IGNORECASE
)

# Characters valid inside an algebraic expression window.
_ALG_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+-*/^(). ")


def _algebraic_window(text: str, eq_pos: int) -> tuple[str, str]:
    """Given a '=' position in text, return (lhs, rhs) windows of contiguous
    algebraic characters on each side."""
    i = eq_pos - 1
    while i >= 0 and text[i] in _ALG_CHARS:
        i -= 1
    j = eq_pos + 1
    while j < len(text) and text[j] in _ALG_CHARS:
        j += 1
    return text[i + 1 : eq_pos].strip(), text[eq_pos + 1 : j].strip()


def _iter_lhs_candidates(lhs_window: str):
    """Progressively drop leading whitespace-separated tokens.

    This handles prompts like 'Solve for x: 2*x + 3' by walking down to the
    largest suffix that sympifies as an expression.
    """
    parts = lhs_window.split()
    for start in range(len(parts)):
        yield " ".join(parts[start:])


def _try_equation(question: str, answer: str) -> Optional[ValidationResult]:
    ans = answer.strip()
    multi = _MULTI_ANSWER.match(ans)
    single = _EQUATION_ANSWER.match(ans)
    if not (multi or single):
        return None

    for pos in [i for i, ch in enumerate(question) if ch == "="]:
        lhs_window, rhs_window = _algebraic_window(question, pos)
        rhs = _safe_parse(rhs_window)
        if rhs is None:
            continue
        for lhs_candidate in _iter_lhs_candidates(lhs_window):
            lhs = _safe_parse(lhs_candidate)
            if lhs is None:
                continue
            # LHS must actually mention a variable — otherwise a bare number
            # like "17" would parse and give a nonsense equation.
            if not lhs.free_symbols:
                continue
            diff = sp.simplify(lhs - rhs)

            def check(var_name: str, val_s: str) -> bool:
                val = _safe_parse(val_s)
                if val is None:
                    return False
                var = sp.Symbol(var_name)
                return sp.simplify(diff.subs(var, val)) == 0

            if multi:
                var_name, v1, v2 = multi.group(1), multi.group(2), multi.group(3)
                if check(var_name, v1) and check(var_name, v2):
                    return ValidationResult(True, "equation solution verified (two roots)")
                return ValidationResult(False, "sympy substitution failed for one or both roots")

            var_name, val_s = single.group(1), single.group(2)
            if check(var_name, val_s):
                return ValidationResult(True, "equation solution verified")
            return ValidationResult(False, f"substitution: {var_name}={val_s} does not satisfy {lhs_candidate} = {rhs_window}")
    return None


def _try_direct_computation(question: str, answer: str) -> Optional[ValidationResult]:
    """For compute/simplify style questions: find the largest sympifiable
    algebraic window in the question and compare it to the answer."""
    expected = _safe_parse(answer)
    if expected is None:
        return None

    windows: list[str] = []
    current = []
    for ch in question:
        if ch in _ALG_CHARS:
            current.append(ch)
        else:
            if current:
                windows.append("".join(current).strip())
                current = []
    if current:
        windows.append("".join(current).strip())

    for window in sorted(windows, key=len, reverse=True):
        if len(window) < 3 or not any(op in window for op in "+-*/^"):
            continue
        # Try progressively shorter prefixes and suffixes by dropping tokens.
        parts = window.split()
        for start in range(len(parts)):
            for end in range(len(parts), start, -1):
                candidate = " ".join(parts[start:end])
                if not any(op in candidate for op in "+-*/^"):
                    continue
                expr = _safe_parse(candidate)
                if expr is None:
                    continue
                try:
                    if sp.simplify(expr - expected) == 0:
                        return ValidationResult(True, "expression simplified matches answer")
                except (TypeError, ValueError):
                    continue
    return ValidationResult(False, f"no matching computation found for answer {answer!r}")


# ---------------------------------------------------------------- calculus
#
# Year 11/12 Methods questions are mostly differentiate / integrate / evaluate.
# These are symbolically checkable, which matters more for an exam paper than a
# practice booklet: a wrong marking key on an exam is a serious defect.
#
# Every helper returns None when it cannot confidently parse the question, so
# the caller falls back to the LLM judge rather than rejecting a good question.

_DERIV_KW = (
    "differentiate", "derivative", "dy/dx", "d/dx", "f'(", "gradient function",
)
_INTEGRAL_KW = (
    "integrate", "integral", "antiderivative", "anti-derivative", "primitive", "∫",
)

# "y = ...", "f(x) = ...", "g(x) = ..." up to a sentence break.
_FUNC_DEF = re.compile(
    r"(?:\by\b|\bf\s*\(\s*x\s*\)|\bg\s*\(\s*x\s*\))\s*=\s*([^,.;?]+)", re.I,
)
# Strip "dy/dx =", "f'(x) =", "y' =" from the front of an answer.
_ANS_PREFIX = re.compile(
    r"^\s*(?:dy\s*/\s*dx|d\s*/\s*dx|f\s*'\s*\(\s*x\s*\)|g\s*'\s*\(\s*x\s*\)|y\s*'"
    r"|f\s*'|answer)\s*[:=]?\s*", re.I,
)
# Integrand: everything between the integral cue and "dx".
_INTEGRAND = re.compile(
    r"(?:∫|integrate|integral of|antiderivative of|anti-derivative of|primitive of)"
    r"\s*(.+?)\s*(?:\bdx\b|with respect to\s*x|$)",
    re.I | re.S,
)
_DEF_LIMITS = re.compile(
    r"(?:from|between)\s+(-?[\d./]+)\s+(?:to|and)\s+(-?[\d./]+)", re.I,
)
# "f'(2)", "find f'(-1)" — derivative evaluated at a point.
_DERIV_AT = re.compile(r"f\s*'\s*\(\s*(-?[\d./]+)\s*\)", re.I)


# Trailing prose that commonly rides along with a captured expression, e.g.
# "y = x^3 + 2x with respect to x".
_TRAILING_PROSE = re.compile(
    r"\s*(?:with respect to\s*\w*|for\s+x|in terms of\s*\w*|where.*|dx)\s*$", re.I,
)

# In Methods, a bare "e" is Euler's number, never a variable. Without this it
# parses as a free symbol and derivatives come out as e**x*log(e).
_MATH_LOCALS = {"e": sp.E, "E": sp.E, "pi": sp.pi}


def _parse_math(s: str):
    """Parse a maths expression, tolerating implicit multiplication and carets.

    Handles the forms an LLM actually emits ("3x^2 + 2x", "sin(2x)", "e^x")
    which the plain sympify path in `_safe_parse` rejects. Returns None on
    failure so the caller can defer rather than wrongly reject.
    """
    if not s or not s.strip():
        return None
    txt = _preprocess(s).strip().rstrip(".,;:")
    txt = _TRAILING_PROSE.sub("", txt).strip()
    # Drop a trailing integration constant so antiderivatives compare cleanly.
    txt = re.sub(r"[+\-]\s*[cC]\s*$", "", txt).strip()
    if not txt:
        return None
    try:
        return parse_expr(txt, transformations=_MATH_TRANSFORMS,
                          local_dict=_MATH_LOCALS)
    except Exception:
        return None


def _is_zero(expr) -> bool:
    """True if expr is identically zero. Tries symbolic simplification first,
    then a multi-point numeric check for expressions simplify can't crack."""
    try:
        if sp.simplify(expr) == 0:
            return True
    except Exception:
        pass
    # Numeric confirmation at several arbitrary points. Only reached when
    # simplify was inconclusive, and several agreeing points is strong evidence.
    try:
        for v in (0.37, 1.53, 2.71, 4.19):
            val = complex(expr.subs(X, v))
            if abs(val) > 1e-7:
                return False
        return True
    except Exception:
        return False


def _check_derivative(question: str, answer: str) -> Optional[ValidationResult]:
    m = _FUNC_DEF.search(question)
    if not m:
        return None
    fn = _parse_math(m.group(1))
    if fn is None or X not in fn.free_symbols:
        return None
    try:
        expected = sp.diff(fn, X)
    except Exception:
        return None

    # "Find f'(2)" — a derivative evaluated at a point, so the answer is a number.
    at = _DERIV_AT.search(question)
    if at:
        point = _parse_math(at.group(1))
        given = _parse_math(_ANS_PREFIX.sub("", answer))
        if point is None or given is None:
            return None
        try:
            want = sp.simplify(expected.subs(X, point))
        except Exception:
            return None
        if _is_zero(sp.nsimplify(want) - given):
            return ValidationResult(True, f"derivative at a point verified: f'({point}) = {want}")
        return ValidationResult(
            False, f"f'({point}) evaluates to {want}, but the answer says {given}",
        )

    given = _parse_math(_ANS_PREFIX.sub("", answer))
    if given is None:
        return None
    if _is_zero(expected - given):
        return ValidationResult(True, f"derivative verified: d/dx({fn}) = {sp.simplify(expected)}")
    return ValidationResult(
        False,
        f"derivative of {fn} is {sp.simplify(expected)}, but the answer says {given}",
    )


def _check_integral(question: str, answer: str) -> Optional[ValidationResult]:
    m = _INTEGRAND.search(question)
    if not m:
        return None
    raw = m.group(1)
    # Strip a leading "the"/"of" and any stated limits from the integrand text.
    raw = re.sub(r"^(?:the|of)\s+", "", raw.strip(), flags=re.I)
    raw = _DEF_LIMITS.sub("", raw).strip()
    integrand = _parse_math(raw)
    if integrand is None:
        return None

    limits = _DEF_LIMITS.search(question)
    if limits:
        lo, hi = _parse_math(limits.group(1)), _parse_math(limits.group(2))
        given = _parse_math(_ANS_PREFIX.sub("", answer))
        if lo is None or hi is None or given is None:
            return None
        try:
            want = sp.integrate(integrand, (X, lo, hi))
        except Exception:
            return None
        if want.has(sp.Integral):       # sympy could not evaluate it
            return None
        if _is_zero(sp.simplify(want - given)):
            return ValidationResult(
                True, f"definite integral verified: value {sp.simplify(want)}")
        return ValidationResult(
            False,
            f"definite integral of {integrand} from {lo} to {hi} is "
            f"{sp.simplify(want)}, but the answer says {given}",
        )

    # Indefinite: differentiating the student's antiderivative must return the
    # integrand. This is more robust than comparing antiderivatives directly,
    # and it makes the "+ c" term irrelevant.
    given = _parse_math(_ANS_PREFIX.sub("", answer))
    if given is None or X not in given.free_symbols:
        return None
    try:
        back = sp.diff(given, X)
    except Exception:
        return None
    if _is_zero(back - integrand):
        return ValidationResult(True, f"antiderivative verified: d/dx(answer) = {integrand}")
    return ValidationResult(
        False,
        f"differentiating the answer gives {sp.simplify(back)}, "
        f"which is not the integrand {integrand}",
    )


def _try_calculus(question: str, answer: str) -> Optional[ValidationResult]:
    """Verify a differentiate/integrate question, or None if not one."""
    low = question.lower()
    try:
        if any(k in low for k in _INTEGRAL_KW):
            return _check_integral(question, answer)
        if any(k in low for k in _DERIV_KW):
            return _check_derivative(question, answer)
    except Exception as e:            # never let a parse quirk break generation
        log.info("validator.calculus_skipped", extra={"error": str(e)[:200]})
        return None
    return None


class SympyValidator:
    def validate(self, q: Question) -> ValidationResult:
        try:
            # Calculus first: it is keyword-gated and more specific, so it must
            # win before the generic expression matcher sees the question.
            calc = _try_calculus(q.question, q.answer)
            if calc is not None:
                log.info("validator.calculus", extra={"verified": calc.verified})
                return calc

            eq_result = _try_equation(q.question, q.answer)
            if eq_result is not None:
                log.info("validator.equation", extra={"verified": eq_result.verified})
                return eq_result

            direct = _try_direct_computation(q.question, q.answer)
            if direct is not None:
                log.info("validator.direct", extra={"verified": direct.verified})
                return direct

            log.info("validator.skipped", extra={"reason": "no verifiable pattern"})
            return ValidationResult(False, "no symbolically verifiable pattern detected")
        except Exception as e:
            log.warning("validator.error", extra={"error": str(e)[:200]})
            return ValidationResult(False, f"validator error: {e}")
