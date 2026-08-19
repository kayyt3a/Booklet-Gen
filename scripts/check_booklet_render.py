#!/usr/bin/env python3
"""Check what the booklet formatter actually puts on the page.

Every case here comes from a real generated booklet. The renderer is run for
real, the PDF is read back with pypdf, and the assertions are made against the
text layer, the fonts and the geometry, not against the code that produced them.

Covers:
  * one booklet with the key at the back: no verification mark beside a
    question, and nothing from the key leaking into the pages worked on
  * reading passages: laid out before the questions that refer to them, printed
    once, and never split from their first question by a page break
  * spelling: a wordless dictation test at the front, the list at the back, and
    the words to call out printed only in the key
  * the mini-lesson: the term taught set in bold, specimens quoted
  * notation: one symbol per operation, and unknowns left alone
  * an "Answer:" rule under every question that wants a short answer
  * page fill: no page abandoned two thirds empty
  * the closing note and the score line
  * the answer key: fractions in lowest terms, units restored, one step per
    line everywhere, a page reference back to the question, and numbering that
    follows the printed order
  * homework split into sittings, and room to work in the warm-up
  * render_exam_pdf still renders (it shares these styles)

Usage:  python scripts/check_booklet_render.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf                                                  # noqa: E402
import pypdf                                                    # noqa: E402
from pydantic import BaseModel, Field                           # noqa: E402
from reportlab.lib import colors                                # noqa: E402
from reportlab.lib.pagesizes import A4                          # noqa: E402
from reportlab.lib.units import cm                              # noqa: E402

from booklet_gen import schemas as S                            # noqa: E402
from booklet_gen.formatter import (                             # noqa: E402
    CHROME_MARGIN, HOMEWORK_MIN_START_CM, MULTIPLY, PAGE_MARGIN,
    _CHALLENGE_MIN_START_CM,
    SPELLING_TEST_SPACES,
    _escape, _lesson_html, _make_styles, _prettify_fractions, _register_fonts,
    answer_line_labels, answer_unit, written_response_rules,
    apply_bold_markup, key_answer, ordered_questions, part_counts, part_labels,
    passage_groups, question_numbering, quote_inline_examples, render_exam_pdf,
    render_pdf, simplify_fractions_in_answer, solution_lines,
    spelling_test_spaces)
from booklet_gen.schemas import (                               # noqa: E402
    ExamPaper, ExamSection, SubtopicTeaching, ValidatedQuestion, WorkedExample)
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
# 0. Schema shims
#
# Passage, Question.passage_id, SubtopicOutput.passages and the spelling models
# are a contract another agent is landing in schemas.py. The formatter reads
# every one of them through getattr and does nothing when they are missing, so
# it works either way; this check has to be able to build the objects either
# way too. When the real fields exist these shims collapse to the real classes.
# ---------------------------------------------------------------------------

if hasattr(S, "Passage"):
    Passage = S.Passage
else:
    class Passage(BaseModel):                                   # type: ignore[no-redef]
        id: str
        title: Optional[str] = None
        paragraphs: List[str] = Field(default_factory=list)

if hasattr(S, "SpellingList"):
    SpellingList, SpellingTest = S.SpellingList, S.SpellingTest
else:
    class SpellingList(BaseModel):                              # type: ignore[no-redef]
        words: List[str] = Field(default_factory=list)

    class SpellingTest(BaseModel):                              # type: ignore[no-redef]
        words: List[str] = Field(default_factory=list)
        from_week: Optional[int] = None

if "passage_id" in S.Question.model_fields:
    Question = S.Question
else:
    class Question(S.Question):                                 # type: ignore[no-redef]
        passage_id: Optional[str] = None

if "passages" in S.SubtopicOutput.model_fields:
    SubtopicOutput = S.SubtopicOutput
else:
    class SubtopicOutput(S.SubtopicOutput):                     # type: ignore[no-redef]
        passages: List[Passage] = Field(default_factory=list)

if "spelling_list" in S.BookletData.model_fields:
    BookletData = S.BookletData
else:
    class BookletData(S.BookletData):                           # type: ignore[no-redef]
        spelling_list: Optional[SpellingList] = None
        spelling_test: Optional[SpellingTest] = None

print(f"Schema contract: {'live' if Question is S.Question else 'shimmed locally'}")


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
    # The bracket form used to survive with a letter x, so the rule a child is
    # taught the formula from printed "2 x (length + width)" three lines above
    # "length × width" in the same box.
    ("Perimeter = 2 x (length + width)", f"2 {MULT} (", " x "),
    ("Check: 2 x (8 + 2) = 20", f"2 {MULT} (8 + 2)", " x "),
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

print("\nNotation")
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

# An extended response used to get no rule at all: the longest questions in the
# booklet had the least structure on the page, a silent gap of white that reads
# as a printing fault and tells a child neither where to start nor how much is
# wanted. They get ruled lines now, sized to what is asked for.
# Fractions were set with Unicode superscript and subscript digits, which are
# about 56 percent the height of a normal digit. At the worked-example size
# that printed a denominator at the visual equivalent of 5.3pt, in a booklet
# whose first topic is comparing fractions. The slash still leans; the digits
# are full size.
# Contrast. The page number was the worst of these and it is the one that
# matters most: every answer in the key ends "(p8)", and that is the only way
# back to the question. The most-used wayfinding element in the booklet was its
# lowest-contrast text, at 3.54:1 against a 4.5:1 standard.
print("\nContrast")


def _relative_luminance(colour) -> float:
    def f(x):
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return (0.2126 * f(colour.red) + 0.7152 * f(colour.green)
            + 0.0722 * f(colour.blue))


def contrast(colour, background=1.0) -> float:
    lo, hi = sorted((_relative_luminance(colour), background))
    return (hi + 0.05) / (lo + 0.05)


_register_fonts()
_styles = _make_styles()
# Styles set in white or near-white are printed on a coloured band and are
# checked against the band, not against the page.
ON_A_BAND = {"part_band", "part_band_sub", "subject_band", "passage_label",
             "answers_heading"}
faint = []
for name, style in sorted(_styles.items()):
    colour = getattr(style, "textColor", None)
    if colour is None or name in ON_A_BAND:
        continue
    if _relative_luminance(colour) > 0.6:      # white text, lives on a band
        continue
    r = contrast(colour)
    if r < 4.5:
        faint.append((name, round(r, 2)))
check(not faint, "every text colour on the page meets AA against white",
      str(faint))
# The passage label prints on the reading box's cream, not on white.
CREAM = colors.HexColor("#FDF8EF")
label_r = contrast(_styles["passage_label"].textColor,
                   _relative_luminance(CREAM))
check(label_r >= 4.5, "and the READ THIS label meets it against the cream box",
      f"{label_r:.2f}:1")

# Chrome has to clear a home printer's unprintable band at the foot of the
# sheet: HP DeskJet 12.7mm, Epson EcoTank 14.0mm. Below that, printing at
# actual size drops the page number the key's back-references depend on, and
# printing to fit rescales the whole sheet and shrinks every ruled line.
check(CHROME_MARGIN / cm * 10 >= 15.0,
      "the page number clears a home printer's dead band at the foot",
      f"{CHROME_MARGIN / cm * 10:.1f} mm from the sheet edge")

print("\nFractions")
_register_fonts()
SUB_SUP_DIGITS = "⁰¹⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉"      # ² and ³ are real exponents, cm², m³
for text, want in [("Which is larger, 3/4 or 5/8?", "3⁄4"),
                   ("Rewrite 3/4 with a denominator of 8.", "3⁄4"),
                   ("Calculate 11/12 - 5/12.", "11⁄12")]:
    got = _prettify_fractions(text)
    check(want in got, f"{text[:44]!r} sets a fraction", f"-> {got}")
    check(not any(ch in got for ch in SUB_SUP_DIGITS),
          "and does it at full digit size", f"-> {got}")
check("2/0" in _prettify_fractions("Calculate 2/0."),
      "a zero denominator is left exactly as written")

print("\nWriting lines for a written answer")
WRITTEN_RULE_CASES = [
    # (question, model answer, expected rules)
    ("Explain how you know 0.7 is larger than 0.68.", "0.7 is larger", 4),
    ("Write two sentences about the moths. Use a simile.", "The moths...", 4),
    ("Write a short paragraph about your favourite place.", "Any answer", 5),
    ("Write a sentence using a simile.", "The train was packed", 2),
    # A drawing needs clear space, not ruling.
    ("Draw a rectangle with an area of 12 square units.", "A 3 by 4", 0),
    ("Shade three quarters of the circle.", "Three parts shaded", 0),
    # Working is laid out down the page, not along a line.
    ("Find the volume. Show your working.", "60 cm3", 0),
    # Arithmetic keeps its single Answer rule.
    ("Calculate 15 x 4 + 7.", "67", 0),
    ("A rectangle is 12 cm long and 4 cm wide. Find its perimeter.", "32 cm", 0),
    # Phrased as neither, but the key answers it in prose: most comprehension
    # questions look like this, and they were the ones getting one rule at the
    # foot of a gap sized for arithmetic working.
    ("What can you infer about the woman with the newspaper?",
     "She is a regular on this route who knows the timetable better than Tess", 3),
    ("Why does the writer end the article that way?",
     "Because animals such as the pygmy possum depend on the moths arriving, "
     "so the ending points past the moths themselves", 4),
]
for text, answer, expect in WRITTEN_RULE_CASES:
    q = Question(question=text, answer=answer, working="")
    got = written_response_rules(q)
    check(got == expect, f"{text[:48]!r}", f"{got} rules, expected {expect}")
    check(not (got and answer_line_labels(q)),
          "and it does not also carry an Answer rule",
          f"rules={got} labels={answer_line_labels(q)}")

# ---------------------------------------------------------------------------
# 3. Mini-lesson prose
#
# Both cases are the owner reading a Year 3 English booklet: the term being
# taught set in the same weight as everything else, and two specimens run into
# a sentence so they do not read as specimens.
# ---------------------------------------------------------------------------

print("\nLesson: specimens quoted")
QUOTE_CASES = [
    ("Match the verb to the subject, like saying the dog runs instead of the dog run.",
     'Match the verb to the subject, like saying "the dog runs" instead of '
     '"the dog run".'),
    ("Try writing enormous rather than big.",
     'Try writing "enormous" rather than "big".'),
    # Nothing to anchor on: left exactly as written.
    ("Use a comma instead of a full stop.", "Use a comma instead of a full stop."),
    ("You could say it in your own words instead.",
     "You could say it in your own words instead."),
    # Already quoted, so not quoted twice.
    ('Like saying "the dog runs" instead of "the dog run".',
     'Like saying "the dog runs" instead of "the dog run".'),
    # Too long to be a specimen: the boundaries would be guesswork.
    ("Try writing a sentence that describes the whole scene in detail rather "
     "than a short one.",
     "Try writing a sentence that describes the whole scene in detail rather "
     "than a short one."),
]
for text, want in QUOTE_CASES:
    got = quote_inline_examples(text)
    check(got == want, text[:52], f"-> {got}")

print("\nLesson: the term taught")
# The lesson writer wraps a newly introduced term in double asterisks. The
# formatter turns that into a bold run, and must never turn it into a broken
# tag or a multiplication sign.
BOLD_CASES = [
    # The happy path.
    ("**Alliteration** is when words start with the same sound.",
     "<b>Alliteration</b> is when words start with the same sound."),
    ("A **synonym** and an **antonym** are opposites in job.",
     "A <b>synonym</b> and an <b>antonym</b> are opposites in job."),
    ("**two words** here", "<b>two words</b> here"),
    # Malformed markup. None of these may produce a tag, and none may leave an
    # asterisk on the page: ReportLab raises on broken markup.
    ("An **unclosed term is still readable.",
     "An unclosed term is still readable."),
    ("A stray ** in the middle.", "A stray  in the middle."),
    ("****", ""),
    ("**", ""),
    ("***overdone***", "<b>overdone</b>"),
    ("**a **b** c", "<b>a b</b> c"),
    ("** spaced out **", " spaced out "),
    # Nothing to do.
    ("Plain prose with no markup.", "Plain prose with no markup."),
]
for text, want in BOLD_CASES:
    got = apply_bold_markup(text)
    ok = got == want and got.count("<b>") == got.count("</b>") and "*" not in got
    check(ok, repr(text[:44]), f"-> {got!r}")

# The collision: the multiplication normaliser rewrites a single asterisk
# between operands, and must leave the emphasis markers alone.
check(_lesson_html("Work out 4 * 5 before you read on.")
      == f"Work out 4 {MULT} 5 before you read on.",
      "a lone asterisk between numbers is still multiplication")
check(_lesson_html("**Volume** is 4 * 5 * 2 for this prism.")
      == f"<b>Volume</b> is 4 {MULT} 5 {MULT} 2 for this prism.",
      "bold markup and multiplication in the same sentence")
check(_lesson_html("A **synonym** is a word.") == "A <b>synonym</b> is a word.",
      "double asterisks are never read as a multiplication sign")
check("<b>" in _lesson_html("**terms** &amp; more <angles>"),
      "escaping runs first, so the tags inserted here are the only markup")
check(_escape("**word**").count("*") == 4,
      "the escape step on its own leaves the markers for this pass")

# ---------------------------------------------------------------------------
# 4. Passage grouping
# ---------------------------------------------------------------------------

print("\nPassage grouping")


def pq(text, passage_id=None, answer="42", working="42", difficulty="medium",
       verified=True):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=working,
                          difficulty=difficulty, passage_id=passage_id),
        verified=verified)


P1 = Passage(id="p1", title="The Lost Kitten", paragraphs=["One.", "Two."])
P2 = Passage(id="p2", title="Storm at Sea", paragraphs=["Three."])
GROUP_QS = [pq("A", "p1"), pq("B"), pq("C", "p1"), pq("D", "p2"), pq("E", "p1"),
            pq("F", "nope")]
groups = passage_groups(GROUP_QS, {"p1": P1, "p2": P2})
shape = [(g[0].id if g[0] else None, [q.question.question for q in g[1]])
         for g in groups]
check(shape == [("p1", ["A", "C", "E"]), (None, ["B"]), ("p2", ["D"]),
                (None, ["F"])],
      "questions gather under their passage, at its first mention", str(shape))
check(sum(1 for g, _ in shape if g == "p1") == 1,
      "a passage is grouped once, not once per question")
check([q.question.question for q in
       ordered_questions(GROUP_QS, {"p1": P1, "p2": P2})]
      == ["A", "C", "E", "B", "D", "F"],
      "the flat order the key must follow")
check(passage_groups(GROUP_QS, {}) == [(None, GROUP_QS)],
      "no passages defined means no regrouping at all")
check([q.question.question for q in ordered_questions([pq("A"), pq("B")], {})]
      == ["A", "B"], "a maths section is untouched")

# ---------------------------------------------------------------------------
# 5. The answer key: what a parent marks from
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
# 6. Render a booklet and read it back
# ---------------------------------------------------------------------------


def vq(text, answer="42", working="42", difficulty="medium", verified=True):
    return pq(text, None, answer, working, difficulty, verified)


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
booklet = render_pdf(data, tmp / "booklet.pdf")

BODY_TOP = A4[1] - PAGE_MARGIN
BODY_BOTTOM = PAGE_MARGIN


def ink_lows(path) -> list:
    """The lowest mark on each page, drawn or typed, in ReportLab coordinates.

    Text alone stopped being a fair measure of how far down the page a booklet
    reaches the moment every question got a drawn working panel. A question
    with four ruled writing lines puts its last WORDS three centimetres above
    the foot of the room it was actually given, and a squared panel is ink with
    no text in it at all, so a text-only tail calls a page abandoned when the
    child has in fact been handed space right down to the margin.

    Read with pymupdf rather than pypdf because the drawn paths are what is
    being measured here, and pypdf gives no geometry for those.
    """
    doc = pymupdf.open(str(path))
    out = []
    for page in doc:
        h = page.rect.height
        ys = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    ys.append(h - span["bbox"][3])
        for drawing in page.get_drawings():
            ys.append(h - drawing["rect"].y1)
        ys = [y for y in ys if BODY_BOTTOM < y < BODY_TOP]
        out.append(min(ys) if ys else BODY_TOP)
    doc.close()
    return out


def read(path):
    """(page texts, lowest body mark per page, (text, y) runs, bold runs, rules)."""
    reader = pypdf.PdfReader(str(path))
    texts, lows, runs, bolds, rules = [], [], [], [], []

    for page in reader.pages:
        ys, seen, bold = [], [], []

        def visit(text, cm_, tm, font_dict, font_size, ys=ys, seen=seen, bold=bold):
            if not text.strip():
                return
            y = cm_[5] + tm[5]
            if BODY_BOTTOM < y < BODY_TOP:      # skip header/footer chrome
                ys.append(y)
                seen.append((text.strip(), y))
                if "Bold" in str((font_dict or {}).get("/BaseFont", "")):
                    bold.append((text.strip(), y))

        texts.append(page.extract_text(visitor_text=visit) or "")
        lows.append(min(ys) if ys else BODY_TOP)
        runs.append(seen)
        bolds.append(bold)
        try:
            data_ = page.get_contents().get_data()
        except Exception:
            data_ = b""
        # Every horizontal rule the tables draw is a "moveto lineto stroke".
        rules.append(len(re.findall(rb"\bl\s+S\b", data_)))
    return texts, ink_lows(path), runs, bolds, rules


def key_page(pages) -> int:
    return next(i for i, p in enumerate(pages) if "Worked Solutions" in p)


pages, lows, runs, bolds, rules = read(booklet)
text = "\n".join(pages)
key_start = key_page(pages)
question_pages = pages[:key_start]
question_text = "\n".join(question_pages)
key_text = "\n".join(pages[key_start:])

print("\nOne booklet, key at the back")
check(not (tmp / "booklet-student.pdf").exists(),
      "render_pdf writes one file and no second copy")
check("Answers &" in text or "Worked Solutions" in text, "the key is present")
check(key_start > 0, "the key is at the back", f"key starts on page {key_start + 1}")
# Printed double-sided, sheet n carries pages 2n-1 and 2n. If the key starts on
# an even page it is the back of the last page the student wrote on, and the
# first thing on the key is the spelling dictation list this booklet takes
# deliberate trouble to keep out of the child's hands. Turning the sheet over
# handed it straight back.
check((key_start + 1) % 2 == 1,
      "and starts on the front of a fresh sheet, not the back of the last one",
      f"page {key_start + 1}")
blank_versos = [i + 1 for i, p in enumerate(pages) if "intentionally blank" in p]
check(all(b == key_start for b in blank_versos),
      "any blank verso sits immediately before the key and nowhere else",
      str(blank_versos))
check(len(blank_versos) <= 1, "and there is at most one of them",
      str(blank_versos))
for q in ("Homework 0.0", "Question 0.0", "Subtopic 4"):
    check(q in question_text, f"the questions come first ({q})")

# The cover used to promise more than the booklet does. "Symbolically
# verified" went on every all-maths cover regardless of what ran, and most of
# a primary maths booklet ("Round 468 to the nearest hundred", "Explain his
# mistake") is decided by the LLM judge, not by SymPy. Claiming an algebra
# engine stood behind "explain his mistake" is what a sceptical tutor
# screenshots. Say it again only when the mark itself distinguishes the two.
cover = " ".join(pages[0].split())    # the claim wraps across lines
check("symbolically" not in cover.lower(),
      "the cover claims no symbolic proof it cannot show per answer")
# "checked for accuracy" was an absolute, and a booklet with an unticked
# answer in its key contradicts it in its own notation. The claim now tracks
# what the key actually shows, so this asserts the honest wording rather than
# the old blanket one.
check("has been checked" in cover,
      "it claims only the checking the key can evidence")
# "Show your working" belongs on a maths cover only; there is no working in an
# English booklet.
check("show your working" in cover,
      "a maths booklet still asks for working")
# The English half of that pair is checked with the English booklet below.

print("\nVerification marks")
# The cover is exempt: it carries the legend explaining what the mark in the
# key means, and saying the word there is the point. What must not happen is a
# mark appearing beside a question the child has not answered yet.
work_text = "\n".join(question_pages[1:])
check(TICK not in work_text and "verified" not in work_text,
      "no verification mark beside an unattempted question")
check(key_text.count(TICK) == n_questions,
      "one mark per answer in the key", f"{key_text.count(TICK)} of {n_questions}")

print("\nNotation in the rendered PDF")
check("*" not in text, "no asterisk anywhere in the booklet")
# A letter x used as a times sign: between two numbers, or between two
# dimension words. "x = 9" and "5x = 45" are unknowns and must survive.
stray_x = re.search(r"\d\s*[xX]\s*\d|(?:length|width|height|depth)\s+[xX]\s+", text)
check(stray_x is None, "no letter-x multiplication",
      stray_x.group(0) if stray_x else "")
check("cubic centimetres" not in text and "cubic cm" not in text,
      "one spelling of volume units")
check("5x = 45" in text, "unknowns survive")

print("\nAnswer lines")
# One rule per question that wants a short answer, plus one for each worked and
# guided example, which print their own "Answer:".
all_questions = ([q.question for q in data.recap_questions]
                 + [q.question for s in sections for q in s.questions]
                 + [q.question for s in sections for q in s.homework_questions]
                 + [q.question for q in data.challenge_questions])
n_examples = sum(1 + len(s.teaching.guided_examples) for s in sections)
expected = sum(len(answer_line_labels(q)) for q in all_questions) + n_examples
check(question_text.count("Answer:") == expected, "an answer rule under every "
      "short-answer question", f"{question_text.count('Answer:')} vs {expected}")
check("a) Answer:" in question_text and "b) Answer:" in question_text,
      "multi-part question gets a rule per part")

# ---------------------------------------------------------------------------
# The answer key, in the rendered PDF
# ---------------------------------------------------------------------------

print("\nAnswer key in the rendered PDF")

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
# Read in order, not into a dict keyed by the printed number: numbering now
# restarts at each reading and each subtopic, so several answers print as "3"
# and keying by the number would silently collapse them.
#
# The tick and the page reference are set in their own right-aligned column
# now, so that they line up down the page instead of trailing whatever length
# the answer happened to be. That puts them in a separate text run from the
# answer, which is why this reads across the line break (re.S) rather than
# expecting "1. Answer: 67 (p2)" on one extracted line.
refs = [(int(n), int(p)) for n, p in
        re.findall(r"^(\d+)\. Answer:.*?\(p(\d+)\)", key_text,
                   re.MULTILINE | re.DOTALL)]
check(len(refs) == n_questions, "every answer carries a page reference",
      f"{len(refs)} of {n_questions}")
wrong = []
for i, marker in enumerate(markers):
    page = next((j + 1 for j, p in enumerate(question_pages) if marker in p), None)
    got = refs[i][1] if i < len(refs) else None
    if got != page:
        wrong.append((marker, got, page))
check(not wrong, "each page reference points at the question's real page",
      str(wrong[:3]))
# The numbering the child sees: restarts, and never runs to the booklet total.
printed = [n for n, _ in refs]
check(printed and max(printed) < n_questions,
      "questions are numbered per section, not 1 to N across the booklet",
      f"highest printed number {max(printed) if printed else None} "
      f"of {n_questions} questions")
check(printed.count(1) > 1, "the numbering restarts more than once",
      f"{printed.count(1)} sections start at 1")

# The key is only usable if the number beside an answer is the number beside
# the question. Class Work restarted its numbering and Homework did not, so a
# parent marking homework read "1." in the key against "17." on the page.
#
# Both are checked against `question_numbering`, the one function that decides
# what a question is called, and each question is found by its own marker text
# rather than by counting numbered lines: mini-lessons number their steps 1, 2,
# 3 as well, and counting lines cannot tell a step from a question.
nums = question_numbering(data)
body_shown, missing = [], []
for i, marker in enumerate(markers, 1):
    m = re.search(r"^(\d+)\.\s[^\n]*" + re.escape(marker),
                  question_text, re.MULTILINE)
    body_shown.append(int(m.group(1)) if m else None)
    if m is None:
        missing.append(marker)
check(not missing, "every question prints with a number beside it",
      str(missing[:3]))
expected = [nums.get(i) for i in range(1, len(markers) + 1)]
wrong_body = [(mk, g, e) for mk, g, e in zip(markers, body_shown, expected)
              if g != e]
check(not wrong_body, "the page numbers every question as the numbering says",
      str(wrong_body[:3]))
wrong_key = [(i, g, e) for i, (g, e) in enumerate(zip(printed, expected), 1)
             if g != e]
check(not wrong_key, "and the key numbers every answer exactly the same way",
      str(wrong_key[:3]))

# The specific regression: Homework used to print the running index while the
# key printed the restarted number, so the two halves of a marked booklet
# disagreed from question 17 on.
hw_start = len(data.recap_questions) + sum(len(s.questions) for s in sections)
hw_shown = body_shown[hw_start:hw_start + sum(len(s.homework_questions)
                                              for s in sections)]
check(hw_shown and max(n for n in hw_shown if n is not None) < len(markers),
      "homework numbers restart too, rather than running on to the booklet total",
      f"highest homework number {max(n for n in hw_shown if n is not None)}")
# `key_start` is a 0-based index, so the key's own first page prints as
# `key_start + 1`. Testing `key_start` tested the last *question* page, which is
# a perfectly legal target and only passed while no question happened to land
# there.
check(f"(p{key_start + 1})" not in key_text and "(p1)" not in key_text,
      "no reference points into the key itself or the cover")

# The key's four parts used to be set in "topic", the same style as the topic
# name inside them, so "Class Work" and "Fractions" were typographically
# identical and whoever was marking could not see where one part stopped.
# What the file calls itself, in a browser tab, a print queue and the
# Properties dialog. Every booklet was titled "<program> Practice Booklet",
# identical for a Year 1 English booklet and a Year 10 maths one, and Subject
# and Creator carried ReportLab's literal "(unspecified)".
# Italics are the one typographic feature every dyslexia guideline names to
# avoid, and the specimen is the text in the booklet needing the most careful
# character by character reading: the sentence the child has to decode, edit or
# correct. The indent, the colour and the quote marks separate it already.
print("\nThe specimen is set upright")
for name in ("we_specimen", "question_specimen"):
    font = _styles[name].fontName
    check("Oblique" not in font and "Italic" not in font,
          f"{name} is not italic", font)
    check(_styles[name].leftIndent > 0
          and _relative_luminance(_styles[name].textColor) < 0.5,
          f"and {name} is still set apart by indent and colour",
          f"indent {_styles[name].leftIndent}")

print("\nPDF metadata")
_meta = pypdf.PdfReader(str(booklet)).metadata
_root = pypdf.PdfReader(str(booklet)).trailer["/Root"]
for field in ("/Title", "/Author", "/Subject", "/Creator"):
    value = str(_meta.get(field) or "")
    check(value and "unspecified" not in value.lower(),
          f"{field} is filled in", repr(value))
title = str(_meta.get("/Title") or "")
check(data.year_level in title and (data.student_name or "") in title,
      "the title identifies the year and the student, so two tabs differ",
      repr(title))
check(str(_root.get("/Lang") or "") == "en-AU",
      "the document declares its language", repr(str(_root.get("/Lang"))))

print("\nAnswer key hierarchy")
KEY_PARTS = ("Warm-up Recap", "Class Work", "Homework", "Final Challenge")
for part in KEY_PARTS:
    check(key_text.count(part) >= 1, f"the key still names {part}")
# Size is the thing that carries the hierarchy, and it is measurable.
sizes = {}
for page in pypdf.PdfReader(str(booklet)).pages[key_start:]:
    page.extract_text(visitor_text=lambda t, c, tm, fd, fs: sizes.setdefault(
        t.strip(), round(float(fs) * abs(tm[0] or 1.0), 1)) if t.strip() else None)
part_sizes = [sizes[p] for p in KEY_PARTS if p in sizes]
topic_sizes = [v for k, v in sizes.items()
               if k in ("Fractions", "Decimals", "Measurement")]
check(bool(part_sizes) and bool(topic_sizes),
      "both a part heading and a topic heading were measured in the key",
      f"parts {part_sizes}, topics {topic_sizes}")
check(part_sizes and topic_sizes and min(part_sizes) > max(topic_sizes),
      "a part heading in the key outranks a topic heading inside it",
      f"parts {sorted(set(part_sizes))} vs topics {sorted(set(topic_sizes))}")

# The Final Challenge is a scored part like the others and it is what the
# product is sold on. It used to arrive as a centred heading a centimetre below
# the last homework question.
challenge_page = next((i for i, p in enumerate(pages[:key_start])
                       if "Final Challenge" in p), None)
check(challenge_page is not None, "the Final Challenge is in the body")
check("You have done the hard part" in question_text.replace("\n", " "),
      "and arrives as an earned part, not as more questions")

print("\nScore line")
check(f"______ / {n_questions}" in text.replace("\n", " "),
      "there is a total to mark out of", str(n_questions))
check("Marked by:" in text and "Date:" in text, "the score line names who and when")
check("Warm-up Recap" in question_text and "Final Challenge" in question_text,
      "the score line breaks down by part")

print("\nHomework sessions")
plan = homework_session_plan(data)
n_hw = sum(len(s.homework_questions) for s in sections)
check(len(plan) >= 2, "homework is split into sittings", f"{len(plan)} sessions")
check(sum(p["count"] for p in plan) == n_hw,
      "every homework question belongs to exactly one session",
      f"{sum(p['count'] for p in plan)} of {n_hw}")
bands = re.findall(r"Session (\d+) of (\d+)", question_text)
check(len(bands) == len(plan), "one band printed per session",
      f"{len(bands)} bands, {len(plan)} planned")
check([int(a) for a, _ in bands] == list(range(1, len(plan) + 1)),
      "sessions are numbered in order", str(bands))
check(all(b == str(len(plan)) for _, b in bands), "each band says the total")
# A band says how much work the sitting holds, not which numbered questions.
# Numbers restart at every subtopic and every reading, so a span like
# "questions 17 to 27" named numbers printed nowhere in the booklet.
band_counts = [int(c) for c in
               re.findall(r"Session \d+ of \d+\s*\|\s*(\d+) questions?",
                          question_text.replace("\n", " "))]
check(band_counts == [p["count"] for p in plan],
      "each band says how many questions its sitting holds", str(band_counts))
check("questions 1 to" not in question_text.replace("\n", " "),
      "and names no numbered span, which restarted numbering makes meaningless")
check(homework_session_plan(BookletData(
    subject="Maths", year_level="Year 5", student_name="A",
    sections=[SubtopicOutput(topic="T", subtopic="S", questions=[],
                             homework_questions=[vq("One.")])])) == [],
      "a handful of homework questions is not split")
stranded = [i + 1 for i, page in enumerate(question_pages)
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
aligned_pages = read(render_pdf(aligned, tmp / "aligned.pdf"))[0]
aligned_text = "\n".join(aligned_pages[:key_page(aligned_pages)])
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

# ---------------------------------------------------------------------------
# The Homework band's total must be the sum of the sittings printed under it
#
# A shipped Year 5 Maths booklet said "Split into 4 sessions, about 179 min in
# total" over sittings of 31, 31, 31 and 29 min. Fifty-seven minutes were
# unaccounted for: the band was quoting the whole homework half, including the
# Final Challenge that has its own band and its own estimate below, and
# including the mini-lessons of subtopics whose practice had moved to Homework,
# which the sitting estimates did not count at all. A parent who plans a
# Tuesday evening around 31 minutes and loses an hour does not buy again.
# ---------------------------------------------------------------------------
print("\nThe homework band adds up")

trimmed_data = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic=f"Topic {i}", subtopic=f"Subtopic {i + 1}", teaching=teaching(2),
        # The last two subtopics did not fit the hour, so their practice moved
        # to Homework and their mini-lessons print down there with it.
        questions=[] if i >= 3 else [
            vq(f"Question {i}.{j}: a box is {j + 2} cm long, 2 cm wide and 3 cm "
               "high. What is its volume in cubic centimetres?")
            for j in range(4)],
        homework_questions=[
            vq(f"Set {i} item {j}: calculate {j + 1}/12 + {j + 1}/12 and give "
               "your answer in its lowest terms.", difficulty="easy")
            for j in range(10)]) for i in range(5)],
    challenge_questions=[
        vq(f"Challenge {k}: a pool is 10 m by 5 m by 1.5 m. What is its volume "
           "in cubic metres?", difficulty="hard") for k in range(8)])

trimmed_plan = homework_session_plan(trimmed_data)
trimmed_times = booklet_timing(trimmed_data)
trimmed_pages = read(render_pdf(trimmed_data, tmp / "trimmed.pdf"))[0]
trimmed_body = " ".join(
    " ".join(trimmed_pages[:key_page(trimmed_pages)]).split())

check(len(trimmed_plan) >= 2 and bool(trimmed_data.challenge_questions),
      "the fixture splits into sittings and has a Final Challenge",
      f"{len(trimmed_plan)} sessions")
band_total = re.search(r"Split into \d+ sessions, about (\d+) min in total",
                       trimmed_body)
check(band_total is not None, "the Homework band states a total",
      trimmed_body[trimmed_body.find("lock it in"):][:150])
printed_sessions = [int(m) for m in re.findall(
    r"Session \d+ of \d+ \| \d+ questions? \| about (\d+) min", trimmed_body)]
check(len(printed_sessions) == len(trimmed_plan),
      "every sitting prints its own estimate", str(printed_sessions))
if band_total and printed_sessions:
    check(int(band_total.group(1)) == sum(printed_sessions),
          "the Homework total is exactly the sittings underneath it added up",
          f"band {band_total.group(1)} vs sittings "
          f"{' + '.join(str(m) for m in printed_sessions)} "
          f"= {sum(printed_sessions)}")
# And the Final Challenge is named as extra, not folded into that total.
challenge_note = re.search(r"Final Challenge at the end adds about (\d+) min",
                           trimmed_body)
check(challenge_note is not None
      and int(challenge_note.group(1)) == trimmed_times["challenge_minutes"],
      "the Final Challenge is quoted separately, not inside the homework total",
      trimmed_body[trimmed_body.find("lock it in"):][:170])
if band_total:
    check(int(band_total.group(1)) + trimmed_times["challenge_minutes"]
          <= trimmed_times["homework_minutes"] + 3,
          "and the two together are the homework half, give or take rounding",
          f"{band_total.group(1)} + {trimmed_times['challenge_minutes']} vs "
          f"{trimmed_times['homework_minutes']}")

# A subtopic whose practice moved down reprints its mini-lesson in Homework, so
# the sitting that holds it has to be charged for it.
no_lesson = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic=s.topic, subtopic=s.subtopic,
        teaching=s.teaching if s.questions else None,
        questions=list(s.questions),
        homework_questions=list(s.homework_questions))
        for s in trimmed_data.sections])
check(sum(p["minutes"] for p in trimmed_plan)
      > sum(p["minutes"] for p in homework_session_plan(no_lesson)) + 2,
      "a moved subtopic's mini-lesson is charged to the sitting that prints it",
      f"{sum(p['minutes'] for p in trimmed_plan)} min with the lessons, "
      f"{sum(p['minutes'] for p in homework_session_plan(no_lesson))} without")

print("\nWarm-up working space")


def gap_below(runs_, needle):
    """Vertical distance from a question's line to the next line under it."""
    for page in runs_:
        for i, (t, y) in enumerate(page):
            if needle in t:
                below = [yy for _, yy in page[i + 1:] if yy < y]
                return (y - max(below)) / cm if below else None
    return None


