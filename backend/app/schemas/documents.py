from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DocumentStatus = Literal["active", "archived"]
DocumentVersionStatus = Literal["processing", "ready", "failed", "archived"]
DocumentDisplayStatus = Literal["active", "pending_review", "processing", "failed", "archived"]
DocumentResultCode = Literal[
    "created",
    "approved",
    "archived",
    "already_active",
    "already_archived",
    "duplicate_content_skipped",
]

MAX_DOCUMENT_TITLE_LENGTH = 255
MAX_CHUNK_PREVIEW_LENGTH = 200


DiffType = Literal["added", "removed", "changed", "unchanged"]


def normalize_document_title(value: str | None, *, fallback: str | None = None) -> str:
    title = value.strip() if value is not None else (fallback or "").strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > MAX_DOCUMENT_TITLE_LENGTH:
        raise ValueError(f"title must be at most {MAX_DOCUMENT_TITLE_LENGTH} characters")
    return title


class DocumentListQuery(BaseModel):
    status: DocumentStatus | None = None
    q: str | None = Field(default=None, max_length=255)
    display_status: DocumentDisplayStatus | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("q")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DocumentVersionSummary(BaseModel):
    document_version_id: int
    version_no: int
    status: DocumentVersionStatus
    is_active: bool
    display_status: DocumentDisplayStatus
    file_name: str | None = None
    mime_type: str | None = None
    file_size_bytes: int | None = None
    page_count: int | None = None
    content_hash: str | None = None
    error_code: str | None = None
    metadata_json: dict[str, object] | None = None
    chunk_count: int | None = None
    created_at: datetime
    updated_at: datetime


class DocumentItem(BaseModel):
    logical_document_id: int
    document_name: str
    title: str
    status: DocumentStatus
    display_status: DocumentDisplayStatus
    latest_version: DocumentVersionSummary | None = None
    active_version: DocumentVersionSummary | None = None
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentItem):
    versions: list[DocumentVersionSummary] = Field(default_factory=list)


class DocumentVersionDetail(DocumentVersionSummary):
    logical_document_id: int


class DocumentChunkItem(BaseModel):
    document_chunk_id: int
    document_version_id: int
    chunk_index: int
    preview: str
    preview_truncated: bool
    page_from: int | None = None
    page_to: int | None = None
    section_title: str | None = None
    metadata_json: dict[str, object] | None = None
    token_count: int | None = None
    char_count: int | None = None
    modality: Literal["text"] = "text"
    chunk_hash: str | None = None
    created_at: datetime


class DocumentSourceLocator(BaseModel):
    logical_document_id: int
    document_version_id: int
    document_chunk_id: int
    chunk_index: int
    version_no: int
    document_title: str
    file_name: str | None = None
    source_type: Literal["upload", "external_url"] = "upload"
    source_url: str | None = None
    display_label: str
    source_label: str
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    sheet_name: str | None = None
    row_from: int | None = None
    row_to: int | None = None
    slide_number: int | None = None
    slide_title: str | None = None
    html_heading_path: str | None = None
    xml_path: str | None = None
    structure_type: str | None = None
    preview: str
    preview_truncated: bool
    old_version_flag: bool


class DocumentMetadataDiffItem(BaseModel):
    field: str
    base_value: str | int | bool | None = None
    target_value: str | int | bool | None = None
    changed: bool


class DocumentChunkDiffSide(BaseModel):
    document_chunk_id: int
    chunk_index: int
    source_label: str
    section_title: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    sheet_name: str | None = None
    row_from: int | None = None
    row_to: int | None = None
    slide_number: int | None = None
    html_heading_path: str | None = None
    xml_path: str | None = None
    preview: str
    preview_truncated: bool


class DocumentChunkDiffItem(BaseModel):
    diff_type: DiffType
    base_chunk: DocumentChunkDiffSide | None = None
    target_chunk: DocumentChunkDiffSide | None = None
    similarity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    match_reason: str


class DocumentVersionCompareSummary(BaseModel):
    added_chunks: int
    removed_chunks: int
    changed_chunks: int
    unchanged_chunks: int
    metadata_changed: bool
    diff_items_returned: int
    diff_items_truncated: bool


class DocumentVersionCompareResponse(BaseModel):
    logical_document_id: int
    base_version: DocumentVersionDetail
    target_version: DocumentVersionDetail
    summary: DocumentVersionCompareSummary
    metadata_diff: list[DocumentMetadataDiffItem]
    chunk_diff_items: list[DocumentChunkDiffItem]


class DocumentUploadResponse(BaseModel):
    logical_document_id: int
    document_version_id: int
    job_id: int
    ingest_status: Literal["queued"]
    version_status: DocumentVersionStatus
    display_status: DocumentDisplayStatus
    result_code: Literal["created"] = "created"
    document: DocumentItem
    version: DocumentVersionDetail


class DocumentUrlIngestRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=MAX_DOCUMENT_TITLE_LENGTH)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("url must not be empty")
        return normalized

    @field_validator("title")
    @classmethod
    def normalize_title_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DocumentVersionCreateResponse(BaseModel):
    status: Literal["created", "duplicate_content_skipped"]
    logical_document_id: int
    document_version_id: int | None = None
    job_id: int | None = None
    ingest_status: Literal["queued"] | None = None
    version_status: DocumentVersionStatus | None = None
    display_status: DocumentDisplayStatus | None = None
    matched_document_version_id: int | None = None
    matched_version_no: int | None = None
    reason: Literal["duplicate_content"] | None = None
    version: DocumentVersionDetail | None = None


class DocumentApproveResponse(BaseModel):
    logical_document_id: int
    document_version_id: int
    version_no: int
    status: DocumentVersionStatus
    is_active: bool
    display_status: DocumentDisplayStatus
    previous_active_document_version_id: int | None = None
    result_code: Literal["approved", "already_active"]
    active_version: DocumentVersionDetail
    qdrant_mirror_job_id: int | None = None


class DocumentArchiveResponse(BaseModel):
    logical_document_id: int
    status: Literal["archived"]
    display_status: Literal["archived"]
    result_code: Literal["archived", "already_archived"]
    retrieval_eligible: Literal[False] = False
    qdrant_mirror_job_id: int | None = None
