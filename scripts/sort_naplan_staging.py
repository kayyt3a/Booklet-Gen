#!/usr/bin/env python3
"""Sort a flat folder of downloaded NAPLAN PDFs into the RAG folder structure.

Pairs with download_pdfs.py: download everything from an ACARA listing page
into a staging folder, then run this to route each file into
rag_sources/<Subject>/<Year>/NAPLAN/ based on year level and subject
detected in the filename.

Usage:
    python scripts/sort_naplan_staging.py
    python scripts/sort_naplan_staging.py --staging rag_sources/_staging --dry-run

Detection is filename-based (ACARA's own naming, which varies release to
release, usually embeds both). Files where neither/only-one is detected are
left in the staging folder and printed at the end so they can be sorted
manually.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

YEAR_RE = re.compile(r"(?:year|yr|y)[\s_-]?0?([3579])(?!\d)", re.IGNORECASE)
MATH_RE = re.compile(r"numeracy", re.IGNORECASE)
ENGLISH_RE = re.compile(
    r"(reading|language|convention|writing|persuasive|narrative|literacy)",
    re.IGNORECASE,
)


def classify(name: str) -> tuple[str, str] | None:
    """Return (subject, year_folder) or None if undetectable."""
    year_m = YEAR_RE.search(name)
    if not year_m:
        return None
    year = f"Year {year_m.group(1)}"

    is_math = bool(MATH_RE.search(name))
    is_english = bool(ENGLISH_RE.search(name))
    if is_math and not is_english:
        return "Mathematics", year
    if is_english and not is_math:
        return "English", year
    return None  # ambiguous or no subject match — needs a human


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sort staged NAPLAN PDFs into rag_sources/<Subject>/<Year>/NAPLAN/",
    )
    parser.add_argument("--staging", default="rag_sources/_staging", type=Path,
                        help="Folder of flat downloaded PDFs (default: rag_sources/_staging)")
    parser.add_argument("--root", default="rag_sources", type=Path,
                        help="RAG sources root (default: rag_sources)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without moving files")
    args = parser.parse_args()

    if not args.staging.exists():
        print(f"No staging folder at {args.staging} — nothing to sort.", file=sys.stderr)
        return 1

    pdfs = sorted(args.staging.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {args.staging}.", file=sys.stderr)
        return 1

    moved = 0
    unmatched: list[Path] = []
    for pdf in pdfs:
        result = classify(pdf.name)
        if result is None:
            unmatched.append(pdf)
            continue
        subject, year = result
        dest_dir = args.root / subject / year / "NAPLAN"
        dest = dest_dir / pdf.name
        print(f"  {pdf.name}  ->  {subject}/{year}/NAPLAN/")
        if not args.dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pdf), str(dest))
        moved += 1

    print(f"\n{'Would move' if args.dry_run else 'Moved'} {moved} file(s).")
    if unmatched:
        print(f"\n{len(unmatched)} file(s) could not be auto-classified "
              f"(left in {args.staging}):")
        for pdf in unmatched:
            print(f"  ? {pdf.name}")
        print(
            "\nMove these manually into rag_sources/<Subject>/<Year>/<Tag>/ — "
            "the filename didn't clearly contain both a year level (y3/yr5/year7...) "
            "and a subject (numeracy/reading/language/writing...)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
