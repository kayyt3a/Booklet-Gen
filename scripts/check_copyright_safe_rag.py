"""Check that NAPLAN generation uses original guidance, not the old RAG store.

Runs without an API key or database:

    PYTHONPATH=. python scripts/check_copyright_safe_rag.py
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path

from booklet_gen.pipeline import BookletPipeline
from booklet_gen.programs import (
    EXTERNAL_CONTENT_ENV,
    customer_programs,
    get_program,
    program_external_rag_enabled,
)
from booklet_gen.schemas import Subtopic


failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        failures.append(label)


naplan = get_program("naplan")
guide = naplan.authoring_guidance() or ""
guide_words = " ".join(guide.split())

print("\nThe NAPLAN program has a dedicated original-authoring guide")
print("-" * 68)
check(len(guide.split()) > 700, "the guide is substantial")
check("Years 3, 5, 7 and 9" in guide, "the guide names the real NAPLAN years")
for domain in ("reading", "writing", "conventions of language", "numeracy"):
    check(domain in guide.lower(), f"the guide covers {domain}")
check("Never reproduce or closely paraphrase" in guide,
      "released questions and passages cannot be paraphrased")
check("ignore that material completely" in guide,
      "assessment excerpts are explicitly refused")
check("not an official NAPLAN document" in guide_words,
      "the guide does not claim endorsement")
check("\u2014" not in guide and "\u2013" not in guide,
      "the guide contains no em or en dash")

print("\nRestricted NAPLAN vectors are off by default")
print("-" * 68)
check(naplan.use_rag is False, "NAPLAN external RAG is disabled")
check(get_program("accelerate").use_rag is False,
      "Accelerate external RAG is disabled, since it also goes on sale")
# The per-program flag must still mean something. Scholarships is off the
# customer menu and keeps retrieval, so a future reviewed store can serve it.
check(get_program("scholarships").use_rag is True,
      "the program switch does not silently disable every product")

print("\nThe guide reaches generation even while retrieval stays off")
print("-" * 68)
pipe = BookletPipeline.__new__(BookletPipeline)
pipe._n_classwork = 1
captured: list[list[str]] = []


def forbidden_retrieval(*_args, **_kwargs):
    raise AssertionError("restricted retrieval was called")


def fake_teaching(_subject, _year, _topic, _subtopic, chunks):
    captured.append(list(chunks))
    return None


def fake_questions(_subject, _year, _topic, _subtopic, chunks, _teaching,
                   _seen, _passages, passage_quota=0):
    captured.append(list(chunks))
    return []


pipe._retrieve = forbidden_retrieval
pipe._make_teaching = fake_teaching
pipe._generate_and_validate = fake_questions
section, retrieved = pipe._process_subtopic(
    "Mathematics",
    "Year 5",
    "Number",
    Subtopic(name="Fractions", difficulty_hint="medium"),
    passage_quota=0,
    authoring_guidance=guide,
    use_rag=False,
)
check(retrieved == [], "no external chunks are returned")
check(len(captured) == 2 and all(parts == [guide] for parts in captured),
      "lesson and question stages receive only the internal guide")
check(section.topic == "Number" and section.subtopic == "Fractions",
      "generation still builds the requested section")

# The general invariant, not just the NAPLAN one. Production runs clean-room,
# so every product a customer can actually buy generates with no retrieval. A
# product with neither RAG nor a guide has no curriculum grounding at all.
print("\nEvery product on sale is grounded without retrieval")
print("-" * 68)
os.environ.pop(EXTERNAL_CONTENT_ENV, None)
for key, program in customer_programs().items():
    check(not program_external_rag_enabled(program),
          f"{program.label} does not reach external content in production")
    check((program.authoring_guidance() or "").split().__len__() > 500,
          f"{program.label} has a substantial authoring guide instead")

accelerate = get_program("accelerate")
acc_guide = accelerate.authoring_guidance() or ""
for band in ("Years 1 and 2", "Years 3 and 4", "Years 5 and 6",
             "Years 7 and 8", "Years 9 and 10"):
    check(acc_guide.count(band) == 2,
          f"the Accelerate guide covers {band} in both subjects")
for strand in ("Number", "Algebra", "Measurement", "Space", "Statistics",
               "Probability", "Language", "Literature", "Literacy"):
    check(strand in acc_guide, f"the Accelerate guide names the {strand} strand")
check("Never reproduce or closely paraphrase" in acc_guide,
      "the Accelerate guide forbids paraphrasing published resources")
check("workbook" in acc_guide and "textbook" in acc_guide,
      "and names textbooks and workbooks as sources it will not draw on")
check("not an official curriculum document" in " ".join(acc_guide.split()),
      "the Accelerate guide does not claim endorsement")
check("—" not in acc_guide and "–" not in acc_guide,
      "the Accelerate guide contains no em or en dash")

# NAPLAN is sat in Years 3, 5, 7 and 9 only, and each of those years now has a
# supplement narrowing the general guide. A year outside that set must fall
# back to the base guide rather than error.
print("\nEach NAPLAN year gets its own supplement")
print("-" * 68)
base_words = len((naplan.authoring_guidance() or "").split())
for year, marker in (("Year 3", "10 000"), ("Year 5", "thousandths"),
                     ("Year 7", "prime factorisation"), ("Year 9", "Pythagoras")):
    supplemented = naplan.authoring_guidance(year) or ""
    check(f"YEAR {year.split()[1]} SUPPLEMENT" in supplemented,
          f"{year} loads its supplement")
    check(len(supplemented.split()) > base_words + 300,
          f"{year} adds substantial year-specific guidance")
    check(marker in supplemented,
          f"{year} pitches its numeracy at the right level")
for year in ("Year 4", "Year 10"):
    check(len((naplan.authoring_guidance(year) or "").split()) == base_words,
          f"{year} falls back to the base guide, since NAPLAN is not sat then")

# The supplement is worthless if the pipeline drops the year on the way in.
# This is the wiring that would break silently.
run_program_src = inspect.getsource(BookletPipeline.run_program)
check("program.authoring_guidance(year_level)" in run_program_src,
      "run_program passes the year level through to the guide")

readme = Path("rag_sources/README.md").read_text(encoding="utf-8")
check("Do not migrate that library into the paid product" in readme,
      "the source instructions quarantine the old library")
check("Past NAPLAN tests" in readme and "Not approved" in readme,
      "the source instructions no longer recommend past papers")

if failures:
    print(f"\n{len(failures)} FAILED")
    raise SystemExit(1)
print("\nALL COPYRIGHT-SAFE RAG CHECKS PASSED")
