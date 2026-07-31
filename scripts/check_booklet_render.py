#!/usr/bin/env python3
"""Check what the booklet formatter actually puts on the page.

Every case here comes from a real generated booklet. The renderer is run for
real, the PDF is read back with pypdf, and the assertions are made against the
text layer and the geometry, not against the code that produced them.

Covers:
  * the student copy: same booklet, no answer key, no verification marks
  * notation: one symbol per operation, and unknowns left alone
  * an "Answer:" rule under every question that wants a short answer
  * page fill: no page abandoned two thirds empty
  * the closing note and the score line
  * the answer key: fractions in lowest terms, units restored, one step per
    line everywhere, and a page reference back to the question
  * homework split into sittings, and room to work in the warm-up
  * render_exam_pdf still renders (it shares these styles)

Usage:  python scripts/check_booklet_render.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf                                                    # noqa: E402
from reportlab.lib.pagesizes import A4                          # noqa: E402
from reportlab.lib.units import cm                              # noqa: E402

from booklet_gen.formatter import (                             # noqa: E402
    HOMEWORK_MIN_START_CM, PAGE_MARGIN, _escape, _register_fonts,
    answer_line_labels, answer_unit, key_answer, part_labels,
    render_booklet_pair, render_exam_pdf, simplify_fractions_in_answer,
    solution_lines, student_copy_path)
from booklet_gen.schemas import (                               # noqa: E402
    BookletData, ExamPaper, ExamSection, Question, SubtopicOutput,
    SubtopicTeaching, ValidatedQuestion, WorkedExample)
from booklet_gen.timing import (                                # noqa: E402
    booklet_timing, homework_session_plan)

MULT = "×"
DIV = "÷"
CUBED = "³"
SQUARED = "²"
TICK = "✓"

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# 1. Notation
# ---------------------------------------------------------------------------

# (input, must appear in output, must not appear in output)
NOTATION_CASES = [
    # The three symbols one real Year 5 booklet used within two pages.
    ("Calculate the value of 15 * 4 + 7.", f"15 {MULT} 4", "*"),
    ("Multiply the top number by 3: 1 x 3 = 3.", f"1 {MULT} 3", " x "),
    ("If 5x = 45, what is the value of x?", "5x = 45", MULT),
    ("Perimeter = 2 * (length + width)", f"2 {MULT} (", "*"),
    ("Volume = 7 * 4 * 2", f"7 {MULT} 4 {MULT} 2", "*"),
    ("Volume = l x w x h.", f"l {MULT} w {MULT} h", " x "),
    ("Volume = 40 cm x 20 cm x 10 cm.", f"cm {MULT} 20", " x "),
    # Division: slash and word, both to one sign.
    ("Calculate 8000 / 2 = 4000.", f"8000 {DIV} 2", "/"),
    ("40 / 8 = 5", f"40 {DIV} 8", "/"),
    ("Find the volume by dividing 8000 divided by 2.", f"8000 {DIV} 2", "divided by 2"),
    # Volume and area units, three spellings to one.
    ("What is the volume in cubic centimetres?", "cm" + CUBED, "cubic"),
    ("A box with a volume of 720 cubic cm", "720 cm" + CUBED, "cubic"),
    ("State the volume with cubic units: 60 cm^3.", "60 cm" + CUBED, "^"),
    ("how many cubic metres of water", "m" + CUBED, "cubic"),
    ("a base area of 24 square centimetres", "24 cm" + SQUARED, "square centimetres"),
    ("A cube has a total surface area of 150 square cm.", "150 cm" + SQUARED, "square cm"),
    # Things that must survive untouched.
    ("Solve for x: x/5 + 1/5 = 4/5.", "Solve for x", MULT),
    ("The box on the table", "box", MULT),
    ("Write your answer in cubic units, like cm^3.", "cubic units", "^"),
    ("A ribbon is 3/4 of a metre long.", "⁄", DIV),          # fraction stays a fraction
    ("Sam eats 2/8 and Jen eats 3/8.", "⁄", "/"),
    ("He sells 3 / 4 of the loaves.", "3 / 4 of", DIV),      # a quantity, not a sum
    ("A rope is 2 m 3 cm long.", "2 m 3 cm", CUBED),         # not cubic metres
    ("The 15 x 4 grid", f"15 {MULT} 4", " x "),
]

# Fraction glyphs need the Unicode font, which is registered on first render.
_register_fonts()

print("Notation")
for text, expect, forbid in NOTATION_CASES:
    out = _escape(text)
    check(expect in out and forbid not in out,
          text[:52], f"-> {out[:64]}")

# ---------------------------------------------------------------------------
# 2. Answer lines
# ---------------------------------------------------------------------------

ANSWER_LINE_CASES = [
    ("Simplify the fraction 4/8 to its simplest form.", ["Answer:"]),
    ("A baker has 24 loaves. He sells 1/4. How many are left?", ["Answer:"]),
    ("A box is 6 cm long. a) Find the volume. b) Find the surface area.",
     ["a) Answer:", "b) Answer:"]),
    ("Explain why doubling the height doubles the volume.", []),
    ("Draw a rectangle with an area of 12 square units.", []),
    ("Describe two ways to simplify a fraction.", []),
]

print("\nAnswer lines")
for text, expect in ANSWER_LINE_CASES:
    got = answer_line_labels(Question(question=text, answer="", working=""))
    check(got == expect, text[:52], f"-> {got}")

check(part_labels("a) one b) two c) three") == ["a", "b", "c"], "part markers found")
check(part_labels("A box 4 cm long.") == [], "no false part markers")

# ---------------------------------------------------------------------------
# 3. The answer key: what a parent marks from
#
# Every question/answer pair below is copied out of
# output/lleyton-accelerate-year-5-20260731-004316.pdf.
# ---------------------------------------------------------------------------

# (question, model answer, what the key should print)
KEY_ANSWER_CASES = [
    # Units the lesson insists on ("always cubed, write cm3") and the key drops.
    ("A cuboid has a length of 5 cm, a width of 3 cm, and a height of 2 cm. What "
     "is the volume of the cuboid in cubic centimetres?", "30", "30 cm³"),
    ("A rectangular prism has a base area of 24 square centimetres and a height "
     "of 5 cm. What is its volume in cubic centimetres?", "120", "120 cm³"),
    ("A box has a length of 6 cm, a width of 4 cm, and a height of 4 cm. "
     "Calculate its volume.", "96", "96 cm³"),
    ("A swimming pool is shaped like a rectangular prism with a length of 7 m, a "
     "width of 4 m, and a depth of 2 m. What is the volume of the pool in cubic "
     "metres?", "56", "56 m³"),
    ("A cube has an edge length of 4 cm. What is the volume of the cube?",
     "64", "64 cm³"),
    ("A rectangular garden bed has a volume of 60 cubic metres. The garden is 5 "
     "metres long and 4 metres wide. If the gardener wants to add soil so the "
     "depth increases by 1 metre, what will the new total volume of the garden "
     "bed be?", "75", "75 m³"),
    # A dimension, not a volume: the unit must not gain a cube.
    ("A rectangular prism has a volume of 100 cubic centimetres. Its length is 5 "
     "cm and its width is 5 cm. What is the height of the prism?", "4", "4 cm"),
    ("A box with a volume of 720 cubic cm has a length of 12 cm and a width of 6 "
     "cm. What is the height of the box in cm?", "10", "10 cm"),
    ("A container is 30 cm long and 20 cm wide. It contains 3000 cubic cm of "
     "water. What is the depth of the water in cm?", "5", "5 cm"),
    # Capacity, and a length asked for in words.
    ("A fish tank is 40 cm long, 25 cm wide, and 30 cm high. It is currently "
     "filled with water to a depth of 20 cm. How many more litres of water are "
     "needed to fill the tank to the top?", "10", "10 litres"),
    ("A rectangular trough is 1 metre long, 50 cm wide, and 20 cm deep. How many "
     "litres of water can it hold when full?", "100", "100 litres"),
    ("Sarah has a ribbon that is 12 metres long. She cuts off 1/3 of the ribbon "
     "to use for a gift. How many metres of ribbon does she have remaining?",
     "8", "8 metres"),
    # Counts, not measurements: nothing may be invented.
    ("A baker has 24 loaves of bread. He sells 1/4 of them in the morning. How "
     "many loaves does he have left?", "18", "18"),
    ("A cuboid is 4 cm long, 2 cm wide, and 5 cm high. How many 1 cm cubes are "
     "needed to build it?", "40", "40"),
    ("A juice carton contains 2 litres of juice. If you pour the juice into "
     "glasses that hold 250 ml each, how many glasses can you fill completely?",
     "8", "8"),
    ("A school garden has 40 plants. 2/5 of the plants are tomatoes and the rest "
     "are peppers. How many pepper plants are in the garden?", "24", "24"),
    # Fractions the key left unsimplified while the lesson said to simplify.
    ("A ribbon is 9/10 of a metre long. A piece measuring 5/10 of a metre is cut "
     "off. How long is the remaining piece of ribbon?", "4/10", "4/10 = 2/5"),
    ("Calculate 11/15 - 3/15 - 2/15.", "6/15", "6/15 = 2/5"),
    ("Evaluate 1/10 + 3/10 + 4/10.", "8/10", "8/10 = 4/5"),
    # Already in lowest terms, or asked for simplified: left exactly as given.
    ("Sarah has a piece of ribbon that is 8/10 of a metre long. She cuts off "
     "3/10 of a metre. Give your answer as a simplified fraction.", "1/2", "1/2"),
    ("Calculate 2/7 + 3/7.", "5/7", "5/7"),
    ("Solve for x: x/5 + 1/5 = 4/5.", "x = 3", "x = 3"),
    # An equivalent fraction is meant to be unsimplified: do not "correct" it.
    ("Find an equivalent fraction for 1/2 by multiplying the numerator and "
     "denominator by 3.", "3/6", "3/6"),
    ("Which fraction is equivalent to 3/9?", "1/3", "1/3"),
    # Prose answers are left alone.
    ("A pizza has 8 slices. Sam eats 2/8 and Jen eats 3/8. What fraction is left?",
     "3/8 of the pizza is left", "3/8 of the pizza is left"),
]

print("\nAnswer key")
for q, ans, want in KEY_ANSWER_CASES:
    got = key_answer(Question(question=q, answer=ans, working=""))
    check(got == want, f"{ans!r} -> {want!r}", f"got {got!r}")

check(simplify_fractions_in_answer("4/10 of a metre") == "4/10 of a metre (4/10 = 2/5)",
      "a fraction inside a phrase gets its simplest form alongside")
check(answer_unit("How many cubes are in the stack?") is None,
      "no unit invented when the question names none")

# The Final Challenge solutions were dense prose while every other solution was
# one operation per line. Both must come out the same shape.
CHALLENGE_WORKING = (
    "Volume of a rectangular prism = length x width x height. Volume = 40 cm x "
    "20 cm x 10 cm. 40 x 20 = 800. 800 x 10 = 8000.")
lines = solution_lines(CHALLENGE_WORKING)
check(len(lines) == 4, "dense prose solution split to one step per line",
      f"{len(lines)} lines")
check(solution_lines("Volume = 7 * 4 * 2\nVolume = 28 * 2\nVolume = 56") ==
      ["Volume = 7 * 4 * 2", "Volume = 28 * 2", "Volume = 56"],
      "a solution already one per line is unchanged")
check(solution_lines("Volume = 10 * 5 * 1.5.\nVolume = 50 * 1.5 = 75.") ==
      ["Volume = 10 * 5 * 1.5.", "Volume = 50 * 1.5 = 75."],
      "a decimal point is not a sentence end")
check(_escape("Volume = length x width x height.") ==
      f"Volume = length {MULT} width {MULT} height.",
      "x between dimension words is multiplication")

# ---------------------------------------------------------------------------
# 4. Render a booklet and read it back
# ---------------------------------------------------------------------------


def vq(text, answer="42", working="42", difficulty="medium", verified=True):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty),
        verified=verified)


def teaching(n_guided=2):
    we = WorkedExample(
        question="A box is 5 cm by 3 cm by 4 cm. What is its volume?",
        steps=["Multiply the length by the width: 5 x 3 = 15.",
               "Multiply that result by the height: 15 x 4 = 60.",
               "State the volume in cubic units: 60 cm^3."],
        answer="60 cubic centimetres")
    guided = [WorkedExample(
        question=f"Find the volume of a prism {i + 2} cm by 2 cm by 3 cm.",
        steps=["Multiply the length and width.", "Multiply by the height."],
        answer="36 cubic cm") for i in range(n_guided)]
    return SubtopicTeaching(
        intro_paragraphs=["Volume is the amount of space inside a 3D object. To "
                          "find the volume of a rectangular prism you multiply "
                          "its length, width and height together."],
        key_points=["Volume equals length times width times height.",
                    "Write your answer in cubic units, like cm^3."],
        worked_example=we, guided_examples=guided)


sections = []
for i in range(4):
    sections.append(SubtopicOutput(
        topic="Volume" if i > 1 else "Fractions",
        subtopic=f"Subtopic {i + 1}",
        teaching=teaching(1 + i % 2),
        # Answers deliberately bare, the way the shipped key gave them: the
        # renderer is the thing that has to put "cubic centimetres" back.
        questions=[vq(f"Question {i}.{j}: A box is {j + 2} cm long, 2 cm wide "
                      "and 3 cm high. What is its volume in cubic centimetres?",
                      answer=str((j + 2) * 6),
                      working=f"Volume = length x width x height. Volume = "
                              f"{j + 2} x 2 x 3. The volume is {(j + 2) * 6}.")
                   for j in range(3)],
        # Answers deliberately unsimplified, the way the shipped key gave them.
        homework_questions=[vq(f"Homework {i}.{j}: Calculate {j + 1}/12 + "
                               f"{j + 1}/12.",
                               answer=f"{2 * (j + 1)}/12", difficulty="easy")
                            for j in range(6)],
        estimated_minutes=10))
# One multi-part and one extended-response question, which must be laid out
# differently from the rest.
sections[-1].homework_questions.append(
    vq("A tank is 40 cm x 20 cm x 10 cm. a) Find its volume. b) Find its volume "
       "in litres."))
sections[-1].homework_questions.append(
    vq("Explain why the volume of a prism is the base area times the height."))

data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    program_label="Academic Accelerate", sections=sections,
    recap_questions=[vq("Calculate 15 * 4 + 7.", answer="67", difficulty="easy"),
                     vq("If 5x = 45, what is x?", answer="x = 9",
                        difficulty="easy")],
    challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its volume in "
                            "cubic metres?", answer="75", difficulty="hard")],
    recap_minutes=6, classwork_minutes=60, homework_minutes=105,
    challenge_minutes=18, total_minutes=170)

# The flat question order the formatter numbers in, with a marker unique to
# each question so the rendered page it landed on can be found again.
markers = ["Calculate 15", "If 5x = 45"]
for i, s in enumerate(sections):
    markers += [f"Question {i}.{j}" for j in range(len(s.questions))]
for i, s in enumerate(sections):
    for j, q in enumerate(s.homework_questions):
        markers.append(f"Homework {i}.{j}" if "Homework" in q.question.question
                       else ("A tank is 40 cm" if "tank" in q.question.question
                             else "Explain why the volume"))
markers.append("A pool is 10 m")

n_questions = (len(data.recap_questions) + len(data.challenge_questions)
               + sum(len(s.questions) + len(s.homework_questions) for s in data.sections))
assert len(markers) == n_questions, (len(markers), n_questions)

tmp = Path(tempfile.mkdtemp(prefix="folio-check-"))
tutor, student = render_booklet_pair(data, tmp / "booklet.pdf")

BODY_TOP = A4[1] - PAGE_MARGIN
BODY_BOTTOM = PAGE_MARGIN


def read(path):
    """Return (page texts, lowest body text y per page, (text, y) runs per page)."""
    reader = pypdf.PdfReader(str(path))
    texts, lows, runs = [], [], []
    for page in reader.pages:
        ys, seen = [], []

        def visit(text, cm_, tm, font_dict, font_size, ys=ys, seen=seen):
            if text.strip():
                y = cm_[5] + tm[5]
                if BODY_BOTTOM < y < BODY_TOP:      # skip header/footer chrome
                    ys.append(y)
                    seen.append((text.strip(), y))

        texts.append(page.extract_text(visitor_text=visit) or "")
        lows.append(min(ys) if ys else BODY_TOP)
        runs.append(seen)
    return texts, lows, runs


tutor_pages, tutor_lows, tutor_runs = read(tutor)
student_pages, student_lows, student_runs = read(student)
tutor_text = "\n".join(tutor_pages)
student_text = "\n".join(student_pages)

print("\nStudent copy")
check(student_copy_path(tmp / "booklet.pdf").name == "booklet-student.pdf",
      "student copy is a sibling file", student.name)
check("Answers" not in student_text and "Worked Solutions" not in student_text,
      "no answer key in the student copy")
check(TICK not in student_text and "verified" not in student_text,
      "no verification marks in the student copy")
check("Answers &" in tutor_text or "Worked Solutions" in tutor_text,
      "tutor copy still has the answer key")
check(len(student_pages) < len(tutor_pages), "student copy is shorter",
      f"{len(student_pages)} vs {len(tutor_pages)} pages")
for q in ("Homework 0.0", "Question 0.0", "Subtopic 4"):
    check(q in student_text, f"student copy keeps the questions ({q})")

print("\nVerification marks")
check(tutor_text.count(TICK) == n_questions,
      "one mark per answer in the key, none beside a question",
      f"{tutor_text.count(TICK)} marks, {n_questions} questions")

print("\nNotation in the rendered PDF")
for pages, label in ((tutor_pages, "tutor"), (student_pages, "student")):
    body = "\n".join(pages)
    check("*" not in body, f"no asterisk in the {label} copy")
    # A letter x used as a times sign: between two numbers, or between two
    # dimension words. "x = 9" and "5x = 45" are unknowns and must survive.
    stray_x = re.search(
        r"\d\s*[xX]\s*\d|(?:length|width|height|depth)\s+[xX]\s+", body)
    check(stray_x is None, f"no letter-x multiplication in the {label} copy",
          stray_x.group(0) if stray_x else "")
    check("cubic centimetres" not in body and "cubic cm" not in body,
          f"one spelling of volume units in the {label} copy")
    check("5x = 45" in body, f"unknowns survive in the {label} copy")

print("\nAnswer lines")
# One rule per question that wants a short answer, plus one for each worked and
# guided example, which print their own "Answer:".
all_questions = ([q.question for q in data.recap_questions]
                 + [q.question for s in sections for q in s.questions]
                 + [q.question for s in sections for q in s.homework_questions]
                 + [q.question for q in data.challenge_questions])
n_examples = sum(1 + len(s.teaching.guided_examples) for s in sections)
expected = sum(len(answer_line_labels(q)) for q in all_questions) + n_examples
check(student_text.count("Answer:") == expected, "an answer rule under every "
      "short-answer question", f"{student_text.count('Answer:')} vs {expected}")
check("a) Answer:" in student_text and "b) Answer:" in student_text,
      "multi-part question gets a rule per part")

# ---------------------------------------------------------------------------
# The answer key, in the rendered PDF
# ---------------------------------------------------------------------------

print("\nAnswer key in the rendered PDF")
key_start = next(i for i, p in enumerate(tutor_pages) if "Worked Solutions" in p)
key_text = "\n".join(tutor_pages[key_start:])
question_text = "\n".join(tutor_pages[:key_start])

# Units the lesson insists on, restored on bare numeric answers.
check("12 cm³" in key_text and "18 cm³" in key_text and "24 cm³" in key_text,
      "volume answers carry their unit in the key")
check("75 m³" in key_text, "the challenge volume answer carries its unit")
# 2/12, 4/12, 6/12, 8/12, 10/12 and 12/12 are all reducible.
simplified = key_text.count("=")
check(simplified >= 20, "unsimplified fraction answers show their lowest terms",
      f"{simplified} equivalences in the key")
check(_escape("2/12 = 1/6") in key_text and _escape("10/12 = 5/6") in key_text,
      "a fraction answer is shown both ways")

# Page references: every one must be the page the question is really on.
refs = {int(n): int(p) for n, p in
        re.findall(r"^(\d+)\. Answer:.*?\(p(\d+)\)", key_text, re.MULTILINE)}
check(len(refs) == n_questions, "every answer carries a page reference",
      f"{len(refs)} of {n_questions}")
wrong = []
for qnum, marker in enumerate(markers, 1):
    page = next((i + 1 for i, p in enumerate(tutor_pages[:key_start])
                 if marker in p), None)
    if refs.get(qnum) != page:
        wrong.append((qnum, marker, refs.get(qnum), page))
check(not wrong, "each page reference points at the question's real page",
      str(wrong[:3]))
check(f"(p{key_start})" not in key_text and "(p1)" not in key_text,
      "no reference points into the key itself or the cover")

print("\nScore line")
for text, label in ((student_text, "student"), (tutor_text, "tutor")):
    check(f"______ / {n_questions}" in text.replace("\n", " "),
          f"the {label} copy has a total to mark out of", str(n_questions))
    check("Marked by:" in text and "Date:" in text,
          f"the {label} copy names who marked it and when")
check("Warm-up Recap" in student_text and "Final Challenge" in student_text,
      "the score line breaks down by part")

print("\nHomework sessions")
plan = homework_session_plan(data)
n_hw = sum(len(s.homework_questions) for s in sections)
check(len(plan) >= 2, "homework is split into sittings", f"{len(plan)} sessions")
check(sum(p["count"] for p in plan) == n_hw,
      "every homework question belongs to exactly one session",
      f"{sum(p['count'] for p in plan)} of {n_hw}")
bands = re.findall(r"Session (\d+) of (\d+)", student_text)
check(len(bands) == len(plan), "one band printed per session",
      f"{len(bands)} bands, {len(plan)} planned")
check([int(a) for a, _ in bands] == list(range(1, len(plan) + 1)),
      "sessions are numbered in order", str(bands))
check(all(b == str(len(plan)) for _, b in bands), "each band says the total")
first_hw = n_questions - n_hw - len(data.challenge_questions) + 1
check(f"questions {first_hw} to" in student_text.replace("\n", " "),
      "the first session names the questions it covers", f"from {first_hw}")
check(homework_session_plan(BookletData(
    subject="Maths", year_level="Year 5", student_name="A",
    sections=[SubtopicOutput(topic="T", subtopic="S", questions=[],
                             homework_questions=[vq("One.")])])) == [],
      "a handful of homework questions is not split")
stranded = [i + 1 for i, page in enumerate(student_pages)
            if [ln for ln in page.splitlines() if ln.strip()][-1].startswith("Session ")]
check(not stranded, "no session band left at the foot of a page", str(stranded))

# A session that begins on the first question of a subtopic puts its band above
# that subtopic's heading, not between the heading and its questions.
aligned = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Lleyton",
    sections=[SubtopicOutput(
        topic="Volume", subtopic=f"Part {k + 1}", questions=[],
        homework_questions=[
            vq(f"Set {k} item {j}: a container is {j + 2} cm long, 5 cm wide "
               "and 4 cm high. What is its volume in cubic centimetres?",
               difficulty="easy") for j in range(5)]) for k in range(4)])
aligned_plan = homework_session_plan(aligned)
_, aligned_student = render_booklet_pair(aligned, tmp / "aligned.pdf")
aligned_text = "\n".join(read(aligned_student)[0])
boundary_section = None
for n, p in enumerate(aligned_plan[1:], 2):
    if p["start"] % 5 == 0:
        boundary_section = (n, p["start"] // 5 + 1)
        break
check(boundary_section is not None, "a session boundary falls on a subtopic",
      str([p["start"] for p in aligned_plan]))
if boundary_section:
    n, part = boundary_section
    # From the Homework band on: the Class Work half lists the same subtopic
    # names higher up the booklet.
    hw_text = aligned_text[aligned_text.index("Split into"):]
    check(hw_text.index(f"Session {n} of") < hw_text.index(f"Part {part}"),
          "the session band sits above the subtopic heading it starts on",
          f"session {n} / Part {part}")

print("\nWarm-up working space")


def gap_below(runs, needle):
    """Vertical distance from a question's line to the next line under it."""
    for page in runs:
        for i, (text, y) in enumerate(page):
            if needle in text:
                below = [yy for _, yy in page[i + 1:] if yy < y]
                return (y - max(below)) / cm if below else None
    return None


