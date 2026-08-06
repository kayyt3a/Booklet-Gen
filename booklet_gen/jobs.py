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

from .webapp import db

log = logging.getLogger(__name__)
TERM_WEEKS = 10


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
    db.finish_job(job_id)
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
    pipeline = BookletPipeline()
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

    data = pipeline.run_program(
        args["program"], args["year"], args["name"],
        subject=args.get("subject"), topic=args.get("topic"),
    )
    path = out_dir / f"{job_id}.pdf"
    render_pdf(data, path)
    db.save_job_file(job_id, user_id, f"{slug}.pdf",
                     "application/pdf", path.read_bytes())
    _finish_and_clean(job_id, path)
