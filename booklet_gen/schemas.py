from typing import List, Optional, Literal
from pydantic import BaseModel, Field


class Subtopic(BaseModel):
    name: str
    difficulty_hint: Literal["easy", "medium", "hard"] = "medium"
    question_types: List[str] = Field(default_factory=list)


class Topic(BaseModel):
    name: str
    subtopics: List[Subtopic]


class Outline(BaseModel):
    subject: str
    year_level: str
    topics: List[Topic]


class Passage(BaseModel):
    """A block of reading that several questions ask about.

    Comprehension used to live inside the question string, which produced two
    defects at once: passages shrank to a sentence, because a question field
    is not where you write three paragraphs, and a question could say "the
    passage above" while the text rendered below it. Making the passage its
    own object lets the formatter guarantee it is laid out before every
    question that refers to it.
    """
    id: str
    title: Optional[str] = None
    # Paragraphs, not one blob: the renderer needs the breaks, and a model
    # asked for a list writes more than a model asked for a string.
    paragraphs: List[str] = Field(default_factory=list)


class SpellingList(BaseModel):
    """The words set for the coming week, printed at the back of a booklet."""
    words: List[str] = Field(default_factory=list)


class SpellingTest(BaseModel):
    """A test on the previous week's list, printed at the front.

    `words` is drawn from the previous booklet's SpellingList, which is why
    this is carried by the term plan rather than generated fresh: a test on
    words the student was never given is not a test.
    """
    words: List[str] = Field(default_factory=list)
    from_week: Optional[int] = None


class TablesList(BaseModel):
    """The times table set to memorise this week, printed at the back.

    Only the table itself is stored. The twelve facts are `table x 1` through
    `table x 12`, so deriving them at render time is the one way the printed
    list and the test next week cannot disagree with each other.
    """
    table: int = 0


class TablesTest(BaseModel):
    """A recall drill on the table set in the previous booklet.

    `order` is the twelve multipliers 1 to 12, shuffled, so the student cannot
    answer it by skip counting down the page: knowing 7 x 8 without walking
    through 7 x 7 is the whole point of the exercise. Shuffled once, when the
    test is made, so the answer key and the question page stay in step.
    """
    table: int = 0
    order: List[int] = Field(default_factory=list)
    from_week: Optional[int] = None


class Question(BaseModel):
    question: str
    answer: str
    working: str
    # Set when the question asks about a Passage; the formatter uses it to
    # place the reading before the questions and to avoid reprinting it.
    passage_id: Optional[str] = None
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    # Exam papers only: marks allocated to this question. Booklet questions
    # leave this as None.
    marks: Optional[int] = None
    # Optional visual: only one of these is populated per question.
    # Maths: diagram_spec triggers a precise programmatic figure.
    # Contextual maths and cross-curricular questions use scene_spec, which
    # composes Folio-owned illustrated objects with exact labels.
    # English/Science may use image_query for a rights-safe source image.
    diagram_spec: Optional[dict] = None
    scene_spec: Optional[dict] = None
    image_query: Optional[str] = None
    # Set by the visual planner after the final question set has been chosen.
    # The pipeline enforces `required` only after a real render attempt, since
    # a plausible JSON spec is not evidence that a printable figure exists.
    visual_priority: Literal["required", "strong", "helpful", "text-only"] = \
        "text-only"
    visual_reason: Optional[str] = None


class QuestionSet(BaseModel):
    questions: List[Question]
    # Reading the questions refer to by passage_id. This has to live on the
    # generator's parse target, not just on SubtopicOutput: Pydantic drops
    # unknown keys silently, so without it a model that correctly emits
    # passages loses them here and the questions reference reading that never
    # prints, which is worse than the inline passages it replaced.
    passages: List[Passage] = Field(default_factory=list)


class ValidatedQuestion(BaseModel):
    question: Question
    verified: bool
    validator_notes: Optional[str] = None
    retry_count: int = 0
    image_path: Optional[str] = None
    image_attribution: Optional[str] = None


class WorkedExample(BaseModel):
    """A fully worked example shown before the practice questions."""
    question: str
    steps: List[str] = Field(default_factory=list)
    answer: str
    diagram_spec: Optional[dict] = None
    scene_spec: Optional[dict] = None
    image_query: Optional[str] = None
    # Resolved after rendering.
    image_path: Optional[str] = None
    image_attribution: Optional[str] = None