warm = gap_below(student_runs, "Calculate 15")
classwork = gap_below(student_runs, "Question 0.0")
check(warm is not None and warm > 2.0,
      "a warm-up question gets more than one line to work in",
      f"{warm:.1f}cm" if warm else "not found")
check(warm is not None and classwork is not None and warm >= classwork * 0.8,
      "the warm-up is spaced like the rest of the booklet",
      f"warm-up {warm:.1f}cm vs class work {classwork:.1f}cm")

print("\nPage fill")
tails = [(low - BODY_BOTTOM) / cm for low in student_lows]
# Page 1 is the cover and the last page ends the booklet. A page that runs into
# a deliberate part break may stop up to the break threshold early; any other
# page stopping more than 6cm short means a page was abandoned.
boundary = {i for i in range(len(student_pages) - 1)
            if "Homework" in "\n".join(student_pages[i + 1].splitlines()[:4])}
worst_boundary = (0, 0.0)
worst_plain = (0, 0.0)
for i, tail in list(enumerate(tails))[1:-1]:
    slot = worst_boundary if i in boundary else worst_plain
    if tail > slot[1]:
        if i in boundary:
            worst_boundary = (i + 1, tail)
        else:
            worst_plain = (i + 1, tail)
check(worst_plain[1] < 6.0, "no page abandoned more than 6cm early",
      f"worst is page {worst_plain[0]} at {worst_plain[1]:.1f}cm")
