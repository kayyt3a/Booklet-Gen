from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Literal

from ..config import Config, load_config

Tier = Literal["fast", "strong"]


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, tier: Tier = "strong", temperature: float = 0.4) -> str:
        ...


def get_client(config: Config | None = None) -> LLMClient:
    cfg = config or load_config()
    if cfg.provider == "gemini":
        from .gemini import GeminiClient
        return GeminiClient(cfg)
    if cfg.provider == "claude":
        from .claude import ClaudeClient
        return ClaudeClient(cfg)
    raise ValueError(f"Unknown LLM_PROVIDER: {cfg.provider!r}")
