"""Generate form (dropdowns), background generation, status, download."""
from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
import time
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

log = logging.getLogger(__name__)

bp = Blueprint("views", __name__)

YEARS = [f"Year {n}" for n in range(1, 13)]
TERM_WEEKS = 10

# Abuse guard: generation is free and unlimited in price, but each one costs
# real Gemini API spend, so cap it. Not a paywall, just a ceiling against a
# bot or a stuck retry loop running up the bill.
#
# The unit is a booklet, not a request. The old FOLIO_DAILY_LIMIT counted job
# rows, and a term plan is one row that generates TERM_WEEKS booklets, so a
# stated cap of 5 was really 50. This is a new variable name on purpose: the
# number means something different now, and a stale FOLIO_DAILY_LIMIT=5 left
# on a host would otherwise make term plans impossible to run at all.
DAILY_BOOKLET_LIMIT = int(os.environ.get("FOLIO_DAILY_BOOKLET_LIMIT", "12"))

# Signup is free and unverified, so the per-account cap only costs an abuser
# the effort of creating more accounts. This is the ceiling for the whole
# instance, i.e. the most API spend one day can produce.
GLOBAL_DAILY_BOOKLET_LIMIT = int(
    os.environ.get("FOLIO_GLOBAL_DAILY_BOOKLET_LIMIT", "120"))

# How long a single job may run before it is presumed dead. Generation is a
# background thread with no timeout of its own, so without this a hung LLM
# call leaves the row "running" and the user watching a spinner for ever.
JOB_TIMEOUT_SECONDS = int(os.environ.get("FOLIO_JOB_TIMEOUT", "2700"))

if os.environ.get("FOLIO_DAILY_LIMIT"):
    log.warning(
        "FOLIO_DAILY_LIMIT is set but no longer used. The cap is now counted "
        "in booklets, not requests: set FOLIO_DAILY_BOOKLET_LIMIT instead "
        "(currently %d).", DAILY_BOOKLET_LIMIT,
    )


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

    # A term plan is one request but TERM_WEEKS booklets, so it costs that
    # much of the budget and is charged that much of the quota.
    units = TERM_WEEKS if is_term else 1

    used = db.booklets_started_last_24h(g.user["id"])
    if used + units > DAILY_BOOKLET_LIMIT:
        remaining = max(0, DAILY_BOOKLET_LIMIT - used)
        if units > 1 and remaining:
            flash(f"A term plan counts as {units} booklets and you have "
                  f"{remaining} left of today's {DAILY_BOOKLET_LIMIT}. "
                  "Generate single booklets, or try the term plan tomorrow.")
        else:
            flash(f"You've reached today's limit of {DAILY_BOOKLET_LIMIT} "
                  "booklets. Please try again tomorrow.")
        return redirect(url_for("views.index"))

    if db.booklets_started_globally_last_24h() + units > GLOBAL_DAILY_BOOKLET_LIMIT:
        log.warning("global daily booklet ceiling reached (limit=%d)",
                    GLOBAL_DAILY_BOOKLET_LIMIT)
        flash("Folio has hit its overall generation limit for today. "
              "Please try again tomorrow.")
        return redirect(url_for("views.index"))

    job_id = uuid.uuid4().hex
    label = f"{PROGRAMS[program].label} - {year}" + (f" - {subject}" if subject else "")
    if is_term:
        label = f"{label} (term plan)"
    db.create_job(job_id, g.user["id"], label, units=units)

    args = dict(program=program, year=year, subject=subject or None,
                topic=topic or None, name=name, is_term=is_term,
                is_exam=is_exam, user_id=g.user["id"], label=label,
                out_dir=str(current_app.config["OUTPUT_DIR"]))
    threading.Thread(target=_run_job, args=(job_id, args), daemon=True).start()
    return redirect(url_for("views.progress", job_id=job_id))


