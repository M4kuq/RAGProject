from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.strategy import (
    DEFAULT_FUSION_METHOD,
    DEFAULT_RETRIEVAL_STRATEGY,
    FusionMethod,
    RetrievalSource,
    RetrievalStrategy,
    RouterFallbackStrategy,
)

TRACE_SCHEMA_VERSION: Literal["phase2.trace.v1"] = "phase2.trace.v1"

SENSITIVE_TRACE_KEY_PARTS = (
    "api-key",
    "api_key",
    "apikey",
    "chunk_text",
    "content_text",
    "credential",
    "cookie",
    "csrf",
    "full_context",
    "password",
    "pii",
    "private_key",
    "prompt",
    "raw_chunk",
    "raw_text",
    "secret",
    "session",
    "token",
)


class SafeTraceModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def reject_sensitive_trace_keys(cls, data: Any) -> Any:
        _reject_sensitive_keys(data)
        return data


class QueryPlanTrace(SafeTraceModel):
    schema_version: Literal["phase2.trace.v1"] = TRACE_SCHEMA_VERSION
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    query_mode: str = Field(default="single_query", max_length=50)
    query_hash: str | None = Field(default=None, min_length=64, max_length=64)
    rewritten_query_hash: str | None = Field(default=None, min_length=64, max_length=64)
    rewrite_applied: bool = False
    sub_query_count: int = Field(default=0, ge=0)
    metadata_filter_applied: bool = False
    metadata_filter_count: int = Field(default=0, ge=0)
    logical_document_filter_count: int = Field(default=0, ge=0)
    reason_codes: list[str] = Field(default_factory=list)


class StrategyDecisionTrace(SafeTraceModel):
    schema_version: Literal["phase2.trace.v1"] = TRACE_SCHEMA_VERSION
    selected_strategy: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    fallback_strategy: RouterFallbackStrategy = RouterFallbackStrategy.DENSE
    fallback_used: bool = False
    router_enabled: bool = False
    decision_source: str = Field(default="default", max_length=100)
    decision_policy: str = Field(default="static_dense", max_length=100)
    reason_codes: list[str] = Field(default_factory=list)


class LatencyBreakdown(SafeTraceModel):
    schema_version: Literal["phase2.trace.v1"] = TRACE_SCHEMA_VERSION
    total_ms: int | None = Field(default=None, ge=0)
    retrieval_ms: int | None = Field(default=None, ge=0)
    query_embedding_ms: int | None = Field(default=None, ge=0)
    qdrant_search_ms: int | None = Field(default=None, ge=0)
    sparse_search_ms: int | None = Field(default=None, ge=0)
    fusion_ms: int | None = Field(default=None, ge=0)
    rdb_final_check_ms: int | None = Field(default=None, ge=0)
    rerank_ms: int | None = Field(default=None, ge=0)
    retrieval_items_persist_ms: int | None = Field(default=None, ge=0)
    context_assembly_ms: int | None = Field(default=None, ge=0)
    generation_ms: int | None = Field(default=None, ge=0)
    citation_build_ms: int | None = Field(default=None, ge=0)
    confidence_ms: int | None = Field(default=None, ge=0)


class RetrievalSettingsSnapshot(SafeTraceModel):
    schema_version: Literal["phase2.trace.v1"] = TRACE_SCHEMA_VERSION
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    default_strategy: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    top_k: int = Field(ge=1, le=20)
    rerank_top_n: int = Field(ge=1, le=20)
    embedding_provider: str | None = Field(default=None, max_length=100)
    rerank_provider: str | None = Field(default=None, max_length=100)
    generation_provider: str | None = Field(default=None, max_length=100)
    qdrant_collection: str | None = Field(default=None, max_length=255)
    rdb_final_check_enabled: bool = True
    modality: str = Field(default="text", max_length=30)
    logical_document_filter_count: int = Field(default=0, ge=0)
    hybrid_enabled: bool = False
    router_enabled: bool = False
    trace_enabled: bool = True
    fusion_method: FusionMethod = DEFAULT_FUSION_METHOD
    sparse_provider: str | None = Field(default=None, max_length=100)
    sparse_language: str | None = Field(default=None, max_length=30)
    sparse_score_normalization: str | None = Field(default=None, max_length=30)
    hybrid_rrf_k: int | None = Field(default=None, ge=1)
    hybrid_dense_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    hybrid_sparse_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    hybrid_candidate_multiplier: int | None = Field(default=None, ge=1)


class ScoreBreakdown(SafeTraceModel):
    schema_version: Literal["phase2.trace.v1"] = TRACE_SCHEMA_VERSION
    retrieval_source: RetrievalSource = RetrievalSource.DENSE
    dense_score: float | None = None
    sparse_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    rank_order: int = Field(ge=1)
    rerank_order: int | None = Field(default=None, ge=1)
    final_rank: int | None = Field(default=None, ge=1)
    selected_flag: bool


class StrategyEvaluationMetricSpec(SafeTraceModel):
    metric_name: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    higher_is_better: bool = True
    min_value: float = Field(default=0.0, ge=0.0, le=1.0)
    max_value: float = Field(default=1.0, ge=0.0, le=1.0)


def _reject_sensitive_keys(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_TRACE_KEY_PARTS):
                raise ValueError(f"trace field is not allowed: {key}")
            _reject_sensitive_keys(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_sensitive_keys(nested)