check(worst_boundary[1] <= HOMEWORK_MIN_START_CM,
      "a part break never throws away more than its threshold",
      f"worst is page {worst_boundary[0]} at {worst_boundary[1]:.1f}cm")
check(sum(tails[1:-1]) / max(1, len(tails) - 2) < 4.0,
      "pages are worked down the page on average",
      f"mean tail {sum(tails[1:-1]) / max(1, len(tails) - 2):.1f}cm")

print("\nPage fill under stress (200 questions of varied length)")
import random                                                   # noqa: E402

random.seed(7)
stress_qs = []
for i in range(200):
    words = " ".join(["word"] * random.randint(4, 70))
    parts = " a) one b) two" if i % 9 == 0 else ""
    stress_qs.append(vq(f"Question {i}: {words}{parts}",
                        difficulty=random.choice(["easy", "medium", "hard"])))
stress = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Stress",
    sections=[SubtopicOutput(topic="T", subtopic="S", questions=stress_qs)])
stress_path, _ = render_booklet_pair(stress, tmp / "stress.pdf")
stress_pages, stress_lows, _ = read(stress_path)
orphans = []
for i, page in enumerate(stress_pages):
    body = [ln for ln in page.splitlines()
            if ln.strip() and not ln.startswith("Page ") and "Mathematics  |" not in ln]
    if body and body[0].strip().endswith("Answer:") and "Question" not in body[0]:
        orphans.append(i + 1)
