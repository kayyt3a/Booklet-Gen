# FolioAI booklet cover design system

The permanent visual design for every booklet cover, unless explicitly told
otherwise. Given by the founder on 2026-08-17, together with two reference
covers: `design_reference/english_cover_reference.png` (Year 8 English) and
`design_reference/math_cover_reference.png` (Year 6 Mathematics). Read those
two images alongside this file before touching cover code; they show the
composition this spec describes in words.

## Core visual identity

- Premium educational workbook aesthetic
- Clean, modern, minimal
- Strong use of white space
- Deep navy as the primary brand colour
- Pale powder blue as the main secondary colour
- Very light blue and warm off-white as supporting backgrounds
- Soft layered curves inspired by turning pages
- Folio booklet/page logo used as the key visual motif
- Rounded, smooth geometric forms
- Subtle depth through layering rather than heavy shadows
- Professional enough for parents and tutors
- Friendly enough for school students
- Must feel like a real educational publisher, not an AI startup template

Avoid: AI sparkles, robots, circuitry, brains, neon effects, excessive
gradients, cartoon illustrations, busy backgrounds, generic stock graphics,
random decorative icons.

## Standard A4 cover structure

Every cover uses the same hierarchy.

### 1. Top branding area

Folio AI logo in the upper-left, small, behaving like a publisher mark, not
the main feature. Beside or underneath it: "FOLIO AI" / "practice booklets".
Generous breathing room around it.

### 2. Main booklet title

Upper-middle third. Bold, modern sans-serif, left aligned. Deep navy on
light covers, white on dark covers. Must read from a thumbnail. E.g.
"Year 6 / Mathematics" or "Abstract / Reasoning".

### 3. Booklet type pill

Small rounded pill under the title: "Practice Booklet" (also allowed:
"Practice Exam", "Revision Booklet", "Weekly Practice", "Assessment
Preparation"). Small and understated.

### 4. Main visual motif

Lower half: a large stylised booklet/page shape containing the Folio F, as
though pages are turning, 2-3 layered page shapes behind it in pale blues
and navy, may extend off the edge. Not centred on the page. Feels integrated
into the layout, not dropped on top of it.

### 5. Flowing background shapes

Large curved shapes sweeping across the bottom third: paper, pages, layered
waves, turning sheets. Large simple shapes, not thin decorative lines. They
visually connect the main booklet icon to the bottom of the page.

## Approved cover families

**LIGHT BLUE** — background very light blue/white, navy title, pale blue
flowing shapes with navy and blue Folio booklet. Best for Mathematics,
English, general academic subjects.

**DARK NAVY** — background deep navy, white title, pale blue secondary text,
large white and pale blue page shapes entering from the lower-right. Best
for Science, advanced subjects, assessments and premium booklets.

**WHITE** — white background, navy title, very subtle pale blue page waves
across the bottom. Best for primary school and general practice material.

**WARM OFF-WHITE** — warm ivory/cream background, navy title, warm white
page shapes combined with pale blue. Best for General Abilities, reasoning
and premium workbook collections.

## Subject differentiation

Layout stays constant; subjects differ only through subtle secondary detail,
never a redesign:

- Mathematics: subtle grids, geometry curves, small mathematical pattern
  details.
- Science: soft circular forms, orbital curves, diagram-like geometry.
- English: page lines, quotation-inspired curves, editorial layout details.
- Quantitative Reasoning: numerical grids, sequences, geometric blocks.
- Abstract Reasoning: subtle matrices, rotations, shape sequences.
- General Abilities: a mixture of simple shapes, lines and page motifs.

The Folio booklet/page mark remains the dominant brand element throughout;
subject detail is always secondary.

## Layout consistency

All Folio booklets should look like they belong on the same shelf. Do not
redesign the cover per subject. Keep constant: logo position, title
alignment, typography, margins, booklet-type pill, lower-page composition,
overall visual hierarchy. Vary only: background variant, subject, year
level, topic, small subject pattern, accent blue, page arrangement.

## Dynamic fields

YEAR_LEVEL, SUBJECT, TOPIC, BOOKLET_TYPE, STUDENT_NAME, WEEK_NUMBER,
DIFFICULTY, COVER_VARIANT.

Example: "Year 6 / Mathematics", topic "Fractions and Decimals", type
"Practice Booklet", name "Kieran Tran", "Week 4".

## Final design goal

Several Folio booklets placed side by side should immediately read as one
coordinated educational publishing series: a modern textbook publisher, a
premium worksheet brand, and a clean SaaS design system, at once. Polished
enough to sell as a professional educational resource. Must not look
AI-generated.
