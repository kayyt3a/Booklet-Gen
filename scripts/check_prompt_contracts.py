"""The prompts are the product, and nothing was checking them.

Everything a parent reads in a booklet is written by one of the files in
`booklet_gen/prompts/`, yet the whole of `scripts/` tested the code around them
and never the prompts themselves. A rule silently dropped from a prompt shows
up as a worse booklet weeks later, with nothing failing in between.

This pins the contracts that are supposed to hold across all of them, and the
few per-file rules that a booklet's quality actually rests on.

Run: python scripts/check_prompt_contracts.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents._shared import PROMPT_DIR, load_prompt

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


PROMPTS = sorted(p.name for p in PROMPT_DIR.glob("*.txt"))
QUESTION_GENERATORS = [p for p in PROMPTS if p.startswith("question_generator_")]
INTRO_WRITERS = [p for p in PROMPTS if p.startswith("intro_writer_")]


# ---------------------------------------------------------------------------
print("\nEvery prompt is loadable and substantial")
print("-" * 62)
check(len(PROMPTS) >= 12, "the prompt directory is populated", f"{len(PROMPTS)} files")
check(bool(QUESTION_GENERATORS) and bool(INTRO_WRITERS),
      "question generators and intro writers are both present",
      f"{len(QUESTION_GENERATORS)} generators, {len(INTRO_WRITERS)} writers")
for name in PROMPTS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(len(body.split()) > 80, f"{name} says something", f"{len(body.split())} words")


# ---------------------------------------------------------------------------
print("\nThe global blocks reach every prompt")
print("-" * 62)
# Both are appended by load_prompt rather than written into each file, so the
# thing worth checking is that no prompt bypasses that path.
for name in PROMPTS:
    loaded = load_prompt(name)
    check("WRITING STYLE" in loaded and "PEOPLE AND PLACES" in loaded,
          f"{name} carries the style and people rules")


# ---------------------------------------------------------------------------
print("\nThe house rule, in the prompts themselves")
print("-" * 62)
# CLAUDE.md: no em dashes anywhere. The formatter strips them as a backstop,
# but a prompt containing one teaches the model to produce them.
DASHES = ("—", "–")
for name in PROMPTS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    hits = [line.strip()[:60] for line in body.splitlines()
            if any(d in line for d in DASHES)]
    check(not hits, f"{name} contains no em or en dash", str(hits[:2]))


# ---------------------------------------------------------------------------
print("\nPeople and places")
print("-" * 62)
# None of this was written down anywhere before. The prompts asked for
# Australian settings and named people, said nothing about which names, and
# nothing about real institutions, which is how a sample came to run an
# invented news story about a real Perth primary school with invented staff
# named in it.
people = load_prompt(PROMPTS[0])
RULES = [
    ("real institutions are off limits", r"[Nn]ever name a real school"),
    ("names come from a real Australian classroom", r"Mandarin|Vietnamese|Punjabi"),
    ("genders are balanced in what they do", r"[Bb]alance what they DO|balance what they do"),
    ("First Nations peoples are written in the present tense",
     r"present tense"),
    ("peoples are never listed alongside animals", r"alongside animals"),
    ("contexts stay affordable", r"[Nn]o overseas holidays|affordable"),
]
for label, pattern in RULES:
    check(bool(re.search(pattern, people)), label)


# ---------------------------------------------------------------------------
print("\nDifficulty ordering, in every question generator")
print("-" * 62)
# The booklet splits each generated set in order, the front becoming in-lesson
# practice and the rest homework. An unordered set puts an easy item exactly
# where the hardest work belongs, and a hard one on question 1. Maths pinned
# this and English left it to luck.
for name in QUESTION_GENERATORS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(bool(re.search(r"easiest to hardest", body, re.IGNORECASE)),
          f"{name} orders its set easiest to hardest")
    check(bool(re.search(r"[Qq]uestion 1 is the plainest", body)),
          f"{name} makes question 1 the plainest form")


# ---------------------------------------------------------------------------
print("\nReading level is given as a number, not a wish")
print("-" * 62)
# "Keep language calibrated to the year level" is unmeasurable and produced a
# Year 5 maths lesson at reading grade 9.9, opening on a 26 word sentence with
# three embedded clauses. A struggling reader cannot reach the maths through
# the prose.
for name in INTRO_WRITERS + ["question_generator_english.txt"]:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(bool(re.search(r"\d+\s*to\s*\d+\s*words a sentence", body)),
          f"{name} names an average sentence length")
    check(bool(re.search(r"no sentence over", body, re.IGNORECASE)),
          f"{name} names a maximum sentence length")


# ---------------------------------------------------------------------------
print("\nWord problems are set in something a child cares about")
print("-" * 62)
maths = (PROMPT_DIR / "question_generator_maths.txt").read_text(encoding="utf-8")
check(bool(re.search(r"household[- ]object", maths)),
      "the maths generator is told to ration household-object contexts")
check(bool(re.search(r"sport|skate|bike|pocket money", maths, re.IGNORECASE)),
      "and given contexts a ten year old would choose")


# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
