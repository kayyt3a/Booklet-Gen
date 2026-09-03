"""Deterministic chemistry: solve a question, and read it back off the page.

Every routine here comes in a pair. `solve_<kind>(check)` computes the answer
from structured data without ever looking at the printed question, and
`extract_<kind>(text)` reads the structured data back out of the printed
question without ever looking at the parameters it was rendered from. The
admission gate in `verify.py` runs both and requires them to agree.

WHY THE SECOND HALF EXISTS
--------------------------
Recomputation on its own proves the answer is arithmetically right. It does not
prove the student is being shown the problem that was solved. A renderer that
prints "35.0 g" while its parameters say 3.50 gets that wrong once and then
ships it in every one of the sixty instances the template expands to, and every
one of them is stamped verified because the checker and the renderer read the
same number. Reading the problem back off the rendered string is the only test
that can catch it, so the extractors are deliberately strict: an extractor that
cannot find what it needs returns None, and None means the instance is thrown
away rather than admitted on the strength of gate one alone.

That strictness is why the prompt in `prompts/practice_template_chemistry.txt`
dictates the exact sentence shape for each kind. A prompt change that breaks a
shape empties that subtopic's bank, which is loud. The alternative failure, a
question that says one thing and is marked against another, is silent.

BALANCING
---------
`balance_equation` is linear algebra, not search. Build the element-by-species
matrix (one extra row for charge), take its nullspace over the rationals, clear
denominators, reduce by the gcd, and require every coefficient to be a positive
integer. An equation that cannot be balanced has no such vector and is refused,
rather than being handed the nearest plausible-looking one.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

import sympy as sp

from .elements import ATOMIC_MASS, atomic_mass

# Values a WACE data sheet prints. R in J per mole per kelvin, which with
# pressure in kPa gives volume directly in litres.
GAS_CONSTANT = 8.314
KW_25C = 1.0e-14


class ChemError(ValueError):
    """A chemistry input that cannot be read or cannot be solved.

    One exception type for both, because the caller does the same thing with
    either: discard the instance. Distinguishing them would invite a caller to
    "recover" from one of them, and there is no safe recovery from a question
    whose chemistry does not work.
    """


# ---------------------------------------------------------------- formulae

_STATE_SYMBOL = re.compile(r"\((?:s|l|g|aq)\)", re.I)
_CHARGE = re.compile(r"\^\s*(\d*)\s*([+-])$")
_HYDRATE_SPLIT = re.compile(r"[.·∙]")

# What a formula is allowed to look like when pulled out of prose. Kept tight
# so a stray English word is never mistaken for a species.
FORMULA_TEXT = r"[A-Z][A-Za-z0-9()]*(?:[.·][0-9]*[A-Z][A-Za-z0-9()]*)*"


@dataclass(frozen=True)
class Species:
    """One chemical species: what it is made of, and what charge it carries."""

    formula: str
    counts: dict[str, int]
    charge: int = 0

    @property
    def molar_mass(self) -> float:
        return sum(atomic_mass(el) * n for el, n in self.counts.items())


def _read_symbol(text: str, i: int) -> tuple[str, int]:
    j = i + 1
    while j < len(text) and text[j].islower():
        j += 1
    while j > i:
        symbol = text[i:j]
        if symbol in ATOMIC_MASS:
            return symbol, j
        j -= 1
    raise ChemError(f"{text[i:i + 2]!r} in {text!r} is not an element symbol")


def _read_int(text: str, i: int) -> tuple[int, int]:
    j = i
    while j < len(text) and text[j].isdigit():
        j += 1
    if j == i:
        return 1, i
    return int(text[i:j]), j


def _count_part(part: str) -> Counter:
    """Atom counts for one formula unit, parentheses nested to any depth."""
    stack: list[Counter] = [Counter()]
    i = 0
    while i < len(part):
        ch = part[i]
        if ch == "(":
            stack.append(Counter())
            i += 1
        elif ch == ")":
            if len(stack) == 1:
                raise ChemError(f"unbalanced brackets in {part!r}")
            group = stack.pop()
            multiplier, i = _read_int(part, i + 1)
            for element, n in group.items():
                stack[-1][element] += n * multiplier
        elif ch.isupper():
            symbol, i = _read_symbol(part, i)
            n, i = _read_int(part, i)
            stack[-1][symbol] += n
        elif ch.isspace():
            i += 1
        else:
            raise ChemError(f"{ch!r} has no meaning inside the formula {part!r}")
    if len(stack) != 1:
        raise ChemError(f"unbalanced brackets in {part!r}")
    if not stack[0]:
        raise ChemError(f"{part!r} names no elements")
    return stack[0]


def parse_species(formula: str) -> Species:
    """A formula string as atom counts and charge.

    Understands nested brackets, hydrate dots (`CuSO4.5H2O`), state symbols,
    and an explicit charge suffix (`SO4^2-`). A formula it cannot read raises,
    because guessing at a formula is how a molar mass silently loses an atom.
    """
    raw = (formula or "").strip()
    if not raw:
        raise ChemError("empty formula")
    body = _STATE_SYMBOL.sub("", raw).replace(" ", "")
    charge = 0
    m = _CHARGE.search(body)
    if m:
        size = int(m.group(1) or "1")
        charge = size if m.group(2) == "+" else -size
        body = body[: m.start()]
    total = Counter()
    for part in _HYDRATE_SPLIT.split(body):
        if not part:
            raise ChemError(f"{raw!r} has an empty piece either side of a dot")
        multiplier, offset = _read_int(part, 0)
        for element, n in _count_part(part[offset:]).items():
            total[element] += n * multiplier
    return Species(raw, dict(total), charge)


def molar_mass(formula: str) -> float:
    """Molar mass in g/mol, from the standard atomic weights in elements.py."""
    return parse_species(formula).molar_mass


# ---------------------------------------------------------------- equations

_ARROW = re.compile(r"-->|->|=>|<->|<=>|→|⇌|↔")


def split_equation(equation: str) -> tuple[list[str], list[str]]:
    """('C3H8', 'O2'), ('CO2', 'H2O') from 'C3H8 + O2 -> CO2 + H2O'."""
    parts = _ARROW.split(equation or "")
    if len(parts) != 2:
        raise ChemError(
            f"{equation!r} does not have exactly one reaction arrow, so which "
            "side is which cannot be decided")
    sides = []
    for side in parts:
        species = [s.strip() for s in side.split("+") if s.strip()]
        if not species:
            raise ChemError(f"one side of {equation!r} is empty")
        sides.append(species)
    return sides[0], sides[1]


_LEADING_COEFFICIENT = re.compile(r"^(\d+)\s*(?=[A-Z(])")


def strip_coefficient(term: str) -> tuple[int, str]:
    """('2', 'H2O') -> (2, 'H2O'). A bare formula is one mole of itself."""
    m = _LEADING_COEFFICIENT.match(term.strip())
    if not m:
        return 1, term.strip()
    return int(m.group(1)), term.strip()[m.end():].strip()


def balance_equation(equation: str) -> list[int]:
    """The smallest whole-number coefficients that balance the equation.

    Refuses in three separate ways, all of them deliberate:

    - the nullspace is empty, so no set of coefficients conserves the atoms;
    - the nullspace has more than one dimension, so the equation is ambiguous
      and several unrelated balancings exist. Picking one would be an opinion;
    - a coefficient comes out zero, negative or fractional after clearing
      denominators, which means a species does not take part in the reaction as
      written.

    Returning "cannot balance" is the point. A search-based balancer that keeps
    trying until something fits will eventually emit a plausible wrong vector
    for an equation that is simply not a reaction.
    """
    left, right = split_equation(equation)
    terms = left + right
    species = []
    for term in terms:
        coefficient, formula = strip_coefficient(term)
        if coefficient != 1:
            raise ChemError(
                f"{term!r} already carries a coefficient. Balance the skeleton "
                "equation, not a partly balanced one, or the answer being "
                "checked is not the answer being asked for")
        species.append(parse_species(formula))

    elements = sorted({el for s in species for el in s.counts})
    rows: list[list[int]] = []
    for element in elements:
        rows.append([
            s.counts.get(element, 0) * (1 if i < len(left) else -1)
            for i, s in enumerate(species)
        ])
    if any(s.charge for s in species):
        rows.append([s.charge * (1 if i < len(left) else -1)
                     for i, s in enumerate(species)])

    null = sp.Matrix(rows).nullspace()
    if not null:
        raise ChemError(f"{equation!r} cannot be balanced: no set of "
                        "coefficients conserves every element")
    if len(null) > 1:
        raise ChemError(
            f"{equation!r} has {len(null)} independent balancings, so the "
            "coefficients are not determined by the equation alone")

    vector = null[0]
    denominators = [sp.Rational(v).q for v in vector]
    scale = sp.ilcm(*denominators) if len(denominators) > 1 else denominators[0]
    whole = [int(sp.Rational(v) * scale) for v in vector]
    common = math.gcd(*[abs(v) for v in whole]) if len(whole) > 1 else abs(whole[0])
    if not common:
        raise ChemError(f"{equation!r} balances only with every coefficient zero")
    whole = [v // common for v in whole]
    if all(v < 0 for v in whole):
        whole = [-v for v in whole]
    if any(v <= 0 for v in whole):
        raise ChemError(
            f"{equation!r} cannot be balanced with positive whole numbers: the "
            f"only solution is {whole}, which asks a species to take part "
            "backwards")
    return whole


def balanced_coefficients(equation: str) -> tuple[dict[str, int], dict[str, int]]:
    """(reactant coefficients, product coefficients) for a balanced equation.

    The equation is read as written and then confirmed against the nullspace,
    so an equation that a template claims is balanced but is not gets refused
    here rather than quietly used as the basis of a stoichiometry answer.
    """
    left, right = split_equation(equation)
    written = [strip_coefficient(t) for t in left + right]
    skeleton = " -> ".join([
        " + ".join(f for _, f in written[:len(left)]),
        " + ".join(f for _, f in written[len(left):]),
    ])
    required = balance_equation(skeleton)
    got = [c for c, _ in written]
    if got != required:
        raise ChemError(
            f"{equation!r} is written with coefficients {got} but balances as "
            f"{required}")
    reactants = {f: c for c, f in written[:len(left)]}
    products = {f: c for c, f in written[len(left):]}
    if len(reactants) != len(left) or len(products) != len(right):
        raise ChemError(f"{equation!r} names the same species twice on one side")
    return reactants, products


# ------------------------------------------------------------------ numbers

_SCIENTIFIC = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(?:x|×|\*)\s*10\s*\^?\s*\(?\s*(-?\d+)\s*\)?", re.I)
_PLAIN_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")
# Prose an answer commonly leads with. Stripped so "pH = 2.60" and "2.60" are
# the same answer, which they are.
_ANSWER_LEAD = re.compile(
    r"^\s*(?:the\s+)?(?:answer|pH|pOH|Kc|K|c|n|m|V|concentration|mass|moles?|"
    r"molar mass|volume|percentage)\s*(?:is)?\s*[:=]?\s*", re.I)


def parse_number(text: str) -> float | None:
    """The number an answer states, or None when it states none.

    Understands `1.86 x 10^-3` as well as `1.86e-3`, because a Chemistry answer
    is written the first way and only ever typed the second way.
    """
    if text is None:
        return None
    cleaned = _ANSWER_LEAD.sub("", str(text).strip()).replace(",", "")
    m = _SCIENTIFIC.search(cleaned)
    if m:
        try:
            return float(m.group(1)) * (10.0 ** int(m.group(2)))
        except (ValueError, OverflowError):
            return None
    m = _PLAIN_NUMBER.search(cleaned)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def round_to(value: float, *, dp: int | None = None, sf: int | None = None) -> float:
    """Round to decimal places or to significant figures, whichever was asked."""
    if dp is not None:
        return round(value, int(dp))
    if sf is not None:
        if value == 0:
            return 0.0
        digits = int(sf) - 1 - math.floor(math.log10(abs(value)))
        return round(value, digits)
    raise ChemError("a rounding rule must be stated: give either dp or sf")


def rounding_of(check: dict) -> tuple[int | None, int | None]:
    dp = check.get("dp")
    sf = check.get("sf")
    if dp is None and sf is None:
        raise ChemError(
            "the check states no rounding, so a printed answer could not be "
            "distinguished from a wrong one by any tolerance")
    return (None if dp is None else int(dp), None if sf is None else int(sf))


def numbers_agree(printed: float, exact: float, *, dp: int | None,
                  sf: int | None) -> bool:
    """Whether a printed answer is a correct rounding of the exact value.

    Compares against the half-unit in the last place asked for rather than
    against `round()`, so a value sitting exactly on a tie is accepted whichever
    way the author broke it, while an answer wrong in that last place is still
    refused.
    """
    if dp is not None:
        tolerance = 0.5 * (10.0 ** -dp)
    else:
        if exact == 0:
            return abs(printed) < 1e-12
        magnitude = math.floor(math.log10(abs(exact)))
        tolerance = 0.5 * (10.0 ** (magnitude - int(sf) + 1))
    return abs(printed - exact) <= tolerance * (1 + 1e-9)


# ------------------------------------------------------------------ solvers
#
# Each takes the structured `check` payload and returns the answer, computed
# from nothing but that payload.

def solve_molar_mass(check: dict) -> float:
    return molar_mass(check["formula"])


def solve_percent_composition(check: dict) -> float:
    species = parse_species(check["formula"])
    element = check["element"]
    if element not in species.counts:
        raise ChemError(f"{element} does not appear in {check['formula']}")
    return 100.0 * atomic_mass(element) * species.counts[element] / species.molar_mass


def solve_empirical_formula(check: dict) -> str:
    """The empirical formula from percentage (or mass) composition.

    Ratios are cleared to whole numbers by trying multipliers 1 to 6, which is
    the range a school question is ever set in. Anything that does not land
    within a tight tolerance is refused rather than rounded into shape: a
    ratio of 1.37 that gets called 1.5 is how a generated question ends up with
    an answer no chemist would write.
    """
    amounts = check.get("percents") or check.get("masses")
    if not amounts:
        raise ChemError("no percentage or mass composition given")
    moles = {el: float(v) / atomic_mass(el) for el, v in amounts.items()}
    smallest = min(moles.values())
    if smallest <= 0:
        raise ChemError("a composition entry is zero or negative")
    ratios = {el: n / smallest for el, n in moles.items()}
    for multiplier in range(1, 7):
        scaled = {el: r * multiplier for el, r in ratios.items()}
        if all(abs(v - round(v)) < 0.06 * multiplier for v in scaled.values()):
            counts = {el: int(round(v)) for el, v in scaled.items()}
            if min(counts.values()) < 1:
                continue
            if math.gcd(*counts.values()) != 1 and len(counts) > 1:
                continue
            return "".join(
                f"{el}{'' if counts[el] == 1 else counts[el]}"
                for el in sorted(counts, key=_hill_order)
            )
    raise ChemError(
        f"the composition {amounts} does not reduce to a whole-number ratio "
        "within six, so no empirical formula can be stated honestly")


def _hill_order(element: str) -> tuple[int, str]:
    """Carbon, then hydrogen, then everything else alphabetically."""
    if element == "C":
        return (0, "")
    if element == "H":
        return (1, "")
    return (2, element)


def solve_moles_mass(check: dict) -> float:
    mass_per_mole = molar_mass(check["formula"])
    want = check.get("want", "moles")
    if want == "moles":
        return float(check["mass"]) / mass_per_mole
    if want == "mass":
        return float(check["moles"]) * mass_per_mole
    raise ChemError(f"moles_mass cannot answer want={want!r}")


def solve_limiting_reagent(check: dict):
    """Either which reagent runs out first, or how much product it allows."""
    reactants, products = balanced_coefficients(check["equation"])
    masses = check["amounts"]
    if set(masses) != set(reactants):
        raise ChemError(
            f"amounts are given for {sorted(masses)} but the equation's "
            f"reactants are {sorted(reactants)}")
    extents = {}
    for formula, grams in masses.items():
        extents[formula] = (float(grams) / molar_mass(formula)) / reactants[formula]
    limiting = min(extents, key=lambda f: extents[f])
    ordered = sorted(extents.values())
    if len(ordered) > 1 and ordered[1] - ordered[0] < 1e-9 * max(1.0, ordered[1]):
        raise ChemError(
            "two reagents run out at the same time, so there is no limiting "
            "reagent to identify")
    want = check.get("want", "limiting")
    if want == "limiting":
        return limiting
    if want == "mass":
        product = check["product"]
        if product not in products:
            raise ChemError(f"{product} is not a product of {check['equation']}")
        return extents[limiting] * products[product] * molar_mass(product)
    raise ChemError(f"limiting_reagent cannot answer want={want!r}")


def solve_concentration_dilution(check: dict) -> float:
    want = check.get("want")
    if want == "c":
        volume = float(check["volume_ml"]) / 1000.0
        if volume <= 0:
            raise ChemError("a solution cannot have zero volume")
        return float(check["moles"]) / volume
    if want == "c2":
        v2 = float(check["v2_ml"])
        if v2 <= 0:
            raise ChemError("a diluted solution cannot have zero volume")
        return float(check["c1"]) * float(check["v1_ml"]) / v2
    if want == "n":
        return float(check["c"]) * float(check["volume_ml"]) / 1000.0
    raise ChemError(f"concentration_dilution cannot answer want={want!r}")


def solve_titration(check: dict) -> float:
    """Concentration of the analyte from the titre and the mole ratio.

    The mole ratio is taken from the question rather than inferred from an
    equation, because inferring it means deciding which species is the titrant,
    and a checker that decides that for itself will confidently answer a
    diprotic titration as though it were 1:1.
    """
    v_analyte = float(check["v_analyte_ml"])
    if v_analyte <= 0:
        raise ChemError("the analyte volume must be positive")
    moles_titrant = float(check["c_titrant"]) * float(check["v_titrant_ml"]) / 1000.0
    moles_analyte = moles_titrant * float(check["ratio_analyte"]) / float(
        check["ratio_titrant"])
    return moles_analyte / (v_analyte / 1000.0)


def solve_ph_strong(check: dict) -> float:
    concentration = float(check["concentration"])
    per_mole = int(check.get("per_mole", 1))
    if concentration <= 0 or per_mole < 1:
        raise ChemError("concentration and ions per mole must both be positive")
    ion = concentration * per_mole
    if check.get("species", "acid") == "acid":
        return -math.log10(ion)
    return 14.0 - (-math.log10(ion))


def solve_ph_weak(check: dict) -> float:
    """pH of a weak acid under the stated negligible-ionisation assumption.

    Refuses when the assumption does not hold. A question that tells a student
    to assume ionisation is negligible, and then has an answer where it is not,
    is teaching the approximation and marking the exact answer.
    """
    concentration = float(check["concentration"])
    ka = float(check["ka"])
    if concentration <= 0 or ka <= 0:
        raise ChemError("concentration and Ka must both be positive")
    hydrogen = math.sqrt(ka * concentration)
    if hydrogen / concentration > 0.05:
        raise ChemError(
            f"the acid is {100 * hydrogen / concentration:.1f} percent ionised, "
            "so the negligible-ionisation assumption the question states is "
            "not valid and the stated answer would be wrong")
    return -math.log10(hydrogen)


def solve_equilibrium_kc(check: dict) -> float:
    reactants, products = balanced_coefficients(check["equation"])
    concentrations = {k: float(v) for k, v in check["concentrations"].items()}
    missing = (set(reactants) | set(products)) - set(concentrations)
    if missing:
        raise ChemError(f"no equilibrium concentration given for {sorted(missing)}")
    if any(v <= 0 for v in concentrations.values()):
        raise ChemError("an equilibrium concentration is zero or negative")
    top = math.prod(concentrations[f] ** n for f, n in products.items())
    bottom = math.prod(concentrations[f] ** n for f, n in reactants.items())
    return top / bottom


def solve_gas_laws(check: dict) -> float:
    want = check.get("want")
    if want == "v2":
        return (float(check["p1"]) * float(check["v1"]) * float(check["t2"])
                / (float(check["t1"]) * float(check["p2"])))
    if want == "p2":
        return (float(check["p1"]) * float(check["v1"]) * float(check["t2"])
                / (float(check["t1"]) * float(check["v2"])))
    if want == "v":
        return (float(check["n"]) * GAS_CONSTANT * float(check["t_k"])
                / float(check["p_kpa"]))
    if want == "n":
        return (float(check["p_kpa"]) * float(check["v_l"])
                / (GAS_CONSTANT * float(check["t_k"])))
    raise ChemError(f"gas_laws cannot answer want={want!r}")


def significant_figures_ambiguous(value: str, figures: int) -> bool:
    """Whether rounding this value to this many figures cannot be written down.

    "4500 to 2 significant figures" is 4500, which is exactly the string that
    started the question and states no precision at all. A student cannot tell
    the answer from the question, and marking it is guesswork. Such a question
    must never be generated, so the checker refuses to bless one.
    """
    text = str(value).strip()
    if "." in text or "e" in text.lower():
        return False
    digits = text.lstrip("+-").lstrip("0")
    return len(digits) > int(figures) and digits.rstrip("0") != digits


def solve_sig_figs(check: dict) -> float:
    value = check["value"]
    figures = int(check["figures"])
    if figures < 1:
        raise ChemError("a value cannot be stated to fewer than one figure")
    if significant_figures_ambiguous(value, figures):
        raise ChemError(
            f"rounding {value!r} to {figures} significant figures produces a "
            "whole number with trailing zeros, which cannot show how many "
            "figures it carries. The question is unmarkable as written")
    return round_to(float(value), sf=figures)


# --------------------------------------------------------------- extractors
#
# Each reads the printed question and rebuilds the check payload. None means
# "this text does not state the problem in a form I can read", which the
# admission gate treats as a failure, never as a pass.

_DP = re.compile(r"(\d+)\s*decimal places?", re.I)
_SF = re.compile(r"(\d+)\s*significant figures?", re.I)


def _rounding_from_text(text: str) -> dict:
    m = _DP.search(text)
    if m:
        return {"dp": int(m.group(1))}
    m = _SF.search(text)
    if m:
        return {"sf": int(m.group(1))}
    return {}


def extract_molar_mass(text: str) -> dict | None:
    m = re.search(rf"molar mass of\s+({FORMULA_TEXT})", text, re.I)
    if not m:
        return None
    return {"formula": m.group(1).rstrip("."), **_rounding_from_text(text)}


def extract_percent_composition(text: str) -> dict | None:
    m = re.search(
        rf"percentage by mass of\s+([A-Z][a-z]?)\s+in\s+({FORMULA_TEXT})",
        text)
    if not m:
        return None
    return {"element": m.group(1), "formula": m.group(2).rstrip("."),
            **_rounding_from_text(text)}


def extract_empirical_formula(text: str) -> dict | None:
    found = re.findall(r"(\d+(?:\.\d+)?)\s*%\s*(?:by mass\s*)?([A-Z][a-z]?)\b", text)
    if len(found) < 2:
        return None
    percents = {element: float(value) for value, element in found}
    if len(percents) != len(found):
        return None
    return {"percents": percents}


def extract_balance_equation(text: str) -> dict | None:
    m = re.search(r"Balance the equation:\s*(.+?)\s*(?:\.\s|\.$|$)", text)
    if not m:
        return None
    return {"equation": m.group(1).strip()}


def extract_moles_mass(text: str) -> dict | None:
    m = re.search(rf"number of moles in\s+(\d+(?:\.\d+)?)\s*g of\s+({FORMULA_TEXT})",
                  text, re.I)
    if m:
        return {"want": "moles", "mass": float(m.group(1)),
                "formula": m.group(2).rstrip("."), **_rounding_from_text(text)}
    m = re.search(rf"mass of\s+(\d+(?:\.\d+)?)\s*mol of\s+({FORMULA_TEXT})",
                  text, re.I)
    if m:
        return {"want": "mass", "moles": float(m.group(1)),
                "formula": m.group(2).rstrip("."), **_rounding_from_text(text)}
    return None


def extract_limiting_reagent(text: str) -> dict | None:
    equation = re.search(r"For the reaction\s+(.+?)\s*,", text)
    if not equation:
        return None
    amounts = re.findall(rf"(\d+(?:\.\d+)?)\s*g of\s+({FORMULA_TEXT})", text)
    if len(amounts) < 2:
        return None
    check: dict = {
        "equation": equation.group(1).strip(),
        "amounts": {formula.rstrip("."): float(grams) for grams, formula in amounts},
    }
    product = re.search(rf"mass of\s+({FORMULA_TEXT})\s+(?:that is\s+)?(?:formed|produced)",
                        text, re.I)
    if product:
        check["want"] = "mass"
        check["product"] = product.group(1).rstrip(".")
        check.update(_rounding_from_text(text))
    elif re.search(r"limiting reagent", text, re.I):
        check["want"] = "limiting"
    else:
        return None
    if len(check["amounts"]) != len(amounts):
        return None
    return check


def extract_concentration_dilution(text: str) -> dict | None:
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*mL of\s+(\d+(?:\.\d+)?)\s*mol/L .*?diluted to\s+"
        r"(\d+(?:\.\d+)?)\s*mL", text, re.I | re.S)
    if m:
        return {"want": "c2", "v1_ml": float(m.group(1)), "c1": float(m.group(2)),
                "v2_ml": float(m.group(3)), **_rounding_from_text(text)}
    m = re.search(
        rf"(\d+(?:\.\d+)?)\s*mol of\s+{FORMULA_TEXT}\s+(?:is\s+)?dissolved to make\s+"
        rf"(\d+(?:\.\d+)?)\s*mL", text, re.I)
    if m:
        return {"want": "c", "moles": float(m.group(1)),
                "volume_ml": float(m.group(2)), **_rounding_from_text(text)}
    return None


def extract_titration(text: str) -> dict | None:
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*mL of .*?required\s+(\d+(?:\.\d+)?)\s*mL of\s+"
        r"(\d+(?:\.\d+)?)\s*mol/L", text, re.I | re.S)
    if not m:
        return None
    ratio = re.search(r"(\d+)\s*:\s*(\d+)\s*mole ratio", text, re.I)
    if not ratio:
        return None
    return {
        "v_analyte_ml": float(m.group(1)),
        "v_titrant_ml": float(m.group(2)),
        "c_titrant": float(m.group(3)),
        "ratio_titrant": int(ratio.group(1)),
        "ratio_analyte": int(ratio.group(2)),
        **_rounding_from_text(text),
    }


def extract_ph_strong(text: str) -> dict | None:
    m = re.search(r"(\d+(?:\.\d+)?(?:\s*[x×]\s*10\s*\^?\s*-?\d+)?)\s*mol/L",
                  text, re.I)
    if not m:
        return None
    concentration = parse_number(m.group(1))
    released = re.search(
        r"releases\s+(\d+)\s+moles?\s+of\s+(hydrogen|hydroxide)\s+ions", text, re.I)
    if concentration is None or not released:
        return None
    return {
        "concentration": concentration,
        "per_mole": int(released.group(1)),
        "species": "acid" if released.group(2).lower() == "hydrogen" else "base",
        **_rounding_from_text(text),
    }


def extract_ph_weak(text: str) -> dict | None:
    conc = re.search(r"(\d+(?:\.\d+)?)\s*mol/L", text, re.I)
    ka = re.search(r"Ka\s*=\s*([0-9.]+(?:\s*[x×]\s*10\s*\^?\s*-?\d+|[eE][+-]?\d+))",
                   text)
    if not conc or not ka:
        return None
    value = parse_number(ka.group(1))
    if value is None:
        return None
    if "negligible" not in text.lower():
        # The solver answers the approximate form. A question that never told
        # the student to approximate is a different question.
        return None
    return {"concentration": float(conc.group(1)), "ka": value,
            **_rounding_from_text(text)}


def extract_equilibrium_kc(text: str) -> dict | None:
    equation = re.search(r"For the equilibrium\s+(.+?)\s*,", text)
    if not equation:
        return None
    found = re.findall(r"\[\s*([A-Za-z0-9()]+)\s*\]\s*=\s*([0-9.]+)", text)
    if not found:
        return None
    concentrations = {f: float(v) for f, v in found}
    if len(concentrations) != len(found):
        return None
    return {"equation": equation.group(1).strip(),
            "concentrations": concentrations, **_rounding_from_text(text)}


def extract_gas_laws(text: str) -> dict | None:
    m = re.search(
        r"occupies\s+(\d+(?:\.\d+)?)\s*L at\s+(\d+(?:\.\d+)?)\s*kPa and\s+"
        r"(\d+(?:\.\d+)?)\s*K.*?volume at\s+(\d+(?:\.\d+)?)\s*kPa and\s+"
        r"(\d+(?:\.\d+)?)\s*K", text, re.I | re.S)
    if m:
        return {"want": "v2", "v1": float(m.group(1)), "p1": float(m.group(2)),
                "t1": float(m.group(3)), "p2": float(m.group(4)),
                "t2": float(m.group(5)), **_rounding_from_text(text)}
    m = re.search(
        r"volume occupied by\s+(\d+(?:\.\d+)?)\s*mol of an ideal gas at\s+"
        r"(\d+(?:\.\d+)?)\s*kPa and\s+(\d+(?:\.\d+)?)\s*K", text, re.I)
    if m:
        return {"want": "v", "n": float(m.group(1)), "p_kpa": float(m.group(2)),
                "t_k": float(m.group(3)), **_rounding_from_text(text)}
    return None


def extract_sig_figs(text: str) -> dict | None:
    m = re.search(r"Express\s+([0-9.]+)\s+correct to\s+(\d+)\s+significant figures",
                  text, re.I)
    if not m:
        return None
    return {"value": m.group(1), "figures": int(m.group(2))}


# ------------------------------------------------------------------ registry

# Kinds whose answer is a string (a formula, a species name), not a number.
TEXT_ANSWER_KINDS = frozenset({"empirical_formula", "balance_equation"})
LIMITING_TEXT_WANT = "limiting"

SOLVERS = {
    "molar_mass": solve_molar_mass,
    "percent_composition": solve_percent_composition,
    "empirical_formula": solve_empirical_formula,
    "balance_equation": None,          # answered by balance_equation() itself
    "moles_mass": solve_moles_mass,
    "limiting_reagent": solve_limiting_reagent,
    "concentration_dilution": solve_concentration_dilution,
    "titration": solve_titration,
    "ph_strong": solve_ph_strong,
    "ph_weak": solve_ph_weak,
    "equilibrium_kc": solve_equilibrium_kc,
    "gas_laws": solve_gas_laws,
    "sig_figs": solve_sig_figs,
}

EXTRACTORS = {
    "molar_mass": extract_molar_mass,
    "percent_composition": extract_percent_composition,
    "empirical_formula": extract_empirical_formula,
    "balance_equation": extract_balance_equation,
    "moles_mass": extract_moles_mass,
    "limiting_reagent": extract_limiting_reagent,
    "concentration_dilution": extract_concentration_dilution,
    "titration": extract_titration,
    "ph_strong": extract_ph_strong,
    "ph_weak": extract_ph_weak,
    "equilibrium_kc": extract_equilibrium_kc,
    "gas_laws": extract_gas_laws,
    "sig_figs": extract_sig_figs,
}

# Every kind this module can settle without a language model. `verify.py`
# imports this rather than re-listing them, so a kind can never be dispatched
# to a solver that does not exist.
KINDS = tuple(sorted(EXTRACTORS))


def solve(kind: str, check: dict):
    """The answer to a check payload, computed from the payload alone."""
    if kind == "balance_equation":
        return balance_equation(check["equation"])
    solver = SOLVERS.get(kind)
    if solver is None:
        raise ChemError(f"{kind!r} is not a chemistry verify kind")
    return solver(check)


def extract(kind: str, text: str) -> dict | None:
    """The check payload a printed question states, or None if it states none."""
    reader = EXTRACTORS.get(kind)
    if reader is None:
        raise ChemError(f"{kind!r} is not a chemistry verify kind")
    try:
        return reader(text or "")
    except (ChemError, ValueError):
        return None


def _normalise(value):
    """Compare extracted against stored without tripping over 2 versus 2.0."""
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 9)
    return str(value).strip()


def payloads_agree(stored: dict, extracted: dict) -> tuple[bool, str]:
    """Whether the question states exactly the problem that was solved.

    Every key of the stored payload must appear in what was read back off the
    page, with the same value. Extra keys in the extracted payload are allowed
    (a question may state its rounding twice); missing or different ones are
    not, because that is precisely the case where the student is reading one
    problem and being marked against another.
    """
    for key, value in stored.items():
        if key not in extracted:
            return False, f"the question never states {key!r}"
        if _normalise(value) != _normalise(extracted[key]):
            return False, (f"the question states {key}={extracted[key]!r} but the "
                           f"answer was computed from {key}={value!r}")
    return True, "the printed question states the problem that was solved"
