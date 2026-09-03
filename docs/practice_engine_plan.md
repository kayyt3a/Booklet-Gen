# FolioAI practice grind engine: implementation plan

WACE ATAR Mathematics Methods and Chemistry. A student picks a scope, presses
an arrow, and grinds verified questions one at a time. No LLM in the request
path.

## 0. What already exists, and what changes because of it

`booklet_gen/senior_syllabus.py` is already on the branch, with
`scripts/check_senior_syllabus.py` covering it. It is the scope tree. This plan
does not respecify it; section 3 records the interface as delivered.

Things this plan leans on that are already in the repo:

| Thing | Where | Used for |
| --- | --- | --- |
| `SympyValidator.validate` returning `ValidationResult(verified, notes, conclusive)` | `booklet_gen/agents/validator.py` | the admission gate for Methods items |
| `_PG_SCHEMA` / `_SQLITE_SCHEMA` twin declarations plus `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migration lists | `booklet_gen/webapp/db.py:69-390` | the shape every new table copies |
| `advisory_lock(key)` | `booklet_gen/dbpool.py:78` | serialising DDL across gunicorn workers |
| `_q()` and `_cursor()` | `booklet_gen/webapp/db.py:38-66` | the single connection path, reused not duplicated |
| `enforce_rate_limit`, CSRF `before_request`, `X-CSRF-Token` header | `booklet_gen/webapp/security.py` | the practice API is protected exactly like `/generate` |
| `login_required`, `g.user` | `booklet_gen/webapp/auth.py:28` | practice is signed-in only in v1 |
| `load_prompt` (appends the no-em-dash, people, and reasoning blocks) | `booklet_gen/agents/_shared.py:89` | the template prompt gets house style for free |
| `record_worker_heartbeat(worker_name=...)` | `booklet_gen/webapp/db.py:437` | the filler is visible in `/healthz` and admin without new code |
| `curriculum.py` copyright posture | `booklet_gen/curriculum.py:20-37` | already replicated in `senior_syllabus.py` |

## 1. Module and file map, partitioned by builder

Three builders, zero shared files. Every builder is an autonomous agent, so per
the guardrail in `CLAUDE.md` nothing is pushed to `main`; all work lands on
`claude/tutoring-booklet-generator-hkt33w` and ships as one PR.

### Builder A: the bank (data layer). Lands first.

| File | Responsibility |
| --- | --- |
| `booklet_gen/practice/__init__.py` | empty package marker |
| `booklet_gen/practice/models.py` | the shared vocabulary: `TemplateRow`, `ItemRow`, `DrawResult`, `SeenEvent` dataclasses that B and C both import |
| `booklet_gen/practice/store.py` | every SQL statement the engine runs: twin schemas, migration list, insert, draw, seen, demand, budget counters, `syllabus_fingerprint()` |
| `booklet_gen/practice/fixtures.py` | an in-memory fake bank seeder used by all the check scripts, so no check needs an LLM or a network |
| `booklet_gen/webapp/db.py` | **one surgical edit only**: two practice deletes inside `delete_account`. See 2.6 and the risk in section 10 |
| `scripts/check_practice_bank_schema.py` | see 7.1 |
| `scripts/check_practice_seen_and_spacing.py` | see 7.2 |

### Builder B: the factory (generation, verification, filler)

| File | Responsibility |
| --- | --- |
| `booklet_gen/practice/templates.py` | one LLM call to a parsed, structurally validated parameterised question family; the pydantic `PracticeTemplateDraft` lives here, not in `schemas.py` |
| `booklet_gen/practice/instances.py` | deterministic seeded expansion of a template into concrete questions plus their `check_json` |
| `booklet_gen/practice/verify.py` | the single admission gate. Dispatches on `verify_kind`, runs both gates, returns the existing `ValidationResult` |
| `booklet_gen/practice/chem.py` | formula parsing, molar mass, balancing by nullspace, stoichiometry, limiting reagent, concentration, pH, equilibrium, significant figures, and the text re-extraction half of each |
| `booklet_gen/practice/elements.py` | atomic masses as IUPAC facts, with a source note in the module docstring |
| `booklet_gen/practice/filler.py` | the background worker: what to generate next, budget, blocking, `main()` |
| `booklet_gen/prompts/practice_template_methods.txt` | system prompt for a Methods template |
| `booklet_gen/prompts/practice_template_chemistry.txt` | system prompt for a Chemistry template |
| `render.yaml`, `DEPLOY.md` | the filler cron service and how to watch it |
| `scripts/check_practice_instance_verification.py` | see 7.3 |
| `scripts/check_practice_chemistry.py` | see 7.4 |
| `scripts/check_practice_filler_budget.py` | see 7.5 |

### Builder C: the grind (API and UI)

| File | Responsibility |
| --- | --- |
| `booklet_gen/webapp/practice_views.py` | the blueprint: picker page, session, next, seen, reset |
| `booklet_gen/webapp/templates/practice.html` | the picker and the question card |
| `booklet_gen/webapp/static/js/practice.js` | prefetch buffer, arrow handling, batched seen flush |
| `booklet_gen/webapp/static/css/practice.css` | its own stylesheet, so `style.css` is never touched |
| `booklet_gen/webapp/__init__.py` | register the blueprint, call `init_practice_db()` next to `init_db()` |
| `scripts/check_practice_picker.py` | see 7.6 |
| `scripts/check_practice_api.py` | see 7.7 |

No CSP change is needed: `script-src 'self'` already permits
`/static/js/practice.js`, and `connect-src 'self'` already permits the fetches
(`booklet_gen/webapp/__init__.py:310-321`).

**`booklet_gen/senior_syllabus.py` is frozen.** No builder edits it. The parent
link defect found during planning is already fixed and covered by a new
assertion in `scripts/check_senior_syllabus.py`.

## 2. The bank schema

Lives entirely in `booklet_gen/practice/store.py`, declared twice exactly as
`db.py` does it, with a migration list for Postgres and `_sqlite_add_columns`
for SQLite. Reuses the one connection path:

```python
from ..webapp.db import _cursor, _q, _sqlite_add_columns
from ..dbpool import advisory_lock, is_postgres

