"""Checks for the weekly times tables routine.

Times tables are the one part of a booklet a parent can mark wrong at a
glance, so the arithmetic has to be exact, and the routine only works if the
term holds together across ten separate booklets: a table set once, tested
once, the week after it was set, and never tested before it was given.

Runs the whole term-plan wiring against stub agents, so it needs no Gemini key.

    PYTHONPATH=. python scripts/check_tables.py
"""
import sys

from booklet_gen.agents import tables as T
from booklet_gen.schemas import BookletData, TablesList, TermPlan, TermWeek

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


class FakeProgram:
    def __init__(self, subjects, pick_subject=False):
        self.subjects = subjects
        self.pick_subject = pick_subject


ACCELERATE = FakeProgram(("Mathematics", "English"), pick_subject=True)
NAPLAN = FakeProgram(("Mathematics", "English"))
SCHOLARSHIP = FakeProgram(("Reasoning",))


def run_term(weeks, year_level="Year 3", revision_from=None):
    """The tables half of BookletPipeline.run_term_plan, with nothing else.

    Mirrors the real loop exactly: test on the carried list, clear it, then set
    a new table unless this is a revision week or the last week.
    """
    plan = TermPlan(weeks=[
        TermWeek(week=n, focus=f"week {n}",
                 revision=bool(revision_from and n >= revision_from))
        for n in range(1, weeks + 1)
    ])
    out, tables_set = [], []
    prev_table, prev_table_week = None, None
    last_week = plan.weeks[-1].week
    for wk in plan.weeks:
        data = BookletData(student_name="Sam", year_level=year_level,
                           subject="Mathematics", program_label="Accelerate",
                           sections=[], recap_questions=[],
                           challenge_questions=[])
        data.tables_test = T.make_test(prev_table, prev_table_week)
        prev_table, prev_table_week = None, None
        if not wk.revision and wk.week != last_week:
            this_table = T.next_list(tables_set, year_level)
            if this_table:
                data.tables_list = this_table
                tables_set.append(this_table.table)
                prev_table, prev_table_week = this_table, wk.week
        out.append((wk, data))
    return out


print("\nTHE ARITHMETIC")

for table in range(1, 13):
    facts = T.facts(table)
    assert len(facts) == 12, table
    assert all(a * b == p for a, b, p in facts), table
    assert [b for _, b, _ in facts] == list(range(1, 13)), table
ok("every table prints 12 facts, in order, with correct products")

test = T.make_test(TablesList(table=7), 3)
questions = T.test_questions(test)
assert len(questions) == 12
assert all(a * b == p for a, b, p in questions)
assert sorted(m for _, m, _ in questions) == list(range(1, 13)), \
    "the shuffled test lost or repeated a multiplier"
ok("a test asks all 12 multipliers exactly once, with correct answers")

assert [m for _, m, _ in questions] != list(range(1, 13)), \
    "the test is in counting order, so it can be answered by skip counting"
ok("the test order is shuffled, not the order it was learned in")

assert T.make_test(TablesList(table=7), 3).order == test.order
ok("the same table and week always shuffle the same way")

orders = {tuple(T.make_test(TablesList(table=t), 1).order) for t in range(2, 13)}
assert len(orders) > 1, "every table shuffles identically"
ok("different tables get different orders")

print("\nA TABLE IS ONLY TESTED IF IT WAS SET")

assert T.make_test(None, None) is None
ok("no previous list means no test, so week 1 opens with none")

assert T.make_test(TablesList(table=0), 2) is None
ok("an empty list produces no test rather than a zero times table")

print("\nACROSS A WHOLE TERM")

term = run_term(10)
set_weeks = {wk.week: d.tables_list.table for wk, d in term if d.tables_list}
test_weeks = {wk.week: d.tables_test.table for wk, d in term if d.tables_test}

assert 1 not in test_weeks, "week 1 tested a table nobody had been given"
ok("the first booklet of term carries no test")

assert len(set(set_weeks.values())) == len(set_weeks), \
    f"a table was set twice: {list(set_weeks.values())}"
ok("no table is set twice in a term")

assert len(set(test_weeks.values())) == len(test_weeks), \
    f"a table was tested twice: {list(test_weeks.values())}"
ok("no table is tested twice in a term")

for week, table in test_weeks.items():
    assert set_weeks.get(week - 1) == table, \
        f"week {week} tested the {table}s, which week {week - 1} did not set"
ok("every test is on the table set in the booklet immediately before it")

for week, table in set_weeks.items():
    if week != max(set_weeks):
        assert test_weeks.get(week + 1) == table, \
            f"the {table}s were set in week {week} and never tested"
ok("every table set is tested the following week")

assert 10 not in set_weeks, \
    "the final week set a table that no later booklet exists to test"
ok("the last week of term sets nothing, because nothing would test it")

for wk, data in term:
    if data.tables_test:
        assert data.tables_test.from_week == wk.week - 1, wk.week
ok("each test names the week its table came from")

print("\nREVISION WEEKS")

