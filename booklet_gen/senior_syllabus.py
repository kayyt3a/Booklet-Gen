"""What ATAR Mathematics Methods and Chemistry actually contain, as a tree.

The practice engine lets a student grind questions inside a scope they choose,
and the scope is the whole point of the feature: "give me everything", "give me
calculus", "give me antidifferentiation and nothing else". None of that is
possible if a subject is a bare string in a prompt, which is all the booklet
generator ever needed. This module is the missing structure.

THE SHAPE
---------
Every subject is a flat list of `Subtopic` records. Each one knows three things
that a student might select on:

  unit    "Unit 3". Units pair into school years: Units 1 and 2 are Year 11,
          Units 3 and 4 are Year 12. This is how "Whole year" resolves.
  strand  "Calculus". Strands deliberately CROSS units, because a student
          revising calculus wants the Unit 2 introduction and the Unit 3
          integrals in the same pile, and no student has ever thought of
          those as separate subjects.
  id      "methods.calculus.antidifferentiation". A stable slug. Question bank
          rows are stamped with it, so renaming a display name later does not
          orphan a thousand generated questions.

A scope is any of those levels, and `resolve_scope` turns whichever the student
picked into the set of subtopic ids to draw questions from.

VERIFICATION, AND WHY IT IS RECORDED HERE
-----------------------------------------
Each subtopic declares how an answer to it can be CHECKED:

  "symbolic"  a computer algebra system settles it. Derivatives, integrals,
              algebraic solves. This is the strongest verification we have.
  "numeric"   the answer is a number or a formula reachable by deterministic
              arithmetic we can write ourselves: molar masses, stoichiometric
              ratios, pH, balancing an equation. Strong, but the checker has to
              be built per topic rather than handed to SymPy.
  "judge"     only a language model can assess it. "Explain in terms of
              intermolecular forces." This is the weakest link in the whole
              product.

The practice bank is graded senior material, where a confident wrong answer
does real damage three weeks before an ATAR exam. So v1 fills the bank ONLY
from subtopics we can check without a judge, and this field is what enforces
that. The judge-only subtopics are still listed, because they are really in the
course and the picker should not lie about what the course contains, but
`bankable()` excludes them until there is something better than an LLM opinion
standing behind them.

SOURCE AND RIGHTS
-----------------
The unit and topic structure of WACE Mathematics Methods and WACE Chemistry
derives from the Western Australian senior secondary syllabuses published by
the School Curriculum and Standards Authority (SCSA), which in turn derive from
the Australian Curriculum senior secondary courses.

Those SCSA documents are licensed for copying only for NON-COMMERCIAL purposes
in educational institutions. Folio is sold. So no SCSA wording is reproduced
here: what follows is a factual index of which topic sits in which unit,
written fresh, in the same way `curriculum.py` handles Years 1 to 10. Content
derived from the Australian Curriculum is available under CC BY 4.0, which does
permit commercial use with attribution, and the attribution is ATTRIBUTION
below.

Do not paste syllabus prose into this file. Which topic belongs to which unit
is a fact about the course; the Authority's sentences are theirs.
"""
from __future__ import annotations

from dataclasses import dataclass

ATTRIBUTION = (
    "Course structure is based on the Western Australian ATAR syllabuses "
    "(School Curriculum and Standards Authority), which incorporate material "
    "from the Australian Curriculum, used under CC BY 4.0."
)

# Verification strengths, strongest first. Ordered, because callers ask
# "is this at least as strong as numeric".
VERIFICATION = ("symbolic", "numeric", "judge")

UNITS_BY_YEAR = {
    "Year 11": ("Unit 1", "Unit 2"),
    "Year 12": ("Unit 3", "Unit 4"),
}


@dataclass(frozen=True)
class Subtopic:
    """One selectable leaf of the syllabus tree."""

    id: str
    name: str
    unit: str
    strand: str
    verification: str
    summary: str
    calculator: str = "either"  # "free", "assumed" or "either"

    @property
    def year(self) -> str:
        for year, units in UNITS_BY_YEAR.items():
            if self.unit in units:
                return year
        return ""

    @property
    def subject_key(self) -> str:
        return self.id.split(".", 1)[0]


def _s(sid, name, unit, strand, verification, summary, calculator="either"):
    return Subtopic(sid, name, unit, strand, verification, summary, calculator)


