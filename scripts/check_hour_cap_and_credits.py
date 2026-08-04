"""Three fixes the owner reported once, none of which had a check.

Against a shipped Year 5 booklet the owner reported:

  * Class Work said "about 100 min" for what is sold as a one hour lesson.
  * "Now you try:" printed over an empty space, immediately under the next
    heading, because that subtopic's practice had been moved to Homework.
  * Every picture carried its Wikimedia licence line underneath it, which on a
    child's worksheet reads as clutter.

All three were fixed. None of the three was pinned by anything, and they live
in code paths ordinary formatter and pipeline work touches, so this file exists
to fail loudly if any of them comes back.

Run: python scripts/check_hour_cap_and_credits.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pypdf
from PIL import Image as PILImage

from booklet_gen.formatter import image_credits, image_is_usable, render_pdf
from booklet_gen.pipeline import (BookletPipeline, CLASSWORK_CAP_MINUTES,
                                  MIN_CLASSWORK_SUBTOPICS)
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)
from booklet_gen.timing import booklet_timing, classwork_section_minutes

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def vq(text: str, answer: str = "42", difficulty: str = "medium") -> ValidatedQuestion:
    return ValidatedQuestion(
        question=Question(question=text, answer=answer, working=answer,
                          difficulty=difficulty),
        verified=True)


def teaching(name: str, n_guided: int = 2) -> SubtopicTeaching:
    return SubtopicTeaching(
        intro_paragraphs=[
            f"{name} is a skill you build one step at a time. Read this through "
            "before you try the questions, because every question below uses "
            "the same method."],
        key_points=[f"{name} always starts the same way.",
                    "Check your answer against the question when you finish."],
        worked_example=WorkedExample(
            question=f"A worked {name.lower()} question to watch first.",
            steps=["Read what the question asks.", "Do the method.",
                   "State the answer."],
            answer="The answer"),
        guided_examples=[
            WorkedExample(question=f"Guided {name.lower()} example {i + 1}.",
                          steps=["Start it.", "Finish it."], answer="Done")
            for i in range(n_guided)])


def overloaded_sections(n: int = 6, per: int = 8) -> list[SubtopicOutput]:
    """A booklet far over the hour: every subtopic teaches and then drills."""
    return [
        SubtopicOutput(
            topic="Number", subtopic=f"SUBTOPIC{i}",
            teaching=teaching(f"Skill {i}"),
            questions=[vq(f"CLASSWORK {i}.{j}: work this one out carefully and "
                          "show every step of your method.", difficulty="hard")
                       for j in range(per)],
            homework_questions=[vq(f"HOMEWORK {i}.{j}: practise it again.",
                                   difficulty="easy") for j in range(2)])
        for i in range(n)]


def in_session(sections) -> list:
    return [s for s in sections if s.questions]


def session_minutes(sections) -> float:
    return sum(classwork_section_minutes(s) for s in in_session(sections))


def question_count(sections) -> int:
    return sum(len(s.questions) + len(s.homework_questions) for s in sections)


# ---------------------------------------------------------------------------
# 1. The hour cap holds, and nothing is thrown away to make it hold
# ---------------------------------------------------------------------------
print("\nThe hour cap")
print("-" * 62)

sections = overloaded_sections()
before_minutes = session_minutes(sections)
before_questions = question_count(sections)

check(before_minutes > CLASSWORK_CAP_MINUTES * 1.5,
      "the fixture really is over the cap before trimming",
      f"{before_minutes:.0f} min against a {CLASSWORK_CAP_MINUTES} min cap")

BookletPipeline._fit_classwork_to_cap(sections)
after_minutes = session_minutes(sections)

check(after_minutes <= CLASSWORK_CAP_MINUTES,
      "an overloaded booklet is trimmed to the cap",
      f"{after_minutes:.1f} min")
check(question_count(sections) == before_questions,
      "the surplus is moved to homework, never deleted",
      f"{before_questions} questions in, {question_count(sections)} out")
check(any(not s.questions for s in sections)
      and all(s.questions for s in in_session(sections)),
      "subtopics either stay in the session with practice or leave it entirely",
      f"{len(in_session(sections))} taught, "
      f"{sum(1 for s in sections if not s.questions)} moved out")
check(len(in_session(sections)) >= MIN_CLASSWORK_SUBTOPICS,
      "the session keeps enough subtopics to be worth sitting down for",
      f"{len(in_session(sections))} subtopics")
check(all(s.teaching is not None for s in sections),
      "a subtopic pushed out of the hour keeps its mini-lesson")

# The complaint was about the number on the page, not the plan behind it, and
# the two are computed by different functions. This is the one that has to hold.
data = BookletData(subject="English", year_level="Year 5", student_name="Sam",
                   sections=sections)
printed = booklet_timing(data)["classwork_minutes"]
check(printed <= CLASSWORK_CAP_MINUTES,
      "and the number PRINTED on the page is within the hour",
      f"Class Work says about {printed} min")

# A booklet that already fits is left alone: the cap must not trim for its own
# sake and hand a parent a thinner booklet than they paid for.
small = overloaded_sections(n=3, per=2)
fits_before = [len(s.questions) for s in small]
BookletPipeline._fit_classwork_to_cap(small)
check([len(s.questions) for s in small] == fits_before,
      "a booklet already inside the hour is not trimmed",
      f"{fits_before} questions per subtopic, unchanged")

# The floor: even one question each can exceed the cap if there are enough
# subtopics, because teaching dominates. Subtopics have to go, not questions.
many = overloaded_sections(n=12, per=1)
BookletPipeline._fit_classwork_to_cap(many)
check(session_minutes(many) <= CLASSWORK_CAP_MINUTES
      or len(in_session(many)) == MIN_CLASSWORK_SUBTOPICS,
      "when teaching alone busts the hour, whole subtopics move out",
      f"{len(in_session(many))} of 12 subtopics taught, "
      f"{session_minutes(many):.0f} min")


# ---------------------------------------------------------------------------
# 2. "Now you try:" is never printed over an empty space
# ---------------------------------------------------------------------------
print("\n\"Now you try:\" always has something to try")
print("-" * 62)

tmp = Path(tempfile.mkdtemp(prefix="folio-cap-check-"))


def page_texts(path: Path) -> list[str]:
    return [p.extract_text() or "" for p in pypdf.PdfReader(str(path)).pages]


# Straight from the reported case: the trimmed booklet above, where the cap has
# emptied some subtopics' class work.
emptied = [s for s in sections if not s.questions]
check(bool(emptied),
      "the trimmed booklet really does contain an emptied subtopic",
      f"{len(emptied)} of {len(sections)} moved to homework")

data.student_name = "Sam"
pdf = render_pdf(data, tmp / "trimmed.pdf")
pages = page_texts(pdf)
text = "\n".join(pages)
classwork_text = text.split("Homework")[0]

taught = len(in_session(sections))
check(classwork_text.count("Now you try:") == taught,
      "one \"Now you try:\" per subtopic actually taught in the hour",
      f"{classwork_text.count('Now you try:')} printed, {taught} taught")

# Structural, not just a count: after each label the next thing on the page has
# to be that subtopic's practice, not the next heading.
for i, piece in enumerate(classwork_text.split("Now you try:")[1:], 1):
    q_at = piece.find("CLASSWORK")
    heading_at = piece.find("SUBTOPIC")
    check(q_at >= 0 and (heading_at < 0 or q_at < heading_at),
          f"\"Now you try:\" #{i} is followed by practice, not by a heading",
          piece.strip()[:48].replace("\n", " "))

# An emptied subtopic still teaches, down in Homework where its practice went.
for s in emptied:
    check(s.subtopic in text,
          f"the moved subtopic {s.subtopic} is still in the booklet")
    check("Now you try:" not in classwork_text.split(s.subtopic)[-1][:200]
          if s.subtopic in classwork_text else True,
          f"and {s.subtopic} prints no orphan label in class work")

# The same orphan, on the other side of the booklet. The key looped over every
# section for Class Work with no guard, so a subtopic the cap had emptied
# printed its heading in the key with no answers under it, and whoever was
# marking read that as a missing page. The Homework half of the key always had
# the guard; the Class Work half did not.
key_text = text.split("Answers &amp; Worked Solutions")[-1] \
    if "Answers &amp; Worked Solutions" in text \
    else text.split("Worked Solutions")[-1]
key_classwork = key_text.split("Homework")[0]
for s in emptied:
    check(s.subtopic not in key_classwork,
          f"the key does not head a Class Work section for {s.subtopic}, "
          "which has no class work",
          key_classwork.strip()[-40:].replace("\n", " "))
for s in in_session(sections):
    check(s.subtopic in key_classwork,
          f"but {s.subtopic}, which was taught, is in the Class Work key")


# ---------------------------------------------------------------------------
# 3. Picture credits are gathered at the back, not printed under each picture
# ---------------------------------------------------------------------------
print("\nPicture credits")
print("-" * 62)

photo = tmp / "photo.png"
PILImage.new("RGB", (240, 160), (90, 140, 200)).save(photo)

REAL_CREDIT = "Lighthouse at Dusk by A Photographer, CC BY-SA 4.0"
GHOST_CREDIT = "Ghost Picture by Nobody, CC BY 2.0"

shown = vq("CREDITED: what does the photograph show?")
shown.image_path = str(photo)
shown.image_attribution = REAL_CREDIT

# A download that failed leaves the path set and the file absent. Crediting
# that picture would list a photograph the booklet does not contain.
ghost = vq("GHOSTED: this picture never resolved.")
ghost.image_path = str(tmp / "never-downloaded.png")
ghost.image_attribution = GHOST_CREDIT

dupe = vq("DUPLICATE: the same photograph again.")
dupe.image_path = str(photo)
dupe.image_attribution = REAL_CREDIT

check(image_is_usable(str(photo)), "a real file is usable")
check(not image_is_usable(str(tmp / "never-downloaded.png")),
      "a path whose file never arrived is not")
check(not image_is_usable(None), "and neither is no path at all")

illustrated = BookletData(
    subject="English", year_level="Year 5", student_name="Sam",
    sections=[SubtopicOutput(
        topic="Reading", subtopic="Comprehension",
        teaching=teaching("Comprehension", n_guided=1),
        questions=[shown, ghost, dupe],
        homework_questions=[vq("HOMEWORK: read it again.")])])

credits = image_credits(illustrated)
check(credits == [REAL_CREDIT],
      "only pictures the booklet actually prints are credited, once each",
      str(credits))
check(GHOST_CREDIT not in credits,
      "a failed download is not credited as though it had printed")

pdf = render_pdf(illustrated, tmp / "illustrated.pdf")
pages = page_texts(pdf)
text = "\n".join(pages)

credit_pages = [i for i, p in enumerate(pages) if REAL_CREDIT[:24] in p]
image_pages = [i for i, p in enumerate(pages) if "CREDITED:" in p]
check(len(credit_pages) == 1, "the credit is printed exactly once",
      f"on page(s) {[i + 1 for i in credit_pages]}")
check(credit_pages and credit_pages[0] == len(pages) - 1,
      "on the very last page of the booklet",
      f"page {credit_pages[0] + 1} of {len(pages)}")
check(bool(image_pages) and credit_pages[0] not in image_pages,
      "and never under the picture itself",
      f"picture on page {[i + 1 for i in image_pages]}")
check("Picture Credits" in text, "the credits page is titled")
check(GHOST_CREDIT[:20] not in text,
      "the ghost picture is credited nowhere in the booklet")

# No pictures, no page: an all-maths booklet should not carry an empty back page.
plain = BookletData(subject="Mathematics", year_level="Year 5",
                    student_name="Sam",
                    sections=[SubtopicOutput(
                        topic="Number", subtopic="Place value",
                        teaching=teaching("Place value", n_guided=1),
                        questions=[vq("What is 300 + 40 + 2?")])])
check(image_credits(plain) == [], "a booklet with no pictures credits nothing")
plain_text = "\n".join(page_texts(render_pdf(plain, tmp / "plain.pdf")))
check("Picture Credits" not in plain_text,
      "and prints no empty credits page")


# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"PDFs written to {tmp}")
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
