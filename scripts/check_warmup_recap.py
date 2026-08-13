"""Checks the warm-up recap knows what it is revising, and at what level.

A shipped Year 10 booklet opened with: 15% of 800, simplify 3a + 5b - a + 2b,
the area of a 12 by 7 rectangle, and solve 2x + 7 = 19. Those are Year 6, Year
7, Year 4 and Year 7 skills. Two separate faults produced that page.

THE RECAP DID NOT KNOW WHAT TO REVISE. Outside a term plan the focus was the
string "quick revision of key Mathematics skills learned earlier", which names
nothing, so the questions were whatever came to mind. A warm-up is spaced
retrieval or it is filler.

THE LEVEL WAS ABSOLUTE, NOT RELATIVE. difficulty_hint is one of three fixed
words and the recap always passed "easy", which the generator reads as easy
full stop rather than easy for the year. Nothing in the request mentioned the
year level at all.

Runs against a fake generator, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_warmup_recap.py
"""
import re
import sys

from booklet_gen.pipeline import BookletPipeline
from booklet_gen.schemas import Question, QuestionSet

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def says(text, phrase):
    """Whitespace-tolerant: these strings are built across several lines."""
    return re.search(r"\s+".join(map(re.escape, phrase.split())), text, re.I)


class SpyGenerator:
    """Records the subtopic the recap asked for."""

    def __init__(self):
        self.asked = []

    def generate(self, subject, year_level, topic, subtopic, chunks, **kw):
        self.asked.append(subtopic)
        return QuestionSet(questions=[
            Question(question=f"q{i}", answer="1", working="w") for i in range(6)])


def build(pipe, gen, *, focus, covered, year="Year 10"):
    gen.asked.clear()
    pipe._generator = gen
    pipe._validate_many = lambda subject, year, qs, chunks=None, **k: [
        type("R", (), {"verified": True, "notes": ""})() for _ in qs]
    pipe._reasoning_reject = lambda subject, q: False
    pipe._build_recap("Mathematics", year, focus, None, covered=covered)
    assert gen.asked, "the recap never called the generator"
    return gen.asked[-1]


pipe = BookletPipeline.__new__(BookletPipeline)
pipe._n_recap = 4
gen = SpyGenerator()

TODAY = [("Algebraic Techniques", "Expanding and factorising algebraic expressions"),
         ("Trigonometry", "Right angled triangles and Pythagoras theorem")]

print("\nA SINGLE BOOKLET: THE WARM-UP WARMS UP THE RIGHT MUSCLE")

st = build(pipe, gen, focus=None, covered=TODAY)

assert not says(st.name, "quick revision of key Mathematics skills learned earlier"), \
    ("the recap still asks for a general quiz, which is what produced a Year 10 "
     "warm-up about the area of a rectangle")
ok("the recap no longer asks for an unspecified general quiz")

assert says(st.name, "PREREQUISITE skills that today's lessons build on"), st.name
ok("a booklet with no week before it revises what today's lessons stand on")

assert says(st.name, "never the subtopic itself"), st.name
ok("it is told to ask for the skill BEFORE the subtopic, not the subtopic")

for _, sub in TODAY:
    assert sub in st.name, sub
ok("today's subtopics are named, so the prerequisites can be derived from them")

print("\nTHE LEVEL IS RELATIVE TO THE YEAR, NOT ABSOLUTE")

assert "Year 10" in st.name, \
    ("the year level is never mentioned in the recap request, so \"easy\" is "
     "read in absolute terms and a Year 10 warm-up asks Year 4 questions")
ok("the request names the year level the warm-up has to sit at")

assert says(st.name, "no more than one year below") or \
       says(st.name, "Nothing here may be more than one year below"), st.name
ok("a floor is stated, so easy cannot mean three years below")

# The floor has to move with the year, or it is just Year 10 hard-coded.
st7 = build(pipe, gen, focus=None, covered=TODAY, year="Year 7")
assert "Year 7" in st7.name and "Year 10" not in st7.name, st7.name
ok("the floor follows the year level rather than being written in once")

assert st.difficulty_hint == "easy", st.difficulty_hint
ok("the warm-up is still the easiest part of the booklet")

print("\nA TERM PLAN: THE WARM-UP REVISES LAST WEEK")

st = build(pipe, gen, focus="Indices and surds; Compound interest", covered=TODAY)
assert says(st.name, "LAST WEEK'S booklet"), st.name
assert "Indices and surds" in st.name and "Compound interest" in st.name, st.name
ok("with a previous week, the recap revises that week by name")

assert not says(st.name, "PREREQUISITE skills that today's lessons build on"), \
    "a term plan week fell back to the prerequisite wording and lost the spacing"
ok("spaced retrieval wins over prerequisites when there is a week to space")

print("\nTHE SPOILER GUARD SURVIVED BOTH PATHS")

for focus in (None, "Indices and surds"):
    st = build(pipe, gen, focus=focus, covered=TODAY)
    assert says(st.name, "Do NOT ask about anything this booklet is about to teach"), focus
    for _, sub in TODAY:
        assert sub in st.name, (focus, sub)
ok("neither path may ask about what the first lesson is about to teach")

print("\nTHE TERM PLAN RECAPS WHAT WAS TAUGHT, NOT WHAT WAS PLANNED")

import inspect  # noqa: E402

src = inspect.getsource(BookletPipeline.run_term_plan)
assert "prev_focus = wk.focus" not in src, (
    "next week's recap revises the planner's label for this week. The outline "
    "parser chooses the real subtopics and the hour cap can drop some, so the "
    "student would be revising a heading rather than the lessons they sat")
assert "data.sections" in src and "prev_focus" in src, src[-600:]
ok("the recap is built from the sections the booklet actually taught")

print(f"\nALL {_passed} WARM-UP CHECKS PASSED")
sys.exit(0)
