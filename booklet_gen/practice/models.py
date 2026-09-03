"""The vocabulary the three halves of the practice engine share.

The bank (store.py), the factory (templates/instances/verify) and the grind
(the web views) all pass these objects around. They live in their own module so
none of those three has to import either of the others just to name a row,
which is what would otherwise force the whole engine into one file.

Nothing here talks to a database, an LLM or Flask. These are records.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

# Outcomes a student can record against a question they were shown. `None`
# means served but not answered, which is a real state: the arrow moved on.
OUTCOMES = ("got_it", "missed", "skipped")

# Difficulty bands. Three, not five: a band a generator cannot reliably hit is
# a band that means nothing to the student reading it.
DIFFICULTIES = ("easy", "medium", "hard")

CALCULATOR = ("free", "assumed", "either")

# Template lifecycle. A rejected template is kept, never deleted: it is the
# only record of what the model gets wrong on a subtopic, and the filler's
# blocking rule is measured on it.
TEMPLATE_STATUS = ("live", "retired", "rejected")


def canonical_json(value: Any) -> str:
    """Stable JSON for hashing and for storing.

    Sorted keys and no incidental whitespace, so the same parameter dict always
    produces the same string and therefore the same variant key. Without this
    the database's uniqueness constraint on a variant would depend on dict
    ordering, which is a duplicate question waiting to happen.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


@dataclass(frozen=True)
class TemplateRow:
    """One parameterised question family, as stored.

    The patterns carry `{placeholder}` markers naming entries in `params`. A
    concrete question is produced by rendering all of them from one parameter
    dict in a single pass, so the question a student reads and the payload the
    verifier checks can never come from different numbers.
    """

    id: str
    subject: str                 # 'methods' | 'chemistry'
    subtopic_id: str             # a senior_syllabus leaf id
    verify_kind: str
    calculator: str
    difficulty: str
    question_pattern: str
    answer_pattern: str
    working_pattern: str
    params: dict[str, Any]
    constraints: list[str]
    check_pattern: dict[str, Any]
    prompt_version: str
    syllabus_version: str
    created_at: int
    marks: Optional[int] = None
    status: str = "live"
    reject_reason: Optional[str] = None
    instances_made: int = 0
    instances_verified: int = 0
    model: Optional[str] = None
    retired_at: Optional[int] = None


@dataclass(frozen=True)
class Instance:
    """A concrete question expanded from a template, before admission.

    Not yet a bank row. It becomes an `ItemRow` only after `verify.admit`
    passes both gates, and is discarded otherwise.
    """

    template_id: str
    params: dict[str, Any]
    question: str
    answer: str
    working: str
    check: dict[str, Any]
    variant_key: str
    shuffle_key: float

    @property
    def params_json(self) -> str:
        return canonical_json(self.params)

    @property
    def check_json(self) -> str:
        return canonical_json(self.check)


@dataclass(frozen=True)
class ItemRow:
    """One verified question in the bank, ready to serve."""

    id: int
    template_id: str
    subject: str
    subtopic_id: str
    calculator: str
    difficulty: str
    question: str
    answer: str
    working: str
    params_json: str
    check_json: str
    variant_key: str
    shuffle_key: float
    verified_by: str
    syllabus_version: str
    created_at: int
    marks: Optional[int] = None
    verifier_notes: Optional[str] = None
    status: str = "live"

    def for_client(self, subtopic_name: str, repeat: bool = False) -> dict:
        """What the browser is allowed to see.

        Deliberately narrow. `check_json` and `params_json` are how a question
        is verified and regenerated, and publishing them would hand a student
        the answer to every question in the family, not just this one.
        """
        return {
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "working": self.working,
            "marks": self.marks,
            "difficulty": self.difficulty,
            "subtopic": subtopic_name,
            "calculator": self.calculator,
            "repeat": repeat,
        }


@dataclass(frozen=True)
class DrawResult:
    """What one call to the bank returned, and how honest it had to be.

    `dry` and `spacing` exist so the interface can tell the student the truth
    rather than quietly degrading. A student who has worked through every
    question in Antidifferentiation should be told so, not fed the same set
    again as though it were new, and never fed a neighbouring topic.
    """

    items: list[ItemRow] = field(default_factory=list)
    remaining_unseen: int = 0
    dry: bool = False
    repeats: frozenset[int] = frozenset()
    spacing: str = "strict"      # 'strict' | 'relaxed'
    unstocked: bool = False      # the scope is real but nothing is banked yet


@dataclass(frozen=True)
class SeenEvent:
    """A student was shown an item, and possibly said how it went.

    Carries `at` from the client so a batch flushed after a lapse in
    connectivity records when the question was actually seen rather than when
    the network recovered.
    """

    item_id: int
    outcome: Optional[str] = None
    at: Optional[int] = None

    def valid(self) -> bool:
        return (isinstance(self.item_id, int) and self.item_id > 0
                and (self.outcome is None or self.outcome in OUTCOMES))
