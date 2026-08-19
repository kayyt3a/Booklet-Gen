"""Checks a topic arrives on the page as a landmark, and costs almost no ink.

A topic used to get a 19pt serif line and nothing else. It was set two points
above the subtopic heading beneath it, in the same face and the same colour, so
the biggest structural division in the booklet was its weakest typographic one:
a new topic could begin two thirds of the way down a page and a parent flipping
the printed stack had nothing to navigate by. The largest run of whitespace in
the booklet sat directly above the weakest signal in it.

A topic now arrives on a full-measure opener: a rule across the page, the
topic's name, where in the booklet it is ("Topic 2 of 4"), and the subtopics
inside it as a mini contents. That is a landmark every two or three pages.

Three constraints, and each is a defect if it breaks.

  * It must not be a reversed-out solid band. Four of those would roughly
    double the booklet's band ink, and this is printed at home on a parent's
    own cartridge. The opener carries its weight with a 2.5pt rule and a light
    tint behind the contents strip. The check measures both and compares them.
  * It must be a real landmark in GREYSCALE, which is how most of these are
    printed. A tint nobody can see is not a landmark.
  * It must not cost a page. A booklet is worse, not better, for a landmark
    that pushed the spelling list onto a sheet of its own, so the opener is
    costed against the plain heading it replaces and the contents strip is
    printed only where there is more than one subtopic to list.

    PYTHONPATH=. python scripts/check_topic_openers.py
"""
import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import pymupdf
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm

from booklet_gen import formatter as F
from booklet_gen.formatter import (PAGE_MARGIN, _make_styles, _topic_opener,
                                   render_pdf, stack_height, topic_contents)
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


def vq(text, answer="42", difficulty="easy"):
    return ValidatedQuestion(
        question=Question(question=text, answer=answer,
                          working="Work it through.", difficulty=difficulty),
        verified=True)


TEACHING = SubtopicTeaching(
    intro_paragraphs=["Line the digits up by place value and work one column "
                      "at a time, starting from the ones."],
    key_points=["Line the digits up.", "Work one column at a time."],
    worked_example=WorkedExample(question="What is 2 385 + 1 947?",
                                 steps=["Add the ones.", "Carry into the tens."],
                                 answer="4 332"))

# Four topics, and deliberately not all the same shape: two hold two subtopics
# and two hold one, which is the case the contents strip has to decide about.
SUBTOPICS = [("Number and Place Value", "Four-digit numbers"),
             ("Number and Place Value", "Rounding to hundreds"),
             ("Addition and Subtraction", "Column addition"),
             ("Addition and Subtraction", "Subtracting with regrouping"),
             ("Multiplication", "Multiplying by ten"),
             ("Measurement", "Perimeter of rectangles")]


def booklet():
    sections = []
    for i, (topic, subtopic) in enumerate(SUBTOPICS):
        sections.append(SubtopicOutput(
            topic=topic, subtopic=subtopic, teaching=TEACHING,
            questions=[vq(f"What is {30 + i * 9 + j} x 7?",
                          answer=str(7 * (30 + i * 9 + j)))
                       for j in range(6)],
            homework_questions=[vq(f"Double {200 + i * 9 + j}.",
                                   answer=str(2 * (200 + i * 9 + j)))
                                for j in range(5)],
            estimated_minutes=10))
    return BookletData(
        subject="Mathematics", year_level="Year 5", student_name="Lleyton",
        program_label="Academic Accelerate", sections=sections,
        recap_questions=[vq("What is 15 x 4?", answer="60")],
        challenge_questions=[vq("What is 25 x 8?", answer="200")],
        recap_minutes=6, classwork_minutes=60, homework_minutes=105,
        challenge_minutes=18, total_minutes=170)


print("\nWHAT THE OPENER KNOWS ABOUT WHERE IT IS")

