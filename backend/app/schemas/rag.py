from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag.strategy import (
    DEFAULT_RAG_ASK_REQUEST_STRATEGY,
    DEFAULT_RAG_SEARCH_REQUEST_STRATEGY,
    RagAskRequestStrategy,
    RagSearchRequestStrategy,
    RetrievalStrategy,
)
from app.schemas.documents import DocumentSourceLocator


class RagSearchFilters(BaseModel):
    logical_document_ids: list[int] | None = Field(default=None, min_length=1)
    modality: Literal["text"] = "text"

    @field_validator("logical_document_ids")
    @classmethod
    def validate_logical_document_ids(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        deduped: list[int] = []
        seen: set[int] = set()
        for item in value:
            if item < 1:
                raise ValueError("logical_document_ids must be positive")
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        return deduped


class RagSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    strategy: RagSearchRequestStrategy = DEFAULT_RAG_SEARCH_REQUEST_STRATEGY
    filters: RagSearchFilters | None = None
    cache_bypass: bool = False

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class RagAskRequest(BaseModel):
    chat_session_id: int = Field(ge=1)
    client_message_id: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1, max_length=8000)
    model_key: str | None = Field(default=None, max_length=128)
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    strategy: RagAskRequestStrategy = DEFAULT_RAG_ASK_REQUEST_STRATEGY
    filters: RagSearchFilters | None = None
    cache_bypass: bool = False

    @field_validator("client_message_id")
    @classmethod
    def validate_client_message_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("client_message_id must not be blank")
        return stripped

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message must not be blank")
        return stripped

    @field_validator("model_key")
    @classmethod
    def validate_model_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("model_key must not be blank")
        return stripped


class RetrievalScoreSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    requested_top_k: int
    qdrant_candidate_count: int
    sparse_candidate_count: int | None = None
    post_filter_candidate_count: int
    selected_count: int
    excluded_by_rdb_check_count: int
    top1_retrieval_score: float | None = None
    top3_avg_retrieval_score: float | None = None
    top1_rerank_score: float | None = None


class RetrievalRunDebugSummary(BaseModel):
    retrieval_run_id: int
    origin_type: Literal["chat", "standalone"]
    chat_session_id: int | None = None
    request_message_id: int | None = None
    status: Literal["running", "succeeded", "failed"]
    strategy_type: RetrievalStrategy
    error_code: str | None = None
    query_hash: str | None = None
    top_k: int | None = None
    retrieval_score_summary: dict[str, object] | None = None
    query_plan_json: dict[str, object] | None = None
    strategy_decision_json: dict[str, object] | None = None
    latency_breakdown_json: dict[str, object] | None = None
    retrieval_settings_json: dict[str, object] | None = None
    context_budget_json: dict[str, object] | None = None
    context_compression_json: dict[str, object] | None = None
    tool_result_compression_json: dict[str, object] | None = None
    cache_summary_json: dict[str, object] | None = None
    rerank_score_top1: float | None = None
    answer_confidence: float | None = None
    groundedness_score: float | None = None
    confidence_label: Literal["High", "Medium", "Low"] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class RetrievalRunDebugItem(BaseModel):
    retrieval_run_item_id: int
    document_chunk_id: int
    retrieval_score: float
    rerank_score: float | None = None
    rank_order: int
    rerank_order: int | None = None
    selected_flag: bool
    retrieval_source: str | None = None
    payload_snapshot: dict[str, object] | None = None
    score_breakdown_json: dict[str, object] | None = None
    source_label: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    old_version_flag: bool | None = None
    created_at: datetime


class RetrievalRunDebugResponse(BaseModel):
    retrieval_run: RetrievalRunDebugSummary
    items: list[RetrievalRunDebugItem]


class RetrievalRunDebugListResponse(BaseModel):
    items: list[RetrievalRunDebugSummary]


class GraphDebugNodeRef(BaseModel):
    provider: str
    node_id: str
    entity_id: int | None = None
    safe_label: str
    entity_type: str | None = None


class GraphDebugRelationRef(BaseModel):
    provider: str
    relation_id: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    relation_type: str
    safe_label: str


