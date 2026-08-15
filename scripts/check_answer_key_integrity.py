"""Checks every writer is told the stated answer must match its own working.

A shipped Year 5 Final Challenge printed an answer key entry that argued with
itself. The headline read "Yes, the student is correct; the mean is 60
degrees." The working two lines below it derived the opposite: 45 + 65 = 110,
180 - 110 = 70, so the student's claim of 80 was wrong. A visible "Wait,
checking calculation:" sat in the middle of it, a thinking-out-loud artifact
that was never meant to reach a printed page a parent pays for.

Nothing in any prompt said the headline `answer` has to be the conclusion the
shown `working` actually reaches, or that reasoning happens before writing
rather than on the page. This is appended once in _shared.py rather than
copied into sixteen prompt files, so every writer (question generator,
challenge generator, exam generator, intro writer, across every subject)
carries the same discipline.

    PYTHONPATH=. python scripts/check_answer_key_integrity.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents._shared import PROMPT_DIR, load_prompt

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nEVERY WRITER IS TOLD THE ANSWER MUST MATCH ITS OWN WORKING")

sample = load_prompt("challenge_generator_maths.txt")

assert "conclusion the shown working actually reaches" in sample or \
       "conclusion the working" in sample, (
    "the loaded prompt does not require the stated answer to agree with the "
    "working shown for it, which is exactly how a Final Challenge shipped an "
    "answer key entry that argued with itself")
ok("the answer must be the conclusion the working actually reaches")

assert "'wait'" in sample.lower() or '"wait"' in sample.lower(), (
    "nothing forbids writing mid-thought self-corrections onto the page, so "
    "a 'Wait, checking calculation:' artifact can reach a printed answer key "
    "again")
ok("mid-thought artifacts ('wait', 'let me check', ...) are explicitly banned")

assert "constant regardless" in sample.lower(), (
    "nothing stops a 'justify by computing X' question where X cannot "
    "actually decide the claim, which is what happened here: the mean of a "
    "triangle's angles is always 60 degrees and proves nothing about any one "
    "angle, yet a question demanded it as the justification")
ok("a 'justify by computing X' question must use an X that can actually decide it")

print("\nEVERY PROMPT GETS IT, NOT JUST ONE FILE")

for name in ("question_generator_maths.txt", "intro_writer_english.txt",
             "exam_generator_methods.txt"):
    body = load_prompt(name)
    assert "conclusion the working" in body or \
           "conclusion the shown working actually reaches" in body, name
ok("the rule reaches question generators, intro writers and the exam "
   "generator too, not just the challenge prompt")

print(f"\nALL {_passed} ANSWER KEY INTEGRITY CHECKS PASSED")
sys.exit(0)
