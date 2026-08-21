"""How maths is written on the page, decided here rather than by the model.

The generator is not consistent with itself, and a booklet is where that
shows. One real Year 5 booklet printed "15 * 4 + 7", then "5x = 45", then
"1 x 3 = 3" within two pages: three meanings across two symbols, for a child
who is still learning what the symbols mean. Volume appeared as "cubic
centimetres", "cubic cm" and "cm^3" in the same document.

None of that can be fixed reliably in a prompt, so it is fixed here, at render
time, deterministically, the same way `formatter._dedash` enforces the
no-em-dash rule regardless of what the model wrote. Correct in the prompt,
verified on the page. After this pass the booklet uses exactly one symbol per
operation:

    x  is always an unknown            *  never appears
    ×  is always multiplication        ÷  is always division
    cm³ is always volume               cm² is always area

THE RULE FOR `x`
    `x` is a variable in algebra and a multiplication sign in primary
    arithmetic, and the same booklet product prints both. It is read as
    multiplication only when both sides are unambiguous operands: a bare
    number, or a single letter that is not itself x, or (on the right) an
    opening bracket, or a unit word or a dimension word on the closed lists
    below. Everything else keeps its letter, which is what protects "5x = 45",
    "3x + 4", "solve for x", "x²" and every ordinary word with an x in it.
    A missed multiplication sign is invisible; a corrupted equation is a wrong
    answer in a customer's booklet, so where the reading is not forced, the
    text is left exactly as written.
"""
from __future__ import annotations

import re

MULTIPLY = "×"
DIVIDE = "÷"

_SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")

# Markdown emphasis, which models emit constantly and which must never reach a
# printed page. Stripped before any notation rule runs, because _STAR_MULT_RE
# below would otherwise read the markers as multiplication: a real booklet
# printed "multiply the numerator and the denominator by the × same × number"
# in the highlighted box the whole topic is named after.
#
# An emphasis marker hugs its text (no space on the inside) and is free on the
# outside, which is exactly the opposite of a multiplication asterisk. That
# asymmetry is what separates them, and it is why "4 * 3" is untouched.
# Single asterisks only. A **double** pair is deliberate markup that
# apply_bold_markup turns into a real bold run later, and it has to survive
# this step intact.
_EMPHASIS_RE = re.compile(
    r"(?<![\w*])\*(?!\*)(?=\S)([^*\n]{1,80}?)(?<=\S)\*(?!\*)(?![\w])")


def strip_emphasis(text: str) -> str:
    return _EMPHASIS_RE.sub(r"\1", text)


# "15 * 4", "2 * (7 + 4)", "5 * side". Emphasis asterisks are already gone by
# the time this runs, so the greedy \s* either side is safe.
_STAR_MULT_RE = re.compile(r"(?<=[0-9A-Za-z\)])\s*\*\s*(?=[0-9A-Za-z\(])")

# "1 x 3", "5x3", "40 x 20 x 10", "l x w x h". Both sides must be a number or a
# single letter that is not itself x, so the unknown in "5x = 45", "solve for x"
# and any ordinary word containing an x are all left alone.
_MULT_OPERAND = r"(?:[0-9]+(?:\.[0-9]+)?|[a-wyzA-WYZ])"
# An opening bracket counts as a right-hand operand too. Without it the rule a
# child is taught the formula from, "2 x (length + width)", kept its letter x
# three lines above "length × width" in the same box.
_X_MULT_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + _MULT_OPERAND + r")\s*[xX]\s*(?=(?:"
    + _MULT_OPERAND + r"(?![A-Za-z0-9])|\())")

# "40 cm x 20 cm", "3 units x 2 units": a unit word, then x, then a number.
_X_MULT_UNIT_RE = re.compile(
    r"\b(cm|mm|km|m|litres?|units?|cubes?|blocks?)\s+[xX]\s+(?=[0-9])", re.IGNORECASE)

# "length x width x height": an x between two dimension words is always a
# multiplication sign. Scoped to this closed list so no ordinary sentence with
# an "x" in it is touched.
_DIMENSION = r"(?:length|width|height|depth|breadth|base|side)"
_X_MULT_DIM_RE = re.compile(
    r"\b(" + _DIMENSION + r")\s+[xX]\s+(?=" + _DIMENSION + r"\b)", re.IGNORECASE)

# "8000 / 2 = 4000". A slash with a space beside it is a division sign; a slash
# with no spaces ("3/4") is a fraction and is left for _prettify_fractions. The
# trailing lookahead keeps "3 / 4 of the pizza" a quantity rather than a sum.
_SLASH_DIV_RE = re.compile(
    r"(?<=[0-9])(?:\s+/\s*|\s*/\s+)(?=[0-9])(?![0-9]{0,3}\s+of\b)")

# "8000 divided by 2" in the middle of an expression.
_WORD_DIV_RE = re.compile(r"(?<=[0-9])\s+divided by\s+(?=[0-9])", re.IGNORECASE)

_UNIT_WORDS = {
    "centimetre": "cm", "centimetres": "cm", "centimeter": "cm", "centimeters": "cm",
    "cm": "cm",
    "millimetre": "mm", "millimetres": "mm", "millimeter": "mm", "millimeters": "mm",
    "mm": "mm",
    "metre": "m", "metres": "m", "meter": "m", "meters": "m", "m": "m",
    "kilometre": "km", "kilometres": "km", "kilometer": "km", "kilometers": "km",
    "km": "km",
}
_POWER_WORD_RE = re.compile(
    r"\b(cubic|square)\s+(" + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE)

# "cm3", "cm^3", "m2" written flat. No space is allowed before the digit, or
# "a rope 2 m 3 cm long" would turn into cubic metres.
_UNIT_POWER_FLAT_RE = re.compile(r"\b(cm|mm|km|m)\^?([23])\b")

# Both superscripts, the multiplication sign and the division sign are Latin-1,
# so they render in Helvetica too and need no Unicode-font guard.
_SUPER_23 = {"2": "²", "3": "³"}


def normalise_notation(text: str) -> str:
    """One symbol per operation, whatever the model wrote."""
    text = _STAR_MULT_RE.sub(f" {MULTIPLY} ", text)
    text = _X_MULT_UNIT_RE.sub(lambda m: f"{m.group(1)} {MULTIPLY} ", text)
    text = _X_MULT_DIM_RE.sub(lambda m: f"{m.group(1)} {MULTIPLY} ", text)
    text = _X_MULT_RE.sub(rf"\1 {MULTIPLY} ", text)
    text = _SLASH_DIV_RE.sub(f" {DIVIDE} ", text)
    text = _WORD_DIV_RE.sub(f" {DIVIDE} ", text)

    def unit_word(m: re.Match) -> str:
        digit = "3" if m.group(1).lower() == "cubic" else "2"
        return _UNIT_WORDS[m.group(2).lower()] + _SUPER_23[digit]

    text = _POWER_WORD_RE.sub(unit_word, text)
    text = _UNIT_POWER_FLAT_RE.sub(lambda m: m.group(1) + _SUPER_23[m.group(2)], text)
    return text
