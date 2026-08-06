#!/usr/bin/env python3
"""Audit FolioAI production settings without contacting external services.

Examples in PowerShell:

    python scripts/launch_readiness.py --stage beta --env-file .env
    python scripts/launch_readiness.py --stage live --env-file .env.production

Only setting names and pass/fail explanations are printed. Secret values are
never included in the report.
"""
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
PUBLISHED_SECRET_VALUES = frozenset({
    "dev-insecure-change-me",
    "change-me-to-a-long-random-string",
    "change-me",
    "changeme",
    "dev",
    "development",
    "secret",
    "test",
    "folio",
    "replace-with-at-least-32-random-characters",
})


@dataclass(frozen=True)
class Finding:
    level: str
    setting: str
    message: str


def _value(env: dict[str, str], key: str) -> str:
    return (env.get(key) or "").strip()


def _truthy(env: dict[str, str], key: str) -> bool:
    return _value(env, key).casefold() in TRUE_VALUES


def _present(findings: list[Finding], env: dict[str, str], key: str,
             explanation: str) -> str:
    value = _value(env, key)
    findings.append(Finding(
        "PASS" if value else "FAIL",
        key,
        "configured" if value else explanation,
    ))
    return value


def _required_true(findings: list[Finding], env: dict[str, str], key: str,
                   explanation: str) -> None:
    enabled = _truthy(env, key)
    findings.append(Finding(
        "PASS" if enabled else "FAIL",
        key,
        "enabled" if enabled else explanation,
    ))


def audit(env: dict[str, str], stage: str) -> list[Finding]:
    """Return a deterministic beta or live launch configuration report."""
    if stage not in {"beta", "live"}:
        raise ValueError("stage must be beta or live")
    findings: list[Finding] = []

    secret = _value(env, "FLASK_SECRET_KEY")
    secret_ok = len(secret) >= 32 and secret.casefold() not in PUBLISHED_SECRET_VALUES
    findings.append(Finding(
        "PASS" if secret_ok else "FAIL",
        "FLASK_SECRET_KEY",
        "strong value configured" if secret_ok
        else "use at least 32 random characters and not a published placeholder",
    ))

    _present(findings, env, "GEMINI_API_KEY", "required for generation")
    database_url = _present(
        findings, env, "DATABASE_URL", "required for durable accounts and queued jobs"
    )
    if database_url:
        postgres = urlparse(database_url).scheme in {"postgres", "postgresql"}
        findings.append(Finding(
            "PASS" if postgres else "FAIL",
            "DATABASE_URL",
            "uses PostgreSQL" if postgres else "must be a PostgreSQL connection URL",
        ))
    _required_true(findings, env, "FOLIO_REQUIRE_POSTGRES",
                   "set to 1 so production cannot fall back to SQLite")

    public_url = _present(findings, env, "FOLIO_PUBLIC_URL",
                          "required for email, Stripe, and customer links")
    if public_url:
        parsed = urlparse(public_url)
        public = parsed.scheme == "https" and bool(parsed.netloc) \
            and parsed.hostname not in {"localhost", "127.0.0.1"}
        findings.append(Finding(
            "PASS" if public else "FAIL",
            "FOLIO_PUBLIC_URL",
            "is a public HTTPS URL" if public else "must be a public HTTPS URL",
        ))

    for key, explanation in (
        ("FOLIO_BUSINESS_NAME", "enter the seller's real business or trading name"),
        ("FOLIO_BUSINESS_NUMBER", "enter the applicable ABN or business number"),
        ("FOLIO_BUSINESS_ADDRESS", "enter the public business contact address"),
        ("FOLIO_SUPPORT_EMAIL", "enter a monitored support address"),
        ("FOLIO_ADMIN_EMAILS", "enter at least one administrator login email"),
    ):
        value = _present(findings, env, key, explanation)
        if value and ("example.com" in value.casefold() or "yourdomain" in value.casefold()):
            findings.append(Finding("FAIL", key, "replace the example value"))

    queue_mode = _value(env, "FOLIO_JOB_MODE").casefold()
    findings.append(Finding(
        "PASS" if queue_mode == "queue" else "FAIL",
        "FOLIO_JOB_MODE",
        "queued worker mode enabled" if queue_mode == "queue"
        else "set to queue for the separate production worker",
    ))
    _required_true(findings, env, "FOLIO_COOKIE_SECURE",
                   "set to 1 for HTTPS-only session cookies")

    _required_true(findings, env, "FOLIO_REQUIRE_PAYMENTS",
                   "set to 1 before accepting paid generation")
    stripe_key = _present(findings, env, "STRIPE_SECRET_KEY",
                          "required for Stripe Checkout")
    _present(findings, env, "STRIPE_WEBHOOK_SECRET",
             "required for authenticated Stripe fulfilment")
    _present(findings, env, "STRIPE_PRICE_SINGLE", "create the single-booklet price")
    _present(findings, env, "STRIPE_PRICE_TERM", "create the term-plan price")
    if stripe_key:
        is_test = stripe_key.startswith("sk_test_")
        is_live = stripe_key.startswith("sk_live_")
        expected = is_test if stage == "beta" else is_live
        findings.append(Finding(
            "PASS" if expected else "FAIL",
            "STRIPE_SECRET_KEY",
            f"matches {stage} mode" if expected
            else f"must use a {'test' if stage == 'beta' else 'live'} Stripe secret key",
        ))

    _required_true(findings, env, "FOLIO_REQUIRE_EMAIL_VERIFICATION",
                   "set to 1 before public signups")
    for key, explanation in (
        ("FOLIO_EMAIL_FROM", "required for verification and reset messages"),
        ("SMTP_HOST", "required for transactional email"),
        ("SMTP_USERNAME", "required for transactional email"),
        ("SMTP_PASSWORD", "required for transactional email"),
    ):
        value = _present(findings, env, key, explanation)
        if value and "example.com" in value.casefold():
            findings.append(Finding("FAIL", key, "replace the example value"))

    for key, explanation in (
        ("SUPABASE_URL", "required for durable private file storage"),
        ("SUPABASE_SERVICE_ROLE_KEY", "required for server-side private storage"),
        ("FOLIO_STORAGE_BUCKET", "required for the private booklet bucket"),
    ):
        _present(findings, env, key, explanation)

    for key in ("FOLIO_PRICE_SINGLE_AUD", "FOLIO_PRICE_TERM_AUD"):
        value = _value(env, key)
        valid = bool(re.fullmatch(r"\d+(?:\.\d{1,2})?", value)) \
            and float(value) > 0
        findings.append(Finding(
            "PASS" if valid else "FAIL",
            key,
            "valid positive display price" if valid
            else "enter a positive AUD amount with at most two decimal places",
        ))

    return findings


def read_env_file(path: Path) -> dict[str, str]:
    """Read the simple KEY=VALUE format used by the project env example."""
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("beta", "live"), default="beta")
    parser.add_argument("--env-file", type=Path, default=None,
                        help="Optional env file. Current process values override it.")
    args = parser.parse_args()

    env: dict[str, str] = {}
    if args.env_file:
        if not args.env_file.is_file():
            print(f"FAIL  env file not found: {args.env_file}")
            return 2
        env.update(read_env_file(args.env_file))
    env.update(os.environ)

    findings = audit(env, args.stage)
    print(f"FolioAI {args.stage} launch configuration")
    print("-" * 72)
    for finding in findings:
        print(f"{finding.level:<5} {finding.setting:<34} {finding.message}")
    failed = sum(f.level == "FAIL" for f in findings)
    print("-" * 72)
    print(f"{len(findings) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
