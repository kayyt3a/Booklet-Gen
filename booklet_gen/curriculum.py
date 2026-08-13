"""What each year level actually means, so "Year 10" is not a guess.

A shipped Year 10 Mathematics booklet taught expanding 4(2x + 5) and
Pythagoras' theorem. Both are Year 8 in the Western Australian curriculum, and
its warm-up asked for the area of a 12 by 7 rectangle, which is Year 4. The
generator had no idea what a year level contains: "Year 10" was a string in a
prompt, and the model filled it in from a general sense of school maths, which
skews years too low.

This module is that missing knowledge. For each subject and year it holds:

  signature  the skills that mark this year out, concrete enough to choose
             subtopics from.
  below      skills that belong to an EARLIER year, named so they can be
             refused. This is the half that fixes under-pitching: telling a
             model to write Year 10 work does not stop it choosing Year 8
             work, but telling it that expanding with a numerical factor IS
             Year 8 does.

SOURCE AND RIGHTS
-----------------
Derived from the Western Australian curriculum achievement standards published
by the School Curriculum and Standards Authority (SCSA), for implementation in
2026 (Mathematics) and 2025 (English), which in turn derive from the
Australian Curriculum.

The SCSA documents themselves are licensed for copying only for
NON-COMMERCIAL purposes in educational institutions. Folio is sold, so their
text is deliberately NOT reproduced here: what follows is a factual summary of
which skills sit at which year, written fresh. Content derived from the
Australian Curriculum is available under CC BY 4.0, which does permit
commercial use with attribution, and the attribution is ATTRIBUTION below.

Do not paste SCSA wording into this file. A list of which topics belong to
which school year is a fact about the curriculum; the Authority's prose is
theirs.
"""
from __future__ import annotations

ATTRIBUTION = (
    "Year level content is based on the Western Australian curriculum "
    "(School Curriculum and Standards Authority), which incorporates material "
    "from the Australian Curriculum, used under CC BY 4.0."
)

YEARS = ["Pre-primary", "Year 1", "Year 2", "Year 3", "Year 4", "Year 5",
         "Year 6", "Year 7", "Year 8", "Year 9", "Year 10"]

