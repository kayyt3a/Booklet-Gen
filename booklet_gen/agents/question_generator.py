from __future__ import annotations

import logging
from typing import List
from uuid import uuid4

from pydantic import Field, ValidationError

from ..llm import LLMClient
from ..schemas import Passage, QuestionSet, Subtopic, SubtopicTeaching
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


# Subjects whose questions hang off a shared block of reading. Maths and
# reasoning questions carry their own stimulus, so asking those engines for
# passages would only invite them to pad the question with prose.
_PASSAGE_SUBJECTS = {"english"}


def wants_passages(subject: str) -> bool:
    return subject.strip().lower() in _PASSAGE_SUBJECTS


class PassageQuestionSet(QuestionSet):
    """A question set that may carry the reading its questions ask about.

    This lives here rather than in schemas.py because it is the wire shape of
    one agent's reply, not part of the booklet contract. Once the pipeline has
    bound the ids, passages live on `SubtopicOutput.passages`.

    It subclasses QuestionSet so every existing caller that only reads
    `.questions` keeps working, including the maths and reasoning paths where
    `passages` is always empty.
    """
    passages: List[Passage] = Field(default_factory=list)


def passage_block(subject: str, total: int, classwork_count: int | None) -> str:
    """Instruct an English generation call to write real passages.

    English booklets used to smuggle comprehension into the question string,
    which capped a "passage" at the sentence or two you can reasonably put in a
    question field, and gave every question its own scrap of text. The product
    wants the opposite: a few substantial readings, each carrying several
    questions.

    `classwork_count` is the split point the pipeline will cut at. Telling the
    model where the cut falls is what lets it group its questions so a passage
    is not left half in Class Work and half in Homework. The pipeline does not
    trust it to comply (see `_passage_safe_split`), but a model that knows the
    boundary lands on it most of the time, and that produces a better booklet
    than any amount of downstream repair.
    """
    if not wants_passages(subject):
        return ""

    lines = [
        "",
        "",
        "READING PASSAGES. Alongside `questions`, return a top-level "
        "`passages` array. Each entry is:",
        '  {"id": "p1", "title": "short title", "paragraphs": ["...", "..."]}',
        "A question that asks about a passage sets `\"passage_id\": \"p1\"` and "
        "does NOT repeat the passage text inside the question field.",
        "",
        "Rules for this set:",
        "- Write 2 passages, and hang at least 3 questions off each. A passage "
        "with one question is a sentence with extra steps, which is exactly "
        "the problem this replaces.",
        "- Make them substantial: 3 to 5 paragraphs for Year 5 and above, 2 to "
        "3 short paragraphs for the early years. A reader should have to "
        "search the text to answer, not glance at it.",
        "- Vary what the questions ask of one passage: a locate-the-fact "
        "question, an inference, a vocabulary-in-context question, and one "
        "about tone, purpose or the writer's choices.",
        "- The passage is printed immediately above the questions that cite "
        "it, so refer to it as \"the passage\". Never write \"the passage "
        "below\", and never write \"the passage above\" in a question that "
        "sets no passage_id.",
        "- Questions that need no reading (grammar, punctuation, spelling "
        "conventions) simply omit passage_id. Do not invent a passage for them.",
        "- Every passage you list must be cited by at least one question, and "
        "every passage_id a question uses must exist in `passages`.",
    ]
    if classwork_count and 0 < classwork_count < total:
        lines.append(
            f"- The first {classwork_count} questions print in Class Work and "
            f"the remaining {total - classwork_count} print in Homework. Order "
            "your questions so that all of a passage's questions sit on the "
            "same side of that boundary: one passage for Class Work, one for "
            "Homework."
        )
    return "\n".join(lines)


def bind_passages(qs: PassageQuestionSet) -> PassageQuestionSet:
    """Make this call's passage ids unique, and clear references that dangle.

    Every call labels its first passage "p1". The pipeline pools passages from
    several calls into one subtopic (a retry regenerates a failed question and
    brings its own reading with it), and two subtopics of the same booklet pool
    into one PDF, so raw ids collide and a question would resolve to somebody
    else's reading. Rewriting them per call is the cheapest way to make the ids
    mean something.

    A passage_id with nothing behind it is cleared rather than kept: the
    question then renders as an ordinary self-contained item, which is a
    smaller defect than a reference the formatter cannot resolve.
    """
    prefix = uuid4().hex[:8]
    remap: dict[str, str] = {}
    kept: list[Passage] = []
    for i, p in enumerate(qs.passages or [], 1):
        text = [para.strip() for para in (p.paragraphs or []) if para and para.strip()]
        if not p.id or not text:
            continue
        new_id = f"{prefix}-{i}"
        remap[p.id] = new_id
        kept.append(Passage(id=new_id, title=p.title, paragraphs=text))

    cited: set[str] = set()
    for q in qs.questions:
        if not q.passage_id:
            continue
        new_id = remap.get(q.passage_id)
        if new_id is None:
            log.info("question_generator.dangling_passage_id",
                     extra={"passage_id": q.passage_id[:40]})
            q.passage_id = None
            continue
        q.passage_id = new_id
        cited.add(new_id)

    qs.passages = [p for p in kept if p.id in cited]
    return qs


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
        classwork_count: int | None = None,
        allow_passages: bool = True,
    ) -> PassageQuestionSet:
        """Generate a question set for one subtopic.

        `teaching` is the mini-lesson that will be printed above these
        questions, when one was written. Pass it whenever it exists: it is the
        only thing that lets the practice exercise the skill the lesson taught.
        It stays optional because the intro writer is allowed to fail (the
        pipeline logs `intro_failed` and carries on), and a booklet of
        questions with no lesson is still a booklet.

        `classwork_count` is where the pipeline will split this set into Class
        Work and Homework, passed through so an English set can group its
        passage questions on one side of the cut.

        `allow_passages` is False for parts of the booklet that have nowhere to
        print a passage: the warm-up recap is a handful of loose questions with
        no section around it, so a passage generated there could never be read.
        """
        passages_wanted = allow_passages and wants_passages(subject)
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
        if passages_wanted:
            base_user += passage_block(subject, self._n, classwork_count)
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
                       "has_teaching": teaching is not None,
                       "passages": passages_wanted},
            )
            raw = self._client.complete(system, user, tier="strong", temperature=0.6)
            try:
                data = extract_json(raw)
                qs = PassageQuestionSet.model_validate(data)
                if not qs.questions:
                    raise ValueError("empty questions array")
                if passages_wanted:
                    qs = bind_passages(qs)
                else:
                    # Nowhere to print reading here, so a passage_id could only
                    # ever dangle.
                    qs.passages = []
                    for q in qs.questions:
                        q.passage_id = None
                log.info(
                    "question_generator.success",
                    extra={"attempt": attempt, "subject": subject,
                           "subtopic": subtopic.name, "count": len(qs.questions),
                           "passages": len(qs.passages)},
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
