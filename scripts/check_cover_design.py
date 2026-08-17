#!/usr/bin/env python3
"""Check the booklet cover against the founder's design system.

`booklet_gen/assets/COVER_DESIGN_SYSTEM.md` is binding, so this asserts the
parts of it a machine can see. Real booklets are rendered, the PDFs are read
back with pypdf, and page 1 is rasterised with PyMuPDF so the colours asserted
are the colours that come off a printer, not the colours in the source.

Written to fail against the old cover: that one was a single full-bleed JPEG
with centred text over it, so it had no per-subject variation, no topic line,
no name row, no pill, and page 1 carried a large embedded image.

Covers:
  * one cover family per booklet, chosen from subject/program/year, and all
    four approved families reachable
  * the fixed composition: wordmark, pill, "Year N" over the subject, topic,
    name, and the week row only on a term-plan week
  * the answer-key sentence still tracks whether every answer was verified
  * page 1 is drawn, not a photograph: no large raster image, many vector fills
  * the drawn cover replaces the old static background, and the environment
    override still works
  * the exam paper's plain cover is untouched
  * no em dash anywhere on the cover

Usage:  python scripts/check_cover_design.py [--png OUTDIR]
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf                                                    # noqa: E402
import pymupdf                                                  # noqa: E402

from booklet_gen import formatter as F                          # noqa: E402
from booklet_gen.formatter import (                             # noqa: E402
    cover_pill, cover_spec, cover_topic, render_exam_pdf, render_pdf)
from booklet_gen.schemas import (                               # noqa: E402
    BookletData, ExamPaper, ExamSection, Question, SubtopicOutput,
    SubtopicTeaching, ValidatedQuestion, WorkedExample)
from booklet_gen.visuals.cover import VARIANTS, variant_for     # noqa: E402

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# Booklets to render
# ---------------------------------------------------------------------------

def vq(text, answer="12", verified=True):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working="6 x 2 = 12",
                          difficulty="medium"),
        verified=verified)


def teaching():
    return SubtopicTeaching(
        intro_paragraphs=["A short mini-lesson paragraph explaining the idea."],
        key_points=["Point one.", "Point two."],
        worked_example=WorkedExample(
            question="A box is 5 cm by 3 cm by 4 cm. What is its volume?",
            steps=["Multiply length by width: 5 x 3 = 15.",
                   "Multiply that by the height: 15 x 4 = 60."],
            answer="60 cubic centimetres"),
        guided_examples=[WorkedExample(
            question="Find the volume of a 4 cm by 2 cm by 3 cm prism.",
            steps=["Multiply the length and width."], answer="24 cubic cm")])


def booklet(subject, year, name, topic, program=None, week=None, weeks=None,
            focus=None, verified=True, topic2=None):
    sections = [SubtopicOutput(
        topic=topic, subtopic="First subtopic", teaching=teaching(),
        questions=[vq(f"Question {i}: work out 6 x 2.", verified=verified)
                   for i in range(3)],
        homework_questions=[vq(f"Homework {i}: work out 7 x 3.", "21")
                            for i in range(4)],
        estimated_minutes=12)]
    if topic2:
        sections.append(SubtopicOutput(
            topic=topic2, subtopic="Second subtopic", teaching=teaching(),
            questions=[vq("Question: work out 8 x 2.")],
            homework_questions=[], estimated_minutes=8))
    return BookletData(
        subject=subject, year_level=year, student_name=name,
        program_label=program, week_number=week, total_weeks=weeks,
        week_focus=focus, sections=sections,
        recap_questions=[vq("Warm-up: what is 9 x 4?", "36")],
        challenge_questions=[vq("Challenge: find the volume.", "75")])


CASES = {
    "maths": booklet("Mathematics", "Year 6", "Kieran Tran",
                     "Fractions and Decimals", "Academic Accelerate"),
    "english": booklet("English", "Year 8", "Amelia Chen", "Literary Analysis",
                       "Academic Accelerate"),
    "science": booklet("Science", "Year 9", "Noah Patel", "Chemical Reactions",
                       "Academic Accelerate"),
    "reasoning": booklet("General Abilities", "Year 6", "Priya Nair",
                         "Abstract Reasoning", "Scholarships"),
    "primary": booklet("Mathematics", "Year 2", "Ollie Brown", "Counting"),
    "naplan": booklet("Mathematics and English", "Year 5", "Sam",
                      "Number", "NAPLAN Practice", topic2="Reading"),
    "termweek": booklet("English", "Year 4", "Harriet Wu", "Persuasive Writing",
                        "Academic Accelerate", week=3, weeks=10,
                        focus="Persuasive devices"),
    "unverified": booklet("Mathematics", "Year 7", "Jesse Lee", "Algebra",
                          verified=False),
}

tmp = Path(tempfile.mkdtemp(prefix="folio-cover-"))
png_dir = None
if "--png" in sys.argv:
    png_dir = Path(sys.argv[sys.argv.index("--png") + 1])
    png_dir.mkdir(parents=True, exist_ok=True)

rendered: dict[str, Path] = {}
for label, data in CASES.items():
    rendered[label] = render_pdf(data, tmp / f"{label}.pdf")
    if png_dir:
        doc = pymupdf.open(rendered[label])
        doc[0].get_pixmap(dpi=150).save(png_dir / f"{label}.png")
        doc.close()


def page1_text(path: Path) -> str:
    return " ".join(pypdf.PdfReader(str(path)).pages[0].extract_text().split())


def page1_pixel(path: Path, fx: float, fy: float):
    """The printed colour at a point on page 1, as (r, g, b)."""
    doc = pymupdf.open(path)
    pix = doc[0].get_pixmap(dpi=72)
    x, y = int(pix.width * fx), int(pix.height * fy)
    px = pix.pixel(x, y)
    doc.close()
    return px[:3]


# ---------------------------------------------------------------------------
print("\nVariant selection covers all four approved families")
# ---------------------------------------------------------------------------
chosen = {label: cover_spec(d).variant for label, d in CASES.items()}
for label, want in (("maths", "light_blue"), ("english", "light_blue"),
                    ("science", "dark_navy"), ("reasoning", "warm"),
                    ("primary", "white")):
    check(chosen[label] == want, f"{label} lands on the {want} family",
          f"got {chosen[label]}")
check(set(chosen.values()) >= {"light_blue", "dark_navy", "warm", "white"},
      "all four families are reachable from real booklet data",
      str(sorted(set(chosen.values()))))
check(all(v in VARIANTS for v in chosen.values()),
      "every chosen family is a defined variant")
check(variant_for("Mathematics", "", "Year 6") ==
      variant_for("Mathematics", "", "Year 6"),
      "variant selection is deterministic")

# The old cover was one image for every booklet. This is the assertion that
# fails against it.
check(len(set(chosen.values())) >= 3,
      "different subjects no longer share one identical cover",
      f"{len(set(chosen.values()))} distinct families across {len(chosen)} booklets")

# ---------------------------------------------------------------------------
print("\nThe fixed composition, on every cover")
# ---------------------------------------------------------------------------
for label, path in rendered.items():
    t = page1_text(path)
    data = CASES[label]
    ok = ("FOLIO AI" in t and "practice booklets" in t
          and data.year_level in t and data.student_name in t
          and cover_pill(data) in t and cover_topic(data) in t
          and "Topic:" in t and "Name:" in t)
    check(ok, f"{label}: wordmark, pill, year, subject, topic and name", t[:110])
    check(data.subject in t or data.subject.split(" and ")[0] in t,
          f"{label}: the subject is on the cover")

check("Week: 3 of 10" in page1_text(rendered["termweek"]),
      "a term-plan week prints its week row")
check("Persuasive devices" in page1_text(rendered["termweek"]),
      "and the week focus beside it")
check("Week:" not in page1_text(rendered["maths"]),
      "a standalone booklet prints no week row")
check("Weekly Practice" in page1_text(rendered["termweek"]),
      "a term-plan week is labelled Weekly Practice, not Practice Booklet")
check(cover_topic(CASES["naplan"]) == "Number and Reading",
      "a two-topic booklet names both topics", cover_topic(CASES["naplan"]))

# DIFFICULTY is in the brief's field list but the schema has no source for it.
check(cover_spec(CASES["maths"]).difficulty == "",
      "difficulty is absent rather than invented when nothing supplies it")

# ---------------------------------------------------------------------------
print("\nThe answer-key sentence still tracks what was verified")
# ---------------------------------------------------------------------------
all_ok = page1_text(rendered["maths"])
some_bad = page1_text(rendered["unverified"])
check("Every answer in the key at the back has been checked" in all_ok,
      "a fully verified booklet still says so")
check("Every answer" not in some_bad,
      "a booklet with an unchecked answer drops the absolute claim")
check("a tick marks an answer that has been checked" in some_bad,
      "and points at the tick instead")
check("show your working" in all_ok,
      "an all-maths booklet still asks for working")
check("show your working" not in page1_text(rendered["english"]),
      "an English booklet does not")
check("17 August" in all_ok or "20" in all_ok, "the date line is present")
check("Estimated time" in all_ok, "the estimated time line is present")

# ---------------------------------------------------------------------------
print("\nPage 1 is drawn, not a photograph")
# ---------------------------------------------------------------------------
for label, path in rendered.items():
    page = pypdf.PdfReader(str(path)).pages[0]
    images = list(page.images)
    big = [im for im in images
           if len(im.data) > 60_000]
    check(not big, f"{label}: no full-bleed raster background on page 1",
          f"{len(images)} images, largest {max((len(i.data) for i in images), default=0)}B")
    content = page.get_contents().get_data()
    fills = len(re.findall(rb"(?m)(?:^|\s)f\*?(?=\s|$)", content))
    curves = len(re.findall(rb"(?m)(?:^|\s)c(?=\s|$)", content))
    check(fills >= 5 and curves >= 12,
          f"{label}: the cover is built from vector fills and curves",
          f"{fills} fills, {curves} beziers")

sizes = {label: path.stat().st_size for label, path in rendered.items()}
check(max(sizes.values()) < 900_000,
      "the cover does not dominate the file size",
      f"largest booklet {max(sizes.values()) // 1024}kB")

# ---------------------------------------------------------------------------
print("\nEach family actually prints its own background")
# ---------------------------------------------------------------------------
# Top-left corner, well clear of every drawn element.
corner = {label: page1_pixel(path, 0.02, 0.02)
          for label, path in rendered.items()}
r, g, b = corner["science"]
check(r < 60 and g < 60 and b < 90, "dark navy prints dark", str(corner["science"]))
r, g, b = corner["reasoning"]
check(r > 240 and b < r - 8, "warm off-white prints warm", str(corner["reasoning"]))
r, g, b = corner["primary"]
check((r, g, b) == (255, 255, 255), "white prints white", str(corner["primary"]))
r, g, b = corner["maths"]
check(b >= r and b > 245 and r < 255, "light blue prints a blue tint",
      str(corner["maths"]))
# And the lower third carries the page shapes on a light family.
check(page1_pixel(rendered["maths"], 0.5, 0.97) !=
      page1_pixel(rendered["maths"], 0.02, 0.02),
      "the lower third carries flowing page shapes")

# ---------------------------------------------------------------------------
print("\nThe old static-image path")
# ---------------------------------------------------------------------------
check(F.cover_background_path() is None,
      "an unset FOLIO_COVER_BACKGROUND means no static override")
check(not (Path(F.ASSET_DIR) / "cover_background.png").exists(),
      "the retired 1.4MB cover PNG is gone")
check("cover_background.png" not in
      (Path(F.ASSET_DIR) / "README.md").read_text(encoding="utf-8")
      .split("## Static image override")[0],
      "the assets README no longer tells anyone to drop a PNG in")

os.environ["FOLIO_COVER_BACKGROUND"] = str(Path(F.ASSET_DIR) / "cover_background.jpg")
try:
    check(F.cover_background_path() is not None,
          "the environment override is still honoured")
    override = render_pdf(CASES["maths"], tmp / "override.pdf")
    imgs = list(pypdf.PdfReader(str(override)).pages[0].images)
    check(any(len(im.data) > 60_000 for im in imgs),
          "and an overridden cover really is the image",
          f"{len(imgs)} images on page 1")
finally:
    del os.environ["FOLIO_COVER_BACKGROUND"]

# ---------------------------------------------------------------------------
print("\nHouse style")
# ---------------------------------------------------------------------------
for label, path in rendered.items():
    check("—" not in page1_text(path), f"{label}: no em dash on the cover")
src = (Path(__file__).resolve().parent.parent
       / "booklet_gen" / "visuals" / "cover.py").read_text(encoding="utf-8")
check("—" not in src, "no em dash in cover.py")

# ---------------------------------------------------------------------------
print("\nExam papers keep their plain cover")
# ---------------------------------------------------------------------------
paper = ExamPaper(
    subject="Mathematics Methods", year_level="Year 12", student_name="Riley",
    unit="Units 3 and 4", reading_minutes=10, working_minutes=100,
    sections=[ExamSection(
        name="Section One: Calculator-free", calculator_allowed=False,
        questions=[ValidatedQuestion(
            question=Question(question="Differentiate y = 3x^2.",
                              answer="6x", working="Power rule.",
                              difficulty="medium", marks=2),
            verified=True)])])
exam = render_exam_pdf(paper, tmp / "exam.pdf")
exam_t = page1_text(exam)
check("Practice Examination" in exam_t, "the exam cover still renders", exam_t[:80])
check("Practice Booklet" not in exam_t and "practice booklets" not in exam_t,
      "and the booklet cover has not leaked onto it")
check(page1_pixel(exam, 0.02, 0.02) == (255, 255, 255),
      "the exam front page is still plain white",
      str(page1_pixel(exam, 0.02, 0.02)))
check(not list(pypdf.PdfReader(str(exam)).pages[0].images),
      "and carries no cover artwork")

print(f"\nPDFs written to {tmp}")
if png_dir:
    print(f"Page-1 renders written to {png_dir}")
print(f"\n{len(failures)} failure(s)" + (": " + ", ".join(failures) if failures else ""))
sys.exit(1 if failures else 0)
