# FolioAI

AI-generated practice booklet product for Years 1-10 (Australia). Parents/tutors
generate PDF booklets: mini-lesson -> worked example -> practice questions ->
cumulative "Final Challenge", with a verified answer key.

## What exists

- **Generator pipeline** (`booklet_gen/pipeline.py`): outline parser -> question
  generator -> validator -> intro/lesson writer -> challenge generator -> PDF
  formatter. Calls Gemini via `booklet_gen/llm/`.
- **Booklet types** (`booklet_gen/programs.py`): Scholarships (reasoning engine),
  NAPLAN Practice (maths+English combined), Academic Accelerate (parent picks
  subject: Mathematics or English; Science exists but is not offered, no RAG
  material), Methods Exam (Year 11-12 WACE practice paper, separate pipeline
  and formatter path). Names here are the source of truth for cover/menu labels.
- **Validation**: SymPy for maths, a deterministic cipher/sequence checker for
  Reasoning (`booklet_gen/agents/reasoning_validator.py`), an LLM judge for
  everything else. Validation is **batched** (one call per subtopic, not one
  per question) via `pipeline._validate_many`. Don't regress this to
  per-question calls, it's the main lever on API cost/quota.
- **RAG**: ingested from `rag_sources/<Subject>/<Year>/<Tag>/` via
  `scripts/ingest_folder.py`. `rag_sources/` is gitignored (large + some
  content is copyrighted for personal use only, e.g. ACER scholarship papers).
  Do not migrate the existing local store into the paid product. NAPLAN external
  RAG is disabled in `programs.py`; it uses the original-authoring guide at
  `booklet_gen/guidance/naplan_practice.txt` instead. Build a fresh production
  store only from sources recorded as commercially approved under
  `rag_sources/README.md`. `scripts/ingest_folder.py` requires an affirmative
  entry in `rag_sources/source_rights.csv` and stamps approval provenance into
  every chunk. The Postgres migration rejects stores without that provenance.
  Two store backends behind one interface (`rag/store.py`): Postgres+pgvector
  when `DATABASE_URL` is set, on-disk ChromaDB otherwise.
- **Database**: one Postgres serves both accounts and the vector store, via
  `DATABASE_URL` (see `dbpool.py`). Without it everything falls back to local
  SQLite + Chroma, which is fine locally but means a deployed instance loses
  accounts on restart and has no RAG. `scripts/migrate_rag_to_postgres.py`
  moves an existing Chroma library up without re-embedding.
- **Web app** (`booklet_gen/webapp/`): Flask, `db.py` (Postgres or SQLite),
  dropdown generate form. Accounts, email verification, credits, Stripe
  Checkout, queued generation, private file storage, customer legal pages, and
  an admin support console are implemented in the current working tree. Daily
  caps remain as abuse guards. Treat auth and payment code with more care than
  the rest.
- **Exam papers**: `pipeline.run_exam()` + `formatter.render_exam_pdf()`
  produce a WACE-shaped Methods practice paper (calculator-free and
  calculator-assumed sections, marks, marking key). Separate path from
  booklets. Calculus answers are verified symbolically in
  `agents/validator.py`.
- **Term plans**: `pipeline.run_term_plan()` generates N weekly booklets with a
  difficulty ramp and revision weeks at the end.
- **Deployment**: `Dockerfile` + `DEPLOY.md`, gunicorn entrypoint
  `booklet_gen.webapp:create_app()`.

## Conventions

- **No em dashes, anywhere** (code output, prose, generated booklets). The
  formatter has a deterministic stripper (`_dedash` in `formatter.py`) as a
  backstop, but write clean in the first place.
- **All commits/pushes go to `main`** directly, per explicit user instruction
  from earlier in this project. This is a deliberate override of the usual
  feature-branch default. Exception: anything an autonomous agent does (see
  below) opens a PR instead.
- User is on **Windows** (PowerShell), moderate technical comfort, learning as
  they go. When giving them commands, use PowerShell syntax and remind them to
  `cd` into the repo first if relevant.
- User's Gemini key has **billing enabled** (since 2026-07-27), so free-tier
  rate limits no longer apply. Batched validation still matters for cost.
- Deployed on **Render** with a **Supabase Postgres** (session pooler) as
  DATABASE_URL, backing both accounts and the pgvector RAG library.

## Running things

```
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt   # Windows
python main.py --program accelerate --subject Maths --year "Year 5" --name "Sam"
python -m booklet_gen.webapp     # local web app at 127.0.0.1:5000
```

## Autonomous agent guardrail

Any agent/routine working on this repo without direct real-time user
supervision must open a PR, never push or merge to `main` directly. This repo
handles user accounts and authentication; changes there need human review.
