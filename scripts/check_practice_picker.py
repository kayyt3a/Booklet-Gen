"""What the practice picker must still be, measured off the page it serves.

The scope IS the feature. A student who wants "everything in Year 12", "all of
Calculus" or "antidifferentiation and nothing else" has to be able to say so in
three clicks, and has to be able to see, from the shape of the page, which of
those three they just chose. `senior_syllabus.py` nests correctly and
`scripts/check_senior_syllabus.py` proves it. That is not the thing that
breaks. What breaks is the RENDERED page: a template that loops over the flat
`scope_options()` list and prints 79 buttons in a column, all the same size, in
no hierarchy at all. The data underneath is still a tree; the product is not.

So every assertion here is measured off the HTML that came out of Flask and off
the JSON that `/practice/scopes` actually returned. Nothing is measured off the
syllabus module, because the syllabus module is not the thing that regresses.

Four defects this is built to fail against:

  * the tree flattened in the template (no nesting, no ancestors)
  * a subtopic silently dropped from the rendered page while the JSON still
    lists it
  * a judge-only subtopic hidden rather than shown and marked, so the picker
    quietly claims the course is smaller than it is
  * a raw scope id ("methods:strand:Calculus") printed where a label belongs

Run: python scripts/check_practice_picker.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.pop("DATABASE_URL", None)
os.environ["FLASK_SECRET_KEY"] = "k" * 40
os.environ["FOLIO_OUTPUT"] = str(Path(tempfile.mkdtemp(prefix="folio-picker-")))
os.environ["FOLIO_JOB_MODE"] = "manual"

from booklet_gen.practice import fixtures                        # noqa: E402
from booklet_gen.webapp import create_app                        # noqa: E402

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# A signed-in student looking at the picker
# ---------------------------------------------------------------------------

fixtures.fresh_database("folio-picker-")

# Seed a bank BEFORE rendering, and seed only part of the tree. This check used
# to run against an empty bank, which was the whole reason it enforced the
# defect it was written to prevent: with nothing banked, the picker's badge came
# from the syllabus's claim that a topic COULD be filled, so every subtopic in
# both subjects advertised itself as "Stocked" and the check asserted that as
# correct. Twenty Methods topics and fifteen Chemistry topics said Stocked and
# served a blank screen.
#
# Seeding a subset means the page has all three states on it at once, and a
# badge can be measured against what the bank actually holds.
from booklet_gen.practice import store                            # noqa: E402

store.init_practice_db()
STOCKED_LEAVES = ["methods.calculus.antidifferentiation",
                  "methods.calculus.chain-rule",
                  "methods.calculus.product-quotient"]
SEEDED = fixtures.seed_bank(subtopic_ids=STOCKED_LEAVES,
                            templates_per_subtopic=4, items_per_template=15)

EMAIL, PASSWORD = "picker@example.com", "fixture-password-123"
fixtures.make_user(EMAIL, PASSWORD)

app = create_app()
client = app.test_client()


def token(path: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"',
                      client.get(path).data.decode())
    assert match, f"no CSRF token on {path}"
    return match.group(1)


client.post("/login", data={"email": EMAIL, "password": PASSWORD,
                            "csrf_token": token("/login")})

page = client.get("/practice")
html = page.data.decode()


# ---------------------------------------------------------------------------
# Read the tree back out of the HTML
# ---------------------------------------------------------------------------

class PickerReader(HTMLParser):
    """Recover the rendered scope tree: depth, ancestors and visible text.

    Depth is counted in nested `<ul class="scopeChildren">` elements, which is
    the only thing that makes the page look like a tree to a person. A template
    that prints every scope in one list therefore reports depth 0 and no
    ancestors for everything, which is exactly the collapse this check exists
    to catch.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.ul_stack: list[bool] = []
        self.path: list[str] = []
        self.subject = ""
        self.subject_stack: list[str] = []
        self.node: dict | None = None
        self.nodes: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "div" and "scopeSubject" in classes:
            self.subject_stack.append(a.get("data-subject", ""))
            self.subject = self.subject_stack[-1]
        elif tag == "ul":
            nested = "scopeChildren" in classes
            self.ul_stack.append(nested)
            if nested:
                self.depth += 1
        elif tag == "button" and "scopePick" in classes:
            label = a.get("data-label", "")
            while len(self.path) <= self.depth:
                self.path.append("")
            self.path[self.depth] = label
            self.node = {
                "scope": a.get("data-scope", ""),
                "label": label,
                "depth": self.depth,
                "subject": self.subject,
                "disabled": any(k == "disabled" for k, _ in attrs),
                "ancestors": list(self.path[:self.depth]),
                "text": "",
                "hidden_attr": any(k == "hidden" for k, _ in attrs),
            }

    def handle_endtag(self, tag):
        if tag == "ul" and self.ul_stack:
            if self.ul_stack.pop():
                self.depth -= 1
        elif tag == "div" and self.subject_stack:
            # Only pops what this reader pushed; unmatched divs elsewhere on
            # the page leave the stack alone because it is only pushed for a
            # scopeSubject wrapper.
            if len(self.subject_stack) > 0 and self.subject:
                pass
        elif tag == "button" and self.node is not None:
            self.node["text"] = " ".join(self.node["text"].split())
            self.nodes.append(self.node)
            self.node = None

    def handle_data(self, data):
        if self.node is not None:
            self.node["text"] += data