warm = gap_below(runs, "Calculate 15")
classwork = gap_below(runs, "Question 0.0")
check(warm is not None and warm > 2.0,
      "a warm-up question gets more than one line to work in",
      f"{warm:.1f}cm" if warm else "not found")
check(warm is not None and classwork is not None and warm >= classwork * 0.8,
      "the warm-up is spaced like the rest of the booklet",
      f"warm-up {warm:.1f}cm vs class work {classwork:.1f}cm")

print("\nPage fill")


# The cover, the sign-off page and the blank verso before the key are all
# meant to end early, so measuring their tails says nothing about how well
# questions are packed. They used to be excluded by position, which only
# worked while the sign-off happened to be last: inserting the blank verso
# pushed it one page in and the check started failing on a page it had never
# been about.
def _exempt_pages(pages_, stop: int) -> set[int]:
    out = {0}
    for i, page in enumerate(pages_[:stop]):
        if "intentionally blank" in page or "Marked by:" in page:
            out.add(i)
    return out


# Both part breaks are preceded by a small spacer in the story, which is part
# of the hole the break leaves and is not covered by the threshold itself.
_PART_BREAK_SLACK_CM = 0.6


# The tinted fill of a worked-example box. A page that opens with a mini-lesson
# is recognised by having one of these near its top.
_WE_BOX_FILL = (0.957, 0.969, 0.984)


