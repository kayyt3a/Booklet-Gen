"""Checks the column arithmetic figure.

Column addition and subtraction is the one primary maths method whose entire
content is the LAYOUT: digits stacked in place-value columns, worked right to
left, with the carried or borrowed digit written above. Described in prose it
is unreadable ("5 + 3 = 8, 2 + 6 = 8, 4 + 1 = 5"), the carries vanish, and the
last line of the working disagrees with the answer, which is also what made
these questions ship without a verified tick.

A wrong carry is worse than no carry, so the arithmetic is checked here
against Python rather than eyeballed.

    PYTHONPATH=. python scripts/check_column_arithmetic.py
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


def marks_for(top, bottom, op):
    """Re-derive what the renderer decided to print above the columns."""
    total = top + bottom if op == "+" else top - bottom
    width = max(len(str(top)), len(str(bottom)), len(str(total)))
    captured = {}

    class Spy:
        def label(self, base=11.0):
            return base

        def note(self, base=7.0):
            return base

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    real_text = plt.Axes.text
    rows = []

    def spy_text(self, x, y, s, *a, **kw):
        rows.append((round(float(y), 2), round(float(x), 2), str(s)))
        return real_text(self, x, y, s, *a, **kw)

    plt.Axes.text = spy_text
    try:
        out = Path("output/diagrams/_check_colarith.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        D._column_arithmetic(
            {"type": "column_arithmetic", "top": top, "bottom": bottom,
             "operation": op, "show_answer": True}, out, Spy())
    finally:
        plt.Axes.text = real_text
    # Filter by the renderer's actual mark row (y_mark = 3.0), not by the
    # highest row present. With no carries at all the highest row IS the top
    # number, and "1111 + 1111" would then read as four carry marks.
    captured["marks"] = [s for y, _, s in sorted(rows) if y == 3.0]
    # Column index, not just the value. A carry with the right digit over the
    # wrong column is the error a child would copy, and comparing only the
    # list of values cannot see it: shifting every carry one column right
    # leaves the values identical. x = x0 + i*col, with x0 = col = 1.0.
    captured["columns"] = {int(round(x - 1.0)): s
                           for y, x, s in rows if y == 3.0}
    captured["rows"] = rows
    captured["width"] = width
    return captured


print("\nTHE FIGURE EXISTS AND IS ROUTED TO")

assert "column_arithmetic" in SUPPORTED_TYPES
ok("column_arithmetic is a registered diagram type")

qgen = Path("booklet_gen/prompts/question_generator_maths.txt").read_text(encoding="utf-8")
intro = Path("booklet_gen/prompts/intro_writer_maths.txt").read_text(encoding="utf-8")
assert "column_arithmetic" in qgen, \
    "the question generator is never told this figure exists, so it will " \
    "never be asked for and the renderer is dead code"
ok("the question generator prompt routes column work to it")

assert "column_arithmetic" in intro, \
    "the lesson writer cannot ask for it, so the worked example still teaches " \
    "the layout in sentences"
ok("the lesson writer prompt routes column work to it")

for text, name in ((qgen, "question generator"), (intro, "lesson writer")):
    spec = re.search(r'\{"type":"column_arithmetic"[^}]*\}', text)
    assert spec, f"{name} names the type but shows no example spec to copy"
    for key in ('"top"', '"bottom"', '"operation"'):
        assert key in spec.group(0), f"{name} example spec omits {key}"
ok("both prompts carry a complete example spec, not just the type name")

print("\nIT RENDERS")

for spec in (
    {"type": "column_arithmetic", "top": 4528, "bottom": 2694, "operation": "+",
     "show_answer": True},
    {"type": "column_arithmetic", "top": 3425, "bottom": 2163, "operation": "+"},
    {"type": "column_arithmetic", "top": 8342, "bottom": 3516, "operation": "-",
     "show_answer": True},
    {"type": "column_arithmetic", "top": 47, "bottom": 85, "operation": "+",
     "show_answer": True},
):
    path = render_diagram(spec)
    assert path and path.exists() and path.stat().st_size > 1000, spec
ok("addition, subtraction, answer shown and answer hidden all render")

print("\nTHE CARRIES AND BORROWS ARE RIGHT")

# Addition: a carry is written above column i-1 exactly when column i sums to
# 10 or more, which is the rule the child is being taught.
cap = marks_for(4528, 2694, "+")
assert cap["marks"] == ["1", "1", "1"], cap["marks"]
ok("4528 + 2694 carries 1 into the tens, hundreds and thousands")

# Columns are numbered left to right, so the carry out of the ones (column 3)
# is written above the tens (column 2), and nothing is written above the ones.
assert cap["columns"] == {0: "1", 1: "1", 2: "1"}, cap["columns"]
ok("each carry sits above the column it is carried INTO, never its own")

cap = marks_for(1111, 1111, "+")
assert cap["marks"] == [], f"carries were invented where none are needed: {cap['marks']}"
ok("1111 + 1111 needs no carrying, and none is drawn")

cap = marks_for(47, 85, "+")
assert cap["marks"] == ["1", "1"], cap["marks"]
assert cap["columns"] == {0: "1", 1: "1"}, cap["columns"]
ok("47 + 85 carries into a hundreds column the question never had")

# A column summing to EXACTLY ten is the only case that separates "carry when
# the column reaches ten" from "carry when it passes ten". Every column of
# 4528 + 2694 sums to eleven or twelve, so that case alone cannot tell a
# correct rule from an off-by-one, and a booklet would teach the off-by-one.
cap = marks_for(55, 55, "+")
assert cap["marks"] == ["1", "1"], \
    f"a column summing to exactly 10 did not carry: {cap['marks']}"
ok("55 + 55 carries on a column that reaches exactly ten, then again")

cap = marks_for(4050, 5050, "+")
assert cap["marks"] == ["1"], cap["marks"]
ok("a single exact-ten column mid-number carries, and only that one")

# Subtraction: 8342 - 3516 borrows twice. The renderer prints what each digit
# BECOMES, which is what a child writes above the crossed-out digit.
cap = marks_for(8342, 3516, "-")
assert cap["marks"] == ["7", "13", "3", "12"], cap["marks"]
assert cap["columns"] == {0: "7", 1: "13", 2: "3", 3: "12"}, cap["columns"]
ok("8342 - 3516 shows 2->12, 4->3, 3->13 and 8->7")

cap = marks_for(8000, 4327, "-")
assert cap["marks"] == ["7", "9", "9", "10"], cap["marks"]
ok("8000 - 4327 cascades the borrow across two zeros, the hardest case")

cap = marks_for(9876, 4321, "-")
assert cap["marks"] == [], f"borrows drawn where none are needed: {cap['marks']}"
ok("9876 - 4321 needs no borrowing, and none is drawn")

print("\nTHE ANSWER IS ONLY SHOWN WHEN ASKED FOR")


def digits_on(spec):
    out = Path("output/diagrams/_check_show.png")
    rows = []
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    real = plt.Axes.text

    def spy(self, x, y, s, *a, **kw):
        rows.append((round(float(y), 2), str(s)))
        return real(self, x, y, s, *a, **kw)

    plt.Axes.text = spy
    try:
        class F:
            label = lambda self, base=11.0: base
            note = lambda self, base=7.0: base
        D._column_arithmetic(spec, out, F())
    finally:
        plt.Axes.text = real
    return rows


shown = digits_on({"type": "column_arithmetic", "top": 4528, "bottom": 2694,
                   "operation": "+", "show_answer": True})
lowest = min(y for y, _ in shown)
assert "".join(s for y, s in shown if y == lowest) == "7222", shown
ok("show_answer prints the result under the rule")

hidden = digits_on({"type": "column_arithmetic", "top": 4528, "bottom": 2694,
                    "operation": "+", "show_answer": False})
assert not any(s in {"7", "2"} and y < 1.0 for y, s in hidden), hidden
joined = "".join(s for _, s in hidden)
assert "7222" not in joined, \
    "the answer is drawn on a question, so the student is handed it"
ok("without show_answer the columns stand empty for the student to fill in")

print("\nTHE CARRIES ARE THE EXERCISE, SO A QUESTION DOES NOT PRINT THEM")

# A shipped Year 5 warm-up asked for 6,015 - 2,759 and drew 5 9 10 15 above it.
# Every borrow in the question was already done, so the only thing being tested
# was four single-digit subtractions off a completed regroup. show_answer used
# to gate the result row alone, which made it look right in a worked example
# and left every practice question solved.
for top, bottom, op, what in (
    (6015, 2759, "-", "a borrow that cascades across a zero"),
    (5837, 3496, "+", "three carries"),
    (8000, 4327, "-", "the hardest borrow on the page"),
):
    rows = digits_on({"type": "column_arithmetic", "top": top, "bottom": bottom,
                      "operation": op, "show_answer": False})
    printed = [s for y, s in rows if y == 3.0]
    assert not printed, (
        f"{top} {op} {bottom} is a question, but {what} is already worked out "
        f"above the columns: {printed}. The student is handed the method they "
        "were asked to carry out.")
ok("no question prints its own carries or borrows, however hard the regroup")

# The same numbers as a worked example must still show everything, or the fix
# has simply deleted the figure's reason to exist.
rows = digits_on({"type": "column_arithmetic", "top": 6015, "bottom": 2759,
                  "operation": "-", "show_answer": True})
assert [s for y, s in rows if y == 3.0] == ["5", "9", "10", "15"], rows
ok("the same sum as a worked example still shows every borrow")

# Hiding the marks must not also crop away the line they were written on: that
# blank row is where the student writes their own regroups, and it is the only
# working space the figure offers.
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

# Drawn directly rather than through render_diagram, which caches by spec hash:
# a cached PNG from an earlier run of DIFFERENT code would be served back here
# and this check would pass by reading a stale picture.
q = Path("output/diagrams/_check_headroom.png")
q.parent.mkdir(parents=True, exist_ok=True)


class _F:
    label = lambda self, base=11.0: base       # noqa: E731
    note = lambda self, base=7.0: base         # noqa: E731


D._column_arithmetic({"type": "column_arithmetic", "top": 6015, "bottom": 2759,
                      "operation": "-", "show_answer": False}, q, _F())
grey = np.array(Image.open(q).convert("L"))
inked = np.where((grey < 240).any(axis=1))[0]
headroom = int(inked[0]) / grey.shape[0]
assert headroom > 0.2, (
    f"only {headroom:.0%} of the figure is blank above the first digit, so the "
    "carry row was cropped away with its contents and the student has nowhere "
    "to write a regroup")
ok(f"the carry row is left open on a question ({headroom:.0%} of the figure)")

print("\nIT REFUSES WHAT IT CANNOT DRAW HONESTLY")

for bad, why in (
    ({"type": "column_arithmetic", "top": 12, "bottom": 99, "operation": "-"},
     "a subtraction whose answer is negative"),
    ({"type": "column_arithmetic", "top": 5, "bottom": 3, "operation": "*"},
     "an operation it does not lay out"),
    ({"type": "column_arithmetic", "top": -4, "bottom": 3, "operation": "+"},
     "a negative operand"),
    ({"type": "column_arithmetic", "top": 99999999, "bottom": 1, "operation": "+"},
     "a number too wide for the page"),
):
    assert render_diagram(bad) is None, f"drew {why} instead of refusing"
ok("negative results, non-column operations and oversized numbers all refuse")

print(f"\nALL {_passed} COLUMN ARITHMETIC CHECKS PASSED")
sys.exit(0)
