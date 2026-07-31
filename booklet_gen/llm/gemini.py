"""Gemini client with a bounded wall-clock cost per call.

Every call used to be unbounded in two separate ways, and fixing only one of
them fixes nothing:

1. No socket timeout. A request that hung held its worker thread until the
   process restarted, which on the web app is a booklet job stuck at
   "running" until the watchdog gives up on it.
2. The SDK's own retry wrapper. It sleeps and retries inside a single
   generate_content call, so a per-request timeout only ever bounds one
   attempt and the total can still run for minutes. scripts/check_models.py
   documents the same trap. Passing retry=None hands retrying to the loop
   below, which is the only place that can see the whole budget.

So the timeout is per attempt and the deadline is per call. Retries still
happen, but they are ours: rate limits and transient server errors are worth
another go, a bad prompt is not.
"""
from __future__ import annotations

import logging
import os
import re
import time

from ..config import Config
from .base import LLMClient, Tier

log = logging.getLogger(__name__)

_RETRY_DELAY_RE = re.compile(r"retry in ([\d\.]+)s")
_MAX_429_RETRIES = 5

# How long one request may take. Generous, because a batched validation call
# over a whole subtopic is a big prompt, but finite.
DEFAULT_TIMEOUT_S = float(os.environ.get("GEMINI_TIMEOUT_S", "120"))
# Ceiling on one complete() call including every retry and every backoff
# sleep. Below the web app's job watchdog so a stuck call surfaces as a
# failed step, not as a job that never reports.
DEFAULT_DEADLINE_S = float(os.environ.get("GEMINI_MAX_ELAPSED_S", "420"))

# Transient server-side conditions worth retrying. A 400 or a 404 is not:
# retrying a malformed prompt just burns the budget. The status codes are
# word-bounded so that a message mentioning "1500 tokens" is not read as a
# 500, which would retry an error that can only ever fail again.
_TRANSIENT = re.compile(
    r"\b(?:500|502|503|504)\b|internal error|unavailable|deadline\s*exceeded"
    r"|timeout|timed out|connection (?:reset|aborted)",
    re.IGNORECASE,
)


class GeminiClient(LLMClient):
    def __init__(self, config: Config,
                 timeout_s: float = DEFAULT_TIMEOUT_S,
                 deadline_s: float = DEFAULT_DEADLINE_S):
        if not config.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        import google.generativeai as genai
        genai.configure(api_key=config.gemini_api_key)
        self._genai = genai
        self._fast = config.gemini_model_fast
        self._strong = config.gemini_model_strong
        self._timeout_s = max(5.0, float(timeout_s))
        # A deadline shorter than one attempt would make the first call
        # pointless, so it is at least one timeout long.
        self._deadline_s = max(self._timeout_s, float(deadline_s))

    def complete(self, system: str, user: str, tier: Tier = "strong", temperature: float = 0.4) -> str:
        model_name = self._strong if tier == "strong" else self._fast
        model = self._genai.GenerativeModel(model_name, system_instruction=system)
        deadline = time.monotonic() + self._deadline_s
        last_error: Exception | None = None

        for attempt in range(1, _MAX_429_RETRIES + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                response = model.generate_content(
                    user,
                    generation_config={"temperature": temperature},
                    # retry=None: the SDK's backoff loop ignores this timeout,
                    # so retrying is done here where the deadline is visible.
                    request_options={
                        "timeout": min(self._timeout_s, remaining),
                        "retry": None,
                    },
                )
                return (response.text or "").strip()
            except Exception as e:
                last_error = e
                retryable = self._is_rate_limit(e) or self._is_transient(e)
                if not retryable or attempt == _MAX_429_RETRIES:
                    raise
                if self._is_rate_limit(e):
                    wait = self._extract_retry_delay(e) or (2 ** attempt)
                    # Clamp to a sensible ceiling; retry hints can be ~60s.
                    wait = min(max(wait, 1.0), 65.0)
                    event = "gemini.rate_limited"
                else:
                    wait = min(2.0 ** attempt, 30.0)
                    event = "gemini.transient_error"
                # Sleeping past the deadline is the failure mode this exists
                # to prevent, so give up now rather than wake up too late.
                if time.monotonic() + wait >= deadline:
                    log.warning(
                        "gemini.deadline_reached",
                        extra={"model": model_name, "attempt": attempt,
                               "error": str(e)[:200]},
                    )
                    raise
                log.warning(
                    event,
                    extra={"model": model_name, "attempt": attempt,
                           "wait_s": wait, "error": str(e)[:200]},
                )
                time.sleep(wait)

        raise TimeoutError(
            f"gemini call exceeded its {self._deadline_s:.0f}s budget"
            + (f": {str(last_error)[:200]}" if last_error else "")
        )

    @staticmethod
    def _is_rate_limit(e: Exception) -> bool:
        return "429" in str(e) or "ResourceExhausted" in type(e).__name__

    @staticmethod
    def _is_transient(e: Exception) -> bool:
        return bool(_TRANSIENT.search(f"{type(e).__name__} {e}"))

    @staticmethod
    def _extract_retry_delay(e: Exception) -> float | None:
        m = _RETRY_DELAY_RE.search(str(e))
        return float(m.group(1)) if m else None