# Mathematics. Each entry lists what the year is FOR, in the order the
# standards introduce it.
MATHS: dict[str, list[str]] = {
    "Pre-primary": [
        "count, say, read and order numbers to 20",
        "subitise and partition small collections",
        "adding, removing, grouping and sharing with objects",
        "name familiar two-dimensional shapes; position and movement",
        "compare length, capacity, mass and the duration of events",
    ],
    "Year 1": [
        "say, read, write and order numbers to 100",
        "partition collections, including into groups of 10",
        "add and subtract within 20",
        "skip count by twos, fives and tens; repeating patterns",
        "one-half; Australian coins and notes",
        "classify 2D shapes, sort and name 3D objects",
        "compare length with uniform informal units; digital clock times",
    ],
    "Year 2": [
        "read, write and order numbers to at least 1000",
        "skip count by twos, threes, fives and tens from any starting point",
        "partition two- and three-digit numbers into tens and hundreds",
        "recall addition and subtraction facts to 10",
        "represent multiplication and division situations",
        "halves, quarters and eighths; dollars and cents",
        "time to the hour, half-hour and quarter-hour; calendars",
        "categorical data in tables and one-to-one picture graphs",
    ],
    "Year 3": [
        "order numbers to at least four digits",
        "recall addition and subtraction facts to 20",
        "add and subtract two- and three-digit numbers",
        "recall multiplication facts for 2, 3, 4, 5 and 10; arrays",
        "increasing and decreasing additive patterns",
        "unit fractions one half, one third, one quarter, one fifth, one tenth",
        "classify 3D objects; compare angles; simple maps",
        "measure length and capacity in metric units; time in minutes",
        "dot plots, tables and column graphs",
    ],
    "Year 4": [
        "add and subtract numbers up to four digits",
        "multiply two-digit numbers by one- and two-digit numbers",
        "divide whole numbers by a one-digit number with NO remainder",
        "recall multiplication facts to 10 x 10",
        "increasing multiplicative patterns",
        "equivalent fractions; connect fractions and decimals",
        "perimeter, capacity and mass with scaled instruments",
        "compare the area of rectangles in informal units",
        "compare angles against a right angle; convert units of time",
        "many-to-one pictographs and column graphs",
    ],
    "Year 5": [
        "multiply larger whole numbers by one- and two-digit numbers",
        "divide whole numbers by a one-digit number INCLUDING remainders",
        "factors and multiples",
        "order decimals; unit fractions on a number line; 100% as a whole",
        "add and subtract fractions with the SAME denominator",
        "follow a rule to create a pattern; simple budgets",
        "choose metric units; square and cubic units for area and volume",
        "nets of 3D objects; construct and name angles in degrees",
        "grid coordinates; convert between 12- and 24-hour time",
        "equally likely outcomes; results as fractions",
    ],
    "Year 6": [
        "square, prime and composite numbers",
        "all four operations with whole numbers",
        "integers and common fractions on a number line",
        "add and subtract fractions with RELATED denominators",
        "decimals to two places; multiply decimals by whole numbers",
        "multiply and divide decimals by powers of 10",
        "a fraction, decimal or percentage of a whole number",
        "rules relating each element of a pattern to its position",
        "convert units of length, mass and capacity",
        "area of rectangles; volume of rectangular prisms",
        "prisms and pyramids; unknown angles with reasoning",
        "translations, reflections, rotations; four quadrants of the plane",
        "chance on a 0 to 1 scale; expected vs observed frequencies",
        "mode, range and shape of a data display",
    ],
    "Year 7": [
        "equivalent fractions and equivalent ratios",
        "convert between fractions, decimals and percentages",
        "add and subtract fractions and integers",
        "multiply and divide positive fractions and decimals",
        "percentage calculations; round to a given number of decimal places",
        "index notation; square numbers and square roots",
        "variables; evaluate expressions by substitution",
        "solve linear equations involving ONE operation",
        "linear patterns in a table of values; plot points",
        "perimeter and area of rectangles; volume of rectangular prisms",
        "classify triangles; unknown angles; area of triangles",
        "angle relationships in parallel lines",
        "single-stage chance experiments and sample spaces",
    ],
    "Year 8": [
        "rational and irrational numbers; terminating and recurring decimals",
        "four operations with integers and positive rational numbers",
        "percentage increase and decrease",
        "proportional reasoning with equivalent ratios; familiar rates",
        "index laws for NUMBERS with positive-integer indices",
        "simplify algebraic expressions with the four operations",
        "expand and factorise expressions with a NUMERICAL factor",
        "solve linear equations involving TWO operations",
        "graph linear relationships from a table of values",
        "classify quadrilaterals; perimeter and area of quadrilaterals",
        "PYTHAGORAS' THEOREM to find an unknown length",
        "circumference and area of circles; volume of right prisms",
        "congruent figures; time zones",
        "two-step chance experiments",
    ],
    "Year 9": [
        "real numbers; scientific notation",
        "index laws with VARIABLE bases, positive-integer and zero indices",
        "expand and factorise expressions with an ALGEBRAIC factor",
        "expand BINOMIAL products",
        "solve linear equations involving BRACKETS",
        "distance between two points, gradient and midpoint",
        "graph straight lines from gradient and y-intercept; direct proportion",
        "solve simple QUADRATIC equations algebraically; graph from a table",
        "simple interest; calculations relating to earning income",
        "perimeter and area of composite shapes",
        "congruent triangles with reasoning",
        "similar figures and scale drawings",
        "TRIGONOMETRIC RATIOS for unknown sides and angles",
        "volume, capacity and surface area of common solids",
    ],
    "Year 10": [
        "approximate versus exact values, and the effect of error",
        "substitute into real-life formulas to find unknowns",
        "solve linear INEQUALITIES and represent them on a number line",
        "solve SIMULTANEOUS linear equations, including graphically",
        "graph QUADRATIC and EXPONENTIAL functions; relate graph to algebra",
        "income tax; COMPOUND INTEREST via repeated simple interest",
        "Pythagoras and trigonometry with ANGLES OF ELEVATION AND DEPRESSION",
        "the effect on perimeter, area and volume of enlarging or reducing",
        "conditions for SIMILAR TRIANGLES, with reasoning",
        "volume, capacity and surface area of COMPOSITE objects",
        "two- and three-stage chance experiments; CONDITIONAL probability",
        "association in scatterplots and two-way tables",
        "compare multiple BOXPLOTS by shape, spread and centre",
        "critically analyse claims and bias in the media",
    ],
}

