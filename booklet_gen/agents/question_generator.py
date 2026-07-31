from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import QuestionSet, Subtopic, SubtopicTeaching
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


_SUBJECT_PROMPT_FILES = {
    "mathematics": "question_generator_maths.txt",
    "maths": "question_generator_maths.txt",
    "math": "question_generator_maths.txt",
    "science": "question_generator_science.txt",
    "english": "question_generator_english.txt",
    "reasoning": "question_generator_reasoning.txt",
    "verbal reasoning": "question_generator_reasoning.txt",
    "quantitative reasoning": "question_generator_reasoning.txt",
}


def _prompt_file_for(subject: str) -> str:
    key = subject.strip().lower()
    if key not in _SUBJECT_PROMPT_FILES:
        raise ValueError(f"No question generator prompt configured for subject {subject!r}")
    return _SUBJECT_PROMPT_FILES[key]


def teaching_block(teaching) -> str:
    """Render the mini-lesson into the question generator's user turn.

    Without this the two agents were blind to each other: they took identical
    arguments and neither saw the other's output, so nothing in either prompt
    could make the practice exercise the skill the worked example had just
    modelled. A real booklet taught equivalent fractions by multiplying up and
    then asked the child to divide down in all nine questions.

    Read defensively (getattr, tolerate empties) so a partial or stubbed
    teaching object degrades to a shorter block rather than raising: questions
    are the product, teaching is the garnish, and a booklet with no lesson must
    still generate.
    """
    if teaching is None:
        return ""
    lines: list[str] = []

    mnemonic = getattr(teaching, "mnemonic", None)
    if mnemonic:
        lines.append(f"Name the lesson gave the method: {mnemonic}")

    key_points = list(getattr(teaching, "key_points", None) or [])
    if key_points:
        lines.append("Key points taught:")
        lines.extend(f"  - {kp}" for kp in key_points)

    worked = getattr(teaching, "worked_example", None)
    worked_q = getattr(worked, "question", "") if worked is not None else ""
    if worked_q:
        lines.append(f'Worked example the tutor models ("I do"): {worked_q}')
        steps = list(getattr(worked, "steps", None) or [])
        if steps:
            lines.append("  Solved by these steps:")
            lines.extend(f"  - {s}" for s in steps)

    guided = [g for g in (getattr(teaching, "guided_examples", None) or [])
              if getattr(g, "question", "")]
    if guided:
        lines.append('Guided examples the student follows along with ("we do"):')
        lines.extend(f"  - {g.question}" for g in guided)

    if not lines:
        return ""

    return (
        "\n\nTHE MINI-LESSON PRINTED DIRECTLY ABOVE THESE QUESTIONS. The "
        "student reads it, then turns to your questions with nothing else to "
        "work from:\n"
        + "\n".join(lines)
        + "\n\nSo the set you write must:\n"
        "- Practise THE METHOD SHOWN ABOVE, in the direction the worked "
        "example ran. A lesson that finds equivalent fractions by multiplying "
        "up, followed by questions that all ask the student to divide down, "
        "asks for a skill nobody taught them.\n"
        "- Cover a second direction or variant ONLY if the lesson above "
        "covered it too, and never in the opening questions.\n"
        "- Never restate an example above. The same problem with new names, or "
        "the same numbers wrapped in a story, is not practice: its answer is "
        "already worked out on the page in front of the student. Change the "
        "numbers AND the situation.\n"
        "- Use the vocabulary, notation and method name the lesson used, so "
        "the student recognises what they are being asked to do."
    )


class QuestionGeneratorAgent:
    def __init__(self, client: LLMClient, max_retries: int = 3, questions_per_subtopic: int = 5):
        self._client = client
        self._max_retries = max_retries
        self._n = questions_per_subtopic
        # Cache system prompts per subject so we don't reload on every subtopic.
        self._system_by_subject: dict[str, str] = {}

    def _system_prompt(self, subject: str) -> str:
        key = subject.strip().lower()
        if key not in self._system_by_subject:
            self._system_by_subject[key] = load_prompt(_prompt_file_for(subject))
        return self._system_by_subject[key]

    def generate(
        self,
        subject: str,
        year_level: str,
        topic: str,
        subtopic: Subtopic,
        reference_chunks: list[str] | None = None,
        teaching: SubtopicTeaching | None = None,
    ) -> QuestionSet:
        """Generate a question set for one subtopic.

        `teaching` is the mini-lesson that will be printed above these
        questions, when one was written. Pass it whenever it exists: it is the
        only thing that lets the practice exercise the skill the lesson taught.
        It stays optional because the intro writer is allowed to fail (the
        pipeline logs `intro_failed` and carries on), and a booklet of
        questions with no lesson is still a booklet.
        """
        system = self._system_prompt(subject)
        base_user = (
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Topic: {topic}\n"
            f"Subtopic: {subtopic.name}\n"
            f"Target difficulty: {subtopic.difficulty_hint}\n"
            f"Question types: {', '.join(subtopic.question_types) or 'any suitable'}\n"
            f"Generate exactly {self._n} questions."
        )
        base_user += teaching_block(teaching)
        if reference_chunks:
            joined = "\n\n---\n\n".join(reference_chunks)
            base_user += (
                "\n\nReference material (real textbook/exam excerpts at this level — "
                "use these to calibrate style, phrasing, and difficulty; do NOT copy "
                "questions verbatim):\n\n" + joined
            )
        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = base_user if not error_feedback else (
                f"{base_user}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object matching the schema."
            )
            log.info(
                "question_generator.attempt",
                extra={"attempt": attempt, "subject": subject, "subtopic": subtopic.name,
                       "has_teaching": teaching is not None},
            )
            raw = self._client.complete(system, user, tier="strong", temperature=0.6)
            try:
                data = extract_json(raw)
                qs = QuestionSet.model_validate(data)
                if not qs.questions:
                    raise ValueError("empty questions array")
                log.info(
                    "question_generator.success",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name, "count": len(qs.questions)},
                )
                return qs
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning(
                    "question_generator.retry",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name, "error": error_feedback[:300]},
                )
        raise RuntimeError(
            f"Question generator failed for {subject}/{subtopic.name!r} after {self._max_retries} attempts: {error_feedback}"
        )
