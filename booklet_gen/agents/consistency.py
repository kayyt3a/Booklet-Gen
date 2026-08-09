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
    # The same tell in the answer field. It slips past the contradiction check
    # below, because "75. Wait, recalculating: 80" reads as two numbers and
    # that check only judges a single-valued answer.
    if has_self_correction(answer):
        return False, "answer contains model self-correction"
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
    # "Find the length of the radius" is how half of these are worded, and the
    # lead pattern only allows an article, so the phrase goes in the noun. Not
    # widened for every key on purpose: "find the area of the base" would then
    # read as a question about the base and hide the one label the student
    # needs to work out the area.
    "radius": (None, r"(?:length\s+of\s+(?:the\s+)?)?(?:radius|diameter)", ""),
    # The hypotenuse is the `c` key of a right_triangle, and it is what almost
    # every Pythagoras question asks for. Printing it on the drawing turns
    # "use the theorem" into "read the label".
    "c": (None, r"(?:length\s+of\s+(?:the\s+)?)?hypotenuse", ""),
    "base": (None, r"base", ""),
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


# Shapes that print every dimension they are given, but whose numbers must not
# be rewritten from the question text.
#
# For a cuboid, "the tank is 40 cm long" names the same thing the spec calls
# `length`, so a mismatched drawing can be corrected. These shapes have no such
# correspondence: a triangle's `base` and `height` are not what a question
# calls its three sides, and a right triangle's `a` and `b` are whichever two
# legs the model chose. Forcing values there would invent a wrong drawing out
# of a right one. Hiding a label cannot: the worst case is a "?" on a side the
# question happens to state, which costs a little information and leaks none.
_LEAK_ONLY_TYPES = frozenset({"right_triangle", "triangle", "parallelogram",
                              "trapezium", "circle"})


# Figures that print a caption naming what they show, where that caption is
# the answer whenever the question asks the student to read the figure.
#
# From a shipped Year 3 booklet: "What number is shown by the blocks in the
# diagram?" with "2 hundreds, 3 tens, 4 ones" printed under the blocks. The
# prompt asks for "label": false on exactly this question and the model did not
# do it, which is what a prompt instruction is worth on its own.
_ASKS_TO_READ_NUMBER = re.compile(
    r"\b(?:what|which)\s+number\b"
    r"|\bnumber\s+(?:is\s+)?(?:shown|represented|made)\b"
    r"|\b(?:read|identify|name|write|state)\s+(?:the\s+)?number\b"
    r"|\bwhat\s+(?:is\s+the\s+)?value\s+(?:is\s+)?shown\b",
    re.IGNORECASE,
)
_ASKS_TO_NAME_SHAPE = re.compile(
    r"\b(?:name|identify)\s+(?:this|the|each)\s+(?:shape|solid|figure)\b"
    r"|\bwhat\s+(?:shape|solid)\b"
    r"|\bwhich\s+(?:shape|solid)\b"
    r"|\bwhat\s+is\s+(?:this|the\s+name\s+of)\b",
    re.IGNORECASE,
)
_CAPTIONED_TYPES = {
    "place_value": _ASKS_TO_READ_NUMBER,
    "shape": _ASKS_TO_NAME_SHAPE,
    "shape_3d": _ASKS_TO_NAME_SHAPE,
}


def _hide_caption(spec: dict, kind: str, question_text: str) -> tuple[dict, bool]:
    """Turn off a figure's caption when the caption answers the question."""
    if spec.get("label", True) is False:
        return spec, False
    if not _CAPTIONED_TYPES[kind].search(question_text or ""):
        return spec, False
    out = dict(spec)
    out["label"] = False
    return out, True


