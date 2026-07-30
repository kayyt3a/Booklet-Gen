"""Deterministic guards against two failure modes the LLM judge waves through.

Both were found in a real Year 5 booklet that had passed validation:

1. A "verified" answer whose own working contradicted it. The key printed
   "Answer: 75" above working that concluded 80, with the model's internal
   monologue ("Wait, recalculating:", "Correction:") left in student-facing
   text. The judge grades the answer against the question, so working that
   disagrees with the answer is invisible to it.

2. A diagram whose labels contradicted the question it illustrated: the text
   said a tank was 40cm x 20cm x 10cm, the drawing beside it was labelled
   4cm x 2cm x 1cm. The model routinely emits a scaled-down spec for
   drawability and forgets that the labels are what the student reads.

Neither is repairable by prompting alone, because both are cases of the model
being locally plausible and globally wrong.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1. Working that contradicts, or visibly second-guesses, its own answer
# --------------------------------------------------------------------------

# Phrases that only appear when the model is thinking out loud. A student-facing
# answer key should never contain them, and their presence is a strong signal
# that the model changed its mind partway and the stated answer may be stale.
# Note the absence of a trailing \b: several of these end in punctuation, and
# \b after a colon or comma can never match, which silently disabled
# "Correction:" and "Actually," until it was tested.
_SELF_CORRECTION = re.compile(
    r"\b(wait\b|hold on\b|actually,|let me recalculate\b|recalculating\b"
    r"|correction:|i made an error\b|that'?s wrong\b|scratch that\b"
    r"|on second thought\b|apologies\b|my mistake\b|let me redo\b)",
    re.IGNORECASE,
)

# Fractions must be matched before bare numbers, or "5/8" is read as 5 and 8.
# Covers "5/8" and the Unicode form the formatter renders, since working is
# checked before and after prettification depending on the call site.
_FRACTION = re.compile(r"(\d+)\s*[/⁄]\s*(\d+)")
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

_SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _numbers(text: str) -> list[float]:
    """Every value in the text, with fractions resolved to their quotient.

    Fractions are extracted first and blanked out, so "3/8 + 2/8 = 5/8" yields
    the three quotients rather than six loose integers. Without this, a
    fractions booklet, which is most of a primary maths booklet, bypasses the
    contradiction check entirely.
    """
    text = (text or "").translate(_SUP).translate(_SUB)
    out = []
    def take_fraction(m):
        num, den = float(m.group(1)), float(m.group(2))
        if den:
            out.append(num / den)
        return " "
    text = _FRACTION.sub(take_fraction, text)
    for raw in _NUMBER.findall(text):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def has_self_correction(working: str) -> bool:
    return bool(_SELF_CORRECTION.search(working or ""))


def working_contradicts_answer(answer: str, working: str) -> bool:
    """True when the working's concluding value disagrees with the answer.

    Deliberately conservative: only fires when the answer is a single clean
    number and the working ends on a different one. Answers that are
    fractions, expressions, sentences, or multi-part are left alone rather
    than risk rejecting good questions.
    """
    if not answer or not working:
        return False

    # Reject only when the answer reduces to a single value. Multi-part
    # answers ("a) 32 b) 64") are out of scope rather than risk false
    # rejections. A fraction counts as one value, not two.
    ans_nums = _numbers(answer)
    if len(ans_nums) != 1:
        return False

    work_nums = _numbers(working)
    if not work_nums:
        return False

    target = ans_nums[0]
    # The answer is fine if it appears anywhere in the working: models often
    # state it mid-derivation and then restate units or simplify afterwards.
    if any(abs(n - target) < 1e-9 for n in work_nums):
        return False

    # A simplification key derives the parts, not the fraction: "Answer: 1/2"
    # over "4/4 = 1, 8/4 = 2" never writes 1/2 anywhere. Accept when both the
    # numerator and denominator appear. Without this the check strips the mark
    # from every simplify-this-fraction question in the booklet.
    frac = _FRACTION.fullmatch(answer.strip())
    if frac:
        num, den = float(frac.group(1)), float(frac.group(2))
        have = lambda v: any(abs(n - v) < 1e-9 for n in work_nums)
        if have(num) and have(den):
            return False

    # Nowhere in the working does the stated answer appear. That is the Q63
    # case: "Answer: 75" over working that only ever produces 80.
    return True


def answer_is_trustworthy(answer: str, working: str) -> tuple[bool, Optional[str]]:
    """(ok, reason). Used to strip a verified mark that was not earned."""
    if has_self_correction(working):
        return False, "working contains model self-correction"
    if working_contradicts_answer(answer, working):
        return False, "stated answer does not appear anywhere in the working"
    return True, None


# --------------------------------------------------------------------------
# 2. Diagram labels that contradict the question
# --------------------------------------------------------------------------

# "40 cm long", "length of 40 cm", "40 metres wide", "height of 10".
_DIM_PATTERNS = {
    "length": [r"(\d+(?:\.\d+)?)\s*(?:cm|mm|m|metres?|meters?|units?|blocks?|cubes?)?\s*(?:long|in length)",
               r"length\s*(?:of|is|=)?\s*(\d+(?:\.\d+)?)"],
    "width": [r"(\d+(?:\.\d+)?)\s*(?:cm|mm|m|metres?|meters?|units?|blocks?|cubes?)?\s*(?:wide|in width)",
              r"width\s*(?:of|is|=)?\s*(\d+(?:\.\d+)?)"],
    "height": [r"(\d+(?:\.\d+)?)\s*(?:cm|mm|m|metres?|meters?|units?|blocks?|cubes?)?\s*(?:high|tall|deep|in height)",
               r"(?:height|depth)\s*(?:of|is|=)?\s*(\d+(?:\.\d+)?)"],
    "radius": [r"radius\s*(?:of|is|=)?\s*(\d+(?:\.\d+)?)"],
}

# Unit as written in the question, so the drawing is labelled the same way.
# Ordered longest-first so "metres" is not matched as "m" mid-word.
_UNIT_RE = re.compile(
    r"\b(?:centimetres?|centimeters?|millimetres?|millimeters?|metres?|meters?"
    r"|cm|mm|km|m)\b", re.IGNORECASE)

_UNIT_CANON = {
    "centimetre": "cm", "centimeter": "cm", "centimetres": "cm", "centimeters": "cm",
    "millimetre": "mm", "millimeter": "mm", "millimetres": "mm", "millimeters": "mm",
    "metre": "m", "meter": "m", "metres": "m", "meters": "m",
}


def dimensions_in_text(text: str) -> dict:
    """Pull labelled dimensions out of a question. Missing keys stay absent."""
    found = {}
    low = (text or "").lower()
    for key, patterns in _DIM_PATTERNS.items():
        for pat in patterns:
            m = re.search(pat, low)
            if m:
                try:
                    found[key] = float(m.group(1))
                except ValueError:
                    pass
                break
    return found


def units_in_text(text: str) -> set:
    """Every distinct unit named in the text, canonicalised."""
    out = set()
    for raw in _UNIT_RE.findall(text or ""):
        low = raw.lower()
        out.add(_UNIT_CANON.get(low, low))
    return out


def unit_in_text(text: str) -> Optional[str]:
    """The question's single unit, or None when it mixes units.

    Returning the *first* unit found and stamping it on every label was
    actively harmful: "1 metre long, 50 cm wide, 20 cm deep" relabelled a
    correct 100 x 50 x 20 cm spec as 1 x 50 x 20 **metres**, inventing the
    very defect this module exists to prevent. Mixed-unit capacity questions
    are standard Year 5 work, so bail rather than guess.
    """
    units = units_in_text(text)
    if len(units) != 1:
        return None
    return next(iter(units))


def reconcile_diagram_spec(spec: dict, question_text: str) -> tuple[dict, bool]:
    """Correct a diagram spec so its labels match the question.

    Returns (spec, changed). The diagram is never discarded: a wrong label is
    fixable, and a missing picture helps nobody. Only keys the question states
    explicitly are overridden, so a spec is never invented from nothing.
    """
    if not spec or not isinstance(spec, dict):
        return spec, False

    kind = str(spec.get("type", "")).lower()
    if kind not in {"cuboid", "cylinder", "rectangle"}:
        return spec, False       # only shapes whose labels are literal measurements

    # Mixed units mean the stated numbers are not on a common scale ("1 metre
    # long, 50 cm wide"), so overriding any of them produces a nonsense
    # drawing. Leave the model's spec alone: it is at least self-consistent.
    if len(units_in_text(question_text)) > 1:
        return spec, False

    stated = dimensions_in_text(question_text)
    if not stated:
        return spec, False

    out = dict(spec)
    changed = False
    keys = ("length", "width", "height", "radius")
    for key in keys:
        if key not in stated or key not in out:
            continue
        try:
            current = float(out[key])
        except (TypeError, ValueError):
            continue
        if abs(current - stated[key]) > 1e-9:
            out[key] = stated[key]
            changed = True

    unit = unit_in_text(question_text)
    if unit and str(out.get("unit", "")).lower() != unit:
        out["unit"] = unit
        changed = True

    return out, changed
