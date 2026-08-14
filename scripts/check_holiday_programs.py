"""Checks the two holiday products exist, are grounded, and stay off the menu.

FolioAI sells term-time booklets. A holiday booklet is worked when school is
NOT running, which removes every support a term booklet quietly leans on: no
teacher, no classroom, no lesson tomorrow, and a parent who may never have been
taught the material. A booklet written for a classroom and sold for the
holidays fails where a customer notices, which is a child stuck on question 3
with nobody in the house able to help.

This file pins the parts that are true whatever shape the product takes:

  THE PROGRAMS EXIST AND THE CLI CAN ADDRESS THEM, so they are testable.
  THEY ARE NOT ON THE CUSTOMER MENU. DEFAULT_WEB_PROGRAMS is the gate that
      keeps an unfinished product off the site, and an unfinished product a
      customer can buy is a refund.
  EACH INHERITS ITS TERM GUIDE rather than copying it, so the acceleration
      rule and the year bands cannot drift into two versions.
  THE ACCELERATION RULE IS TIED TO SUMMER AND ONLY SUMMER. Over summer a
      student returns to the NEXT year, so a booklet a year above the year on
      the request is the year they actually start. On a mid-year break they
      return to the SAME year, so the identical booklet is a year off. The
      guard below is deterministic because the alternative is a prompt: guides
      are CONCATENATED, never overridden, so a short-break supplement saying
      "ignore the rule above" would sit in the same prompt as the rule.
  NAPLAN WORKS AT THE TRUE YEAR, in every break, because the test is sat at a
      fixed year and lifting the content a year prepares a student for a paper
      they will not sit.
  THE CURRICULUM MAP REACHES BOTH, per subject and at the requested year.

Booklet counts, pricing, credits, the web menu surface and delivery are being
decided separately and are deliberately not asserted here.

Runs against fake agents, so it needs no Gemini key and makes no API call.

    PYTHONPATH=. python scripts/check_holiday_programs.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from booklet_gen import curriculum
from booklet_gen.pipeline import BookletPipeline
from booklet_gen.programs import (
    DEFAULT_WEB_PROGRAMS,
    PROGRAMS,
    customer_programs,
    get_program,
    program_external_rag_enabled,
    web_program_keys,
)
from booklet_gen.schemas import Outline, Subtopic, Topic

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label
          + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


HOLIDAY_KEYS = ("accelerate_summer", "naplan_holiday")
ACCELERATION_RULE = "THE ACCELERATION RULE"
ACCELERATE_BASE = "accelerate_practice.txt"


print("\nBoth holiday programs exist and are addressable")
print("-" * 68)
for key in HOLIDAY_KEYS:
    check(key in PROGRAMS, f"{key} is a program")
if failures:
    print("\nnothing else can be checked without the programs")
    raise SystemExit(1)

acc = get_program("accelerate_summer")
nap = get_program("naplan_holiday")

check(acc.key == "accelerate_summer" and nap.key == "naplan_holiday",
      "each entry's key matches the dict it is filed under")

# The label is printed on the cover and would be printed on a menu, so it has
# to read as a product a parent recognises, and it has to say which family it
# belongs to. A season on its own is not a product name.
for p in (acc, nap):
    check(len(p.label.split()) >= 3, f"{p.key} has a label a parent can read",
          p.label)
    check("Program" in p.label,
          f"{p.key} reads as a holiday program on the cover", p.label)
    check(p.blurb.endswith(".") and len(p.blurb.split()) >= 6,
          f"{p.key} has a menu blurb written as a sentence", p.blurb)
    check("—" not in p.label + p.blurb and "–" not in p.label + p.blurb,
          f"{p.key} label and blurb carry no em or en dash")
check(acc.label.startswith("Academic Accelerate") and "Summer" in acc.label,
      "the Accelerate product is named as Academic Accelerate, and as summer",
      acc.label)
check(nap.label.startswith("NAPLAN") and "Holiday" in nap.label,
      "the NAPLAN product is named as NAPLAN, and as a holiday program",
      nap.label)

# main.py builds --program from PROGRAMS, so a new entry is CLI-addressable for
# free. That is the whole reason it can be tested without touching the menu.
main_src = Path("main.py").read_text(encoding="utf-8")
check("choices=list(PROGRAMS)" in main_src,
      "the CLI still offers every program, so holiday runs are testable")


print("\nThey are NOT on the customer menu")
print("-" * 68)
# The gate, not a preference. An unfinished product a customer can buy is a
# refund, and whether these belong on the menu is not this file's call.
for key in HOLIDAY_KEYS:
    check(key not in DEFAULT_WEB_PROGRAMS,
          f"{key} is outside DEFAULT_WEB_PROGRAMS")
    check(key not in web_program_keys(),
          f"{key} is not in the live web program list")
    check(key not in customer_programs(),
          f"{key} is not offered to customers")
check(set(DEFAULT_WEB_PROGRAMS) == {"naplan", "accelerate"},
      "the customer menu is unchanged by this work", str(DEFAULT_WEB_PROGRAMS))

# The allowlist is the seam whoever decides the shape uses later. If it stopped
# working, adding these to the menu would need a code change.
_saved = os.environ.get("FOLIO_WEB_PROGRAM_ALLOWLIST")
os.environ["FOLIO_WEB_PROGRAM_ALLOWLIST"] = "naplan,accelerate,accelerate_summer"
try:
    opened = web_program_keys()
finally:
    if _saved is None:
        os.environ.pop("FOLIO_WEB_PROGRAM_ALLOWLIST", None)
    else:
        os.environ["FOLIO_WEB_PROGRAM_ALLOWLIST"] = _saved
check("accelerate_summer" in opened,
      "the allowlist can open a holiday product without a code change")
check("accelerate_summer" not in web_program_keys(),
      "and the default is restored once the allowlist is cleared")


print("\nShape follows the term product it comes from")
print("-" * 68)
check(acc.pick_subject is True and acc.subjects == (),
      "Accelerate summer asks the parent to pick the subject")
check(nap.pick_subject is False and nap.subjects == ("Mathematics", "English"),
      "NAPLAN holiday runs numeracy and literacy in one booklet",
      str(nap.subjects))
check(nap.subject_display == "Numeracy and Literacy" and acc.subject_display == "",
      "the cover's second line matches the term product")
for p in (acc, nap):
    check(p.use_rag is False,
          f"{p.key} generates clean-room, like the product it comes from")
    check(not program_external_rag_enabled(p),
          f"{p.key} reaches no external content in production")
check(get_program("scholarships").use_rag is True,
      "and the flag still means something: a non-holiday product keeps it")

# describe() is keyed off the program key. A NAPLAN product that fell through
# to the generic branch would ask for "Year 5 English", not literacy practice,
# and would quietly stop being NAPLAN practice.
d_maths = nap.describe("Mathematics", "Year 5", None)
d_eng = nap.describe("English", "Year 5", None)
check("NAPLAN practice" in d_maths and "numeracy" in d_maths,
      "the NAPLAN holiday numeracy request is phrased as NAPLAN practice", d_maths)
check("NAPLAN practice" in d_eng and "literacy" in d_eng,
      "the NAPLAN holiday literacy request is phrased as NAPLAN practice", d_eng)
check(d_maths == get_program("naplan").describe("Mathematics", "Year 5", None),
      "and it matches the term product's request exactly")
check(acc.describe("English", "Year 5", None) == "Year 5 English",
      "Accelerate summer keeps the plain subject request",
      acc.describe("English", "Year 5", None))


print("\nEach guide is inherited from the term guide, not copied")
print("-" * 68)
acc_base = get_program("accelerate").authoring_guidance() or ""
nap_base = get_program("naplan").authoring_guidance() or ""
acc_guide = acc.authoring_guidance() or ""
nap_guide = nap.authoring_guidance() or ""

check(acc.base_guidance_file == ACCELERATE_BASE
      and acc.guidance_file == "accelerate_summer.txt",
      "Accelerate summer names the term guide as its base")
check(nap.base_guidance_file == "naplan_practice.txt"
      and nap.guidance_file == "naplan_holiday.txt",
      "NAPLAN holiday names the term guide as its base")

# Both files wrap the phrase across lines, so count it whitespace-tolerantly
# rather than as a literal, or the count is of line breaks rather than rules.
def rule_mentions(text):
    return len(re.findall(r"THE\s+ACCELERATION\s+RULE", text))


check(rule_mentions(acc_base) == 1,
      "the acceleration rule is written once in the term guide",
      str(rule_mentions(acc_base)))
check(rule_mentions(acc_guide) == 2,
      "the summer guide inherits it and refers to it, rather than restating it",
      str(rule_mentions(acc_guide)))
for band in ("Years 1 and 2", "Years 5 and 6", "Years 9 and 10"):
    check(acc_guide.count(band) == acc_base.count(band) == 2,
          f"the year band {band} exists once per subject, not twice over")

check(acc_guide.startswith(acc_base.split("\n")[0]),
      "the base guide is read FIRST, so the supplement qualifies it")
check(acc_guide.index("ACADEMIC ACCELERATE SUMMER SUPPLEMENT")
      > re.search(r"THE\s+ACCELERATION\s+RULE", acc_guide).start(),
      "the summer rules come after the rules they modify")
check(nap_guide.index("NAPLAN PRACTICE HOLIDAY SUPPLEMENT")
      > nap_guide.index("NON-NEGOTIABLE ORIGINALITY RULES"),
      "the NAPLAN holiday rules come after the originality rules")

# Everything the base guide says is still said. That is the point of composing,
# and it is also why a supplement can never take a rule AWAY (below).
for phrase in ("Never reproduce or closely paraphrase",
               "ignore that material completely",
               "not an official curriculum document"):
    check(" ".join(phrase.split()) in " ".join(acc_guide.split()),
          f"the Accelerate summer guide still carries: {phrase}")
for phrase in ("Never reproduce or closely paraphrase",
               "Years 3, 5, 7 and 9",
               "not an official NAPLAN document"):
    check(" ".join(phrase.split()) in " ".join(nap_guide.split()),
          f"the NAPLAN holiday guide still carries: {phrase}")

for p, guide in ((acc, acc_guide), (nap, nap_guide)):
    check(len(guide.split()) > 1500,
          f"{p.key} has a substantial guide", str(len(guide.split())))
    check("—" not in guide and "–" not in guide,
          f"{p.key} guide contains no em or en dash")


print("\nThe acceleration rule belongs to summer, and to nothing else")
print("-" * 68)
# Over summer a student returns to the NEXT year level, so "one full year above
# the year requested" is the year they actually start: correct, and for the
# first time literally true. On the September break they return to the SAME
# year in Term 4, so the identical booklet is simply a year off, and a parent
# who paid to have their child ready for Term 4 gets work their teacher will
# not touch for another twelve months.
#
# This is a code guard rather than a note in a guide because the guides are
# CONCATENATED, not overridden: a short-break supplement saying "ignore the
# acceleration rule above" would be in the same prompt as the rule, and the
# model would pick one per subtopic. A short-break Accelerate product must be
# a standalone guide with base_guidance_file unset.
inheritors = [k for k, p in PROGRAMS.items()
              if ACCELERATE_BASE in (p.base_guidance_file, p.guidance_file)]
check(set(inheritors) == {"accelerate", "accelerate_summer"},
      "only the term product and the summer product use the acceleration guide",
      str(sorted(inheritors)))
for key in inheritors:
    check(key in ("accelerate", "accelerate_summer"),
          f"{key} inherits the acceleration rule but is not a summer product")
check(not any(k for k in PROGRAMS
              if ACCELERATE_BASE in (PROGRAMS[k].base_guidance_file,
                                     PROGRAMS[k].guidance_file)
              and k not in ("accelerate", "accelerate_summer")),
      "no mid-year break product inherits a rule that would pitch it a year off")

# The mechanism the guard exists because of. Composition adds; it cannot remove.
check(rule_mentions(acc_guide) >= 1,
      "a supplement loaded after the base guide cannot suppress it")


print("\nThe supplements say what a holiday changes")
print("-" * 68)
acc_only = Path("booklet_gen/guidance/accelerate_summer.txt").read_text(
    encoding="utf-8")
nap_only = Path("booklet_gen/guidance/naplan_holiday.txt").read_text(
    encoding="utf-8")

for name, text in (("summer", acc_only), ("naplan", nap_only)):
    flat = " ".join(text.split())
    # No teacher, no classroom, one adult who may not know the material.
    check("no teacher" in flat.lower() and "no classroom" in flat.lower(),
          f"the {name} supplement says there is no teacher and no classroom")
    check(bool(re.search(r"parent, who may never have been taught", flat)),
          f"the {name} supplement says the parent may not know the material")
    # Self-contained: the consequence, and the whole point.
    check("EVERYTHING THE STUDENT NEEDS IS ON THE PAGE" in text,
          f"the {name} supplement demands a self-contained page")
    check("Never refer the student to a teacher" in flat,
          f"the {name} supplement forbids sending the student to a teacher")
    check("video or an app" in flat,
          f"the {name} supplement forbids sending the student to look it up")
    check("partner, a group, a class discussion" in flat,
          f"the {name} supplement forbids work that needs another person")
    check("pencil" in flat and "ruler" in flat,
          f"the {name} supplement bounds the equipment a home must have")
    # The answer key is the only feedback in the house.
    check("THE ANSWER KEY IS READ BY A PARENT" in text,
          f"the {name} supplement writes the key for a parent to mark from")
    check("complete working" in flat,
          f"the {name} supplement requires complete working, not just answers")
    # Shape is decided elsewhere and must not be pre-empted in a prompt.
    for word in ("week 1", "5 booklets", "five booklets", "$"):
        check(word.lower() not in flat.lower(),
              f"the {name} supplement does not decide the product shape ({word})")

acc_flat = " ".join(acc_only.split())
check("just FINISHED" in acc_flat,
      "the summer supplement says the requested year is the year just finished")
check("about to begin" in acc_flat or "walk into on the first day back" in acc_flat,
      "and that a year above it is the year the student actually starts")
check(rule_mentions(acc_only) >= 1 or "one full year above" in acc_only,
      "the summer supplement keeps the acceleration rule in force")
check("stricter" in acc_only and "taught before" in acc_flat,
      "and says the missing teacher makes taught-before-asked stricter, not looser")
check("summer" in acc_flat.lower() and "September" not in acc_flat,
      "the summer supplement is about summer and does not claim other breaks")

nap_flat = " ".join(nap_only.split())
check("TRUE year level" in nap_only or "TRUE YEAR LEVEL" in nap_only,
      "the NAPLAN supplement holds the booklet at the true year level")
check("acceleration rule used by Academic Accelerate does not apply" in nap_flat,
      "and refuses the acceleration rule by name")
check("one full year above" not in nap_guide and rule_mentions(nap_guide) == 0,
      "no part of the NAPLAN holiday guide tells it to work a year above")
check("Pitching below the year" in nap_flat,
      "and under-pitching is refused too, since that is the commoner failure")


print("\nNAPLAN holiday keeps the year supplements")
print("-" * 68)
base_words = len(nap_guide.split())
for year, marker in (("Year 3", "10 000"), ("Year 5", "thousandths"),
                     ("Year 7", "prime factorisation"), ("Year 9", "Pythagoras")):
    supplemented = nap.authoring_guidance(year) or ""
    n = year.split()[1]
    check(f"YEAR {n} SUPPLEMENT" in supplemented,
          f"{year} loads its NAPLAN year supplement")
    check(marker in supplemented, f"{year} is pitched at the right level")
    check(len(supplemented.split()) > base_words + 300,
          f"{year} adds substantial year-specific guidance")
    check(supplemented.index("NAPLAN PRACTICE HOLIDAY SUPPLEMENT")
          < supplemented.index(f"YEAR {n} SUPPLEMENT"),
          f"{year}: the year supplement is read last, after the holiday rules")
check(len((nap.authoring_guidance("Year 4") or "").split()) == base_words,
      "a year NAPLAN is not sat in falls back rather than erroring")
check(acc.year_guidance_pattern is None,
      "Accelerate summer has no year supplement to load, and does not invent one")


print("\nThe curriculum map reaches a holiday booklet, per subject and year")
print("-" * 68)


class FakeParser:
    """Records the guidance the outline parser was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def parse(self, description, guidance=None):
        self.calls.append((description, guidance or ""))
        return Outline(subject="Mathematics", year_level="Year 0",
                       topics=[Topic(name="Number",
                                     subtopics=[Subtopic(name="Fractions")])])