_PRACTICE_SCHEMA_LOCK_KEY = 72_461_002   # NOT db._SCHEMA_LOCK_KEY (72_461_001)
```

Importing three private helpers is deliberate and must carry a comment saying
so: two connection paths would mean two answers to "is this in a transaction",
and `_cursor` already carries the WAL pragma and the Postgres `dict_row`
factory.

### 2.1 practice_templates

```sql
CREATE TABLE IF NOT EXISTS practice_templates (
    id                  TEXT PRIMARY KEY,
    subject             TEXT NOT NULL,              -- 'methods' | 'chemistry'
    subtopic_id         TEXT NOT NULL,              -- senior_syllabus leaf id
    verify_kind         TEXT NOT NULL,              -- verify.KINDS
    calculator          TEXT NOT NULL,              -- 'free' | 'assumed' | 'either'
    difficulty          TEXT NOT NULL,              -- 'easy' | 'medium' | 'hard'
    marks               INTEGER,
    question_pattern    TEXT NOT NULL,
    answer_pattern      TEXT NOT NULL,
    working_pattern     TEXT NOT NULL,
    params_json         TEXT NOT NULL,
    constraints_json    TEXT NOT NULL,
    check_pattern_json  TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'live',  -- live|retired|rejected
    reject_reason       TEXT,
    instances_made      INTEGER NOT NULL DEFAULT 0,
    instances_verified  INTEGER NOT NULL DEFAULT 0,
    model               TEXT,
    prompt_version      TEXT NOT NULL,
    syllabus_version    TEXT NOT NULL,
    created_at          BIGINT NOT NULL,
    retired_at          BIGINT
);
CREATE INDEX IF NOT EXISTS practice_templates_node_idx
    ON practice_templates (subtopic_id, status);
```

SQLite is character for character the same with `BIGINT` to `INTEGER`.

A rejected template is **kept**, with its reason. Deleting it loses the only
record of what the model gets wrong on that subtopic, which is what section 6's
blocking rule is measured on.

### 2.2 practice_items

```sql
CREATE TABLE IF NOT EXISTS practice_items (
    id               BIGSERIAL PRIMARY KEY,
    template_id      TEXT NOT NULL REFERENCES practice_templates(id) ON DELETE CASCADE,
    subject          TEXT NOT NULL,
    subtopic_id      TEXT NOT NULL,
    calculator       TEXT NOT NULL,
    difficulty       TEXT NOT NULL,
    marks            INTEGER,
    question         TEXT NOT NULL,
    answer           TEXT NOT NULL,
    working          TEXT NOT NULL,
    params_json      TEXT NOT NULL,
    check_json       TEXT NOT NULL,
    variant_key      TEXT NOT NULL,
    shuffle_key      DOUBLE PRECISION NOT NULL,
    verified_by      TEXT NOT NULL,
    verifier_notes   TEXT,
    syllabus_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'live',
    created_at       BIGINT NOT NULL,
    UNIQUE (template_id, variant_key)
);
CREATE INDEX IF NOT EXISTS practice_items_draw_idx
    ON practice_items (subtopic_id, status, shuffle_key);
CREATE INDEX IF NOT EXISTS practice_items_template_idx
    ON practice_items (template_id);
```

SQLite: `id INTEGER PRIMARY KEY AUTOINCREMENT`, `shuffle_key REAL NOT NULL`,
`FOREIGN KEY(template_id) REFERENCES practice_templates(id)`, same `UNIQUE` and
same two indexes.

- `UNIQUE (template_id, variant_key)` makes an exact duplicate question
  **impossible at the database level**, not merely unlikely in code. This is
  the first of the three anti-repetition layers.
- `shuffle_key` is a random double assigned at insert, never derived from the
  parameters. That is what stops the parameter sweep order (a=1, a=2, a=3)
  becoming the serving order. `practice_items_draw_idx` makes the draw a single
  index range scan.
- `verified_by` records which checker settled it, for example
  `sympy:derivative` or `chem:limiting_reagent`. It is the column the coverage
  check is measured on.

### 2.3 practice_sessions

```sql
CREATE TABLE IF NOT EXISTS practice_sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject      TEXT NOT NULL,
    scope_id     TEXT NOT NULL,      -- as chosen, may be any level
    scope_label  TEXT NOT NULL,
    calculator   TEXT,
    served       INTEGER NOT NULL DEFAULT 0,
    answered     INTEGER NOT NULL DEFAULT 0,
    correct      INTEGER NOT NULL DEFAULT 0,
    created_at   BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS practice_sessions_user_idx
    ON practice_sessions (user_id, last_seen_at DESC);