CONTENTS = topic_contents(booklet())
check(CONTENTS.get("Number and Place Value", (0, 0, []))[:2] == (1, 4)
      and CONTENTS.get("Measurement", (0, 0, []))[:2] == (4, 4),
      "the topics are numbered in printed order and all count the same total",
      f"topic numbering came out as "
      f"{[(t, n, of) for t, (n, of, _) in CONTENTS.items()]}. A topic that "
      "says 'Topic 2 of 3' on one page and 'Topic 2 of 4' on another is a "
      "contents that disagrees with itself")
check(CONTENTS["Number and Place Value"][2]
      == ["Four-digit numbers", "Rounding to hundreds"],
      "a topic lists the subtopics it holds, in order",
      f"got {CONTENTS['Number and Place Value'][2]}")

print("\nIT COSTS NOT MUCH MORE THAN THE HEADING IT REPLACES")

STYLES = _make_styles()
plain = stack_height([F.Paragraph("Number and Place Value", STYLES["topic"])])
one = stack_height([_topic_opener(STYLES, "Multiplication", 3, 4,
                                  ["Multiplying by ten"])])
two = stack_height([_topic_opener(STYLES, "Number and Place Value", 1, 4,
                                  ["Four-digit numbers", "Rounding to hundreds"])])
check(one <= plain + 0.35 * cm,
      f"a topic with one subtopic opens in {one / cm:.2f}cm against the "
      f"{plain / cm:.2f}cm the plain heading took",
      f"the opener is {one / cm:.2f}cm against the plain heading's "
      f"{plain / cm:.2f}cm. On a six page English booklet an extra "
      "centimetre per topic is what pushes the spelling list onto a sheet of "
      "its own, and a booklet is worse for a landmark that cost it a page")
check(two > one and two <= plain + 1.3 * cm,
      f"a topic with two subtopics lists them and opens in {two / cm:.2f}cm",
      f"a topic holding two subtopics opens in {two / cm:.2f}cm. The contents "
      "strip has to earn its height")

# A contents line naming one thing is not a contents, it is the subtopic
# heading printed twice with a tint behind it.
solo = _topic_opener(STYLES, "Multiplication", 3, 4, ["Multiplying by ten"])
pair = _topic_opener(STYLES, "Number and Place Value", 1, 4,
                     ["Four-digit numbers", "Rounding to hundreds"])
check(len(solo._cellvalues) < len(pair._cellvalues),
      "a topic holding a single subtopic prints no contents strip at all",
      "a topic with one subtopic still printed a contents strip listing that "
      "one subtopic, immediately above the same name set as a heading")

out = Path(tempfile.mkdtemp(prefix="folio-topics-")) / "topics.pdf"
render_pdf(booklet(), out)
print(f"\nrendered {out}")

doc = pymupdf.open(out)
PAGES = [" ".join(p.get_text().split()) for p in doc]
KEY_START = next(i for i, t in enumerate(PAGES) if "Worked Solutions" in t)

print("\nEVERY TOPIC ARRIVES ON ONE")

TOPICS = list(CONTENTS)
body = " ".join(PAGES[1:KEY_START])
missing = [t for i, t in enumerate(TOPICS, 1)
           if f"{t} TOPIC {i} OF {len(TOPICS)}" not in body]
check(not missing,
      f"all {len(TOPICS)} topics open with their name and their place in the "
      "booklet",
      f"these topics never printed an opener: {missing}. A topic that arrives "
      "as one slightly larger line can begin two thirds of the way down a page "
      "with nothing to say a new part of the booklet has started")

listed = [t for t, (_, _, subs) in CONTENTS.items() if len(subs) > 1]
absent = [t for t in listed
          if F._TOPIC_SEPARATOR.join(CONTENTS[t][2]).replace("   ", " ")
          not in body.replace("   ", " ")]
check(not absent,
      f"and the {len(listed)} topics holding more than one subtopic list them "
      "as a mini contents",
      f"these topics printed no contents: {absent}. The contents strip is "
      "what makes the opener a landmark rather than a bigger heading")

