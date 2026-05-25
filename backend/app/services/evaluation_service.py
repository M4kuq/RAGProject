from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol, cast

from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, ResourceNotFound, ValidationFailed
from app.core.job_utils import redact_error_message
from app.db.evaluation_models import EvaluationResult
from app.db.models import EvaluationCase as EvaluationCaseModel
from app.db.models import EvaluationDataset, EvaluationRun, EvaluationRunItem, User
from app.evaluation.fixtures import EvaluationCase, EvaluationFixtureError, load_evaluation_cases
from app.evaluation.metrics import (
    EvaluationMetricInputs,
    MetricValue,
    calculate_metrics,
    failure_metrics,
)
from app.evaluation.rag_service import RagEvaluationResult, create_evaluation_rag_service
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.repositories.evaluation_repository import EvaluationRepository, EvaluationResultInput
from app.repositories.job_repository import JobRepository
from app.schemas.common import PaginationMeta, PaginationParams
from app.schemas.evaluations import (
    DATASET_MANIFEST_SCHEMA_VERSION,
    EVALUATION_SCHEMA_VERSION,
    EvaluationCaseCreateRequest,
    EvaluationCaseResponse,
    EvaluationCaseSpec,
    EvaluationCaseUpdateRequest,
    EvaluationDatasetCreateRequest,
    EvaluationDatasetImportResponse,
    EvaluationDatasetManifest,
    EvaluationDatasetManifestInfo,
    EvaluationDatasetResponse,
    EvaluationDatasetUpdateRequest,
    EvaluationMetricResult,
    EvaluationRunCreateRequest,
    EvaluationRunCreateResponse,
    EvaluationRunDetail,
    EvaluationRunItemResponse,
    EvaluationRunSummary,
    EvaluationStatus,
    MetricSpec,
)

SCORE_QUANT = Decimal("0.000001")

STRATEGY_METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        metric_name="recall_at_k",
        display_name="Recall@k",
        description="Fraction of expected references retrieved in the top-k result set.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="mrr",
        display_name="MRR",
        description="Mean reciprocal rank for expected references.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="citation_coverage",
        display_name="Citation coverage",
        description="Fraction of required answers with at least one safe citation.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="groundedness",
        display_name="Groundedness",
        description="Groundedness score derived from the local confidence heuristic.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="faithfulness",
        display_name="Faithfulness",
        description="Keyword-based faithfulness signal for deterministic smoke tests.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="no_context_rate",
        display_name="No-context rate",
        description="Fraction of cases where retrieval returned no usable context.",
        higher_is_better=False,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="p95_latency",
        display_name="p95 latency",
        description="95th percentile end-to-end evaluation latency in milliseconds.",
        higher_is_better=False,
        value_unit="ms",
        min_value=0.0,
        max_value=None,
    ),
    MetricSpec(
        metric_name="strategy_selection_accuracy",
        display_name="Strategy selection accuracy",
        description="Fraction of cases where a router selected the expected strategy.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
)


