# Handoff: Folio (Booklet-Gen)

## Current snapshot, 5 August 2026

This section supersedes older statements later in this file that say billing,
admin tooling, legal pages, job completion timestamps, or copyright controls do
not exist. The current working tree contains:

- Stripe Checkout, one-off credit packs, and idempotent webhook fulfilment
- durable queued jobs, completion timestamps, a separate worker, and automatic
  credit return when generation fails
- private Supabase Storage with ownership-checked downloads
- verification and password-reset email
- Privacy, Terms, Support, and Pricing pages
- an administrator support console
- a NAPLAN original-authoring guide with external NAPLAN RAG disabled
- a tracked source-rights register, fail-closed PDF ingestion, and a migration
  block for old vector stores without approval provenance
- an offline beta/live configuration audit at
  `scripts/launch_readiness.py`

These changes are not yet a clean committed release. The founder's remaining
decisions and provider-account actions are isolated in `FOUNDER_TODO.md`. The
technical deployment sequence is in `DEPLOY.md`. Treat older sections below as
historical context when they conflict with this snapshot.

Written for whoever picks this up next (currently: Codex), with no memory of
prior sessions. Read this, then `CLAUDE.md` for the full house rules — this
file is the situation report, `CLAUDE.md` is the standing instructions.

## What this is

Folio generates AI-written practice booklets for Years 1-10 (Australia):
mini-lesson -> worked example -> practice questions -> cumulative "Final
Challenge", plus a verified answer key. Also produces WACE Methods exam
papers (Years 11-12) and multi-week term plans. Owner is a solo, moderate-
technical-comfort developer on Windows/PowerShell, "learning as they go."

**Current state: the product is good and the site around it is real.** This
was not always true. A long quality pass (see "Recent work" below) fixed
real defects a panel of reviewer agents found — wrong homework numbering
against the key, split reading passages, false "symbolically verified"
claims, sub-5pt fraction glyphs, contrast failures, and more. The web app
went from a bare generate form to a real front end with a landing page,
signup/login, a styled generate flow, and a library of past booklets. None
of that is aspirational — it's committed, checked, and pushed to `main`.

**What's NOT done: monetisation.** Folio is currently free and unlimited
(abuse-guarded by a daily cap, not by payment). The owner wants to start
charging — was thinking ~$8/booklet but hasn't committed to a model. This is
the live open question. See "The open task" below.

## Orientation: where things are

```
booklet_gen/
  pipeline.py          The generator: outline -> questions -> validate -> lesson -> PDF
  formatter.py         PDF layout (ReportLab). ~2100 lines. Read before touching.
  schemas.py           Pydantic models shared across the pipeline
  timing.py            Recomputes every printed time estimate from the booklet itself
  programs.py          The 4 product lines (Scholarships/NAPLAN/Accelerate/Methods) — labels here are the source of truth for cover/menu text
  agents/              One agent per pipeline stage (outline_parser, question_generator, intro_writer, challenge_generator, llm_judge, reasoning_validator, validator/SymPy, spelling, term_planner, exam_generator)
  prompts/*.txt        The actual prompts. Treated as load-bearing, not incidental — see check_prompt_contracts.py
  rag/                 Retrieval: store.py picks Postgres+pgvector or local Chroma based on whether DATABASE_URL is set
  webapp/               Flask app — signup/login, generate form, background-threaded generation, polling progress, library, account
    templates/          landing.html (logged-out) / generate.html (logged-in) split at views.index()
    static/css/style.css  The whole stylesheet, one file
    templates/_motifs.html  Hand-drawn SVG line-art macros (pencil, book, paper plane, etc.), traced from booklet_gen/assets/cover_background.jpg
scripts/check_*.py     14 of them. NOT pytest — each is a standalone script with a `check(ok, label, detail)` helper, run directly with PYTHONPATH=.
output/samples/         Two hand-authored Year 5 samples (maths, English) used as reference/regression fixtures. Regenerate via a scratch script that calls render_pdf() directly — see git log for `make_samples.py` if you need to rebuild one.
DEPLOY.md, render.yaml  Deployment. Host-agnostic Dockerfile + gunicorn; render.yaml is a Render Blueprint convenience file only.
```

