"""Deterministic validation for auto-checkable reasoning questions.

The LLM judge is fine for open-ended verbal reasoning, but it waves through
a specific failure mode that a computer can catch exactly: letter-shift
ciphers and number sequences whose stated example is internally
inconsistent (no derivable rule) or whose answer simply doesn't follow the
pattern.

Real example this was built to catch:
    "If 'FLUTE' is coded as 'GNVSF', how is 'PIANO' coded?"
    F->G (+1), L->N (+2), U->V (+1), T->S (-1), E->F (+1)
    The shifts are +1,+2,+1,-1,+1 — no single rule — so PIANO's code is
    not derivable and the question is broken. The LLM judge passed it.

This validator returns:
    None                       -> not a checkable type; caller uses LLM judge
    ValidationResult(True)     -> rule found and the answer follows it
    ValidationResult(False)    -> definitively broken (no rule, or wrong answer)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..schemas import Question
from .validator import ValidationResult

log = logging.getLogger(__name__)

# Quotes people actually type, straight and curly.
_QUOTED = re.compile(r"['\"‘’“”]([A-Za-z]{2,})['\"‘’“”]")


def _letters_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isalpha()).upper()


def _shift_letter(ch: str, k: int) -> str:
    base = ord("A")
    return chr((ord(ch.upper()) - base + k) % 26 + base)


class ReasoningValidator:
    def validate(self, q: Question) -> Optional[ValidationResult]:
        cipher = self._check_cipher(q)
        if cipher is not None:
            log.info("reasoning_validator.cipher", extra={"verified": cipher.verified})
            return cipher
        seq = self._check_sequence(q)
        if seq is not None:
            log.info("reasoning_validator.sequence", extra={"verified": seq.verified})
            return seq
        return None

    # ---------- letter-shift ciphers ----------

    def _check_cipher(self, q: Question) -> Optional[ValidationResult]:
        text = q.question
        if "cod" not in text.lower() and "cipher" not in text.lower():
            return None

        words = _QUOTED.findall(text)
        if len(words) < 3:
            # Fall back to bare ALL-CAPS tokens (>=2 letters).
            words = re.findall(r"\b([A-Z]{2,})\b", text)
        if len(words) < 3:
            return None

        source, code, target = words[0].upper(), words[1].upper(), words[2].upper()
        if len(source) != len(code):
            return None  # not a straight per-position cipher — leave to the judge

        ans_words = _QUOTED.findall(q.answer)
        provided = (ans_words[0].upper() if ans_words else _letters_only(q.answer))
        if not provided:
            return None

        # Per-position shift, normalised to the signed range (-13, 13].
        shifts: list[int] = []
        for s_ch, c_ch in zip(source, code):
            d = (ord(c_ch) - ord(s_ch)) % 26
            if d > 13:
                d -= 26
            shifts.append(d)

        rule = self._derive_shift_rule(shifts)
        if rule is None:
            return ValidationResult(
                False,
                f"cipher example {source!r}->{code!r} follows no consistent shift "
                f"rule (per-letter shifts {shifts}); the target code is not "
                f"derivable, so a student cannot solve it",
            )

        derived = []
        for i, ch in enumerate(target):
            k = rule[1] if rule[0] == "const" else rule[1] + i * rule[2]
            derived.append(_shift_letter(ch, k))
        derived_s = "".join(derived)

        if derived_s == provided:
            return ValidationResult(True, f"cipher verified ({rule[0]} shift): {target}->{derived_s}")
        return ValidationResult(
            False,
            f"cipher rule ({rule[0]} shift) gives {target}->{derived_s}, "
            f"but the answer says {provided!r}",
        )

    @staticmethod
    def _derive_shift_rule(shifts: list[int]):
        """Return ('const', k) or ('arith', s0, diff), else None.

        Covers the two cipher rules selective tests actually use: a fixed
        Caesar shift, and a positional shift that steps by a constant amount
        (+1,+2,+3,...). Anything else is treated as having no derivable rule.
        """
        if not shifts:
            return None
        if all(s == shifts[0] for s in shifts):
            return ("const", shifts[0])
        diffs = [shifts[i + 1] - shifts[i] for i in range(len(shifts) - 1)]
        if diffs and all(d == diffs[0] for d in diffs):
            return ("arith", shifts[0], diffs[0])
        return None

    # ---------- number sequences ----------

    def _check_sequence(self, q: Question) -> Optional[ValidationResult]:
        low = q.question.lower()
        if not any(kw in low for kw in (
            "sequence", "next number", "series", "comes next",
            "next term", "pattern of numbers",
        )):
            return None

        # Longest comma-separated run of >=3 integers in the question.
        runs = re.findall(r"-?\d+(?:\s*,\s*-?\d+){2,}", q.question)
        if not runs:
            return None
        seq = [int(x) for x in re.findall(r"-?\d+", max(runs, key=len))]
        if len(seq) < 3:
            return None

        ans_m = re.search(r"-?\d+", q.answer)
        if not ans_m:
            return None
        provided = int(ans_m.group())

        diffs = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
        if all(d == diffs[0] for d in diffs):
            expected = seq[-1] + diffs[0]
            if expected == provided:
                return ValidationResult(True, f"arithmetic sequence (d={diffs[0]}); next={expected}")
            return ValidationResult(
                False, f"arithmetic sequence (d={diffs[0]}) next is {expected}, not {provided}",
            )

        if all(x != 0 for x in seq[:-1]) and all(
            seq[i + 1] % seq[i] == 0 for i in range(len(seq) - 1)
        ):
            ratios = [seq[i + 1] // seq[i] for i in range(len(seq) - 1)]
            if all(r == ratios[0] for r in ratios):
                expected = seq[-1] * ratios[0]
                if expected == provided:
                    return ValidationResult(True, f"geometric sequence (r={ratios[0]}); next={expected}")
                return ValidationResult(
                    False, f"geometric sequence (r={ratios[0]}) next is {expected}, not {provided}",
                )

        # Not a clean arithmetic/geometric run (could be squares, Fibonacci,
        # primes...). Defer to the judge rather than risk a false rejection.
        return None
