"""Booklet types (product lines) offered to parents.

A "program" sits above the subject engines. It decides which subject(s) a
booklet covers, how the request is phrased to the generator, and the label
printed on the cover. Renaming a product line is a one-line edit to `label`
here: nothing else in the codebase hard-codes these names.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


GUIDANCE_DIR = Path(__file__).resolve().parent / "guidance"

# External material is a production safety boundary, not a convenience toggle.
# Leave this unset for FolioAI's clean-room mode. It may only be set after every
# source in the production store, plus any network image workflow, has completed
# a commercial-rights review.
EXTERNAL_CONTENT_ENV = "FOLIO_ALLOW_REVIEWED_EXTERNAL_CONTENT"

# Only these independently authored products are offered to customers by
# default. CLI users can still address every entry in PROGRAMS. A reviewed
# product must be named explicitly before the web app will display or accept it.
WEB_PROGRAM_ALLOWLIST_ENV = "FOLIO_WEB_PROGRAM_ALLOWLIST"
DEFAULT_WEB_PROGRAMS = ("naplan", "accelerate")

# Products that practise for the NAPLAN tests, whatever wrapper they are sold
# in. `describe` phrases their request as numeracy or literacy practice rather
# than as a plain school subject.
NAPLAN_PROGRAMS = frozenset({"naplan"})


# How many booklets a term plan is. ONE definition, because this number is
# simultaneously what the customer is charged and what the customer receives.
# It used to live twice: views.py decided the credit cost, jobs.py decided how
# many booklets to generate, and nothing connected them. Editing one and not
# the other bills for ten and delivers four, or the reverse, and neither shows
# up as an error anywhere.
TERM_PLAN_WEEKS = 10


@dataclass(frozen=True)
class Program:
    key: str                       # internal id, used on the CLI/web
    label: str                     # display name on the cover and menus
    subject_display: str           # secondary line under the title
    subjects: tuple[str, ...]      # subject engines to run (empty = parent picks)
    pick_subject: bool             # parent chooses the subject (Academic Accelerate)
    blurb: str                     # one-line description for menus
    guidance_file: Optional[str] = None
    year_guidance_pattern: Optional[str] = None
    use_rag: bool = True
    # This product's own word for ONE subject engine, keyed by the engine name.
    #
    # `subject_display` above is the whole cover line and cannot be taken
    # apart, so when the pipeline has to narrow it (a booklet that lost a
    # subject upstream must not keep claiming it) the only names left to fall
    # back on are the engines': a maths-only NAPLAN booklet was covered
    # "Mathematics" when the product, its menu and its marketing all call that
    # half "Numeracy". Honesty and the product's vocabulary are not a trade,
    # and this file is the source of truth for cover and menu labels, so the
    # words live here rather than in the pipeline that needs them.
    #
    # Empty is legitimate for a product whose cover line is not a list of
    # subjects at all; `display_for` then falls back to the engine name, which
    # is what happened everywhere before this existed.
    subject_display_by_subject: dict[str, str] = field(default_factory=dict)

    def display_for(self, subject: str) -> str:
        """This product's word for one subject, or the engine name."""
        return self.subject_display_by_subject.get(subject) or subject

    def display_for_all(self, subjects) -> str:
        """The cover line for exactly these subjects, in this product's words.

        The whole declared list reproduces `subject_display` (checked by
        scripts/check_cover_vocabulary.py, which is what keeps the two in step
        when a product is renamed), so a caller narrowing a cover can use this
        for any subset and get a line in the same voice as the one it replaces.
        """
        words = [self.display_for(s) for s in subjects if s]
        return " and ".join(words)

    def describe(self, subject: str, year_level: str, topic: Optional[str]) -> str:
        """Build the free-text description the outline parser expects, for one
        subject within this program."""
        t = f", {topic}" if topic else ""
        key = self.key
        if key == "scholarships":
            return (f"{year_level} scholarship and selective-school preparation, "
                    f"verbal and quantitative reasoning{t}")
        # Every NAPLAN-shaped product, not just the term one. A key added here
        # and forgotten would fall through to the generic subject description
        # below, and the booklet would quietly stop being NAPLAN practice.
        if key in NAPLAN_PROGRAMS:
            if subject == "Mathematics":
                return f"{year_level} numeracy, NAPLAN practice{t}"
            return (f"{year_level} literacy, NAPLAN practice: reading comprehension "
                    f"and language conventions{t}")
        if key == "methods_exam":
            return (f"{year_level} WACE Mathematics Methods Units 3 and 4, "
                    f"ATAR examination practice{t}")
        # accelerate (or any subject-driven program)
        return f"{year_level} {subject}{t}"

    def authoring_guidance(self, year_level: Optional[str] = None) -> Optional[str]:
        """Load base instructions and an optional year-specific supplement.

        Calls without a year keep the original behaviour. A missing supplement
        is harmless so the four NAPLAN year guides can be added incrementally.

        Order is fixed and load-bearing: this program's guide, then the year
        supplement. A supplement that arrived before the guide it qualifies
        would be read as the general rule and contradicted by everything after
        it.

        Note that this CONCATENATES. There is no override. A second guidance
        file saying "ignore the rule above" would sit in the same prompt as the
        rule itself, and the model would follow whichever it liked, per
        subtopic. Any product that needs a different rule needs its own
        standalone guide, not a supplement.
        """
        parts: list[str] = []
        if self.guidance_file:
            path = GUIDANCE_DIR / self.guidance_file
            parts.append(path.read_text(encoding="utf-8").strip())
        if self.year_guidance_pattern and year_level:
            digits = "".join(ch for ch in year_level if ch.isdigit())
            if digits:
                path = GUIDANCE_DIR / self.year_guidance_pattern.format(year=digits)
                if path.is_file():
                    parts.append(path.read_text(encoding="utf-8").strip())
        return "\n\n".join(part for part in parts if part) or None