## Load-bearing constraints (do not violate these silently)

1. **No em dashes, anywhere.** Code, prose, prompts, generated booklets. There
   is a deterministic stripper in `formatter.py` (`_dedash`) as a backstop
   for PDF output, and `scripts/check_prompt_contracts.py` sweeps the entire
   `booklet_gen/` package source for stray em/en dashes (it found and fixed
   four inside Python f-strings that were reaching the LLM, not just docs).
   Write clean the first time.
2. **Validation is batched — one LLM call per subtopic, not per question.**
   This is explicitly called out in `CLAUDE.md` as "the main lever on API
   cost/quota." Do not regress this to per-question calls.
3. **All commits/pushes go to `main` directly**, per the owner's explicit
   standing instruction — a deliberate override of the usual feature-branch
   default. *Exception:* anything an autonomous agent does without direct
   real-time supervision opens a PR instead, because this repo handles real
   user accounts and authentication.
4. **Every formatter/pipeline change needs a check script**, or an addition
   to an existing one, that fails on the old behaviour and passes on the
   new. This repo's whole quality-assurance model is these 14 scripts, not
   a test framework — see `scripts/check_booklet_render.py` for the density
   of what "pinned" means here (geometry measurement, contrast ratios,
   numbering cross-checks against the answer key, not just "does it run").
5. **A page in a Folio booklet must not claim something untrue of itself.**
   This came up for real: the cover used to say "symbolically verified" on
   every all-maths booklet when only ~5% of questions were SymPy-provable
   (the rest go through an LLM judge). Any new claim printed in a booklet or
   shown on the site needs to be true of what actually ran, not aspirational
   copy.
6. **No external network calls from the web app's pages.** Verified with a
   real headless-browser request audit (`scripts/check_webapp_pages.py` +
   a Playwright run) — zero off-site requests across every page. Keep it
   that way; no CDN fonts, no CDN scripts, no third party analytics without
   a deliberate decision to break this.
7. **Image searches (Wikimedia lookups for booklet illustrations) refuse
   queries naming people, ceremonies, or anything sacred**, before the
   search runs — see `booklet_gen/visuals/wikimedia.py:query_is_refused`.
   This exists because an LLM-written query like "Aboriginal ceremony" can
   return culturally restricted material onto a child's worksheet. Do not
   loosen this filter without understanding why it's there.

## Recent work (chronological, most recent first)

Two big pushes, both merged to `main`, both pushed:

**Web app redesign + hosting decision.** The owner asked to "spin this up
into something people can use," which turned into two conversations worth
recording:
- *Hosting*: the owner initially wanted to move off Render, believing
  Render couldn't be used to ship a real public product. That premise was
  wrong — Render ships real products fine; the only real issue was the
  **free** plan's ~15min idle spin-down. Resolution: stayed on Render,
  `render.yaml` moved to `plan: starter`. The Dockerfile was already
  host-agnostic (no Render-specific code anywhere), so this cost nothing.
- *Front end*: the logged-out landing page didn't exist — visitors saw the
  generate form's template with the form hidden and a bare "Sign up" card.
  Built a real `landing.html` (hero, real screenshot of an actual generated
  booklet page rendered via `pdftoppm`, the four product lines pulled live
  from `programs.py` so copy can't drift from code), extracted the inline
  CSS to `static/css/style.css`, added hand-drawn SVG motifs traced from the
  booklet's own cover art (`booklet_gen/assets/cover_background.jpg`) so the
  site and the printed product finally look like one brand, fixed unused
  CSS custom properties (`--orange`, `--green` were declared and never
  referenced), promoted hardcoded hex colours to tokens. New check script:
  `scripts/check_webapp_pages.py`.
- Owner's reaction: likes the redesign, thinks it's "still a tiny bit
  plain" but is happy with it overall. Not asking for another redesign pass
  right now — asking about monetisation next.

**Booklet quality pass.** Ran a panel of reviewer subagents against the two
sample PDFs across ~10 criteria (layout, pedagogy, accessibility, trust/
safety, engagement, production practicalities, etc.), verified every finding
against source before acting on it (several agent claims turned out to be
wrong or already-fixed and were correctly *not* acted on), then fixed what
verified as real, each with a new or extended check script:
- Homework question numbers didn't match the answer key (body printed raw
  running indices, key printed restarted numbers)
