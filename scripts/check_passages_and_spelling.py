#!/usr/bin/env python3
"""Check comprehension passages, weekly spelling, and the term's skill ladder.

Three product defects, all of them structural. No wording in any prompt file
could have fixed them, because until the schema landed there was nowhere to put
the content:

  A PASSAGE IS A PASSAGE, NOT A SENTENCE IN A QUESTION
      English questions carried their own reading inside the `question` string,
      so a "passage" was as long as you can reasonably put in a question field
      and every question got its own scrap of text. Passages are now objects on
      the subtopic, questions point at them, and, crucially, the classwork /
      homework split is made to fall BETWEEN passages rather than through one.

  A SPELLING TEST IS ON WORDS THAT WERE SET
      Spelling was taught through worked examples, which is a category error:
      there is no method to derive for whether "black" takes a c. It is now a
      20 word list at the back of week N and a 12 word test at the front of
      week N+1, drawn from that list and from nowhere else. Week 1 has a list
      and no test. No two weeks share a word.

  A TERM IS A LADDER, NOT ONE TOPIC NUMBERED TEN TIMES
      "fractions (part 1)" through "(part 6)" passes any duplicate check and
      still generates six near-identical booklets. Each week now names a
      different skill, repeats earn a retry with the offending weeks named, and
      anything still repeating is replaced from the subject's skill ladder.

And two things that must NOT change: a maths booklet is untouched by any of
this, and a single booklet generated on its own carries no spelling at all.

No API key is needed or used: every LLM call is served by a stub client.

Usage:  PYTHONPATH=. python scripts/check_passages_and_spelling.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Some checks deliberately drive agents into their retry/fallback paths.
logging.disable(logging.CRITICAL)

from booklet_gen.agents.question_generator import (              # noqa: E402
    PassageQuestionSet, QuestionGeneratorAgent, bind_passages, passage_block,
    passage_quotas)
from booklet_gen.agents.spelling import (                        # noqa: E402
    LIST_SIZE, TEST_SIZE, SpellingAgent, _bank_for)
from booklet_gen.agents.term_planner import (                    # noqa: E402
    TermPlannerAgent, _ladder, _norm_focus)
from booklet_gen.pipeline import (                               # noqa: E402
    BookletPipeline, CLASSWORK_CAP_MINUTES)
from booklet_gen.schemas import (                                # noqa: E402
    BookletData, Passage, Question, SpellingList, Subtopic, SubtopicOutput,
    SubtopicTeaching, TermPlan, TermWeek, ValidatedQuestion, WorkedExample)
from booklet_gen.timing import (                                 # noqa: E402
    booklet_timing, classwork_section_minutes, homework_minutes_in_order,
    homework_session_plan, passage_minutes, question_minutes, round_display)

PASSED = 0
TOTAL = 0


def check(good: bool, label: str, detail: str = "") -> None:
    global PASSED, TOTAL
    TOTAL += 1
    PASSED += bool(good)
    print(f"{'ok  ' if good else '*** FAIL ***':<14}{label}")
    if not good and detail:
        print(f"{'':<14}{detail[:400]}")


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------

PARA_1 = ("The tide came in faster than Mira expected. She had walked out to "
          "the sandbar at dawn, when the water was still far away, and now the "
          "channel behind her was already knee deep.")
PARA_2 = ("She thought about waiting. The sandbar would be underwater within "
          "the hour, and nobody on the beach could see her from that distance. "
          "Waiting was not really a choice, only a way of not deciding.")
PARA_3 = ("Mira put her shoes in her backpack, pulled the straps tight, and "
          "stepped into the channel before she could talk herself out of it.")


def english_payload() -> str:
    """Six questions across two passages, interleaved.

    Interleaved on purpose: the model is not reliable about ordering, and the
    pipeline must group them itself before it can split on a boundary. A
    payload that arrived already grouped would let a broken splitter pass.
    """
    on_p1 = [
        ("Why was the channel deeper than when Mira arrived?",
         "Because the tide had come in while she was on the sandbar."),
        ("What does the phrase \"only a way of not deciding\" tell you about "
         "waiting?", "That waiting was itself a choice, and a poor one."),
        ("Find a word in the passage that means a narrow stretch of water.",
         "Channel."),
    ]
    on_p2 = [
        ("How does the writer show that Mira had made up her mind?",
         "She acted before she could talk herself out of it."),
        ("What is the mood of the final paragraph?",
         "Decisive, after the hesitation of the paragraph before it."),
        ("Why does Mira put her shoes in her backpack?",
         "So they stay dry while she wades across."),
    ]
    rows = []
    for (q1, a1), (q2, a2) in zip(on_p1, on_p2):
        rows.append((q1, a1, "p1"))
        rows.append((q2, a2, "p2"))
    return json.dumps({
        "passages": [
            {"id": "p1", "title": "The Sandbar",
             "paragraphs": [PARA_1, PARA_2, PARA_3]},
            {"id": "p2", "title": "Crossing",
             "paragraphs": [PARA_3, PARA_2]},
        ],
        "questions": [
            {"question": q, "answer": a, "working": "Read the passage closely.",
             "difficulty": "medium", "passage_id": pid}
            for q, a, pid in rows
        ],
    })


_ENGLISH_LESSON = json.dumps({
    "intro_paragraphs": ["A writer's word choice shapes how a scene feels."],
    "key_points": ["Look for the word that carries the feeling.",
                   "Check what the sentence around it is doing."],
    "mnemonic": "Word, Then World",
    "worked_example": {"question": "What does 'crept' suggest about the fog?",
                       "steps": ["'Crept' is slow and secretive.",
                                 "So the fog arrives quietly."],
                       "answer": "That it arrived slowly and quietly.",
                       "diagram_spec": None},
    "guided_examples": [],
})

_OUTLINE = json.dumps({
    "subject": "English", "year_level": "Year 6",
    "topics": [{"name": "Reading comprehension",
                "subtopics": [{"name": "Inference", "difficulty_hint": "medium",
                               "question_types": []}]}],
})

MATHS_PAYLOAD = json.dumps({"questions": [
    {"question": f"Calculate {i}/16 + 2/16.", "answer": f"{i + 2}/16",
     "working": f"{i} + 2 = {i + 2}"} for i in range(1, 7)]})


def _judge_reply(user: str) -> str:
    """Pass every question, echoing its own proposed answer back.

    Echoing matters: the judge cross-checks its solve against the proposed
    answer, so a stub that returns a blank or a constant would have questions
    failing for reasons that have nothing to do with what is under test.
    """
    answers = re.findall(r"^Proposed answer: (.*)$", user, re.MULTILINE)
    return json.dumps({"results": [
        {"index": i, "solved_answer": a, "verified": True, "reason": "stub"}
        for i, a in enumerate(answers)
    ]})


class StubClient:
    """Serves every agent in the pipeline, dispatching on the system prompt."""

    def __init__(self, *, questions=None, weeks=3, plan=None, spelling=None,
                 spelling_fails=False):
        self.calls: list[tuple[str, str, str]] = []
        self._questions = questions or english_payload()
        self._weeks = weeks
        self._plan = plan
        self._spelling = spelling
        self._spelling_fails = spelling_fails
        self._spell_call = 0
        self._lock = threading.Lock()

    @staticmethod
    def agent_of(system: str) -> str:
        # Dispatch on how each prompt OPENS, not on a word that happens to
        # appear in it. This used to test `"mini-lesson" in system`, which is
        # true of the intro writer and also of any other prompt that mentions
        # the mini-lesson in passing: telling the question generator to make
        # question 1 match the mini-lesson's worked example silently turned
        # every question-generation call into an intro call, and the failure
        # surfaced as a schema error three layers away.
        if system.startswith("You convert a short natural-language description"):
            return "outline"
        if system.startswith("You generate practice"):
            return "questions"
        if "writing a mini-lesson" in system:
            return "intro"
        if system.startswith("You set the weekly spelling list"):
            return "spelling"
        if system.startswith("You plan a school term"):
            return "plan"
        if system.startswith("You are an independent grader"):
            return "judge"
        if "FINAL CHALLENGE" in system:
            return "challenge"
        return "questions"

    def complete(self, system, user, tier="strong", temperature=0.0):
        agent = self.agent_of(system)
        with self._lock:
            self.calls.append((agent, system, user))
            if agent == "spelling":
                self._spell_call += 1
                n = self._spell_call
        if agent == "outline":
            return _OUTLINE
        if agent == "intro":
            return _ENGLISH_LESSON
        if agent == "judge":
            return _judge_reply(user)
        if agent == "plan":
            return self._plan or json.dumps({"weeks": [
                {"week": i, "focus": _ladder("english")[i - 1],
                 "difficulty": "medium", "revision": False}
                for i in range(1, self._weeks + 1)
            ]})
        if agent == "spelling":
            if self._spelling_fails:
                raise RuntimeError("spelling service unavailable")
            if self._spelling is not None:
                return self._spelling(n) if callable(self._spelling) else self._spelling
            return json.dumps({"words": [f"w{n}x{i:02d}" for i in range(LIST_SIZE)]})
        if agent == "challenge":
            return json.dumps({"questions": []})
        # NAPLAN runs two engines into one booklet, and the booklet-wide dedupe
        # is real: serve each engine its own questions or the second one is
        # thrown away as duplicates of the first.
        if "Subject: Mathematics" in user:
            return MATHS_PAYLOAD
        return self._questions

    def turns(self, agent: str) -> list[str]:
        return [u for a, _, u in self.calls if a == agent]


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


def build_pipeline(client, **kw) -> BookletPipeline:
    defaults = dict(questions_per_subtopic=3, homework_per_subtopic=3,
                    recap_questions=0, challenge_questions=0, max_workers=1)
    defaults.update(kw)
    return BookletPipeline(client=client, retriever=NoRetriever(), **defaults)


def vq(text, pid=None):
    return ValidatedQuestion(
        question=Question(question=text, answer="a", working="w", passage_id=pid),
        verified=True)


# --------------------------------------------------------------------------
def passages() -> None:
    print("== the generator asks for passages, and only where they belong ==")
    block = passage_block("English", 6, 3)
    check("passages" in block and "passage_id" in block,
          "the English request names the passages array and passage_id")
    check("exactly 5 questions off each" in block,
          "it asks for five questions per passage, which is the whole point")
    check("VARY THE LENGTH" in block,
          "the readings differ in length rather than all being one size")
    check("first 3 questions print in Class Work" in block,
          "it tells the model where the classwork/homework cut falls", block)
    check(passage_block("Mathematics", 6, 3) == "",
          "maths gets no passage instructions at all")
    check(passage_block("Reasoning", 6, 3) == "",
          "reasoning gets none either")
    check("passage below" in passage_block("English", 6, 3),
          "it forbids 'the passage below', which is what shipped when the "
          "formatter could not control the order")
    check("FIVE paragraphs" in block,
          "it asks for a whole five-paragraph text, not an extract")
    for want in ("opening", "three paragraphs that develop", "concludes"):
        check(want in block, f"the five-paragraph shape names its {want!r} part")
    # Structure and variety are not in tension, and the booklet wants both: a
    # student who only ever reads narrative is unprepared for the comprehension
    # they actually sit. Each type is spelled out in the same five-part shape.
    for kind in ("narrative", "information report", "diary", "news report",
                 "persuasive"):
        check(kind in block, f"{kind!r} is offered as a text type")
        check(f"* {kind}" in block or f"* {kind.split()[0]}" in block,
              f"and {kind!r} is given its own five-paragraph shape")
    check("DIFFERENT TEXT TYPE" in block,
          "the set must span types rather than reusing one")

    print("\n== four passages per booklet, two per half, however the "
          "outline is shaped ==")
    # The count is a booklet-level target, so it cannot be decided inside a
    # subtopic: four English subtopics each deciding "2 for me" is eight.
    for n in range(1, 7):
        q = passage_quotas(n)
        check(len(q) == n and sum(q) == 4,
              f"{n} subtopic{'' if n == 1 else 's'} still share four passages",
              str(q))
    check(passage_quotas(0) == [], "an empty outline asks for nothing")
    check(passage_quotas(4) == [2, 2, 0, 0],
          "the budget concentrates rather than spreading: a subtopic holding "
          "two puts one either side of the classwork cut by itself",
          str(passage_quotas(4)))
    check(sum(passage_quotas(3, wanted=5)) == 5,
          "an odd budget is still spent in full", str(passage_quotas(3, wanted=5)))
    check(passage_block("English", 6, 3, 0) == "",
          "a subtopic with no quota is told nothing about passages, rather "
          "than being told to write some and then ignored")
    check("exactly 1 passage," in passage_block("English", 6, 3, 1),
          "the count is stated, and it agrees in number")
    check("exactly 4 passages," in passage_block("English", 6, 3, 4),
          "a single-subtopic English booklet carries the whole budget")

    print("\n== ids are made unique per call, and dangling ones are cleared ==")
    first = bind_passages(PassageQuestionSet.model_validate(json.loads(english_payload())))
    second = bind_passages(PassageQuestionSet.model_validate(json.loads(english_payload())))
    ids_a = {p.id for p in first.passages}
    ids_b = {p.id for p in second.passages}
    check(not (ids_a & ids_b),
          "two calls that both label their passage 'p1' do not collide "
          f"({sorted(ids_a)} vs {sorted(ids_b)})")
    check(all(q.passage_id in ids_a for q in first.questions),
          "every question's passage_id was remapped with its passage")
    check(len(first.passages) == 2 and all(len(p.paragraphs) >= 2 for p in first.passages),
          "both passages survive with their paragraphs intact")

    dangling = PassageQuestionSet.model_validate({
        "passages": [{"id": "p1", "paragraphs": ["Some reading."]}],
        "questions": [
            {"question": "About p1?", "answer": "a", "working": "w", "passage_id": "p1"},
            {"question": "About p9?", "answer": "a", "working": "w", "passage_id": "p9"},
        ],
    })
    bound = bind_passages(dangling)
    check(bound.questions[1].passage_id is None,
          "a question citing a passage that was never written has its "
          "reference cleared, not kept")
    check(bound.questions[0].passage_id is not None and len(bound.passages) == 1,
          "and the sound reference is untouched")

    empty_passage = bind_passages(PassageQuestionSet.model_validate({
        "passages": [{"id": "p1", "paragraphs": []},
                     {"id": "p2", "paragraphs": ["Real reading."]}],
        "questions": [
            {"question": "About p1?", "answer": "a", "working": "w", "passage_id": "p1"},
            {"question": "About p2?", "answer": "a", "working": "w", "passage_id": "p2"},
        ],
    }))
    check(len(empty_passage.passages) == 1
          and empty_passage.questions[0].passage_id is None,
          "a passage with no paragraphs is dropped and its citation cleared")

    uncited = bind_passages(PassageQuestionSet.model_validate({
        "passages": [{"id": "p1", "paragraphs": ["Nobody asks about this."]}],
        "questions": [{"question": "Unrelated?", "answer": "a", "working": "w"}],
    }))
    check(uncited.passages == [],
          "a passage no question cites is dropped, so the booklet never "
          "prints a page of reading with nothing under it")

    print("\n== the agent sends and parses it end to end ==")
    client = StubClient()
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=6)
    out = agent.generate("English", "Year 6", "Reading", Subtopic(name="Inference"),
                         None, None, classwork_count=3)
    check("passage_id" in client.turns("questions")[0],
          "the English user turn carries the passage contract")
    check(len(out.passages) == 2 and len(out.questions) == 6,
          "the reply parses into 2 passages and 6 questions")

    client = StubClient(questions=MATHS_PAYLOAD)
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=6)
    out = agent.generate("Mathematics", "Year 5", "Fractions",
                         Subtopic(name="Adding fractions"), None, None,
                         classwork_count=3)
    check("passage_id" not in client.turns("questions")[0] and out.passages == [],
          "a maths call is byte-for-byte the request it always was")

    client = StubClient()
    agent = QuestionGeneratorAgent(client, max_retries=3, questions_per_subtopic=6)
    out = agent.generate("English", "Year 6", "Warm-up Recap",
                         Subtopic(name="Revision"), None, None,
                         allow_passages=False)
    check(out.passages == [] and not any(q.passage_id for q in out.questions),
          "allow_passages=False strips passages AND their citations, so the "
          "recap cannot ask about reading the booklet never prints")

    print("\n== the split falls between passages, never through one ==")
    seq = [vq("q1", "A"), vq("q2", "A"), vq("q3", "A"),
           vq("q4", "B"), vq("q5", "B"), vq("q6", "B")]
    for target, want in [(3, 3), (2, 3), (4, 3), (1, 3), (5, 6), (6, 6)]:
        got = BookletPipeline._passage_safe_split(seq, target)
        check(got == want,
              f"target {target} on two 3-question passages cuts at {want} (got {got})")

    loose = [vq("q1", "A"), vq("q2", "A"), vq("q3"), vq("q4"), vq("q5")]
    check(BookletPipeline._passage_safe_split(loose, 3) == 3,
          "questions that need no reading let the cut land exactly on target")
    check(BookletPipeline._passage_safe_split(loose, 1) == 2,
          "a cut inside a passage moves out to its edge rather than through it")

    one_group = [vq(f"q{i}", "A") for i in range(6)]
    check(BookletPipeline._passage_safe_split(one_group, 3) == 6,
          "one passage covering the whole subtopic is not cut at all")
    check(BookletPipeline._passage_safe_split([vq("q1", "A")], 3) == 1,
          "a single question is never split to nothing")
    check(BookletPipeline._passage_safe_split(
        [vq("q1"), vq("q2"), vq("q3")], 0) >= 1,
        "the classwork half is never emptied: a lesson needs something under it")

    maths = [vq(f"q{i}") for i in range(7)]
    check(BookletPipeline._passage_safe_split(maths, 3) == 3,
          "with no passages anywhere the cut is exactly where it always was")
    check(BookletPipeline._group_by_passage(maths) == maths,
          "and the ordering is left completely alone")

    print("\n== a subtopic keeps its reading with its questions ==")
    client = StubClient()
    pipe = build_pipeline(client)
    section, _ = pipe._process_subtopic(
        "English", "Year 6", "Reading comprehension", Subtopic(name="Inference"))
    ids = {p.id for p in section.passages}
    cw = [q.question.passage_id for q in section.questions]
    hw = [q.question.passage_id for q in section.homework_questions]
    check(len(section.passages) == 2, f"the section carries both passages (got {len(section.passages)})")
    check(len(set(cw)) == 1 and len(set(hw)) == 1 and set(cw) != set(hw),
          f"Class Work is one whole passage and Homework is the other "
          f"(classwork {cw}, homework {hw})")
    check(len(section.questions) == 3 and len(section.homework_questions) == 3,
          "with three questions hanging off each, which is the shape the "
          "product owner asked for")
    check(all(pid in ids for pid in cw + hw if pid),
          "every passage_id in either half resolves against section.passages")
    check(set(cw) | set(hw) == ids,
          "and no passage is carried that nothing asks about")

    print("\n== the hour cap does not undo it ==")
    section = SubtopicOutput(
        topic="Reading", subtopic="Inference",
        questions=[vq("c1", "A"), vq("c2", "B"), vq("c3", "B"), vq("c4", "B")],
        homework_questions=[vq("h1", "C")],
        passages=[Passage(id="B", paragraphs=["Reading."])])
    BookletPipeline._move_tail_to_homework(section)
    check([q.question.question for q in section.questions] == ["c1"],
          "trimming the hour moves a passage's questions as a group, not one "
          "at a time", str([q.question.question for q in section.questions]))
    check([q.question.question for q in section.homework_questions]
          == ["c2", "c3", "c4", "h1"],
          "and they stay contiguous and in order in Homework")

    # A section that is nothing but one reading used to give ground a question
    # at a time, which whittled a five question comprehension down to two in
    # Class Work and three in Homework under the same passage printed twice.
    # Five questions follow a reading. It refuses to move instead, and the
    # caller moves the whole subtopic.
    only_group = SubtopicOutput(
        topic="Reading", subtopic="Inference",
        questions=[vq(f"c{i}", "A") for i in range(1, 6)],
        passages=[Passage(id="A", paragraphs=["Reading."])])
    moved = BookletPipeline._move_tail_to_homework(only_group)
    check(moved is False and len(only_group.questions) == 5
          and not only_group.homework_questions,
          "a section that is nothing but one reading is never split, it "
          "refuses to move and says so",
          f"moved={moved}, {len(only_group.questions)} left in class work")
    check(only_group.passages[0].id == "A",
          "and the passage stays on the SUBTOPIC, so a group that does move "
          "whole is still resolvable from both halves: this is why passages "
          "are not stored per half")

    lone = SubtopicOutput(topic="t", subtopic="s", questions=[vq("only", "A")])
    BookletPipeline._move_tail_to_homework(lone)
    check(len(lone.questions) == 1, "the last classwork question never moves")

    # End to end, through the real cap fitter, on an English booklet far over
    # the hour. This is the shape the bug actually appeared in: five readings
    # of five questions each, where trimming a question at a time left the
    # student two questions in class and three in homework under the same
    # passage reprinted.
    def reading_section(i: int) -> SubtopicOutput:
        return SubtopicOutput(
            topic="Reading", subtopic=f"Reading {i}",
            teaching=SubtopicTeaching(
                intro_paragraphs=["A mini-lesson long enough to cost real "
                                  "minutes when the cap is measured. " * 6],
                key_points=["Read the whole thing first."],
                worked_example=WorkedExample(
                    question="A worked comprehension question.",
                    steps=["Find it.", "Say it."], answer="There"),
                guided_examples=[WorkedExample(
                    question="Try this one together.",
                    steps=["Find it.", "Say it."], answer="There")]),
            questions=[vq(f"r{i}q{j}", f"P{i}") for j in range(5)],
            passages=[Passage(id=f"P{i}",
                              paragraphs=["A story worth reading."] * 5)])

    booklet = [reading_section(i) for i in range(5)]
    BookletPipeline._fit_classwork_to_cap(booklet)
    check(sum(1 for s in booklet if s.questions) < 5,
          "an English booklet over the hour really is trimmed by the cap",
          f"{sum(1 for s in booklet if s.questions)} of 5 readings taught")
    split = [s.subtopic for s in booklet
             if {q.question.passage_id for q in s.questions}
             & {q.question.passage_id for q in s.homework_questions}]
    check(not split,
          "and no reading ends up with some questions in Class Work and the "
          "rest in Homework", str(split))
    for s in booklet:
        half = s.questions or s.homework_questions
        check(len(half) == 5,
              f"{s.subtopic} keeps all five questions in whichever half it "
              f"landed in", f"{len(half)} questions")

    print("\n== the hour is shared out across topics, not spent on one ==")
    # The English booklet the outline parser produces always carries Reading,
    # Writing, Language Conventions and Vocabulary, in that order. The subtopic
    # pushed out of the hour used to be whichever came last, so it was always
    # grammar or vocabulary: measured on a Year 5 sample, two readings held 42
    # of the 60 minutes and ten of the eleven questions, Similes was left with
    # one question and Commas with none.
    def lesson(name: str) -> SubtopicTeaching:
        return SubtopicTeaching(
            intro_paragraphs=[f"{name} takes a little explaining. " * 22],
            key_points=["Read it through first."],
            worked_example=WorkedExample(question=f"A worked {name} question.",
                                         steps=["Start.", "Finish."],
                                         answer="Done"),
            guided_examples=[WorkedExample(question="Try it together.",
                                           steps=["Start.", "Finish."],
                                           answer="Done")] * 2)

    booklet = [
        SubtopicOutput(topic="Reading and Comprehension", subtopic=f"Reading {i}",
                       teaching=lesson(f"Reading {i}"),
                       questions=[vq(f"r{i}q{j}", f"P{i}") for j in range(5)],
                       passages=[Passage(id=f"P{i}",
                                         paragraphs=["A story worth reading. " * 12] * 5)])
        for i in range(2)
    ] + [
        SubtopicOutput(topic="Vocabulary and Word Study", subtopic="Similes",
                       teaching=lesson("Similes"),
                       questions=[vq(f"v{j}", None) for j in range(4)]),
        SubtopicOutput(topic="Language Conventions", subtopic="Commas",
                       teaching=lesson("Commas"),
                       questions=[vq(f"c{j}", None) for j in range(4)]),
    ]
    over = sum(classwork_section_minutes(s) for s in booklet)
    check(over > CLASSWORK_CAP_MINUTES,
          "the fixture English booklet really is over the hour",
          f"{over:.0f} min against a {CLASSWORK_CAP_MINUTES} min cap")

    BookletPipeline._fit_classwork_to_cap(booklet)
    taught = [s for s in booklet if s.questions]
    check(all(len(s.questions) > 1 for s in taught),
          "no subtopic is left in the session holding a single token question",
          str([(s.subtopic, len(s.questions)) for s in taught]))
    topics = {s.topic for s in taught}
    check("Language Conventions" in topics and "Vocabulary and Word Study" in topics,
          "grammar and vocabulary are still taught, not always the ones dropped",
          str(sorted(topics)))
    check(sum(1 for s in booklet if not s.questions) == 1
          and not [s for s in booklet if not s.questions][0].questions,
          "one subtopic left the session, and it came from the doubled topic",
          str([s.subtopic for s in booklet if not s.questions]))
    check([s for s in booklet if not s.questions][0].topic
          == "Reading and Comprehension",
          "which is the reading, because Reading still has another subtopic in")
    check(len([s for s in booklet if not s.questions][0].homework_questions) == 5,
          "and it took all five of its questions with it")

    print("\n== homework is charged for the reading it asks about ==")
    # Class Work has always charged for a passage; Homework charged nothing, so
    # the same text was worth minutes in the session and free a week later. A
    # sitting holding two whole texts was billed as though the child already
    # knew them, and the number on the band is a promise to the parent.
    hw_only = BookletData(
        subject="English", year_level="Year 5", student_name="Sam",
        sections=[SubtopicOutput(
            topic="Reading", subtopic="Comprehension", questions=[],
            homework_questions=[vq(f"h{j}", "P") for j in range(6)],
            passages=[Passage(id="P", paragraphs=["Two hundred words of story. "
                                                  * 20] * 5)])])
    read = passage_minutes(hw_only.sections[0].passages[0])
    check(read > 3.0, "the fixture reading really does take a while",
          f"{read:.1f} min")

    per_q = homework_minutes_in_order(hw_only)
    check(len(per_q) == 6, "one figure per homework question", str(len(per_q)))
    check(per_q[0] - per_q[1] > read * 0.95,
          "the first question under a reading carries the reading's time",
          f"first {per_q[0]:.1f} min against {per_q[1]:.1f} for the next")
    check(all(abs(a - b) < 0.01 for a, b in zip(per_q[1:], per_q[2:])),
          "and the rest are charged alike, because by then it has been read")

    totals = booklet_timing(hw_only)
    check(totals["homework_raw"] > sum(
        question_minutes(q.question, "homework")
        for q in hw_only.sections[0].homework_questions) + read * 0.95,
        "the printed Homework total counts the reading too",
        f"about {totals['homework_minutes']} min")

    plan = homework_session_plan(hw_only)
    check(sum(p["minutes"] for p in plan) >= round_display(read),
          "and a sitting's own estimate is not left short of the reading in it",
          str([(p["count"], p["minutes"]) for p in plan]))

    print("\n== a maths booklet is untouched ==")
    client = StubClient(questions=MATHS_PAYLOAD)
    pipe = build_pipeline(client)
    section, _ = pipe._process_subtopic(
        "Mathematics", "Year 5", "Fractions", Subtopic(name="Adding fractions"))
    check(section.passages == [] and len(section.questions) == 3
          and len(section.homework_questions) == 3,
          "no passages, and the 3/3 split is exactly what it was before")


# --------------------------------------------------------------------------
def spelling() -> None:
    print("\n== a test can only ask for words that were set ==")
    agent = SpellingAgent(StubClient(), max_retries=1)
    check(agent.make_test(None, None) is None,
          "week 1 has no previous list, so it gets no test")
    check(agent.make_test(SpellingList(words=[]), 1) is None,
          "and an empty list produces no test either")

    week1 = SpellingList(words=[f"word{i:02d}" for i in range(LIST_SIZE)])
    test = agent.make_test(week1, 1)
    check(len(test.words) == TEST_SIZE, f"the test is {TEST_SIZE} words (got {len(test.words)})")
    check(set(test.words) <= set(week1.words),
          "every tested word came from the list that was set")
    check(test.from_week == 1, "and the test records which week the words came from")
    check(test.words == [w for w in week1.words if w in set(test.words)],
          "the words are in list order, so a parent can mark against the sheet")
    check(agent.make_test(week1, 1).words == test.words,
          "the sample is deterministic: regenerating a week gives the same test")
    short = agent.make_test(SpellingList(words=["a", "b", "c"]), 4)
    check(len(short.words) == 3,
          "a short list yields a short test rather than inventing words")

    print("\n== no two weeks share a list ==")
    client = StubClient()
    agent = SpellingAgent(client, max_retries=1)
    used: list[str] = []
    lists = []
    for wk in range(1, 5):
        lst = agent.generate_list("Year 6", wk, used, "inference")
        lists.append(lst.words)
        used.extend(lst.words)
    check(all(len(w) == LIST_SIZE for w in lists),
          f"each week sets {LIST_SIZE} words")
    flat = [w for w in used]
    check(len(flat) == len(set(flat)), "and no word is set twice across the term")
    check("Words already set earlier this term" in client.turns("spelling")[-1],
          "the request tells the model what has already been set")
    check("inference" in client.turns("spelling")[0],
          "and passes the week's English focus so the list can suit it")

    # The instruction is not the guarantee. A model that ignores it entirely
    # must not be able to set the same list twice.
    same = json.dumps({"words": [f"repeat{i:02d}" for i in range(LIST_SIZE)]})
    client = StubClient(spelling=same)
    agent = SpellingAgent(client, max_retries=1)
    used = []
    weeks = []
    for wk in range(1, 4):
        lst = agent.generate_list("Year 6", wk, used, None)
        weeks.append(lst.words)
        used.extend(lst.words)
    check(len(set(used)) == len(used),
          "a model that returns the same 20 words every week still produces "
          "distinct lists: the repeat is filtered in code, not trusted away")
    check(len(weeks[1]) == LIST_SIZE and len(weeks[2]) == LIST_SIZE,
          "and the short lists are topped up from the fallback bank")
    bank = set(_bank_for("Year 6"))
    check(set(weeks[1]) <= bank,
          "the top-up words come from the year-appropriate band")

    # Total failure of the model call.
    client = StubClient(spelling_fails=True)
    agent = SpellingAgent(client, max_retries=2)
    used = []
    for wk in range(1, 4):
        lst = agent.generate_list("Year 6", wk, used, None)
        check(len(lst.words) == LIST_SIZE,
              f"week {wk} still gets a full list when every model call fails")
        used.extend(lst.words)
    check(len(set(used)) == len(used),
          "and the fallback weeks still differ from each other")

    y2 = SpellingAgent(StubClient(spelling_fails=True), 1).generate_list("Year 2", 1, [], None)
    y10 = SpellingAgent(StubClient(spelling_fails=True), 1).generate_list("Year 10", 1, [], None)
    check(not set(y2.words) & set(y10.words),
          "a Year 2 list and a Year 10 list share no words")
    check(max(len(w) for w in y2.words) < min(len(w) for w in y10.words) + 4,
          f"and the Year 2 words are the shorter ones "
          f"(y2 longest {max(len(w) for w in y2.words)}, "
          f"y10 shortest {min(len(w) for w in y10.words)})")

    print("\n== the list reaches next week's test, through the pipeline ==")
    client = StubClient(weeks=4)
    pipe = build_pipeline(client)
    booklets = pipe.run_term_plan("accelerate", "Year 6", "Sam",
                                  subject="English", weeks=4)
    check(len(booklets) == 4, f"four weekly booklets came back (got {len(booklets)})")
    check(booklets[0].spelling_test is None and booklets[0].spelling_list is not None,
          "week 1 sets a list and sits no test")
    ok = True
    detail = ""
    for i in range(1, 4):
        prev, cur = booklets[i - 1], booklets[i]
        if cur.spelling_test is None:
            ok, detail = False, f"week {i + 1} has no test"
            break
        if not set(cur.spelling_test.words) <= set(prev.spelling_list.words):
            ok, detail = False, f"week {i + 1} tests words week {i} never set"
            break
        if cur.spelling_test.from_week != prev.week_number:
            ok, detail = False, (f"week {i + 1} test says from_week="
                                 f"{cur.spelling_test.from_week}, expected "
                                 f"{prev.week_number}")
            break
    check(ok, "every later week's test is drawn from the previous week's list, "
              "and names the week it came from", detail)
    check(all(len(b.spelling_list.words) == LIST_SIZE for b in booklets),
          f"every week sets {LIST_SIZE} words")
    check(all(len(b.spelling_test.words) == TEST_SIZE for b in booklets[1:]),
          f"every test after week 1 is {TEST_SIZE} words")
    all_words = [w for b in booklets for w in b.spelling_list.words]
    check(len(all_words) == len(set(all_words)),
          "and across the whole term no word is set twice")

    print("\n== spelling stays out of where it does not belong ==")
    client = StubClient(weeks=2, questions=MATHS_PAYLOAD)
    pipe = build_pipeline(client)
    maths_weeks = pipe.run_term_plan("accelerate", "Year 6", "Sam",
                                     subject="Mathematics", weeks=2)
    check(all(b.spelling_list is None and b.spelling_test is None for b in maths_weeks),
          "a maths term plan carries no spelling at all")
    check(client.turns("spelling") == [],
          "and never spends an API call generating a list nobody prints")

    client = StubClient()
    pipe = build_pipeline(client)
    single = pipe.run_program("accelerate", "Year 6", "Sam", subject="English")
    check(single.spelling_list is None and single.spelling_test is None,
          "a single English booklet, generated on its own, has no spelling: "
          "there is no next week to test and no previous week to test from")
    check(client.turns("spelling") == [],
          "and makes no spelling call")
    check(len(single.sections) == 1 and single.sections[0].passages,
          "while still producing its passages, so the two features are "
          "independent")

    # NAPLAN Practice runs maths and English into one booklet, so it gets
    # spelling (its literacy half is English) and its English sections get
    # passages while its maths sections do not.
    client = StubClient(weeks=2)
    pipe = build_pipeline(client)
    naplan = pipe.run_term_plan("naplan", "Year 5", "Sam", weeks=2)
    by_subject = {s.subject: s for s in naplan[0].sections}
    check(all(b.spelling_list is not None for b in naplan)
          and naplan[1].spelling_test is not None,
          "a NAPLAN term plan gets spelling: its literacy half is English")
    check(set(naplan[1].spelling_test.words) <= set(naplan[0].spelling_list.words),
          "and week 2 is tested on week 1's list there too")
    check(bool(by_subject.get("English") and by_subject["English"].passages)
          and by_subject["Mathematics"].passages == [],
          "the English half of a NAPLAN booklet carries passages and the maths "
          "half carries none",
          str({k: len(v.passages) for k, v in by_subject.items()}))


# --------------------------------------------------------------------------
def term_ladder() -> None:
    print("\n== each week hones a different named skill ==")
    client = StubClient(weeks=6)
    planner = TermPlannerAgent(client, max_retries=3)
    plan = planner.plan("Academic Accelerate", "English", "Year 6", 6)
    sent = client.turns("plan")[0]
    check("DIFFERENT NAMED SKILL" in sent,
          "the request asks for a different named skill per week")
    check("alliteration and repetition" in sent and "kinds of rhyme" in sent,
          "and shows the ladder the owner described", sent[-600:])
    check("(part 1)" in sent and "Never number the same topic" in sent,
          "and names the failure mode it is ruling out: one topic numbered N "
          "times", sent[-800:])
    check(len({_norm_focus(w.focus) for w in plan.weeks}) == 6,
          "six weeks, six distinct foci")

    print("\n== a repeated focus earns a retry, then a repair ==")
    repeated = json.dumps({"weeks": [
        {"week": 1, "focus": "fractions (part 1)", "difficulty": "easy", "revision": False},
        {"week": 2, "focus": "fractions (part 1)", "difficulty": "easy", "revision": False},
        {"week": 3, "focus": "adding and subtracting fractions", "difficulty": "medium",
         "revision": False},
        {"week": 4, "focus": "", "difficulty": "hard", "revision": False},
    ]})
    client = StubClient(plan=repeated)
    planner = TermPlannerAgent(client, max_retries=3)
    plan = planner.plan("Academic Accelerate", "Mathematics", "Year 6", 4)
    turns = client.turns("plan")
    check(len(turns) == 3,
          f"a plan with repeats is sent back for another go (calls: {len(turns)})")
    check("week 2" in turns[1] and "'fractions (part 1)'" in turns[1],
          "and the retry names the offending week and its focus", turns[1][-400:])
    check("week 4" in turns[1],
          "an empty focus is treated as a repeat: it generates the same vague "
          "booklet as its neighbours")
    foci = [w.focus for w in plan.weeks]
    check(len({_norm_focus(f) for f in foci}) == 4,
          f"a model that never complies is repaired from the ladder: {foci}")
    check(foci[0] == "fractions (part 1)" and foci[2] == "adding and subtracting fractions",
          "the weeks that were already distinct are left exactly as they were",
          str(foci))
    check(all(f in _ladder("mathematics") for f in (foci[1], foci[3])),
          "and the replacements are real named maths skills", str(foci))

    print("\n== the fallback is a plan a parent would accept ==")
    class _Dead:
        def complete(self, *a, **kw):
            raise RuntimeError("planner unavailable")

    plan = TermPlannerAgent(_Dead(), 2).plan("Academic Accelerate", "English", "Year 6", 10)
    foci = [w.focus for w in plan.weeks]
    check(len(plan.weeks) == 10, "ten weeks")
    check(len({_norm_focus(f) for f in foci}) == 10,
          f"all ten differ: {foci}")
    check(not any(re.search(r"\(part \d+\)", f) for f in foci),
          "and none of them is the same topic with a number after it", str(foci))
    check(foci[0] == "alliteration and repetition" and foci[1].startswith("kinds of rhyme"),
          "week 1 alliteration and repetition, week 2 kinds of rhyme, exactly "
          "as the owner described it", str(foci[:2]))
    check(sum(1 for w in plan.weeks if w.revision) == 2,
          "the last two weeks are still revision")
    revision = [w.focus for w in plan.weeks if w.revision]
    check(len(set(revision)) == 2 and all(f.startswith("revision") for f in revision),
          f"and the two revision weeks revise different things: {revision}")

    maths = TermPlannerAgent(_Dead(), 2).plan("Academic Accelerate", "Mathematics",
                                              "Year 6", 8)
    check(maths.weeks[0].focus == "place value and rounding",
          "the maths ladder is a maths ladder", maths.weeks[0].focus)
    naplan = TermPlannerAgent(_Dead(), 2).plan("NAPLAN Practice", "Numeracy and Literacy",
                                               "Year 5", 6)
    kinds = [w.focus for w in naplan.weeks if not w.revision]
    check(any(f in _ladder("english") for f in kinds)
          and any(f in _ladder("mathematics") for f in kinds),
          f"a NAPLAN term interleaves literacy and numeracy weeks: {kinds}")

    hinted = TermPlannerAgent(_Dead(), 2).plan("Academic Accelerate", "English",
                                               "Year 6", 4, topic_hint="poetry")
    check(all("poetry" in w.focus for w in hinted.weeks if not w.revision),
          "a topic hint is honoured without collapsing the weeks into one topic",
          str([w.focus for w in hinted.weeks]))

    print("\n== a short plan is padded with different weeks, not the same one ==")
    short = TermPlan(weeks=[
        TermWeek(week=1, focus="alliteration and repetition", difficulty="easy"),
        TermWeek(week=2, focus="similes and metaphors", difficulty="easy"),
    ])
    padded = TermPlannerAgent._normalise(short, 5, "English", None)
    foci = [w.focus for w in padded.weeks]
    check(len(padded.weeks) == 5 and len({_norm_focus(f) for f in foci}) == 5,
          f"a 2-week reply padded to 5 gives 5 different weeks: {foci}")
    check(all(w.week == i for i, w in enumerate(padded.weeks, 1)),
          "renumbered 1..N as before")

    print("\n== TermPlan and TermWeek need no schema change ==")
    check(set(TermWeek.model_fields) == {"week", "focus", "difficulty", "revision"},
          f"TermWeek is unchanged: {sorted(TermWeek.model_fields)}")
    check(set(TermPlan.model_fields) == {"weeks"},
          "TermPlan is unchanged: `focus` already IS the named skill, so the "
          "progression is a question of what goes in it, not of shape")


def main() -> int:
    passages()
    spelling()
    term_ladder()
    print(f"\n{PASSED}/{TOTAL} behaved as expected")
    return 0 if PASSED == TOTAL else 1


if __name__ == "__main__":
    raise SystemExit(main())