# ---------------------------------------------------------------------------
# Mathematics Methods
# ---------------------------------------------------------------------------
# Strand names are what a student would call them, not what the syllabus
# document calls them. "Calculus" spans Units 2, 3 and 4 on purpose.

METHODS: list[Subtopic] = [
    # Unit 1
    _s("methods.functions.linear", "Linear relationships", "Unit 1",
       "Functions and graphs", "symbolic",
       "gradient, intercepts, equations of lines, simultaneous linear "
       "equations, linear modelling", "free"),
    _s("methods.functions.quadratic", "Quadratic relationships", "Unit 1",
       "Functions and graphs", "symbolic",
       "factorising, completing the square, the quadratic formula, the "
       "discriminant, turning points, quadratic modelling", "free"),
    _s("methods.functions.inverse-proportion", "Inverse proportion", "Unit 1",
       "Functions and graphs", "symbolic",
       "the graph of k/x, asymptotes, direct and inverse variation"),
    _s("methods.functions.powers-polynomials", "Powers and polynomials",
       "Unit 1", "Functions and graphs", "symbolic",
       "cubic and higher polynomials, factor and remainder theorems, sketching "
       "from factored form"),
    _s("methods.functions.transformations", "Graphs and transformations",
       "Unit 1", "Functions and graphs", "symbolic",
       "translations, dilations and reflections of a graph, and reading them "
       "back off an equation"),
    _s("methods.functions.notation", "Functions and function notation",
       "Unit 1", "Functions and graphs", "symbolic",
       "domain and range, composite functions, inverse functions, piecewise "
       "definitions", "free"),

    _s("methods.trigonometry.sine-cosine-rules", "Sine and cosine rules",
       "Unit 1", "Trigonometry", "numeric",
       "non-right triangles, the ambiguous case, area of a triangle",
       "assumed"),
    _s("methods.trigonometry.radians", "Radian measure", "Unit 1",
       "Trigonometry", "symbolic",
       "converting degrees and radians, arc length, sector area, exact values",
       "free"),
    _s("methods.trigonometry.functions", "Trigonometric functions", "Unit 1",
       "Trigonometry", "symbolic",
       "graphs of sine, cosine and tangent, amplitude, period and phase, "
       "solving trigonometric equations, periodic modelling"),

    _s("methods.probability.combinations", "Counting and combinations",
       "Unit 1", "Counting and probability", "numeric",
       "the multiplication principle, permutations, combinations, Pascal's "
       "triangle"),
    _s("methods.probability.events-sets", "Events, sets and probability",
       "Unit 1", "Counting and probability", "numeric",
       "sample spaces, Venn diagrams, the addition rule, complementary events"),
    _s("methods.probability.conditional", "Conditional probability and "
       "independence", "Unit 1", "Counting and probability", "numeric",
       "tree diagrams, two-way tables, the multiplication rule, testing "
       "independence"),

    # Unit 2
    _s("methods.exponentials.index-laws", "Index laws", "Unit 2",
       "Exponentials and logarithms", "symbolic",
       "integer, negative and fractional indices, simplifying index "
       "expressions, scientific notation", "free"),
    _s("methods.exponentials.functions", "Exponential functions", "Unit 2",
       "Exponentials and logarithms", "symbolic",
       "graphs of a^x and e^x, growth and decay models, solving exponential "
       "equations"),

    _s("methods.sequences.arithmetic", "Arithmetic sequences and series",
       "Unit 2", "Sequences and series", "symbolic",
       "the nth term, the sum of n terms, arithmetic modelling"),
    _s("methods.sequences.geometric", "Geometric sequences and series",
       "Unit 2", "Sequences and series", "symbolic",
       "the nth term, the sum of n terms, infinite geometric series, "
       "compound growth"),
    _s("methods.sequences.recursion", "Recursion and modelling", "Unit 2",
       "Sequences and series", "numeric",
       "recursive definitions, first-order linear recurrence, long-run "
       "behaviour"),

    _s("methods.calculus.rates-of-change", "Rates of change", "Unit 2",
       "Calculus", "symbolic",
       "average and instantaneous rate, the gradient of a chord approaching a "
       "tangent"),
    _s("methods.calculus.first-principles", "Differentiation from first "
       "principles", "Unit 2", "Calculus", "symbolic",
       "the limit definition of the derivative, applied to polynomials",
       "free"),
    _s("methods.calculus.polynomial-derivatives", "Derivatives of polynomials",
       "Unit 2", "Calculus", "symbolic",
       "the power rule, sums and constant multiples, derivatives of negative "
       "and fractional powers", "free"),
    _s("methods.calculus.tangents-stationary", "Tangents and stationary "
       "points", "Unit 2", "Calculus", "symbolic",
       "equations of tangents and normals, locating and classifying stationary "
       "points, increasing and decreasing intervals"),
    _s("methods.calculus.antidifferentiation", "Antidifferentiation", "Unit 2",
       "Calculus", "symbolic",
       "reversing the power rule, the constant of integration, recovering a "
       "function from its derivative and a point", "free"),

    # Unit 3
    _s("methods.calculus.exponential-derivatives", "Derivatives of exponential "
       "functions", "Unit 3", "Calculus", "symbolic",
       "the derivative of e^x and e^(kx), exponential rates of change", "free"),
    _s("methods.calculus.trig-derivatives", "Derivatives of trigonometric "
       "functions", "Unit 3", "Calculus", "symbolic",
       "the derivatives of sin, cos and tan, and their use in periodic models",
       "free"),
    _s("methods.calculus.chain-rule", "The chain rule", "Unit 3", "Calculus",
       "symbolic",
       "differentiating composite functions, including nested exponentials and "
       "trigonometric functions", "free"),
    _s("methods.calculus.product-quotient", "Product and quotient rules",
       "Unit 3", "Calculus", "symbolic",
       "differentiating products and quotients, and combining them with the "
       "chain rule", "free"),
    _s("methods.calculus.second-derivative", "The second derivative", "Unit 3",
       "Calculus", "symbolic",
       "concavity, points of inflection, the second derivative test"),
    _s("methods.calculus.optimisation", "Optimisation", "Unit 3", "Calculus",
       "symbolic",
       "forming a function from a described situation and maximising or "
       "minimising it, including with a constraint"),
    _s("methods.calculus.definite-integrals", "Definite integrals and the "
       "fundamental theorem", "Unit 3", "Calculus", "symbolic",
       "evaluating definite integrals, the fundamental theorem of calculus, "
       "properties of the integral", "free"),
    _s("methods.calculus.area", "Area under and between curves", "Unit 3",
       "Calculus", "symbolic",
       "signed area, area between two curves, setting up the integral from a "
       "shaded region"),
    _s("methods.calculus.motion", "Straight-line motion", "Unit 3", "Calculus",
       "symbolic",
       "displacement, velocity and acceleration as derivatives and integrals, "
       "distance travelled versus displacement"),
    _s("methods.calculus.increments", "The increments formula", "Unit 3",
       "Calculus", "symbolic",
       "approximating a small change using the derivative", "free"),

    _s("methods.statistics.discrete", "Discrete random variables", "Unit 3",
       "Statistics and distributions", "numeric",
       "probability distributions, expected value, variance and standard "
       "deviation"),
    _s("methods.statistics.bernoulli", "Bernoulli distributions", "Unit 3",
       "Statistics and distributions", "numeric",
       "single-trial probability, mean and variance of a Bernoulli variable"),
    _s("methods.statistics.binomial", "Binomial distributions", "Unit 3",
       "Statistics and distributions", "numeric",
       "binomial probabilities, mean and variance, when a situation is "
       "binomial and when it is not", "assumed"),

    # Unit 4
    _s("methods.exponentials.log-laws", "Logarithm laws and equations",
       "Unit 4", "Exponentials and logarithms", "symbolic",
       "the logarithm laws, change of base, solving equations involving "
       "logarithms", "free"),
    _s("methods.exponentials.log-graphs", "Graphs of logarithmic functions",
       "Unit 4", "Exponentials and logarithms", "symbolic",
       "the shape of ln(x), transformations, domain and asymptotes"),
    _s("methods.calculus.log-derivatives", "Calculus of the logarithmic "
       "function", "Unit 4", "Calculus", "symbolic",
       "the derivative of ln(x), integrals giving a logarithm, applications",
       "free"),

    _s("methods.statistics.continuous", "Continuous random variables",
       "Unit 4", "Statistics and distributions", "symbolic",
       "probability density functions, finding an unknown constant, "
       "probabilities as integrals, mean and variance by integration"),
    _s("methods.statistics.normal", "The normal distribution", "Unit 4",
       "Statistics and distributions", "numeric",
       "standardising, normal probabilities, inverse normal, the empirical "
       "rule", "assumed"),
    _s("methods.statistics.sample-proportions", "Random sampling and sample "
       "proportions", "Unit 4", "Statistics and distributions", "numeric",
       "the distribution of a sample proportion, its mean and standard "
       "deviation, the normal approximation", "assumed"),
    _s("methods.statistics.confidence-intervals", "Confidence intervals for "
       "proportions", "Unit 4", "Statistics and distributions", "numeric",
       "margin of error, confidence intervals, the effect of sample size and "
       "confidence level", "assumed"),
]


