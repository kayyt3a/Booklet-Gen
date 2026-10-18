"""Safe, structured timing events for booklet generation."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
import logging
from time import perf_counter
from typing import Callable, Iterator, TypeVar

SCHEMA_VERSION = 1
TIMING_LOGGER_NAME = "booklet_gen.generation_timing"

job_id_var: ContextVar[str | None] = ContextVar("generation_job_id", default=None)
generation_kind_var: ContextVar[str | None] = ContextVar(
    "generation_kind", default=None,
)

_REQUIRED_FIELDS = frozenset({
    "schema_version", "component", "stage", "outcome", "duration_ms",
    "job_id", "generation_kind",
})
_SAFE_LLM_FIELDS = (
    "llm_provider", "llm_model", "llm_tier", "llm_attempt",
)
ALLOWED_TIMING_FIELDS = _REQUIRED_FIELDS | frozenset(_SAFE_LLM_FIELDS)

_T = TypeVar("_T")


@dataclass(frozen=True)
class TimingEvent:
    component: str
    stage: str
    outcome: str
    duration_ms: int
    job_id: str | None
    generation_kind: str | None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_tier: str | None = None
    llm_attempt: int | None = None

    def to_dict(self) -> dict[str, object]:
        event: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "component": self.component,
            "stage": self.stage,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "job_id": self.job_id,
            "generation_kind": self.generation_kind,
        }
        for name in _SAFE_LLM_FIELDS:
            value = getattr(self, name)
            if value is not None:
                event[name] = value
        return event


@contextmanager
def generation_context(job_id: str, generation_kind: str) -> Iterator[None]:
    job_token = job_id_var.set(job_id)
    kind_token = generation_kind_var.set(generation_kind)
    try:
        yield
    finally:
        generation_kind_var.reset(kind_token)
        job_id_var.reset(job_token)


def submit_with_context(executor, function: Callable[..., _T], /, *args, **kwargs):
    context = copy_context()
    return executor.submit(context.run, function, *args, **kwargs)


def emit_timing(
    component: str,
    stage: str,
    outcome: str,
    duration_ms: int,
    *,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_tier: str | None = None,
    llm_attempt: int | None = None,
) -> None:
    event = TimingEvent(
        component=component,
        stage=stage,
        outcome=outcome,
        duration_ms=max(0, duration_ms),
        job_id=job_id_var.get(),
        generation_kind=generation_kind_var.get(),
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_tier=llm_tier,
        llm_attempt=llm_attempt,
    )
    logging.getLogger(TIMING_LOGGER_NAME).info(
        "generation_timing", extra={"timing_event": event},
    )


@contextmanager
def timed(
    component: str,
    stage: str,
    *,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_tier: str | None = None,
    llm_attempt: int | None = None,
) -> Iterator[None]:
    started = perf_counter()
    outcome = "success"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        emit_timing(
            component, stage, outcome,
            int(round((perf_counter() - started) * 1000)),
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_tier=llm_tier,
            llm_attempt=llm_attempt,
        )