```

`scope_id` stores what the student picked, not what it resolved to. Storing the
resolved leaf list would freeze the session against a syllabus that later gains
a subtopic.

### 2.4 practice_seen: what this student has already been shown

```sql
CREATE TABLE IF NOT EXISTS practice_seen (
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id        BIGINT NOT NULL REFERENCES practice_items(id) ON DELETE CASCADE,
    subtopic_id    TEXT NOT NULL,
    template_id    TEXT NOT NULL,
    outcome        TEXT,           -- got_it | missed | skipped | NULL (served only)
    times_seen     INTEGER NOT NULL DEFAULT 1,
    first_seen_at  BIGINT NOT NULL,
    last_seen_at   BIGINT NOT NULL,
    PRIMARY KEY (user_id, item_id)
);
CREATE INDEX IF NOT EXISTS practice_seen_user_node_idx
    ON practice_seen (user_id, subtopic_id);
CREATE INDEX IF NOT EXISTS practice_seen_recent_idx
    ON practice_seen (user_id, last_seen_at DESC);
```

Three decisions worth defending:

- **Per user, not per session.** A student who closes the tab and comes back
  tomorrow must not be handed the same twenty questions. Going round again is
  an explicit button (`POST /practice/reset`), never an accident.
- `subtopic_id` and `template_id` are denormalised onto this row so "reset just
  Antidifferentiation" and the template spacing rule are single-table reads.
  Without them the spacing rule joins `practice_seen` to `practice_items` on
  every arrow press.
- The composite PK `(user_id, item_id)` is exactly what the draw's `NOT EXISTS`
  anti-join needs, and it makes the seen POST idempotent by construction: a
  duplicate event is an `ON CONFLICT DO UPDATE`, not a second row.

### 2.5 Demand, budget and node state

```sql
CREATE TABLE IF NOT EXISTS practice_scope_demand (
    subtopic_id       TEXT PRIMARY KEY,
    requests          INTEGER NOT NULL DEFAULT 0,
    dry_requests      INTEGER NOT NULL DEFAULT 0,
    last_requested_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS practice_generation_log (
    day                 TEXT NOT NULL,      -- 'YYYY-MM-DD' UTC
    subtopic_id         TEXT NOT NULL,
    calls               INTEGER NOT NULL DEFAULT 0,
    templates_kept      INTEGER NOT NULL DEFAULT 0,
    templates_rejected  INTEGER NOT NULL DEFAULT 0,
    items_kept          INTEGER NOT NULL DEFAULT 0,
    items_discarded     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, subtopic_id)
);
CREATE TABLE IF NOT EXISTS practice_node_state (
    subtopic_id           TEXT PRIMARY KEY,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    blocked_reason        TEXT,
    blocked_at            BIGINT,
    last_filled_at        BIGINT
);
```

The budget must live in the database, not in a process variable, or a cron job
that runs twice or a worker that restarts spends the cap twice. `calls_today()`
is `SELECT COALESCE(SUM(calls),0) FROM practice_generation_log WHERE day=?`.

### 2.6 The one edit to db.py

Inside `db.delete_account`, before `DELETE FROM users`, matching the existing
deferred-import style already used there for `storage`:

```python
from ..practice import store as practice_store
practice_store.delete_user_practice_data(cur, user_id)
```

`delete_user_practice_data(cur, user_id)` issues the two deletes and **swallows
only a missing-table error** (`sqlite3.OperationalError` containing "no such
table", `psycopg.errors.UndefinedTable`). Without that guard, a process that
never ran `init_practice_db()` would abort the whole account-deletion
transaction, and a customer who cannot delete their account is a worse defect
than the one being fixed.

Nothing else in `db.py` changes. `store.py` never names `credit_ledger`,
`payments` or `jobs`; check 7.1 asserts that by inspecting the module source.

## 3. The scope tree

### 3.1 The interface as delivered (do not respecify, do not edit)

```python
@dataclass(frozen=True)
class Subtopic:
    id: str; name: str; unit: str; strand: str
    verification: str          # 'symbolic' | 'numeric' | 'judge'
    summary: str; calculator: str = "either"
    @property year -> str            # 'Year 11' | 'Year 12'
    @property subject_key -> str     # 'methods' | 'chemistry'

@dataclass(frozen=True)
class Scope:
    id: str; label: str; level: str; subject: str; count: int; parent: str | None

ATTRIBUTION: str
VERIFICATION = ("symbolic", "numeric", "judge")
UNITS_BY_YEAR = {"Year 11": ("Unit 1","Unit 2"), "Year 12": ("Unit 3","Unit 4")}
SUBJECTS: dict[str, list[Subtopic]]        # 42 Methods, 37 Chemistry
SUBJECT_KEYS = {"Mathematics Methods": "methods", "Chemistry": "chemistry"}

