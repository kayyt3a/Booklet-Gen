"""How much reading a maths question may demand of the child it is set for.

Correct in the prompt, verified on the page. The maths prompts carry a reading
budget per year band, and until this module existed nothing measured whether
the model kept it, so a model that ignored the budget produced exactly the
booklet that shipped before the budget was written. A real Year 1 booklet went
out with a 39-word question in it, and Year 1 was carrying about seventy per
cent of Year 7's reading load: at Year 1 the maths is one addition and the
question is the obstacle, which is a reading comprehension test wearing a maths
costume. A child who can add 10 and 6 fails it on decoding, and neither they
nor their parent can tell which of the two things went wrong.

The numbers here are NOT new. They are transcribed from the two prompts that
ask for them, and scripts/check_reading_load.py parses both prompt files and
fails if this table has drifted from either, so there is one set of numbers
with two copies rather than two sets.

What this module does NOT do is shorten a question. Every other deterministic
guard in the codebase either repairs something the student never sees
(`_dedash`, `reconcile_diagram_spec`) or drops an item outright
(`question_states_its_answer`, `implausible_magnitude`), and the reason those
drop rather than edit is that the answer key was written against the exact
wording. Reading load is the same: cutting words out of a question can change
what it asks, and a key that no longer matches its question is a worse fault
than a long question. So the action is to drop, and only when dropping leaves
a set the child can actually read. See `hold_to_budget`.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..timing import year_number

log = logging.getLogger(__name__)

# The subject engines these budgets were written for. English is deliberately
# absent: a comprehension question is read next to a passage that is itself
# hundreds of words by design, and the English prompt budgets the PASSAGE
# instead. Reasoning is absent for the same kind of reason, its items are
# puzzles whose text is the puzzle.
_MATHS_SUBJECTS = {"mathematics", "maths", "math"}

# (top year of the band, at most this many words in one question).
# From booklet_gen/prompts/question_generator_maths.txt, the "STAY INSIDE THE
# YEAR LEVEL" bands. Practice, and the warm-up recap, which is written by the
# same agent from the same prompt.
PRACTICE_MAX_WORDS: tuple[tuple[int, int], ...] = (
    (2, 12), (4, 25), (6, 35), (8, 50), (10, 60),
)
# The same prompt's target average for a set, which is a set-level property and
# so is reported rather than enforced: dropping questions until a mean falls
# would empty a section to fix a number.
PRACTICE_AVERAGE_WORDS: tuple[tuple[int, int], ...] = (
    (2, 8), (4, 14), (6, 20), (8, 25), (10, 30),
)
# From booklet_gen/prompts/challenge_generator_maths.txt. Deliberately looser:
# a Final Challenge question is multi-step, so it is allowed more words, and
# the prompt is explicit that harder still never means longer to read.
CHALLENGE_MAX_WORDS: tuple[tuple[int, int], ...] = (
    (2, 15), (4, 30), (6, 45), (8, 60), (10, 75),
)

BUDGETS = {
    "practice": PRACTICE_MAX_WORDS,
    "challenge": CHALLENGE_MAX_WORDS,
}

# A word is a whitespace-separated run holding at least one letter or digit, so
# a lone "=" or "?" is not counted and "27 508" is two. Calibrated against the
# prompt's own worked example: it calls "A basket has 10 apples and 6 oranges.
# How many pieces of fruit are there altogether?" sixteen words and its rewrite
# nine, and this counts them sixteen and nine. A counter that disagreed with
# the prompt would be enforcing a different rule from the one asked for.
_WORD = re.compile(r"[^\W_]", re.UNICODE)


def word_count(text: str | None) -> int:
    return sum(1 for run in (text or "").split() if _WORD.search(run))


def applies(subject: str | None) -> bool:
    """Whether a reading budget is defined for this subject engine."""
    return (subject or "").strip().lower() in _MATHS_SUBJECTS


def max_words(year_level: str | None, kind: str = "practice") -> Optional[int]:
    """The budget for one question, or None when there is no band for it.

    None for a year the bands do not cover (Years 11 and 12 sit above them,
    and an unparseable label is a caller error), and None is permissive: this
    module never invents a limit the prompts did not ask for.
    """
    year = year_number(year_level)
    if year is None:
        return None
    for top, words in BUDGETS.get(kind, PRACTICE_MAX_WORDS):
        if year <= top:
            return words
    return None


def average_words(year_level: str | None) -> Optional[int]:
    year = year_number(year_level)
    if year is None:
        return None
    for top, words in PRACTICE_AVERAGE_WORDS:
        if year <= top:
            return words
    return None


def over_budget(text: str, year_level: str | None, subject: str | None,
                kind: str = "practice") -> Optional[str]:
    """Why this question reads too long for its year, or None.

    Returns a reason string rather than a bool, matching the other guards in
    `consistency.py`, so a caller's log line says what was wrong without
    recomputing it.
    """
    if not applies(subject):
        return None
    limit = max_words(year_level, kind)
    if limit is None:
        return None
    n = word_count(text)
    if n <= limit:
        return None
    return f"{n} words against a {limit} word budget for {year_level}"


def hold_to_budget(questions, year_level: str | None, subject: str | None,
                   keep_at_least: int = 0, kind: str = "practice",
                   text_of=lambda q: getattr(q, "question", "") or ""):
    """Drop the questions that read too long, while the set can spare them.

    Returns (kept, dropped). Order is preserved in `kept`, because the
    generator sorts a set easiest to hardest and the ramp is worth keeping.

    Two rules, and both are about not making the booklet worse than the fault:

    * `keep_at_least` is never broken. A subtopic that keeps four questions is
      a promise on the pricing page, and a mini-lesson with nothing under it is
      a defect this codebase has already paid for once.
    * Dropping is a repair, not a diet. Either every offending question in the
      set can go, in which case they all go and what is left is inside the
      year's reading age, or none of them does. Taking the worst two out of six
      offenders leaves a child with four questions they still cannot read and
      the parent with a thinner booklet: the reading load is unchanged per
      question and the practice is gone. In that case the set is kept whole and
      the caller logs it, which is the honest option rather than the tidy one.
      The alternative is regenerating the set, and that is a whole extra API
      call per subtopic spent on a model that has just shown it is not reading
      the instruction, with the customer waiting on it.

    A question that FITS is never dropped, so a set only ever loses reading the
    child could not have done.
    """
    items = list(questions)
    if not applies(subject):
        return items, []
    limit = max_words(year_level, kind)
    if limit is None:
        return items, []
    doomed = {i for i, q in enumerate(items) if word_count(text_of(q)) > limit}
    if not doomed:
        return items, []
    if len(items) - len(doomed) < max(0, keep_at_least):
        return items, []
    kept = [q for i, q in enumerate(items) if i not in doomed]
    dropped = [q for i, q in enumerate(items) if i in doomed]
    return kept, dropped