check(not orphans, "no working space separated from its question", str(orphans))
stress_tails = [(low - BODY_BOTTOM) / cm for low in stress_lows][1:-1]
check(max(stress_tails) < 6.0, "no page abandoned early under stress",
      f"worst {max(stress_tails):.1f}cm")

print("\nClosing")
check("That is the end of the booklet, Lleyton." in student_text.replace("\n", " "),
      "student copy closes by name")
check("That is the end of the booklet, Lleyton." in tutor_text.replace("\n", " "),
      "tutor copy closes by name")
closing_page = next(i for i, p in enumerate(student_pages)
                    if "end of the booklet" in p.replace("\n", " "))
check(closing_page >= len(student_pages) - 2,
      "the closing note is the last thing before the score line",
      f"page {closing_page + 1} of {len(student_pages)}")
check("Answers" not in student_pages[closing_page],
      "nothing follows the closing note but the score line")

print("\nTiming")
t = booklet_timing(data)
check(t["classwork_minutes"] > sum(len(s.questions) for s in sections) * 2.5,
      "class work is charged for its teaching, not only its questions",
      f"{t['classwork_minutes']} min")
check(t["homework_minutes"] < t["classwork_minutes"],
      "repetition homework is not charged the classwork rate",
      f"{t['homework_minutes']} min")
