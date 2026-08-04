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
    # True when the verdict is a proof, in either direction: the checker read
    # the question's whole computation and either confirmed the answer or
    # refuted it. False means "I could not tell", and the caller must hand the
    # question to the LLM judge rather than treat the verdict as final.
    #
    # A PARTIAL match is not a pass, it is inconclusive. That distinction is
    # the whole point of this field: the old validator returned a bare
    # verified=True when any fragment of the question happened to equal the
    # answer, and the pipeline short-circuited the judge on it.
    conclusive: bool = True


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


# "3 x 4" is a multiplication written the primary-school way, not an
# expression in the variable x. Implicit multiplication would happily read it
# as 12x and then "refute" a perfectly good answer.
_TIMES_IDIOM = re.compile(r"\d\s+[xX]\s+\d")


def _parse_side(text: str, var: sp.Symbol, allow_var: bool):
    """Parse one side of an equation, or None if it is not clean algebra.

    Plain sympify first, then the implicit-multiplication parser so that "2x +
    3" is read the way a Year 8 textbook writes it. The result is accepted only
    when its symbols are exactly what we expect, which keeps prose windows
    ("costs 5 dollars") from parsing into a pile of one-letter symbols.
    """
    for parse in (_safe_parse, _parse_math):
        if parse is _parse_math and _TIMES_IDIOM.search(text):
            continue
        expr = parse(text)
        if expr is None or not isinstance(expr, sp.Expr):
            continue
        symbols = expr.free_symbols
        if allow_var and symbols != {var}:
            continue
        if not allow_var and not symbols <= {var}:
            continue
        return expr
    return None


def _try_equation(question: str, answer: str) -> Optional[ValidationResult]:
    ans = answer.strip()
    multi = _MULTI_ANSWER.match(ans)
    single = _EQUATION_ANSWER.match(ans)
    if not (multi or single):
        return None
    var = sp.Symbol((multi or single).group(1))

    for pos in [i for i, ch in enumerate(question) if ch == "="]:
        lhs_window, rhs_window = _algebraic_window(question, pos)
        rhs = _parse_side(rhs_window, var, allow_var=False)
        if rhs is None:
            continue
        for lhs_candidate in _iter_lhs_candidates(lhs_window):
            # LHS must actually mention the variable the answer solves for.
            # Otherwise a bare number like "17" parses and gives a nonsense
            # equation, and prose gives a nonsense variable.
            lhs = _parse_side(lhs_candidate, var, allow_var=True)
            if lhs is None:
                continue
            diff = sp.simplify(lhs - rhs)

            def check(val_s: str) -> Optional[bool]:
                """True/False if the root can be tested, None if it cannot.

                An answer we cannot parse ("x = 4 cm") is not a wrong answer,
                so it must defer rather than be refuted.
                """
                val = _safe_parse(val_s.strip().rstrip("."))
                if val is None or not isinstance(val, sp.Expr):
                    return None
                return sp.simplify(diff.subs(var, val)) == 0

            if multi:
                v1, v2 = multi.group(2), multi.group(3)
                first, second = check(v1), check(v2)
                if first is None or second is None:
                    return None
                if first and second:
                    return ValidationResult(True, "equation solution verified (two roots)")
                return ValidationResult(False, "sympy substitution failed for one or both roots")

            val_s = single.group(2)
            ok = check(val_s)
            if ok is None:
                return None
            if ok:
                return ValidationResult(True, "equation solution verified")
            return ValidationResult(
                False,
                f"substitution: {var}={val_s} does not satisfy "
                f"{lhs_candidate} = {rhs_window}",
            )
    return None


# ------------------------------------------------------- direct computation
#
# This path used to scan the question for ANY window of tokens that sympified
# and pass the question if any window equalled the answer. That is an existence
# check, not a verification, and it blessed the two commonest wrong answers in
# primary maths:
#
#   "Calculate 11/15 - 3/15 - 2/15."  answered 8/15   (11/15 - 3/15 is a window)
#   "Subtract 4/9 from 8/9."          answered 4/9    (an operand is a window)
#   "Sam had 20 apples and gave away 5 + 3 of them."  answered 8  (5 + 3)
#
# The rule now is the opposite: fire only when the question IS a computation
# that can be read whole, and compare the answer to the value of that whole
# computation. Anything less is inconclusive and goes to the LLM judge. Word
# problems, where the arithmetic a student must do is not written down, are
# exactly the case this path must decline.

# A token made only of digits, operators, brackets and decimal points. Letters
# are deliberately excluded: "apples", "of", "from" and "x" are not operands.
_MATH_TOKEN = re.compile(r"^[0-9()+\-*/.]+$")

# Prose that may precede the computation. Anything outside this list means the
# numbers are embedded in a story rather than presented as a sum, and the
# question is not a direct computation. Keeping this list tight is what stops
# "Tom has 5 apples and buys 3 + 2 more" being read as "3 + 2".
_INSTRUCTION_WORDS = {
    "calculate", "compute", "evaluate", "simplify", "work", "out", "find",
    "solve", "what", "whats", "what's", "is", "the", "value", "of", "sum",
    "total", "difference", "product", "quotient", "answer", "result", "to",
    "give", "please", "now", "does", "equal", "equals", "determine", "state",
    "add", "subtract", "multiply", "divide", "much", "how",
}

