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

# Also appended to every system prompt. These are rules about the people and
# places that appear in a booklet, and every agent that writes text can put a
# person in it: a question, a mini-lesson, a reading passage, a challenge. The
# booklet is sold to Australian families and read by one child at a time, so
# getting this wrong is not a style problem, it is the kind of thing a parent
# photographs and sends to someone.
#
# None of it was written down anywhere before. The prompts asked for Australian
# settings and named people and said nothing about which names, which meant the
# model's untuned default, and nothing about real institutions, which is how a
# hand-written sample ended up running an invented news story about a real Perth
# primary school with invented staff and students named in it.
_GLOBAL_PEOPLE = (
    "\n\nPEOPLE AND PLACES (applies to every name, character and setting you "
    "write):\n"
    "- Invent institutions. Never name a real school, business, hospital, club "
    "or organisation, and never put words in the mouth of a real living "
    "person. Real suburbs, towns, rivers, landmarks and public geography are "
    "fine and welcome; 'Karrinyup Primary School' is not, 'a primary school in "
    "Perth' is.\n"
    "- Draw names from the mix of an actual Australian classroom, across the "
    "whole set rather than one token name: Anglo, but also Mandarin, "
    "Vietnamese, Arabic, Punjabi, Filipino, Greek, Italian, and Aboriginal "
    "English naming traditions. Spell them correctly and do not explain or "
    "remark on them.\n"
    "- Balance genders across the set, and balance what they DO. Do not let "
    "one gender hold all the acting, building, calculating and arguing while "
    "the other only eats, waits or is asked about. If a character makes the "
    "mistake the student has to diagnose, vary who that is.\n"
    "- Write about Aboriginal and Torres Strait Islander peoples in the "
    "present tense. They are living peoples with continuing cultures, not a "
    "historical subject. Name the specific nation or language group where you "
    "know it. Never present cultural practice only as something that happened "
    "in the past, and never list peoples alongside animals in one group (for "
    "example as 'living things that depend on' something). If you are not "
    "confident of a cultural detail, leave it out rather than guess.\n"
    "- Keep contexts affordable. Assume nothing about a family's money: no "
    "overseas holidays, ski trips, private coaching, restaurants, brand names "
    "or devices. A bus fare is about the most anyone should have to spend."
)


def load_prompt(name: str) -> str:
    return ((PROMPT_DIR / name).read_text(encoding="utf-8")
            + _GLOBAL_STYLE + _GLOBAL_PEOPLE)


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