PROGRAMS: dict[str, Program] = {
    "scholarships": Program(
        key="scholarships",
        label="Scholarships",
        subject_display="Verbal and Quantitative Reasoning",
        subjects=("Reasoning",),
        pick_subject=False,
        blurb="Selective-school and scholarship-test style reasoning practice.",
        # One engine, and the cover line is already this product's name for it.
        # Stated anyway so a narrowed cover cannot fall back to the bare word
        # "Reasoning", which is the engine's name and not the product's.
        subject_display_by_subject={
            "Reasoning": "Verbal and Quantitative Reasoning"},
    ),
    "naplan": Program(
        key="naplan",
        label="NAPLAN Practice",
        subject_display="Numeracy and Literacy",
        subjects=("Mathematics", "English"),
        pick_subject=False,
        blurb="NAPLAN-style numeracy and literacy in one booklet.",
        # The two halves of "Numeracy and Literacy", split so a cover narrowed
        # to one of them still speaks NAPLAN's language. The test the customer
        # is buying practice for uses these words; "Mathematics" over a
        # numeracy booklet is a different product's vocabulary.
        subject_display_by_subject={"Mathematics": "Numeracy",
                                    "English": "Literacy"},
        guidance_file="naplan_practice.txt",
        year_guidance_pattern="naplan_year_{year}.txt",
        # The existing vector library contains past assessment papers whose
        # terms do not permit commercial app use. Keep external retrieval off
        # for this product until the store is rebuilt entirely from reviewed,
        # commercially permitted sources. The internal guide above still
        # reaches every generation stage.
        use_rag=False,
    ),
    "accelerate": Program(
        key="accelerate",
        label="Academic Accelerate",
        subject_display="",  # filled with the chosen subject at runtime
        subjects=(),
        pick_subject=True,
        blurb="Curriculum revision to help a student get ahead at school. Pick the subject.",
        # Deliberately empty. The parent picks the subject from a menu that
        # already reads "Mathematics" or "English", and the cover prints the
        # word they chose, so the engine name IS this product's word for it.
        # An identity mapping here would be a second copy of ACCELERATE_SUBJECTS
        # that nothing forces to agree with the first.
        # Accelerate is a launch product and production runs clean-room, so
        # external retrieval is off for it exactly as it is for NAPLAN. Without
        # a guide it would be the only customer-facing product generating with
        # no curriculum grounding at all.
        guidance_file="accelerate_practice.txt",
        use_rag=False,
    ),
    "methods_exam": Program(
        key="methods_exam",
        label="Methods Exam",
        subject_display="Mathematics Methods Units 3 and 4",
        subjects=("Mathematics Methods",),
        pick_subject=False,
        blurb="A full practice ATAR examination paper for WACE Mathematics Methods, "
              "with a calculator-free and a calculator-assumed section and a marking key.",
        # One engine again, and the paper is titled the way SCSA titles it.
        subject_display_by_subject={
            "Mathematics Methods": "Mathematics Methods Units 3 and 4"},
    ),
}

# Programs that produce an exam paper rather than a teaching booklet. These
# take a different pipeline entry point (run_exam) and formatter
# (render_exam_pdf), and are only offered at senior-secondary year levels.
EXAM_PROGRAMS = {"methods_exam"}
EXAM_YEARS = ("Year 11", "Year 12")

# Subjects a parent may pick for Academic Accelerate.
# Science is intentionally not offered: there is no Science material in the RAG
# library, so it would generate ungrounded. The engine and its prompts are
# still present, so re-enabling it is just adding "Science" back here.
ACCELERATE_SUBJECTS = ("Mathematics", "English")

_SUBJECT_ALIASES = {
    "maths": "Mathematics", "math": "Mathematics", "mathematics": "Mathematics",
    "english": "English", "science": "Science",
}


def normalise_subject(subject: str) -> Optional[str]:
    return _SUBJECT_ALIASES.get(subject.strip().lower())


def get_program(key: str) -> Program:
    k = key.strip().lower()
    if k not in PROGRAMS:
        raise ValueError(
            f"Unknown program {key!r}. Choose one of: {', '.join(PROGRAMS)}"
        )
    return PROGRAMS[k]


def reviewed_external_content_enabled() -> bool:
    """True only after the production external-content review is approved."""
    return os.environ.get(EXTERNAL_CONTENT_ENV, "").strip() == "1"


def program_external_rag_enabled(program: Program) -> bool:
    """Effective RAG permission for a program, including the global gate."""
    return program.use_rag and reviewed_external_content_enabled()


def web_program_keys() -> tuple[str, ...]:
    """Customer program keys, fail-closed to the reviewed default set."""
    configured = os.environ.get(WEB_PROGRAM_ALLOWLIST_ENV)
    if configured is None:
        requested = DEFAULT_WEB_PROGRAMS
    else:
        requested = tuple(
            key.strip().lower() for key in configured.split(",") if key.strip()
        )
    requested_set = set(requested)
    return tuple(key for key in PROGRAMS if key in requested_set)


def customer_programs() -> dict[str, Program]:
    """Programs the website may display and accept in the current process."""
    return {key: PROGRAMS[key] for key in web_program_keys()}
