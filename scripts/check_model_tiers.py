"""Checks that the model tiers resolve to the models they name.

The tier system had two levels, so choosing a better model was all or nothing.
Moving the strong tier to a Pro model upgraded six agents at once and made
booklets slow enough that the progress page called every healthy job "slower
than most", so it was reverted and the product went back to flash-lite
EVERYWHERE, including the answer validator.

That is the wrong place to economise. The validator is the tick a parent reads
before handing the page to their child, it is the one step whose output nobody
can check by eye, and it is already batched at one call per subtopic rather
than one per question. So there is now a third tier, "exact", used by the
validator alone. It falls back to the strong model, so leaving it unset changes
nothing at all; setting GEMINI_MODEL_EXACT buys a better grader without slowing
down teaching, question writing, the challenge or the term planner.

THE TRAP THIS FILE EXISTS FOR. Both clients used to pick a model like this:

    model_name = self._strong if tier == "strong" else self._fast

Every tier that is not exactly "strong" resolves to the CHEAPEST model. Adding
"exact" under that rule would have silently pointed the validator at flash-lite
and the only symptom would have been worse booklets, months later, with no
error anywhere. Both clients now look the tier up and raise on anything they do
not know, and that is asserted below in both directions.

    PYTHONPATH=. python scripts/check_model_tiers.py
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSED = 0
TOTAL = 0
ROOT = Path(__file__).resolve().parent.parent


def check(condition: bool, claim: str, consequence: str = "") -> bool:
    global PASSED, TOTAL
    TOTAL += 1
    if condition:
        PASSED += 1
        print(f"  ok            {claim}")
    else:
        print(f"  *** FAIL ***  {claim}")
        if consequence:
            print(f"                {consequence}")
    return bool(condition)


def config_with(**environ):
    """Load a fresh Config under exactly these model settings."""
    keys = ("GEMINI_MODEL_FAST", "GEMINI_MODEL_STRONG", "GEMINI_MODEL_EXACT",
            "CLAUDE_MODEL_FAST", "CLAUDE_MODEL_STRONG", "CLAUDE_MODEL_EXACT")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(environ)
        from booklet_gen import config as config_module
        return config_module.load_config()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


print("\n== the exact tier costs nothing until it is asked for ==")

cfg = config_with(GEMINI_MODEL_FAST="a-fast", GEMINI_MODEL_STRONG="a-strong")
check(cfg.gemini_model_exact == "a-strong",
      "unset, the exact tier is the strong model",
      f"got {cfg.gemini_model_exact!r}. Adding a tier must not change what any "
      "existing deployment runs, or this lands as a surprise on a live product")
check(cfg.claude_model_exact == cfg.claude_model_strong,
      "and the same on the Claude side",
      "the two providers disagree about what an unset tier means")

cfg = config_with(GEMINI_MODEL_FAST="a-fast", GEMINI_MODEL_STRONG="a-strong",
                  GEMINI_MODEL_EXACT="a-pro")
check(cfg.gemini_model_exact == "a-pro" and cfg.gemini_model_strong == "a-strong",
      "set, it applies without dragging the strong tier with it",
      f"exact={cfg.gemini_model_exact!r} strong={cfg.gemini_model_strong!r}: "
      "the whole point is upgrading one step, not six")


print("\n== every tier reaches the model it names ==")

from booklet_gen.llm.gemini import GeminiClient                   # noqa: E402
from booklet_gen.llm.claude import ClaudeClient                   # noqa: E402


class FakeConfig:
    gemini_api_key = "fake"
    anthropic_api_key = "fake"
    gemini_model_fast = "g-fast"
    gemini_model_strong = "g-strong"
    gemini_model_exact = "g-exact"
    claude_model_fast = "c-fast"
    claude_model_strong = "c-strong"
    claude_model_exact = "c-exact"
    max_retries = 3


# Neither client is constructed for real: one needs the Google SDK configured
# and the other the Anthropic package. Only the mapping is under test.
gemini = GeminiClient.__new__(GeminiClient)
gemini._models = {"fast": "g-fast", "strong": "g-strong", "exact": "g-exact"}
claude = ClaudeClient.__new__(ClaudeClient)
claude._models = {"fast": "c-fast", "strong": "c-strong", "exact": "c-exact"}

for client, prefix, label in ((gemini, "g", "Gemini"), (claude, "c", "Claude")):
    for tier in ("fast", "strong", "exact"):
        check(client._models.get(tier) == f"{prefix}-{tier}",
              f"{label}: {tier} resolves to the {tier} model",
              f"got {client._models.get(tier)!r}")


print("\n== an unrecognised tier fails loudly, not cheaply ==")

# The defect this file is named after. A conditional sent anything it did not
# recognise to the cheapest model, so a typo in a tier name would downgrade the
# work rather than break the build.
for name, source in (("gemini", inspect.getsource(GeminiClient.complete)),
                     ("claude", inspect.getsource(ClaudeClient.complete))):
    check('if tier == "strong" else' not in source,
          f"{name} does not pick its model with a strong-or-else conditional",
          "this is the line that makes every unknown tier resolve to the "
          "cheapest model available, silently")
    check("raise ValueError" in source,
          f"{name} raises on a tier it does not know",
          "an unknown tier produces a booklet written by the wrong model, with "
          "no error and no log line to find it by")


print("\n== the validator, and only the validator, asks for exactness ==")

judge = (ROOT / "booklet_gen" / "agents" / "llm_judge.py").read_text(encoding="utf-8")
_exact = judge.count('tier="exact"')
_strong = judge.count('tier="strong"')
check(_exact == 2 and _strong == 0,
      "both validator calls use the exact tier",
      f"found {_exact} exact and {_strong} strong. A validator on the cheap "
      "tier is a tick a parent trusts and nothing checked")

others = {}
for path in sorted((ROOT / "booklet_gen" / "agents").glob("*.py")):
    if path.name == "llm_judge.py":
        continue
    count = path.read_text(encoding="utf-8").count('tier="exact"')
    if count:
        others[path.name] = count
check(not others,
      "nothing else claims it, so the upgrade stays cheap",
      f"{others} also use the exact tier. Each one added is another sequential "
      "step per subtopic, which is how the all-or-nothing switch made booklets "
      "too slow to sell in the first place")

writers = {}
for name in ("question_generator.py", "intro_writer.py",
             "challenge_generator.py", "term_planner.py", "visual_planner.py"):
    text = (ROOT / "booklet_gen" / "agents" / name).read_text(encoding="utf-8")
    writers[name] = text.count('tier="strong"')
check(all(count >= 1 for count in writers.values()),
      "and the writing agents still use the strong tier",
      f"{writers}: an agent that stopped asking for strong has been quietly "
      "moved to the fast model")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
