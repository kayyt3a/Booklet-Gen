# FolioAI

An AI practice-booklet generator for Australian students. A parent or tutor
picks a booklet type, year level, and topic, and FolioAI produces a printable PDF:
a mini-lesson, worked examples, guided practice, independent questions, a
cumulative final challenge, and an answer key whose maths has been verified
symbolically rather than taken on trust.

It also produces full WACE-shaped practice examination papers for Year 12
Mathematics Methods, with calculator-free and calculator-assumed sections,
mark allocations, and a marking key.

The customer web app includes one free booklet, then pay-as-you-go booklet
credits. A CLI is also included for owner and development use.

## Product lines

| Program              | Covers                                    | Years |
| -------------------- | ----------------------------------------- | ----- |
| Academic Accelerate  | School revision. Parent picks the subject | 1-10  |
| NAPLAN Practice      | Numeracy and literacy in one booklet      | 1-10  |
| Scholarships         | Verbal and quantitative reasoning         | 1-10  |
| Methods Exam         | Full ATAR practice paper + marking key    | 11-12 |

Term plans generate ten weekly booklets at once, with a difficulty ramp and
revision weeks at the end.

## Pipeline

```
request ("Year 8 maths, fractions and ratios")
  -> Outline Parser        fast tier, Pydantic-validated JSON
  -> per subtopic, concurrently:
       approved RAG        commercially reviewed curriculum material, when enabled
       Intro Writer        mini-lesson, worked example, guided examples
       Question Generator  one batch covering classwork and homework
       Validator           batched, one call per subtopic
  -> Recap + Final Challenge
  -> Formatter             ReportLab PDF
```

Each agent is its own module under `booklet_gen/agents/`, and each subject's
system prompt is a separate file under `booklet_gen/prompts/`, so prompts can
be iterated without touching code. Subtopics run on a thread pool since each is
a batch of network-bound calls.

Exam papers take a separate path: `pipeline.run_exam()` targets a mark total
per section rather than a question count, and `formatter.render_exam_pdf()`
renders an examination front page, marks in the margin, working space scaled to
the marks, and a marking key.

## Validation

The point of the project. Generated answers are checked before they reach a
student, by whichever method can actually prove the answer.

| Path                    | Handles                                            | Cost      |
| ----------------------- | -------------------------------------------------- | --------- |
| SymPy symbolic          | Algebra, arithmetic, derivatives, integrals         | No API call |
| Reasoning checker       | Letter-shift ciphers, arithmetic/geometric sequences | No API call |
| LLM judge, fresh context| English, comprehension, everything else             | One batched call |

- **Algebra**: substitutes the proposed answer back into the equation found in
  the question text, or checks `simplify(expr - answer) == 0`.
- **Calculus**: derivatives compared against `sp.diff` (including derivatives
  at a point), definite integrals against `sp.integrate`, and indefinite
  integrals verified by differentiating the answer back to the integrand, which
  makes the `+ c` term irrelevant. The parser accepts what a model actually
  writes: `3x^2`, `sin(2x)`, `e^x`.
- **Reasoning**: derives the shift rule from a cipher's worked example and
  rejects the question outright if no consistent rule exists, catching
  unsolvable puzzles that an LLM judge waves through.
- **LLM judge**: a separate stateless call, so it grades someone else's work
  rather than self-checking. Validation is **batched**, one call per subtopic
  rather than one per question, which is the main lever on API cost.

Anything a checker cannot parse defers to the judge rather than being wrongly
rejected. Unverified questions still appear but without the check mark, and
each subtopic's failure rate is logged.

## Resource library (RAG)

Commercially reviewed curriculum material can be chunked, embedded with Gemini
`gemini-embedding-001`, and retrieved per subtopic. Past assessment papers are
not an approved commercial source merely because they are public. The NAPLAN
product therefore keeps external RAG disabled and uses an internally written,
copyright-safe authoring guide instead. See `rag_sources/README.md` before
ingesting or migrating any source.

Two interchangeable backends:

- **Postgres + pgvector** when `DATABASE_URL` is set. Deployments use this for
  a freshly reviewed production library.
