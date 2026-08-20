"""Checks that validation grades what it claims to grade, and stays batched.

Three separate defects live here, all of them invisible on a booklet that
looks fine:

1. A comprehension question's answer is in its reading, not in its question.
   The judge is told to solve each question itself before grading it, so a
   judge that never receives the passage is grading English by guesswork while
   printing the same check mark maths earns from SymPy.

2. The generator is told to sort a set easiest to hardest. When a question
   failed validation the pipeline pulled a fresh set and took the first
   acceptable question from the TOP of it, so a hard question was replaced by
   the easiest one the model could write and dropped into a late slot. The
   difficulty ramp was dismantled after the model had finished building it.

3. Validation is batched, one call per set, and the Final Challenge had
   regressed to one call per question.

Runs against fake agents, so it needs no Gemini key and makes no API calls.

    PYTHONPATH=. python scripts/check_validation_integrity.py
"""
import sys

from booklet_gen.agents.llm_judge import LLMJudgeValidator
from booklet_gen.pipeline import BookletPipeline
from booklet_gen.schemas import Outline, Passage, Question, QuestionSet

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


class RecordingClient:
    """An LLM client that records every user turn and answers plausibly."""

    def __init__(self):
        self.calls = []

    def complete(self, system, user, tier="fast", temperature=0.0, **kw):
        self.calls.append(user)
        n = user.count("[Question ")
        if n:
            results = ", ".join(
                '{"index": %d, "solved_answer": "42", "verified": true, '
                '"reason": "ok"}' % i for i in range(n)
            )
            return '{"results": [%s]}' % results
        return '{"solved_answer": "42", "verified": true, "reason": "ok"}'


def q(text, answer="42", passage_id=None):
    return Question(question=text, answer=answer, working="working",
                    passage_id=passage_id)


PASSAGE = Passage(id="p1", title="The Lighthouse Keeper",
                  paragraphs=["Amara climbed the stairs each night.",
                              "She stayed because the ships needed her."])

print("\nTHE JUDGE SEES THE READING IT IS GRADING AGAINST")

client = RecordingClient()
judge = LLMJudgeValidator(client)

judge.validate("English", "Year 5",
               q("Why does Amara stay?", "The ships need her", passage_id="p1"),
               passages={"p1": PASSAGE})
turn = client.calls[-1]
assert "She stayed because the ships needed her." in turn, \
    "the judge graded a comprehension question without ever seeing the passage"
ok("a single comprehension question carries its reading to the judge")

assert "The Lighthouse Keeper" in turn
ok("the reading's title goes with it")

client.calls.clear()
judge.validate("English", "Year 5",
               q("Why does Amara stay?", passage_id="p1"), passages=[PASSAGE])
assert "the ships needed her" in client.calls[-1]
ok("a plain list of passages works as well as the id->passage pool")

client.calls.clear()
judge.validate("Mathematics", "Year 5", q("What is 6 x 7?"), passages={"p1": PASSAGE})
assert "Amara" not in client.calls[-1], \
    "an unrelated passage was pasted into a maths question's grading call"
ok("a question with no passage_id carries no reading, so maths pays nothing")

client.calls.clear()
judge.validate("English", "Year 5", q("Why?", passage_id="missing"),
               passages={"p1": PASSAGE})
ok("a passage_id that resolves to nothing is not a crash")

print("\nBATCHED GRADING CARRIES THE READING TOO")

client.calls.clear()
batch = [q("Why does Amara stay?", passage_id="p1"),
         q("What time does she climb?", passage_id="p1"),
         q("What is 6 x 7?")]
judge.validate_batch("English", "Year 5", batch, passages={"p1": PASSAGE})
turn = client.calls[-1]
assert "She stayed because the ships needed her." in turn, \
    "the batched judge never received the passage"
ok("a batched call carries the reading its questions ask about")

assert turn.count("She stayed because the ships needed her.") == 1, \
    "the passage was repeated per question, multiplying the tokens of the " \
    "one call batching exists to save"
ok("a shared reading is printed once, not once per question")

assert turn.index("She stayed") < turn.index("[Question 0]"), \
    "the reading printed after the questions that depend on it"
ok("the reading comes before the questions")

client.calls.clear()
visual = q("Use the labelled triangles to find EF.", "12")
visual.diagram_spec = {
    "type": "similar_triangles", "sides": [6, 5, 4], "scale": 2,
    "unknown": ["d"],
}
judge.validate_batch("Mathematics", "Year 9", [visual])
assert '"type": "similar_triangles"' in client.calls[-1]
assert '"unknown": ["d"]' in client.calls[-1]
ok("the existing batched judge receives diagram semantics")

