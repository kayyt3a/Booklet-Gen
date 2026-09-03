"""What the grind must still do, measured through real HTTP.

Two things are being defended here, and the first is architectural.

NO MODEL IN THE REQUEST PATH. The entire reason this feature can exist is that
pressing an arrow is a database read. Every pressure over time pushes the other
way: a subtopic runs dry and someone reaches for "just generate one", a scope
has no checker and someone reaches for "ask the model this once". Each of those
is individually reasonable and together they turn a 10ms read into a six second
wait, at which point nobody grinds anything. So the assertion is not that the
endpoint is fast. It is that the endpoint still works when `get_client` is
replaced with something that raises on contact. Nothing else settles it.

NOBODY ELSE'S QUESTIONS OR HISTORY. Sessions are addressed by an opaque id, and
an id is not authorisation. A route that looks a session up without also
matching the user hands one student another student's run.

Everything below goes through the Flask test client against a real seeded bank,
because the interesting failures are in the wiring rather than in the store, and
the store has its own check.

    PYTHONPATH=. python scripts/check_practice_api.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("DATABASE_URL", None)
os.environ["FLASK_SECRET_KEY"] = "k" * 40
os.environ["FOLIO_OUTPUT"] = str(Path(tempfile.mkdtemp(prefix="folio-api-")))
os.environ["FOLIO_JOB_MODE"] = "manual"

from booklet_gen.practice import fixtures, store                 # noqa: E402
from booklet_gen.webapp import create_app                        # noqa: E402

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


fixtures.fresh_database("folio-api-")
store.init_practice_db()
bank = fixtures.seed_bank(templates_per_subtopic=8, items_per_template=25)
SCOPE = bank.subtopic_ids[0]

app = create_app()


def sign_in(email: str):
    client = app.test_client()
    fixtures.make_user(email, "fixture-password-123")
    page = client.get("/login").data.decode()
    token = re.search(r'name="csrf_token" value="([^"]+)"', page)
    client.post("/login", data={"email": email,
                                "password": "fixture-password-123",
                                "csrf_token": token.group(1) if token else ""})
    return client


def csrf(client) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"',
                      client.get("/practice").data.decode())
    return match.group(1) if match else ""


def post(client, path, payload, token=None):
    return client.post(path, data=json.dumps(payload),
                       content_type="application/json",
                       headers={"X-CSRF-Token": token if token is not None
                                else csrf(client)})


student = sign_in("grind@example.com")
TOKEN = csrf(student)

print("\n== signed out, this serves nothing ==")

stranger = app.test_client()
for path in ("/practice", f"/practice/next?session=anything&n=5",
             "/practice/scopes"):
    reply = stranger.get(path)
    check(reply.status_code in (301, 302, 401, 403),
          f"GET {path.split('?')[0]} is refused when signed out "
          f"({reply.status_code})",
          "practice content is being served to anyone who asks")

reply = post(stranger, "/practice/session", {"subject": "Mathematics Methods",
                                             "scope_id": SCOPE}, token="")
check(reply.status_code in (301, 302, 400, 401, 403),
      f"a session cannot be started signed out ({reply.status_code})")

print("\n== a session, and then the arrow ==")

reply = post(student, "/practice/session",
             {"subject": "Mathematics Methods", "scope_id": SCOPE})
check(reply.status_code == 200, f"a session starts ({reply.status_code})",
      reply.data.decode()[:200])
session = reply.get_json() or {}
sid = session.get("session_id", "")
check(bool(sid), "and it carries a session id")
check(bool(session.get("scope_label")),
      f"and the label the student chose: {session.get('scope_label')!r}",
      "a run with no label leaves the student unable to tell what they picked")
check(int(session.get("depth") or 0) > 0,
      f"and how deep the bank is for it ({session.get('depth')})",
      "a student cannot tell a thin scope from a full one before starting")

first = student.get(f"/practice/next?session={sid}&n=10").get_json() or {}
items = first.get("items") or []
check(len(items) == 10, f"a prefetch of ten returned {len(items)}",
      "the browser's buffer starts short and the arrow waits on the network")
ids = [i["id"] for i in items]
check(len(set(ids)) == 10, "all ten are distinct",
      "the buffer holds the same question twice")
check(all(i.get("question") and i.get("answer") for i in items),
      "each carries a question and an answer",
      "the reveal has nothing to show")
check(not any("check_json" in i or "params_json" in i for i in items),
      "and none carries the payload it was verified from",
      "publishing check_json hands the student the answer to every question in "
      "the family, not just the one on screen")

print("\n== the buffer is never handed back what it already holds ==")

held = ",".join(str(i) for i in ids)
second = student.get(
    f"/practice/next?session={sid}&n=10&exclude={held}").get_json() or {}
again = [i["id"] for i in second.get("items") or []]
check(bool(again) and not (set(again) & set(ids)),
      f"a refetch excluding ten held ids returned {len(again)} fresh ones",
      "the refetch duplicated what the browser was already holding, which the "
      "student sees as the same question twice in a row")

print("\n== no model is reachable from the request path ==")

# The assertion the whole architecture rests on. Not "the endpoint is fast":
# the endpoint still works when the model client refuses on contact.
import booklet_gen.llm as llm_module                             # noqa: E402

reached = {"called": False}


def _refuse(*args, **kwargs):
    reached["called"] = True
    raise AssertionError(
        "the practice request path asked for a model client. That is the one "
        "thing this feature cannot do: a database read is 10ms and a model "
        "call is seconds, and a student does not grind against seconds")


saved = llm_module.get_client
try:
    llm_module.get_client = _refuse
    blind = student.get(f"/practice/next?session={sid}&n=10")
    served = len((blind.get_json() or {}).get("items") or [])
finally:
    llm_module.get_client = saved

check(blind.status_code == 200 and served > 0,
      f"the arrow served {served} questions with the model client refusing",
      "generation has crept into the request path")
check(not reached["called"],
      "and the model client was never reached at all",
      "something in the request path is constructing an LLM client, even if it "
      "did not use it")

print("\n== one student cannot reach another's run ==")

intruder = sign_in("intruder@example.com")
reply = intruder.get(f"/practice/next?session={sid}&n=5")
body = reply.get_json() or {}
check(reply.status_code in (403, 404) or not (body.get("items") or []),
      f"another account asking for this session gets nothing "
      f"({reply.status_code})",
      "a session id is not authorisation, and this hands one student another "
      "student's run")

reply = post(intruder, "/practice/reset", {"session_id": sid})
check(reply.status_code in (403, 404),
      f"and cannot reset it either ({reply.status_code})",
      "one student can wipe another's history")

print("\n== a replayed flush changes nothing ==")

events = [{"item_id": i, "outcome": "got_it"} for i in ids[:5]]
post(student, "/practice/seen", {"session_id": sid, "events": events})
post(student, "/practice/seen", {"session_id": sid, "events": events})
post(student, "/practice/seen", {"session_id": sid, "events": events})
third = student.get(
    f"/practice/next?session={sid}&n=10&exclude=").get_json() or {}
repeated = set(i["id"] for i in third.get("items") or []) & set(ids[:5])
check(not repeated,
      "questions marked seen are not served again after three identical flushes",
      "a retry after a flaky connection either double-counted or was lost")

print("\n== CSRF is enforced on everything that writes ==")

for path, payload in (("/practice/session",
                       {"subject": "Mathematics Methods", "scope_id": SCOPE}),
                      ("/practice/seen", {"session_id": sid, "events": []}),
                      ("/practice/reset", {"session_id": sid})):
    reply = post(student, path, payload, token="not-the-token")
    check(reply.status_code in (400, 403),
          f"POST {path} without a valid token is refused ({reply.status_code})",
          "a write endpoint with no CSRF check is reachable from any page the "
          "student happens to have open")

print("\n== a narrow scope that runs dry says so, and stays narrow ==")

thin = fixtures.seed_thin_scope()
node = thin.subtopic_ids[0]
reply = post(student, "/practice/session",
             {"subject": "Mathematics Methods", "scope_id": node})
thin_sid = (reply.get_json() or {}).get("session_id", "")

drained, dry = [], False
for _ in range(8):
    payload = student.get(
        f"/practice/next?session={thin_sid}&n=5").get_json() or {}
    batch = payload.get("items") or []
    drained += [i["id"] for i in batch]
    post(student, "/practice/seen",
         {"session_id": thin_sid,
          "events": [{"item_id": i["id"]} for i in batch]})
    if payload.get("dry"):
        dry = True
        break

check(dry, f"the scope reported dry after {len(drained)} questions",
      "a student who has worked through a narrow scope is not told, so they "
      "cannot tell revision from new work")
served_nodes = {store.get_item(i).subtopic_id for i in drained
                if store.get_item(i)}
check(served_nodes <= set(thin.subtopic_ids),
      f"and every question stayed inside the chosen scope ({served_nodes})",
      "the API widened the scope to cover a shortfall, which is the one thing "
      "this feature must never do")

print("\n== a refetch over a full bank is fast enough to hide ==")

deep = fixtures.seed_bank(subtopic_ids=bank.subtopic_ids,
                          templates_per_subtopic=12, items_per_template=60,
                          seed=99)
runner = sign_in("timing@example.com")
reply = post(runner, "/practice/session",
             {"subject": "Mathematics Methods",
              "scope_id": bank.subtopic_ids[0]})
run_sid = (reply.get_json() or {}).get("session_id", "")

samples = []
for _ in range(5):
    started = time.perf_counter()
    runner.get(f"/practice/next?session={run_sid}&n=10")
    samples.append((time.perf_counter() - started) * 1000)
worst = max(samples)
print(f"                refetch of ten over a bank of {deep.size + bank.size}: "
      f"median {sorted(samples)[len(samples) // 2]:.0f}ms, worst {worst:.0f}ms")
check(worst < 400,
      f"the slowest refetch was {worst:.0f}ms",
      "the buffer drains faster than it refills and the arrow starts waiting, "
      "which is the whole thing this design exists to prevent")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