- Reading comprehension passages got split 2-in-class/3-in-homework instead
  of staying whole
- Answer key printed a "Class Work" heading for a subtopic with no answers
  under it
- Homework wasn't charged time for the reading passage it referenced
- The "symbolically verified" over-claim described above, plus three other
  printed claims that weren't true of the specific booklet
- Fractions rendered at ~5.3pt effective size (Unicode super/subscript
  digits are ~56% the height of a normal digit) — now full-size with a
  fraction slash
- Extended-response questions ("Explain...") got zero ruled lines to write
  on — now sized lines based on the model answer's length
- Faint text (page numbers, answer-key back-references) below WCAG contrast
  — retuned to AA
- English booklets were structurally starved of grammar/vocabulary practice
  because the hour-cap trimmer always dropped whichever subtopic came last
  in generation order, which for English is always grammar — fixed to
  spread trimming across topics instead of always eating the same one
- Prompts had zero guidance on cultural representation, names, gender
  balance, First Nations content handling, or difficulty ordering outside
  maths — all four addressed in `booklet_gen/prompts/`
- New: `scripts/check_prompt_contracts.py`, which sweeps every prompt file
  for the "no dash" rule and checks specific content rules are present

Full detail is in the git log (`git log --oneline`) — commit messages in
this repo are written as prose explaining *why*, not just *what*, so they're
worth reading directly rather than summarised further here.

## The open task: making this shippable as a paid product

This is genuinely unresolved — treat everything below as "things discussed,
not things decided," except where marked otherwise.

### Unit economics (established, not a live question)
Per booklet is roughly 16-18 LLM calls (mostly Gemini Flash-tier "strong"
calls: one per subtopic for intro-writing, one per subtopic for question
generation, one batched validation call per subtopic, plus outline parse,
recap, challenge). Costs tens of cents, not dollars. **Margin is not the
constraint on pricing — perceived value is.** Nothing currently measures
actual token spend or wall-clock generation time per job; the `jobs` table
has `created_at` and no completion timestamp. Worth adding before pricing
commits to anything, so support conversations aren't guesswork.

### Pricing direction discussed, not decided
- A flat ~$8/booklet was the owner's opening idea. Pushback given: a parent
  comparing that to a $20 Officeworks workbook covering a whole term will
  find it steep, even though this one is personalised.
- The stronger existing asset is `run_term_plan` (`TERM_WEEKS = 10` in
  `pipeline.py`) — 10 booklets with a difficulty ramp and revision weeks
  already built. Priced as a bundle (~$39-49, i.e. $4-5/booklet) it reads
  like "buying a workbook" rather than "an $8 line item," and undercuts a
  tutor by a wide margin.
- A subscription (~$12-19/month, a monthly booklet allowance) was raised as
  possibly the better fit if the owner wants recurring revenue rather than
  one-off sales. Most of the entitlement-counting logic already exists —
  `FOLIO_DAILY_BOOKLET_LIMIT` and the rolling-window `SUM(units)` query in
  `db.py` were built for abuse prevention but are structurally most of what
  a subscription allowance needs.
- No final decision. **Ask the owner which model they want before building
  billing**, don't assume.

### The one risk worth resolving before any money changes hands

**Update, 5 August 2026:** the NAPLAN path now refuses external RAG and uses
`booklet_gen/guidance/naplan_practice.txt`, an internally written item-design
guide with explicit originality rules. `rag_sources/README.md` now treats past
NAPLAN, WACE, ACER and textbook material as quarantined unless commercial rights
are documented. The local files have not been deleted, and other products can
still reach the shared Mathematics store, so production still needs a clean
commercially reviewed vector database rather than a migration of the old one.
`rag_sources/` (gitignored, not present in most sessions — it's real
curriculum material the owner has locally) is documented in `CLAUDE.md` as
containing content "copyrighted for personal use only, e.g. ACER
scholarship papers." I traced the code path: `_generate_from_outline`
returns a `rag_pool` that feeds the question generator, the warm-up recap,
**and** the Final Challenge (`pipeline.py:156-160`) — so retrieved chunks
ground most of a booklet's content when RAG is active for a program.