def lesson_openings(path) -> dict:
    """{page index: height in cm of the mini-lesson that opens that page}.

    A mini-lesson has to arrive on one page with its own worked example: the
    box is a Table and cannot split, so a lesson that starts at the foot of a
    page leaves its heading, its introduction and its key points above five
    centimetres of white with the example overleaf. The formatter therefore
    moves the whole lesson to the next page when it does not fit, which is
    right, and which necessarily leaves the foot of the page before it short.

    How short is allowed to be is not a constant. It is exactly how tall the
    lesson turned out to be, which varies by several centimetres with the
    number of steps the model wrote. So it is measured off the page the lesson
    actually landed on: from the top of the type area to the bottom of the
    first worked-example box.
    """
    doc = pymupdf.open(str(path))
    out = {}
    for i, page in enumerate(doc):
        boxes = [d["rect"] for d in page.get_drawings()
                 if d.get("fill") and max(abs(a - b) for a, b in
                                          zip(d["fill"], _WE_BOX_FILL)) < 0.01
                 and d["rect"].width > 0.5 * (A4[0] - 2 * PAGE_MARGIN)]
        if not boxes:
            continue
        first = min(boxes, key=lambda r: r.y0)
        # Near the top: a box further down the page arrived under questions,
        # so the page did not open with a lesson.
        if first.y0 - PAGE_MARGIN < 8 * cm:
            out[i] = (first.y1 - PAGE_MARGIN) / cm
    doc.close()
    return out


