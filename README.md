# Booklet-Gen

A multi-agent tutoring booklet generator. Ships **Maths, Science, and English** — each with its own generator prompt and its own validation strategy. Output is a clean PDF booklet with a verified-answer key.

## Pipeline

```
description ("Year 8 maths, fractions and ratios")
  -> Outline Parser Agent   (fast LLM tier, Pydantic-validated JSON)
  -> Question Generator Agent (strong LLM tier, subject-specific system prompt, loops per subtopic)
  -> Validator Agent        (tiered: sympy for maths, LLM-as-judge for science/english)
  -> Formatter              (ReportLab PDF)
```

Each agent lives in its own module (`booklet_gen/agents/`). Each subject's generator system prompt lives in its own file under `booklet_gen/prompts/` so you can iterate per-subject without touching code.

## Tiered validation

| Subject     | Primary validator            | Fallback                        |
| ----------- | ---------------------------- | ------------------------------- |
| Mathematics | `sympy` symbolic check       | LLM-judge if sympy has no handle |
| Science     | LLM-as-judge (fresh context) | —                               |
| English     | LLM-as-judge (fresh context) | —                               |

- **Sympy path**: for algebra/arithmetic/equations. Substitutes the proposed answer back into the equation from the question (`x = 4` into `2x + 3 = 11`), or checks `simplify(expr - answer) == 0` for compute/simplify questions. Handles prompt prefixes like "Solve for x:" by walking off leading tokens until sympify succeeds.
- **LLM-judge path**: a separate LLM call with an independent system prompt (`prompts/validator_llm_judge.txt`). Because the API call is stateless, the judge is grading someone else's work rather than self-checking — per the brief.
- Unverified questions are still included in the booklet but skip the check mark, and every subtopic's failure rate is logged so weak spots are visible.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env — set LLM_PROVIDER and the matching API key
```

The LLM backend is provider-agnostic. Swap providers by changing `LLM_PROVIDER` in `.env`:

- `gemini` (default) — uses `google-generativeai`, needs `GEMINI_API_KEY`
- `claude` — uses `anthropic`, needs `ANTHROPIC_API_KEY`

Fast/strong tiers per provider are set via `*_MODEL_FAST` / `*_MODEL_STRONG`. Outline parsing runs on the fast tier; question generation and LLM-judge run on the strong tier.

## Run

CLI:
```bash
python main.py "Year 8 maths, fractions and ratios" --name "Alex" --questions 5
python main.py "Year 9 science, chemical reactions and stoichiometry" --name "Sam"
python main.py "Year 10 English, persuasive writing" --name "Priya"
```

Web:
```bash
python app.py
# open http://127.0.0.1:5000
```

Outputs land in `output/`. Structured JSONL logs land in `logs/`.

## Resource library (RAG)

The pipeline reads from a local, private ChromaDB store before each subtopic runs. Retrieved chunks are passed to the Question Generator as reference material (for style/difficulty calibration) and to the LLM-judge as grounding (for cross-checking answers against real textbook material).

**Ingest a source:**
```bash
python -m booklet_gen.rag.ingest path/to/file.pdf \
    --subject Mathematics --year "Year 6" --topics "Fractions,Decimals"
```

Supported inputs: `.pdf` (text-based, no OCR), `.txt`, `.md`.

**Where things live** (all gitignored):
- Raw source files: `rag_sources/` (recommended — not enforced)
- Vector store: `rag_store/`

**Embeddings**: Gemini `text-embedding-004` (free tier). Requires `GEMINI_API_KEY` even if `LLM_PROVIDER=claude` — embeddings and generation are decoupled. Without a Gemini key, the retriever degrades gracefully (returns no chunks; the pipeline runs exactly as before RAG was added).

**Copyright**: keep source files and the vector store private. The `.gitignore` already excludes them.

## Not in v1

- **File-upload outlines** — the Outline Parser already handles arbitrary text; a PDF upload path in the web UI just needs to extract text and call the same agent.
- **Additional subjects** — add a `prompts/question_generator_<subject>.txt` file, register the subject in `question_generator.py`, and optionally add a subject-specific validator (e.g. a chemistry-equation-balancing rules engine to strengthen science validation).
- **Senior maths sympy adapters** (integration, implicit differentiation, vectors) — currently fall back to LLM-judge; a real sympy adapter would give rigorous symbolic verification for Year 11-12 topics.
- **Analytics / auth** — v2.

## Layout

```
booklet_gen/
  agents/
    outline_parser.py       description -> Outline JSON
    question_generator.py   subject-aware question generation
    validator.py            sympy symbolic validator
    llm_judge.py            LLM-as-judge validator (fresh context)
  llm/
    base.py                 provider-agnostic interface
    gemini.py               Gemini backend
    claude.py               Claude backend
  prompts/
    outline_parser.txt
    question_generator_maths.txt
    question_generator_science.txt
    question_generator_english.txt
    validator_llm_judge.txt
  config.py                 env-loaded config
  schemas.py                Pydantic schemas at every agent boundary
  pipeline.py               orchestration + validator routing
  formatter.py              PDF renderer
  logging_setup.py          JSONL structured logs
app.py                      Flask web UI
main.py                     CLI
```