class GraphDebugSourceMapping(BaseModel):
    source_chunk_id: int = Field(ge=1)
    document_chunk_id: int = Field(ge=1)
    retrieval_run_item_id: int = Field(ge=1)
    selected_flag: bool
    old_version_flag: bool
    citation_ids: list[int] = Field(default_factory=list)
    local_citation_ids: list[int] = Field(default_factory=list)


class GraphPathDebugTrace(BaseModel):
    graph_retrieval_path_id: int = Field(ge=1)
    path_id: str
    provider: str
    validation_status: Literal["valid", "excluded"]
    reason_codes: list[str] = Field(default_factory=list)
    safe_metadata: dict[str, object] = Field(default_factory=dict)
    source_chunk_ids: list[int] = Field(default_factory=list)
    depth: int | None = Field(default=None, ge=0)
    path_score: float | None = Field(default=None, ge=0.0, le=1.0)
    safe_entity_labels: list[str] = Field(default_factory=list)
    relation_types: list[str] = Field(default_factory=list)
    node_refs: list[GraphDebugNodeRef] = Field(default_factory=list)
    relation_refs: list[GraphDebugRelationRef] = Field(default_factory=list)
    source_mappings: list[GraphDebugSourceMapping] = Field(default_factory=list)


class GraphCitationCoverageResponse(BaseModel):
    path_count: int = Field(ge=0)
    valid_path_count: int = Field(ge=0)
    citable_path_count: int = Field(ge=0)
    excluded_path_count: int = Field(ge=0)
    source_chunk_count: int = Field(ge=0)
    resolved_source_chunk_count: int = Field(ge=0)
    citable_source_chunk_count: int = Field(ge=0)
    citation_source_count: int = Field(ge=0)
    source_chunk_coverage_ratio: float = Field(ge=0.0, le=1.0)
    citation_coverage_ratio: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class GraphRunDebugTraceResponse(BaseModel):
    schema_version: str
    retrieval_run_id: int = Field(ge=1)
    graph_path_count: int = Field(ge=0)
    valid_path_count: int = Field(ge=0)
    citable_path_count: int = Field(ge=0)
    excluded_path_count: int = Field(ge=0)
    citation_source_count: int = Field(ge=0)
    coverage: GraphCitationCoverageResponse
    paths: list[GraphPathDebugTrace] = Field(default_factory=list)


class RagSearchItem(BaseModel):
    retrieval_run_item_id: int
    document_chunk_id: int
    source_label: str
    snippet: str
    page_from: int | None = None
    page_to: int | None = None
    retrieval_score: float
    rerank_score: float | None = None
    rank_order: int
    rerank_order: int | None = None
    selected_flag: bool
    payload_snapshot: dict[str, object]


class RagSearchResponse(BaseModel):
    retrieval_run_id: int
    status: Literal["succeeded"]
    retrieval_score_summary: RetrievalScoreSummary
    items: list[RagSearchItem]


class RagAskUserMessage(BaseModel):
    chat_message_id: int
    chat_session_id: int
    role: Literal["user"]
    content: str
    client_message_id: str
    created_at: datetime


class RagAskAssistantMessage(BaseModel):
    chat_message_id: int
    chat_session_id: int
    role: Literal["assistant"]
    content: str
    linked_retrieval_run_id: int
    created_at: datetime


class RagAskCitation(BaseModel):
    citation_id: int = Field(ge=1)
    local_citation_id: int = Field(ge=1)
    document_chunk_id: int = Field(ge=1)
    source_label: str
    snippet: str
    page_from: int | None = None
    page_to: int | None = None
    section_title: str | None = None
    old_version_flag: bool


class RagCitationSourceResponse(DocumentSourceLocator):
    citation_id: int = Field(ge=1)
    local_citation_id: int = Field(ge=1)


class RagAskConfidence(BaseModel):
    answer_confidence: float = Field(ge=0.0, le=1.0)
    groundedness_score: float = Field(ge=0.0, le=1.0)
    confidence_label: Literal["High", "Medium", "Low"]
    # Plain string (not an enum) so future bases (e.g. "calibrated") don't break
    # clients. The label is a heuristic blend of retrieval signals, not a
    # calibrated probability of answer correctness.
    confidence_basis: str = "retrieval_signals"


