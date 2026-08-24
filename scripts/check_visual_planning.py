"""Deterministic contract for planning and enforcing student visuals.

    PYTHONPATH=. python scripts/check_visual_planning.py
"""
import os
import sys
import tempfile
from pathlib import Path

from booklet_gen.agents.consistency import (
    reconcile_diagram_spec,
    reconcile_scene_spec,
)
from booklet_gen.agents.visual_planner import (
    VisualPlanItem,
    VisualPlannerAgent,
    apply_visual_plan,
)
from booklet_gen.schemas import (
    Question,
    QuestionSet,
    Subtopic,
    SubtopicTeaching,
    ValidatedQuestion,
    WorkedExample,
)
from booklet_gen.visual_policy import (deterministic_diagram_spec,
                                       deterministic_priority,
                                       rendered_visual_coverage,
                                       student_safe_spec,
                                       visual_coverage_policy,
                                       visual_planner_enabled)
from booklet_gen.visuals import diagrams

_diagram_cache = tempfile.TemporaryDirectory(prefix="folio-visual-planning-")
diagrams.CACHE_DIR = Path(_diagram_cache.name)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


class RecordingClient:
    def __init__(self):
        self.calls = []

    def complete(self, system, user, **kwargs):
        self.calls.append(user)
        return """{"plans":[
          {"item_index":0,"priority":"required","visual_kind":"scene",
           "scene_spec":{"template":"shadow_similarity","version":1,
             "unit":"m","objects":[
               {"id":"reference","kind":"tree","height":6,"shadow":4},
               {"id":"target","kind":"tree","height":null,"shadow":10}],
             "unknown":{"object_id":"target","measure":"height","symbol":"x"}},
           "reason":"The measurements form the question."},
          {"item_index":1,"priority":"helpful","visual_kind":"diagram",
           "diagram_spec":{"type":"number_line","start":0,"end":10},
           "reason":"Shows order."}
        ]}"""


print("\nTHE PLANNER SEES QUESTIONS, NOT THE ANSWER KEY")
client = RecordingClient()
planner = VisualPlannerAgent(client)
questions = [
    Question(question="A 6 m tree casts a 4 m shadow. A second tree casts a "
                      "10 m shadow. Find its height.",
             answer="SECRET ANSWER 15", working="SECRET WORKING ratio"),
    Question(question="Place 7 on a number line.", answer="SECRET TWO",
             working="SECRET WORKING TWO",
             diagram_spec={"type": "number_line", "start": 0, "end": 8}),
]
plans = planner.plan("Mathematics", "Year 9", "Similarity", "Scale", questions)
assert len(client.calls) == 1, "planner made more than one call for one subtopic"
turn = client.calls[0]
for secret in ("SECRET ANSWER 15", "SECRET WORKING ratio", "SECRET TWO",
               "SECRET WORKING TWO"):
    assert secret not in turn, f"planner received answer-key material: {secret}"
assert questions[0].question in turn and questions[1].question in turn
ok("one batch contains question text and no answer or working")

apply_visual_plan(questions, plans)
assert questions[0].scene_spec["template"] == "shadow_similarity"
assert questions[1].diagram_spec["end"] == 8, (
    "planner overwrote an existing generator-authored spec")
ok("a missing scene is added while an existing good spec is preserved")

broken = Question(
    question="Place 7 on the number line.",
    answer="7",
    working="Mark 7.",
    diagram_spec={"type": "similar_triangles", "sides": [1, 2, 9], "scale": 2},
)
fallback = VisualPlanItem(
    item_index=0,
    priority="required",
    visual_kind="diagram",
    diagram_spec={"type": "number_line", "start": 0, "end": 10, "marks": [7]},
)
apply_visual_plan([broken], [fallback])
assert broken.diagram_spec and broken.diagram_spec["type"] == "number_line"
ok("an unrenderable existing spec yields to a valid planner fallback")

print("\nDETERMINISTIC POLICY OVERRIDES AN UNSAFE TEXT-ONLY CHOICE")
assert deterministic_priority("Use the diagram below to find x.") == "required"
assert deterministic_priority("Find the missing side of similar triangles.") == "required"
ok("figure-dependent and inherently visual questions are required")

assert deterministic_priority(
    "How is courage shown in the passage?", "English", "Comprehension",
) == "text-only"
assert deterministic_priority("Find the area of the shape shown below.") == "required"
assert deterministic_priority("What value is shown in the graph?") == "required"
ok("ordinary English uses of shown stay text-only while real figures are required")

print("\nEXACT QUESTION FORMS GET A FORMAL VISUAL FALLBACK")
assert deterministic_diagram_spec(
    "Calculate 694 ÷ 5.", "Mathematics", "Division with remainders",
) == {
    "type": "short_division", "dividend": 694, "divisor": 5,
    "show_answer": False,
}
assert deterministic_diagram_spec(
    "Calculate 582 - 246.", "Mathematics", "Three-digit subtraction",
)["type"] == "column_arithmetic"
line = deterministic_diagram_spec(
    "Mark the position of 1/4 on a number line from 0 to 1.",
    "Mathematics", "Unit fractions on a number line",
)
assert line and line["divisions"] == 4 and line["mark_at"] == []
assert deterministic_diagram_spec(
    "Identify the fraction marked on the number line.",
    "Mathematics", "Unit fractions on a number line",
) is None
ok("arithmetic and construct-a-number-line tasks no longer depend on model compliance")