# ---------------------------------------------------------------------------
# Chemistry
# ---------------------------------------------------------------------------
# Chemistry has no SymPy. Everything marked "numeric" here is arithmetic we can
# check ourselves given a purpose-built checker: molar masses from a formula,
# stoichiometric ratios, balancing by linear algebra over the element matrix,
# pH, cell potentials. Everything marked "judge" is an explanation, and stays
# out of the bank in v1.

CHEMISTRY: list[Subtopic] = [
    # Unit 1
    _s("chemistry.atomic.structure", "Atomic structure and isotopes", "Unit 1",
       "Atomic structure and the periodic table", "numeric",
       "protons, neutrons and electrons, isotopes, relative atomic mass from "
       "isotopic abundance"),
    _s("chemistry.atomic.electron-configuration", "Electron configuration",
       "Unit 1", "Atomic structure and the periodic table", "numeric",
       "writing configurations for atoms and ions, subshells, valence "
       "electrons"),
    _s("chemistry.atomic.periodic-trends", "Periodic trends", "Unit 1",
       "Atomic structure and the periodic table", "judge",
       "atomic radius, ionisation energy and electronegativity across a period "
       "and down a group, and why"),

    _s("chemistry.bonding.ionic", "Ionic bonding and formulae", "Unit 1",
       "Bonding and structure", "numeric",
       "predicting ion charges, writing ionic formulae, naming ionic "
       "compounds, lattice properties"),
    _s("chemistry.bonding.covalent", "Covalent bonding and molecular shape",
       "Unit 1", "Bonding and structure", "judge",
       "Lewis structures, bond polarity, VSEPR shapes, molecular polarity"),
    _s("chemistry.bonding.metallic", "Metallic bonding", "Unit 1",
       "Bonding and structure", "judge",
       "the metallic model and the properties it explains, alloys"),
    _s("chemistry.bonding.intermolecular", "Intermolecular forces", "Unit 1",
       "Bonding and structure", "judge",
       "dispersion forces, dipole-dipole, hydrogen bonding, and their effect "
       "on physical properties"),

    _s("chemistry.stoichiometry.equations", "Writing and balancing equations",
       "Unit 1", "Stoichiometry and the mole", "numeric",
       "balancing chemical equations, states of matter, ionic and net ionic "
       "equations"),
    _s("chemistry.stoichiometry.mole", "The mole and molar mass", "Unit 1",
       "Stoichiometry and the mole", "numeric",
       "molar mass from a formula, converting between mass, moles and number "
       "of particles, percentage composition, empirical formula"),
    _s("chemistry.stoichiometry.mass-calculations", "Mass and mole "
       "calculations", "Unit 1", "Stoichiometry and the mole", "numeric",
       "mass to mass calculations through a balanced equation"),
    _s("chemistry.stoichiometry.limiting", "Limiting reagent and yield",
       "Unit 1", "Stoichiometry and the mole", "numeric",
       "identifying the limiting reagent, theoretical and percentage yield, "
       "excess reagent remaining"),

    _s("chemistry.energy.enthalpy", "Enthalpy and calorimetry", "Unit 1",
       "Energy and thermochemistry", "numeric",
       "exothermic and endothermic change, enthalpy change from calorimetry, "
       "molar enthalpy of combustion"),
    _s("chemistry.energy.bond-energy", "Bond energy and energy profiles",
       "Unit 1", "Energy and thermochemistry", "numeric",
       "estimating enthalpy change from bond energies, reading an energy "
       "profile diagram"),

    # Unit 2
    _s("chemistry.gases.laws", "Gas laws and the ideal gas equation", "Unit 2",
       "Gases and solutions", "numeric",
       "pressure, volume and temperature relationships, the ideal gas "
       "equation, molar volume at standard conditions"),
    _s("chemistry.solutions.concentration", "Concentration and dilution",
       "Unit 2", "Gases and solutions", "numeric",
       "molarity, mass per volume, parts per million, dilution calculations"),
    _s("chemistry.solutions.solubility", "Solubility and precipitation",
       "Unit 2", "Gases and solutions", "numeric",
       "solubility rules, predicting precipitates, writing net ionic "
       "equations for precipitation"),
    _s("chemistry.solutions.volumetric", "Volumetric analysis", "Unit 2",
       "Gases and solutions", "numeric",
       "titration calculations, standard solutions, choosing glassware"),

    _s("chemistry.rates.collision-theory", "Reaction rates and collision "
       "theory", "Unit 2", "Reaction rates", "judge",
       "the effect of concentration, temperature, surface area and catalysts, "
       "explained by collision theory"),

    # Unit 3
    _s("chemistry.equilibrium.constant", "The equilibrium constant", "Unit 3",
       "Equilibrium", "numeric",
       "writing the expression for K, calculating K from equilibrium "
       "concentrations, the meaning of the size of K"),
    _s("chemistry.equilibrium.calculations", "Equilibrium calculations",
       "Unit 3", "Equilibrium", "numeric",
       "finding equilibrium concentrations from initial amounts and K, "
       "reaction quotient and direction of shift"),
    _s("chemistry.equilibrium.le-chatelier", "Le Chatelier's principle",
       "Unit 3", "Equilibrium", "judge",
       "predicting and explaining the effect of concentration, pressure, "
       "volume and temperature changes"),

    _s("chemistry.acids.conjugate-pairs", "Bronsted-Lowry acids and bases",
       "Unit 3", "Acids and bases", "numeric",
       "identifying conjugate acid-base pairs, amphiprotic species, writing "
       "proton transfer equations"),
    _s("chemistry.acids.ph-strong", "pH of strong acids and bases", "Unit 3",
       "Acids and bases", "numeric",
       "pH, pOH, Kw, calculating pH from concentration and back"),
    _s("chemistry.acids.weak", "Weak acids, Ka and Kb", "Unit 3",
       "Acids and bases", "numeric",
       "the acid dissociation constant, pH of a weak acid, percentage "
       "ionisation"),
    _s("chemistry.acids.buffers", "Buffers", "Unit 3", "Acids and bases",
       "numeric",
       "how a buffer resists pH change, buffer pH calculations"),
    _s("chemistry.acids.titration-curves", "Titration curves and indicators",
       "Unit 3", "Acids and bases", "judge",
       "the shape of a titration curve, equivalence point, choosing an "
       "indicator"),

    _s("chemistry.redox.oxidation-numbers", "Oxidation numbers", "Unit 3",
       "Redox and electrochemistry", "numeric",
       "assigning oxidation numbers, identifying what is oxidised and reduced"),
    _s("chemistry.redox.half-equations", "Balancing redox equations", "Unit 3",
       "Redox and electrochemistry", "numeric",
       "writing and balancing half-equations in acidic and basic conditions, "
       "combining them into an overall equation"),
    _s("chemistry.redox.galvanic", "Galvanic cells", "Unit 3",
       "Redox and electrochemistry", "numeric",
       "cell diagrams, anode and cathode, calculating cell potential from "
       "standard electrode potentials, predicting spontaneity"),
    _s("chemistry.redox.electrolysis", "Electrolysis", "Unit 3",
       "Redox and electrochemistry", "numeric",
       "products of electrolysis, quantity of charge, mass deposited using "
       "Faraday's constant"),

    # Unit 4
    _s("chemistry.organic.nomenclature", "Organic nomenclature", "Unit 4",
       "Organic chemistry", "numeric",
       "naming and drawing alkanes, alkenes, alcohols, aldehydes, ketones, "
       "carboxylic acids, esters, amines and amides"),
    _s("chemistry.organic.isomers", "Isomers", "Unit 4", "Organic chemistry",
       "judge",
       "structural isomers, chain, position and functional group isomerism"),
    _s("chemistry.organic.reactions", "Reactions of organic compounds",
       "Unit 4", "Organic chemistry", "judge",
       "substitution, addition, oxidation, esterification and hydrolysis, and "
       "the conditions each needs"),
    _s("chemistry.organic.pathways", "Organic reaction pathways", "Unit 4",
       "Organic chemistry", "judge",
       "designing a multi-step route from one compound to another"),

    _s("chemistry.synthesis.yield", "Yield and atom economy", "Unit 4",
       "Chemical synthesis and analysis", "numeric",
       "percentage yield over multiple steps, atom economy, choosing between "
       "routes on efficiency"),
    _s("chemistry.synthesis.design", "Chemical synthesis and design", "Unit 4",
       "Chemical synthesis and analysis", "judge",
       "industrial synthesis, the compromise between rate, yield and cost, "
       "green chemistry principles"),
    _s("chemistry.synthesis.analysis", "Instrumental analysis", "Unit 4",
       "Chemical synthesis and analysis", "judge",
       "identifying a compound from mass spectrometry, infrared and NMR data, "
       "and from chromatography"),
]


