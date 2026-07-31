"""Signup / login / logout. Session-based; passwords hashed via werkzeug."""
from __future__ import annotations

import functools
import re

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash, g,
)

from . import db
from .security import rotate_csrf_token, safe_redirect_target

bp = Blueprint("auth", __name__)

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
    session["user_id"] = user_id
    rotate_csrf_token()


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not _EMAIL_RE.match(email):
            flash("Please enter a valid email address.")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.")
        elif db.get_user_by_email(email):
            flash("An account with that email already exists. Try logging in.")
        else:
            uid = db.create_user(email, password)
            _start_session(uid)
            return redirect(url_for("views.index"))
    return render_template("signup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = db.verify_login(email, password)
        if user:
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


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("views.index"))
