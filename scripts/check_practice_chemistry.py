"""Checks the chemistry a Year 12 will be marked against.

Methods has SymPy standing behind it. Chemistry has nothing equivalent, so
every chemistry question this product ships is only as trustworthy as the code
in `booklet_gen/practice/chem.py`. That code both generates and verifies, which
is efficient and also dangerous: a routine that is wrong in the same way twice
agrees with itself perfectly and ships the error to every student who picks
that subtopic.

So the values here are computed BY HAND, from the periodic table, and written
into the file as constants. If `chem.py` is rewritten tomorrow it has to agree
with arithmetic done outside it, not with its own earlier opinion.

The refusals matter as much as the answers. A generator that produces a
question it cannot honestly mark is worse than one that produces fewer
questions, because the student has no way to tell the difference. Every
`solve_` routine that cannot reach a defensible answer has to say so rather
than round something into shape.

    PYTHONPATH=. python scripts/check_practice_chemistry.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.practice import chem, verify                  # noqa: E402
from booklet_gen import senior_syllabus as S                   # noqa: E402

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


def close(got, want, tol=0.02) -> bool:
    try:
        return abs(float(got) - float(want)) <= tol
    except (TypeError, ValueError):
        return False


def refuses(fn, payload, label: str) -> bool:
    try:
        got = fn(payload)
    except Exception:                                          # noqa: BLE001
        return True
    print(f"                {label} returned {got!r} instead of refusing")
    return False


print("\n== molar masses, against the periodic table and not against ourselves ==")

# Ca(NO3)2 = 40.08 + 2 x (14.01 + 3 x 16.00) = 40.08 + 2 x 62.01 = 164.10
# CuSO4.5H2O = 63.55 + 32.06 + 4 x 16.00 + 5 x 18.02 = 159.61 + 90.10 = 249.68
# (NH4)2SO4 = 2 x (14.01 + 4 x 1.008) + 32.06 + 4 x 16.00 = 36.08 + 96.06 = 132.14
# C6H12O6 = 6 x 12.011 + 12 x 1.008 + 6 x 16.00 = 72.07 + 12.10 + 96.00 = 180.16
BY_HAND = {
    "Ca(NO3)2": 164.10,
    "CuSO4.5H2O": 249.68,
    "(NH4)2SO4": 132.14,
    "C6H12O6": 180.16,
    "H2SO4": 98.08,
    "NaHCO3": 84.01,
}
for formula, wanted in BY_HAND.items():
    got = chem.molar_mass(formula)
    check(close(got, wanted, 0.05), f"{formula} is {got:.2f} g/mol",
          f"hand calculation says {wanted}, so either the atomic masses or the "
          "formula parser is wrong, and every question on this compound is "
          "wrong with it")

check(refuses(chem.molar_mass, "Xx2O", "an invented element"),
      "an unknown element symbol is refused, not silently skipped",
      "a typo in a formula would otherwise produce a plausible molar mass "
      "that no student could reproduce")

print("\n== balancing, including the ones that need real coefficients ==")

# C3H8 + 5O2 -> 3CO2 + 4H2O, worked by hand.
# KMnO4 + HCl: 2 KMnO4 + 16 HCl -> 2 KCl + 2 MnCl2 + 5 Cl2 + 8 H2O.
BALANCES = {
    "C3H8 + O2 -> CO2 + H2O": [1, 5, 3, 4],
    "KMnO4 + HCl -> KCl + MnCl2 + Cl2 + H2O": [2, 16, 2, 2, 5, 8],
    "Fe + O2 -> Fe2O3": [4, 3, 2],
    "C2H6 + O2 -> CO2 + H2O": [2, 7, 4, 6],
}
for equation, wanted in BALANCES.items():
    got = chem.balance_equation(equation)
    check(got == wanted, f"{equation} balances as {got}",
          f"hand calculation says {wanted}. A wrong marking key on a balancing "
          "question is the most visible error this product can make, because "
          "the student can check it in thirty seconds")

check(refuses(chem.balance_equation, "Na + Cl2 -> NaBr",
              "an equation with an element only on one side"),
      "an unbalanceable equation is refused, not forced",
      "returning a plausible wrong coefficient vector is worse than returning "
      "nothing, because it looks like an answer")

print("\n== stoichiometry worked by hand ==")

# 28.0 g N2 is 1.00 mol; 4.0 g H2 is 1.98 mol. N2 + 3H2 -> 2NH3 needs 3 mol H2
# per mol N2, so 1.00 mol N2 would need 3.00 mol H2 and only 1.98 is present.
# Hydrogen runs out first.
got = chem.solve_limiting_reagent(
    {"equation": "N2 + 3H2 -> 2NH3", "amounts": {"N2": 28.0, "H2": 4.0},
     "want": "limiting"})
check(got == "H2", f"hydrogen is the limiting reagent, got {got!r}",
      "1.00 mol N2 needs 3.00 mol H2 and only 1.98 mol is present")

# The trap: equal masses are not equal moles, and the heavier reagent is not
# automatically the one in excess.
got = chem.solve_limiting_reagent(
    {"equation": "N2 + 3H2 -> 2NH3", "amounts": {"N2": 28.0, "H2": 12.0},
     "want": "limiting"})
check(got == "N2", f"with 12 g of hydrogen the nitrogen limits, got {got!r}",
      "5.95 mol H2 is more than the 3.00 mol required, so nitrogen runs out")

# 25.0 mL of 2.00 M diluted to 250.0 mL: c2 = 2.00 x 25.0 / 250.0 = 0.200 M
got = chem.solve_concentration_dilution(
    {"want": "c2", "c1": 2.00, "v1_ml": 25.0, "v2_ml": 250.0})
check(close(got, 0.200, 0.001), f"a ten-fold dilution of 2.00 M gives {got} M",
      "hand calculation says 0.200 M")

check(refuses(chem.solve_concentration_dilution,
              {"want": "c2", "c1": 2.0, "v1_ml": 25.0, "v2_ml": 0.0},
              "a dilution into zero volume"),
      "a dilution into zero volume is refused",
      "an infinite concentration would otherwise be printed as an answer")

print("\n== acids, where a factor of ten is a whole grade ==")

# 0.010 M strong acid: pH = -log10(0.010) = 2.00
got = chem.solve_ph_strong({"concentration": 0.010, "species": "acid"})
check(close(got, 2.00, 0.01), f"0.010 M strong acid has pH {got}",
      "hand calculation says 2.00")

# 0.010 M strong base: pOH = 2.00, so pH = 12.00
got = chem.solve_ph_strong({"concentration": 0.010, "species": "base"})
check(close(got, 12.00, 0.01), f"0.010 M strong base has pH {got}",
      "hand calculation says 12.00, and returning 2.00 here would be the "
      "classic acid-for-base error shipped to every student")

# Weak acid, Ka = 1.8e-5, c = 0.10: [H+] = sqrt(Ka x c) = sqrt(1.8e-6)
# = 1.342e-3, pH = 2.87
got = chem.solve_ph_weak({"ka": 1.8e-5, "concentration": 0.10})
check(close(got, 2.87, 0.02), f"0.10 M ethanoic acid has pH {got}",
      "hand calculation says 2.87")

print("\n== significant figures, and the question that cannot be marked ==")

check(close(chem.solve_sig_figs({"value": 0.0234567, "figures": 3}), 0.0235,
            1e-6),
      "0.0234567 to three significant figures is 0.0235")

# 12345 to three figures is 12300, and nothing on the page can show whether
# those trailing zeros are significant. The routine must refuse to set it.
check(refuses(chem.solve_sig_figs, {"value": 12345, "figures": 3},
              "a whole number with trailing zeros"),
      "a rounding whose answer is ambiguous is refused, not printed",
      "12345 to three figures is 12300, and the page cannot show whether those "
      "zeros are significant. The question would be unmarkable, and the "
      "student would be marked wrong for reading it correctly")

print("\n== empirical formula, refused rather than rounded into shape ==")

# 40.0% C, 6.7% H, 53.3% O gives 3.33 : 6.65 : 3.33, which is 1 : 2 : 1.
got = chem.solve_empirical_formula(
    {"percents": {"C": 40.0, "H": 6.7, "O": 53.3}})
check(got == "CH2O", f"40.0/6.7/53.3 percent gives {got}",
      "hand calculation says CH2O")

check(refuses(chem.solve_empirical_formula,
              {"percents": {"C": 41.3, "H": 5.9, "O": 52.8}},
              "a composition with no clean ratio"),
      "a composition that does not reduce within six is refused",
      "a ratio of 1.37 rounded to 1.5 is how a generated question ends up with "
      "an answer no chemist would write")

print("\n== the round trip: the printed question states the problem solved ==")

# Gate 2 of admission. A renderer that prints one number while its payload
# carries another gets it wrong once and then ships it hundreds of times, and
# this is the only thing standing between that and a student's screen.
stated = "Calculate the molar mass of Ca(NO3)2."
read = chem.extract("molar_mass", stated)
check(read is not None and read.get("formula") == "Ca(NO3)2",
      f"the formula is read back off the printed question as {read}",
      "the round-trip gate cannot see this question, so every instance of the "
      "family it comes from would be discarded")

agree, why = chem.payloads_agree({"kind": "molar_mass", "formula": "Ca(NO3)2"},
                                 dict(read or {}, kind="molar_mass"))
check(agree, "and it agrees with the stored payload", why)

mismatched, why = chem.payloads_agree(
    {"kind": "molar_mass", "formula": "Ca(NO3)2"},
    {"kind": "molar_mass", "formula": "CaNO3"})
check(not mismatched,
      "a payload naming a different compound from the question is caught",
      "a question printing one formula while its answer is computed from "
      "another would ship to every student in that family")

print("\n== nothing in the bank may rest on a language model's opinion ==")

judge_only = [s.id for s in S.CHEMISTRY if s.verification == "judge"]
leaked = [sid for sid in judge_only if verify.fillable(sid)]
check(not leaked,
      f"none of the {len(judge_only)} judge-only Chemistry subtopics is fillable",
      f"{leaked[:3]} would be stocked with questions no deterministic routine "
      "can settle, and a student grinding three weeks before an exam cannot "
      "tell a checked answer from a plausible one")

for kind in verify.CHEMISTRY_KINDS:
    check(hasattr(chem, f"solve_{kind}") or kind in ("balance_equation",),
          f"the kind {kind!r} has a deterministic solver behind it",
          "a verify kind with no solver would admit whatever it was given")

coverage = verify.coverage()["chemistry"]
print(f"\n                chemistry coverage: {coverage['fillable']} of "
      f"{coverage['bankable']} bankable subtopics have a working checker "
      f"({coverage['subtopics']} in the course)")
check(coverage["fillable"] >= 8,
      f"{coverage['fillable']} chemistry subtopics can actually be filled",
      "below about eight there is not enough for a student to grind and "
      "chemistry should not be offered at all rather than offered thin")

print(f"\n{PASSED}/{TOTAL} behaved as expected")
raise SystemExit(0 if PASSED == TOTAL else 1)
