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


class Question(BaseModel):
    question: str
    answer: str
    working: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    # Exam papers only: marks allocated to this question. Booklet questions
    # leave this as None.
    marks: Optional[int] = None
    # Optional visual — only one of these is populated per question.
    # Maths: diagram_spec triggers a matplotlib-rendered figure.
    # English/Science: image_query triggers a Wikimedia Commons lookup.
    diagram_spec: Optional[dict] = None
    image_query: Optional[str] = None


class QuestionSet(BaseModel):
    questions: List[Question]


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
    # Resolved after rendering.
    image_path: Optional[str] = None


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