rev = run_term(10, revision_from=8)
rev_set = {wk.week: d.tables_list.table for wk, d in rev if d.tables_list}
rev_test = {wk.week: d.tables_test.table for wk, d in rev if d.tables_test}
assert not any(w >= 8 for w in rev_set), \
    f"a revision week set a new table: {sorted(rev_set)}"
ok("a revision week sets no new table")

assert rev_test.get(8) == rev_set.get(7), "week 8 did not test week 7's table"
ok("a revision week still tests the table set before it")

assert 9 not in rev_test and 10 not in rev_test, \
    "a table was tested twice because the carried list was never cleared"
ok("the week after a revision week re-tests nothing")

print("\nWHO GETS THE ROUTINE")

assert T.tables_apply(ACCELERATE, "Mathematics", "Year 3")
assert T.tables_apply(ACCELERATE, "Mathematics", "Year 4")
ok("Accelerate Mathematics in Years 3 and 4 carries tables")

assert not T.tables_apply(ACCELERATE, "English", "Year 3")
ok("an English booklet gets no times table at the back")

assert not T.tables_apply(SCHOLARSHIP, "Reasoning", "Year 3")
ok("a reasoning booklet gets none either")

assert T.tables_apply(NAPLAN, "", "Year 3")
ok("NAPLAN Practice carries them, its numeracy half being mathematics")

for year in ("Year 1", "Year 2", "Year 5", "Year 7", "Year 10"):
    assert not T.tables_apply(ACCELERATE, "Mathematics", year), year
ok("no other year level spends a page a week on this")

print("\nTHE TEACHING ORDER")

assert sorted(T.TEACHING_ORDER) == [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
    T.TEACHING_ORDER
ok("every table from 2 to 12 appears in the order exactly once")

assert T.TEACHING_ORDER[:3] == (2, 5, 10), T.TEACHING_ORDER
ok("the skip-counting anchors come first")

y4 = [T.next_list(list(T.TEACHING_ORDER[3:3 + i]), "Year 4").table
      for i in range(4)]
assert 2 not in y4 and 5 not in y4 and 10 not in y4, y4
ok("a Year 4 term does not open on the tables a Year 3 term already sets")

exhausted = T.next_list(list(T.TEACHING_ORDER), "Year 3")
assert exhausted is None, f"the sequence wrapped around and re-set {exhausted}"
ok("running out of tables sets nothing rather than repeating one")

print("\nWHAT REACHES THE PAGE")

from booklet_gen import formatter  # noqa: E402
from booklet_gen.timing import booklet_timing  # noqa: E402

data = BookletData(student_name="Sam", year_level="Year 3",
                   subject="Mathematics", program_label="Academic Accelerate",
                   sections=[], recap_questions=[], challenge_questions=[])
data.tables_test = T.make_test(TablesList(table=5), 2)
data.tables_list = TablesList(table=10)

counts = dict(formatter.part_counts(data))
assert counts.get("Times Tables Test") == 12, counts
ok("the score table counts the test out of 12")

times = booklet_timing(data)
assert times["tables_minutes"] and times["tables_minutes"] > 0
assert times["total_minutes"] >= times["tables_minutes"]
ok("the test is charged time, and the booklet total includes it")

styles = formatter._make_styles()
test_flow = formatter._tables_test_block(styles, data.tables_test)
key_flow = formatter._tables_key_block(styles, data.tables_test)
list_flow = formatter._tables_list_block(styles, data.tables_list)
assert test_flow and key_flow and list_flow
ok("the test page, the learn page and the answer key all render")


def flow_text(flow):
    """Every string anywhere in a tree of ReportLab flowables.

    `_content` holds a KeepTogether's children and `_cellvalues` a Table's, so
    both have to be descended into, not just read as text.
    """
    out = []
    stack = list(flow)
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        text = getattr(item, "text", None)
        if isinstance(text, str):
            out.append(text)
        for attr in ("_content", "_flowables", "_cellvalues"):
            nested = getattr(item, attr, None)
            if not nested or isinstance(nested, str):
                continue
            for child in nested:
                stack.extend(child if isinstance(child, (list, tuple))
                             else [child])
    return " ".join(out)


answers = [str(5 * m) for m in data.tables_test.order]
key_text = flow_text(key_flow)
assert all(a in key_text for a in answers), key_text[:200]
ok("the answer key carries the product of every question asked")

test_text = flow_text(test_flow)
# Products are the one thing rendered bold, and the question text never is, so
# any bold on the test page is an answer that leaked onto it. Matching the
# numbers themselves would not work: "5 x 9 =" already contains a 5 and a 9.
assert "<b>" not in test_text, \
    f"the test page printed an answer beside a question: {test_text[:160]}"
ok("the test page shows no products, only the questions and a line to write on")

assert "<b>" in flow_text(list_flow), \
    "the learn page hid the products, so there is nothing to memorise from"
ok("the learn page does print the products, which is the difference between them")

assert "5 &times;" in test_text or "5 x" in test_text.lower()
ok("the test page names the table it is testing")

print(f"\nALL {_passed} TIMES TABLES CHECKS PASSED")
sys.exit(0)
