"""Checks a booklet does not write what it is about to throw away.

A Year 3 booklet's logs, read with their reasons for the first time:

    pipeline.subtopic_dropped subtopic="Angles and shape comparison" ...
    pipeline.subtopic_dropped subtopic="Multiplication facts and arrays" ...
    ... seven of these ...
    pipeline.session_plan year_level="Year 3" cap=40 subtopics_taught=3

The outline planned ten subtopics. All ten were written, graded and drawn,
then the session cap dropped seven to fit a forty minute lesson. About two
thirds of the booklet's Gemini spend and wall-clock time went on pages nobody
saw.

TWO FAULTS, AND THE SECOND IS THE WORSE ONE.

GENERATION HAD NO CEILING. The cap fitter needs real minutes, so it cannot
run before a section is written, but it can never keep more subtopics than the
cap divided by the least a subtopic can cost. Anything past that number is
certain to be thrown away, so it is now never written.
MIN_SUBTOPIC_SESSION_MINUTES sits below the MEASURED cost of a mini-lesson on
its own, which this file checks, so the early trim can only skip subtopics
the fitter was always going to remove.

THE FINAL CHALLENGE WAS WRITTEN BEFORE THE CUT. The warm-up and the Final
Challenge were built inside the generation loop, from every subtopic the
outline planned, and the fitter ran afterwards. So a cumulative question could
test a subtopic that was then removed from the booklet: a challenge on a
lesson the child was never given. Both are now written after the fit, from
the sections that are printed.

Runs against fakes, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_generate_only_what_fits.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen import pipeline as pipeline_module                 # noqa: E402
from booklet_gen.pipeline import BookletPipeline                    # noqa: E402
from booklet_gen.schemas import (                                   # noqa: E402
    Outline, Question, Subtopic, SubtopicOutput, SubtopicTeaching,
    Topic, ValidatedQuestion, WorkedExample)
from booklet_gen.timing import classwork_section_minutes, session_plan  # noqa: E402

PASSED = 0
TOTAL = 0


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


# The outline the shipped Year 3 booklet planned, read off its logs.
SHIPPED = {
    "Number and Place Value": ["Place value and ordering four-digit numbers",
                               "Adding and subtracting three-digit numbers",
                               "Multiplication facts and arrays",
                               "Division with and without remainders"],
    "Fractions and Decimals": ["Unit fractions and equivalent fractions",
                               "Decimals to hundredths"],
    "Patterns and Algebra": ["Additive number patterns"],
    "Measurement and Geometry": ["Capacity and volume",
                                 "Angles and shape comparison",
                                 "Mass and scales"],
}


def outline(year="Year 3") -> Outline:
    return Outline(subject="Mathematics", year_level=year, topics=[
        Topic(name=t, subtopics=[Subtopic(name=s, difficulty_hint="medium")
                                 for s in subs])
        for t, subs in SHIPPED.items()])


def teaching() -> SubtopicTeaching:
    return SubtopicTeaching(
        intro_paragraphs=["A short introduction to the idea, two sentences "
                          "long, the way the intro writer produces them."],
        key_points=["First key point.", "Second key point."],
        worked_example=WorkedExample(question="Calculate 74 divided by 3.",
                                     steps=["Divide the tens.",
                                            "Divide the ones.",
                                            "Write the remainder."],
                                     answer="24 remainder 2"),
        guided_examples=[WorkedExample(question="Calculate 87 divided by 5.",
                                       steps=["Divide the tens.",
                                              "Divide the ones."],
                                       answer="17 remainder 2")])


def q(n):
    return ValidatedQuestion(
        question=Question(question=f"Question {n}?", answer=str(n),
                          working=f"{n} = {n}", difficulty="medium"),
        verified=True, validator_notes="", retry_count=0)


print("\n== the outline is trimmed before anything is generated ==")

tasks = [(t.name, st) for t in outline().topics for st in t.subtopics]
kept = BookletPipeline._within_session_budget(tasks, "Year 3")
check(len(kept) < len(tasks),
      f"Year 3: {len(tasks)} planned, {len(kept)} generated",
      f"all {len(tasks)} are still generated, and the 40 minute cap keeps "
      "three. That is the shipped booklet: seven subtopics written, graded "
      "and drawn for nobody")
check(len({topic for topic, _ in kept}) == len(SHIPPED),
      f"and every one of the {len(SHIPPED)} topics keeps a subtopic",
      f"kept topics {sorted({t for t, _ in kept})}. The fitter holds a floor "
      "on topics covered, and a trim that keeps three subtopics of one topic "
      "leaves it nothing to honour that floor with")
check([st.name for _, st in kept]
      == [st.name for _, st in tasks if (_, st) in kept or st in
          [k[1] for k in kept]],
      "in the outline's own order",
      "the trim reordered the booklet, which the outline parser decides")


print("\n== and it can never skip a subtopic the cap would have kept ==")

# The safety argument for trimming early, measured rather than asserted: the
# floor the trim assumes must sit below what a subtopic really costs with its
# lesson and no practice at all, the cheapest it can ever be in the session.
lesson_only = SubtopicOutput(topic="T", subtopic="S", subject="Mathematics",
                             teaching=teaching(), questions=[q(1)],
                             homework_questions=[], estimated_minutes=0)
measured = classwork_section_minutes(lesson_only)
floor = pipeline_module.MIN_SUBTOPIC_SESSION_MINUTES
check(floor < measured,
      f"the assumed floor of {floor} min sits under the measured "
      f"{measured:.1f} min of one lesson with one question",
      f"a lesson measures {measured:.1f} min and the trim assumes {floor}. "
      "With the floor above the real cost, the trim skips subtopics the "
      "session had room for and the booklet comes out short")

for year in ("Year 1", "Year 3", "Year 6", "Year 9"):
    plan = session_plan(year)
    cap = BookletPipeline._session_cap(plan)
    most_that_fit = int(cap // measured)
    n = len(BookletPipeline._within_session_budget(tasks, year))
    check(n >= max(most_that_fit, plan.min_subtopics),
          f"{year}: generates {n}, and at most {most_that_fit} could ever fit "
          f"its {cap} minutes",
          f"generating {n} where up to {most_that_fit} could be kept, so the "
          "early trim is now deciding the booklet rather than saving waste")


print("\n== the Final Challenge is written from what survived the cut ==")

pipe = BookletPipeline.__new__(BookletPipeline)
pipe._parser = type("P", (), {"parse": lambda self, d, g=None: outline()})()


def fake_generate(self, out, seen=None, authoring_guidance=None, use_rag=True):
    kept = self._within_session_budget(
        [(t.name, st) for t in out.topics for st in t.subtopics],
        out.year_level)
    sections = [SubtopicOutput(topic=t, subtopic=st.name, subject=out.subject,
                               teaching=teaching(),
                               questions=[q(i) for i in range(4)],
                               homework_questions=[q(i) for i in range(3)],
                               estimated_minutes=14)
                for t, st in kept]
    return sections, [(t, st.name) for t, st in kept], []


asked: dict[str, list] = {"challenge": [], "recap": []}
BookletPipeline._generate_from_outline = fake_generate
pipe._build_challenge = lambda subj, yl, covered, ctx, seen, alone=True: (
    asked["challenge"].extend(covered) or [])
pipe._build_recap = lambda subj, yl, pf, ctx, seen, covered=None: (
    asked["recap"].extend(covered or []) or [])

booklet = pipe.run_program("accelerate", "Year 3", "Sam", subject="Mathematics")
printed = {(s.topic, s.subtopic) for s in booklet.sections}
stray = [c for c in asked["challenge"] if c not in printed]
check(asked["challenge"] and not stray,
      f"the Final Challenge was asked about {len(asked['challenge'])} "
      f"subtopic(s), all of them printed",
      f"it was asked about {stray}, which the booklet does not teach. A "
      "cumulative question on a lesson the child was never given")
stray = [c for c in asked["recap"] if c not in printed]
check(not stray, "and so was the warm-up",
      f"the warm-up was built on {stray}, which are not in the booklet")
check(len(booklet.sections) <= len(kept),
      f"{len(booklet.sections)} subtopic(s) printed",
      "more printed than were generated, which cannot be right")


print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
