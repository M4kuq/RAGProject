from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE, GRAPH_INDEX_RUN_STATUSES

GraphIndexRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_METADATA_KEYS = {
    "raw_document_text",
    "raw_chunk_text",
    "raw_prompt",
    "full_context",
    "chunk_text",
    "document_text",
    "evidence_text",
    "mention_text",
    "secret",
    "token",
    "credential",
    "password",
    "pii",
}


class GraphEntityCreate(BaseModel):
    canonical_name: str = Field(min_length=1, max_length=255)
    entity_type: str = Field(min_length=1, max_length=80)
    aliases_json: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=1000)
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("canonical_name", "entity_type")
    @classmethod
    def normalize_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_graph_metadata(value)


class GraphEntityRead(BaseModel):
    graph_entity_id: int
    canonical_name: str
    entity_type: str
    aliases_json: list[object] = Field(default_factory=list)
    description: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class GraphRelationCreate(BaseModel):
    source_entity_id: int = Field(gt=0)
    target_entity_id: int = Field(gt=0)
    relation_type: str = Field(min_length=1, max_length=120)
    relation_label: str | None = Field(default=None, max_length=255)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    source_document_chunk_id: int | None = Field(default=None, gt=0)
    evidence_text_hash: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("relation_type")
    @classmethod
    def normalize_relation_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("relation_type must not be empty")
        return normalized

    @field_validator("evidence_text_hash", mode="after")
    @classmethod
    def validate_evidence_hash(cls, value: str | None) -> str | None:
        return validate_sha256_or_none(value)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_graph_metadata(value)

    @model_validator(mode="after")
    def validate_distinct_entities(self) -> GraphRelationCreate:
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("source_entity_id and target_entity_id must differ")
        return self


class GraphRelationRead(BaseModel):
    graph_relation_id: int
    source_entity_id: int
    target_entity_id: int
    relation_type: str
    relation_label: str | None = None
    confidence: Decimal | None = None
    source_document_chunk_id: int | None = None
    evidence_text_hash: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class GraphEntityMentionCreate(BaseModel):
    graph_entity_id: int = Field(gt=0)
    document_chunk_id: int = Field(gt=0)
    document_version_id: int = Field(gt=0)
    mention_text_hash: str | None = None
    mention_offset_start: int | None = Field(default=None, ge=0)
    mention_offset_end: int | None = Field(default=None, ge=0)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("mention_text_hash", mode="after")
    @classmethod
    def validate_mention_hash(cls, value: str | None) -> str | None:
        return validate_sha256_or_none(value)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_graph_metadata(value)

    @model_validator(mode="after")
    def validate_offset_order(self) -> GraphEntityMentionCreate:
        if (
            self.mention_offset_start is not None
            and self.mention_offset_end is not None
            and self.mention_offset_end < self.mention_offset_start
        ):
            raise ValueError("mention_offset_end must be >= mention_offset_start")
        return self


class GraphEntityMentionRead(BaseModel):
    graph_entity_mention_id: int
    graph_entity_id: int
    document_chunk_id: int
    document_version_id: int
    mention_text_hash: str | None = None
    mention_offset_start: int | None = None
    mention_offset_end: int | None = None
    confidence: Decimal | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class GraphIndexRunCreate(BaseModel):
    document_version_id: int | None = Field(default=None, gt=0)
    job_id: int | None = Field(default=None, gt=0)
    extractor_type: str = Field(default="none", min_length=1, max_length=80)
    extractor_version: str | None = Field(default=None, max_length=80)
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("metadata_json")
    @classmethod
    def validate_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_graph_metadata(value)


class GraphIndexRunRead(BaseModel):
    graph_index_run_id: int
    document_version_id: int | None = None
    job_id: int | None = None
    status: GraphIndexRunStatus
    extractor_type: str
    extractor_version: str | None = None
    entity_count: int
    relation_count: int
    mention_count: int
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    metadata_json: dict[str, object] = Field(default_factory=dict)


class GraphRetrievalPathCreate(BaseModel):
    retrieval_run_id: int = Field(gt=0)
    path_json: dict[str, object]
    score_breakdown_json: dict[str, object] = Field(default_factory=dict)
    source_chunk_ids_json: list[int] = Field(default_factory=list)

    @field_validator("path_json", "score_breakdown_json")
    @classmethod
    def validate_path_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_graph_metadata(value)


class GraphRetrievalPathRead(BaseModel):
    graph_retrieval_path_id: int
    retrieval_run_id: int
    path_json: dict[str, object]
    score_breakdown_json: dict[str, object] = Field(default_factory=dict)
    source_chunk_ids_json: list[int] = Field(default_factory=list)
    created_at: datetime


class GraphIndexJobPayload(BaseModel):
    job_type: Literal["graph_index_build"] = GRAPH_INDEX_BUILD_JOB_TYPE
    document_version_id: int = Field(gt=0)
    graph_index_run_id: int | None = Field(default=None, gt=0)


class GraphIndexSummary(BaseModel):
    entity_count: int = Field(ge=0)
    relation_count: int = Field(ge=0)
    mention_count: int = Field(ge=0)


def validate_sha256_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("hash must be lowercase sha256 hex")
    return value


def validate_graph_index_status(value: str) -> str:
    if value not in GRAPH_INDEX_RUN_STATUSES:
        raise ValueError("invalid graph index run status")
    return value


def validate_safe_graph_metadata(value: dict[str, object]) -> dict[str, object]:
    _assert_safe_mapping(value)
    return value


def _assert_safe_mapping(value: dict[str, object], *, parent_key: str = "") -> None:
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.lower()
        if lowered.endswith("_hash"):
            pass
        elif any(part in lowered for part in _FORBIDDEN_METADATA_KEYS):
            raise ValueError(f"unsafe graph metadata key: {parent_key}{key}")
        if isinstance(raw_value, dict):
            _assert_safe_mapping(raw_value, parent_key=f"{parent_key}{key}.")
        elif isinstance(raw_value, str) and len(raw_value) > 1000:
            raise ValueError(f"graph metadata string is too long: {parent_key}{key}")
