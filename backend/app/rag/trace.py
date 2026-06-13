from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from app.core.config import Settings
from app.rag.retrieval import RetrievalFilters
from app.rag.strategy import (
    DEFAULT_RETRIEVAL_STRATEGY,
    FusionMethod,
    RetrievalSource,
    RetrievalStrategy,
)
from app.schemas.rag_strategy import (
    SENSITIVE_TRACE_KEY_PARTS,
    LatencyBreakdown,
    QueryPlanTrace,
    RetrievalSettingsSnapshot,
    RouterDecisionTrace,
    ScoreBreakdown,
    StrategyDecisionTrace,
)

TRACE_SCHEMA_VERSION = "phase2.trace.v1"

_RETRIEVAL_LATENCY_KEYS = (
    "sufficiency_check_ms",
    "merge_dedupe_ms",
    "rerank_after_merge_ms",
    "llm_orchestrator_ms",
    "llm_tool_planning_ms",
    "llm_tool_execution_ms",
    "langchain_agentic_ms",
    "langchain_planning_ms",
    "langchain_tool_execution_ms",
    "langgraph_agentic_ms",
    "langgraph_planning_ms",
    "langgraph_tool_execution_ms",
    "strategy_router_ms",
    "query_embedding_ms",
    "qdrant_search_ms",
    "sparse_search_ms",
    "graph_search_ms",
    "fusion_ms",
    "rdb_final_check_ms",
    "rerank_ms",
    "retrieval_items_persist_ms",
)
_LLM_ORCHESTRATOR_NESTED_LATENCY_KEYS = frozenset(
    {
        "llm_tool_planning_ms",
        "llm_tool_execution_ms",
        "langchain_planning_ms",
        "langchain_tool_execution_ms",
        "langgraph_planning_ms",
        "langgraph_tool_execution_ms",
        "query_embedding_ms",
        "qdrant_search_ms",
        "sparse_search_ms",
        "fusion_ms",
        "rdb_final_check_ms",
        "rerank_ms",
    }
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|\s)(?:export\s+)?"
    r"([A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*)"
    r"\s*[:=]\s*\S+"
)
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


class TraceRedactor:
    @classmethod
    def safe_dict(cls, payload: Mapping[str, Any]) -> dict[str, object]:
        redacted = cls.redact(payload)
        if not isinstance(redacted, dict):
            return {}
        return redacted

    @classmethod
    def redact(cls, value: Any) -> object:
        if isinstance(value, Mapping):
            safe: dict[str, object] = {}
            for key, nested in value.items():
                key_text = str(key)
                if cls.is_sensitive_key(key_text):
                    continue
                safe[key_text] = cls.redact(nested)
            return safe
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            return [cls.redact(item) for item in value]
        if isinstance(value, str):
            return cls.safe_string(value)
        return value

    @classmethod
    def safe_string(cls, value: str, *, max_length: int = 255) -> str:
        normalized = " ".join(value.replace("\x00", " ").split())
        if (
            _SECRET_ASSIGNMENT_RE.search(normalized)
            or _URL_RE.search(normalized)
            or _EMAIL_RE.search(normalized)
        ):
            return "redacted"
        return normalized[:max_length]

    @staticmethod
    def is_sensitive_key(key: str) -> bool:
        key_text = key.lower()
        return any(part in key_text for part in SENSITIVE_TRACE_KEY_PARTS)


class LatencyTracker:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started_at = clock()
        self._spans_ms: dict[str, int] = {}

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        started_at = self._clock()
        try:
            yield
        finally:
            self.record_ms(name, _elapsed_ms(started_at, self._clock()))

    def record_ms(self, name: str, duration_ms: int) -> None:
        safe_duration = max(0, int(duration_ms))
        self._spans_ms[name] = self._spans_ms.get(name, 0) + safe_duration

    def snapshot(self) -> dict[str, object]:
        spans: dict[str, int] = dict(self._spans_ms)
        retrieval_latency_keys = _retrieval_latency_keys_for(spans)
        retrieval_ms = sum(spans[key] for key in retrieval_latency_keys if key in spans)
        if retrieval_ms > 0:
            spans["retrieval_ms"] = retrieval_ms
        spans["total_ms"] = _elapsed_ms(self._started_at, self._clock())
        return build_latency_breakdown(**spans)


def build_default_dense_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    plan_metadata: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    logical_document_filter_count = len(filters.logical_document_ids or ())
    metadata_filter_applied = logical_document_filter_count > 0 or filters.modality != "text"
    trace = QueryPlanTrace(
        strategy_type=DEFAULT_RETRIEVAL_STRATEGY,
        query_mode="single_query",
        query_hash=query_hash,
        rewrite_applied=False,
        sub_query_count=0,
        metadata_filter_applied=metadata_filter_applied,
        metadata_filter_count=logical_document_filter_count,
        logical_document_filter_count=logical_document_filter_count,
        reason_codes=["phase1_compat_default_dense"],
    )
    return _safe_query_plan(trace, plan_metadata=plan_metadata)