class RagAskRetrievalSummary(BaseModel):
    retrieval_run_id: int
    strategy_type: RetrievalStrategy
    selected_strategy: str | None = None
    execution_strategy: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    fallback_used: bool | None = None
    fallback_reason: str | None = Field(default=None, max_length=100)
    graph_store_provider: str | None = Field(default=None, max_length=50)
    graph_requested_provider: str | None = Field(default=None, max_length=50)
    graph_fallback_reason_codes: list[str] = Field(default_factory=list)
    no_context: bool | None = None


_GRAPH_FALLBACK_REASON_CODE_ALLOWLIST = frozenset(
    {
        "graph_disabled",
        "graph_fallback_dense",
        "graph_fallback_hybrid",
        "graph_fallback_hybrid_disabled",
        "graph_no_evidence_fallback",
        "graph_store_provider_unavailable",
        "graph_timeout",
        "graph_unavailable",
        "neo4j_connection_failed",
        "neo4j_driver_unavailable",
        "neo4j_not_configured",
        "neo4j_projection_empty",
        "neo4j_query_failed",
        "neo4j_to_postgres_fallback",
        "neo4j_unavailable",
    }
)
_GRAPH_GENERIC_FALLBACK_REASON_CODES = frozenset(
    {
        "graph_fallback_dense",
        "graph_fallback_hybrid",
        "graph_fallback_hybrid_disabled",
        "graph_no_evidence_fallback",
        "graph_store_provider_unavailable",
        "neo4j_to_postgres_fallback",
    }
)


def build_rag_ask_retrieval_summary(
    *,
    retrieval_run_id: int,
    strategy_type: str,
    strategy_decision: Mapping[str, object] | None,
    retrieval_score_summary: Mapping[str, object] | None,
) -> RagAskRetrievalSummary:
    decision = _safe_mapping(strategy_decision)
    score_summary = _safe_mapping(retrieval_score_summary)
    tools_used = _summary_string_list(decision.get("tools_used"), max_length=80)

    explicit_graph_fallback_reason_codes = _filter_graph_fallback_reason_codes(
        _merged_summary_string_list(
            decision.get("graph_fallback_reason_codes"),
            score_summary.get("graph_fallback_reason_codes"),
            max_length=100,
        )
    )
    fallback_used = _summary_fallback_used(
        decision=decision,
        score_summary=score_summary,
        has_graph_fallback_reasons=bool(explicit_graph_fallback_reason_codes),
    )
    graph_fallback_reason_codes = list(explicit_graph_fallback_reason_codes)
    if fallback_used is True:
        _extend_unique(
            graph_fallback_reason_codes,
            _filter_graph_fallback_reason_codes(
                _merged_summary_string_list(
                    decision.get("graph_reason_codes"),
                    score_summary.get("graph_reason_codes"),
                    max_length=100,
                )
            ),
        )

    fallback_reason_value = _summary_fallback_reason_value(
        decision=decision,
        score_summary=score_summary,
    )
    fallback_reason = (
        _primary_graph_fallback_reason(fallback_reason_value, graph_fallback_reason_codes)
        if fallback_used is not False
        else None
    )
    selected_strategy = _summary_strategy_value(
        decision=decision,
        score_summary=score_summary,
        key="selected_strategy",
    )
    execution_strategy = _summary_strategy_value(
        decision=decision,
        score_summary=score_summary,
        key="execution_strategy",
    )
    return RagAskRetrievalSummary(
        retrieval_run_id=retrieval_run_id,
        strategy_type=RetrievalStrategy(strategy_type),
        selected_strategy=selected_strategy,
        execution_strategy=execution_strategy,
        tools_used=tools_used,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        graph_store_provider=_summary_optional_string(
            decision.get("graph_store_provider") or score_summary.get("graph_store_provider"),
            max_length=50,
        ),
        graph_requested_provider=_summary_graph_requested_provider(
            decision=decision,
            score_summary=score_summary,
            selected_strategy=selected_strategy,
        ),
        graph_fallback_reason_codes=graph_fallback_reason_codes,
        no_context=(
            decision.get("no_context") if isinstance(decision.get("no_context"), bool) else None
        ),
    )


def _safe_mapping(value: Mapping[str, object] | None) -> dict[str, object]:
    if value is None:
        return {}
    return {str(key): item for key, item in value.items()}


