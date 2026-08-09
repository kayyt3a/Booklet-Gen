#!/usr/bin/env python3
"""Check the answer-consistency, diagram and missing-figure guards.

Every case here is drawn from a real generated booklet. The PASS cases are
genuine entries that must survive untouched; the REJECT cases are defects
that shipped. Both directions matter: a guard that mangles good questions is
as damaging as one that misses bad ones, and these guards delete or blank
content, so a false positive is invisible to the reader.

Four things are checked:

  answer consistency      working that contradicts its own answer
  diagram reconciliation  labels that contradict the question
  answer leakage          labels that ARE the answer, and the rendered
                          drawing proving the number never reaches the page
  missing figure          text promising a picture that was never drawn

Usage:  PYTHONPATH=. python scripts/check_consistency_guards.py
"""
from __future__ import annotations

import logging
import sys
from types import SimpleNamespace
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The guards below are supposed to log what they drop; the report is the
# output here, so keep the log lines out of it.
logging.disable(logging.CRITICAL)

from booklet_gen.agents.consistency import (          # noqa: E402
    answer_is_trustworthy, diagram_dimensionality_matches,
    example_spoils_passage, implausible_magnitude, reconcile_diagram_spec,
    refers_to_missing_figure)

# (answer, working, should_be_trusted)
ANSWER_CASES = [
    # Real entries from pages 19-25 that must keep their mark.
    ("1/2", "Divide by the greatest common divisor, which is 4.\n4 / 4 = 1\n8 / 4 = 2", True),
    ("1/3", "Divide the numerator and denominator by 3.\n3 / 3 = 1\n9 / 3 = 3", True),
    ("3/4", "Divide both by 3.\n9 / 3 = 3\n12 / 3 = 4", True),
    ("5/7", "The denominators are the same, so add the numerators.\n2 + 3 = 5\nKeep the denominator as 7.", True),
    ("8000", "Volume = 40 cm x 20 cm x 10 cm.\n40 x 20 = 800. 800 x 10 = 8000.", True),
    ("56", "Volume = 7 * 4 * 2\nVolume = 28 * 2\nVolume = 56", True),
    ("125", "150 = 6 * side2.\nside2 = 25.\nside = 5.\nVolume = 5^3 = 125.", True),
    ("x = 9", "Divide both sides by 5\nx = 45 / 5\nx = 9", True),
    ("3/8 of the pizza is left", "2/8 + 3/8 = 5/8. Then 8/8 - 5/8 = 3/8.", True),

    # Q63, page 25 of the shipped booklet: stated 75 over working reaching 80.
    ("75", "New volume = 5 x 4 x 4 = 80. Wait, recalculating: 60 + 20 = 80. "
           "Correction: 80 cubic metres.", False),
    # Self-correction phrases that a trailing \b silently disabled.
    ("20", "Correction: the answer is 20.", False),
    ("12", "Actually, it is 12.", False),
    ("6", "My mistake, the answer is 6.", False),
    # A fraction answer contradicted by its own working.
    ("5/8", "3/8 + 2/8 = 6/8. So the answer is 6/8.", False),

    # Self-correction in the ANSWER rather than the working. This slipped
    # through: the contradiction check only judges a single-valued answer, and
    # "75. Wait, recalculating: 80" reads as two numbers, so it was skipped
    # while the working alone looked consistent.
    ("75. Wait, recalculating: 80", "New volume = 5 x 4 x 4 = 80.", False),
    ("20. Correction: 24", "6 x 4 = 24.", False),
    ("Actually, my mistake, it is 14", "7 + 7 = 14.", False),
    # An answer that merely discusses a student's error must keep its mark.
    ("He added the denominators", "2/5 + 1/5 = 3/5, not 3/10.", True),
]

# (spec, question, expect_changed, note)
DIAGRAM_CASES = [
    ({"type": "cuboid", "length": 4, "width": 2, "height": 1, "unit": "cm"},
     "A rectangular tank is 40 cm long, 20 cm wide, and 10 cm high.",
     True, "page 9: drawing was a tenth of the stated size"),
    ({"type": "cuboid", "length": 4, "width": 2, "height": 1, "unit": "dm"},
     "A fish tank has a base 40 cm long and 20 cm wide, filled to a height of 10 cm.",
     True, "page 17: question in cm, drawing in dm"),
    ({"type": "cuboid", "length": 7, "width": 4, "height": 2, "unit": "m"},
     "A pool is a rectangular prism with a length of 7 m, a width of 4 m, and a depth of 2 m.",
     False, "already correct, must not be touched"),
    ({"type": "cuboid", "length": 100, "width": 50, "height": 20, "unit": "cm"},
     "A trough is 1 metre long, 50 cm wide, and 20 cm deep.",
     False, "mixed units: overriding would invent a 1m x 50m x 20m trough"),
    ({"type": "cuboid", "length": 3, "width": 3, "height": 3, "unit": "cm"},
     "A solid object is made by stacking cubes. How many cubes are in the stack?",
     False, "nothing stated, must not invent"),
]