# Sentence break: a full stop followed by whitespace or end of string, so
# decimals such as "3.5" stay intact.
_SEGMENT_SPLIT = re.compile(r"[?!,;:=\n]+|\.(?=\s|$)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
# Guards against a pathological exponent locking up sympy.
_BIG_POWER = re.compile(r"\*\*\s*\d{4,}")

# Wording elsewhere in the question that adds a step the written sum does not
# contain: "Calculate 3 + 4. Then double the result." states 3 + 4 but wants
# 14, and "Round your answer to 1 decimal place" changes the expected form.
# Seeing any of these means the written computation is not the whole task, so
# no verdict on it can be final.
_FOLLOW_UP_STEP = re.compile(
    r"\b(then|next|after that|double|doubling|halve|half|triple|treble|square|"
    r"cube|add|subtract|multiply|divide|times|increase|decrease|round|rounded|"
    r"convert|remainder|left over|estimate|nearest)\b", re.I,
)


def _is_math_token(tok: str) -> bool:
    return bool(_MATH_TOKEN.match(tok))


def _has_binary_operator(expr: str) -> bool:
    """True when an operator is used between two operands, not as a sign.

    "-3" is a negative number, "8 - 3" is a subtraction. Without this a bare
    number in the question would count as a computation.
    """
    prev = ""
    for ch in expr:
        if ch in "+-*/" and prev in "0123456789.)":
            return True
        if not ch.isspace():
            prev = ch
    return False


def _segment_computation(segment: str) -> Optional[str]:
    """The computation a segment states outright, or None if it states none.

    A segment qualifies only when its maths runs unbroken to the end of the
    segment and anything before it is plain instruction wording. Prose sitting
    between the numbers ("Subtract 4/9 from 8/9") or after them ("1/3 play
    soccer") disqualifies it, because then the arithmetic on the page is not
    the arithmetic the student must do.
    """
    tokens = segment.split()
    if not tokens:
        return None
    # "12 x 5" is a multiplication in every primary textbook in the country.
    for i, tok in enumerate(tokens):
        if tok in {"x", "X"} and 0 < i < len(tokens) - 1 \
                and _is_math_token(tokens[i - 1]) and _is_math_token(tokens[i + 1]):
            tokens[i] = "*"

    math_positions = [i for i, t in enumerate(tokens) if _is_math_token(t)]
    if not math_positions:
        return None
    first, last = math_positions[0], math_positions[-1]
    if last != len(tokens) - 1:
        return None                                  # prose trails the maths
    if len(math_positions) != last - first + 1:
        return None                                  # prose interrupts the maths
    for tok in tokens[:first]:
        if tok.strip(".()'\"").lower() not in _INSTRUCTION_WORDS:
            return None                              # a story, not an instruction

    candidate = " ".join(tokens[first:])
    if not any(ch.isdigit() for ch in candidate):
        return None
    if not _has_binary_operator(candidate):
        return None
    if first == 0 and not _has_binary_operator(candidate.replace("/", "")):
        # No instruction word introduced it and its only operator is a slash,
        # so this is a fraction quoted in prose ("0.5, 1/4, 0.75" in a
        # put-these-in-order question), not a division to perform.
        return None
    return candidate


def _computations_in(question: str) -> Optional[tuple[list[tuple[str, object]], bool]]:
    """([(computation, its value)], whether the question implies more steps).

    None when a computation could not be parsed. That matters: a computation we
    cannot read is one we cannot claim to have checked, so the question must go
    to the judge rather than be judged on the fragments we did read.
    """
    text = _THOUSANDS.sub("", _preprocess(question))
    found: list[tuple[str, object]] = []
    leftover: list[str] = []
    for segment in _SEGMENT_SPLIT.split(text):
        candidate = _segment_computation(segment)
        if candidate is None:
            leftover.append(segment)
            continue
        if _BIG_POWER.search(candidate):
            return None
        expr = _safe_parse(candidate)
        if expr is None or not _is_scalar(expr):
            return None
        found.append((candidate, expr))
    more_steps = bool(_FOLLOW_UP_STEP.search(" ".join(leftover)))
    return found, more_steps


_ANSWER_LEAD = re.compile(r"^\s*(?:the\s+)?(?:answer|ans|result)\s*(?:is)?\s*[:=]?\s*", re.I)
# "12 cm", "8 apples", "5 students". The value first, a unit or noun after.
_ANSWER_WITH_UNIT = re.compile(r"^([0-9][0-9+\-*/().\s]*?)\s*[a-zA-Z%][a-zA-Z%.\s]*$")


def _is_scalar(expr) -> bool:
    """True for a single definite number. Rejects tuples, lists and booleans,
    which sympify happily returns for "1/4, 0.5, 0.75" or "3 < 5"."""
    return isinstance(expr, sp.Expr) and bool(expr.is_number)


def parse_answer_value(answer: str) -> Optional[tuple[object, bool]]:
    """(value, exact) for an answer that names a number, else None.

    `exact` is False when a trailing unit or noun had to be stripped to make it
    parse. Such an answer is still good enough to CONFIRM a computation, but
    not to refute one, because the stripped words may have carried meaning (a
    unit conversion, say) that the bare number does not.
    """
    if not answer:
        return None
    text = _THOUSANDS.sub("", _preprocess(answer))
    text = _ANSWER_LEAD.sub("", text).strip().lstrip("$").strip()
    text = text.rstrip(".!").strip()
    if not text:
        return None
    exact = True
    expr = _safe_parse(text)
    if expr is None:
        m = _ANSWER_WITH_UNIT.match(text)
        if not m:
            return None
        exact = False
        expr = _safe_parse(m.group(1))
        if expr is None:
            return None
    if not _is_scalar(expr):
        return None
    return expr, exact


def _values_match(a, b) -> bool:
    """True when two parsed values are the same number."""
    try:
        if sp.simplify(a - b) == 0:
            return True
    except (TypeError, ValueError, AttributeError):
        return False
    try:
        return abs(complex(sp.N(a - b))) < 1e-9
    except (TypeError, ValueError, AttributeError):
        return False


def answers_conflict(proposed: str, solved: str) -> bool:
    """True only when both answers name a number and the numbers differ.

    Used to cross-check the LLM judge against its own working: a judge that
    solves a question one way and then verifies a different answer has not
    graded anything. Anything that is not a plain numeric answer (prose,
    multi-part, algebraic) returns False rather than risk a false alarm.
    """
    left = parse_answer_value(proposed or "")
    right = parse_answer_value(solved or "")
    if left is None or right is None:
        return False
    return not _values_match(left[0], right[0])


def _try_direct_computation(question: str, answer: str) -> Optional[ValidationResult]:
    """Verify a question that states its computation, e.g. "Calculate 3/8 + 2/8".

    Returns None when the question is not of that shape, so the caller defers
    to the LLM judge.
    """
    parsed = parse_answer_value(answer)
    if parsed is None:
        return None
    expected, exact = parsed

    read = _computations_in(question)
    if read is None:
        return None
    values, more_steps = read
    if not values:
        return None

    matches = [c for c, expr in values if _values_match(expr, expected)]

    if more_steps:
        # The question asks for something beyond the sum it prints, so the
        # printed sum is not the answer and cannot settle anything.
        return ValidationResult(
            bool(matches),
            "question adds a step the written computation does not cover; "
            "needs independent grading",
            conclusive=False,
        )

    if len(values) == 1:
        candidate, expr = values[0]
        try:
            value = sp.nsimplify(sp.simplify(expr))
        except Exception:            # display only, never worth failing over
            value = expr
        if matches:
            return ValidationResult(
                True, f"full computation verified: {candidate} = {value}")
        if not exact:
            # The answer carried a unit we had to strip, so a mismatch may be a
            # conversion rather than an error. Not ours to call.
            return ValidationResult(
                False,
                f"{candidate} = {value}, but the answer {answer!r} carries units; "
                "deferring",
                conclusive=False,
            )
        return ValidationResult(
            False,
            f"the question computes {candidate} = {value}, "
            f"but the answer says {answer.strip()}",
        )

    # More than one computation is written down, so this is a multi-step
    # question and no single one of them is the answer. Matching one is a
    # partial result, which is exactly what must NOT be reported as verified.
    if matches:
        return ValidationResult(
            True,
            f"answer matches only one step ({matches[0]}) of a multi-step "
            "question; needs independent grading",
            conclusive=False,
        )
    return ValidationResult(
        False,
        "question states several computations and the answer matches none of "
        "them; needs independent grading",
        conclusive=False,
    )


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
# "f'(2)", "find f'(-1)": derivative evaluated at a point.
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

    # "Find f'(2)": a derivative evaluated at a point, so the answer is a number.
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
                log.info("validator.calculus",
                         extra={"verified": calc.verified, "conclusive": calc.conclusive})
                return calc

            eq_result = _try_equation(q.question, q.answer)
            if eq_result is not None:
                log.info("validator.equation",
                         extra={"verified": eq_result.verified,
                                "conclusive": eq_result.conclusive})
                return eq_result

            direct = _try_direct_computation(q.question, q.answer)
            if direct is not None:
                log.info("validator.direct",
                         extra={"verified": direct.verified,
                                "conclusive": direct.conclusive})
                return direct

            log.info("validator.skipped", extra={"reason": "no verifiable pattern"})
            return ValidationResult(
                False, "no symbolically verifiable pattern detected", conclusive=False)
        except Exception as e:
            log.warning("validator.error", extra={"error": str(e)[:200]})
            # An internal error proves nothing about the answer, so let the
            # judge see the question rather than silently failing it.
            return ValidationResult(False, f"validator error: {e}", conclusive=False)