def page_fill(pages_, lows_, label, path=None):
    """Worst tail of blank space on the question pages, ignoring part breaks.

    Three things are allowed to start on a fresh page rather than squeeze into
    the foot of the one before: Homework, the Final Challenge and a mini-lesson.
    Each is measured against its own threshold rather than against a flat 6cm.
    The Final Challenge used to be missing from this list, so the one break that
    may legally throw away nine centimetres was judged as if it were an
    ordinary page, and it passed only for as long as pagination happened to
    keep it off a boundary.

    The mini-lesson case was added when short questions started being set two to
    a row. Two-up packing puts more questions on a page, so the room left over
    when the next lesson does not fit is larger than it was, and a flat 6cm
    began failing on a break that is doing exactly what it was built to do. The
    replacement is not a looser number: it is the height of the lesson itself,
    measured off the page it landed on, so a break still fails if it threw away
    more room than the lesson needed.
    """
    stop = key_page(pages_)
    tails = [(low - BODY_BOTTOM) / cm for low in lows_[:stop]]
    lessons = lesson_openings(path) if path is not None else {}

    def break_limit(i):
        """The threshold this page's tail is allowed, or None if it is not a
        part boundary."""
        head = "\n".join(pages_[i + 1].splitlines()[:4]) if i + 1 < stop else ""
        if "Homework" in head:
            return HOMEWORK_MIN_START_CM + _PART_BREAK_SLACK_CM
        if "Final Challenge" in head:
            return _CHALLENGE_MIN_START_CM + _PART_BREAK_SLACK_CM
        if i + 1 in lessons:
            return lessons[i + 1] + _PART_BREAK_SLACK_CM
        return None

    exempt = _exempt_pages(pages_, stop)
    worst_p = (0, 0.0)
    over = []
    measured = [(i, t) for i, t in enumerate(tails) if i not in exempt]
    for i, tail in measured:
        limit = break_limit(i)
        if limit is None:
            worst_p = max(worst_p, (i + 1, tail), key=lambda t: t[1])
        elif tail > limit:
            over.append((i + 1, round(tail, 1), limit))
    mean = sum(t for _, t in measured) / max(1, len(measured))
    check(worst_p[1] < 6.0, f"no {label} page abandoned more than 6cm early",
          f"worst is page {worst_p[0]} at {worst_p[1]:.1f}cm")
    check(not over,
          f"a {label} part break never throws away more than its threshold",
          str(over))
    check(mean < 4.0, f"{label} pages are worked down the page on average",
          f"mean tail {mean:.1f}cm")


