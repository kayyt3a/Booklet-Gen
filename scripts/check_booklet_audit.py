"""Checks that the booklet auditor finds real defects and invents none.

`scripts/audit_booklet.py` reads a finished PDF and reports what is wrong with
it. It is the only thing in the product that looks at a whole booklet the way
a customer meets one, so if it goes wrong nothing else notices, and it goes
wrong in two directions that need catching separately.

MISSING A DEFECT is the obvious failure. A parser that stops matching after a
formatter change reports a clean booklet and the tool quietly becomes a
rubber stamp.

INVENTING ONE is the failure that actually kills the tool. Writing it turned
up four false alarms before it turned up anything true:

  * an answer of 7222 above column-addition working reading "write 2, carry 1"
    three times, reported as an answer that disagrees with its own working.
    It is correct: a column algorithm builds its result one digit at a time
    and never states it.
  * a 22-word angle question with no answer line under it, which ran on to the
    foot of its page, swallowed the Homework section header, and came back as
    86 words against a budget of 25.
  * numbered worked-example STEPS counted as questions, which made a 22
    question booklet report 41.
  * the contents page and the how-to page audited as pages a child writes on,
    because the running header was upper-cased before matching and the
    contents page lists "Warm-up Recap" in title case.

A tool that raises a false alarm on correct work teaches its user to ignore
it, which costs more than the defect it might have caught. So the negative
control below matters more than the positive one: a clean booklet must come
back clean.

Renders real booklets through the real formatter, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_booklet_audit.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# The auditor is a script rather than a module, so its own directory has to be
# importable for `import audit_booklet` below to work from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from booklet_gen.formatter import render_pdf                       # noqa: E402
from booklet_gen.schemas import (                                  # noqa: E402
    BookletData, Question, SubtopicOutput, SubtopicTeaching,
    ValidatedQuestion, WorkedExample)

import audit_booklet                                               # noqa: E402

PASSED = 0
TOTAL = 0
OUT = Path(tempfile.mkdtemp())


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


def q(text, answer, working, difficulty="medium"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty),
        verified=True, validator_notes="", retry_count=0)


def teaching(name):
    # The steps are deliberately LONGER than the Year 3 reading budget of 25
    # words. That is realistic, because a step is written for an adult to read
    # aloud while a question is written for a child to read alone, and it is
    # what makes the assertion about them able to fail: steps that were
    # already inside the budget could be miscounted as questions all day and
    # nothing would report it.
    return SubtopicTeaching(
        intro_paragraphs=[f"{name} is about counting carefully and checking "
                          "the result before moving on."],
        key_points=["Work left to right.", "Check the units at the end."],
        worked_example=WorkedExample(
            question="Calculate 48 + 25.",
            steps=["Add the ones column first, because 8 and 5 make 13, which "
                   "is more than one column can hold, so you write the 3 "
                   "underneath in the ones column and carry the 1 across into "
                   "the tens column where it waits for the next step.",
                   "Add the tens column next, remembering to include the 1 "
                   "that was carried over, so that 4 and 2 and 1 together "
                   "make 7, and that 7 is written underneath in the tens "
                   "column to finish the calculation off properly."],
            answer="73"))


def subtopic(topic, name, practice, homework):
    return SubtopicOutput(topic=topic, subtopic=name, subject="Mathematics",
                          teaching=teaching(name), questions=practice,
                          homework_questions=homework, estimated_minutes=14)


CLEAN_PRACTICE = [
    q("Calculate 58 divided by 4.", "14 remainder 2",
      "4 goes into 5 once with 1 left over. 4 goes into 18 four times with 2 "
      "left over, so the answer is 14 remainder 2."),
    q("A rectangle is 8 cm long and 3 cm wide. What is its perimeter?",
      "22 cm", "8 + 3 + 8 + 3 = 22 cm."),
    q("What is the value of the digit 5 in 5241?", "5000",
      "The 5 is in the thousands column, so it is worth 5000."),
    q("Calculate 95 divided by 7.", "13 remainder 4",
      "7 into 9 goes once, remainder 2. 7 into 25 goes 3 times, remainder 4."),
    # Column addition, and its answer appears NOWHERE in its own working,
    # because that is how the algorithm is written. This question is what
    # makes the false-alarm assertion able to fail: without it the clean
    # booklet contains no working of this shape at all, and the check passes
    # whether the guard is there or not.
    q("Calculate 4528 + 2694.", "7222",
      "8 + 4 = 12 (write 2, carry 1). 2 + 9 + 1 = 12 (write 2, carry 1). "
      "5 + 6 + 1 = 12 (write 2, carry 1). 4 + 2 + 1 = 7."),
]
CLEAN_HOMEWORK = [
    q("A secret number divided by 5 gives 16 remainder 3. Find it.", "83",
      "16 x 5 = 80, and 80 + 3 = 83."),
    q("A rectangle has an area of 20 square cm and a width of 4 cm. How long "
      "is it?", "5 cm", "20 divided by 4 = 5 cm."),
    q("Which is larger, 3/4 or 0.7?", "3/4",
      "3/4 = 0.75, and 0.75 is larger than 0.7."),
]
CLEAN_CHALLENGE = [
    q("A garden bed is 6 m by 4 m. What is its area?", "24 square m",
      "6 x 4 = 24 square m."),
    q("A class of 30 splits into teams of 4. How many teams, and how many "
      "left over?", "7 teams, 2 left over", "30 divided by 4 = 7 remainder 2."),
    q("Ella reads 12 pages a day for 5 days. How many pages?", "60 pages",
      "12 x 5 = 60 pages."),
    q("A fence costs $8 a metre. How much for 9 metres?", "$72",
      "8 x 9 = 72 dollars."),
]


def booklet(challenge, extra_practice=(), year="Year 3"):
    return BookletData(
        subject="Mathematics", year_level=year, student_name="Sam",
        program_label="Academic Accelerate",
        sections=[
            subtopic("Number and Place Value", "Division with remainders",
                     list(CLEAN_PRACTICE) + list(extra_practice),
                     CLEAN_HOMEWORK),
            subtopic("Measurement and Geometry", "Perimeter and area",
                     CLEAN_PRACTICE, CLEAN_HOMEWORK),
            subtopic("Statistics and Probability", "Column graphs",
                     CLEAN_PRACTICE, CLEAN_HOMEWORK),
        ],
        recap_questions=list(CLEAN_PRACTICE),
        challenge_questions=list(challenge),
        recap_minutes=5, challenge_minutes=4, classwork_minutes=43,
        homework_minutes=13, total_minutes=65)


def audit_of(data, name) -> list:
    path = OUT / f"{name}.pdf"
    render_pdf(data, path)
    return audit_booklet.audit(path)


def kinds(found, severity=None):
    return [f.kind for f in found
            if severity is None or f.severity == severity]


print("\n== a sound booklet comes back sound ==")

clean = audit_of(booklet(CLEAN_CHALLENGE), "clean")
serious = [f for f in clean if f.severity == "SERIOUS"]
check(not serious,
      "nothing serious on a booklet with nothing wrong with it",
      f"reported {[(f.kind, f.where, f.what) for f in serious]}. This is the "
      "failure that kills the tool: a reader who has been shown a false alarm "
      "stops reading the true ones, and every check below becomes worthless")

check("key disagrees" not in kinds(clean),
      "the worked example's column arithmetic is not called a disagreement",
      "'8 + 5 = 13, write 3 and carry 1' never states its own result, which "
      "is how the algorithm is taught and not a fault to report")


print("\n== a subtopic taught and never set is caught ==")

# The Year 6 booklet spent seventeen minutes on angles and set no angle
# question for the week. Its own numbering gives it away: the homework run
# reads TOPIC 1, TOPIC 3.
missing = booklet(CLEAN_CHALLENGE)
missing.sections[1].homework_questions = []
found = audit_of(missing, "nohomework")
check("taught, not set" in kinds(found, "SERIOUS"),
      "a subtopic with class work and no homework is reported",
      f"got {[(f.severity, f.kind) for f in found]}. The child works through "
      "it in the lesson and never sees it again, which is the week the "
      "booklet exists to plan")

# The negative control, and the reason this check is built on the booklet's
# own topic numbering. The first version read which BAND each page belonged
# to, and the running header names the band that STARTS on a page, so a
# Homework topic beginning on the page the Final Challenge also begins on was
# filed under Final Challenge and reported as never set. That false alarm
# fired on a booklet whose homework was complete.
check("taught, not set" not in kinds(clean),
      "and a booklet whose homework covers every topic is left alone",
      "a complete booklet is reported as missing homework, which is the false "
      "alarm that teaches a reader to ignore this tool")


print("\n== a one-question Final Challenge is caught ==")

stub = audit_of(booklet(CLEAN_CHALLENGE[:1]), "stub")
check("thin section" in kinds(stub, "SERIOUS"),
      "a Final Challenge holding one question is reported as serious",
      f"got {[(f.severity, f.kind) for f in stub]}. This is the defect that "
      "shipped twice, on a page of its own, with a score box reading / 1")


print("\n== an answer that disagrees with its own working is caught ==")

wrong = audit_of(booklet(
    list(CLEAN_CHALLENGE[:3]) + [
        q("Four plots each 12 m by 8 m are joined in a row. What is the "
          "perimeter of the new rectangle?", "128 m",
          "The new rectangle is 48 m long and 8 m wide. The perimeter is "
          "2 times 48 plus 8, which is 112 m.")]), "wrong")
check("key disagrees" in kinds(wrong, "SERIOUS"),
      "an answer of 128 m over working that concludes 112 m is caught",
      f"got {[(f.severity, f.kind, f.what) for f in wrong]}. A booklet "
      "shipped exactly this, and the page marks a correct child wrong")


print("\n== the model thinking out loud is caught ==")

aloud = audit_of(booklet(
    list(CLEAN_CHALLENGE[:3]) + [
        q("Four plots each 12 m by 8 m are joined. Find the perimeter.",
          "112 m",
          "Joined in a row the rectangle is 48 m by 8 m, giving 112 m. "
          "Alternatively, if they are joined along the 8 m side it is 12 m "
          "by 32 m, giving 88 m. Let us re-calculate for the most common "
          "interpretation: 112 m.")]), "aloud")
check("deliberation" in kinds(aloud, "SERIOUS"),
      "'Alternatively' and 'Let us re-calculate' in a key are caught",
      f"got {[(f.severity, f.kind) for f in aloud]}. That is deliberation "
      "printed in the document a parent marks from, and it means the answer "
      "above it is a guess between readings rather than a result")


print("\n== a question too long to read is caught, and steps are not ==")

wordy = audit_of(booklet(CLEAN_CHALLENGE, extra_practice=[
    q("A community garden has 4 rectangular plots and each plot is 12 metres "
      "long and 8 metres wide, and a volunteer wants to walk right around the "
      "outside of all four plots once they have been joined together along "
      "their longest sides to make one single larger rectangle, so how far "
      "does the volunteer walk altogether?", "112 m", "2 x (48 + 8) = 112 m")
]), "wordy")
check("long to read" in kinds(wordy),
      "a 50 word question in a Year 3 booklet is reported",
      f"got {[(f.kind, f.what) for f in wordy]}. A maths question that is "
      "hard to READ is a reading test wearing a maths question's clothes")

# The same booklet's worked examples are numbered prose written for an adult
# to read aloud. Counting them as questions put lesson text through a budget
# written for a child reading alone.
long_findings = [f for f in wordy if f.kind == "long to read"]
check(all("Add the ones column" not in f.why
          and "Add the tens column" not in f.why for f in long_findings),
      "and no worked-example step is reported as an over-long question",
      f"{[f.why[:60] for f in long_findings]}: lesson steps are being read as "
      "questions, which is how a 22 question booklet reported 41")

# And the count, which is the same fault seen from the other side.
audited = audit_booklet.Booklet(OUT / "wordy.pdf")
printed = audited.score_box().get("Total", 0)
counted = len(audited.questions())
check(abs(counted - printed) <= 1,
      f"{counted} questions found against the {printed} the booklet prints",
      f"found {counted} where the score box says {printed}. Anything much "
      "over means the mini-lesson's numbered steps are being counted as "
      "questions, and every per-question finding below is then reporting on "
      "lesson prose")


print("\n== the em dash stripper is confirmed, not assumed ==")

dashed = audit_of(booklet(CLEAN_CHALLENGE, extra_practice=[
    q("A ribbon — the long one — is 40 cm. How long is half of it?",
      "20 cm", "40 divided by 2 = 20 cm.")]), "dashed")
check("em dash" not in kinds(dashed),
      "em dashes written into a question do not reach the printed page",
      "the formatter's _dedash stripper is the backstop for a rule the whole "
      "project follows, and this is the only thing that checks it end to end")


print("\n== the booklet's own score box is read, not guessed at ==")

b = audit_booklet.Booklet(OUT / "clean.pdf")
box = b.score_box()
check(box.get("Final Challenge") == len(CLEAN_CHALLENGE),
      f"the score box reports the Final Challenge as {box.get('Final Challenge')}",
      f"read {box}. The box is what the booklet asserts about itself and what "
      "a parent reads, so it is the honest source for section sizes")
check(box.get("Total") == sum(v for k, v in box.items() if k != "Total"),
      f"and its total of {box.get('Total')} is the sum of its parts",
      f"read {box}: the printed total disagrees with the printed sections")

# Read directly, against the page shape a SHORT Final Challenge produces: the
# section title and the score box end up on one page, so "Final Challenge"
# appears twice and the page offers six labels for five numbers. Matching
# those by equal length finds nothing, the auditor reports "score box could
# not be read", and the thin-section check it feeds silently stops running on
# exactly the booklets it exists for. A render cannot be relied on to put
# those two on one page, so the text is handed over instead.
crowded = audit_booklet.Booklet(OUT / "clean.pdf")
crowded.text = [
    "Page 17 of 21\nAcademic Accelerate  |  Year 3  |  Sam\nFINAL CHALLENGE\n"
    "FOLIO AI\nFinal Challenge\nYou have done the hard part. These last "
    "questions mix everything together.\nPART 4 OF 4\n"
    "1. A garden bed is 6 m by 4 m. What is its area?\nAnswer:\n"
    "That is the end of the booklet, Sam.\n"
    "Score Marked by: ____________________ Date: ______________\n"
    "Warm-up Recap\nClass Work\nHomework\nFinal Challenge\nTotal\n"
    "______ / 4\n______ / 12\n______ / 7\n______ / 1\n______ / 24\nFOLIO AI\n"]
crowded.bands = ["FINAL CHALLENGE"]
read = crowded.score_box()
check(read == {"Warm-up Recap": 4, "Class Work": 12, "Homework": 7,
               "Final Challenge": 1, "Total": 24},
      f"a score box sharing its page with the section title still reads: {read}",
      f"read {read}. The page offers one more label than it has numbers, and "
      "a booklet whose score box cannot be read is one the thin-section check "
      "never runs on, which is precisely the booklet it was written for")

pages = b.content_pages()
check(pages and all(b.bands[i] != "ANSWERS" for i in pages),
      f"{len(pages)} pages counted as pages a child writes on",
      "the answer key is being audited as though a child writes on it")

# The front matter, named specifically. "Not the answer key" was the whole of
# this assertion at first and it could not fail: the contents page matched a
# band because the header was upper-cased before comparing, and a contents
# page is not the answer key either, so it sailed through and got measured
# for dead space like a page of questions.
front = [i for i, t in enumerate(b.text)
         if "How to use this booklet" in t or "\nContents\n" in t]
check(front and not (set(front) & set(pages)),
      f"and the {len(front)} front-matter page(s) are not among them",
      f"front matter at {[i + 1 for i in front]} overlaps the content pages "
      f"{[i + 1 for i in pages]}. A contents page is mostly white by design, "
      "so auditing it for dead space makes every booklet look worse than it "
      "is and hides the pages that are genuinely empty")


print("\n== a question stops where the question stops ==")

# Driven over the page text directly rather than through a render, because
# what broke was the boundary and the shape that broke it is a question with
# ruled lines instead of an answer line, immediately above a section heading.
# Arranging that to land on a rendered page is luck; handing the parser the
# text is the measurement.
shaped = audit_booklet.Booklet(OUT / "clean.pdf")
shaped.text = [
    "Page 11 of 18\nAcademic Accelerate  |  Year 3  |  Sam\nCLASS WORK\n"
    "FOLIO AI\n"
    "4. Priya says the angle shown is obtuse because it looks big. Is she "
    "correct? Justify your answer using the square corner method.\n"
    "Homework\nDo these through the week to lock it in. Split into 2 "
    "sessions, about 10 min in total. The Final Challenge at the end adds "
    "about 4 min.\nPART 3 OF 4\nSession 1 of 2 | 4 questions | about 6 min | "
    "Date: __________\nNumber and Place Value\nTOPIC 1 OF 3\n"
    "Place value to thousands\n"]
shaped.bands = ["CLASS WORK"]
got = shaped.questions()
check(len(got) == 1 and 18 <= len(got[0]["text"].split()) <= 26,
      f"a question with no answer line under it measures "
      f"{len(got[0]['text'].split()) if got else 0} words, not the page",
      f"got {[(len(x['text'].split()), x['text'][:50]) for x in got]}. This "
      "exact question ran on to the foot of its page, swallowed the Homework "
      "header, and was reported as 86 words against a budget of 25")


print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