subject_for_key(key) -> str
subtopics(subject) -> list[Subtopic]
subtopic(subtopic_id) -> Subtopic | None
strands(subject, year=None) -> list[str]
bankable(sub) -> bool                      # verification in ('symbolic','numeric')
scope_options(subject, year=None) -> list[Scope]
resolve_scope(scope_id, bankable_only=True) -> list[str]
scope_label(scope_id) -> str
guidance_block(sub) -> str
```

Scope id grammar, and therefore how the three levels the product owner named
resolve:

| Student picks | Scope id | `resolve_scope` returns |
| --- | --- | --- |
| Whole year | `methods:year:Year 12` | every bankable subtopic in Units 3 and 4 |
| Calculus | `methods:strand:Calculus` | every bankable Calculus subtopic, deliberately across Units 2, 3 and 4 |
| Antidifferentiation | `methods.calculus.antidifferentiation` | exactly that one id |

**The query is therefore: `resolve_scope(scope_id)` in Python, then
`WHERE subtopic_id IN (?,?,...)` with the ids expanded as bind parameters.**
There is no recursive SQL, no array column, no `ANY()`, and no join table. The
widest scope is 42 ids, well inside any parameter limit, and the same statement
runs unmodified on both backends. `store.draw()` asserts
`len(leaf_ids) <= 200` and raises rather than truncating, so a future subject
that outgrows this fails loudly instead of silently serving a subset.

`scope_options` returns rows whose `parent` is always present in the same
response, at every year filter, and `scripts/check_senior_syllabus.py` asserts
that plus the absence of cycles. The picker can therefore build its tree
straight from `Scope.parent`.

`store.syllabus_fingerprint()` computes `sha256` of the sorted
`(id, verification, calculator)` triples across `SUBJECTS`, hex truncated to 12,
and that is what is stamped as `syllabus_version`. A hand-maintained version
constant is a version somebody forgets to bump.

### 3.2 Coverage: what is actually stocked

`bankable()` already enforces the "Chemistry deterministic half only" rule.
Measured: 42 of 42 Methods subtopics and 25 of 37 Chemistry subtopics are
bankable. The 12 judge-only Chemistry subtopics stay visible in the picker (the
course really contains them) and are never stocked.

There is a second, tighter gate on top, and section 4.5 explains why it has to
exist.

## 4. Templates, instances, and verification

### 4.1 What a template is

One LLM call returns one parameterised family:

```json
{
  "verify_kind": "derivative",
  "calculator": "free",
  "difficulty": "medium",
  "marks": 3,
  "question_pattern": "Differentiate y = {a}x^{n} + {b}x with respect to x.",
  "answer_pattern": "dy/dx = {a*n}x^{n-1} + {b}",
  "working_pattern": "Bring the index down and reduce it by one. ...",
  "params": {
    "a": {"type": "int", "range": [2, 9], "exclude": [0]},
    "n": {"type": "int", "range": [2, 6]},
    "b": {"type": "int", "range": [-9, 9], "exclude": [0]}
  },
  "constraints": ["a != b", "gcd(a, b) == 1"],
  "check_pattern": {"function": "{a}*x**{n} + {b}*x", "variable": "x"}
}
```

Four rules the prompt enforces and `templates.py` re-checks structurally before
the template is ever expanded:

- Every placeholder in `question_pattern`, `answer_pattern` and `check_pattern`
  is declared in `params`, and every declared param is used. An undeclared
  placeholder is a template that renders `{a}` to the student.
- The parameter space, after constraints, has at least `MIN_SPACE = 40`
  members. A template with six possible instances goes stale in one session.
- `verify_kind` is in `verify.KINDS` **and** in the allowlist for that subtopic
  (section 4.5).
- `question_pattern` renders into a shape the declared verifier can read. For
  Methods that means the printed text must state the function as `y = ...`,
  `f(x) = ...` or state the integrand after an integral cue, because
  `agents/validator.py` is keyword-gated on `_DERIV_KW`, `_INTEGRAL_KW`,
  `_FUNC_DEF` and `_INTEGRAND`. Checked by rendering one probe instance at
  ingest, before any of the others are made.

The `answer_pattern` is **never trusted**. It exists only as a cross-check: if
it disagrees with what the verifier independently computes, the whole template
is rejected, because a family whose author cannot state its own answer is a
family whose questions are wrong in ways the verifier may not always catch.

### 4.2 How instances are seeded and generated

`instances.py::expand(template, count, seed) -> list[Instance]`:

1. `rng = random.Random(f"{template.id}:{seed}")`. Same template plus same seed
   yields byte-identical instances, so any item in the bank can be regenerated
   offline and re-verified. That is what makes check 7.3's whole-bank
   re-derivation possible.
2. Enumerate the parameter space with `itertools.product` over the declared
   ranges, filter by the constraints (evaluated through `sympy.sympify` with a
   symbol whitelist, never `eval`), and cap the enumeration at 200,000 tuples;
   above that, sample without replacement using a seen-set.
3. `rng.shuffle` the surviving tuples and take the first `count`. Sweeping and
   then shuffling, rather than sampling with replacement, is what guarantees
   `count` **distinct** instances rather than count draws that mostly differ.
4. Render `question`, `answer`, `working` and `check_json` from the same
   parameter dict, in one pass. One dict, one render, so the printed question
   and the checked payload cannot come from different numbers.
5. `variant_key = sha256(canonical_json(params)).hexdigest()[:32]`.
   `shuffle_key = rng.random()`.

Default `count = 60`, cap 200 per template.

### 4.3 The admission gate: two checks, both required

`verify.py::admit(instance) -> ValidationResult`. An instance enters the bank
only if **both** pass.

**Gate 1, independent recomputation.** The verifier recomputes the answer from
`check_json` without ever reading `answer_pattern`. It proves the answer is
arithmetically right.

**Gate 2, text round trip.** The verifier re-extracts the problem from the
**rendered question string** and asserts it equals `check_json`. It proves the
printed question states the problem that was solved.

Gate 2 is the one that earns its keep. A parameterised renderer that prints
"35.0 g" while its parameters say 3.50 gets that wrong once and then ships it
800 times. For Methods, gate 2 is nearly free: `SympyValidator.validate` already
reads the printed text and nothing else, so calling it **is** the round trip.
For Chemistry, `chem.py` provides an `extract_<kind>(text)` beside every
`solve_<kind>()`.

Admission requires `result.verified and result.conclusive`. Not `verified`
alone. `ValidationResult.conclusive` exists precisely because a partial match is
not a pass (`agents/validator.py:33-40`), and an inconclusive verdict on senior
material means the item does not ship.

### 4.4 Verify kinds

Methods, delegating straight to the existing `SympyValidator` with no change to
`agents/validator.py`: `derivative`, `derivative_at`, `integral_indefinite`,
`integral_definite`, `solve_equation`, `direct_computation`.

Methods, new deterministic routines in `verify.py` (all SymPy, no new
dependency): `expression_equivalence` (`simplify(printed - answer) == 0`,
covering expand, factorise, index laws, log laws), `roots` (solve set
comparison), `function_value`, `binomial` and `normal` via `sympy.stats` with an
explicit rounding tolerance.

Chemistry, all in `chem.py`, dispatched by `verify.py`: `molar_mass`,
`percent_composition`, `empirical_formula`, `balance_equation`, `moles_mass`,
`limiting_reagent`, `concentration_dilution`, `titration`, `ph_strong`,
`ph_weak`, `equilibrium_kc`, `gas_laws`, `sig_figs`.

`balance_equation` is the nicest of these: build the element by species matrix,
take `Matrix.nullspace()` over the rationals, clear denominators with the lcm,
reduce by the gcd, and require every coefficient to be a positive integer.
Refuse anything else. That single routine both generates and verifies, and it
refutes an unbalanceable equation rather than producing a plausible wrong
vector.

Explanation and mechanism questions have no kind and never will in v1. That is
enforced twice: `bankable()` excludes judge-only subtopics, and `verified_by` on
every stored row is asserted against the allowlist in check 7.4, measured off
the bank rather than off the prompt.

### 4.5 The coverage gap, stated honestly

`senior_syllabus.py` marks all 42 Methods subtopics `symbolic` or `numeric`, but
`agents/validator.py` cannot settle "state the range of f", "describe the
transformation", or "find the inverse function". **`verification` in the
syllabus is a claim about the topic; `verify.KINDS_FOR_SUBTOPIC` is the claim
about the code that exists.** The second one is authoritative.

`verify.py` owns `KINDS_FOR_SUBTOPIC: dict[str, tuple[str, ...]]`, keyed by
subtopic id. A subtopic with an empty tuple is not fillable no matter what
`bankable()` says: the filler skips it and records
`blocked_reason='no checker'`, and the API reports it as not yet stocked rather
than serving a blank screen. Check 7.3 prints the coverage table and asserts a
floor, so the gap is a number in the output of every run rather than a surprise
in production.

### 4.6 Never two near-identical questions back to back

Three layers, each catching what the one before it cannot:

1. **Bank level.** `UNIQUE (template_id, variant_key)` makes an exact duplicate
   unstorable.
2. **Draw level.** `store.draw()` over-fetches `limit * 4` candidates ordered by
   `shuffle_key`, then filters in Python: at most one item per `template_id` per
   returned batch, and no template the student saw in their last
   `SPACING_WINDOW = 5` items. The filtering is in Python, not SQL, because
   `DISTINCT ON` is Postgres only and this app also runs on SQLite. When the
   scope holds fewer than four live templates the rule cannot be satisfied, so
   it degrades to "never the same item twice" and the payload says
   `spacing: "relaxed"` rather than pretending.
3. **Ordering level.** `shuffle_key` is random at insert and independent of the
   parameters, so the sweep order (a=2, then a=3, then a=4) is never the serving
   order. This is the layer that stops "differentiate 2x^3" being followed by
   "differentiate 3x^3", which layers 1 and 2 both consider perfectly distinct.

## 5. The API surface

All routes are `login_required`, CSRF-protected through the existing
`before_request` (`security.py:77`), and rate limited with
`enforce_rate_limit`.

| Route | Purpose |
| --- | --- |
| `GET /practice` | picker page. Embeds `scope_options()` as JSON in the template, so choosing a scope costs no roundtrip |
| `GET /practice/scopes?subject=...&year=...` | the same tree as JSON. Exists so check 7.6 can measure the picker off real output |
| `POST /practice/session` | `{subject, scope_id, calculator?}` returns `{session_id, scope_label, breadcrumb, stocked, depth}`. Rate limit `("practice-session", 60, 3600)` |
| `GET /practice/next?session=...&n=10&exclude=1,2,3` | the arrow's supply. Rate limit `("practice-next", 600, 3600)`, which is 6000 questions an hour and cannot be reached by a human |
| `POST /practice/seen` | `{session_id, events:[{item_id, outcome, at}]}`, batched, idempotent |
| `POST /practice/reset` | `{session_id}` clears seen rows for the scope's subtopics for this user |

`/practice/next` payload:

```json
{ "items": [ {"id": 88231, "question": "...", "answer": "...", "working": "...",
              "marks": 3, "difficulty": "medium", "subtopic": "Antidifferentiation",
              "calculator": "free", "repeat": false} ],
  "remaining_unseen": 212, "dry": false, "spacing": "strict" }
