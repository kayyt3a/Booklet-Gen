"""Checks the generator is told what a year level actually contains.

A shipped Year 10 Mathematics booklet taught, as its class work:

    Expanding and factorising algebraic expressions   (worked example: 4(2x+5))
    Solving simultaneous linear equations
    Right angled triangles and Pythagoras theorem

In the Western Australian curriculum, expanding and factorising with a
NUMERICAL factor is Year 8, and Pythagoras' theorem is Year 8. Two of the three
lessons were two years below the booklet's own cover, and this is an Academic
Accelerate booklet, which is meant to work a year ABOVE. Its warm-up asked for
the area of a 12 by 7 rectangle, which is Year 4.

Nothing in the pipeline knew any of that. "Year 10" was a string in a prompt,
and a model asked to write Year 10 maths fills it in from a general sense of
school maths, which skews low. Telling it to write harder questions does not
fix that, because the subtopics it chose are not obviously wrong: they are
plausible maths topics that happen to belong to another year.

    PYTHONPATH=. python scripts/check_curriculum_level.py
"""
import inspect
import re
import sys

from booklet_gen import curriculum
from booklet_gen.pipeline import BookletPipeline

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nTHE MAP KNOWS WHICH YEAR A SKILL BELONGS TO")

y10 = " | ".join(curriculum.skills_for("Mathematics", "Year 10")).lower()
for phrase, why in (
    ("simultaneous", "the one subtopic the shipped booklet got right"),
    ("quadratic", "Year 10 graphs quadratics and exponentials"),
    ("compound interest", "Year 10 financial maths"),
    ("conditional", "Year 10 conditional probability"),
    ("elevation and depression", "what Year 10 trigonometry actually asks for"),
):
    assert phrase in y10, f"Year 10 maths is missing {why}: {phrase}"
ok("Year 10 maths lists the content that year is actually for")

y8 = " | ".join(curriculum.skills_for("Mathematics", "Year 8")).lower()
assert "pythagoras" in y8, "Pythagoras is Year 8 and the map must say so"
assert "numerical factor" in y8, "expanding with a numerical factor is Year 8"
ok("the two subtopics that shipped as Year 10 are recorded as Year 8")

assert curriculum.skills_for("Mathematics", "Year 99") == []
assert curriculum.skills_for("Astrology", "Year 5") == []
ok("an unknown year or subject returns nothing rather than guessing")

print("\nTHE BLOCK NAMES THE EARLIER WORK SO IT CAN BE REFUSED")

block = curriculum.guidance_block("Mathematics", "Year 10")
assert block, "no guidance produced for Year 10 Mathematics"

# The half that fixes under-pitching. Listing what Year 10 covers does not stop
# a model choosing Year 8 work, because Year 8 work still looks like maths.
assert "Year 8" in block, block[:200]
assert re.search(r"Pythagoras.*\(Year 8\)", block, re.I | re.S), \
    ("Pythagoras is not named as belonging to an earlier year, so nothing "
     "stops it being chosen as a Year 10 subtopic again")
assert re.search(r"numerical factor.*\(Year 8\)", block, re.I | re.S), block[-800:]
ok("both shipped subtopics appear in the block as earlier-year work")

assert "NONE of them is a subtopic" in block, block[-600:]
ok("the block says plainly that earlier work may be used but not taught")

# It has to be assumable, or the block would forbid using arithmetic in algebra.
assert "may assume these" in block
ok("earlier skills may still be USED inside a harder question")

print("\nIT MOVES WITH THE YEAR")

b5 = curriculum.guidance_block("Mathematics", "Year 5")
# The heading is upper-cased, so compare case-insensitively.
assert "year 5" in b5.split("\n")[1].lower() and "Year 10" not in b5
assert "remainders" in b5.lower(), "Year 5 divides WITH remainders; Year 4 does not"
assert "no remainder" in " ".join(
    s for _, s in curriculum.earlier_skills("Mathematics", "Year 5")).lower()
ok("Year 5 gets Year 5 content, with Year 4 named as already covered")

assert curriculum.guidance_block("Mathematics", "Pre-primary")
assert "Year 1" not in curriculum.guidance_block("Mathematics", "Pre-primary")
ok("the earliest year has no earlier years to refuse, and does not invent any")

print("\nENGLISH IS COVERED TOO")

e10 = " ".join(curriculum.skills_for("English", "Year 10")).lower()
e7 = " ".join(curriculum.skills_for("English", "Year 7")).lower()
assert "evaluate" in e10, "Year 10 English evaluates, it does not only explain"
assert "explain" in e7 and "evaluate" not in e7, \
    "Year 7 English explains; evaluating is later, and the map must separate them"
ok("English separates explain (Year 7) from evaluate (Year 10)")

assert curriculum.guidance_block("English", "Year 9")
ok("English produces a block as well as Mathematics")

print("\nTHE PIPELINE ACTUALLY SENDS IT")

src = inspect.getsource(BookletPipeline.run_program)
assert "curriculum.guidance_block" in src, (
    "the curriculum map exists but no agent is given it, which makes it a "
    "document rather than a change")
assert "self._parser.parse(description, subject_guidance)" in src, (
    "the outline parser is the agent that chooses the subtopics and stamps "
    "their difficulty, so it is the one that has to know the year's content")
ok("the outline parser receives the curriculum for its subject and year")

assert re.search(r"subject_guidance\b.*=.*authoring_guidance\s*\+", src, re.S), src[:400]
ok("the product guide is kept and the curriculum is added to it, not swapped in")

# NAPLAN runs maths and English through the same loop; one shared block would
# hand English teachers a list of quadratics.
assert src.index("for subj in subjects") < src.index("curriculum.guidance_block"), \
    "the block is built once for all subjects, so a NAPLAN booklet would get " \
    "one subject's curriculum for both"
ok("the block is built per subject, inside the loop")

print("\nRIGHTS ARE RECORDED")

doc = curriculum.__doc__ or ""
assert "NON-COMMERCIAL" in doc and "CC BY 4.0" in doc, doc[:300]
ok("the licensing position is written down where the data lives")
assert curriculum.ATTRIBUTION and "Australian Curriculum" in curriculum.ATTRIBUTION
ok("an attribution string exists for anywhere this is surfaced")

print(f"\nALL {_passed} CURRICULUM CHECKS PASSED")
sys.exit(0)