def run(program_key, year_level, subject=None):
    """Run run_program with every agent faked out. Returns
    (booklet, parser calls, generation calls)."""
    pipe = BookletPipeline.__new__(BookletPipeline)
    parser = FakeParser()
    pipe._parser = parser
    generated: list[dict] = []

    def fake_generate(outline, seen, authoring_guidance=None, use_rag=True):
        generated.append({"subject": outline.subject,
                          "year_level": outline.year_level,
                          "guidance": authoring_guidance or "",
                          "use_rag": use_rag})
        return [], [], []

    pipe._generate_from_outline = fake_generate
    pipe._build_recap = lambda *a, **k: []
    pipe._build_challenge = lambda *a, **k: []
    booklet = pipe.run_program(program_key, year_level, "Sam", subject=subject)
    return booklet, parser.calls, generated


booklet, calls, generated = run("naplan_holiday", "Year 5")
check(len(calls) == 2 and len(generated) == 2,
      "a NAPLAN holiday booklet runs both subject engines", str(len(generated)))
by_subject = {g["subject"]: g for g in generated}
check(set(by_subject) == {"Mathematics", "English"},
      "one run each for numeracy and literacy", str(sorted(by_subject)))

for subj in ("Mathematics", "English"):
    guidance = by_subject[subj]["guidance"]
    check(f"WHAT YEAR 5 {subj.upper()} ACTUALLY COVERS" in guidance,
          f"the {subj} half carries the Year 5 curriculum")
    other = "English" if subj == "Mathematics" else "Mathematics"
    check(f"WHAT YEAR 5 {other.upper()} ACTUALLY COVERS" not in guidance,
          f"and not the {other} curriculum as well")
    check("NAPLAN PRACTICE HOLIDAY SUPPLEMENT" in guidance,
          f"the {subj} half carries the holiday supplement")
    check(guidance.index("NAPLAN PRACTICE HOLIDAY SUPPLEMENT")
          < guidance.index("WHAT YEAR 5"),
          f"the {subj} half reads the product guide before the curriculum")
    check(by_subject[subj]["use_rag"] is False,
          f"the {subj} half generates with retrieval off")

