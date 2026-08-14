"""Checks generation survives a deploy without anyone flipping a switch.

Production ran FOLIO_JOB_MODE=inline, so booklets were generated in a thread
inside the web service and the worker was commented out of render.yaml. Every
deploy, restart or gunicorn recycle destroyed whatever was generating. A term
plan runs for tens of minutes, so shipping the website meant interrupting work
customers had paid for.

The documented fix was a three-step dance: provision the worker, confirm its
heartbeat, then flip FOLIO_JOB_MODE=queue. Done in the wrong order it breaks
generation in one direction or the other. Set to queue with no worker, the site
refuses every order. Left on inline with a worker running, the worker idles and
deploys keep eating jobs.

So the mode is decided per request instead. What makes that safe, and what this
file checks, is that claim_job is an atomic conditional update: an inline
thread and a worker can both believe the job is theirs and only one can have
it.

    PYTHONPATH=. python scripts/check_job_dispatch.py
"""
import inspect
import sys
import uuid
from pathlib import Path

import yaml

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nA JOB CAN NEVER RUN TWICE, WHATEVER EACH SIDE BELIEVES")

import os  # noqa: E402
S = "/tmp/claude-0/-home-user-Booklet-Gen/bd3ebbb5-2acb-556d-9e4a-d748ead9e9ef/scratchpad"
os.environ.setdefault("FOLIO_DB", f"{S}/dispatch_check.sqlite")
Path(os.environ["FOLIO_DB"]).unlink(missing_ok=True)

from booklet_gen.webapp import create_app, db, views  # noqa: E402

app = create_app()
with app.app_context():
    uid = db.create_user("dispatch@test.local", "x" * 60)
    db.grant_credits(uid, 50, reason="test", reference="dispatch-seed")
    jid = uuid.uuid4().hex
    assert db.enqueue_job(jid, uid, "J", 1, {"program": "accelerate"}, True)

    # This is the whole safety argument. Two claimants, one winner.
    first = db.claim_job(jid)
    second = db.claim_job(jid)
    assert first is not None, "nobody could claim a queued job"
    assert second is None, (
        "a job already claimed was claimed again. Inline and worker would "
        "both generate it, spending twice and racing on the same output")
ok("a queued job can be claimed exactly once")

src = inspect.getsource(db.claim_job)
assert "status='queued'" in src.replace('"', "'"), src
ok("the claim is conditional on the job still being queued, in one statement")

print("\nTHE MODE IS DECIDED PER REQUEST, NOT BY A HUMAN")

assert views.JOB_MODE == "auto", (
    f"default mode is {views.JOB_MODE!r}. inline loses jobs on every deploy "
    "and queue refuses orders when the worker is missing")
ok("auto is the default, so a fresh deploy needs no switch flipped")

disp = inspect.getsource(views._dispatch_job)
assert 'JOB_MODE == "auto"' in disp and "_worker_is_live()" in disp, disp
ok("auto asks whether a worker is alive before leaving the job queued")

live = inspect.getsource(views._worker_is_live)
assert "except Exception" in live and "return False" in live, live
ok("a worker that cannot be asked about counts as absent, so the job still runs")

print("\nEACH MODE STILL DOES WHAT ITS NAME SAYS")

calls = []
real_thread = views.threading.Thread


class FakeThread:
    def __init__(self, *a, **kw):
        calls.append(kw.get("args", a))

    def start(self):
        pass


views.threading.Thread = FakeThread
try:
    for mode, worker_live, should_run_here in (
        ("inline", False, True),
        ("inline", True, True),     # inline ignores the worker entirely
        ("queue", False, False),
        ("queue", True, False),
        ("auto", False, True),      # no worker: this process does it
        ("auto", True, False),      # worker alive: leave it queued
    ):
        calls.clear()
        views.JOB_MODE = mode
        views._worker_is_live = lambda live=worker_live: live
        views._dispatch_job("job-" + mode, {})
        ran = bool(calls)
        assert ran == should_run_here, (
            f"mode={mode} worker_live={worker_live}: ran_in_process={ran}, "
            f"expected {should_run_here}")
finally:
    views.threading.Thread = real_thread
    views.JOB_MODE = "auto"
ok("inline always generates here, queue never does, auto decides on the worker")

print("\nTHE DEPLOY CONFIG MATCHES")

cfg = yaml.safe_load(Path("render.yaml").read_text(encoding="utf-8"))
kinds = {s["type"]: s["name"] for s in cfg["services"]}
assert kinds.get("worker"), (
    "render.yaml defines no worker, so generation still runs inside the web "
    "service and a deploy still kills it")
ok(f"render.yaml provisions the worker ({kinds['worker']})")

web = next(s for s in cfg["services"] if s["type"] == "web")
mode = next(e["value"] for e in web["envVars"] if e.get("key") == "FOLIO_JOB_MODE")
assert mode == "auto", mode
ok("the web service ships FOLIO_JOB_MODE=auto")

worker = next(s for s in cfg["services"] if s["type"] == "worker")
assert worker.get("maxShutdownDelaySeconds", 0) >= 300, worker.get("maxShutdownDelaySeconds")
ok("the worker is given time to finish a booklet on a redeploy")

# Secrets entered once. Two copies of a database URL is two chances to point
# half the system at the wrong database.
groups = {g["name"] for g in cfg.get("envVarGroups", [])}
assert groups, "no env var group, so every secret must be pasted into both services"
for svc in (web, worker):
    assert any(e.get("fromGroup") in groups for e in svc["envVars"]), svc["name"]
ok(f"both services read the shared secret group ({', '.join(sorted(groups))})")

shared = {e["key"] for g in cfg["envVarGroups"] for e in g["envVars"]}
for needed in ("GEMINI_API_KEY", "DATABASE_URL"):
    assert needed in shared, f"{needed} is not shared, so the worker cannot generate"
ok("the worker inherits the API key and the database it needs to generate")

print(f"\nALL {_passed} DISPATCH CHECKS PASSED")
sys.exit(0)