# The label that IS the answer. reconcile_diagram_spec used to correct only
# the dimensions a question states, which by definition excludes the one a
# find-the-missing-dimension question is asking for, so the giveaway label
# survived by design.
# (spec, question, expected "unknown" list, note)
LEAK_CASES = [
    ({"type": "cuboid", "length": 4, "width": 3, "height": 2, "unit": "blocks"},
     "A storage box is built using 24 cubic blocks. The base is 4 blocks by "
     "3 blocks. How many layers high is the box?",
     ["height"], "the shipped case: the height label was the answer"),
    ({"type": "cuboid", "length": 8, "width": 5, "height": 3, "unit": "cm"},
     "A tank holds 120 cubic cm. Its base is 8 cm long and 5 cm wide. "
     "How deep is the tank?",
     ["height"], "depth asked, base stated"),
    ({"type": "rectangle", "length": 6, "width": 4, "unit": "cm"},
     "A rectangle has an area of 24 square cm. Its width is 4 cm. "
     "What is the length?",
     ["length"], "rectangle: the length is the answer"),

    # The shapes added for Years 5 to 10. Each of them prints every dimension
    # it is given, so each of them can hand over the answer, and Pythagoras is
    # the worst case: the whole point of the question is that the third side
    # is not measured off the page.
    ({"type": "right_triangle", "a": 4, "b": 3, "c": 5, "unit": "cm"},
     "A ramp rises 3 m over a run of 4 m. Find the length of the hypotenuse.",
     ["c"], "Pythagoras: the hypotenuse label is the answer"),
    ({"type": "right_triangle", "a": 4, "b": 3, "c": 5, "unit": "cm"},
     "What is the hypotenuse of this triangle?",
     ["c"], "and the same asked without the word length"),
    ({"type": "right_triangle", "a": 4, "b": 3, "c": 5, "unit": "cm"},
     "The hypotenuse is 5 cm and one leg is 3 cm. Find the area.",
     [], "but a stated hypotenuse leaks nothing and stays on the drawing"),
    ({"type": "circle", "radius": 7, "unit": "cm"},
     "A circular garden bed has an area of about 154 square cm. "
     "Work out its radius.",
     ["radius"], "circle: the radius label is the answer"),
    ({"type": "circle", "diameter": 14, "unit": "cm"},
     "The circumference of a wheel is 44 cm. Find its diameter.",
     ["diameter"], "and a diameter-labelled circle hides the diameter"),
    ({"type": "circle", "radius": 7, "unit": "cm"},
     "The circle has a radius of 7 cm. Find its circumference.",
     [], "a stated radius stays, since the drawing is then worth more"),
    ({"type": "triangle", "base": 10, "height": 6, "unit": "cm"},
     "A triangle has an area of 30 square cm and a base of 10 cm. "
     "Find the height.",
     ["height"], "triangle: the perpendicular height is the answer"),
    ({"type": "triangle", "base": 10, "height": 6, "unit": "cm"},
     "Find the area of the base of the triangle.",
     [], "but 'the area of the base' asks for the area, not the base, and "
         "hiding the base would make it unanswerable"),
    ({"type": "parallelogram", "base": 9, "height": 4, "unit": "cm"},
     "A parallelogram has an area of 36 square cm and a height of 4 cm. "
     "What is the base?",
     ["base"], "parallelogram: the base is the answer"),
    ({"type": "trapezium", "top": 5, "bottom": 9, "height": 4, "unit": "cm"},
     "A trapezium has parallel sides of 5 cm and 9 cm and an area of "
     "28 square cm. Find the height.",
     ["height"], "trapezium: the height is the answer"),
    ({"type": "cylinder", "radius": 3, "height": 5, "unit": "cm"},
     "A cylinder is 5 cm high and holds about 141 cubic cm. Find the radius.",
     ["radius"], "cylinder: the radius is the answer"),
    ({"type": "cylinder", "radius": 3, "height": 5, "unit": "cm"},
     "A cylinder is 5 cm high. What is its diameter if the radius is asked "
     "for in the next part?",
     ["radius"], "a labelled radius gives the diameter away too"),

    # Must NOT be blanked. Every one of these is answerable only because the
    # drawing is fully labelled.
    ({"type": "cuboid", "length": 7, "width": 4, "height": 2, "unit": "m"},
     "A pool is 7 m long, 4 m wide and 2 m deep. What is its volume?",
     [], "all three stated: nothing to hide"),
    ({"type": "cuboid", "length": 40, "width": 20, "height": 10, "unit": "cm"},
     "A rectangular tank is 40 cm long, 20 cm wide and 10 cm high. "
     "How long does it take to fill at 2 litres per minute?",
     [], "'how long' about time, not about the side"),
    ({"type": "cuboid", "length": 5, "width": 3, "height": 4, "unit": "cm"},
     "What is the volume of a box with a height of 4 cm, a length of 5 cm "
     "and a width of 3 cm?",
     [], "'height' follows the verb only by accident"),
    ({"type": "cuboid", "length": 10, "width": 4, "height": 6, "unit": "cm"},
     "A box is 10 cm long, 4 cm wide and 6 cm high. How tall are two boxes "
     "stacked on top of each other?",
     [], "the height is asked for AND stated, so the label leaks nothing"),
    ({"type": "rectangle", "length": 8, "width": 3, "unit": "cm"},
     "A rectangle is 8 cm long and 3 cm wide. What is its perimeter?",
     [], "'long' and 'wide' as statements, not questions"),
]

