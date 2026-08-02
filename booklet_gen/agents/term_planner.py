from __future__ import annotations

import logging
import re

from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import TermPlan, TermWeek
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


# Named skills, in teaching order, one per week. These are the shape the owner
# asked for ("week 1 alliteration and repetition, week 2 kinds of rhyme"): a
# ladder of specific, nameable skills rather than a topic repeated with a
# different number after it.
#
# They serve twice. They are the deterministic fallback when the planner call
# fails, and they are the pool the repair step draws from when the model hands
# back the same focus in two weeks. Both paths need the ladder to be long
# enough that a ten week term never runs out.
_LADDERS: dict[str, tuple[str, ...]] = {
    "english": (
        "alliteration and repetition",
        "kinds of rhyme: perfect, half and internal",
        "similes and metaphors",
        "personification and imagery",
        "rhythm, syllables and line breaks",
        "narrative structure: orientation, complication, resolution",
        "characterisation through dialogue",
        "point of view and narrative voice",
        "finding the main idea in a text",
        "inference: reading between the lines",
        "author's purpose and tone",
        "persuasive devices in advertising",
        "sentence types and clauses",
        "punctuating direct speech",
        "apostrophes: contraction and possession",
        "commas in lists and after introductory phrases",
        "verb tense agreement",
        "prefixes, suffixes and word families",
        "synonyms, antonyms and shades of meaning",
        "homophones and commonly confused words",
    ),
    "mathematics": (
        "place value and rounding",
        "addition and subtraction with regrouping",
        "multiplication facts and strategies",
        "short and long division",
        "factors, multiples and prime numbers",
        "equivalent fractions",
        "adding and subtracting fractions",
        "multiplying and dividing fractions",
        "decimals and the four operations",
        "percentages of an amount",
        "ratio and proportion",
        "number patterns and their rules",
        "expressions and substitution",
        "solving one step equations",
        "perimeter and area",
        "volume and capacity",
        "angles and angle rules",
        "reflection, rotation and translation",
        "collecting and displaying data",
        "chance and probability",
    ),
    "reasoning": (
        "odd one out",
        "verbal analogies",
        "letter codes and simple ciphers",
        "number sequences",
        "letter and symbol sequences",
        "logical deduction from statements",
        "ordering and seating puzzles",
        "hidden words and word building",
        "worded number problems",
        "figure series and matrices",
        "reading data from tables",
        "time, distance and speed puzzles",
        "if-then reasoning",
        "elimination grids",
        "nets, cubes and spatial reasoning",
        "fraction and percentage reasoning",
        "verbal inference",
        "algebraic thinking without algebra",
        "probability reasoning",
        "mixed puzzle sets against the clock",
    ),
}

_SUBJECT_KEYS = {
    "english": "english", "literacy": "english",
    "mathematics": "mathematics", "maths": "mathematics", "math": "mathematics",
    "numeracy": "mathematics", "mathematics methods": "mathematics",
    "reasoning": "reasoning", "verbal reasoning": "reasoning",
    "quantitative reasoning": "reasoning",
}


def _ladder(subject: str) -> tuple[str, ...]:
    """The skill ladder for a subject.

    NAPLAN Practice runs maths and English in one booklet, so a plan for it
    interleaves the two ladders: a week is a literacy skill or a numeracy
    skill, never both, which keeps each week's focus concrete.
    """
    key = _SUBJECT_KEYS.get((subject or "").strip().lower())
    if key:
        return _LADDERS[key]
    interleaved: list[str] = []
    for eng, maths in zip(_LADDERS["english"], _LADDERS["mathematics"]):
        interleaved.append(eng)
        interleaved.append(maths)
    return tuple(interleaved)


def _norm_focus(focus: str) -> str:
    """Normalise a focus for repeat detection.

    Case, whitespace and trailing punctuation only. "Alliteration and
    repetition" and "alliteration and repetition." are the same week's work;
    "kinds of rhyme" and "internal rhyme" are not, and deciding otherwise is
    the planner's job rather than the comparison's.
    """
    return re.sub(r"\s+", " ", (focus or "").strip().lower()).strip(" .:;,")


# What a week's focus has to look like before it is worth generating against.
# "Fractions (part 3)" passes this and is still a bad focus, which is why the
# ladder exists; this only catches the empties and the essays.
_MIN_FOCUS_WORDS = 2


