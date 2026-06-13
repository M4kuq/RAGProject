from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.schemas.rag import RetrievalScoreSummary

# Identifies what the confidence composite is derived from. The label is a
# heuristic blend of retrieval signals, not a calibrated probability of answer
# correctness. Exposed in the API/UI so clients can communicate this basis;
# kept a plain string so future bases (e.g. "calibrated") don't break clients.
CONFIDENCE_BASIS = "retrieval_signals"


@dataclass(frozen=True)
class ConfidenceScores:
    answer_confidence: float
    groundedness_score: float
    confidence_label: str


@dataclass(frozen=True)
class _ConfidenceComponent:
    weight: float
    value: float
    present: bool


@dataclass(frozen=True)
class ConfidenceInputs:
    retrieval_score_summary: RetrievalScoreSummary
    marker_count: int
    unique_citation_count: int
    selected_count: int


def calculate_confidence(inputs: ConfidenceInputs, settings: Settings) -> ConfidenceScores:
    """Compute a heuristic confidence composite from retrieval signals.

    The returned ``answer_confidence`` is a weighted blend of retrieval strength,
    rerank strength, groundedness, and context strength. It is a heuristic
    composite of retrieval signals, **not** a calibrated probability that the
    answer is correct.

    Each component carries a nominal weight (retrieval 0.35, rerank 0.35,
    groundedness 0.20, context_strength 0.10). Optional retrieval signals may be
    absent (e.g. ``top1_rerank_score`` is ``None`` when reranking is disabled). In
    that case the score is the weighted sum over the *present* components divided
    by the sum of their weights, i.e. the remaining weights are renormalized to
    1.0 so an absent signal does not silently drag the score toward zero. When all
    optional scores are present this is numerically identical to the flat formula.
    """
    selected_count = max(0, inputs.selected_count)
    citation_coverage = _safe_ratio(
        inputs.unique_citation_count,
        _required_citation_count(
            selected_count=selected_count,
            unique_citation_count=inputs.unique_citation_count,
        ),
    )
    marker_presence = 1.0 if inputs.marker_count > 0 else 0.0

    groundedness = _clamp((0.75 * citation_coverage) + (0.25 * marker_presence))
    context_strength = _clamp(selected_count / 3)

    top1_retrieval = inputs.retrieval_score_summary.top1_retrieval_score
    top1_rerank = inputs.retrieval_score_summary.top1_rerank_score
    components: list[_ConfidenceComponent] = [
        _ConfidenceComponent(
            weight=0.35,
            value=_optional_score(top1_retrieval),
            present=top1_retrieval is not None,
        ),
        _ConfidenceComponent(
            weight=0.35,
            value=_optional_score(top1_rerank),
            present=top1_rerank is not None,
        ),
        _ConfidenceComponent(weight=0.20, value=groundedness, present=True),
        _ConfidenceComponent(weight=0.10, value=context_strength, present=True),
    ]
    answer_confidence = _clamp(_weighted_renormalized(components))
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


def _weighted_renormalized(components: list[_ConfidenceComponent]) -> float:
    present = [component for component in components if component.present]
    total_weight = sum(component.weight for component in present)
    if total_weight <= 0:
        return 0.0
    weighted_sum = sum(component.weight * component.value for component in present)
    return weighted_sum / total_weight


def _optional_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return _clamp(float(value))


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(numerator / denominator)


def _required_citation_count(*, selected_count: int, unique_citation_count: int) -> int:
    if selected_count <= 0 or unique_citation_count <= 0:
        return max(1, selected_count)
    # selected_count is the retrieval pool size, not the number of claims in the answer.
    # A concise answer can be fully grounded by one strong cited source.
    return min(selected_count, unique_citation_count)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))
