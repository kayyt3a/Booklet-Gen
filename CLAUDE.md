# Folio

AI-generated practice booklet product for Years 1-10 (Australia). Parents/tutors
generate PDF booklets: mini-lesson -> worked example -> practice questions ->
cumulative "Final Challenge", with a verified answer key.

## What exists

- **Generator pipeline** (`booklet_gen/pipeline.py`): outline parser -> question
  generator -> validator -> intro/lesson writer -> challenge generator -> PDF
  formatter. Calls Gemini via `booklet_gen/llm/`.
- **Booklet types** (`booklet_gen/programs.py`): Scholarships (reasoning engine),
  NAPLAN Practice (maths+English combined), Academic Accelerate (parent picks
  subject). Names here are the source of truth for cover/menu labels.
- **Validation**: SymPy for maths, a deterministic cipher/sequence checker for
  Reasoning (`booklet_gen/agents/reasoning_validator.py`), an LLM judge for
  everything else. Validation is **batched** (one call per subtopic, not one
  per question) via `pipeline._validate_many` — don't regress this to
  per-question calls, it's the main lever on API cost/quota.
- **RAG**: local ChromaDB store, ingested from `rag_sources/<Subject>/<Year>/<Tag>/`
  via `scripts/ingest_folder.py`. `rag_sources/` is gitignored (large + some
  content is copyrighted for personal use only, e.g. ACER scholarship papers).
- **Web app** (`booklet_gen/webapp/`): Flask, SQLite (`db.py`), Stripe checkout
  (`billing.py`), dropdown generate form. Accounts + credits are live in this
  codebase — treat auth/payment code with more care than the rest.
- **Term plans**: `pipeline.run_term_plan()` generates N weekly booklets with a
  difficulty ramp and revision weeks at the end.
- **Deployment**: `Dockerfile` + `DEPLOY.md`, gunicorn entrypoint
  `booklet_gen.webapp:create_app()`.

## Conventions

- **No em dashes, anywhere** (code output, prose, generated booklets). The
  formatter has a deterministic stripper (`_dedash` in `formatter.py`) as a
  backstop, but write clean in the first place.
- **All commits/pushes go to `main`** directly, per explicit user instruction
  from earlier in this project — this is a deliberate override of the usual
  feature-branch default. Exception: anything an autonomous agent does (see
  below) opens a PR instead.
- User is on **Windows** (PowerShell), moderate technical comfort, learning as
  they go. When giving them commands, use PowerShell syntax and remind them to
  `cd` into the repo first if relevant.
- User's Gemini key is on the **free tier** (500 requests/day) unless they've
  said otherwise recently — batched validation and `--workers` exist partly to
  stretch this.

## Running things

```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt   # Windows
python main.py --program accelerate --subject Maths --year "Year 5" --name "Sam"
python -m booklet_gen.webapp     # local web app at 127.0.0.1:5000
```

## Autonomous agent guardrail

Any agent/routine working on this repo without direct real-time user
supervision must open a PR, never push or merge to `main` directly. This repo
handles user accounts and Stripe payments; changes there need human review.