SUBJECTS: dict[str, list[Subtopic]] = {
    "Mathematics Methods": METHODS,
    "Chemistry": CHEMISTRY,
}

# The slug that prefixes every subtopic id for a subject, so a bank row or a
# scope string can be read back to a subject without a lookup table.
SUBJECT_KEYS = {"Mathematics Methods": "methods", "Chemistry": "chemistry"}
_KEY_TO_SUBJECT = {v: k for k, v in SUBJECT_KEYS.items()}


def subject_for_key(key: str) -> str:
    """"methods" -> "Mathematics Methods". Empty string when unknown."""
    return _KEY_TO_SUBJECT.get((key or "").strip().lower(), "")


def subtopics(subject: str) -> list[Subtopic]:
    return list(SUBJECTS.get((subject or "").strip(), ()))


def subtopic(subtopic_id: str) -> Subtopic | None:
    key = (subtopic_id or "").split(".", 1)[0]
    for s in SUBJECTS.get(_KEY_TO_SUBJECT.get(key, ""), ()):
        if s.id == subtopic_id:
            return s
    return None


def strands(subject: str, year: str | None = None) -> list[str]:
    """Strand names in the order a student meets them, deduplicated."""
    seen: list[str] = []
    for s in subtopics(subject):
        if year and s.year != year:
            continue
        if s.strand not in seen:
            seen.append(s.strand)
    return seen


