"""Checks for the launch readiness board.

This script is the thing that tells the founder whether the deployment is
configured, so its own verdicts have to be right. The case that matters most
is the one that used to be impossible: an Australian sole trader selling as an
individual, with no ABN, must be able to reach a clean board. Stripe registers
sellers on exactly those terms and the legal pages already omit the line when
it is unset, so a hard FAIL there was telling a correctly configured seller
they were not ready.

    PYTHONPATH=. python scripts/check_launch_readiness.py
"""
import sys

from scripts.launch_readiness import audit

_passed = 0


def ok(msg):
    global _passed
    _passed += 1
    print("  ok:", msg)


CONFIGURED = {
    "FLASK_SECRET_KEY": "z" * 48,
    "GEMINI_API_KEY": "a-real-looking-key",
    "DATABASE_URL": "postgresql://user:pass@host/db",
    "FOLIO_REQUIRE_POSTGRES": "1",
    "FOLIO_PUBLIC_URL": "https://folio-45rh.onrender.com",
    "FOLIO_BUSINESS_NAME": "A Real Trading Name",
    "FOLIO_BUSINESS_ADDRESS": "1 Somewhere Street, Perth WA",
    "FOLIO_SUPPORT_EMAIL": "support@folio.test.au",
    "FOLIO_ADMIN_EMAILS": "owner@folio.test.au",
    "FOLIO_JOB_MODE": "queue",
    "FOLIO_COOKIE_SECURE": "1",
    "FOLIO_REQUIRE_PAYMENTS": "1",
    "STRIPE_SECRET_KEY": "sk_test_abc",
    "STRIPE_WEBHOOK_SECRET": "whsec_abc",
    "STRIPE_PRICE_SINGLE": "price_single",
    "STRIPE_PRICE_TERM": "price_term",
    "FOLIO_REQUIRE_EMAIL_VERIFICATION": "1",
    "FOLIO_EMAIL_FROM": "no-reply@folio.test.au",
    "SMTP_HOST": "smtp.provider.test",
    "SMTP_USERNAME": "smtp-user",
    "SMTP_PASSWORD": "smtp-pass",
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
    "FOLIO_STORAGE_BUCKET": "booklets",
    "FOLIO_PRICE_SINGLE_AUD": "6.90",
    "FOLIO_PRICE_TERM_AUD": "36.00",
}


def failures(env, stage="beta"):
    return [f for f in audit(dict(env), stage) if f.level == "FAIL"]


def without(*keys):
    return {k: v for k, v in CONFIGURED.items() if k not in keys}


print("\nSELLING WITHOUT AN ABN")

bad = failures(CONFIGURED)
assert not bad, [f"{f.setting}: {f.message}" for f in bad]
ok("a fully configured seller with no ABN reaches a clean board")

numbered = failures({**CONFIGURED, "FOLIO_BUSINESS_NUMBER": "12 345 678 901"})
assert not numbered, [f.setting for f in numbered]
ok("supplying an ABN is still accepted")

placeholder = failures({**CONFIGURED,
                        "FOLIO_BUSINESS_NUMBER": "your-abn-here"})
assert any(f.setting == "FOLIO_BUSINESS_NUMBER" for f in placeholder), placeholder
ok("an unreplaced ABN placeholder is still caught")

note = [f for f in audit(dict(CONFIGURED), "beta")
        if f.setting == "FOLIO_BUSINESS_NUMBER"]
assert note and "without an ABN" in note[0].message, note
ok("the board says why the field is blank rather than going quiet")

print("\nEVERYTHING ELSE STILL BLOCKS")

# The ABN change must not have loosened any neighbouring requirement.
for key in ("FOLIO_BUSINESS_NAME", "FOLIO_BUSINESS_ADDRESS",
            "FOLIO_SUPPORT_EMAIL", "FOLIO_ADMIN_EMAILS", "SMTP_HOST",
            "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
            "SUPABASE_SERVICE_ROLE_KEY", "GEMINI_API_KEY", "DATABASE_URL"):
    missing = failures(without(key))
    assert any(f.setting == key for f in missing), \
        f"removing {key} no longer fails the board"
ok("every other required setting still fails the board when missing")

for key in ("FOLIO_REQUIRE_POSTGRES", "FOLIO_COOKIE_SECURE",
            "FOLIO_REQUIRE_PAYMENTS", "FOLIO_REQUIRE_EMAIL_VERIFICATION"):
    off = failures({**CONFIGURED, key: "0"})
    assert any(f.setting == key for f in off), f"{key}=0 was accepted"
ok("the safety switches must be on, not merely present")

placeholder_secret = failures({**CONFIGURED,
                               "FLASK_SECRET_KEY": "dev-insecure-change-me"})
assert any(f.setting == "FLASK_SECRET_KEY" for f in placeholder_secret)
ok("a published placeholder secret key is refused")

inline = failures({**CONFIGURED, "FOLIO_JOB_MODE": "inline"})
assert any(f.setting == "FOLIO_JOB_MODE" for f in inline)
ok("inline job mode is refused for a deployment with a worker")

print("\nTHE ADVERTISED PRICES")

for bad_price in ("0", "-1", "6.905", "six ninety", ""):
    priced = failures({**CONFIGURED, "FOLIO_PRICE_SINGLE_AUD": bad_price})
    assert any(f.setting == "FOLIO_PRICE_SINGLE_AUD" for f in priced), bad_price
ok("a zero, negative, over-precise, empty or non-numeric price is refused")

print(f"\nALL {_passed} LAUNCH READINESS CHECKS PASSED")
sys.exit(0)
