from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.evaluation.fixtures import (
    EvaluationCase,
    evaluation_case_question_hash,
    evaluation_case_snapshot_hash,
)
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary

EVALUATION_DETAIL_SCHEMA_VERSION: Final = "phase2.eval.v1"
EVALUATION_METRIC_SEMANTICS_VERSION: Final = "rag.eval.metric.v2"


@dataclass(frozen=True)
class MetricValue:
    metric_name: str
    metric_score: float | None
    metric_label: str | None
    details: dict[str, object]
    metric_value: float | None = None


@dataclass(frozen=True)
class RetrievedEvaluationItem:
    document_chunk_id: int
    logical_document_id: int | None
    rank_order: int
    snippet: str


@dataclass(frozen=True)
class EvaluationMetricInputs:
    case: EvaluationCase
    answer_text: str
    citations: list[RagAskCitation]
    confidence: RagAskConfidence | None
    retrieval_summary: RetrievalScoreSummary | None
    retrieved_items: list[RetrievedEvaluationItem] | None = None
    latency_ms: int | None = None
    error_code: str | None = None


def calculate_metrics(inputs: EvaluationMetricInputs) -> list[MetricValue]:
    retrieved_items = sorted(inputs.retrieved_items or [], key=lambda item: item.rank_order)
    retrieval_evidence_text = " ".join(item.snippet for item in retrieved_items)
    if not retrieval_evidence_text:
        retrieval_evidence_text = " ".join(citation.snippet for citation in inputs.citations)

    has_answer = bool(inputs.answer_text.strip())
    answer_keyword_hits = _keyword_hits(inputs.answer_text, inputs.case.expected_keywords)
    expected_answer_slots = _metadata_string_values(
        inputs.case.metadata_json,
        "expected_answer_slots",
    )
    answer_slot_hits = _keyword_hits(inputs.answer_text, expected_answer_slots)
    expected_signal_count = len(inputs.case.expected_keywords)
    faithfulness = (
        _ratio(answer_keyword_hits, expected_signal_count)
        if has_answer and expected_signal_count
        else None
    )
    answer_completeness = (
        _ratio(answer_slot_hits, len(expected_answer_slots))
        if has_answer and expected_answer_slots
        else None
    )
    citation_presence = (
        (1.0 if not inputs.case.required_citation or inputs.citations else 0.0)
        if has_answer
        else None
    )
    if has_answer:
        citation_correctness, citation_correctness_details = _citation_correctness(
            inputs.case,
            inputs.citations,
            retrieved_items,
        )
    else:
        citation_correctness = None
        citation_correctness_details = {
            "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
            "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
            "citation_count": len(inputs.citations),
            "not_applicable": True,
            "reason_code": "answer_not_generated",
        }
    selected_count = (
        inputs.retrieval_summary.selected_count if inputs.retrieval_summary is not None else 0
    )
    groundedness = (
        (
            inputs.confidence.groundedness_score
            if inputs.confidence
            else (1.0 if selected_count > 0 else 0.0)
        )
        if has_answer
        else None
    )
    retrieval_signal_hits = _expected_signal_hits(retrieval_evidence_text, inputs.case)
    context_precision = _context_precision(
        evidence_text=retrieval_evidence_text,
        selected_count=(selected_count),
        keyword_hits=retrieval_signal_hits,
    )
    metadata_details: dict[str, object] = {
        "case_id": inputs.case.case_id,
        "question_hash": evaluation_case_question_hash(inputs.case.question),
        "case_snapshot_hash": evaluation_case_snapshot_hash(
            question=inputs.case.question,
            expected_answer=inputs.case.expected_answer,
            expected_keywords=inputs.case.expected_keywords,
            expected_document_ids=inputs.case.expected_document_ids,
            expected_chunk_ids=inputs.case.expected_chunk_ids,
            required_citation=inputs.case.required_citation,
            metadata_json=inputs.case.metadata_json,
        ),
        "expected_keyword_count": len(inputs.case.expected_keywords),
        "expected_answer_slot_count": len(expected_answer_slots),
        "required_citation": inputs.case.required_citation,
        "answer_generated": has_answer,
    }
    if inputs.error_code:
        metadata_details["error_code"] = inputs.error_code

    recall = _recall_at_k(inputs.case, retrieved_items, retrieval_evidence_text)
    first_rank = _first_relevant_rank(inputs.case, retrieved_items, retrieval_evidence_text)
    mrr = (
        None
        if _target_count(inputs.case) <= 0
        else (0.0 if first_rank is None else round(1.0 / first_rank, 6))
    )
    no_context_rate = 1.0 if selected_count <= 0 else 0.0

    return [
        MetricValue(
            metric_name="case_metadata",
            metric_score=None,
            metric_label=inputs.case.case_id,
            details=metadata_details,
        ),
        MetricValue(
            metric_name="recall_at_k",
            metric_score=recall,
            metric_label=_optional_label(recall),
            details=_relevance_details(
                inputs.case,
                retrieved_items,
                matched_count=(
                    None
                    if recall is None
                    else _matched_target_count(
                        inputs.case,
                        retrieved_items,
                        retrieval_evidence_text,
                    )
                ),
                rank=None,
                not_applicable=recall is None,
            ),
        ),
        MetricValue(
            metric_name="mrr",
            metric_score=mrr,
            metric_label=_optional_label(mrr),
            details=_relevance_details(
                inputs.case,
                retrieved_items,
                matched_count=None,
                rank=first_rank,
                not_applicable=mrr is None,
            ),
        ),
        MetricValue(
            metric_name="faithfulness",
            metric_score=faithfulness,
            metric_label=_optional_label(faithfulness),
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
                "evidence_scope": "answer_text_only",
                "matched_expected_keywords": answer_keyword_hits,
                "matched_expected_answer": False,
                "expected_keyword_count": len(inputs.case.expected_keywords),
                "expected_signal_count": expected_signal_count,
                "not_applicable": faithfulness is None,
                "reason_code": (
                    "answer_not_generated"
                    if not has_answer
                    else ("expected_keywords_not_configured" if not expected_signal_count else None)
                ),
            },
        ),
        MetricValue(
            metric_name="answer_completeness",
            metric_score=answer_completeness,
            metric_label=_optional_label(answer_completeness),
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
                "evidence_scope": "answer_text_only",
                "expected_answer_slot_count": len(expected_answer_slots),
                "matched_answer_slot_count": answer_slot_hits,
                "not_applicable": answer_completeness is None,
                "reason_code": (
                    None
                    if answer_completeness is not None
                    else "answer_not_generated"
                    if not has_answer
                    else "expected_answer_slots_missing"
                ),
            },
        ),
        MetricValue(
            metric_name="groundedness",
            metric_score=_clamp01(groundedness) if groundedness is not None else None,
            metric_label=_optional_label(groundedness),
            details={
                "source": ("rag_confidence" if inputs.confidence else "retrieval_presence")
                if has_answer
                else "not_applicable",
                "has_confidence": inputs.confidence is not None,
                "selected_count": selected_count,
                "not_applicable": not has_answer,
                "reason_code": None if has_answer else "answer_not_generated",
            },
        ),
        MetricValue(
            metric_name="citation_coverage",
            metric_score=citation_presence,
            metric_label=_optional_label(citation_presence),
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
                "legacy_alias_for": "citation_presence",
                "required_citation": inputs.case.required_citation,
                "citation_count": len(inputs.citations),
                "not_applicable": not has_answer,
                "reason_code": None if has_answer else "answer_not_generated",
            },
        ),
        MetricValue(
            metric_name="citation_presence",
            metric_score=citation_presence,
            metric_label=_optional_label(citation_presence),
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
                "required_citation": inputs.case.required_citation,
                "citation_count": len(inputs.citations),
                "not_applicable": not has_answer,
                "reason_code": None if has_answer else "answer_not_generated",
            },
        ),
        MetricValue(
            metric_name="citation_correctness",
            metric_score=citation_correctness,
            metric_label=_optional_label(citation_correctness),
            details=citation_correctness_details,
        ),
        MetricValue(
            metric_name="context_precision",
            metric_score=context_precision,
            metric_label=_label(context_precision),
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
                "evidence_scope": "retrieval_evidence_only",
                "selected_count": (selected_count),
                "matched_expected_signals": retrieval_signal_hits,
            },
        ),
        MetricValue(
            metric_name="no_context_rate",
            metric_score=no_context_rate,
            metric_label=_label(1.0 - no_context_rate),
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "selected_count": (selected_count),
                "no_context": bool(no_context_rate),
            },
        ),
        MetricValue(
            metric_name="p95_latency",
            metric_score=None,
            metric_label="ms" if inputs.latency_ms is not None else "not_applicable",
            metric_value=float(inputs.latency_ms) if inputs.latency_ms is not None else None,
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "unit": "ms",
                "sample_latency_ms": inputs.latency_ms,
                "not_applicable": inputs.latency_ms is None,
            },
        ),
        MetricValue(
            metric_name="strategy_selection_accuracy",
            metric_score=None,
            metric_label="not_applicable",
            details={
                "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
                "not_applicable": True,
                "reason_code": "agentic_router_not_implemented",
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


def _expected_signal_hits(text: str, case: EvaluationCase) -> int:
    return _keyword_hits(text, case.expected_keywords) + _expected_answer_hit(text, case)


def _citation_correctness(
    case: EvaluationCase,
    citations: list[RagAskCitation],
    retrieved_items: list[RetrievedEvaluationItem],
) -> tuple[float | None, dict[str, object]]:
    details: dict[str, object] = {
        "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
        "metric_semantics_version": EVALUATION_METRIC_SEMANTICS_VERSION,
        "citation_count": len(citations),
    }
    if not citations:
        if not case.required_citation:
            details.update(
                {
                    "not_applicable": True,
                    "reason_code": "citation_not_required",
                    "gold_source": "none",
                    "matched_citation_count": 0,
                }
            )
            return None, details
        details.update(
            {
                "not_applicable": False,
                "reason_code": "required_citation_missing",
                "gold_source": "citation_presence",
                "matched_citation_count": 0,
            }
        )
        return 0.0, details

    matching_citation_count = 0
    gold_source: str
    if case.expected_chunk_ids:
        expected_chunk_ids = set(case.expected_chunk_ids)
        gold_source = "expected_chunk_ids"
        matching_citation_count = sum(
            1 for citation in citations if citation.document_chunk_id in expected_chunk_ids
        )
    elif case.expected_document_ids:
        document_id_by_chunk_id = {
            item.document_chunk_id: item.logical_document_id
            for item in retrieved_items
            if item.logical_document_id is not None
        }
        if not document_id_by_chunk_id:
            details.update(
                {
                    "not_applicable": True,
                    "reason_code": "citation_document_mapping_unavailable",
                    "gold_source": "expected_document_ids",
                    "matched_citation_count": 0,
                }
            )
            return None, details
        expected_document_ids = set(case.expected_document_ids)
        gold_source = "expected_document_ids"
        matching_citation_count = sum(
            1
            for citation in citations
            if document_id_by_chunk_id.get(citation.document_chunk_id) in expected_document_ids
        )
    elif case.expected_keywords:
        gold_source = "expected_keywords"
        matching_citation_count = sum(
            1
            for citation in citations
            if _keyword_hits(citation.snippet, case.expected_keywords) > 0
        )
    elif case.expected_answer:
        gold_source = "expected_answer"
        matching_citation_count = sum(
            1
            for citation in citations
            if case.expected_answer.casefold() in citation.snippet.casefold()
        )
    else:
        details.update(
            {
                "not_applicable": True,
                "reason_code": "citation_gold_signal_missing",
                "gold_source": "none",
                "matched_citation_count": 0,
            }
        )
        return None, details

    details.update(
        {
            "not_applicable": False,
            "gold_source": gold_source,
            "matched_citation_count": matching_citation_count,
        }
    )
    return _ratio(matching_citation_count, len(citations)), details


def _metadata_string_values(
    metadata_json: dict[str, object] | None,
    key: str,
) -> tuple[str, ...]:
    if not isinstance(metadata_json, dict):
        return ()
    raw_values = metadata_json.get(key)
    if not isinstance(raw_values, list):
        return ()
    values: list[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            continue
        value = " ".join(raw_value.replace("\x00", " ").split())
        if value and value not in values:
            values.append(value)
    return tuple(values)


def _recall_at_k(
    case: EvaluationCase,
    retrieved_items: list[RetrievedEvaluationItem],
    evidence_text: str,
) -> float | None:
    expected_count = _target_count(case)
    if expected_count <= 0:
        return None
    return _ratio(_matched_target_count(case, retrieved_items, evidence_text), expected_count)


def _target_count(case: EvaluationCase) -> int:
    if case.expected_chunk_ids:
        return len(set(case.expected_chunk_ids))
    if case.expected_document_ids:
        return len(set(case.expected_document_ids))
    if case.expected_keywords:
        return len(case.expected_keywords)
    return 1 if case.expected_answer else 0


def _matched_target_count(
    case: EvaluationCase,
    retrieved_items: list[RetrievedEvaluationItem],
    evidence_text: str,
) -> int:
    if case.expected_chunk_ids:
        retrieved_chunk_ids = {item.document_chunk_id for item in retrieved_items}
        return len(set(case.expected_chunk_ids).intersection(retrieved_chunk_ids))
    if case.expected_document_ids:
        retrieved_document_ids = {
            item.logical_document_id
            for item in retrieved_items
            if item.logical_document_id is not None
        }
        return len(set(case.expected_document_ids).intersection(retrieved_document_ids))
    if case.expected_keywords:
        return _keyword_hits(evidence_text, case.expected_keywords)
    return _expected_answer_hit(evidence_text, case)


def _first_relevant_rank(
    case: EvaluationCase,
    retrieved_items: list[RetrievedEvaluationItem],
    evidence_text: str,
) -> int | None:
    if not retrieved_items:
        return None
    expected_chunk_ids = set(case.expected_chunk_ids)
    expected_document_ids = set(case.expected_document_ids)
    if expected_chunk_ids:
        return next(
            (
                item.rank_order
                for item in retrieved_items
                if item.document_chunk_id in expected_chunk_ids
            ),
            None,
        )
    if expected_document_ids:
        return next(
            (
                item.rank_order
                for item in retrieved_items
                if item.logical_document_id in expected_document_ids
            ),
            None,
        )
    if case.expected_keywords:
        for item in retrieved_items:
            haystack = item.snippet.casefold()
            if any(keyword.casefold() in haystack for keyword in case.expected_keywords):
                return item.rank_order
        return None
    if case.expected_answer and case.expected_answer.casefold() in evidence_text.casefold():
        return retrieved_items[0].rank_order
    return None


def _relevance_details(
    case: EvaluationCase,
    retrieved_items: list[RetrievedEvaluationItem],
    *,
    matched_count: int | None,
    rank: int | None,
    not_applicable: bool,
) -> dict[str, object]:
    details: dict[str, object] = {
        "schema_version": EVALUATION_DETAIL_SCHEMA_VERSION,
        "case_key": case.case_id,
        "expected_document_ids_count": len(case.expected_document_ids),
        "expected_chunk_ids_count": len(case.expected_chunk_ids),
        "expected_keyword_count": len(case.expected_keywords),
        "retrieved_count": len(retrieved_items),
        "not_applicable": not_applicable,
    }
    if matched_count is not None:
        details["matched_count"] = matched_count
    if rank is not None:
        details["rank"] = rank
    return details


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


def _optional_label(value: float | None) -> str:
    if value is None:
        return "not_applicable"
    return _label(value)
