#!/usr/bin/env python3
"""Show what is actually in the RAG library, and which programs it can ground.

Inspects whichever backend is configured: Postgres/pgvector when DATABASE_URL
is set, otherwise the local Chroma store.

Usage:
    python scripts/rag_status.py              # summary by subject and year
    python scripts/rag_status.py --sources    # also list every source file
    python scripts/rag_status.py --gaps       # only what is missing

The retriever filters on subject AND year, so material tagged with a subject
the app never asks for is effectively invisible. That is what this reports.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from booklet_gen.dbpool import is_postgres          # noqa: E402
from booklet_gen.rag.store import VectorStore       # noqa: E402

# What each product line asks the retriever for. Keep in step with
# programs.py: these are the subject names the pipeline passes through.
PROGRAM_NEEDS = {
    "Scholarships": ["Reasoning"],
    "NAPLAN Practice": ["Mathematics", "English"],
    "Academic Accelerate": None,      # filled from ACCELERATE_SUBJECTS below
    "Methods Exam": ["Mathematics Methods"],
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", action="store_true", help="List every source file")
    ap.add_argument("--gaps", action="store_true", help="Only show coverage gaps")
    args = ap.parse_args()

    from booklet_gen.programs import ACCELERATE_SUBJECTS
    needs = dict(PROGRAM_NEEDS)
    needs["Academic Accelerate"] = list(ACCELERATE_SUBJECTS)

    backend = "Postgres (pgvector)" if is_postgres() else "local Chroma (rag_store/)"
    store = VectorStore()
    total = store.count()
    print(f"Backend: {backend}")
    print(f"Total chunks: {total}\n")
    if total == 0:
        print("The library is empty. Every booklet will generate without grounding.")
        print("Ingest with: python scripts/ingest_folder.py")
        return 0

    rows = store.stats()

    # subject -> year -> chunks
    by_subject: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_subject_tag: dict[str, set] = defaultdict(set)
    for r in rows:
        by_subject[r["subject"]][r["year_level"]] += r["chunks"]
        by_subject_tag[r["subject"]].add(r["topics"])

    if not args.gaps:
        print("What is in the library")
        print("-" * 62)
        for subject in sorted(by_subject):
            years = by_subject[subject]
            n = sum(years.values())
            tags = ", ".join(sorted(t for t in by_subject_tag[subject] if t != "?"))
            print(f"  {subject}  ({n} chunks)")
            print(f"    years: {', '.join(sorted(years, key=_year_key))}")
            if tags:
                print(f"    tags:  {tags}")
        print()

    if args.sources:
        print("Sources")
        print("-" * 62)
        for r in sorted(rows, key=lambda r: (r["subject"], r["year_level"], r["source"])):
            print(f"  {r['subject']:<22} {r['year_level']:<10} "
                  f"{r['chunks']:>4}  {r['source']}")
        print()

    print("Coverage by product line")
    print("-" * 62)
    have = set(by_subject)
    for program, subjects in needs.items():
        missing = [s for s in subjects if s not in have]
        if missing and not args.gaps:
            status = f"PARTIAL - no {', '.join(missing)}" if len(missing) < len(subjects) \
                else "NOT GROUNDED"
            print(f"  {program:<22} {status}")
        elif missing:
            print(f"  {program:<22} missing: {', '.join(missing)}")
        elif not args.gaps:
            print(f"  {program:<22} grounded")
    print()
    print("Subjects with no material generate from the model's own knowledge,")
    print("which still works but is not calibrated to real papers.")
    return 0


def _year_key(y: str):
    """Sort 'Year 3' before 'Year 12', and put the 'Any' wildcard first."""
    if y == "Any":
        return (-1, "")
    try:
        return (int(y.split()[-1]), "")
    except (ValueError, IndexError):
        return (999, y)


if __name__ == "__main__":
    raise SystemExit(main())
