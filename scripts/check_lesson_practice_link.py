#!/usr/bin/env python3
"""Check that the practice questions can actually follow the mini-lesson.

Three defects this exists to prevent, all of them structural rather than
prompt-level (no wording in any prompt file could have fixed them):

  TEACHING REACHES THE GENERATOR
      QuestionGeneratorAgent.generate() and IntroWriterAgent.write() took
      identical arguments and neither received the other's output. A booklet
      taught equivalent fractions by multiplying up, then asked the child to
      divide down in all nine questions. The lesson now goes into the
      generator's user turn, and it must still be there after a retry.

  ABSENT TEACHING STILL GENERATES
      The intro writer is allowed to fail: the pipeline logs `intro_failed`,
      returns teaching=None and carries on. That path must keep working, and
      the prompt must not then contain a dangling, empty lesson block.

  LESSONS VARY IN SHAPE
      _reading_budget() demanded "key_points: exactly N", so every Year 5
      lesson in a booklet carried exactly three bullets. Six subtopics, six
      identically shaped lessons. It is now a range, and still bounded.

  DEDUPE SPANS THE BOOKLET
      The seen-set was rebuilt per subtopic while subtopics run concurrently,
      so nothing could see across them; a booklet shipped sixteen consecutive
      volume questions. It is now one thread-safe set for the whole booklet,
      and the claim on a question text must be atomic.

No API key is needed or used: every LLM call is served by a stub client that
records what it was asked.

Usage:  PYTHONPATH=. python scripts/check_lesson_practice_link.py
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Some checks deliberately drive agents into their retry/failure paths; the
# report below is the output, not the log noise.
logging.disable(logging.CRITICAL)

from booklet_gen.agents.intro_writer import (                    # noqa: E402
    IntroWriterAgent, _BUDGETS, _reading_budget)
from booklet_gen.agents.question_generator import (              # noqa: E402
    QuestionGeneratorAgent, teaching_block)
from booklet_gen.pipeline import BookletPipeline, _SeenQuestions  # noqa: E402
from booklet_gen.schemas import (                                # noqa: E402
    Outline, Subtopic, SubtopicTeaching, Topic, WorkedExample)

PASSED = 0
TOTAL = 0


def check(good: bool, label: str, detail: str = "") -> None:
    global PASSED, TOTAL
    TOTAL += 1
    PASSED += bool(good)
    print(f"{'ok  ' if good else '*** FAIL ***':<14}{label}")
    if not good and detail:
        print(f"{'':<14}{detail[:300]}")


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

# The lesson under test throughout: it teaches equivalent fractions by
# MULTIPLYING UP, which is the exact case the shipped booklet got wrong.
LESSON = SubtopicTeaching(
    intro_paragraphs=["Equivalent fractions name the same amount."],
    key_points=["Multiply the top and bottom by the same number.",
                "The value does not change."],
    mnemonic="Same Move, Top and Bottom",
    worked_example=WorkedExample(
        question="Write a fraction equivalent to 2/3 with a denominator of 12.",
        steps=["Step 1: 3 x 4 = 12, so the multiplier is 4.",
               "Step 2: 2 x 4 = 8.",
               "Step 3: The answer is 8/12."],
        answer="8/12",
    ),
    guided_examples=[
        WorkedExample(question="Write 3/5 with a denominator of 20.",
                      steps=["5 x 4 = 20", "3 x 4 = 12"], answer="12/20"),
    ],
)

SUBTOPIC = Subtopic(name="Equivalent fractions", difficulty_hint="medium")

_LESSON_JSON = json.dumps({
    "intro_paragraphs": LESSON.intro_paragraphs,
    "key_points": LESSON.key_points,
    "mnemonic": LESSON.mnemonic,
    "worked_example": {"question": LESSON.worked_example.question,
                       "steps": LESSON.worked_example.steps,
                       "answer": LESSON.worked_example.answer,
                       "diagram_spec": None},
    "guided_examples": [{"question": g.question, "steps": g.steps,
                         "answer": g.answer, "diagram_spec": None}
                        for g in LESSON.guided_examples],
})


def _questions_json(n: int, offset: int = 0) -> str:
    """n distinct additions sympy can verify, so no judge call is needed."""
    return json.dumps({"questions": [
        {"question": f"Calculate {i}/16 + 2/16.", "answer": f"{i + 2}/16",
         "working": f"{i} + 2 = {i + 2}", "difficulty": "medium"}
        for i in range(1 + offset, 1 + offset + n)
    ]})


class RecordingClient:
    """Serves every agent in the pipeline and records what it was asked.

    Dispatches on the system prompt, which is the only thing that identifies
    the caller. `calls` holds (agent, system, user) in order.
    """

    def __init__(self, *, intro_fails: bool = False, question_payloads=None):
        self.calls: list[tuple[str, str, str]] = []
        self._intro_fails = intro_fails
        self._payloads = list(question_payloads or [])
        self._n_questions = 0
        self._lock = threading.Lock()

    def _agent_of(self, system: str) -> str:
        # Dispatch on how the prompt OPENS. Matching a bare "mini-lesson"
        # anywhere in the text made every prompt that merely mentions the
        # mini-lesson look like the intro writer.
        if system.startswith("You generate practice"):
            return "questions"
        if "writing a mini-lesson" in system:
            return "intro"
        if "practice maths questions" in system or "questions" in system.lower():
            return "questions"
        return "other"

    def complete(self, system, user, tier="strong", temperature=0.0):
        agent = self._agent_of(system)
        with self._lock:
            self.calls.append((agent, system, user))
            n = self._n_questions
            self._n_questions += 1
        if agent == "intro":
            if self._intro_fails:
                return "I am afraid I cannot help with that."   # never parses
            return _LESSON_JSON
        if self._payloads:
            return self._payloads[min(n, len(self._payloads) - 1)]
        return _questions_json(3, offset=n * 10)

    # what the question generator was asked, in order
    def question_turns(self) -> list[str]:
        return [u for a, _, u in self.calls if a == "questions"]


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


def build_pipeline(client, **kw) -> BookletPipeline:
    defaults = dict(questions_per_subtopic=2, homework_per_subtopic=1,
                    recap_questions=0, challenge_questions=0, max_workers=4)
    defaults.update(kw)
    return BookletPipeline(client=client, retriever=NoRetriever(), **defaults)


# --------------------------------------------------------------------------
def main() -> int:
    print("== the lesson is rendered into the generator's user turn ==")
    block = teaching_block(LESSON)
    check(LESSON.worked_example.question in block,
          "the worked example question is in the block", block)
    check(all(s in block for s in LESSON.worked_example.steps),
          "the worked example's steps are in the block, so the METHOD is visible")
    check(LESSON.guided_examples[0].question in block,
          "the guided ('we do') example question is in the block")
    check(all(kp in block for kp in LESSON.key_points),
          "the key points are in the block")
    check(LESSON.mnemonic in block, "the method's name is in the block")
    check("restate" in block.lower(),
          "the block forbids restating an example (the worked example with a "
          "story wrapper is not practice)")
    check("direction" in block.lower(),
          "the block pins the DIRECTION of the skill (multiply up vs divide down)")

    print("\n== absent or empty teaching produces no block at all ==")
    check(teaching_block(None) == "", "teaching=None renders nothing")

    class _Empty:
        mnemonic = None
        key_points: list = []
        worked_example = None
        guided_examples: list = []

    check(teaching_block(_Empty()) == "",
          "a teaching object with nothing in it renders nothing, not a header "
          "with no content under it")

    class _Partial:
        """Only a worked example: the shape a stricter schema might yield."""
        worked_example = WorkedExample(question="Halve 3/4.", steps=[], answer="3/8")

    part = teaching_block(_Partial())
    check("Halve 3/4." in part and "Key points" not in part,
          "a partial teaching object degrades to a shorter block, it does not raise")

    print("\n== the agent actually sends it ==")
    client = RecordingClient()
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=3)
    agent.generate("Mathematics", "Year 5", "Fractions", SUBTOPIC, None, LESSON)
    sent = client.question_turns()[0]
    check(LESSON.worked_example.question in sent,
          "generate(teaching=...) puts the worked example in the user turn", sent[:400])
    check("Equivalent fractions" in sent and "Year 5" in sent,
          "the existing user turn content survives alongside it")

    client = RecordingClient()
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=3)
    out = agent.generate("Mathematics", "Year 5", "Fractions", SUBTOPIC, None)
    sent = client.question_turns()[0]
    check(len(out.questions) == 3 and "MINI-LESSON" not in sent,
          "generate() with no teaching still works and sends no lesson block")

    # RAG chunks and the lesson must coexist: both are appended to the same turn.
    client = RecordingClient()
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=3)
    agent.generate("Mathematics", "Year 5", "Fractions", SUBTOPIC,
                   ["A textbook excerpt about equivalent fractions."], LESSON)
    sent = client.question_turns()[0]
    check("A textbook excerpt about equivalent fractions." in sent
          and LESSON.worked_example.question in sent,
          "reference chunks and the lesson both survive in one user turn")

    # A retry must not silently drop the lesson: the corrected attempt is the
    # one most likely to ship, and it needs the teaching most.
    client = RecordingClient(question_payloads=["not json at all", _questions_json(3)])
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=3)
    agent.generate("Mathematics", "Year 5", "Fractions", SUBTOPIC, None, LESSON)
    turns = client.question_turns()
    check(len(turns) == 2 and all(LESSON.worked_example.question in t for t in turns),
          "the retry after a bad response still carries the lesson")

    print("\n== end to end through the pipeline ==")
    client = RecordingClient()
    pipe = build_pipeline(client)
    section, _ = pipe._process_subtopic("Mathematics", "Year 5", "Fractions", SUBTOPIC)
    turns = client.question_turns()
    check(bool(turns) and LESSON.worked_example.question in turns[0],
          "_process_subtopic hands the intro writer's worked example to the "
          "question generator", (turns[0] if turns else "<no call>")[:400])
    check(section.teaching is not None and len(section.questions) == 2,
          "the section still carries its lesson and its classwork")
    order = [a for a, _, _ in client.calls]
    check(order.index("intro") < order.index("questions"),
          "the lesson is written before the questions (it already was, so "
          "sequencing them costs no extra wall-clock time)")

    # The failure path the pipeline logs as `intro_failed`.
    client = RecordingClient(intro_fails=True)
    pipe = build_pipeline(client)
    section, _ = pipe._process_subtopic("Mathematics", "Year 5", "Fractions", SUBTOPIC)
    turns = client.question_turns()
    check(section.teaching is None and len(section.questions) == 2,
          "a failed intro writer still yields a section with its questions")
    check(bool(turns) and "MINI-LESSON" not in turns[0],
          "and the generator's turn carries no empty lesson block")

    print("\n== key points are a range, not a fixed count ==")
    for year in ("Year 1", "Year 3", "Year 5", "Year 8", "Year 10"):
        budget = _reading_budget(year)
        check("exactly" not in budget.lower() and "between" in budget,
              f"{year}: key_points is a range, not 'exactly N'", budget)
    check(all(kp_min < kp_max for _, _, _, kp_min, kp_max, _, _ in _BUDGETS),
          "every band offers a genuine choice (min < max)")
    check(all(kp_max <= 5 and kp_min >= 1 for _, _, _, kp_min, kp_max, _, _ in _BUDGETS),
          "and no band can balloon (1 <= min, max <= 5)")
    y5 = _reading_budget("Year 5")
    check("do not settle on one habitual number" in y5,
          "the budget tells the writer not to pick the same count every lesson")
    check("at most 4 short steps" in y5 and "at most 1 paragraph" in y5,
          "the other ceilings are untouched", y5)
    # The intro writer must still parse a lesson whose bullet count varies.
    for n in (1, 2, 3, 4):
        client = RecordingClient()
        client._payloads = []
        payload = json.loads(_LESSON_JSON)
        payload["key_points"] = [f"point {i}" for i in range(n)]

        class _Fixed:
            def complete(self, system, user, tier="strong", temperature=0.0):
                return json.dumps(payload)

        got = IntroWriterAgent(_Fixed()).write(
            "Mathematics", "Year 5", "Fractions", SUBTOPIC)
        check(len(got.key_points) == n,
              f"a lesson with {n} key point(s) parses (the schema never "
              "enforced a count, only the prompt did)")

    print("\n== the seen-set spans the booklet and is thread-safe ==")
    seen = _SeenQuestions()
    check(seen.add("a") and not seen.add("a"),
          "a text can be claimed once")

    winners = []
    barrier = threading.Barrier(24)
    shared = _SeenQuestions()

    def race():
        barrier.wait()
        winners.append(shared.add("calculate 5/16 + 2/16"))

    threads = [threading.Thread(target=race) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(sum(winners) == 1,
          f"24 threads claiming the same question: exactly one wins "
          f"(got {sum(winners)})")

    # Two subtopics that both produce the same question: the second must not
    # print it again. The stub can only ever emit that one question, so the
    # second subtopic legitimately ends up empty; what matters is that the
    # duplicate never reaches the booklet.
    one = json.dumps({"questions": [
        {"question": "Calculate 3/8 + 2/8.", "answer": "5/8",
         "working": "3 + 2 = 5", "difficulty": "medium"}]})
    client = RecordingClient(question_payloads=[one])
    pipe = build_pipeline(client, max_workers=1)
    seen = _SeenQuestions()
    first = pipe._generate_and_validate(
        "Mathematics", "Year 5", "Fractions", SUBTOPIC, None, None, seen)
    calls_before = len(client.question_turns())
    second = pipe._generate_and_validate(
        "Mathematics", "Year 5", "Decimals", SUBTOPIC, None, None, seen)
    check(len(first) == 1 and len(second) == 0,
          "a question already used by another subtopic is not printed twice")
    check(len(client.question_turns()) == calls_before + 1,
          "and duplicate handling does not buy an unvalidated replacement batch")

    # Same thing through the concurrent path, which is where the old per-subtopic
    # set was blind.
    client = RecordingClient(question_payloads=[one])
    pipe = build_pipeline(client, max_workers=4)
    outline = Outline(subject="Mathematics", year_level="Year 5", topics=[
        Topic(name="Fractions", subtopics=[
            Subtopic(name="Adding eighths"), Subtopic(name="Adding quarters"),
            Subtopic(name="Adding halves")]),
    ])
    sections, _, _ = pipe._generate_from_outline(outline)
    texts = [vq.question.question
             for s in sections for vq in (s.questions + s.homework_questions)]
    check(len(texts) == len(set(texts)) and len(texts) == 1,
          f"3 concurrent subtopics emitting one identical question keep it once "
          f"(kept {len(texts)})")

    # The Final Challenge is meant to combine the skills, not reprint them.
    challenge_payload = json.dumps({"questions": [
        {"question": "Calculate 3/8 + 2/8.", "answer": "5/8",
         "working": "3 + 2 = 5", "difficulty": "hard"},
        {"question": "Calculate 7/8 - 2/8.", "answer": "5/8",
         "working": "7 - 2 = 5", "difficulty": "hard"}]})

    class _Challenger:
        def complete(self, system, user, tier="strong", temperature=0.0):
            return challenge_payload

    pipe = build_pipeline(_Challenger(), challenge_questions=2)
    seen = _SeenQuestions()
    seen.add(BookletPipeline._norm_q("Calculate 3/8 + 2/8."))
    challenge = pipe._build_challenge(
        "Mathematics", "Year 5", [("Fractions", "Adding eighths")], None, seen)
    check([c.question.question for c in challenge] == ["Calculate 7/8 - 2/8."],
          "the challenge drops a question the practice already asked")

    print("\n== duplicate detection ==")
    norm = BookletPipeline._norm_q
    for a, b, same, note in [
        ("Calculate 3/8 + 2/8.", "calculate 3/8 + 2/8", True, "case and full stop"),
        ("Calculate  3/8  +  2/8.", "Calculate 3/8 + 2/8.", True, "whitespace"),
        ("What is 7 x 8?", "What is 7 x 8", True, "question mark"),
        ("Calculate 3/8 + 2/8.", "Calculate 3/8 + 1/8.", False,
         "different numbers stay different"),
        ("Find the total of 1,200 + 300.", "Find the total of 1200 + 300.", False,
         "interior punctuation is never normalised away"),
    ]:
        check((norm(a) == norm(b)) is same, f"{note}: {a!r} vs {b!r}")

    print(f"\n{PASSED}/{TOTAL} behaved as expected")
    return 0 if PASSED == TOTAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
