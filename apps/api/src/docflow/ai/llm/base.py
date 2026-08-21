"""The LLM provider contract.

Call sites ask for a *tier*, never a model name. That single indirection is what
makes model routing, provider migration, cost experiments and eval sweeps
configuration changes rather than code changes.

We deliberately do not use LangChain or LiteLLM here. Both abstract away exactly
the things this product needs to control — cache breakpoints, token accounting,
retry semantics and cost attribution — and the whole cost story depends on
owning those. Three thin adapters total a few hundred lines and can be read end
to end in one sitting.

What the abstraction must NOT do is flatten provider superpowers. Prompt-cache
breakpoints, effort levels and strict tool schemas are exposed as optional
arguments that simply no-op on providers that lack them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from docflow.models.enums import ModelTier


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(slots=True)
class LLMResponse:
    """Everything a caller — and the usage ledger — needs from one call."""

    content: str
    parsed: dict[str, Any] | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    model_id: str = ""
    provider: str = ""
    latency_ms: int = 0
    cost_usd: float = 0.0
    finish_reason: str | None = None
    provider_request_id: str | None = None
    is_stub: bool = False

    @property
    def cache_hit_ratio(self) -> float:
        billed = self.usage.input_tokens + self.usage.cache_read_tokens
        return self.usage.cache_read_tokens / billed if billed else 0.0


@dataclass(slots=True)
class EmbeddingResponse:
    vectors: list[list[float]]
    model_id: str
    provider: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    cost_usd: float = 0.0
    latency_ms: int = 0
    cache_hits: int = 0
    is_stub: bool = False


@dataclass(slots=True)
class ToolSchema:
    """A structured-output contract, expressed once and translated per provider."""

    name: str
    description: str
    json_schema: dict[str, Any]
    strict: bool = True


@dataclass(slots=True)
class Message:
    role: str  # "user" | "assistant"
    content: str
    cache: bool = False  # place a prompt-cache breakpoint after this message


@dataclass(slots=True)
class ModelSpec:
    provider: str
    model_id: str
    supports_effort: bool = False
    supports_thinking: bool = False
    supports_prompt_cache: bool = True
    max_output_tokens: int = 8192

    @classmethod
    def parse(cls, spec: str) -> ModelSpec:
        """Parse a `provider:model` string from settings."""
        provider, _, model_id = spec.partition(":")
        if not model_id:
            raise ValueError(f"model spec must be 'provider:model', got {spec!r}")
        return cls(
            provider=provider,
            model_id=model_id,
            supports_effort=_supports_effort(model_id),
            supports_thinking=_supports_thinking(model_id),
        )


# Capability detection by model family. Getting these wrong is a 400, not a
# graceful degradation, so they are explicit rather than inferred.
_EFFORT_MODELS = ("claude-opus-5", "claude-opus-4", "claude-sonnet-5", "claude-fable-5")
_THINKING_MODELS = _EFFORT_MODELS


def _supports_effort(model_id: str) -> bool:
    return any(model_id.startswith(prefix) for prefix in _EFFORT_MODELS)


def _supports_thinking(model_id: str) -> bool:
    return any(model_id.startswith(prefix) for prefix in _THINKING_MODELS)


@runtime_checkable
class LLMProvider(Protocol):
    """What every adapter must implement. Deliberately small."""

    name: str

    async def complete(
        self,
        *,
        model: ModelSpec,
        system: str,
        messages: list[Message],
        tool: ToolSchema | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
        effort: str | None = None,
        thinking: bool = False,
        timeout: float = 120.0,
    ) -> LLMResponse: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str

    async def embed(
        self,
        *,
        model: ModelSpec,
        texts: list[str],
        dimensions: int | None = None,
        timeout: float = 60.0,
    ) -> EmbeddingResponse: ...


class ProviderError(Exception):
    """Raised by adapters. `retryable` drives the backoff policy in client.py."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


__all__ = [
    "EmbeddingProvider",
    "EmbeddingResponse",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "ModelSpec",
    "ModelTier",
    "ProviderError",
    "TokenUsage",
    "ToolSchema",
]