client.calls.clear()
scene = q("A tree casts a 4 m shadow.", "6 m")
scene.scene_spec = {
    "template": "shadow_similarity", "version": 1, "unit": "m",
    "objects": [{"id": "tree", "kind": "tree", "height": 6, "shadow": 4}],
}
judge.validate_batch("Mathematics", "Year 9", [scene])
assert '"template": "shadow_similarity"' in client.calls[-1]
ok("the same batched judge receives scene semantics")

print("\nA SUBTOPIC HAS ONE VALIDATION BATCH")


class ScriptedGenerator:
    """Returns one final set, including one item the judge withholds."""

    def __init__(self):
        self.calls = 0

    def generate(self, subject, year_level, topic_name, subtopic,
                 reference_chunks=None, teaching=None, classwork_count=None,
                 passage_quota=2, **kw):
        self.calls += 1
        return QuestionSet(questions=[
            q("easy 1"), q("medium 2"), q("hard 3"), q("hardest 4")])


def build_pipeline(generator):
    p = BookletPipeline.__new__(BookletPipeline)
    p._generator = generator
    p._max_generation_attempts = 3
    p._n_classwork = 2
    p._norm_q = BookletPipeline._norm_q
    return p


# Grade "hard 3" a failure once, everything else a pass, and record which
# question the pipeline put in slot 3 afterwards.
class SlotPipeline(BookletPipeline):
    pass


from booklet_gen.agents.validator import ValidationResult  # noqa: E402

pipe = build_pipeline(ScriptedGenerator())
pipe._sympy = None
pipe._reasoning = None
pipe._judge = None
pipe._retriever = None


validation_calls = []


def fake_validate_many(subject, year_level, questions, reference_chunks=None,
                       passages=None):
    validation_calls.append(list(questions))
    return [ValidationResult(qq.question != "hard 3", "") for qq in questions]


pipe._validate_many = fake_validate_many
pipe._reasoning_reject = lambda subject, qq: False
pipe._resolve_visual = lambda qq: (None, None)
pipe._orphan_figure = lambda text, path: None
pipe._self_answering = lambda qq: None
pipe._absurd_quantity = lambda text: None
pipe._trusted = staticmethod(lambda qq, verified: verified)

from booklet_gen.pipeline import _SeenQuestions  # noqa: E402
from booklet_gen.schemas import Subtopic  # noqa: E402

seen = _SeenQuestions()
out = BookletPipeline._generate_and_validate(
    pipe, "Mathematics", "Year 5", "Number",
    Subtopic(name="Fractions", difficulty_hint="medium"),
    [], seen=seen,
)
texts = [vq.question.question for vq in out]
assert pipe._generator.calls == 1, "a verdict triggered question regeneration"
assert len(validation_calls) == 1 and len(validation_calls[0]) == 4, (
    "a subtopic used more than one validation batch")
ok("the final subtopic is generated once and validated once")

hard = next(vq for vq in out if vq.question.question == "hard 3")
assert hard.verified is False
ok("an unverified item is retained without an unearned verification mark")

print("\nVALIDATION STAYS BATCHED")

import inspect  # noqa: E402

src = inspect.getsource(BookletPipeline._build_challenge)
assert "_validate_many(" in src, \
    "the Final Challenge no longer validates as a batch"
assert "self._validate(" not in src, (
    "the Final Challenge validates one question per call, which is the "
    "per-question pattern the project notes forbid")
ok("the Final Challenge grades its whole set in one call, not one per question")

generation_src = inspect.getsource(BookletPipeline._generate_and_validate)
assert "self._validate(" not in generation_src, (
    "subtopic regeneration contains a per-question validation call")
assert generation_src.count("self._plan_question_visuals(") == 1, (
    "a subtopic must plan visuals exactly once, after its final question set "
    "is selected")
planner_src = inspect.getsource(BookletPipeline._plan_question_visuals)
assert planner_src.count("planner.plan(") == 1, (
    "the shared visual-planning helper must make one batched planner call")
assert generation_src.count("_validate_many(") == 1, (
    "a subtopic must have exactly one validation call")
ok("subtopics validate once and visual planning happens once")

many_src = inspect.getsource(BookletPipeline._validate_many)
assert "self._judge.validate(" not in many_src, (
    "a failed validation batch expands into one judge call per question")
ok("a failed judge batch fails closed instead of multiplying calls")

print(f"\nALL {_passed} VALIDATION INTEGRITY CHECKS PASSED")
sys.exit(0)
