"""Credit pricing and pack definitions. All money values in AUD cents.

Credits are the unit of value: 1 credit = 1 single booklet. A 10-week term
plan costs fewer credits per booklet than buying singles (the bundle
incentive). Tune these freely; nothing else hard-codes the numbers.
"""
from __future__ import annotations

from dataclasses import dataclass

# How many credits each kind of generation costs.
CREDITS_PER_BOOKLET = 1


def term_plan_cost(weeks: int) -> int:
    """A term plan is billed at ~0.8 credits per week (a bundle discount),
    rounded up, minimum equal to the number of weeks' worth minus a little."""
    return max(1, round(weeks * 0.8))


@dataclass(frozen=True)
class CreditPack:
    key: str
    name: str
    credits: int
    price_cents: int      # AUD cents
    blurb: str

    @property
    def price_display(self) -> str:
        return f"${self.price_cents / 100:.2f}"


# Sell credits in packs. Bigger packs are better value.
CREDIT_PACKS: dict[str, CreditPack] = {
    "starter": CreditPack("starter", "Starter", 5, 900,
                          "5 booklets. Try it out."),
    "family": CreditPack("family", "Family", 15, 2400,
                         "15 booklets. Best for ongoing practice."),
    "term": CreditPack("term", "Term Bundle", 40, 5900,
                       "40 booklets. Enough for a full term plan with room to spare."),
}


def get_pack(key: str) -> CreditPack | None:
    return CREDIT_PACKS.get(key)