# The parser is the agent that chooses the subtopics, so it is the one that has
# to know what the year contains. A booklet whose subtopics are a year low is
# already wrong before a single question is written.
check(len(calls) == 2 and all("WHAT YEAR 5" in g for _, g in calls),
      "the outline parser is handed the curriculum too")
check(booklet.program_label == nap.label,
      "the cover carries the holiday product's label", str(booklet.program_label))

# It has to move with the year, or it is Year 5 hard-coded.
_, _, gen9 = run("naplan_holiday", "Year 9")
g9 = {g["subject"]: g["guidance"] for g in gen9}["Mathematics"]
check("WHAT YEAR 9 MATHEMATICS ACTUALLY COVERS" in g9
      and "WHAT YEAR 5 MATHEMATICS" not in g9,
      "a Year 9 request carries Year 9 content, not Year 5")
check("scientific notation" in g9,
      "and the Year 9 content is actually Year 9")
check("YEAR 9 SUPPLEMENT" in g9,
      "the Year 9 NAPLAN supplement rides along with it")
# The map writes it as PYTHAGORAS' THEOREM, so compare case-insensitively.
_b9 = curriculum.guidance_block("Mathematics", "Year 9").lower()
check("pythagoras" in _b9 and "(year 8)" in _b9,
      "the block names Year 8 work so a Year 9 booklet can refuse it")

