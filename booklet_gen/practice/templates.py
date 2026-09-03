"""One LLM call to one structurally validated question family.

This is the only place in the practice engine that talks to a language model,
and it makes exactly ONE call per attempt. No internal retry loop. That is a
budget decision before it is a design one: the filler counts calls, blocks a
subtopic after three consecutive rejections, and reads its daily cap out of the
database. A hidden retry inside here would spend budget the filler never sees
and would make "three consecutive failures" mean something different every
night.

WHY A DRAFT IS CHECKED FOUR TIMES BEFORE IT IS EXPANDED
-------------------------------------------------------
A template is worth about sixty verified questions. Finding out it is broken
after making all sixty costs sixty verifications, and each of the four checks
below finds a different kind of broken:

  1. PLACEHOLDERS. Every marker in the question, answer, working and check
     patterns names a declared parameter, and every declared parameter is used.
     An undeclared marker prints a literal "{a}" on a student's screen.

  2. THE SPACE. After the constraints are applied the family must have at least
     `instances.MIN_SPACE` distinct members. A family with six possible
     questions is one a student exhausts in a sitting and then meets all week.

  3. THE KIND, AND THE PAYLOAD'S EXACT SHAPE. `verify_kind` must be a real kind
     AND one the subtopic is allowed to be filled with, and the check payload
     must carry exactly the keys that kind's extractor returns. This is the
     check that pays for itself most often. `_extract_methods` for a derivative
     returns only {"function"}; a payload that also carries "variable" cannot
     round trip, so every single instance of that family fails the admission
     gate, and the failure reads like a mathematics error rather than the
     spelling mistake it is.

  4. THE PROBE. One instance is rendered and put through `verify.admit` before
     any of the others are made. A family whose printed question the verifier
     cannot read back is discovered here, at a cost of one instance, rather
     than after sixty. One instance is enough on purpose: a wording the
     extractor cannot read is a property of the pattern, not of the numbers, so
     the sixtieth instance fails for the same reason as the first. The other
     fifty-nine are still each verified individually afterwards, by the filler.

THE MODEL'S ANSWER IS STILL NEVER TRUSTED
-----------------------------------------
None of the above reads `answer_pattern` as truth. Gate 1 inside `verify.admit`
computes the answer from the check payload and compares. All this module does
is refuse to spend sixty verifications finding out what one can tell us.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from .. import senior_syllabus as syllabus
from ..agents._shared import extract_json, load_prompt
from ..llm import LLMClient
from . import instances, store, verify
from .models import CALCULATOR, DIFFICULTIES, TemplateRow, canonical_json

log = logging.getLogger(__name__)

# Stamped onto every template row. Bump it when a prompt changes in a way that
# changes what a valid family looks like, so a bank can be told apart from the
# rules that produced it.
PROMPT_VERSION = "practice-template-v1"

PROMPT_FILES = {
    "methods": "practice_template_methods.txt",
    "chemistry": "practice_template_chemistry.txt",
}

# How many instances the probe renders. One, deliberately. See the module
# docstring: a wording the extractor cannot read fails identically for every
# member of the family, so a second probe would cost an instance and prove
# nothing the first did not.
PROBE_COUNT = 1


class TemplateRejected(ValueError):
    """A draft that cannot become a family. The message is the stored reason."""


# ---------------------------------------------------------------------------
# The draft
# ---------------------------------------------------------------------------

class PracticeTemplateDraft(BaseModel):
    """Exactly what the model is asked to return, and nothing more.

    Lives here rather than in `schemas.py` because nothing outside the practice
    engine has any use for it, and `schemas.py` is already the shared vocabulary
    of the booklet pipeline.
    """

    verify_kind: str
    calculator: str = "either"
    difficulty: str = "medium"
    marks: Optional[int] = None
    question_pattern: str
    answer_pattern: str
    working_pattern: str
    params: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    check_pattern: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Check 3: the exact payload shape, per kind
# ---------------------------------------------------------------------------
#
# Required keys are what the kind's gate-1 routine cannot work without.
# Optional keys are those a variant of the kind uses (a `want` that selects
# between two sets of inputs) or that state the rounding. Anything outside both
# sets is refused by name, because an unexpected key is the commonest way a
# whole family dies at the round trip.
#
# Read off `verify._extract_methods` and `chem.EXTRACTORS`, which are the only
# authorities on what a printed question can be read back as. Where those two
# disagree with a prompt, they win, and this table is how that is enforced
# before an LLM call turns into sixty wasted verifications.

_ROUNDING = frozenset({"dp", "sf"})

CHECK_KEYS: dict[str, tuple[frozenset, frozenset]] = {
    # --- Methods -------------------------------------------------------------
    "derivative": (frozenset({"function"}), frozenset()),
    "derivative_at": (frozenset({"function", "at"}), frozenset()),
    "integral_indefinite": (frozenset({"integrand"}), frozenset()),
    "integral_definite": (
        frozenset({"integrand", "lower", "upper"}), frozenset()),
    "solve_equation": (frozenset({"lhs", "rhs"}), frozenset({"variable"})),
    "roots": (frozenset({"polynomial"}), frozenset({"variable"})),
    "direct_computation": (frozenset({"expression"}), frozenset()),
    "expression_equivalence": (frozenset({"expression"}), frozenset()),
    "function_value": (frozenset({"function", "at"}), frozenset()),
    "binomial": (frozenset({"n", "p", "k", "mode", "dp"}), frozenset()),
    # Only the "between" mode may carry an upper bound. A stored "upper" on a
    # one-sided question is never returned by the extractor, so it can never
    # round trip.
    "normal": (frozenset({"mu", "sigma", "mode", "bound", "dp"}),
               frozenset({"upper"})),

    # --- Chemistry -----------------------------------------------------------
    "molar_mass": (frozenset({"formula"}), _ROUNDING),
    "percent_composition": (frozenset({"element", "formula"}), _ROUNDING),
    # The only kind with no rounding at all: its answer is a formula, not a
    # number, so there is nothing to round.
    "empirical_formula": (frozenset({"percents"}), frozenset()),
    "balance_equation": (frozenset({"equation"}), frozenset()),
    "moles_mass": (frozenset({"want", "formula"}),
                   _ROUNDING | frozenset({"mass", "moles"})),
    "limiting_reagent": (frozenset({"equation", "amounts", "want"}),
                         _ROUNDING | frozenset({"product"})),
    "concentration_dilution": (
        frozenset({"want"}),
        _ROUNDING | frozenset({"v1_ml", "c1", "v2_ml", "moles", "volume_ml"})),
    "titration": (
        frozenset({"v_analyte_ml", "v_titrant_ml", "c_titrant",
                   "ratio_titrant", "ratio_analyte"}), _ROUNDING),
    "ph_strong": (frozenset({"concentration", "per_mole", "species"}),
                  _ROUNDING),
    "ph_weak": (frozenset({"concentration", "ka"}), _ROUNDING),
    "equilibrium_kc": (frozenset({"equation", "concentrations"}), _ROUNDING),
    "gas_laws": (frozenset({"want"}),
                 _ROUNDING | frozenset({"v1", "p1", "t1", "p2", "t2",
                                        "n", "p_kpa", "t_k"})),
    "sig_figs": (frozenset({"value", "figures"}), frozenset()),
}

# Kinds whose answer is a number and therefore need a stated rounding, without
# which `chem.rounding_of` refuses and every instance is discarded for a reason
# that has nothing to do with the chemistry.
NEEDS_ROUNDING = frozenset({
    "molar_mass", "percent_composition", "moles_mass",
    "concentration_dilution", "titration", "ph_strong", "ph_weak",
    "equilibrium_kc", "gas_laws", "binomial", "normal",
})

# Kinds whose correct answer cannot be WRITTEN DOWN as a pattern.
#
# `answer_pattern` is rendered, not evaluated per instance, so every answer has
# to be arithmetic over the parameters using `instances._ALLOWED_FUNCTIONS`.
# That set has no logarithm and no error function, which rules out three kinds
# outright: a pH is a base ten logarithm and a normal probability is an error
# function. A family for one of these can only be written by pinning the answer
# to a constant, and pinning it costs the parameter space the MIN_SPACE floor
# then refuses.
#
# This is a limit of the renderer, not of the verifier: `verify.py` settles all
# three perfectly well, and an item generated another way would be admitted.
# So it is recorded here as a reason rather than removed from the coverage
# table, and the filler skips the affected subtopics with this text as the
# blocking reason. Adding `log` and `erf` to the renderer's whitelist clears
# all three, which is exactly the kind of decision a human should make about a
# module they own rather than a factory taking for itself.
# Kinds whose answers the instance renderer cannot express, and the reason,
# so a subtopic is skipped with something a human can read rather than
# failing sixty times a night.
#
# Empty, and worth keeping rather than deleting. ph_strong, ph_weak and normal
# were all in here because the renderer had no logarithm and no error
# function: they verified perfectly and could not state their own answers.
# `instances._ALLOWED_FUNCTIONS` now carries log, log10, exp, erf and a
# decimal round, and a real ph_strong family was measured admitting 12 of 12
# through both gates, so all three are fillable. The next kind to outgrow the
# renderer goes here rather than silently burning a night of budget.
KINDS_NEEDING_ABSENT_MATH: dict[str, str] = {}


def writable_kinds(subtopic_id: str) -> tuple[str, ...]:
    """The kinds this subtopic may be filled with AND whose answers render.

    `verify.kinds_for` says what the code can CHECK. This says what the factory
    can WRITE. A subtopic where the two differ to nothing is skipped before any
    call is made, because a call that can only end in a refusal is a call that
    should not be made.
    """
    return tuple(k for k in verify.kinds_for(subtopic_id)
                 if k not in KINDS_NEEDING_ABSENT_MATH)


def unwritable_reason(subtopic_id: str) -> Optional[str]:
    """Why this subtopic cannot be filled at all, or None if it can."""
    kinds = verify.kinds_for(subtopic_id)
    if not kinds:
        return "no checker"
    if writable_kinds(subtopic_id):
        return None
    reasons = sorted({KINDS_NEEDING_ABSENT_MATH[k] for k in kinds})
    return "; ".join(reasons)


# `want` values a printed question can actually be read back as. The solvers
# answer more than the extractors can read, and a family built on one of the
# unreachable ones passes gate 1 and then fails every round trip.
READABLE_WANTS = {
    "moles_mass": ("moles", "mass"),
    "limiting_reagent": ("limiting", "mass"),
    "concentration_dilution": ("c2", "c"),
    "gas_laws": ("v2", "v"),
}


# ---------------------------------------------------------------------------
# Check 1: placeholders
# ---------------------------------------------------------------------------

# A name inside a placeholder body or a constraint. Function names from the
# expression whitelist are not parameters, and neither is a bare number.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _names_in(text: str) -> set[str]:
    """Every declared-parameter name a pattern's placeholders could refer to."""
    found: set[str] = set()
    for body in instances._PLACEHOLDER.findall(text or ""):
        for token in _IDENT.findall(body):
            if token not in instances._ALLOWED_FUNCTIONS:
                found.add(token)
    return found


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(_walk_strings(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_walk_strings(item))
        return out
    return []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_draft(draft: PracticeTemplateDraft, subtopic_id: str) -> None:
    """Every structural check that can be made without rendering anything.

    Raises `TemplateRejected` with a reason a human can act on. Deliberately
    one reason at a time: the first thing wrong with a family is usually the
    only thing wrong with it, and a list of eight complaints reads as a broken
    generator rather than a broken template.
    """
    kind = (draft.verify_kind or "").strip()
    if kind not in verify.KINDS:
        raise TemplateRejected(
            f"{kind!r} is not a verify kind, so nothing in this codebase could "
            f"ever check a question from this family")
    allowed = verify.kinds_for(subtopic_id)
    if not allowed:
        raise TemplateRejected(
            f"{subtopic_id} has no checker at all, so it should never have "
            "been asked for")
    if kind not in allowed:
        raise TemplateRejected(
            f"{kind!r} is not one of the kinds {subtopic_id} may be filled "
            f"with ({', '.join(allowed)}). A question checked by the wrong "
            "routine is checked against a different problem")

    if draft.calculator not in CALCULATOR:
        raise TemplateRejected(
            f"calculator {draft.calculator!r} is not one of {CALCULATOR}")
    if draft.difficulty not in DIFFICULTIES:
        raise TemplateRejected(
            f"difficulty {draft.difficulty!r} is not one of {DIFFICULTIES}")
    if draft.marks is not None and not 1 <= int(draft.marks) <= 12:
        raise TemplateRejected(
            f"marks {draft.marks!r} is not a mark total a marker would award")

    for name, text in (("question_pattern", draft.question_pattern),
                       ("answer_pattern", draft.answer_pattern),
                       ("working_pattern", draft.working_pattern)):
        if not (text or "").strip():
            raise TemplateRejected(f"{name} is empty")

    _check_payload_shape(kind, draft.check_pattern)
    _check_placeholders(draft)

    if not draft.params:
        raise TemplateRejected(
            "the family declares no parameters, so every instance of it would "
            "be the same question")


def _check_payload_shape(kind: str, payload: dict) -> None:
    """The exact keys this kind's extractor returns, and no others.

    The single most expensive mistake a template can make. A payload key the
    extractor never returns fails the round trip on every instance, and the
    rejection reads as a mathematics failure rather than as the spelling
    mistake it is, so it is caught here by name.
    """
    if not isinstance(payload, dict) or not payload:
        raise TemplateRejected("check_pattern is not an object")

    stated = str(payload.get("kind") or "").strip()
    if stated and stated != kind:
        raise TemplateRejected(
            f"check_pattern says kind={stated!r} while the family says "
            f"{kind!r}, so the question would be settled by the wrong routine")

    required, optional = CHECK_KEYS[kind]
    keys = {k for k in payload if k != "kind"}

    unknown = sorted(keys - required - optional)
    if unknown:
        raise TemplateRejected(
            f"check_pattern carries {unknown}, which the {kind} reader never "
            "returns from a printed question. Every instance of this family "
            "would fail the round trip")
    missing = sorted(required - keys)
    if missing:
        raise TemplateRejected(
            f"check_pattern is missing {missing}, which the {kind} checker "
            "cannot compute an answer without")

    if kind in NEEDS_ROUNDING and not (keys & _ROUNDING):
        raise TemplateRejected(
            f"a {kind} question states a number, so it must state the rounding "
            "it wants as dp or sf. Without one no printed answer can be told "
            "apart from a wrong one")
    if len(keys & _ROUNDING) > 1:
        raise TemplateRejected(
            "check_pattern states both dp and sf, so which rounding the answer "
            "is marked against is undecided")

    wants = READABLE_WANTS.get(kind)
    if wants is not None:
        want = str(payload.get("want") or "").strip()
        if want not in wants:
            raise TemplateRejected(
                f"want={want!r} cannot be read back off a printed {kind} "
                f"question. Only {list(wants)} can")


def _check_placeholders(draft: PracticeTemplateDraft) -> None:
    declared = set(draft.params or {})
    used_in_question = _names_in(draft.question_pattern)
    used = (used_in_question
            | _names_in(draft.answer_pattern)
            | _names_in(draft.working_pattern))
    for text in _walk_strings(draft.check_pattern):
        used |= _names_in(text)

    undeclared = sorted(used - declared)
    if undeclared:
        raise TemplateRejected(
            f"the patterns use {undeclared}, which the family never declares. "
            "An undeclared placeholder renders as a literal brace on the "
            "student's screen")
    unused = sorted(declared - used)
    if unused:
        raise TemplateRejected(
            f"the family declares {unused} and never uses them, so the "
            "parameter space it claims is larger than the set of distinct "
            "questions it can actually produce")
    if not used_in_question:
        raise TemplateRejected(
            "question_pattern holds no placeholders, so every instance of this "
            "family is literally the same question")


# ---------------------------------------------------------------------------
# Building the row
# ---------------------------------------------------------------------------

def template_id(subtopic_id: str, draft: PracticeTemplateDraft) -> str:
    """A stable id derived from what the family IS, not from when it was made.

    Content addressed on purpose. A model that returns the same family twice
    then writes to the same row rather than banking a second copy of it, and
    the filler can see that a call bought nothing new instead of quietly
    doubling a subtopic's template count with duplicates.
    """
    digest = hashlib.sha256(canonical_json([
        subtopic_id, draft.verify_kind, draft.question_pattern,
        draft.answer_pattern, draft.params, draft.constraints,
        draft.check_pattern,
    ]).encode("utf-8")).hexdigest()
    return f"t_{digest[:24]}"


def to_row(draft: PracticeTemplateDraft, sub: syllabus.Subtopic, *,
           model: Optional[str] = None, now: Optional[int] = None,
           status: str = "live",
           reject_reason: Optional[str] = None) -> TemplateRow:
    """A draft as a bank row, with the check payload's kind filled in.

    The prompt asks for `kind` inside `check_pattern` and this fills it in when
    the model leaves it out, because `verify.admit` dispatches on it and a
    payload without one is checked by nothing at all. A payload that states a
    DIFFERENT kind is a rejection, not a repair: that is the model contradicting
    itself, and quietly picking a side would hide it.
    """
    payload = dict(draft.check_pattern or {})
    payload["kind"] = draft.verify_kind
    return TemplateRow(
        id=template_id(sub.id, draft),
        subject=sub.subject_key,
        subtopic_id=sub.id,
        verify_kind=draft.verify_kind,
        calculator=draft.calculator,
        difficulty=draft.difficulty,
        question_pattern=draft.question_pattern.strip(),
        answer_pattern=draft.answer_pattern.strip(),
        working_pattern=draft.working_pattern.strip(),
        params=dict(draft.params or {}),
        constraints=[str(c) for c in (draft.constraints or [])],
        check_pattern=payload,
        prompt_version=PROMPT_VERSION,
        syllabus_version=store.syllabus_fingerprint(),
        created_at=int(time.time()) if now is None else int(now),
        marks=None if draft.marks is None else int(draft.marks),
        status=status,
        reject_reason=reject_reason,
        model=model,
    )


def check_space(template: TemplateRow) -> int:
    """Check 2. The number of distinct instances the family can produce."""
    try:
        size = instances.space_size(template)
    except instances.TemplateError as exc:
        raise TemplateRejected(f"the parameter space will not enumerate: {exc}")
    if size < instances.MIN_SPACE:
        raise TemplateRejected(
            f"the family has only {size} distinct instances after its "
            f"constraints, below the {instances.MIN_SPACE} floor. A student "
            "would meet the same question again within a week")
    return size


def probe(template: TemplateRow):
    """Check 4. Render ONE instance and put it through the real admission gate.

    Returns the probe instance and its verdict. Raises `TemplateRejected` when
    the verdict is not a pass, because a family whose first question the
    verifier cannot settle is a family whose sixtieth it cannot settle either,
    and finding that out now costs one instance instead of sixty.
    """
    try:
        made = instances.expand(template, count=PROBE_COUNT, seed=0)
    except instances.TemplateError as exc:
        raise TemplateRejected(f"no instance of the family renders: {exc}")
    instance = made[0]
    verdict = verify.admit(instance)
    if not (verdict.verified and verdict.conclusive):
        raise TemplateRejected(
            f"the probe question was not admitted: {verdict.notes}. "
            f"Question as printed: {instance.question!r}; answer as stated: "
            f"{instance.answer!r}")
    return instance, verdict


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TemplateAttempt:
    """What one LLM call bought.

    `template` is present even when `ok` is False whenever the draft parsed far
    enough to become a row, because a rejected template is KEPT with its
    reason. It is the only record of what the model gets wrong on a subtopic,
    and the filler's blocking rule is measured on exactly that.
    """

    ok: bool
    calls: int
    template: Optional[TemplateRow] = None
    reason: Optional[str] = None
    space: int = 0
    probe_question: Optional[str] = None


class TemplateFactory:
    """One call in, one validated family out. No retries, by design."""

    def __init__(self, client: LLMClient, model: Optional[str] = None):
        self._client = client
        self._model = model
        self._prompts: dict[str, str] = {}

    def system_prompt(self, subject_key: str) -> str:
        if subject_key not in PROMPT_FILES:
            raise ValueError(
                f"no practice template prompt for subject {subject_key!r}")
        if subject_key not in self._prompts:
            self._prompts[subject_key] = load_prompt(PROMPT_FILES[subject_key])
        return self._prompts[subject_key]

    def user_turn(self, sub: syllabus.Subtopic, kind: str,
                  difficulty: str = "medium",
                  existing: tuple[str, ...] = ()) -> str:
        """What to ask for, including what the bank already has.

        The existing question openings are listed so a second family for the
        same subtopic is a different question rather than the same one with the
        ranges nudged. Nothing enforces it, which is why it is a request and
        not a rule; the parameter space check is what actually stops a family
        being worthless.
        """
        lines = [
            f"Write one parameterised question family.",
            f"Subject: WACE {syllabus.subject_for_key(sub.subject_key)}.",
            f"verify_kind: {kind}. Use exactly this kind.",
            f"difficulty: {difficulty}.",
            f"calculator: {sub.calculator}.",
            syllabus.guidance_block(sub),
        ]
        if existing:
            lines.append(
                "The bank already holds these families for this subtopic. "
                "Write one that asks something different, not a rewording:")
            lines.extend(f"  - {text}" for text in existing)
        return "\n".join(lines)

    def generate(self, sub: syllabus.Subtopic, kind: str, *,
                 difficulty: str = "medium",
                 existing: tuple[str, ...] = (),
                 temperature: float = 0.7) -> TemplateAttempt:
        """One LLM call, then all four structural checks. Never more than one call."""
        system = self.system_prompt(sub.subject_key)
        user = self.user_turn(sub, kind, difficulty=difficulty,
                              existing=existing)
        log.info("practice.template.call",
                 extra={"subtopic": sub.id, "kind": kind})
        raw = self._client.complete(system, user, tier="strong",
                                    temperature=temperature)

        try:
            payload = extract_json(raw)
        except (ValueError, TypeError) as exc:
            return TemplateAttempt(
                ok=False, calls=1,
                reason=f"the reply was not JSON: {exc}")

        refusal = payload.get("refuse") if isinstance(payload, dict) else None
        if refusal:
            # An honest "this kind cannot be parameterised here" is a real
            # answer and is recorded as a failure, because it still cost a call
            # and it still means the subtopic is not being filled.
            return TemplateAttempt(
                ok=False, calls=1,
                reason=f"the generator refused: {str(refusal)[:300]}")

        try:
            draft = PracticeTemplateDraft.model_validate(payload)
        except ValidationError as exc:
            return TemplateAttempt(
                ok=False, calls=1,
                reason=f"the reply does not match the template schema: "
                       f"{str(exc)[:300]}")

        try:
            validate_draft(draft, sub.id)
        except TemplateRejected as exc:
            # No row: a draft that fails the shape checks may still be worth
            # storing as a rejection, so build the row first where we can.
            row = _safe_row(draft, sub, self._model, str(exc))
            return TemplateAttempt(ok=False, calls=1, template=row,
                                   reason=str(exc))

        row = to_row(draft, sub, model=self._model)
        try:
            space = check_space(row)
            instance, _ = probe(row)
        except TemplateRejected as exc:
            return TemplateAttempt(
                ok=False, calls=1,
                template=_rejected(row, str(exc)), reason=str(exc))
        except Exception as exc:                                   # noqa: BLE001
            reason = (f"expanding the family raised "
                      f"{type(exc).__name__}: {str(exc)[:200]}")
            return TemplateAttempt(ok=False, calls=1,
                                   template=_rejected(row, reason),
                                   reason=reason)

        log.info("practice.template.kept",
                 extra={"subtopic": sub.id, "kind": kind, "space": space,
                        "template": row.id})
        return TemplateAttempt(ok=True, calls=1, template=row, space=space,
                               probe_question=instance.question)


def _rejected(row: TemplateRow, reason: str) -> TemplateRow:
    from dataclasses import replace
    return replace(row, status="rejected", reject_reason=reason[:1000])


def _safe_row(draft: PracticeTemplateDraft, sub: syllabus.Subtopic,
              model: Optional[str], reason: str) -> Optional[TemplateRow]:
    """A rejected row when one can be built, None when the draft is too broken.

    A draft naming a kind that does not exist still has patterns worth keeping,
    but a draft that cannot even be hashed into an id is not worth failing the
    whole run over.
    """
    try:
        return to_row(draft, sub, model=model, status="rejected",
                      reject_reason=reason[:1000])
    except Exception:                                              # noqa: BLE001
        return None