# (text, an image was resolved, should the item be dropped, note)
FIGURE_CASES = [
    # The real defect: a mini-lesson worked example with no diagram at all.
    ("How many cubes are needed to build this object?", False, True,
     "the shipped case: no spec was emitted"),
    ("Look at the diagram. What is the area of the shaded part?", False, True,
     "refers to a diagram that does not exist"),
    ("Find the volume of the solid shown below.", False, True, ""),
    ("What fraction of the shape below is shaded?", False, True, ""),
    ("Count the squares in the figure to find the perimeter.", False, True,
     "'the figure' with nothing beside it"),
    ("Mark 3/4 on the number line.", False, True, "no line to mark"),

    # Same texts, with an image: nothing to answer for.
    ("How many cubes are needed to build this object?", True, False,
     "the picture is there, the question is fine"),
    ("Find the volume of the solid shown below.", True, False, ""),
    ("Mark 3/4 on the number line.", True, False, ""),

    # Must survive without an image. These read as figure references to a
    # careless matcher and are answerable exactly as written.
    ("What is 11/15 - 3/15 - 2/15?", False, False, ""),
    ("A rectangle is 8 cm long and 3 cm wide. What is its area?", False, False, ""),
    ("The test scores are shown below: 4, 7, 9, 10. Find the mean.", False, False,
     "inline data introduced with a colon, not a picture"),
    ("The table below shows the results.\nMon 5\nTue 7\nFind the total.",
     False, False, "a table is text, and the data is right there"),
    ("Continue this pattern: 2, 5, 8, 11, ...", False, False,
     "the pattern is printed in the question"),
    ("What is the name of a shape with five sides?", False, False,
     "'a shape', not 'this shape'"),
    ("Draw a picture to show 1/4 of 8.", False, False,
     "the child draws it; nothing is promised"),
    ("Write the number 3 042 in words.", False, False, ""),
    ("Sketch the graph of y = 2x + 1.", False, False,
     "the child produces the graph; there is nothing to show them"),
    ("Draw the number line from 0 to 1 and mark 3/4 on it.", False, False,
     "same: 'draw the number line' is the task"),
    ("Identify the figure of speech in line 2.", False, False,
     "an English term, not a drawing"),
    ("What image does the poet create in the second stanza?", False, False,
     "'image' in the English sense"),
    ("Look at the image below and describe the setting.", False, True,
     "'image' WITH a position word is a real reference"),
]