def _hide_the_answer(spec: dict, kind: str, question_text: str) -> tuple[dict, bool]:
    """Mark as "?" any label on `spec` that states what the question asks for."""
    hidden = set(unknown_dimensions(spec, question_text))
    if kind == "circle":
        # The spec carries whichever of radius or diameter it was given, and
        # either one hands over the other. `unknown_dimensions` looks for its
        # own key name in the spec, so neither is found unless it is mapped.
        asked = asked_dimensions(question_text)
        stated = dimensions_in_text(question_text)
        if "radius" in asked and "radius" not in stated:
            hidden |= {k for k in ("radius", "diameter") if k in spec}
    if not hidden or sorted(_unknown_keys(spec)) == sorted(hidden):
        return spec, False
    out = dict(spec)
    out["unknown"] = sorted(hidden)
    return out, True


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
    if kind in _CAPTIONED_TYPES:
        return _hide_caption(spec, kind, question_text)
    if kind in _LEAK_ONLY_TYPES:
        return _hide_the_answer(spec, kind, question_text)
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
# 3b. A flat shape drawn for a solid question, or the reverse
# --------------------------------------------------------------------------

# Dimensionality is the one thing a child must read correctly off a maths
# figure. Area and perimeter live on a flat shape; volume, capacity and
# surface area live on a solid. Drawing a rectangle beside "find the volume"
# teaches that a box is a square, which is worse than drawing nothing, and it
# is the confusion this stage of primary maths exists to undo.
_SOLID_TYPES = frozenset({"cuboid", "cylinder", "shape_3d"})
_FLAT_TYPES = frozenset({
    "rectangle", "l_shape", "circle_slices", "bar_model",
    # Every flat figure that carries a measurement. A net is deliberately NOT
    # here: it is a flat drawing of a solid, so it is the right picture for a
    # surface-area question and the wrong one to reject for naming a prism.
    "triangle", "right_triangle", "parallelogram", "trapezium", "circle",
    "grid_area", "shape", "symmetry",
})

# "Volume", "capacity" and "surface area" need a solid. Note surface area is
# deliberately here and not below: it is a property of a 3D object.
_NEEDS_SOLID_RE = re.compile(
    r"\b(?:volume|capacity|surface\s+area|cubic|holds?\s+\d|"
    r"how\s+(?:much|many)\s+(?:water|sand|liquid|cubes?|blocks?)|"
    r"litres?|millilitres?|cuboid|prism|cylinder)\b",
    re.IGNORECASE,
)

# "Area" and "perimeter" need a flat shape. Guarded so "surface area" does not
# match here, since that one belongs above.
_NEEDS_FLAT_RE = re.compile(
    r"\b(?:perimeter|(?<!surface\s)area|square\s+(?:cm|m|metres?|centimetres?)|"
    r"how\s+far\s+around|fence|border)\b",
    re.IGNORECASE,
)


def diagram_dimensionality_matches(spec: dict, question_text: str) -> bool:
    """False when a solid question carries a flat figure, or the reverse.

    Only judges when the question is unambiguous. A question naming both
    ("find the area of the base, then the volume") is left alone rather than
    risk dropping a good figure.
    """
    if not spec or not isinstance(spec, dict):
        return True
    kind = str(spec.get("type", "")).lower()
    if kind not in _SOLID_TYPES and kind not in _FLAT_TYPES:
        return True          # compare, number_line and anything unrecognised

    text = question_text or ""
    wants_solid = bool(_NEEDS_SOLID_RE.search(text))
    wants_flat = bool(_NEEDS_FLAT_RE.search(text))
    if wants_solid == wants_flat:
        return True          # neither, or both: not our call to make
    if wants_solid:
        return kind in _SOLID_TYPES
    return kind in _FLAT_TYPES


# --------------------------------------------------------------------------
# 3c. A lesson example that answers a reading the student has not reached
# --------------------------------------------------------------------------


def _quoted_titles(text: str) -> set:
    """Titles the text names, however they are quoted."""
    found = set()
    for m in re.finditer(r"['\"‘’“”]([^'\"‘’“”]{4,90})"
                         r"['\"‘’“”]", text or ""):
        found.add(m.group(1).strip().lower())
    return found