def build_default_dense_strategy_decision() -> dict[str, object]:
    trace = StrategyDecisionTrace(
        selected_strategy=DEFAULT_RETRIEVAL_STRATEGY,
        fallback_used=False,
        router_enabled=False,
        decision_source="default",
        decision_policy="static_dense",
        reason_codes=["phase1_compat_default_dense"],
    )
    payload = trace.model_dump(mode="json", exclude_none=True)
    payload["execution_strategy"] = DEFAULT_RETRIEVAL_STRATEGY.value
    return TraceRedactor.safe_dict(payload)


def build_sparse_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    normalized_term_count: int,
    plan_metadata: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    logical_document_filter_count = len(filters.logical_document_ids or ())
    metadata_filter_applied = logical_document_filter_count > 0 or filters.modality != "text"
    trace = QueryPlanTrace(
        strategy_type=RetrievalStrategy.SPARSE,
        query_mode="single_query",
        query_hash=query_hash,
        rewrite_applied=False,
        sub_query_count=0,
        metadata_filter_applied=metadata_filter_applied,
        metadata_filter_count=logical_document_filter_count,
        logical_document_filter_count=logical_document_filter_count,
        reason_codes=[
            "phase2_sparse_lexical",
            f"normalized_terms:{normalized_term_count}",
        ],
    )
    return _safe_query_plan(trace, plan_metadata=plan_metadata)


def build_sparse_strategy_decision() -> dict[str, object]:
    trace = StrategyDecisionTrace(
        selected_strategy=RetrievalStrategy.SPARSE,
        fallback_used=False,
        router_enabled=False,
        decision_source="request",
        decision_policy="explicit_sparse",
        reason_codes=["explicit_strategy_sparse"],
    )
    payload = trace.model_dump(mode="json", exclude_none=True)
    payload["execution_strategy"] = RetrievalStrategy.SPARSE.value
    return TraceRedactor.safe_dict(payload)


def build_hybrid_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    normalized_term_count: int,
    fusion_method: FusionMethod,
    plan_metadata: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    logical_document_filter_count = len(filters.logical_document_ids or ())
    metadata_filter_applied = logical_document_filter_count > 0 or filters.modality != "text"
    trace = QueryPlanTrace(
        strategy_type=RetrievalStrategy.HYBRID,
        query_mode="dense_sparse_single_query",
        query_hash=query_hash,
        rewrite_applied=False,
        sub_query_count=0,
        metadata_filter_applied=metadata_filter_applied,
        metadata_filter_count=logical_document_filter_count,
        logical_document_filter_count=logical_document_filter_count,
        reason_codes=[
            "phase2_hybrid_dense_sparse",
            f"fusion_method:{fusion_method.value}",
            f"normalized_terms:{normalized_term_count}",
        ],
    )
    return _safe_query_plan(trace, plan_metadata=plan_metadata)


def build_hybrid_strategy_decision(*, fusion_method: FusionMethod) -> dict[str, object]:
    trace = StrategyDecisionTrace(
        selected_strategy=RetrievalStrategy.HYBRID,
        fallback_used=False,
        router_enabled=False,
        decision_source="request",
        decision_policy=f"explicit_hybrid_{fusion_method.value}",
        reason_codes=["explicit_strategy_hybrid", f"fusion_method:{fusion_method.value}"],
    )
    payload = trace.model_dump(mode="json", exclude_none=True)
    payload["execution_strategy"] = RetrievalStrategy.HYBRID.value
    return TraceRedactor.safe_dict(payload)


def build_router_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    execution_strategy: RetrievalStrategy,
    plan_metadata: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    logical_document_filter_count = len(filters.logical_document_ids or ())
    metadata_filter_applied = logical_document_filter_count > 0 or filters.modality != "text"
    trace = QueryPlanTrace(
        strategy_type=RetrievalStrategy.AGENTIC_ROUTER,
        query_mode="router_single_strategy",
        query_hash=query_hash,
        rewrite_applied=False,
        sub_query_count=0,
        metadata_filter_applied=metadata_filter_applied,
        metadata_filter_count=logical_document_filter_count,
        logical_document_filter_count=logical_document_filter_count,
        reason_codes=[
            "phase2_agentic_router",
            f"execution_strategy:{execution_strategy.value}",
        ],
    )
    return _safe_query_plan(
        trace,
        plan_metadata=_router_plan_metadata(plan_metadata),
    )


