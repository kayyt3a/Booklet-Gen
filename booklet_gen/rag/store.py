"""Vector store with two backends behind one interface.

* **Postgres + pgvector** when DATABASE_URL is set. The RAG library then lives
  in the same managed database as the accounts, so a deployed instance has
  real curriculum grounding instead of an empty local store.
* **ChromaDB on disk** otherwise, for a local checkout.

Filters are expressed as plain arguments (`subject`, `year_levels`) rather than
Chroma's `where` dialect, so the retriever does not need to know which backend
is in use.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional, Sequence

from ..dbpool import advisory_lock, get_pool, is_postgres

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("rag_store")
COLLECTION = "booklet_gen"

# Distinct from webapp/db.py's schema lock key. Booklet generation runs
# subtopics on a thread pool, and each can be the first to build a Retriever,
# so this schema setup is just as exposed to the same boot-race as db.py's.
_SCHEMA_LOCK_KEY = 72_461_002

# gemini-embedding-001 returns 3072 dimensions by default. Override only if you
# also set output_dimensionality on the embedder.
EMBED_DIM = int(os.environ.get("FOLIO_EMBED_DIM", "3072"))

# pgvector's HNSW and IVFFlat indexes only support up to 2000 dimensions.
# Above that we store the vectors and let Postgres do an exact scan, which is
# correct and fast enough for a library of a few thousand chunks.
_MAX_INDEXABLE_DIM = 2000


class _ChromaStore:
    def __init__(self, persist_dir: Path | str = DEFAULT_DIR):
        import chromadb
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        self._collection = self._client.get_or_create_collection(name=COLLECTION)

    def add_chunks(self, chunks, embeddings, metadatas, source_id: str) -> int:
        assert len(chunks) == len(embeddings) == len(metadatas)
        ids = [f"{source_id}::{i}" for i in range(len(chunks))]
        self._collection.upsert(
            ids=ids, documents=chunks, embeddings=embeddings, metadatas=metadatas,
        )
        return len(chunks)

    def query(self, query_embedding, top_k: int, subject: Optional[str] = None,
              year_levels: Optional[Sequence[str]] = None) -> list[dict]:
        clauses = []
        if subject:
            clauses.append({"subject": subject})
        if year_levels:
            years = list(year_levels)
            clauses.append(
                {"year_level": years[0]} if len(years) == 1
                else {"$or": [{"year_level": y} for y in years]}
            )
        where = None
        if len(clauses) == 1:
            where = clauses[0]
        elif clauses:
            where = {"$and": clauses}

        result = self._collection.query(
            query_embeddings=[query_embedding], n_results=top_k, where=where,
        )
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        dists = result.get("distances", [[]])[0]
        return [{"text": d, "metadata": m or {}, "distance": dist}
                for d, m, dist in zip(docs, metas, dists)]

    def count(self) -> int:
        return self._collection.count()

    def delete_by_source(self, source_id: str) -> None:
        self._collection.delete(where={"source_id": source_id})


def _vec_literal(vec) -> str:
    """Render an embedding as a pgvector literal.

    Coerce every component to a plain float: Chroma hands back numpy scalars,
    and str(list_of_numpy) produces "np.float64(0.13)" which pgvector rejects.
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


class _PgVectorStore:
    """Chunks in Postgres, similarity search via pgvector's cosine operator."""

    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with advisory_lock(_SCHEMA_LOCK_KEY) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS rag_chunks (
                    id         TEXT PRIMARY KEY,
                    source_id  TEXT NOT NULL,
                    source     TEXT,
                    subject    TEXT,
                    year_level TEXT,
                    topics     TEXT,
                    ordinal    INTEGER,
                    text       TEXT NOT NULL,
                    metadata   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding  vector({EMBED_DIM})
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_source_idx"
                " ON rag_chunks (source_id)")
            # Filters run before the similarity scan, so index them.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_filter_idx"
                " ON rag_chunks (subject, year_level)")
            if EMBED_DIM <= _MAX_INDEXABLE_DIM:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx"
                    " ON rag_chunks USING hnsw (embedding vector_cosine_ops)")
            else:
                log.info("rag.no_ann_index", extra={
                    "dim": EMBED_DIM, "limit": _MAX_INDEXABLE_DIM,
                    "reason": "pgvector indexes cap at 2000 dims; using exact scan",
                })

    def add_chunks(self, chunks, embeddings, metadatas, source_id: str) -> int:
        assert len(chunks) == len(embeddings) == len(metadatas)
        rows = []
        for i, (text, vec, meta) in enumerate(zip(chunks, embeddings, metadatas)):
            rows.append((
                f"{source_id}::{i}", source_id, meta.get("source"),
                meta.get("subject"), meta.get("year_level"), meta.get("topics"),
                meta.get("ordinal", i), text, json.dumps(meta), _vec_literal(vec),
            ))
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO rag_chunks
                        (id, source_id, source, subject, year_level, topics,
                         ordinal, text, metadata, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        source_id=EXCLUDED.source_id, source=EXCLUDED.source,
                        subject=EXCLUDED.subject, year_level=EXCLUDED.year_level,
                        topics=EXCLUDED.topics, ordinal=EXCLUDED.ordinal,
                        text=EXCLUDED.text, metadata=EXCLUDED.metadata,
                        embedding=EXCLUDED.embedding
                    """,
                    rows,
                )
        return len(chunks)

    def query(self, query_embedding, top_k: int, subject: Optional[str] = None,
              year_levels: Optional[Sequence[str]] = None) -> list[dict]:
        from psycopg.rows import dict_row
        where, where_params = [], []
        if subject:
            where.append("subject = %s")
            where_params.append(subject)
        if year_levels:
            where.append("year_level = ANY(%s)")
            where_params.append(list(year_levels))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        # The vector placeholder appears in the SELECT list, so it binds before
        # the WHERE parameters; LIMIT binds last.
        params = [_vec_literal(query_embedding), *where_params, top_k]
        with get_pool().connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    SELECT text, metadata,
                           embedding <=> %s::vector AS distance
                    FROM rag_chunks {clause}
                    ORDER BY distance
                    LIMIT %s
                    """,
                    params,
                )
                rows = cur.fetchall()
        return [{"text": r["text"], "metadata": r["metadata"] or {},
                 "distance": float(r["distance"])} for r in rows]

    def count(self) -> int:
        with get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM rag_chunks")
                return int(cur.fetchone()[0])

    def delete_by_source(self, source_id: str) -> None:
        with get_pool().connection() as conn:
            conn.execute("DELETE FROM rag_chunks WHERE source_id = %s", (source_id,))


def VectorStore(persist_dir: Path | str | None = None):
    """Return the vector store for the current configuration.

    Kept callable-like-a-class so existing call sites (`VectorStore()`,
    `VectorStore(path)`) keep working unchanged.
    """
    if is_postgres():
        return _PgVectorStore()
    return _ChromaStore(persist_dir or DEFAULT_DIR)


def source_id_for(path: Path) -> str:
    """Stable id per source file so re-ingesting overwrites the same rows."""
    return hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:16]
