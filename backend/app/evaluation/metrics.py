from __future__ import annotations

from dataclasses import dataclass

from app.evaluation.fixtures import EvaluationCase
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_score: float | None
    metric_label: str | None
    details: dict[str, object]


@dataclass(frozen=True)
class EvaluationMetricInputs:
    case: EvaluationCase
    answer_text: str
    citations: list[RagAskCitation]
    confidence: RagAskConfidence | None
    retrieval_summary: RetrievalScoreSummary | None
    error_code: str | None = None


def calculate_metrics(inputs: EvaluationMetricInputs) -> list[MetricValue]:
    evidence_text = " ".join(
        [inputs.answer_text, *[citation.snippet for citation in inputs.citations]]
    )
    keyword_hits = _keyword_hits(evidence_text, inputs.case.expected_keywords)
    answer_hit = _expected_answer_hit(evidence_text, inputs.case)
    expected_signal_count = len(inputs.case.expected_keywords) + (
        1 if inputs.case.expected_answer and not inputs.case.expected_keywords else 0
    )
    matched_signal_count = keyword_hits + answer_hit
    faithfulness = _ratio(matched_signal_count, expected_signal_count)
    citation_coverage = 1.0 if not inputs.case.required_citation or inputs.citations else 0.0
    groundedness = inputs.confidence.groundedness_score if inputs.confidence else 0.0
    context_precision = _context_precision(
        evidence_text=evidence_text,
        selected_count=(
            inputs.retrieval_summary.selected_count if inputs.retrieval_summary is not None else 0
        ),
        keyword_hits=matched_signal_count,
    )
    metadata_details: dict[str, object] = {
        "case_id": inputs.case.case_id,
        "expected_keyword_count": len(inputs.case.expected_keywords),
        "required_citation": inputs.case.required_citation,
    }
    if inputs.error_code:
        metadata_details["error_code"] = inputs.error_code
    return [
        MetricValue(
            metric_name="case_metadata",
            metric_score=None,
            metric_label=inputs.case.case_id,
            details=metadata_details,
        ),
        MetricValue(
            metric_name="faithfulness",
            metric_score=faithfulness,
            metric_label=_label(faithfulness),
            details={
                "matched_expected_keywords": keyword_hits,
                "matched_expected_answer": bool(answer_hit),
                "expected_keyword_count": len(inputs.case.expected_keywords),
                "expected_signal_count": expected_signal_count,
            },
        ),
        MetricValue(
            metric_name="groundedness",
            metric_score=_clamp01(groundedness),
            metric_label=_label(groundedness),
            details={
                "source": "rag_confidence",
                "has_confidence": inputs.confidence is not None,
            },
        ),
        MetricValue(
            metric_name="citation_coverage",
            metric_score=citation_coverage,
            metric_label=_label(citation_coverage),
            details={
                "required_citation": inputs.case.required_citation,
                "citation_count": len(inputs.citations),
            },
        ),
        MetricValue(
            metric_name="context_precision",
            metric_score=context_precision,
            metric_label=_label(context_precision),
            details={
                "selected_count": (
                    inputs.retrieval_summary.selected_count
                    if inputs.retrieval_summary is not None
                    else 0
                ),
                "matched_expected_keywords": keyword_hits,
                "matched_expected_answer": bool(answer_hit),
            },
        ),
    ]


def failure_metrics(case: EvaluationCase, *, error_code: str) -> list[MetricValue]:
    return calculate_metrics(
        EvaluationMetricInputs(
            case=case,
            answer_text="",
            citations=[],
            confidence=None,
            retrieval_summary=None,
            error_code=error_code,
        )
    )


def _keyword_hits(text: str, expected_keywords: tuple[str, ...]) -> int:
    haystack = text.casefold()
    return sum(1 for keyword in expected_keywords if keyword.casefold() in haystack)


def _expected_answer_hit(text: str, case: EvaluationCase) -> int:
    if case.expected_keywords or not case.expected_answer:
        return 0
    return 1 if case.expected_answer.casefold() in text.casefold() else 0


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp01(numerator / denominator)


def _context_precision(*, evidence_text: str, selected_count: int, keyword_hits: int) -> float:
    if selected_count <= 0:
        return 0.0
    if not evidence_text.strip():
        return 0.0
    return _clamp01(keyword_hits / selected_count)


def _clamp01(value: float) -> float:
    return round(min(1.0, max(0.0, float(value))), 6)


def _label(value: float) -> str:
    score = _clamp01(value)
    if score >= 0.75:
        return "pass"
    if score >= 0.45:
        return "partial"
    return "fail"
