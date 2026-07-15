from __future__ import annotations

import logging
import re
import time

from ..config import Config
from .base import LLMClient, Tier

log = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retry in ([\d\.]+)s")
_MAX_429_RETRIES = 5


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
        for attempt in range(1, _MAX_429_RETRIES + 1):
            try:
                response = model.generate_content(
                    user,
                    generation_config={"temperature": temperature},
                )
                return (response.text or "").strip()
            except Exception as e:
                if not self._is_rate_limit(e) or attempt == _MAX_429_RETRIES:
                    raise
                wait = self._extract_retry_delay(e) or (2 ** attempt)
                # Clamp to a sensible ceiling; free-tier retry hints can be up to ~60s.
                wait = min(max(wait, 1.0), 65.0)
                log.warning(
                    "gemini.rate_limited",
                    extra={"model": model_name, "attempt": attempt, "wait_s": wait},
                )
                time.sleep(wait)
        raise RuntimeError("unreachable")  # pragma: no cover

    @staticmethod
    def _is_rate_limit(e: Exception) -> bool:
        return "429" in str(e) or "ResourceExhausted" in type(e).__name__

    @staticmethod
    def _extract_retry_delay(e: Exception) -> float | None:
        m = _RETRY_DELAY_RE.search(str(e))
        return float(m.group(1)) if m else None