print("\nSTUDENT ALGORITHMS NEVER PRINT THEIR ANSWERS")
for kind in ("column_arithmetic", "long_multiplication", "short_division"):
    source = {"type": kind, "show_answer": True}
    student = student_safe_spec(source, mode="student")
    teaching = student_safe_spec(source, mode="teaching")
    assert student["show_answer"] is False
    assert teaching["show_answer"] is True
    assert source["show_answer"] is True, "resolver mutated the caller's dict"
ok("resolver mode suppresses student answers and permits teaching answers")

line_spec = {
    "type": "number_line", "from": 0, "to": 1, "divisions": 3,
    "mark_at": [1 / 3], "label_at": ["0.33"],
}
student_line, changed = reconcile_diagram_spec(
    line_spec, "Mark the position of 1/3 on a number line.", mode="student",
)
teacher_line, _ = reconcile_diagram_spec(
    line_spec, "Mark the position of 1/3 on a number line.", mode="teaching",
)
assert changed and student_line["mark_at"] == [] and student_line["label_at"] == []
assert teacher_line["mark_at"] == [1 / 3]
ok("guided number lines hide the pre-drawn point while worked examples may show it")

print("\nSCENES CARRY ONLY STATED FACTS AND HIDE THE UNKNOWN")
scene = questions[0].scene_spec
fixed, changed = reconcile_scene_spec(scene, questions[0].question, mode="student")
assert fixed is not None
target = next(o for o in fixed["objects"] if o["id"] == "target")
assert target["height"] is None
assert fixed["unknown"]["symbol"] == "x"
ok("the student scene clears its unknown value")

unsafe = {
    "template": "shadow_similarity", "version": 1, "unit": "m",
    "objects": [{"id": "tree", "kind": "tree", "height": 6, "shadow": 99}],
}
fixed, changed = reconcile_scene_spec(
    unsafe, "A 6 m tree casts a 4 m shadow.", mode="student")
assert fixed is None, "an unstated visible measurement survived reconciliation"
ok("a scene with an invented visible fact is refused")

mixed = {
    "template": "shadow_similarity", "version": 1, "unit": "m",
    "objects": [
        {"id": "one", "kind": "tree", "height": 6, "shadow": 4},
        {"id": "two", "kind": "tree", "height": None, "shadow": 10},
    ],
    "unknown": {"object_id": "two", "measure": "height", "symbol": "x"},
}
fixed, _ = reconcile_scene_spec(
    mixed, "A 6 m tree has a 4 m shadow and another has a 10 cm shadow.")
assert fixed is None, "a mixed-unit scene was accepted as one scale"
ok("mixed measurement units are refused")

print("\nCOVERAGE COUNTS REAL RENDERS, NOT SPECS")
required = Question(question="Use the diagram to answer.", answer="1", working="",
                    diagram_spec={"type": "number_line"},
                    visual_priority="required")
helpful = Question(question="Compare fractions.", answer="1/2", working="",
                   diagram_spec={"type": "fraction_bar"},
                   visual_priority="helpful")
items = [
    ValidatedQuestion(question=required, verified=True, image_path=None),
    ValidatedQuestion(question=helpful, verified=True, image_path="rendered.png"),
]
coverage = rendered_visual_coverage(items)
assert coverage.rendered == 1 and coverage.eligible_rendered == 1
assert coverage.required == 1 and coverage.required_rendered == 0
assert coverage.required_rate == 0.0
ok("a spec without an image_path does not count as visual coverage")

strong = [
    ValidatedQuestion(
        question=Question(question=f"Context {i}", answer="1", working="1",
                          visual_priority="strong"),
        verified=True,
        image_path="scene.png" if i < 2 else None,
    )
    for i in range(4)
]
assert visual_coverage_policy(strong).met
strong[1].image_path = None
policy = visual_coverage_policy(strong)
assert not policy.met and policy.target == 2 and policy.shortfall == 1
ok("strong visual sets expose a deterministic failure below half coverage")

print("\nA REQUIRED VISUAL THAT DOES NOT RENDER CANNOT SHIP")
from booklet_gen.agents.validator import ValidationResult  # noqa: E402
from booklet_gen.pipeline import BookletPipeline, _SeenQuestions  # noqa: E402


print("\nTHE PLANNER HAS A SAFE OPERATIONAL KILL SWITCH")
assert visual_planner_enabled({})
for value in ("0", "false", "FALSE", "no", "off"):
    assert not visual_planner_enabled({"FOLIO_VISUAL_PLANNER_ENABLED": value})


