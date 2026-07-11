from __future__ import annotations

from ..config import Config
from .base import LLMClient, Tier


class GeminiClient(LLMClient):
    def __init__(self, config: Config):
        if not config.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        import google.generativeai as genai
        genai.configure(api_key=config.gemini_api_key)
        self._genai = genai
        self._fast = config.gemini_model_fast
        self._strong = config.gemini_model_strong

    def complete(self, system: str, user: str, tier: Tier = "strong", temperature: float = 0.4) -> str:
        model_name = self._strong if tier == "strong" else self._fast
        model = self._genai.GenerativeModel(model_name, system_instruction=system)
        response = model.generate_content(
            user,
            generation_config={"temperature": temperature},
        )
        return (response.text or "").strip()
