"""Small SMTP mailer for verification and password-recovery messages."""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from flask import current_app, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

log = logging.getLogger(__name__)


def _truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def mail_enabled() -> bool:
    return bool(
        (os.environ.get("SMTP_HOST") or "").strip()
        and (os.environ.get("FOLIO_EMAIL_FROM") or "").strip()
    )


def verification_required() -> bool:
    return _truthy("FOLIO_REQUIRE_EMAIL_VERIFICATION")


def validate_mail_config() -> None:
    if verification_required() and not mail_enabled():
        raise RuntimeError(
            "FOLIO_REQUIRE_EMAIL_VERIFICATION is enabled, but SMTP_HOST or "
            "FOLIO_EMAIL_FROM is missing."
        )


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def make_token(purpose: str, payload: dict) -> str:
    return _serializer().dumps(payload, salt=f"folio-{purpose}")


def read_token(purpose: str, token: str, max_age: int) -> dict | None:
    try:
        value = _serializer().loads(
            token, salt=f"folio-{purpose}", max_age=max_age,
        )
        return value if isinstance(value, dict) else None
    except (BadSignature, SignatureExpired):
        return None


def _send(to_email: str, subject: str, body: str) -> None:
    if not mail_enabled():
        raise RuntimeError("Email delivery is not configured.")
    msg = EmailMessage()
    msg["From"] = os.environ["FOLIO_EMAIL_FROM"].strip()
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    host = os.environ["SMTP_HOST"].strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = (os.environ.get("SMTP_USERNAME") or "").strip()
    password = os.environ.get("SMTP_PASSWORD") or ""
    use_tls = _truthy("SMTP_STARTTLS", "1")
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        if use_tls:
            smtp.starttls()
        if username:
            smtp.login(username, password)
        smtp.send_message(msg)


def send_verification(user) -> None:
    token = make_token("verify", {"uid": int(user["id"]), "email": user["email"]})
    link = url_for("auth.verify_email", token=token, _external=True)
    _send(
        user["email"],
        "Verify your FolioAI email",
        "Welcome to FolioAI. Verify your email address using this link:\n\n"
        f"{link}\n\nThis link expires in 24 hours. If you did not create the "
        "account, you can ignore this message.",
    )


def send_password_reset(user) -> None:
    marker = str(user["password_hash"])[-16:]
    token = make_token("reset", {"uid": int(user["id"]), "marker": marker})
    link = url_for("auth.reset_password", token=token, _external=True)
    _send(
        user["email"],
        "Reset your FolioAI password",
        "Use this link to choose a new FolioAI password:\n\n"
        f"{link}\n\nThis link expires in one hour. If you did not request a "
        "reset, you can ignore this message.",
    )
