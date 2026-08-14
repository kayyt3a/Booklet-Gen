"""Consumer generation, progress, library, download, and account routes."""
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
from pathlib import Path

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash,
    jsonify, send_file, abort, g, current_app,
)

from . import db
from .auth import login_required
from .commerce import payments_enabled
from .security import enforce_rate_limit
from ..programs import (
    PROGRAMS, ACCELERATE_SUBJECTS, EXAM_PROGRAMS, EXAM_YEARS,
    NAPLAN_PROGRAMS, customer_programs,
)

log = logging.getLogger(__name__)

bp = Blueprint("views", __name__)

YEARS = [f"Year {n}" for n in range(1, 13)]
BOOKLET_YEARS = [f"Year {n}" for n in range(1, 11)]

# NAPLAN is sat in Years 3, 5, 7 and 9 in Australia and in no other year. A
# "Year 6 NAPLAN booklet" is not a harder or easier version of a real thing, it
# is practice for a test that does not exist, and the guidance the product
# leans on has no Year 6 to lean on either: booklet_gen/guidance/ holds
# naplan_year_{3,5,7,9}.txt only, and a missing year supplement is skipped in
# silence, so the booklet would ship wearing a NAPLAN label with none of the
# year calibration behind it.
NAPLAN_YEARS = ("Year 3", "Year 5", "Year 7", "Year 9")
TERM_WEEKS = 10

# Abuse guard. Paid credits control entitlements while these limits cap the
# maximum API spend from a compromised account or automated attack.
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
JOB_MODE = (os.environ.get("FOLIO_JOB_MODE") or "inline").strip().lower()

if os.environ.get("FOLIO_DAILY_LIMIT"):
    log.warning(
        "FOLIO_DAILY_LIMIT is set but no longer used. The cap is now counted "
        "in booklets, not requests: set FOLIO_DAILY_BOOKLET_LIMIT instead "
        "(currently %d).", DAILY_BOOKLET_LIMIT,
    )


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s or "").strip("-").lower() or "booklet"


def _quota_allows(user_id: int, units: int) -> bool:
    used = db.booklets_started_last_24h(user_id)
    if used + units > DAILY_BOOKLET_LIMIT:
        remaining = max(0, DAILY_BOOKLET_LIMIT - used)
        if units > 1 and remaining:
            flash(f"A term plan counts as {units} booklets and you have "
                  f"{remaining} left of today's {DAILY_BOOKLET_LIMIT}. "
                  "Generate single booklets, or try the term plan tomorrow.")
        else:
            flash(f"You've reached today's limit of {DAILY_BOOKLET_LIMIT} "
                  "booklets. Please try again tomorrow.")
        return False
    if db.booklets_started_globally_last_24h() + units > GLOBAL_DAILY_BOOKLET_LIMIT:
        log.warning("global daily booklet ceiling reached (limit=%d)",
                    GLOBAL_DAILY_BOOKLET_LIMIT)
        flash("FolioAI has hit its overall generation limit for today. "
              "Please try again tomorrow.")
        return False
    return True


@bp.route("/")
def index():
    # Two different pages behind one URL. A signed-out visitor is deciding
    # whether this is worth an account, so they get the landing page: what a
    # booklet is, a real page out of one, and the four product lines. Someone
    # signed in has already decided and wants the form.
    #
    # This used to be one template branching on `g.user`, which meant a
    # prospective customer's first impression was a heading and a Sign up
    # button on an otherwise empty page.
    programs = customer_programs()
    customer_exam_programs = EXAM_PROGRAMS.intersection(programs)
    if not g.user:
        return render_template("landing.html", programs=programs,
                               exam_programs=customer_exam_programs)
    return render_template(
        "generate.html",
        programs=programs, years=YEARS, subjects=ACCELERATE_SUBJECTS,
        naplan_years=NAPLAN_YEARS, naplan_programs=sorted(NAPLAN_PROGRAMS),
        term_weeks=TERM_WEEKS, exam_programs=customer_exam_programs,
        exam_years=EXAM_YEARS,
        credits=db.credit_balance(g.user["id"]),
        payments_enabled=payments_enabled(),
    )