class TermPlannerAgent:
    """Plans a term as a sequence of weekly skills with a difficulty ramp.

    The plan drives ten separate booklets, so two weeks sharing a focus is not
    a cosmetic problem: it is the same booklet printed twice. The model is
    asked for distinct named skills, the reply is checked for repeats, a repeat
    earns one retry with the offending weeks named, and anything still
    repeating is replaced from the subject's skill ladder. Falls back to the
    ladder outright if the model call fails, so a term plan never dies on a bad
    planner response.
    """

    def __init__(self, client: LLMClient, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries
        self._system = load_prompt("term_planner.txt")

    def plan(
        self,
        program_label: str,
        subject: str,
        year_level: str,
        weeks: int,
        topic_hint: str | None = None,
    ) -> TermPlan:
        user = (
            f"Booklet type: {program_label}\n"
            f"Subject: {subject}\n"
            f"Year level: {year_level}\n"
            f"Number of weeks: {weeks}\n"
        )
        if topic_hint:
            user += f"Focus the term around: {topic_hint}\n"
        user += self._progression_block(subject, weeks)
        user += f"\nProduce exactly {weeks} weeks."

        error = ""
        for attempt in range(1, self._max_retries + 1):
            msg = user if not error else (
                f"{user}\n\nYour previous attempt failed:\n{error}\nReturn corrected JSON."
            )
            try:
                raw = self._client.complete(self._system, msg, tier="strong", temperature=0.4)
                plan = TermPlan.model_validate(extract_json(raw))
                plan = self._normalise(plan, weeks, subject, topic_hint)
                repeats = self._repeated_weeks(plan)
                if repeats and attempt < self._max_retries:
                    error = self._repeat_feedback(plan, repeats)
                    log.warning("term_planner.repeated_focus",
                                extra={"attempt": attempt, "weeks": repeats})
                    continue
                if repeats:
                    plan = self._repair(plan, repeats, subject, topic_hint)
                    log.warning("term_planner.repaired_focus", extra={"weeks": repeats})
                log.info("term_planner.success",
                         extra={"weeks": len(plan.weeks), "attempt": attempt})
                return plan
            except (ValueError, ValidationError) as e:
                error = str(e)
                log.warning("term_planner.retry", extra={"attempt": attempt, "error": error[:200]})
            except Exception as e:  # client/network failure: no point retrying the same call
                log.warning("term_planner.client_error", extra={"error": str(e)[:200]})
                break

        log.warning("term_planner.fallback")
        return self._fallback(subject, weeks, topic_hint)

    # ---------- what we ask for ----------

    @staticmethod
    def _progression_block(subject: str, weeks: int) -> str:
        """Spell the progression out in the user turn.

        The system prompt says "do not repeat the same focus twice", which a
        model satisfies with "fractions (part 1)" through "fractions (part 6)".
        What the product wants is a different named skill each week, so the
        request has to name that and show it.
        """
        ladder = _ladder(subject)
        example = ", ".join(f"week {i + 1} {s}" for i, s in enumerate(ladder[:3]))
        return (
            "\nEach week must hone a DIFFERENT NAMED SKILL, and the week's focus "
            "is the name of that skill. A term reads as a ladder: "
            f"{example}, and so on for all {weeks} weeks.\n"
            "- Never number the same topic across weeks. \"fractions (part 1)\", "
            "\"fractions (part 2)\" is one topic wearing different labels, not a "
            "progression, and it produces near-identical booklets.\n"
            "- No two weeks may share a focus, revision weeks included. A "
            "revision week names the earlier skills it revisits, so week 9 and "
            "week 10 revise different things.\n"
            "- Order them so each week builds on what the weeks before it "
            "taught.\n"
        )

    # ---------- checking what came back ----------

    @staticmethod
    def _repeated_weeks(plan: TermPlan) -> list[int]:
        """Week numbers whose focus repeats an earlier week, or says nothing.

        A blank or one-word focus counts as a repeat: it will generate the same
        vague booklet as its neighbours, which is the defect this is here to
        catch, and it takes the same repair.
        """
        seen: set[str] = set()
        bad: list[int] = []
        for w in plan.weeks:
            key = _norm_focus(w.focus)
            if not key or len(key.split()) < _MIN_FOCUS_WORDS or key in seen:
                bad.append(w.week)
                continue
            seen.add(key)
        return bad

    @staticmethod
    def _repeat_feedback(plan: TermPlan, repeats: list[int]) -> str:
        named = ", ".join(
            f"week {w.week} ({w.focus!r})" for w in plan.weeks if w.week in set(repeats)
        )
        return (
            f"These weeks repeat an earlier focus or name no real skill: {named}. "
            "Give each of them a different named skill, and keep the weeks that "
            "were already distinct as they are."
        )

    @classmethod
    def _repair(cls, plan: TermPlan, repeats: list[int], subject: str,
                topic_hint: str | None) -> TermPlan:
        """Replace repeated foci from the ladder, keeping the good weeks.

        Last line of defence, reached only when the model has already had its
        retry. Shipping a term with two identical weeks is worse than shipping
        one week whose focus the planner did not choose.
        """
        bad = set(repeats)
        taken = {_norm_focus(w.focus) for w in plan.weeks if w.week not in bad}
        spare = [s for s in _ladder(subject) if _norm_focus(s) not in taken]
        for w in plan.weeks:
            if w.week not in bad:
                continue
            if not spare:
                break
            skill = spare.pop(0)
            w.focus = cls._with_hint(skill, topic_hint)
            taken.add(_norm_focus(skill))
        return plan

    # ---------- shaping ----------

    @classmethod
    def _normalise(cls, plan: TermPlan, weeks: int, subject: str = "",
                   topic_hint: str | None = None) -> TermPlan:
        """Trim/pad to exactly `weeks` and renumber 1..weeks.

        Padding used to append the identical week ("revision and mixed
        practice") as many times as it needed, so a planner that returned eight
        weeks for a ten week term produced two byte-identical booklets. Pad
        weeks now name different earlier skills to revise.
        """
        ws = plan.weeks[:weeks]
        while len(ws) < weeks:
            ws.append(TermWeek(week=len(ws) + 1,
                               focus=cls._revision_focus(ws, len(ws) + 1, subject),
                               difficulty="hard", revision=True))
        for i, w in enumerate(ws, 1):
            w.week = i
        return TermPlan(weeks=ws)

    @staticmethod
    def _revision_focus(earlier: list[TermWeek], week: int, subject: str) -> str:
        """A revision week that names what it revises.

        Two revision weeks that both say "revision and mixed practice" generate
        the same booklet. Naming the skills makes them different from each
        other and more useful to the parent, who can see what the week covers.
        """
        skills = [w.focus for w in earlier if w.focus and not w.revision]
        if not skills:
            skills = list(_ladder(subject))
        n_rev = sum(1 for w in earlier if w.revision)
        taken = {_norm_focus(w.focus) for w in earlier}
        # Deal the term's skills out between the revision weeks rather than
        # handing each of them the same list. Rotating the deal by how many
        # revision weeks are already placed is what makes week 9 and week 10
        # different booklets.
        for offset in range(len(skills)):
            share = skills[(n_rev + offset) % len(skills)::2] or skills
            focus = "revision: " + ", ".join(share[:3])
            if _norm_focus(focus) not in taken:
                return focus
        return f"revision: mixed practice, week {week}"

    @staticmethod
    def _with_hint(skill: str, topic_hint: str | None) -> str:
        return f"{topic_hint}: {skill}" if topic_hint else skill

    @classmethod
    def _fallback(cls, subject: str, weeks: int, topic_hint: str | None) -> TermPlan:
        """A real ladder, not the same topic numbered N times.

        This used to emit "<topic> (part 1)" through "(part N)", which is
        distinct enough to pass a duplicate check and still generates the same
        booklet every week. The fallback runs whenever the planner call fails,
        so it is what a parent gets on a bad API day; it has to be a plan they
        would have been happy to receive.
        """
        ladder = _ladder(subject)
        out: list[TermWeek] = []
        n_teaching = max(1, weeks - 2) if weeks > 2 else weeks
        for i in range(1, weeks + 1):
            if i > n_teaching:
                out.append(TermWeek(week=i,
                                    focus=cls._revision_focus(out, i, subject),
                                    difficulty="hard", revision=True))
                continue
            skill = ladder[(i - 1) % len(ladder)]
            diff = "easy" if i <= weeks / 3 else ("medium" if i <= 2 * weeks / 3 else "hard")
            out.append(TermWeek(week=i, focus=cls._with_hint(skill, topic_hint),
                                difficulty=diff))
        return TermPlan(weeks=out)