def build_router_strategy_decision(*, decision: RouterDecisionTrace) -> dict[str, object] | None:
    if not decision.store_decision_trace:
        return None
    return TraceRedactor.safe_dict(decision.model_dump(mode="json", exclude_none=True))


def build_retrieval_settings_snapshot(
    *,
    settings: Settings,
    top_k: int,
    rerank_top_n: int,
    filters: RetrievalFilters,
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
) -> dict[str, object]:
    snapshot = RetrievalSettingsSnapshot(
        strategy_type=strategy_type,
        default_strategy=DEFAULT_RETRIEVAL_STRATEGY,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        embedding_provider=TraceRedactor.safe_string(settings.embedding_provider, max_length=100),
        rerank_provider=TraceRedactor.safe_string(settings.rerank_provider, max_length=100),
        generation_provider=TraceRedactor.safe_string(
            settings.generation_provider,
            max_length=100,
        ),
        qdrant_collection=TraceRedactor.safe_string(
            settings.qdrant_collection_name,
            max_length=255,
        ),
        rdb_final_check_enabled=True,
        modality=filters.modality,
        logical_document_filter_count=len(filters.logical_document_ids or []),
        hybrid_enabled=bool(settings.hybrid_enabled),
        router_enabled=bool(
            strategy_type == RetrievalStrategy.AGENTIC_ROUTER and settings.router_enabled
        ),
        trace_enabled=True,
        evidence_pack_enabled=bool(settings.evidence_pack_enabled),
        evidence_pack_max_items=settings.evidence_pack_max_items,
        evidence_pack_max_items_per_source=settings.evidence_pack_max_items_per_source,
        evidence_pack_max_chars_per_item=settings.evidence_pack_max_chars_per_item,
        evidence_pack_max_total_chars=settings.evidence_pack_max_total_chars,
        evidence_pack_near_duplicate_threshold=round(
            float(settings.evidence_pack_near_duplicate_threshold),
            6,
        ),
        fusion_method=FusionMethod(settings.hybrid_fusion_method),
        hybrid_rrf_k=settings.hybrid_rrf_k,
        hybrid_dense_weight=round(float(settings.hybrid_dense_weight), 6),
        hybrid_sparse_weight=round(float(settings.hybrid_sparse_weight), 6),
        hybrid_candidate_multiplier=settings.hybrid_candidate_multiplier,
        router_mode=(
            TraceRedactor.safe_string(settings.router_mode, max_length=30)
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        router_fallback_strategy=(
            RetrievalStrategy.FALLBACK_DENSE
            if settings.router_fallback_strategy == RetrievalStrategy.FALLBACK_DENSE.value
            else RetrievalStrategy.DENSE
        )
        if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
        else None,
        router_allow_agentic_search=(
            bool(settings.router_allow_agentic_search)
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        router_allow_agentic_ask=(
            bool(settings.router_allow_agentic_ask)
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        max_retrieval_calls=(
            settings.router_max_retrieval_calls
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        max_fallback_calls=(
            settings.router_max_fallback_calls
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        sufficiency_min_candidates=(
            settings.router_sufficiency_min_candidates
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        sufficiency_min_selected=(
            settings.router_sufficiency_min_selected
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        sufficiency_top_score_threshold=(
            round(float(settings.router_sufficiency_top_score_threshold), 6)
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        no_context_after_budget_exhausted=(
            bool(settings.router_no_context_after_budget_exhausted)
            if strategy_type == RetrievalStrategy.AGENTIC_ROUTER
            else None
        ),
        sparse_provider=(
            TraceRedactor.safe_string(settings.sparse_provider, max_length=100)
            if strategy_type
            in {
                RetrievalStrategy.SPARSE,
                RetrievalStrategy.HYBRID,
                RetrievalStrategy.AGENTIC_ROUTER,
                RetrievalStrategy.LANGCHAIN_AGENTIC,
                RetrievalStrategy.LANGGRAPH_AGENTIC,
            }
            else None
        ),
        sparse_language=(
            TraceRedactor.safe_string(settings.sparse_language, max_length=30)
            if strategy_type
            in {
                RetrievalStrategy.SPARSE,
                RetrievalStrategy.HYBRID,
                RetrievalStrategy.AGENTIC_ROUTER,
                RetrievalStrategy.LANGCHAIN_AGENTIC,
                RetrievalStrategy.LANGGRAPH_AGENTIC,
            }
            else None
        ),
        sparse_score_normalization=(
            TraceRedactor.safe_string(settings.sparse_score_normalization, max_length=30)
            if strategy_type
            in {
                RetrievalStrategy.SPARSE,
                RetrievalStrategy.HYBRID,
                RetrievalStrategy.AGENTIC_ROUTER,
                RetrievalStrategy.LANGCHAIN_AGENTIC,
                RetrievalStrategy.LANGGRAPH_AGENTIC,
            }
            else None
        ),
    )
    return TraceRedactor.safe_dict(snapshot.model_dump(mode="json", exclude_none=True))


def build_latency_breakdown(**spans_ms: int | None) -> dict[str, object]:
    trace = LatencyBreakdown(**{key: value for key, value in spans_ms.items() if value is not None})
    return TraceRedactor.safe_dict(trace.model_dump(mode="json", exclude_none=True))


def build_dense_score_breakdown(
    *,
    dense_score: float,
    rank_order: int,
    rerank_score: float | None,
    rerank_order: int | None,
    final_rank: int,
    selected_flag: bool,
    retrieval_source: RetrievalSource = RetrievalSource.DENSE,
) -> dict[str, object]:
    breakdown = ScoreBreakdown(
        retrieval_source=retrieval_source,
        dense_score=round(float(dense_score), 6),
        rerank_score=round(float(rerank_score), 6) if rerank_score is not None else None,
        rank_order=rank_order,
        rerank_order=rerank_order,
        final_rank=final_rank,
        selected_flag=selected_flag,
    )
    return TraceRedactor.safe_dict(breakdown.model_dump(mode="json", exclude_none=True))


def build_sparse_score_breakdown(
    *,
    sparse_score: float,
    rank_order: int,
    final_rank: int,
    selected_flag: bool,
) -> dict[str, object]:
    breakdown = ScoreBreakdown(
        retrieval_source=RetrievalSource.SPARSE,
        sparse_score=round(float(sparse_score), 6),
        rank_order=rank_order,
        final_rank=final_rank,
        selected_flag=selected_flag,
    )
    return TraceRedactor.safe_dict(breakdown.model_dump(mode="json", exclude_none=True))


def build_hybrid_score_breakdown(
    *,
    dense_score: float | None,
    sparse_score: float | None,
    fused_score: float,
    rank_order: int,
    final_rank: int,
    selected_flag: bool,
    fusion_method: FusionMethod,
    dense_rank: int | None,
    sparse_rank: int | None,
) -> dict[str, object]:
    breakdown = ScoreBreakdown(
        retrieval_source=RetrievalSource.HYBRID,
        dense_score=round(float(dense_score), 6) if dense_score is not None else None,
        sparse_score=round(float(sparse_score), 6) if sparse_score is not None else None,
        fused_score=round(float(fused_score), 6),
        rank_order=rank_order,
        final_rank=final_rank,
        selected_flag=selected_flag,
        fusion_method=fusion_method.value,
        dense_rank=dense_rank,
        sparse_rank=sparse_rank,
    )
    return TraceRedactor.safe_dict(breakdown.model_dump(mode="json", exclude_none=True))


def _elapsed_ms(started_at: float, finished_at: float) -> int:
    return max(0, int(round((finished_at - started_at) * 1000)))


def _retrieval_latency_keys_for(spans: Mapping[str, int]) -> tuple[str, ...]:
    if (
        "llm_orchestrator_ms" not in spans
        and "langchain_agentic_ms" not in spans
        and "langgraph_agentic_ms" not in spans
    ):
        return _RETRIEVAL_LATENCY_KEYS
    return tuple(
        key for key in _RETRIEVAL_LATENCY_KEYS if key not in _LLM_ORCHESTRATOR_NESTED_LATENCY_KEYS
    )


def _safe_query_plan(
    trace: QueryPlanTrace,
    *,
    plan_metadata: Mapping[str, Any] | None,
) -> dict[str, object]:
    payload: dict[str, Any] = trace.model_dump(mode="json", exclude_none=True)
    if plan_metadata:
        payload.update(plan_metadata)
    return TraceRedactor.safe_dict(payload)


def _router_plan_metadata(plan_metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not plan_metadata:
        return None
    metadata = _clean_router_plan_value(plan_metadata)
    if not isinstance(metadata, dict):
        return None
    return metadata


def _clean_router_plan_value(value: Any) -> object:
    if isinstance(value, Mapping):
        cleaned: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            if key_text == "disabled_reason" and nested == "strategy_router_not_implemented":
                continue
            cleaned[key_text] = _clean_router_plan_value(nested)
        safety_flags = cleaned.get("safety_flags")
        if isinstance(safety_flags, Sequence) and not isinstance(
            safety_flags, str | bytes | bytearray
        ):
            cleaned["safety_flags"] = _router_executed_safety_flags(safety_flags)
        return cleaned
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_clean_router_plan_value(item) for item in value]
    return value


def _router_executed_safety_flags(safety_flags: Sequence[Any]) -> list[object]:
    normalized = [
        flag for flag in safety_flags if flag not in {"planned_only", "router_not_executed"}
    ]
    if "router_executed" not in safety_flags:
        normalized.append("router_executed")
    return normalized
