from __future__ import annotations

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
    no_context: bool | None = None


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
