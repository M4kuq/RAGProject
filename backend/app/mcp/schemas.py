from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class McpRagSearchInput(McpInputModel):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class McpRagAskInput(McpInputModel):
    question: str = Field(min_length=1, max_length=8000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class McpListDocumentsInput(McpInputModel):
    status: Literal["active", "archived"] | None = "active"
    display_status: (
        Literal["active", "pending_review", "processing", "failed", "archived"] | None
    ) = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class McpGetDocumentStatusInput(McpInputModel):
    logical_document_id: int = Field(ge=1)


class McpGetJobStatusInput(McpInputModel):
    job_id: int = Field(ge=1)


class McpListEvaluationRunsInput(McpInputModel):
    status: Literal["queued", "running", "succeeded", "failed", "canceled"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class McpGetEvaluationResultInput(McpInputModel):
    evaluation_run_id: int = Field(ge=1)
