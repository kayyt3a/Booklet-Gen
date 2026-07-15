#!/usr/bin/env python3
"""Walk `rag_sources/` and ingest every PDF, deriving metadata from the folder tree.

Expected layout:

    rag_sources/
      <Subject>/                e.g. Mathematics, English, Science
        <Year>/                 e.g. "Year 5", or "All Years" (wildcard)
          <TopicTag>/           e.g. NAPLAN, SCSA, Cambridge, Textbook
            some-source.pdf
            another.pdf

Every PDF gets tagged with subject=<Subject>, year=<Year>, and one topic tag
= <TopicTag>. Drop a new PDF anywhere in the tree and re-run this script.

The special year folder name "All Years" (case-insensitive) becomes the
wildcard year "Any" in the store — the retriever matches "Any" chunks
against every year-level query. Use this for cross-year curriculum documents
like SCSA scope-and-sequence PDFs that cover multiple year levels in one
file.

Files sitting loose at the top level of rag_sources/ are ingested with
minimal metadata and logged as a warning — prefer the structured layout.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root is one dir up from scripts/
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from booklet_gen.logging_setup import configure_logging  # noqa: E402
from booklet_gen.rag.ingest import ingest  # noqa: E402
from booklet_gen.rag.store import VectorStore  # noqa: E402


def _iter_pdfs(root: Path):
    for p in sorted(root.rglob("*.pdf")):
        yield p


def _meta_from_path(pdf: Path, root: Path) -> tuple[str, str, list[str]] | None:
    """Return (subject, year, topics) inferred from the folder tree, or None."""
    try:
        rel = pdf.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    # Expect: <Subject>/<Year>/<TopicTag>/file.pdf  (parts len >= 4)
    # Also accept: <Subject>/<Year>/file.pdf (len 3), with topic derived from filename stem
    if len(parts) >= 4:
        subject, year, topic_tag = parts[0], parts[1], parts[2]
        return subject, _normalise_year(year), [topic_tag]
    if len(parts) == 3:
        subject, year = parts[0], parts[1]
        return subject, _normalise_year(year), [pdf.stem]
    return None


def _normalise_year(year: str) -> str:
    """Map wildcard-style folder names to the store's "Any" tag."""
    if year.strip().lower() in {"all years", "all", "any", "p-10", "k-10"}:
        return "Any"
    return year


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest every PDF under rag_sources/ using folder-based metadata",
    )
    parser.add_argument("--root", default="rag_sources",
                        help="Root folder to walk (default: rag_sources)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan without ingesting")
    args = parser.parse_args()

    configure_logging()
    root = Path(args.root)
    if not root.exists():
        print(f"No {root} directory. Create it and drop PDFs in "
              f"{root}/<Subject>/<Year>/<TopicTag>/", file=sys.stderr)
        return 1

    plan: list[tuple[Path, str, str, list[str]]] = []
    loose: list[Path] = []
    for pdf in _iter_pdfs(root):
        meta = _meta_from_path(pdf, root)
        if meta is None:
            loose.append(pdf)
            continue
        plan.append((pdf, *meta))

    if not plan and not loose:
        print(f"No PDFs found under {root}/", file=sys.stderr)
        return 1

    print(f"Found {len(plan)} structured PDFs" + (
        f" and {len(loose)} loose PDFs" if loose else ""))
    for pdf, subject, year, topics in plan:
        print(f"  {subject:<14} {year:<8} [{','.join(topics)}]  {pdf.name}")
    for pdf in loose:
        print(f"  LOOSE (skipping): {pdf.relative_to(root)}  "
              "-- move under <Subject>/<Year>/<TopicTag>/ to ingest")

    if args.dry_run:
        return 0

    for pdf, subject, year, topics in plan:
        try:
            added = ingest(pdf, subject, year, topics, source_name=pdf.name)
            print(f"  + {pdf.name}: {added} chunks")
        except Exception as e:
            print(f"  ! {pdf.name}: FAILED — {e}", file=sys.stderr)

    total = VectorStore().count()
    print(f"\nStore now contains {total} chunks total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
