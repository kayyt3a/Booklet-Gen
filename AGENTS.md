# FolioAI: agent instructions

**`CLAUDE.md` is the source of truth for this repository. Read it first.**
It describes the pipeline, the booklet types, the RAG rights gate, the database
layout, the web app, and how to run things. This file exists because Codex
looks for `AGENTS.md` and Claude Code looks for `CLAUDE.md`. Keeping two full
copies drifts, and has already drifted once, so this one stays short and points
at the other.

The rules below are repeated here rather than only referenced, because they are
the ones that cause real damage when missed.

## Non-negotiable

- **No em dashes or en dashes anywhere.** Code, prompts, docs, commit messages,
  generated booklets. `_dedash` in `formatter.py` is a backstop, not a licence.
- **Keep validation batched** through `pipeline._validate_many`, one call per
  subtopic. Never one LLM call per question. It is the main lever on API cost.
- **Autonomous work opens a pull request.** Never push or merge to `main`
  without direct real-time supervision. This repo handles accounts, auth, and
  payments.
- **Preserve unrelated dirty work.** Do not reset, discard, or overwrite
  changes you did not make.
- **Treat auth, account deletion, payments, credits, and downloads as
  high-risk.** Read the surrounding code before changing it.
- **Every pipeline, formatter, security, commerce, or operational change needs
  a deterministic check script** under `scripts/check_*.py` that fails on the
  previous behaviour.
- **No third-party assessment material.** Do not upload, embed, migrate, quote,
  paraphrase, or generate from past NAPLAN, WACE, ACER, textbook, workbook, or
  commercial tutoring content. Production launches with an empty vector store
  and external retrieval disabled. See the RAG section of `CLAUDE.md`.
- **Never ask the founder to paste** passwords, banking details, identity
  documents, full database URLs, or secret keys into a chat, and never commit
  them.
- **Do not delete local assessment PDFs** on the founder's machine merely to
  make a check pass.

## Checks are the contract between agents

Two agents cannot read each other's reasoning, only each other's code. A check
script is an executable claim about behaviour, so it survives a handoff in a
way that prose does not. Before trusting a description of what was built, run:

```
PYTHONPATH=. python scripts/check_<name>.py
```

`check_models.py` needs `GEMINI_API_KEY` and `check_postgres_backends.py` needs
`DATABASE_URL`. The rest run offline.

When a check fails, work out which side is stale before editing either one. A
check written against older behaviour is a stale check, not a code defect.
