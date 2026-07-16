"""Booklet types (product lines) offered to parents.

A "program" sits above the subject engines. It decides which subject(s) a
booklet covers, how the request is phrased to the generator, and the label
printed on the cover. Renaming a product line is a one-line edit to `label`
here — nothing else in the codebase hard-codes these names.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Program:
    key: str                       # internal id, used on the CLI/web
    label: str                     # display name on the cover and menus
    subject_display: str           # secondary line under the title
    subjects: tuple[str, ...]      # subject engines to run (empty = parent picks)
    pick_subject: bool             # parent chooses the subject (Academic Accelerate)
    blurb: str                     # one-line description for menus

    def describe(self, subject: str, year_level: str, topic: Optional[str]) -> str:
        """Build the free-text description the outline parser expects, for one
        subject within this program."""
        t = f", {topic}" if topic else ""
        key = self.key
        if key == "scholarships":
            return (f"{year_level} scholarship and selective-school preparation, "
                    f"verbal and quantitative reasoning{t}")
        if key == "naplan":
            if subject == "Mathematics":
                return f"{year_level} numeracy, NAPLAN practice{t}"
            return (f"{year_level} literacy, NAPLAN practice: reading comprehension "
                    f"and language conventions{t}")
        # accelerate (or any subject-driven program)
        return f"{year_level} {subject}{t}"


PROGRAMS: dict[str, Program] = {
    "scholarships": Program(
        key="scholarships",
        label="Scholarships",
        subject_display="Verbal and Quantitative Reasoning",
        subjects=("Reasoning",),
        pick_subject=False,
        blurb="Selective-school and scholarship-test style reasoning practice.",
    ),
    "naplan": Program(
        key="naplan",
        label="NAPLAN Practice",
        subject_display="Numeracy and Literacy",
        subjects=("Mathematics", "English"),
        pick_subject=False,
        blurb="NAPLAN-style numeracy and literacy in one booklet.",
    ),
    "accelerate": Program(
        key="accelerate",
        label="Academic Accelerate",
        subject_display="",  # filled with the chosen subject at runtime
        subjects=(),
        pick_subject=True,
        blurb="Curriculum revision to help a student get ahead at school. Pick the subject.",
    ),
}

# Subjects a parent may pick for Academic Accelerate.
ACCELERATE_SUBJECTS = ("Mathematics", "English", "Science")

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
