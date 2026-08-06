"""Commercial configuration for FolioAI's one-time purchase model."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    units: int
    price_id: str
    display_price: str
    description: str


def products() -> dict[str, Product]:
    return {
        "single": Product(
            key="single",
            label="One practice booklet",
            units=1,
            price_id=(os.environ.get("STRIPE_PRICE_SINGLE") or "").strip(),
            display_price=(os.environ.get("FOLIO_PRICE_SINGLE_AUD") or "7.90").strip(),
            description="One personalised printable booklet with a worked answer key.",
        ),
        "term": Product(
            key="term",
            label="10-week term plan",
            units=10,
            price_id=(os.environ.get("STRIPE_PRICE_TERM") or "").strip(),
            display_price=(os.environ.get("FOLIO_PRICE_TERM_AUD") or "39.00").strip(),
            description="Ten progressively planned booklets in one ZIP download.",
        ),
    }


def stripe_secret_key() -> str:
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def stripe_webhook_secret() -> str:
    return (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()


def payments_enabled() -> bool:
    catalog = products()
    return bool(
        stripe_secret_key()
        and stripe_webhook_secret()
        and all(p.price_id for p in catalog.values())
    )


def payments_required() -> bool:
    return _truthy("FOLIO_REQUIRE_PAYMENTS")


def validate_commerce_config() -> None:
    if payments_required() and not payments_enabled():
        raise RuntimeError(
            "FOLIO_REQUIRE_PAYMENTS is enabled, but Stripe is not fully "
            "configured. Set STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, "
            "STRIPE_PRICE_SINGLE and STRIPE_PRICE_TERM."
        )