```

**Prefetch.** `practice.js` holds a buffer of 10, refetches when 4 remain, and
passes the buffered ids as `exclude` so the server never re-serves what the
browser is already holding. The arrow shifts the buffer and does no network work
at all. Latency budget for a refetch is under 150 ms, which check 7.7 measures
against a 5000-item seeded bank.

**Seen state.** Answering marks the item seen locally; the buffer flushes every
5 events, on scope change, and on `visibilitychange` via
`fetch(..., {keepalive: true})`. Not `navigator.sendBeacon`, which cannot set the
`X-CSRF-Token` header the app requires. Server side it is one
`ON CONFLICT (user_id, item_id) DO UPDATE SET times_seen = times_seen + 1, ...`,
so a replayed batch after a flaky connection changes nothing.

**When the bank runs dry for a narrow scope.** Fallback order, and the order is
the point:

1. Unseen items in the exact scope.
2. If none, seen items from the exact scope, oldest `last_seen_at` first, each
   flagged `repeat: true`, with `dry: true` on the response so the UI can say
   "you have worked through all 34 questions in Antidifferentiation, going round
   again".
3. **Never widen the scope.** Widening is the tempting fix and it is the wrong
   one: a student who chose Antidifferentiation and is quietly fed confidence
   intervals has been lied to by the one feature whose entire promise is the
   scope. `resolve_scope` already fails in this direction for unknown scopes;
   the API must match it.

Every dry request calls `store.note_scope_demand(subtopic_id, dry=True)`, which
is how the filler learns what students actually grind.

A fourth case is distinct from all three: a scope that resolves to nothing
because every subtopic in it is judge-only. Detect it with
`resolve_scope(scope_id, bankable_only=False)` returning a non-empty list while
the bankable call returns empty, and say so plainly, rather than showing the
same "bank is dry" message.

## 6. The filler worker

`python -m booklet_gen.practice.filler --once`, a standalone process. It does
**not** go into `booklet_gen/worker.py`. That worker generates booklets a
customer has paid for and is waiting on, and a filler sharing its loop is a
filler that can delay paid work. Deployed as a Render cron service in
`render.yaml`, nightly:

```yaml
  - type: cron
    name: folio-practice-filler
    runtime: docker
    schedule: "15 16 * * *"        # 00:15 Perth
    dockerfilePath: ./Dockerfile
    dockerCommand: python -m booklet_gen.practice.filler --once
    envVars:
      - fromGroup: folio-secrets
      - key: FOLIO_REQUIRE_POSTGRES
        value: "1"
      - key: FOLIO_PRACTICE_TEMPLATE_BUDGET_PER_DAY
        value: "40"
