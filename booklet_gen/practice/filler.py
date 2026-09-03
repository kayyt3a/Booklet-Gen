"""The background worker that stocks the practice bank.

    python -m booklet_gen.practice.filler --once

A standalone process, deliberately NOT part of `booklet_gen/worker.py`. That
worker generates booklets a customer has paid for and is waiting on. A filler
sharing its loop is a filler that can delay paid work, and no amount of care
inside the loop makes that untrue.

FOUR INDEPENDENT BRAKES ON WHAT THIS COSTS
------------------------------------------
1. TEMPLATES, NOT QUESTIONS. One call buys a family, and a family expands into
   about sixty individually verified questions. That ratio is the only reason a
   bank deep enough to grind against is affordable at all.

2. FREE WORK FIRST, EVERY RUN. Before anything else, every subtopic below the
   depth floor is topped up from the families already in the bank. Expanding
   more instances from an existing template costs ZERO LLM calls, and most
   nights the run should stop there. This is checked first for exactly that
   reason, not as an optimisation at the end.

3. A DAILY CAP READ FROM THE DATABASE. `store.calls_today()` is a SELECT, not a
   counter in this process. A cron that fires twice, a container that restarts
   mid run, or two fillers racing each other cannot spend the cap twice,
   because none of them is the thing holding the number.

4. BLOCKING. Three consecutive rejected templates for one subtopic sets a
   blocking reason and the filler skips it from then on. Without it, one
   subtopic the model cannot do burns budget every night for ever. The block is
   a row a human can read and clear, because the cause is usually a prompt or a
   checker that somebody will fix.

WHAT IS SKIPPED, AND WHY THAT IS A NORMAL STATE
-----------------------------------------------
Two thirds of the syllabus cannot be banked, and the filler treats that as
ordinary rather than as an error to work around:

  not bankable    the syllabus marks the subtopic judge-only. It stays in the
                  picker, because the course really contains it, and it is
                  never stocked.
  no checker      `verify.KINDS_FOR_SUBTOPIC` has an empty tuple. The topic may
                  well be checkable in principle; no routine for it exists.
  no arithmetic   a checker exists but the answer cannot be WRITTEN as a
                  pattern, because the instance renderer has no logarithm and
                  no error function. See `templates.KINDS_NEEDING_ABSENT_MATH`.

Each is recorded against the node as a blocking reason, so "why is
Antidifferentiation stocked and pH not" is one query rather than an
investigation.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from .. import senior_syllabus as syllabus
from ..llm import LLMClient
from ..webapp import db as accounts_db
from . import instances, store, templates, verify
from .models import TemplateRow

log = logging.getLogger(__name__)

# How deep a subtopic has to be before the filler leaves it alone. A student
# grinding one narrow scope for an hour sees maybe eighty questions, so a floor
# below that is a floor that lets somebody hit the bottom.
DEFAULT_MIN_DEPTH = 150
# What a well stocked subtopic looks like. Work above the floor and below this
# happens only once every deficit below the floor is closed.
DEFAULT_TARGET_DEPTH = 400

DEFAULT_BUDGET = 40
DEFAULT_INSTANCES = instances.DEFAULT_COUNT

# Consecutive rejected templates before a subtopic stops costing anything.
FAILURES_TO_BLOCK = 3

# A ceiling on how many times the run cycles the queue, so a bug that makes
# every attempt look productive cannot turn into an unbounded loop. The daily
# cap already bounds the spend; this bounds the wall clock.
MAX_ROUNDS = 40

BLOCK_NO_CHECKER = "no checker"


def _env_int(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return fallback
    try:
        return int(str(raw).strip())
    except ValueError:
        log.warning("filler.bad_env", extra={"name": name, "value": raw})
        return fallback


# ---------------------------------------------------------------------------
# What the run did
# ---------------------------------------------------------------------------

@dataclass
class FillReport:
    """One run, in numbers a person can compare against a bill."""

    calls: int = 0
    templates_kept: int = 0
    templates_rejected: int = 0
    items_from_existing: int = 0      # the free half
    items_from_new: int = 0
    items_discarded: int = 0
    blocked: list[tuple[str, str]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    stopped: str = "nothing left to do"
    depths_before: dict[str, int] = field(default_factory=dict)
    depths_after: dict[str, int] = field(default_factory=dict)

    @property
    def items_added(self) -> int:
        return self.items_from_existing + self.items_from_new

    def summary(self) -> str:
        return (f"{self.calls} call(s), {self.templates_kept} template(s) kept, "
                f"{self.templates_rejected} rejected, "
                f"{self.items_from_existing} question(s) from families already "
                f"banked and {self.items_from_new} from new ones, "
                f"{self.items_discarded} discarded by the verifier. "
                f"Stopped: {self.stopped}.")


# ---------------------------------------------------------------------------
# Which subtopics are in play
# ---------------------------------------------------------------------------

def candidate_nodes(subjects: Optional[Sequence[str]] = None,
                    only: Optional[Sequence[str]] = None
                    ) -> tuple[list[str], dict[str, str]]:
    """(subtopics worth filling, {subtopic: why it was skipped}).

    Both halves are returned because the second half is the honest answer to
    "why is my topic empty", and a filler that quietly iterated the first half
    would leave that question unanswerable.
    """
    wanted = {s.strip().lower() for s in (subjects or ()) if s and s.strip()}
    chosen = {s for s in (only or ()) if s}
    live: list[str] = []
    skipped: dict[str, str] = {}

    for subject, pool in syllabus.SUBJECTS.items():
        key = syllabus.SUBJECT_KEYS[subject]
        if wanted and key not in wanted:
            continue
        for sub in pool:
            if chosen and sub.id not in chosen:
                continue
            if not verify.fillable(sub.id):
                # Covers both the judge-only subtopics and the ones whose
                # verification the syllabus claims but no routine provides.
                skipped[sub.id] = BLOCK_NO_CHECKER
                continue
            reason = templates.unwritable_reason(sub.id)
            if reason:
                skipped[sub.id] = reason
                continue
            live.append(sub.id)
    return live, skipped


def _record_skips(skipped: dict[str, str], already: dict[str, dict]) -> None:
    """Write each skip reason onto the node once, not once per night."""
    for node, reason in skipped.items():
        current = already.get(node, {}).get("blocked_reason")
        if current != reason:
            store.block_node(node, reason)


# ---------------------------------------------------------------------------
# Brake 2: the free half
# ---------------------------------------------------------------------------

def top_up_from_existing(node: str, deficit: int, *,
                         count: int = DEFAULT_INSTANCES,
                         report: Optional[FillReport] = None) -> int:
    """More questions for one subtopic from the families it already has.

    ZERO LLM calls. Every family has a parameter space far larger than the
    sixty instances first taken from it, so a subtopic that has ever been
    generated for can almost always be deepened for nothing. This runs before
    any call is considered, which is what makes "most nights cost nothing" true
    rather than aspirational.

    Variants already banked are passed to `expand` as `skip`, so a second
    night's top-up extends a family's stock instead of colliding with it and
    relying on the UNIQUE constraint to notice.
    """
    if deficit <= 0:
        return 0
    live = store.live_templates([node])
    if not live:
        return 0

    banked = store.live_items([node], limit=10_000)
    keys_by_template: dict[str, set] = {}
    for item in banked:
        keys_by_template.setdefault(item.template_id, set()).add(item.variant_key)

    added = 0
    exhausted: set[str] = set()
    # Passes, not one bite each. A single family can carry a whole deficit, and
    # stopping after `count` instances from it would leave a subtopic short and
    # send the run to an LLM call it did not need. The loop ends when a full
    # pass over the families adds nothing, which is the only honest definition
    # of "the free work is finished".
    while added < deficit and len(exhausted) < len(live):
        gained = 0
        for template in live:
            if added >= deficit or template.id in exhausted:
                continue
            skip = keys_by_template.setdefault(template.id, set())
            want = min(count, deficit - added)
            try:
                made = instances.expand(template, count=want,
                                        seed=len(skip), skip=skip)
            except instances.TemplateError as exc:
                # The family is used up, or its parameters no longer render.
                # Not a failure of the run: there are usually others here.
                log.info("filler.exhausted",
                         extra={"template": template.id,
                                "reason": str(exc)[:200]})
                exhausted.add(template.id)
                continue
            kept, discarded = _admit_all(template, made)
            stored = _bank(template, kept)
            skip.update(i.variant_key for i in made)
            added += stored
            gained += stored
            if report is not None:
                report.items_discarded += discarded
            store.bump_template_counts(template.id, made=len(made),
                                       verified=len(kept))
            store.note_generation(node, items_kept=stored,
                                  items_discarded=discarded)
            if stored == 0:
                exhausted.add(template.id)
        if not gained:
            break
    if added:
        store.note_filled(node)
    return added


def _admit_all(template: TemplateRow, made: Iterable) -> tuple[list, int]:
    """Every instance through the real admission gate, one at a time.

    Not sampled. The probe in `templates.py` proved the family CAN be checked;
    this proves each question IS checked, and the two are different claims. An
    instance that fails here is dropped and counted, because a family can be
    right in general and wrong at the edge of its own parameter range.
    """
    kept: list = []
    discarded = 0
    for instance in made:
        verdict = verify.admit(instance)
        if verdict.verified and verdict.conclusive:
            kept.append(instance)
        else:
            discarded += 1
            log.debug("filler.discarded",
                      extra={"template": template.id,
                             "reason": (verdict.notes or "")[:200]})
    return kept, discarded


def _bank(template: TemplateRow, kept: list) -> int:
    if not kept:
        return 0
    return store.add_items(
        template, kept, verified_by=verify.verified_by(template.verify_kind),
        verifier_notes=f"admitted by {verify.verified_by(template.verify_kind)}")


# ---------------------------------------------------------------------------
# Ordering: demand first
# ---------------------------------------------------------------------------

def order_nodes(nodes: Sequence[str], depths: dict[str, int],
                demand: dict[str, dict], ceiling: int) -> list[str]:
    """Which shallow subtopic to spend on first.

    Demand before evenness. A scope real students have hit dry is worth more
    than an even bank, and `dry_requests` is the only signal that says which
    scopes those are. Depth breaks the tie, so among topics nobody has asked
    for the emptiest goes first.
    """
    short = [n for n in nodes if depths.get(n, 0) < ceiling]
    return sorted(short, key=lambda n: (
        -int(demand.get(n, {}).get("dry_requests", 0) or 0),
        -int(demand.get(n, {}).get("requests", 0) or 0),
        depths.get(n, 0),
        n,
    ))


def _kind_for(node: str, live: Sequence[TemplateRow]) -> Optional[str]:
    """The kind this subtopic has fewest families of.

    Spreading families across a subtopic's kinds is what stops a bank where
    every Antidifferentiation question is the same shape with different
    numbers, which is the failure the whole spacing machinery cannot fix
    because it is not a repetition, it is a monotony.
    """
    kinds = templates.writable_kinds(node)
    if not kinds:
        return None
    counts = {k: 0 for k in kinds}
    for template in live:
        if template.verify_kind in counts:
            counts[template.verify_kind] += 1
    return min(kinds, key=lambda k: (counts[k], kinds.index(k)))


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def fill_once(client: Optional[LLMClient] = None, *,
              subjects: Optional[Sequence[str]] = None,
              only: Optional[Sequence[str]] = None,
              budget: Optional[int] = None,
              min_depth: Optional[int] = None,
              target_depth: Optional[int] = None,
              count: Optional[int] = None,
              dry_run: bool = False,
              client_factory: Optional[Callable[[], LLMClient]] = None,
              now: Optional[int] = None) -> FillReport:
    """One pass of the filler. Everything the cron service does.

    `client` is injected rather than constructed so a check can count calls
    without a network or an API key, and it is constructed LAZILY when it is
    not injected, so a run that only does free work never needs a key at all.
    """
    budget = int(DEFAULT_BUDGET if budget is None else budget)
    floor = int(DEFAULT_MIN_DEPTH if min_depth is None else min_depth)
    ceiling = int(DEFAULT_TARGET_DEPTH if target_depth is None else target_depth)
    per_template = int(DEFAULT_INSTANCES if count is None else count)
    report = FillReport()

    nodes, skipped = candidate_nodes(subjects, only)
    report.skipped = dict(skipped)
    blocked = store.blocked_nodes()
    if not dry_run:
        _record_skips(skipped, blocked)

    report.depths_before = store.bank_depth(nodes) if nodes else {}
    if not nodes:
        report.stopped = "no subtopic in this run has both a checker and an " \
                         "answer the renderer can write"
        report.depths_after = dict(report.depths_before)
        return report

    # ---------------------------------------------------------- brake 2
    # Free work first, before a single call is considered.
    depths = dict(report.depths_before)
    for node in order_nodes(nodes, depths, store.scope_demand(), floor):
        deficit = floor - depths.get(node, 0)
        if dry_run:
            continue
        added = top_up_from_existing(node, deficit, count=per_template,
                                     report=report)
        if added:
            report.items_from_existing += added
            depths[node] = depths.get(node, 0) + added

    depths = store.bank_depth(nodes) if not dry_run else depths

    # ---------------------------------------------------------- brake 1, 3, 4
    factory: Optional[templates.TemplateFactory] = None
    stopped = "every subtopic is at target depth"

    for _round in range(MAX_ROUNDS):
        demand = store.scope_demand()
        blocked = store.blocked_nodes()
        queue = [n for n in order_nodes(nodes, depths, demand, floor)
                 if n not in blocked]
        if not queue:
            queue = [n for n in order_nodes(nodes, depths, demand, ceiling)
                     if n not in blocked]
        if not queue:
            break

        progressed = False
        for node in queue:
            # Brake 3. Re-read every time, from the database. A counter held in
            # this process would let a second run, or a restart, spend the same
            # cap again.
            spent = store.calls_today(now)
            if spent >= budget:
                stopped = (f"the daily cap of {budget} call(s) is spent "
                           f"({spent} recorded today)")
                report.stopped = stopped
                report.depths_after = store.bank_depth(nodes)
                return report

            if dry_run:
                progressed = True
                continue

            live = store.live_templates([node])
            kind = _kind_for(node, live)
            if kind is None:
                continue
            if factory is None:
                factory = templates.TemplateFactory(
                    _resolve_client(client, client_factory))

            attempt = _one_template(factory, node, kind, live, per_template,
                                    report)
            depths = store.bank_depth(nodes)
            progressed = progressed or attempt

        if not progressed:
            stopped = "no subtopic could take another family this run"
            break

    report.stopped = stopped
    report.depths_after = store.bank_depth(nodes) if not dry_run else depths
    return report


def _resolve_client(client: Optional[LLMClient],
                    factory: Optional[Callable[[], LLMClient]]) -> LLMClient:
    if client is not None:
        return client
    if factory is not None:
        return factory()
    from ..llm import get_client
    return get_client()


def _one_template(factory: templates.TemplateFactory, node: str, kind: str,
                  live: Sequence[TemplateRow], per_template: int,
                  report: FillReport) -> bool:
    """One call, one family, and everything that follows from it.

    The call is recorded in `practice_generation_log` IMMEDIATELY after it
    returns, before the template is validated or expanded. That ordering is the
    whole of brake 3: a crash between the call and the bookkeeping would
    otherwise leave the cap believing the money was never spent.
    """
    sub = syllabus.subtopic(node)
    existing = tuple(t.question_pattern[:100] for t in live)[:6]
    try:
        attempt = factory.generate(sub, kind, existing=existing)
    except Exception as exc:                                       # noqa: BLE001
        # A network or provider failure is not the subtopic's fault, so it must
        # not count towards the three strikes that block it. It did not cost a
        # call either, as far as anything here can tell.
        log.warning("filler.call_failed",
                    extra={"subtopic": node, "error": str(exc)[:300]})
        return False

    store.note_generation(node, calls=attempt.calls)
    report.calls += attempt.calls

    if not attempt.ok:
        if attempt.template is not None:
            store.save_template(attempt.template)
        store.note_generation(node, templates_rejected=1)
        report.templates_rejected += 1
        runs = store.record_node_failure(node)
        log.info("filler.rejected",
                 extra={"subtopic": node, "kind": kind, "run": runs,
                        "reason": (attempt.reason or "")[:200]})
        if runs >= FAILURES_TO_BLOCK:
            # Brake 4. The reason carries the last rejection, because that is
            # what a human needs to decide whether the prompt, the checker or
            # the subtopic itself is at fault.
            reason = (f"{runs} templates in a row rejected. Last: "
                      f"{(attempt.reason or 'no reason recorded')[:400]}")
            store.block_node(node, reason)
            report.blocked.append((node, reason))
        return False

    template = attempt.template
    store.save_template(template)
    store.note_generation(node, templates_kept=1)
    report.templates_kept += 1

    try:
        made = instances.expand(template, count=per_template, seed=0)
    except instances.TemplateError as exc:
        log.warning("filler.expand_failed",
                    extra={"template": template.id, "error": str(exc)[:200]})
        made = []
    kept, discarded = _admit_all(template, made)
    stored = _bank(template, kept)
    store.bump_template_counts(template.id, made=len(made), verified=len(kept))
    store.note_generation(node, items_kept=stored, items_discarded=discarded)
    report.items_from_new += stored
    report.items_discarded += discarded
    store.clear_node_failures(node)
    log.info("filler.kept",
             extra={"subtopic": node, "kind": kind, "template": template.id,
                    "banked": stored, "discarded": discarded})
    return stored > 0


# ---------------------------------------------------------------------------
# Reporting and the command line
# ---------------------------------------------------------------------------

def status(subjects: Optional[Sequence[str]] = None) -> str:
    """Depth, demand and blocks, as text. What `--status` prints."""
    nodes, skipped = candidate_nodes(subjects)
    depths = store.bank_depth(nodes) if nodes else {}
    blocked = store.blocked_nodes()
    demand = store.scope_demand()
    lines = [f"fillable subtopics: {len(nodes)}",
             f"skipped: {len(skipped)}",
             f"calls today: {store.calls_today()}", ""]
    for node in sorted(nodes, key=lambda n: depths.get(n, 0)):
        mark = "BLOCKED " if node in blocked else "        "
        lines.append(f"  {mark}{depths.get(node, 0):>6}  {node}"
                     f"  (dry {demand.get(node, {}).get('dry_requests', 0)})")
    if skipped:
        lines.append("")
        lines.append("  not stocked:")
        for node, reason in sorted(skipped.items()):
            lines.append(f"    {node}: {reason}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--once", action="store_true",
                        help="run one pass and exit. This is the only mode; "
                             "the flag exists so the cron command reads "
                             "honestly.")
    parser.add_argument("--status", action="store_true",
                        help="print bank depth and blocks, make no calls")
    parser.add_argument("--subject", action="append", default=None,
                        help="methods or chemistry, repeatable")
    parser.add_argument("--subtopic", action="append", default=None,
                        help="one subtopic id, repeatable")
    parser.add_argument("--budget", type=int, default=None,
                        help="LLM calls allowed today, across every process")
    parser.add_argument("--min-depth", type=int, default=None)
    parser.add_argument("--target-depth", type=int, default=None)
    parser.add_argument("--count", type=int, default=None,
                        help="instances expanded per template")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be filled, spend nothing")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.getenv("FOLIO_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    accounts_db.init_db()
    store.init_practice_db()

    if args.status:
        print(status(args.subject))
        return 0

    try:
        accounts_db.record_worker_heartbeat(worker_name="practice-filler")
    except Exception:                                              # noqa: BLE001
        # Visible in the admin console is a nice-to-have. Not filling the bank
        # because the liveness row would not write is not.
        log.exception("could not record the practice filler heartbeat")

    started = time.time()
    report = fill_once(
        subjects=args.subject, only=args.subtopic,
        budget=args.budget if args.budget is not None
        else _env_int("FOLIO_PRACTICE_TEMPLATE_BUDGET_PER_DAY", DEFAULT_BUDGET),
        min_depth=args.min_depth if args.min_depth is not None
        else _env_int("FOLIO_PRACTICE_MIN_DEPTH", DEFAULT_MIN_DEPTH),
        target_depth=args.target_depth if args.target_depth is not None
        else _env_int("FOLIO_PRACTICE_TARGET_DEPTH", DEFAULT_TARGET_DEPTH),
        count=args.count if args.count is not None
        else _env_int("FOLIO_PRACTICE_INSTANCES_PER_TEMPLATE", DEFAULT_INSTANCES),
        dry_run=args.dry_run,
    )
    print(report.summary())
    for node, reason in report.blocked:
        print(f"  blocked {node}: {reason}")
    log.info("filler.done",
             extra={"calls": report.calls, "items": report.items_added,
                    "seconds": round(time.time() - started, 1)})
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