@dataclass(frozen=True)
class LoadedEvaluationCase:
    case: EvaluationCase
    evaluation_case_id: int | None
    case_key: str


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
        dataset_name = payload.dataset_name
        if payload.evaluation_dataset_id is not None:
            dataset = self.repository.get_dataset(
                db,
                evaluation_dataset_id=payload.evaluation_dataset_id,
            )
            if dataset is None or dataset.status != "active":
                raise ResourceNotFound()
            dataset_name = dataset.dataset_name

        strategy_type = payload.strategy_type.value
        trigger_type = payload.trigger_type.value
        run = self.repository.create_run(
            db,
            created_by=user.user_id,
            dataset_name=dataset_name,
            evaluation_dataset_id=payload.evaluation_dataset_id,
            case_limit=payload.case_limit,
            strategy_type=strategy_type,
            trigger_type=trigger_type,
            retrieval_settings_json=_retrieval_settings_snapshot(
                strategy_type=strategy_type,
                case_limit=payload.case_limit,
            ),
        )
        job = self.job_repository.create_job(
            db,
            job_type="evaluation_run",
            target_type="evaluation_run",
            target_id=run.evaluation_run_id,
            payload_json={
                "evaluation_run_id": run.evaluation_run_id,
                "dataset_name": dataset_name,
                "evaluation_dataset_id": payload.evaluation_dataset_id,
                "case_limit": payload.case_limit,
                "strategy_type": strategy_type,
                "trigger_type": trigger_type,
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

    def create_dataset(
        self,
        db: Session,
        *,
        payload: EvaluationDatasetCreateRequest,
        user: User,
    ) -> EvaluationDatasetResponse:
        if self.repository.get_dataset_by_name(db, dataset_name=payload.dataset_name):
            raise ConflictError()
        dataset = self.repository.create_dataset(
            db,
            dataset_name=payload.dataset_name,
            description=payload.description,
            version=payload.version,
            source_type=payload.source_type.value,
            status=payload.status.value,
            metadata_json=payload.metadata_json,
            created_by=user.user_id,
        )
        db.commit()
        db.refresh(dataset)
        return self._dataset_response(db, dataset)

    def list_datasets(
        self,
        db: Session,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> tuple[list[EvaluationDatasetResponse], PaginationMeta]:
        datasets, total = self.repository.list_datasets(
            db,
            offset=pagination.offset,
            limit=pagination.page_size,
            status=status,
        )
        return [self._dataset_response(db, dataset) for dataset in datasets], pagination_meta(
            pagination, total
        )

    def get_dataset_detail(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDatasetResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        return self._dataset_response(db, dataset)

    def update_dataset(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        payload: EvaluationDatasetUpdateRequest,
    ) -> EvaluationDatasetResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        self.repository.update_dataset(
            db,
            dataset=dataset,
            description=payload.description,
            version=payload.version,
            metadata_json=payload.metadata_json,
            updated_at=datetime.now(UTC),
        )
        db.commit()
        db.refresh(dataset)
        return self._dataset_response(db, dataset)

    def archive_dataset(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDatasetResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        self.repository.archive_dataset(db, dataset=dataset, updated_at=datetime.now(UTC))
        db.commit()
        db.refresh(dataset)
        return self._dataset_response(db, dataset)

    def create_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        payload: EvaluationCaseCreateRequest,
    ) -> EvaluationCaseResponse:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        if self.repository.get_case_by_key(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            case_key=payload.case_key,
        ):
            raise ConflictError()
        case = self.repository.create_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            case_key=payload.case_key,
            question=payload.question,
            expected_answer=payload.expected_answer,
            expected_keywords=payload.expected_keywords,
            expected_document_ids=payload.expected_document_ids,
            expected_chunk_ids=payload.expected_chunk_ids,
            required_citation=payload.required_citation,
            tags=payload.tags,
            metadata_json=payload.metadata_json,
            status=payload.status.value,
        )
        db.commit()
        db.refresh(case)
        return self._case_response(case)

    def list_cases(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> tuple[list[EvaluationCaseResponse], PaginationMeta]:
        if self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id) is None:
            raise ResourceNotFound()
        cases, total = self.repository.list_cases(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            offset=pagination.offset,
            limit=pagination.page_size,
            status=status,
        )
        return [self._case_response(case) for case in cases], pagination_meta(pagination, total)

    def get_case_detail(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
    ) -> EvaluationCaseResponse:
        case = self.repository.get_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            evaluation_case_id=evaluation_case_id,
        )
        if case is None:
            raise ResourceNotFound()
        return self._case_response(case)

    def update_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
        payload: EvaluationCaseUpdateRequest,
    ) -> EvaluationCaseResponse:
        case = self.repository.get_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            evaluation_case_id=evaluation_case_id,
        )
        if case is None:
            raise ResourceNotFound()
        values = payload.model_dump(exclude_unset=True)
        if values:
            self.repository.update_case(
                db,
                case=case,
                values=values,
                updated_at=datetime.now(UTC),
            )
            db.commit()
            db.refresh(case)
        return self._case_response(case)

    def archive_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
    ) -> EvaluationCaseResponse:
        case = self.repository.get_case(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            evaluation_case_id=evaluation_case_id,
        )
        if case is None:
            raise ResourceNotFound()
        self.repository.archive_case(db, case=case, updated_at=datetime.now(UTC))
        db.commit()
        db.refresh(case)
        return self._case_response(case)

    def import_dataset_manifest(
        self,
        db: Session,
        *,
        manifest: EvaluationDatasetManifest,
        user: User,
    ) -> EvaluationDatasetImportResponse:
        dataset = self.repository.get_dataset_by_name(
            db,
            dataset_name=manifest.dataset.dataset_name,
        )
        result_code = "updated"
        if dataset is None:
            result_code = "created"
            dataset = self.repository.create_dataset(
                db,
                dataset_name=manifest.dataset.dataset_name,
                description=manifest.dataset.description,
                version=manifest.dataset.version,
                source_type=manifest.dataset.source_type.value,
                status=manifest.dataset.status.value,
                metadata_json=manifest.dataset.metadata_json,
                created_by=user.user_id,
            )
        else:
            self.repository.update_dataset(
                db,
                dataset=dataset,
                description=manifest.dataset.description,
                version=manifest.dataset.version,
                metadata_json=manifest.dataset.metadata_json,
                updated_at=datetime.now(UTC),
            )
            dataset.source_type = manifest.dataset.source_type.value
            dataset.status = manifest.dataset.status.value
        db.flush()

        imported_case_count = 0
        for case_spec in manifest.cases:
            existing = self.repository.get_case_by_key(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                case_key=case_spec.case_key,
            )
            if existing is None:
                self.repository.create_case(
                    db,
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    case_key=case_spec.case_key,
                    question=case_spec.question,
                    expected_answer=case_spec.expected_answer,
                    expected_keywords=case_spec.expected_keywords,
                    expected_document_ids=case_spec.expected_document_ids,
                    expected_chunk_ids=case_spec.expected_chunk_ids,
                    required_citation=case_spec.required_citation,
                    tags=case_spec.tags,
                    metadata_json=case_spec.metadata_json,
                    status=case_spec.status.value,
                )
            else:
                self.repository.update_case(
                    db,
                    case=existing,
                    values={
                        "question": case_spec.question,
                        "expected_answer": case_spec.expected_answer,
                        "expected_keywords": case_spec.expected_keywords,
                        "expected_document_ids": case_spec.expected_document_ids,
                        "expected_chunk_ids": case_spec.expected_chunk_ids,
                        "required_citation": case_spec.required_citation,
                        "tags": case_spec.tags,
                        "metadata_json": case_spec.metadata_json,
                        "status": case_spec.status.value,
                    },
                    updated_at=datetime.now(UTC),
                )
            imported_case_count += 1
        db.commit()
        db.refresh(dataset)
        return EvaluationDatasetImportResponse(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            dataset_name=dataset.dataset_name,
            case_count=self.repository.count_cases(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
            ),
            imported_case_count=imported_case_count,
            result_code=cast(Literal["created", "updated"], result_code),
        )

    def export_dataset_manifest(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDatasetManifest:
        dataset = self.repository.get_dataset(db, evaluation_dataset_id=evaluation_dataset_id)
        if dataset is None:
            raise ResourceNotFound()
        cases, _ = self.repository.list_cases(
            db,
            evaluation_dataset_id=evaluation_dataset_id,
            offset=0,
            limit=None,
        )
        if not cases:
            raise ValidationFailed({"dataset": "dataset has no cases"})
        return EvaluationDatasetManifest(
            schema_version=DATASET_MANIFEST_SCHEMA_VERSION,
            dataset=EvaluationDatasetManifestInfo(
                dataset_name=dataset.dataset_name,
                description=dataset.description,
                version=dataset.version,
                source_type=dataset.source_type,
                status=dataset.status,
                metadata_json=dataset.metadata_json,
            ),
            cases=[self._case_spec(case) for case in cases],
            metric_specs=list(STRATEGY_METRIC_SPECS),
        )

    def list_runs(
        self,
        db: Session,
        *,
        pagination: PaginationParams,
        status: str | None = None,
    ) -> tuple[list[EvaluationRunSummary], PaginationMeta]:
        runs, total = self.repository.list_runs(
            db,
            offset=pagination.offset,
            limit=pagination.page_size,
            status=status,
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

        try:
            cases = self._load_cases_for_run(db, run)
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

        if run.strategy_type != RetrievalStrategy.DENSE.value:
            run = self._require_run(db, evaluation_run_id)
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code="strategy_runner_not_implemented",
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            return {
                "status": "failed",
                "evaluation_run_id": evaluation_run_id,
                "error_code": "strategy_runner_not_implemented",
                "strategy_type": run.strategy_type,
            }

        succeeded_count = 0
        failed_count = 0
        try:
            rag_service = self.rag_service_factory(self.settings, db)
            strategy_type = cast(str, config["strategy_type"])
            for loaded_case in cases:
                item = self.repository.create_item(
                    db,
                    evaluation_run_id=evaluation_run_id,
                    status="running",
                    strategy_type=strategy_type,
                    evaluation_case_id=loaded_case.evaluation_case_id,
                    case_key=loaded_case.case_key,
                )
                item_id = item.evaluation_run_item_id
                db.commit()
                try:
                    case_result = self._run_case(
                        db,
                        rag_service=rag_service,
                        case=loaded_case.case,
                        request_id=request_id,
                    )
                except Exception:
                    db.rollback()
                    failed_count += 1
                    self._store_case_failure(
                        db,
                        item_id=item_id,
                        case=loaded_case.case,
                        strategy_type=strategy_type,
                    )
                    db.commit()
                    continue
                if case_result["status"] == "succeeded":
                    succeeded_count += 1
                else:
                    failed_count += 1
                self._store_case_result(
                    db,
                    item=item,
                    case_result=case_result,
                    strategy_type=strategy_type,
                )
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
        run.strategy_metrics_summary_json = _strategy_metrics_summary_json(
            strategy_type=run.strategy_type,
            metric_summary=self._summary(db, run).metric_summary,
            case_count=len(cases),
            succeeded_count=succeeded_count,
            failed_count=failed_count,
        )
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
        strategy_type: str,
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
        metric_summary_json = _metric_summary_json(metrics)
        self.repository.finish_item(
            db,
            item=item,
            status=status,
            retrieval_run_id=rag_result.retrieval_run_id,
            faithfulness_score=_metric_decimal(metric_by_name.get("faithfulness")),
            groundedness_score=_metric_decimal(metric_by_name.get("groundedness")),
            citation_coverage=_metric_decimal(metric_by_name.get("citation_coverage")),
            latency_ms=latency_ms,
            latency_breakdown_json=_latency_breakdown_json(latency_ms),
            metric_summary_json=metric_summary_json,
            error_code=rag_result.error_code if status == "failed" else None,
            error_message=None,
        )
        self.repository.save_results(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            results=[
                _result_input(metric, strategy_type=strategy_type)
                for metric in metrics
                if isinstance(metric, MetricValue)
            ],
        )

    def _store_case_failure(
        self,
        db: Session,
        *,
        item_id: int,
        case: EvaluationCase,
        strategy_type: str,
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
            latency_breakdown_json=_latency_breakdown_json(None),
            metric_summary_json=_metric_summary_json(metrics),
            error_code="internal_error",
            error_message=redact_error_message("Evaluation case failed."),
        )
        self.repository.save_results(
            db,
            evaluation_run_item_id=item.evaluation_run_item_id,
            results=[_result_input(metric, strategy_type=strategy_type) for metric in metrics],
        )

    def _summary(self, db: Session, run: EvaluationRun) -> EvaluationRunSummary:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        metric_summary = _metric_summary(results_by_item)
        job = self.repository.find_job_for_run(db, evaluation_run_id=run.evaluation_run_id)
        planned_case_count = (
            self._planned_case_count(db, run) if run.status in {"queued", "running"} else 0
        )
        case_count = max(len(items), planned_case_count)
        return EvaluationRunSummary(
            evaluation_run_id=run.evaluation_run_id,
            job_id=job.job_id if job is not None else None,
            evaluation_dataset_id=run.evaluation_dataset_id,
            dataset_name=cast(str, _config(run)["dataset_name"]),
            strategy_type=cast(RetrievalStrategy, run.strategy_type),
            trigger_type=run.trigger_type,
            status=cast(EvaluationStatus, run.status),
            case_count=case_count,
            succeeded_count=sum(1 for item in items if item.status == "succeeded"),
            failed_count=sum(1 for item in items if item.status == "failed"),
            metric_summary=metric_summary,
            strategy_metrics_summary_json=run.strategy_metrics_summary_json,
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
            evaluation_case_id=item.evaluation_case_id,
            retrieval_run_id=item.retrieval_run_id,
            strategy_type=cast(RetrievalStrategy, item.strategy_type),
            status=cast(EvaluationStatus, item.status),
            faithfulness_score=_decimal_float(item.faithfulness_score),
            groundedness_score=_decimal_float(item.groundedness_score),
            citation_coverage=_decimal_float(item.citation_coverage),
            context_precision=_decimal_float(context_precision),
            latency_ms=item.latency_ms,
            latency_breakdown_json=item.latency_breakdown_json,
            metric_summary_json=item.metric_summary_json,
            error_code=item.error_code,
            error_message=redact_error_message(item.error_message) if item.error_message else None,
            case_id=case_id,
            case_key=item.case_key,
            metrics=metric_results,
        )

    def _require_run(self, db: Session, evaluation_run_id: int) -> EvaluationRun:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise EvaluationFixtureError("evaluation_run_not_found")
        return run

    def _load_cases_for_run(
        self,
        db: Session,
        run: EvaluationRun,
    ) -> list[LoadedEvaluationCase]:
        config = _config(run)
        case_limit = cast(int | None, config["case_limit"])
        if run.evaluation_dataset_id is not None:
            cases, _ = self.repository.list_cases(
                db,
                evaluation_dataset_id=run.evaluation_dataset_id,
                offset=0,
                limit=case_limit,
                status="active",
            )
            if not cases:
                raise EvaluationFixtureError("evaluation_dataset_empty")
            return [_loaded_case_from_model(case) for case in cases]

        fixture_cases = load_evaluation_cases(
            cast(str, config["dataset_name"]),
            case_limit=case_limit,
        )
        return [
            LoadedEvaluationCase(
                case=case,
                evaluation_case_id=None,
                case_key=case.case_id,
            )
            for case in fixture_cases
        ]

    def _planned_case_count(self, db: Session, run: EvaluationRun) -> int:
        config = _config(run)
        case_limit = cast(int | None, config["case_limit"])
        if run.evaluation_dataset_id is not None:
            count = self.repository.count_cases(
                db,
                evaluation_dataset_id=run.evaluation_dataset_id,
                status="active",
            )
            return min(count, case_limit) if case_limit is not None else count
        return _fixture_planned_case_count(run)

    def _dataset_response(
        self, db: Session, dataset: EvaluationDataset
    ) -> EvaluationDatasetResponse:
        return EvaluationDatasetResponse(
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            dataset_name=dataset.dataset_name,
            description=dataset.description,
            version=dataset.version,
            source_type=dataset.source_type,
            status=dataset.status,
            metadata_json=dataset.metadata_json,
            case_count=self.repository.count_cases(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
            ),
            created_by=dataset.created_by,
            created_at=dataset.created_at,
            updated_at=dataset.updated_at,
        )

    def _case_response(self, case: EvaluationCaseModel) -> EvaluationCaseResponse:
        return EvaluationCaseResponse(
            evaluation_case_id=case.evaluation_case_id,
            evaluation_dataset_id=case.evaluation_dataset_id,
            case_key=case.case_key,
            question=case.question,
            expected_answer=case.expected_answer,
            expected_keywords=_string_list(case.expected_keywords),
            expected_document_ids=_int_list(case.expected_document_ids),
            expected_chunk_ids=_int_list(case.expected_chunk_ids),
            required_citation=case.required_citation,
            tags=_string_list(case.tags),
            metadata_json=case.metadata_json,
            status=case.status,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )

    def _case_spec(self, case: EvaluationCaseModel) -> EvaluationCaseSpec:
        return EvaluationCaseSpec(
            case_key=case.case_key,
            question=case.question,
            expected_answer=case.expected_answer,
            expected_keywords=_string_list(case.expected_keywords),
            expected_document_ids=_int_list(case.expected_document_ids),
            expected_chunk_ids=_int_list(case.expected_chunk_ids),
            required_citation=case.required_citation,
            tags=_string_list(case.tags),
            metadata_json=case.metadata_json,
            status=case.status,
        )


def _config(run: EvaluationRun) -> dict[str, object]:
    config = run.metrics_config or {}
    dataset_name = config.get("dataset_name")
    evaluation_dataset_id = config.get("evaluation_dataset_id")
    case_limit = config.get("case_limit")
    strategy_type = config.get("strategy_type") or run.strategy_type
    trigger_type = config.get("trigger_type") or run.trigger_type
    return {
        "dataset_name": dataset_name if isinstance(dataset_name, str) else "phase1_smoke",
        "evaluation_dataset_id": (
            evaluation_dataset_id if isinstance(evaluation_dataset_id, int) else None
        ),
        "case_limit": case_limit if isinstance(case_limit, int) else None,
        "strategy_type": strategy_type if isinstance(strategy_type, str) else "dense",
        "trigger_type": trigger_type if isinstance(trigger_type, str) else "manual",
    }


def _fixture_planned_case_count(run: EvaluationRun) -> int:
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


def _result_input(metric: MetricValue, *, strategy_type: str) -> EvaluationResultInput:
    detail = metric.details
    return EvaluationResultInput(
        metric_name=metric.metric_name,
        metric_score=_decimal_score(metric.metric_score),
        metric_value=_decimal_metric_value(metric.metric_score),
        metric_label=metric.metric_label,
        details_json=detail,
        metric_detail_json=detail,
        strategy_type=strategy_type,
    )


def _metric_decimal(metric: MetricValue | None) -> Decimal | None:
    if metric is None:
        return None
    return _decimal_score(metric.metric_score)


def _decimal_score(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 6))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def _decimal_metric_value(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 6))).quantize(SCORE_QUANT, rounding=ROUND_HALF_UP)


