"""Minimal Flask app: a form -> a generated PDF download."""
from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, send_file, url_for, jsonify, abort

from booklet_gen.formatter import render_pdf
from booklet_gen.logging_setup import configure_logging
from booklet_gen.pipeline import BookletPipeline

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

_configured = False


def _ensure_logging():
    global _configured
    if not _configured:
        configure_logging()
        _configured = True


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "booklet"


# Simple in-memory job store. Fine for v1 single-process use.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _run_job(job_id: str, description: str, student_name: str, questions: int):
    try:
        pipeline = BookletPipeline(questions_per_subtopic=questions)
        data = pipeline.run(description, student_name)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = OUTPUT_DIR / f"{_slug(student_name)}-{_slug(description)}-{ts}.pdf"
        render_pdf(data, out_path)
        with _jobs_lock:
            _jobs[job_id].update(status="done", path=str(out_path))
    except Exception as e:
        with _jobs_lock:
            _jobs[job_id].update(status="error", error=str(e))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    _ensure_logging()
    description = (request.form.get("description") or "").strip()
    student_name = (request.form.get("student_name") or "Student").strip()
    questions = int(request.form.get("questions") or 5)
    if not description:
        return render_template("index.html", error="Please describe the topic and year level."), 400

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "running", "description": description, "student_name": student_name}
    thread = threading.Thread(
        target=_run_job, args=(job_id, description, student_name, questions), daemon=True,
    )
    thread.start()
    return render_template("progress.html", job_id=job_id, description=description, student_name=student_name)


@app.route("/status/<job_id>")
def status(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        abort(404)
    payload = {"status": job["status"]}
    if job["status"] == "done":
        payload["download_url"] = url_for("download", job_id=job_id)
    elif job["status"] == "error":
        payload["error"] = job.get("error")
    return jsonify(payload)


@app.route("/download/<job_id>")
def download(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job.get("status") != "done":
        abort(404)
    path = Path(job["path"])
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/pdf")


if __name__ == "__main__":
    _ensure_logging()
    app.run(host="127.0.0.1", port=5000, debug=False)
