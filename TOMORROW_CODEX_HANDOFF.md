# FolioAI handoff for Codex on the laptop

Prepared on 6 August 2026 from the main PC.

This document is deliberately self-contained. The laptop Codex must treat the
laptop repository as authoritative because uncommitted files and Codex task
memory do not transfer between devices.

## User objective

Resolve every currently identified FolioAI launch blocker that can be completed
without the founder's identity, banking details, provider logins, legal
signature, or final business approval.

The finished technical result must include:

- a clean-room copyright-safe product boundary
- a safe initial customer product scope
- production job and worker monitoring
- customer policy, support, refund, and beta materials
- copyright-safe original authoring guidance
- comprehensive deterministic verification
- a review branch or pull request, never an autonomous merge to `main`

## Non-negotiable repository rules

Read `AGENTS.md`, `CLAUDE.md`, `handoff.md`, and `DEPLOY.md` before editing.

- Never use an em dash or en dash in code, prompts, docs, or generated copy.
- Keep validation batched through `pipeline._validate_many`. Never change it to
  one LLM call per question.
- Preserve unrelated dirty work. Do not reset, discard, or overwrite existing
  changes.
- Treat authentication, account deletion, payments, credits, and downloads as
  high-risk code.
- Every pipeline, formatter, security, commerce, or operational change needs a
  deterministic check script that fails on the previous behaviour.
- Do not delete the main PC's local assessment PDFs merely to make a check pass.
- Do not upload, embed, migrate, quote, paraphrase, or generate from past
  NAPLAN, WACE, ACER, textbook, workbook, or commercial tutoring material.
- Autonomous work must use a review branch and pull request. Do not push or
  merge directly to `main`.

## Critical device-transfer warning

The current main-PC branch is:

```text
codex/overnight-product-readiness-20260806
```

At the time this file was written, that branch had not been committed or pushed.
The main PC contains a large dirty working tree with the paid-product build and
new safety work. A laptop clone of `origin/main` will not contain those changes.

Before continuing on the laptop, determine which situation applies:

### If the review branch has since been pushed

```powershell
cd "PATH\TO\Booklet-Gen"
git fetch origin
git switch codex/overnight-product-readiness-20260806
git status --short
```

### If the review branch was not pushed

Do not assume that `origin/main` contains the paid product. Either:

1. copy the complete repository from the main PC, including hidden `.git`, or
2. return to the main PC and commit and push the review branch first, or
3. reimplement the work from this handoff after auditing the laptop checkout.

Never copy only a few modified Python files. The paid product spans the web app,
database schema, worker, templates, environment configuration, requirements,
deployment files, and check scripts.

## Product architecture intended for launch

```text
GitHub repository
  -> Render Docker web service
       -> Flask accounts, pages, Stripe Checkout, downloads
  -> Render Docker background worker
       -> Gemini booklet generation

Both Render services
  -> Supabase Postgres for accounts, credits, payments, jobs, and vectors
  -> private Supabase Storage bucket named booklets

Web service
  -> SMTP provider for verification and password reset
  -> Stripe for payment collection and signed webhooks
```

Render is the intended host. `render.yaml` defines a Starter web service and a
Starter background worker. `DEPLOY.md` is the deployment walkthrough.

## Work already present on the main PC

The following was implemented and passed deterministic checks before the final
pause. Verify the laptop checkout rather than assuming it exists there.

### Paid product

- Stripe Checkout for one and ten booklet-credit packs
- idempotent webhook fulfilment
- credit ledger with atomic reservations and failed-job refunds
- durable Postgres-backed generation queue
- separate `python -m booklet_gen.worker` process
- private Supabase Storage with database fallback
- email verification and password reset
- account export and deletion
- Privacy, Terms, Support, Pricing, payment-success, and admin pages
- administrator credit grants and no-charge retries
- job created, started, and completed timestamps

Important files include:

```text
booklet_gen/jobs.py
booklet_gen/worker.py
booklet_gen/webapp/admin.py
booklet_gen/webapp/commerce.py
booklet_gen/webapp/mailer.py
booklet_gen/webapp/payments.py
booklet_gen/webapp/public.py
booklet_gen/webapp/storage.py
booklet_gen/webapp/db.py
booklet_gen/webapp/views.py
render.yaml
.env.webapp.example
```

