#!/usr/bin/env python3
"""Check generation timing event safety and context propagation.

Usage: PYTHONPATH=. python scripts/check_generation_timing.py
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import inspect
import io
import json
import logging
import sys
import types

from booklet_gen.agents.validator import ValidationResult
from booklet_gen.config import Config
from booklet_gen.generation_timing import (
    ALLOWED_TIMING_FIELDS,
    TIMING_LOGGER_NAME,
    generation_context,
    generation_kind_var,
    job_id_var,
    submit_with_context,
)
from booklet_gen.logging_setup import configure_generation_timing_logging
from booklet_gen.pipeline import BookletPipeline
from booklet_gen.schemas import Question


class _Response:
    def __init__(self, text: str):
        self.text = text


class _StubModel:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def generate_content(self, _user, generation_config=None, request_options=None):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(outcome)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _TimingCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []
        self.encoded = []

    def emit(self, record):
        self.events.append(record.timing_event.to_dict())
        self.encoded.append(json.dumps(record.timing_event.to_dict(), separators=(",", ":")))


def _gemini_client(outcomes):
    stub_genai = types.SimpleNamespace(
        configure=lambda **_kw: None,
        GenerativeModel=lambda _name, system_instruction=None: _StubModel(outcomes),
    )
    google = types.ModuleType("google")
    generativeai = types.ModuleType("google.generativeai")
    generativeai.configure = stub_genai.configure
    generativeai.GenerativeModel = stub_genai.GenerativeModel
    google.generativeai = generativeai
    sys.modules["google"] = google
    sys.modules["google.generativeai"] = generativeai

    from booklet_gen.llm import gemini as gemini_mod

    gemini_mod.time = _Clock()
    cfg = Config(
        provider="gemini", gemini_model_fast="fast", gemini_model_strong="strong",
        gemini_model_exact="exact", claude_model_fast="", claude_model_strong="",
        claude_model_exact="", gemini_api_key="test-key", anthropic_api_key="",
        max_retries=3,
    )
    return gemini_mod.GeminiClient(cfg, timeout_s=90, deadline_s=420)


def main() -> int:
    passed = 0

    def check(condition, message):
        nonlocal passed
        assert condition, message
        passed += 1
        print(f"  ok: {message}")

    logger = logging.getLogger(TIMING_LOGGER_NAME)
    capture = _TimingCapture()
    logger.addHandler(capture)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        print("\nCONTEXT REACHES EVERY POOL TASK")
        with generation_context("job-timing-1", "exam"):
            with ThreadPoolExecutor(max_workers=1) as executor:
                observed = submit_with_context(
                    executor, lambda: (job_id_var.get(), generation_kind_var.get()),
                ).result()
        check(observed == ("job-timing-1", "exam"),
              "copied ContextVars reach worker threads")
        check("submit_with_context" in inspect.getsource(BookletPipeline._generate_from_outline),
              "subtopic executor submits copied contexts")
        check("submit_with_context" in inspect.getsource(BookletPipeline.run_exam),
              "ordered exam executor submits copied contexts")

        print("\nTIMING JSON IS ALLOWLISTED AND REDACTED")
        with generation_context("job-timing-2", "program"):
            client = _gemini_client([
                Exception("429 private exception text, retry in 1s"), "completion secret",
            ])
            output = client.complete("system prompt secret", "user prompt secret")
        check(output == "completion secret", "retrying Gemini call returns its completion")
        encoded = capture.encoded
        for event in [json.loads(line) for line in encoded]:
            keys = set(event)
            check(keys <= ALLOWED_TIMING_FIELDS,
                  f"event keys stay in the allowlist ({sorted(keys)})")
            check({"schema_version", "component", "stage", "outcome", "duration_ms",
                   "job_id", "generation_kind"} <= keys,
                  "event has every required field")
            check(event["job_id"] == "job-timing-2" and event["generation_kind"] == "program",
                  "LLM event carries generation context")
        redacted = "\n".join(encoded)
        for secret in (
            "system prompt secret", "user prompt secret", "completion secret",
            "private exception text",
        ):
            check(secret not in redacted, f"timing JSON excludes {secret!r}")

        attempts = [event for event in capture.events if event["stage"] == "attempt"]
        complete = [event for event in capture.events if event["stage"] == "complete"]
        check([event["llm_attempt"] for event in attempts] == [1, 2],
              "Gemini emits one physical attempt event per retry attempt")
        check([event["outcome"] for event in attempts] == ["error", "success"],
              "attempt outcomes record failure then success without exception text")
        check(len(complete) == 1 and complete[0]["outcome"] == "success",
              "Gemini emits one whole-call timing event")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            configure_generation_timing_logging()
            with generation_context("job-timing-3", "program"):
                client.complete("another system prompt", "another user prompt")
        timing_lines = [line for line in stdout.getvalue().splitlines() if line]
        required = {"schema_version", "component", "stage", "outcome", "duration_ms",
                    "job_id", "generation_kind"}
        check(len(timing_lines) == 3 and all(
            required <= set(json.loads(line)) <= ALLOWED_TIMING_FIELDS
            for line in timing_lines
        ), "dedicated timing logger writes allowlisted JSONL to stdout")

        print("\nVALIDATION MAKES ONE JUDGE BATCH CALL")

        class Judge:
            def __init__(self):
                self.calls = []

            def validate_batch(self, subject, year_level, questions,
                               reference_chunks=None, passages=None):
                self.calls.append((subject, year_level, list(questions)))
                return [ValidationResult(True, "") for _ in questions]

        pipeline = BookletPipeline.__new__(BookletPipeline)
        pipeline._judge = Judge()
        result = pipeline._validate_many(
            "English", "Year 5",
            [Question(question="Question one", answer="one", working=""),
             Question(question="Question two", answer="two", working="")],
        )
        check(len(pipeline._judge.calls) == 1 and len(result) == 2,
              "two questions use one judge batch call")
    finally:
        logger.removeHandler(capture)

    print(f"\nALL {passed} GENERATION TIMING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