def bankable(sub: Subtopic) -> bool:
    """Whether the practice bank may hold questions on this subtopic.

    False for judge-only subtopics. A senior student grinding before an exam
    has no way to tell a checked answer from a plausible one, so a subtopic we
    cannot verify deterministically does not get stocked. It stays in the tree
    and stays visible in the picker as a topic the course contains.
    """
    return sub.verification in ("symbolic", "numeric")


# ---------------------------------------------------------------------------
# Scopes: what a student can choose to grind
# ---------------------------------------------------------------------------
# A scope id is one of:
#   "methods"                                 the whole subject
#   "methods:year:Year 12"                    a school year (two units)
#   "methods:unit:Unit 3"                     one unit
#   "methods:strand:Calculus"                 a strand, across every unit
#   "methods:strand:Calculus:Year 12"         a strand within one year
#   "methods.calculus.antidifferentiation"    one subtopic (the bare id)


@dataclass(frozen=True)
class Scope:
    """One selectable row in the picker."""

    id: str
    label: str
    level: str          # "subject", "year", "unit", "strand", "subtopic"
    subject: str
    count: int          # bankable subtopics inside it
    parent: str | None = None


def _key(subject: str) -> str:
    return SUBJECT_KEYS.get((subject or "").strip(), "")


def scope_options(subject: str, year: str | None = None) -> list[Scope]:
    """Every scope a student may pick, ordered broadest to narrowest.

    The order is the order the picker should display: whole subject, then
    years, then units, then strands, then individual subtopics. A student who
    knows exactly what they want scrolls; a student who does not takes the
    first row.

    `year` narrows the whole list to one school year, which is what the picker
    does once a student has said they are in Year 12.
    """
    subject = (subject or "").strip()
    key = _key(subject)
    if not key:
        return []
    pool = [s for s in subtopics(subject) if not year or s.year == year]
    if not pool:
        return []

    def n(items) -> int:
        return sum(1 for s in items if bankable(s))

    out: list[Scope] = []
    if year:
        out.append(Scope(f"{key}:year:{year}", f"Whole year ({year})", "year",
                         subject, n(pool)))
    else:
        out.append(Scope(key, f"Everything in {subject}", "subject", subject,
                         n(pool)))
        for y, units in UNITS_BY_YEAR.items():
            inside = [s for s in pool if s.unit in units]
            if inside:
                out.append(Scope(f"{key}:year:{y}", f"Whole year ({y})",
                                 "year", subject, n(inside), key))

    for unit in ("Unit 1", "Unit 2", "Unit 3", "Unit 4"):
        inside = [s for s in pool if s.unit == unit]
        if inside:
            out.append(Scope(f"{key}:unit:{unit}", unit, "unit", subject,
                             n(inside), key))

    for strand in strands(subject, year):
        inside = [s for s in pool if s.strand == strand]
        sid = (f"{key}:strand:{strand}:{year}" if year
               else f"{key}:strand:{strand}")
        out.append(Scope(sid, strand, "strand", subject, n(inside), key))

    for s in pool:
        out.append(Scope(s.id, s.name, "subtopic", subject,
                         1 if bankable(s) else 0,
                         f"{key}:strand:{s.strand}"))
    return out