### Copyright-safe NAPLAN boundary

- `booklet_gen/guidance/naplan_practice.txt` contains a substantial original
  item-writing guide.
- `Program` has `guidance_file` and `use_rag` controls.
- NAPLAN uses `use_rag=False`.
- NAPLAN guidance reaches lessons, questions, recap, and challenge stages.
- Prompt wrappers explicitly refuse reproduction and close paraphrase.
- `scripts/check_copyright_safe_rag.py` verifies the boundary.

### Source-rights enforcement

- `booklet_gen/rag/rights.py` parses a fail-closed CSV register.
- `rag_sources/source_rights.csv` is the tracked register template.
- `scripts/ingest_folder.py` blocks unregistered, uncertain, quarantined, or
  rejected PDFs and stamps approved provenance into every chunk.
- `scripts/migrate_rag_to_postgres.py` rejects old vector stores without
  approval provenance.
- `scripts/check_source_rights.py` verifies missing and ambiguous sources are
  blocked.

The founder subsequently decided not to obtain copyright permissions. The final
product should therefore use no third-party educational RAG at all. Keep the
rights gate as defence in depth, but production should launch with an empty
vector store and external retrieval disabled globally.

### Launch configuration and customer materials

- `scripts/launch_readiness.py` audits beta and live environment configuration
  without printing secrets.
- `scripts/check_launch_readiness.py` verifies the auditor.
- `FOUNDER_TODO.md` contains only irreducible founder actions.
- `LAUNCH_OFFER.md` recommends A$6.90 for one credit, A$36.00 for ten credits,
  one welcome credit, and no subscription during beta.
- `SUPPORT_PLAYBOOK.md` defines support, refund, security, and incident handling.
- `PAID_BETA.md` defines beta entry conditions, test scenarios, feedback, and
  go or no-go measures.
- Privacy, Terms, Support, and Pricing templates were updated with a 14-day
  voluntary quality promise and clearer child-data minimisation.
- `scripts/check_webapp_pages.py` was extended to check the new policy wording.

## Partial work that must be audited before use

An operations agent was interrupted after adding a `worker_heartbeats` table and
helper functions to `booklet_gen/webapp/db.py`. At the last inspection, heartbeat
references appeared only in `db.py`. The worker, health endpoint, admin console,
and deterministic check were not yet confirmed to use them.

Treat this as partial code. Inspect the exact diff, finish it, and test it. Do
not claim worker monitoring is complete merely because the table exists.

The clean-room agent and independent shipping-audit agent were interrupted
before their results were integrated. Reperform both tasks.

## Highest-priority remaining implementation

### 1. Global clean-room mode

Implement one fail-closed configuration boundary with these properties:

- External RAG is disabled by default for every program.
- The separate Methods exam path cannot retrieve by default.
- Retrieval can be enabled only by one explicit environment setting after a
  reviewed source decision.
- NAPLAN remains external-RAG disabled even when a broader development setting
  is enabled, unless the code has a specific safe reason to change this.
- External Wikimedia or other network image lookup is disabled by default.
- Programmatic diagrams, charts, tables, and Folio-owned local assets remain
  available.
- A deterministic check proves that no retriever or external image search is
  called in default production configuration.
- Update `.env.webapp.example`, `render.yaml`, `README.md`, `CLAUDE.md`, and
  `DEPLOY.md` with the final environment-setting names.

Do not rely on an empty database alone. The code itself must default to no
external educational retrieval.

### 2. Safe customer product scope

Default web launch scope:

- Academic Accelerate Mathematics
- Academic Accelerate English
- independently written literacy and numeracy practice for Years 3, 5, 7, and 9

Hide Scholarships and Methods Exam from customer pages and generation requests
by default. Keep them usable through the CLI for development. If an environment
allowlist is used, validate it against known program keys and fail safely.

Server-side generation validation must reject a held product even if a customer
manually submits its old form value. Hiding a radio button alone is insufficient.

Update checks so the landing and generate pages list only customer-enabled
products while the CLI still recognises all program definitions.