page_fill(pages, lows, "booklet", booklet)

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
stress_pages, stress_lows, _, _, _ = read(render_pdf(stress, tmp / "stress.pdf"))
stress_stop = key_page(stress_pages)
orphans = []
for i, page in enumerate(stress_pages[:stress_stop]):
    body = [ln for ln in page.splitlines()
            if ln.strip() and not ln.startswith("Page ") and "Mathematics  |" not in ln]
    if body and body[0].strip().endswith("Answer:") and "Question" not in body[0]:
        orphans.append(i + 1)
check(not orphans, "no working space separated from its question", str(orphans))
stress_exempt = _exempt_pages(stress_pages, stress_stop)
stress_tails = [(low - BODY_BOTTOM) / cm
                for i, low in enumerate(stress_lows[:stress_stop])
                if i not in stress_exempt]
check(max(stress_tails) < 6.0, "no page abandoned early under stress",
      f"worst {max(stress_tails):.1f}cm")

print("\nClosing")
check("That is the end of the booklet, Lleyton." in question_text.replace("\n", " "),
      "the booklet closes by name")
closing_page = next(i for i, p in enumerate(question_pages)
                    if "end of the booklet" in p.replace("\n", " "))
check(closing_page >= key_start - 2,
      "the closing note is the last thing before the score line",
      f"page {closing_page + 1} of {key_start}")