reader = PickerReader()
reader.feed(html)
nodes = reader.nodes
by_scope = {n["scope"]: n for n in nodes}
by_label = {}
for n in nodes:
    by_label.setdefault(n["label"], n)

print("\nThe page renders a tree, not a list")
print("-" * 62)
check(page.status_code == 200,
      "a signed-in student can open the picker at all",
      str(page.status_code))
check(len(nodes) > 100,
      "every scope in both courses reaches the page; a truncated picker hides "
      "topics a student paid to practise", f"{len(nodes)} scope buttons")
depths = sorted({n["depth"] for n in nodes})
check(len(depths) >= 3,
      "the rendered page has at least three levels of nesting, so a whole "
      "year, a strand and a single topic do not all look like the same kind "
      "of choice", f"depths present: {depths}")


# ---------------------------------------------------------------------------
# The three levels the product owner named, by name
# ---------------------------------------------------------------------------

print("\nThe three scopes the product was asked for are all selectable")
print("-" * 62)

WHOLE_YEAR = "methods:year:Year 12"
STRAND = "methods:strand:Calculus"
SUBTOPIC = "methods.calculus.antidifferentiation"

for scope_id, spoken in ((WHOLE_YEAR, "Whole year"),
                         (STRAND, "Calculus"),
                         (SUBTOPIC, "Antidifferentiation")):
    node = by_scope.get(scope_id)
    check(node is not None,
          f'a student can pick "{spoken}"; without it the one feature whose '
          f"promise is the scope cannot deliver that scope", scope_id)
    if node is None:
        continue
    check(not node["disabled"],
          f'"{spoken}" is offered as a live choice and not greyed out',
          node["label"])

year_node = by_scope.get(WHOLE_YEAR)
strand_node = by_scope.get(STRAND)
leaf_node = by_scope.get(SUBTOPIC)

if year_node and strand_node and leaf_node:
    check(leaf_node["depth"] > strand_node["depth"],
          "Antidifferentiation is rendered INSIDE Calculus, not beside it; a "
          "flat picker makes a student read 42 topics to find one",
          f"strand depth {strand_node['depth']}, topic depth {leaf_node['depth']}")
    check("Calculus" in leaf_node["ancestors"],
          "Antidifferentiation's rendered ancestors include Calculus, so the "
          "page itself says which strand it belongs to",
          str(leaf_node["ancestors"]))
    check(year_node["depth"] > 0 and year_node["ancestors"],
          "Whole year (Year 12) hangs off the course above it rather than "
          "floating at the top level", str(year_node["ancestors"]))
    unit = by_scope.get("methods:unit:Unit 3")
    check(unit is not None and unit["depth"] > year_node["depth"],
          "Unit 3 is rendered inside Year 12, which is what makes the year "
          "row worth pressing", str(unit["ancestors"]) if unit else "missing")


# ---------------------------------------------------------------------------
# Labels are for people
# ---------------------------------------------------------------------------

print("\nNothing on the page prints a raw scope id")
print("-" * 62)
bad_colon = [n for n in nodes if ":" in n["text"]]
check(not bad_colon,
      "no visible label contains a colon; a colon in this page's text means a "
      "scope id has leaked into the copy a student reads",
      str([n["text"] for n in bad_colon[:3]]))
bad_id = [n for n in nodes
          if re.search(r"\b(methods|chemistry)[.:]", n["text"])]
check(not bad_id,
      "no visible label contains a raw subtopic id such as "
      '"methods.calculus.antidifferentiation"',
      str([n["text"] for n in bad_id[:3]]))
check(all(n["label"] and n["label"] in n["text"] for n in nodes),
      "every button prints its own label, so no scope is rendered as a blank "
      "or a count with nothing beside it")


# ---------------------------------------------------------------------------
# Judge-only topics: shown, and told the truth about
# ---------------------------------------------------------------------------

print("\nJudge-only topics appear, and are marked as not stocked")
print("-" * 62)
chem = client.get("/practice/scopes?subject=chemistry")
chem_rows = chem.get_json()["scopes"]
check(chem.status_code == 200 and len(chem_rows) > 30,
      "the scope API answers for Chemistry", f"{len(chem_rows)} rows")

# Measured off the API's own count field, not off senior_syllabus: a subtopic
# the bank may never stock is one the API reports as count 0.
judge_only = [r for r in chem_rows
              if r["level"] == "subtopic" and r["count"] == 0]
check(len(judge_only) >= 5,
      "the scope API reports subtopics that can only be marked by a language "
      "model", f"{len(judge_only)} of them")

missing = [r["id"] for r in judge_only if r["id"] not in by_scope]
check(not missing,
      "every judge-only subtopic still appears in the picker; hiding them "
      "makes the page claim the course is smaller than it is",
      str(missing[:3]))
