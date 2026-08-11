"""Checks two promises about what a booklet contains.

HOMEWORK IS PRACTICE, NOT A SECOND LESSON. A subtopic that will not fit the
hour leaves the booklet. It used to move into Homework carrying its
mini-lesson, which turned Homework into a longer, unsupervised lesson: a
shipped Year 5 booklet taught three subtopics in the session and eight more
inside Homework, 66 homework questions against 12 in class, and printed an
estimate of 230 minutes on its cover. A child working alone cannot be taught a
new method by a box of text, and a parent cannot help with one they never saw
covered.

THE LEVEL IS SET WHERE IT IS CHOSEN. The outline parser picks the subtopics
and stamps each with a difficulty hint, so it decides the level of the whole
booklet, and it was the one agent that never read the product line's guide.
Nothing downstream can rescue a booklet whose outline is a year too easy.

Runs against fake agents, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_homework_and_level.py
"""
import re
import sys
from pathlib import Path

from booklet_gen.agents.outline_parser import OutlineParserAgent
from booklet_gen.pipeline import BookletPipeline, CLASSWORK_CAP_MINUTES
from booklet_gen.schemas import (
    Passage, Question, SubtopicOutput, SubtopicTeaching, ValidatedQuestion,
    WorkedExample,
)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def vq(text):
    return ValidatedQuestion(
        question=Question(question=text, answer="42", working="w"),
        verified=True, validator_notes="", retry_count=0)


def teaching(name):
    return SubtopicTeaching(
        intro_paragraphs=["Some teaching for " + name] * 3,
        key_points=["a", "b", "c"],
        worked_example=WorkedExample(question="q", steps=["s1", "s2"], answer="a"),
        guided_examples=[WorkedExample(question="g", steps=["s"], answer="a")],
    )


def section(name, n_class=4, n_home=4):
    return SubtopicOutput(
        topic=f"Topic {name}", subtopic=name, teaching=teaching(name),
        questions=[vq(f"{name} class {i}") for i in range(n_class)],
        homework_questions=[vq(f"{name} home {i}") for i in range(n_home)],
    )


print("\nHOMEWORK IS PRACTICE ON WHAT WAS TAUGHT")

# Eleven subtopics is what the shipped Year 5 booklet actually had. Only three
# of them fit the hour.
sections = [section(f"Sub {i}") for i in range(11)]
BookletPipeline._fit_classwork_to_cap(sections)

assert sections, "the fitter emptied the booklet"
ok(f"{len(sections)} of 11 subtopics survive the hour cap")

orphans = [s.subtopic for s in sections if not s.questions]
assert not orphans, (
    "a subtopic is in the booklet with no class work, so its lesson and its "
    f"homework print with nothing taught in the session: {orphans}")
ok("every subtopic left in the booklet is taught in the session")

homework_only = [s.subtopic for s in sections
                 if s.homework_questions and not s.questions]
assert not homework_only, homework_only
ok("no homework belongs to a subtopic the session never covered")

for s in sections:
    assert s.teaching is not None and s.questions, s.subtopic
ok("every mini-lesson in the booklet is followed by class work, not homework")

# The whole point: the booklet got smaller rather than heavier.
total_hw = sum(len(s.homework_questions) for s in sections)
total_cw = sum(len(s.questions) for s in sections)
assert total_hw <= total_cw * 2, (
    f"homework is {total_hw} questions against {total_cw} in class; the "
    "overflow is still being dumped into the homework half")
ok(f"homework ({total_hw}) is in proportion to class work ({total_cw})")

print("\nAN ENGLISH BOOKLET, WHERE A READING CANNOT BE SPLIT")

# A comprehension subtopic is one reading and all its questions, and the
# fitter will not split one. That drives it down a different branch: it cannot
# thin practice, so a whole subtopic must leave. Without a case like this the
# maths fixture above never reaches that branch at all, and a relocation bug
# there would ship unseen.
def reading_section(name, n=5):
    sec = section(name, n_class=n, n_home=n)
    for v in sec.questions + sec.homework_questions:
        v.question.passage_id = f"p-{name}"
    sec.passages = [Passage(id=f"p-{name}", title=name,
                            paragraphs=["Some reading."] * 3)]
    return sec


eng = [reading_section(f"Reading {i}") for i in range(8)]
BookletPipeline._fit_classwork_to_cap(eng)
stranded = [s.subtopic for s in eng if not s.questions]
assert not stranded, (
    "a comprehension subtopic was left in the booklet with its reading and "
    f"homework but nothing taught in the session: {stranded}")
ok("a reading that will not fit leaves the booklet whole, rather than "
   "becoming unsupervised homework")