check("Answers" not in question_pages[closing_page],
      "nothing follows the closing note but the score line")

print("\nTiming")
t = booklet_timing(data)
check(t["classwork_minutes"] > sum(len(s.questions) for s in sections) * 2.5,
      "class work is charged for its teaching, not only its questions",
      f"{t['classwork_minutes']} min")
check(t["homework_minutes"] < t["classwork_minutes"],
      "repetition homework is not charged the classwork rate",
      f"{t['homework_minutes']} min")
check(f"About {t['classwork_minutes']} min" in text,
      "the printed class work estimate is the recomputed one")
check(t["spelling_minutes"] is None, "no spelling time when there is no test")

# ---------------------------------------------------------------------------
# 7. A Year 3 English booklet: passages, spelling, a term to teach
# ---------------------------------------------------------------------------

print("\nEnglish booklet: render")

KITTEN = Passage(
    id="kitten", title="The Lost Kitten",
    paragraphs=[
        "Mia heard a tiny sound behind the shed. She pushed back the long grass "
        "and found a kitten curled up in an old cardboard box.",
        "The kitten was small and grey, and it shivered when Mia lifted it. She "
        "carried it inside and wrapped it in a warm towel.",
        "By the evening the kitten was purring on the couch, and Mia had already "
        "decided on a name for it.",
    ])
# Deliberately long: the passage and its first question must still land on the
# same page, which is the case a short passage never tests.
STORM = Passage(
    id="storm", title="Storm at Sea",
    paragraphs=[" ".join(
        ["The wind rose steadily through the afternoon and the small boat "
         "climbed each grey wave before sliding down the far side."] * 6),
        " ".join(["The captain tied everything down and watched the horizon for "
                  "the first break in the cloud."] * 6)])

english_sections = [
    SubtopicOutput(
        topic="Vocabulary", subtopic="Synonyms and Antonyms",
        passages=[KITTEN, STORM],
        teaching=SubtopicTeaching(
            intro_paragraphs=[
                "A **synonym** is a word that means nearly the same thing as "
                "another word. Writers choose a sharper synonym when the first "
                "word they think of is a dull one.",
                "An **antonym** is the opposite. Knowing both gives you two "
                "ways to make a sentence say exactly what you mean.",
                # Markup the model got wrong: it must degrade, not explode.
                "You can **always look for a shorter word too.",
            ],
            key_points=["Swap a dull word for a sharp one, like writing "
                        "enormous rather than big."],
            worked_example=WorkedExample(
                question="Find a synonym for the word cold.",
                steps=["Think about what the word means.",
                       "Choose a word that means nearly the same."],
                answer="freezing"),
            guided_examples=[]),
        questions=[
            pq("KITTENA. Referring to the passage above, where did Mia find the "
               "kitten?", "kitten", answer="Behind the shed"),
            pq("LOOSEB. Write a synonym for the word happy.", None,
               answer="cheerful"),
            pq("KITTENC. Referring to the passage above, write an antonym for "
               "the word tiny.", "kitten", answer="enormous"),
            pq("STORMD. Referring to the passage above, what did the captain "
               "watch for?", "storm", answer="A break in the cloud"),
            pq("KITTENE. Referring to the passage above, which word tells you "
               "the kitten was cold?", "kitten", answer="shivered"),
        ],
        homework_questions=[
            pq("HWKITTEN1. Referring to the passage above, who found the "
               "kitten?", "kitten", answer="Mia"),
            pq("HWLOOSE2. Write an antonym for the word early.", None,
               answer="late"),
            pq("HWKITTEN3. Referring to the passage above, what was the kitten "
               "wrapped in?", "kitten", answer="A warm towel"),
        ]),
    SubtopicOutput(
        topic="Grammar", subtopic="Verb Agreement",
        teaching=SubtopicTeaching(
            intro_paragraphs=[
                "Verb agreement means the verb matches the person doing it. "
                "Match the verb to the subject, like saying the dog runs "
                "instead of the dog run.",
            ],
            key_points=["One dog runs. Two dogs run."],
            worked_example=WorkedExample(
                question="Choose the correct verb: The birds (sing / sings).",
                steps=["Ask how many birds there are."], answer="sing"),
            guided_examples=[]),
        questions=[pq("VERB1. Choose the correct verb: The cat (sleep / "
                      "sleeps) on the mat.", None, answer="sleeps")],
        homework_questions=[]),
]

