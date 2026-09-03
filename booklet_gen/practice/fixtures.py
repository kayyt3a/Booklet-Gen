"""A practice bank you can stand up in a check, with no LLM and no network.

Every check script in this feature needs questions in the bank before it can
measure anything, and none of them may pay for a Gemini call to get them. So
the bank is seeded from arithmetic: the templates are real `TemplateRow`
records and the items are real rows written through `store`, but the questions
are addition and multiplication rather than calculus, and nothing here imports
the factory or the verifier.

That is deliberate. A fixture that called the real generator would make every
check a test of the model's mood, and a fixture that wrote rows with its own
SQL would stop proving anything about the statements the product runs.

Determinism matters as much: the same seed produces the same ids, the same
parameters and the same shuffle keys, so a check that fails does so for
everybody and a check that passes is not passing by luck.

    from booklet_gen.practice import fixtures
    fixtures.fresh_database()
    student = fixtures.make_user("student@example.com")
    bank = fixtures.seed_bank(templates_per_subtopic=6, items_per_template=20)
"""
from __future__ import annotations

import hashlib
import os
import random
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import senior_syllabus
from ..dbpool import is_postgres
from ..webapp import db
from . import store
from .models import TemplateRow, canonical_json

# What a seeded item says it was checked by. Never a real verifier name: an
# item in a seeded bank has not been verified by anything, and a check that
# asserts coverage over `verified_by` must not be able to mistake one for a
# question that passed the admission gate.
#
# Taken from the store rather than declared again here, because the store now
# refuses to bank a stamp it does not recognise, and two copies of this string
# would eventually disagree and break seeding for a reason nobody would guess.
FIXTURE_VERIFIER = store.FIXTURE_VERIFIER

FIXTURE_PROMPT_VERSION = "fixture-v1"

# The families a seeded subtopic gets, in order. Small integer arithmetic, so
# an answer can be read at a glance when a check prints a question it did not
# expect to be served.
_FAMILIES = (
    ("sum", "What is {a} + {b}?", "{a} + {b}"),
    ("difference", "What is {a} - {b}?", "{a} - {b}"),
    ("product", "What is {a} x {b}?", "{a} x {b}"),
    ("double", "What is double {a}, less {b}?", "2 x {a} - {b}"),
    ("halve", "What is half of {a}, plus {b}?", "{a} / 2 + {b}"),
    ("square", "What is {a} squared, less {b}?", "{a}^2 - {b}"),
    ("triple", "What is triple {a}, plus {b}?", "3 x {a} + {b}"),
    ("share", "Share {a} counters between {b} students. How many each?",
     "{a} / {b}"),
)


@dataclass(frozen=True)
class SeededBank:
    """What a seeding call put in the bank, for a check to measure against."""

    subtopic_ids: list[str] = field(default_factory=list)
    template_ids: list[str] = field(default_factory=list)
    item_ids: list[int] = field(default_factory=list)
    templates: list[TemplateRow] = field(default_factory=list)
    params_by_item: dict[int, str] = field(default_factory=dict)
    template_by_item: dict[int, str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.item_ids)


def _refuse_real_database() -> None:
    """A fixture must never write questions into the production Postgres.

    Seeding is destructive in effect if not in intent: it writes templates and
    items that would then be served to paying students, and `fresh_database`
    would repoint nothing because Postgres ignores FOLIO_DB entirely.
    """
    if is_postgres():
        raise RuntimeError(
            "practice fixtures refuse to run against DATABASE_URL. They seed "
            "made-up questions, and on Postgres that would put arithmetic "
            "fixtures into the bank real students draw from. Unset "
            "DATABASE_URL before importing anything in a check script.")


def fresh_database(prefix: str = "folio-practice-") -> Path:
    """Point the accounts and the bank at an empty SQLite file. Its path.

    `db.DB_PATH` is assigned as well as the environment variable, because
    `webapp.db` reads FOLIO_DB once at import and a check that imported it
    earlier would otherwise keep writing to the previous file.
    """
    _refuse_real_database()
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    path = tmp / "folio.db"
    os.environ["FOLIO_DB"] = str(path)
    db.DB_PATH = path
    db.init_db()
    store.init_practice_db()
    return path


def make_user(email: str = "student@example.com",
              password: str = "fixture-password-123") -> int:
    """One signed-up account, returned as its user id."""
    _refuse_real_database()
    existing = db.get_user_by_email(email)
    if existing is not None:
        return int(existing["id"])
    return int(db.create_user(email, password))