def _run_job(job_id: str, a: dict):
    """Background worker. Imported lazily so the web process starts fast."""
    from ..pipeline import BookletPipeline
    from ..formatter import render_booklet_pair, render_exam_pdf

    # A Python thread cannot be killed from outside, and the LLM client owns
    # its own socket timeout (llm/gemini.py is not ours to change), so the
    # half we can implement is the bookkeeping: after the timeout the job is
    # reported as failed instead of spinning for ever. If the thread is still
    # alive and later succeeds, finish_job flips it back to done and the
    # booklet appears in the library.
    def _timed_out():
        if db.fail_job_if_running(job_id, (
                f"Generation timed out after {JOB_TIMEOUT_SECONDS // 60} "
                "minutes and was abandoned. Please try again.")):
            log.warning("job %s timed out after %ds", job_id, JOB_TIMEOUT_SECONDS)

    watchdog = threading.Timer(JOB_TIMEOUT_SECONDS, _timed_out)
    watchdog.daemon = True
    watchdog.start()
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
                # Writes both the tutor copy and a "-student" copy beside it;
                # the zip below globs *.pdf and picks up each pair.
                render_booklet_pair(data, folder / fn)
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
            # A tutor copy has the answer key bound in, so a tutoring firm
            # cannot hand it to a student. Deliver both, zipped: job_files is
            # keyed by job id and stores one file per job, which is the same
            # reason term plans are zipped.
            tutor, student = render_booklet_pair(data, path)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(tutor, f"{slug}.pdf")
                zf.write(student, f"{slug}-student.pdf")
            db.save_job_file(job_id, a["user_id"], f"{slug}.zip",
                             "application/zip", buf.getvalue())
            db.finish_job(job_id, path=str(path))
    except Exception as e:
        db.fail_job(job_id, str(e))
    finally:
        watchdog.cancel()


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
        # Module logger, not current_app: this runs in a background thread
        # with no application context, where touching current_app would raise
        # and turn a cosmetic archive failure into a failed job.
        log.warning("archive failed for %s: %s", job_id, e)


@bp.route("/progress/<job_id>")
@login_required
def progress(job_id: str):
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"]:
        abort(404)
    return render_template("progress.html", job=job)


def _settle_if_stale(job) -> dict:
    """Turn a job that has outlived the timeout into a reported failure.

    The worker thread it belonged to is gone (a redeploy, a spin-down, or a
    hung call), so leaving it "running" means an endless spinner.
    """
    if job["status"] != "running":
        return job
    if int(time.time()) - int(job["created_at"]) < JOB_TIMEOUT_SECONDS:
        return job
    db.fail_job_if_running(job["id"], (
        "Generation stopped before it finished. This usually means the server "
        "restarted mid-run. Please try again."))
    return db.get_job(job["id"]) or job


@bp.route("/status/<job_id>")
@login_required
def status(job_id: str):
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"]:
        abort(404)
    job = _settle_if_stale(job)
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
    jobs = db.list_jobs(g.user["id"])
    # Only touch the database again if this page would otherwise show a
    # spinner for a job whose worker cannot still be alive.
    cutoff = int(time.time()) - JOB_TIMEOUT_SECONDS
    if any(j["status"] == "running" and int(j["created_at"]) < cutoff for j in jobs):
        try:
            db.fail_stale_running_jobs(JOB_TIMEOUT_SECONDS)
            jobs = db.list_jobs(g.user["id"])
        except Exception as e:
            log.warning("stale job sweep failed: %s", e)
    return render_template("library.html", jobs=jobs,
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


# ---------- account: export and deletion ----------

@bp.route("/account")
@login_required
def account():
    jobs = db.list_jobs(g.user["id"], limit=10_000)
    return render_template(
        "account.html",
        job_count=len(jobs),
        file_count=sum(1 for j in jobs if j["filename"]),
        daily_limit=DAILY_BOOKLET_LIMIT,
        used_today=db.booklets_started_last_24h(g.user["id"]),
    )


@bp.route("/account/export")
@login_required
def account_export():
    """Download everything held about this account as JSON."""
    payload = db.export_account(g.user["id"])
    buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
    return send_file(buf, as_attachment=True,
                     download_name="folio-account-export.json",
                     mimetype="application/json")


def _remove_leftovers(leftovers) -> None:
    """Best effort removal of a deleted account's files from the instance disk.

    Only paths inside OUTPUT_DIR are touched, so a corrupt or hand-edited row
    cannot point the delete at anything else on the filesystem.
    """
    import shutil
    root = Path(current_app.config["OUTPUT_DIR"]).resolve()
    for path, folder in leftovers:
        for raw, is_dir in ((path, False), (folder, True)):
            if not raw:
                continue
            try:
                target = Path(raw).resolve()
                # Strictly below OUTPUT_DIR. `root` itself must never match,
                # or a null path would delete every user's output directory.
                if root not in target.parents:
                    continue
                if is_dir and target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                elif not is_dir and target.is_file():
                    target.unlink()
            except OSError as e:
                log.warning("could not remove %s: %s", raw, e)


@bp.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    """Delete this account and everything stored for it.

    Requires the account password: a logged-in session on a shared computer
    should not be enough to wipe someone's history, and it is a second check
    behind the CSRF token.
    """
    password = request.form.get("password") or ""
    if not db.verify_login(g.user["email"], password):
        flash("That password is not correct, so nothing was deleted.")
        return redirect(url_for("views.account"))

    user_id = g.user["id"]
    leftovers = db.delete_account(user_id)
    _remove_leftovers(leftovers)
    session.clear()
    log.info("account %s deleted at owner request", user_id)
    flash("Your account and all of its booklets have been deleted.")
    return redirect(url_for("views.index"))
