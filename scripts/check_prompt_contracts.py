"""The prompts are the product, and nothing was checking them.

Everything a parent reads in a booklet is written by one of the files in
`booklet_gen/prompts/`, yet the whole of `scripts/` tested the code around them
and never the prompts themselves. A rule silently dropped from a prompt shows
up as a worse booklet weeks later, with nothing failing in between.

This pins the contracts that are supposed to hold across all of them, and the
few per-file rules that a booklet's quality actually rests on.

Run: python scripts/check_prompt_contracts.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from booklet_gen.agents._shared import PROMPT_DIR, load_prompt

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


PROMPTS = sorted(p.name for p in PROMPT_DIR.glob("*.txt"))
QUESTION_GENERATORS = [p for p in PROMPTS if p.startswith("question_generator_")]
INTRO_WRITERS = [p for p in PROMPTS if p.startswith("intro_writer_")]


# ---------------------------------------------------------------------------
print("\nEvery prompt is loadable and substantial")
print("-" * 62)
check(len(PROMPTS) >= 12, "the prompt directory is populated", f"{len(PROMPTS)} files")
check(bool(QUESTION_GENERATORS) and bool(INTRO_WRITERS),
      "question generators and intro writers are both present",
      f"{len(QUESTION_GENERATORS)} generators, {len(INTRO_WRITERS)} writers")
for name in PROMPTS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(len(body.split()) > 80, f"{name} says something", f"{len(body.split())} words")


# ---------------------------------------------------------------------------
print("\nThe global blocks reach every prompt")
print("-" * 62)
# Both are appended by load_prompt rather than written into each file, so the
# thing worth checking is that no prompt bypasses that path.
for name in PROMPTS:
    loaded = load_prompt(name)
    check("WRITING STYLE" in loaded and "PEOPLE AND PLACES" in loaded,
          f"{name} carries the style and people rules")


# ---------------------------------------------------------------------------
print("\nThe house rule, in the prompts themselves")
print("-" * 62)
# CLAUDE.md: no em dashes anywhere. The formatter strips them as a backstop,
# but a prompt containing one teaches the model to produce them.
DASHES = ("—", "–")
for name in PROMPTS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    hits = [line.strip()[:60] for line in body.splitlines()
            if any(d in line for d in DASHES)]
    check(not hits, f"{name} contains no em or en dash", str(hits[:2]))


# ---------------------------------------------------------------------------
# The user turns are built in Python, not in the prompt files, and four of them
# carried an em dash into the same request that tells the model never to use
# one. The rest were comments, swept for the same house rule.
ALLOWED_DASH_LINES = ("_GLOBAL_STYLE", "_EM_DASH", "_EN_RANGE", "_EN_DASH",
                      "(—) or en dashes")
package = Path(__file__).resolve().parent.parent / "booklet_gen"
offenders = []
for py in sorted(package.rglob("*.py")):
    for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
        if any(d in line for d in DASHES) and not any(
                a in line for a in ALLOWED_DASH_LINES):
            offenders.append(f"{py.relative_to(package.parent)}:{n}")
check(not offenders, "no em or en dash anywhere in the package source",
      str(offenders[:3]))


print("\nPeople and places")
print("-" * 62)
# None of this was written down anywhere before. The prompts asked for
# Australian settings and named people, said nothing about which names, and
# nothing about real institutions, which is how a sample came to run an
# invented news story about a real Perth primary school with invented staff
# named in it.
people = load_prompt(PROMPTS[0])
RULES = [
    ("real institutions are off limits", r"[Nn]ever name a real school"),
    ("names come from a real Australian classroom", r"Mandarin|Vietnamese|Punjabi"),
    ("genders are balanced in what they do", r"[Bb]alance what they DO|balance what they do"),
    ("First Nations peoples are written in the present tense",
     r"present tense"),
    ("peoples are never listed alongside animals", r"alongside animals"),
    ("contexts stay affordable", r"[Nn]o overseas holidays|affordable"),
]
for label, pattern in RULES:
    check(bool(re.search(pattern, people)), label)


# ---------------------------------------------------------------------------
print("\nDifficulty ordering, in every question generator")
print("-" * 62)
# The booklet splits each generated set in order, the front becoming in-lesson
# practice and the rest homework. An unordered set puts an easy item exactly
# where the hardest work belongs, and a hard one on question 1. Maths pinned
# this and English left it to luck.
for name in QUESTION_GENERATORS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(bool(re.search(r"easiest to hardest", body, re.IGNORECASE)),
          f"{name} orders its set easiest to hardest")
    check(bool(re.search(r"[Qq]uestion 1 is the plainest", body)),
          f"{name} makes question 1 the plainest form")


# ---------------------------------------------------------------------------
print("\nReading level is given as a number, not a wish")
print("-" * 62)
# "Keep language calibrated to the year level" is unmeasurable and produced a
# Year 5 maths lesson at reading grade 9.9, opening on a 26 word sentence with
# three embedded clauses. A struggling reader cannot reach the maths through
# the prose.
for name in INTRO_WRITERS + ["question_generator_english.txt"]:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(bool(re.search(r"\d+\s*to\s*\d+\s*words a sentence", body)),
          f"{name} names an average sentence length")
    check(bool(re.search(r"no sentence over", body, re.IGNORECASE)),
          f"{name} names a maximum sentence length")


# ---------------------------------------------------------------------------
print("\nImage queries never go looking for people")
print("-" * 62)
# The licence filter checks copyright, which has nothing to say about what is
# in the picture, and the result prints on a page a child reads. For Aboriginal
# and Torres Strait Islander subjects in particular, an image of the deceased
# or of restricted material is a serious harm and not something a decorative
# photograph is worth.
from booklet_gen.visuals.wikimedia import query_is_refused   # noqa: E402

IMAGE_QUERY_AUTHORS = [
    "question_generator_english.txt",
    "question_generator_science.txt",
    "challenge_generator_english.txt",
    "challenge_generator_science.txt",
    "visual_planner.txt",
]
for name in IMAGE_QUERY_AUTHORS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    check(bool(re.search(r"OBJECT, ANIMAL|Never people", body)),
          f"{name} tells the model to name a thing, not a person")

ALLOWED = ["Bogong moth", "wetland with frogs", "steam locomotive",
           "Kalgoorlie goldfields pipeline", "skate ramp", "coral reef"]
REFUSED = ["Aboriginal ceremony", "Torres Strait Islander dancers",
           "a group of children playing", "Indigenous burial site",
           "sacred site", "war memorial soldiers", "family portrait"]
for q in ALLOWED:
    check(not query_is_refused(q), f"{q!r} is searchable")
for q in REFUSED:
    check(query_is_refused(q), f"{q!r} is refused before the search runs")
check(not query_is_refused(""), "an empty query is handled without blowing up")


print("\nPurposeful visuals are planned as a safe batch")
print("-" * 62)
planner = (PROMPT_DIR / "visual_planner.txt").read_text(encoding="utf-8")
check("one complete batch" in planner and "never one call per question" in planner,
      "the visual planner is one batched call")
for level in ("required", "strong", "helpful", "text-only"):
    check(level in planner, f"the visual planner defines {level!r}")
for field in ("diagram_spec", "scene_spec", "image_query"):
    check(field in planner, f"the visual planner returns {field}")
check("must not contain `answer` or `working`" in planner,
      "answers and working are never sent to the visual planner")
check("Never infer, calculate, request, repeat or encode an answer" in planner,
      "the visual planner cannot encode answers")
check("semantic facts" in planner and "coordinates" in planner,
      "scene specs contain semantic facts rather than drawing instructions")
check(bool(re.search(r"Unknown values stay null|unknown value stays null", planner,
                     re.IGNORECASE)),
      "unknown scene values stay null")
check("exactly `x` or `?`" in planner,
      "unknown scene labels stay x or question mark")

SCENE_TEMPLATES = [
    "shadow_similarity", "ladder_wall", "shelves", "ribbon_measure",
    "race_progress", "shopping", "equal_groups_scene", "scoreboard",
    "garden", "timeline", "science_process", "force_scene", "circuit",
    "particle_model", "life_cycle", "reasoning_sequence", "logic_grid",
]
for template in SCENE_TEMPLATES:
    check(f"`{template}`" in planner,
          f"the visual planner knows the {template} scene")

from booklet_gen.visuals.scene_specs import normalise_scene_spec  # noqa: E402

prefix = "{\"template\""
scene_examples = [part for line in planner.splitlines()
                  for part in line.split("`") if part.startswith(prefix)]
validated_templates = []
for example in scene_examples:
    try:
        validated_templates.append(normalise_scene_spec(json.loads(example))["template"])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
check(len(validated_templates) == 16,
      "every compact scene example passes the real scene validator",
      f"{len(validated_templates)} of 16")
check(all(t in validated_templates for t in SCENE_TEMPLATES
          if t != "shadow_similarity"),
      "the validated examples cover every non-shadow scene template")
check('"template": "shadow_similarity"' in planner
      and '"height": null' in planner and '"symbol": "x"' in planner,
      "the shadow example preserves its unknown instead of solving it")

check("similar_triangles" in planner and "column_arithmetic" in planner,
      "the planner retains existing technical diagrams")
check("source-rights approval and human review" in planner,
      "external images require rights approval and human review")


print("\nEvery content stage has the same visual schema")
print("-" * 62)
VISUAL_STAGE_PROMPTS = (
    QUESTION_GENERATORS
    + INTRO_WRITERS
    + [p for p in PROMPTS if p.startswith("challenge_generator_")]
    + ["exam_generator_methods.txt"]
)
for name in VISUAL_STAGE_PROMPTS:
    body = (PROMPT_DIR / name).read_text(encoding="utf-8")
    for field in ("diagram_spec", "scene_spec", "image_query"):
        check(field in body, f"{name} carries {field}")
    check(bool(re.search(r"Always include `diagram_spec`, `scene_spec` and `image_query`",
                         body)),
          f"{name} makes all visual fields explicit")


print("\nClean-room imagery stays honest")
print("-" * 62)
guidance_dir = Path(__file__).resolve().parent.parent / "booklet_gen" / "guidance"
visual_contract_files = [PROMPT_DIR / p for p in PROMPTS]
visual_contract_files += [
    guidance_dir / "accelerate_practice.txt",
    guidance_dir / "naplan_practice.txt",
]
dead_promises = []
for path in visual_contract_files:
    body = path.read_text(encoding="utf-8")
    for phrase in ("We search Wikimedia", "look these up on Wikimedia",
                   "print the top free-licensed"):
        if phrase.lower() in body.lower():
            dead_promises.append(f"{path.name}: {phrase}")
check(not dead_promises,
      "no prompt promises that Wikimedia will supply production imagery",
      str(dead_promises[:3]))
for name in ("accelerate_practice.txt", "naplan_practice.txt"):
    body = (guidance_dir / name).read_text(encoding="utf-8")
    check("PURPOSEFUL VISUALS" in body,
          f"{name} has curriculum-wide visual guidance")
    check("required, strong, helpful or" in body,
          f"{name} carries visual priority classes")
    check("semantic facts only" in body and "rights approval" in body,
          f"{name} pins scene and image rights safety")

intro_maths = (PROMPT_DIR / "intro_writer_maths.txt").read_text(encoding="utf-8")
check("The picture in a guided example is part of the fill-in task" in intro_maths,
      "guided maths visuals are explicitly unsolved")
check("omit `mark_at` and `label_at`" in intro_maths,
      "guided number lines cannot print the point the child must place")


print("\nWord problems are set in something a child cares about")
print("-" * 62)
maths = (PROMPT_DIR / "question_generator_maths.txt").read_text(encoding="utf-8")
check(bool(re.search(r"household[- ]object", maths)),
      "the maths generator is told to ration household-object contexts")
check(bool(re.search(r"sport|skate|bike|pocket money", maths, re.IGNORECASE)),
      "and given contexts a ten year old would choose")


# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
if failures:
    print(f"\n{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