def default_subtopics(count: int = 3, subject: str = "Mathematics Methods"
                      ) -> list[str]:
    """Real bankable subtopic ids, so scope resolution works in a check.

    Made-up ids would seed a bank that `resolve_scope` cannot reach, and a
    check measuring the draw would then be measuring nothing.
    """
    ids = senior_syllabus.resolve_scope(senior_syllabus.SUBJECT_KEYS[subject])
    return ids[:max(1, int(count))]


def seed_bank(*, subtopic_ids: list[str] | None = None,
              templates_per_subtopic: int = 6, items_per_template: int = 20,
              seed: int = 11, subject: str = "methods",
              calculator: str = "free", difficulty: str = "medium",
              marks: int | None = 2, now: int | None = None) -> SeededBank:
    """Fill the bank with deterministic arithmetic questions.

    Every item in the returned bank carries a parameter pair that appears
    nowhere else, so a check that sees two adjacent questions with the same
    parameters has caught the draw serving the same question twice, not a
    collision this seeder manufactured.
    """
    _refuse_real_database()
    stamp = int(time.time()) if now is None else int(now)
    nodes = list(subtopic_ids or default_subtopics(3))
    rng = random.Random(f"practice-fixtures:{seed}")
    version = store.syllabus_fingerprint()

    bank = SeededBank(subtopic_ids=nodes)
    # A counter that never repeats across the whole seeded bank, so no two
    # items anywhere share a parameter pair.
    serial = 0
    records: list[dict] = []
    for node_index, node in enumerate(nodes):
        for family_index in range(max(1, int(templates_per_subtopic))):
            name, question_pattern, answer_pattern = _FAMILIES[
                family_index % len(_FAMILIES)]
            template_id = f"fixture-{node_index}-{family_index}-{name}"
            template = TemplateRow(
                id=template_id, subject=subject, subtopic_id=node,
                verify_kind=f"fixture_{name}", calculator=calculator,
                difficulty=difficulty,
                question_pattern=question_pattern,
                answer_pattern=answer_pattern,
                working_pattern="Work it out, then check the arithmetic.",
                params={"a": {"type": "int", "range": [2, 400]},
                        "b": {"type": "int", "range": [1, 9]}},
                constraints=["a != b"],
                check_pattern={"expression": answer_pattern},
                prompt_version=FIXTURE_PROMPT_VERSION,
                syllabus_version=version, created_at=stamp, marks=marks,
            )
            store.save_template(template)
            bank.templates.append(template)
            bank.template_ids.append(template_id)

            for _ in range(max(1, int(items_per_template))):
                serial += 1
                a, b = 10 + serial, 1 + (serial % 9)
                params = {"a": a, "b": b}
                params_json = canonical_json(params)
                records.append({
                    "template_id": template_id, "subject": subject,
                    "subtopic_id": node, "calculator": calculator,
                    "difficulty": difficulty, "marks": marks,
                    "question": question_pattern.format(a=a, b=b),
                    "answer": str(_answer(name, a, b)),
                    "working": f"{answer_pattern.format(a=a, b=b)} = "
                               f"{_answer(name, a, b)}.",
                    "params_json": params_json,
                    "check_json": canonical_json(
                        {"expression": answer_pattern.format(a=a, b=b)}),
                    "variant_key": hashlib.sha256(
                        f"{template_id}:{params_json}".encode()).hexdigest()[:32],
                    # Random and independent of a and b, exactly as the real
                    # filler assigns it, so a check measuring the serving order
                    # is measuring the product's ordering and not a fixture's.
                    "shuffle_key": rng.random(),
                    "verified_by": FIXTURE_VERIFIER,
                    "syllabus_version": version,
                })

    stored = store.bulk_insert_items(records, now=stamp)
    bank.item_ids.extend(stored)
    for item_id, record in zip(stored, records):
        bank.params_by_item[item_id] = record["params_json"]
        bank.template_by_item[item_id] = record["template_id"]
    return bank


def _answer(family: str, a: int, b: int):
    if family == "sum":
        return a + b
    if family == "difference":
        return a - b
    if family == "product":
        return a * b
    if family == "double":
        return 2 * a - b
    if family == "halve":
        return a / 2 + b
    if family == "square":
        return a * a - b
    if family == "triple":
        return 3 * a + b
    return round(a / b, 2)


def seed_thin_scope(subtopic_id: str | None = None, templates: int = 2,
                    items_per_template: int = 3, seed: int = 5) -> SeededBank:
    """A scope too small for the spacing rule, for checking honest degradation.

    Below four live families the draw cannot promise "never the same family
    twice", and what it must do then is say `spacing='relaxed'` rather than
    quietly returning three questions when ten were asked for.
    """
    node = subtopic_id or default_subtopics(4)[-1]
    return seed_bank(subtopic_ids=[node], templates_per_subtopic=templates,
                     items_per_template=items_per_template, seed=seed)