# A real-world quantity that is absurd by orders of magnitude. Page 10 of a
# shipped Year 5 Maths booklet, in a subtopic called "Reading and writing
# numbers up to millions", told a child that Perth is 3,421,000 km from
# Melbourne, that a stadium holds 9,900,009 people and that a Queensland
# national park is three times the size of Queensland.
#
# The keep cases matter more than the drop cases here. This guard deletes
# customer content, and most of the keeps are real questions from the same
# booklet, one of them on the very next page.
# (question, should_be_dropped, note)
MAGNITUDE_CASES = [
    # The three shipped defects, in the wording they shipped in.
    ("The distance from Perth to Melbourne is approximately 3,421,000 "
     "kilometres. Write this distance in words.", True, "page 10, the shipped case"),
    ("A large stadium can hold 9,900,009 spectators. Write this capacity in "
     "words.", True, "page 11, the shipped case"),
    ("A national park in Queensland covers an area of five million, seven "
     "hundred and two km squared. Write this area in numerals.", True,
     "page 11, written out in words"),
    ("A national park in Queensland covers an area of 5,702,000 square "
     "kilometres.", True, "the same claim in numerals"),
    ("The population of a small country town is 2,050,100 people. Write this "
     "number in words.", True, "page 10: a small town the size of Brisbane"),
    ("The distance between Sydney and Brisbane is 912,000,000 metres.", True,
     "wrong in metres is still wrong"),
    ("The MCG is an oval that seats 9,900,009 people.", True,
     "the capacity phrase can come either way round"),

    # Real questions from the same booklet, which must survive untouched.
    ("The distance from Perth to Adelaide is approximately 2695 kilometres. "
     "Round this distance to the nearest 100 kilometres.", False,
     "page 13, correct, and the second number is a rounding instruction"),
    ("A game company sold 10,000,000 copies of its new release. Write this "
     "number in words.", False, "page 11: no rule covers copies sold"),
    ("A popular online video has been viewed six million, ninety-five "
     "thousand, and forty-two times.", False, "page 11: views are unbounded"),
    ("Write the number 'Four million, one hundred and twenty thousand, five "
     "hundred and three' in numerals.", False, "page 10: no real-world claim"),
    ("A charity event raised seven million, eight thousand, and sixteen "
     "dollars.", False, "page 10: dollars are not in the table"),

    # Correct versions of the flagged claims, and near neighbours.
    ("The distance from Perth to Melbourne is about 3,400 km.", False,
     "the truth passes"),
    ("The distance from Perth to Melbourne is about 3,400,000 metres. Write "
     "this in kilometres.", False, "a unit conversion is not an error"),
    ("A large stadium can hold 100,000 spectators.", False, "about the MCG"),
    ("Kakadu National Park covers about 19,800 km2.", False,
     "the largest national park in the country"),
    ("The area of Western Australia is 2,527,013 km squared.", False, ""),
    ("The population of Australia is about 27,000,000 people.", False, ""),
    ("The population of a town is 12,500 people. Round it to the nearest 1000.",
     False, "a town the size of a town"),
    ("The distance from the Earth to the Sun is about 150,000,000 kilometres.",
     False, "both ends must be Australian towns, and one of these is a star"),
    ("Light travels 9,460,000,000,000 kilometres in a year.", False,
     "no rule claims to know how far light goes"),

    # Sums and running totals are not claims about how big one thing is.
    ("The distance from Perth to Melbourne is about 3,400 km. A truck makes "
     "30 return trips, covering 204,000 km altogether.", False,
     "the second figure is a total, and it is in another sentence"),
    ("The stadium sold 2,000,000 seats over the season.", False,
     "a season's sales are not a capacity"),
]