assert eng, "the English booklet was emptied"
assert all(s.questions and s.teaching for s in eng), [s.subtopic for s in eng]
ok(f"{len(eng)} of 8 readings survive, each still taught in the session")

print("\nTHE HOUR IS STILL HELD")

from booklet_gen.timing import classwork_section_minutes  # noqa: E402

minutes = sum(classwork_section_minutes(s) for s in sections)
assert minutes <= CLASSWORK_CAP_MINUTES + 1, minutes
ok(f"the session fits the {CLASSWORK_CAP_MINUTES} minute cap ({round(minutes)} min)")

assert len(sections) >= 3, f"the booklet fell below the three-topic floor: {len(sections)}"
ok("the three-subtopic floor a credit buys is still met")

print("\nA BOOKLET THAT ALREADY FITS IS LEFT ALONE")

small = [section(f"S{i}") for i in range(3)]
before = [(s.subtopic, len(s.questions), len(s.homework_questions)) for s in small]
BookletPipeline._fit_classwork_to_cap(small)
after = [(s.subtopic, len(s.questions), len(s.homework_questions)) for s in small]
assert before == after, f"the fitter cut a booklet that already fitted\n{before}\n{after}"
ok("a three-subtopic booklet inside the hour is untouched")

print("\nTHE OUTLINE PARSER READS THE PRODUCT GUIDE")


class SpyClient:
    def __init__(self):
        self.user_turns = []

    def complete(self, system, user, tier="fast", temperature=0.2, **kw):
        self.user_turns.append(user)
        return ('{"subject":"Mathematics","year_level":"Year 5","topics":['
                '{"name":"A","subtopics":[{"name":"a1","difficulty_hint":"hard",'
                '"question_types":["word problem"]}]},'
                '{"name":"B","subtopics":[{"name":"b1","difficulty_hint":"medium",'
                '"question_types":["compute"]}]},'
                '{"name":"C","subtopics":[{"name":"c1","difficulty_hint":"hard",'
                '"question_types":["find the missing input"]}]}]}')


client = SpyClient()
agent = OutlineParserAgent(client, max_retries=2, min_topics=3)
agent.parse("Year 5 Mathematics", "GUIDANCE-SENTINEL: work one year above.")
assert "GUIDANCE-SENTINEL" in client.user_turns[-1], (
    "the outline parser never receives the product guide, so the agent that "
    "chooses the subtopics and their difficulty is the one agent working blind")
ok("the product guide reaches the agent that chooses the level")

client2 = SpyClient()
OutlineParserAgent(client2, max_retries=2, min_topics=3).parse("Year 5 Mathematics")
assert "GUIDANCE" not in client2.user_turns[-1]
ok("a call with no guide still works, so other products are unaffected")

print("\nTHE ACCELERATION RULE IS WRITTEN DOWN AND NOT CONTRADICTED")

guide = Path("booklet_gen/guidance/accelerate_practice.txt").read_text(encoding="utf-8")
parser_prompt = Path("booklet_gen/prompts/outline_parser.txt").read_text(encoding="utf-8")

# Whitespace-tolerant: the guide is hard-wrapped prose, so any phrase long
# enough to be worth asserting will sometimes straddle a line break.
def says(text, phrase):
    return re.search(r"\s+".join(map(re.escape, phrase.split())), text, re.I)


assert says(guide, "one full year above the year level requested"), \
    "the Accelerate guide does not state the working level"
ok("the Accelerate guide says the booklet is built one year above")

for phrase, why in (
    ("nothing may appear in a question whose method is not taught",
     "the teach-before-asking safety rule"),
    ("teach that prerequisite first", "the foundation rule"),
):
    assert says(guide, phrase), f"the guide raises the level without {why}"
ok("acceleration is paired with teaching it first and building the foundation")

# The parser prompt used to forbid reaching up in absolute terms, which is the
# exact opposite of the rule above. A prompt that contradicts the guide it is
# handed will follow whichever it read last, which is not a design.
assert "Never reach into a later year" not in parser_prompt, (
    "the outline prompt still forbids reaching into a later year, which "
    "directly contradicts the Accelerate guide it is now given")
ok("the outline prompt no longer contradicts the acceleration rule")

assert re.search(r"authoring instructions.*(win|say otherwise)", parser_prompt, re.I), \
    "the outline prompt does not say the product guide overrides its default"
ok("the outline prompt defers to the product guide on working level")

assert 'may be "easy"' in parser_prompt, \
    "nothing caps how many subtopics may be stamped easy, and that field is " \
    "copied into the question writer verbatim"
ok("at most one subtopic in an outline may be marked easy")

print(f"\nALL {_passed} HOMEWORK AND LEVEL CHECKS PASSED")
sys.exit(0)
