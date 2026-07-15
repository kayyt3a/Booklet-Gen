"""Retrieval helper. Degrades gracefully when the library is empty or absent."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    text: str
    source: str
    subject: str
    year_level: str
    distance: float


class Retriever:
    def __init__(self, top_k: int = 3, persist_dir: Optional[str] = None):
        self._top_k = top_k
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
        strict = {"$and": [
            {"subject": subject},
            {"$or": [{"year_level": year_level}, {"year_level": "Any"}]},
        ]}
        for where in (strict, {"subject": subject}):
            try:
                hits = self._store.query(vecs[0], top_k=self._top_k, where=where)
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
