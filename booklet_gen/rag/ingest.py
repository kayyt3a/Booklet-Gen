"""Ingest CLI: add a source file to the RAG library.

Usage:
    python -m booklet_gen.rag.ingest path/to/file.pdf \\
        --subject Mathematics --year "Year 6" --topics "Fractions,Decimals"
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from ..logging_setup import configure_logging
from .chunker import chunk_text
from .embeddings import get_embedder
from .pdf import read_text
from .store import VectorStore, source_id_for

log = logging.getLogger(__name__)


def ingest(
    path: Path,
    subject: str,
    year_level: str,
    topics: list[str],
    source_name: str | None = None,
) -> int:
    text = read_text(path)
    chunks = chunk_text(text)
    if not chunks:
        raise RuntimeError(f"No chunks produced from {path}")

    source_id = source_id_for(path)
    display_source = source_name or path.name

    metadatas = [
        {
            "source_id": source_id,
            "source": display_source,
            "subject": subject,
            "year_level": year_level,
            "topics": ",".join(topics),
            "ordinal": c.ordinal,
        }
        for c in chunks
    ]

    log.info(
        "ingest.embed",
        extra={"source": display_source, "chunks": len(chunks)},
    )
    embedder = get_embedder()
    vectors = embedder.embed([c.text for c in chunks], task_type="retrieval_document")

    store = VectorStore()
    # Overwrite any prior chunks from this source so re-ingest is idempotent.
    store.delete_by_source(source_id)
    added = store.add_chunks(
        [c.text for c in chunks], vectors, metadatas, source_id=source_id,
    )
    log.info(
        "ingest.done",
        extra={"source": display_source, "added": added, "total_in_store": store.count()},
    )
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a source into the RAG library")
    parser.add_argument("path", type=Path)
    parser.add_argument("--subject", required=True,
                        help="e.g. Mathematics, Science, English")
    parser.add_argument("--year", dest="year_level", required=True,
                        help="e.g. 'Year 6'")
    parser.add_argument("--topics", default="",
                        help="Comma-separated topic tags, e.g. 'Fractions,Decimals'")
    parser.add_argument("--source-name", default=None,
                        help="Override the display name (defaults to file name)")
    args = parser.parse_args()

    configure_logging()
    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    added = ingest(args.path, args.subject, args.year_level, topics, args.source_name)
    print(f"Ingested {added} chunks from {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
