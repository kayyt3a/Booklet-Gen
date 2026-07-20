"""Pricing. All money values in AUD cents.

The product sells at $30 per single booklet, or $240 for a full 10-week term
(which is 10 x $30 = $300, so the term saves 20%). Credits are the internal
unit: 1 credit = 1 booklet, and a term costs 10 credits but is sold at the
discounted bundle price.
"""
from __future__ import annotations

from dataclasses import dataclass

CREDITS_PER_BOOKLET = 1
TERM_WEEKS = 10

SINGLE_BOOKLET_CENTS = 3000     # $30.00
TERM_BUNDLE_CENTS = 24000       # $240.00 (20% off 10 x $30)


def term_plan_cost(weeks: int) -> int:
    """A term plan costs one credit per week."""
    return max(1, weeks)


@dataclass(frozen=True)
class CreditPack:
    key: str
    name: str
    credits: int
    price_cents: int      # AUD cents
    blurb: str

    @property
    def price_display(self) -> str:
        return f"${self.price_cents / 100:.0f}"

    @property
    def per_booklet_display(self) -> str:
        return f"${self.price_cents / 100 / self.credits:.2f} per booklet"


# Two clear options, matching the pricing the parent sees on the site.
CREDIT_PACKS: dict[str, CreditPack] = {
    "single": CreditPack(
        "single", "Single Booklet", 1, SINGLE_BOOKLET_CENTS,
        "One booklet. $30 each.",
    ),
    "term": CreditPack(
        "term", "Full Term (10 booklets)", TERM_WEEKS, TERM_BUNDLE_CENTS,
        "A whole term's booklets. Save 20% versus buying singles.",
    ),
}


def get_pack(key: str) -> CreditPack | None:
    return CREDIT_PACKS.get(key)
