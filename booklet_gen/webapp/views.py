"""Generate form (dropdowns), background generation, status, download."""
from __future__ import annotations

import io
import os
import re
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash,
    jsonify, send_file, abort, g, current_app,
)

from . import db
from .auth import login_required
from ..programs import PROGRAMS, ACCELERATE_SUBJECTS, EXAM_PROGRAMS, EXAM_YEARS

bp = Blueprint("views", __name__)

YEARS = [f"Year {n}" for n in range(1, 13)]
TERM_WEEKS = 10

# Abuse guard: generation is free and unlimited in price, but each one costs
# real Gemini API spend, so cap requests per account. Not a paywall, just a
# ceiling against a bot or a stuck retry loop running up the bill.
DAILY_GENERATION_LIMIT = int(os.environ.get("FOLIO_DAILY_LIMIT", "5"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower() or "booklet"


@bp.route("/")
def index():
    return render_template(
        "index.html",
        programs=PROGRAMS, years=YEARS, subjects=ACCELERATE_SUBJECTS,
        term_weeks=TERM_WEEKS, exam_programs=EXAM_PROGRAMS, exam_years=EXAM_YEARS,
    )


@bp.route("/generate", methods=["POST"])
@login_required
def generate():
    program = (request.form.get("program") or "").strip()
    year = (request.form.get("year") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    name = (request.form.get("student_name") or "Student").strip()
    is_term = request.form.get("term_plan") == "on"

    if program not in PROGRAMS:
        flash("Please choose a booklet type.")
        return redirect(url_for("views.index"))
    if year not in YEARS:
        flash("Please choose a year level.")
        return redirect(url_for("views.index"))
    if PROGRAMS[program].pick_subject and subject not in ACCELERATE_SUBJECTS:
        flash("Please choose a subject for Academic Accelerate.")
        return redirect(url_for("views.index"))
    is_exam = program in EXAM_PROGRAMS
    if is_exam and year not in EXAM_YEARS:
        flash(f"{PROGRAMS[program].label} is only available for "
              f"{' and '.join(EXAM_YEARS)}.")
        return redirect(url_for("views.index"))

    if db.jobs_started_last_24h(g.user["id"]) >= DAILY_GENERATION_LIMIT:
        flash(f"You've reached today's limit of {DAILY_GENERATION_LIMIT} booklets. "
              "Please try again tomorrow.")
        return redirect(url_for("views.index"))

    job_id = uuid.uuid4().hex
    label = f"{PROGRAMS[program].label} - {year}" + (f" - {subject}" if subject else "")
    if is_term:
        label = f"{label} (term plan)"
    db.create_job(job_id, g.user["id"], label)

    args = dict(program=program, year=year, subject=subject or None,
                topic=topic or None, name=name, is_term=is_term,
                is_exam=is_exam, user_id=g.user["id"], label=label,
                out_dir=str(current_app.config["OUTPUT_DIR"]))
    threading.Thread(target=_run_job, args=(job_id, args), daemon=True).start()
    return redirect(url_for("views.progress", job_id=job_id))


def _run_job(job_id: str, a: dict):
    """Background worker. Imported lazily so the web process starts fast."""
    from ..pipeline import BookletPipeline
    from ..formatter import render_pdf, render_exam_pdf
    try:
        pipeline = BookletPipeline()
        out_dir = Path(a["out_dir"])
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = _slug(a.get("label") or "booklet")
        if a.get("is_exam"):
            paper = pipeline.run_exam(
                a["year"], a["name"], topic_focus=a["topic"],
            )
            path = out_dir / f"{job_id}.pdf"
            render_exam_pdf(paper, path)
            _archive(job_id, a["user_id"], path, f"{slug}.pdf", "application/pdf")
            db.finish_job(job_id, path=str(path))
        elif a["is_term"]:
            booklets = pipeline.run_term_plan(
                a["program"], a["year"], a["name"],
                subject=a["subject"], weeks=TERM_WEEKS, topic_hint=a["topic"],
            )
            folder = out_dir / f"{job_id}"
            folder.mkdir(parents=True, exist_ok=True)
            for data in booklets:
                fn = f"week-{data.week_number:02d}-{_slug(data.week_focus or 'booklet')}.pdf"
                render_pdf(data, folder / fn)
            # Archive the term plan as the same zip the user would download.
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for pdf in sorted(folder.glob("*.pdf")):
                    zf.write(pdf, pdf.name)
            db.save_job_file(job_id, a["user_id"], f"{slug}.zip",
                             "application/zip", buf.getvalue())
            db.finish_job(job_id, dir=str(folder))
        else:
            data = pipeline.run_program(
                a["program"], a["year"], a["name"],
                subject=a["subject"], topic=a["topic"],
            )
            path = out_dir / f"{job_id}.pdf"
            render_pdf(data, path)
            _archive(job_id, a["user_id"], path, f"{slug}.pdf", "application/pdf")
            db.finish_job(job_id, path=str(path))
    except Exception as e:
        db.fail_job(job_id, str(e))


def _archive(job_id: str, user_id: int, path: Path, filename: str, mimetype: str):
    """Copy a finished file into the database.

    The instance filesystem is ephemeral, so anything left only on disk is gone
    after the next restart or deploy and the history page would link to
    nothing. Failing to archive must not fail the job: the user can still
    download it in this session from disk.
    """
    try:
        db.save_job_file(job_id, user_id, filename, mimetype, path.read_bytes())
    except Exception as e:
        current_app.logger.warning("archive failed for %s: %s", job_id, e)


@bp.route("/progress/<job_id>")
@login_required
def progress(job_id: str):
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"]:
        abort(404)
    return render_template("progress.html", job=job)


@bp.route("/status/<job_id>")
@login_required
def status(job_id: str):
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"]:
        abort(404)
    payload = {"status": job["status"]}
    if job["status"] == "done":
        payload["download_url"] = url_for("views.download", job_id=job_id)
    elif job["status"] == "error":
        payload["error"] = job["error"]
    return jsonify(payload)


@bp.route("/library")
@login_required
def library():
    """Everything this account has generated, newest first."""
    return render_template("library.html", jobs=db.list_jobs(g.user["id"]),
                           retention=db.FILE_RETENTION_PER_USER)


@bp.route("/download/<job_id>")
@login_required
def download(job_id: str):
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"] or job["status"] != "done":
        abort(404)

    # Prefer the archived copy: it is the only one that survives a restart.
    stored = db.get_job_file(job_id)
    if stored is not None:
        return send_file(
            io.BytesIO(bytes(stored["data"])), as_attachment=True,
            download_name=stored["filename"], mimetype=stored["mimetype"],
        )

    # Fall back to the instance filesystem (same session, or archiving failed).
    if job["path"] and Path(job["path"]).exists():
        return send_file(Path(job["path"]), as_attachment=True,
                         download_name=f"{_slug(job['label'])}.pdf",
                         mimetype="application/pdf")
    if job["dir"] and Path(job["dir"]).exists():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for pdf in sorted(Path(job["dir"]).glob("*.pdf")):
                zf.write(pdf, pdf.name)
        buf.seek(0)
        return send_file(buf, as_attachment=True,
                         download_name=f"{_slug(job['label'])}.zip",
                         mimetype="application/zip")
    abort(404)