def _render_checks() -> list:
    """Draw the diagrams and read back every label matplotlib was asked for.

    Checking the spec is not enough: the leak happens on the page. This spies
    on Axes.text so the check is against the strings that actually get drawn,
    which is the only thing the child sees.
    """
    from matplotlib.axes import Axes
    from booklet_gen.visuals import diagrams

    drawn: list[str] = []
    original = Axes.text

    def spy(self, x, y, s, *args, **kwargs):
        drawn.append(str(s))
        return original(self, x, y, s, *args, **kwargs)

    out = []
    tmp = Path(tempfile.mkdtemp(prefix="folio-diagram-check-"))
    old_cache = diagrams.CACHE_DIR
    Axes.text = spy
    diagrams.CACHE_DIR = tmp        # never read a cached PNG: we need the draw
    try:
        def labels(spec):
            """The distinct labels drawn for one spec.

            Distinct, not the raw sequence: render_diagram now draws a figure
            more than once when the first attempt puts text on the page too
            small to read (see _draw_legibly), so the same label legitimately
            arrives several times. What this file is about is *which* strings
            reach the page, and that is unchanged by how often they are drawn.
            """
            drawn.clear()
            path = diagrams.render_diagram(dict(spec))
            assert path is not None and path.exists(), f"render failed: {spec}"
            seen = []
            for s in drawn:
                if s not in seen:
                    seen.append(s)
            return seen

        # The shipped case, after reconciliation.
        spec, _ = reconcile_diagram_spec(
            {"type": "cuboid", "length": 4, "width": 3, "height": 2, "unit": "blocks"},
            "A storage box is built using 24 cubic blocks. The base is 4 blocks "
            "by 3 blocks. How many layers high is the box?")
        got = labels(spec)
        out.append((
            "?" in got and not any("2" in s for s in got),
            f"storage box: drawn labels {got} (answer '2 blocks' never printed)",
        ))

        # A fully stated cuboid keeps all three numbers.
        got = labels({"type": "cuboid", "length": 7, "width": 4, "height": 2,
                      "unit": "m"})
        out.append((
            got == ["7 m", "2 m", "4 m"] and "?" not in got,
            f"stated cuboid: drawn labels {got} (unchanged)",
        ))

        # Rectangle and cylinder hide the asked-for side too.
        got = labels({"type": "rectangle", "length": 6, "width": 4,
                      "unit": "cm", "unknown": ["length"]})
        out.append(("?" in got and "4 cm" in got and "6 cm" not in got,
                    f"rectangle: drawn labels {got}"))

        got = labels({"type": "cylinder", "radius": 3, "height": 5,
                      "unit": "cm", "unknown": ["radius"]})
        out.append(("r = ?" in got and "5 cm" in got and "3 cm" not in got,
                    f"cylinder: drawn labels {got}"))

        # An unknown key must not stop the picture being drawn at all.
        path = diagrams.render_diagram(
            {"type": "cuboid", "length": 4, "width": 3, "height": 2,
             "unknown": ["height"]})
        out.append((path is not None and path.exists(),
                    "a spec with an unknown side still renders a PNG"))

        # A number line places its mark by value, so the position is the fact
        # and the label is a claim about it. The shipped Year 5 booklet taught
        # "round 347 to the nearest 100" with a line from 300 to 400, the dot
        # at 347, and "300" written over the dot: the answer, printed on the
        # point it is not. The position wins.
        got = labels({"type": "number_line", "from": 300, "to": 400,
                      "divisions": 10, "mark_at": [347], "label_at": ["300"]})
        out.append(("347" in got and "300" in got and got.count("300") == 1,
                    f"number line: the mark labelled '300' at 347 reads {got} "
                    f"('300' survives only as the left endpoint)"))

        # A label that already agrees is untouched, in every form a label
        # legitimately takes.
        got = labels({"type": "number_line", "from": 0, "to": 1,
                      "divisions": 4, "mark_at": [0.75], "label_at": ["3/4"]})
        out.append(("3/4" in got,
                    f"number line: a correct fraction label is kept: {got}"))

        got = labels({"type": "number_line", "from": 0, "to": 4,
                      "divisions": 8, "mark_at": [1.5], "label_at": ["1 1/2"]})
        out.append(("1 1/2" in got,
                    f"number line: a correct mixed number is kept: {got}"))

        # Words describe the point rather than naming it, so they are left be.
        got = labels({"type": "number_line", "from": 0, "to": 100,
                      "divisions": 10, "mark_at": [47], "label_at": ["just under half"]})
        out.append(("just under half" in got,
                    f"number line: a worded label is left alone: {got}"))

        # A fraction label that disagrees is corrected like any other.
        got = labels({"type": "number_line", "from": 0, "to": 1,
                      "divisions": 4, "mark_at": [0.75], "label_at": ["1/2"]})
        out.append(("1/2" not in got,
                    f"number line: a fraction label naming the wrong point goes: {got}"))

        # A mark off the end of the line is clipped away by the axes, so its
        # label would float over empty space. Both go.
        got = labels({"type": "number_line", "from": 0, "to": 10,
                      "divisions": 10, "mark_at": [50], "label_at": ["50"]})
        out.append(("50" not in got,
                    f"number line: a mark past the end is dropped, label and all: {got}"))
    finally:
        Axes.text = original
        diagrams.CACHE_DIR = old_cache
    return out


