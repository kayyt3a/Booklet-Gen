"""Checks the long multiplication and short division figures.

These are the same class of thing as column arithmetic: methods whose entire
content is the LAYOUT. A shipped Year 5 booklet taught multiplication as
"243 x 2 = 486, then 243 x 10 = 2430, then add", which is a description of
the answer rather than the method. It never shows a carry, never shows where
the placeholder zero comes from, and hands the student two multiplications
they cannot do in place of the procedure that does them.

A wrong carry is worse than no carry, so every digit is checked here against
Python rather than eyeballed.

    PYTHONPATH=. python scripts/check_long_multiplication_and_division.py
"""
import re
import sys
from pathlib import Path

from booklet_gen.visuals.diagrams import SUPPORTED_TYPES, render_diagram
from booklet_gen.visuals import diagrams as D

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


class _F:
    label = lambda self, base=11.0: base       # noqa: E731
    note = lambda self, base=7.0: base         # noqa: E731


def drawn(renderer, spec):
    """Every text run the renderer emits, as (y, x, string), plus the lines."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows, lines = [], []
    real_text, real_plot = plt.Axes.text, plt.Axes.plot

    def spy_text(self, x, y, s, *a, **kw):
        rows.append((round(float(y), 2), round(float(x), 2), str(s)))
        return real_text(self, x, y, s, *a, **kw)

    def spy_plot(self, *a, **kw):
        if len(a) >= 2:
            lines.append((a[0], a[1]))
        return real_plot(self, *a, **kw)

    plt.Axes.text, plt.Axes.plot = spy_text, spy_plot
    try:
        out = Path("output/diagrams/_check_lmsd.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        renderer(spec, out, _F())
    finally:
        plt.Axes.text, plt.Axes.plot = real_text, real_plot
    return rows, lines


def row_at(rows, y):
    """Everything printed on one row, left to right, operators included."""
    return "".join(s for yy, _, s in sorted(rows, key=lambda r: r[1]) if yy == y)


def digits_at(rows, y):
    """Just the number on one row. The "+" beside the last partial product
    sits on that row too, so a raw read of it comes back as "+22920"."""
    return "".join(s for yy, _, s in sorted(rows, key=lambda r: r[1])
                   if yy == y and s.isdigit())


print("\nTHE FIGURES EXIST AND ARE ROUTED TO")

for kind in ("long_multiplication", "short_division"):
    assert kind in SUPPORTED_TYPES, f"{kind} is not a registered diagram type"
ok("both are registered diagram types")

qgen = Path("booklet_gen/prompts/question_generator_maths.txt").read_text(encoding="utf-8")
intro = Path("booklet_gen/prompts/intro_writer_maths.txt").read_text(encoding="utf-8")

for kind in ("long_multiplication", "short_division"):
    assert kind in qgen, (
        f"the question generator is never told {kind} exists, so it will never "
        "be asked for and the renderer is dead code")
    assert kind in intro, (
        f"the lesson writer cannot ask for {kind}, so the worked example still "
        "teaches the method in sentences")
ok("both prompts route multiplication and division to the figures")

for text, name in ((qgen, "question generator"), (intro, "lesson writer")):
    for kind, keys in (("long_multiplication", ('"top"', '"bottom"')),
                       ("short_division", ('"dividend"', '"divisor"'))):
        spec = re.search(r'\{"type":"' + kind + r'"[^}]*\}', text)
        assert spec, f"{name} names {kind} but shows no example spec to copy"
        for key in keys:
            assert key in spec.group(0), f"{name} {kind} example omits {key}"
ok("both prompts carry a complete example spec, not just the type name")

# The defect this whole change exists to fix: the lesson writer used to be
# free to teach multiplication as whole partial products with no digit
# working, which is what the shipped Year 5 booklet did.
assert "243 x 2 = 486" in intro, (
    "the lesson prompt does not name the partial-products-in-prose method as "
    "the thing to avoid, so nothing stops it coming back")
assert "carry" in intro.lower() and "placeholder" in intro.lower(), \
    "the lesson prompt does not require carries and the placeholder zero"
ok("the lesson prompt bans the prose shortcut and requires the real method")

assert "short division" in intro.lower() and "bus stop" in intro.lower(), \
    "the lesson prompt does not require short division by name"
assert "long division" in intro.lower(), \
    "the lesson prompt does not say when long division is NOT wanted"
ok("the lesson prompt requires short division and bounds long division")

print("\nLONG MULTIPLICATION: THE DIGITS ARE RIGHT")

for top, bottom in ((573, 46), (243, 12), (325, 22), (709, 45), (406, 14),
                    (8, 7), (999, 99), (250, 40)):
    rows, _ = drawn(D._long_multiplication,
                    {"type": "long_multiplication", "top": top,
                     "bottom": bottom, "show_answer": True})
    ys = sorted({y for y, _, _ in rows})
    assert digits_at(rows, ys[0]) == str(top * bottom), (
        f"{top} x {bottom}: bottom row is {digits_at(rows, ys[0])!r}, "
        f"expected {top * bottom}")
ok("the product on the bottom row is correct for every case checked")

# The two partial products are the method. 573 x 46 must show 573x6 = 3438 and
# 573x40 = 22920, the second carrying its placeholder zero.
rows, _ = drawn(D._long_multiplication,
                {"type": "long_multiplication", "top": 573, "bottom": 46,
                 "show_answer": True})
ys = sorted({y for y, _, _ in rows})
assert digits_at(rows, ys[1]) == "22920", digits_at(rows, ys[1])
assert digits_at(rows, ys[2]) == "3438", digits_at(rows, ys[2])
ok("573 x 46 shows partial products 3438 and 22920, in that order up the page")

assert digits_at(rows, ys[1]).endswith("0"), \
    "the second partial product has no placeholder zero, which is the step " \
    "the student is meant to be learning"
ok("the second partial product ends in the placeholder zero")

# The carries, per row, worked out independently here.
def carries_for(top, digit):
    marks, carry = [], 0
    for ch in reversed(str(top)):
        p = int(ch) * digit + carry
        carry = p // 10
        marks.append(str(carry) if carry else "")
    return [m for m in reversed(marks[:-1]) if m] or []


rows, _ = drawn(D._long_multiplication,
                {"type": "long_multiplication", "top": 573, "bottom": 46,
                 "show_answer": True})
# Addressed by the renderer's own coordinates rather than by "the top two
# rows": with no carries at all those rows carry no text, so the top row
# present would be the top NUMBER and the check would read it as carries.
ONES_CARRY_Y, TENS_CARRY_Y = 3.0, 3.85
assert row_at(rows, TENS_CARRY_Y) == "21", (
    f"the tens-digit carries are {row_at(rows, TENS_CARRY_Y)!r}, expected "
    "'21' (573 x 4 carries 1 then 2)")
assert row_at(rows, ONES_CARRY_Y) == "41", (
    f"the ones-digit carries are {row_at(rows, ONES_CARRY_Y)!r}, expected "
    "'41' (573 x 6 carries 1 then 4)")
ok("573 x 46 prints both carry rows, each with the right digits")

for top, digit, want in ((573, 6, ["4", "1"]), (573, 4, ["2", "1"]),
                         (999, 9, ["8", "8"]), (111, 2, [])):
    assert carries_for(top, digit) == want, (top, digit, carries_for(top, digit))
ok("the independent carry model agrees on the cases used above")

rows, _ = drawn(D._long_multiplication,
                {"type": "long_multiplication", "top": 111, "bottom": 11,
                 "show_answer": True})
assert not [s_ for y, _, s_ in rows if y >= ONES_CARRY_Y], \
    "carries were invented for 111 x 11, which needs none"
ok("111 x 11 needs no carrying, and none is drawn")

print("\nLONG MULTIPLICATION: THE FINISHED ROW'S CARRIES ARE CROSSED OUT")

# Crossing the ones row out is how a child keeps it apart from the tens row.
# Without it the two sets of carries sit above each other unmarked and the
# figure teaches a confusion rather than a method.
rows, lines = drawn(D._long_multiplication,
                    {"type": "long_multiplication", "top": 573, "bottom": 46,
                     "show_answer": True})
ones_y, tens_y = ONES_CARRY_Y, TENS_CARRY_Y
strikes = [(xs, yy) for xs, yy in lines
           if len(yy) == 2 and abs(sum(yy) / 2 - ones_y) < 0.3 and yy[0] != yy[1]]
assert len(strikes) == 2, (
    f"{len(strikes)} strike-throughs drawn over the finished carry row, "
    "expected one per carry")
assert not [(xs, yy) for xs, yy in lines
            if len(yy) == 2 and abs(sum(yy) / 2 - tens_y) < 0.3 and yy[0] != yy[1]], \
    "the carry row still in use was struck through as well"
ok("the finished row's carries are struck through and the live row's are not")

print("\nSHORT DIVISION: THE DIGITS ARE RIGHT")

for dividend, divisor in ((746, 3), (4824, 4), (938, 7), (146, 3), (1000, 8),
                          (91, 7), (5555, 5), (12345, 6)):
    rows, _ = drawn(D._short_division,
                    {"type": "short_division", "dividend": dividend,
                     "divisor": divisor, "show_answer": True})
    ys = sorted({y for y, _, _ in rows})
    quot = row_at(rows, ys[-1])
    want = str(dividend // divisor)
    rem = dividend % divisor
    if rem:
        want += f"r {rem}"
    assert quot.replace(" ", "") == want.replace(" ", ""), (
        f"{dividend} / {divisor}: quotient row is {quot!r}, expected {want!r}")
ok("the quotient and remainder are correct for every case checked")

# The carried remainders are the method: 746 / 3 carries 1 in front of the 4
# and 2 in front of the 6, which is what makes 14 and 26.
rows, _ = drawn(D._short_division,
                {"type": "short_division", "dividend": 746, "divisor": 3,
                 "show_answer": True})
ys = sorted({y for y, _, _ in rows})
carries = row_at(rows, ys[1])          # the small row just above the dividend
assert carries == "12", f"carried remainders are {carries!r}, expected '12'"
ok("746 / 3 carries the 1 in front of the 4 and the 2 in front of the 6")

rows, _ = drawn(D._short_division,
                {"type": "short_division", "dividend": 693, "divisor": 3,
                 "show_answer": True})
ys = sorted({y for y, _, _ in rows})
assert row_at(rows, ys[-1]) == "231", row_at(rows, ys[-1])
assert not [s for y, _, s in rows if y not in (ys[0], ys[-1])], \
    "carried remainders were drawn for 693 / 3, which divides exactly"
ok("693 / 3 divides exactly, and no remainder is carried anywhere")

# A leading zero is not written on the page: 146 / 3 starts at the 14.
rows, _ = drawn(D._short_division,
                {"type": "short_division", "dividend": 146, "divisor": 3,
                 "show_answer": True})
ys = sorted({y for y, _, _ in rows})
assert row_at(rows, ys[-1]).startswith("4"), \
    f"quotient starts {row_at(rows, ys[-1])!r}: a leading zero was printed"
ok("146 / 3 prints 48 r 2 without a leading zero over the hundreds")

print("\nA QUESTION DOES NOT PRINT ITS OWN WORKING")

# The same principle column_arithmetic already holds to: the carries and the
# partial products ARE the exercise, so printing them on a practice question
# hands the student the only part they were asked to do.
for top, bottom in ((573, 46), (325, 22), (463, 24)):
    rows, _ = drawn(D._long_multiplication,
                    {"type": "long_multiplication", "top": top,
                     "bottom": bottom, "show_answer": False})
    printed = "".join(s for _, _, s in rows)
    for forbidden in (str(top * bottom), str(top * (bottom % 10))):
        assert forbidden not in printed.replace(" ", ""), (
            f"{top} x {bottom} is a question but {forbidden} is already drawn "
            "on it")
    # Only the two numbers and the operator may appear.
    assert set(printed) <= set(str(top) + str(bottom) + "×"), printed
ok("a multiplication question shows no carries, partial products or answer")

for dividend, divisor in ((746, 3), (938, 7), (4824, 4)):
    rows, _ = drawn(D._short_division,
                    {"type": "short_division", "dividend": dividend,
                     "divisor": divisor, "show_answer": False})
    printed = "".join(s for _, _, s in rows)
    assert str(dividend // divisor) not in printed.replace(" ", ""), (
        f"{dividend} / {divisor} is a question but the quotient is drawn on it")
    assert "r" not in printed, "the remainder is drawn on a question"
    assert set(printed) <= set(str(dividend) + str(divisor)), printed
ok("a division question shows no quotient, remainder or carried digits")

# Hiding the working must not crop away the space it goes in. The quotient
# line above the bus stop is where the student writes, and a figure that
# cropped to the dividend would leave them nowhere to put the answer.
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

q = Path("output/diagrams/_check_sd_headroom.png")
q.parent.mkdir(parents=True, exist_ok=True)
D._short_division({"type": "short_division", "dividend": 938, "divisor": 7,
                   "show_answer": False}, q, _F())
grey = np.array(Image.open(q).convert("L"))
inked = np.where((grey < 240).any(axis=1))[0]
headroom = int(inked[0]) / grey.shape[0]
assert headroom > 0.15, (
    f"only {headroom:.0%} of the figure is blank above the first ink, so the "
    "quotient line was cropped away and the student has nowhere to answer")
ok(f"the quotient line is left open on a question ({headroom:.0%} of the figure)")

print("\nTHEY RENDER, AND REFUSE WHAT THEY CANNOT DRAW HONESTLY")

for spec in (
    {"type": "long_multiplication", "top": 573, "bottom": 46, "show_answer": True},
    {"type": "long_multiplication", "top": 325, "bottom": 22},
    {"type": "long_multiplication", "top": 243, "bottom": 7, "show_answer": True},
    {"type": "short_division", "dividend": 746, "divisor": 3, "show_answer": True},
    {"type": "short_division", "dividend": 938, "divisor": 7},
):
    path = render_diagram(spec)
    assert path and path.exists() and path.stat().st_size > 1000, spec
ok("both figures render with the answer shown and hidden")

for bad, why in (
    ({"type": "long_multiplication", "top": 573, "bottom": 461},
     "a 3-digit multiplier, which is no longer this method"),
    ({"type": "long_multiplication", "top": -5, "bottom": 4},
     "a negative operand"),
    ({"type": "long_multiplication", "top": 99999, "bottom": 99},
     "a product too wide for the page"),
    ({"type": "short_division", "dividend": 746, "divisor": 24},
     "a 2-digit divisor, which is long division"),
    ({"type": "short_division", "dividend": 746, "divisor": 1},
     "dividing by one"),
    ({"type": "short_division", "dividend": -12, "divisor": 3},
     "a negative dividend"),
):
    assert render_diagram(bad) is None, f"drew {why} instead of refusing"
ok("3-digit multipliers, 2-digit divisors and bad operands all refuse")

print(f"\nALL {_passed} LONG MULTIPLICATION AND SHORT DIVISION CHECKS PASSED")
sys.exit(0)
