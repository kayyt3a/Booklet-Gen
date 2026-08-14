"""Checks NAPLAN products are offered only for the years NAPLAN is sat.

NAPLAN is sat in Years 3, 5, 7 and 9 in Australia and in no other year. A
"Year 6 NAPLAN booklet" is not a harder or easier version of a real thing, it
is practice for a test that does not exist.

It would also have been silently ungrounded. booklet_gen/guidance/ holds
naplan_year_{3,5,7,9}.txt and nothing else, and Program.authoring_guidance
skips a missing year supplement without raising, so a Year 6 NAPLAN booklet
would ship wearing the label with none of the year calibration behind it and
no error anywhere to say so.

The dropdown filter is a courtesy. The year arrives in a POST body, so the
server check is the actual rule, and that is what most of this file tests.

    PYTHONPATH=. python scripts/check_naplan_years.py
"""
import re
import sys
from pathlib import Path

from booklet_gen.programs import NAPLAN_PROGRAMS, PROGRAMS
from booklet_gen.webapp import views

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nTHE YEARS ARE THE ONES NAPLAN IS ACTUALLY SAT IN")

assert views.NAPLAN_YEARS == ("Year 3", "Year 5", "Year 7", "Year 9"), views.NAPLAN_YEARS
ok("Years 3, 5, 7 and 9, and no others")

for bad in ("Year 1", "Year 2", "Year 4", "Year 6", "Year 8", "Year 10"):
    assert bad not in views.NAPLAN_YEARS
ok("every other year in the booklet range is excluded")

print("\nEVERY YEAR OFFERED HAS A GUIDANCE FILE BEHIND IT")

# The reason the list cannot quietly grow. A year added here without its
# supplement is the silent-skip failure this check exists to prevent.
guidance = Path("booklet_gen/guidance")
for year in views.NAPLAN_YEARS:
    n = "".join(ch for ch in year if ch.isdigit())
    path = guidance / f"naplan_year_{n}.txt"
    assert path.is_file(), (
        f"{year} is offered for NAPLAN but {path} does not exist. A missing "
        "year supplement is skipped in silence, so this booklet would ship "
        "with no year calibration and no error")
ok("each offered year has its naplan_year_N.txt supplement")

present = sorted(int(m.group(1)) for m in
                 (re.match(r"naplan_year_(\d+)\.txt$", f.name)
                  for f in guidance.iterdir()) if m)
assert present == [3, 5, 7, 9], present
ok("no orphan year supplement exists for a year that is not offered")

print("\nTHE SERVER REFUSES A YEAR THAT WAS NEVER IN THE MENU")

src = Path("booklet_gen/webapp/views.py").read_text(encoding="utf-8")
assert "program in NAPLAN_PROGRAMS and year not in NAPLAN_YEARS" in src, (
    "nothing on the server rejects a NAPLAN request for a year NAPLAN is not "
    "sat in. The dropdown is filtered in JavaScript, which a posted form does "
    "not have to obey")
ok("views.generate rejects a NAPLAN request outside those years")

# Order matters: the check has to sit before credits are reserved, or a refused
# request has already cost the customer.
i_check = src.index("program in NAPLAN_PROGRAMS and year not in NAPLAN_YEARS")
i_units = src.index("units = TERM_WEEKS if is_term else 1")
assert i_check < i_units, (
    "the year is validated after the credit cost is worked out, so a rejected "
    "request could still spend the customer's credits")
ok("the check runs before any credit is reserved")

print("\nIT COVERS EVERY NAPLAN-SHAPED PRODUCT, NOT JUST ONE KEY")

assert "naplan" in NAPLAN_PROGRAMS
ok(f"NAPLAN_PROGRAMS carries {len(NAPLAN_PROGRAMS)} key(s): "
   f"{', '.join(sorted(NAPLAN_PROGRAMS))}")

for key in NAPLAN_PROGRAMS:
    assert key in PROGRAMS, f"{key} is named as NAPLAN-shaped but is not a program"
ok("every key named NAPLAN-shaped is a real program")

# The rule is written against the set, not against the string "naplan", so a
# holiday or any future NAPLAN product inherits it rather than being forgotten.
assert '"naplan"' not in src.split("NAPLAN_PROGRAMS")[0][-400:], \
    "the year rule is pinned to one hard-coded key instead of the set"
ok("a new NAPLAN product inherits the restriction automatically")

print("\nTHE MENU IS FILTERED TOO")

tpl = Path("booklet_gen/webapp/templates/generate.html").read_text(encoding="utf-8")
assert 'data-naplan-year=' in tpl, "year options do not say which are NAPLAN years"
assert 'data-naplan=' in tpl, "program choices do not say which are NAPLAN products"
assert "getAttribute('data-naplan-year')" in tpl, \
    "the year dropdown is never filtered for NAPLAN, so a parent is offered " \
    "Year 6 NAPLAN and only finds out after submitting"
ok("the year dropdown narrows when a NAPLAN product is chosen")

assert "naplan_years=" in src and "naplan_programs=" in src, \
    "the template is never given the year list, so its filter is empty"
ok("the template is handed the years and the product keys from one source")

print(f"\nALL {_passed} NAPLAN YEAR CHECKS PASSED")
sys.exit(0)
