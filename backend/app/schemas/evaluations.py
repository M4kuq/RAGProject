from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import PaginationMeta

EvaluationStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


class EvaluationRunCreateRequest(BaseModel):
    dataset_name: str = Field(default="phase1_smoke", min_length=1, max_length=100)
    case_limit: int | None = Field(default=10, ge=1, le=50)

    @field_validator("dataset_name")
    @classmethod
    def validate_dataset_name(cls, value: str) -> str:
        stripped = value.strip()
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789_-"
        if not stripped or any(char not in allowed for char in stripped):
            raise ValueError(
                "dataset_name must be lowercase letters, digits, underscores or hyphens"
            )
        return stripped


class EvaluationRunCreateResponse(BaseModel):
    evaluation_run_id: int
    job_id: int
    status: Literal["queued"]


class EvaluationMetricResult(BaseModel):
    metric_name: str
    metric_score: float | None = None
    metric_label: str | None = None
    details: dict[str, object] | None = None


class EvaluationRunItemResponse(BaseModel):
    evaluation_run_item_id: int
    retrieval_run_id: int | None = None
    status: EvaluationStatus
    faithfulness_score: float | None = None
    groundedness_score: float | None = None
    citation_coverage: float | None = None
    context_precision: float | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    case_id: str | None = None
    metrics: list[EvaluationMetricResult] = Field(default_factory=list)


class EvaluationRunSummary(BaseModel):
    evaluation_run_id: int
    job_id: int | None = None
    dataset_name: str
    status: EvaluationStatus
    case_count: int
    succeeded_count: int
    failed_count: int
    metric_summary: dict[str, float]
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EvaluationRunDetail(EvaluationRunSummary):
    items: list[EvaluationRunItemResponse] = Field(default_factory=list)


class PagedEvaluationRuns(BaseModel):
    items: list[EvaluationRunSummary]
    pagination: PaginationMeta | None = None
