#!/usr/bin/env python3
"""Check that a Gemini call cannot run for ever.

The failure this exists to prevent: the client set no socket timeout, so a
hung request held its worker thread until the process restarted. On the web
app that is a booklet job stuck at "running" with a user watching a spinner.

The half that is easy to get wrong is the second one. A timeout alone does
not bound anything, because the SDK's own retry wrapper sleeps and retries
inside a single generate_content call and ignores the timeout while it does
(scripts/check_models.py documents the same trap). So the client must pass
retry=None and do its own retrying against a deadline it can see.

No API key and no network are used: a stub stands in for the SDK, so the
retry and deadline behaviour is exercised directly.

Usage:  PYTHONPATH=. python scripts/check_llm_timeout.py
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.disable(logging.CRITICAL)


class _Response:
    def __init__(self, text):
        self.text = text


class _StubModel:
    """Records how each call was made and replays a scripted outcome."""

    def __init__(self, outcomes, log):
        self._outcomes = list(outcomes)
        self._log = log

    def generate_content(self, user, generation_config=None, request_options=None):
        self._log.append(request_options or {})
        outcome = self._outcomes.pop(0) if self._outcomes else "ok"
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


class _Clock:
    """A stand-in for `time` whose sleeps are instant but still pass.

    Faking sleep without advancing the clock would let the deadline test
    pass a client that never enforces one, which is the whole point of the
    exercise, so the fake clock moves forward by exactly what was slept.
    """

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _client(outcomes, timeout_s=120.0, deadline_s=420.0):
    """A GeminiClient wired to a stub SDK. Returns (client, calls, sleeps)."""
    calls: list[dict] = []
    clock = _Clock()
    sleeps = clock.sleeps

    stub_genai = types.SimpleNamespace(
        configure=lambda **kw: None,
        GenerativeModel=lambda name, system_instruction=None: _StubModel(outcomes, calls),
    )
    google = types.ModuleType("google")
    generativeai = types.ModuleType("google.generativeai")
    generativeai.configure = stub_genai.configure
    generativeai.GenerativeModel = stub_genai.GenerativeModel
    google.generativeai = generativeai
    sys.modules["google"] = google
    sys.modules["google.generativeai"] = generativeai

    from booklet_gen.config import Config
    from booklet_gen.llm import gemini as gemini_mod

    gemini_mod.time = clock          # instant, but the clock still advances

    cfg = Config(provider="gemini", gemini_model_fast="fast",
                 gemini_model_strong="strong", claude_model_fast="",
                 claude_model_strong="", gemini_api_key="test-key",
                 anthropic_api_key="", max_retries=3)
    client = gemini_mod.GeminiClient(cfg, timeout_s=timeout_s, deadline_s=deadline_s)
    return client, calls, sleeps


def main() -> int:
    results: list[tuple[bool, str]] = []

    def check(ok, line):
        results.append((bool(ok), line))

    # 1. Every request carries a timeout, and disables the SDK's retry loop.
    client, calls, _ = _client(["hello"], timeout_s=90)
    out = client.complete("sys", "user")
    opts = calls[0]
    check(out == "hello", f"a normal call returns its text ({out!r})")
    check(opts.get("timeout") == 90,
          f"the request carries a timeout ({opts.get('timeout')}s)")
    check("retry" in opts and opts["retry"] is None,
          "retry=None: the SDK cannot sleep past the timeout on its own")

    # 2. A rate limit is still retried, with the hinted delay.
    client, calls, sleeps = _client(
        [Exception("429 Resource has been exhausted, retry in 12.5s"), "second"])
    out = client.complete("sys", "user")
    check(out == "second" and len(calls) == 2 and sleeps == [12.5],
          f"429 retried after the hinted {sleeps} s pause")

    # 3. A transient 503 is retried too. It used to be the SDK's job, and
    #    turning the SDK's retries off without replacing them would have
    #    turned a blip into a failed booklet.
    client, calls, sleeps = _client([Exception("503 Service Unavailable"), "third"])
    out = client.complete("sys", "user")
    check(out == "third" and len(calls) == 2 and sleeps,
          f"503 retried after {sleeps} s")

    # 4. A timeout is a retryable condition, not a crash.
    client, calls, _ = _client([TimeoutError("Deadline Exceeded"), "fourth"])
    check(client.complete("sys", "user") == "fourth" and len(calls) == 2,
          "a timed-out attempt is retried once, not raised straight up")

    # 5. A bad request is NOT retried: it would only burn the budget.
    client, calls, _ = _client([Exception("400 Invalid argument"), "never"])
    try:
        client.complete("sys", "user")
        check(False, "a 400 must not be retried")
    except Exception as e:
        check("400" in str(e) and len(calls) == 1,
              "a 400 is raised on the first attempt, not retried")

    # 5b. A number that merely contains a status code is not one.
    client, calls, _ = _client(
        [Exception("400 the prompt is 1500 tokens over the limit"), "never"])
    try:
        client.complete("sys", "user")
        check(False, "a size error must not be read as a 500")
    except Exception:
        check(len(calls) == 1,
              "'1500 tokens' is not a 500: raised once, not retried")

    # 6. The deadline binds. Six 429s each hinting a 60s wait would sleep for
    #    five minutes; with a 90s budget the client gives up instead of
    #    sleeping past it. This is the case a naive timeout does not cover.
    rate_limited = [Exception("429 quota, retry in 60s")] * 6
    client, calls, sleeps = _client(rate_limited, timeout_s=30, deadline_s=90)
    try:
        client.complete("sys", "user")
        check(False, "the deadline must stop the retry loop")
    except Exception:
        check(sum(sleeps) < 90 and len(calls) <= 2,
              f"gave up after {len(calls)} attempts and {sum(sleeps)}s of "
              "sleeping, inside the 90s budget")

    # 7. The per-attempt timeout never exceeds what is left of the budget.
    client, calls, sleeps = _client(
        [Exception("429 quota, retry in 30s"), "late"], timeout_s=45, deadline_s=60)
    client.complete("sys", "user")
    timeouts = [c["timeout"] for c in calls]
    check(timeouts == [45, 30],
          f"attempt timeouts shrink with what is left of the budget: {timeouts}")

    # 8. A deadline shorter than one attempt would be self-defeating.
    client, calls, _ = _client(["ok"], timeout_s=120, deadline_s=5)
    client.complete("sys", "user")
    check(calls[0]["timeout"] > 5 or client._deadline_s >= client._timeout_s,
          "a deadline below one timeout is raised to one timeout")

    print("Gemini call bounds")
    print("-" * 66)
    failures = 0
    for ok, line in results:
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {line}")
    print(f"\n{len(results) - failures}/{len(results)} behaved as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
