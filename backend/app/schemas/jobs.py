from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "succeeded", "failed", "canceled"]


class JobPayloadView(BaseModel):
    payload: dict[str, object] = Field(default_factory=dict)
    payload_redacted: Literal[True] = True


class JobItem(BaseModel):
    job_id: int
    job_type: str
    status: JobStatus
    priority: int
    target_type: str | None = None
    target_id: int | None = None
    retry_of_job_id: int | None = None
    retry_count: int
    created_by: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    payload_view: JobPayloadView


class JobDetail(JobItem):
    locked_at: datetime | None = None
    lease_expires_at: datetime | None = None
    result_json: dict[str, object] | None = None
    source_job_id: int | None = None
    active_retry_job_id: int | None = None


class JobRetryResponse(BaseModel):
    result_code: Literal["retry_created"] = "retry_created"
    job_id: int
    source_job_id: int
    status: Literal["queued"] = "queued"
    retry_count: int