SPELL_LIST = ["accident", "against", "answer", "believe", "breath", "build",
              "certain", "circle", "complete", "consider", "decide", "describe",
              "different", "difficult", "disappear", "early", "earth", "eight",
              "enough", "exercise"]
SPELL_TEST = ["famous", "favourite", "february", "forward", "fruit", "grammar",
              "group", "guard", "guide", "heard", "heart", "height"]

english = BookletData(
    subject="English", year_level="Year 3", student_name="Ivy",
    program_label="Academic Accelerate", sections=english_sections,
    spelling_list=SpellingList(words=SPELL_LIST),
    spelling_test=SpellingTest(words=SPELL_TEST, from_week=2))

e_pdf = render_pdf(english, tmp / "english.pdf")
e_pages, e_lows, e_runs, e_bolds, e_rules = read(e_pdf)
e_key_start = key_page(e_pages)
e_question_pages = e_pages[:e_key_start]
e_question_text = "\n".join(e_question_pages)
e_key_text = "\n".join(e_pages[e_key_start:])
e_text = "\n".join(e_pages)

e_cover = " ".join(e_pages[0].split())
check("show your working" not in e_cover,
      "an English cover does not ask for working it has no room for")
check("symbolically" not in e_cover.lower()
      and "has been checked" in e_cover,
      "and claims the same accuracy check the maths cover does")
check((e_key_start + 1) % 2 == 1,
      "the English key also starts on the front of a fresh sheet",
      f"page {e_key_start + 1}")


def page_of(pages_, needle):
    return next((i for i, p in enumerate(pages_) if needle in p.replace("\n", " ")),
                None)


check(len(e_pages) > 3, "the English booklet renders", f"{len(e_pages)} pages")

print("\nPassages on the page")
# Class work only. A passage set in both parts is deliberately printed twice,
# once per part (see below), so counting across the whole booklet would
# contradict that: what must not happen is the same reading printed once per
# question inside a single part.
e_hw_start = next(i for i, p in enumerate(e_question_pages)
                  if "Homework" in p)
e_classwork_text = "\n".join(e_question_pages[:e_hw_start + 1]).split("Homework")[0]
e_homework_text = "\n".join(e_question_pages[e_hw_start:])
for title in ("The Lost Kitten", "Storm at Sea"):
    check(e_classwork_text.count(title) == 1,
          f"{title!r} is printed once in the class work",
          f"{e_classwork_text.count(title)} times")
check("READ THIS" in e_question_text, "the passage is marked as something to read")
# The passage must be above every question that refers to it, and on the same
# page as the first of them.
for title, first, rest in (("The Lost Kitten", "KITTENA", ["KITTENC", "KITTENE"]),
                           ("Storm at Sea", "STORMD", [])):
    p_title = page_of(e_question_pages, title)
    p_first = page_of(e_question_pages, first)
    check(p_title is not None and p_title == p_first,
          f"{title!r} shares a page with its first question",
          f"passage p{p_title}, question p{p_first}")
    ys = {t: y for page in e_runs for t, y in page}
    check(all(page_of(e_question_pages, r) >= p_title for r in rest),
          f"every question about {title!r} follows it")
# The passage sits above its first question on that page.
title_y = next(y for page in e_runs for t, y in page if "The Lost Kitten" in t)
q_y = next(y for page in e_runs for t, y in page if "KITTENA" in t)
check(title_y > q_y, "the reading is laid out above the question, not below",
      f"passage y={title_y:.0f}, question y={q_y:.0f}")
# Regrouping happened: C is printed before B even though B was emitted first.
order = [m for m in re.findall(r"KITTENA|LOOSEB|KITTENC|STORMD|KITTENE",
                               e_question_text)]
check(order == ["KITTENA", "KITTENC", "KITTENE", "LOOSEB", "STORMD"],
      "questions are reordered to sit under their passage", str(order))
check(e_classwork_text.count("The Lost Kitten") == 1
      and e_homework_text.count("The Lost Kitten") == 1,
      "a passage used again in homework is reprinted there, not referred back to",
      f"{e_classwork_text.count('The Lost Kitten')} in class work, "
      f"{e_homework_text.count('The Lost Kitten')} in homework")

print("\nThe key follows the printed order")
mis = []
for marker, answer in (("KITTENA", "Behind the shed"), ("LOOSEB", "cheerful"),
                       ("KITTENC", "enormous"), ("STORMD", "A break in the cloud"),
                       ("KITTENE", "shivered"), ("VERB1", "sleeps")):
    body_n = re.search(r"(\d+)\.\s*" + marker, e_question_text)
    key_n = re.search(r"(\d+)\. Answer: " + re.escape(answer), e_key_text)
    if not body_n or not key_n or body_n.group(1) != key_n.group(1):
        mis.append((marker, body_n and body_n.group(1), key_n and key_n.group(1)))
check(not mis, "every answer is numbered as the question was printed", str(mis))

print("\nSpelling")
check(spelling_test_spaces(english.spelling_test) == 12,
      "the test page has twelve spaces")
check(spelling_test_spaces(None) == 0, "no test object means no test page")
check(spelling_test_spaces(SpellingTest()) == SPELLING_TEST_SPACES,
      "a test with no words chosen still prints its spaces")
test_page = page_of(e_pages, "Spelling Test")
list_page = page_of(e_pages, "Spelling List")
check(test_page == 1, "the test is the first thing after the cover",
      f"page {test_page + 1 if test_page is not None else None}")
check(list_page is not None and list_page < e_key_start,
      "the list is at the back of the booklet, before the key",
      f"list p{list_page}, key p{e_key_start}")
check(list_page > page_of(e_pages, "VERB1"),
      "the list comes after the last question")
# The child must not be able to read the answers off the page. Two different
# things are being asserted, and only the first can be an exact match: a test
# word may occur incidentally in a story ("Mia heard a tiny sound") or in a
# heading ("Grammar"), and no formatter can prevent that. What it can prevent
# is the words appearing on the test page itself, or a run of the list leaking
# onto a page the child works on.
on_test_page = [w for w in SPELL_TEST if w in e_pages[test_page].lower()]
check(not on_test_page, "not one test word is printed on the test page",
      str(on_test_page))
worst_page = max(
    (sum(w in p.lower() for w in SPELL_TEST), i)
    for i, p in enumerate(e_question_pages))
check(worst_page[0] < 3, "no run of the test list leaks onto a page worked on",
      f"page {worst_page[1] + 1} shows {worst_page[0]}")
check(all(w in e_key_text.lower() for w in SPELL_TEST),
      "the key carries the words to call out")
check("read them out one at a time" in e_key_text.lower(),
      "the key says what to do with them")
missing_list = [w for w in SPELL_LIST if w not in e_question_text]
check(not missing_list, "all twenty words to learn are printed", str(missing_list))
numbers_on_test = [t for t, _ in e_runs[test_page] if re.fullmatch(r"\d{1,2}\.", t)]
# The grid is two columns, so reading order interleaves them (1, 7, 2, 8, ...),
# and the page carries on into the class work below, which starts numbering
# again. Both are fine. What matters is that the twelve lines the child writes
# on are numbered 1 to 12 with none missing and none repeated.
check(sorted(int(t[:-1]) for t in numbers_on_test[:12]) == list(range(1, 13)),
      "the test lines are numbered 1 to 12", str(numbers_on_test[:12]))
