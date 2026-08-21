"""OpenAI adapter.

Primarily an embedding provider — `text-embedding-3-small` is the MVP choice for
being cheap and infrastructure-free — but chat completion is implemented too, so
the tier router genuinely has a second provider to fail over to rather than a
theoretical one.
"""

from __future__ import annotations

import json
import time
from typing import Any

from docflow.ai.llm.base import (
    EmbeddingResponse,
    LLMResponse,
    Message,
    ModelSpec,
    ProviderError,
    TokenUsage,
    ToolSchema,
)
from docflow.ai.llm.pricing import compute_cost


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, max_retries=0)

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
        import openai

        payload: dict[str, Any] = {
            "model": model.model_id,
            "max_completion_tokens": max_tokens,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": m.role, "content": m.content} for m in messages],
        }
        if tool is not None:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.json_schema,
                        "strict": tool.strict,
                    },
                }
            ]
            payload["tool_choice"] = {"type": "function", "function": {"name": tool.name}}
        if temperature is not None:
            payload["temperature"] = temperature

        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(timeout=timeout, **payload)
        except openai.RateLimitError as exc:
            raise ProviderError(str(exc), retryable=True, status=429) from exc
        except openai.APIStatusError as exc:
            retryable = exc.status_code >= 500 or exc.status_code in (408, 409)
            raise ProviderError(str(exc), retryable=retryable, status=exc.status_code) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(str(exc), retryable=True) from exc

        choice = response.choices[0]
        parsed: dict[str, Any] | None = None
        if choice.message.tool_calls:
            try:
                parsed = json.loads(choice.message.tool_calls[0].function.arguments)
            except json.JSONDecodeError as exc:
                raise ProviderError(f"tool call returned invalid JSON: {exc}") from exc

        usage = TokenUsage(
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )
        return LLMResponse(
            content=choice.message.content or "",
            parsed=parsed,
            usage=usage,
            model_id=model.model_id,
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=compute_cost(
                model.model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            ),
            finish_reason=choice.finish_reason,
            provider_request_id=response.id,
        )

    async def embed(
        self,
        *,
        model: ModelSpec,
        texts: list[str],
        dimensions: int | None = None,
        timeout: float = 60.0,
    ) -> EmbeddingResponse:
        import openai

        payload: dict[str, Any] = {"model": model.model_id, "input": texts}
        if dimensions:
            payload["dimensions"] = dimensions

        started = time.perf_counter()
        try:
            response = await self._client.embeddings.create(timeout=timeout, **payload)
        except openai.RateLimitError as exc:
            raise ProviderError(str(exc), retryable=True, status=429) from exc
        except openai.APIStatusError as exc:
            retryable = exc.status_code >= 500
            raise ProviderError(str(exc), retryable=retryable, status=exc.status_code) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(str(exc), retryable=True) from exc

        # The API preserves input order, but it is cheap to be certain.
        ordered = sorted(response.data, key=lambda d: d.index)
        usage = TokenUsage(input_tokens=response.usage.prompt_tokens if response.usage else 0)

        return EmbeddingResponse(
            vectors=[list(d.embedding) for d in ordered],
            model_id=model.model_id,
            provider=self.name,
            usage=usage,
            cost_usd=compute_cost(model.model_id, input_tokens=usage.input_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
