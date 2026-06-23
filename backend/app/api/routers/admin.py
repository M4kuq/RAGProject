from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import pagination_params, require_admin, require_csrf
from app.api.responses import paginate, success_response
from app.db.models import AuditLog, SystemSetting, User
from app.db.session import get_db
from app.schemas.common import PaginationParams
from app.schemas.evaluations import (
    EvaluationCaseCreateRequest,
    EvaluationCaseUpdateRequest,
    EvaluationDatasetCreateRequest,
    EvaluationDatasetManifest,
    EvaluationDatasetUpdateRequest,
    EvaluationFailurePromotionRequest,
    EvaluationRunCreateRequest,
)
from app.services.evaluation_service import EvaluationService

router = APIRouter()


@router.get("/evaluations/datasets")
def evaluation_datasets(
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows, page_meta = EvaluationService().list_datasets(db, pagination=pagination)
    return success_response([row.model_dump(mode="json") for row in rows], request, page_meta)


@router.post("/evaluations/datasets", status_code=status.HTTP_201_CREATED)
def create_evaluation_dataset(
    request: Request,
    payload: EvaluationDatasetCreateRequest,
    _: None = Depends(require_csrf),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().create_dataset(db, payload=payload, user=user)
    return success_response(result.model_dump(mode="json"), request)


@router.post("/evaluations/datasets/import")
def import_evaluation_dataset(
    request: Request,
    manifest: EvaluationDatasetManifest,
    _csrf: None = Depends(require_csrf),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().import_dataset_manifest(db, manifest=manifest, user=user)
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/datasets/{evaluation_dataset_id}")
def evaluation_dataset_detail(
    evaluation_dataset_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().get_dataset_detail(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.patch("/evaluations/datasets/{evaluation_dataset_id}")
def update_evaluation_dataset(
    evaluation_dataset_id: int,
    request: Request,
    payload: EvaluationDatasetUpdateRequest,
    _csrf: None = Depends(require_csrf),
    _user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().update_dataset(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
        payload=payload,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.post("/evaluations/datasets/{evaluation_dataset_id}/archive")
def archive_evaluation_dataset(
    evaluation_dataset_id: int,
    request: Request,
    _csrf: None = Depends(require_csrf),
    _user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().archive_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/datasets/{evaluation_dataset_id}/cases")
def evaluation_cases(
    evaluation_dataset_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    rows, page_meta = EvaluationService().list_cases(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
        pagination=pagination,
    )
    return success_response([row.model_dump(mode="json") for row in rows], request, page_meta)


@router.post(
    "/evaluations/datasets/{evaluation_dataset_id}/cases",
    status_code=status.HTTP_201_CREATED,
)
def create_evaluation_case(
    evaluation_dataset_id: int,
    request: Request,
    payload: EvaluationCaseCreateRequest,
    _csrf: None = Depends(require_csrf),
    _user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().create_case(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
        payload=payload,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/datasets/{evaluation_dataset_id}/cases/{evaluation_case_id}")
def evaluation_case_detail(
    evaluation_dataset_id: int,
    evaluation_case_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().get_case_detail(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
        evaluation_case_id=evaluation_case_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.patch("/evaluations/datasets/{evaluation_dataset_id}/cases/{evaluation_case_id}")
def update_evaluation_case(
    evaluation_dataset_id: int,
    evaluation_case_id: int,
    request: Request,
    payload: EvaluationCaseUpdateRequest,
    _csrf: None = Depends(require_csrf),
    _user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().update_case(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
        evaluation_case_id=evaluation_case_id,
        payload=payload,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.post("/evaluations/datasets/{evaluation_dataset_id}/cases/{evaluation_case_id}/archive")
def archive_evaluation_case(
    evaluation_dataset_id: int,
    evaluation_case_id: int,
    request: Request,
    _csrf: None = Depends(require_csrf),
    _user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().archive_case(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
        evaluation_case_id=evaluation_case_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/datasets/{evaluation_dataset_id}/export")
def export_evaluation_dataset(
    evaluation_dataset_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().export_dataset_manifest(
        db,
        evaluation_dataset_id=evaluation_dataset_id,
    )
    return success_response(result.model_dump(mode="json"), request)


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


@router.get("/evaluations/runs/compare")
def compare_evaluation_runs(
    request: Request,
    base: int = Query(ge=1),
    candidate: int = Query(ge=1),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().compare_runs(
        db,
        base_run_id=base,
        candidate_run_id=candidate,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/runs/{evaluation_run_id}")
def evaluation_run_detail(
    evaluation_run_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().get_run_detail(db, evaluation_run_id=evaluation_run_id)
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/runs/{evaluation_run_id}/strategy-comparison")
def evaluation_run_strategy_comparison(
    evaluation_run_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().get_strategy_comparison(
        db,
        evaluation_run_id=evaluation_run_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/evaluations/runs/{evaluation_run_id}/failure-candidates")
def evaluation_run_failure_candidates(
    evaluation_run_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().list_failure_candidates(
        db,
        evaluation_run_id=evaluation_run_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.post("/evaluations/runs/{evaluation_run_id}/promote-failures")
def promote_evaluation_failures(
    evaluation_run_id: int,
    request: Request,
    payload: EvaluationFailurePromotionRequest,
    _csrf: None = Depends(require_csrf),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = EvaluationService().promote_failures(
        db,
        evaluation_run_id=evaluation_run_id,
        payload=payload,
    )
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