def _summary_fallback_used(
    *,
    decision: Mapping[str, object],
    score_summary: Mapping[str, object],
    has_graph_fallback_reasons: bool,
) -> bool | None:
    decision_fallback_used = decision.get("fallback_used")
    score_fallback_used = score_summary.get("fallback_used")
    graph_fallback_used = score_summary.get("graph_fallback_used")
    if score_fallback_used is True and decision_fallback_used is not True:
        return True
    if graph_fallback_used is True and decision_fallback_used is not True:
        return True
    if isinstance(decision_fallback_used, bool):
        return decision_fallback_used
    if isinstance(score_fallback_used, bool):
        return score_fallback_used
    if isinstance(graph_fallback_used, bool):
        return graph_fallback_used
    if has_graph_fallback_reasons:
        return True
    return None


def _summary_fallback_reason_value(
    *,
    decision: Mapping[str, object],
    score_summary: Mapping[str, object],
) -> object:
    decision_fallback_used = decision.get("fallback_used")
    score_fallback_used = score_summary.get("fallback_used")
    if score_fallback_used is True and decision_fallback_used is not True:
        return score_summary.get("fallback_reason")
    return decision.get("fallback_reason") or score_summary.get("fallback_reason")


def _summary_strategy_value(
    *,
    decision: Mapping[str, object],
    score_summary: Mapping[str, object],
    key: str,
) -> str | None:
    return _summary_optional_string(decision.get(key) or score_summary.get(key))


def _summary_graph_requested_provider(
    *,
    decision: Mapping[str, object],
    score_summary: Mapping[str, object],
    selected_strategy: str | None,
) -> str | None:
    explicit_provider = _summary_optional_string(
        decision.get("graph_requested_provider") or score_summary.get("graph_requested_provider"),
        max_length=50,
    )
    if explicit_provider is not None:
        return explicit_provider
    if selected_strategy == "graph_neo4j":
        return "neo4j"
    if selected_strategy == "graph_postgres":
        return "postgres"
    return None


def _primary_graph_fallback_reason(
    fallback_reason_value: object,
    graph_fallback_reason_codes: list[str],
) -> str | None:
    fallback_reason = _summary_optional_string(fallback_reason_value, max_length=100)
    if fallback_reason and fallback_reason not in _GRAPH_GENERIC_FALLBACK_REASON_CODES:
        return fallback_reason
    for code in graph_fallback_reason_codes:
        if code not in _GRAPH_GENERIC_FALLBACK_REASON_CODES:
            return code
    return fallback_reason or (
        graph_fallback_reason_codes[0] if graph_fallback_reason_codes else None
    )


def _filter_graph_fallback_reason_codes(reason_codes: list[str]) -> list[str]:
    return [code for code in reason_codes if _is_graph_fallback_reason_code(code)]


def _is_graph_fallback_reason_code(reason_code: str) -> bool:
    if reason_code in _GRAPH_FALLBACK_REASON_CODE_ALLOWLIST:
        return True
    if reason_code.startswith("graph_fallback_"):
        return True
    if not reason_code.startswith("neo4j_"):
        return False
    return any(
        token in reason_code
        for token in (
            "empty",
            "failed",
            "fallback",
            "not_configured",
            "unavailable",
        )
    )


def _merged_summary_string_list(*values: object, max_length: int) -> list[str]:
    merged: list[str] = []
    for value in values:
        _extend_unique(merged, _summary_string_list(value, max_length=max_length))
    return merged


def _summary_string_list(value: object, *, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    safe_values: list[str] = []
    for item in value:
        safe = _summary_optional_string(item, max_length=max_length)
        if safe is not None:
            safe_values.append(safe)
    return safe_values


def _extend_unique(values: list[str], additions: list[str]) -> None:
    for item in additions:
        if item not in values:
            values.append(item)


def _summary_optional_string(value: object, *, max_length: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    printable = "".join(char if char.isprintable() else " " for char in value)
    sanitized = " ".join(printable.replace("\x00", " ").split())
    return sanitized[:max_length] if sanitized else None


class RagAskGeneration(BaseModel):
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: int | None = None


class RagAskResponse(BaseModel):
    chat_session_id: int
    user_message: RagAskUserMessage
    assistant_message: RagAskAssistantMessage
    citations: list[RagAskCitation]
    confidence: RagAskConfidence
    retrieval_run_id: int
    retrieval_summary: RagAskRetrievalSummary
    replayed: bool = False
    generation: RagAskGeneration | None = None
