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
  * the closing note
  * render_exam_pdf still renders (it shares these styles)

Usage:  python scripts/check_booklet_render.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf                                                    # noqa: E402
from reportlab.lib.pagesizes import A4                          # noqa: E402
from reportlab.lib.units import cm                              # noqa: E402

from booklet_gen.formatter import (                             # noqa: E402
    HOMEWORK_MIN_START_CM, PAGE_MARGIN, _escape, _register_fonts,
    answer_line_labels, part_labels, render_booklet_pair, render_exam_pdf,
    student_copy_path)
from booklet_gen.schemas import (                               # noqa: E402
    BookletData, ExamPaper, ExamSection, Question, SubtopicOutput,
    SubtopicTeaching, ValidatedQuestion, WorkedExample)
from booklet_gen.timing import booklet_timing                   # noqa: E402

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
# 3. Render a booklet and read it back
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
        questions=[vq(f"Question {i}.{j}: what is {j + 2} * 4 cubic centimetres?")
                   for j in range(3)],
        homework_questions=[vq(f"Homework {i}.{j}: simplify {j + 2}/8.",
                               difficulty="easy") for j in range(6)],
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
    recap_questions=[vq("Calculate 15 * 4 + 7.", difficulty="easy"),
                     vq("If 5x = 45, what is x?", difficulty="easy")],
    challenge_questions=[vq("A pool is 10 m x 5 m x 1.5 m. What is its volume in "
                            "cubic metres?", difficulty="hard")],
    recap_minutes=6, classwork_minutes=60, homework_minutes=105,
    challenge_minutes=18, total_minutes=170)

n_questions = (len(data.recap_questions) + len(data.challenge_questions)
               + sum(len(s.questions) + len(s.homework_questions) for s in data.sections))

tmp = Path(tempfile.mkdtemp(prefix="folio-check-"))
tutor, student = render_booklet_pair(data, tmp / "booklet.pdf")

BODY_TOP = A4[1] - PAGE_MARGIN
BODY_BOTTOM = PAGE_MARGIN


def read(path):
    """Return (page texts, lowest body text y per page)."""
    reader = pypdf.PdfReader(str(path))
    texts, lows = [], []
    for page in reader.pages:
        ys = []

        def visit(text, cm_, tm, font_dict, font_size, ys=ys):
            if text.strip():
                y = cm_[5] + tm[5]
                if BODY_BOTTOM < y < BODY_TOP:      # skip header/footer chrome
                    ys.append(y)

        texts.append(page.extract_text(visitor_text=visit) or "")
        lows.append(min(ys) if ys else BODY_TOP)
    return texts, lows


tutor_pages, tutor_lows = read(tutor)
student_pages, student_lows = read(student)
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
    check(" x " not in body.replace("x " + MULT, ""), f"no letter-x multiplication in the {label} copy")
    check("cubic centimetres" not in body and "cubic cm" not in body,
          f"one spelling of volume units in the {label} copy")
    check("5x = 45" in body, f"unknowns survive in the {label} copy")

print("\nAnswer lines")
# One rule per question, plus one for each worked/guided example answer, plus
# the extra part; minus the extended-response question, which gets none.
n_examples = sum(1 + len(s.teaching.guided_examples) for s in sections)
expected = n_questions - 1 + 1 + n_examples
check(student_text.count("Answer:") == expected, "an answer rule under every "
      "short-answer question", f"{student_text.count('Answer:')} vs {expected}")
check("a) Answer:" in student_text and "b) Answer:" in student_text,
      "multi-part question gets a rule per part")

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
stress_pages, stress_lows = read(stress_path)
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
check(student_text.rstrip().endswith("go over again."),
      "the closing note is the last thing in the student copy")

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
                        verified=k % 2 == 0) for k in range(1, 5)]),
    ],
    materials=["To be provided by the supervisor: this Question/Answer booklet."])
exam_path = render_exam_pdf(exam, tmp / "exam.pdf")
exam_pages, _ = read(exam_path)
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

print(f"\nPDFs written to {tmp}")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nAll checks passed.")
