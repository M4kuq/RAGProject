from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE, GRAPH_INDEX_RUN_STATUSES

GraphIndexRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_LABEL_RE = re.compile(
    r"(raw[_ -]?(document|chunk|prompt)|full[_ -]?context|"
    r"evidence[_ -]?text|mention[_ -]?text|"
    r"password\s*[:=]|secret\s*[:=]|token\s*[:=]|credential\s*[:=]|"
    r"api[_ -]?key\s*[:=]|bearer\s+)",
    re.IGNORECASE,
)
_FORBIDDEN_METADATA_KEYS = {
    "raw_document_text",
    "raw_chunk_text",
    "raw_prompt",
    "full_context",
    "chunk_text",
    "document_text",
    "evidence_text",
    "mention_text",
    "api_key",
    "apikey",
    "api-key",
    "api key",
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
    description: str | None = Field(default=None, max_length=240)
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("canonical_name")
    @classmethod
    def validate_canonical_name(cls, value: str) -> str:
        return validate_safe_graph_label(value, field_name="canonical_name", max_length=255)

    @field_validator("entity_type")
    @classmethod
    def validate_entity_type(cls, value: str) -> str:
        return validate_safe_graph_label(value, field_name="entity_type", max_length=80)

    @field_validator("aliases_json")
    @classmethod
    def validate_aliases(cls, value: list[str]) -> list[str]:
        if len(value) > 32:
            raise ValueError("aliases_json must contain at most 32 aliases")
        normalized_aliases: list[str] = []
        seen: set[str] = set()
        for alias in value:
            normalized = validate_safe_graph_label(alias, field_name="aliases_json", max_length=120)
            dedupe_key = normalized.lower()
            if dedupe_key not in seen:
                normalized_aliases.append(normalized)
                seen.add(dedupe_key)
        return normalized_aliases

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_graph_label(value, field_name="description", max_length=240)

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
    source_entity_id: StrictInt = Field(gt=0)
    target_entity_id: StrictInt = Field(gt=0)
    relation_type: str = Field(min_length=1, max_length=120)
    relation_label: str | None = Field(default=None, max_length=120)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    source_document_chunk_id: StrictInt | None = Field(default=None, gt=0)
    evidence_text_hash: str | None = None
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("relation_type")
    @classmethod
    def validate_relation_type(cls, value: str) -> str:
        return validate_safe_graph_label(value, field_name="relation_type", max_length=120)

    @field_validator("relation_label")
    @classmethod
    def validate_relation_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_graph_label(value, field_name="relation_label", max_length=120)

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
    graph_entity_id: StrictInt = Field(gt=0)
    document_chunk_id: StrictInt = Field(gt=0)
    document_version_id: StrictInt = Field(gt=0)
    mention_text_hash: str | None = None
    mention_offset_start: StrictInt | None = Field(default=None, ge=0)
    mention_offset_end: StrictInt | None = Field(default=None, ge=0)
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
    document_version_id: StrictInt | None = Field(default=None, gt=0)
    job_id: StrictInt | None = Field(default=None, gt=0)
    extractor_type: str = Field(default="none", min_length=1, max_length=80)
    extractor_version: str | None = Field(default=None, max_length=80)
    metadata_json: dict[str, object] = Field(default_factory=dict)

    @field_validator("extractor_type")
    @classmethod
    def validate_extractor_type(cls, value: str) -> str:
        return validate_safe_graph_label(value, field_name="extractor_type", max_length=80)

    @field_validator("extractor_version")
    @classmethod
    def validate_extractor_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_graph_label(value, field_name="extractor_version", max_length=80)

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
    retrieval_run_id: StrictInt = Field(gt=0)
    path_json: dict[str, object]
    score_breakdown_json: dict[str, object] = Field(default_factory=dict)
    source_chunk_ids_json: list[StrictInt] = Field(default_factory=list)

    @field_validator("path_json", "score_breakdown_json")
    @classmethod
    def validate_path_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_graph_metadata(value)

    @field_validator("source_chunk_ids_json")
    @classmethod
    def validate_source_chunk_ids(cls, value: list[int]) -> list[int]:
        for chunk_id in value:
            if isinstance(chunk_id, bool) or not isinstance(chunk_id, int) or chunk_id <= 0:
                raise ValueError("source_chunk_ids_json must contain positive integer ids")
        return value


class GraphRetrievalPathRead(BaseModel):
    graph_retrieval_path_id: int
    retrieval_run_id: int
    path_json: dict[str, object]
    score_breakdown_json: dict[str, object] = Field(default_factory=dict)
    source_chunk_ids_json: list[int] = Field(default_factory=list)
    created_at: datetime


class GraphIndexJobPayload(BaseModel):
    job_type: Literal["graph_index_build"] = GRAPH_INDEX_BUILD_JOB_TYPE
    document_version_id: StrictInt = Field(gt=0)
    graph_index_run_id: StrictInt | None = Field(default=None, gt=0)


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


def validate_safe_graph_label(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if any(char in normalized for char in ("\n", "\r", "\t")):
        raise ValueError(f"{field_name} must be a single-line safe label")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    if _UNSAFE_LABEL_RE.search(normalized):
        raise ValueError(f"{field_name} contains unsafe graph text")
    return normalized


def validate_safe_graph_metadata(value: dict[str, object]) -> dict[str, object]:
    _assert_safe_mapping(value)
    return value


def _assert_safe_mapping(value: Mapping[Any, object], *, parent_key: str = "") -> None:
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        lowered = key.lower()
        has_forbidden_part = any(part in lowered for part in _FORBIDDEN_METADATA_KEYS)
        if has_forbidden_part:
            if lowered.endswith("_hash"):
                if not isinstance(raw_value, str) or not _SHA256_RE.fullmatch(raw_value):
                    raise ValueError(f"unsafe graph metadata hash value: {parent_key}{key}")
            else:
                raise ValueError(f"unsafe graph metadata key: {parent_key}{key}")
        _assert_safe_metadata_value(raw_value, parent_key=f"{parent_key}{key}")


def _assert_safe_metadata_value(value: object, *, parent_key: str) -> None:
    if isinstance(value, Mapping):
        _assert_safe_mapping(value, parent_key=f"{parent_key}.")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        for index, item in enumerate(value):
            _assert_safe_metadata_value(item, parent_key=f"{parent_key}[{index}]")
        return
    if isinstance(value, str):
        if len(value) > 1000:
            raise ValueError(f"graph metadata string is too long: {parent_key}")
        if _UNSAFE_LABEL_RE.search(value):
            raise ValueError(f"graph metadata string contains unsafe content: {parent_key}")
    if isinstance(value, (bytes, bytearray)):
        raise ValueError(f"graph metadata bytes are not allowed: {parent_key}")
