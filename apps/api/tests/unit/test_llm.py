from __future__ import annotations

import pytest

from docflow.ai.llm.base import Message, ModelSpec, ToolSchema
from docflow.ai.llm.pricing import (
    BATCH_MULTIPLIER,
    CACHE_READ_MULTIPLIER,
    compute_cost,
    estimate_tokens,
    price_for,
)
from docflow.ai.llm.stub_provider import StubProvider

# --------------------------------------------------------------------------
# model spec / capability detection
# --------------------------------------------------------------------------


def test_model_spec_parsing() -> None:
    spec = ModelSpec.parse("anthropic:claude-sonnet-5")
    assert (spec.provider, spec.model_id) == ("anthropic", "claude-sonnet-5")


def test_model_spec_rejects_bare_model_name() -> None:
    with pytest.raises(ValueError, match="provider:model"):
        ModelSpec.parse("claude-sonnet-5")


@pytest.mark.parametrize(
    ("model", "effort", "thinking"),
    [
        ("anthropic:claude-opus-5", True, True),
        ("anthropic:claude-sonnet-5", True, True),
        ("anthropic:claude-haiku-4-5", False, False),
    ],
)
def test_capability_detection(model: str, effort: bool, thinking: bool) -> None:
    """Passing `effort` or `temperature` to a model that rejects it is a 400,
    not a graceful degradation — so capability is explicit, never assumed."""
    spec = ModelSpec.parse(model)
    assert spec.supports_effort is effort
    assert spec.supports_thinking is thinking


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------


def test_known_model_pricing() -> None:
    assert price_for("claude-sonnet-5").input_per_mtok == 3.00
    assert price_for("claude-haiku-4-5").output_per_mtok == 5.00
    assert price_for("claude-opus-5").input_per_mtok == 5.00


def test_dated_snapshot_falls_back_to_family_price() -> None:
    """A dated model id must cost something, not silently zero."""
    assert price_for("claude-sonnet-5-20260801").input_per_mtok == 3.00


def test_cost_is_computed_per_million_tokens() -> None:
    cost = compute_cost("claude-sonnet-5", input_tokens=1_000_000, output_tokens=0)
    assert cost == pytest.approx(3.00)


def test_cached_reads_are_billed_at_a_tenth() -> None:
    """Folding cache reads into input_tokens would make the caching strategy
    invisible in the ledger, which is the main thing the ledger exists to show."""
    fresh = compute_cost("claude-sonnet-5", input_tokens=100_000)
    cached = compute_cost("claude-sonnet-5", cache_read_tokens=100_000)
    assert cached == pytest.approx(fresh * CACHE_READ_MULTIPLIER)


def test_cache_writes_cost_more_than_fresh_input() -> None:
    fresh = compute_cost("claude-sonnet-5", input_tokens=100_000)
    written = compute_cost("claude-sonnet-5", cache_write_tokens=100_000)
    assert written > fresh


def test_batch_api_halves_cost() -> None:
    normal = compute_cost("claude-sonnet-5", input_tokens=50_000, output_tokens=5_000)
    batched = compute_cost("claude-sonnet-5", input_tokens=50_000, output_tokens=5_000, batch=True)
    assert batched == pytest.approx(normal * BATCH_MULTIPLIER)


def test_ten_page_document_cost_is_in_the_expected_band() -> None:
    """Guards the blueprint's ~$0.065 estimate for a 10-page document.

    Assumptions: 650 tokens/page, 3 windows of 4 pages, 2.5k cached prompt read
    per window, ~700 output tokens per window.
    """
    extraction = compute_cost(
        "claude-sonnet-5",
        input_tokens=3 * 2600,
        cache_read_tokens=3 * 2500,
        output_tokens=3 * 700,
    )
    classify = compute_cost("claude-haiku-4-5", input_tokens=1380, output_tokens=150)
    merge = compute_cost("claude-haiku-4-5", input_tokens=2500, output_tokens=600)
    total = extraction + classify + merge
    assert 0.03 < total < 0.12, f"cost model drifted: {total:.4f}"


def test_token_estimate_over_counts_non_ascii() -> None:
    """A budget guard that under-counts is not a guard. Bengali tokenises worse
    than English, so it must estimate higher per character."""
    english = estimate_tokens("application deadline is fifteen september")
    bengali = estimate_tokens("আবেদনের শেষ তারিখ পনেরোই সেপ্টেম্বর")
    assert bengali > english


# --------------------------------------------------------------------------
# stub provider — the reason `make demo` runs with no API keys
# --------------------------------------------------------------------------

CIRCULAR = """[page 3]
University of Dhaka Admission Circular 2026.
Candidates must submit the completed application form by 15 September 2026.
Required documents include an academic transcript and a copy of the National ID card.
"""

EXTRACTION_TOOL = ToolSchema(
    name="extract",
    description="extract facts",
    json_schema={
        "type": "object",
        "properties": {
            "document_type": {"type": "string"},
            "language": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "facts": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["document_type", "language", "facts"],
    },
)


async def _extract(text: str = CIRCULAR) -> dict:
    response = await StubProvider().complete(
        model=ModelSpec.parse("stub:stub"),
        system="extract",
        messages=[Message(role="user", content=f"<document nonce='x'>{text}</document nonce='x'>")],
        tool=EXTRACTION_TOOL,
    )
    assert response.parsed is not None
    return response.parsed


async def test_stub_satisfies_the_requested_schema() -> None:
    parsed = await _extract()
    for key in EXTRACTION_TOOL.json_schema["required"]:
        assert key in parsed


async def test_stub_quotes_are_verbatim_so_alignment_really_runs() -> None:
    """The stub is a product feature, not a mock.

    Its quotes are lifted from the input so the citation path is genuinely
    exercised end to end rather than bypassed.
    """
    parsed = await _extract()
    assert parsed["facts"], "stub produced no facts for a document that clearly has some"
    for fact in parsed["facts"]:
        assert fact["source_quote"] in CIRCULAR


async def test_stub_finds_deadline_and_requirement() -> None:
    parsed = await _extract()
    kinds = {f["kind"] for f in parsed["facts"]}
    assert "deadline" in kinds
    assert "requirement" in kinds


async def test_stub_reads_the_page_marker() -> None:
    parsed = await _extract()
    assert all(f["source_page"] == 3 for f in parsed["facts"])


async def test_stub_is_free_and_flagged() -> None:
    """Stub responses must never be mistaken for real inference in the ledger."""
    response = await StubProvider().complete(
        model=ModelSpec.parse("stub:stub"),
        system="s",
        messages=[Message(role="user", content=CIRCULAR)],
    )
    assert response.cost_usd == 0.0
    assert response.is_stub is True
    assert response.model_id.startswith("stub:")


async def test_stub_embeddings_are_deterministic_and_unit_norm() -> None:
    provider = StubProvider()
    spec = ModelSpec.parse("stub:stub")
    first = await provider.embed(model=spec, texts=["alpha", "beta"], dimensions=64)
    second = await provider.embed(model=spec, texts=["alpha"], dimensions=64)

    assert len(first.vectors) == 2
    assert len(first.vectors[0]) == 64
    assert first.vectors[0] == second.vectors[0], "same text must give the same vector"
    assert first.vectors[0] != first.vectors[1]
    assert sum(v * v for v in first.vectors[0]) == pytest.approx(1.0, abs=1e-6)


async def test_stub_handles_empty_document_without_raising() -> None:
    parsed = await _extract("")
    assert parsed["facts"] == []