def _teaching_checks() -> list:
    """The mini-lesson half: an example pointing at a picture that is missing.

    Guided examples are optional so they are dropped. The worked example is
    not (the formatter always renders one), so a clean guided example is
    promoted into its place, and only a mini-lesson with nothing usable left
    is discarded outright.
    """
    from booklet_gen.pipeline import BookletPipeline
    from booklet_gen.schemas import SubtopicTeaching, WorkedExample

    pipe = object.__new__(BookletPipeline)     # no API key needed for this
    out = []

    def teaching(worked, guided):
        return SubtopicTeaching(
            intro_paragraphs=["Volume is length times width times height."],
            worked_example=worked, guided_examples=guided)

    good = WorkedExample(question="Find the volume of a 2 cm by 3 cm by 4 cm box.",
                         steps=["2 x 3 x 4"], answer="24 cubic cm")
    orphan = WorkedExample(question="How many cubes are needed to build this object?",
                           steps=["Count the layers."], answer="12")
    drawn = WorkedExample(question="How many cubes are needed to build this object?",
                          steps=["Count the layers."], answer="12",
                          image_path="output/diagrams/whatever.png")

    t = pipe._drop_orphan_examples(teaching(good, [orphan, good]), "Volume")
    out.append((t is not None and len(t.guided_examples) == 1
                and t.worked_example is good,
                "a guided example with no picture is dropped, the rest kept"))

    t = pipe._drop_orphan_examples(teaching(orphan, [good]), "Volume")
    out.append((t is not None and t.worked_example is good
                and t.guided_examples == [],
                "an orphaned worked example is replaced by a clean guided one"))

    t = pipe._drop_orphan_examples(teaching(orphan, []), "Volume")
    out.append((t is None,
                "nothing usable left: the mini-lesson is dropped, not printed"))

    t = pipe._drop_orphan_examples(teaching(drawn, [good]), "Volume")
    out.append((t is not None and t.worked_example is drawn
                and len(t.guided_examples) == 1,
                "the same example WITH a picture is untouched"))

    t = pipe._drop_orphan_examples(teaching(good, [good, good]), "Volume")
    out.append((t is not None and len(t.guided_examples) == 2,
                "a clean mini-lesson is untouched"))
    return out


def _pipeline_checks() -> list:
    """The guards wired into the real question path, not just called directly.

    A guard nothing calls is worth nothing, and both of these live in a loop
    that is easy to reorder by accident.
    """
    from booklet_gen.agents.validator import SympyValidator, ValidationResult
    from booklet_gen.agents.reasoning_validator import ReasoningValidator
    from booklet_gen.pipeline import BookletPipeline
    from booklet_gen.schemas import Question, QuestionSet, Subtopic
    from booklet_gen.visuals import diagrams

    questions = [
        Question(question="How many cubes are needed to build this object?",
                 answer="12", working="3 layers of 4."),
        # Verified, correctly worked, and impossible. The judge marks the
        # arithmetic and the arithmetic is right; only the world is wrong.
        Question(question="A large stadium can hold 9,900,009 spectators. "
                          "Write this capacity in words.",
                 answer="Nine million, nine hundred thousand and nine",
                 working="Read the digits in groups of three."),
        Question(question="Calculate 3/8 + 2/8.", answer="5/8",
                 working="3 + 2 = 5, keep the denominator."),
        Question(question="A storage box is built using 24 cubic blocks. The base "
                          "is 4 blocks by 3 blocks. How many layers high is the box?",
                 answer="2", working="24 / 12 = 2.",
                 diagram_spec={"type": "cuboid", "length": 4, "width": 3,
                               "height": 2, "unit": "blocks"}),
    ]

    class _Generator:
        def generate(self, *a, **kw):
            return QuestionSet(questions=[q.model_copy(deep=True) for q in questions])

    class _Judge:
        def validate(self, *a, **kw):
            return ValidationResult(True, "stub judge")

        def validate_batch(self, subject, year_level, qs, chunks=None):
            return [ValidationResult(True, "stub judge") for _ in qs]

    pipe = object.__new__(BookletPipeline)
    pipe._generator = _Generator()
    pipe._judge = _Judge()
    pipe._sympy = SympyValidator()
    pipe._reasoning = ReasoningValidator()
    pipe._max_generation_attempts = 1

    tmp = Path(tempfile.mkdtemp(prefix="folio-pipeline-check-"))
    old_cache = diagrams.CACHE_DIR
    diagrams.CACHE_DIR = tmp
    try:
        out = pipe._generate_and_validate(
            "Mathematics", "Year 5", "Volume", Subtopic(name="Volume of prisms"), [])
    finally:
        diagrams.CACHE_DIR = old_cache

    kept = [vq.question.question for vq in out]
    checks = [(
        len(out) == 2 and not any("this object" in k for k in kept),
        f"{len(out)} of 4 questions kept: the one pointing at a missing "
        "figure was dropped",
    ), (
        not any("9,900,009" in k for k in kept),
        "and the stadium holding ten million people never reaches the page",
    )]

    box = [vq for vq in out if "storage box" in vq.question.question]
    spec = box[0].question.diagram_spec if box else {}
    checks.append((
        bool(box) and spec.get("unknown") == ["height"] and box[0].image_path,
        f"the find-the-height question keeps its drawing with the height "
        f"hidden (unknown={spec.get('unknown')})",
    ))
    return checks