check(f"About {t['classwork_minutes']} min" in tutor_text,
      "the printed class work estimate is the recomputed one")

print("\nExam paper (shares these styles)")
exam = ExamPaper(
    subject="Mathematics Methods", year_level="Year 12", student_name="Lleyton",
    unit="Units 3 and 4",
    sections=[
        ExamSection(name="Section One: Calculator-free", calculator_allowed=False,
                    description="Answer all questions. Show your working.",
                    working_minutes=50,
                    questions=[ValidatedQuestion(
                        question=Question(question=f"Differentiate y = {k} * x^2.",
                                          answer=f"{2 * k}x", working="Power rule.",
                                          marks=k + 1),
                        verified=True) for k in range(1, 6)]),
        ExamSection(name="Section Two: Calculator-assumed", calculator_allowed=True,
                    working_minutes=100,
                    questions=[ValidatedQuestion(
                        question=Question(question=f"A tank holds {k} cubic metres. "
                                                   "a) Find the depth. b) Find the rate.",
                                          answer="See key", working="Integrate.",
                                          marks=k),
                        verified=k % 2 == 0) for k in range(1, 5)]
                    + [ValidatedQuestion(
                        question=Question(
                            question="A tank drains 6 cubic metres in 12 minutes. "
                                     "What is the depth of the remaining water in "
                                     "metres?",
                            answer="2/8", working="Solve.", marks=3),
                        verified=False)]),
    ],
    materials=["To be provided by the supervisor: this Question/Answer booklet."])
