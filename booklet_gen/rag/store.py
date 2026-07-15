"""ChromaDB wrapper. Persistent, local, gitignored."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

DEFAULT_DIR = Path("rag_store")
COLLECTION = "booklet_gen"


class VectorStore:
    def __init__(self, persist_dir: Path | str = DEFAULT_DIR):
        import chromadb
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        self._collection = self._client.get_or_create_collection(name=COLLECTION)

    def add_chunks(
        self,
        chunks: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
        source_id: str,
    ) -> int:
        assert len(chunks) == len(embeddings) == len(metadatas)
        ids = [f"{source_id}::{i}" for i in range(len(chunks))]
        self._collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return len(chunks)

    def query(
        self,
        query_embedding: list[float],
        top_k: int,
        where: Optional[dict] = None,
    ) -> list[dict]:
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {"text": d, "metadata": m or {}, "distance": dist}
            for d, m, dist in zip(docs, metas, distances)
        ]

    def count(self) -> int:
        return self._collection.count()

    def delete_by_source(self, source_id: str) -> None:
        self._collection.delete(where={"source_id": source_id})


def source_id_for(path: Path) -> str:
    """Stable id per source file so re-ingesting overwrites the same rows."""
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
