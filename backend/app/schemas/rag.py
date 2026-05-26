from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy


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
    strategy: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY
    filters: RagSearchFilters | None = None

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
    filters: RagSearchFilters | None = None

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
    requested_top_k: int
    qdrant_candidate_count: int
    sparse_candidate_count: int | None = None
    post_filter_candidate_count: int
    selected_count: int
    excluded_by_rdb_check_count: int
    top1_retrieval_score: float | None = None
    top3_avg_retrieval_score: float | None = None
    top1_rerank_score: float | None = None


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


class RagAskConfidence(BaseModel):
    answer_confidence: float = Field(ge=0.0, le=1.0)
    groundedness_score: float = Field(ge=0.0, le=1.0)
    confidence_label: Literal["High", "Medium", "Low"]


class RagAskResponse(BaseModel):
    chat_session_id: int
    user_message: RagAskUserMessage
    assistant_message: RagAskAssistantMessage
    citations: list[RagAskCitation]
    confidence: RagAskConfidence
    retrieval_run_id: int
    replayed: bool = False
