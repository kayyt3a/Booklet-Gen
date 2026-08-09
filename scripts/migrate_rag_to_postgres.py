#!/usr/bin/env python3
"""Copy a local Chroma RAG store into Postgres/pgvector.

Moves the existing embeddings across as-is, so nothing is re-embedded and the
migration costs no Gemini quota.

Usage (PowerShell):
    $env:DATABASE_URL="postgresql://user:pass@host/dbname"
    .venv\\Scripts\\python scripts\\migrate_rag_to_postgres.py --dry-run
    .venv\\Scripts\\python scripts\\migrate_rag_to_postgres.py

Re-running is safe: rows are keyed by source id and chunk ordinal, so a second
run overwrites rather than duplicating. Migration refuses a store containing
chunks without approved rights metadata.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from booklet_gen.dbpool import is_postgres                      # noqa: E402
from booklet_gen.logging_setup import configure_logging          # noqa: E402
from booklet_gen.rag.store import (                              # noqa: E402
    DEFAULT_DIR, EMBED_DIM, _ChromaStore, _PgVectorStore,
)

BATCH = 200


def _rights_audit(metadatas: list[dict]) -> list[str]:
    """Return failures for vector sources that lack approval provenance."""
    failures: list[str] = []
    by_source: dict[str, list[dict]] = {}
    for metadata in metadatas:
        item = metadata or {}
        key = item.get("source_id") or item.get("source") or "unknown"
        by_source.setdefault(str(key), []).append(item)
    for source_id, rows in sorted(by_source.items()):
        first = rows[0]
        name = first.get("source") or source_id
        decisions = {str(row.get("rights_decision") or "").casefold()
                     for row in rows}
        rights_ids = {str(row.get("rights_source_id") or "").strip()
                      for row in rows}
        review_dates = {str(row.get("rights_review_date") or "").strip()
                        for row in rows}
        if decisions != {"approved"}:
            failures.append(f"{name}: rights_decision is missing or not approved")
        if "" in rights_ids or len(rights_ids) != 1:
            failures.append(f"{name}: rights_source_id is missing or inconsistent")
        if "" in review_dates or len(review_dates) != 1:
            failures.append(f"{name}: rights_review_date is missing or inconsistent")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-dir", default=str(DEFAULT_DIR),
                    help=f"Chroma store directory (default: {DEFAULT_DIR})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would move without writing")
    args = ap.parse_args()

    configure_logging()

    if not is_postgres():
        print("DATABASE_URL is not set, so there is no Postgres to migrate into.",
              file=sys.stderr)
        return 2

    src_dir = Path(args.from_dir)
    if not src_dir.exists():
        print(f"No Chroma store at {src_dir}. Nothing to migrate.", file=sys.stderr)
        return 1

    src = _ChromaStore(src_dir)
    total = src.count()
    print(f"Source Chroma store: {src_dir}  ({total} chunks)")
    if total == 0:
        print("Empty store, nothing to do.")
        return 0

    # Pull everything, including the stored vectors.
    raw = src._collection.get(include=["documents", "metadatas", "embeddings"])
    docs = raw.get("documents") or []
    metas = raw.get("metadatas") or []
    embs = raw.get("embeddings")
    embs = [] if embs is None else list(embs)

    if not embs:
        print("Chroma returned no embeddings; cannot migrate without re-embedding.",
              file=sys.stderr)
        return 1

    rights_failures = _rights_audit(metas)
    if rights_failures:
        print("RIGHTS AUDIT FAILED. Migration is blocked:", file=sys.stderr)
        for failure in rights_failures:
            print(f"  - {failure}", file=sys.stderr)
        print("Rebuild the local store using scripts/ingest_folder.py and the "
              "approved source-rights register.", file=sys.stderr)
        return 3

    dim = len(embs[0])
    if dim != EMBED_DIM:
        print(f"Embedding dimension mismatch: store has {dim}, "
              f"FOLIO_EMBED_DIM is {EMBED_DIM}.", file=sys.stderr)
        print(f"Set FOLIO_EMBED_DIM={dim} (also in your host's environment) "
              "and re-run.", file=sys.stderr)
        return 1

    # Group by source so re-ingest semantics (delete-then-insert per source)
    # carry over unchanged.
    by_source: dict[str, list[int]] = {}
    for i, m in enumerate(metas):
        by_source.setdefault((m or {}).get("source_id") or "unknown", []).append(i)

    print(f"{len(by_source)} distinct sources, {dim}-dimension vectors")
    for sid, idxs in sorted(by_source.items()):
        name = (metas[idxs[0]] or {}).get("source", "?")
        print(f"  {sid}  {name}  ({len(idxs)} chunks)")

    if args.dry_run:
        print("\nDry run: nothing written.")
        return 0

    dst = _PgVectorStore()
    moved = 0
    for sid, idxs in by_source.items():
        dst.delete_by_source(sid)
        for start in range(0, len(idxs), BATCH):
            window = idxs[start:start + BATCH]
            dst.add_chunks(
                [docs[i] for i in window],
                [embs[i] for i in window],
                [metas[i] or {} for i in window],
                source_id=sid,
            )
            moved += len(window)
            print(f"  ... {moved}/{total}", end="\r", flush=True)

    print(f"\nMigrated {moved} chunks. Postgres now holds {dst.count()} chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
