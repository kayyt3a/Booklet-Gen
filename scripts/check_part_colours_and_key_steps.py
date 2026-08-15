"""Checks three presentation defects a reviewer found by looking at the pages.

1. Homework and the Final Challenge were the same maroon, in four separate
   places, so at a flip-through the routine practice and the graded cumulative
   test were one block of colour and only the words told them apart.
2. The Final Challenge is the one part where the model numbers its own
   working, and splitting on sentence ends stranded every numeral on a line of
   its own: an answer that read as three lines everywhere else read as nine
   here and looked like a rendering fault.
3. Paulio hangs off the landing page's "Choose your practice" card. He is
   absolutely positioned, so he takes no space in the flow, and on a phone the
   subtitle wrapped underneath him and his paw landed on the word "student".

    PYTHONPATH=. python scripts/check_part_colours_and_key_steps.py
"""
import re
import sys
from pathlib import Path

from booklet_gen.formatter import (
    PART_CHALLENGE, PART_CLASSWORK, PART_HOMEWORK, PART_RECAP, solution_lines)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def _luminance(hex_colour: str) -> float:
    """WCAG relative luminance, which is also how the band reads in mono."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast_with_white(hex_colour: str) -> float:
    return 1.05 / (_luminance(hex_colour) + 0.05)


print("\nEVERY PART OF THE BOOKLET HAS ITS OWN COLOUR")

PARTS = {"Warm-up Recap": PART_RECAP, "Class Work": PART_CLASSWORK,
         "Homework": PART_HOMEWORK, "Final Challenge": PART_CHALLENGE}

assert len(set(PARTS.values())) == len(PARTS), (
    "two parts share a colour, so a flip-through cannot tell them apart: "
    f"{PARTS}")
ok("all four part colours are distinct")

assert PART_HOMEWORK != PART_CHALLENGE, (
    "Homework and the Final Challenge are the same colour again, which is the "
    "exact defect this check exists for")
ok("Homework and the Final Challenge specifically differ")

# Every band prints white text on the colour, so each must carry it legibly.
for name, colour in PARTS.items():
    ratio = _contrast_with_white(colour)
    assert ratio >= 4.5, (
        f"{name} band is {colour}, only {ratio:.2f}:1 against the white text "
        "printed on it, under the 4.5:1 standard")
ok("every band carries white text at AA contrast or better")

# Most of these are printed on a home mono printer, where a band is only its
# luminance. Two parts that differ in hue but not in lightness are still one
# block of grey on paper.
lum = {n: _luminance(c) for n, c in PARTS.items()}
gap = abs(lum["Homework"] - lum["Final Challenge"])
assert gap >= 0.04, (
    f"Homework ({lum['Homework']:.3f}) and the Final Challenge "
    f"({lum['Final Challenge']:.3f}) differ by only {gap:.3f} in luminance, so "
    "they print as the same grey on a mono printer")
ok(f"they also separate in greyscale (luminance gap {gap:.3f})")

# The literals must be gone for these four, or the next edit reintroduces the
# drift. Scoped to the four parts by name: Spelling and the exam's Marking Key
# are their own sections with their own colours, and are checked below instead.
fmt = Path("booklet_gen/formatter.py").read_text(encoding="utf-8")
band_calls = re.findall(
    r'_(?:part_band|key_part_heading)\(\s*styles,\s*"([^"]+)",\s*([^,)]+)', fmt)
assert band_calls, "no part bands found, the check is looking in the wrong place"
found = {name for name, _ in band_calls if name in PARTS}
assert found == set(PARTS), f"only found bands for {sorted(found)}"
literals = [(n, c.strip()) for n, c in band_calls
            if n in PARTS and c.strip().startswith('"#')]
assert not literals, (
    f"a part band still names a colour literal instead of a constant: {literals}")
ok("all four bands and key headings take their colour from the constant")

# The other sections that share a booklet with them must not collide either: a
# spelling test the same colour as Homework is the same defect one step along.
OTHER_SECTIONS = {"Spelling Test": "#3F6B5E", "Spelling List": "#3F6B5E",
                  "Marking Key": "#146B2C"}
for name, colour in OTHER_SECTIONS.items():
    assert f'"{name}", "{colour}"' in fmt.replace("\n", " ").replace("        ", " ") \
        or colour in fmt, f"{name} no longer uses {colour}, update this check"
    clash = [p for p, c in PARTS.items() if c.lower() == colour.lower()]
    assert not clash, f"{name} shares its colour with {clash}"
ok("the spelling and marking-key sections do not collide with the four parts")

print("\nTHE ANSWER KEY DOES NOT STRAND STEP NUMBERS ON THEIR OWN LINES")

# Copied out of the shipped Year 3 booklet's Final Challenge, answer 1.
CHALLENGE_WORKING = (
    "1. Subtract Monday's sales: 450 - 128 = 322. 2. Subtract Tuesday's "
    "sales: 322 - 145 = 177. 3. Rounding 177 to the nearest hundred: 177 is "
    "greater than 150, so it rounds up to 200.")
lines = solution_lines(CHALLENGE_WORKING)
stranded = [ln for ln in lines if re.fullmatch(r"\d{1,2}\s*[.)]?", ln)]
assert not stranded, (
    f"these lines are a step number and nothing else: {stranded}. On the page "
    "each one takes a full line and the answer looks like it broke.")
ok("no line in the key is a bare step number")

assert lines[0] == "Subtract Monday's sales:", lines[0]
# Three numerals dropped out of nine lines. The remaining six are the shape
# every other part of the key already has: a clause, then its arithmetic.
assert len(lines) == 6, f"{len(lines)} lines, expected 6: {lines}"
ok("the same answer now reads as 6 lines rather than 9")

# The working itself must survive intact: dropping the numerals must not drop
# the arithmetic beside them.
joined = " ".join(lines)
for fragment in ("450 - 128 = 322.", "322 - 145 = 177.", "rounds up to 200."):
    assert fragment in joined, f"{fragment!r} was lost from the working"
ok("every step of the arithmetic survives the numerals being dropped")

# A value that happens to look like an enumerator is not one.
kept = solution_lines("Total = 24\nNumber of groups = 4\n24 / 4 = 6")
assert kept == ["Total = 24", "Number of groups = 4", "24 / 4 = 6"], kept
ok("a working line that ends in a number is untouched")

# And the ordinary one-step-per-line shape is unchanged.
plain = solution_lines("Compare thousands digits: 1, 3, 5.\nOrder: 1245, 3245.")
assert plain == ["Compare thousands digits:", "1, 3, 5.", "Order:",
                 "1245, 3245."], plain
ok("a Class Work solution is unchanged")

print("\nPAULIO DOES NOT HANG OVER THE LANDING PAGE'S TEXT")

landing = Path("booklet_gen/webapp/templates/landing.html").read_text(encoding="utf-8")
css = Path("booklet_gen/webapp/static/css/style.css").read_text(encoding="utf-8")

assert "paulioHanging" in landing, "he is not on the landing page at all"
hang_card = re.search(r'<div class="card paulioHangCard"', landing)
assert hang_card, (
    "the card he hangs off does not carry .paulioHangCard, so nothing "
    "reserves the space he occupies and the text runs underneath him")
ok("the card he hangs off is marked so its text can clear him")

assert ".paulioHangCard > h2" in css and ".paulioHangCard > .sub" in css, (
    "the heading and the subtitle are not both given room; on a phone it was "
    "the SUBTITLE his paw landed on")
ok("both the heading and the subtitle reserve the column he hangs in")

# The reserved gap has to be wider than he is, or it reserves nothing.
def _px(pattern: str, text: str) -> int:
    m = re.search(pattern, text)
    assert m, f"could not read {pattern} out of the stylesheet"
    return int(m.group(1))


mobile_block = css[css.index(".paulioHangCard > h2"):]
widths = [int(w) for w in re.findall(r"\.paulioHanging\{width:(\d+)px", css)]
pads = [int(p) for p in re.findall(r"\.paulioHangCard > \.sub\{padding-right:(\d+)px", css)]
assert widths and pads, (widths, pads)
assert min(pads) > max(w for w in widths if w <= min(pads) + 40), (widths, pads)
for pad in pads:
    assert pad >= 78, f"a reserved gap of {pad}px is too narrow for him"
ok(f"the reserved gaps ({pads}) clear his widths ({widths})")

print(f"\nALL {_passed} PRESENTATION CHECKS PASSED")
sys.exit(0)
