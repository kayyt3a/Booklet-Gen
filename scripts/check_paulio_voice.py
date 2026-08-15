"""Checks Paulio narrates worked examples in primary booklets and only there.

Paulio is the study-buddy mascot. Having him teach the worked examples is
warm and on-brand for a Year 3 booklet. In a Year 10 Methods practice paper
it reads as patronising, and that is the booklet a tutor shows a paying
parent, so the mascot has a range rather than being applied everywhere.

The failure this guards against is silent in both directions: nothing errors
if a Year 10 booklet starts saying "Paulio shows you first", and nothing
errors if a Year 2 booklet never mentions him. Only reading the PDF would
catch it.

    PYTHONPATH=. python scripts/check_paulio_voice.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import formatter
from booklet_gen.formatter import paulio_teaches

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nPAULIO TEACHES PRIMARY, AND STOPS AT THE END OF IT")

for year in ("Year 1", "Year 2", "Year 3", "Year 4", "Year 5", "Year 6"):
    assert paulio_teaches(year), f"{year} should get the Paulio labels"
ok("Years 1 to 6 get the Paulio worked-example labels")

for year in ("Year 7", "Year 8", "Year 9", "Year 10", "Year 11", "Year 12"):
    assert not paulio_teaches(year), (
        f"{year} gets the Paulio labels. A bear cub narrating the worked "
        "example is not what a secondary student or their tutor is paying for")
ok("Years 7 to 12 keep the neutral labels")

print("\nBEFORE YEAR 1 COUNTS AS PRIMARY, NOT AS UNKNOWN")

for year in ("Pre-primary", "pre primary", "Kindergarten", "Foundation", "Prep"):
    assert paulio_teaches(year), (
        f"{year!r} carries no digit, so it must be named explicitly or the "
        "youngest students in the product are the ones who lose the mascot")
ok("Pre-primary, Kindergarten, Foundation and Prep are treated as primary")

print("\nAN UNRECOGNISED YEAR FAILS TOWARDS THE SAFE LABELS")

for bad in (None, "", "   ", "unknown", "Senior School"):
    assert not paulio_teaches(bad), (
        f"{bad!r} enabled Paulio. The neutral labels read fine to a seven "
        "year old; the Paulio labels do not read fine to a sixteen year old, "
        "so an unparseable year must fall to neutral, not to the mascot")
ok("an empty or unparseable year level falls back to the neutral labels")

print("\nTHE LABELS THEMSELVES ARE DISTINCT AND BOTH REACH THE PAGE")

assert formatter._WE_LABEL != formatter._WE_LABEL_PAULIO
assert formatter._GE_LABEL != formatter._GE_LABEL_PAULIO
assert "Paulio" in formatter._WE_LABEL_PAULIO
assert "Paulio" not in formatter._WE_LABEL
assert "Paulio" not in formatter._GE_LABEL
ok("the two label sets differ, and only the Paulio set names him")

import inspect  # noqa: E402
src = inspect.getsource(formatter._lesson_flowables)
assert "paulio_teaches(year_level)" in src, (
    "_lesson_flowables no longer decides from the year level, so every "
    "booklet gets whichever label set is hard-coded")
ok("_lesson_flowables picks the label set from the year level it is given")

# The year has to actually arrive, or the parameter defaults to None and
# every booklet silently renders neutral.
render_src = Path("booklet_gen/formatter.py").read_text(encoding="utf-8")
# Call sites only: the `def` line matches the same substring, so exclude it.
call_sites = [ln for ln in render_src.splitlines()
              if "_lesson_flowables(styles," in ln and "def " not in ln]
assert len(call_sites) == 2, (
    f"expected exactly two lesson call sites (class work, and the homework "
    f"carry-down), found {len(call_sites)}: {call_sites}")
# Calls wrap across lines, so compare against a whitespace-collapsed copy.
flat = " ".join(render_src.split())
assert "_lesson_flowables(styles, t, data.year_level)" in flat, (
    "the class work lesson is built without the year level, so Paulio never "
    "appears no matter which year the booklet is for")
assert "_lesson_flowables(styles, section.teaching, data.year_level)" in flat, (
    "the homework carry-down lesson is built without the year level, so a "
    "subtopic moved to Homework loses the mascot the rest of the booklet has")
ok("both lesson call sites pass the booklet's real year level")

print(f"\nALL {_passed} PAULIO VOICE CHECKS PASSED")
sys.exit(0)