booklet_a, calls_a, gen_a = run("accelerate_summer", "Year 7", subject="Maths")
check(len(gen_a) == 1 and gen_a[0]["subject"] == "Mathematics",
      "Academic Accelerate summer runs only the subject the parent picked",
      str([g["subject"] for g in gen_a]))
ga = gen_a[0]["guidance"]
check("WHAT YEAR 7 MATHEMATICS ACTUALLY COVERS" in ga,
      "the picked subject carries the Year 7 curriculum")
check(rule_mentions(ga) >= 1,
      "and the acceleration rule reaches generation with it")
check(re.search(r"THE\s+ACCELERATION\s+RULE", ga).start() < ga.index("WHAT YEAR 7"),
      "the product's own rule is read first, then the curriculum qualifies it")
check(gen_a[0]["use_rag"] is False,
      "Accelerate summer generates with retrieval off")
check(booklet_a.subject == "Mathematics" and booklet_a.program_label == acc.label,
      "the cover names the picked subject and the holiday product")

# A subject the parent may not pick must fail loudly rather than generate.
try:
    run("accelerate_summer", "Year 7")
except ValueError as exc:
    check("subject" in str(exc).lower(),
          "Accelerate summer refuses a request with no subject", str(exc))
else:
    check(False, "Accelerate summer refuses a request with no subject",
          "it generated something instead")


if failures:
    print(f"\n{len(failures)} FAILED")
    for f in failures:
        print("   -", f)
    raise SystemExit(1)
print("\nALL HOLIDAY PROGRAM CHECKS PASSED")
sys.exit(0)
