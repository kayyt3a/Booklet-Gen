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
    worked_example: WorkedExample


class SubtopicOutput(BaseModel):
    topic: str
    subtopic: str
    subject: Optional[str] = None  # set on multi-subject (program) booklets
    teaching: Optional[SubtopicTeaching] = None
    questions: List[ValidatedQuestion]
    failure_rate: float = 0.0
    estimated_minutes: Optional[int] = None  # rough "about N min" for this section


class BookletData(BaseModel):
    subject: str
    year_level: str
    student_name: str
    sections: List[SubtopicOutput]
    challenge_questions: List[ValidatedQuestion] = Field(default_factory=list)
    challenge_minutes: Optional[int] = None
    total_minutes: Optional[int] = None
    # Product line ("Scholarships", "NAPLAN Practice", "Academic Accelerate").
    # When set, the cover leads with this and `subject` becomes the secondary line.
    program_label: Optional[str] = None
    # Set when this booklet is one week of a term plan. Shown on the cover.
    week_number: Optional[int] = None
    total_weeks: Optional[int] = None
    week_focus: Optional[str] = None


class TermWeek(BaseModel):
    week: int
    focus: str                     # the topic focus for this week
    difficulty: str = "medium"     # easy | medium | hard
    revision: bool = False         # a mixed/revision week near the end


class TermPlan(BaseModel):
    weeks: List[TermWeek]