While Folio was free, "personal use" was at least arguable. Charging money
for output derived from a rights-holder's copyrighted exam material is a
different, real legal exposure — this is the one item on this whole list
that can actually cause harm, not just a bad review.

**Action needed, not yet taken:** determine which `rag_sources/<Subject>/
<Year>/<Tag>/` folders feed which of the 4 programs (Scholarships / NAPLAN
/ Academic Accelerate / Methods Exam). If ACER material is scoped to
Scholarships specifically, the straightforward move is to launch the other
three for money and hold Scholarships back (free/offline) until that
corpus is replaced with licensed or originally-written material. This
needs the owner's rag_sources directory contents to resolve — not visible
in every session since the folder is gitignored.

### What "accepting payment" needs beyond a Stripe integration
Worth surfacing to the owner rather than assuming scope:
- **No admin/support tooling exists at all.** If a paying customer's job
  fails, there is currently no way to look them up, refund them, or
  re-trigger generation without hand-editing the database. A failed
  generation currently just shows a red "Failed" pill and stops — for a
  paid product you need failed jobs to not consume an entitlement, plus
  some retry/support path.
- **No ToS or privacy policy.** The app stores emails and password hashes;
  under Australian Privacy Act obligations plus Australian Consumer Law
  (which restricts what a refund policy can contractually exclude), this
  needs real documents before checkout goes live, not boilerplate.
- Note for context: **payments were already built once and deliberately
  removed** — commit `872dbdd`, `"Remove pricing: make Folio free and
  unlimited"`, which is explicit in its own message about dropping Stripe
  checkout/webhooks, the credits column, and the payments table. Re-adding
  billing is a reasonable direction, just flag to the owner that it's a
  deliberate reversal of a past decision they made on purpose, not a gap
  that was simply never filled in. `CLAUDE.md` currently states as fact
  that generation is free with no payments — it needs updating the moment
  this direction is locked in, or it will actively mislead the next person
  who reads it (including you, next time).

## How to verify anything you touch

```powershell
# Windows/PowerShell — the owner's environment
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
python main.py --program accelerate --subject Maths --year "Year 5" --name "Sam"
python -m booklet_gen.webapp     # local web app at 127.0.0.1:5000
```

```bash
# Check scripts (14 of them). Each is standalone, not pytest.
PYTHONPATH=. python scripts/check_booklet_render.py
PYTHONPATH=. python scripts/check_webapp_pages.py
PYTHONPATH=. python scripts/check_prompt_contracts.py
# ...etc. check_models.py and check_postgres_backends.py need
# GEMINI_API_KEY / DATABASE_URL respectively and will fail without them —
# that's expected in a bare checkout, not a regression.
```

Two samples exist at `output/samples/year5-{maths,english}-sample.pdf` for
visual reference — they were hand-authored (not LLM-generated) specifically
so formatter/layout work could be tested without an API key. If you need to
regenerate them after a formatter change, they're built by calling
`render_pdf()` directly against hand-built `BookletData` fixtures; check
recent git history for the scratch script that does this if you need to
rebuild rather than reconstructing it from scratch.

## What the owner actually wants from you next

Their own words: "I want to focus on how to actually spin Folio up into a
final shippable product from here... my goal is to start making some light
money from this — maybe $8 per booklet? I don't know, I need to work out
pricing as well."

Read: they want a concrete path to charging money, but they do not have a
firm pricing model yet and said so themselves — this is a place to ask
questions and lay out tradeoffs (as above), not to silently pick a number
and build billing around it. The RAG/copyright question above should be
resolved or explicitly deferred-with-reason before checkout goes live,
because it's the one item here with real legal weight rather than just
product risk.
