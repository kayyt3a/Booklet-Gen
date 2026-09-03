"""Checks that grinding the bank never feels like grinding the same question.

This is the check the whole practice feature rests on. A student pressing an
arrow two hundred times in an evening will forgive a plain page and a thin
explanation, but they will not forgive being handed a question they answered
four screens ago, and they will not forgive "differentiate 2x^3" followed by
"differentiate 3x^3". A bank with thirty thousand verified questions in it is
worth nothing if the draw hands them out in the order they were generated.

Three separate mechanisms have to hold, and each one catches what the others
cannot:

  the UNIQUE constraint     an exact duplicate cannot be stored at all
  shuffle_key               the serving order is divorced from the parameter
                            sweep order that produced the items
  _apply_spacing            no two questions from one family land together

and one promise on top of all three: when a narrow scope runs out, the draw
says so. It never widens. A student who chose Antidifferentiation and is
quietly fed confidence intervals has been lied to by the one feature whose
entire promise is the scope.

    PYTHONPATH=. python scripts/check_practice_seen_and_spacing.py
"""
from __future__ import annotations


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.practice import fixtures, store            # noqa: E402
from booklet_gen.practice.models import SeenEvent           # noqa: E402

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


DB_PATH = fixtures.fresh_database()
store.init_practice_db()

# Six families of twenty across three subtopics. Every item carries a
# parameter pair that appears nowhere else in the bank, so two adjacent
# questions with equal parameters mean the draw served the same question
# twice, not that the seeder manufactured a collision.
bank = fixtures.seed_bank(templates_per_subtopic=6, items_per_template=20)
scope = bank.subtopic_ids
student = fixtures.make_user("grinder@example.com")

print(f"\n== a run of 60 questions from a bank of {bank.size} ==")

served: list = []
for _ in range(6):
    result = store.draw(student, scope, limit=10)
    served.extend(result.items)
    store.record_seen(student, [SeenEvent(i.id) for i in result.items])

check(len(served) == 60,
      f"six draws of ten returned {len(served)} questions",
      "a draw that quietly returns fewer than it was asked for empties the "
      "prefetch buffer and the arrow key starts waiting on the network")

ids = [i.id for i in served]
check(len(set(ids)) == len(ids),
      "no question was served twice while unseen stock remained",
      f"{len(ids) - len(set(ids))} repeat(s) inside the first 60 of a bank of "
      f"{bank.size}: the student is being handed back work they have done")

families = [i.template_id for i in served]
adjacent = [(a, b) for a, b in zip(families, families[1:]) if a == b]
check(not adjacent,
      "no two consecutive questions came from the same family",
      f"{len(adjacent)} adjacent pair(s) from one template: this is the "
      "'differentiate 2x^3 then differentiate 3x^3' failure, which the "
      "uniqueness constraint considers two perfectly distinct questions")

params = [bank.params_by_item.get(i.id) for i in served]
same_params = [(a, b) for a, b in zip(params, params[1:]) if a == b and a]
check(not same_params,
      "no two consecutive questions had identical parameters",
      f"{len(same_params)} adjacent pair(s) share a parameter tuple")

check(all(i.subtopic_id in scope for i in served),
      "every question served came from inside the chosen scope",
      "the draw reached outside the scope the student picked")

# --------------------------------------------------------------------------
print("\n== the sweep order is not the serving order ==")

# The seeder inserts each family's twenty items in parameter order. If the
# draw returned them in insertion order the student would climb a=1, a=2, a=3
# through every family in turn, which is the most tedious possible way to meet
# a bank this size, and every assertion above would still pass.
by_family: dict[str, list[int]] = {}
for item in served:
    by_family.setdefault(item.template_id, []).append(item.id)
runs = [v for v in by_family.values() if len(v) >= 3]
ascending = [v for v in runs if v == sorted(v)]
check(len(runs) >= 3, f"{len(runs)} families contributed three or more items",
      "too few families to judge ordering")
check(len(ascending) < len(runs),
      f"{len(ascending)} of {len(runs)} families arrived in insertion order",
      "every family came out in the order it was generated, so shuffle_key is "
      "not being used and the student climbs a=1, a=2, a=3 through the bank")

# --------------------------------------------------------------------------
print("\n== what the student has seen outlives the session ==")

before = store.seen_count(student, scope)
fresh = store.draw(student, scope, limit=10)
overlap = {i.id for i in fresh.items} & set(ids)
check(before == 60, f"{before} sightings recorded for this student",
      "seen state is not being written, so tomorrow starts from scratch")
