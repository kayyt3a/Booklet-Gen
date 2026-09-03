"""Checks the ATAR scope tree the practice picker is built on.

The practice engine's whole promise is that a student can say "just
antidifferentiation" and grind only that. Every way that promise can break is
in this file:

  - a scope resolving to nothing, so the arrow key serves a blank screen
  - a scope resolving to too much, so a student who asked for one subtopic is
    fed the whole course
  - a subtopic we cannot verify leaking into the bank, which is how a wrong
    answer reaches a Year 12 three weeks out from an exam
  - duplicate or unstable ids, which orphan every generated question that
    carries them

    PYTHONPATH=. python scripts/check_senior_syllabus.py
"""
import sys

from booklet_gen import senior_syllabus as S

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


print("\nEVERY SUBTOPIC IS WELL FORMED")

seen_ids = set()
for subject, pool in S.SUBJECTS.items():
    key = S.SUBJECT_KEYS[subject]
    assert pool, f"{subject} has no subtopics, so its picker would be empty"
    for sub in pool:
        assert sub.id not in seen_ids, (
            f"duplicate subtopic id {sub.id!r}: two topics would share a bank, "
            "and questions on one would be served for the other")
        seen_ids.add(sub.id)
        assert sub.id.startswith(key + "."), (
            f"{sub.id!r} does not start with the {subject} key {key!r}, so it "
            "cannot be read back to a subject without a lookup")
        assert sub.id.count(".") >= 2, (
            f"{sub.id!r} is not subject.strand.topic shaped")
        assert sub.verification in S.VERIFICATION, (
            f"{sub.id} declares verification {sub.verification!r}, which is "
            "not one of " + ", ".join(S.VERIFICATION))
        assert sub.calculator in ("free", "assumed", "either"), (
            f"{sub.id} declares calculator {sub.calculator!r}")
        assert sub.year, (
            f"{sub.id} sits in {sub.unit!r}, which belongs to no school year, "
            "so 'Whole year' would silently skip it")
        assert sub.summary and len(sub.summary) > 20, (
            f"{sub.id} has no usable summary, so the generator prompt for it "
            "would say nothing about what to write")
        assert "—" not in (sub.name + sub.summary), (
            f"{sub.id} contains an em dash")
ok(f"{len(seen_ids)} subtopics across {len(S.SUBJECTS)} subjects, all unique "
   "and fully specified")

print("\nEVERY SCOPE RESOLVES TO SOMETHING, AND ONLY TO WHAT IT NAMES")

for subject in S.SUBJECTS:
    options = S.scope_options(subject)
    assert options, f"{subject} offers no scopes at all"
    for scope in options:
        ids = S.resolve_scope(scope.id, bankable_only=False)
        assert ids, (
            f"scope {scope.id!r} ({scope.label!r}) resolves to no subtopics, "
            "so a student who picks it gets a blank practice session")
        for sid in ids:
            sub = S.subtopic(sid)
            assert sub is not None, f"{scope.id} resolved to unknown {sid!r}"
            if scope.level == "subtopic":
                assert sid == scope.id, (
                    f"picking the single subtopic {scope.id!r} served "
                    f"{sid!r} as well")
            if scope.level == "strand":
                assert sub.strand == scope.label.split(" (")[0], (
                    f"strand scope {scope.id!r} served {sid!r} from strand "
                    f"{sub.strand!r}")
            if scope.level == "unit":
                assert sub.unit == scope.label, (
                    f"unit scope {scope.id!r} served {sid!r} from {sub.unit}")
ok("every offered scope resolves, and none reaches outside what it names")

print("\nTHE THREE LEVELS THE PRODUCT OWNER ASKED FOR ACTUALLY WORK")

whole = S.resolve_scope("methods:year:Year 12", bankable_only=False)
strand = S.resolve_scope("methods:strand:Calculus", bankable_only=False)
leaf = S.resolve_scope("methods.calculus.antidifferentiation")

assert len(whole) > len(strand) > len(leaf) == 1, (
    "the picker levels do not nest: 'Whole year' must be wider than "
    "'Calculus', which must be wider than 'Antidifferentiation'. Got "
    f"{len(whole)}, {len(strand)}, {len(leaf)}")
assert leaf == ["methods.calculus.antidifferentiation"], leaf
assert all(S.subtopic(i).year == "Year 12" for i in whole), (
    "'Whole year (Year 12)' included a Year 11 subtopic")