def resolve_scope(scope_id: str, bankable_only: bool = True) -> list[str]:
    """Turn whatever the student picked into the subtopic ids to draw from.

    Returns an empty list for an unknown scope, so a caller that passes a
    stale or hand-edited scope serves nothing rather than serving everything.
    That direction matters: the failure mode of the opposite default is a
    student who asked for antidifferentiation being fed confidence intervals.
    """
    raw = (scope_id or "").strip()
    if not raw:
        return []

    def keep(items):
        return [s.id for s in items if not bankable_only or bankable(s)]

    # A bare subtopic id.
    one = subtopic(raw)
    if one is not None:
        return keep([one])

    parts = raw.split(":")
    subject = subject_for_key(parts[0])
    if not subject:
        return []
    pool = subtopics(subject)

    if len(parts) == 1:
        return keep(pool)
    if len(parts) < 3:
        return []
    level, value = parts[1], parts[2]

    if level == "year":
        units = UNITS_BY_YEAR.get(value, ())
        return keep([s for s in pool if s.unit in units])
    if level == "unit":
        return keep([s for s in pool if s.unit == value])
    if level == "strand":
        inside = [s for s in pool if s.strand == value]
        if len(parts) >= 4:
            inside = [s for s in inside if s.year == parts[3]]
        return keep(inside)
    return []


