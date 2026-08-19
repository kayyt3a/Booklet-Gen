"""Checks that "Now let's try one together" never hands over its own answer.

The guided box is the only teaching box the child writes in. It works only
because something is missing from it, and the [[value]] convention in
blanks.py is how the generator says what: a ruled gap on the student's page,
the value in bold in the answer key.

A rendered Year 5 booklet broke that in the one way the formatter cannot see.
The Answer line was correctly blanked, and two lines above it a step read "so
the order is 3,105, 3,142, 3,190". The child reads the answer, writes it in
the gap, and learns nothing. A second render logged "guided example has no
[[blanks]]", which is the same defect arriving by the other route: no markers
at all, so the box prints a finished demonstration under a heading that says
"together".

Neither is repairable by prompting alone, because both are the model being
locally reasonable, so `consistency.seal_guided_example` blanks the leak
before the teaching leaves the agent. This checks that guard through the real
agent, with a canned model reply standing in for the API, because that is the
seam every booklet actually passes through.

What this file does NOT cover: how a [[value]] is drawn once it exists. That
is `scripts/check_guided_blanks.py`.

    PYTHONPATH=. python scripts/check_guided_answer_leak.py
"""
import json
import sys
from pathlib import Path

from booklet_gen.agents.intro_writer import IntroWriterAgent
from booklet_gen.blanks import fill_in, has_blanks, plain_gap
from booklet_gen.schemas import Subtopic

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


class CannedModel:
    """Returns one prepared mini-lesson, so this check needs no API key."""

    def __init__(self, guided):
        self._guided = guided

    def complete(self, system, user, tier="strong", temperature=0.4):
        return json.dumps({
            "intro_paragraphs": ["Compare numbers digit by digit."],
            "key_points": ["Start at the biggest place value."],
            "mnemonic": None,
            "worked_example": {
                "question": "Order 41, 38 and 45 from smallest to largest.",
                "steps": ["Compare the tens: 3 is smallest, so 38 comes first.",
                          "41 and 45 both have 4 tens, so compare the ones."],
                "answer": "38, 41, 45",
            },
            "guided_examples": self._guided,
        })


def teach(guided, subject="Mathematics"):
    """One mini-lesson, straight through the agent that builds every booklet."""
    return IntroWriterAgent(CannedModel(guided)).write(
        subject, "Year 5", "Number", Subtopic(name="Ordering numbers"))


def student_page(example) -> str:
    """What the child reads: the steps with every [[value]] taken out."""
    return "\n".join(plain_gap(s) for s in example.steps)


def marker_page(example) -> str:
    """What the answer key prints: the same steps, completed."""
    return "\n".join(fill_in(s) for s in example.steps) + "\n" + fill_in(example.answer)


print("\nA STEP CANNOT PRINT THE ANSWER THE GAP BELOW IT ASKS FOR")

# The defect as it shipped: answer correctly blanked, answer also spelled out
# in the working above it.
LEAKED = [{
    "question": "Order 3,142, 3,105 and 3,190 from smallest to largest.",
    "steps": ["All three start with 3 thousand, so compare the hundreds.",
              "1 hundred is smallest, so the order is 3,105, 3,142, 3,190."],
    "answer": "[[3,105, 3,142, 3,190]]",
}]
guided = teach(LEAKED).guided_examples[0]
assert "3,105, 3,142, 3,190" not in student_page(guided), (
    "the guided example still prints its answer in its own steps, two lines "
    f"above the gap that asks for it:\n{student_page(guided)}")
ok("a step that restated the answer comes back with it blanked")

assert "3,105, 3,142, 3,190" in marker_page(guided), (
    "the value was hidden from the child and from the marker too, so nobody "
    f"can tell what belongs in the gap:\n{marker_page(guided)}")
ok("the value it blanked is still in the answer key")

assert "compare the hundreds" in student_page(guided).lower(), (
    "the method was blanked along with the answer: the child is now guessing "
    f"rather than working:\n{student_page(guided)}")
ok("the words that teach the method are left on the page")

print("\nA GUIDED EXAMPLE WITH NO MARKERS AT ALL STILL ASKS SOMETHING")

