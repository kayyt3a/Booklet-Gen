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
    x⁴ is always an index              x^4 never appears

THE RULE FOR INDICES
    A caret is source code, not mathematics. A real Year 9 booklet printed
    "Simplify the expression: (x³ × x^4) ÷ x²" and "Volume of cylinder =
    π * 3^2 × 10", which is two notations for an index inside one line and a
    Python multiplication in a formula. Every caret becomes a raised index.
    The index is the maximal run of digits ("c^12" is c to the twelfth, not
    c squared then a 2), or a single letter, or a bracketed group whose
    brackets are dropped because the group is the whole index ("x^(a+b)" is
    x to the a plus b). Where every character of the index has a Unicode
    superscript form the glyph is used, because that is what the rest of the
    booklet already sets ("cm³", "x²") and mixing the two is the defect this
    module exists to remove. Where it does not, which in practice means an
    index containing a multiplication sign, the index is set with ReportLab's
    <super> markup at the size and rise measured off the Unicode superscript
    glyphs, so the two paths land in the same place on the same line.

THE RULE FOR DIVISION
    Division has two right answers and the difference is spacing, so spacing
    is what decides. A slash with a space beside it is an operator between two
    complete terms and is printed "÷", whether the terms are numbers
    ("8000 / 2") or algebra ("e⁹ / e³"), because the lesson that teaches the
    index laws already writes them with "÷" and a booklet must not use both
    for one operation two lines apart. A slash with no space is left exactly
    as written: that is where fractions ("3/4"), rates ("km/h"), dates
    ("15/07/2025"), scores and "and/or" live, and every one of them means
    something other than "divide these two things". A single-letter term
    either side of a spaced slash is still a term, so "m / s" and the other
    rate pairs are excluded by name rather than by guesswork.

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
    text is left exactly as written. That is the standing rule here: prove
    both readings, or leave it alone.

THE MARKUP-FREE MODE
    Everything above is written for ReportLab, which is the only consumer that
    can raise an index the font cannot spell. Matplotlib draws the labels on
    the diagrams, and it has no idea what `<super>` means: handed one it prints
    the tag, angle brackets, percent signs and all, in the middle of a figure.
    So `markup=False` says "emit characters only". An index that has a Unicode
    superscript spelling is still set as one, because that is a character; an
    index that has not is LEFT EXACTLY AS WRITTEN, caret included.

    Leaving it alone is the deliberate choice and it is the same trade the rule
    for `x` makes. A missed normalisation on a figure is invisible: it prints
    the notation the model wrote, which is what would have printed anyway. A
    literal `<super size="56%" rise="33%">` across a diagram is a defect a
    customer photographs. The unrepresentable cases are rare and narrow, being
    an index containing a multiplication sign ("f^(2 × 3)"), a decimal index
    ("x^1.5"), an uppercase index and a "q", so the cost of leaving them is a
    caret in a corner of a figure once in a long while, against the certainty
    of printed markup every time otherwise.

    Matplotlib's own mathtext ("$x^{1.5}$") was the other candidate and is not
    used. A mathtext parse error raises at draw time, and a raise inside a
    renderer loses the whole figure (`render_diagram` returns None and the
    question prints with no diagram at all), so a notation tidy-up would be
    able to delete a picture. Swapping the font mid-label for one string in one
    corner of a figure is also visibly not the rest of the figure.

WHAT IS DELIBERATELY NOT TOUCHED
    Subscripts. The distance formula arrives as "sqrt((x2 - x1)^2 + ...)", and
    "x2" is a subscripted point in that line but a perfectly ordinary product
    or variable name elsewhere. There is no rule that separates them, and
    guessing wrong rewrites a formula.