def example_spoils_passage(example, passages) -> bool:
    """True when a lesson example gives away an answer about a booklet reading.

    The mini-lesson prints above the questions, and a passage prints with the
    question group that uses it. So an example naming one of this section's
    readings hands the student a worked answer about a story printed further
    down the same page, before they have read a word of it.

    The prompt already tells the lesson to carry its own two to four sentence
    excerpt instead. This catches the case where it does not.
    """
    titles = {(getattr(p, "title", "") or "").strip().lower()
              for p in (passages or [])}
    titles.discard("")
    if not titles:
        return False
    text = " ".join(filter(None, [
        getattr(example, "question", "") or "",
        getattr(example, "answer", "") or "",
    ]))
    named = _quoted_titles(text)
    if titles & named:
        return True
    # An unquoted mention still gives it away, but only match a title long
    # enough that a coincidence is implausible.
    low = text.lower()
    return any(len(t) >= 12 and t in low for t in titles)


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


# --------------------------------------------------------------------------
# 5. Real-world quantities that are absurd by orders of magnitude
# --------------------------------------------------------------------------
#
# Page 10 of a shipped Year 5 Maths booklet asked the student to write out
# "The distance from Perth to Melbourne is approximately 3,421,000 kilometres"
# (it is about 3,400), then "A large stadium can hold 9,900,009 spectators"
# (the MCG holds about 100,000), then "A national park in Queensland covers an
# area of five million, seven hundred and two km squared" (the whole state is
# 1.85 million). The subtopic those questions belong to is called "Reading and
# writing numbers up to millions": a booklet teaching number sense taught a
# child that a stadium and a country are the same size.
#
# This is a prompt-proof failure. The generator is asked for a seven-digit
# number and a context to put it in, and it picks whichever context makes a
# sentence, because nothing in its objective connects the digits to the world.
# Another rule in a prompt already full of rules will not fix it.
#
# The guard is deliberately narrow, because it deletes customer content:
#
#   * a hard-coded table of quantities a Year 1 to 10 booklet actually uses,
#     nothing inferred;
#   * a quantity is only flagged when it exceeds the plausible maximum by more
#     than an order of magnitude, so "the MCG holds 100,000 spectators" and
#     "Perth to Adelaide is 2695 km" pass with room to spare;
#   * only the too-large side is guarded. Too-small numbers are rarer, and an
#     obvious low bound collides with legitimate small numbers in the same
#     sentence, e.g. "Round this distance to the nearest 100 kilometres";
#   * a quantity must carry a unit the rule recognises, and must sit in the
#     same clause as the thing it describes. A bare number, or a number in the
#     next sentence, is never judged.
#
# Callers drop the question. Rewriting it means inventing a replacement number
# and hoping the rest of the sentence still means something, and the answer key
# was written against the number being replaced.

_ORDER_OF_MAGNITUDE = 10.0

# Multiplier words that may follow a digit group: "9.9 million spectators".
_SCALE_WORDS = {"hundred": 100, "thousand": 1_000, "million": 1_000_000,
                "billion": 1_000_000_000}

_WORD_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}

# A run of number words, comma, hyphen or "and" separated. The booklet wrote
# one of the three defects out longhand: "five million, seven hundred and two".
_WORD_NUMBER_TOKENS = sorted(set(_WORD_UNITS) | set(_SCALE_WORDS),
                             key=len, reverse=True)
_WORD_RUN = re.compile(
    r"\b(?:" + "|".join(_WORD_NUMBER_TOKENS) + r")"
    r"(?:[\s,\-]+(?:and[\s,\-]+)?(?:" + "|".join(_WORD_NUMBER_TOKENS) + r"))*\b",
    re.IGNORECASE,
)

