"""Checks that stocking the practice bank cannot run up a bill nobody agreed to.

The filler is the one part of this product that spends money with no customer
waiting on the result. Everything else is paid for before it runs. So the
question this check asks is not "does it work" but "what is the most it can
cost", and there are four independent answers, each of which has to hold on
its own because any one of them can fail quietly:

  FREE WORK FIRST      a subtopic that is short can almost always be topped up
                       from the question families already banked, and that
                       costs nothing at all. If this brake slips, the bill goes
                       up by a factor of sixty and the bank looks identical.

  THE CAP IS IN THE DATABASE   `calls_today()` is a SELECT, not a counter in
                       the process. This is the one that a reasonable person
                       gets wrong: a module-level integer passes every test
                       that runs in one process, and then a cron that fires
                       twice, or a container that restarts at 00:20, spends the
                       whole cap again. So the cap is measured here across TWO
                       SEPARATE PYTHON PROCESSES, and the same measurement is
                       run against a build that holds the counter in memory to
                       prove the measurement can tell them apart.

  BLOCKING             three rejected templates in a row and the subtopic stops
                       costing anything. Without it, one subtopic the model
                       cannot do burns budget every night for ever.

  DEMAND FIRST         a scope real students have hit dry is filled before one
                       that is merely shallow.

No network, no API key, no real model. Every call goes to a fake client that
counts, and any call the filler was not supposed to make lands on a client that
raises rather than one that quietly answers.

    PYTHONPATH=. python scripts/check_practice_filler_budget.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# A fixture refuses to run against Postgres, and so should this: it seeds
# invented questions, and on the production database those would be served.
os.environ.pop("DATABASE_URL", None)

import booklet_gen.llm as real_llm                                # noqa: E402
from booklet_gen.practice import fixtures, filler, store          # noqa: E402

# Every client in this file is injected. If the filler ever falls through to
# building a real one it would read an API key and open a socket, so the real
# factory is replaced with something that refuses and remembers.
REAL_CLIENT_ASKED_FOR = []


def _refuse_real_client(*args, **kwargs):
    REAL_CLIENT_ASKED_FOR.append(True)
    raise AssertionError(
        "the filler tried to build a real LLM client during a check")


real_llm.get_client = _refuse_real_client

PASSED = 0
TOTAL = 0


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


# ---------------------------------------------------------------------------
# The fake models
# ---------------------------------------------------------------------------
#
# Real families, not stubs. They have to survive all four structural checks in
# templates.py and then the real admission gate in verify.py, or this check
# would be measuring the budget of a filler that never banks anything, which is
# a much easier thing to keep cheap.

DERIVATIVE = {
    "verify_kind": "derivative",
    "calculator": "free", "difficulty": "medium", "marks": 3,
    "question_pattern": "Differentiate y = {a}x^{n} + {b}x with respect to x.",
    "answer_pattern": "dy/dx = {a*n}x^{n-1} + {b}",
    "working_pattern": "Bring each index down as a coefficient and reduce it "
                       "by one, so {a}x^{n} becomes {a*n}x^{n-1} and {b}x "
                       "becomes {b}.",
    "params": {"a": {"type": "int", "range": [2, 9]},
               "n": {"type": "int", "range": [2, 6]},
               "b": {"type": "int", "range": [-9, 9], "exclude": [0]}},
    "constraints": [],
    "check_pattern": {"kind": "derivative",
                      "function": "{a}*x**{n} + {b}*x"},
}

DERIVATIVE_AT = {
    "verify_kind": "derivative_at",
    "calculator": "free", "difficulty": "medium", "marks": 3,
    "question_pattern": "Given f(x) = {a}x^{n} + {b}x, find f'({p}).",
    "answer_pattern": "{a*n*p**(n-1) + b}",
    "working_pattern": "Differentiate to get {a*n}x^{n-1} + {b}, then "
                       "substitute x = {p}.",
    "params": {"a": {"type": "int", "range": [2, 9]},
               "n": {"type": "int", "range": [2, 4]},
               "b": {"type": "int", "range": [-9, 9], "exclude": [0]},
               "p": {"type": "int", "range": [1, 5]}},
    "constraints": [],
    "check_pattern": {"kind": "derivative_at",
                      "function": "{a}*x**{n} + {b}*x", "at": "{p}"},
}

FAMILIES = {"derivative": DERIVATIVE, "derivative_at": DERIVATIVE_AT}


class CountingClient:
    """Answers with a real family and counts what it was asked for.

    Each answer widens one parameter range by the call number, so every call
    produces a family the bank has not seen. Returning a byte-identical family
    every time would make the second call bank nothing, and a check measuring a
    budget would then be measuring a duplicate detector instead.
    """

    def __init__(self):
        self.calls = 0
        self.kinds: list[str] = []

    def complete(self, system, user, tier="strong", temperature=0.4):
        self.calls += 1
        kind = "derivative"
        for line in user.splitlines():
            if line.startswith("verify_kind:"):
                kind = line.split(":", 1)[1].split(".")[0].strip()
        self.kinds.append(kind)
        family = json.loads(json.dumps(FAMILIES.get(kind, DERIVATIVE)))
        family["params"]["a"]["range"] = [2, 9 + self.calls]
        return json.dumps(family)


class WrongAnswerClient:
    """A family whose stated answer is short by one term.

    The commonest real failure, and the one the probe exists for: the question
    is fine, the parameters are fine, and the author cannot state the answer to
    their own family.
    """

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, tier="strong", temperature=0.4):
        self.calls += 1
        family = json.loads(json.dumps(DERIVATIVE))
        family["answer_pattern"] = "dy/dx = {a*n}x^{n-1}"
        family["params"]["a"]["range"] = [2, 9 + self.calls]
        return json.dumps(family)


class ExplodingClient:
    """Any call at all is the failure. Never a quiet answer."""

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, tier="strong", temperature=0.4):
        self.calls += 1
        raise AssertionError(
            "the filler made an LLM call it had no reason to make")


# Three subtopics whose only verify kind is `derivative`, so the fake model is
# always asked for something it can honestly answer.
NODE = "methods.calculus.polynomial-derivatives"
CHAIN = "methods.calculus.chain-rule"
PRODUCT = "methods.calculus.product-quotient"


def fresh() -> Path:
    path = fixtures.fresh_database()
    store.init_practice_db()
    return path


# ---------------------------------------------------------------------------
print("\n== a bank that is already deep costs nothing to leave alone ==")

DB_PATH = fresh()
bank = fixtures.seed_bank(subtopic_ids=[NODE], templates_per_subtopic=2,
                          items_per_template=60)
idle = ExplodingClient()
report = filler.fill_once(client=idle, only=[NODE], budget=20,
                          min_depth=100, target_depth=100, count=10)
check(idle.calls == 0 and report.calls == 0,
      f"a subtopic already {bank.size} deep drew {report.calls} call(s)",
      "the filler generates against a full bank, which is the whole nightly "
      "bill spent on questions nobody needed")

# ---------------------------------------------------------------------------
print("\n== free work first: a deficit an existing family can close ==")

DB_PATH = fresh()
seed = CountingClient()
filler.fill_once(client=seed, only=[NODE], budget=1, min_depth=100,
                 target_depth=100, count=20)
seeded_depth = store.bank_depth([NODE]).get(NODE, 0)
check(seed.calls == 1 and seeded_depth > 0,
      f"one call seeded one family and {seeded_depth} verified question(s)",
      "the fake family did not survive the admission gate, so nothing below "
      "this measures a real filler")

free = ExplodingClient()
report = filler.fill_once(client=free, only=[NODE], budget=20,
                          min_depth=150, target_depth=150, count=60)
after = store.bank_depth([NODE]).get(NODE, 0)
check(free.calls == 0 and report.calls == 0,
      f"closing a deficit of {150 - seeded_depth} from families already "
      f"banked cost {report.calls} call(s)",
      "the cheapest work in the whole engine is being skipped, and every "
      "question it would have made for nothing is being paid for instead")
check(after >= 150,
      f"the subtopic reached {after} questions with no model involved",
      "free work ran but stopped short, so the run still falls through to a "
      "call it did not need")
check(report.items_from_existing == after - seeded_depth,
      f"all {report.items_from_existing} new question(s) are recorded as "
      f"coming from families already banked",
      "the run cannot tell free work from paid work in its own accounting, "
      "so the nightly report cannot either")

# ---------------------------------------------------------------------------
print("\n== a shallow subtopic spends, and stops exactly at the cap ==")

DB_PATH = fresh()
spender = CountingClient()
report = filler.fill_once(client=spender, only=[NODE], budget=3,
                          min_depth=0, target_depth=5000, count=5)
check(spender.calls == 3,
      f"a budget of 3 bought exactly {spender.calls} call(s)",
      "the cap is not the thing deciding when the run stops")
check(store.calls_today() == 3,
      f"the database records {store.calls_today()} call(s) for today",
      "the run spent money the log does not know about, so tomorrow's cap "
      "starts from the wrong number")
check("cap" in report.stopped,
      f"the run says why it stopped: {report.stopped!r}",
      "a run that stops silently at its cap looks identical to a run that "
      "finished the work")

# ---------------------------------------------------------------------------
print("\n== the cap holds across two separate processes ==")

RUNNER = textwrap.dedent('''
    """One filler run, in its own process. Prints the calls it made."""
    import json, os, sys
    sys.path.insert(0, {repo!r})
    os.environ.pop("DATABASE_URL", None)

    from booklet_gen.practice import filler, store

    FAMILY = json.loads({family!r})

    class Counting:
        def __init__(self):
            self.calls = 0
        def complete(self, system, user, tier="strong", temperature=0.4):
            self.calls += 1
            family = json.loads(json.dumps(FAMILY))
            family["params"]["a"]["range"] = [2, 9 + self.calls + {salt}]
            return json.dumps(family)

    if "--in-memory" in sys.argv:
        # The build this check exists to fail. Everything else is identical;
        # the only change is that the budget is counted in a process variable
        # instead of being read back out of the database.
        spent = {{"n": 0}}
        real_note = store.note_generation
        def note(subtopic_id, **kw):
            spent["n"] += int(kw.get("calls", 0))
            return real_note(subtopic_id, **kw)
        store.note_generation = note
        store.calls_today = lambda now=None: spent["n"]

    client = Counting()
    filler.fill_once(client=client, only=[{node!r}], budget={budget},
                     min_depth=0, target_depth=5000, count=4)
    print(json.dumps({{"calls": client.calls}}))
''')


def run_in_process(script: Path, db_path: Path, in_memory: bool) -> int:
    env = dict(os.environ)
    env["FOLIO_DB"] = str(db_path)
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = str(REPO)
    argv = [sys.executable, str(script)] + (["--in-memory"] if in_memory else [])
    done = subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=str(REPO), timeout=600)
    if done.returncode != 0:
        print(done.stdout[-2000:])
        print(done.stderr[-2000:])
        raise SystemExit(
            "the filler runner process failed, so the cap could not be "
            "measured across processes at all")
    return int(json.loads(done.stdout.strip().splitlines()[-1])["calls"])


BUDGET = 2
work = Path(fresh()).parent
script_path = work / "run_filler_once.py"


def write_runner(salt: int) -> Path:
    script_path.write_text(RUNNER.format(
        repo=str(REPO), family=json.dumps(DERIVATIVE), node=NODE,
        budget=BUDGET, salt=salt), encoding="utf-8")
    return script_path


honest_db = Path(store.accounts_db.DB_PATH)
first = run_in_process(write_runner(0), honest_db, in_memory=False)
second = run_in_process(write_runner(100), honest_db, in_memory=False)
print(f"                two runs of the shipped filler: {first} + {second} "
      f"call(s) against a cap of {BUDGET}")
check(first + second <= BUDGET,
      f"two separate processes spent {first + second} call(s) in total, "
      f"never more than the cap of {BUDGET}",
      "the second run spent the cap again. A cron that fires twice, or a "
      "container that restarts, doubles the overnight bill")
check(first == BUDGET and second == 0,
      "the first process spent the cap and the second found it already spent",
      "the two runs did not divide the cap the way a restart would, so this "
      "measurement is not testing what it claims to")

# The same measurement against the build that holds the counter in memory.
# Run, not asserted from reading the code: if this passes, the check above
# proves nothing.
DB_PATH = fresh()
memory_db = Path(store.accounts_db.DB_PATH)
work = memory_db.parent
script_path = work / "run_filler_once.py"
naive_first = run_in_process(write_runner(0), memory_db, in_memory=True)
naive_second = run_in_process(write_runner(100), memory_db, in_memory=True)
print(f"                two runs holding the counter in memory: "
      f"{naive_first} + {naive_second} call(s) against the same cap")
check(naive_first + naive_second > BUDGET,
      f"the in-memory build measurably overspends ({naive_first + naive_second} "
      f"against a cap of {BUDGET})",
      "the in-memory build passed the same measurement, so the check above "
      "cannot tell a database-backed cap from a process variable and proves "
      "nothing at all")

# ---------------------------------------------------------------------------
print("\n== three rejections in a row and the subtopic stops costing ==")

DB_PATH = fresh()
bad = WrongAnswerClient()
runs = []
for attempt in range(3):
    report = filler.fill_once(client=bad, only=[NODE], budget=10,
                              min_depth=0, target_depth=5000, count=5)
    runs.append(report.calls)
state = store.node_state(NODE)
check(sum(runs) == 3,
      f"three nights of a family whose answer is wrong cost {sum(runs)} "
      f"call(s), one each",
      "a subtopic the model cannot do is being retried inside a single run, "
      "which multiplies the cost of the failure it is meant to bound")
check(bool(state.get("blocked_reason")),
      f"the subtopic is blocked: {str(state.get('blocked_reason'))[:80]!r}",
      "nothing stops this subtopic being tried again every night for ever")
check(store.bank_depth([NODE]).get(NODE, 0) == 0,
      "not one question from a family with a wrong answer reached the bank",
      "a family whose stated answer disagrees with the verifier is banking "
      "questions anyway")

after_block = ExplodingClient()
report = filler.fill_once(client=after_block, only=[NODE], budget=10,
                          min_depth=0, target_depth=5000, count=5)
check(after_block.calls == 0,
      "the fourth night spent nothing on the blocked subtopic",
      "the block is recorded but not honoured, so it saves no money")

rejected = [t for t in store.live_templates([NODE])]
check(not rejected,
      "no rejected family is live in the bank",
      "a family the probe refused is being drawn from")

# ---------------------------------------------------------------------------
print("\n== demand decides who gets the budget ==")

DB_PATH = fresh()
store.note_scope_demand([CHAIN], dry=True)
store.note_scope_demand([CHAIN], dry=True)
picky = CountingClient()
report = filler.fill_once(client=picky, only=[PRODUCT, CHAIN], budget=1,
                          min_depth=0, target_depth=5000, count=4)
log = {row["subtopic_id"]: int(row["calls"] or 0)
       for row in store.generation_log()}
check(picky.calls == 1 and log.get(CHAIN, 0) == 1 and log.get(PRODUCT, 0) == 0,
      f"the single call went to the subtopic students hit dry ({log})",
      "the budget was spent evening out a bank nobody is grinding while a "
      "scope real students ran dry stayed empty")

# ---------------------------------------------------------------------------
print("\n== nothing here needed a network or a key ==")

check(not REAL_CLIENT_ASKED_FOR,
      "the filler never reached for a real LLM client",
      "an injected client was ignored somewhere, so one of the measurements "
      "above was taken against a build that would have opened a socket and "
      "read an API key")
check(os.environ.get("DATABASE_URL") is None,
      "the run never touched a Postgres database",
      "this check seeds invented questions, and on production that would put "
      "them in front of paying students")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
