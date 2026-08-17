"""The FolioAI booklet cover, drawn on the canvas rather than dropped in as a
picture.

Until now the cover was one static full-bleed JPEG with a few centred text
flowables laid over it, so every booklet in the catalogue had an identical
front page and nothing on it could respond to the subject, the year, the topic
or the week. The founder's design system (`booklet_gen/assets/
COVER_DESIGN_SYSTEM.md`, with the two reference mockups beside it in
`assets/design_reference/`) asks for a cover that varies by background family
and subject while keeping the composition fixed, which a fixed image cannot do.

Everything here is drawn with ReportLab primitives on page 1, except the Folio
mark itself, which is the real brand asset
(`webapp/static/img/brand/mark-512.png`) reused at two sizes: a small publisher
lockup top left, and the large page motif in the lower right.

The module knows nothing about BookletData. The formatter assembles a
`CoverSpec` of finished strings and hands it over, which keeps the one piece of
cover copy that has to be right, the sentence about what has and has not been
checked in the answer key, next to the comment in formatter.py that explains
why it is written the way it is.

Drawing is pure: `render_cover` reads the spec and writes to the canvas, with
no global state and no caching, because render_pdf builds the document twice
(a throwaway pass to find page numbers, then the real one) and the two builds
have to produce the same pages.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics

log = logging.getLogger(__name__)

W, H = A4
MARGIN = 56.7                      # 2.0cm, the same margin the body pages use

# The real brand mark: fanned pages behind an "F", navy on white, with true
# transparency. The other two files in that folder (source/mark.png,
# icon-square.png) have a glow and a dark backdrop baked in, which would print
# as a grey rectangle on a light cover.
LOGO_PATH = (Path(__file__).resolve().parent.parent
             / "webapp" / "static" / "img" / "brand" / "mark-512.png")


# --------------------------------------------------------------------------
# Palette
#
# Sampled from the two reference mockups rather than invented: the navy is the
# title colour lifted off english_cover_reference.png (#00114E), and the blues
# are the four wave tones that appear in both files. The website's --navy
# (#1F3A5F) is a lighter, greyer navy used for headings in the booklet body;
# the cover deliberately runs deeper, the way a printed workbook cover does.
# --------------------------------------------------------------------------
NAVY = HexColor("#00114E")
NAVY_SOFT = HexColor("#2A3F73")
ACCENT = HexColor("#6E9EFD")
BLUE_DEEP = HexColor("#4C86EE")
BLUE_MID = HexColor("#A8C5FC")
BLUE_PALE = HexColor("#C8DBFD")
BLUE_FAINT = HexColor("#DEE9FE")
BLUE_MIST = HexColor("#EDF3FE")
WHITE = HexColor("#FFFFFF")
CREAM = HexColor("#FBF6EC")
CREAM_PALE = HexColor("#F5EEDF")


@dataclass(frozen=True)
class Variant:
    """One of the four approved cover families."""
    key: str
    background: object
    ink: object                 # title and row labels
    muted: object               # secondary text
    pill_fill: object
    pill_ink: object
    rule: object
    waves: tuple                # (colour, knots) from back to front
    detail: object              # subject decoration colour
    detail_alpha: float


# Wave knots are (x, y) in fractions of the page, left to right, and each band
# is filled from its curve down to the bottom edge. Painted back to front, they
# read as sheets of paper sliding over one another, which is the "turning
# pages" idea in section 5 of the brief. The first band in each family sweeps
# up the right hand side, which is what pulls the composition together in both
# reference mockups.
_SWEEP = [
    ((0.00, 0.32), (0.34, 0.38), (0.70, 0.58), (1.00, 0.84)),
    ((0.00, 0.225), (0.30, 0.265), (0.66, 0.405), (1.00, 0.66)),
    ((0.00, 0.135), (0.35, 0.205), (0.70, 0.265), (1.00, 0.43)),
    ((0.00, 0.085), (0.30, 0.145), (0.62, 0.125), (1.00, 0.225)),
    ((0.00, 0.042), (0.28, 0.088), (0.60, 0.052), (1.00, 0.118)),
]

# The dark family reverses the idea: the page shapes are the light thing, and
# they enter low from the right instead of covering the bottom third.
_SWEEP_DARK = [
    ((0.00, 0.16), (0.34, 0.20), (0.70, 0.36), (1.00, 0.62)),
    ((0.00, 0.10), (0.32, 0.135), (0.68, 0.225), (1.00, 0.44)),
    ((0.00, 0.055), (0.30, 0.085), (0.62, 0.115), (1.00, 0.26)),
    ((0.00, 0.022), (0.28, 0.045), (0.60, 0.038), (1.00, 0.10)),
]


VARIANTS = {
    # Mathematics, English, general academic subjects.
    "light_blue": Variant(
        key="light_blue", background=HexColor("#F7FAFF"), ink=NAVY,
        muted=NAVY_SOFT, pill_fill=BLUE_PALE, pill_ink=NAVY, rule=ACCENT,
        waves=((BLUE_MIST, _SWEEP[0]), (BLUE_FAINT, _SWEEP[1]),
               (BLUE_PALE, _SWEEP[2]), (BLUE_MID, _SWEEP[3]),
               (BLUE_DEEP, _SWEEP[4])),
        detail=BLUE_DEEP, detail_alpha=0.30),
    # Science, advanced subjects, assessments, premium booklets.
    "dark_navy": Variant(
        key="dark_navy", background=HexColor("#081538"), ink=WHITE,
        muted=HexColor("#B9CDF2"), pill_fill=HexColor("#22376E"),
        pill_ink=HexColor("#DCE7FC"), rule=ACCENT,
        waves=((HexColor("#12245C"), _SWEEP_DARK[0]),
               (HexColor("#1B3474"), _SWEEP_DARK[1]),
               (HexColor("#3F6CC4"), _SWEEP_DARK[2]),
               (HexColor("#C8DBFD"), _SWEEP_DARK[3])),
        detail=HexColor("#7FA6F0"), detail_alpha=0.35),
    # Primary school and general practice material.
    "white": Variant(
        key="white", background=WHITE, ink=NAVY, muted=NAVY_SOFT,
        pill_fill=BLUE_FAINT, pill_ink=NAVY, rule=ACCENT,
        waves=((HexColor("#F4F8FE"), _SWEEP[1]), (BLUE_MIST, _SWEEP[2]),
               (BLUE_FAINT, _SWEEP[3]), (BLUE_MID, _SWEEP[4])),
        detail=BLUE_MID, detail_alpha=0.55),
    # General Abilities, reasoning, premium workbook collections.
    "warm": Variant(
        key="warm", background=CREAM, ink=NAVY, muted=NAVY_SOFT,
        pill_fill=HexColor("#EADCC0"), pill_ink=NAVY, rule=HexColor("#C79A4B"),
        waves=((HexColor("#FDFAF3"), _SWEEP[0]), (CREAM_PALE, _SWEEP[1]),
               (BLUE_FAINT, _SWEEP[2]), (BLUE_PALE, _SWEEP[3]),
               (BLUE_MID, _SWEEP[4])),
        detail=HexColor("#B08A45"), detail_alpha=0.40),
}
DEFAULT_VARIANT = "light_blue"


# --------------------------------------------------------------------------
# Variant selection
# --------------------------------------------------------------------------

# Keyword tables, checked in this order against the subject, the program label
# and the topic. "Best for" in the brief is written in terms of subjects, so
# the mapping is too; there is no cover_variant field on BookletData yet, and
# inventing one is a schema change this does not need.
_REASONING_WORDS = ("general abilit", "reasoning", "abstract", "quantitative",
                    "verbal", "non-verbal", "aptitude")
_SCIENCE_WORDS = ("science", "biolog", "chemis", "physic")
_PREMIUM_WORDS = ("scholarship", "exam", "assessment", "selective", "methods",
                  "specialist", "atar")
_ACADEMIC_WORDS = ("math", "english", "humanit", "hass", "history",
                   "geograph", "naplan", "literac", "numerac")
_PRIMARY_YEARS = ("year 1", "year 2", "year 3", "year 4", "foundation",
                  "pre-primary", "kindergarten")


def variant_for(subject: str = "", program_label: str = "",
                year_level: str = "", topic: str = "") -> str:
    """Pick a cover family from what the booklet already knows about itself.

    Deterministic and total: every booklet gets a family, and the same booklet
    always gets the same one, which matters because render_pdf builds twice.
    """
    hay = " ".join(x for x in (subject, program_label, topic) if x).lower()
    year = (year_level or "").lower()
    if any(w in hay for w in _REASONING_WORDS):
        return "warm"
    if any(w in hay for w in _SCIENCE_WORDS):
        return "dark_navy"
    if any(w in hay for w in _PREMIUM_WORDS):
        return "dark_navy"
    if any(w in hay for w in _ACADEMIC_WORDS):
        # A Year 2 maths booklet is still primary material, and the brief puts
        # primary on the white family.
        if any(y in year for y in _PRIMARY_YEARS):
            return "white"
        return "light_blue"
    return "white"


# --------------------------------------------------------------------------
# The spec the formatter fills in
# --------------------------------------------------------------------------

@dataclass
class CoverSpec:
    title_lines: list = field(default_factory=list)   # "Year 6", "Mathematics"
    pill: str = "Practice Booklet"
    eyebrow: str = ""            # product line, e.g. "ACADEMIC ACCELERATE"
    subject: str = ""            # drives the subject decoration only
    topic: str = ""
    student_name: str = ""
    week: str = ""               # "3 of 10" or "3 of 10  |  Persuasive devices"
    # DIFFICULTY is in the brief's dynamic field list, but BookletData has no
    # source for it today, so it renders only when a caller actually supplies
    # one and is otherwise absent rather than guessed at.
    difficulty: str = ""
    meta_lines: list = field(default_factory=list)    # date, estimated time
    footer_note: str = ""
    variant: str = DEFAULT_VARIANT
    font_regular: str = "Helvetica"
    font_bold: str = "Helvetica-Bold"
    # Escape hatch: a full-bleed image that replaces the drawn cover entirely.
    background_image: str = ""


# --------------------------------------------------------------------------
# Drawing primitives
# --------------------------------------------------------------------------

def _smooth(path, pts) -> None:
    """A Catmull-Rom spline through `pts`, emitted as cubic beziers.

    Waves defined by four knots and drawn as straight-ish beziers looked like
    folded card. Interpolating the knots keeps the curve continuous at every
    one of them, which is what makes it read as paper.
    """
    path.moveTo(*pts[0])
    n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[0]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[-1]
        path.curveTo(p1[0] + (p2[0] - p0[0]) / 6.0,
                     p1[1] + (p2[1] - p0[1]) / 6.0,
                     p2[0] - (p3[0] - p1[0]) / 6.0,
                     p2[1] - (p3[1] - p1[1]) / 6.0,
                     p2[0], p2[1])


def _wave(c, knots, colour) -> None:
    """Fill the region under a smooth curve, from the curve to the page foot."""
    pts = [(x * W, y * H) for x, y in knots]
    c.saveState()
    c.setFillColor(colour)
    c.setStrokeColor(colour)
    p = c.beginPath()
    _smooth(p, pts)
    p.lineTo(W, -2)
    p.lineTo(-2, -2)
    p.close()
    c.drawPath(p, stroke=0, fill=1)
    c.restoreState()


def _text(c, x, y, s, font, size, colour, alpha: float = 1.0) -> float:
    if not s:
        return x
    c.saveState()
    c.setFont(font, size)
    c.setFillColor(colour)
    if alpha < 1.0:
        c.setFillAlpha(alpha)
    c.drawString(x, y, s)
    c.restoreState()
    return x + pdfmetrics.stringWidth(s, font, size)


def _wrap(s: str, font: str, size: float, width: float) -> list:
    """Greedy word wrap. The canvas has no paragraph engine of its own."""
    out, line = [], ""
    for word in s.split():
        trial = f"{line} {word}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    if line:
        out.append(line)
    return out


def _fit(s: str, font: str, size: float, width: float) -> float:
    """Shrink a size until the string fits. Long subjects such as "Mathematics
    and English" must not run off the trim edge, and the title has to read from
    a thumbnail, so it shrinks rather than wraps."""
    while size > 12 and pdfmetrics.stringWidth(s, font, size) > width:
        size -= 0.5
    return size


# --------------------------------------------------------------------------
# Row icons
#
# Stroked, never filled, on a nominal 12pt square, the same discipline as the
# website's motif macros in webapp/templates/_motifs.html.
# --------------------------------------------------------------------------

def _icon_target(c, x, y, s) -> None:
    c.circle(x + s / 2, y + s / 2, s / 2, stroke=1, fill=0)
    c.circle(x + s / 2, y + s / 2, s / 5, stroke=1, fill=0)


def _icon_person(c, x, y, s) -> None:
    c.circle(x + s / 2, y + s * 0.72, s * 0.22, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(x + s * 0.12, y + s * 0.06)
    p.curveTo(x + s * 0.12, y + s * 0.40, x + s * 0.88, y + s * 0.40,
              x + s * 0.88, y + s * 0.06)
    c.drawPath(p, stroke=1, fill=0)


def _icon_calendar(c, x, y, s) -> None:
    c.roundRect(x + s * 0.08, y + s * 0.06, s * 0.84, s * 0.76, s * 0.14,
                stroke=1, fill=0)
    c.line(x + s * 0.08, y + s * 0.60, x + s * 0.92, y + s * 0.60)
    c.line(x + s * 0.30, y + s * 0.82, x + s * 0.30, y + s * 0.98)
    c.line(x + s * 0.70, y + s * 0.82, x + s * 0.70, y + s * 0.98)


def _icon_bars(c, x, y, s) -> None:
    for i, h in enumerate((0.30, 0.52, 0.76)):
        c.rect(x + s * (0.10 + i * 0.30), y + s * 0.06, s * 0.18, s * h,
               stroke=1, fill=0)


_ROW_ICONS = {"topic": _icon_target, "name": _icon_person,
              "week": _icon_calendar, "difficulty": _icon_bars}


# --------------------------------------------------------------------------
# Subject decoration
#
# Secondary to the Folio mark in every case: faint, small, and confined to the
# band left of the logo motif so it never competes with it. The brief is
# explicit that subjects differ only by this detail and never by a redesign.
# --------------------------------------------------------------------------

def _pencil(c, cx, cy, length, width, angle_deg) -> None:
    """A stroked pencil: hexagonal body, angled tip, a cap line near the
    eraser end. Same outline language as the pencil motif on the website
    (webapp/templates/_motifs.html), redrawn here because that one is an SVG
    macro and this is a canvas; the reference cover carries this pencil
    crossing the grid and it was dropped in the first pass at this file."""
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle_deg)
    half = width / 2.0
    tip = length * 0.16
    p = c.beginPath()
    p.moveTo(-length / 2, -half)
    p.lineTo(length / 2 - tip, -half)
    p.lineTo(length / 2, 0)
    p.lineTo(length / 2 - tip, half)
    p.lineTo(-length / 2, half)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.line(length / 2 - tip, -half, length / 2 - tip * 0.25, 0)
    c.line(length / 2 - tip, half, length / 2 - tip * 0.25, 0)
    c.line(-length / 2 + width * 0.9, -half, -length / 2 + width * 0.9, half)
    c.restoreState()


def _ruler(c, x, y, size, angle_deg) -> None:
    """A set-square: a right-angle triangle with tick marks along the base,
    the same shape the reference cover pairs with the pencil."""
    c.saveState()
    c.translate(x, y)
    c.rotate(angle_deg)
    p = c.beginPath()
    p.moveTo(0, 0)
    p.lineTo(size, 0)
    p.lineTo(0, size * 0.62)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    ticks = 5
    for i in range(1, ticks):
        tx = size * i / ticks
        c.line(tx, 0, tx, size * 0.62 * (1 - i / ticks) * 0.22)
    c.restoreState()


def _detail_maths(c, x, y, w, h, font: str = "") -> None:
    # Four groups, each confined to its own corner of the band so their
    # silhouettes stay readable: the first pass let the pencil and the grid
    # cross each other, which read as clutter rather than a desk scene. The
    # reference mockup keeps the grid and the pie up top, the pencil resting
    # on the ruler low and to the right, and the operators alone at the foot,
    # touching nothing else.
    #
    # Operators, isolated at the foot of the band.
    ox, oy, d = x + w * 0.05, y + h * 0.08, w * 0.045
    for i, glyph in enumerate(("+", "-", "x", "/")):
        px, py = ox + (i % 2) * d * 3.0, oy + (i // 2) * d * 2.6
        if glyph == "+":
            c.line(px - d, py, px + d, py)
            c.line(px, py - d, px, py + d)
        elif glyph == "-":
            c.line(px - d, py, px + d, py)
        elif glyph == "x":
            c.line(px - d * 0.7, py - d * 0.7, px + d * 0.7, py + d * 0.7)
            c.line(px - d * 0.7, py + d * 0.7, px + d * 0.7, py - d * 0.7)
        else:
            c.line(px - d, py, px + d, py)
            c.circle(px, py + d * 0.6, d * 0.16, stroke=1, fill=1)
            c.circle(px, py - d * 0.6, d * 0.16, stroke=1, fill=1)

    # A short, wide lattice along the top of the band, well clear of the
    # pencil below it.
    cols, rows_n = 5, 2
    step = w * 0.11
    gx, gy = x + w * 0.30, y + h * 0.66
    gh = h * 0.30
    for i in range(cols + 1):
        c.line(gx + i * step, gy, gx + i * step, gy + gh)
    for i in range(rows_n + 1):
        c.line(gx, gy + i * gh / rows_n, gx + cols * step, gy + i * gh / rows_n)

    # A pie wedge at the top corner, beside the grid rather than crossing it.
    cx, cy, r = x + w * 0.93, y + h * 0.88, w * 0.06
    c.circle(cx, cy, r, stroke=1, fill=0)
    c.line(cx, cy, cx, cy + r)
    c.line(cx, cy, cx + r * math.cos(math.radians(-30)),
           cy + r * math.sin(math.radians(-30)))

    # The ruler low and mostly level, the pencil resting across it, tip near
    # the ruler's corner: the one place these two are meant to touch.
    _ruler(c, x + w * 0.42, y + h * 0.02, w * 0.42, 5)
    _pencil(c, x + w * 0.66, y + h * 0.30, w * 0.50, w * 0.042, 54)


# --------------------------------------------------------------------------
# The maths hero illustration
#
# math_cover_reference.png does not carry the page-and-mark motif every other
# subject gets: the pencil, ruler, pie and grid fill that whole space instead,
# full size and in colour, not a faint line reduced to fit beside the mark.
# Two passes at squeezing that scene into the small secondary band alongside
# the mark could not close that gap, because the reference was never doing
# that; it replaces the mark for this one subject rather than sharing space
# with it. _detail_maths above stays for anywhere still calling detail_for
# directly (scripts, tests); render_cover routes maths to this instead.
# --------------------------------------------------------------------------
PENCIL_WOOD = HexColor("#E8D3A3")
PENCIL_FERRULE = HexColor("#DCE6F5")


def _hero_pencil(c, cx, cy, length, width, angle_deg) -> None:
    """A filled pencil in navy, with a pale ferrule band and a wood tip,
    the same three bands the reference photo shows, not a single outline."""
    c.saveState()
    c.translate(cx, cy)
    c.rotate(angle_deg)
    half = width / 2.0
    tip = length * 0.15
    wood = tip * 0.62
    barrel_end = length / 2 - tip

    def body(x0, x1):
        p = c.beginPath()
        p.moveTo(x0, -half)
        p.lineTo(x1, -half)
        p.lineTo(x1, half)
        p.lineTo(x0, half)
        p.close()
        return p

    c.setFillColor(NAVY)
    c.setStrokeColor(NAVY)
    c.drawPath(body(-length / 2, barrel_end - width * 0.55), stroke=1, fill=1)
    c.setFillColor(PENCIL_FERRULE)
    c.drawPath(body(barrel_end - width * 0.55, barrel_end), stroke=1, fill=1)
    # The highlight stripe that reads as a rounded barrel rather than a flat
    # bar, one line's width in from the top edge.
    c.setStrokeColor(BLUE_MID)
    c.setLineWidth(width * 0.16)
    c.line(-length / 2 + width * 0.3, half * 0.35,
           barrel_end - width * 0.65, half * 0.35)
    # Wood cone, then the graphite point.
    c.setFillColor(PENCIL_WOOD)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.0)
    p = c.beginPath()
    p.moveTo(barrel_end, -half)
    p.lineTo(barrel_end + wood, -half * 0.18)
    p.lineTo(barrel_end + wood, half * 0.18)
    p.lineTo(barrel_end, half)
    p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.setFillColor(NAVY)
    p = c.beginPath()
    p.moveTo(barrel_end + wood, -half * 0.18)
    p.lineTo(length / 2, 0)
    p.lineTo(barrel_end + wood, half * 0.18)
    p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.restoreState()


def _hero_ruler(c, x, y, size, angle_deg) -> None:
    """A translucent set-square: filled pale blue, navy edge, ticks along
    the base, the shape the reference cover rests the pencil against."""
    c.saveState()
    c.translate(x, y)
    c.rotate(angle_deg)
    c.setFillColor(BLUE_PALE)
    c.setFillAlpha(0.55)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.4)
    p = c.beginPath()
    p.moveTo(0, 0)
    p.lineTo(size, 0)
    p.lineTo(0, size * 0.62)
    p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.setFillAlpha(1)
    ticks = 8
    for i in range(1, ticks):
        tx = size * i / ticks
        c.line(tx, 0, tx, size * 0.62 * (1 - i / ticks) * 0.22)
    c.restoreState()


def _hero_maths(c, x, y, w, h) -> None:
    """The maths cover's dominant lower-right graphic, replacing the page
    motif for this subject. See the module note above _hero_pencil."""
    # Grid, upper area, pale and behind everything else.
    c.setStrokeColor(BLUE_FAINT)
    c.setLineWidth(1.3)
    cols, rows_n = 6, 4
    step = w * 0.075
    gx, gy = x + w * 0.28, y + h * 0.20
    gh = h * 0.40
    for i in range(cols + 1):
        c.line(gx + i * step, gy, gx + i * step, gy + gh)
    for i in range(rows_n + 1):
        c.line(gx, gy + i * gh / rows_n, gx + cols * step, gy + i * gh / rows_n)

    # Pie, filled two-tone, top right corner, clear of the pencil's tip.
    cx, cy, r = x + w * 0.93, y + h * 0.98, w * 0.075
    c.setFillColor(BLUE_MIST)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.6)
    c.circle(cx, cy, r, stroke=1, fill=1)
    wedge = c.beginPath()
    wedge.moveTo(cx, cy)
    wedge.lineTo(cx, cy + r)
    steps = 16
    for i in range(steps + 1):
        ang = math.radians(90 - 120 * i / steps)
        wedge.lineTo(cx + r * math.cos(ang), cy + r * math.sin(ang))
    wedge.close()
    c.setFillColor(BLUE_MID)
    c.drawPath(wedge, stroke=1, fill=1)
    c.line(cx, cy, cx, cy + r)

    # Operators, bold and filled, lower left, clear of everything else.
    c.setFillColor(NAVY)
    c.setStrokeColor(NAVY)
    ox, oy, d = x + w * 0.02, y + h * 0.06, w * 0.05
    c.setLineWidth(d * 0.55)
    c.setLineCap(1)
    for i, glyph in enumerate(("+", "-", "x", "/")):
        px, py = ox + (i % 2) * d * 3.0, oy + (i // 2) * d * 2.8
        if glyph == "+":
            c.line(px - d, py, px + d, py)
            c.line(px, py - d, px, py + d)
        elif glyph == "-":
            c.line(px - d, py, px + d, py)
        elif glyph == "x":
            c.line(px - d * 0.7, py - d * 0.7, px + d * 0.7, py + d * 0.7)
            c.line(px - d * 0.7, py + d * 0.7, px + d * 0.7, py - d * 0.7)
        else:
            c.line(px - d, py, px + d, py)
            c.circle(px, py + d * 0.6, d * 0.18, stroke=0, fill=1)
            c.circle(px, py - d * 0.6, d * 0.18, stroke=0, fill=1)

    # Ruler low and level; the pencil resting across it, tip at the corner,
    # kept short of the pie so the two shapes don't collide.
    _hero_ruler(c, x + w * 0.40, y + h * 0.02, w * 0.52, 5)
    _hero_pencil(c, x + w * 0.60, y + h * 0.26, w * 0.58, w * 0.052, 54)


def _detail_english(c, x, y, w, h, font: str = "Helvetica-Bold") -> None:
    # An opening quotation mark over ruled lines of prose. Hand-built quote
    # shapes came out as two rounded blocks; the typographic glyph is the same
    # mark the reference cover uses and is unmistakable at any size.
    c.saveState()
    c.setFont(font, w * 0.42)
    c.drawString(x + w * 0.02, y + h * 0.60, "“")
    c.restoreState()
    for i, frac in enumerate((1.0, 0.94, 0.72, 0.86, 0.55)):
        ly = y + h * (0.44 - i * 0.09)
        c.setLineWidth(2.6)
        c.line(x + w * (0.30 if i % 2 else 0.22), ly,
               x + w * (0.30 if i % 2 else 0.22) + w * 0.55 * frac, ly)


def _detail_science(c, x, y, w, h, font: str = "") -> None:
    cx, cy = x + w * 0.42, y + h * 0.52
    for r in (w * 0.10, w * 0.22, w * 0.34):
        c.circle(cx, cy, r, stroke=1, fill=0)
    c.saveState()
    c.translate(cx, cy)
    for angle in (0, 60, 120):
        c.saveState()
        c.rotate(angle)
        p = c.beginPath()
        p.moveTo(-w * 0.44, 0)
        p.curveTo(-w * 0.44, w * 0.30, w * 0.44, w * 0.30, w * 0.44, 0)
        p.curveTo(w * 0.44, -w * 0.30, -w * 0.44, -w * 0.30, -w * 0.44, 0)
        c.drawPath(p, stroke=1, fill=0)
        c.restoreState()
    c.restoreState()
    c.circle(cx, cy, w * 0.035, stroke=0, fill=1)


def _detail_quantitative(c, x, y, w, h, font: str = "") -> None:
    step = min(w * 0.13, h * 0.17)
    for row in range(3):
        for col in range(4):
            bx = x + w * 0.08 + col * step * 1.35
            by = y + h * 0.80 - (row + 1) * step * 1.35
            if (row + col) % 3 == 0:
                c.rect(bx, by, step, step, stroke=1, fill=1)
            else:
                c.rect(bx, by, step, step, stroke=1, fill=0)


def _detail_abstract(c, x, y, w, h, font: str = "") -> None:
    # Three rows have to fit inside the band; at w*0.20 the bottom row ran off
    # the foot of the sheet.
    step = min(w * 0.16, h * 0.20)
    for row in range(3):
        for col in range(3):
            bx = x + w * 0.10 + col * step * 1.5
            by = y + h * 0.80 - (row + 1) * step * 1.5
            kind = (row + col) % 3
            if kind == 0:
                c.circle(bx + step / 2, by + step / 2, step / 2, stroke=1, fill=0)
            elif kind == 1:
                c.rect(bx, by, step, step, stroke=1, fill=0)
            else:
                p = c.beginPath()
                p.moveTo(bx, by)
                p.lineTo(bx + step, by)
                p.lineTo(bx + step / 2, by + step)
                p.close()
                c.drawPath(p, stroke=1, fill=0)


def _detail_general(c, x, y, w, h, font: str = "") -> None:
    c.circle(x + w * 0.14, y + h * 0.66, w * 0.11, stroke=1, fill=0)
    c.rect(x + w * 0.34, y + h * 0.52, w * 0.18, w * 0.18, stroke=1, fill=0)
    p = c.beginPath()
    p.moveTo(x + w * 0.62, y + h * 0.52)
    p.lineTo(x + w * 0.84, y + h * 0.52)
    p.lineTo(x + w * 0.73, y + h * 0.74)
    p.close()
    c.drawPath(p, stroke=1, fill=0)
    for i in range(4):
        ly = y + h * (0.36 - i * 0.10)
        c.setLineWidth(2.6)
        c.line(x + w * 0.10, ly, x + w * (0.42 + 0.12 * (i % 3)), ly)


_DETAILS = (
    (("abstract",), _detail_abstract),
    (("quantitative", "numerac"), _detail_quantitative),
    (("general abilit", "reasoning", "aptitude"), _detail_general),
    (("science", "biolog", "chemis", "physic"), _detail_science),
    (("english", "literac", "reading", "writing", "humanit", "history"),
     _detail_english),
    (("math", "numer"), _detail_maths),
)


def detail_for(subject: str, topic: str = ""):
    hay = f"{subject} {topic}".lower()
    for words, fn in _DETAILS:
        if any(w in hay for w in words):
            return fn
    return _detail_general


# --------------------------------------------------------------------------
# The Folio mark
# --------------------------------------------------------------------------

def _logo_reader():
    """An ImageReader for mark-512.png, or None if it cannot be read.

    The file is a palette PNG with transparency, which ReportLab will not mask
    correctly on its own, so it is converted to RGBA first. A missing or
    unreadable logo must never take the booklet down: the cover simply loses
    the mark.
    """
    try:
        from PIL import Image
        return ImageReader(Image.open(LOGO_PATH).convert("RGBA"))
    except Exception as e:                                   # pragma: no cover
        log.info("cover.logo_unavailable", extra={"reason": str(e)[:200]})
        return None


LOGO_ASPECT = 512.0 / 475.0        # height / width of mark-512.png


def _draw_logo(c, reader, x, y, width) -> None:
    if reader is None:
        return
    c.drawImage(reader, x, y, width=width, height=width * LOGO_ASPECT,
                mask="auto", preserveAspectRatio=True, anchor="sw")


def _page_motif(c, v: Variant, reader) -> None:
    """The large lower-right visual: layered page shapes with the Folio mark
    sitting on the front one, running off the right edge the way the reference
    mockups do."""
    logo_w = W * 0.33
    logo_h = logo_w * LOGO_ASPECT
    lx = W * 0.60
    ly = H * 0.055
    # Two page shapes fanned out behind it. Rounded and rotated, not squared
    # off: the brief asks for depth through layering, not drop shadows.
    for dx, dy, ang, colour, alpha in (
            (logo_w * 0.42, -logo_h * 0.10, -9, v.waves[-1][0], 0.85),
            (logo_w * 0.26, -logo_h * 0.02, -4, v.waves[-2][0], 0.9)):
        c.saveState()
        c.setFillColor(colour)
        c.setFillAlpha(alpha)
        c.translate(lx + dx + logo_w * 0.4, ly + dy + logo_h * 0.4)
        c.rotate(ang)
        c.roundRect(-logo_w * 0.36, -logo_h * 0.34, logo_w * 0.78,
                    logo_h * 0.74, logo_w * 0.10, stroke=0, fill=1)
        c.restoreState()
    _draw_logo(c, reader, lx, ly, logo_w)


# --------------------------------------------------------------------------
# The cover
# --------------------------------------------------------------------------

def render_cover(c, spec: CoverSpec) -> None:
    """Draw the whole cover onto page 1 of `c`.

    Leaves the canvas graphics state as it found it, so the caller's
    saveState/restoreState around page chrome still balances.
    """
    v = VARIANTS.get(spec.variant, VARIANTS[DEFAULT_VARIANT])
    reg, bold = spec.font_regular, spec.font_bold
    c.saveState()

    # An operator can still override the whole thing with a picture. See
    # booklet_gen/assets/README.md.
    if spec.background_image:
        try:
            c.drawImage(spec.background_image, 0, 0, width=W, height=H,
                        preserveAspectRatio=False, mask="auto")
            c.restoreState()
            return
        except Exception as e:
            log.info("cover.background_image_failed",
                     extra={"reason": str(e)[:200]})

    # 1. Background and the flowing page shapes across the lower third.
    c.setFillColor(v.background)
    c.rect(0, 0, W, H, stroke=0, fill=1)
    for colour, knots in v.waves:
        _wave(c, knots, colour)

    # 2. Subject decoration. Maths gets the dominant lower-right graphic the
    # reference photo actually shows, in place of the page motif, not a faint
    # band beside it; every other subject keeps the mark and gets the small
    # secondary band, matching english_cover_reference.png.
    reader = _logo_reader()
    is_maths = detail_for(spec.subject, spec.topic) is _detail_maths
    if is_maths:
        c.saveState()
        _hero_maths(c, W * 0.04, H * 0.02, W * 0.94, H * 0.48)
        c.restoreState()
    else:
        c.saveState()
        c.setStrokeColor(v.detail)
        c.setFillColor(v.detail)
        c.setStrokeAlpha(v.detail_alpha)
        c.setFillAlpha(v.detail_alpha)
        c.setLineWidth(1.6)
        c.setLineCap(1)
        # The band left of the page motif, held clear of the foot of the sheet.
        detail_for(spec.subject, spec.topic)(c, W * 0.06, H * 0.10, W * 0.46,
                                             H * 0.21, bold)
        c.restoreState()

        _page_motif(c, v, reader)

    # 3. Publisher lockup, top left.
    lock_w = 44.0
    _draw_logo(c, reader, MARGIN, H - 36 - lock_w * LOGO_ASPECT, lock_w)
    tx = MARGIN + lock_w + 14
    top = H - 36 - lock_w * LOGO_ASPECT
    end = _text(c, tx, top + lock_w * LOGO_ASPECT - 21, "FOLIO", bold, 24, v.ink)
    _text(c, end + 7, top + lock_w * LOGO_ASPECT - 21, "AI", bold, 24, ACCENT)
    _text(c, tx + 1, top + lock_w * LOGO_ASPECT - 40, "practice booklets",
          bold, 11.5, v.muted)

    # 4. Product line, then the booklet-type pill, then the title.
    #
    # The vertical rhythm below is measured off the two reference mockups: the
    # pill sits at about a fifth down the sheet, the title runs from a quarter
    # to a third, and the detail rows finish around three fifths, which is
    # where the page motif takes over.
    y = H * 0.815
    if spec.eyebrow:
        # Letterspaced small caps, which only a text object can do: it reads as
        # the series line on a publisher's cover rather than a second title.
        c.saveState()
        t = c.beginText(MARGIN, y)
        t.setFont(bold, 9)
        t.setFillColor(v.muted)
        t.setCharSpace(1.6)
        t.textOut(spec.eyebrow.upper())
        c.drawText(t)
        c.restoreState()
    y -= 30
    if spec.pill:
        pw = pdfmetrics.stringWidth(spec.pill, bold, 11) + 30
        c.setFillColor(v.pill_fill)
        c.roundRect(MARGIN, y - 8, pw, 26, 13, stroke=0, fill=1)
        _text(c, MARGIN + 15, y, spec.pill, bold, 11, v.pill_ink)

    y -= 56
    for line in spec.title_lines:
        size = _fit(line, bold, 40, W - 2 * MARGIN - W * 0.06)
        _text(c, MARGIN, y, line, bold, size, v.ink)
        y -= 48

    # The accent rule that separates the title from the detail rows.
    y += 16
    c.setFillColor(v.rule)
    c.roundRect(MARGIN, y, 150, 5, 2.5, stroke=0, fill=1)

    # 5. Topic / Name / Week rows, each with a small stroked glyph.
    y -= 44
    rows = [("topic", "Topic:", spec.topic),
            ("name", "Name:", spec.student_name),
            ("week", "Week:", spec.week),
            ("difficulty", "Level:", spec.difficulty)]
    for icon, label, value in rows:
        if not value:
            continue
        c.saveState()
        c.setStrokeColor(v.ink)
        c.setFillColor(v.ink)
        c.setLineWidth(1.5)
        c.setLineJoin(1)
        c.setLineCap(1)
        _ROW_ICONS[icon](c, MARGIN, y - 2, 15)
        c.restoreState()
        end = _text(c, MARGIN + 26, y, label, bold, 12.5, v.ink)
        _text(c, end + 8, y, value, reg, 12.5, v.muted)
        y -= 27

    # 6. Date and estimated time, then the sentence about the answer key.
    y -= 8
    for line in spec.meta_lines:
        _text(c, MARGIN, y, line, reg, 10, v.muted)
        y -= 15
    if spec.footer_note:
        y -= 12
        # Held to the left column: the page motif owns the right of the sheet
        # from about here down.
        for line in _wrap(spec.footer_note, reg, 9.5, W * 0.60):
            _text(c, MARGIN, y, line, reg, 9.5, v.muted)
            y -= 13

    c.restoreState()