unmarked = [r["id"] for r in judge_only
            if r["id"] in by_scope
            and "not stocked" not in by_scope[r["id"]]["text"].lower()]
check(not unmarked,
      'every judge-only subtopic is marked "Not stocked", so a student does '
      "not press it and get an empty screen", str(unmarked[:3]))
still_clickable = [r["id"] for r in judge_only
                   if r["id"] in by_scope and not by_scope[r["id"]]["disabled"]]
check(not still_clickable,
      "and none of them is clickable, because there is nothing behind them",
      str(still_clickable[:3]))

# The assertion that used to live here read "a subtopic the bank CAN hold is
# never greyed out", measured off the syllabus. That is the defect: what the
# bank COULD hold and what it DOES hold are different facts, and a student
# pressing a green badge cares only about the second. Measured off the bank now.
stock = store.bank_depth()


def is_leaf(scope_id: str) -> bool:
    """A subtopic id, not a group scope. Group ids carry a colon."""
    return ":" not in scope_id and "." in scope_id


live_rows = [sid for sid, node in by_scope.items()
             if is_leaf(sid) and not node["disabled"]]
lying = [sid for sid in live_rows if stock.get(sid, 0) <= 0]
check(not lying,
      "every subtopic offered as a live choice actually has questions behind "
      "it",
      f"{lying[:3]} are clickable with an empty bank, so a student presses a "
      "green badge and gets a blank screen. This is the exact failure that "
      "made a Year 12 close the tab in review")

hidden = [sid for sid, held in stock.items()
          if held > 0 and sid in by_scope and by_scope[sid]["disabled"]]
check(not hidden,
      "and every subtopic that does have questions is offered",
      f"{hidden[:3]} hold questions nobody can reach")

check(len(live_rows) == len(STOCKED_LEAVES),
      f"exactly the {len(STOCKED_LEAVES)} seeded subtopics are live "
      f"({len(live_rows)} were)",
      "the page is offering more or fewer topics than the bank holds")

# The unstocked ones must still be visible, and must say which kind of empty
# they are: waiting for questions, or never getting any.
waiting = html.count(">No questions yet<")
refused = html.count(">Not stocked<")
check(waiting > 0 and refused > 0,
      f"the empty topics are split into {waiting} waiting and {refused} that "
      "will never be stocked",
      "collapsing the two tells a student a topic is coming when nothing will "
      "ever produce it, which they find out by coming back to check")


# ---------------------------------------------------------------------------
# The page and the API describe the same tree
# ---------------------------------------------------------------------------

print("\nThe rendered page and /practice/scopes agree")
print("-" * 62)
methods = client.get("/practice/scopes?subject=methods")
methods_rows = methods.get_json()["scopes"]
api_ids = {r["id"] for r in methods_rows}
page_ids = {n["scope"] for n in nodes if n["subject"] == "methods"}
check(api_ids <= page_ids,
      "every scope the API offers is rendered on the page; a scope only the "
      "API knows about is a scope no student can choose",
      f"missing from page: {sorted(api_ids - page_ids)[:3]}")

year_only = client.get("/practice/scopes?subject=methods&year=Year 12")
year_rows = year_only.get_json()["scopes"]
check(year_only.status_code == 200 and year_rows
      and all(r["parent"] is None or r["parent"] in {x["id"] for x in year_rows}
              for r in year_rows),
      "narrowing to one year still returns a tree whose every parent is "
      "present, so the picker's year filter cannot orphan a row")

check(client.get("/practice/scopes?subject=nonsense").status_code == 404,
      "an unknown course is a 404 and not the first course in the dictionary")


# ---------------------------------------------------------------------------
# The page carries its attribution and its assets
# ---------------------------------------------------------------------------

print("\nAttribution, assets and the policy that allows them")
print("-" * 62)
attribution = methods.get_json()["attribution"]
check(attribution and attribution in html,
      "the picker carries the syllabus attribution verbatim; the course "
      "structure is used under CC BY 4.0 and the licence requires it",
      attribution[:48] + "...")

css = client.get("/static/css/practice.css")
check(css.status_code == 200, "the practice stylesheet is served",
      str(css.status_code))
css_text = css.data.decode()
indent = re.search(r"\.scopeChildren\s*\{[^}]*?(margin-left|padding-left)",
                   css_text, re.S)
check(bool(indent),
      "nested scopes are indented in the stylesheet; without it the HTML "
      "nests but the page still LOOKS like a flat list of 79 buttons")

js = client.get("/static/js/practice.js")
check(js.status_code == 200, "the practice script is served", str(js.status_code))

csp = page.headers.get("Content-Security-Policy", "")
check("script-src 'self'" in csp and "connect-src 'self'" in csp,
      "the existing policy already allows this page's own script and its own "
      "fetches, so shipping practice needs no CSP change", csp[:60] + "...")

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print("  - " + f)
    raise SystemExit(1)
print("All picker checks passed.")