print("\nIT IS DRAWN, NOT JUST SET")

opener_pages = [i for i in range(1, KEY_START)
                if re.search(r"TOPIC \d+ OF \d+", PAGES[i])]
MEASURE = A4[0] - 2 * PAGE_MARGIN
rules = {}
for i in opener_pages:
    page = doc[i]
    y = min((line["bbox"][1]
             for block in page.get_text("dict")["blocks"]
             for line in block.get("lines", [])
             if "TOPIC " in "".join(s["text"] for s in line["spans"])),
            default=None)
    if y is None:
        continue
    found = [d for d in page.get_drawings()
             if d.get("fill") and d["rect"].width > 0.9 * MEASURE
             and 1.5 <= d["rect"].height <= 4 and abs(d["rect"].y1 - y) < 20]
    rules[i] = found
if not check(bool(rules) and all(rules.values()),
             f"a rule is drawn across the measure above every one of the "
             f"{len(rules)} openers",
             f"no rule is drawn above the topic name on opener pages "
             f"{[i + 1 for i, r in rules.items() if not r] or 'none of which exist'}. "
             "Without it the opener is type at a larger size, which is what it "
             "replaced"):
    # Nothing below this can be measured off a page with no opener on it, and
    # the failures above already say why.
    print(f"\n{len(_failed)} TOPIC OPENER CHECKS FAILED")
    sys.exit(1)

print("\nAND IT IS NOT A REVERSED-OUT BAND")

# What the opener ADDS is its drawn furniture: the rule above the name and the
# tint behind the contents. The topic's name was already being printed as a
# 19pt line, so counting it here would flatter nothing and measure nothing.
# Set against the reversed-out part bands the booklet already carries, which is
# the thing four more of these would have been.
PART_INKS = [tuple(round(int(h[i:i + 2], 16) / 255, 3) for i in (1, 3, 5))
             for h in (F.PART_RECAP, F.PART_CLASSWORK, F.PART_HOMEWORK,
                       F.PART_CHALLENGE)]


def coverage(page_, clip):
    pix = page_.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, clip=clip)
    grey = np.frombuffer(pix.samples, dtype=np.uint8)
    return (255.0 - grey.mean()) / 255.0


def ink(page_, clip):
    """Ink in square centimetres of solid black, which is what a cartridge
    pays for: coverage times the area covered."""
    return coverage(page_, clip) * (clip.width * clip.height) / (cm * cm)


furniture, strips = 0.0, []
for i in opener_pages:
    page_ = doc[i]
    rule = min(rules[i], key=lambda d: d["rect"].y0)["rect"]
    tints = [d["rect"] for d in page_.get_drawings()
             if d.get("fill") and d["rect"].width > 0.9 * MEASURE
             and rule.y1 < d["rect"].y0 < rule.y1 + 2.5 * cm]
    furniture += ink(page_, rule) + sum(ink(page_, t) for t in tints)
    strips.append((i, rule, tints))

bands = [(i, d["rect"]) for i in range(1, KEY_START)
         for d in doc[i].get_drawings()
         if d.get("fill") and d["rect"].width > 0.9 * MEASURE
         and d["rect"].height > 1.5 * cm
         and any(max(abs(a - b) for a, b in zip(d["fill"], p)) < 0.02
                 for p in PART_INKS)]
band_ink = sum(ink(doc[i], r) for i, r in bands)

check(bands and furniture <= band_ink / 4,
      f"the {len(opener_pages)} openers add {furniture:.1f}cm2 of solid-black "
      f"equivalent between them, against {band_ink:.1f}cm2 for the "
      f"{len(bands)} reversed-out part bands already in the booklet",
      f"the openers add {furniture:.1f}cm2 of ink against the part bands' "
      f"{band_ink:.1f}cm2 across {len(bands)} bands. Reversed out, four topic "
      "openers would have roughly doubled the band ink in a booklet a parent "
      "prints at home on their own cartridge")

