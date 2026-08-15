"""Checks "a"/"an" agreement is corrected deterministically, not just asked for.

A shipped Year 5 booklet printed "A athlete runs 3/20 of a kilometre for each
lap of a track." The prompt can be told to get this right (and now is, see
check_prompt_contracts.py's global blocks), but a prompt is never a
guarantee. This is the "verified on the page" half, on the same footing as
_dedash: a deterministic pass over the actual rendered text.

    PYTHONPATH=. python scripts/check_article_agreement.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.formatter import _escape, _fix_articles

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nTHE EXACT DEFECT THAT SHIPPED IS FIXED")

fixed = _fix_articles("A athlete runs 3/20 of a kilometre for each lap.")
assert fixed == "An athlete runs 3/20 of a kilometre for each lap.", fixed
ok('"A athlete" is corrected to "An athlete"')

print("\nTHE DEFAULT RULE HOLDS BOTH DIRECTIONS")

cases = [
    ("a elephant ate", "an elephant ate"),
    ("an dog barked", "a dog barked"),
    ("a apple fell", "an apple fell"),
    ("An orange rolled", "An orange rolled"),   # already correct, unchanged
    ("A book sat", "A book sat"),               # already correct, unchanged
]
for src, want in cases:
    got = _fix_articles(src)
    assert got == want, f"{src!r} -> {got!r}, expected {want!r}"
ok("vowel-letter and consonant-letter words are corrected in both directions, "
   "and already-correct text is left alone")

print("\nSOUND-BASED EXCEPTIONS OVERRIDE THE SPELLING RULE")

exceptions = [
    ("an university", "a university"),   # 'yoo' sound despite the U
    ("an uniform", "a uniform"),
    ("an one-way street", "a one-way street"),  # 'won' sound
    ("a hour later", "an hour later"),    # silent H
    ("a honest answer", "an honest answer"),
]
for src, want in exceptions:
    got = _fix_articles(src)
    assert got == want, f"{src!r} -> {got!r}, expected {want!r}"
ok("the sound exceptions (university, one, hour, honest, ...) override the "
   "letter-based default in both directions")

print("\nA NUMBER AFTER THE ARTICLE IS LEFT ALONE, NOT GUESSED AT")

untouched = "a 8 cm rectangle and an 180 cm rope"
assert _fix_articles(untouched) == untouched, _fix_articles(untouched)
ok("digit-led words are never touched, since a wrong guess there would be a "
   "new bug rather than a fix for this one")

print("\nTHE FIX RUNS ON EVERY PRINTED FIELD, NOT JUST ONE HELPER")

rendered = _escape("A athlete ran—fast.")
assert "An athlete" in rendered, rendered
assert "—" not in rendered, "the em dash backstop should still fire too"
ok("_escape applies the article fix alongside the existing em-dash backstop, "
   "so every question, answer and lesson line gets both")

print(f"\nALL {_passed} ARTICLE AGREEMENT CHECKS PASSED")
sys.exit(0)
