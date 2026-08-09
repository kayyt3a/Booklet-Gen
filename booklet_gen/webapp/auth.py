"""Signup / login / logout. Session-based; passwords hashed via werkzeug."""
from __future__ import annotations

import functools
import logging
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash, g,
)

from . import db
from . import mailer
from .security import enforce_rate_limit, rotate_csrf_token, safe_redirect_target

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.before_app_request
def load_user():
    uid = session.get("user_id")
    g.user = db.get_user(uid) if uid else None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _start_session(user_id: int) -> None:
    """Log a user in on a clean session.

    Clearing first means a session id handed to the visitor before login (by
    someone else, in a session fixation attempt) cannot carry over into the
    authenticated session, and the CSRF token is re-minted with the new
    identity.
    """
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    rotate_csrf_token()


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        enforce_rate_limit("signup", 8, 3600)
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not _EMAIL_RE.match(email):
            flash("Please enter a valid email address.")
        elif len(email) > 254:
            flash("Email addresses cannot be longer than 254 characters.")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif len(password) > 256:
            flash("Password cannot be longer than 256 characters.")
        elif db.get_user_by_email(email):
            flash("An account with that email already exists. Try logging in.")
        else:
            needs_verification = mailer.verification_required()
            uid = db.create_user(
                email, password, email_verified=not needs_verification,
            )
            if needs_verification:
                try:
                    mailer.send_verification(db.get_user(uid))
                    return render_template("verify_sent.html", email=email)
                except Exception:
                    log.exception("could not send verification email")
                    flash("Your account was created, but the verification email "
                          "could not be sent. Try resending it below.")
                    return redirect(url_for("auth.resend_verification"))
            _start_session(uid)
            return redirect(url_for("views.index"))
    return render_template("signup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        enforce_rate_limit("login", 20, 900)
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if len(email) > 254 or len(password) > 256:
            flash("Incorrect email or password.")
            return render_template("login.html")
        user = db.verify_login(email, password)
        if user:
            if mailer.verification_required() and not bool(user["email_verified"]):
                try:
                    mailer.send_verification(user)
                except Exception:
                    log.exception("could not resend verification email during login")
                flash("Verify your email before logging in. We sent a new link if "
                      "email delivery is available.")
                return redirect(url_for("auth.resend_verification"))
            # `next` comes from the query string of a link someone may have
            # sent the user, so it is attacker controlled. Only same-site
            # relative paths are honoured; anything else lands on the home
            # page rather than turning login into an open redirect.
            nxt = safe_redirect_target(
                request.args.get("next"), url_for("views.index"))
            _start_session(user["id"])
            return redirect(nxt)
        flash("Incorrect email or password.")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("views.index"))


@bp.route("/verify/<token>")
def verify_email(token: str):
    payload = mailer.read_token("verify", token, max_age=86400)
    if payload is None:
        flash("That verification link is invalid or has expired.")
        return redirect(url_for("auth.resend_verification"))
    user = db.get_user(int(payload.get("uid", 0)))
    if user is None or user["email"] != payload.get("email"):
        flash("That verification link is not valid.")
        return redirect(url_for("auth.resend_verification"))
    if bool(user["email_verified"]):
        flash("That email is already verified. Log in to continue.")
        return redirect(url_for("auth.login"))
    db.mark_email_verified(user["id"])
    _start_session(user["id"])
    flash("Your email is verified. Your free booklet credit is ready.")
    return redirect(url_for("views.index"))


@bp.route("/verify/resend", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        enforce_rate_limit("verify-resend", 6, 3600)
        email = (request.form.get("email") or "").strip().lower()
        user = db.get_user_by_email(email)
        if user is not None and not bool(user["email_verified"]):
            try:
                mailer.send_verification(user)
            except Exception:
                log.exception("could not resend verification email")
        flash("If that unverified account exists, a new link has been sent.")
    return render_template("resend_verification.html")


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        enforce_rate_limit("password-reset", 6, 3600)
        email = (request.form.get("email") or "").strip().lower()
        user = db.get_user_by_email(email)
        if user is not None:
            try:
                mailer.send_password_reset(user)
            except Exception:
                log.exception("could not send password reset email")
        flash("If that account exists, a password-reset link has been sent.")
    return render_template("forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token: str):
    payload = mailer.read_token("reset", token, max_age=3600)
    user = db.get_user(int(payload.get("uid", 0))) if payload else None
    valid = bool(
        user is not None
        and payload.get("marker") == str(user["password_hash"])[-16:]
    )
    if not valid:
        flash("That password-reset link is invalid or has expired.")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        enforce_rate_limit("password-change", 10, 3600)
        password = request.form.get("password") or ""
        if len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif len(password) > 256:
            flash("Password cannot be longer than 256 characters.")
        else:
            db.update_password(user["id"], password)
            _start_session(user["id"])
            flash("Your password has been updated.")
            return redirect(url_for("views.index"))
    return render_template("reset_password.html", token=token)