"""
from __future__ import annotations

import re

MULTIPLY = "×"
DIVIDE = "÷"
ROOT = "√"

# Every character that has a superscript form in DejaVu, which is the family
# the booklet embeds (formatter._register_fonts). Checked glyph by glyph
# against DejaVuSans, DejaVuSans-Bold, DejaVuSerif and DejaVuSerif-Bold by
# scripts/check_math_notation.py, so nothing here can print as a box.
#
# There is no superscript "q", no superscript capital, no superscript full
# stop and, the one that matters, no superscript multiplication sign. Those
# fall to the markup path below rather than being approximated: "ˣ" is the
# letter x and setting a times sign as one would print an index law that says
# something else.
_SUPER_CHAR = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "−": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ",
    "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ",
    "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ", "v": "ᵛ",
    "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}
SUPERSCRIPTS = "".join(sorted(set(_SUPER_CHAR.values())))

# The size and rise of the markup fallback, as percentages of the surrounding
# font size, measured off the Unicode superscript digits so an index set by
# either path sits in the same place.
#
# In DejaVu (2048 units per em) a superscript digit inks from y=668 to y=1520
# and a full-size digit from y=0 to y=1520. So the superscript is
# 852/1520 = 56% of full size, sitting on a baseline 668/2048 = 33% of the em
# above the real one. ReportLab's own default for <super> is 80% at a rise of
# 50%, which pushes the ink above the top of the line box and into the
# descenders of the line above; that is the collision that made <sup> unusable
# for fractions (see formatter._prettify_fractions) and 56/33 avoids it.
SUPER_SIZE_PCT = 56
SUPER_RISE_PCT = 33


def super_markup(body: str) -> str:
    """An index ReportLab has to raise itself, because Unicode cannot."""
    return (f'<super size="{SUPER_SIZE_PCT}%" rise="{SUPER_RISE_PCT}%">'
            f"{body}</super>")

# What can carry an index: a digit, a letter, a closing bracket, pi, or a
# character that is already a superscript ("(x²)^3").
_INDEX_BASE = r"[0-9A-Za-z\)\]π" + SUPERSCRIPTS + r"]"

# The index itself, in the three shapes that have one reading and no other.
# The digit run takes every digit ("c^12" is c to the twelfth) but refuses a
# decimal point that has a digit after it, so "x^1.5" goes to the markup path
# rather than being read as x to the first. A full stop with nothing after it
# is the end of the sentence and stays out of the index: "The result is c^12."
_INDEX_BODY = (r"(?:\([^()\n]{1,24}\)"
               r"|[-+−]?[0-9]{1,3}(?![0-9])(?!\.[0-9])"
               r"|[-+−]?[A-Za-z](?![A-Za-z0-9]))")

_INDEX_RE = re.compile(r"(?<=" + _INDEX_BASE + r") ?\^ ?(" + _INDEX_BODY + r")")

# Anything else that is written as a caret index: "f^(2 × 3)", "x^1.5".
# Bounded so a stray caret in prose cannot swallow the rest of a sentence.
_INDEX_ANY_RE = re.compile(
    r"(?<=" + _INDEX_BASE + r") ?\^ ?(\([^()\n]{1,40}\)|[^\s,;:!?]{1,16})")


def _as_superscript(body: str) -> str | None:
    """The index as superscript glyphs, or None if it cannot be set that way.

    A bracketed group loses its brackets, because the group is the whole index
    and "x^(a+b)" is written xᵃ⁺ᵇ. Spaces go with them: an index is set tight.
    """
    inner = body.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    inner = inner.replace(" ", "")
    if not inner:
        return None
    out = []
    for ch in inner:
        glyph = _SUPER_CHAR.get(ch)
        if glyph is None:
            return None
        out.append(glyph)
    return "".join(out)


def _raised(body: str) -> str:
    """The markup path, with the sentence's own punctuation left on the line.

    The catch-all pattern is deliberately loose about where an index ends, so
    the full stop of "The result is c^12." can land inside the capture. A
    raised full stop is a typographic error the reader notices immediately.
    """
    tail = ""
    while body and body[-1] in ".,;:":
        body, tail = body[:-1], body[-1] + tail
    # The brackets go for the same reason they go on the Unicode path: the
    # group is the whole index. Both paths have to agree about this, or one
    # index law prints as xᵃ⁺ᵇ and the next as x⁽ᵃ ˣ ᵇ⁾ two lines below it.
    if body.startswith("(") and body.endswith(")") and "(" not in body[1:-1]:
        body = body[1:-1]
    return (super_markup(body) if body else "") + tail


def set_indices(text: str, unicode_ok: bool = True,
                markup: bool = True) -> str:
    """Every caret index becomes a raised index. See THE RULE FOR INDICES.

    With `markup=False` the caller cannot render a `<super>` tag, so an index
    with no superscript spelling is returned untouched rather than tagged.
    See THE MARKUP-FREE MODE.
    """
    if unicode_ok:
        def glyphs(m: re.Match) -> str:
            sup = _as_superscript(m.group(1))
            if sup is not None:
                return sup
            return _raised(m.group(1)) if markup else m.group(0)
        text = _INDEX_RE.sub(glyphs, text)
    if not markup:
        return text
    # Whatever is left has no superscript spelling: set it in the real font.
    return _INDEX_ANY_RE.sub(lambda m: _raised(m.group(1)), text)


# "sqrt(9 + 16)" is a function call, not a square root. Only the bracketed
# form is converted: bare "sqrt 25" would need the root to reach over the 25
# and there is no way to draw the vinculum in a paragraph.
_SQRT_RE = re.compile(r"\bsqrt\s*(?=\()", re.IGNORECASE)

# "C = pi * d -> 62.8 = 3.14 × d". The ASCII arrow is the same defect as the
# ASCII times sign. Spaces both sides, so no "-->" or "<-" is half-converted.
# The escaped form is what actually arrives, because _escape turns the model's
# angle brackets into entities before this pass runs.
_ARROW_RE = re.compile(r"(?<=\s)-(?:>|&gt;)(?=\s)")

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
#
# Pi, a root and a superscripted term count as operands. Without them the Year
# 7 circle formulas printed "C = π * d" and "A = π * r²" in the box the topic
# is taught from, which is the one place on the page that has to look like
# mathematics.
_STAR_OPERAND = r"[0-9A-Za-z\)\]π" + SUPERSCRIPTS + r"]"
_STAR_MULT_RE = re.compile(
    r"(?<=" + _STAR_OPERAND + r")\s*\*\s*(?=[0-9A-Za-z\(π√])")

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

# "8000 / 2 = 4000", "e⁹ / e³", "(4a³b⁻²) / (2a⁻¹b⁴)". A slash with a space
# beside it is a division sign; a slash with no spaces ("3/4") is a fraction, a
# rate or a date and is never touched. See THE RULE FOR DIVISION.
#
# A term is a number, or a number-and-one-letter with an optional index. One
# letter is what keeps words out: "and / or", "yes / no" and "his / her" all
# fail because a second letter follows the first, and there is no reading of
# those in which the slash divides anything.
_SPACED_SLASH = r"(?:\s+/\s*|\s*/\s+)"
_DIV_TERM = (r"(?:[0-9]*[A-Za-z]|[0-9]+(?:\.[0-9]+)?)[" + SUPERSCRIPTS + r"]*")
# "3 / 4 of the pizza" is a quantity, not a sum.
_NOT_OF = r"(?![0-9]{0,3}\s+of\b)"
# The right operand must end where the term ends, or the "or" of "and / or"
# would divide. A bracket opens a term too: "(4a³b⁻²) / (2a⁻¹b⁴)".
_DIV_RIGHT = r"(?=(?P<rhs>" + _DIV_TERM + r")(?![A-Za-z0-9])|\()"
_SLASH_DIV_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<lhs>" + _DIV_TERM + r")" + _SPACED_SLASH
    + _NOT_OF + _DIV_RIGHT)
_SLASH_DIV_BRACKET_RE = re.compile(
    r"(?<=[\)\]])" + _SPACED_SLASH + _NOT_OF + _DIV_RIGHT)

# Rates a single letter either side would otherwise swallow. "m / s" is metres
# per second in every booklet that writes it and "m ÷ s" is not a thing. Only
# single-letter left operands need naming: a two letter unit like "km" is not
# a term under the rule above and "km / h" is already left alone.
_RATE_PAIRS = {("m", "s"), ("m", "h"), ("g", "L"), ("g", "l"), ("L", "s"),
               ("L", "h"), ("c", "s"), ("s", "m")}

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


def _divide(m: re.Match) -> str:
    lhs = (m.groupdict().get("lhs") or "")
    rhs = m.groupdict().get("rhs")
    if rhs is not None and (lhs, rhs) in _RATE_PAIRS:
        return m.group(0)
    return f"{lhs} {DIVIDE} "


def normalise_notation(text: str, unicode_ok: bool = True,
                       markup: bool = True) -> str:
    """One symbol per operation, whatever the model wrote.

    `unicode_ok` says whether the Unicode font registered. Superscripts past
    ² and ³, the root and the arrow are not Latin-1, so on the Helvetica
    fallback path they would print as boxes; indices go through the markup
    path instead and the rest is left as the model wrote it.

    `markup` says whether the caller can render ReportLab markup. Matplotlib
    cannot, so the diagram path passes False and gets characters only. See
    THE MARKUP-FREE MODE.

    Indices run first, so that a term carrying one ("e⁹", "b⁻²") is still
    recognisable as an operand to the multiplication and division rules that
    follow. The markup the fallback emits carries no space, no asterisk and no
    spaced slash, so nothing downstream can rewrite the inside of a tag.
    """
    text = set_indices(text, unicode_ok, markup)
    if unicode_ok:
        text = _SQRT_RE.sub(ROOT, text)
        text = _ARROW_RE.sub("→", text)
    text = _STAR_MULT_RE.sub(f" {MULTIPLY} ", text)
    text = _X_MULT_UNIT_RE.sub(lambda m: f"{m.group(1)} {MULTIPLY} ", text)
    text = _X_MULT_DIM_RE.sub(lambda m: f"{m.group(1)} {MULTIPLY} ", text)
    text = _X_MULT_RE.sub(rf"\1 {MULTIPLY} ", text)
    text = _SLASH_DIV_BRACKET_RE.sub(_divide, text)
    text = _SLASH_DIV_RE.sub(_divide, text)
    text = _WORD_DIV_RE.sub(f" {DIVIDE} ", text)

    def unit_word(m: re.Match) -> str:
        digit = "3" if m.group(1).lower() == "cubic" else "2"
        return _UNIT_WORDS[m.group(2).lower()] + _SUPER_23[digit]

    text = _POWER_WORD_RE.sub(unit_word, text)
    text = _UNIT_POWER_FLAT_RE.sub(lambda m: m.group(1) + _SUPER_23[m.group(2)], text)
    return text


# Every character this module can put on a page in markup-free mode. A
# consumer that draws its own text has to be able to draw all of these, or the
# tidy-up prints boxes; scripts/check_diagram_notation.py holds matplotlib's
# own default face to exactly this set.
PLAIN_GLYPHS = frozenset(SUPERSCRIPTS) | {MULTIPLY, DIVIDE, ROOT, "→"}


def normalise_plain(text: str) -> str:
    """One symbol per operation, in characters only, for a consumer that
    cannot render markup. See THE MARKUP-FREE MODE.

    Emphasis markers come off first, exactly as they do in `formatter._escape`
    and for the same reason: a model writes `*area*` for emphasis constantly,
    and the multiplication rule would otherwise read the markers as operators.
    """
    return normalise_notation(strip_emphasis(text), unicode_ok=True,
                              markup=False)
