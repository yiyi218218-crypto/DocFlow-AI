"""Deterministic stub provider — the reason `make demo` works with no API keys.

This is not test scaffolding. It is a product feature:

  * `docker compose up` gives a recruiter a working app in minutes, offline.
  * `DEMO_MODE=1` makes the hackathon demo independent of venue wifi and
    provider rate limits, which is the single highest-leverage prep item.
  * The pipeline's plumbing — schema validation, alignment, confidence,
    persistence — can be tested end to end without spending money.

It produces *structurally valid* output whose quotes are lifted verbatim from the
input, so span alignment genuinely verifies and the citation path is exercised
for real rather than mocked out.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any

from docflow.ai.llm.base import (
    EmbeddingResponse,
    LLMResponse,
    Message,
    ModelSpec,
    TokenUsage,
    ToolSchema,
)
from docflow.ai.llm.pricing import estimate_tokens
from docflow.ai.normalization.bangla import detect_language

_DOCUMENT_BLOCK = re.compile(r"<document[^>]*>(.*?)</document[^>]*>", re.DOTALL)
# Two kinds of newline in PDF text, and they mean different things:
#   * a single \n is a line wrap inside a sentence — folding it is required, or
#     "…submitted by\n15 September 2026." splits into a fragment with the cue and
#     a fragment with the date, and neither reads as a deadline;
#   * a blank line is a real paragraph boundary — folding it merges the header
#     block into the following sentence, so the first date found is the
#     publication date rather than the actual deadline.
# So: split on blank lines first, fold single newlines within each paragraph.
_SENTENCE = re.compile(r"[^.।]+[.।]?")
_PARAGRAPH = re.compile(r"\n\s*\n")
_LINEWRAP = re.compile(r"\s*\n\s*")

# Cues that mark a sentence as carrying a deadline, a requirement or an action.
_DEADLINE_CUES = (
    "deadline",
    "last date",
    "by ",
    "submit",
    "due",
    "closes",
    "closing",
    "no later than",
    "শেষ তারিখ",
    "সময়সীমা",
)
_REQUIREMENT_CUES = (
    "required",
    "must",
    "documents",
    "copy of",
    "transcript",
    "certificate",
    "photograph",
    "attach",
    "eligib",
    "প্রয়োজন",
    "আবশ্যক",
)
_DATE_HINT = re.compile(
    r"\b\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}\b|"
    r"\b\d{1,2}\s+\w+\s+\d{4}\b|\b\w+\s+\d{1,2},?\s+\d{4}\b|"
    r"[০-৯]{1,2}\s+\S+\s+[০-৯]{4}",
)


_TIME_HINT = re.compile(
    r"\b(?:(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s*[ap]\.?m\.?|(?:[01]?\d|2[0-3]):[0-5]\d)\b",
    re.IGNORECASE,
)


class StubProvider:
    name = "stub"

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
        started = time.perf_counter()
        prompt = "\n".join(m.content for m in messages)
        document = self._extract_document(prompt)

        parsed: dict[str, Any] | None = None
        content = ""
        if tool is not None:
            parsed = self._build_payload(tool, document)
        else:
            content = self._summarize(document)

        return LLMResponse(
            content=content,
            parsed=parsed,
            usage=TokenUsage(
                input_tokens=estimate_tokens(prompt),
                output_tokens=estimate_tokens(str(parsed) if parsed else content),
            ),
            model_id=f"stub:{model.model_id}",
            provider=self.name,
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=0.0,
            finish_reason="end_turn",
            is_stub=True,
        )

    async def embed(
        self,
        *,
        model: ModelSpec,
        texts: list[str],
        dimensions: int | None = None,
        timeout: float = 60.0,
    ) -> EmbeddingResponse:
        """Hash-based pseudo-embeddings.

        Deterministic and unit-norm, so vector storage, indexing and ranking are
        all exercised. Identical text yields identical vectors and similar text
        does not — good enough to test the plumbing, useless for real retrieval,
        which is exactly the honest boundary.
        """
        dims = dimensions or 1536
        vectors = [self._hash_vector(text, dims) for text in texts]
        return EmbeddingResponse(
            vectors=vectors,
            model_id=f"stub:{model.model_id}",
            provider=self.name,
            usage=TokenUsage(input_tokens=sum(estimate_tokens(t) for t in texts)),
            cost_usd=0.0,
            is_stub=True,
        )

    # --- internals --------------------------------------------------------

    @staticmethod
    def _extract_document(prompt: str) -> str:
        match = _DOCUMENT_BLOCK.search(prompt)
        return (match.group(1) if match else prompt).strip()

    @staticmethod
    def _sentences(text: str) -> list[str]:
        sentences: list[str] = []
        for paragraph in _PARAGRAPH.split(text):
            flattened = _LINEWRAP.sub(" ", paragraph)
            sentences.extend(
                s.strip() for s in _SENTENCE.findall(flattened) if len(s.strip()) >= 20
            )
        return sentences

    @staticmethod
    def _summarize(text: str) -> str:
        sentences = StubProvider._sentences(text)
        if not sentences:
            return "This document could not be summarised in demo mode."
        return " ".join(sentences[:3])[:600]

    def _build_payload(self, tool: ToolSchema, document: str) -> dict[str, Any]:
        """Fill the requested schema, keeping quotes verbatim so alignment works."""
        properties = tool.json_schema.get("properties", {})
        sentences = self._sentences(document)
        payload: dict[str, Any] = {}

        if "document_type" in properties:
            payload["document_type"] = self._guess_type(document)
        if "language" in properties:
            payload["language"] = detect_language(document)
        if "title" in properties:
            payload["title"] = sentences[0][:120] if sentences else "Untitled document"
        if "summary" in properties:
            payload["summary"] = self._summarize(document)
        if "organization" in properties:
            payload["organization"] = self._guess_organization(document)
        if "facts" in properties:
            payload["facts"] = self._build_facts(document, sentences)

        # Satisfy any remaining required keys so schema validation still passes.
        for key in tool.json_schema.get("required", []):
            payload.setdefault(key, self._empty_for(properties.get(key, {})))
        return payload

    def _build_facts(self, document: str, sentences: list[str]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        page = self._guess_page(document)

        for sentence in sentences:
            lowered = sentence.lower()
            date_match = _DATE_HINT.search(sentence)
            time_match = _TIME_HINT.search(sentence)

            if date_match and any(cue in lowered for cue in _DEADLINE_CUES):
                raw = date_match.group(0)
                # Flat fields, matching the discriminated-union schema. Nesting
                # them under "payload" fails validation — the payload column is a
                # persistence detail, not part of the model's contract.
                facts.append(
                    {
                        "kind": "deadline",
                        "source_quote": sentence,
                        "source_page": page,
                        "llm_confidence": 0.8,
                        "label": "Application deadline",
                        "raw_date_text": raw,
                        "time_of_day_text": time_match.group(0) if time_match else None,
                        "date_kind": "absolute",
                        "is_primary": "submit" in lowered or "deadline" in lowered,
                    }
                )
            elif any(cue in lowered for cue in _REQUIREMENT_CUES):
                facts.append(
                    {
                        "kind": "requirement",
                        "source_quote": sentence,
                        "source_page": page,
                        "llm_confidence": 0.7,
                        "requirement_type": "document",
                        "description": sentence[:200],
                        "is_mandatory": "must" in lowered or "required" in lowered,
                    }
                )

            if len(facts) >= 12:
                break
        return facts

    @staticmethod
    def _guess_page(document: str) -> int:
        match = re.search(r"\[page\s+(\d+)\]", document, re.IGNORECASE)
        return int(match.group(1)) if match else 1

    @staticmethod
    def _guess_type(document: str) -> str:
        lowered = document.lower()
        for cue, value in (
            ("scholarship", "scholarship_notice"),
            ("admission", "admission_notice"),
            ("exam", "exam_notice"),
            ("invoice", "invoice"),
            ("agreement", "contract"),
            ("circular", "university_circular"),
        ):
            if cue in lowered:
                return value
        return "other"

    @staticmethod
    def _guess_organization(document: str) -> str | None:
        match = re.search(
            r"^(.{0,80}?(?:University|College|Institute|Ministry|Board|Department|Authority).{0,40})$",
            document,
            re.MULTILINE | re.IGNORECASE,
        )
        return match.group(1).strip() if match else None

    @staticmethod
    def _empty_for(schema: dict[str, Any]) -> Any:
        return {
            "array": [],
            "object": {},
            "string": "",
            "integer": 0,
            "number": 0.0,
            "boolean": False,
        }.get(schema.get("type", "string"))

    @staticmethod
    def _hash_vector(text: str, dims: int) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < dims:
            block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
            values.extend((b - 127.5) / 127.5 for b in block)
            counter += 1
        values = values[:dims]
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
