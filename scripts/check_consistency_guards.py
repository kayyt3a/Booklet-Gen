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
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The guards below are supposed to log what they drop; the report is the
# output here, so keep the log lines out of it.
logging.disable(logging.CRITICAL)

from booklet_gen.agents.consistency import (          # noqa: E402
    answer_is_trustworthy, reconcile_diagram_spec, refers_to_missing_figure)

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
            drawn.clear()
            path = diagrams.render_diagram(dict(spec))
            assert path is not None and path.exists(), f"render failed: {spec}"
            return list(drawn)

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
        f"{len(out)} of 3 questions kept: the one pointing at a missing "
        "figure was dropped",
    )]

    box = [vq for vq in out if "storage box" in vq.question.question]
    spec = box[0].question.diagram_spec if box else {}
    checks.append((
        bool(box) and spec.get("unknown") == ["height"] and box[0].image_path,
        f"the find-the-height question keeps its drawing with the height "
        f"hidden (unknown={spec.get('unknown')})",
    ))
    return checks


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

    total = (len(ANSWER_CASES) + len(DIAGRAM_CASES) + len(LEAK_CASES)
             + len(FIGURE_CASES) + len(rendered) + len(wiring) + len(teaching))
    print(f"\n{total - failures}/{total} behaved as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