# Reversed out means light type on a dark ground, and the giveaway is that most
# of the strip is dark. Here most of it has to be paper.
page = doc[opener_pages[0]]
rule = strips[0][1]
opener = pymupdf.Rect(rule.x0, rule.y0, rule.x1,
                      max([t.y1 for t in strips[0][2]] or [rule.y1 + 1.2 * cm]))
pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY, clip=opener)
grey = np.frombuffer(pix.samples, dtype=np.uint8)
check(np.mean(grey > 200) > 0.75,
      f"{np.mean(grey > 200):.0%} of the opener's strip is paper, so the topic "
      "name is printed dark on light rather than knocked out of a solid",
      f"only {np.mean(grey > 200):.0%} of the opener's strip is paper. That is "
      "a reversed-out band by another name, which is the one thing this was "
      "not allowed to be")

tints = strips[0][2]

print("\nIT IS STILL A LANDMARK IN GREYSCALE")

s = 300 / 72
band_pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY,
                           clip=pymupdf.Rect(rule.x0, rule.y0, rule.x1, rule.y1))
darkest = int(np.frombuffer(band_pix.samples, dtype=np.uint8).min())
check(darkest < 120,
      f"the rule prints at {darkest}/255 in greyscale, which is a line a "
      "reader sees from across the room",
      f"the rule prints at {darkest}/255 in greyscale. A mono home printer is "
      "how most of these are printed, and a landmark nobody can see is not a "
      "landmark")

if tints:
    tint_pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY,
                               clip=pymupdf.Rect(tints[0].x0 + 4, tints[0].y0 + 2,
                                                 tints[0].x0 + 30, tints[0].y1 - 2))
    tint_grey = int(np.median(np.frombuffer(tint_pix.samples, dtype=np.uint8)))
    check(200 <= tint_grey <= 248,
          f"the contents strip prints as a {tint_grey}/255 tint: visible as a "
          "band, still readable through",
          f"the contents strip prints at {tint_grey}/255. Above 248 it "
          "disappears on a home printer and below 200 it fights the words "
          "sitting on it")

print("\nAN OPENER IS NEVER THE LAST THING ON A PAGE")

stranded = []
for i in opener_pages:
    page_ = doc[i]
    lines = [(line["bbox"][3], "".join(s["text"] for s in line["spans"]).strip())
             for block in page_.get_text("dict")["blocks"]
             for line in block.get("lines", [])
             if line["bbox"][1] > PAGE_MARGIN - 6
             and line["bbox"][3] < page_.rect.height - PAGE_MARGIN + 6]
    if not lines:
        continue
    y_open = max(y for y, t in lines if "TOPIC " in t)
    if not [t for y, t in lines if y > y_open + 2]:
        stranded.append(i + 1)
check(not stranded,
      f"all {len(opener_pages)} openers have their topic under them on the "
      "same page",
      f"an opener was the last thing on pages {stranded}. A topic announced at "
      "the foot of a page with the first subtopic overleaf is the old defect "
      "back, in a louder typeface")

print("\nAND THE ANSWER KEY IS LEFT ALONE")

# The key is set in two columns, where a full-measure opener has no measure to
# be full across, and where the marker is scanning rather than navigating.
key = " ".join(PAGES[KEY_START:])
check("TOPIC 1 OF" not in key,
      "no opener is printed in the answer key",
      "a topic opener was printed in the answer key. The key runs in two 8cm "
      "columns and a full-measure element laid across them is a broken page")
check(all(t in key for t in TOPICS),
      "but the key still names every topic",
      f"the key is missing topics: {[t for t in TOPICS if t not in key]}")

doc.close()

if _failed:
    print(f"\n{len(_failed)} TOPIC OPENER CHECKS FAILED")
    sys.exit(1)
print(f"\nALL {_passed} TOPIC OPENER CHECKS PASSED")
sys.exit(0)
