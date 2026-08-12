"""Prove transactional email works, before a customer's account depends on it.

Without this the only way to test the mail path is to sign up on the live site
and wait, which is a bad way to discover that SMTP is misconfigured: with
FOLIO_REQUIRE_EMAIL_VERIFICATION=1 a broken mailer means nobody can complete a
signup at all, and the failure looks like a dead form rather than a mail
problem.

This connects for real and sends a real message. It never prints the password.

    PYTHONPATH=. python scripts/check_email_delivery.py you@example.com

Reads the same variables the app does, so a pass here means the app will send:
FOLIO_EMAIL_FROM, SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
SMTP_STARTTLS.
"""
from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

# Provider-specific failures are the ones worth translating: the raw SMTP error
# for "you used your account password instead of an app password" is a bare 535,
# which sends people to the wrong problem.
HINTS = {
    535: (
        "The server rejected the username or password.\n"
        "  If this is Gmail: an ordinary account password will always fail "
        "here.\n"
        "  You need 2-Step Verification switched on, then a 16-character app\n"
        "  password from https://myaccount.google.com/apppasswords, entered\n"
        "  with no spaces."
    ),
    534: (
        "The server wants an application-specific password.\n"
        "  For Gmail, generate one at "
        "https://myaccount.google.com/apppasswords."
    ),
    550: (
        "The server accepted the login but refused the sender.\n"
        "  FOLIO_EMAIL_FROM must be an address the account is allowed to send\n"
        "  as. Gmail will not let you send as a domain you have not verified."
    ),
}


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        print("\nUsage: python scripts/check_email_delivery.py <recipient>",
              file=sys.stderr)
        return 2
    recipient = sys.argv[1].strip()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    sender = (os.environ.get("FOLIO_EMAIL_FROM") or "").strip()
    host = (os.environ.get("SMTP_HOST") or "").strip()
    port = (os.environ.get("SMTP_PORT") or "587").strip()
    username = (os.environ.get("SMTP_USERNAME") or "").strip()
    password = os.environ.get("SMTP_PASSWORD") or ""
    starttls = (os.environ.get("SMTP_STARTTLS") or "1").strip().lower() not in {
        "0", "false", "no", ""}

    print("Configuration")
    print("-" * 62)
    rows = [
        ("FOLIO_EMAIL_FROM", sender or "(missing)"),
        ("SMTP_HOST", host or "(missing)"),
        ("SMTP_PORT", port),
        ("SMTP_USERNAME", username or "(missing)"),
        # Length only. A password is never printed, not even partially: a
        # transcript of this run should be safe to paste to anyone.
        ("SMTP_PASSWORD", f"set, {len(password)} characters" if password else "(missing)"),
        ("SMTP_STARTTLS", "on" if starttls else "off"),
    ]
    for k, v in rows:
        print(f"  {k:22} {v}")

    missing = [k for k, v in rows if v == "(missing)"]
    if missing:
        print(f"\nFAILED: {', '.join(missing)} not set.")
        print("Set them in your .env locally, or in Render's Environment tab.")
        return 1

    if password.strip() != password:
        print("\nWarning: SMTP_PASSWORD has leading or trailing whitespace.")
        print("Gmail app passwords are shown in groups of four; paste them with")
        print("no spaces at all.")

    print("\nDelivery")
    print("-" * 62)
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = "FolioAI test message"
    msg.set_content(
        "This is a test from scripts/check_email_delivery.py.\n\n"
        "If you are reading it, verification emails and password resets will "
        "reach customers.\n"
    )

    try:
        with smtplib.SMTP(host, int(port), timeout=25) as smtp:
            print(f"  connected to {host}:{port}")
            if starttls:
                smtp.starttls(context=ssl.create_default_context())
                print("  STARTTLS negotiated")
            if username:
                smtp.login(username, password)
                print("  authenticated")
            smtp.send_message(msg)
            print(f"  accepted for delivery to {recipient}")
    except smtplib.SMTPAuthenticationError as e:
        print(f"\nFAILED: authentication rejected ({e.smtp_code}).")
        print(HINTS.get(e.smtp_code, e.smtp_error.decode(errors="replace")
                        if isinstance(e.smtp_error, bytes) else str(e.smtp_error)))
        return 1
    except smtplib.SMTPResponseException as e:
        print(f"\nFAILED: server said {e.smtp_code}.")
        print(HINTS.get(e.smtp_code, str(e.smtp_error)))
        return 1
    except (OSError, ssl.SSLError) as e:
        print(f"\nFAILED: could not reach {host}:{port}. {e}")
        print("  Check the host and port, and that outbound SMTP is allowed.")
        return 1

    print("\nPASSED: the server accepted the message.")
    print("Check the inbox, and the spam folder. A message that is accepted")
    print("but lands in spam still fails the customer, and a plain Gmail")
    print("sender with no SPF or DKIM for your own domain is the usual cause.")
    print("\nOnly set FOLIO_REQUIRE_EMAIL_VERIFICATION=1 once this arrives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
