from __future__ import annotations

import logging
import os
import re
import threading
from collections import Counter
from typing import Optional

from .agents.challenge_generator import ChallengeGeneratorAgent
from .agents.exam_generator import ExamGeneratorAgent
from .agents.intro_writer import IntroWriterAgent
from .agents.llm_judge import LLMJudgeValidator
from .agents.outline_parser import OutlineParserAgent
from .agents.question_generator import (QuestionGeneratorAgent, passage_quotas,
                                        wants_passages)
from .agents.reasoning_validator import ReasoningValidator
from .agents.spelling import SpellingAgent
from .agents import tables as tables_agent
from .agents.term_planner import TermPlannerAgent
from .agents.validator import SympyValidator, ValidationResult
from . import curriculum
from .timing import (section_minutes, round_display, round_total,
                     classwork_section_minutes, session_plan)
from .config import Config, load_config
from .llm import get_client
from .rag import Retriever
from .schemas import (
    BookletData, ExamPaper, ExamSection, Passage, Question, SpellingList,
    SubtopicOutput, SubtopicTeaching, TablesList, ValidatedQuestion,
)

# The longest a Class Work section may ever be, whatever the year level. A
# tutoring session runs an hour, so this is that hour; anything past it moves to
# Homework rather than being cut.
#
# It is a CEILING now, not the cap itself. `timing.session_plan` sets the cap
# for the student's year (thirty minutes for a Year 1, sixty for a Year 9) and
# `_session_cap` takes whichever of the two is smaller, so a deployment that
# lowers FOLIO_CLASSWORK_CAP_MINUTES still shortens every booklet and no year
# band can ever raise its own session above the hour.
#
# One flat hour for every year is what produced five booklets in a row, Years 1
# to 9, with fifty-two to fifty-seven minutes of class work each. The hour was
# never wrong for Year 9. It was applied to a six year old.
CLASSWORK_CAP_MINUTES = int(os.environ.get("FOLIO_CLASSWORK_CAP_MINUTES", "60"))

# Never teach fewer than this many subtopics in a session, even if the cap
# says so. A booklet that teaches one thing is not worth an hour of a
# tutor's time, and at that point the honest answer is a longer session.
MIN_CLASSWORK_SUBTOPICS = int(os.environ.get("FOLIO_MIN_CLASSWORK_SUBTOPICS", "3"))

# ---------------------------------------------------------------------------
# What one credit buys
#
# These two are the product promise, printed on the pricing page, and the hour
# cap gives way to them rather than the other way round.
#
# They exist because the shipped booklets did not keep either. A Year 3 maths
# booklet taught six subtopics and printed "Now you try:" followed by ONE
# question under every one of them, because the cap fitter's floor was a single
# question and six mini-lessons filled the hour on their own. A mini-lesson
# with one question after it is a demonstration, not practice.
#
# Holding a floor of four means fewer subtopics fit the hour, and that is the
# trade being made deliberately: three things practised properly beat six
# things shown once. The subtopics that no longer fit are not lost, they move
# to Homework with their mini-lessons attached.
MIN_NOW_YOU_TRY = int(os.environ.get("FOLIO_MIN_NOW_YOU_TRY", "4"))

# Distinct top-level topics the class work must still cover. Three subtopics
# all sitting under "Number" is not three topics, and MIN_CLASSWORK_SUBTOPICS
# alone would allow exactly that.
MIN_CLASSWORK_TOPICS = int(os.environ.get("FOLIO_MIN_CLASSWORK_TOPICS", "3"))

# WACE ATAR examination shape: two sections, 35% calculator-free and 65%
# calculator-assumed, 10 minutes reading plus 150 minutes working time.
EXAM_SPEC = {
    "reading_minutes": 10,
    "sections": (
        # (name, calculator_allowed, marks, working_minutes)
        ("Section One: Calculator-free", False, 52, 50),
        ("Section Two: Calculator-assumed", True, 98, 100),
    ),
}

log = logging.getLogger(__name__)


class _WithQuestions:
    """A read-only view of a subtopic with a different question list.

    Lets the fitter cost a hypothetical ("what if this section kept only one
    question?") without mutating the real section or copying its teaching.
    """
    __slots__ = ("_section", "questions")

    def __init__(self, section, questions):
        self._section = section
        self.questions = questions

    def __getattr__(self, name):
        return getattr(self._section, name)


class _SeenQuestions:
    """Question texts already accepted into THIS booklet, shared by every part.

    The dedupe used to be a plain set rebuilt inside each subtopic, so it could
    only ever see one subtopic's own questions. Subtopics run concurrently and
    shared nothing, which is how a booklet shipped sixteen consecutive volume
    questions: each subtopic was internally clean and none of them could see the
    others. The recap and the Final Challenge were blind in the same way.

    Concurrency: `add` is the one atomic check-and-claim, so two threads that
    generate the same text cannot both keep it. `__contains__` is a deliberately
    unlocked read used by the regeneration loop, where a stale answer costs at
    worst one extra retry and never a wrong result.
    """

    __slots__ = ("_seen", "_lock")

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def __contains__(self, norm: str) -> bool:
        return norm in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, norm: str) -> bool:
        """Claim `norm`. True if it was new, False if another caller has it."""
        with self._lock:
            if norm in self._seen:
                return False
            self._seen.add(norm)
            return True


