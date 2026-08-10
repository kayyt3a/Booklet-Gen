"""Weekly times tables: one table set at the back of a booklet, tested at the
front of the next.

Times tables are not a method to derive, they are facts to know cold, and the
only thing that reliably installs them is spaced retrieval: meet the table,
take it away, be asked for it later without warning. That is a routine running
across a term, not a topic taught inside one booklet, so it is modelled the
same way spelling already is.

No model is called anywhere in this file. The three times table is the three
times table, and asking an LLM to produce it would introduce a failure mode
where none needs to exist.

Three properties matter more than which table lands in which week:

  A TABLE IS TESTED ONLY IF IT WAS SET
      `make_test` takes the previous booklet's `TablesList` and nothing else.
      With no previous list it returns None, so week 1 opens with no test
      rather than a test on a table the student was never given.

  NO TABLE IS TESTED TWICE, AND NONE IS SET TWICE
      The caller clears its carried list as soon as a test is made, so a week
      that sets no new table (a revision week, or the last week of term) does
      not cause the week after it to re-test the table before. `next_list`
      filters against everything set so far.

  THE ORDER IS TAUGHT, NOT ARBITRARY
      Anchors first (2, 5, 10, all reachable by skip counting), then the
      doubling chain (4 is double the 2s, 8 is double the 4s), then the threes
      family (3, 6, 9), then 7, which has the fewest hooks of any of them and
      is easiest once its neighbours are known, and finally the two that are
      pattern rather than recall.
"""
from __future__ import annotations

import logging
import random
import re

from ..schemas import TablesList, TablesTest

log = logging.getLogger(__name__)

TEACHING_ORDER: tuple[int, ...] = (2, 5, 10, 4, 8, 3, 6, 9, 7, 11, 12)

# 1 through 12. Twelve is the Australian convention and the reason 11 and 12
# are worth setting at all.
HIGHEST_MULTIPLIER = 12

# Year 3 is where tables are learned and Year 4 is where they are expected to
# be fluent. Year 2 meets 2s, 5s and 10s but as skip counting rather than
# recall, and by Year 5 a booklet spending a page a week on this is taking
# space from the curriculum it is supposed to be accelerating.
TABLES_YEARS = (3, 4)

# A Year 4 booklet claiming to get a student ahead should not spend its first
# three weeks on the tables that Year 3 already sets. A Year 4 student who is
# in fact shaky on the 2s is found out by the 4s and the 8s a fortnight later,
# which is a better place to discover it than a week nobody had to think in.
_START_INDEX = {3: 0, 4: 3}


def _year_number(year_level: str) -> int | None:
    digits = re.search(r"\d+", year_level or "")
    return int(digits.group()) if digits else None


def tables_apply(program, plan_subject: str, year_level: str) -> bool:
    """Whether this booklet carries the weekly tables routine.

    It rides on booklets that teach mathematics at the years where tables are
    the actual work: Academic Accelerate with Mathematics picked, and NAPLAN
    Practice, whose numeracy half is mathematics. An English or reasoning
    booklet with a times table at the back is noise the parent has to explain,
    which is the same reason spelling stays off maths booklets.
    """
    year = _year_number(year_level)
    if year not in TABLES_YEARS:
        return False
    subjects = (plan_subject,) if program.pick_subject else program.subjects
    return any((s or "").strip().lower() in {"mathematics", "maths"}
               for s in subjects)


def next_list(already_set: list[int], year_level: str) -> TablesList | None:
    """The next table to set, or None once the sequence is used up.

    Running out returns None rather than wrapping around. A term long enough to
    exhaust the order has taught every table there is, and re-setting the 2s in
    week 12 would be a week the student does for free.
    """
    start = _START_INDEX.get(_year_number(year_level) or 0, 0)
    for table in TEACHING_ORDER[start:]:
        if table not in already_set:
            return TablesList(table=table)
    log.info("tables.sequence_exhausted", extra={"set": len(already_set)})
    return None


def make_test(previous: TablesList | None,
              from_week: int | None) -> TablesTest | None:
    """A shuffled recall drill on the table set in the previous booklet.

    Seeded from the table and the week it was set, so regenerating a term
    produces the same paper twice. An unseeded shuffle would mean the answer
    key printed in one run did not match the question page of another.
    """
    if previous is None or not previous.table:
        return None
    order = list(range(1, HIGHEST_MULTIPLIER + 1))
    random.Random(f"tables:{previous.table}:{from_week}").shuffle(order)
    return TablesTest(table=previous.table, order=order, from_week=from_week)


def facts(table: int) -> list[tuple[int, int, int]]:
    """(table, multiplier, product) for the whole table, in order."""
    return [(table, m, table * m)
            for m in range(1, HIGHEST_MULTIPLIER + 1)]


def test_questions(test) -> list[tuple[int, int, int]]:
    """(table, multiplier, product) in the shuffled order the test asks them."""
    table = getattr(test, "table", 0) or 0
    if not table:
        return []
    order = list(getattr(test, "order", None) or [])
    if not order:
        order = list(range(1, HIGHEST_MULTIPLIER + 1))
    return [(table, m, table * m) for m in order]