def _decimal_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _metric_response(result: EvaluationResult) -> EvaluationMetricResult:
    return EvaluationMetricResult(
        metric_name=result.metric_name,
        metric_score=_decimal_float(result.metric_score),
        metric_value=_decimal_float(result.metric_value),
        metric_label=result.metric_label,
        details=result.details_json,
        metric_detail_json=result.metric_detail_json,
        strategy_type=cast(RetrievalStrategy, result.strategy_type),
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


def _metric_summary_json(metrics: list[MetricValue]) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "metrics": {
            metric.metric_name: metric.metric_score
            for metric in metrics
            if metric.metric_score is not None and metric.metric_name != "case_metadata"
        },
    }


def _latency_breakdown_json(latency_ms: int | None) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "total_ms": latency_ms,
        "evaluation_case_ms": latency_ms,
    }


def _retrieval_settings_snapshot(
    *,
    strategy_type: str,
    case_limit: int | None,
) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "strategy_type": strategy_type,
        "case_limit": case_limit,
        "runner_implementation": "phase1_dense_fixture",
        "strategy_runner_enabled": strategy_type == DEFAULT_RETRIEVAL_STRATEGY.value,
    }


def _strategy_metrics_summary_json(
    *,
    strategy_type: str,
    metric_summary: dict[str, float],
    case_count: int,
    succeeded_count: int,
    failed_count: int,
) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "strategy_type": strategy_type,
        "metric_summary": metric_summary,
        "case_count": case_count,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }


def _loaded_case_from_model(case: EvaluationCaseModel) -> LoadedEvaluationCase:
    return LoadedEvaluationCase(
        case=EvaluationCase(
            case_id=case.case_key,
            question=case.question,
            expected_keywords=tuple(_string_list(case.expected_keywords)),
            required_citation=case.required_citation,
        ),
        evaluation_case_id=case.evaluation_case_id,
        case_key=case.case_key,
    )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if isinstance(item, int) and not isinstance(item, bool)]