# English. The standards are organised by mode rather than by topic, so these
# describe what the student is expected to DO with a text at each year.
ENGLISH: dict[str, list[str]] = {
    "Pre-primary": [
        "retell and respond to familiar texts",
        "identify rhyme, syllables and initial sounds",
        "read consonant-vowel-consonant words; write simple sentences",
    ],
    "Year 1": [
        "connect ideas in a text to personal experience",
        "blend and segment sounds to read and spell one-syllable words",
        "write sentences with a capital letter and a full stop",
    ],
    "Year 2": [
        "identify the purpose of a text and literal meaning",
        "read fluently with common digraphs and blends",
        "write short sequenced texts with correct sentence punctuation",
    ],
    "Year 3": [
        "literal and simple inferred meaning",
        "identify how a text is organised for its purpose",
        "paragraphs; commas in lists; more precise vocabulary",
    ],
    "Year 4": [
        "inferred meaning with evidence from the text",
        "explain how language choices shape a reader's response",
        "structure a text with clear paragraphs; use quotation marks",
    ],
    "Year 5": [
        "decode unfamiliar words using phonic, grammatical and contextual cues",
        "build literal AND inferred meaning in less familiar topics",
        "explain how ideas develop across a text; begin to evaluate them",
        "use topic-specific vocabulary and literary devices deliberately",
    ],
    "Year 6": [
        "compare how different texts treat the same idea",
        "explain how text structure and language features shape meaning",
        "select evidence to support an interpretation",
        "vary sentence structure and use cohesive devices",
    ],
    "Year 7": [
        "explain how ideas are PORTRAYED and how context influences a text",
        "discuss the aesthetic qualities of a text",
        "explain how structure, language features, literary devices and "
        "visual features shape meaning",
        "select evidence from a text to develop an original response",
    ],
    "Year 8": [
        "analyse how a text positions its audience",
        "explain the effect of specific language and structural choices",
        "sustain an argument with selected textual evidence",
    ],
    "Year 9": [
        "ANALYSE and INTERPRET representations of people, places and events",
        "analyse how texts reflect their contexts",
        "analyse the effect of intertextual references and multimodal features",
        "incorporate evidence as substantiation, not illustration",
        "select and experiment with structures and devices in own writing",
    ],
    "Year 10": [
        "analyse, interpret and EVALUATE representations",
        "evaluate how a reader's own context shapes their interpretation",
        "evaluate the effect of structure, language, literary devices and "
        "intertextual connections on a text's aesthetic qualities",
        "select, adapt and vary rhetorical and literary devices in own writing",
        "sustain and substantiate an evaluative argument",
    ],
}

SUBJECTS = {"Mathematics": MATHS, "Maths": MATHS, "English": ENGLISH}


def _norm_year(year_level: str) -> str | None:
    y = (year_level or "").strip()
    for candidate in YEARS:
        if candidate.lower() == y.lower():
            return candidate
    return None


def skills_for(subject: str, year_level: str) -> list[str]:
    """The signature skills of this year, or [] for a year or subject the map
    does not cover (Year 11-12 exam work, Science, Reasoning)."""
    table = SUBJECTS.get((subject or "").strip())
    year = _norm_year(year_level)
    if not table or not year:
        return []
    return table.get(year, [])


def earlier_skills(subject: str, year_level: str, years_back: int = 2) -> list[tuple[str, str]]:
    """(year, skill) pairs from the years just below this one.

    Named so a booklet can refuse them. Under-pitching is not a vague failure:
    it looks exactly like a plausible subtopic, and the only way to catch it is
    to know which year the skill actually belongs to.
    """
    table = SUBJECTS.get((subject or "").strip())
    year = _norm_year(year_level)
    if not table or not year:
        return []
    i = YEARS.index(year)
    out: list[tuple[str, str]] = []
    for j in range(max(0, i - years_back), i):
        for skill in table.get(YEARS[j], []):
            out.append((YEARS[j], skill))
    return out


def guidance_block(subject: str, year_level: str) -> str:
    """The curriculum block appended to an agent's instructions.

    Empty when the map has nothing for this subject or year, so callers can
    concatenate unconditionally.
    """
    skills = skills_for(subject, year_level)
    if not skills:
        return ""
    lines = [
        "",
        f"WHAT {year_level.upper()} {subject.upper()} ACTUALLY COVERS",
        f"From the Western Australian curriculum. This is what a {year_level} "
        f"student is working on THIS year:",
    ]
    lines += [f"- {s}" for s in skills]

    earlier = earlier_skills(subject, year_level)
    if earlier:
        lines += [
            "",
            "ALREADY COVERED IN EARLIER YEARS. A booklet may assume these, and "
            "may use them inside a harder question, but NONE of them is a "
            f"subtopic for {year_level}. Choosing one means the booklet is "
            "pitched below the student:",
        ]
        lines += [f"- {skill} ({yr})" for yr, skill in earlier]
    return "\n".join(lines) + "\n"