class UnexpectedPlanner:
    def plan(self, *args, **kwargs):
        raise AssertionError("disabled visual planner was called")


disabled_pipe = BookletPipeline.__new__(BookletPipeline)
disabled_pipe._visual_planner = UnexpectedPlanner()
disabled_question = Question(
    question="Calculate 582 - 246.", answer="336", working="582 - 246 = 336",
)
old_switch = os.environ.get("FOLIO_VISUAL_PLANNER_ENABLED")
os.environ["FOLIO_VISUAL_PLANNER_ENABLED"] = "0"
try:
    disabled_pipe._plan_question_visuals(
        "Mathematics", "Year 4", "Subtraction", "Written algorithms",
        [disabled_question],
    )
finally:
    if old_switch is None:
        os.environ.pop("FOLIO_VISUAL_PLANNER_ENABLED", None)
    else:
        os.environ["FOLIO_VISUAL_PLANNER_ENABLED"] = old_switch
assert disabled_question.diagram_spec
assert disabled_question.diagram_spec["type"] == "column_arithmetic"
ok("the kill switch skips model calls but keeps deterministic required diagrams")


class OneQuestionGenerator:
    def generate(self, *args, **kwargs):
        return QuestionSet(questions=[Question(
            question="Use the diagram below to find x.", answer="4", working="4",
        )])


pipe = BookletPipeline.__new__(BookletPipeline)
pipe._generator = OneQuestionGenerator()
pipe._max_generation_attempts = 1
pipe._n_classwork = 1
pipe._validate_many = lambda *a, **k: [ValidationResult(True, "ok")]
pipe._reasoning_reject = lambda *a: False
pipe._resolve_visual = lambda *a, **k: (None, None)
pipe._orphan_figure = lambda *a: None
pipe._self_answering = lambda *a: None
pipe._absurd_quantity = lambda *a: None
pipe._trusted = lambda *a: True
out = BookletPipeline._generate_and_validate(
    pipe, "Mathematics", "Year 7", "Geometry", Subtopic(name="Figures"),
    [], seen=_SeenQuestions(),
)
assert out == [], "a required figure failure remained in the booklet"
ok("required render failure drops the item")

print("\nTEACHING AND PRACTICE SHARE ONE SAFE PLANNING BATCH")


class PlainQuestionGenerator:
    def generate(self, *args, **kwargs):
        return QuestionSet(questions=[Question(
            question="What is 2 + 2?", answer="4", working="2 + 2 = 4",
        )])


class BatchPlanner:
    def __init__(self):
        self.calls = []

    def plan(self, subject, year_level, topic, subtopic, questions):
        self.calls.append(list(questions))
        return [VisualPlanItem(item_index=i) for i in range(len(questions))]


lesson = SubtopicTeaching(
    intro_paragraphs=["Add the parts."],
    key_points=["Count on."],
    worked_example=WorkedExample(
        question="What is 1 + 1?", steps=["Count on one."], answer="2",
    ),
    guided_examples=[WorkedExample(
        question="Mark 1/3 on the number line.",
        steps=["Split the interval into three equal parts."], answer="1/3",
        diagram_spec={
            "type": "number_line", "start": 0, "end": 1,
            "intervals": 3, "mark_at": 1 / 3, "label_at": "1/3",
        },
    )],
)
pipe = BookletPipeline.__new__(BookletPipeline)
pipe._generator = PlainQuestionGenerator()
pipe._visual_planner = BatchPlanner()
pipe._n_classwork = 1
pipe._validate_many = lambda *a, **k: [ValidationResult(True, "ok")]
pipe._reasoning_reject = lambda *a: False
resolved = []


def fake_resolve(item, mode="student"):
    resolved.append((item, mode))
    return Path(f"{mode}.png"), None


pipe._resolve_visual = fake_resolve
pipe._orphan_figure = lambda *a: None
pipe._self_answering = lambda *a: None
pipe._absurd_quantity = lambda *a: None
pipe._trusted = lambda *a: True
out = BookletPipeline._generate_and_validate(
    pipe, "Mathematics", "Year 3", "Number", Subtopic(name="Addition"),
    [], teaching=lesson, seen=_SeenQuestions(),
)
assert len(out) == 1
assert len(pipe._visual_planner.calls) == 1
planned = pipe._visual_planner.calls[0]
assert [q.question for q in planned] == [
    "What is 1 + 1?", "Mark 1/3 on the number line.", "What is 2 + 2?",
]
assert planned[0].answer == "" and planned[0].working == ""
assert any(item is lesson.worked_example and mode == "teaching"
           for item, mode in resolved)
assert any(item is lesson.guided_examples[0] and mode == "guided"
           for item, mode in resolved)
assert lesson.guided_examples[0].answer_image_path == "teaching.png"
assert lesson.guided_examples[0].image_path == "guided.png"
ok("worked examples and final questions use one answer-free planner call")

print(f"\nALL {_passed} VISUAL-PLANNING CHECKS PASSED")
sys.exit(0)