class BookletPipeline:
    def __init__(
        self,
        config: Optional[Config] = None,
        # Classwork "Now you try" per subtopic. Matches MIN_NOW_YOU_TRY: asking
        # for fewer than the floor guarantees the floor cannot be met.
        questions_per_subtopic: int = MIN_NOW_YOU_TRY,
        homework_per_subtopic: int = 4,        # homework practice per subtopic
        recap_questions: int = 4,              # warm-up recap at the start (0 to disable)
        challenge_questions: int = 5,
        max_generation_attempts: int = 3,
        max_workers: int = 4,
        client=None,
        retriever: Optional[Retriever] = None,
    ):
        self._config = config or load_config()
        self._client = client or get_client(self._config)
        self._parser = OutlineParserAgent(self._client, self._config.max_retries,
                                          min_topics=MIN_CLASSWORK_TOPICS)
        self._intro = IntroWriterAgent(self._client, self._config.max_retries)
        self._n_classwork = max(1, questions_per_subtopic)
        self._n_homework = max(0, homework_per_subtopic)
        # One generation call per subtopic yields both classwork and homework,
        # then we split it, so homework costs no extra API calls.
        self._generator = QuestionGeneratorAgent(
            self._client,
            max_retries=self._config.max_retries,
            questions_per_subtopic=self._n_classwork + self._n_homework,
        )
        self._challenger = ChallengeGeneratorAgent(self._client, self._config.max_retries)
        self._exam = ExamGeneratorAgent(self._client, self._config.max_retries)
        self._term_planner = TermPlannerAgent(self._client, self._config.max_retries)
        self._speller = SpellingAgent(self._client, self._config.max_retries)
        self._sympy = SympyValidator()
        self._reasoning = ReasoningValidator()
        self._judge = LLMJudgeValidator(self._client)
        self._max_generation_attempts = max_generation_attempts
        self._max_workers = max(1, max_workers)
        self._n_challenge = challenge_questions
        self._n_recap = max(0, recap_questions)
        self._retriever = retriever if retriever is not None else Retriever()

    def run(self, description: str, student_name: str) -> BookletData:
        log.info("pipeline.start", extra={"description": description})
        outline = self._parser.parse(description)
        log.info(
            "pipeline.outline",
            extra={"subject": outline.subject, "year_level": outline.year_level,
                   "topics": len(outline.topics)},
        )
        # One seen-set for the whole booklet: a question the student has
        # already answered under one heading is not practice under another.
        seen = _SeenQuestions()
        sections, covered, rag_pool = self._generate_from_outline(outline, seen)
        recap = self._build_recap(outline.subject, outline.year_level, None,
                                  rag_pool, seen, covered=covered)
        challenge = self._build_challenge(
            outline.subject, outline.year_level, covered, rag_pool, seen,
        )
        # Free-text booklets carry a year level too ("Year 8 maths, fractions"),
        # so they are sized by it exactly as a program booklet is.
        self._fit_classwork_to_cap(sections, session_plan(outline.year_level))
        t = self._timing(sections, challenge, recap)
        return BookletData(
            subject=outline.subject,
            year_level=outline.year_level,
            student_name=student_name,
            sections=sections,
            recap_questions=recap,
            challenge_questions=challenge,
            recap_minutes=t["recap_minutes"],
            classwork_minutes=t["classwork_minutes"],
            homework_minutes=t["homework_minutes"],
            challenge_minutes=t["challenge_minutes"],
            total_minutes=t["total_minutes"],
        )

    def run_program(
        self,
        program_key: str,
        year_level: str,
        student_name: str,
        subject: Optional[str] = None,
        topic: Optional[str] = None,
        week_number: Optional[int] = None,
        total_weeks: Optional[int] = None,
        prev_focus: Optional[str] = None,
    ) -> BookletData:
        """Generate a booklet for a product line (Scholarships / NAPLAN
        Practice / Academic Accelerate). Multi-subject programs (NAPLAN) run
        each subject engine and merge the results into one booklet.
        `prev_focus` (a term plan's previous week) drives the warm-up recap."""
        from .programs import get_program, normalise_subject, ACCELERATE_SUBJECTS

        program = get_program(program_key)
        authoring_guidance = program.authoring_guidance(year_level) or ""
        if program.pick_subject:
            norm = normalise_subject(subject or "")
            if norm is None:
                raise ValueError(
                    f"{program.label} needs a subject "
                    f"({', '.join(ACCELERATE_SUBJECTS)}); got {subject!r}"
                )
            subjects = (norm,)
            # Through the program rather than straight off the subject, so one
            # function decides how a product names a subject on its cover.
            # Accelerate maps nothing, so this is the engine name it always
            # was; a product that does have its own word gets it here too.
            subject_display = program.display_for(norm)
        else:
            subjects = program.subjects
            subject_display = program.subject_display

        log.info("pipeline.program.start",
                 extra={"program": program.key, "subjects": list(subjects),
                        "year_level": year_level})

        all_sections: list[SubtopicOutput] = []
        all_challenge: list[ValidatedQuestion] = []
        all_recap: list[ValidatedQuestion] = []
        # Spans the subjects too: one booklet, one set of questions. A term
        # plan gets a fresh set per week, which is right, since revisiting a
        # skill a week later is the point of the recap.
        seen = _SeenQuestions()
        for subj in subjects:
            description = program.describe(subj, year_level, topic)
            # What this year level actually contains, per subject, appended
            # after the product guide so the product's own rule is read first
            # and the curriculum qualifies it. Per subject because NAPLAN runs
            # maths and English through this loop and they share nothing.
            #
            # Without it "Year 10" was a bare string and the model filled it in
            # from a general sense of school maths, which runs years low: a
            # shipped Year 10 booklet taught expanding 4(2x + 5) and Pythagoras,
            # both of which are Year 8.
            subject_guidance = authoring_guidance + curriculum.guidance_block(
                subj, year_level)
            outline = self._parser.parse(description, subject_guidance)
            # Force the engine subject: the parser normalises to a known
            # subject, but the program is authoritative about which engine runs.
            outline.subject = subj
            sections, covered, rag_pool = self._generate_from_outline(
                outline,
                seen,
                authoring_guidance=subject_guidance,
                use_rag=program.use_rag,
            )
            generation_context = (
                [subject_guidance, *rag_pool]
                if subject_guidance else rag_pool
            )
            for s in sections:
                s.subject = subj
            all_sections.extend(sections)
            # Recap revises the previous week (term plan) or earlier skills.
            all_recap.extend(self._build_recap(subj, year_level, prev_focus,
                                              generation_context, seen,
                                              covered=covered))
            all_challenge.extend(
                self._build_challenge(
                    subj, year_level, covered, generation_context, seen,
                )
            )

        # Sized for the student's year, not for a flat hour. A NAPLAN booklet
        # merges two subjects into one session, so the cap is applied to the
        # merged list once, after both halves exist.
        plan = session_plan(year_level)
        self._fit_classwork_to_cap(all_sections, plan)
        log.info("pipeline.session_plan",
                 extra={"year_level": year_level, "band": plan.band,
                        "cap": self._session_cap(plan),
                        "subtopics_taught": sum(1 for s in all_sections if s.questions),
                        "classwork_questions": sum(len(s.questions) for s in all_sections),
                        "homework_questions": sum(len(s.homework_questions)
                                                  for s in all_sections),
                        "challenge": len(all_challenge),
                        "recap": len(all_recap)})
        # Last line of defence on the cover. The trimmer above is now the thing
        # that keeps both halves of a two-subject booklet, but the cover should
        # not be able to name a subject that is not inside it whatever happens
        # three steps upstream.
        subject_display = self._honest_subject_display(
            subject_display, subjects, all_sections,
            loose_questions=[*all_recap, *all_challenge],
            display_by_subject=program.subject_display_by_subject)
        t = self._timing(all_sections, all_challenge, all_recap)
        return BookletData(
            subject=subject_display,
            year_level=year_level,
            student_name=student_name,
            sections=all_sections,
            recap_questions=all_recap,
            challenge_questions=all_challenge,
            recap_minutes=t["recap_minutes"],
            classwork_minutes=t["classwork_minutes"],
            homework_minutes=t["homework_minutes"],
            challenge_minutes=t["challenge_minutes"],
            total_minutes=t["total_minutes"],
            program_label=program.label,
            week_number=week_number,
            total_weeks=total_weeks,
            week_focus=topic,
        )

    def run_term_plan(
        self,
        program_key: str,
        year_level: str,
        student_name: str,
        subject: Optional[str] = None,
        weeks: int = 10,
        topic_hint: Optional[str] = None,
    ) -> list[BookletData]:
        """Generate a whole term: one booklet per week, progressing in
        difficulty, with the last weeks as revision. Returns one BookletData
        per week (write each to its own PDF).

        This is the only place spelling can be set, because spelling is the one
        part of a booklet that depends on the booklet before it: week N's list
        is set at the back of week N, and week N+1 opens with a test on it.
        A single booklet generated on its own has no previous week, so it
        carries neither (see `run_program`, which never touches those fields).
        """
        from .programs import get_program, normalise_subject

        program = get_program(program_key)
        plan_subject = (
            normalise_subject(subject or "") or "Mathematics"
            if program.pick_subject else (program.subjects[0] if program.subjects else "Mathematics")
        )
        plan = self._term_planner.plan(
            program.label, plan_subject, year_level, weeks, topic_hint,
        )
        spelling = self._spelling_applies(program, plan_subject)
        tables = tables_agent.tables_apply(program, plan_subject, year_level)
        log.info("pipeline.term_plan.start",
                 extra={"program": program.key, "weeks": len(plan.weeks),
                        "spelling": spelling, "tables": tables})

        booklets: list[BookletData] = []
        prev_focus = None
        # Every word set so far this term, so no two weeks share a list.
        words_set: list[str] = []
        prev_list: SpellingList | None = None
        prev_list_week: int | None = None
        # Same idea for tables, except the carried list is cleared the moment
        # it is tested, so a week that sets nothing cannot make the week after
        # it re-test a table the student has already been examined on.
        tables_set: list[int] = []
        prev_table: TablesList | None = None
        prev_table_week: int | None = None
        last_week = plan.weeks[-1].week if plan.weeks else None
        for wk in plan.weeks:
            log.info("pipeline.term_plan.week",
                     extra={"week": wk.week, "focus": wk.focus, "difficulty": wk.difficulty})
            data = self.run_program(
                program_key, year_level, student_name,
                subject=subject, topic=wk.focus,
                week_number=wk.week, total_weeks=len(plan.weeks),
                prev_focus=prev_focus,   # recap revises last week
            )
            if spelling:
                # The test comes first: it is on the list carried in from the
                # previous booklet, and `prev_list` is still that one until
                # this week's list replaces it below. Week 1 has no previous
                # list, so it gets a list and no test.
                data.spelling_test = self._speller.make_test(prev_list, prev_list_week)
                this_list = self._speller.generate_list(
                    year_level, wk.week, words_set, wk.focus,
                )
                if this_list.words:
                    data.spelling_list = this_list
                    words_set.extend(this_list.words)
                    prev_list, prev_list_week = this_list, wk.week
                else:
                    # No list this week means no test next week rather than a
                    # test on a list two weeks old wearing the wrong week
                    # number. `prev_list` is left alone, so next week tests the
                    # last list actually printed and says which week it came
                    # from.
                    log.warning("pipeline.term_plan.no_spelling_list",
                                extra={"week": wk.week})
                log.info("pipeline.term_plan.spelling",
                         extra={"week": wk.week,
                                "list": len(this_list.words),
                                "test": len(data.spelling_test.words)
                                if data.spelling_test else 0,
                                "test_from_week": data.spelling_test.from_week
                                if data.spelling_test else None})
            if tables:
                # Test first, on the table carried in from the previous
                # booklet, then clear it: one table, tested exactly once.
                data.tables_test = tables_agent.make_test(prev_table,
                                                          prev_table_week)
                prev_table, prev_table_week = None, None
                # Nothing new in a revision week or in the final week. Both
                # would set a table that no later booklet exists to test, which
                # is homework the student is never asked about.
                if not wk.revision and wk.week != last_week:
                    this_table = tables_agent.next_list(tables_set, year_level)
                    if this_table:
                        data.tables_list = this_table
                        tables_set.append(this_table.table)
                        prev_table, prev_table_week = this_table, wk.week
                log.info("pipeline.term_plan.tables",
                         extra={"week": wk.week,
                                "set": data.tables_list.table
                                if data.tables_list else None,
                                "tested": data.tables_test.table
                                if data.tables_test else None,
                                "test_from_week": data.tables_test.from_week
                                if data.tables_test else None})
            booklets.append(data)
            # What the week actually taught, not what it was planned to teach.
            # wk.focus is the planner's label, written before generation: the
            # outline parser chooses the real subtopics, and the hour cap can
            # drop some of them, so next week's recap was revising a heading
            # rather than the lessons the student sat through.
            taught = [s.subtopic for s in data.sections if s.subtopic]
            prev_focus = "; ".join(taught) if taught else wk.focus
        return booklets

    def run_plan_week(
        self,
        program_key: str,
        year_level: str,
        student_name: str,
        *,
        week: int,
        total_weeks: int,
        subject: Optional[str] = None,
        focus: Optional[str] = None,
        prev_focus: Optional[str] = None,
        prev_spelling_words: Optional[list[str]] = None,
        prev_spelling_week: Optional[int] = None,
        words_already_set: Optional[list[str]] = None,
        prev_table: Optional[int] = None,
        prev_table_week: Optional[int] = None,
        tables_already_set: Optional[list[int]] = None,
        is_revision: bool = False,
    ) -> BookletData:
        """One week of a stored study plan, generated on its own.

        `run_term_plan` generates ten weeks in a single call and carries the
        state between them in local variables, which is why spelling and times
        tables could only ever live there. A customer generating week 4 in
        August, having printed week 3 in July, needs that same carry-over
        without the other nine booklets, so the state arrives as arguments
        instead: the caller reads it back from the plan's history and hands it
        in. Nothing here touches the database.

        The week is never inferred. A tutor generating week 1 for a fifth
        student must get week 1 again, so this makes no attempt to work out
        "the next" week; it generates the week it is given.
        """
        from .programs import get_program, normalise_subject

        program = get_program(program_key)
        plan_subject = (
            normalise_subject(subject or "") or "Mathematics"
            if program.pick_subject
            else (program.subjects[0] if program.subjects else "Mathematics")
        )
        spelling = self._spelling_applies(program, plan_subject)
        tables = tables_agent.tables_apply(program, plan_subject, year_level)
        log.info("pipeline.plan_week.start",
                 extra={"program": program.key, "week": week,
                        "total_weeks": total_weeks, "focus": focus,
                        "spelling": spelling, "tables": tables})

        data = self.run_program(
            program_key, year_level, student_name,
            subject=subject, topic=focus,
            week_number=week, total_weeks=total_weeks,
            prev_focus=prev_focus,
        )

        if spelling:
            # The test is on the words the previous week actually set, which
            # the caller read out of the plan. No previous week means a list
            # and no test, exactly as week 1 of a term plan behaves.
            previous = (SpellingList(words=list(prev_spelling_words))
                        if prev_spelling_words else None)
            data.spelling_test = self._speller.make_test(previous,
                                                         prev_spelling_week)
            this_list = self._speller.generate_list(
                year_level, week, list(words_already_set or []), focus,
            )
            if this_list.words:
                data.spelling_list = this_list
            else:
                log.warning("pipeline.plan_week.no_spelling_list",
                            extra={"week": week})

        if tables:
            previous_table = TablesList(table=prev_table) if prev_table else None
            data.tables_test = tables_agent.make_test(previous_table,
                                                      prev_table_week)
            # A revision week and the last week of the plan set nothing: the
            # table would be homework no later booklet ever tests. Same rule
            # run_term_plan applies, for the same reason.
            if not is_revision and week != total_weeks:
                this_table = tables_agent.next_list(
                    list(tables_already_set or []), year_level)
                if this_table:
                    data.tables_list = this_table

        log.info("pipeline.plan_week.done",
                 extra={"week": week,
                        "spelling_set": len(data.spelling_list.words)
                        if data.spelling_list else 0,
                        "spelling_tested": len(data.spelling_test.words)
                        if data.spelling_test else 0,
                        "tables_set": data.tables_list.table
                        if data.tables_list else None,
                        "tables_tested": data.tables_test.table
                        if data.tables_test else None})
        return data

    def plan_ladder(self, program_key: str, year_level: str,
                    subject: Optional[str] = None, weeks: int = 10,
                    topic_hint: Optional[str] = None) -> list[dict]:
        """The week-by-week skill ladder for a plan, planned once up front.

        Planning the whole term when the plan is created, rather than a week
        at a time on demand, is what makes the weeks a curriculum rather than
        ten unrelated booklets: the planner can order them so each builds on
        the last, and it cannot repeat itself later because it has already
        committed to every week.
        """
        from .programs import get_program, normalise_subject

        program = get_program(program_key)
        plan_subject = (
            normalise_subject(subject or "") or "Mathematics"
            if program.pick_subject
            else (program.subjects[0] if program.subjects else "Mathematics")
        )
        plan = self._term_planner.plan(
            program.label, plan_subject, year_level, weeks, topic_hint,
        )
        return [{"week": w.week, "focus": w.focus,
                 "difficulty": w.difficulty, "revision": bool(w.revision)}
                for w in plan.weeks]

    @staticmethod
    def _spelling_applies(program, plan_subject: str) -> bool:
        """Whether this product line gets a weekly spelling list.

        Spelling is a literacy routine. It rides on booklets that teach
        English, which is Academic Accelerate with English picked and NAPLAN
        Practice (whose literacy half is English). A maths or reasoning booklet
        with twenty words at the back is noise the parent has to explain.
        """
        subjects = (plan_subject,) if program.pick_subject else program.subjects
        return any((s or "").strip().lower() == "english" for s in subjects)

    def run_exam(
        self,
        year_level: str,
        student_name: str,
        subject: str = "Mathematics Methods",
        topic_focus: Optional[str] = None,
        unit: Optional[str] = "Units 3 and 4",
    ) -> ExamPaper:
        """Generate a full practice ATAR examination paper.

        Sections run concurrently (each is one network-bound call plus batched
        validation). Questions that fail validation are dropped rather than
        regenerated: on an exam paper a wrong marking key is worse than a
        slightly short section, and the calculus validator now catches the
        derivative/integral errors that matter most here.
        """
        log.info("pipeline.exam.start",
                 extra={"subject": subject, "year_level": year_level,
                        "topic_focus": topic_focus})

        specs = EXAM_SPEC["sections"]

        def build(spec) -> ExamSection:
            name, calc, marks, minutes = spec
            from .programs import reviewed_external_content_enabled
            chunks = (
                self._retrieve(subject, year_level, "exam", topic_focus or name)
                if reviewed_external_content_enabled() else []
            )
            draft = self._exam.generate_section(
                subject, year_level, name, calc, marks,
                topic_focus=topic_focus, reference_chunks=chunks,
            )
            verdicts = self._validate_many(
                "Mathematics", year_level, draft.questions, chunks,
            )
            kept: list[ValidatedQuestion] = []
            for q, r in zip(draft.questions, verdicts):
                image_path, image_attr = self._resolve_visual(q)
                orphan = self._orphan_figure(q.question, image_path)
                if orphan:
                    log.info("pipeline.exam.drop_missing_figure",
                             extra={"section": name, "phrase": orphan})
                    continue
                kept.append(ValidatedQuestion(
                    question=q, verified=self._trusted(q, r.verified),
                    validator_notes=r.notes,
                    image_path=str(image_path) if image_path else None,
                    image_attribution=image_attr,
                ))
            log.info("pipeline.exam.section",
                     extra={"section": name, "questions": len(kept),
                            "marks": sum(q.question.marks or 0 for q in kept),
                            "verified": sum(1 for q in kept if q.verified)})
            return ExamSection(
                name=name, calculator_allowed=calc,
                description=(
                    "Calculators are not permitted. Give answers in exact form."
                    if not calc else
                    "Calculators satisfying SCSA conditions are permitted."
                ),
                questions=kept, working_minutes=minutes,
            )

        if self._max_workers > 1 and len(specs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(self._max_workers, len(specs))) as ex:
                sections = list(ex.map(build, specs))
        else:
            sections = [build(s) for s in specs]

        return ExamPaper(
            subject=subject,
            year_level=year_level,
            student_name=student_name,
            unit=unit,
            sections=sections,
            reading_minutes=EXAM_SPEC["reading_minutes"],
            materials=[
                "To be provided by the supervisor: this Question/Answer booklet, "
                "and the Formula sheet.",
                "To be provided by the candidate: pens, pencils, eraser, ruler, "
                "and a highlighter.",
                "Special items for Section Two: drawing instruments, templates, "
                "notes on two unfolded sheets of A4 paper, and up to three "
                "calculators satisfying SCSA conditions.",
            ],
        )

    @staticmethod
    def _honest_subject_display(subject_display, subjects, sections,
                                loose_questions=(), display_by_subject=None) -> str:
        """The cover line, narrowed to the subjects the booklet really holds.

        `subject_display` is a fixed string chosen from the program before a
        single question exists ("Numeracy and Literacy"), while the contents are
        whatever survived generation and the cap fitter. That gap is the general
        shape of the defect the subject-aware trimmer fixes: a booklet that says
        one thing on its cover and contains another, with no error anywhere to
        say so.

        The trimmer is the fix. This is the guarantee, and it is enforced at the
        point of printing rather than three steps upstream, so any future path
        that loses a subject (a subject engine returning nothing, a validator
        rejecting a whole half) cannot get a false cover past it either.

        It narrows rather than raises. A parent who has paid and waited is
        better served by a booklet correctly labelled as the half that
        generated than by no booklet at all, and the error log is what tells
        support the other half went missing.

        `loose_questions` is the Warm-up Recap and the Final Challenge: every
        question in the booklet that has no section around it. They are counted
        because narrowing the cover is a claim about the WHOLE booklet, and
        those two parts are generated per subject and merged into one flat
        list. A NAPLAN booklet whose sections came out maths-only but whose
        Final Challenge still holds five literacy items was covered
        "Mathematics", printed over English questions, which is the same defect
        this guard exists to prevent, pointing the other way.

        `display_by_subject` is the product's own word for each subject
        (`Program.subject_display_by_subject`). Narrowing without it swaps
        vocabularies as well as scope: a maths-only NAPLAN booklet came out
        covered "Mathematics" when the product, its menu and the test it
        practises for all call that half "Numeracy". Telling the truth and
        speaking the product's language are not a trade. Without a mapping it
        falls back to the engine names, which is what it did before.
        """
        declared = [s for s in subjects if s]
        if len(declared) < 2:
            return subject_display
        present = {s.subject for s in sections}
        present.update(vq.question.subject for vq in loose_questions)
        missing = [s for s in declared if s not in present]
        if not missing:
            return subject_display
        words = display_by_subject or {}
        kept = [s for s in declared if s in present]
        narrowed = " and ".join(words.get(s) or s for s in kept) or subject_display
        log.error("pipeline.subject_missing_from_booklet",
                  extra={"declared": declared, "missing": missing,
                         "cover_was": subject_display,
                         "cover_now": narrowed})
        return narrowed

    @staticmethod
    def _floor_questions(section) -> int:
        """The fewest classwork questions this subtopic can be left with.

        MIN_NOW_YOU_TRY, normally. All of them when the section is a single
        reading, since a reading and its questions are never separated: such a
        section keeps every question or leaves the session entirely.

        This used to be one, and one is what the booklets printed. Every "Now
        you try:" in a shipped Year 3 booklet had a single question under it,
        because six mini-lessons filled the hour and the fitter was allowed to
        thin practice to nothing to make room.
        """
        n = BookletPipeline._tail_group_size(section)
        if n >= len(section.questions):
            return len(section.questions)
        return min(len(section.questions), MIN_NOW_YOU_TRY)

    @staticmethod
    def _topics_in_session(in_session) -> int:
        """How many distinct top-level topics the hour still covers."""
        return len({s.topic for s in in_session})

    @staticmethod
    def _subjects_in_session(in_session) -> Counter:
        """Subtopics per subject still taught in the session.

        Single-subject booklets carry `subject=None` on every section (only
        `run_program` stamps it), so they land in one bucket and every subject
        rule below is a no-op for them, which is what we want: Accelerate and
        Scholarships run one engine and have no breadth to protect.
        """
        return Counter(s.subject for s in in_session)

    @staticmethod
    def _subject_spares(in_session):
        """Subtopics that can leave without taking their whole subject with them.

        This is the floor the trimmer used not to have. A NAPLAN booklet is two
        engines merged into one list, and every rule below this line counted
        subtopics and topics only: three maths subtopics under three maths
        topics satisfied both floors perfectly while the literacy half, cover
        billing and all, had been deleted one subtopic at a time.
        """
        per_subject = BookletPipeline._subjects_in_session(in_session)
        if len(per_subject) < 2:
            return list(in_session)
        return [s for s in in_session if per_subject[s.subject] > 1]

    @staticmethod
    def _droppable(in_session, plan=None):
        """The subtopics that are allowed to leave the session right now.

        Three floors, none implied by another:

        * Subtopic count. A booklet that teaches one thing is not worth a
          session.
        * Subject. Every subject the cover names has to survive the trimming
          with a lesson and practice of its own.
        * Topic. Three subtopics all under "Number" is one topic, not three.

        The counts come from the year band when there is one, and they are the
        same three everywhere today. That is on purpose: the pricing page sells
        both numbers, so a shorter session for a younger student is bought by
        lowering their cap, never by handing them a narrower booklet than the
        product promises. `plan` carries them anyway so the decision has a
        single place to live if the product ever changes.
        """
        min_subtopics = plan.min_subtopics if plan else MIN_CLASSWORK_SUBTOPICS
        min_topics = plan.min_topics if plan else MIN_CLASSWORK_TOPICS
        if len(in_session) <= min_subtopics:
            return []
        candidates = BookletPipeline._subject_spares(in_session)
        if BookletPipeline._topics_in_session(in_session) <= min_topics:
            # At the topic floor, a subtopic may still leave as long as its
            # topic keeps another one in the session.
            per_topic = Counter(s.topic for s in in_session)
            candidates = [s for s in candidates if per_topic[s.topic] > 1]
        return candidates

    @staticmethod
    def _may_drop(in_session, plan=None) -> bool:
        """Whether one more subtopic can leave the session."""
        return bool(BookletPipeline._droppable(in_session, plan))

    @staticmethod
    def _leaves_the_session(in_session, plan=None):
        """Which subtopic gives up its place in the hour.

        It used to be whichever came last, which is not a choice at all, and
        for English it was the same choice every time. `outline_parser` emits
        Reading, Writing, Language Conventions and Vocabulary in that order, so
        the last subtopic was always grammar or vocabulary, and a booklet over
        the hour taught its comprehension in full and its grammar not at all.
        Measured on a Year 5 sample: two readings held 42 of the 60 minutes and
        ten of the eleven questions, Similes was left with one, and Commas with
        none.

        Four rules, in order. Each one only ever chooses between what the rule
        above it left on the table.

        Subject first, and this is a floor rather than a preference, so it
        lives in `_droppable`: the last subtopic of a subject never leaves. A
        NAPLAN booklet prints "Numeracy and Literacy" on its cover, and an
        English subtopic carries a reading passage, so it costs more minutes
        than any maths subtopic and "drop the longest" chose English every
        time. Years 3 and 5 shipped with no literacy in them at all.

        Then subject balance: among what may leave, take from whichever subject
        currently holds the most of the session. Breadth alone would have left
        one English subtopic against three of maths, which is a booklet sold as
        two halves and delivered as seven eighths of one. Alternating the drops
        this way spends the trimming evenly and lands both halves near half the
        session. It is a no-op on a single-subject booklet, where every
        candidate belongs to the same subject.

        Then topic breadth, the original rule, now applied inside the subject
        that was chosen: prefer a subtopic whose topic still has another
        subtopic in the session. Losing one of two readings costs the child a
        reading; losing the only grammar subtopic costs them grammar. A booklet
        that teaches three things is worth more than one that teaches one thing
        thoroughly and two things nominally, and the subtopic that leaves is
        not lost, it is worked at home with its mini-lesson attached.

        Then cost: among those, the longest, so one drop frees the most time
        and fewer subtopics have to go.
        """
        candidates = BookletPipeline._droppable(in_session, plan) or list(in_session)

        # Subject balance. Measured in minutes, not in subtopic count, because
        # minutes are what the cap is spent in and an English subtopic is worth
        # about one and a third maths ones.
        per_subject_minutes: Counter = Counter()
        for s in in_session:
            per_subject_minutes[s.subject] += classwork_section_minutes(s)
        heaviest = max({s.subject for s in candidates},
                       key=lambda subj: (per_subject_minutes[subj], str(subj)))
        pool = [s for s in candidates if s.subject == heaviest]

        # Topic breadth, then cost.
        per_topic = Counter(s.topic for s in in_session)
        shared = [s for s in pool if per_topic[s.topic] > 1]
        return max(shared or pool, key=classwork_section_minutes)

    @staticmethod
    def _session_cap(plan) -> int:
        """The Class Work cap for this student, in minutes.

        The year band's cap, held under the deployment's ceiling. `plan` is
        None for callers with no year level (the guard checks drive the fitter
        directly), and that means the hour, which is what the fitter did before
        year bands existed.
        """
        if plan is None:
            return CLASSWORK_CAP_MINUTES
        return min(CLASSWORK_CAP_MINUTES, plan.classwork_cap_minutes)

    @staticmethod
    def _fit_classwork_to_cap(sections, plan=None) -> None:
        """Move classwork past the time cap into homework, in place.

        The session has to fit the time the student can actually work for,
        which `timing.session_plan` sets from their year: thirty minutes for a
        Year 1, an hour for a Year 9. Trimming the printed estimate alone would
        just make the booklet lie about its own workload, so move the surplus
        questions into Homework instead, where there is no clock. Longest
        section first, so the trimming lands on whichever subtopic is
        overweight rather than flattening every one of them equally.

        `plan` is optional: without one the cap is the flat hour, which is what
        this did before year bands existed and what the guard checks exercise.

        Every subtopic keeps at least one classwork question: a mini-lesson
        with nothing to try immediately afterwards is worse than a slightly
        long session.

        A subtopic that cannot fit the hour LEAVES THE BOOKLET, taking its
        homework with it. It used to move to Homework with its mini-lesson
        attached, which quietly turned Homework into a second, longer lesson:
        one shipped Year 5 booklet taught three subtopics in the session and
        eight more inside Homework, 66 homework questions against 12 in class,
        and a printed estimate of 230 minutes. Homework is practice on what was
        taught in the session. A child working alone at the kitchen table on
        Wednesday cannot be taught a new method by a box of text, and a parent
        cannot help with one they never saw covered.

        A multi-subject booklet (NAPLAN merges numeracy and literacy into one
        list before this runs) is trimmed subject by subject rather than
        cheapest-first: see `_leaves_the_session`. The cover names both halves,
        so neither half may be trimmed away to make room for the other.

        The hour is held in every case but one. A reading and its five
        questions move as a unit and are never split, so an English session
        already down to `MIN_CLASSWORK_SUBTOPICS` readings can only get under
        the cap by dropping to two, which throws away a third of the session to
        save a minute or two. It runs a little long instead, and logs that it
        did. The printed estimate is recomputed from the booklet either way, so
        the page still tells the truth about the overrun.
        """
        if not sections:
            return

        cap = BookletPipeline._session_cap(plan)

        def in_session():
            """Subtopics the tutor actually covers in the hour.

            A subtopic with no classwork questions left is not taught in the
            session, but it stays in the booklet: its mini-lesson still prints
            for the student to read, and its questions have moved to Homework.
            Nothing is deleted, so the time comes down without content loss.
            """
            return [s for s in sections if s.questions]

        def total() -> float:
            return sum(classwork_section_minutes(s) for s in in_session())

        # Teaching is the product, so trim in the order that costs the least
        # of it.
        #
        def leanest() -> float:
            """What the session would cost with practice cut to the bone.

            Teaching dominates: a mini-lesson with its worked and guided
            examples runs about 12 minutes before a single practice question.
            So this is the floor a given set of subtopics can reach, and if
            even that is over the cap then no amount of trimming practice will
            help and a subtopic has to go.

            The floor is not one question everywhere. A reading and its
            questions move as a unit, so a subtopic that is one reading can
            keep all of them or none, and costing it at one question was a
            lie that made step 1 believe a session would fit when it could
            not. Step 2 then trimmed every other subtopic to a single question
            trying to reach a cap that was out of reach, and only afterwards
            dropped the reading, by which point the trimming had been for
            nothing: a Year 5 English session ended at 39 minutes with two
            subtopics holding one question each, inside an hour with room for
            all of them.
            """
            return sum(
                classwork_section_minutes(
                    _WithQuestions(s, s.questions[:BookletPipeline._floor_questions(s)]))
                for s in in_session()
            )

        # Step 1: hand whole subtopics to Homework, but only when practice
        # cannot absorb the overrun. Dropping a subtopic costs about 16
        # minutes, so doing it first overshoots: a 64-minute booklet would
        # fall to 48 and waste a quarter of the session. A subtopic that
        # moves keeps its questions as homework practice; only its mini-lesson
        # goes unread in the session.
        while (leanest() > cap
               and BookletPipeline._may_drop(in_session(), plan)):
            dropped = BookletPipeline._leaves_the_session(in_session(), plan)
            sections.remove(dropped)
            log.info("pipeline.subtopic_dropped",
                     extra={"subtopic": dropped.subtopic,
                            "subject": dropped.subject,
                            "homework_lost": len(dropped.homework_questions),
                            "cap": cap,
                            "still_over_by": round(total() - cap, 1)})

        # Step 2: thin the practice on what remains.
        # Bounded: each pass moves at least one question and no section goes
        # below one.
        while total() > cap:
            # Sections that can give ground without splitting a reading go
            # first, so an English booklet loses a maths-shaped tail or a whole
            # spare reading before any comprehension is broken up.
            # A section can give ground only while it stays above its floor.
            # Without the second test the fitter thins every subtopic to a
            # single question, which is exactly what the shipped booklets did.
            movable = [
                s for s in in_session()
                if BookletPipeline._tail_group_size(s) < len(s.questions)
                and (len(s.questions) - BookletPipeline._tail_group_size(s)
                     >= BookletPipeline._floor_questions(s))
            ]
            if movable:
                biggest = max(movable, key=lambda s: len(s.questions))
                BookletPipeline._move_tail_to_homework(biggest)
                continue
            # Nothing can give ground without splitting a reading, which is
            # the thing we will not do. The hour gives way here, not the
            # product floor: a session a few minutes long is a smaller problem
            # than a booklet that covers two topics or prints one question
            # after a lesson. The printed estimate is recomputed from the
            # booklet, so the page still tells the truth about the overrun.
            #
            # There is deliberately no second drop site here. Step 1 already
            # removes whole subtopics while `leanest()` is over the cap, so
            # reaching this point means either thinning can get us under it
            # (and the loop above is still working) or the floors forbid
            # dropping anything at all. A fuzz over 400 mixed booklet shapes
            # never reached a drop here, and an unreachable branch that
            # relocates content is exactly where the homework-as-a-lesson bug
            # would grow back unseen.
            in_play = in_session()
            log.warning(
                "pipeline.classwork_over_cap",
                extra={"minutes": round(total(), 1),
                       "cap": cap,
                       "subtopics": len(in_play),
                       "topics": BookletPipeline._topics_in_session(in_play),
                       "reason": "at the subtopic, topic or practice floor"})
            break

        for s in sections:
            s.estimated_minutes = (
                round_display(classwork_section_minutes(s)) if s.questions else None)

    @staticmethod
    def _tail_group_size(section) -> int:
        """How many classwork questions the last one cannot be separated from.

        One, unless the last question hangs off a reading, in which case it is
        every question about that reading.
        """
        qs = section.questions
        if not qs:
            return 0
        pid = qs[-1].question.passage_id
        if not pid:
            return 1
        n = 1
        while n < len(qs) and qs[-(n + 1)].question.passage_id == pid:
            n += 1
        return n

    @staticmethod
    def _move_tail_to_homework(section) -> bool:
        """Move the last classwork question, or its whole reading, down.

        Trimming the hour one question at a time would undo the passage-safe
        split: the last two questions of a three question reading would end up
        in Homework while the third stayed in Class Work, and the student would
        meet the same passage in both halves. Moving the group keeps a reading
        and its questions together.

        A reading is never split, not even when it is the whole section. It
        used to be: a section that was one reading gave ground a question at a
        time, so a five question comprehension was whittled to two in Class
        Work and three in Homework under the same passage printed twice. Five
        questions follow a reading, and a booklet that quietly delivers two is
        not the product. Returns False in that case, leaving the section alone
        for `_fit_classwork_to_cap` to move whole instead.
        """
        qs = section.questions
        n = BookletPipeline._tail_group_size(section)
        if len(qs) <= 1 or n >= len(qs):
            return False
        moved = qs[-n:]
        del qs[-n:]
        # Prepend, so questions arrive in Homework in the order they were
        # written and a moved group stays contiguous there too.
        section.homework_questions[:0] = moved
        return True

    @staticmethod
    def _timing(sections, challenge, recap) -> dict:
        """Return per-part minutes: recap, classwork, homework (incl. the final
        challenge as its capstone), challenge, and the grand total."""
        # Only subtopics the session actually covers. A subtopic the cap fitter
        # moved out has no classwork questions left, and counting its lesson
        # here was why a booklet the fitter had trimmed to the hour printed
        # "about 100 min": the fitter measured the sections still in the
        # session, this measured every section that existed, and the two
        # disagreed by one mini-lesson per moved subtopic.
        classwork_raw = sum(
            section_minutes(len(s.questions), s.teaching is not None, None)
            for s in sections if s.questions
        )
        homework_raw = sum(
            section_minutes(len(s.homework_questions), False, None) for s in sections
        )
        recap_raw = section_minutes(len(recap), False, "easy") if recap else 0.0
        challenge_raw = section_minutes(len(challenge), False, "hard") if challenge else 0.0
        homework_raw += challenge_raw  # the Final Challenge lives in the homework half
        return {
            "recap_minutes": round_display(recap_raw) if recap else None,
            "classwork_minutes": round_display(classwork_raw) if sections else None,
            "homework_minutes": round_display(homework_raw) if homework_raw else None,
            "challenge_minutes": round_display(challenge_raw) if challenge else None,
            "total_minutes": round_total(recap_raw + classwork_raw + homework_raw),
        }

    def _build_recap(self, subject, year_level, recap_focus, reference_chunks,
                     seen=None, covered=None):
        """A short easy warm-up quiz revising earlier material (spaced retrieval).
        For a term plan `recap_focus` is the previous week's topic.

        The recap carries no mini-lesson by design: it revises what the student
        already knows, so there is nothing to teach and nothing to pass to the
        generator.

        It does get told what today's booklet teaches, as an exclusion. Without
        it the recap knew nothing about the booklet it opens, and the only
        guard was an exact-text dedupe, which catches a repeated question and
        not a repeated skill and not a pre-taught one. A warm-up that asks
        about the thing the first lesson is about to teach is a spoiler, and
        one that asks about something the student has never been taught is an
        ambush on question 1, which is where a child decides how this is going
        to go.
        """
        # The warm-up was already the right length at every year (four to six
        # minutes on the shipped booklets), so the year band only trims it by a
        # question in lower primary, where three is a warm-up and four is the
        # start of a worksheet.
        n_recap = min(self._n_recap, session_plan(year_level).recap_questions)
        if n_recap <= 0:
            return []
        from .schemas import Subtopic
        if seen is None:
            seen = _SeenQuestions()
        today = [s for _, s in (covered or []) if s]
        if recap_focus:
            # A term plan: revise the week before, which is the whole point of
            # a recap and the only thing that makes it spaced retrieval rather
            # than a general quiz.
            name = ("revision of the material taught in LAST WEEK'S booklet, "
                    "which covered: " + recap_focus)
        elif today:
            # A single booklet has no week before it, so there is nothing to
            # space. The next best warm-up is not a random quiz but the ground
            # today's lessons stand on: for each subtopic below, the one skill
            # a student must already have to follow it. That also warms up the
            # right muscle, instead of whichever one the model thought of.
            name = ("revision of the PREREQUISITE skills that today's lessons "
                    "build on. For each subtopic listed here, ask about the "
                    "skill a student needs BEFORE they can learn it, never the "
                    "subtopic itself: " + "; ".join(today))
        else:
            name = f"quick revision of key {subject} skills learned earlier"
        if today:
            name += (". Do NOT ask about anything this booklet is about to "
                     "teach: " + "; ".join(today))
        # The difficulty hint is one of three fixed words, and "easy" on its own
        # is read in absolute terms: a Year 10 warm-up came back asking for 15%
        # of 800 and the area of a 12 by 7 rectangle, which are Year 6 and Year
        # 4 skills. Easy has to mean easy FOR THIS YEAR, so the floor is stated
        # here, where there is room to say it.
        name += (f". These are warm-up questions: quick, confidence-building, "
                 f"a minute each at most. They are still {year_level} "
                 f"questions. Nothing here may be more than one year below "
                 f"{year_level}. A question a student could answer three years "
                 f"ago is not a warm-up, it is filler, and it tells the "
                 f"student this booklet has misjudged them before they reach "
                 f"the first lesson.")
        st = Subtopic(name=name, difficulty_hint="easy")
        try:
            # allow_passages=False: the recap is a handful of loose questions
            # with no section around them, so BookletData has nowhere to keep a
            # passage and a comprehension item generated here would ask about
            # reading the booklet never prints.
            qs = self._generator.generate(
                subject, year_level, "Warm-up Recap", st, reference_chunks,
                allow_passages=False)
        except Exception as e:
            log.warning("pipeline.recap_failed", extra={"error": str(e)[:200]})
            return []
        picked = qs.questions[:n_recap]
        verdicts = self._validate_many(subject, year_level, picked, reference_chunks)
        out: list[ValidatedQuestion] = []
        for q, r in zip(picked, verdicts):
            if self._reasoning_reject(subject, q):
                continue
            # A warm-up that asks something the booklet asks again later is a
            # spoiler, not spaced retrieval.
            if not seen.add(self._norm_q(q.question)):
                log.info("pipeline.drop_duplicate_recap", extra={"subject": subject})
                continue
            img, attr = self._resolve_visual(q)
            orphan = self._orphan_figure(q.question, img)
            if orphan:
                log.info("pipeline.drop_missing_figure_recap",
                         extra={"subject": subject, "phrase": orphan})
                continue
            self_answering = self._self_answering(q)
            if self_answering:
                log.info("pipeline.drop_self_answering_recap",
                         extra={"reason": self_answering,
                                "question": q.question[:70]})
                continue
            absurd = self._absurd_quantity(q.question)
            if absurd:
                log.info("pipeline.drop_absurd_quantity_recap",
                         extra={"subject": subject, "reason": absurd})
                continue
            # Attribute the warm-up to the engine that wrote it. The recap has
            # no section around it, so this is the only record of which half of
            # a two-subject booklet a question came from.
            q.subject = subject
            out.append(ValidatedQuestion(
                question=q, verified=self._trusted(q, r.verified),
                    validator_notes=r.notes,
                image_path=str(img) if img else None, image_attribution=attr))
        return out

    def _process_subtopic(self, subject, year_level, topic_name, subtopic,
                          seen=None, passage_quota=2,
                          authoring_guidance=None, use_rag=True):
        """Generate one subtopic: lesson + validated questions. Returns
        (SubtopicOutput, rag_chunks). Self-contained so it can run in a thread.

        The lesson is written FIRST and handed to the question generator, so
        the practice exercises the skill the worked example just modelled. This
        ordering was already what the code did, so nothing was serialised to
        get it: the two calls were sequential within a subtopic before, and
        subtopics still run concurrently with each other. Passing the teaching
        along costs no extra wall-clock time.
        """
        if seen is None:
            seen = _SeenQuestions()
        from .programs import reviewed_external_content_enabled
        retrieved_chunks = (
            self._retrieve(subject, year_level, topic_name, subtopic.name)
            if use_rag and reviewed_external_content_enabled() else []
        )
        chunks = (
            [authoring_guidance, *retrieved_chunks]
            if authoring_guidance else retrieved_chunks
        )
        teaching = self._make_teaching(subject, year_level, topic_name, subtopic, chunks)
        passages: list[Passage] = []
        # How much homework this year level is set on one subtopic. A six year
        # old cannot be set the week a fourteen year old can, and until tonight
        # both were set four questions per subtopic and sixteen per booklet.
        _, n_homework = self._question_budget(year_level)
        validated = self._generate_and_validate(
            subject, year_level, topic_name, subtopic, chunks, teaching, seen,
            passages, passage_quota=passage_quota,
        )
        total = len(validated)
        failed = sum(1 for v in validated if not v.verified)
        failure_rate = failed / total if total else 0.0
        log.info(
            "pipeline.subtopic.done",
            extra={"subject": subject, "topic": topic_name,
                   "subtopic": subtopic.name, "total": total, "failed": failed,
                   "failure_rate": failure_rate,
                   "rag_chunks": len(retrieved_chunks),
                   "authoring_guidance": bool(authoring_guidance),
                   "passages": len(passages)},
        )
        # Split the generated set: the first chunk is classwork ("Now you try"),
        # the rest is homework practice on the same topic (repetition for
        # retention). Homework costs no extra API calls, it's the same batch.
        #
        # A comprehension passage complicates that split, because its questions
        # are only answerable next to the reading. The split is therefore made
        # to land on a passage boundary rather than blindly at
        # `self._n_classwork`. Both steps are no-ops for maths, where no
        # question carries a passage_id.
        validated = self._group_by_passage(validated)
        cut = self._passage_safe_split(validated, self._n_classwork)
        classwork = validated[:cut]
        # Held to the year band's budget even when the model wrote more than it
        # was asked for, which it does often enough to matter: the count in the
        # prompt is a request, this is the guarantee.
        homework = validated[cut:cut + n_homework]
        # A guided example that works through a question about one of this
        # section's readings prints above that reading, so it hands over the
        # answer before the student has read a word. Guided examples are
        # optional, so dropping one costs the lesson little. The worked example
        # is the lesson, so it is kept and logged rather than removed.
        if teaching is not None and passages:
            from .agents.consistency import example_spoils_passage
            kept = [g for g in (teaching.guided_examples or [])
                    if not example_spoils_passage(g, passages)]
            if len(kept) != len(teaching.guided_examples or []):
                log.warning("pipeline.guided_example_spoiled_passage",
                            extra={"subtopic": subtopic.name,
                                   "dropped": len(teaching.guided_examples) - len(kept)})
                teaching.guided_examples = kept
            if example_spoils_passage(teaching.worked_example, passages):
                log.warning("pipeline.worked_example_spoils_passage",
                            extra={"subtopic": subtopic.name})

        raw_min = section_minutes(
            len(classwork), teaching is not None, subtopic.difficulty_hint,
        )
        section = SubtopicOutput(
            topic=topic_name,
            subtopic=subtopic.name,
            teaching=teaching,
            questions=classwork,
            homework_questions=homework,
            passages=passages,
            failure_rate=failure_rate,
            estimated_minutes=round_display(raw_min),
        )
        return section, retrieved_chunks

    @staticmethod
    def _group_by_passage(validated):
        """Reorder so each passage's questions are consecutive.

        Returns the list unchanged when no question carries a passage_id, which
        is every maths, reasoning and science subtopic, so this only ever moves
        English questions.

        Questions that need no reading go last. That is not cosmetic: it puts
        the loose questions where the split can fall anywhere, so the cut has
        somewhere to land near the target even when the passages are large.
        """
        if not any(vq.question.passage_id for vq in validated):
            return list(validated)
        order: list[str] = []
        groups: dict[str, list] = {}
        loose: list = []
        for vq in validated:
            pid = vq.question.passage_id
            if not pid:
                loose.append(vq)
                continue
            if pid not in groups:
                groups[pid] = []
                order.append(pid)
            groups[pid].append(vq)
        out = [vq for pid in order for vq in groups[pid]]
        out.extend(loose)
        return out

    @staticmethod
    def _passage_safe_split(validated, target: int) -> int:
        """Where to cut Class Work from Homework without straddling a passage.

        The design decision this encodes: a passage's questions are NOT split
        across the two halves. The alternative (split freely and reprint the
        reading in both halves) makes the student read the same three
        paragraphs twice in one booklet and pads the page count for nothing.

        So the cut moves to the nearest boundary between passage groups. Ties
        go to the larger Class Work half, because the hour cap
        (`_fit_classwork_to_cap`) can move surplus work down into Homework
        afterwards and nothing ever moves work back up.

        The cut is never 0: a mini-lesson with no practice under it is worse
        than a slightly long section, which is the same rule the hour cap
        keeps.

        This is a best effort, not a guarantee, and it is deliberately not the
        only defence. `_fit_classwork_to_cap` can still move a question across
        the line later, and `SubtopicOutput.passages` holds the reading for the
        whole subtopic rather than for one half, so a group that does end up
        split is still resolvable from either side.
        """
        n = len(validated)
        if n <= 1:
            return n

        def pid(i):
            return validated[i].question.passage_id

        cuts = [i for i in range(1, n) if not (pid(i) and pid(i) == pid(i - 1))]
        cuts.append(n)
        return min(cuts, key=lambda c: (abs(c - target), -c))

    def _generate_from_outline(self, outline, seen=None,
                               authoring_guidance=None, use_rag=True):
        """Run generation for every subtopic in a single-subject outline.
        Subtopics are independent apart from the shared `seen` set (which is
        thread-safe), so they run concurrently (each is a batch of
        network-bound LLM calls). Output order matches the outline. Returns
        (sections, covered, rag_pool)."""
        if seen is None:
            seen = _SeenQuestions()
        tasks = [(t.name, st) for t in outline.topics for st in t.subtopics]
        results: list = [None] * len(tasks)
        # Decided here, for the outline as a whole, because the target is four
        # readings per booklet and only this level knows how many subtopics
        # there are to share them between. Each subject's outline gets its own
        # budget, so a NAPLAN booklet's English half gets the same four as an
        # Academic Accelerate English booklet, and its maths half gets none.
        quotas = passage_quotas(len(tasks)) if wants_passages(outline.subject) \
            else [0] * len(tasks)

        if self._max_workers > 1 and len(tasks) > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
                futures = {
                    ex.submit(self._process_subtopic, outline.subject,
                              outline.year_level, tn, st, seen, quotas[i],
                              authoring_guidance, use_rag): i
                    for i, (tn, st) in enumerate(tasks)
                }
                for fut in as_completed(futures):
                    results[futures[fut]] = fut.result()
        else:
            for i, (tn, st) in enumerate(tasks):
                results[i] = self._process_subtopic(
                    outline.subject, outline.year_level, tn, st, seen, quotas[i],
                    authoring_guidance, use_rag)

        sections: list[SubtopicOutput] = []
        covered: list[tuple[str, str]] = []
        rag_pool: list[str] = []
        for (tn, st), (section, chunks) in zip(tasks, results):
            sections.append(section)
            covered.append((tn, st.name))
            rag_pool.extend(chunks)
        return sections, covered, rag_pool

    # ---------- RAG ----------

    def _retrieve(self, subject, year_level, topic, subtopic) -> list[str]:
        try:
            hits = self._retriever.retrieve(subject, year_level, topic, subtopic)
        except Exception as e:
            log.warning("pipeline.retrieve_failed", extra={"error": str(e)[:200]})
            return []
        if hits:
            log.info(
                "pipeline.retrieved",
                extra={"subject": subject, "subtopic": subtopic, "count": len(hits),
                       "sources": [h.source for h in hits]},
            )
        return [h.text for h in hits]

    # ---------- Teaching ----------

    def _make_teaching(
        self, subject, year_level, topic_name, subtopic, reference_chunks,
    ) -> SubtopicTeaching | None:
        try:
            teaching = self._intro.write(
                subject, year_level, topic_name, subtopic, reference_chunks,
            )
        except Exception as e:
            # Soft failure: the booklet still renders without the mini-lesson.
            log.warning(
                "pipeline.intro_failed",
                extra={"subject": subject, "subtopic": subtopic.name, "error": str(e)[:200]},
            )
            return None
        # Render diagrams for the worked example and each guided ("we do") example.
        for ex in [teaching.worked_example, *teaching.guided_examples]:
            spec = ex.diagram_spec
            if not spec:
                continue
            from .visuals import render_diagram
            from .agents.consistency import reconcile_diagram_spec
            spec, fixed = reconcile_diagram_spec(spec, ex.question)
            if fixed:
                log.info("pipeline.example_diagram_corrected",
                         extra={"type": spec.get("type")})
                ex.diagram_spec = spec
            try:
                path = render_diagram(spec)
            except Exception as e:
                log.warning("pipeline.example_diagram_failed", extra={"error": str(e)[:200]})
                path = None
            if path:
                ex.image_path = str(path)
        return self._drop_orphan_examples(teaching, subtopic.name)

    def _drop_orphan_examples(self, teaching, subtopic_name: str):
        """Remove teaching examples that point at a figure that was not drawn.

        A worked example reading "how many cubes are needed to build this
        object" with nothing beside it teaches the child that the booklet is
        broken. The guided examples are optional, so a bad one is simply
        dropped. The worked example is not optional (the formatter always
        renders one), so a clean guided example is promoted into its place;
        only when there is nothing left to promote does the whole mini-lesson
        go, which is the same soft failure the intro writer already has.
        """
        def orphan(ex):
            return self._orphan_figure(ex.question, ex.image_path)

        kept = []
        for ex in teaching.guided_examples:
            phrase = orphan(ex)
            if phrase:
                log.info("pipeline.drop_orphan_guided_example",
                         extra={"subtopic": subtopic_name, "phrase": phrase})
                continue
            kept.append(ex)
        teaching.guided_examples = kept

        phrase = orphan(teaching.worked_example)
        if not phrase:
            return teaching
        if kept:
            log.info("pipeline.promote_guided_example",
                     extra={"subtopic": subtopic_name, "phrase": phrase})
            teaching.worked_example = kept[0]
            teaching.guided_examples = kept[1:]
            return teaching
        log.warning("pipeline.drop_teaching_orphan_figure",
                    extra={"subtopic": subtopic_name, "phrase": phrase})
        return None

    # ---------- Validation ----------

    def _validate(
        self, subject: str, year_level: str, q: Question,
        reference_chunks: list[str] | None = None,
        passages=None,
    ) -> ValidationResult:
        key = subject.strip().lower()
        if key in {"mathematics", "maths", "math"}:
            result = self._sympy.validate(q)
            # Only a CONCLUSIVE sympy verdict is final, in either direction. A
            # pass that only matched part of the question is not a pass: it
            # goes to the judge like any other unproven answer. Treating a
            # partial match as final is what let wrong answers ship with a
            # check mark, because it also short-circuited the judge.
            if result.conclusive:
                return result
            fallback = self._judge.validate(subject, year_level, q, reference_chunks,
                                            passages=passages)
            fallback.notes = f"sympy inconclusive; {fallback.notes}"
            return fallback
        if key in {"reasoning", "verbal reasoning", "quantitative reasoning"}:
            # Deterministic check for ciphers and number sequences first, it
            # catches broken examples the LLM judge waves through. Only when it
            # can't assess (returns None) do we fall back to the judge.
            det = self._reasoning.validate(q)
            if det is not None:
                return det
            return self._judge.validate(subject, year_level, q, reference_chunks,
                                        passages=passages)
        return self._judge.validate(subject, year_level, q, reference_chunks,
                                    passages=passages)

    def _validate_many(
        self, subject: str, year_level: str, questions: list[Question],
        reference_chunks: list[str] | None = None,
        passages=None,
    ) -> list[ValidationResult]:
        """Validate a whole set at once. Local checks (sympy, reasoning) run
        per question for free; every question that still needs the LLM judge is
        graded in a SINGLE batched call instead of one call each. Falls back to
        per-question judging if the batch call fails."""
        key = subject.strip().lower()
        is_maths = key in {"mathematics", "maths", "math"}
        is_reasoning = key in {"reasoning", "verbal reasoning", "quantitative reasoning"}

        results: list[ValidationResult | None] = [None] * len(questions)
        needs_judge: list[int] = []
        for i, q in enumerate(questions):
            if is_maths:
                r = self._sympy.validate(q)
                # Conclusive either way (proved right, proved wrong) is final.
                # Inconclusive, including a match against only part of the
                # question, is handed to the judge in the batched call below.
                if r.conclusive:
                    results[i] = r
                else:
                    needs_judge.append(i)
            elif is_reasoning:
                det = self._reasoning.validate(q)
                if det is not None:
                    results[i] = det
                else:
                    needs_judge.append(i)
            else:
                needs_judge.append(i)

        if needs_judge:
            batch_qs = [questions[i] for i in needs_judge]
            judged = self._judge.validate_batch(subject, year_level, batch_qs,
                                                reference_chunks, passages=passages)
            if judged is None:  # batch failed: fall back to individual grading
                judged = [self._judge.validate(subject, year_level, q, reference_chunks,
                                               passages=passages)
                          for q in batch_qs]
            for slot, i in enumerate(needs_judge):
                r = judged[slot]
                if is_maths and not r.verified:
                    r.notes = f"sympy inconclusive; {r.notes}"
                results[i] = r

        out = [r if r is not None else ValidationResult(False, "unvalidated")
               for r in results]
        log.info(
            "pipeline.validated",
            extra={"subject": subject, "total": len(out),
                   "verified": sum(1 for r in out if r.verified),
                   "judged": len(needs_judge)},
        )
        return out

    @staticmethod
    def _norm_q(text: str) -> str:
        """Normalise a question for duplicate detection.

        Case and whitespace only, plus trailing punctuation: "Calculate 3/8 +
        2/8." and "Calculate 3/8 + 2/8" are the same question. Nothing deeper
        is normalised on purpose, since digits and interior punctuation are
        what make two similar questions different ("1,200" is not "1200" by
        accident). This catches restatements, not same-shape questions with
        different numbers; those are the generator's job, not the dedupe's.
        """
        return re.sub(r"\s+", " ", text.strip().lower()).strip(" .?!:;")

    def _reasoning_reject(self, subject: str, q: Question) -> bool:
        """True if the deterministic reasoning checker can PROVE the question is
        broken (unsolvable cipher, wrong sequence). Deterministic and free, no
        API call. Returns False for non-reasoning subjects or questions the
        checker can't assess."""
        if subject.strip().lower() not in {
            "reasoning", "verbal reasoning", "quantitative reasoning",
        }:
            return False
        det = self._reasoning.validate(q)
        return det is not None and not det.verified

    def _question_budget(self, year_level) -> tuple[int, int]:
        """(class work, homework) questions to write for one subtopic.

        Class work is the same at every year: MIN_NOW_YOU_TRY is a promise on
        the pricing page, not a dial. Homework is the year band's, because it
        is worked alone across a week and that is where a six year old and a
        fourteen year old genuinely differ.

        Read defensively, for the same reason `_generate_and_validate` reads
        `_n_classwork` defensively: the guard checks drive both methods on a
        bare pipeline that sets only the attributes they exercise.
        """
        plan = session_plan(year_level)
        n_classwork = max(1, getattr(self, "_n_classwork", None)
                          or MIN_NOW_YOU_TRY)
        configured = getattr(self, "_n_homework", None)
        n_homework = (plan.homework_per_subtopic if configured is None
                      else min(configured, plan.homework_per_subtopic))
        return n_classwork, n_homework

    def _generate_and_validate(
        self, subject, year_level, topic_name, subtopic, reference_chunks,
        teaching=None, seen=None, passages_out=None, passage_quota=2,
    ) -> list[ValidatedQuestion]:
        """Generate, validate and dedupe one subtopic's question set.

        `teaching` is the mini-lesson that prints above these questions, so the
        practice can exercise the method it modelled. `seen` is the booklet-wide
        set of question texts already taken; it defaults to a private one so a
        caller with no booklet context still behaves sanely.

        `passages_out` is an optional list the reading blocks are appended to,
        in the order the kept questions cite them. It is an out-parameter rather
        than a second return value so that callers who only want questions (the
        whole maths path, and the checks) keep the signature they had.
        """
        results: list[ValidatedQuestion] = []
        if seen is None:
            seen = _SeenQuestions()
        # Passages from every call this subtopic makes, not just the first: a
        # regenerated question below arrives from a fresh call and brings its
        # own reading, and a question whose passage was not pooled would print
        # asking about text that is not in the booklet.
        pool: dict[str, Passage] = {}
        # Where the caller will cut Class Work from Homework, passed to the
        # generator as a hint so an English set can group its passage questions
        # on one side of it. Read defensively: it is only a hint, and the guard
        # checks drive this method on a bare pipeline that sets just the
        # attributes they exercise.
        cut_at = getattr(self, "_n_classwork", None)
        # Ask for exactly what this year level will print. The agent is built
        # once, before anyone knows whose booklet this is, so the number has to
        # arrive per call: a Year 1 subtopic is set six questions and a Year 9
        # one eight, and generating eight everywhere means paying to write and
        # to validate two that are then thrown away.
        n_classwork, n_homework = self._question_budget(year_level)
        count = n_classwork + n_homework

        def pooled(question_set):
            for p in getattr(question_set, "passages", None) or []:
                pool[p.id] = p
            return question_set

        qs = pooled(self._generator.generate(
            subject, year_level, topic_name, subtopic, reference_chunks, teaching,
            classwork_count=cut_at, passage_quota=passage_quota, count=count))
        # Validate the whole set in one batched judge call up front; per-question
        # regeneration below still uses the single-question path.
        initial = self._validate_many(subject, year_level, qs.questions,
                                      reference_chunks, passages=pool)
        for slot, (q, result) in enumerate(zip(qs.questions, initial)):
            retry_count = 0
            # Regenerate while the question fails validation OR duplicates one we
            # already accepted. On each retry we pull a fresh BATCH and scan all
            # of its questions for the best non-duplicate, taking only the first
            # question (the old behaviour) produced repeated questions whenever
            # the model favoured the same opener.
            while (not result.verified or self._norm_q(q.question) in seen) \
                    and retry_count < self._max_generation_attempts - 1:
                retry_count += 1
                log.info(
                    "pipeline.regenerate_single",
                    extra={"subject": subject, "subtopic": subtopic.name,
                           "retry": retry_count,
                           "reason": result.notes if not result.verified else "duplicate"},
                )
                fresh = pooled(self._generator.generate(
                    subject, year_level, topic_name, subtopic, reference_chunks,
                    teaching, classwork_count=cut_at,
                    passage_quota=passage_quota, count=count,
                ))
                best_q, best_result = None, None
                # Start at this question's OWN position in the fresh set, not
                # at the top. The generator is told to sort a set easiest to
                # hardest, so scanning from index 0 replaced a failed hard
                # question with the easiest question the model can write, and
                # dropped it into a late slot. Hard questions fail validation
                # more often than easy ones, so the ramp the prompt builds was
                # being eaten from the top down, exactly the "easy item late in
                # the list" the generator prompt warns against.
                candidates = fresh.questions[slot:] or fresh.questions
                for cand in candidates:
                    if self._norm_q(cand.question) in seen:
                        continue
                    cand_result = self._validate(subject, year_level, cand,
                                                 reference_chunks, passages=pool)
                    # Prefer a verified non-duplicate; otherwise keep the first
                    # non-duplicate as a fallback.
                    if cand_result.verified:
                        best_q, best_result = cand, cand_result
                        break
                    if best_q is None:
                        best_q, best_result = cand, cand_result
                if best_q is not None:
                    q, result = best_q, best_result
            # A reasoning question the deterministic checker can prove is
            # broken (unsolvable cipher/sequence) must never appear, so drop it
            # rather than show an unsolvable question, even without a check mark.
            if self._reasoning_reject(subject, q):
                log.info("pipeline.drop_broken",
                         extra={"subject": subject, "subtopic": subtopic.name,
                                "reason": result.notes})
                continue
            image_path, image_attr = self._resolve_visual(q)
            orphan = self._orphan_figure(q.question, image_path)
            if orphan:
                log.info("pipeline.drop_missing_figure",
                         extra={"subject": subject, "subtopic": subtopic.name,
                                "phrase": orphan})
                continue
            self_answering = self._self_answering(q)
            if self_answering:
                log.info("pipeline.drop_self_answering",
                         extra={"reason": self_answering,
                                "question": q.question[:70]})
                continue
            absurd = self._absurd_quantity(q.question)
            if absurd:
                log.info("pipeline.drop_absurd_quantity",
                         extra={"subject": subject, "subtopic": subtopic.name,
                                "reason": absurd})
                continue
            # The one atomic claim, made only once the question is going to be
            # kept. Losing it means a concurrent subtopic wrote the same text
            # first while we were retrying; the retry loop above is
            # best-effort, this is the guarantee. Claiming after the drops
            # above matters: a question discarded for a missing figure must
            # not block a sound one with the same text later.
            if not seen.add(self._norm_q(q.question)):
                log.info("pipeline.drop_duplicate",
                         extra={"subject": subject, "subtopic": subtopic.name})
                continue
            # Stamped here as well as on the loose questions, so subject
            # attribution is a property of every question in the booklet rather
            # than of the two parts that happened to need it first. A section
            # carries the same value on SubtopicOutput.subject; agreeing with
            # itself costs nothing and disagreeing would be a bug worth seeing.
            q.subject = subject
            results.append(ValidatedQuestion(
                question=q,
                verified=self._trusted(q, result.verified),
                validator_notes=result.notes,
                retry_count=retry_count,
                image_path=str(image_path) if image_path else None,
                image_attribution=image_attr,
            ))
        self._collect_passages(results, pool, passages_out)
        return results

    @staticmethod
    def _collect_passages(results, pool: dict, passages_out) -> None:
        """Hand back the reading the kept questions actually cite.

        Two things have to be true by the time a section is built. Every
        passage_id on a kept question must resolve, or the formatter is asked
        for text nobody wrote: a citation with nothing behind it is cleared, so
        the question prints as an ordinary self-contained item. And every
        passage handed on must be cited by a kept question, or the booklet
        prints a page of reading with no questions under it. Questions get
        dropped here for duplication and missing figures, so both directions
        need re-checking after the loop rather than trusting the generator.
        """
        cited: list[str] = []
        for vq in results:
            pid = vq.question.passage_id
            if not pid:
                continue
            if pid not in pool:
                log.info("pipeline.drop_dangling_passage_id")
                vq.question.passage_id = None
                continue
            if pid not in cited:
                cited.append(pid)
        if passages_out is None:
            return
        passages_out.extend(pool[pid] for pid in cited)

    # ---------- Challenge ----------

    def _build_challenge(
        self, subject, year_level, covered, reference_chunks, seen=None,
    ) -> list[ValidatedQuestion]:
        # The Final Challenge is cumulative and its questions are the longest in
        # the booklet: five of them cost a Year 1 nineteen minutes on the
        # shipped booklet, on top of everything else. Three is the lower primary
        # share of that.
        n_challenge = min(self._n_challenge,
                          session_plan(year_level).challenge_questions)
        if not covered or n_challenge <= 0:
            return []
        if seen is None:
            seen = _SeenQuestions()
        try:
            qs = self._challenger.generate(
                subject, year_level, covered, n_challenge, reference_chunks,
            )
        except Exception as e:
            log.warning("pipeline.challenge_failed", extra={"error": str(e)[:200]})
            return []
        # Drop what needs no LLM to reject FIRST, then grade everything that
        # survives in ONE batched call. This used to validate inside the loop,
        # one judge call per question, which is the per-question pattern the
        # project notes single out as the main lever on API cost and quota. It
        # also ran serially, so it added five round trips to the tail of every
        # booklet, and to each of the ten inside a term plan.
        #
        # Skip duplicate challenge questions outright: the challenge set is a
        # single batch, so we dedupe rather than regenerate. `seen` spans the
        # whole booklet: the Final Challenge is meant to combine the skills,
        # not reprint a question already answered in the practice.
        candidates = []
        # Never more than the year band asked for: the generator is told the
        # number but a model that returns six would otherwise print six.
        for q in qs.questions[:n_challenge]:
            if self._norm_q(q.question) in seen:
                continue
            if self._reasoning_reject(subject, q):
                log.info("pipeline.drop_broken_challenge",
                         extra={"subject": subject})
                continue
            candidates.append(q)
        verdicts = self._validate_many(subject, year_level, candidates,
                                       reference_chunks)

        results: list[ValidatedQuestion] = []
        for q, result in zip(candidates, verdicts):
            norm = self._norm_q(q.question)
            # For the challenge set we tolerate the LLM's first attempt more,
            # regeneration would cost another full-set call. Just record the
            # verification status.
            image_path, image_attr = self._resolve_visual(q)
            orphan = self._orphan_figure(q.question, image_path)
            if orphan:
                log.info("pipeline.drop_missing_figure_challenge",
                         extra={"subject": subject, "phrase": orphan})
                continue
            self_answering = self._self_answering(q)
            if self_answering:
                log.info("pipeline.drop_self_answering_challenge",
                         extra={"reason": self_answering,
                                "question": q.question[:70]})
                continue
            absurd = self._absurd_quantity(q.question)
            if absurd:
                log.info("pipeline.drop_absurd_quantity_challenge",
                         extra={"subject": subject, "reason": absurd})
                continue
            # Claim only once it is going to be kept, so a question dropped as
            # broken or figureless does not block a sound one later.
            if not seen.add(norm):
                continue
            # Same attribution the recap gets, and for the same reason: a
            # NAPLAN booklet runs this once per subject and merges the two sets
            # into one Final Challenge, after which nothing else in the booklet
            # can tell a numeracy item from a literacy one.
            q.subject = subject
            results.append(ValidatedQuestion(
                question=q,
                verified=self._trusted(q, result.verified),
                validator_notes=result.notes,
                retry_count=0,
                image_path=str(image_path) if image_path else None,
                image_attribution=image_attr,
            ))
        return results

    @staticmethod
    def _trusted(q, verified: bool) -> bool:
        """Withhold the verified mark when the working betrays the answer.

        The judge grades the answer against the question, so it never sees
        working that disagrees with the answer it is supposedly justifying.
        A real booklet shipped "Answer: 75" above working that concluded 80,
        carrying a check mark. An unmarked question is a small loss; a wrong
        one wearing a verified badge undermines every other question in the
        booklet.
        """
        if not verified:
            return False
        from .agents.consistency import answer_is_trustworthy
        ok, why = answer_is_trustworthy(
            getattr(q, "answer", "") or "", getattr(q, "working", "") or "")
        if not ok:
            log.warning("pipeline.answer_untrusted",
                        extra={"reason": why,
                               "answer": (getattr(q, "answer", "") or "")[:40]})
        return ok

    # ---------- Visuals ----------

    @staticmethod
    def _orphan_figure(text: str, image_path) -> str | None:
        """The phrase pointing at a picture we do not have, or None.

        A question that says "how many cubes are needed to build this object"
        with nothing drawn beside it cannot be answered, and no amount of
        validation notices: the judge reads the text, which is fine on its
        own terms. Callers drop the item. Stripping the phrase instead would
        leave the same unanswerable question with the evidence removed.
        """
        from .agents.consistency import refers_to_missing_figure
        return refers_to_missing_figure(text or "", bool(image_path))

    @staticmethod
    def _self_answering(q) -> str | None:
        """The reason this question gives away its own answer, or None.

        A shipped Year 3 booklet asked "Use the place value blocks to identify
        the number shown" and then wrote "There are 2 hundreds, 5 tens, and 3
        ones" in the same question. Nothing caught it: the judge checks that a
        question CAN be answered, and being answerable from the question alone
        is the fault rather than the test.

        Callers drop the item, as they do for an absurd quantity and a missing
        figure, and for the same reason: the answer key was written against
        this wording, so editing the giveaway out would leave a key that no
        longer matches.
        """
        from .agents.consistency import question_states_its_answer
        return question_states_its_answer(q.question or "", q.answer or "")

    @staticmethod
    def _absurd_quantity(text: str) -> str | None:
        """The reason a stated real-world quantity is impossible, or None.

        A shipped Year 5 booklet taught "reading and writing numbers up to
        millions" with the distance from Perth to Melbourne given as 3,421,000
        km, a stadium holding 9,900,009 spectators and a Queensland national
        park covering five million square kilometres, which is three times the
        state. The judge marks the arithmetic, and the arithmetic is fine; it
        is the world that is wrong. Callers drop the question rather than
        rewrite it: the answer key was written against the number, so changing
        the number silently breaks the key.
        """
        from .agents.consistency import implausible_magnitude
        return implausible_magnitude(text or "")

    def _resolve_visual(self, q):
        """Return (path, attribution) for whichever optional visual the LLM asked for."""
        if q.diagram_spec:
            from .visuals import render_diagram
            from .agents.consistency import reconcile_diagram_spec
            # Models often emit a scaled-down spec so the shape draws nicely
            # and forget the labels are what the student reads, producing a
            # 4cm x 2cm x 1cm drawing beside a 40cm x 20cm x 10cm question.
            spec, fixed = reconcile_diagram_spec(q.diagram_spec, q.question)
            if fixed:
                log.info("pipeline.diagram_corrected",
                         extra={"type": spec.get("type")})
                q.diagram_spec = spec
            # A flat shape beside a volume question, or a solid beside an area
            # question, teaches the wrong thing. Dropping the figure is a real
            # loss, but a child who reads "volume" off a rectangle learns that
            # a box is a square, and undoing that is exactly what this stage of
            # primary maths is for.
            from .agents.consistency import diagram_dimensionality_matches
            if not diagram_dimensionality_matches(q.diagram_spec, q.question):
                log.warning("pipeline.diagram_wrong_dimension",
                            extra={"type": q.diagram_spec.get("type"),
                                   "question": q.question[:60]})
                q.diagram_spec = None
            else:
                path = render_diagram(q.diagram_spec)
                if path:
                    log.info("pipeline.diagram",
                             extra={"type": q.diagram_spec.get("type")})
                    return path, None
        if q.image_query:
            from .visuals import fetch_image
            path, attr = fetch_image(q.image_query)
            if path:
                log.info("pipeline.image", extra={"query": q.image_query, "attr": attr})
                return path, attr
            log.info("pipeline.image_missed", extra={"query": q.image_query})
        return None, None
