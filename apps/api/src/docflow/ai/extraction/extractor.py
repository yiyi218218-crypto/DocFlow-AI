"""Windowed extraction, merge, alignment and scoring.

Extraction runs over overlapping page windows rather than the whole document at
once. In order of importance:

1. Provenance is structurally enforced — a model that only sees pages 5-8 cannot
   cite page 40. Window boundaries are a correctness guardrail, not just a cost
   measure.
2. Recall over long inputs degrades; windows keep every page near an attention edge.
3. Windows run concurrently, so wall-clock is one window rather than N.
4. A window that fails schema validation twice costs one window, not the document.
5. The system prompt and tool schema are cached once and re-read per window at a
   tenth of the input price.

The cost is duplicate facts across window boundaries, which the merge step handles.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from docflow.ai.alignment.span_matcher import Alignment, align_quote
from docflow.ai.alignment.text_map import TextMap
from docflow.ai.confidence import ConfidenceInputs, ConfidenceResult, compute_confidence
from docflow.ai.extraction.schemas.base import DocumentAnalysis, to_tool_schema
from docflow.ai.llm.base import ToolSchema
from docflow.ai.llm.client import call_llm
from docflow.ai.normalization.dates import NormalizedDate, normalize_date
from docflow.ai.prompts.loader import compose_system_prompt
from docflow.ai.security.injection import scan_for_injection
from docflow.core.config import settings
from docflow.core.logging import get_logger
from docflow.documents.pdf import ParsedDocument, ParsedPage
from docflow.models.enums import (
    CitationStatus,
    DocumentType,
    FactKind,
    Language,
    ModelTier,
    TextExtractionMethod,
)

logger = get_logger(__name__)

EXTRACTION_TOOL_NAME = "record_document_facts"
CORE_PROMPT = "extraction/v1_core.md"

# Type-specific prompt overlays. Adding a vertical is one entry here plus one
# schema file plus one UI panel — and no migration.
TYPE_PROMPTS: dict[DocumentType, str] = {}


@dataclass(slots=True)
class ExtractedFact:
    """One fact, aligned and scored, ready to persist."""

    kind: FactKind
    payload: dict[str, Any]
    source_quote: str
    source_page: int
    alignment: Alignment
    confidence: ConfidenceResult
    normalized_date: NormalizedDate | None = None
    windows_seen: int = 1
    windows_covering: int = 1

    @property
    def citation_status(self) -> CitationStatus:
        return CitationStatus(self.alignment.status.value)

    @property
    def due_at(self) -> Any:
        return self.normalized_date.value if self.normalized_date else None


@dataclass(slots=True)
class ExtractionResult:
    facts: list[ExtractedFact] = field(default_factory=list)
    document_type: DocumentType = DocumentType.OTHER
    language: Language = Language.UNKNOWN
    title: str | None = None
    summary: str | None = None
    organization: str | None = None
    reference_number: str | None = None
    published_date: Any = None
    security_flags: list[str] = field(default_factory=list)
    prompt_version: str = ""
    model_id: str = ""
    total_cost_usd: float = 0.0
    windows_run: int = 0
    windows_failed: int = 0

    @property
    def verified_ratio(self) -> float:
        """Citation verification rate — the flagship quality metric, and a
        hallucination signal that needs no judge model and no labels."""
        if not self.facts:
            return 0.0
        verified = sum(1 for f in self.facts if f.citation_status is CitationStatus.VERIFIED)
        return verified / len(self.facts)


def build_windows(pages: list[ParsedPage], *, size: int, overlap: int) -> list[list[ParsedPage]]:
    """Overlapping page windows. Short documents go through whole."""
    if not pages:
        return []
    if len(pages) <= max(size, 6):
        return [pages]

    step = max(1, size - overlap)
    windows = [pages[i : i + size] for i in range(0, len(pages), step)]
    return [w for w in windows if w]


def _render_window(pages: list[ParsedPage], nonce: str) -> str:
    """Wrap window text in a nonce-delimited block.

    The nonce is random per request, so a malicious document cannot close the
    delimiter and impersonate the operator — it cannot guess the tag.
    """
    body = "\n\n".join(f"[page {p.page_number}]\n{p.text}" for p in pages)
    return (
        f'<document nonce="{nonce}">\n{body}\n</document nonce="{nonce}">\n\n'
        f"Extract facts from pages {pages[0].page_number}-{pages[-1].page_number} only."
    )


async def _extract_window(
    pages: list[ParsedPage],
    *,
    system: str,
    tool: ToolSchema,
    prompt_version: str,
    workspace_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
) -> tuple[DocumentAnalysis | None, float, str]:
    """One window. Returns (analysis, cost, model_id)."""
    nonce = secrets.token_hex(8)
    user_content = _render_window(pages, nonce)
    cost = 0.0
    model_id = ""

    response = await call_llm(
        tier=ModelTier.BALANCED,
        system=system,
        user_content=user_content,
        tool=tool,
        operation="extraction",
        prompt_version=prompt_version,
        workspace_id=workspace_id,
        document_id=document_id,
        max_tokens=8192,
    )
    cost += response.cost_usd
    model_id = response.model_id

    try:
        return DocumentAnalysis.model_validate(response.parsed or {}), cost, model_id
    except ValidationError as first_error:
        # Python deletes the `as` name at the end of the except block, so the
        # message has to be captured here to be usable in the repair prompt below.
        validation_errors = str(first_error)
        logger.warning(
            "extraction.schema_invalid",
            pages=[p.page_number for p in pages],
            errors=first_error.error_count(),
        )

    # Exactly one repair round-trip, echoing the validation errors back. Never a
    # loop — that is how one malformed document becomes a $50 bill.
    repair = await call_llm(
        tier=ModelTier.BALANCED,
        system=system,
        user_content=(
            f"{user_content}\n\nYour previous response failed schema validation:\n"
            f"{validation_errors}\n\nReturn a corrected response through the tool."
        ),
        tool=tool,
        operation="extraction_repair",
        prompt_version=prompt_version,
        workspace_id=workspace_id,
        document_id=document_id,
        max_tokens=8192,
    )
    cost += repair.cost_usd
    try:
        return DocumentAnalysis.model_validate(repair.parsed or {}), cost, model_id
    except ValidationError:
        logger.error("extraction.schema_invalid_after_repair", pages=[p.page_number for p in pages])
        return None, cost, model_id


async def extract_document(
    parsed: ParsedDocument,
    *,
    document_type: DocumentType = DocumentType.OTHER,
    page_methods: dict[int, TextExtractionMethod] | None = None,
    page_ocr_confidence: dict[int, float] | None = None,
    workspace_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> ExtractionResult:
    """Extract, merge, align and score every fact in a document."""
    prompt_paths = [CORE_PROMPT]
    if (overlay := TYPE_PROMPTS.get(document_type)) is not None:
        prompt_paths.append(overlay)
    system, prompt_version = compose_system_prompt(*prompt_paths)

    tool_def = to_tool_schema(
        DocumentAnalysis,
        name=EXTRACTION_TOOL_NAME,
        description="Record every fact found in the document, each with a verbatim source quote.",
    )
    tool = ToolSchema(
        name=tool_def["name"],
        description=tool_def["description"],
        json_schema=tool_def["json_schema"],
        strict=True,
    )

    windows = build_windows(
        parsed.pages,
        size=settings.extraction_window_pages,
        overlap=settings.extraction_window_overlap,
    )

    outcomes = await asyncio.gather(
        *(
            _extract_window(
                w,
                system=system,
                tool=tool,
                prompt_version=prompt_version,
                workspace_id=workspace_id,
                document_id=document_id,
            )
            for w in windows
        ),
        return_exceptions=True,
    )

    result = ExtractionResult(prompt_version=prompt_version, windows_run=len(windows))
    analyses: list[DocumentAnalysis] = []

    for window, outcome in zip(windows, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            result.windows_failed += 1
            logger.error(
                "extraction.window_failed",
                pages=[p.page_number for p in window],
                error=type(outcome).__name__,
            )
            continue
        analysis, cost, model_id = outcome
        result.total_cost_usd += cost
        result.model_id = model_id or result.model_id
        if analysis is None:
            result.windows_failed += 1
            continue
        analyses.append(analysis)

    _merge_metadata(result, analyses, parsed)
    result.facts = _align_and_score(
        analyses,
        text_maps=parsed.text_maps(),
        page_methods=page_methods or {},
        page_ocr_confidence=page_ocr_confidence or {},
        window_count=len(windows),
        published_date=result.published_date,
    )

    logger.info(
        "extraction.completed",
        facts=len(result.facts),
        verified_ratio=round(result.verified_ratio, 3),
        windows=len(windows),
        failed=result.windows_failed,
        cost_usd=round(result.total_cost_usd, 6),
    )
    return result


def _merge_metadata(
    result: ExtractionResult, analyses: list[DocumentAnalysis], parsed: ParsedDocument
) -> None:
    """Document-level fields: first non-empty wins, since the first window sees
    the header where title, organization and reference number actually live."""
    for a in analyses:
        result.title = result.title or a.title
        result.summary = result.summary or a.summary
        result.organization = result.organization or a.organization
        result.reference_number = result.reference_number or a.reference_number
        if a.document_type != DocumentType.OTHER and result.document_type == DocumentType.OTHER:
            result.document_type = a.document_type
        if a.language != Language.UNKNOWN and result.language == Language.UNKNOWN:
            result.language = a.language
        for flag in a.security_flags:
            if flag not in result.security_flags:
                result.security_flags.append(flag)
        if a.published_date_text and result.published_date is None:
            parsed_date = normalize_date(a.published_date_text)
            if parsed_date.is_usable:
                result.published_date = parsed_date.value

    # Independent detection: never rely solely on the model to report that a
    # document tried to manipulate it.
    detected = scan_for_injection(parsed.full_text)
    for flag in detected:
        if flag not in result.security_flags:
            result.security_flags.append(flag)


def _fact_identity(fact: Any) -> tuple[str, str]:
    """Key for cross-window de-duplication."""
    from docflow.ai.normalization.text import normalize_plain

    payload = fact.model_dump()
    for candidate in ("raw_date_text", "description", "name", "value", "url", "label"):
        if payload.get(candidate):
            return (str(fact.kind), normalize_plain(str(payload[candidate]))[:120])
    return (str(fact.kind), normalize_plain(fact.source_quote)[:120])


def _align_and_score(
    analyses: list[DocumentAnalysis],
    *,
    text_maps: dict[int, TextMap],
    page_methods: dict[int, TextExtractionMethod],
    page_ocr_confidence: dict[int, float],
    window_count: int,
    published_date: Any,
) -> list[ExtractedFact]:
    # Group by identity so a fact seen in two overlapping windows counts as
    # agreement rather than as a duplicate.
    grouped: dict[tuple[str, str], list[Any]] = {}
    for analysis in analyses:
        for fact in analysis.facts:
            grouped.setdefault(_fact_identity(fact), []).append(fact)

    extracted: list[ExtractedFact] = []
    for occurrences in grouped.values():
        best = max(occurrences, key=lambda f: f.llm_confidence)
        alignment = align_quote(
            best.source_quote,
            text_maps,
            claimed_page=best.source_page,
            verified_threshold=settings.citation_verified_threshold,
            partial_threshold=settings.citation_partial_threshold,
        )

        page = alignment.page or best.source_page
        method = page_methods.get(page, TextExtractionMethod.NATIVE)

        normalized: NormalizedDate | None = None
        payload = best.model_dump(exclude={"kind", "source_quote", "source_page", "llm_confidence"})

        if best.kind is FactKind.DEADLINE:
            normalized = normalize_date(
                best.raw_date_text,
                reference_date=published_date,
            )
            payload["normalization_status"] = normalized.status.value
            payload["normalization_reason"] = normalized.reason
            if normalized.alternatives:
                payload["date_alternatives"] = [d.isoformat() for d in normalized.alternatives]

        confidence = compute_confidence(
            ConfidenceInputs(
                citation_status=CitationStatus(alignment.status.value),
                citation_match_score=alignment.score,
                extraction_method=method,
                ocr_confidence=page_ocr_confidence.get(page),
                windows_containing_fact=len(occurrences),
                windows_covering_span=min(window_count, 2) if window_count > 1 else 1,
                model_self_report=best.llm_confidence,
                normalization_status=normalized.status if normalized else None,
            )
        )

        extracted.append(
            ExtractedFact(
                kind=best.kind,
                payload=payload,
                source_quote=best.source_quote,
                source_page=page,
                alignment=alignment,
                confidence=confidence,
                normalized_date=normalized,
                windows_seen=len(occurrences),
                windows_covering=window_count,
            )
        )

    # Deadlines first, then by confidence: the UI shows them in this order and
    # sorting once here keeps every consumer consistent.
    kind_rank = {
        FactKind.DEADLINE: 0,
        FactKind.REQUIREMENT: 1,
        FactKind.ACTION_ITEM: 2,
        FactKind.RISK: 3,
    }
    extracted.sort(key=lambda f: (kind_rank.get(f.kind, 5), -f.confidence.calibrated))
    return extracted