class SubtopicTeaching(BaseModel):
    """What the intro_writer agent produces for a subtopic."""
    intro_paragraphs: List[str] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    # A short memorable name or mnemonic for the method (e.g. "Keep-Change-Flip"),
    # when one fits naturally. Sticky hooks help kids remember.
    mnemonic: Optional[str] = None
    worked_example: WorkedExample              # "I do" - fully worked
    # "We do" - a couple of follow-along examples with the solution shown, the
    # guided middle step between the worked example and independent practice.
    guided_examples: List[WorkedExample] = Field(default_factory=list)


class SubtopicOutput(BaseModel):
    topic: str
    subtopic: str
    subject: Optional[str] = None  # set on multi-subject (program) booklets
    teaching: Optional[SubtopicTeaching] = None
    questions: List[ValidatedQuestion]                 # classwork "Now you try"
    homework_questions: List[ValidatedQuestion] = Field(default_factory=list)
    # Reading blocks this subtopic's questions refer to, looked up by
    # Question.passage_id. Empty for maths.
    passages: List[Passage] = Field(default_factory=list)
    failure_rate: float = 0.0
    estimated_minutes: Optional[int] = None  # classwork time for this section


class BookletData(BaseModel):
    subject: str
    year_level: str
    student_name: str
    sections: List[SubtopicOutput]
    # Short warm-up quiz at the very start, revising earlier material (spaced
    # retrieval). For a term plan this revises the previous week.
    recap_questions: List[ValidatedQuestion] = Field(default_factory=list)
    recap_minutes: Optional[int] = None
    challenge_questions: List[ValidatedQuestion] = Field(default_factory=list)
    challenge_minutes: Optional[int] = None
    total_minutes: Optional[int] = None
    classwork_minutes: Optional[int] = None
    homework_minutes: Optional[int] = None
    # Product line ("Scholarships", "NAPLAN Practice", "Academic Accelerate").
    # When set, the cover leads with this and `subject` becomes the secondary line.
    program_label: Optional[str] = None
    # Set when this booklet is one week of a term plan. Shown on the cover.
    week_number: Optional[int] = None
    total_weeks: Optional[int] = None
    week_focus: Optional[str] = None
    # Spelling runs across a term: this week's list is set at the back, and
    # next week's booklet opens with a test on it.
    spelling_list: Optional[SpellingList] = None
    spelling_test: Optional[SpellingTest] = None
    tables_list: Optional[TablesList] = None
    tables_test: Optional[TablesTest] = None


class ExamSection(BaseModel):
    """One section of an exam paper, e.g. WACE Section One (calculator-free)."""
    name: str                                   # "Section One: Calculator-free"
    calculator_allowed: bool = False
    description: Optional[str] = None           # instructions shown under the heading
    questions: List[ValidatedQuestion] = Field(default_factory=list)
    working_minutes: Optional[int] = None

    @property
    def total_marks(self) -> int:
        return sum(vq.question.marks or 0 for vq in self.questions)


class ExamPaper(BaseModel):
    """A full exam paper. Structurally different from BookletData: no teaching
    content, questions carry marks, and questions are grouped into timed
    calculator-free / calculator-assumed sections."""
    subject: str                                # "Mathematics Methods"
    year_level: str                             # "Year 12"
    student_name: str
    unit: Optional[str] = None                  # "Units 3 and 4"
    sections: List[ExamSection] = Field(default_factory=list)
    reading_minutes: int = 10
    # Free-text lines printed in the "materials" block on the cover.
    materials: List[str] = Field(default_factory=list)

    @property
    def total_marks(self) -> int:
        return sum(s.total_marks for s in self.sections)

    @property
    def working_minutes(self) -> int:
        return sum(s.working_minutes or 0 for s in self.sections)


class ExamQuestionDraft(BaseModel):
    """What the exam generator returns for one section, before validation."""
    questions: List[Question] = Field(default_factory=list)


class TermWeek(BaseModel):
    week: int
    focus: str                     # the topic focus for this week
    difficulty: str = "medium"     # easy | medium | hard
    revision: bool = False         # a mixed/revision week near the end


class TermPlan(BaseModel):
    weeks: List[TermWeek]
