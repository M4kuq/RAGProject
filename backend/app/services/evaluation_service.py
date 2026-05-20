from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol, cast

from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.config import Settings, get_settings
from app.core.errors import ResourceNotFound
from app.core.job_utils import redact_error_message
from app.db.evaluation_models import EvaluationResult
from app.db.models import EvaluationRun, EvaluationRunItem, User
from app.evaluation.fixtures import EvaluationCase, EvaluationFixtureError, load_evaluation_cases
from app.evaluation.metrics import (
    EvaluationMetricInputs,
    MetricValue,
    calculate_metrics,
    failure_metrics,
)
from app.evaluation.rag_service import RagEvaluationResult, create_evaluation_rag_service
from app.repositories.evaluation_repository import EvaluationRepository, EvaluationResultInput
from app.repositories.job_repository import JobRepository
from app.schemas.common import PaginationMeta, PaginationParams
from app.schemas.evaluations import (
    EvaluationMetricResult,
    EvaluationRunCreateRequest,
    EvaluationRunCreateResponse,
    EvaluationRunDetail,
    EvaluationRunItemResponse,
    EvaluationRunSummary,
    EvaluationStatus,
)

SCORE_QUANT = Decimal("0.000001")


class EvaluationRagService(Protocol):
    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult: ...


