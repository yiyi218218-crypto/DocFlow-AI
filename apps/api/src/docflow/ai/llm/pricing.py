"""Model pricing, in USD per 1M tokens.

Every LLM call writes a `usage_records` row with a computed cost. That one
append-only table powers the cost dashboard, per-workspace budgets, the eval cost
report and future metered billing — so the numbers here need to be right and
need to be easy to update.

Prices as published for the Anthropic first-party API, August 2026. Partner
platforms (Bedrock, Vertex) bill separately. VERIFY BEFORE RELYING ON THESE FOR
BILLING — provider pricing changes and this table is a cache, not a source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass

# Anthropic prompt-cache multipliers, applied to the base input rate.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

# The Batch API runs asynchronously with a 24h SLA at half price. Worth routing
# documents over ~50 pages through it.
BATCH_MULTIPLIER = 0.5


@dataclass(frozen=True, slots=True)
class Price:
    input_per_mtok: float
    output_per_mtok: float


PRICING: dict[str, Price] = {
    # Anthropic
    "claude-opus-5": Price(5.00, 25.00),
    "claude-opus-4-8": Price(5.00, 25.00),
    "claude-opus-4-7": Price(5.00, 25.00),
    "claude-opus-4-6": Price(5.00, 25.00),
    "claude-sonnet-5": Price(3.00, 15.00),
    "claude-sonnet-4-6": Price(3.00, 15.00),
    "claude-haiku-4-5": Price(1.00, 5.00),
    "claude-fable-5": Price(10.00, 50.00),
    # OpenAI embeddings
    "text-embedding-3-small": Price(0.02, 0.0),
    "text-embedding-3-large": Price(0.13, 0.0),
    # Local / stub
    "stub": Price(0.0, 0.0),
}

# Non-token costs, for the same ledger.
CLOUD_OCR_PER_PAGE = 0.0015


def price_for(model_id: str) -> Price:
    if model_id in PRICING:
        return PRICING[model_id]
    # Prefix match so a dated snapshot still costs something rather than zero.
    for known, price in PRICING.items():
        if model_id.startswith(known):
            return price
    # Unknown model: charge nothing rather than guess, but the caller records the
    # model id so a zero-cost row with a real model is a visible anomaly.
    return Price(0.0, 0.0)


def compute_cost(
    model_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    batch: bool = False,
) -> float:
    """Cost of one call in USD.

    Cached tokens are billed separately from fresh input: a cache read is ~10% of
    the input rate and a write ~125%. Folding them into input_tokens would make
    the caching strategy invisible in the ledger, which is the main thing the
    ledger exists to show.
    """
    price = price_for(model_id)
    million = 1_000_000.0

    cost = (
        input_tokens * price.input_per_mtok
        + output_tokens * price.output_per_mtok
        + cache_read_tokens * price.input_per_mtok * CACHE_READ_MULTIPLIER
        + cache_write_tokens * price.input_per_mtok * CACHE_WRITE_MULTIPLIER
    ) / million

    if batch:
        cost *= BATCH_MULTIPLIER
    return round(cost, 8)


def estimate_tokens(text: str) -> int:
    """Rough token estimate for budget pre-checks, before a call is made.

    ~3.6 chars/token for mixed English, lower for Bengali which tokenises worse.
    Deliberately an over-estimate: a budget guard that under-counts is useless.
    """
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other = len(text) - ascii_chars
    return int(ascii_chars / 3.6 + other / 1.8) + 1
