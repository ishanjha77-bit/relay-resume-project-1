"""Per-model token prices, for cost-per-incident accounting.

Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-04).
Cache reads are not a fixed fraction of the input price across models
(0.05x on Claude Opus 5.5, 0.1x on most others), so each price is listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from relay_agent.llm.types import Usage


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(4.00, 5.00, 8.00, 0.20, 20.00),
    "claude-sonnet-5-5": Price(2.00, 2.50, 4.00, 0.20, 10.00),
    "claude-haiku-4-5": Price(1.00, 1.25, 2.00, 0.10, 5.00),
    "claude-fable-5-1": Price(10.00, 12.50, 20.00, 0.25, 50.00),
    "claude-opus-5": Price(5.00, 6.25, 10.00, 0.50, 25.00),
    "claude-opus-4-8": Price(5.00, 6.25, 10.00, 0.50, 25.00),
    "claude-sonnet-5": Price(2.00, 2.50, 4.00, 0.20, 10.00),
}


_SNAPSHOT_SUFFIX = re.compile(r"-\d{8}$")


def price_for(model: str) -> Price | None:
    """Price by model ID; responses may name a dated snapshot (claude-haiku-4-5-20251001)."""
    return PRICES.get(model) or PRICES.get(_SNAPSHOT_SUFFIX.sub("", model))


def cost_usd(model: str, usage: Usage) -> float:
    """Cost of one call. Models without a listed price (Gemini's free tier, local
    or replayed runs) cost nothing; their tokens are still counted."""
    price = price_for(model)
    if price is None:
        return 0.0
    return (
        usage.input_tokens * price.input
        + usage.cache_write_5m_tokens * price.cache_write_5m
        + usage.cache_write_1h_tokens * price.cache_write_1h
        + usage.cache_read_tokens * price.cache_read
        + usage.output_tokens * price.output
    ) / 1_000_000