class EvaluationService:
    def __init__(
        self,
        *,
        repository: EvaluationRepository | None = None,
        job_repository: JobRepository | None = None,
        rag_service_factory: Callable[
            [Settings, Session],
            EvaluationRagService,
        ] = create_evaluation_rag_service,
        settings: Settings | None = None,
    ) -> None:
        self.repository = repository or EvaluationRepository()
        self.job_repository = job_repository or JobRepository()
        self.rag_service_factory = rag_service_factory
        self.settings = settings or get_settings()

    def create_run(
        self,
        db: Session,
        *,
        payload: EvaluationRunCreateRequest,
        user: User,
    ) -> EvaluationRunCreateResponse:
        run = self.repository.create_run(
            db,
            created_by=user.user_id,
            dataset_name=payload.dataset_name,
            case_limit=payload.case_limit,
        )
        job = self.job_repository.create_job(
            db,
            job_type="evaluation_run",
            target_type="evaluation_run",
            target_id=run.evaluation_run_id,
            payload_json={
                "evaluation_run_id": run.evaluation_run_id,
                "dataset_name": payload.dataset_name,
                "case_limit": payload.case_limit,
            },
            created_by=user.user_id,
            priority=100,
        )
        db.commit()
        db.refresh(run)
        db.refresh(job)
        return EvaluationRunCreateResponse(
            evaluation_run_id=run.evaluation_run_id,
            job_id=job.job_id,
            status="queued",
        )

    def list_runs(
        self,
        db: Session,
        *,
        pagination: PaginationParams,
    ) -> tuple[list[EvaluationRunSummary], PaginationMeta]:
        runs, total = self.repository.list_runs(
            db,
            offset=pagination.offset,
            limit=pagination.page_size,
        )
        return [self._summary(db, run) for run in runs], pagination_meta(pagination, total)

    def get_run_detail(self, db: Session, *, evaluation_run_id: int) -> EvaluationRunDetail:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        summary = self._summary(db, run)
        items = self.repository.list_items(db, evaluation_run_id=evaluation_run_id)
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        return EvaluationRunDetail(
            **summary.model_dump(),
            items=[
                self._item_response(item, results_by_item.get(item.evaluation_run_item_id, []))
                for item in items
            ],
        )

    def run_job(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        request_id: str | None,
    ) -> dict[str, object]:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id, for_update=True)
        if run is None:
            raise EvaluationFixtureError("evaluation_run_not_found")
        if run.status == "succeeded":
            return {"status": "succeeded", "evaluation_run_id": evaluation_run_id, "noop": True}

        config = _config(run)
        now = datetime.now(UTC)
        self.repository.mark_run_running(db, run=run, started_at=now)
        self.repository.delete_items_and_results(db, evaluation_run_id=evaluation_run_id)
        db.commit()

        dataset_name = cast(str, config["dataset_name"])
        case_limit = cast(int | None, config["case_limit"])
        try:
            cases = load_evaluation_cases(
                dataset_name,
                case_limit=case_limit,
            )
        except EvaluationFixtureError as exc:
            run = self._require_run(db, evaluation_run_id)
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code=str(exc),
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            raise

        succeeded_count = 0
        failed_count = 0
        try:
            rag_service = self.rag_service_factory(self.settings, db)
            for case in cases:
                item = self.repository.create_item(
                    db,
                    evaluation_run_id=evaluation_run_id,
                    status="running",
                )
                item_id = item.evaluation_run_item_id
                db.commit()
                try:
                    case_result = self._run_case(
                        db,
                        rag_service=rag_service,
                        case=case,
                        request_id=request_id,
                    )
                except Exception:
                    db.rollback()
                    failed_count += 1
                    self._store_case_failure(db, item_id=item_id, case=case)
                    db.commit()
                    continue
                if case_result["status"] == "succeeded":
                    succeeded_count += 1
                else:
                    failed_count += 1
                self._store_case_result(db, item=item, case_result=case_result)
                db.commit()
        except Exception:
            db.rollback()
            run = self._require_run(db, evaluation_run_id)
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code="internal_error",
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            raise

        run = self._require_run(db, evaluation_run_id)
        self.repository.mark_run_succeeded(db, run=run, finished_at=datetime.now(UTC))
        db.commit()
        return {
            "status": "succeeded",
            "evaluation_run_id": evaluation_run_id,
            "case_count": len(cases),
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
        }

    def _run_case(
        self,
        db: Session,
        *,
        rag_service: EvaluationRagService,
        case: EvaluationCase,
        request_id: str | None,
    ) -> dict[str, object]:
        started = time.perf_counter()
        rag_result = rag_service.evaluate_question(
            db,
            question=case.question,
            request_id=request_id,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        metrics = calculate_metrics(
            EvaluationMetricInputs(
                case=case,
                answer_text=rag_result.answer_text,
                citations=rag_result.citations,
                confidence=rag_result.confidence,
                retrieval_summary=rag_result.retrieval_score_summary,
                error_code=rag_result.error_code,
            )
        )
        status = "succeeded" if rag_result.status == "succeeded" else "failed"
        return {
            "case": case,
            "rag_result": rag_result,
            "metrics": metrics,
            "latency_ms": latency_ms,
            "status": status,
        }

    def _store_case_result(
        self,
        db: Session,
        *,
        item: EvaluationRunItem,
        case_result: dict[str, object],
    ) -> None:
        rag_result = case_result["rag_result"]
        metrics = case_result["metrics"]
        if not isinstance(rag_result, RagEvaluationResult) or not isinstance(metrics, list):
            raise RuntimeError("invalid_evaluation_case_result")
        latency_ms = case_result["latency_ms"]
        if not isinstance(latency_ms, int):
            raise RuntimeError("invalid_evaluation_case_result")
        status = str(case_result["status"])
        metric_by_name = {
            metric.metric_name: metric for metric in metrics if isinstance(metric, MetricValue)
        }
        self.repository.finish_item(
            db,
            item=item,
            status=status,
            retrieval_run_id=rag_result.retrieval_run_id,
            faithfulness_score=_metric_decimal(metric_by_name.get("faithfulness")),
            groundedness_score=_metric_decimal(metric_by_name.get("groundedness")),
            citation_coverage=_metric_decimal(metric_by_name.get("citation_coverage")),
            latency_ms=latency_ms,
            error_code=rag_result.error_code if status == "failed" else None,
            error_message=None,
        )
        self.repository.save_results(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            results=[
                _result_input(metric) for metric in metrics if isinstance(metric, MetricValue)
            ],
        )

    def _store_case_failure(
        self,
        db: Session,
        *,
        item_id: int,
        case: EvaluationCase,
    ) -> None:
        item = db.get(EvaluationRunItem, item_id)
        if item is None:
            return
        metrics = failure_metrics(case, error_code="internal_error")
        self.repository.finish_item(
            db,
            item=item,
            status="failed",
            retrieval_run_id=None,
            faithfulness_score=_metric_decimal(_find_metric(metrics, "faithfulness")),
            groundedness_score=_metric_decimal(_find_metric(metrics, "groundedness")),
            citation_coverage=_metric_decimal(_find_metric(metrics, "citation_coverage")),
            latency_ms=None,
            error_code="internal_error",
            error_message=redact_error_message("Evaluation case failed."),
        )
        self.repository.save_results(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            results=[_result_input(metric) for metric in metrics],
        )

    def _summary(self, db: Session, run: EvaluationRun) -> EvaluationRunSummary:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        metric_summary = _metric_summary(results_by_item)
        job = self.repository.find_job_for_run(db, evaluation_run_id=run.evaluation_run_id)
        case_count = len(items)
        if case_count == 0 and run.status in {"queued", "running"}:
            case_count = _planned_case_count(run)
        return EvaluationRunSummary(
            evaluation_run_id=run.evaluation_run_id,
            job_id=job.job_id if job is not None else None,
            dataset_name=cast(str, _config(run)["dataset_name"]),
            status=cast(EvaluationStatus, run.status),
            case_count=case_count,
            succeeded_count=sum(1 for item in items if item.status == "succeeded"),
            failed_count=sum(1 for item in items if item.status == "failed"),
            metric_summary=metric_summary,
            error_code=run.error_code,
            error_message=redact_error_message(run.error_message) if run.error_message else None,
            started_at=run.started_at,
            finished_at=run.finished_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    def _item_response(
        self,
        item: EvaluationRunItem,
        results: list[EvaluationResult],
    ) -> EvaluationRunItemResponse:
        metric_results = [_metric_response(result) for result in results]
        context_precision = next(
            (
                result.metric_score
                for result in results
                if result.metric_name == "context_precision"
            ),
            None,
        )
        case_id = next(
            (
                str(result.details_json.get("case_id"))
                for result in results
                if result.metric_name == "case_metadata"
                and isinstance(result.details_json, dict)
                and result.details_json.get("case_id")
            ),
            None,
        )
        return EvaluationRunItemResponse(
            evaluation_run_item_id=item.evaluation_run_item_id,
            retrieval_run_id=item.retrieval_run_id,
            status=cast(EvaluationStatus, item.status),
            faithfulness_score=_decimal_float(item.faithfulness_score),
            groundedness_score=_decimal_float(item.groundedness_score),
            citation_coverage=_decimal_float(item.citation_coverage),
            context_precision=_decimal_float(context_precision),
            latency_ms=item.latency_ms,
            error_code=item.error_code,
            error_message=redact_error_message(item.error_message) if item.error_message else None,
            case_id=case_id,
            metrics=metric_results,
        )

    def _require_run(self, db: Session, evaluation_run_id: int) -> EvaluationRun:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise EvaluationFixtureError("evaluation_run_not_found")
        return run


def _config(run: EvaluationRun) -> dict[str, object]:
    config = run.metrics_config or {}
    dataset_name = config.get("dataset_name")
    case_limit = config.get("case_limit")
    return {
        "dataset_name": dataset_name if isinstance(dataset_name, str) else "phase1_smoke",
        "case_limit": case_limit if isinstance(case_limit, int) else None,
    }


def _planned_case_count(run: EvaluationRun) -> int:
    config = _config(run)
    try:
        return len(
            load_evaluation_cases(
                cast(str, config["dataset_name"]),
                case_limit=cast(int | None, config["case_limit"]),
            )
        )
    except EvaluationFixtureError:
        return 0


def _result_input(metric: MetricValue) -> EvaluationResultInput:
    return EvaluationResultInput(
        metric_name=metric.metric_name,
        metric_score=_decimal_score(metric.metric_score),
        metric_label=metric.metric_label,
        details_json=metric.details,
    )


def _metric_decimal(metric: MetricValue | None) -> Decimal | None:
    if metric is None:
        return None
    return _decimal_score(metric.metric_score)


def _decimal_score(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 6))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _metric_response(result: EvaluationResult) -> EvaluationMetricResult:
    return EvaluationMetricResult(
        metric_name=result.metric_name,
        metric_score=_decimal_float(result.metric_score),
        metric_label=result.metric_label,
        details=result.details_json,
    )


def _find_metric(metrics: list[MetricValue], name: str) -> MetricValue | None:
    return next((metric for metric in metrics if metric.metric_name == name), None)


def _metric_summary(results_by_item: dict[int, list[EvaluationResult]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for results in results_by_item.values():
        for result in results:
            if result.metric_score is None or result.metric_name == "case_metadata":
                continue
            values.setdefault(result.metric_name, []).append(float(result.metric_score))
    return {
        metric_name: round(sum(scores) / len(scores), 6)
        for metric_name, scores in sorted(values.items())
        if scores
    }
