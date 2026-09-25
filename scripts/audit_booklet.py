"""Reads a finished booklet PDF and reports what is wrong with it.

    python scripts/audit_booklet.py output/my-booklet.pdf

WHY THIS EXISTS. Every quality problem found in this product so far was found
by a person opening the PDF and reading it. That does not scale, it does not
run while anyone sleeps, and it stops the moment the person stops. Meanwhile
each fix shipped with a check script proving that one defect is gone, and
nothing looks at a whole finished booklet and asks whether it is worth the
money.

This is the other half. It measures a booklet the way a customer meets one:
after generation, from the file alone, with no access to the pipeline that
made it. So it works on any booklet from any version, including ones already
sold, and a booklet that scores clean here is one nobody has to read before
sending.

WHAT IT LOOKS FOR, and every one of these is a defect that actually shipped:

  DEAD SPACE   A Year 3 booklet averaged 4.8cm of empty page above the footer
               across all 14 content pages, and one page wasted 12.4cm of a
               24.6cm column. 24 questions read like 15. This is the single
               biggest reason a booklet looks thin.

  THIN SECTION A Final Challenge printed ONE question, on a page of its own,
               with a score box reading "___ / 1". The year band sets four.

  KEY DISAGREES An answer key printed "128 m" above working whose every line
               said 112 m. The guard that noticed withheld the tick and the
               question shipped anyway.

  DELIBERATION An answer key printed "Alternatively," and "Let us re-calculate
               for the most common interpretation". That is the model thinking
               out loud, in the document a parent marks from.

  NO TICK      A tick means the answer was checked. A booklet where most
               answers carry no tick is one a parent cannot mark with.

  LONG TO READ A Year 1 booklet shipped a 39-word question. A maths question
               that is hard to READ is not a hard maths question, it is a
               reading test wearing one.

  EM DASH      Banned across this project, including in generated booklets.

Findings are ranked. SERIOUS means do not sell this booklet. The exit status
is non-zero when anything serious is found, so this can gate a release.

Needs pymupdf (pip install pymupdf).
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pymupdf
except ImportError:                                          # pragma: no cover
    try:
        import fitz as pymupdf
    except ImportError:
        print("This needs pymupdf:  pip install pymupdf")
        raise SystemExit(2)


# ---------------------------------------------------------------------------
# Thresholds, each one measured off a real booklet rather than chosen
# ---------------------------------------------------------------------------

# The footer sits in the bottom 62pt of the page. Content above it is content;
# ink below it is the page furniture every page carries.
FOOTER_TOP_PT = 780.0
# Likewise the running header.
HEADER_BOTTOM_PT = 60.0

# Empty space above the footer, in centimetres. A page always ends a little
# short, because a question is an indivisible block and one rarely lands
# exactly on the last line. Six centimetres is about a fifth of the column and
# is where a reader stops seeing a page and starts seeing a gap.
DEAD_SPACE_NOTE_CM = 4.0
DEAD_SPACE_SERIOUS_CM = 6.0

# A tick rate below this means most of the key is unverified.
TICK_RATE_SERIOUS = 0.60
TICK_RATE_NOTE = 0.85

# The model thinking out loud, in a document someone marks from. Taken from
# the answer key of a shipped Year 4 booklet.
#
# IMPORTED FROM THE PIPELINE'S OWN GUARD, not written again here. This file
# began with its own copy, and that is exactly how the defect reached a
# customer: `consistency._SELF_CORRECTION` held "let me recalculate" and the
# booklet said "Let us re-calculate", so the pipeline saw nothing and only the
# auditor did. Two detectors for one rule will always drift, and the one that
# drifts is the one that ships. Importing means broadening either of them
# broadens both.
try:
    from booklet_gen.agents.consistency import (                   # noqa: E402
        _ALTERNATIVE_READINGS, _SELF_CORRECTION)
    DELIBERATION = re.compile(
        f"(?:{_SELF_CORRECTION.pattern})|(?:{_ALTERNATIVE_READINGS.pattern})",
        re.IGNORECASE)
except ImportError:
    # Audits a PDF from outside the project, where the package is not
    # importable. Reported below rather than guessed at silently.
    DELIBERATION = None

# Working that sets out a column algorithm, where the answer is built one
# digit at a time and is NOT expected to appear anywhere as a whole number:
#
#     8 + 4 = 12 (write 2, carry 1)
#     2 + 9 + 1 = 12 (write 2, carry 1)
#     5 + 6 + 1 = 12 (write 2, carry 1)
#     4 + 2 + 1 = 7            <- the answer is 7222, and it is correct
#
# The first draft of this file reported that as an answer disagreeing with its
# own working. It is the opposite: it is column arithmetic written the way a
# child is taught to write it. The premise of the agreement check is simply
# false for this genre, so the check is skipped rather than fudged. A tool
# that raises a false alarm on correct work teaches its user to ignore it,
# which costs more than the one real defect it might have caught here.
COLUMN_ALGORITHM = re.compile(
    r"\b(?:carry|carried|borrow|borrowed|regroup|regrouped)\b"
    r"|\bwrite\s+\d\b", re.IGNORECASE)

# A phrase that promises a figure. If the page has no figure on it, the child
# is being asked to read something that is not there.
PROMISES_FIGURE = re.compile(
    r"\b(?:the (?:graph|diagram|figure|chart|table|shape|grid|number line)"
    r"\s+(?:above|below|shown|shows)"
    r"|shown (?:above|below)|(?:above|below) shows"
    r"|in the (?:graph|diagram|figure|chart))\b", re.IGNORECASE)

BANDS = ("WARM-UP RECAP", "CLASS WORK", "HOMEWORK", "FINAL CHALLENGE",
         "ANSWERS")

# Where a question STOPS. Without this, a question with no answer line under
# it ran to the foot of the page and swallowed whatever came next: one 22-word
# angle question absorbed the entire Homework section header and was reported
# as 86 words against a budget of 25. The reading-load finding was invented by
# the reader, not by the booklet.
QUESTION_ENDS = re.compile(
    r"\n(?:Answer:"
    r"|\d+\.\s"
    r"|PART \d+ OF \d+"
    r"|TOPIC \d+ OF \d+"
    r"|Session \d+ of \d+"
    r"|Now you try"
    r"|Remember\b"
    r"|Paulio shows you first"
    r"|✎"
    r"|Homework\b|Class Work\b|Warm-up Recap\b|Final Challenge\b"
    r"|That is the end of the booklet"
    r"|Score Marked by)")


class Finding:
    def __init__(self, severity, kind, where, what, why):
        self.severity = severity        # "SERIOUS" | "WORTH FIXING" | "NOTE"
        self.kind = kind
        self.where = where
        self.what = what
        self.why = why


class Booklet:
    """A finished booklet, read the way a customer meets one: from the file."""

    def __init__(self, path: Path):
        self.path = path
        self.doc = pymupdf.open(path)
        self.pages = len(self.doc)
        self.text = [p.get_text() for p in self.doc]
        self.year = self._year()
        self.bands = [self._band(t) for t in self.text]

    def _year(self) -> str | None:
        for t in self.text[:4]:
            m = re.search(r"\b(Year\s+\d+)\b", t)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _band(text: str) -> str | None:
        """Which part of the booklet this page belongs to.

        Matched case SENSITIVELY against the running header, which is set in
        capitals. Upper-casing the header first looks harmless and is not: the
        contents page lists "Warm-up Recap" in title case, so folding the case
        made the contents page, and the how-to page after it, both report as
        warm-up pages and get audited as pages a child writes on.
        """
        head = "\n".join(text.split("\n")[:6])
        for band in BANDS:
            if band in head:
                return band
        return None

    def content_pages(self) -> list[int]:
        """Pages a child writes on: everything before the answer key."""
        return [i for i, band in enumerate(self.bands)
                if band and band != "ANSWERS"]

    def lowest_content(self, i: int) -> float:
        """The y of the lowest real ink on a page, ignoring page furniture."""
        page = self.doc[i]
        low = HEADER_BOTTOM_PT
        for block in page.get_text("blocks"):
            if HEADER_BOTTOM_PT < block[3] < FOOTER_TOP_PT:
                low = max(low, block[3])
        for drawing in page.get_drawings():
            rect = drawing["rect"]
            if HEADER_BOTTOM_PT < rect.y1 < FOOTER_TOP_PT:
                low = max(low, rect.y1)
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if HEADER_BOTTOM_PT < rect.y1 < FOOTER_TOP_PT:
                    low = max(low, rect.y1)
        return low

    def figures_on(self, i: int) -> int:
        """Figures a child could read something off.

        The mascot is drawn at about 31pt square and appears beside every
        mini-lesson heading, so anything that small is page decoration rather
        than a figure, and counting it would hide a question whose graph is
        genuinely missing.
        """
        n = 0
        page = self.doc[i]
        for image in page.get_images(full=True):
            for rect in page.get_image_rects(image[0]):
                if rect.width > 45 and rect.height > 45:
                    n += 1
        return n

    def score_box(self) -> dict[str, int]:
        """Section sizes, read off the score box the booklet prints itself.

        Far more reliable than counting questions out of the text: the box is
        what the booklet asserts about itself, and it is what a parent reads.
        """
        for text in reversed(self.text):
            if "Total" not in text:
                continue
            labels = re.findall(
                r"^(Warm-up Recap|Class Work|Homework|Final Challenge|Total)$",
                text, re.MULTILINE)
            counts = [int(n) for n in re.findall(r"_{3,}\s*/\s*(\d+)", text)]
            # The LAST labels, not all of them. The score box sits on the same
            # page as the Final Challenge, whose own heading matches the label
            # pattern, so the page offers one more label than it has numbers
            # and an equal-length test finds nothing at all. The box is the run
            # that ends at "Total", and counting back from there is what makes
            # this immune to whatever heading happens to share the page.
            if counts and len(labels) >= len(counts):
                return dict(zip(labels[-len(counts):], counts))
        return {}

    def answer_key(self) -> list[dict]:
        """Every answer in the key, with its working and whether it is ticked."""
        pages = [i for i, b in enumerate(self.bands) if b == "ANSWERS"]
        if not pages:
            return []
        blob = "\n".join(self.text[i] for i in pages)
        # Each entry starts "N. Answer: ..." and runs to the next one.
        parts = re.split(r"\n(?=\d+\.\s*Answer:)", blob)
        out = []
        for part in parts:
            m = re.match(r"(\d+)\.\s*Answer:\s*(.*)", part)
            if not m:
                continue
            body = part[m.end():]
            # The key prints the page its question sits on, as "(p14)". That
            # is the locator to report, because the question NUMBER restarts
            # at 1 under every subtopic heading, so three separate findings
            # all came back as "answer 1" and none of them could be found.
            at = re.search(r"\(p(\d+)\)", body[:40])
            out.append({
                "n": int(m.group(1)),
                "answer": m.group(2).strip(),
                "ticked": "✓" in part[:len(m.group(0)) + 40],
                "working": body.strip(),
                "where": (f"q{m.group(1)} on p{at.group(1)}" if at
                          else f"answer {m.group(1)}"),
            })
        return out

    @staticmethod
    def _worked_example_spans(body: str) -> list[tuple[int, int]]:
        """Character ranges holding a worked example's numbered STEPS.

        A mini-lesson sets its method out as "1. Divide the tens... 2. Divide
        the ones...", which looks exactly like a numbered question and is not
        one. Counting them made a Year 4 booklet report 41 questions where the
        score box said 22, and put lesson prose through the reading-load
        budget, where a step written for an adult to read aloud is naturally
        longer than a question written for a child to read alone.

        A span opens at a worked-example heading and closes at "Now you try",
        which is the line the booklet itself uses to hand the pen over.
        """
        spans = []
        opens = [m.start() for m in re.finditer(
            r"Paulio shows you first|Now let'?s try one together", body)]
        for start in opens:
            closer = re.search(r"Now you try", body[start:])
            spans.append((start, start + closer.start() if closer
                          else len(body)))
        return spans

    def questions(self) -> list[dict]:
        """Numbered questions off the child's pages, with the page they sit on.

        Deliberately loose about what a question SAYS and strict about where
        one starts and stops, because a miscounted boundary invents findings:
        one 22-word angle question with no answer line under it ran on to the
        foot of its page, absorbed the Homework section header, and was
        reported as 86 words against a budget of 25.

        The count lands on the score box exactly for one of the two booklets
        this was built against and one over for the other, because `band` is
        read off the running header and a section that begins halfway down a
        page leaves the questions above it labelled with the section below.
        That costs nothing today: `band` only chooses between the practice and
        challenge reading budgets, and the Final Challenge always starts on a
        fresh page. It would start costing something the moment a check needs
        to know which section a question belongs to, so it is written down
        here rather than discovered then.
        """
        out = []
        for i in self.content_pages():
            body = self.text[i]
            lessons = self._worked_example_spans(body)
            for m in re.finditer(
                    r"^(\d+)\.\s+(.+?)(?=" + QUESTION_ENDS.pattern + r"|\Z)",
                    body, re.MULTILINE | re.DOTALL):
                if any(lo <= m.start() < hi for lo, hi in lessons):
                    continue
                text = " ".join(m.group(2).split())
                if len(text) < 12:
                    continue
                out.append({"page": i, "n": int(m.group(1)), "text": text,
                            "band": self.bands[i]})
        return out


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def check_dead_space(b: Booklet) -> list[Finding]:
    """One finding, not one per page.

    Dead space is a single property of the whole booklet, and the first draft
    of this reported it seven times for one Year 3 booklet, which buried
    everything under it. The average is the number that matters and the worst
    pages are the evidence for it, so they belong in the same line.
    """
    # Every content page but the last. The last one carries the end of the
    # Final Challenge and the score box, and a booklet stops where it stops:
    # there is nothing left to pull up into the space under it. Counting it
    # charged every booklet for its own ending, which made a well set booklet
    # read as 2.3cm wasted a page when the pages that could actually be
    # improved averaged 1.8cm.
    pages = b.content_pages()[:-1]
    gaps = sorted(((FOOTER_TOP_PT - b.lowest_content(i)) / 72 * 2.54, i)
                  for i in pages)
    if not gaps:
        return []
    mean = sum(g for g, _ in gaps) / len(gaps)
    worst = [(g, i) for g, i in reversed(gaps) if g >= DEAD_SPACE_SERIOUS_CM]
    if mean >= DEAD_SPACE_SERIOUS_CM:
        severity = "SERIOUS"
    elif mean >= DEAD_SPACE_NOTE_CM or worst:
        severity = "WORTH FIXING"
    else:
        severity = "NOTE"
    detail = f"{mean:.1f}cm empty on average across {len(gaps)} pages"
    if worst:
        named = ", ".join(f"p{i + 1} {g:.1f}cm" for g, i in worst[:5])
        detail += f"; worst {named}"
    return [Finding(
        severity, "dead space", "page density", detail,
        "the column is about 24.6cm, so this is the share of every page that "
        "prints blank. It is what makes a booklet of 24 questions look like "
        "15, and it is the first thing a parent judges")]


def check_section_sizes(b: Booklet) -> list[Finding]:
    found = []
    box = b.score_box()
    if not box:
        return [Finding("NOTE", "structure", "score box",
                        "could not be read",
                        "section sizes could not be checked from this file")]
    try:
        from booklet_gen.timing import session_plan
        plan = session_plan(b.year)
        expected = {"Warm-up Recap": plan.recap_questions,
                    "Final Challenge": plan.challenge_questions}
    except Exception:
        expected = {}
    for name, want in expected.items():
        got = box.get(name)
        if got is None:
            continue
        if got <= 1:
            found.append(Finding(
                "SERIOUS", "thin section", name,
                f"{got} question against a {b.year} band of {want}",
                "a section that announces itself on the contents page, takes a "
                "page, and holds one question is the most visible fault a "
                "booklet can have"))
        elif got < (want + 1) // 2:
            found.append(Finding(
                "WORTH FIXING", "thin section", name,
                f"{got} questions against a {b.year} band of {want}",
                "under half the year band, so the section is not doing the job "
                "its heading promises"))
    return found


def check_answer_key(b: Booklet) -> list[Finding]:
    found = []
    key = b.answer_key()
    if not key:
        return [Finding("SERIOUS", "answer key", "whole booklet",
                        "no answer key could be read",
                        "the key is the half of the product the adult uses")]

    ticked = sum(1 for a in key if a["ticked"])
    rate = ticked / len(key)
    if rate < TICK_RATE_SERIOUS:
        severity = "SERIOUS"
    elif rate < TICK_RATE_NOTE:
        severity = "WORTH FIXING"
    else:
        severity = "NOTE"
    found.append(Finding(
        severity, "answer key", f"{len(key)} answers",
        f"{ticked} ticked ({rate:.0%})",
        "a tick means that answer was checked, and a key that is mostly "
        "unticked is one a parent cannot mark from with any confidence"))

    if DELIBERATION is None:
        found.append(Finding(
            "NOTE", "answer key", "deliberation check",
            "skipped: booklet_gen is not importable from here",
            "run this from the project root to check the key for model "
            "deliberation, which shares its detector with the pipeline"))
    for a in key:
        if DELIBERATION is not None and DELIBERATION.search(a["working"]):
            phrase = DELIBERATION.search(a["working"]).group(0)
            found.append(Finding(
                "SERIOUS", "deliberation", a["where"],
                f"the working says {phrase!r}",
                "the model thinking out loud, printed in the document a parent "
                "marks from. It also means the answer above it is a guess "
                "between readings rather than a result"))

        # The stated answer should appear in its own working. Numbers only:
        # a worded answer legitimately paraphrases its working, and column
        # arithmetic legitimately never states its result at all.
        nums_answer = re.findall(r"-?\d[\d,]*(?:\.\d+)?", a["answer"])
        if (not nums_answer or not a["working"]
                or COLUMN_ALGORITHM.search(a["working"])):
            continue
        stated = nums_answer[-1].replace(",", "")
        in_working = [n.replace(",", "")
                      for n in re.findall(r"-?\d[\d,]*(?:\.\d+)?", a["working"])]
        if stated not in in_working:
            found.append(Finding(
                "SERIOUS", "key disagrees", a["where"],
                f"states {a['answer']!r}, which appears nowhere in its working",
                "a booklet shipped '128 m' above working whose every line said "
                "112 m. Whichever is right, the page marks a correct child "
                "wrong"))
    return found


def check_reading_load(b: Booklet) -> list[Finding]:
    found = []
    try:
        from booklet_gen.agents import reading_load
    except Exception:
        return found
    over = []
    for q in b.questions():
        kind = ("challenge" if q["band"] == "FINAL CHALLENGE" else "practice")
        try:
            budget = reading_load.max_words(b.year, kind)
            words = reading_load.word_count(q["text"])
        except Exception:
            return found
        if words > budget:
            over.append((words, budget, q))
    for words, budget, q in sorted(over, reverse=True, key=lambda x: x[0])[:6]:
        found.append(Finding(
            "WORTH FIXING", "long to read", f"page {q['page'] + 1} q{q['n']}",
            f"{words} words against a {b.year} budget of {budget}",
            "a maths question that is hard to READ is not a hard maths "
            f"question: {q['text'][:70]}..."))
    if len(over) > 6:
        found.append(Finding(
            "NOTE", "long to read", "whole booklet",
            f"{len(over)} questions over the reading budget",
            "only the six longest are listed above"))
    return found


def check_promised_figures(b: Booklet) -> list[Finding]:
    found = []
    for q in b.questions():
        if not PROMISES_FIGURE.search(q["text"]):
            continue
        if b.figures_on(q["page"]) == 0:
            found.append(Finding(
                "SERIOUS", "missing figure", f"page {q['page'] + 1} q{q['n']}",
                "names a figure, and the page has none",
                f"the child is asked to read something that is not there: "
                f"{q['text'][:70]}..."))
    return found


def check_em_dashes(b: Booklet) -> list[Finding]:
    hits = defaultdict(int)
    for i, text in enumerate(b.text):
        n = text.count("—")
        if n:
            hits[i + 1] += n
    if not hits:
        return []
    total = sum(hits.values())
    return [Finding(
        "WORTH FIXING", "em dash",
        ", ".join(f"page {p}" for p in sorted(hits))[:60],
        f"{total} em dash(es)",
        "banned across this project including in generated booklets, and the "
        "formatter's stripper is meant to catch every one")]


CHECKS = (check_dead_space, check_section_sizes, check_answer_key,
          check_reading_load, check_promised_figures, check_em_dashes)

RANK = {"SERIOUS": 0, "WORTH FIXING": 1, "NOTE": 2}


def audit(path: Path) -> list[Finding]:
    b = Booklet(path)
    print(f"\n{path.name}")
    print(f"{b.pages} pages, {b.year or 'year unknown'}, "
          f"{len(b.content_pages())} pages a child writes on")
    box = b.score_box()
    if box:
        print("  " + "   ".join(f"{k}: {v}" for k, v in box.items()))
    found = []
    for check in CHECKS:
        try:
            found += check(b)
        except Exception as e:                               # pragma: no cover
            found.append(Finding("NOTE", "audit", check.__name__,
                                 f"could not run: {e}",
                                 "this check did not report on this booklet"))
    return sorted(found, key=lambda f: RANK[f.severity])


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.split("WHY THIS EXISTS")[0].strip())
        return 2
    worst = 0
    for name in argv[1:]:
        path = Path(name)
        if not path.exists():
            print(f"no such file: {path}")
            worst = 2
            continue
        found = audit(path)
        last = None
        for f in found:
            if f.severity != last:
                print(f"\n  {f.severity}")
                last = f.severity
            print(f"    {f.kind:<15} {f.where:<22} {f.what}")
            print(f"    {'':<15} {'':<22} {f.why}")
        serious = sum(1 for f in found if f.severity == "SERIOUS")
        fixable = sum(1 for f in found if f.severity == "WORTH FIXING")
        print(f"\n  {serious} serious, {fixable} worth fixing, "
              f"{len(found) - serious - fixable} note(s)")
        if serious:
            print("  Do not sell this one.")
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
