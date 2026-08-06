#!/usr/bin/env python3
"""Check the offline beta and live configuration auditor."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "launch_readiness", Path("scripts/launch_readiness.py")
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = module
spec.loader.exec_module(module)

failures: list[str] = []


def check(ok: bool, label: str) -> None:
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        failures.append(label)


def complete_env(stripe_key: str) -> dict[str, str]:
    return {
        "FLASK_SECRET_KEY": "a" * 48,
        "GEMINI_API_KEY": "configured-secret",
        "DATABASE_URL": "postgresql://user:password@database.example/db",
        "FOLIO_REQUIRE_POSTGRES": "1",
        "FOLIO_PUBLIC_URL": "https://folio.example.au",
        "FOLIO_BUSINESS_NAME": "Folio Learning Pty Ltd",
        "FOLIO_BUSINESS_NUMBER": "ABN configured",
        "FOLIO_BUSINESS_ADDRESS": "Public contact address configured",
        "FOLIO_SUPPORT_EMAIL": "support@folio.example.au",
        "FOLIO_ADMIN_EMAILS": "admin@folio.example.au",
        "FOLIO_JOB_MODE": "queue",
        "FOLIO_COOKIE_SECURE": "1",
        "FOLIO_REQUIRE_PAYMENTS": "1",
        "STRIPE_SECRET_KEY": stripe_key,
        "STRIPE_WEBHOOK_SECRET": "configured-webhook-secret",
        "STRIPE_PRICE_SINGLE": "configured-single-price",
        "STRIPE_PRICE_TERM": "configured-term-price",
        "FOLIO_REQUIRE_EMAIL_VERIFICATION": "1",
        "FOLIO_EMAIL_FROM": "FolioAI <hello@folio.example.au>",
        "SMTP_HOST": "smtp.folio.example.au",
        "SMTP_USERNAME": "configured-user",
        "SMTP_PASSWORD": "configured-password",
        "SUPABASE_URL": "https://project.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "configured-service-role-key",
        "FOLIO_STORAGE_BUCKET": "booklets",
        "FOLIO_PRICE_SINGLE_AUD": "7.90",
        "FOLIO_PRICE_TERM_AUD": "39.00",
    }


def failed_settings(findings) -> set[str]:
    return {finding.setting for finding in findings if finding.level == "FAIL"}


print("\nA complete beta configuration passes offline checks")
print("-" * 68)
beta = module.audit(complete_env("sk_test_configured"), "beta")
check(not failed_settings(beta), "test-mode Stripe settings pass the beta audit")

print("\nLive mode rejects test credentials")
print("-" * 68)
wrong_live = module.audit(complete_env("sk_test_configured"), "live")
check("STRIPE_SECRET_KEY" in failed_settings(wrong_live),
      "a test Stripe secret cannot pass the live audit")
live = module.audit(complete_env("sk_live_configured"), "live")
check(not failed_settings(live), "a complete live configuration passes")

print("\nSecurity and identity values fail closed")
print("-" * 68)
broken = complete_env("sk_test_configured")
broken.update({
    "FLASK_SECRET_KEY": "replace-with-at-least-32-random-characters",
    "DATABASE_URL": "sqlite:///folio.db",
    "FOLIO_PUBLIC_URL": "http://127.0.0.1:5000",
    "FOLIO_SUPPORT_EMAIL": "support@example.com",
    "FOLIO_JOB_MODE": "inline",
    "FOLIO_COOKIE_SECURE": "0",
    "FOLIO_PRICE_SINGLE_AUD": "free",
})
failed = failed_settings(module.audit(broken, "beta"))
for setting in (
    "FLASK_SECRET_KEY",
    "DATABASE_URL",
    "FOLIO_PUBLIC_URL",
    "FOLIO_SUPPORT_EMAIL",
    "FOLIO_JOB_MODE",
    "FOLIO_COOKIE_SECURE",
    "FOLIO_PRICE_SINGLE_AUD",
):
    check(setting in failed, f"{setting} is rejected when unsafe or incomplete")

source = Path("scripts/launch_readiness.py").read_text(encoding="utf-8")
check("print(secret" not in source and "print(stripe_key" not in source,
      "the report does not print secret values")

if failures:
    print(f"\n{len(failures)} FAILED")
    raise SystemExit(1)
print("\nALL LAUNCH-READINESS CHECKS PASSED")