_DIGIT_NUMBER = re.compile(
    r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b")

# Unit families, tried in order: the area forms have to win before "km" does,
# and "km" before the bare "m", or "3,421,000 km" is read as metres.
_QUANTITY_UNITS = (
    ("area_km2",
     r"(?:sq\.?|square)\s*(?:km|kilometres|kilometers)\b"
     r"|(?:km|kilometres|kilometers)\s*(?:\^\s*2|2|²|squared)\b"),
    ("distance_km", r"(?:km|kilometres|kilometers)\b"),
    ("distance_m", r"(?:m|metres|meters)\b"),
    ("people",
     r"(?:people|persons|spectators|supporters|fans|residents|inhabitants"
     r"|seats)\b"),
)
_QUANTITY_UNIT_RE = re.compile(
    r"\s*(?:" + "|".join(f"(?P<{name}>{pat})" for name, pat in _QUANTITY_UNITS)
    + r")", re.IGNORECASE)

# Everything is compared in one unit per family.
_TO_CANONICAL = {"area_km2": ("area_km2", 1.0),
                 "distance_km": ("distance_km", 1.0),
                 "distance_m": ("distance_km", 0.001),
                 "people": ("people", 1.0)}


def _words_to_number(run: str) -> Optional[float]:
    total, current, seen = 0.0, 0.0, False
    for token in re.split(r"[\s,\-]+", run.lower()):
        if not token or token == "and":
            continue
        if token in _WORD_UNITS:
            current += _WORD_UNITS[token]
            seen = True
        elif token == "hundred":
            current = (current or 1) * 100
            seen = True
        elif token in _SCALE_WORDS:
            total += (current or 1) * _SCALE_WORDS[token]
            current = 0.0
            seen = True
        else:
            return None
    return total + current if seen else None


def _quantities(text: str) -> list[tuple[float, str]]:
    """Every (value, unit family) the text states outright, canonicalised.

    Numbers with no unit, or a unit no rule knows about, are not returned:
    this guard never judges a bare number.
    """
    found: list[tuple[float, str]] = []
    spans: list[tuple[int, float]] = []

    for m in _DIGIT_NUMBER.finditer(text):
        try:
            spans.append((m.end(), float(m.group(0).replace(",", ""))))
        except ValueError:
            continue
    for m in _WORD_RUN.finditer(text):
        value = _words_to_number(m.group(0))
        if value is not None:
            spans.append((m.end(), value))

    for end, value in spans:
        tail = text[end:end + 40]
        # "9.9 million spectators": the multiplier sits between the digits and
        # the unit, and belongs to the number.
        scale = re.match(r"\s+(hundred|thousand|million|billion)\b", tail,
                         re.IGNORECASE)
        if scale:
            value *= _SCALE_WORDS[scale.group(1).lower()]
            tail = tail[scale.end():]
        unit = _QUANTITY_UNIT_RE.match(tail)
        if not unit or not unit.lastgroup:
            continue
        family, factor = _TO_CANONICAL[unit.lastgroup]
        found.append((value * factor, family))
    return found


# Places a booklet names, and the largest value each quantity can plausibly
# take. The figures are ordinary general knowledge and deliberately generous:
# a rule only fires at ten times the number below it.
_AU_CITIES = (
    "sydney|melbourne|brisbane|perth|adelaide|canberra|hobart|darwin"
    "|newcastle|wollongong|geelong|cairns|townsville|broome|kalgoorlie"
    "|albany|geraldton|bunbury|alice springs|gold coast|sunshine coast"
)
_AU_STATES = (
    r"western\s+australia|south\s+australia|new\s+south\s+wales|victoria"
    r"|queensland|tasmania|the\s+northern\s+territory"
)

# (label, subject pattern, unit family, largest plausible value)
_MAGNITUDE_RULES: tuple[tuple[str, str, str, float], ...] = (
    # Perth to Brisbane by road is about 4,300 km, so 6,000 is already ample
    # and the guard stays silent below 60,000. Both ends must be named
    # Australian towns, so "the distance from the Earth to the Sun" is not
    # this rule's business.
    ("distance between Australian cities",
     r"distance\s+(?:from|between)\s+(?:" + _AU_CITIES + r")\s+(?:to|and)\s+(?:"
     + _AU_CITIES + r")",
     "distance_km", 6_000),
    # The MCG, the largest ground in the country, holds about 100,000.
    ("stadium capacity",
     r"\b(?:stadium|arena|sports\s+ground|oval|grandstand)\b[^.]{0,80}"
     r"\b(?:holds?|seats?|capacity|fits?|accommodates?|packed)\b"
     r"|\b(?:holds?|seats?|capacity\s+of|accommodates?)\b[^.]{0,40}"
     r"\b(?:stadium|arena|sports\s+ground|oval|grandstand)\b",
     "people", 120_000),
    ("population of Australia",
     r"population\s+of\s+australia", "people", 40_000_000),
    # A town, not a city: the same booklet put two million people in a "small
    # country town".
    ("population of a town",
     r"population\s+of\s+(?:a|the|this)?\s*(?:small\s+|little\s+|country\s+"
     r"|rural\s+|quiet\s+)*town", "people", 60_000),
    ("population of an Australian state",
     r"population\s+of\s+(?:" + _AU_STATES + r")", "people", 10_000_000),
    # Kakadu, the largest national park in the country, is under 20,000.
    ("area of a national park",
     r"national\s+park", "area_km2", 30_000),
    ("area of Australia",
     r"area\s+of\s+australia", "area_km2", 8_000_000),
    ("area of an Australian state",
     r"area\s+of\s+(?:" + _AU_STATES + r")", "area_km2", 2_600_000),
)

_COMPILED_RULES = tuple(
    (label, re.compile(pattern, re.IGNORECASE), family, plausible_max)
    for label, pattern, family, plausible_max in _MAGNITUDE_RULES)

# A running total is not a claim about how big one thing is. "The distance from
# Perth to Melbourne is 3400 km. A truck makes 30 return trips, covering
# 204,000 km altogether" is a perfectly good Year 6 question, and the second
# figure is not a distance between two cities. Clause windowing catches most of
# these; this catches the rest, and only ever makes the guard fire less.
_AGGREGATE = re.compile(
    r"\b(?:in total|altogether|combined|totals?|totalled|sold|sells|sale"
    r"|tickets?|over the (?:year|season|month|week)|each (?:year|season)"
    r"|per (?:year|season)|across)\b", re.IGNORECASE)

# How far either side of the phrase a stated quantity may sit and still be the
# quantity that phrase is about.
_CLAIM_RADIUS = 90


def _claim_window(text: str, start: int, end: int) -> str:
    """The clause around a matched phrase, never crossing a sentence end."""
    left = 0
    for stop in (". ", "? ", "! ", "; "):
        cut = text.rfind(stop, 0, start)
        if cut >= 0:
            left = max(left, cut + len(stop))
    left = max(left, start - _CLAIM_RADIUS)
    right = len(text)
    for stop in (". ", "? ", "! ", "; "):
        cut = text.find(stop, end)
        if cut >= 0:
            right = min(right, cut)
    right = min(right, end + _CLAIM_RADIUS)
    return text[left:right]


# --------------------------------------------------------------------------
# A question that answers itself
# --------------------------------------------------------------------------

# From a shipped Year 3 booklet: "Use the place value blocks to identify the
# number shown. There are 2 hundreds, 5 tens, and 3 ones." The student is asked
# for 253 and handed 253 in the same breath. The judge passes it, because the
# arithmetic is fine and the question is answerable; being answerable from the
# question alone is exactly the fault.
#
# The prompt has forbidden this for months. It is here because a prompt
# instruction is a request and this is a guarantee.
#
# Both rules below are deliberately narrow. Dropping a question is invisible to
# the reader, so a false positive costs more than a miss, and "the answer is
# implied somewhere in the wording" is not something a regex can judge. What is
# left is the two shapes that recur and can be recognised exactly.

_PLACE_VALUE_PART = re.compile(
    r"(\d+)\s+(hundred|ten|one|thousand)s?\b", re.IGNORECASE)
_PLACE_VALUE_UNIT = {"thousand": 1000, "hundred": 100, "ten": 10, "one": 1}

# Questions that legitimately restate their own answer: the task is to justify
# or check a value, not to find one.
_RESTATES_BY_DESIGN = re.compile(
    r"\b(?:show\s+that|prove|verify|check\s+(?:that|whether|if)|explain\s+why|"
    r"justify|is\s+(?:this|that|the\s+\w+)\s+correct|why\s+is|true\s+or\s+false)\b",
    re.IGNORECASE,
)
_ASKS_FOR_A_VALUE = re.compile(
    # "what NUMBER is shown" and "what FRACTION is shaded" are the common
    # forms, so the noun between "what" and the verb has to be allowed for.
    r"\b(?:what(?:\s+\w+){0,2}\s+(?:is|are)|what'?s|how\s+many|how\s+much|"
    r"how\s+long|how\s+far|find|calculate|work\s+out|determine|state|give|"
    r"write|identify|name|read)\b",
    re.IGNORECASE,
)
# A whole answer that is one quantity: "253", "5 cm", "$12", "40%", "1.5 m".
_SINGLE_QUANTITY = re.compile(
    r"^\s*(?:\$\s*)?(\d[\d,]*(?:\.\d+)?)\s*(%|[a-zA-Z]{1,12}(?:\s?\^?\d)?)?\s*\.?\s*$")


def _assembled_place_value(text: str) -> Optional[int]:
    """The number a "2 hundreds, 5 tens and 3 ones" phrase spells out.

    None unless at least two different place names appear, so "3 ones" on its
    own or a stray "two tens" in prose is not treated as a spelled-out answer.
    """
    seen: dict[str, int] = {}
    for count, name in _PLACE_VALUE_PART.findall(text or ""):
        name = name.lower()
        if name in seen:            # "2 tens ... 5 tens" is prose, not a number
            return None
        seen[name] = int(count)
    if len(seen) < 2:
        return None
    return sum(_PLACE_VALUE_UNIT[n] * c for n, c in seen.items())


def question_states_its_answer(question: str, answer: str) -> Optional[str]:
    """The reason this question hands over its own answer, or None.

    Callers drop the question. Rewriting it is not an option: the answer key
    was written against it, and removing the giveaway would leave a question
    the key no longer matches.
    """
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or not a:
        return None
    if _RESTATES_BY_DESIGN.search(q):
        return None
    if not _ASKS_FOR_A_VALUE.search(q):
        return None

    m = _SINGLE_QUANTITY.match(a)
    if not m:
        return None
    number, unit = m.group(1).replace(",", ""), (m.group(2) or "").strip()

    # 1. The answer spelled out in place-value words.
    assembled = _assembled_place_value(q)
    if assembled is not None and str(assembled) == number:
        return (f"the question spells out its own answer as place value parts "
                f"totalling {assembled}")

    # 2. The answer restated verbatim, as a whole token rather than as digits
    #    inside a longer number. "4" must not match the 4 in "24".
    pattern = re.escape(m.group(1))
    if unit:
        pattern += r"\s*" + re.escape(unit)
    if re.search(rf"(?<![\d.]){pattern}(?![\d.])", q, re.IGNORECASE):
        return f"the question already states the answer, {a!r}"
    return None


def implausible_magnitude(text: str) -> Optional[str]:
    """A short reason when the text states a real-world quantity wrong by more
    than an order of magnitude, or None.

    Only the quantities in the table above are judged, only when they carry a
    unit, only within the clause that names the thing, and only when they are
    ten times larger than the largest plausible value. Everything else passes.
    """
    if not text:
        return None
    low = " ".join(text.lower().split())
    for label, subject, family, plausible_max in _COMPILED_RULES:
        ceiling = plausible_max * _ORDER_OF_MAGNITUDE
        for match in subject.finditer(low):
            window = _claim_window(low, match.start(), match.end())
            if _AGGREGATE.search(window):
                continue
            for value, found_family in _quantities(window):
                if found_family == family and value > ceiling:
                    return (f"{label}: {value:,.0f} is more than ten times the "
                            f"plausible maximum of {plausible_max:,.0f}")
    return None
