"""Rough time estimates for a booklet, shown so a kid can see a section is
short and not feel discouraged.

These are deliberately gentle, round numbers, not stopwatch-accurate. The aim
is "this bit is about 10 minutes", not precision.
"""
from __future__ import annotations

# Minutes per practice question by difficulty. A child spends longer on a
# multi-step "hard" question than a quick "easy" one.
_PER_QUESTION = {"easy": 1.5, "medium": 2.5, "hard": 3.5}

# Reading the mini-lesson (intro, key points, worked example).
_LESSON_MINUTES = 2.0


def section_minutes(n_questions: int, has_lesson: bool, difficulty: str | None) -> float:
    per_q = _PER_QUESTION.get((difficulty or "medium").strip().lower(), 2.5)
    return (_LESSON_MINUTES if has_lesson else 0.0) + n_questions * per_q


def round_display(minutes: float) -> int:
    """Round a raw estimate to a friendly whole number, minimum 3."""
    return max(3, round(minutes))


def round_total(minutes: float) -> int:
    """Round a booklet total to the nearest 5 minutes, minimum 5."""
    return max(5, int(round(minutes / 5.0)) * 5)
