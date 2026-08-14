"""Checks the customer is charged for exactly what the customer receives.

The number of booklets in a term plan used to be written down twice:

    booklet_gen/webapp/views.py   TERM_WEEKS = 10   decides the CREDIT COST
    booklet_gen/jobs.py           TERM_WEEKS = 10   decides the BOOKLETS MADE

Nothing connected them. They agreed only because both said 10, and the first
person to change one and not the other would have shipped a product that bills
for ten and delivers four, or bills for four and delivers ten. Neither shows up
as an error: the job succeeds, the ZIP downloads, and the only signal is a
customer counting files.

Also checked here: there is no per-account daily cap. Credits are the
entitlement. A cap rations work the customer has already paid for, and it made
the intended buyer impossible, because a tutoring firm is one account.

    PYTHONPATH=. python scripts/check_charge_matches_delivery.py
"""
import inspect
import re
import sys
from pathlib import Path

from booklet_gen import jobs, programs
from booklet_gen.webapp import db, views

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nONE NUMBER, NOT TWO")

assert programs.TERM_PLAN_WEEKS == views.TERM_WEEKS == jobs.TERM_WEEKS, (
    f"charge={views.TERM_WEEKS} delivery={jobs.TERM_WEEKS} "
    f"source={programs.TERM_PLAN_WEEKS}")
ok(f"charge, delivery and source all read {programs.TERM_PLAN_WEEKS}")

# Equal values are not enough: two literals that happen to match today are the
# bug. They have to be the SAME object, reached by import.
for mod, path in ((views, "booklet_gen/webapp/views.py"),
                  (jobs, "booklet_gen/jobs.py")):
    src = Path(path).read_text(encoding="utf-8")
    assert not re.search(r"^TERM_WEEKS\s*=\s*\d+", src, re.M), (
        f"{path} defines TERM_WEEKS as its own literal. That is exactly the "
        "shape of the bug: two numbers that agree until someone edits one")
    assert "TERM_PLAN_WEEKS" in src, f"{path} does not import the shared value"
ok("neither module redefines the number as a literal")

print("\nTHE CHARGE IS COMPUTED FROM IT, AND SO IS THE DELIVERY")

charge_src = inspect.getsource(views.generate)
assert "units = TERM_WEEKS if is_term else 1" in charge_src, charge_src[:200]
ok("views.generate charges TERM_WEEKS credits for a term plan")

deliver_src = inspect.getsource(jobs)
assert "weeks=TERM_WEEKS" in deliver_src, (
    "jobs does not generate TERM_WEEKS booklets, so the count it delivers is "
    "decided somewhere other than where it was charged")
ok("jobs generates TERM_WEEKS booklets for a term plan")

print("\nTHE FREE BOOKLETS ARE ONE NUMBER TOO")

assert db.WELCOME_CREDITS == 2, db.WELCOME_CREDITS
ok(f"a new account starts with {db.WELCOME_CREDITS} booklets")

db_src = Path("booklet_gen/webapp/db.py").read_text(encoding="utf-8")
grants = re.findall(r"'welcome credit'|\"welcome credit\"", db_src)
assert len(grants) == 3, f"expected 3 grant sites, found {len(grants)}"
# The signup path and both backfill migrations. None may carry a literal.
assert not re.search(r"(?:SELECT id|user_id),\s*\d+,\s*['\"]welcome credit", db_src), (
    "a welcome grant still hard-codes the number of credits, so the three "
    "sites can disagree about what a new account is worth")
ok("all three grant sites read WELCOME_CREDITS, none hard-codes it")

print("\nTHERE IS NO PER-ACCOUNT DAILY CAP")

assert not hasattr(views, "DAILY_BOOKLET_LIMIT"), (
    "a per-account daily cap is back. Credits are the entitlement, and a cap "
    "rations work a customer has already bought")
ok("views has no DAILY_BOOKLET_LIMIT")

gen_src = inspect.getsource(views.generate)
assert "daily_limit=None" in gen_src, (
    "the enqueue transaction is still handed a per-account cap")
ok("the enqueue transaction is told there is no per-account cap")

# The instance ceiling is a different thing and must survive: it bounds what a
# day can cost when free welcome credits are farmed across many signups.
assert views.GLOBAL_DAILY_BOOKLET_LIMIT > 0
assert "global_daily_limit=GLOBAL_DAILY_BOOKLET_LIMIT" in gen_src
ok(f"the instance ceiling is still enforced ({views.GLOBAL_DAILY_BOOKLET_LIMIT}/day)")

# It has to bound the real spend, not just single booklets, or one account
# could clear it with term plans.
assert "def _quota_allows" in inspect.getsource(views)
q = inspect.getsource(views._quota_allows)
assert "units" in q, "the ceiling counts requests rather than booklets"
ok("the ceiling counts booklets, so a term plan costs it TERM_WEEKS")

print(f"\nALL {_passed} CHARGE AND DELIVERY CHECKS PASSED")
sys.exit(0)
