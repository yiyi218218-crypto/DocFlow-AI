"""Confidence: computed, not self-reported.

An LLM asked "how confident are you?" produces a number that is poorly calibrated
and systematically optimistic. Showing it to users is a trust error. So confidence
here is a composite of signals that are mostly *objective*:

  * citation match score  — did the quote actually appear in the source? (objective)
  * source quality        — native text layer, good OCR, or a coordinate-less
                            vision pass? (objective)
  * window agreement      — did overlapping extraction windows independently
                            produce this fact? (objective)
  * model self-report     — one weak prior, deliberately the smallest weight

The composite is then mapped through a calibration curve fitted on the eval set,
and only the resulting *bucket* is shown in the UI. "0.73" implies a precision we
do not have and invites users to reason about it; high/medium/low is honest and
actionable.

Weights below are a documented starting point, to be refitted once the eval set
has ≥200 labelled facts. The fitted weights and reliability diagram belong in
docs/EVALUATION.md — publishing a calibration curve is what separates measured
ML engineering from prompt engineering.
"""

from __future__ import annotations

from dataclasses import dataclass

from docflow.models.enums import (
    CitationStatus,
    ConfidenceBucket,
    NormalizationStatus,
    TextExtractionMethod,
)

WEIGHT_CITATION = 0.45
WEIGHT_SOURCE_QUALITY = 0.20
WEIGHT_AGREEMENT = 0.20
WEIGHT_MODEL_SELF_REPORT = 0.15

HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.60

# How much to trust text depending on how it was obtained. The vision tier scores
# lowest not because its text is worst — it is often the best — but because it
# returns no coordinates, so the citation cannot be verified positionally.
SOURCE_QUALITY: dict[TextExtractionMethod, float] = {
    TextExtractionMethod.NATIVE: 1.0,
    TextExtractionMethod.OCR_CLOUD: 0.85,
    TextExtractionMethod.OCR_TESSERACT: 0.70,
    TextExtractionMethod.OCR_VLM: 0.40,
    TextExtractionMethod.NONE: 0.20,
}


@dataclass(slots=True)
class ConfidenceInputs:
    citation_status: CitationStatus
    citation_match_score: float = 0.0  # 0-100 from rapidfuzz
    extraction_method: TextExtractionMethod = TextExtractionMethod.NATIVE
    ocr_confidence: float | None = None  # 0-100 mean word confidence
    windows_containing_fact: int = 1
    windows_covering_span: int = 1
    model_self_report: float = 0.5  # 0-1, one weak prior
    normalization_status: NormalizationStatus | None = None


@dataclass(slots=True)
class ConfidenceResult:
    raw: float
    calibrated: float
    bucket: ConfidenceBucket

    @property
    def is_actionable(self) -> bool:
        """Only high-confidence facts auto-generate tasks."""
        return self.bucket is ConfidenceBucket.HIGH


def _citation_component(inputs: ConfidenceInputs) -> float:
    if inputs.citation_status is CitationStatus.UNVERIFIED:
        return 0.0
    if inputs.citation_status is CitationStatus.PAGE_ONLY:
        # Text was located but not positioned. Real evidence, weaker evidence.
        return 0.55
    # Map 85-100 onto 0-1: below 85 an alignment is not meaningful support.
    return max(0.0, min(1.0, (inputs.citation_match_score - 85.0) / 15.0))


def _source_component(inputs: ConfidenceInputs) -> float:
    base = SOURCE_QUALITY.get(inputs.extraction_method, 0.5)
    if inputs.ocr_confidence is not None:
        # Blend the tier's prior with what the engine actually reported.
        base = 0.5 * base + 0.5 * max(0.0, min(1.0, inputs.ocr_confidence / 100.0))
    return base


def _agreement_component(inputs: ConfidenceInputs) -> float:
    """Only meaningful where windows actually overlapped.

    A fact seen once in a region only one window covered is not evidence of
    disagreement, so it gets a neutral score rather than a penalty.
    """
    if inputs.windows_covering_span <= 1:
        return 0.6
    return max(0.0, min(1.0, inputs.windows_containing_fact / inputs.windows_covering_span))


def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    raw = (
        WEIGHT_CITATION * _citation_component(inputs)
        + WEIGHT_SOURCE_QUALITY * _source_component(inputs)
        + WEIGHT_AGREEMENT * _agreement_component(inputs)
        + WEIGHT_MODEL_SELF_REPORT * max(0.0, min(1.0, inputs.model_self_report))
    )

    # A date we could not resolve caps the fact's confidence regardless of how
    # well the quote matched: the quote is right, the actionable value is not.
    if inputs.normalization_status is not None and inputs.normalization_status not in (
        NormalizationStatus.OK,
    ):
        raw = min(raw, 0.55)

    # An unverifiable citation can never be high confidence, whatever else agrees.
    if inputs.citation_status is CitationStatus.UNVERIFIED:
        raw = min(raw, 0.45)

    calibrated = calibrate(raw)
    return ConfidenceResult(raw=raw, calibrated=calibrated, bucket=bucket_for(calibrated))


def calibrate(raw: float) -> float:
    """Map the raw composite to an estimated probability of correctness.

    Identity until the eval set is large enough to fit an isotonic regression.
    Kept as a named seam so swapping in the fitted curve touches one function and
    every stored score records which curve produced it.
    """
    return max(0.0, min(1.0, raw))


def bucket_for(calibrated: float) -> ConfidenceBucket:
    if calibrated >= HIGH_THRESHOLD:
        return ConfidenceBucket.HIGH
    if calibrated >= MEDIUM_THRESHOLD:
        return ConfidenceBucket.MEDIUM
    return ConfidenceBucket.LOW
