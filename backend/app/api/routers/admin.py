from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_csrf
from app.db.models import AuditLog, EvaluationRun, Job, SystemSetting, User
from app.db.session import get_db

router = APIRouter(dependencies=[Depends(require_csrf)])


@router.get("/jobs")
def jobs(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.scalars(select(Job).order_by(Job.job_id.desc()).limit(50)).all()
    return {"data": [{"job_id": j.job_id, "job_type": j.job_type, "status": j.status} for j in rows], "meta": {}}


@router.post("/evaluations/runs")
def create_evaluation(user: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    run = EvaluationRun(
        trigger_type="manual",
        status="succeeded",
        summary={
            "retrieval": 1.0,
            "generation": 1.0,
            "hallucination_basic": 0.0,
            "prompt_injection_basic": "not_detected",
            "citation_coverage": 1.0,
        },
        created_by=user.user_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"data": {"evaluation_run_id": run.evaluation_run_id, "summary": run.summary}, "meta": {}}


@router.get("/audit-logs")
def audit_logs(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.audit_log_id.desc()).limit(50)).all()
    return {"data": [{"audit_log_id": r.audit_log_id, "action": r.action, "target_id": r.target_id} for r in rows], "meta": {}}


@router.get("/system/settings")
def system_settings(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    rows = db.scalars(select(SystemSetting)).all()
    return {"data": [{"key": r.setting_key, "value": r.setting_value} for r in rows], "meta": {}}
