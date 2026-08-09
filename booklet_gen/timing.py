"""Time estimates for a booklet.

`classwork_section_minutes` is the estimate that matters. It charges for
everything a student actually has to do in the session: reading the
mini-lesson, following the worked example, working through each guided
example, any reading passage, and each practice question sized by its own
difficulty, length and number of parts.

Both the pipeline and the formatter measure with it, and that is deliberate.
The pipeline trims Class Work against it (`CLASSWORK_CAP_MINUTES`) and the
formatter prints it, so the hour the booklet is trimmed to is the hour it
claims on the page. They used to disagree: the pipeline capped an hour of
*question* time while the printed number also counted the teaching, which is
how a booklet sold as a one hour lesson came to say "about 100 min".

Two consequences worth knowing before changing anything here:

* Teaching dominates. A mini-lesson with its worked and guided examples runs
  about twelve minutes before a single practice question, so trimming practice
  alone cannot always reach the cap and whole subtopics sometimes have to move
  to Homework.
* A subtopic that moves takes its mini-lesson with it, and its teaching time
  goes to Homework with it. `booklet_timing` charges a section to Class Work
  only when it still has classwork questions.

`section_minutes` is the older coarse heuristic, now used only for the warm-up
recap, which has no teaching to charge for.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Capacity heuristic (pipeline)
# ---------------------------------------------------------------------------

# Minutes per practice question by difficulty. A child spends longer on a
# multi-step "hard" question than a quick "easy" one.
_PER_QUESTION = {"easy": 1.5, "medium": 2.5, "hard": 3.5}

# Nominal allowance for a mini-lesson in the capacity heuristic.
_LESSON_MINUTES = 2.0


def section_minutes(n_questions: int, has_lesson: bool, difficulty: str | None) -> float:
    """Coarse question-capacity estimate, for the warm-up recap only.

    See the module docstring: Class Work is measured with
    `classwork_section_minutes`, which is what the booklet prints.
    """
    per_q = _PER_QUESTION.get((difficulty or "medium").strip().lower(), 2.5)
    return (_LESSON_MINUTES if has_lesson else 0.0) + n_questions * per_q


def round_display(minutes: float) -> int:
    """Round a raw estimate to a friendly whole number, minimum 3."""
    return max(3, round(minutes))


def round_total(minutes: float) -> int:
    """Round a booklet total to the nearest 5 minutes, minimum 5."""
    return max(5, int(round(minutes / 5.0)) * 5)


# ---------------------------------------------------------------------------
# Printed estimate (formatter)
# ---------------------------------------------------------------------------

# Base minutes for one practice question, before its length and parts are
# counted. A first attempt at a newly taught skill, not a drill.
_Q_BASE = {"easy": 1.2, "medium": 1.8, "hard": 2.6}

# Reading and parsing the question itself. A one-line "Simplify 9/12" and a
# forty-word word problem are not the same job, and charging both the same flat
# rate is what made the old homework estimate twice the truth.
_Q_MINUTES_PER_WORD = 1.0 / 40.0

# Each extra labelled part, "a) ... b) ...", is close to another question.
_Q_PER_EXTRA_PART = 0.7

# Where a question sits changes how long it takes, even for identical text.
#   classwork: first independent attempt, tutor still explaining
#   homework:  the same skill a second time, alone, faster
#   recap:     deliberately easy warm-up on old material
#   challenge: mixed and cumulative, so recall costs before working does
_CONTEXT_FACTOR = {
    "classwork": 1.0,
    "homework": 0.65,
    "recap": 0.8,
    "challenge": 1.2,
}

# Reading the mini-lesson prose: intro paragraphs, key points, mnemonic.
# Roughly 120 words a minute for a primary reader meeting new material, with a
# floor so a terse lesson is not charged at nothing.
_LESSON_WORDS_PER_MINUTE = 120.0
_LESSON_FLOOR_MINUTES = 0.8

# "I do": the student reads a solved problem and follows the reasoning.
_WORKED_BASE = 1.2
_WORKED_PER_STEP = 0.2

# "We do": the student actually attempts it alongside the tutor. This is the
# line the old estimate missed most: a guided example is not reading, it is
# working, and it costs about as much as a practice question.
_GUIDED_BASE = 2.2
_GUIDED_PER_STEP = 0.2

# Settling, marking and moving on between subtopics.
_SECTION_CHANGEOVER = 0.5


def _words(text: str | None) -> int:
    return len(text.split()) if text else 0


def question_minutes(question, context: str = "classwork") -> float:
    """Minutes for one practice question, from what the question actually says."""
    base = _Q_BASE.get((getattr(question, "difficulty", None) or "medium").strip().lower(), 1.8)
    text = getattr(question, "question", "") or ""
    minutes = base + _words(text) * _Q_MINUTES_PER_WORD
    minutes += _Q_PER_EXTRA_PART * max(0, _part_count(text) - 1)
    return minutes * _CONTEXT_FACTOR.get(context, 1.0)


def _part_count(text: str) -> int:
    # Imported lazily to keep this module free of formatter imports.
    from .formatter import part_labels
    return len(part_labels(text))


# Reading a passage costs time the questions do not: the first question under a
# passage is charged for reading it, at a primary reader's pace on unfamiliar
# text, and the rest are not, because by then it has been read.
_PASSAGE_WORDS_PER_MINUTE = 110.0
_PASSAGE_FLOOR_MINUTES = 0.5


def passage_minutes(passage) -> float:
    words = sum(_words(p) for p in (getattr(passage, "paragraphs", None) or ()))
    words += _words(getattr(passage, "title", None))
    if not words:
        return 0.0
    return max(_PASSAGE_FLOOR_MINUTES, words / _PASSAGE_WORDS_PER_MINUTE)


def section_passage_minutes(section, questions) -> float:
    """Reading time for every passage the given questions actually use."""
    from .formatter import passage_groups, section_passages
    return sum(passage_minutes(p)
               for p, group in passage_groups(questions, section_passages(section))
               if p is not None and group)


def questions_minutes(vqs, context: str = "classwork") -> float:
    return sum(question_minutes(vq.question, context) for vq in vqs)


def worked_example_minutes(we, guided: bool = False) -> float:
    base = _GUIDED_BASE if guided else _WORKED_BASE
    per_step = _GUIDED_PER_STEP if guided else _WORKED_PER_STEP
    steps = len(getattr(we, "steps", None) or ())
    return base + per_step * steps + _words(getattr(we, "question", "")) * _Q_MINUTES_PER_WORD


def teaching_minutes(teaching) -> float:
    """Minutes for one mini-lesson: prose, worked example, guided examples."""
    if teaching is None:
        return 0.0
    words = sum(_words(p) for p in (teaching.intro_paragraphs or ()))
    words += sum(_words(k) for k in (teaching.key_points or ()))
    words += _words(teaching.mnemonic)
    minutes = max(_LESSON_FLOOR_MINUTES, words / _LESSON_WORDS_PER_MINUTE)
    if teaching.worked_example is not None:
        minutes += worked_example_minutes(teaching.worked_example)
    for ge in teaching.guided_examples or ():
        minutes += worked_example_minutes(ge, guided=True)
    return minutes


def classwork_section_minutes(section) -> float:
    """Minutes for one Class Work section: lesson, reading, "now you try" set."""
    return (teaching_minutes(section.teaching)
            + section_passage_minutes(section, section.questions)
            + questions_minutes(section.questions, "classwork")
            + (_SECTION_CHANGEOVER if section.teaching is not None else 0.0))


# Homework is set once and worked "through the week", which in a real booklet
# meant 35 questions and one number. Split it into sittings of about this long,
# never more than a handful, so a parent can hand out one at a time.
_SESSION_TARGET_MINUTES = 20.0
_MAX_SESSIONS = 4
_MIN_QUESTIONS_TO_SPLIT = 6


def homework_minutes_in_order(data) -> list[float]:
    """Minutes for each homework question, in the order the page prints them.

    The first question under a reading carries that reading's time, exactly as
    Class Work charges it in `classwork_section_minutes`. Homework used to get
    its passages free, so a sitting containing two whole texts was billed as
    though the child already knew them: a Year 5 English booklet promised 19
    minutes for a sitting holding about 3.5 minutes of unbilled reading.

    A subtopic the hour could not fit prints its mini-lesson down here, and the
    first of its homework questions carries that lesson exactly as it carries a
    reading. `booklet_timing` has always charged Homework for those lessons, so
    leaving them out here made the sitting estimates add up to less than the
    total printed above them.
    """
    from .formatter import passage_groups, section_passages
    out: list[float] = []
    for s in data.sections:
        # Only a subtopic whose practice moved to Homework reprints its lesson;
        # one taught in the session does not.
        lesson = teaching_minutes(s.teaching) if not s.questions else 0.0
        first = True
        for passage, group in passage_groups(s.homework_questions,
                                             section_passages(s)):
            read = passage_minutes(passage) if passage is not None else 0.0
            for i, vq in enumerate(group):
                out.append(question_minutes(vq.question, "homework")
                           + (read if i == 0 else 0.0)
                           + (lesson if first else 0.0))
                first = False
    return out


def homework_session_plan(data) -> list[dict]:
    """Split the homework questions into sittings of roughly equal length.

    Returns a list of {"start", "end", "count", "minutes"} over the flat
    homework question list, in printed order. Empty when there is too little
    homework to be worth splitting.
    """
    mins = homework_minutes_in_order(data)
    n = len(mins)
    if n < _MIN_QUESTIONS_TO_SPLIT:
        return []
    total = sum(mins)
    k = max(2, min(_MAX_SESSIONS, int(round(total / _SESSION_TARGET_MINUTES))))
    k = min(k, n)

    cum, run = [], 0.0
    for m in mins:
        run += m
        cum.append(run)

    bounds: list[int] = []
    for j in range(1, k):
        thr = total * j / k
        # The tolerance matters: with questions of equal length the running
        # total lands exactly on the threshold, and float error would otherwise
        # push every boundary one question late.
        idx = next((i + 1 for i, c in enumerate(cum) if c >= thr - 1e-9), n)
        idx = max(idx, (bounds[-1] if bounds else 0) + 1)
        idx = min(idx, n - (k - j))
        if not bounds or idx > bounds[-1]:
            bounds.append(idx)

    starts = [0] + bounds
    ends = bounds + [n]
    return [{"start": s, "end": e, "count": e - s,
             "minutes": round_display(sum(mins[s:e]))}
            for s, e in zip(starts, ends) if e > s]


# A dictated spelling test: the adult says the word, the child writes it, both
# pause. Half a minute a word is what that costs in a real sitting, and a test
# that appears on the first page has to be in the number on the cover or the
# cover is wrong before the child starts.
_SPELLING_MINUTES_PER_WORD = 0.5


def spelling_test_minutes(data) -> float:
    from .formatter import spelling_test_spaces
    return spelling_test_spaces(getattr(data, "spelling_test", None)) \
        * _SPELLING_MINUTES_PER_WORD


def booklet_timing(data) -> dict:
    """Recompute every printed time from the booklet itself.

    Returns raw floats under `*_raw` and rounded display integers under the
    same keys the BookletData carries, plus `section_minutes` keyed by the
    index of each section.
    """
    # A section the hour could not fit has had its practice moved to Homework
    # and its mini-lesson goes with it, so it is not part of the session and
    # its teaching time is not charged to Class Work. Charging it there is what
    # made a booklet the pipeline had trimmed to 58 minutes print "about 67".
    section_raw = [classwork_section_minutes(s) if s.questions else 0.0
                   for s in data.sections]
    classwork_raw = sum(section_raw)
    # One source of truth for the Homework half. This is the same list the
    # sitting bands are cut from, so the total printed on the Homework band and
    # the sittings printed underneath it cannot drift apart. It already counts
    # the readings those questions are about (Class Work always charged for a
    # passage and Homework never did) and the mini-lesson of any subtopic whose
    # practice moved down here.
    homework_raw = sum(homework_minutes_in_order(data))
    recap_raw = questions_minutes(data.recap_questions, "recap")
    challenge_raw = questions_minutes(data.challenge_questions, "challenge")
    # The Final Challenge is printed inside the Homework half, so its time
    # belongs to that heading.
    homework_total_raw = homework_raw + challenge_raw
    spelling_raw = spelling_test_minutes(data)
    return {
        "spelling_raw": spelling_raw,
        "spelling_minutes": round_display(spelling_raw) if spelling_raw else None,
        "section_raw": section_raw,
        "section_minutes": [round_display(m) for m in section_raw],
        "recap_raw": recap_raw,
        "classwork_raw": classwork_raw,
        "homework_raw": homework_total_raw,
        "challenge_raw": challenge_raw,
        "recap_minutes": round_display(recap_raw) if data.recap_questions else None,
        "classwork_minutes": round_display(classwork_raw) if data.sections else None,
        "homework_minutes": round_display(homework_total_raw) if homework_total_raw else None,
        # The homework questions on their own, without the Final Challenge that
        # is printed after them under its own band and its own estimate. The
        # Homework band prints this one: a band that said "179 min in total"
        # over sittings of 31, 31, 31 and 29 was counting the challenge, and the
        # lessons, in a number the page then contradicted.
        "homework_only_raw": homework_raw,
        "homework_only_minutes": round_display(homework_raw) if homework_raw else None,
        "challenge_minutes": round_display(challenge_raw) if data.challenge_questions else None,
        "total_minutes": round_total(spelling_raw + recap_raw + classwork_raw
                                     + homework_total_raw),
    }
