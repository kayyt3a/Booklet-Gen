"""Standard atomic weights, as facts rather than as opinions.

SOURCE
------
The values below are the IUPAC Commission on Isotopic Abundances and Atomic
Weights conventional atomic weights, as published in the 2021 table of standard
atomic weights (Prohaska et al., "Standard atomic weights of the elements 2021",
Pure and Applied Chemistry 94(5), 573-600). Where an element has no stable
isotope the value is the mass number of its longest-lived isotope, which is what
a school data sheet prints, and it is marked in `NO_STABLE_ISOTOPE`.

WHY THIS FILE IS SEPARATE, AND WHY IT IS PLAIN DATA
---------------------------------------------------
One wrong number here does not produce one wrong question. A template is
expanded roughly sixty times and every one of those instances is stamped
"verified", so a molar mass that is out by a hundredth ships that error to
every student who ever draws the topic. The verifier cannot catch it either:
the generator and the checker would agree, because both read this table. The
only defence is that these values are checked against the published table by
hand, and that `scripts/check_practice_chemistry.py` recomputes molar masses
that a Year 12 data book states independently (Ca(NO3)2 and CuSO4.5H2O).

Do not "improve" a value by rounding it to the figures a data sheet shows.
Rounding belongs at the point a question is printed, not in the constant.
"""
from __future__ import annotations

# Symbol -> standard atomic weight in g/mol.
#
# Coverage stops at uranium. Everything past it is synthetic, appears in no WACE
# Chemistry question, and would be eleven more chances to mistype a digit for no
# gain.
ATOMIC_MASS: dict[str, float] = {
    "H": 1.008,
    "He": 4.002602,
    "Li": 6.94,
    "Be": 9.0121831,
    "B": 10.81,
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "F": 18.998403162,
    "Ne": 20.1797,
    "Na": 22.98976928,
    "Mg": 24.305,
    "Al": 26.9815384,
    "Si": 28.085,
    "P": 30.973761998,
    "S": 32.06,
    "Cl": 35.45,
    "Ar": 39.95,
    "K": 39.0983,
    "Ca": 40.078,
    "Sc": 44.955907,
    "Ti": 47.867,
    "V": 50.9415,
    "Cr": 51.9961,
    "Mn": 54.938043,
    "Fe": 55.845,
    "Co": 58.933194,
    "Ni": 58.6934,
    "Cu": 63.546,
    "Zn": 65.38,
    "Ga": 69.723,
    "Ge": 72.630,
    "As": 74.921595,
    "Se": 78.971,
    "Br": 79.904,
    "Kr": 83.798,
    "Rb": 85.4678,
    "Sr": 87.62,
    "Y": 88.905838,
    "Zr": 91.224,
    "Nb": 92.90637,
    "Mo": 95.95,
    "Tc": 98.0,
    "Ru": 101.07,
    "Rh": 102.90549,
    "Pd": 106.42,
    "Ag": 107.8682,
    "Cd": 112.414,
    "In": 114.818,
    "Sn": 118.710,
    "Sb": 121.760,
    "Te": 127.60,
    "I": 126.90447,
    "Xe": 131.293,
    "Cs": 132.90545196,
    "Ba": 137.327,
    "La": 138.90547,
    "Ce": 140.116,
    "Pr": 140.90766,
    "Nd": 144.242,
    "Pm": 145.0,
    "Sm": 150.36,
    "Eu": 151.964,
    "Gd": 157.249,
    "Tb": 158.925354,
    "Dy": 162.500,
    "Ho": 164.930329,
    "Er": 167.259,
    "Tm": 168.934219,
    "Yb": 173.045,
    "Lu": 174.9668,
    "Hf": 178.486,
    "Ta": 180.94788,
    "W": 183.84,
    "Re": 186.207,
    "Os": 190.23,
    "Ir": 192.217,
    "Pt": 195.084,
    "Au": 196.96657,
    "Hg": 200.592,
    "Tl": 204.38,
    "Pb": 207.2,
    "Bi": 208.98040,
    "Po": 209.0,
    "At": 210.0,
    "Rn": 222.0,
    "Fr": 223.0,
    "Ra": 226.0,
    "Ac": 227.0,
    "Th": 232.0377,
    "Pa": 231.03588,
    "U": 238.02891,
}

# Elements whose entry above is a mass number, not a measured atomic weight.
# A question that asks for the molar mass of one of these is asking about a
# convention rather than a measurement, so the generator is told to avoid them.
NO_STABLE_ISOTOPE = frozenset({
    "Tc", "Pm", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Pa",
})

# The elements a WACE Chemistry question may safely be built from. Deliberately
# narrower than the table: a question about hafnium is not wrong, it is just not
# a question anyone sitting the course would recognise, and a bank full of them
# is a bank that looks generated.
SCHOOL_ELEMENTS = frozenset({
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Br", "Rb", "Sr", "Ag", "Sn", "I", "Ba", "Pt", "Au", "Hg", "Pb",
})

# Longest symbol first, so a scanner matching "Cl" never stops at "C".
SYMBOLS_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(ATOMIC_MASS, key=lambda s: (-len(s), s))
)


def atomic_mass(symbol: str) -> float:
    """The standard atomic weight of one element symbol.

    Raises rather than returning a default. A silent zero for a mistyped symbol
    would make every molar mass computed from it wrong by exactly the mass of
    the element nobody noticed was missing.
    """
    try:
        return ATOMIC_MASS[symbol]
    except KeyError:
        raise KeyError(
            f"{symbol!r} is not an element symbol this table knows. Coverage "
            "stops at uranium and symbols are case sensitive: 'CO' is carbon "
            "monoxide, 'Co' is cobalt."
        ) from None
