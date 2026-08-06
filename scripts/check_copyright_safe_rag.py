"""Check that NAPLAN generation uses original guidance, not the old RAG store.

Runs without an API key or database:

    PYTHONPATH=. python scripts/check_copyright_safe_rag.py
"""
from __future__ import annotations

from pathlib import Path

from booklet_gen.pipeline import BookletPipeline
from booklet_gen.programs import get_program
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
check(get_program("accelerate").use_rag is True,
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

readme = Path("rag_sources/README.md").read_text(encoding="utf-8")
check("Do not migrate that library into the paid product" in readme,
      "the source instructions quarantine the old library")
check("Past NAPLAN tests" in readme and "Not approved" in readme,
      "the source instructions no longer recommend past papers")

if failures:
    print(f"\n{len(failures)} FAILED")
    raise SystemExit(1)
print("\nALL COPYRIGHT-SAFE RAG CHECKS PASSED")
