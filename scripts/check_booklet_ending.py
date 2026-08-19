"""Checks the booklet ends, and the answer key ends, rather than stopping.

Two pages used to just stop.

The last page the student sees floated the closing note and the score card in
its top third with twelve to fifteen centimetres of white underneath them, and
those two are among the best-designed elements in the document. The last page of
the answer key did the same: the answers ran out part way down a column and the
rest of the sheet was blank. A document that stops rather than ends is the
clearest signal in the whole booklet that nobody laid it out.

The booklet now ends on a finish page: the mascot at a size he is worth here and
nowhere else, the sign-off, the score card, and the wordmark, centred on the
sheet. The score table itself is untouched; its proportions and its hierarchy
were already right. The key ends on a colophon: a rule, the wordmark and one
line saying what the key is.

The line is the part that matters, because it is a claim about accuracy printed
under the brand. "Every answer in this key has been checked" may only print when
every answer carries a verification mark, decided by the same function the cover
asks, so the front of the booklet and the back of it cannot claim different
things about the same key. A booklet that says it on the last page and prints
ten unticked answers has told a parent, in the product's own notation, that it
is not telling the truth; they do not have to find a wrong answer to want their
money back.

    PYTHONPATH=. python scripts/check_booklet_ending.py
"""
import sys
import tempfile
from pathlib import Path

import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen import formatter as F
from booklet_gen.formatter import (PAGE_MARGIN, _COLOPHON_CHECKED,
                                   _COLOPHON_PARTIAL, cover_footer_note,
                                   every_answer_checked, render_pdf)
from booklet_gen.schemas import (BookletData, Question, SubtopicOutput,
                                 SubtopicTeaching, ValidatedQuestion,
                                 WorkedExample)

_passed = 0
_failed: list[str] = []


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


def bad(msg):
    _failed.append(msg)
    print("  FAIL:", msg)


def check(cond, good, consequence):
    if cond:
        ok(good)
    else:
        bad(consequence)
    return cond


def vq(text, answer="42", verified=True, difficulty="easy"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer,
                          working="Work it through.", difficulty=difficulty),
        verified=verified)


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Line the digits up by place value and work one column "
                      "at a time."],
    key_points=["Line the digits up.", "Work one column at a time."],
    worked_example=WorkedExample(question="What is 2 385 + 1 947?",
                                 steps=["Add the ones.", "Carry into the tens."],
                                 answer="4 332"))


