"""Anthropic adapter.

Thin on purpose. The only real work is translating our provider-neutral
ToolSchema/Message shapes into the Messages API and back, and being careful about
parameters that are *rejected* rather than ignored on current models:

  * `temperature` returns 400 on Opus 5 / Sonnet 5 / the 4.6+ family.
  * `budget_tokens` is removed; `thinking` is `{"type": "adaptive"}`.
  * `effort` lives inside `output_config`, not at the top level.

Getting these wrong is a hard error, not a graceful degradation, so capability is
checked per model rather than assumed.
"""

from __future__ import annotations

import time
from typing import Any

from docflow.ai.llm.base import (
    LLMResponse,
    Message,
    ModelSpec,
    ProviderError,
    TokenUsage,
    ToolSchema,
)
from docflow.ai.llm.pricing import compute_cost
from docflow.core.logging import get_logger

logger = get_logger(__name__)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, max_retries=0)  # retries owned by client.py

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
    ) -> LLMResponse:
        import anthropic

        kwargs: dict[str, Any] = {
            "model": model.model_id,
            "max_tokens": min(max_tokens, model.max_output_tokens),
            "messages": self._build_messages(messages),
        }

        # A cache breakpoint on the system block is the single highest-value
        # caching placement: the system prompt plus tool schema is identical
        # across every window of a document and is re-read on each one.
        if system:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    **(
                        {"cache_control": {"type": "ephemeral"}}
                        if model.supports_prompt_cache
                        else {}
                    ),
                }
            ]

        if tool is not None:
            kwargs["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.json_schema,
                    **({"strict": True} if tool.strict else {}),
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": tool.name}

        if effort and model.supports_effort:
            kwargs["output_config"] = {"effort": effort}
        if thinking and model.supports_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        # Sampling parameters were removed on current models; passing one is a 400.
        if temperature is not None and not model.supports_effort:
            kwargs["temperature"] = temperature

        started = time.perf_counter()
        try:
            response = await self._client.messages.create(timeout=timeout, **kwargs)
        except anthropic.RateLimitError as exc:
            raise ProviderError(str(exc), retryable=True, status=429) from exc
        except anthropic.APIStatusError as exc:
            # 5xx and 408/409 are worth retrying; 4xx generally is not.
            retryable = exc.status_code >= 500 or exc.status_code in (408, 409)
            raise ProviderError(str(exc), retryable=retryable, status=exc.status_code) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(str(exc), retryable=True) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._to_response(response, model, latency_ms)

    @staticmethod
    def _build_messages(messages: list[Message]) -> list[dict[str, Any]]:
        built: list[dict[str, Any]] = []
        for msg in messages:
            block: dict[str, Any] = {"type": "text", "text": msg.content}
            if msg.cache:
                block["cache_control"] = {"type": "ephemeral"}
            built.append({"role": msg.role, "content": [block]})
        return built

    def _to_response(self, response: Any, model: ModelSpec, latency_ms: int) -> LLMResponse:
        text_parts: list[str] = []
        parsed: dict[str, Any] | None = None

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # Always json-parse tool inputs; never string-match the raw form.
                parsed = dict(block.input)

        usage = TokenUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )

        # A safety refusal arrives as HTTP 200 with stop_reason "refusal", so it
        # must be checked before reading content rather than caught as an error.
        if response.stop_reason == "refusal":
            logger.warning("llm.refusal", model=model.model_id)

        return LLMResponse(
            content="\n".join(text_parts),
            parsed=parsed,
            usage=usage,
            model_id=model.model_id,
            provider=self.name,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                model.model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            ),
            finish_reason=response.stop_reason,
            provider_request_id=getattr(response, "id", None),
        )
