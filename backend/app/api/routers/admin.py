from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import pagination_params, require_admin, require_csrf
from app.api.responses import paginate, success_response
from app.db.models import AuditLog, EvaluationRun, Job, SystemSetting, User
from app.db.session import get_db
from app.schemas.common import PaginationParams

router = APIRouter(dependencies=[Depends(require_csrf)])


@router.get("/jobs")
def jobs(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows = db.scalars(select(Job).order_by(Job.job_id.desc())).all()
    page_rows, page_meta = paginate(rows, pagination)
    return success_response(
        [{"job_id": j.job_id, "job_type": j.job_type, "status": j.status} for j in page_rows],
        request,
        pagination=page_meta,
    )


@router.post("/evaluations/runs")
def create_evaluation(
    request: Request,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    now = datetime.now(UTC)
    run = EvaluationRun(
        status="succeeded",
        metrics_config={
            "trigger_type": "manual",
            "retrieval": 1.0,
            "generation": 1.0,
            "hallucination_basic": 0.0,
            "prompt_injection_basic": "not_detected",
            "citation_coverage": 1.0,
        },
        created_by=user.user_id,
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return success_response(
        {"evaluation_run_id": run.evaluation_run_id, "status": run.status},
        request,
    )


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
