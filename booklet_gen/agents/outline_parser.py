from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import Outline
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


class OutlineParserAgent:
    def __init__(self, client: LLMClient, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries
        self._system = load_prompt("outline_parser.txt")

    def parse(self, description: str) -> Outline:
        error_feedback = ""
        for attempt in range(1, self._max_retries + 1):
            user = description if not error_feedback else (
                f"{description}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object."
            )
            log.info("outline_parser.attempt", extra={"attempt": attempt})
            raw = self._client.complete(self._system, user, tier="fast", temperature=0.2)
            try:
                data = extract_json(raw)
                outline = Outline.model_validate(data)
                log.info("outline_parser.success", extra={"attempt": attempt})
                return outline
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning("outline_parser.retry", extra={"attempt": attempt, "error": error_feedback[:300]})
        raise RuntimeError(f"Outline parser failed after {self._max_retries} attempts: {error_feedback}")
