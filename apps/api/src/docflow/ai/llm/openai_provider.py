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

# OpenAI's strict mode accepts a narrower JSON Schema dialect than Anthropic's:
# every property must appear in `required`, and most validation keywords are
# rejected outright. Optionality is expressed by making the type nullable rather
# than by omitting the key.
#
# This is exactly the kind of provider difference the abstraction exists to
# absorb — the shared ToolSchema stays provider-neutral and each adapter speaks
# its own dialect.
_STRICT_UNSUPPORTED = frozenset(
    {
        "default",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "examples",
        # Pydantic emits `discriminator` alongside a union; strict mode rejects it.
        "discriminator",
    }
)


def to_strict_schema(node: Any) -> Any:
    """Rewrite a JSON Schema into OpenAI's strict dialect."""
    if isinstance(node, list):
        return [to_strict_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned = {k: to_strict_schema(v) for k, v in node.items() if k not in _STRICT_UNSUPPORTED}

    # Pydantic emits `oneOf` for a discriminated union; strict mode only accepts
    # `anyOf`. They are equivalent here because the branches are disjoint by
    # construction — the discriminator guarantees exactly one matches.
    if "oneOf" in cleaned:
        cleaned["anyOf"] = cleaned.pop("oneOf")

    if cleaned.get("type") == "object" and isinstance(cleaned.get("properties"), dict):
        properties: dict[str, Any] = cleaned["properties"]
        previously_required = set(cleaned.get("required", []))

        for name, subschema in properties.items():
            if name not in previously_required:
                properties[name] = _make_nullable(subschema)

        # Strict mode requires every key present in `required`.
        cleaned["required"] = list(properties.keys())
        cleaned["additionalProperties"] = False

    return cleaned


def _make_nullable(subschema: Any) -> Any:
    """Express "optional" as "may be null", which is how strict mode models it."""
    if not isinstance(subschema, dict):
        return subschema

    if "anyOf" in subschema:
        branches = subschema["anyOf"]
        if not any(isinstance(b, dict) and b.get("type") == "null" for b in branches):
            subschema["anyOf"] = [*branches, {"type": "null"}]
        return subschema

    node_type = subschema.get("type")
    if isinstance(node_type, str) and node_type != "null":
        subschema["type"] = [node_type, "null"]
    elif isinstance(node_type, list) and "null" not in node_type:
        subschema["type"] = [*node_type, "null"]
    elif node_type is None and "$ref" in subschema:
        # A $ref cannot carry a type, so wrap it.
        return {"anyOf": [subschema, {"type": "null"}]}
    return subschema


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
                        "parameters": (
                            to_strict_schema(tool.json_schema) if tool.strict else tool.json_schema
                        ),
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