```

It calls `db.record_worker_heartbeat(worker_name="practice-filler")`, reusing
the existing writer, so it shows up in the admin console with no new code.

**What to generate next**, evaluated in this order, cheapest first:

1. `store.bank_depth()` for every bankable subtopic that has a checker.
2. Any subtopic below `MIN_DEPTH` (default 150) whose **existing live templates
   can still yield unused variants**: expand more instances. This costs **zero
   LLM calls**, and it is checked first for exactly that reason. Most nights the
   filler should stop here.
3. Otherwise, subtopics below `MIN_DEPTH`, ordered by
   `dry_requests DESC, requests DESC, depth ASC`. Demand first: a scope real
   students hit dry is worth more than an even bank.
4. Then subtopics below `TARGET_DEPTH` (default 400), same ordering.

**Cost control**, four independent brakes:

- **Templates, not questions.** One call yields about 60 verified instances. 67
  bankable subtopics at 8 templates each is about 536 calls to fill the bank
  once, producing roughly 32,000 individually verified questions. That ratio is
  the whole reason this architecture was chosen.
- **A database-backed daily cap.** `FOLIO_PRACTICE_TEMPLATE_BUDGET_PER_DAY`,
  default 40, read via `calls_today()`. In the database and not in memory, so a
  cron that fires twice or a container that restarts cannot spend it twice.
- **Free work first.** Rule 2 above. If instance top-up can close the deficit,
  no call is made at all.
- **Blocking.** Three consecutive templates rejected for one subtopic sets
  `blocked_reason` and `blocked_at`, and the filler skips it. A subtopic the
  model cannot do stops burning budget every night for ever, and the block is a
  row a human can read and clear.

Per-template accounting goes to `practice_generation_log`, so "what did last
night cost and what did it buy" is one query.

## 7. Verification plan

Seven scripts, house style: a docstring saying **why**, assertion messages that
state the **consequence**, and everything measured off real output rather than
source strings. Each one names the build it must fail against. Where a check
must run against a build that lacks the feature, it uses the shim pattern from
`scripts/check_job_heartbeat.py:66-98` so that build reports every behaviour it
gets wrong instead of stopping on an `AttributeError` at line one.

**7.1 `scripts/check_practice_bank_schema.py`** (Builder A)
Runs `init_practice_db()` on a fresh SQLite file, then on a legacy database that
already has `users` and `jobs`, then twice more (a redeploy). Asserts every
column and every named index exists; asserts the
`UNIQUE (template_id, variant_key)` constraint actually rejects a duplicate
insert rather than merely being declared; asserts Postgres gets the same columns
from the migration list by source inspection; asserts `store.py`'s source never
names `credit_ledger`, `payments` or `jobs`; asserts `delete_account` removes
practice rows **and still succeeds** on a database where the practice tables do
not exist.
*Catches:* schema drift between the two backends, the
`CREATE TABLE IF NOT EXISTS` that does nothing on a database that already
exists, and account deletion breaking or leaving a student's history behind.

**7.2 `scripts/check_practice_seen_and_spacing.py`** (Builder A)
Seeds a known bank (6 templates, 20 instances each) via `fixtures.py`, draws 60
items across six calls, and measures the served sequence. Asserts: no item
repeats while unseen stock remains; no two adjacent items share a `template_id`
while four or more templates have unseen stock; no two adjacent items have
identical parameter tuples; after exhaustion the response is `dry` with `repeat`
items oldest-first and **every returned `subtopic_id` is still inside the chosen
scope**; seen state survives a brand new session for the same user; a second
user's draw is unaffected by the first user's history.
*Catches:* the repetition the whole feature exists to avoid, and silent scope
widening when a narrow scope runs out.
*Fails against:* the obvious naive build, `ORDER BY random() LIMIT n`.

**7.3 `scripts/check_practice_instance_verification.py`** (Builder B)
Feeds `verify.admit` a template whose `answer_pattern` is deliberately wrong by
one term, and asserts every instance is discarded, the template is marked
`rejected` with a reason, and **not one row reached `practice_items`**. Feeds it
a template whose question text `SympyValidator` cannot read whole, and asserts
rejection rather than silent admission. Feeds it a template that produces an
inconclusive verdict, and asserts it is not admitted (`verified` alone is not
enough). Then takes a good template, asserts a high verification rate, and
re-derives **every live item in the seeded bank** from its stored
`template_id`, `params_json` and seed, confirming the stored question and answer
still match. Prints the `KINDS_FOR_SUBTOPIC` coverage table and asserts a floor.
*Catches:* the model's claimed answer being trusted, unverifiable questions
leaking into the bank, and bank rot after a template edit.

**7.4 `scripts/check_practice_chemistry.py`** (Builder B)
Hand-computed cases: `Ca(NO3)2` and `CuSO4.5H2O` molar masses; balancing
`C3H8 + O2` and `KMnO4 + HCl`, which need non-trivial coefficients; a limiting
reagent problem worked by hand; dilution; strong and weak acid pH; significant
figures. The negative half matters as much: an unbalanceable equation must
return "cannot balance" rather than a plausible wrong vector, and a sig-fig
question must never be generated in the ambiguous trailing-zero form. Finally,
asserts that **no item in the bank carries a `verified_by` outside the
deterministic allowlist**.
*Catches:* a chemistry answer only an LLM judge could defend reaching a Year 12.

**7.5 `scripts/check_practice_filler_budget.py`** (Builder B)
A fake LLM client that counts calls. Asserts: filling an already-deep bank makes
**zero** calls; a deficit satisfiable from existing templates makes **zero**
calls; a shallow subtopic makes calls but stops at the cap, and the cap holds
**across two separate process runs** because it is read from the database; a
subtopic that fails three times is blocked and consumes no further budget; the
filler prefers subtopics with recorded dry demand over subtopics that are merely
shallow.
*Catches:* an unbounded overnight bill, and one broken subtopic eating the whole
budget every night for ever.

**7.6 `scripts/check_practice_picker.py`** (Builder C)
Measured off the rendered `/practice` page and `/practice/scopes` JSON, not off
`senior_syllabus.py`. Asserts the three levels the product owner named are all
selectable and visibly nested in the rendered HTML; asserts no rendered label
contains a `:` or a raw id; asserts judge-only subtopics appear but are marked as
not stocked; asserts the page carries `senior_syllabus.ATTRIBUTION`.
*Catches:* the hierarchical picker collapsing to a flat list in the rendered
page even though the data underneath it nests correctly.

**7.7 `scripts/check_practice_api.py`** (Builder C)
The whole loop through the Flask test client. Asserts: signed out gets a
redirect and not questions; POST without a CSRF token is refused; another user's
`session_id` is a 404 and not their questions; a prefetch of 10 returns 10
distinct items and honours `exclude`; a replayed seen batch does not
double-count; **`/practice/next` makes no LLM call**, proved by monkeypatching
`booklet_gen.llm.get_client` to raise and confirming the endpoint still serves;
and a refetch over a 5000-item seeded bank completes inside the latency budget.
*Catches:* an LLM creeping into the request path, which is the one architectural
decision that cannot be allowed to erode, and cross-account data leakage.

`scripts/check_senior_syllabus.py` already exists and stays as the eighth. It
covers scope resolution, nesting, parent integrity, unknown scopes serving
nothing, and the judge-only exclusion, so none of the seven above duplicate it.

## 8. Things only the human can do

1. **Verify the syllabus tree against the real SCSA Methods and Chemistry
   documents**, unit and strand placement, and confirm no SCSA prose was pasted
   into `senior_syllabus.py`. The copyright position is the founder's to hold.
2. **Decide the commercial model** before launch: free, credit-priced, or
   subscription. The code deliberately spends no credits in v1.
3. **Provision the filler**: sync the Render blueprint to create
   `folio-practice-filler`, and set `FOLIO_PRACTICE_TEMPLATE_BUDGET_PER_DAY`.
4. **Run the one-off seeding pass** with the cap raised (roughly 536 calls to
   fill the bank once), watch the Gemini billing console during it, and confirm
   the actual first-night spend against the estimate.
5. **Read 30 questions drawn from the live bank**, one per strand, as a teacher.
   Symbolic verification proves the answer matches the question. It cannot tell
   you the question is a sensible thing to ask a Year 12 three weeks before an
   exam.
6. **Check the Privacy page** still describes what is now stored about a
   student: a per-user history of every question they have been shown and
   whether they got it right.

## 9. Ordering

```
Now      senior_syllabus.py is frozen. No builder edits it.
Step 1   Builder A publishes booklet_gen/practice/models.py and fixtures.py
         FIRST, in its own commit. B and C code against those signatures
         immediately; nothing else in A blocks them.
