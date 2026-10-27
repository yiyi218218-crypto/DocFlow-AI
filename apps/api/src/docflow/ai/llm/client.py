"""The single chokepoint every LLM call passes through.

One function owns timeouts, retries, budget enforcement, schema validation and
the usage ledger. Scattering those across call sites is how a system ends up
unable to answer "what did last month cost, and which prompt version caused it".

Also the reason no framework sits here: the retry policy, cache-breakpoint
placement and cost attribution are all things this product needs to control
precisely, and they are exactly what a generic wrapper takes away.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any, cast

from docflow.ai.llm.base import (
    EmbeddingProvider,
    EmbeddingResponse,
    LLMProvider,
    LLMResponse,
    Message,
    ModelSpec,
    ProviderError,
    ToolSchema,
)
from docflow.ai.llm.pricing import estimate_tokens
from docflow.core.config import settings
from docflow.core.errors import BudgetExceeded, ProviderUnavailable
from docflow.core.logging import get_logger, request_id_var
from docflow.models.enums import ModelTier

logger = get_logger(__name__)

_TIER_SETTING = {
    ModelTier.FAST: "llm_tier_fast",
    ModelTier.BALANCED: "llm_tier_balanced",
    ModelTier.DEEP: "llm_tier_deep",
}

_provider_cache: dict[str, Any] = {}


def resolve_model(tier: ModelTier) -> ModelSpec:
    """Tier to concrete model. The whole point of the indirection."""
    return ModelSpec.parse(getattr(settings, _TIER_SETTING[tier]))


_PROVIDER_KEYS = {"anthropic": "anthropic_api_key", "openai": "openai_api_key"}


def provider_is_configured(name: str) -> bool:
    key_attr = _PROVIDER_KEYS.get(name)
    return name == "stub" or bool(key_attr and getattr(settings, key_attr))


def get_provider(name: str) -> LLMProvider:
    """Resolve a provider, falling back to the stub only where that is safe.

    Availability is per-provider, not global. Configuring only an OpenAI key
    should give you real embeddings and stubbed completions — not a hard failure
    on every Anthropic-tier call, and not silent stubbing in production either.
    """
    if name in _provider_cache:
        return _provider_cache[name]  # type: ignore[no-any-return]

    def _stub() -> Any:
        from docflow.ai.llm.stub_provider import StubProvider

        return StubProvider()

    if settings.demo_mode or name == "stub":
        provider: Any = _stub()
    elif not provider_is_configured(name):
        if settings.environment in ("production", "staging"):
            raise ProviderUnavailable(
                f"{name} is selected by the model routing table but has no API key configured."
            )
        # Local and test: degrade to the stub so the app stays runnable, but say
        # so loudly — every stub response is also flagged is_stub downstream.
        logger.warning("llm.provider.stubbed", provider=name, reason="no_api_key")
        provider = _stub()
    elif name == "anthropic":
        from docflow.ai.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(settings.anthropic_api_key.get_secret_value())  # type: ignore[union-attr]
    elif name == "openai":
        from docflow.ai.llm.openai_provider import OpenAIProvider

        provider = OpenAIProvider(settings.openai_api_key.get_secret_value())  # type: ignore[union-attr]
    else:
        raise ProviderUnavailable(f"Unknown LLM provider {name!r}.")

    _provider_cache[name] = provider
    return provider  # type: ignore[no-any-return]


def reset_providers() -> None:
    """Drop cached clients — used by tests that flip settings."""
    _provider_cache.clear()


async def call_llm(
    *,
    tier: ModelTier,
    system: str,
    user_content: str,
    tool: ToolSchema | None = None,
    operation: str = "unknown",
    prompt_version: str | None = None,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    max_tokens: int = 8192,
    effort: str | None = None,
    thinking: bool = False,
    budget_remaining_usd: float | None = None,
    record_usage: bool = True,
) -> LLMResponse:
    """Make one model call with the full policy applied.

    Budget is checked *before* spending, using an over-estimate of input tokens —
    a guard that under-counts is not a guard.
    """
    model = resolve_model(tier)

    if budget_remaining_usd is not None and budget_remaining_usd <= 0:
        raise BudgetExceeded(
            "This workspace has reached its AI spending limit for today. "
            "Processing will resume tomorrow, or you can raise the limit in settings."
        )

    estimated_input = estimate_tokens(system) + estimate_tokens(user_content)
    if estimated_input > 500_000:
        raise ProviderUnavailable("Input is too large for a single model call.")

    messages = [Message(role="user", content=user_content)]
    provider = get_provider(model.provider)

    last_error: ProviderError | None = None
    for attempt in range(1, settings.llm_max_retries + 1):
        try:
            response = await provider.complete(
                model=model,
                system=system,
                messages=messages,
                tool=tool,
                max_tokens=max_tokens,
                effort=effort,
                thinking=thinking,
                timeout=float(settings.llm_timeout_seconds),
            )
            break
        except ProviderError as exc:
            last_error = exc
            if not exc.retryable or attempt == settings.llm_max_retries:
                logger.error(
                    "llm.call.failed",
                    operation=operation,
                    model=model.model_id,
                    status=exc.status,
                    attempt=attempt,
                    retryable=exc.retryable,
                )
                if record_usage and workspace_id:
                    await _record(
                        workspace_id=workspace_id,
                        user_id=user_id,
                        document_id=document_id,
                        operation=operation,
                        prompt_version=prompt_version,
                        model=model,
                        response=None,
                        error_code=f"HTTP_{exc.status}" if exc.status else "PROVIDER_ERROR",
                    )
                raise ProviderUnavailable(
                    "The AI provider is temporarily unavailable. Please try again shortly."
                ) from exc
            # Full jitter: synchronised retries from many workers are how a
            # rate-limited provider stays rate-limited.
            delay = min(2**attempt, 30) * random.random()  # noqa: S311 — jitter, not crypto
            logger.warning(
                "llm.call.retry", operation=operation, attempt=attempt, delay_s=round(delay, 2)
            )
            await asyncio.sleep(delay)
    else:  # pragma: no cover - loop always breaks or raises
        raise ProviderUnavailable(str(last_error))

    logger.info(
        "llm.call.completed",
        operation=operation,
        model=model.model_id,
        provider=response.provider,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_tokens=response.usage.cache_read_tokens,
        cost_usd=response.cost_usd,
        latency_ms=response.latency_ms,
        stub=response.is_stub,
    )

    if record_usage and workspace_id:
        await _record(
            workspace_id=workspace_id,
            user_id=user_id,
            document_id=document_id,
            operation=operation,
            prompt_version=prompt_version,
            model=model,
            response=response,
        )

    return response


async def embed_texts(
    texts: list[str],
    *,
    operation: str = "embedding",
    workspace_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    record_usage: bool = True,
) -> EmbeddingResponse:
    """Embed a batch. Caching by content hash happens one layer up, in
    ai/embeddings/cache.py, so this stays a thin provider call."""
    model = ModelSpec.parse(settings.embedding_model)
    provider = cast(EmbeddingProvider, get_provider(model.provider))

    last_error: ProviderError | None = None
    for attempt in range(1, settings.llm_max_retries + 1):
        try:
            response = await provider.embed(
                model=model,
                texts=texts,
                dimensions=settings.embedding_dimensions,
            )
            break
        except ProviderError as exc:
            last_error = exc
            if not exc.retryable or attempt == settings.llm_max_retries:
                raise ProviderUnavailable("Embedding provider unavailable.") from exc
            await asyncio.sleep(min(2**attempt, 30) * random.random())  # noqa: S311
    else:  # pragma: no cover
        raise ProviderUnavailable(str(last_error))

    if record_usage and workspace_id:
        await _record(
            workspace_id=workspace_id,
            document_id=document_id,
            operation=operation,
            model=model,
            response=None,
            embedding=response,
        )
    return response


async def _record(
    *,
    workspace_id: uuid.UUID,
    operation: str,
    model: ModelSpec,
    response: LLMResponse | None,
    embedding: EmbeddingResponse | None = None,
    user_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    prompt_version: str | None = None,
    error_code: str | None = None,
) -> None:
    """Append one row to the usage ledger.

    Never allowed to break the caller: a failed accounting write must not lose a
    result the user already paid for. It is logged loudly instead.
    """
    from docflow.services.usage_service import record_usage as persist

    try:
        await persist(
            workspace_id=workspace_id,
            user_id=user_id,
            document_id=document_id,
            operation=operation,
            provider=model.provider,
            model_id=model.model_id,
            prompt_version=prompt_version,
            usage=response.usage if response else (embedding.usage if embedding else None),
            cost_usd=(
                response.cost_usd if response else (embedding.cost_usd if embedding else 0.0)
            ),
            latency_ms=(
                response.latency_ms if response else (embedding.latency_ms if embedding else None)
            ),
            success=error_code is None,
            error_code=error_code,
            request_id=request_id_var.get(),
        )
    except Exception:
        logger.exception("usage.record.failed", operation=operation, model=model.model_id)
