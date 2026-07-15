from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import sympy as sp

from ..schemas import Question

log = logging.getLogger(__name__)


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


class SympyValidator:
    def validate(self, q: Question) -> ValidationResult:
        try:
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
