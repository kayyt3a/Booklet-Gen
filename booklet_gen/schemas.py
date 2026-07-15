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


class SubtopicOutput(BaseModel):
    topic: str
    subtopic: str
    questions: List[ValidatedQuestion]
    failure_rate: float = 0.0


class BookletData(BaseModel):
    subject: str
    year_level: str
    student_name: str
    sections: List[SubtopicOutput]