@bp.route("/generate", methods=["POST"])
@login_required
def generate():
    program = (request.form.get("program") or "").strip()
    year = (request.form.get("year") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    topic = (request.form.get("topic") or "").strip()
    name = (request.form.get("student_name") or "").strip() or "Student"
    is_term = request.form.get("term_plan") == "on"

    if program not in customer_programs():
        flash("That booklet type is not currently available.")
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
    if not is_exam and year not in BOOKLET_YEARS:
        flash("Practice booklets are available for Years 1 to 10.")
        return redirect(url_for("views.index"))
    # Enforced here and not only in the dropdown. The year arrives in a POST
    # body, so the filtered menu is a courtesy and this is the actual rule.
    if program in NAPLAN_PROGRAMS and year not in NAPLAN_YEARS:
        flash(f"NAPLAN is only sat in {', '.join(NAPLAN_YEARS[:-1])} and "
              f"{NAPLAN_YEARS[-1]}. Pick one of those, or choose "
              f"{PROGRAMS['accelerate'].label} for any other year.")
        return redirect(url_for("views.index"))
    if len(name) > 80:
        flash("Student names cannot be longer than 80 characters.")
        return redirect(url_for("views.index"))
    if len(topic) > 240:
        flash("Topic focus cannot be longer than 240 characters.")
        return redirect(url_for("views.index"))
    if is_exam:
        # Ignore a forged term-plan checkbox for exam products. The public UI
        # hides it, and one exam paper must never reserve 10 credits.
        is_term = False

    # A term plan is one request but TERM_WEEKS booklets, so it costs that
    # much of the budget and is charged that much of the quota.
    units = TERM_WEEKS if is_term else 1

    if not _quota_allows(g.user["id"], units):
        return redirect(url_for("views.index"))

    job_id = uuid.uuid4().hex
    # The student's name belongs in the label. Without it every Year 5 maths
    # booklet a tutor generates is called "Academic Accelerate - Year 5 -
    # Mathematics", so five students give five identical rows in My Booklets
    # and five downloads the browser quietly numbers (1), (2), (3). The name is
    # already collected and already printed on the cover.
    label = f"{PROGRAMS[program].label} - {year}" + (f" - {subject}" if subject else "")
    if name:
        label = f"{label} - {name}"
    if is_term:
        label = f"{label} (term plan)"
    args = dict(program=program, year=year, subject=subject or None,
                topic=topic or None, name=name, is_term=is_term,
                is_exam=is_exam)
    available, why = generation_is_available()
    if not available:
        log.error("refusing generation: the queue has no live worker (%s)", why)
        flash("Booklet generation is paused for maintenance right now. "
              "Nothing has been charged. Please try again shortly.")
        return redirect(url_for("views.index"))
    if not db.enqueue_job(
            job_id, g.user["id"], label, units, args,
            reserve_credits=payments_enabled(),
            daily_limit=DAILY_BOOKLET_LIMIT,
            global_daily_limit=GLOBAL_DAILY_BOOKLET_LIMIT):
        flash("You need more booklet credits for that selection.")
        return redirect(url_for("payments.pricing"))
    _dispatch_job(job_id, args)
    return redirect(url_for("views.progress", job_id=job_id))


def _run_job(job_id: str, _args: dict | None = None):
    """Compatibility wrapper for local inline execution and existing checks."""
    from ..jobs import run_job_by_id
    return run_job_by_id(job_id)


# How stale a worker heartbeat may be before the queue counts as unattended.
# The worker beats once per poll (2s by default), so this is generous.
WORKER_HEARTBEAT_MAX_AGE = int(
    os.environ.get("FOLIO_WORKER_HEARTBEAT_MAX_AGE", "120"))


def generation_is_available() -> tuple[bool, str]:
    """(ok, reason). False when queue mode has nothing behind it.

    In queue mode the web service only enqueues; a separate worker generates.
    With no worker running, every booklet sits at "generating" for ever, which
    is worse than an outage because the site looks like it is working and the
    customer's credit is already spent. Refusing up front costs them nothing
    and tells them the truth.
    """
    if JOB_MODE != "queue":
        return True, ""
    status = db.worker_status(WORKER_HEARTBEAT_MAX_AGE)["status"]
    if status == "healthy":
        return True, ""
    return False, status


def _dispatch_job(job_id: str, args: dict | None = None) -> None:
    if JOB_MODE == "queue":
        return
    threading.Thread(target=_run_job, args=(job_id, args), daemon=True).start()


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
    if job["status"] not in {"queued", "running"}:
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
    if any(j["status"] in {"queued", "running"}
           and int(j["created_at"]) < cutoff for j in jobs):
        try:
            db.fail_stale_running_jobs(JOB_TIMEOUT_SECONDS)
            jobs = db.list_jobs(g.user["id"])
        except Exception as e:
            log.warning("stale job sweep failed: %s", e)
    ratings = db.feedback_for_jobs(
        [j["id"] for j in jobs if j["status"] == "done"])
    return render_template("library.html", jobs=jobs, ratings=ratings,
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
        if stored["storage_key"]:
            try:
                from . import storage
                return redirect(storage.signed_download_url(stored["storage_key"]))
            except Exception as exc:
                log.exception("could not create stored-file download URL: %s", exc)
                return render_template(
                    "error.html",
                    heading="Your download is temporarily unavailable",
                    detail=("The booklet is safe, but FolioAI could not open its "
                            "download link. Please try again shortly."),
                ), 503
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


@bp.route("/retry/<job_id>", methods=["POST"])
@login_required
def retry(job_id: str):
    original = db.get_job(job_id)
    if (not original or original["user_id"] != g.user["id"]
            or original["status"] != "error" or not original["request_json"]):
        abort(404)
    try:
        args = json.loads(original["request_json"])
    except (TypeError, ValueError):
        flash("That job cannot be retried. Create a new booklet instead.")
        return redirect(url_for("views.index"))
    units = int(original["units"])
    if not _quota_allows(g.user["id"], units):
        return redirect(url_for("views.library"))
    new_id = uuid.uuid4().hex
    available, why = generation_is_available()
    if not available:
        log.error("refusing generation: the queue has no live worker (%s)", why)
        flash("Booklet generation is paused for maintenance right now. "
              "Nothing has been charged. Please try again shortly.")
        return redirect(url_for("views.index"))
    if not db.enqueue_job(
            new_id, g.user["id"], original["label"], units,
            args, reserve_credits=payments_enabled(),
            daily_limit=DAILY_BOOKLET_LIMIT,
            global_daily_limit=GLOBAL_DAILY_BOOKLET_LIMIT):
        flash("You need more booklet credits to retry that job.")
        return redirect(url_for("payments.pricing"))
    _dispatch_job(new_id, args)
    return redirect(url_for("views.progress", job_id=new_id))


# ---------- booklet feedback ----------
#
# The rating exists to open the comment box, not to be averaged. What the
# star actually buys is a follow-up question chosen to suit it: a 2 needs
# "what went wrong", a 3 needs "what would have made this a 5", and a 5 needs
# "what worked best". Three stars is the reading a thumbs-up/down cannot
# express, and for a product whose real risk is being dull rather than wrong,
# it is the most valuable answer on the page.

FEEDBACK_PROMPTS = {
    1: "What went wrong?",
    2: "What went wrong?",
    3: "What would have made this a 5?",
    4: "What worked best?",
    5: "What worked best?",
}
FEEDBACK_DEFAULT_PROMPT = "Anything you would change?"


def _redact_student_name(text: str, request_json) -> str:
    """Take the child's name back out of free text.

    SUPPORT_PLAYBOOK.md forbids a child's name in the support log and the form
    says so in as many words, but a parent typing "question 4 was too hard for
    Ella" is not reading form hints. Same discipline as the formatter's em
    dash stripper: ask for clean input, then enforce it anyway.
    """
    if not text or not request_json:
        return text
    try:
        name = (json.loads(request_json) or {}).get("name") or ""
    except (TypeError, ValueError):
        return text
    for part in str(name).split():
        # One-character fragments would only ever match a stray initial, and
        # "Student" is the placeholder used when the parent left it blank.
        if len(part) < 2 or part.lower() == "student":
            continue
        text = re.sub(rf"\b{re.escape(part)}\b", "[name]", text,
                      flags=re.IGNORECASE)
    return text


def _rateable_job(job_id: str):
    """The job, if this account may rate it. 404 otherwise.

    Ownership and `done` are both required: another account's booklet is not
    yours to rate, and a booklet that failed produced nothing to judge.
    """
    job = db.get_job(job_id)
    if not job or job["user_id"] != g.user["id"] or job["status"] != "done":
        abort(404)
    return job


@bp.route("/feedback/<job_id>")
@login_required
def feedback(job_id: str):
    job = _rateable_job(job_id)
    return render_template("feedback.html", job=job,
                           existing=db.get_feedback(job_id),
                           prompts=FEEDBACK_PROMPTS,
                           default_prompt=FEEDBACK_DEFAULT_PROMPT,
                           comment_max=db.COMMENT_MAX)


@bp.route("/feedback/<job_id>", methods=["POST"])
@login_required
def feedback_save(job_id: str):
    job = _rateable_job(job_id)
    enforce_rate_limit("feedback", 40, 900)
    try:
        rating = int((request.form.get("rating") or "").strip())
    except ValueError:
        rating = 0
    if not db.RATING_MIN <= rating <= db.RATING_MAX:
        flash("Please choose a star rating from 1 to 5.")
        return redirect(url_for("views.feedback", job_id=job_id))

    raw = job["request_json"]
    comment = _redact_student_name(
        (request.form.get("comment") or "").strip()[:db.COMMENT_MAX], raw)
    question_ref = _redact_student_name(
        (request.form.get("question_ref") or "").strip()[:db.QUESTION_REF_MAX],
        raw)
    db.save_feedback(job_id, g.user["id"], rating, question_ref, comment)
    log.info("feedback recorded: %s stars on job %s", rating, job_id)
    flash("Thanks. That is genuinely useful.")
    return redirect(url_for("views.library"))


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
        credits=db.credit_balance(g.user["id"]),
        payments=db.list_payments(g.user["id"]),
        payments_enabled=payments_enabled(),
    )


@bp.route("/account/export")
@login_required
def account_export():
    """Download everything held about this account as JSON."""
    payload = db.export_account(g.user["id"])
    buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
    return send_file(buf, as_attachment=True,
                     download_name="folioai-account-export.json",
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
