from __future__ import annotations

from ..config import Config
from .base import LLMClient, Tier


class ClaudeClient(LLMClient):
    def __init__(self, config: Config):
        if not config.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._fast = config.claude_model_fast
        self._strong = config.claude_model_strong

    def complete(self, system: str, user: str, tier: Tier = "strong", temperature: float = 0.4) -> str:
        model_name = self._strong if tier == "strong" else self._fast
        message = self._client.messages.create(
            model=model_name,
            max_tokens=4096,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = []
        for block in message.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip()
