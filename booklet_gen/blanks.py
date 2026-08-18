"""The [[value]] convention: one string that knows both the gap and its answer.

A guided example's step and a cloze question's sentence have the same problem.
The page the child writes on needs a ruled gap; the answer key needs the word
that goes in it; and if those are two separate fields a model can fill them in
so they disagree, which prints a booklet whose own key is wrong.

So there is one string, and the missing part is wrapped: "The old man's
[[melancholy]] expression". Every renderer below reads that same string and
shows a different face of it.

This lives outside formatter.py because the LLM judge needs it too, and
formatter.py pulls in ReportLab. `plain_gap` is the reason: handing the judge
"[[melancholy]]" and asking whether "melancholy" is right makes the check
vacuous, so it grades the sentence with the word actually taken out.
"""
from __future__ import annotations

import re

BLANK_RE = re.compile(r"\[\[(.+?)\]\]")

# A gap is written into in pencil by a child, so it is sized for a hand rather
# than for the text it hides: the padding is what makes "21" comfortable to
# write rather than cramped. It still grows with the answer, so the gap hints
# at the size of what goes in it, and it is capped so a long phrase cannot push
# the line off the page.
PAD = 3
MIN_CHARS, MAX_CHARS = 6, 16

# What the judge and any other plain-text reader sees in place of the gap.
PLAIN_GAP = "_____"


def has_blanks(text: str) -> bool:
    return bool(BLANK_RE.search(text or ""))


def blank_out(escaped: str) -> str:
    """Replace every [[value]] with a ruled gap to write the value into."""
    def gap(m: re.Match) -> str:
        width = min(max(len(m.group(1)) + PAD, MIN_CHARS), MAX_CHARS)
        return f"<u>{'&nbsp;' * width}</u>"
    return BLANK_RE.sub(gap, escaped or "")


def fill_in(escaped: str) -> str:
    """Replace every [[value]] with the value, bold, for the answer key."""
    return BLANK_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped or "")


def strip_markers(text: str) -> str:
    """Keep the words, drop the brackets. For anywhere a gap makes no sense,
    such as the instruction that states what a question is asking."""
    return BLANK_RE.sub(r"\1", text or "")


def plain_gap(text: str) -> str:
    """Replace every [[value]] with a plain-text gap.

    For the validator. A cloze question whose sentence still carries the word
    hands the judge the answer it is being asked to check, so it agrees every
    time and the tick beside that answer means nothing.
    """
    return BLANK_RE.sub(PLAIN_GAP, text or "")