### 3. Year-specific original authoring guides

Create original clean-room guides for:

```text
booklet_gen/guidance/naplan_year_3.txt
booklet_gen/guidance/naplan_year_5.txt
booklet_gen/guidance/naplan_year_7.txt
booklet_gen/guidance/naplan_year_9.txt
```

Each guide should describe, in original language:

- suitable reading length and complexity
- writing expectations
- spelling, grammar, and punctuation range
- numeracy content and representation range
- prerequisite skills
- common misconceptions
- difficulty progression
- accessibility and child-safety constraints
- originality review questions

Do not quote Australian Curriculum content descriptions, NAPLAN proficiency
standards, published questions, marking rubrics, passages, or prompts.

Make `Program.authoring_guidance` append the matching year guide for NAPLAN.
Preserve backward compatibility for callers that do not pass a year. Add a
deterministic check that each eligible year receives its own guide and other
years do not receive the wrong guide.

### 4. Worker heartbeat and operations

Complete the partial heartbeat implementation:

- worker writes a heartbeat at boot and no more often than about every 30 seconds
- both SQLite and Postgres are supported
- heartbeat includes non-secret worker status and timestamp
- `/healthz` still checks database connectivity
- in queue mode, health becomes unhealthy when the worker is missing or stale,
  after a reasonable deployment startup grace
- inline local mode does not require a worker heartbeat
- public health output contains no customer identifiers or internal errors
- admin page shows worker freshness, queued count, oldest queue age, recent
  success/failure counts, and typical duration
- add deterministic checks for fresh, missing, and stale heartbeats

Avoid a database write every two seconds. The worker currently polls frequently,
so heartbeat writes must be rate limited.

### 5. Refund ledger operation

`SUPPORT_PLAYBOOK.md` identifies a remaining operational gap: refunds can be
issued through Stripe, but the admin console has only positive credit grants.

Implement an audited credit adjustment that can add or remove credits safely:

- admin-only and CSRF-protected
- reason required
- unique immutable ledger reference
- negative adjustment cannot make the balance negative
- sensible absolute limit per action
- adjustment appears in account history or export
- deterministic security and database checks

Do not permit arbitrary database editing through the browser.

### 6. Release and deployment documentation

After final environment names and features are known:

- update `DEPLOY.md` in exact deployment order
- update `render.yaml` with safe defaults
- make the beta/live launch auditor check clean-room mode, product allowlist,
  worker mode, payments, email, storage, seller details, and HTTPS cookies
- add an explicit fresh-Supabase recommendation so no old vectors can survive
- record the final test commands in `README.md`
- update the current snapshot at the top of `handoff.md`

## Business registration recommendation already researched

The likely initial registration for a solo light-revenue launch is:

```text
Structure: Individual / sole trader
Legal name: founder's full personal legal name
Business name: FolioAI, registered separately with ASIC
Main activity: Educational Support Services
Likely ANZSIC: 8220
Description: Development and online sale of AI-generated educational practice
             booklets for Australian school students
GST: normally not required until expected GST turnover reaches A$75,000
```

This remains a founder and accountant decision. The founder must check ASIC
business-name availability and IP Australia trade marks before registering.

Authoritative references:

- https://www.abr.gov.au/business-super-funds-charities/applying-abn
- https://www.abs.gov.au/statistics/classifications/australian-and-new-zealand-standard-industrial-classification-anzsic/2006-revision-2-0/detailed-classification/p/82/822/8220
- https://business.gov.au/planning/business-structures-and-types/business-structures/sole-trader
- https://www.asic.gov.au/for-business/registering-a-business-name/
- https://business.gov.au/registrations/register-for-taxes/register-for-goods-and-services-tax-gst

## Copyright position already researched

The clean-room approach is based on these boundaries:

- Australian copyright protects original expression, not underlying ideas or
  information.
- Past NAPLAN tests must not be uploaded to an app or used as a commercial or
  noncommercial coaching bank under ACARA's published position.
- FolioAI should independently express general educational skills without
  seeing, storing, retrieving, paraphrasing, or imitating protected material.

Authoritative references:

- https://www.ag.gov.au/rights-and-protections/copyright/copyright-basics
- https://www.acara.edu.au/_resources/20160530_ACARA_information_sheet_NAPLAN_tests_and_copyright.pdf

This is a risk-control strategy, not a guarantee or legal opinion.

## Environment and verification on Windows

The main PC's `.venv` was stale and referenced another user's path. On the
laptop, build a fresh environment:

```powershell
cd "PATH\TO\Booklet-Gen"
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For Unicode output and Matplotlib cache safety:

```powershell
New-Item -ItemType Directory -Force -Path output\.mpl-cache | Out-Null
$env:PYTHONPATH = ".;.venv\Lib\site-packages"
$env:PYTHONUTF8 = "1"
$env:MPLCONFIGDIR = (Resolve-Path output\.mpl-cache).Path
```

Run at minimum:

```powershell
python -m compileall -q booklet_gen scripts
python scripts\check_answer_verification.py
python scripts\check_booklet_render.py
python scripts\check_calculus_validator.py
python scripts\check_commerce_and_jobs.py
python scripts\check_consistency_guards.py
python scripts\check_copyright_safe_rag.py
python scripts\check_hour_cap_and_credits.py
python scripts\check_launch_readiness.py
python scripts\check_lesson_practice_link.py
python scripts\check_library.py
python scripts\check_llm_timeout.py
python scripts\check_passages_and_spelling.py
python scripts\check_prompt_contracts.py
python scripts\check_source_rights.py
python scripts\check_webapp_pages.py
python scripts\check_webapp_security.py
git diff --check
```

Also run every new clean-room, worker-health, admin-adjustment, and year-guide
check added during completion.

`scripts/check_models.py` requires a Gemini key. Postgres backend checks require
a safe test `DATABASE_URL`. Do not point tests at production customer data.

The last complete offline regression run before the partial heartbeat edit had
all 16 selected scripts passing. Compilation also passed after the agents were
interrupted, but that is not proof that the heartbeat feature is complete.

## Completion audit required before claiming done

Do not mark the work complete until current evidence proves every item:

1. Default web generation cannot call external educational RAG.
2. Default exam generation cannot call external educational RAG.
3. Default generation cannot perform an external image lookup.
4. Held products are absent from pages and rejected server-side.
5. All four NAPLAN years receive the correct original year guide.
6. Old vector stores cannot migrate to production.
7. Worker heartbeat affects queue-mode health and is rate limited.
8. Admin metrics contain no public or cross-account exposure.
9. Refund-related credit removal is audited and cannot overdraw the balance.
10. Customer policies and support promise match implemented behaviour.
11. Render and environment docs match actual code setting names.
12. The comprehensive offline suite passes from a clean process.
13. No em dash or en dash exists in newly added package source, prompts, or docs.
14. The result is committed on a review branch and offered as a pull request.
15. `main` has not been autonomously merged or pushed.

## Actions only the founder can finish

Even after all code is complete, leave these for the founder:

- approve or amend `LAUNCH_OFFER.md`
- choose sole trader or company with professional advice if desired
- obtain the ABN and register the FolioAI business name
- decide GST registration and configure pricing accordingly
- supply real seller, support, and public-address details
- accept the final Privacy, Terms, and refund wording
- complete Stripe identity and payout verification
- create Stripe live prices and webhook
- choose and configure the SMTP provider and DNS records
- create the private Supabase bucket and backup policy
- enter secrets in Render
- connect the public domain
- make the controlled live purchase and refund
- print and personally review a booklet
- invite beta testers and make the final launch decision

Never request that the founder paste passwords, banking details, identity
documents, full database URLs, or secret keys into chat.

## First instruction to give laptop Codex

Copy this exact instruction with the file:

> Read `TOMORROW_CODEX_HANDOFF.md`, `AGENTS.md`, `CLAUDE.md`, `handoff.md`, and
> `DEPLOY.md` completely. Audit the laptop checkout against the handoff before
> editing because the main PC had uncommitted work that may not be present.
> Continue the full FolioAI product-readiness objective. Do not use third-party
> educational RAG or external worksheet images. Implement and verify every item
> in the completion audit. Work on a review branch and do not merge or push to
> main.
