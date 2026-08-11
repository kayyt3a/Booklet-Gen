from __future__ import annotations

import logging
from pydantic import ValidationError

from ..llm import LLMClient
from ..schemas import Outline
from ._shared import load_prompt, extract_json

log = logging.getLogger(__name__)


class OutlineParserAgent:
    def __init__(self, client: LLMClient, max_retries: int = 3,
                 min_topics: int = 1):
        self._client = client
        self._max_retries = max_retries
        self._min_topics = max(1, min_topics)
        self._system = load_prompt("outline_parser.txt")

    def parse(self, description: str,
              authoring_guidance: str | None = None) -> Outline:
        """Turn a description into an outline.

        `authoring_guidance` is the product line's own guide. This agent picks
        which skills the booklet covers and stamps each with a difficulty
        hint, so it is the agent that actually decides the level of the
        booklet, and it used to be the only one that never read the guide.
        Everything downstream inherits what is chosen here: a subtopic named
        "simplifying fractions" cannot be rescued into demanding work by any
        later prompt.
        """
        error_feedback = ""
        guide = ""
        if authoring_guidance:
            guide = ("\n\nAuthoring instructions for this product line. Follow "
                     "them as rules when choosing the topics, the subtopics and "
                     "each difficulty_hint:\n\n" + authoring_guidance.strip())
        for attempt in range(1, self._max_retries + 1):
            base = description + guide
            user = base if not error_feedback else (
                f"{base}\n\nYour previous attempt failed validation:\n{error_feedback}\n"
                "Return a corrected JSON object."
            )
            log.info("outline_parser.attempt", extra={"attempt": attempt})
            raw = self._client.complete(self._system, user, tier="fast", temperature=0.2)
            try:
                data = extract_json(raw)
                outline = Outline.model_validate(data)
                # The topic floor is part of what a credit buys, so it is
                # enforced here rather than asked for in the prompt and hoped
                # for. Fed back as a validation error, which is the channel
                # the retry loop already understands.
                if len(outline.topics) < self._min_topics:
                    raise ValueError(
                        f"the outline has {len(outline.topics)} top-level "
                        f"topics and every booklet must cover at least "
                        f"{self._min_topics}. Add whole topics a full unit at "
                        f"this year level would include, not more subtopics "
                        f"under the ones you already have.")
                log.info("outline_parser.success", extra={"attempt": attempt})
                return outline
            except (ValueError, ValidationError) as e:
                error_feedback = str(e)
                log.warning("outline_parser.retry", extra={"attempt": attempt, "error": error_feedback[:300]})
        raise RuntimeError(f"Outline parser failed after {self._max_retries} attempts: {error_feedback}")
