from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from starlette import status as http_status

from app.api.deps import pagination_params, require_admin, require_csrf
from app.api.responses import success_response
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import PaginationParams
from app.services.job_service import JobService

router = APIRouter()


def job_service() -> JobService:
    return JobService()


@router.get("")
def list_jobs(
    request: Request,
    status: str | None = None,
    job_type: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    _: User = Depends(require_admin),
    pagination: PaginationParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    service: JobService = Depends(job_service),
) -> dict[str, object]:
    items, page_meta = service.list_jobs(
        db,
        status=status,
        job_type=job_type,
        target_type=target_type,
        target_id=target_id,
        pagination=pagination,
    )
    return success_response([item.model_dump(mode="json") for item in items], request, page_meta)


@router.get("/{job_id}")
def get_job_detail(
    job_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    service: JobService = Depends(job_service),
) -> dict[str, object]:
    result = service.get_job_detail(db, job_id=job_id)
    return success_response(result.model_dump(mode="json"), request)


@router.post("/{job_id}/retry", status_code=http_status.HTTP_201_CREATED)
def retry_job(
    job_id: int,
    request: Request,
    user: User = Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: JobService = Depends(job_service),
) -> dict[str, object]:
    result = service.retry_job(db, job_id=job_id, user=user)
    return success_response(result.model_dump(mode="json"), request)
