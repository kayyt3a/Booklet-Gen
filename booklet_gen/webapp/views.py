"""Generate form (dropdowns), background generation, status, download."""
from __future__ import annotations

import io
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
from ..programs import PROGRAMS, ACCELERATE_SUBJECTS

bp = Blueprint("views", __name__)

YEARS = [f"Year {n}" for n in range(1, 11)]
TERM_WEEKS = 10


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower() or "booklet"


@bp.route("/")
def index():
    return render_template(
        "index.html",
        programs=PROGRAMS, years=YEARS, subjects=ACCELERATE_SUBJECTS,
        term_weeks=TERM_WEEKS,
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

    job_id = uuid.uuid4().hex
    label = f"{PROGRAMS[program].label} - {year}" + (f" - {subject}" if subject else "")
    if is_term:
        label = f"{label} (term plan)"
    db.create_job(job_id, g.user["id"], label)

    args = dict(program=program, year=year, subject=subject or None,
                topic=topic or None, name=name, is_term=is_term,
                user_id=g.user["id"],
                out_dir=str(current_app.config["OUTPUT_DIR"]))
    threading.Thread(target=_run_job, args=(job_id, args), daemon=True).start()
    return redirect(url_for("views.progress", job_id=job_id))


def _run_job(job_id: str, a: dict):
    """Background worker. Imported lazily so the web process starts fast."""
    from ..pipeline import BookletPipeline
    from ..formatter import render_pdf
    try:
        pipeline = BookletPipeline()
        out_dir = Path(a["out_dir"])
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        if a["is_term"]:
            booklets = pipeline.run_term_plan(
                a["program"], a["year"], a["name"],
                subject=a["subject"], weeks=TERM_WEEKS, topic_hint=a["topic"],
            )
            folder = out_dir / f"{job_id}"
            folder.mkdir(parents=True, exist_ok=True)
            for data in booklets:
                fn = f"week-{data.week_number:02d}-{_slug(data.week_focus or 'booklet')}.pdf"
                render_pdf(data, folder / fn)
            db.finish_job(job_id, dir=str(folder))
        else:
            data = pipeline.run_program(
                a["program"], a["year"], a["name"],
                subject=a["subject"], topic=a["topic"],
            )
            path = out_dir / f"{job_id}.pdf"
            render_pdf(data, path)
            db.finish_job(job_id, path=str(path))
    except Exception as e:
        db.fail_job(job_id, str(e))


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


@bp.route("/download/<job_id>")
@login_required
def download(job_id: str):
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"] or job["status"] != "done":
        abort(404)
    if job["path"]:
        p = Path(job["path"])
        if not p.exists():
            abort(404)
        return send_file(p, as_attachment=True, download_name=f"{_slug(job['label'])}.pdf",
                         mimetype="application/pdf")
    # Term plan: zip the folder of weekly PDFs.
    folder = Path(job["dir"])
    if not folder.exists():
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for pdf in sorted(folder.glob("*.pdf")):
            zf.write(pdf, pdf.name)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{_slug(job['label'])}.zip",
                     mimetype="application/zip")
