from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from sqlalchemy.orm import Session

from app.core.job_utils import redact_error_message

JobHandlerStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class JobExecutionContext:
    job_id: int
    job_type: str
    target_type: str | None
    target_id: int | None
    payload: Mapping[str, object]
    worker_instance_id: str
    session_factory: Callable[[], Session] | None = None


@dataclass(frozen=True)
class JobHandlerResult:
    status: JobHandlerStatus
    result_json: dict[str, object] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def succeeded(cls, result_json: dict[str, object] | None = None) -> JobHandlerResult:
        return cls(status="succeeded", result_json=result_json or {})

    @classmethod
    def failed(cls, *, error_code: str, error_message: str) -> JobHandlerResult:
        return cls(
            status="failed",
            error_code=error_code,
            error_message=redact_error_message(error_message),
        )


class JobHandler(Protocol):
    def handle(self, context: JobExecutionContext) -> JobHandlerResult: ...


class RetryableJobError(RuntimeError):
    pass


class PermanentJobError(RuntimeError):
    pass
