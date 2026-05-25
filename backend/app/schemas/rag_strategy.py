from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.strategy import (
    DEFAULT_RETRIEVAL_STRATEGY,
    FusionMethod,
    RetrievalSource,
    RetrievalStrategy,
    RouterFallbackStrategy,
)

SENSITIVE_TRACE_KEY_PARTS = (
    "api_key",
    "chunk_text",
    "content_text",
    "credential",
    "full_context",
    "password",
    "pii",
    "prompt",
    "raw_chunk",
    "raw_text",
    "secret",
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
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    query_hash: str | None = Field(default=None, min_length=64, max_length=64)
    rewritten_query_hash: str | None = Field(default=None, min_length=64, max_length=64)
    sub_query_count: int = Field(default=0, ge=0)
    metadata_filter_count: int = Field(default=0, ge=0)
    reason_codes: list[str] = Field(default_factory=list)


class StrategyDecisionTrace(SafeTraceModel):
    selected_strategy: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    fallback_strategy: RouterFallbackStrategy = RouterFallbackStrategy.DENSE
    fallback_used: bool = False
    decision_policy: str = Field(default="static_dense", max_length=100)
    reason_codes: list[str] = Field(default_factory=list)


class LatencyBreakdown(SafeTraceModel):
    retrieval_ms: int | None = Field(default=None, ge=0)
    rerank_ms: int | None = Field(default=None, ge=0)
    generation_ms: int | None = Field(default=None, ge=0)
    total_ms: int | None = Field(default=None, ge=0)


class RetrievalSettingsSnapshot(SafeTraceModel):
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    default_strategy: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    top_k: int = Field(ge=1, le=20)
    rerank_top_n: int = Field(ge=1, le=20)
    modality: str = Field(default="text", max_length=30)
    logical_document_filter_count: int = Field(default=0, ge=0)
    hybrid_enabled: bool = False
    router_enabled: bool = False
    trace_enabled: bool = True
    fusion_method: FusionMethod = FusionMethod.RRF


class ScoreBreakdown(SafeTraceModel):
    retrieval_source: RetrievalSource = RetrievalSource.DENSE
    dense_score: float | None = None
    sparse_score: float | None = None
    fused_score: float | None = None
    rerank_score: float | None = None
    rank_order: int = Field(ge=1)
    rerank_order: int | None = Field(default=None, ge=1)
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