assert all(S.subtopic(i).strand == "Calculus" for i in strand), (
    "the Calculus strand included a subtopic from another strand")
# Calculus deliberately spans units: a student revising calculus wants the
# Unit 2 introduction and the Unit 3 integrals in one pile.
assert len({S.subtopic(i).unit for i in strand}) > 1, (
    "the Calculus strand sits in a single unit, so a Year 12 revising "
    "calculus would not be offered the Unit 2 foundations")
ok("'Whole year', 'Calculus' and 'Antidifferentiation' nest correctly")

print("\nAN UNKNOWN SCOPE SERVES NOTHING, NOT EVERYTHING")

for bad in ("", "   ", "physics", "methods:strand:Astrology",
            "methods:unit:Unit 9", "methods:year:Year 7", "methods:strand",
            "methods.calculus.does-not-exist", "chemistry:nonsense:x"):
    assert S.resolve_scope(bad) == [], (
        f"the unknown scope {bad!r} resolved to questions. A stale or "
        "hand-edited scope must serve nothing: the opposite default feeds a "
        "student who asked for one topic the entire course")
ok("unknown, empty and malformed scopes all resolve to nothing")

print("\nTHE BANK ONLY STOCKS WHAT WE CAN ACTUALLY CHECK")

for subject in S.SUBJECTS:
    for sid in S.resolve_scope(S.SUBJECT_KEYS[subject]):
        sub = S.subtopic(sid)
        assert sub.verification != "judge", (
            f"{sid} is verified only by a language model but is stocked in "
            "the bank. A senior student cannot tell a checked answer from a "
            "plausible one, so judge-only topics stay out until something "
            "better stands behind them")
ok("no judge-only subtopic is bankable")

# ...but they are still visible, or the picker would be lying about the course.
judge_only = [s for s in S.CHEMISTRY if s.verification == "judge"]
assert judge_only, (
    "no Chemistry subtopic is marked judge-only, which cannot be right: "
    "explanation questions are half the course, and pretending otherwise "
    "means one has been mislabelled as checkable")
labels = {sc.id for sc in S.scope_options("Chemistry")}
for sub in judge_only:
    assert sub.id in labels, (
        f"{sub.id} is in the course but absent from the picker, so the picker "
        "misrepresents what Chemistry contains")
ok(f"{len(judge_only)} judge-only Chemistry subtopics stay visible but unstocked")

print("\nCHEMISTRY V1 HAS ENOUGH CHECKABLE MATERIAL TO BE WORTH SHIPPING")

stocked = S.resolve_scope("chemistry")
assert len(stocked) >= 20, (
    f"only {len(stocked)} Chemistry subtopics are checkable without a judge. "
    "Below about twenty there is not enough for a student to grind, and the "
    "subject should not be offered at all rather than offered thin")
by_strand = {}
for sid in stocked:
    by_strand.setdefault(S.subtopic(sid).strand, []).append(sid)
assert len(by_strand) >= 6, (
    f"checkable Chemistry clusters into only {len(by_strand)} strands, so the "
    "picker would offer breadth it cannot fill")
ok(f"{len(stocked)} checkable Chemistry subtopics across {len(by_strand)} strands")

print("\nEVERY SCOPE HAS A LABEL A STUDENT CAN READ")

for subject in S.SUBJECTS:
    for scope in S.scope_options(subject):
        label = S.scope_label(scope.id)
        assert label and label != "Practice", (
            f"scope {scope.id!r} has no readable label, so the picker would "
            "show a raw syllabus code")
        assert ":" not in label and "." not in label.rstrip("."), (
            f"scope {scope.id!r} labelled {label!r}, which is an id and not "
            "something a student would recognise")
ok("every scope prints a human label, never an id")

print("\nTHE GENERATOR PROMPT BLOCK NAMES THE SUBTOPIC AND ITS BOUNDS")

block = S.guidance_block(S.subtopic("methods.calculus.antidifferentiation"))
for needed in ("Antidifferentiation", "Unit 2", "Year 11", "Calculus",
               "constant of integration", "Calculator: free"):
    assert needed in block, (
        f"the prompt block for antidifferentiation never mentions {needed!r}, "
        f"so the generator is not told what it is writing:\n{block}")
ok("the prompt block carries the subtopic, unit, year, strand and bounds")

print(f"\nALL {_passed} SENIOR SYLLABUS CHECKS PASSED")
sys.exit(0)