- **On-disk ChromaDB** otherwise, for a local checkout.

```bash
python scripts/ingest_folder.py        # ingest rag_sources/<Subject>/<Year>/<Tag>/
python scripts/rag_status.py           # what is in the library, and what is missing
python scripts/migrate_rag_to_postgres.py   # approved stores only, never the old paper archive
```

Retrieval filters on subject and year, with an `All Years` wildcard for
cross-year curriculum documents. It degrades gracefully: an empty or
unreachable store returns no chunks and generation continues ungrounded.

PDF text extraction is pypdf per page, falling back to Tesseract OCR for pages
that extract almost nothing, which is common in workbook scans.

Raw source files and the vector store are gitignored. The auditable
`rag_sources/source_rights.csv` register remains tracked. Personal-use material
must remain outside the paid production store.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # set GEMINI_API_KEY
```

The LLM backend is provider-agnostic via `LLM_PROVIDER`: `gemini` (default,
needs `GEMINI_API_KEY`) or `claude` (needs `ANTHROPIC_API_KEY`). Fast and
strong tiers are set per provider with `*_MODEL_FAST` / `*_MODEL_STRONG`.
Outline parsing uses the fast tier; everything quality-sensitive uses strong.

Embeddings always use Gemini, so a `GEMINI_API_KEY` is needed for RAG even when
generating with Claude.

## Run

CLI:
```bash
python main.py --program accelerate --subject Maths --year "Year 5" --name "Sam"
```

Web app:
```bash
python -m booklet_gen.webapp        # http://127.0.0.1:5000
```

Outputs land in `output/`, structured JSONL logs in `logs/`.

## Web app

Flask, in `booklet_gen/webapp/`. Accounts gate access. New accounts receive one
booklet credit, and Stripe Checkout sells single-booklet and term-plan credit
packs. A per-account and whole-service daily cap limits abusive API spend.
Generation jobs are stored in Postgres and claimed by a separate worker, so a
web redeploy does not silently discard the queue. Finished files use private
Supabase Storage when configured, with a database fallback.

Passwords are hashed with werkzeug. Sessions are signed with
`FLASK_SECRET_KEY`. Production can require email verification, and password
recovery uses signed, expiring links sent over SMTP.

## Deployment

Dockerfile plus `render.yaml`, with a gunicorn web service and a durable
generation worker. See `DEPLOY.md`. One Postgres backs accounts, payments,
generation jobs, and the vector library.

## Layout

```
booklet_gen/
  agents/          outline parser, intro writer, question + challenge +
                   exam generators, term planner, validators
  llm/             provider-agnostic client (Gemini, Claude)
  prompts/         one system prompt per subject and agent
  rag/             chunking, embeddings, store (pgvector or Chroma), retrieval
  visuals/         matplotlib diagrams, including 3D solids for volume
  webapp/          Flask app: auth, generate form, library, downloads
  pipeline.py      orchestration, validator routing, concurrency
  formatter.py     booklet and exam PDF renderers
  schemas.py       Pydantic schemas at every agent boundary
  dbpool.py        shared Postgres pool
scripts/           ingestion, RAG status, migration, verification checks
main.py            CLI
```

## Verification scripts

Not a full test suite, but the correctness-critical paths have checks:

```bash
python scripts/check_calculus_validator.py   # 17 cases, right and wrong answers
python scripts/check_postgres_backends.py    # accounts + pgvector, needs DATABASE_URL
python scripts/check_library.py              # history, downloads, retention, isolation
python scripts/check_copyright_safe_rag.py   # NAPLAN guide and restricted-RAG boundary
python scripts/check_source_rights.py         # ingestion and migration approval gate
python scripts/check_launch_readiness.py      # beta and live environment audit
```

## Known gaps

- No automated test suite beyond the verification scripts above.
- Science has an engine and prompts but is not offered, since there is no
  Science material in the library to ground it.
- `google-generativeai` is deprecated upstream; a migration to `google-genai`
  is pending.
- The policy templates must be reviewed and populated with the operator's real
  business details before launch.