# A figure with the wrong number of dimensions. The critic's complaint was the
# absence of diagrams; this is the failure that arrives with them. A child who
# reads "volume" off a flat rectangle learns that a box is a square.
DIMENSION_CASES = [
    ({"type": "rectangle", "length": 5, "width": 4},
     "Find the volume of a box 5 cm long, 4 cm wide and 3 cm high.",
     False, "a flat rectangle cannot show a volume"),
    ({"type": "cuboid", "length": 5, "width": 4, "height": 3},
     "Find the volume of a box 5 cm long, 4 cm wide and 3 cm high.",
     True, "a cuboid can"),
    ({"type": "cuboid", "length": 8, "width": 5, "height": 2},
     "Find the area of a rectangle 8 cm long and 5 cm wide.",
     False, "a solid cannot show a flat area"),
    ({"type": "rectangle", "length": 8, "width": 5},
     "What is the perimeter of a rectangle 8 cm long and 5 cm wide?",
     True, "perimeter is flat"),

    # The wider library. Every new shape that carries a measurement had to be
    # classified, or the guard waves it through and the whole rule is only
    # enforced for the four types it happened to be written against.
    ({"type": "triangle", "base": 10, "height": 6},
     "Find the volume of a prism with a triangular cross-section.",
     False, "a flat triangle cannot show a volume"),
    ({"type": "triangle", "base": 10, "height": 6},
     "Find the area of a triangle with base 10 cm and height 6 cm.",
     True, "but it can show the area"),
    ({"type": "grid_area", "width": 6, "height": 4},
     "Find the area of this shape by counting squares.",
     True, "counting squares is a flat area"),
    ({"type": "circle", "radius": 7},
     "Find the volume of a cylinder with radius 7 cm.",
     False, "a flat circle cannot show a cylinder's volume"),
    ({"type": "shape_3d", "solids": ["cube"]},
     "Find the volume of this solid.",
     True, "a drawn solid can"),
    ({"type": "shape_3d", "solids": ["cube"]},
     "Find the area of a rectangle 8 cm long and 5 cm wide.",
     False, "and cannot show a flat area"),
    # A net is a flat drawing OF a solid, which makes it the right picture for
    # surface area and the wrong thing to reject as flat. It is in neither set
    # for that reason, so the guard leaves it alone.
    ({"type": "net", "solid": "cube", "edge": 4},
     "Find the surface area of a cube with edge 4 cm.",
     True, "a net is the textbook figure for surface area"),
    ({"type": "cuboid", "length": 4, "width": 4, "height": 4},
     "Find the surface area of a cube with edge 4 cm.",
     True, "and so is the solid itself"),
    ({"type": "rectangle", "length": 4, "width": 4},
     "Find the surface area of a cube with edge 4 cm.",
     False, "but one face of it is not"),
    ({"type": "cuboid", "length": 3, "width": 3, "height": 3},
     "Find the surface area of this cube.",
     True, "surface area is a property of a solid"),
    ({"type": "rectangle", "length": 3, "width": 3},
     "Find the surface area of this cube.",
     False, "and so cannot be drawn flat"),
    ({"type": "cuboid", "length": 20, "width": 10, "height": 10},
     "A tank holds 2 litres of water.",
     True, "capacity is a solid"),
    # Left alone rather than risk dropping a good figure.
    ({"type": "circle_slices", "slices": 4, "shaded": 3},
     "What fraction of the circle is shaded?",
     True, "a question naming neither is not ours to judge"),
    ({"type": "cuboid", "length": 5, "width": 4, "height": 3},
     "Find the area of the base, then use it to find the volume.",
     True, "a question naming both is not ours to judge"),
]