exam_path = render_exam_pdf(exam, tmp / "exam.pdf")
exam_pages, _, _ = read(exam_path)
exam_text = "\n".join(exam_pages)
check(len(exam_pages) >= 3, "exam paper renders", f"{len(exam_pages)} pages")
check("Marking Key" in exam_text, "marking key present")
check("Section One: Calculator-free" in exam_text, "sections present")
check(exam_text.count("mark") >= 5, "marks printed")
n_exam_verified = sum(1 for s in exam.sections for q in s.questions if q.verified)
check(exam_text.count(TICK) == n_exam_verified,
      "verified answers marked in the exam key only",
      f"{exam_text.count(TICK)} marks, {n_exam_verified} verified")
check("Practice Examination" in exam_text, "exam cover intact")
check(f"150 m{CUBED}" not in exam_text and "cubic metres" not in exam_text,
      "exam text is normalised too")
# The booklet key's answer tidying must not reach the exam marking key: senior
# answers are marked exactly as the marking scheme states them.
check(_escape("2/8") in exam_text and _escape("2/8 = 1/4") not in exam_text,
      "the exam marking key prints answers exactly as given")
check(answer_unit("A tap fills a tank. What is the flow rate in litres per "
                  "minute?") is None,
      "no half a compound unit is guessed for a rate")

print(f"\nPDFs written to {tmp}")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nAll checks passed.")
