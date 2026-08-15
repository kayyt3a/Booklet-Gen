"""Checks a booklet's subtopics actually use the concurrency the pipeline offers.

pipeline.py runs subtopics through a ThreadPoolExecutor sized by max_workers,
but jobs.py (the path every real booklet takes, inline or via the worker) used
to build BookletPipeline() with every default, which pins it at 4. Each
subtopic is a handful of sequential network calls to Gemini and does no
meaningful CPU work while it waits, so 4 was never a resource limit, it was an
accident: an 8-subtopic booklet serialised into two full rounds of teaching +
questions + validation for no reason.

    PYTHONPATH=. python scripts/check_generation_concurrency.py
"""
import importlib
import inspect
import os
import sys

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nA REAL BOOKLET ACTUALLY GETS MORE THAN 4 WORKERS")

os.environ.pop("FOLIO_MAX_WORKERS", None)
from booklet_gen import jobs  # noqa: E402
importlib.reload(jobs)

assert jobs.MAX_WORKERS > 4, (
    f"jobs.MAX_WORKERS is {jobs.MAX_WORKERS}. Left at the pipeline's own "
    "default of 4, the concurrency pipeline.py offers is never used")
ok(f"jobs.MAX_WORKERS defaults to {jobs.MAX_WORKERS}, above the pipeline default")

src = inspect.getsource(jobs._generate)
assert "BookletPipeline(max_workers=MAX_WORKERS)" in src, (
    "_generate still builds BookletPipeline() with every default, so a real "
    "job never sees the raised worker count")
ok("_generate passes MAX_WORKERS to the pipeline it actually runs")

print("\nTHE NUMBER IS TUNABLE WITHOUT A CODE CHANGE")

os.environ["FOLIO_MAX_WORKERS"] = "3"
importlib.reload(jobs)
assert jobs.MAX_WORKERS == 3, (
    "FOLIO_MAX_WORKERS is set but ignored, so the only way to react to a "
    "rate-limit ceiling or a slow instance is to edit and redeploy code")
ok("FOLIO_MAX_WORKERS overrides the default, so the pool can be tuned live")
os.environ.pop("FOLIO_MAX_WORKERS", None)
importlib.reload(jobs)

print("\nTHE PIPELINE'S OWN DEFAULT IS UNTOUCHED FOR OTHER CALLERS")

from booklet_gen.pipeline import BookletPipeline  # noqa: E402
sig = inspect.signature(BookletPipeline.__init__)
assert sig.parameters["max_workers"].default == 4, (
    "BookletPipeline's own default changed. The CLI and the check scripts "
    "construct it directly and should keep a small, predictable default; "
    "only the web/worker path in jobs.py should run hotter")
ok("BookletPipeline(max_workers=4) is still the bare constructor's default")

print(f"\nALL {_passed} CONCURRENCY CHECKS PASSED")
sys.exit(0)