# A lesson example that answers a question about a reading printed below it.
# year5-english-sample.pdf page 11: "Let's do this one together, In 'The Last
# Bus to Mullaloo', what can you infer is in the warm paper bag? Answer: Hot
# food she bought with her bus money" with the story starting in the box
# immediately underneath.
# (example question, example answer, should_be_dropped, note)
SPOILER_CASES = [
    ("In 'The Last Bus to Mullaloo', what can you infer is in the paper bag?",
     "Hot food she bought with her bus money", True,
     "the shipped case: a quoted title"),
    ("In The Last Bus to Mullaloo, how does Tess feel?", "Anxious", True,
     "an unquoted title still gives it away"),
    ("What does 'From the Diary of Alice Weir' suggest about the town?",
     "That it is failing", True, "the second reading, quoted"),
    ("Read this: 'The dog barked twice, then sat.' What can you infer?",
     "It had heard something", False, "the lesson carrying its own excerpt"),
    ("In 'A Walk to School', what happens first?", "She misses the bus", False,
     "a title this section does not define"),
    ("What does the word steadily suggest?", "Calmness", False,
     "no title named at all"),
]


def main() -> int:
    failures = 0

    print("Answer consistency")
    print("-" * 62)
    for answer, working, want in ANSWER_CASES:
        got, why = answer_is_trustworthy(answer, working)
        ok = got == want
        failures += not ok
        label = "trusted" if got else "rejected"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:8} {answer[:26]!r:30} {why or ''}")

    print("\nLesson examples that spoil a reading")
    print("-" * 62)
    passages = [SimpleNamespace(title="The Last Bus to Mullaloo"),
                SimpleNamespace(title="From the Diary of Alice Weir")]
    for question, answer, want, note in SPOILER_CASES:
        example = SimpleNamespace(question=question, answer=answer)
        got = example_spoils_passage(example, passages)
        ok = got == want
        failures += not ok
        verdict = "dropped" if got else "kept"
        print(f"  {'ok  ' if ok else 'FAIL'}  {verdict:8} {note}")

    print("\nDiagram dimensionality")
    print("-" * 62)
    for spec, question, want, note in DIMENSION_CASES:
        got = diagram_dimensionality_matches(spec, question)
        ok = got == want
        failures += not ok
        kept = "kept" if got else "dropped"
        print(f"  {'ok  ' if ok else 'FAIL'}  {kept:8} {note}")

    print("\nDiagram reconciliation")
    print("-" * 62)
    for spec, question, want, note in DIAGRAM_CASES:
        _, changed = reconcile_diagram_spec(dict(spec), question)
        ok = changed == want
        failures += not ok
        label = "corrected" if changed else "unchanged"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:10} {note}")

    print("\nDiagram answer leakage")
    print("-" * 62)
    for spec, question, want, note in LEAK_CASES:
        out, _ = reconcile_diagram_spec(dict(spec), question)
        got = sorted(out.get("unknown") or [])
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  hidden={str(got):<12} {note or question[:44]}")

    rendered = _render_checks()
    print("\nRendered labels (the number must not reach the page)")
    print("-" * 62)
    for ok, line in rendered:
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")

    print("\nMissing figure")
    print("-" * 62)
    for text, has_image, want, note in FIGURE_CASES:
        phrase = refers_to_missing_figure(text, has_image)
        ok = bool(phrase) == want
        failures += not ok
        label = f"drop ({phrase})" if phrase else "keep"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:26} "
              f"{note or text[:40]}")

    print("\nImpossible real-world quantities")
    print("-" * 62)
    for text, want_drop, note in MAGNITUDE_CASES:
        reason = implausible_magnitude(text)
        ok = bool(reason) == want_drop
        failures += not ok
        label = "drop" if reason else "keep"
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:5} {note or text[:44]}")
        if reason and not ok:
            print(f"          {reason}")

    wiring = _pipeline_checks()
    print("\nPipeline wiring")
    print("-" * 62)
    for ok, line in wiring:
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")

    teaching = _teaching_checks()
    print("\nTeaching examples")
    print("-" * 62)
    for ok, line in teaching:
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")

    total = (len(ANSWER_CASES) + len(SPOILER_CASES) + len(DIMENSION_CASES) + len(DIAGRAM_CASES)
             + len(LEAK_CASES) + len(FIGURE_CASES) + len(MAGNITUDE_CASES)
             + len(rendered) + len(wiring) + len(teaching))
    print(f"\n{total - failures}/{total} behaved as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
