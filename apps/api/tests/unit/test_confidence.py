from __future__ import annotations

import pytest

from docflow.ai.confidence import ConfidenceInputs, bucket_for, compute_confidence
from docflow.models.enums import (
    CitationStatus,
    ConfidenceBucket,
    NormalizationStatus,
    TextExtractionMethod,
)


def score(**kw) -> float:  # type: ignore[no-untyped-def]
    kw.setdefault("citation_status", CitationStatus.VERIFIED)
    kw.setdefault("citation_match_score", 99.0)
    return compute_confidence(ConfidenceInputs(**kw)).calibrated


def test_perfect_signals_reach_high_bucket() -> None:
    result = compute_confidence(
        ConfidenceInputs(
            citation_status=CitationStatus.VERIFIED,
            citation_match_score=100.0,
            extraction_method=TextExtractionMethod.NATIVE,
            windows_containing_fact=2,
            windows_covering_span=2,
            model_self_report=0.95,
        )
    )
    assert result.bucket is ConfidenceBucket.HIGH
    assert result.is_actionable


def test_unverified_citation_can_never_be_high() -> None:
    """The load-bearing rule: no source, no confidence, whatever else agrees."""
    result = compute_confidence(
        ConfidenceInputs(
            citation_status=CitationStatus.UNVERIFIED,
            citation_match_score=0.0,
            extraction_method=TextExtractionMethod.NATIVE,
            windows_containing_fact=3,
            windows_covering_span=3,
            model_self_report=1.0,
        )
    )
    assert result.calibrated <= 0.45
    assert result.bucket is ConfidenceBucket.LOW
    assert not result.is_actionable


def test_model_self_report_cannot_dominate() -> None:
    """Self-reported confidence is one weak prior, not the answer.

    A maximally confident model with a bad citation must still score below a
    modest model with a good one.
    """
    boastful = score(
        citation_status=CitationStatus.PARTIAL, citation_match_score=88.0, model_self_report=1.0
    )
    modest = score(citation_match_score=99.0, model_self_report=0.2)
    assert modest > boastful


def test_vision_ocr_scores_below_native_text() -> None:
    """Not because its text is worse, but because it has no coordinates."""
    native = score(extraction_method=TextExtractionMethod.NATIVE)
    vlm = score(
        citation_status=CitationStatus.PAGE_ONLY, extraction_method=TextExtractionMethod.OCR_VLM
    )
    assert vlm < native


def test_ocr_confidence_blends_with_tier_prior() -> None:
    bad_scan = score(extraction_method=TextExtractionMethod.OCR_TESSERACT, ocr_confidence=35.0)
    good_scan = score(extraction_method=TextExtractionMethod.OCR_TESSERACT, ocr_confidence=95.0)
    assert good_scan > bad_scan


def test_window_disagreement_lowers_confidence() -> None:
    agreed = score(windows_containing_fact=2, windows_covering_span=2)
    disagreed = score(windows_containing_fact=1, windows_covering_span=3)
    assert agreed > disagreed


def test_single_window_coverage_is_neutral_not_penalised() -> None:
    """Seeing a fact once where only one window looked is not disagreement."""
    single = score(windows_containing_fact=1, windows_covering_span=1)
    disagreed = score(windows_containing_fact=1, windows_covering_span=4)
    assert single > disagreed


@pytest.mark.parametrize(
    "status",
    [
        NormalizationStatus.AMBIGUOUS,
        NormalizationStatus.RELATIVE_UNRESOLVED,
        NormalizationStatus.IMPLAUSIBLE,
        NormalizationStatus.FAILED,
    ],
)
def test_unresolved_date_caps_confidence(status: NormalizationStatus) -> None:
    """The quote may be perfect while the actionable value is not."""
    result = compute_confidence(
        ConfidenceInputs(
            citation_status=CitationStatus.VERIFIED,
            citation_match_score=100.0,
            normalization_status=status,
        )
    )
    assert result.bucket is not ConfidenceBucket.HIGH


def test_resolved_date_is_not_capped() -> None:
    result = compute_confidence(
        ConfidenceInputs(
            citation_status=CitationStatus.VERIFIED,
            citation_match_score=100.0,
            windows_containing_fact=2,
            windows_covering_span=2,
            model_self_report=0.9,
            normalization_status=NormalizationStatus.OK,
        )
    )
    assert result.bucket is ConfidenceBucket.HIGH


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, ConfidenceBucket.HIGH),
        (0.85, ConfidenceBucket.HIGH),
        (0.84, ConfidenceBucket.MEDIUM),
        (0.60, ConfidenceBucket.MEDIUM),
        (0.59, ConfidenceBucket.LOW),
        (0.0, ConfidenceBucket.LOW),
    ],
)
def test_bucket_boundaries(value: float, expected: ConfidenceBucket) -> None:
    assert bucket_for(value) is expected


def test_scores_stay_in_range() -> None:
    for citation in CitationStatus:
        for method in TextExtractionMethod:
            result = compute_confidence(
                ConfidenceInputs(
                    citation_status=citation,
                    citation_match_score=100.0,
                    extraction_method=method,
                    model_self_report=1.0,
                )
            )
            assert 0.0 <= result.calibrated <= 1.0
