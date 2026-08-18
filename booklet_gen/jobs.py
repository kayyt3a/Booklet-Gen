"""Durable booklet job execution, shared by inline development and workers."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from .programs import TERM_PLAN_WEEKS
from .webapp import db

log = logging.getLogger(__name__)
# Imported, never redefined: this is the same number the customer was
# charged for in views.generate.
TERM_WEEKS = TERM_PLAN_WEEKS

# Subtopics within one booklet generate concurrently (pipeline.py's
# ThreadPoolExecutor); each is a handful of sequential network calls to
# Gemini and no meaningful CPU work while it waits, so this pool can run far
# larger than the CPU count without starving the instance. Left at the
# pipeline's own default of 4, an 8-subtopic booklet serialised into two full
# rounds of teaching + questions + validation for no reason. Configurable
# because the right number depends on the API key's actual concurrent-request
# ceiling, which this file cannot see.
MAX_WORKERS = int(os.environ.get("FOLIO_MAX_WORKERS", "8"))


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower() or "booklet"


def _clear_target(target: Path) -> None:
    try:
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    except OSError as exc:
        log.warning("could not clear worker output %s: %s", target, exc)


def _finish_and_clean(job_id: str, target: Path) -> None:
    """Settle the durable copy, then remove ephemeral worker output."""
    if not db.finish_job(job_id):
        # The stale sweep already failed and refunded this job while it was
        # still running. Do not resurrect it: the credits have gone back, so
        # completing it now would hand over the booklet for nothing.
        log.warning("job %s finished after it was already settled, "
                    "leaving it failed and refunded", job_id)
    _clear_target(target)


def run_job_by_id(job_id: str) -> bool:
    job = db.claim_job(job_id)
    if job is None:
        return False
    execute_claimed_job(job)
    return True


def execute_claimed_job(job: dict) -> None:
    """Run one already-claimed job and always settle its database state."""
    job_id = job["id"]
    try:
        args = json.loads(job.get("request_json") or "{}")
        if not args:
            raise ValueError("The queued job has no generation request.")
        _generate(job, args)
    except Exception as exc:
        log.exception("generation job %s failed", job_id)
        db.fail_job(job_id, str(exc))
    finally:
        # An account can be deleted while its worker is still running. In
        # that case archiving fails after a PDF has reached local disk, so
        # clear both possible output paths even when the job row is gone.
        out_dir = Path(os.environ.get("FOLIO_OUTPUT", "output"))
        _clear_target(out_dir / f"{job_id}.pdf")
        _clear_target(out_dir / job_id)


def _generate(job: dict, args: dict) -> None:
    from .formatter import render_exam_pdf, render_pdf
    from .pipeline import BookletPipeline

    job_id = job["id"]
    user_id = int(job["user_id"])
    out_dir = Path(os.environ.get("FOLIO_OUTPUT", "output"))
    out_dir.mkdir(parents=True, exist_ok=True)
    pipeline = BookletPipeline(max_workers=MAX_WORKERS)
    slug = f"{_slug(job.get('label') or 'booklet')}-{datetime.now():%Y%m%d}"

    if args.get("is_exam"):
        paper = pipeline.run_exam(
            args["year"], args["name"], topic_focus=args.get("topic"),
        )
        path = out_dir / f"{job_id}.pdf"
        render_exam_pdf(paper, path)
        db.save_job_file(job_id, user_id, f"{slug}.pdf",
                         "application/pdf", path.read_bytes())
        _finish_and_clean(job_id, path)
        return

    if args.get("is_term"):
        booklets = pipeline.run_term_plan(
            args["program"], args["year"], args["name"],
            subject=args.get("subject"), weeks=TERM_WEEKS,
            topic_hint=args.get("topic"),
        )
        folder = out_dir / job_id
        folder.mkdir(parents=True, exist_ok=True)
        for data in booklets:
            filename = (
                f"week-{data.week_number:02d}-"
                f"{_slug(data.week_focus or 'booklet')}.pdf"
            )
            render_pdf(data, folder / filename)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for pdf in sorted(folder.glob("*.pdf")):
                archive.write(pdf, pdf.name)
        db.save_job_file(job_id, user_id, f"{slug}.zip",
                         "application/zip", buffer.getvalue())
        _finish_and_clean(job_id, folder)
        return

    plan_id = args.get("plan_id")
    if plan_id:
        data = _run_plan_week(pipeline, args, int(plan_id), job_id)
    else:
        data = pipeline.run_program(
            args["program"], args["year"], args["name"],
            subject=args.get("subject"), topic=args.get("topic"),
        )
    path = out_dir / f"{job_id}.pdf"
    render_pdf(data, path)
    db.save_job_file(job_id, user_id, f"{slug}.pdf",
                     "application/pdf", path.read_bytes())
    _finish_and_clean(job_id, path)


def _run_plan_week(pipeline, args: dict, plan_id: int, job_id: str):
    """Generate one week of a study plan, then record what it taught.

    The reading and the writing both live here rather than in the pipeline,
    which knows nothing about the database. What gets recorded is what the
    booklet actually contains: the outline parser picks the real subtopics and
    the hour cap can drop some of them, so storing the planner's label would
    have next week recapping a heading the student never sat through.
    """
    plan = db.get_plan(plan_id)
    if plan is None:
        # The customer deleted the plan between ordering and generation. Their
        # credit is already spent, so produce the booklet they paid for rather
        # than failing; it simply carries nothing over.
        log.warning("job %s names plan %s, which is gone; generating "
                    "a standalone booklet", job_id, plan_id)
        return pipeline.run_program(
            args["program"], args["year"], args["name"],
            subject=args.get("subject"), topic=args.get("topic"),
        )

    week = int(args.get("plan_week") or 1)
    ladder = {int(entry.get("week", 0)): entry for entry in plan["ladder"]}
    entry = ladder.get(week, {})
    history = db.plan_history(plan_id)
    previous = db.get_plan_week(plan_id, week - 1) if week > 1 else None

    data = pipeline.run_plan_week(
        plan["program"], plan["year_level"], plan["student_name"],
        week=week, total_weeks=int(plan["total_weeks"]),
        subject=plan["subject"],
        focus=entry.get("focus") or args.get("topic"),
        # Only the week immediately before may be recapped or tested, so a
        # gap in the plan carries nothing rather than reaching further back
        # and asking about a booklet the student may never have printed.
        prev_focus=(previous or {}).get("taught"),
        prev_spelling_words=(previous or {}).get("spelling_words"),
        prev_spelling_week=(week - 1) if previous else None,
        words_already_set=history["words_set"],
        prev_table=(previous or {}).get("tables_table"),
        prev_table_week=(week - 1) if previous else None,
        tables_already_set=history["tables_set"],
        is_revision=bool(entry.get("revision")),
    )

    taught = "; ".join(s.subtopic for s in data.sections if s.subtopic)
    db.record_plan_week(
        plan_id, week, job_id,
        taught or entry.get("focus"),
        data.spelling_list.words if data.spelling_list else [],
        data.tables_list.table if data.tables_list else None,
    )
    return data
