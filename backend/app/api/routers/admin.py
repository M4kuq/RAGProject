from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import pagination_params, require_admin, require_csrf
from app.api.responses import paginate, success_response
from app.db.models import AuditLog, SystemSetting, User
from app.db.session import get_db
from app.schemas.common import PaginationParams
from app.schemas.evaluations import EvaluationRunCreateRequest
from app.services.evaluation_service import EvaluationService

router = APIRouter()


@router.post("/evaluations/runs", status_code=status.HTTP_202_ACCEPTED)
def create_evaluation(
    request: Request,
    payload: EvaluationRunCreateRequest = Body(default_factory=EvaluationRunCreateRequest),
    _: None = Depends(require_csrf),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().create_run(db, payload=payload, user=user)
    return success_response(result.model_dump(), request)


@router.get("/evaluations/runs")
def evaluation_runs(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows, page_meta = EvaluationService().list_runs(db, pagination=pagination)
    return success_response([row.model_dump(mode="json") for row in rows], request, page_meta)


@router.get("/evaluations/runs/{evaluation_run_id}")
def evaluation_run_detail(
    evaluation_run_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().get_run_detail(db, evaluation_run_id=evaluation_run_id)
    return success_response(result.model_dump(mode="json"), request)


@router.get("/audit-logs")
def audit_logs(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.audit_log_id.desc())).all()
    page_rows, page_meta = paginate(rows, pagination)
    return success_response(
        [
            {"audit_log_id": r.audit_log_id, "action": r.action_type, "target_id": r.target_id}
            for r in page_rows
        ],
        request,
        pagination=page_meta,
    )


@router.get("/system/settings")
def system_settings(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows = db.scalars(select(SystemSetting)).all()
    page_rows, page_meta = paginate(rows, pagination)
    return success_response(
        [{"key": r.setting_key, "value": r.setting_value} for r in page_rows],
        request,
        pagination=page_meta,
    )
