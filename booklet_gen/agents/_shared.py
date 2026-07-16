import json
import re
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Appended to every system prompt. Em dashes read as an AI tell and look less
# professional in a printed booklet; commas, colons, or separate sentences are
# cleaner. The formatter also strips any that slip through, but instructing the
# model keeps the phrasing natural rather than relying on mechanical swaps.
_GLOBAL_STYLE = (
    "\n\nWRITING STYLE (applies to all text you produce): Never use em dashes "
    "(—) or en dashes (–). Use commas, colons, brackets, or separate sentences "
    "instead. Use a plain hyphen only inside genuinely hyphenated words."
)


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8") + _GLOBAL_STYLE


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from an LLM response.

    Handles models that occasionally wrap JSON in code fences or add stray
    leading/trailing prose despite instructions.
    """
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON object found in response: {text[:200]!r}")
    return json.loads(cleaned[start : end + 1])