Step 2   A: store.py + the db.py delete_account edit + checks 7.1, 7.2.
         B: chem.py, elements.py, verify.py, instances.py, templates.py,
            prompts, filler.py, render.yaml + checks 7.3, 7.4, 7.5.
         C: practice_views.py, practice.html, practice.js, practice.css,
            the __init__.py registration + checks 7.6, 7.7.
Step 3   Critic reviews the whole tree. Consumer reviews the feature.
Step 4   Run all eight checks against the merged tree.
Step 5   Human: seeding run, then the 30-question read-through (section 8).
Step 6   Ship Methods. Chemistry follows only once its checker coverage
         clears the floor in check 7.3.
```

Within Builder B there is one internal ordering that matters: `chem.py` and
`verify.py` before `templates.py` and `filler.py`. The verifier defines what a
template is allowed to be, so writing the prompt first means writing a prompt
for a contract that does not exist yet.

## 10. Risks, stated honestly

**The `db.py` edit sits beside the money.** `db.py` holds `credit_ledger`,
`payments` and the whole refund path, and it is explicitly flagged as needing
more care than the rest of the repo. Mitigations: exactly one function is
touched, `delete_account`, and exactly two statements are added, inside its
existing transaction; `store.py` never names a money table and check 7.1 asserts
that by source inspection; the missing-table guard means the practice deletes
can never abort an account deletion. Only Builder A may touch `db.py`, and the
PR diff on that file should be reviewable in ten seconds. If it is not, it is
wrong.

**A latent trap this feature walks straight into.** `delete_account` deletes
table by table and does **not** delete `study_plans`. On Postgres the
`ON DELETE CASCADE` covers it; on SQLite, `PRAGMA foreign_keys` is never enabled
in `_cursor` (only `journal_mode=WAL`), so orphan rows carrying `student_name`
survive. Production is Postgres so the live impact today is nil, but the new
`practice_seen` and `practice_sessions` tables have exactly the same shape,
which is why they are deleted **explicitly** rather than left to a cascade that
only fires on one of the two backends.

**The syllabus claims more verifiability than the code has.** All 42 Methods
subtopics are marked `symbolic`, but `agents/validator.py` cannot settle a
range, a transformation or an inverse function. Section 4.5 resolves it by
making `verify.KINDS_FOR_SUBTOPIC` authoritative and printing the coverage table
in every run of check 7.3. The honest expectation is that v1 Methods stocks
fewer than 42 subtopics, and the picker must say so rather than serving a blank
card.

**Verification can silently stop firing.** `agents/validator.py` is
keyword-gated. A prompt change that stops producing "Differentiate y = ..."
makes every instance fail verification. That fails in the safe direction, an
empty bank, and it is loud. The dangerous direction is somebody "fixing" the
resulting 100 percent discard rate by relaxing the gate to accept
`conclusive=False`. Check 7.3 asserts that specific relaxation fails.

**Connection pool pressure.** Supabase's session pooler with
`FOLIO_DB_POOL_MAX=5` against gunicorn's 2 workers by 4 threads is already
tight, and this adds sustained read load to the pool that also serves checkout.
Mitigations: one request per ten questions rather than per question, a
two-statement indexed draw, and a batched seen flush. Watch pool saturation
after launch before adding a second pool.

**Cost of the first fill.** About 536 LLM calls to stock the bank once. That is
a real number that appears on a real bill, which is why the seeding run is a
human step under supervision rather than something a cron job discovers
overnight.

**SCSA copyright.** The template prompts must forbid reproducing a past WACE
paper question. Generated-fresh and parameterised is the defence, and the
existing posture in `senior_syllabus.py` and `curriculum.py` is the precedent to
follow.