check(e_rules[test_page] >= 12, "there is a rule to write each word on",
      f"{e_rules[test_page]} rules drawn")
rows = dict(part_counts(english))
check(rows.get("Spelling Test") == 12, "the score line counts the spelling test",
      str(rows))
check("______ / 12" in e_text.replace("\n", " "), "and it can be marked out of 12")
et = booklet_timing(english)
check(et["spelling_minutes"] == 6, "the dictation is charged for",
      f"{et['spelling_minutes']} min")

print("\nMini-lesson presentation")
bold_runs = {t.strip() for page in e_bolds for t, _ in page}
check("synonym" in bold_runs, "the marked-up term is bold on the page",
      str(sorted(b for b in bold_runs if "nym" in b)))
check("antonym" in bold_runs, "a second marked-up term as well")
check(e_question_text.count("synonym") >= 2 and
      sum(1 for page in e_bolds for t, _ in page if t.strip() == "synonym") == 1,
      "and only where the lesson marked it")
check("*" not in e_text, "no asterisk survives to the page, not even a broken one")
check("always look for a shorter word" in e_question_text.replace("\n", " "),
      "an unclosed marker degrades to plain text rather than losing the words")
check('"the dog runs"' in e_question_text and '"the dog run"' in e_question_text,
      "the two specimens in a sentence read as specimens")
check('"enormous"' in e_question_text and '"big"' in e_question_text,
      "and so do the ones in a key point")

page_fill(e_pages, e_lows, "English", e_pdf)

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
exam_pages = read(render_exam_pdf(exam, tmp / "exam.pdf"))[0]
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


# ---------------------------------------------------------------------------
# A homework session that starts mid-subtopic says what it is
#
# year5-maths-sample.pdf page 10 opens "Session 2 of 2 | 9 questions | about
# 12 min" and the next line is "2. Write 0.305 in words." No topic, no
# question 1. The child sits down days later on a page that starts in the
# middle of a list they cannot see.
# ---------------------------------------------------------------------------
print("\nA session starting mid-subtopic")

cont = BookletData(
    subject="Mathematics", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic="Decimals", subtopic="Place value in decimals", questions=[],
        homework_questions=[
            vq(f"Write the value of the digit 5 in {j}.305 as a fraction.",
               difficulty="easy") for j in range(1, 15)])])
cont_plan = homework_session_plan(cont)
cont_pages = read(render_pdf(cont, tmp / "continued.pdf"))[0]
cont_body = "\n".join(cont_pages[:key_page(cont_pages)])
mid = [p for p in cont_plan[1:] if p["start"] > 0]
check(len(cont_plan) > 1 and bool(mid),
      "the fixture splits into more than one session",
      str([p["start"] for p in cont_plan]))
if mid:
    check("(continued)" in cont_body,
          "a session opening part way through a subtopic names it",
          cont_body[cont_body.find("Session 2"):][:90].replace("\n", " | "))


# ---------------------------------------------------------------------------
# Markdown emphasis must never print, and must not become multiplication
#
# A real booklet printed "multiply the numerator and the denominator by the
# x same x number" inside the highlighted box its topic is named after.
# _STAR_MULT_RE read the model's *same* emphasis markers as multiplication,
# because the \s* either side of the asterisk swallowed the space that was
# supposed to protect them.
# ---------------------------------------------------------------------------
print("\nMarkdown emphasis")
for raw, want, note in [
    ("multiply the numerator and denominator by the *same* number",
     "multiply the numerator and denominator by the same number",
     "the shipped case"),
    ("This is **really** important.", "This is **really** important.",
     "double asterisks are left for apply_bold_markup"),
    ("Calculate 15 * 4 + 7.", "Calculate 15 " + MULTIPLY + " 4 + 7.",
     "real multiplication survives"),
    ("Volume = 7 * 4 * 2", "Volume = 7 " + MULTIPLY + " 4 " + MULTIPLY + " 2",
     "a chain of multiplications survives"),
    ("Area = length * width", "Area = length " + MULTIPLY + " width",
     "multiplication between words survives"),
]:
    got = _escape(raw)
    check(got == want, f"emphasis: {note}", f"{got!r}")

# ---------------------------------------------------------------------------
# The cover claim must match the key it points at
#
# A real booklet promised "every answer in the key at the back has been checked
# for accuracy" on page 1, then printed ten of ninety-nine answers with no tick
# beside them. That tells a parent, in the product's own notation, that the
# cover is false.
# ---------------------------------------------------------------------------
print("\nThe cover claims only what the key delivers")


def vq2(text, verified):
    return ValidatedQuestion(
        question=Question(question=text, answer="24", working="8 x 3 = 24",
                          difficulty="easy"),
        verified=verified)


def cover_text(all_verified):
    d = BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Sam",
        sections=[SubtopicOutput(
            topic="Number", subtopic="Multiplying",
            questions=[vq("What is 6 times 7?"),
                       vq2("And 8 times 3?", all_verified)],
            homework_questions=[])])
    pages = read(render_pdf(d, tmp / f"cover-{all_verified}.pdf"))[0]
    return " ".join(pages[0].split())


all_ok = cover_text(True)
some_unchecked = cover_text(False)
check("Every answer in the key at the back has been checked" in all_ok,
      "a fully verified booklet still says so")
check("Every answer" not in some_unchecked,
      "a booklet with an unchecked answer drops the absolute claim",
      some_unchecked[:120])
check("a tick marks an answer that has been checked" in some_unchecked,
      "and points at the tick instead")

print(f"\nPDFs written to {tmp}")


# ---------------------------------------------------------------------------
# Two readings under one subtopic: the key must say which is which
#
# year5-english-sample.pdf, key pages 19-20: "Making inferences" appeared once,
# then answers 1 to 5 for 'The Last Bus to Mullaloo', then immediately another
# run of 1 to 5 for 'From the Diary of Alice Weir' with nothing between them.
# Numbering restarts under each passage, exactly as the student page numbers
# it, so whoever was marking beside the student had no way to tell where the
# second reading began and marked against the wrong set.
# ---------------------------------------------------------------------------
print("\nAnswer key: two readings in one subtopic")

two_readings = BookletData(
    subject="English", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic="Comprehension", subtopic="Making inferences",
        passages=[P1, P2],
        questions=[pq("Why did the kitten hide?", "p1", answer="It was afraid"),
                   pq("What did she find?", "p1", answer="A collar"),
                   pq("How does the crew feel?", "p2", answer="Frightened"),
                   pq("What warned them?", "p2", answer="The falling glass")],
        homework_questions=[])])
tr_pages = read(render_pdf(two_readings, tmp / "two-readings.pdf"))[0]
tr_key = "\n".join(tr_pages[key_page(tr_pages):])
check(P1.title in tr_key and P2.title in tr_key,
      "the key names both readings, so a restarted 1 is not ambiguous",
      f"{P1.title in tr_key}/{P2.title in tr_key}")
check(tr_key.index(P1.title) < tr_key.index(P2.title),
      "and names them in the order the student met them")

# One reading needs no label: the subtopic heading already says what it is.
one_reading = BookletData(
    subject="English", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic="Comprehension", subtopic="Making inferences",
        passages=[P1],
        questions=[pq("Why did the kitten hide?", "p1", answer="It was afraid"),
                   pq("What did she find?", "p1", answer="A collar")],
        homework_questions=[])])
or_pages = read(render_pdf(one_reading, tmp / "one-reading.pdf"))[0]
or_key = "\n".join(or_pages[key_page(or_pages):])
check("Questions on" not in or_key,
      "a subtopic with a single reading is not labelled needlessly")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
print("\nAll checks passed.")