# The "guided example has no [[blanks]]" warning. Left alone this is a second
# fully worked demonstration under a heading that says "together".
BARE = [{
    "question": "A crate is 8 m long, 2 m wide and 3 m high. Find its volume.",
    "steps": ["Multiply the length by the width: 8 x 2 = 16.",
              "Multiply that by the height: 16 x 3 = 48."],
    "answer": "48 m3",
}]
guided = teach(BARE).guided_examples[0]
assert any(has_blanks(s) for s in guided.steps), (
    "a guided example arrived with no [[ ]] anywhere and left that way, so "
    "the child is handed a finished demonstration and copies its last number "
    f"into the Answer gap:\n{guided.steps}")
assert has_blanks(guided.answer), (
    f"the Answer line prints the answer: {guided.answer!r}")
page = student_page(guided)
assert "48" not in page, f"the result is still on the child's page:\n{page}"
assert "8 x 2" in page and "16 x 3" in page, (
    "the numbers the question gave were blanked out, so the step cannot be "
    f"worked at all:\n{page}")
ok("every step's result is withheld, and every given number is kept")

print("\nAN EXAMPLE THAT WAS ALREADY RIGHT IS LEFT EXACTLY AS IT WAS")

# Over-blanking is the other way to ruin this box, so the guard has to be
# silent on the common case rather than merely quiet.
GOOD = [{
    "question": "Work out 27 x 3.",
    "steps": ["Multiply the ones: 7 x 3 = [[21]], write 1 and carry the [[2]].",
              "Multiply the tens: 2 x 3 = 6, add the carried 2 = [[8]]."],
    "answer": "[[81]]",
}]
guided = teach(GOOD).guided_examples[0]
assert guided.steps == GOOD[0]["steps"] and guided.answer == GOOD[0]["answer"], (
    "a correctly marked guided example was rewritten, which risks blanking "
    f"so much of the method that the child cannot follow it:\n{guided.steps}")
ok("a well-marked example passes through untouched")

print("\nTHE 'I DO' EXAMPLE IS NEVER TOUCHED")

teaching = teach(LEAKED)
assert not has_blanks(" ".join(teaching.worked_example.steps)), (
    "the worked example was blanked. It is the one complete model of the "
    "method on the page and the practice underneath starts from it, so a gap "
    f"here leaves the child nothing to copy:\n{teaching.worked_example.steps}")
assert "38, 41, 45" in teaching.worked_example.answer, (
    "the worked example's answer was withheld: it is a demonstration, not an "
    "exercise")
ok("the worked example keeps every value")

print("\nENGLISH: THE LINE THE ANSWER IS FOUND IN STAYS READABLE")

# Blanking the quoted evidence would hide the very thing the child is being
# taught to hunt for, so quoted text is exempt and only the step's own
# conclusion is blanked.
ENGLISH = [{
    "question": "Which word tells you how Ana left?",
    "steps": ["Look at the line \"she left reluctantly\".",
              "The word describing how she left is reluctantly."],
    "answer": "reluctantly",
}]
guided = teach(ENGLISH, subject="English").guided_examples[0]
page = student_page(guided)
assert "she left reluctantly" in page, (
    "the quoted line was blanked, so the child is asked to find a word in "
    f"text they cannot see:\n{page}")
assert not page.rstrip().endswith("is reluctantly."), (
    f"the conclusion still names the answer:\n{page}")
ok("the quoted evidence stays, the conclusion is blanked")

print("\nTHE PROMPT STILL ASKS FOR THIS, SO THE GUARD IS THE SECOND LINE")

for name in ("maths", "english", "reasoning", "science"):
    text = Path("booklet_gen/prompts", f"intro_writer_{name}.txt").read_text(
        encoding="utf-8")
    assert "NO step may print the final answer" in text, (
        f"intro_writer_{name}.txt no longer forbids restating the answer in a "
        "step, which leaves a deterministic repair as the only thing standing "
        "between the model and a booklet that answers itself")
    assert "never none" in text, (
        f"intro_writer_{name}.txt no longer requires a blanked result in "
        "every working step, so the model is free to return none at all")
ok("all four mini-lesson prompts still demand the blanks")

print(f"\nALL {_passed} GUIDED-ANSWER-LEAK CHECKS PASSED")
sys.exit(0)
