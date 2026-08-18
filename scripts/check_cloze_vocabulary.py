"""Checks fill-in-the-blank vocabulary questions.

English had synonym, antonym and word-in-context items, and no cloze at all:
the one format that makes a student use a new word rather than describe it.

A cloze carries its missing word inside the sentence as [[word]], so the gap
and its answer come from one string and cannot drift apart. Three readers see
that string differently, and this checks all three:

  the page   a ruled gap, and the word must not be anywhere on it
  the key    the word
  the judge  a plain _____, because handing it "[[melancholy]]" and asking
             whether "melancholy" is right makes the tick beside that answer
             mean nothing

    PYTHONPATH=. python scripts/check_cloze_vocabulary.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf

from booklet_gen import formatter as F
from booklet_gen.blanks import plain_gap
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


CLOZE = ('Complete the sentence with the correct word: "The keeper\'s '
         '[[melancholy]] expression told us he had been alone a long time."')
PLAIN = 'Give a synonym for the word "vast" as it is used in the reading.'


def booklet() -> BookletData:
    qs = [
        ValidatedQuestion(question=Question(
            question=CLOZE, answer="melancholy",
            working="He had been alone a long time, so the word means sad."),
            verified=True),
        ValidatedQuestion(question=Question(
            question=PLAIN, answer="enormous", working="Vast means very large."),
            verified=True),
    ]
    return BookletData(
        subject="English", year_level="Year 6", student_name="Sam",
        sections=[SubtopicOutput(
            topic="Vocabulary", subtopic="Words in context",
            teaching=SubtopicTeaching(
                intro_paragraphs=["A new word is easiest to hold on to in a sentence."],
                key_points=["Read the whole sentence before you choose."],
                worked_example=WorkedExample(
                    question='Complete the sentence: "The cave was so '
                             '[[cavernous]] that our voices echoed."',
                    steps=["Echoing needs a big empty space."],
                    answer="cavernous")),
            questions=qs)])


out = Path(tempfile.mkdtemp()) / "cloze.pdf"
F.render_pdf(booklet(), out)
doc = pymupdf.open(out)
pages = [p.get_text() for p in doc]
doc.close()
key_at = next(i for i, p in enumerate(pages) if "Answers & Worked Solutions" in p)
body = "\n".join(pages[:key_at])
answers = "\n".join(pages[key_at:])

print("\nTHE GAP IS ON THE PAGE, THE WORD IS IN THE KEY")

assert "melancholy" not in body, (
    "the missing word is printed on the page the student writes on, so the "
    "question answers itself")
assert "The keeper's" in body, "the sentence around the gap is missing"
assert "expression told us" in body, "the words after the gap were swallowed"
ok("the sentence prints with its word taken out")

assert "melancholy" in answers, "the key does not say what belongs in the gap"
ok("the key prints the word the gap was hiding")

print("\nNO MACHINERY REACHES THE CUSTOMER")

whole = "\n".join(pages)
assert "[[" not in whole and "]]" not in whole, (
    "raw [[ ]] markers printed in the booklet")
# The worked example is the finished demonstration above the practice, so its
# sentence shows the word. It used to print the brackets instead: only the
# instruction was stripped, and a specimen sentence is not the instruction.
assert "cavernous" in body, (
    "the worked example's sentence lost its word, or printed the markers")
ok("no raw markers anywhere, and the worked example still reads as prose")

print("\nA QUESTION WITHOUT A BLANK IS UNTOUCHED")

assert PLAIN.replace('"', "") in body.replace('"', "").replace("\n", " ") or \
    "synonym for the word" in body, "a plain question was altered"
assert "enormous" in answers and "enormous" not in body
ok("a question with no [[ ]] renders exactly as before")

print("\nTHE VALIDATOR IS NOT HANDED THE ANSWER IT IS CHECKING")

judged = plain_gap(CLOZE)
assert "melancholy" not in judged, (
    "the judge sees the missing word inside the question, so it agrees with "
    "any answer and the verified tick beside a cloze means nothing")
assert "_____" in judged, f"the judge sees no gap at all: {judged}"
assert "The keeper's" in judged and "expression told us" in judged, (
    "the judge lost the sentence it has to grade against")
ok("the judge grades the sentence with the word actually removed")

# The judge builds its prompt from q.question directly, so the wiring matters
# as much as the helper.
import inspect  # noqa: E402

from booklet_gen.agents import llm_judge  # noqa: E402

source = inspect.getsource(llm_judge)
assert "plain_gap(q.question)" in source, (
    "llm_judge interpolates q.question raw somewhere, which leaks the answer "
    "into the prompt that is meant to be checking it")
assert "f\"Question: {q.question}" not in source, (
    "a raw question interpolation is still present in llm_judge")
ok("every judge prompt goes through the gap, not the raw question")

print(f"\nALL {_passed} CLOZE CHECKS PASSED")
sys.exit(0)
