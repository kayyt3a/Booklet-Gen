"""Checks a multi-subject booklet still contains every subject it advertises.

NAPLAN Practice is declared in programs.py with
`subjects=("Mathematics", "English")` and `subject_display="Numeracy and
Literacy"`, and that string is PRINTED ON THE COVER. It is one of only two
products on the customer menu.

The contents of the cover are not, however, decided by the cover. Both subject
engines run, their sections are merged into one list, and
`BookletPipeline._fit_classwork_to_cap` then trims that merged list down to the
minutes a student of that year can actually work for. The trimmer removes whole
subtopics from the booklet, homework and all, and it used to know nothing about
subject:

  * `_leaves_the_session` dropped the LONGEST subtopic among those whose topic
    still had a sibling in the session. An English subtopic carries a reading
    passage, so it costs more minutes than any maths subtopic, and the longest
    is English almost every time.
  * Once every remaining topic held a single subtopic, the topic-breadth rule
    had nothing to say and the choice collapsed to "drop the longest", which is
    English again, and again, until there was none left.
  * `_may_drop` only counted subtopics and topics. Three maths subtopics under
    three maths topics satisfy both floors perfectly.

Measured through `run_program("naplan", ...)` on the stub below, before the fix:

    Year 3   sections: Mathematics 3            class work: Mathematics 3
    Year 5   sections: Mathematics 3            class work: Mathematics 3
    Year 7   sections: Mathematics 3, English 1 class work: Mathematics 3, English 1
    Year 9   sections: Mathematics 3, English 1 class work: Mathematics 3, English 1

and after it:

    Year 3   Mathematics 2, English 1   English 35% of the class work
    Year 5   Mathematics 2, English 1   English 35%
    Year 7   Mathematics 2, English 2   English 52%
    Year 9   Mathematics 2, English 2   English 52%

At Years 3 and 5 the customer received a booklet titled "NAPLAN Practice /
Numeracy and Literacy" containing no literacy whatsoever: no reading passage,
no comprehension, no language conventions, in the lesson, the practice or the
homework. That is not a layout defect, it is the booklet not being the product
that was bought.

What is pinned here is deliberately the whole promise, not the mechanism:

  * Every subject the program declares reaches the finished booklet.
  * Every subject is TAUGHT, meaning it has a section with a mini-lesson and
    classwork questions. A subject present only as leftover homework is not
    what "in one booklet" means.
  * Neither subject is reduced to a token. A booklet that is seven eighths
    numeracy and one English question still misdescribes itself. Two ways: a
    third of the session in minutes, which is what the customer experiences,
    and a spread of at most one subtopic, which is the unit the trimmer moves
    and so the thing it can actually be held to.
  * The session cap is still held. The cheap way to pass everything above is to
    stop trimming, which hands a Year 3 an hour of work; that is the defect
    this trimmer exists to prevent, so it is asserted against here too.
  * The cover cannot name a subject the booklet does not hold. Everything above
    is about the trimmer; this last one is about the printed claim, and it
    holds however the contents came to be narrower than the label.

No API key is needed or used: every call is served by the stub client below.

    PYTHONPATH=. python scripts/check_naplan_both_subjects.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The fitter logs a warning whenever a floor holds a session over its cap. That
# is an expected outcome in lower primary, not a failure.
logging.disable(logging.CRITICAL)

from booklet_gen.pipeline import BookletPipeline            # noqa: E402
from booklet_gen.programs import PROGRAMS                   # noqa: E402
from booklet_gen.schemas import SubtopicOutput              # noqa: E402
from booklet_gen.timing import (booklet_timing,             # noqa: E402
                                classwork_section_minutes, session_plan)

NAPLAN_YEARS = ("Year 3", "Year 5", "Year 7", "Year 9")

_passed = 0
_failed: list[str] = []


def ok(msg: str) -> None:
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg: str) -> None:
    _failed.append(msg)
    print("  FAIL:", msg)


def check(good: bool, msg: str, why: str = "") -> None:
    """Assert `msg`. `why` is what the customer receives when it is false.

    Kept out of the passing line on purpose: a check that prints the disaster
    it is preventing next to the word "ok" trains a reader to skim it.
    """
    if good:
        ok(msg)
    else:
        bad(f"{msg}. {why}" if why else msg)


# ---------------------------------------------------------------------------
# A stub model shaped like the two halves of a real NAPLAN booklet
#
# The numeracy outline is three topics of school maths. The literacy outline is
# the four-part shape the outline parser really returns for "reading
# comprehension and language conventions", and its comprehension subtopics
# carry five-paragraph readings, because that is what makes an English subtopic
# cost more minutes than a maths one. That cost difference is the whole
# mechanism, so a fixture that priced both halves the same would prove nothing.
# ---------------------------------------------------------------------------

MATHS_OUTLINE = {
    "subject": "Mathematics", "year_level": "YEAR",
    "topics": [
        {"name": "Number and Algebra", "subtopics": [
            {"name": "Place value to 10 000", "difficulty_hint": "medium",
             "question_types": ["computation"]},
            {"name": "Multiplication facts", "difficulty_hint": "medium",
             "question_types": ["computation"]}]},
        {"name": "Measurement and Geometry", "subtopics": [
            {"name": "Perimeter of rectangles", "difficulty_hint": "medium",
             "question_types": ["word problem"]},
            {"name": "Reading scales", "difficulty_hint": "easy",
             "question_types": ["short answer"]}]},
        {"name": "Statistics and Probability", "subtopics": [
            {"name": "Reading column graphs", "difficulty_hint": "medium",
             "question_types": ["short answer"]}]},
    ],
}

ENGLISH_OUTLINE = {
    "subject": "English", "year_level": "YEAR",
    "topics": [
        {"name": "Reading and Comprehension", "subtopics": [
            {"name": "Finding information in a text",
             "difficulty_hint": "medium", "question_types": ["comprehension"]},
            {"name": "Inference and author purpose",
             "difficulty_hint": "hard", "question_types": ["comprehension"]}]},
        {"name": "Language Conventions", "subtopics": [
            {"name": "Commas in lists and clauses",
             "difficulty_hint": "medium", "question_types": ["short answer"]},
            {"name": "Apostrophes", "difficulty_hint": "medium",
             "question_types": ["short answer"]}]},
        {"name": "Vocabulary", "subtopics": [
            {"name": "Synonyms and shades of meaning",
             "difficulty_hint": "medium", "question_types": ["short answer"]}]},
    ],
}

MATHS_LESSON = json.dumps({
    "intro_paragraphs": [
        "Place value tells you what each digit in a number is worth. The digit "
        "4 in 4 213 is worth four thousand, not four."],
    "key_points": ["Read the number from the left.",
                   "Each place is ten times the one on its right."],
    "mnemonic": "Left is largest",
    "worked_example": {
        "question": "What is the value of the 7 in 27 508?",
        "steps": ["Write the number under place value headings.",
                  "Find the column the 7 sits in.",
                  "Read off the value of that column."],
        "answer": "7 000", "diagram_spec": None},
    "guided_examples": [{
        "question": "What is the value of the 3 in 13 460?",
        "steps": ["Set out the headings.", "Locate the 3.", "Read the column."],
        "answer": "3 000", "diagram_spec": None}],
})

ENGLISH_LESSON = json.dumps({
    "intro_paragraphs": [
        "A comma separates the items in a list so a reader does not run two of "
        "them together. It also marks off a clause that could be lifted out."],
    "key_points": ["Commas separate the items in a list.",
                   "A comma marks off an added clause."],
    "mnemonic": "Pause, then read on",
    "worked_example": {
        "question": "Add the commas: We packed apples pears grapes and figs.",
        "steps": ["Find the items being listed.",
                  "Put a comma after every item except the last.",
                  "Read it back and listen for the pauses."],
        "answer": "We packed apples, pears, grapes and figs.",
        "diagram_spec": None},
    "guided_examples": [{
        "question": "Add the commas: Mia who lives next door won the race.",
        "steps": ["Find the clause that could be lifted out.",
                  "Put a comma at each end of it."],
        "answer": "Mia, who lives next door, won the race.",
        "diagram_spec": None}],
})

# Five paragraphs, the length the passage prompt asks for at middle primary.
PARAGRAPHS = [
    "Every winter the humpback whales leave the cold water near Antarctica and "
    "swim north along the Australian coast. They travel thousands of "
    "kilometres without stopping to feed.",
    "The whales follow the warm current past Western Australia. Families "
    "travel together, and the calves swim close beside their mothers the "
    "whole way.",
    "Scientists at Exmouth count the whales from a small boat each morning. "
    "They photograph the underside of each tail, because the pattern of white "
    "marks on a tail is different on every whale.",
    "The count has risen every year since whaling stopped in Australian "
    "waters. In 1963 there were fewer than a thousand humpbacks left on this "
    "coast, and today there are more than thirty thousand.",
    "The whales turn south again in spring with their new calves. Anyone "
    "standing on a headland in September has a good chance of seeing a spout "
    "of spray far out beyond the surf.",
]


def maths_questions(n: int, tag: str) -> dict:
    out = []
    for i in range(1, n + 1):
        if i <= 2:
            out.append({"question": f"{tag} {i}. What is the value of the 6 in "
                                    f"{6000 + i}?",
                        "answer": "6 000", "working": "the 6 is in the "
                                                      "thousands column",
                        "difficulty": "easy"})
        else:
            out.append({"question": f"{tag} {i}. A hall has {i} rows of 24 "
                                    f"chairs. How many chairs are there "
                                    f"altogether?",
                        "answer": str(24 * i), "working": f"24 x {i}",
                        "difficulty": "medium"})
    return {"questions": out}


def english_questions(n: int, quota: int, tag: str) -> dict:
    """A passage-carrying set when a quota was asked for, plain items when not."""
    if quota <= 0:
        return {"questions": [
            {"question": f"{tag} {i}. Add the commas to this sentence: "
                         f"sentence {i} listing four things in a row.",
             "answer": "a comma after each item except the last",
             "working": "the list rule", "difficulty": "medium"}
            for i in range(1, n + 1)]}
    passages = [{"id": f"p{k}", "title": f"The Whale Count {tag[:12]} {k}",
                 "paragraphs": PARAGRAPHS} for k in range(1, quota + 1)]
    out = []
    for k in range(1, quota + 1):
        for j in range(1, 6):
            out.append({"question": f"{tag} p{k} q{j}: according to the "
                                    f"passage, why do the scientists "
                                    f"photograph the underside of each tail?",
                        "answer": "The pattern of white marks is different on "
                                  "every whale.",
                        "working": "stated in the third paragraph",
                        "difficulty": "medium", "passage_id": f"p{k}"})
    while len(out) < n:
        i = len(out) + 1
        out.append({"question": f"{tag} {i}. Which word is closest in meaning "
                                f"to 'enormous' in item {i}?",
                    "answer": "huge", "working": "a synonym",
                    "difficulty": "medium"})
    return {"questions": out, "passages": passages}


def judge_payload(user: str) -> str:
    answers = re.findall(r"^Proposed answer: (.*)$", user, re.MULTILINE)
    return json.dumps({"results": [
        {"index": i, "solved_answer": a, "verified": True, "reason": "stub"}
        for i, a in enumerate(answers)]})


def challenge_payload(user: str) -> str:
    m = re.search(r"exactly (\d+) cumulative", user)
    n = int(m.group(1)) if m else 5
    subject = "numeracy" if "Subject: Mathematics" in user else "literacy"
    return json.dumps({"questions": [
        {"question": f"Final Challenge {subject} {i}: work this out and "
                     f"explain how you know your answer is right.",
         "answer": "42", "working": "shown", "difficulty": "hard"}
        for i in range(1, n + 1)]})


class StubClient:
    def __init__(self, year: str) -> None:
        self.year = year

    def complete(self, system, user, tier="strong", temperature=0.0):
        if system.startswith("You convert a short natural-language description"):
            # The description is the FIRST LINE of the user turn. The product
            # guide is appended after it and mentions both numeracy and
            # literacy, so only the first line can tell the two halves apart.
            head = user.split("\n", 1)[0]
            body = ENGLISH_OUTLINE if "literacy" in head else MATHS_OUTLINE
            return json.dumps(body).replace("YEAR", self.year)
        if "writing a mini-lesson" in system:
            return (ENGLISH_LESSON
                    if system.startswith("You are a warm, expert English")
                    else MATHS_LESSON)
        if system.startswith("You are an independent grader"):
            return judge_payload(user)
        if "FINAL CHALLENGE" in system:
            return challenge_payload(user)
        m = re.search(r"Generate exactly (\d+) questions", user)
        n = int(m.group(1)) if m else 6
        sub = re.search(r"Subtopic: (.*)", user)
        # Every question text carries its subtopic. The booklet-wide dedupe is
        # real, so a fixture that returned the same text for every subtopic
        # would silently empty nine sections out of ten and measure nothing.
        tag = (sub.group(1) if sub else "x")[:24]
        if "Subject: English" in user:
            quota = re.search(r"Write exactly (\d+) passage", user)
            return json.dumps(
                english_questions(n, int(quota.group(1)) if quota else 0, tag))
        return json.dumps(maths_questions(n, tag))


class NoRetriever:
    def retrieve(self, *a, **kw):
        return []


def build(year: str):
    pipeline = BookletPipeline(client=StubClient(year), retriever=NoRetriever(),
                              max_workers=1)
    return pipeline.run_program("naplan", year, "Kieran")


def by_subject(sections) -> Counter:
    return Counter(s.subject for s in sections)


# ---------------------------------------------------------------------------
program = PROGRAMS["naplan"]
DECLARED = tuple(program.subjects)

print("\nTHE PRODUCT PROMISES TWO SUBJECTS ON THE COVER")
check(len(DECLARED) > 1,
      f"{program.label} declares {len(DECLARED)} subjects: "
      f"{', '.join(DECLARED)}")
check(bool(program.subject_display),
      f'and the cover prints "{program.subject_display}" under the title')

booklets = {year: build(year) for year in NAPLAN_YEARS}

print("\nWHAT A NAPLAN BOOKLET ACTUALLY CONTAINS, YEAR BY YEAR")
print(f"    {'year':<9}{'sections':<34}{'taught in class work':<34}"
      f"{'class min':>10}")
for year, data in booklets.items():
    taught = [s for s in data.sections if s.questions]
    everything = ", ".join(f"{s} {n}" for s, n in
                           sorted(by_subject(data.sections).items()))
    in_class = ", ".join(f"{s} {n}" for s, n in
                         sorted(by_subject(taught).items())) or "nothing"
    print(f"    {year:<9}{everything:<34}{in_class:<34}"
          f"{booklet_timing(data)['classwork_minutes'] or 0:>10}")

print("\nEVERY SUBJECT ON THE COVER IS TAUGHT INSIDE THE BOOKLET")
for year, data in booklets.items():
    taught = [s for s in data.sections if s.questions]
    present = by_subject(data.sections)
    lessons = by_subject([s for s in taught if s.teaching is not None])
    for subject in DECLARED:
        other = ", ".join(s for s in DECLARED if s != subject)
        check(present[subject] > 0,
              f"{year} carries {present[subject]} {subject} subtopic(s) in the "
              f"booklet",
              f"A parent who bought {program.label} for {year} receives a "
              f'booklet whose cover reads "{program.subject_display}" and '
              f"which contains nothing but {other}. The {subject} half was "
              f"generated and then deleted by the session trimmer: "
              f"mini-lesson, practice, homework and reading passages, all of "
              f"it, with no error anywhere to say so")
        check(lessons[subject] > 0,
              f"{year} teaches {subject} in Class Work "
              f"({lessons[subject]} mini-lesson(s) with practice under them)",
              f"The {subject} half of this {year} booklet is not taught in the "
              f"session. A subject that survives only as homework has no "
              f"lesson behind it, and a child working alone at the kitchen "
              f"table cannot be taught a new method by a box of text")

print("\nNEITHER SUBJECT IS REDUCED TO A TOKEN")
# A booklet that is 90 percent numeracy with one English section bolted on the
# end still misdescribes itself. The cover gives the two halves equal billing,
# so the target is an even split and the floor is a third.
#
# A third rather than a half because whole subtopics are the unit being moved,
# and rather than the quarter this file first asked for because a quarter lets
# a three-to-one booklet through: Years 7 and 9 passed a quarter at 27 percent
# while teaching three maths subtopics against one of English, which is the
# defect wearing a smaller hat.
#
# A third is also as far as this can honestly be pushed. `MIN_CLASSWORK_SUBTOPICS`
# is 3 and both floors are sold on the pricing page, so the closest to even a
# Year 3 session can come is two subtopics against one. Demanding more of the
# smaller half than a third would be demanding a product change (a two-subject
# session that teaches four subtopics, or a shorter mini-lesson) and pretending
# it was a code one.
FLOOR = 1.0 / 3.0
for year, data in booklets.items():
    minutes = Counter()
    for s in data.sections:
        if s.questions:
            minutes[s.subject] += classwork_section_minutes(s)
    total = sum(minutes.values()) or 1.0
    for subject in DECLARED:
        share = minutes[subject] / total
        check(share >= FLOOR,
              f"{year} gives {subject} {share:.0%} of the class work "
              f"({minutes[subject]:.0f} of {total:.0f} min)",
              f"A {year} booklet sold as \"{program.subject_display}\" is "
              f"really one subject with a token section of the other stapled "
              f"to it. The parent bought two halves and is printing one")

print("\nAND THE SPLIT IS EVEN IN THE UNIT THE TRIMMER ACTUALLY MOVES")
# The minute share above is what the customer experiences, but it is a ratio of
# two numbers the fixture chose, so on its own it can be satisfied by luck: a
# subject with one long subtopic clears a third against three short ones. What
# the trimmer decides is which whole subtopic leaves, so that is the unit this
# is asserted in, and it is the assertion that would fail first if the balance
# rule in `_leaves_the_session` were removed and only the subject floor left.
for year, data in booklets.items():
    taught = by_subject([s for s in data.sections if s.questions])
    counts = {subject: taught[subject] for subject in DECLARED}
    spread = max(counts.values()) - min(counts.values())
    check(spread <= 1,
          f"{year} teaches "
          f"{', '.join(f'{n} {s}' for s, n in sorted(counts.items()))}, "
          f"a spread of {spread} subtopic(s)",
          f"The {year} session is shared out {counts}. Both halves survive, so "
          f"nothing above catches it, but the child still gets one subject "
          f"taught properly and the other touched on. Trimming is spent on "
          f"whichever subject is heaviest for exactly this reason")

print("\nAND THE SESSION IS STILL TRIMMED TO WHAT THE STUDENT CAN WORK FOR")
# The cheap way to pass everything above is to stop trimming. That hands a
# Year 3 an hour and a half of seated work, which is the fault the trimmer was
# written for, so it is asserted against here rather than left to another file.
for year, data in booklets.items():
    plan = session_plan(year)
    cap = BookletPipeline._session_cap(plan)
    # Measured the way the PAGE measures it (booklet_timing), not from the
    # coarse estimate the pipeline carries on BookletData.
    minutes = booklet_timing(data)["classwork_minutes"] or 0
    # The floors can hold a session a little over its cap and that is by
    # design (see _fit_classwork_to_cap), so the assertion is against a
    # booklet that was never trimmed at all, not against the cap to the minute.
    check(minutes <= cap + 15,
          f"{year} class work is {minutes} min against a {cap} min cap",
          f"Keeping both subjects must not be bought by abandoning the cap. "
          f"An untrimmed NAPLAN booklet is both halves in full, which sits a "
          f"{year} student down for well over an hour of written work")
    topics = len({s.topic for s in data.sections if s.questions})
    check(topics >= plan.min_topics,
          f"{year} still covers {topics} topics, the promise is "
          f"{plan.min_topics}",
          "The pricing page renders this number into a promise, so a booklet "
          "under it is a promise the customer can check and find false")

print("\nAND THE COVER CANNOT NAME A SUBJECT THE BOOKLET DOES NOT HOLD")
# Everything above pins the trimmer, which is the fix. This pins the guarantee,
# at the point of printing, which is stronger: `subject_display` is a fixed
# string chosen before a single question exists, and the trimmer is only one of
# the ways the contents can end up narrower than it. An engine that returns
# nothing, or a validator that rejects a whole half, prints the same false
# cover, and no assertion about the trimmer would notice.
for year, data in booklets.items():
    check(data.subject == program.subject_display,
          f'{year} still prints "{data.subject}" on the cover, and holds both '
          f"halves to back it up",
          f'The finished {year} booklet is labelled "{data.subject}" rather '
          f'than "{program.subject_display}", which means the honesty guard '
          f"fired and a subject really did go missing upstream")

FAKE = [SubtopicOutput(topic="Number and Algebra", subtopic="Place value",
                       subject="Mathematics", questions=[]),
        SubtopicOutput(topic="Language Conventions", subtopic="Commas",
                       subject="English", questions=[])]
narrowed = BookletPipeline._honest_subject_display(
    program.subject_display, DECLARED, FAKE[:1])
check(narrowed == "Mathematics",
      f'a booklet holding only maths would be covered "{narrowed}", not '
      f'"{program.subject_display}"',
      "The cover line is still whatever the program declared, so a booklet "
      "that loses a subject by any route the trimmer does not control goes "
      "out claiming to teach it")
kept = BookletPipeline._honest_subject_display(
    program.subject_display, DECLARED, FAKE)
check(kept == program.subject_display,
      f'and a booklet holding both is left alone at "{kept}"',
      "The guard is rewriting covers it should not touch, which would replace "
      "the product's own words with the engine names on every booklet")

print(f"\n{_passed} passed, {len(_failed)} failed")
if _failed:
    for f in _failed:
        print("  -", f)
    sys.exit(1)
