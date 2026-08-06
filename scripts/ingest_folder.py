#!/usr/bin/env python3
"""Ingest rights-approved PDFs under `rag_sources/` into the RAG store.

Expected layout:

    rag_sources/
      <Subject>/
        <Year>/
          <TopicTag>/
            some-source.pdf

Every PDF gets subject, year, and topic metadata derived from its path. It is
ingested only when the same relative path has an approved record in
`source_rights.csv` and all required reuse permissions are affirmative.

The special year folder names "All Years", "All", "Any", "P-10", and "K-10"
become the wildcard year "Any" in the store.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from booklet_gen.logging_setup import configure_logging  # noqa: E402
from booklet_gen.rag.ingest import ingest  # noqa: E402
from booklet_gen.rag.rights import (  # noqa: E402
    RightsRecord,
    RightsRegister,
    RightsRegisterError,
)
from booklet_gen.rag.store import VectorStore  # noqa: E402


def _iter_pdfs(root: Path):
    for path in sorted(root.rglob("*.pdf")):
        yield path


def _meta_from_path(pdf: Path, root: Path) -> tuple[str, str, list[str]] | None:
    """Return (subject, year, topics) inferred from the folder tree."""
    try:
        relative = pdf.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
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
        description="Ingest rights-approved PDFs using folder-based metadata",
    )
    parser.add_argument("--root", default="rag_sources",
                        help="Root folder to walk (default: rag_sources)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the rights-audited plan without ingesting")
    parser.add_argument(
        "--rights-register",
        default=None,
        help=("CSV rights register. Defaults to source_rights.csv inside "
              "the selected root."),
    )
    args = parser.parse_args()

    configure_logging()
    root = Path(args.root)
    if not root.exists():
        print(f"No {root} directory. Create it and place PDFs in "
              f"{root}/<Subject>/<Year>/<TopicTag>/", file=sys.stderr)
        return 1

    register_path = Path(args.rights_register) if args.rights_register \
        else root / "source_rights.csv"
    try:
        rights = RightsRegister.load(register_path)
    except RightsRegisterError as exc:
        print(f"RIGHTS CHECK FAILED: {exc}", file=sys.stderr)
        return 2

    plan: list[tuple[Path, str, str, list[str], RightsRecord]] = []
    blocked: list[tuple[Path, str]] = []
    loose: list[Path] = []
    for pdf in _iter_pdfs(root):
        meta = _meta_from_path(pdf, root)
        if meta is None:
            loose.append(pdf)
            continue
        relative_path = pdf.relative_to(root).as_posix()
        record, reason = rights.approved_record(relative_path)
        if record is None or not record.approved:
            blocked.append((pdf, reason))
            continue
        plan.append((pdf, *meta, record))

    if not plan and not loose and not blocked:
        print(f"No PDFs found under {root}/", file=sys.stderr)
        return 1

    print(f"Rights register: {register_path} ({len(rights)} records)")
    print(f"Approved for ingestion: {len(plan)} PDF(s)")
    for pdf, subject, year, topics, record in plan:
        print(f"  APPROVED {subject:<14} {year:<8} "
              f"[{','.join(topics)}]  {pdf.name}  ({record.source_id})")
    for pdf, reason in blocked:
        print(f"  BLOCKED  {pdf.relative_to(root)}: {reason}")
    for pdf in loose:
        print(f"  LOOSE    {pdf.relative_to(root)}: move under "
              "<Subject>/<Year>/<TopicTag>/ and register it")

    if args.dry_run:
        return 0 if plan else 2
    if not plan:
        print("No rights-approved PDFs to ingest.", file=sys.stderr)
        return 2

    failures = 0
    for pdf, subject, year, topics, record in plan:
        try:
            added = ingest(
                pdf,
                subject,
                year,
                topics,
                source_name=pdf.name,
                rights_metadata=record.vector_metadata(),
            )
            print(f"  + {pdf.name}: {added} chunks")
        except Exception as exc:
            failures += 1
            print(f"  ! {pdf.name}: FAILED: {exc}", file=sys.stderr)

    total = VectorStore().count()
    print(f"\nStore now contains {total} chunks total.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