def booklet(all_verified=True, year="Year 5", n=9):
    qs = [vq(f"What is {30 + j} x 7?", answer=str(7 * (30 + j)))
          for j in range(n)]
    if not all_verified:
        qs[1] = vq("What is 96 x 4?", answer="384", verified=False)
    return BookletData(
        subject="Mathematics", year_level=year, student_name="Lleyton",
        program_label="Academic Accelerate",
        sections=[SubtopicOutput(topic="Number", subtopic="Multiplying",
                                 teaching=TEACHING, questions=qs,
                                 homework_questions=[
                                     vq(f"Double {200 + j}.",
                                        answer=str(2 * (200 + j)))
                                     for j in range(6)],
                                 estimated_minutes=10)],
        recap_questions=[vq("What is 15 x 4?", answer="60")],
        challenge_questions=[vq("What is 25 x 8?", answer="200")],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


tmp = Path(tempfile.mkdtemp(prefix="folio-ending-"))


def render(tag, data):
    out = tmp / f"{tag}.pdf"
    render_pdf(data, out)
    return pymupdf.open(out)


print("\nTHE CLAIM ON THE LAST PAGE OF THE KEY IS ONLY MADE WHEN IT IS TRUE")

check(every_answer_checked(booklet(True))
      and not every_answer_checked(booklet(False)),
      "one function decides whether the booklet may claim a fully checked key",
      "every_answer_checked does not distinguish a booklet with an unverified "
      "answer from one without. That is the condition the whole claim rests on")

# The cover and the colophon have to be reading the same thing. A booklet whose
# cover hedges and whose key does not is a booklet that contradicts itself
# between page 1 and the last page.
check(("Every answer" in cover_footer_note(booklet(True)))
      and ("Every answer" not in cover_footer_note(booklet(False))),
      "and the cover asks that same function, so the two ends of the booklet "
      "cannot disagree",
      "the cover's claim and the key's colophon are decided separately. A "
      "booklet whose cover hedges and whose key does not tells a parent one of "
      "the two is wrong")

full = render("full", booklet(True))
part = render("partial", booklet(False))
FULL = " ".join(" ".join(p.get_text().split()) for p in full)
PART = " ".join(" ".join(p.get_text().split()) for p in part)

check(_COLOPHON_CHECKED in FULL,
      f"a fully verified booklet ends its key with {_COLOPHON_CHECKED!r}",
      "a booklet in which every answer was verified does not say so at the end "
      "of the key. The claim is the reason the colophon is worth printing")
check(_COLOPHON_CHECKED not in PART,
      "a booklet with one unverified answer does not make that claim",
      f"a booklet with an unverified answer still printed {_COLOPHON_CHECKED!r} "
      "at the end of its key, over a page that also prints an answer with no "
      "tick beside it. It has told the reader, in its own notation, that it is "
      "not telling the truth")
check(_COLOPHON_PARTIAL in PART,
      "it prints the truthful variant instead",
      "a booklet with an unverified answer printed no closing line at all. The "
      "tick column needs explaining precisely when it is not complete")

print("\nTHE KEY ENDS ON SOMETHING")

MEASURE = A4[0] - 2 * PAGE_MARGIN
key_start = next(i for i, p in enumerate(full) if "Worked Solutions" in p.get_text())
last_key = len(full) - 1
page = full[last_key]
y_line = max((line["bbox"][1]
              for block in page.get_text("dict")["blocks"]
              for line in block.get("lines", [])
              if _COLOPHON_CHECKED in "".join(s["text"] for s in line["spans"])),
             default=None)
check(y_line is not None,
      f"the colophon is on the last page of the key, page {last_key + 1}",
      "the closing line is not on the last page of the key. A colophon four "
      "pages from the end is not an ending")
if y_line is not None:
    rules = [d["rect"] for d in page.get_drawings()
             if d.get("fill") and d["rect"].height < 4
             and d["rect"].width > 0.3 * MEASURE
             and y_line - 3 * cm < d["rect"].y0 < y_line]
    check(rules,
          "with a rule drawn above it",
          "the closing line has no rule above it. One grey sentence under the "
          "last answer reads as a stray line rather than as the end of the "
          "document")
    words = " ".join("".join(s["text"] for s in line["spans"])
                     for block in page.get_text("dict")["blocks"]
                     for line in block.get("lines", []))
    check("FOLIO" in words,
          "and the wordmark with it",
          "the key's last page carries no wordmark. The colophon is where a "
          "publisher signs the document")

print("\nTHE BOOKLET ENDS ON A PAGE ABOUT FINISHING")

finish = next(i for i in range(key_start) if "Marked by:" in full[i].get_text())
page = full[finish]
lines = [line["bbox"] for block in page.get_text("dict")["blocks"]
         for line in block.get("lines", [])
         if "".join(s["text"] for s in line["spans"]).strip()
         and line["bbox"][1] > PAGE_MARGIN + 0.6 * cm
         and line["bbox"][3] < page.rect.height - PAGE_MARGIN]
# Pictures only. The wordmark lockup carries the 0.62cm brand mark, which is
# furniture rather than illustration and would otherwise pass for a mascot.
MASCOT_MIN = 2.0 * cm
placed = [page.get_image_bbox(i) for i in page.get_images(full=True)]
images = [b for b in placed if b.height > MASCOT_MIN]
top = min([b[1] for b in lines] + [b.y0 for b in placed])
bottom = max([b[3] for b in lines] + [b.y1 for b in placed])
above = top - PAGE_MARGIN
below = page.rect.height - PAGE_MARGIN - bottom

check(abs(above - below) < 2.5 * cm,
      f"the ending is centred on the sheet: {above / cm:.1f}cm above it and "
      f"{below / cm:.1f}cm below",
      f"the ending sits {above / cm:.1f}cm from the top and {below / cm:.1f}cm "
      "from the bottom. The complaint was that the closing note and the score "
      "card float in the top third of a page with twelve to fifteen "
      "centimetres of white underneath, and an off-centre block is that same "
      "page with a picture added to it")

check(images,
      f"the mascot is printed on it at "
      f"{max([i.height for i in images] or [0]) / cm:.1f}cm",
      "the finish page carries no picture. This is the one page in the booklet "
      "where a large mascot is earned, and without him the page is the same "
      "two boxes it always was")
if images:
    biggest = max(i.height for i in images)
    elsewhere = max([b.height for i in range(1, key_start) if i != finish
                     for b in [full[i].get_image_bbox(im)
                               for im in full[i].get_images(full=True)]]
                    or [0])
    check(biggest > 2.5 * cm and biggest > elsewhere,
          f"which is larger than anywhere else in the booklet "
          f"({elsewhere / cm:.1f}cm)",
          f"the mascot is {biggest / cm:.1f}cm here against {elsewhere / cm:.1f}"
          "cm elsewhere. He is a 1.1cm icon beside a worked example everywhere "
          "else on purpose; if he is the same size here the page has not been "
          "given an ending")

words = " ".join(" ".join(s["text"] for s in line["spans"])
                 for block in page.get_text("dict")["blocks"]
                 for line in block.get("lines", []))
check("FOLIO" in words,
      "and the wordmark signs it off",
      "the finish page carries no wordmark")
check("That is the end of the booklet" in words and "Total" in words,
      "with the sign-off and the score card on it",
      "the finish page is missing the sign-off or the score card. Those two "
      "are what the page is for; the mascot and the wordmark are the frame "
      "round them")

print("\nAND THE SCORE TABLE ITSELF IS NOT RESTYLED")

# Its proportions and its hierarchy were already right, so the only thing that
# changed is where on the page it sits. One row of part names over one row of
# marks, ruled, with the tinted header the booklet already used.
grid = [d for d in page.get_drawings()
        if d["rect"].width > 0.8 * MEASURE and 0.8 * cm < d["rect"].height < 3 * cm]
check(grid,
      "the score card is still one wide two-row table across the measure",
      "the score card is no longer a single wide table. It was not asked to "
      "change and its proportions were already right")
header = [d for d in page.get_drawings()
          if d.get("fill") and d["rect"].width > 0.8 * MEASURE
          and 0.3 * cm < d["rect"].height < 1.2 * cm]
check(header,
      "with its tinted header row",
      "the score card lost its tinted header row, which is what separates the "
      "part names from the marks written under them")

print("\nA BOOKLET WITHOUT THE MASCOT STILL ENDS PROPERLY")

# He narrates worked examples up to Year 6 and is deliberately absent above it.
# A large teddy on the last page of a Year 10 booklet undoes every other
# decision on the page, so the finish page has to work without him.
senior = render("senior", booklet(True, year="Year 10"))
s_key = next(i for i, p in enumerate(senior) if "Worked Solutions" in p.get_text())
s_finish = next(i for i in range(s_key) if "Marked by:" in senior[i].get_text())
s_page = senior[s_finish]
s_pictures = [b for b in (s_page.get_image_bbox(i)
                          for i in s_page.get_images(full=True))
              if b.height > MASCOT_MIN]
check(not s_pictures,
      "a Year 10 booklet's finish page carries no mascot",
      "a Year 10 booklet printed a large teddy on its last page. He is absent "
      "from the rest of a senior booklet on purpose, and arriving only at the "
      "end reads as a template someone forgot to switch off")
s_words = " ".join(s_page.get_text().split())
check("That is the end of the booklet" in s_words and "FOLIO" in s_words,
      "but it still ends on the sign-off, the score card and the wordmark",
      "a senior booklet's finish page lost the sign-off or the wordmark along "
      "with the mascot")

full.close()
part.close()
senior.close()

if _failed:
    print(f"\n{len(_failed)} BOOKLET ENDING CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} BOOKLET ENDING CHECKS PASSED")
sys.exit(0)