def scope_label(scope_id: str) -> str:
    """What to print at the top of a practice session. Never empty."""
    raw = (scope_id or "").strip()
    one = subtopic(raw)
    if one is not None:
        return one.name
    parts = raw.split(":")
    subject = subject_for_key(parts[0] if parts else "")
    if not subject:
        return "Practice"
    if len(parts) == 1:
        return f"Everything in {subject}"
    if len(parts) >= 3 and parts[1] == "year":
        return f"Whole year ({parts[2]})"
    if len(parts) >= 3 and parts[1] == "unit":
        return parts[2]
    if len(parts) >= 3 and parts[1] == "strand":
        return parts[2] if len(parts) < 4 else f"{parts[2]} ({parts[3]})"
    return "Practice"


def guidance_block(sub: Subtopic) -> str:
    """The syllabus block appended to a generator's instructions.

    Narrow on purpose. The generator is writing questions for ONE subtopic, and
    the failure this prevents is a question that drifts into a neighbouring
    topic and is then graded as if it were on the one the student chose.
    """
    return "\n".join([
        "",
        f"SUBTOPIC: {sub.name}",
        f"Course: WACE {_KEY_TO_SUBJECT.get(sub.subject_key, '')} "
        f"{sub.unit} ({sub.year}).",
        f"Strand: {sub.strand}.",
        f"This subtopic covers: {sub.summary}.",
        "Every question must sit inside that description. A question that "
        "needs a technique from another subtopic to start is out of scope, "
        "because the student chose this subtopic and will be graded on it.",
        f"Calculator: {sub.calculator}.",
    ]) + "\n"
