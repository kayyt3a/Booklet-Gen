"""Deterministic guards against failure modes the LLM judge waves through.

All of these were found in real booklets that had passed validation:

1. A "verified" answer whose own working contradicted it. The key printed
   "Answer: 75" above working that concluded 80, with the model's internal
   monologue ("Wait, recalculating:", "Correction:") left in student-facing
   text. The judge grades the answer against the question, so working that
   disagrees with the answer is invisible to it.

2. A diagram whose labels contradicted the question it illustrated: the text
   said a tank was 40cm x 20cm x 10cm, the drawing beside it was labelled
   4cm x 2cm x 1cm. The model routinely emits a scaled-down spec for
   drawability and forgets that the labels are what the student reads.

3. A diagram that printed the answer. "A storage box is built using 24 cubic
   blocks, the base is 4 blocks by 3 blocks, how many layers high is the box?"
   was drawn as a cuboid with "2 blocks" written on the height, which is the
   answer. Guard 2 could not catch this: it only corrects dimensions the
   question states, and the whole point of a find-the-missing-dimension
   question is that the answer is the one dimension it does not state.

4. Text referring to a picture that was never drawn. A mini-lesson worked
   example asked "how many cubes are needed to build this object" with no
   diagram_spec emitted, leaving a child looking for a figure that does not
   exist. The judge reads the text only, so a missing picture is invisible
   to it too.

None of these is repairable by prompting alone, because all are cases of the
model being locally plausible and globally wrong.
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


# --------------------------------------------------------------------------
# 3. A diagram that labels the answer
# --------------------------------------------------------------------------

# What the question is ASKING for, as opposed to what it states. Two families
# of phrasing per dimension:
#   adjective form  "how many layers high", "how tall is it", "how wide"
#   noun form       "find the height", "what is the missing width"
# The noun form deliberately requires the dimension word to follow the verb
# immediately (allowing only "the"/"its" and "missing"/"unknown" between), so
# that "what is the volume of a box with height 4" is not read as asking for
# the height.
_ASK_VERB = r"(?:what\s+is|what'?s|find|calculate|work\s+out|determine|state|give)"
_ASK_LEAD = r"(?:\s+the|\s+its|\s+their)?\s+(?:missing\s+|unknown\s+)?"

_ASK_TERMS = {
    # key: (adjective alternation, noun alternation, adjective exclusion)
    "height": (r"(?:high|tall|deep)", r"(?:height|depth)", ""),
    # "how long does it take" is a question about time, not about a side.
    "length": (r"long", r"length", r"(?!\s+(?:does|did|do|will|would|has|have|ago|before|until))"),
    "width": (r"wide", r"(?:width|breadth)", ""),
    # A question that asks for the diameter is given away just as badly by a
    # labelled radius, so both map to the radius label.
    "radius": (None, r"(?:radius|diameter)", ""),
}


def _ask_patterns(key: str) -> list[str]:
    adj, noun, excl = _ASK_TERMS[key]
    pats = [
        rf"{_ASK_VERB}{_ASK_LEAD}{noun}\b",
        rf"\b(?:missing|unknown)\s+{noun}\b",
    ]
    if adj:
        # "how high", "how tall", "how many layers high", "how many cubes deep",
        # "how many layers of blocks high". The window has to reach four words:
        # the real booklet wrote "How many layers of blocks high is the box?",
        # and a two-word window matched only the shorter paraphrase.
        pats.append(rf"\bhow\s+(?:many\s+(?:\w+\s+){{0,4}})?{adj}\b{excl}")
    return pats


_ASK_COMPILED = {
    key: [re.compile(p, re.IGNORECASE) for p in _ask_patterns(key)]
    for key in _ASK_TERMS
}


def asked_dimensions(text: str) -> set:
    """Dimensions the question asks the student to find."""
    out = set()
    for key, patterns in _ASK_COMPILED.items():
        if any(p.search(text or "") for p in patterns):
            out.add(key)
    return out


def _unknown_keys(spec: dict) -> set:
    raw = spec.get("unknown") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(k).strip().lower() for k in raw}


def unknown_dimensions(spec: dict, question_text: str) -> list:
    """Which of the spec's dimension labels would give the answer away.

    A dimension qualifies when the question asks for it and does not itself
    state it. The "does not state it" half matters: if the question prints the
    number, the label leaks nothing and the drawing is more useful with it.
    """
    stated = dimensions_in_text(question_text)
    asked = asked_dimensions(question_text)
    hidden = _unknown_keys(spec)
    for key in asked:
        if key in stated:
            continue
        if key in spec:
            hidden.add(key)
    return sorted(hidden)


def reconcile_diagram_spec(spec: dict, question_text: str) -> tuple[dict, bool]:
    """Correct a diagram spec so its labels match the question.

    Returns (spec, changed). Two repairs, in order:

    1. Any dimension the question is *asking for* has its label hidden, so the
       drawing cannot print the answer.
    2. Any dimension the question *states* is forced to the stated value, so
       the drawing cannot contradict the text.

    The diagram is never discarded: a wrong or leaked label is fixable, and a
    shape with one side marked "?" is exactly how a textbook poses a
    find-the-missing-dimension question. Only keys the question states
    explicitly are overridden, so a spec is never invented from nothing.
    """
    if not spec or not isinstance(spec, dict):
        return spec, False

    kind = str(spec.get("type", "")).lower()
    if kind not in {"cuboid", "cylinder", "rectangle"}:
        return spec, False       # only shapes whose labels are literal measurements

    out = dict(spec)
    changed = False

    hidden = unknown_dimensions(out, question_text)
    if hidden and sorted(_unknown_keys(out)) != hidden:
        out["unknown"] = hidden
        changed = True

    # Mixed units mean the stated numbers are not on a common scale ("1 metre
    # long, 50 cm wide"), so overriding any of them produces a nonsense
    # drawing. Leave the model's numbers alone: they are at least
    # self-consistent. Hiding the answer above still applies.
    if len(units_in_text(question_text)) > 1:
        return out, changed

    stated = dimensions_in_text(question_text)
    if not stated:
        return out, changed

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


# --------------------------------------------------------------------------
# 4. Text pointing at a picture that was never drawn
# --------------------------------------------------------------------------

# Phrases that only make sense beside a figure. This guard deletes questions,
# so every entry earns its place and several obvious-looking ones are
# deliberately absent:
#
#   * "table" and "pattern" carry their data inline as text. "The table below
#     shows..." and "Continue this pattern: 2, 5, 8" are answerable as
#     written.
#   * A bare "shown below" followed by a colon is introducing inline data
#     ("The scores are shown below: 4, 7, 9"), not pointing at a picture.
#   * "image" and "photograph" only count with a position word after them.
#     English questions talk about the image a poet creates, and that is not
#     a missing figure.
#   * "figure of speech" is an English term, not a drawing.
#   * A question that asks the child to DRAW the thing is not referring to
#     one: "Sketch the graph of y = 2x + 1" needs no figure of its own.
_FIGURE_NOUN = (r"(?:diagram|figure\b(?!\s+of\s+speech)|picture|drawing"
                r"|graph|sketch|net|number\s+line)")
_POSITIONED_NOUN = (r"(?:image|photo|photograph|illustration|object|solid"
                    r"|shape|prism|cuboid|cube|cylinder|rectangle|triangle"
                    r"|model|stack|tower)")
_HERE = r"(?:below|above|shown|drawn|opposite|on\s+the\s+right)"

_FIGURE_REFERENCE = re.compile(
    rf"\b(?:the|this|these|each)\s+{_FIGURE_NOUN}\b"
    rf"|\bthis\s+(?:object|solid|shape)\b"
    rf"|\b(?:the|this)\s+{_POSITIONED_NOUN}\s+{_HERE}\b"
    rf"|\b(?:as\s+)?shown\s+(?:below|above|opposite)\b(?!\s*:)"
    rf"|\bpictured\b"
    rf"|\bin\s+the\s+{_FIGURE_NOUN}\b",
    re.IGNORECASE,
)

# "Draw the number line", "Sketch the graph": the child produces the figure,
# so its absence is the point. Kept to verbs that genuinely create one:
# "build this object" must NOT be excused, since that is the shipped defect.
_PRODUCES_FIGURE = re.compile(
    r"\b(?:draw|sketch|plot)\w*\s+(?:a|an|the|this|your|its)?\s*$",
    re.IGNORECASE,
)


def figure_reference(text: str) -> Optional[str]:
    """The phrase that promises the reader a picture, or None.

    Returns the matched phrase rather than a bool so the caller can log what
    it dropped and why.
    """
    text = text or ""
    for m in _FIGURE_REFERENCE.finditer(text):
        if _PRODUCES_FIGURE.search(text[max(0, m.start() - 30):m.start()]):
            continue
        return m.group(0)
    return None


def refers_to_missing_figure(text: str, has_image: bool) -> Optional[str]:
    """The offending phrase when text promises a figure that does not exist.

    Stripping the phrase was the alternative and it is worse: the reference is
    not decoration, it stands in for the data the task needs. Delete "this
    object" from "how many cubes are needed to build this object" and the
    question is still unanswerable, only now the child cannot tell that
    something is missing. Dropping the item is the one action that cannot
    mislead.
    """
    if has_image:
        return None
    return figure_reference(text)
