# Booklet-Gen

A multi-agent tutoring booklet generator. v1 ships maths only, one year level at a time, with a `sympy`-verified answer key and clean PDF output.

## Pipeline

```
description ("Year 8 maths, fractions and ratios")
  -> Outline Parser Agent   (fast LLM tier, Pydantic-validated JSON)
  -> Question Generator Agent (strong LLM tier, per subtopic)
  -> Validator Agent        (sympy symbolic check)
  -> Formatter              (ReportLab PDF)
```

Each agent lives in its own module (`booklet_gen/agents/`) and its system prompt lives in its own file (`booklet_gen/prompts/`) so you can iterate per-subject without touching code.

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

Fast/strong tiers per provider are set via `*_MODEL_FAST` / `*_MODEL_STRONG`. Parsing runs on the fast tier; question generation runs on the strong tier.

## Run

CLI:
```bash
python main.py "Year 8 maths, fractions and ratios" --name "Alex" --questions 5
```

Web:
```bash
python app.py
# open http://127.0.0.1:5000
```

Outputs land in `output/`. Structured JSON logs land in `logs/`.

## Verified answers

Every question is checked with `sympy` after generation:

- Equation-style answers (`x = 3`, `x = 1/2 or x = -2`) — substitute the answer back into the equation from the question.
- Compute/simplify-style answers — parse the largest expression in the question and check `simplify(expr - answer) == 0`.

Verified questions get a green check mark in the PDF and a "verified" marker in the answer key. Unverified questions are flagged in the logs (`pipeline.subtopic.done` records the `failure_rate` per subtopic) but still included — never silently discarded.

## Not in v1 (extensible architecture)

The architecture is designed to extend without rewrites:

- **Second subject (chemistry, etc.)** — add a new prompt file `prompts/question_generator_<subject>.txt` and a subject-specific validator (rules engine for stoichiometry, LLM-as-judge for qualitative subjects). The Question Generator agent loads its system prompt by subject.
- **RAG resource library** — retrieval runs before Question Generator per subtopic. Chunks pass into the generator context as reference material.
- **File-upload outlines** — the Outline Parser already handles arbitrary text; a PDF upload path just extracts text and calls the same agent.
- **Analytics / auth** — v2.

## Layout

```
booklet_gen/
  agents/           outline_parser.py, question_generator.py, validator.py
  llm/              provider-agnostic LLM interface + gemini/claude backends
  prompts/          per-agent, per-subject system prompts
  config.py         env-loaded config
  schemas.py        Pydantic schemas at every agent boundary
  pipeline.py       orchestration
  formatter.py      PDF renderer
  logging_setup.py  JSONL structured logs
app.py              Flask web UI
main.py             CLI
```