check(not overlap,
      "a brand new draw served nothing the student had already seen",
      f"{len(overlap)} question(s) came back: closing the tab and returning "
      "hands the student the same twenty questions again")

# A second student is a different person, not a continuation of the first.
other = fixtures.make_user("other@example.com")
theirs = store.draw(other, scope, limit=10)
check(len({i.id for i in theirs.items} & set(ids)) > 0,
      "a second student is served questions the first has already seen",
      "seen state is leaking across accounts, so every student after the "
      "first grinds a smaller bank than the one they paid for")
check(store.seen_count(other, scope) == 0,
      "and the second student's own history is still empty",
      "drawing wrote a sighting for the wrong account")

# --------------------------------------------------------------------------
print("\n== a replayed batch changes nothing ==")

batch = [SeenEvent(i.id, "got_it") for i in theirs.items]
store.record_seen(other, batch)
first_pass = store.seen_count(other, scope)
store.record_seen(other, batch)
store.record_seen(other, batch)
check(store.seen_count(other, scope) == first_pass == len(batch),
      f"three sends of the same batch left {first_pass} rows",
      "a flaky connection that retries a flush would inflate the history and "
      "shrink the bank the student can still be served")

# --------------------------------------------------------------------------
print("\n== running a narrow scope dry is said out loud, never widened ==")

narrow = fixtures.seed_thin_scope()
node = narrow.subtopic_ids[0]
digger = fixtures.make_user("digger@example.com")

drained: list = []
for _ in range(6):
    result = store.draw(digger, [node], limit=5)
    drained.extend(result.items)
    store.record_seen(digger, [SeenEvent(i.id) for i in result.items])
    if result.dry:
        break

check(result.dry,
      f"the draw reported dry after {len(drained)} questions from a scope of "
      f"{narrow.size}",
      "a student who has worked through everything in a narrow scope is not "
      "told, so they cannot tell revision from new work")
check(all(i.subtopic_id == node for i in drained + list(result.items)),
      "and every question still came from the one subtopic they chose",
      "the scope was widened to cover the shortfall, which is the one thing "
      "this feature must never do: it is the whole promise")
check(result.spacing == "relaxed",
      f"a scope of {narrow.size} questions reports spacing='relaxed'",
      "the payload claims variety a scope this thin cannot deliver")

repeats = [i for i in result.items if i.id in result.repeats]
check(bool(result.repeats) and len(repeats) == len(result.items),
      f"{len(result.repeats)} of the dry batch are flagged as repeats",
      "questions the student has already answered are being presented as new")

# --------------------------------------------------------------------------
print("\n== an unstocked scope is a different sentence from an empty one ==")

judge_only = ["chemistry.bonding.covalent"]
blank = store.draw(digger, judge_only, limit=5)
check(blank.unstocked and not blank.items,
      "a subtopic with nothing banked reports unstocked, not dry",
      "'you have finished everything here' and 'we have not written this yet' "
      "are different sentences, and telling a student the first when the "
      "second is true is a lie about their own progress")
check(not store.draw(digger, [], limit=5).items,
      "an empty scope serves nothing at all",
      "an unresolvable scope fell through to serving the whole bank")

# --------------------------------------------------------------------------
print("\n== the naive build this is all defending against ==")

# ORDER BY random() LIMIT n is what anyone writes first, and it passes a
# casual look: the questions differ. Measured over a run it repeats items and
# clusters families, which is exactly what a grinding student notices and what
# the three layers above exist to stop. Run here rather than asserted.
import sqlite3  # noqa: E402

naive_ids: list[str] = []
naive_families: list[str] = []
with sqlite3.connect(DB_PATH) as conn:
    conn.row_factory = sqlite3.Row
    holes = ",".join("?" * len(scope))
    for _ in range(6):
        rows = conn.execute(
            f"""SELECT id, template_id FROM practice_items
                WHERE status='live' AND subtopic_id IN ({holes})
                ORDER BY random() LIMIT 10""", tuple(scope)).fetchall()
        naive_ids += [r["id"] for r in rows]
        naive_families += [r["template_id"] for r in rows]

naive_repeats = len(naive_ids) - len(set(naive_ids))
naive_adjacent = sum(1 for a, b in zip(naive_families, naive_families[1:])
                     if a == b)
print(f"                naive draw over the same bank: {naive_repeats} "
      f"repeated question(s), {naive_adjacent} same-family adjacent pair(s)")
check(naive_repeats > 0 or naive_adjacent > 0,
      "the naive draw measurably fails what the real draw passes",
      "the naive build happened to behave this run, so this comparison "
      "proves nothing and the seeded bank is too small to tell them apart")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
