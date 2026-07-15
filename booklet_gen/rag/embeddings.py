"""Embedding backends. Gemini today; swap in a local model later if needed."""
from __future__ import annotations

from typing import Literal, Sequence

from ..config import Config, load_config


TaskType = Literal["retrieval_document", "retrieval_query"]


class GeminiEmbedder:
    """Gemini text-embedding-004. Free tier is generous enough for a
    personal library that grows over time."""

    MODEL = "models/gemini-embedding-001"

    def __init__(self, config: Config | None = None):
        cfg = config or load_config()
        if not cfg.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set (needed for embeddings)")
        import google.generativeai as genai
        genai.configure(api_key=cfg.gemini_api_key)
        self._genai = genai

    def embed(self, texts: Sequence[str], task_type: TaskType = "retrieval_document") -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            resp = self._genai.embed_content(
                model=self.MODEL,
                content=text,
                task_type=task_type.upper(),
            )
            vectors.append(list(resp["embedding"]))
        return vectors


def get_embedder(config: Config | None = None):
    """Returns whichever embedder makes sense for the configured provider.

    Today: always Gemini (works fine even when LLM_PROVIDER=claude, as long as
    a GEMINI_API_KEY is present — embeddings and generation are decoupled).
    """
    return GeminiEmbedder(config)
