"""Retrieval helper. Degrades gracefully when the library is empty or absent."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Chunks of source material fed to the generator per subtopic. Chunks target
# 800 characters, so 6 is roughly 5k characters of grounding: enough to
# calibrate style and difficulty against real papers without crowding the
# prompt. Raise it when the library is deep for a subject, lower it if the
# retrieved material starts drowning out the instructions.
_FALLBACK_TOP_K = 6


def default_top_k() -> int:
    """Read at call time rather than import time: at import this can run
    before .env is loaded, silently ignoring FOLIO_RAG_TOP_K."""
    try:
        return max(1, int(os.environ.get("FOLIO_RAG_TOP_K", _FALLBACK_TOP_K)))
    except ValueError:
        return _FALLBACK_TOP_K


@dataclass
class RetrievedChunk:
    text: str
    source: str
    subject: str
    year_level: str
    distance: float


class Retriever:
    def __init__(self, top_k: Optional[int] = None, persist_dir: Optional[str] = None):
        self._top_k = default_top_k() if top_k is None else top_k
        self._persist_dir = persist_dir
        self._store = None
        self._embedder = None

    def _ensure(self) -> bool:
        """Lazy-init. Returns False if the library is unavailable or empty."""
        if self._store is not None:
            return True
        try:
            from .store import VectorStore, DEFAULT_DIR
            from .embeddings import get_embedder
            path = self._persist_dir or DEFAULT_DIR
            self._store = VectorStore(path)
            if self._store.count() == 0:
                log.info("rag.empty", extra={"path": str(path)})
                self._store = None
                return False
            self._embedder = get_embedder()
            return True
        except Exception as e:
            log.info("rag.unavailable", extra={"reason": str(e)[:200]})
            self._store = None
            return False

    def retrieve(self, subject: str, year_level: str, topic: str, subtopic: str) -> list[RetrievedChunk]:
        if not self._ensure():
            return []
        query = f"{subject} {year_level} {topic} {subtopic}"
        try:
            vecs = self._embedder.embed([query], task_type="retrieval_query")
        except Exception as e:
            log.warning("rag.embed_query_failed", extra={"error": str(e)[:200]})
            return []

        # Strict filter: subject AND (year matches OR year is the wildcard "Any",
        # used for cross-year curriculum docs like the SCSA scope-and-sequence).
        # Fallback: subject-only.
        attempts = (
            {"subject": subject, "year_levels": [year_level, "Any"]},
            {"subject": subject},
        )
        for filters in attempts:
            try:
                hits = self._store.query(vecs[0], top_k=self._top_k, **filters)
            except Exception as e:
                log.warning("rag.query_failed", extra={"error": str(e)[:200]})
                return []
            if hits:
                return [
                    RetrievedChunk(
                        text=h["text"],
                        source=h["metadata"].get("source", "unknown"),
                        subject=h["metadata"].get("subject", ""),
                        year_level=h["metadata"].get("year_level", ""),
                        distance=h.get("distance", 0.0),
                    )
                    for h in hits
                ]
        return []
