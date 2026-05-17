from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.rag import RetrievalScoreSummary


@dataclass(frozen=True)
class ConfidenceScores:
    answer_confidence: float
    groundedness_score: float
    confidence_label: str


@dataclass(frozen=True)
class ConfidenceInputs:
    retrieval_score_summary: RetrievalScoreSummary
    marker_count: int
    unique_citation_count: int
    selected_count: int


def calculate_confidence(inputs: ConfidenceInputs, settings: Settings) -> ConfidenceScores:
    selected_count = max(0, inputs.selected_count)
    citation_coverage = _safe_ratio(inputs.unique_citation_count, selected_count)
    marker_presence = 1.0 if inputs.marker_count > 0 else 0.0

    groundedness = _clamp((0.75 * citation_coverage) + (0.25 * marker_presence))
    retrieval_strength = _optional_score(inputs.retrieval_score_summary.top1_retrieval_score)
    rerank_strength = _optional_score(inputs.retrieval_score_summary.top1_rerank_score)
    context_strength = _clamp(selected_count / 3)
    answer_confidence = _clamp(
        (0.35 * retrieval_strength)
        + (0.35 * rerank_strength)
        + (0.20 * groundedness)
        + (0.10 * context_strength)
    )
    label = _confidence_label(
        answer_confidence=answer_confidence,
        groundedness_score=groundedness,
        settings=settings,
    )
    return ConfidenceScores(
        answer_confidence=round(answer_confidence, 6),
        groundedness_score=round(groundedness, 6),
        confidence_label=label,
    )


def _confidence_label(
    *,
    answer_confidence: float,
    groundedness_score: float,
    settings: Settings,
) -> str:
    if (
        answer_confidence >= settings.confidence_high_threshold
        and groundedness_score >= settings.groundedness_high_threshold
    ):
        return "High"
    if (
        answer_confidence >= settings.confidence_medium_threshold
        and groundedness_score >= settings.groundedness_medium_threshold
    ):
        return "Medium"
    return "Low"


def _optional_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return _clamp(float(value))


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(numerator / denominator)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
