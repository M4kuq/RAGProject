from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Protocol, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.responses import pagination_meta
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, ResourceNotFound, ValidationFailed
from app.core.job_utils import redact_error_message
from app.db.evaluation_models import EvaluationResult
from app.db.models import EvaluationCase as EvaluationCaseModel
from app.db.models import EvaluationDataset, EvaluationRun, EvaluationRunItem, RetrievalRun, User
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
    DEFAULT_EVALUATION_METRICS,
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
    EvaluationFailureCandidate,
    EvaluationFailureCandidatesResponse,
    EvaluationFailurePromotionItem,
    EvaluationFailurePromotionRequest,
    EvaluationFailurePromotionResponse,
    EvaluationFailureSeverity,
    EvaluationMetricResult,
    EvaluationRunCreateRequest,
    EvaluationRunCreateResponse,
    EvaluationRunDetail,
    EvaluationRunItemResponse,
    EvaluationRunSummary,
    EvaluationStatus,
    EvaluationStrategyComparisonResponse,
    MetricSpec,
    StrategyComparisonMetric,
)

SCORE_QUANT = Decimal("0.000001")
RETRIEVAL_RUN_REQUEST_ID_MAX_LENGTH = 100

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
    MetricSpec(
        metric_name="fallback_rate",
        display_name="Fallback rate",
        description="Fraction of agentic-router cases that used a bounded fallback retrieval.",
        higher_is_better=False,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="budget_exhausted_rate",
        display_name="Budget exhausted rate",
        description="Fraction of agentic-router cases that exhausted the retrieval budget.",
        higher_is_better=False,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="sufficiency_score_avg",
        display_name="Average sufficiency score",
        description="Average bounded context-sufficiency score for agentic-router cases.",
        higher_is_better=True,
        value_unit="ratio",
        min_value=0.0,
        max_value=1.0,
    ),
    MetricSpec(
        metric_name="retrieval_call_count_avg",
        display_name="Average retrieval calls",
        description="Average retrieval call count used by agentic-router cases.",
        higher_is_better=False,
        value_unit="count",
        min_value=0.0,
        max_value=None,
    ),
)


@dataclass(frozen=True)
class LoadedEvaluationCase:
    case: EvaluationCase
    evaluation_case_id: int | None
    case_key: str
    metadata_json: dict[str, object] | None = None
    tags: list[str] | None = None


@dataclass(frozen=True)
class PromotionSourceCase:
    evaluation_case_id: int | None
    case_key: str
    question: str
    expected_answer: str | None
    expected_keywords: list[str]
    expected_document_ids: list[int]
    expected_chunk_ids: list[int]
    required_citation: bool
    tags: list[str]
    metadata_json: dict[str, object] | None


class EvaluationRagService(Protocol):
    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
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
            if dataset is None:
                raise ResourceNotFound()
            if dataset.status != "active":
                raise ValidationFailed({"evaluation_dataset_id": "dataset is archived"})
            active_case_count = self.repository.count_cases(
                db,
                evaluation_dataset_id=payload.evaluation_dataset_id,
                status="active",
            )
            if active_case_count < 1:
                raise ValidationFailed({"evaluation_dataset_id": "dataset has no active cases"})
            dataset_name = dataset.dataset_name

        strategies = [strategy.value for strategy in _selected_strategies(payload)]
        metrics = [metric.value for metric in payload.metrics]
        strategy_type = strategies[0]
        trigger_type = payload.trigger_type.value
        run = self.repository.create_run(
            db,
            created_by=user.user_id,
            dataset_name=dataset_name,
            evaluation_dataset_id=payload.evaluation_dataset_id,
            case_limit=payload.case_limit,
            strategy_type=strategy_type,
            strategies=strategies,
            metrics=metrics,
            top_k=payload.top_k,
            rerank_top_n=payload.rerank_top_n,
            trigger_type=trigger_type,
            retrieval_settings_json=_retrieval_settings_snapshot(
                strategy_type=strategy_type,
                strategies=strategies,
                metrics=metrics,
                case_limit=payload.case_limit,
                top_k=payload.top_k,
                rerank_top_n=payload.rerank_top_n,
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
                "strategies": strategies,
                "metrics": metrics,
                "top_k": payload.top_k,
                "rerank_top_n": payload.rerank_top_n,
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
            strategies=[cast(RetrievalStrategy, strategy) for strategy in strategies],
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
        try:
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
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError() from exc
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
        fields_set = payload.model_fields_set
        self.repository.update_dataset(
            db,
            dataset=dataset,
            description=payload.description,
            version=payload.version,
            metadata_json=payload.metadata_json,
            updated_at=datetime.now(UTC),
            description_provided="description" in fields_set,
            metadata_json_provided="metadata_json" in fields_set,
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
        try:
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
        except IntegrityError as exc:
            db.rollback()
            raise ConflictError() from exc
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
            _assert_case_expected_signal(case, values)
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
                description_provided=True,
                metadata_json_provided=True,
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
            failure_candidates=self._failure_candidates(db, run=run),
        )

    def get_strategy_comparison(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationStrategyComparisonResponse:
        detail = self.get_run_detail(db, evaluation_run_id=evaluation_run_id)
        return EvaluationStrategyComparisonResponse(
            evaluation_run_id=evaluation_run_id,
            strategies=detail.strategies,
            metrics=detail.strategy_comparison,
        )

    def list_failure_candidates(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> EvaluationFailureCandidatesResponse:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        return EvaluationFailureCandidatesResponse(
            evaluation_run_id=evaluation_run_id,
            candidates=self._failure_candidates(db, run=run),
        )

    def promote_failures(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        payload: EvaluationFailurePromotionRequest,
    ) -> EvaluationFailurePromotionResponse:
        run = self.repository.get_run(db, evaluation_run_id=evaluation_run_id)
        if run is None:
            raise ResourceNotFound()
        target = self.repository.get_dataset(
            db,
            evaluation_dataset_id=payload.target_dataset_id,
        )
        if target is None:
            raise ResourceNotFound()
        if target.status != "active":
            raise ValidationFailed({"target_dataset_id": "target dataset is archived"})

        selected_types = set(payload.failure_types or [])
        candidates = [
            candidate
            for candidate in self._failure_candidates(db, run=run)
            if (not selected_types or candidate.failure_type in selected_types)
            and _severity_rank(candidate.severity) >= _severity_rank(payload.min_severity)
        ]
        candidates = _primary_failure_candidates(candidates)
        limit = min(payload.limit, self.settings.evaluation_failure_max_promotions_per_run)
        candidates = candidates[:limit]
        source_cases = self._promotion_source_cases(db, run)

        created_count = 0
        skipped_count = 0
        items: list[EvaluationFailurePromotionItem] = []
        for candidate in candidates:
            source_case = source_cases.get(candidate.evaluation_run_item_id)
            if source_case is None:
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        result_code="source_case_missing",
                    )
                )
                continue

            case_key = f"failure_{candidate.promotion_key[:16]}"
            existing = self.repository.get_case_by_key(
                db,
                evaluation_dataset_id=payload.target_dataset_id,
                case_key=case_key,
            )
            if existing is not None:
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        promoted_case_id=existing.evaluation_case_id,
                        case_key=existing.case_key,
                        result_code="already_exists",
                    )
                )
                continue

            try:
                with db.begin_nested():
                    created = self.repository.create_case(
                        db,
                        evaluation_dataset_id=payload.target_dataset_id,
                        case_key=case_key,
                        question=source_case.question,
                        expected_answer=source_case.expected_answer,
                        expected_keywords=source_case.expected_keywords,
                        expected_document_ids=source_case.expected_document_ids,
                        expected_chunk_ids=source_case.expected_chunk_ids,
                        required_citation=source_case.required_citation,
                        tags=_promotion_tags(source_case.tags, candidate.recommended_tags),
                        metadata_json=_promotion_metadata(candidate, source_case.metadata_json),
                        status="active",
                    )
            except IntegrityError as exc:
                existing = self.repository.get_case_by_key(
                    db,
                    evaluation_dataset_id=payload.target_dataset_id,
                    case_key=case_key,
                )
                if existing is None:
                    raise ConflictError("evaluation_case_conflict") from exc
                skipped_count += 1
                items.append(
                    EvaluationFailurePromotionItem(
                        promotion_key=candidate.promotion_key,
                        failure_type=candidate.failure_type,
                        strategy_type=candidate.strategy_type,
                        evaluation_run_item_id=candidate.evaluation_run_item_id,
                        evaluation_case_id=candidate.evaluation_case_id,
                        promoted_case_id=existing.evaluation_case_id,
                        case_key=existing.case_key,
                        result_code="already_exists",
                    )
                )
                continue
            created_count += 1
            items.append(
                EvaluationFailurePromotionItem(
                    promotion_key=candidate.promotion_key,
                    failure_type=candidate.failure_type,
                    strategy_type=candidate.strategy_type,
                    evaluation_run_item_id=candidate.evaluation_run_item_id,
                    evaluation_case_id=candidate.evaluation_case_id,
                    promoted_case_id=created.evaluation_case_id,
                    case_key=created.case_key,
                    result_code="created",
                )
            )

        db.commit()
        return EvaluationFailurePromotionResponse(
            evaluation_run_id=evaluation_run_id,
            target_dataset_id=payload.target_dataset_id,
            created_count=created_count,
            skipped_count=skipped_count,
            items=items,
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

        succeeded_count = 0
        failed_count = 0
        try:
            rag_service = self.rag_service_factory(self.settings, db)
            strategies = _strategy_values(config)
            requested_metrics = set(cast(list[str], config["metrics"]))
            top_k = cast(int | None, config["top_k"])
            rerank_top_n = cast(int | None, config["rerank_top_n"])
            for loaded_case in cases:
                for strategy_type in strategies:
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
                            case_metadata_json=loaded_case.metadata_json,
                            strategy_type=RetrievalStrategy(strategy_type),
                            requested_metrics=requested_metrics,
                            request_id=_case_request_id(
                                request_id,
                                case_key=loaded_case.case_key,
                                strategy_type=strategy_type,
                            ),
                            top_k=top_k,
                            rerank_top_n=rerank_top_n,
                        )
                    except Exception:
                        db.rollback()
                        failed_count += 1
                        self._store_case_failure(
                            db,
                            item_id=item_id,
                            case=loaded_case.case,
                            strategy_type=strategy_type,
                            requested_metrics=requested_metrics,
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
        summary = self._summary(db, run)
        item_count = len(cases) * len(_strategy_values(config))
        failure_candidates = self._failure_candidates(db, run=run)
        run.strategy_metrics_summary_json = _strategy_metrics_summary_json(
            strategies=_strategy_values(_config(run)),
            strategy_comparison=summary.strategy_comparison,
            metric_summary=summary.metric_summary,
            case_count=item_count,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            failure_candidates=failure_candidates,
        )
        if succeeded_count == 0:
            self.repository.mark_run_failed(
                db,
                run=run,
                error_code="all_cases_failed",
                error_message=None,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            raise EvaluationFixtureError("all_cases_failed")
        self.repository.mark_run_succeeded(db, run=run, finished_at=datetime.now(UTC))
        db.commit()
        return {
            "status": "succeeded",
            "evaluation_run_id": evaluation_run_id,
            "case_count": item_count,
            "succeeded_count": succeeded_count,
            "failed_count": failed_count,
        }

    def _run_case(
        self,
        db: Session,
        *,
        rag_service: EvaluationRagService,
        case: EvaluationCase,
        case_metadata_json: dict[str, object] | None,
        strategy_type: RetrievalStrategy,
        requested_metrics: set[str],
        request_id: str | None,
        top_k: int | None,
        rerank_top_n: int | None,
    ) -> dict[str, object]:
        started = time.perf_counter()
        strategy_runner = getattr(rag_service, "evaluate_strategy", None)
        if callable(strategy_runner):
            rag_result = strategy_runner(
                db,
                question=case.question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        else:
            rag_result = rag_service.evaluate_question(
                db,
                question=case.question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        metrics = _replace_metrics(
            calculate_metrics(
                EvaluationMetricInputs(
                    case=case,
                    answer_text=rag_result.answer_text,
                    citations=rag_result.citations,
                    confidence=rag_result.confidence,
                    retrieval_summary=rag_result.retrieval_score_summary,
                    retrieved_items=rag_result.retrieved_items,
                    latency_ms=latency_ms,
                    error_code=rag_result.error_code,
                )
            ),
            self._agentic_metrics(
                db,
                strategy_type=strategy_type,
                case_metadata_json=case_metadata_json,
                rag_result=rag_result,
            ),
        )
        metrics = _filter_metrics(metrics, requested_metrics)
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
        requested_metrics: set[str],
    ) -> None:
        item = db.get(EvaluationRunItem, item_id)
        if item is None:
            return
        metrics = _filter_metrics(
            failure_metrics(case, error_code="internal_error"),
            requested_metrics,
        )
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
        config = _config(run)
        metric_summary = _metric_summary(results_by_item)
        strategy_comparison = _strategy_comparison(items, results_by_item)
        job = self.repository.find_job_for_run(db, evaluation_run_id=run.evaluation_run_id)
        planned_item_count = (
            self._planned_item_count(db, run) if run.status in {"queued", "running"} else 0
        )
        case_count = max(len(items), planned_item_count)
        return EvaluationRunSummary(
            evaluation_run_id=run.evaluation_run_id,
            job_id=job.job_id if job is not None else None,
            evaluation_dataset_id=run.evaluation_dataset_id,
            dataset_name=cast(str, config["dataset_name"]),
            strategy_type=cast(RetrievalStrategy, run.strategy_type),
            strategies=[cast(RetrievalStrategy, strategy) for strategy in _strategy_values(config)],
            metric_names=cast(list[str], config["metrics"]),
            trigger_type=run.trigger_type,
            status=cast(EvaluationStatus, run.status),
            case_count=case_count,
            succeeded_count=sum(1 for item in items if item.status == "succeeded"),
            failed_count=sum(1 for item in items if item.status == "failed"),
            metric_summary=metric_summary,
            strategy_comparison=strategy_comparison,
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
        case_id = (
            next(
                (
                    str(result.details_json.get("case_id"))
                    for result in results
                    if result.metric_name == "case_metadata"
                    and isinstance(result.details_json, dict)
                    and result.details_json.get("case_id")
                ),
                None,
            )
            or item.case_key
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

    def _agentic_metrics(
        self,
        db: Session,
        *,
        strategy_type: RetrievalStrategy,
        case_metadata_json: dict[str, object] | None,
        rag_result: RagEvaluationResult,
    ) -> list[MetricValue]:
        if strategy_type != RetrievalStrategy.AGENTIC_ROUTER:
            return []
        retrieval_run = (
            db.get(RetrievalRun, rag_result.retrieval_run_id)
            if rag_result.retrieval_run_id is not None
            else None
        )
        decision = _dict_or_empty(
            retrieval_run.strategy_decision_json if retrieval_run is not None else None
        )
        score_summary = _dict_or_empty(
            retrieval_run.retrieval_score_summary if retrieval_run is not None else None
        )
        expected_strategy, acceptable_strategies = _expected_strategy_hints(case_metadata_json)
        selected_strategy = _safe_strategy_value(decision.get("selected_strategy"))
        execution_strategy = _safe_strategy_value(decision.get("execution_strategy"))
        accuracy: float | None = None
        accuracy_label = "not_applicable"
        not_applicable = True
        if expected_strategy or acceptable_strategies:
            accepted = set(acceptable_strategies)
            if expected_strategy:
                accepted.add(expected_strategy)
            accuracy = (
                1.0 if selected_strategy in accepted or execution_strategy in accepted else 0.0
            )
            accuracy_label = "correct" if accuracy == 1.0 else "incorrect"
            not_applicable = False

        fallback_used = _first_bool(
            decision.get("fallback_used"),
            score_summary.get("fallback_used"),
        )
        budget_exhausted = _first_bool(
            decision.get("budget_exhausted"),
            score_summary.get("budget_exhausted"),
        )
        sufficiency_score = _float_or_none(
            decision.get("sufficiency_score") or score_summary.get("sufficiency_score")
        )
        retrieval_call_count = _float_or_none(
            decision.get("retrieval_call_count") or score_summary.get("retrieval_call_count")
        )
        fallback_count = 1 if fallback_used else 0

        return [
            MetricValue(
                metric_name="strategy_selection_accuracy",
                metric_score=accuracy,
                metric_label=accuracy_label,
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "not_applicable": not_applicable,
                    "expected_strategy": expected_strategy,
                    "acceptable_strategies": acceptable_strategies,
                    "selected_strategy": selected_strategy,
                    "execution_strategy": execution_strategy,
                },
            ),
            MetricValue(
                metric_name="fallback_rate",
                metric_score=1.0 if fallback_used else 0.0,
                metric_label="used" if fallback_used else "not_used",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "fallback_used": fallback_used,
                    "fallback_strategy": _safe_strategy_value(decision.get("fallback_strategy")),
                    "fallback_reason": _safe_reason(decision.get("fallback_reason")),
                    "execution_strategy": execution_strategy,
                },
            ),
            MetricValue(
                metric_name="budget_exhausted_rate",
                metric_score=1.0 if budget_exhausted else 0.0,
                metric_label="exhausted" if budget_exhausted else "available",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "budget_exhausted": budget_exhausted,
                    "retrieval_call_count": retrieval_call_count,
                    "max_retrieval_calls": _float_or_none(
                        decision.get("max_retrieval_calls")
                        or score_summary.get("max_retrieval_calls")
                    ),
                },
            ),
            MetricValue(
                metric_name="sufficiency_score_avg",
                metric_score=sufficiency_score,
                metric_label=_metric_label(sufficiency_score),
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "sufficiency_score": sufficiency_score,
                    "sufficient": bool(decision.get("sufficient"))
                    if "sufficient" in decision
                    else None,
                    "sufficiency_reason_codes": _string_values(
                        decision.get("sufficiency_reason_codes")
                    ),
                },
            ),
            MetricValue(
                metric_name="retrieval_call_count_avg",
                metric_score=None,
                metric_value=retrieval_call_count,
                metric_label="count" if retrieval_call_count is not None else "not_applicable",
                details={
                    "schema_version": EVALUATION_SCHEMA_VERSION,
                    "retrieval_call_count": retrieval_call_count,
                    "fallback_count": fallback_count,
                },
            ),
        ]

    def _failure_candidates(
        self,
        db: Session,
        *,
        run: EvaluationRun,
    ) -> list[EvaluationFailureCandidate]:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        if not items:
            return []
        results_by_item = self.repository.list_results(
            db,
            evaluation_run_item_ids=[item.evaluation_run_item_id for item in items],
        )
        source_cases = self._promotion_source_cases(db, run)
        candidates: list[EvaluationFailureCandidate] = []
        for item in items:
            source_case = source_cases.get(item.evaluation_run_item_id)
            results = results_by_item.get(item.evaluation_run_item_id, [])
            metric_by_name = {result.metric_name: result for result in results}
            metric_snapshot = _metric_snapshot(metric_by_name)
            question_hash = _question_hash(source_case.question if source_case else item.case_key)
            case_key = source_case.case_key if source_case is not None else item.case_key

            for failure_type, severity, reason_codes in _failure_reasons(
                item,
                metric_by_name,
                self.settings,
            ):
                promotion_key = _promotion_key(
                    run=run,
                    item=item,
                    case_key=case_key,
                    question_hash=question_hash,
                    failure_type=failure_type,
                )
                candidates.append(
                    EvaluationFailureCandidate(
                        evaluation_run_id=run.evaluation_run_id,
                        evaluation_run_item_id=item.evaluation_run_item_id,
                        evaluation_case_id=item.evaluation_case_id,
                        case_key=case_key,
                        question_hash=question_hash,
                        strategy_type=cast(RetrievalStrategy, item.strategy_type),
                        failure_type=failure_type,
                        severity=severity,
                        failure_reason_codes=reason_codes,
                        metric_snapshot=metric_snapshot,
                        recommended_tags=[
                            "failure_promoted",
                            failure_type,
                            f"strategy_{item.strategy_type}",
                        ],
                        promotion_key=promotion_key,
                    )
                )
        return candidates

    def _promotion_source_cases(
        self,
        db: Session,
        run: EvaluationRun,
    ) -> dict[int, PromotionSourceCase]:
        items = self.repository.list_items(db, evaluation_run_id=run.evaluation_run_id)
        source_cases: dict[int, PromotionSourceCase] = {}
        fixture_by_key: dict[str, EvaluationCase] = {}
        if run.evaluation_dataset_id is None:
            try:
                fixture_by_key = {
                    case.case_id: case
                    for case in load_evaluation_cases(
                        cast(str, _config(run)["dataset_name"]),
                        case_limit=cast(int | None, _config(run)["case_limit"]),
                    )
                }
            except EvaluationFixtureError:
                fixture_by_key = {}

        for item in items:
            source: PromotionSourceCase | None = None
            if item.evaluation_case_id is not None:
                model = db.get(EvaluationCaseModel, item.evaluation_case_id)
                if model is not None:
                    source = PromotionSourceCase(
                        evaluation_case_id=model.evaluation_case_id,
                        case_key=model.case_key,
                        question=model.question,
                        expected_answer=model.expected_answer,
                        expected_keywords=_string_list(model.expected_keywords),
                        expected_document_ids=_int_list(model.expected_document_ids),
                        expected_chunk_ids=_int_list(model.expected_chunk_ids),
                        required_citation=model.required_citation,
                        tags=_string_list(model.tags),
                        metadata_json=model.metadata_json,
                    )
            elif item.case_key is not None and item.case_key in fixture_by_key:
                fixture = fixture_by_key[item.case_key]
                source = PromotionSourceCase(
                    evaluation_case_id=None,
                    case_key=fixture.case_id,
                    question=fixture.question,
                    expected_answer=fixture.expected_answer,
                    expected_keywords=list(fixture.expected_keywords),
                    expected_document_ids=list(fixture.expected_document_ids),
                    expected_chunk_ids=list(fixture.expected_chunk_ids),
                    required_citation=fixture.required_citation,
                    tags=[],
                    metadata_json=None,
                )
            if source is not None:
                source_cases[item.evaluation_run_item_id] = source
        return source_cases

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
                metadata_json=None,
                tags=None,
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

    def _planned_item_count(self, db: Session, run: EvaluationRun) -> int:
        config = _config(run)
        return self._planned_case_count(db, run) * len(_strategy_values(config))

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
    raw_strategies = config.get("strategies")
    raw_metrics = config.get("metrics")
    top_k = config.get("top_k")
    rerank_top_n = config.get("rerank_top_n")
    trigger_type = config.get("trigger_type") or run.trigger_type
    strategies = (
        [str(strategy) for strategy in raw_strategies if isinstance(strategy, str)]
        if isinstance(raw_strategies, list)
        else []
    )
    if not strategies and isinstance(strategy_type, str):
        strategies = [strategy_type]
    metrics = (
        [str(metric) for metric in raw_metrics if isinstance(metric, str)]
        if isinstance(raw_metrics, list)
        else [metric.value for metric in DEFAULT_EVALUATION_METRICS]
    )
    return {
        "dataset_name": dataset_name if isinstance(dataset_name, str) else "phase1_smoke",
        "evaluation_dataset_id": (
            evaluation_dataset_id if isinstance(evaluation_dataset_id, int) else None
        ),
        "case_limit": case_limit if isinstance(case_limit, int) else None,
        "strategy_type": strategy_type if isinstance(strategy_type, str) else "dense",
        "strategies": strategies,
        "metrics": metrics,
        "top_k": top_k if isinstance(top_k, int) else None,
        "rerank_top_n": rerank_top_n if isinstance(rerank_top_n, int) else None,
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
        metric_value=_decimal_metric_value(
            metric.metric_value if metric.metric_value is not None else metric.metric_score
        ),
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


def _filter_metrics(metrics: list[MetricValue], requested_metrics: set[str]) -> list[MetricValue]:
    return [metric for metric in metrics if metric.metric_name in requested_metrics]


def _replace_metrics(base: list[MetricValue], replacements: list[MetricValue]) -> list[MetricValue]:
    if not replacements:
        return base
    replacement_names = {metric.metric_name for metric in replacements}
    return [metric for metric in base if metric.metric_name not in replacement_names] + replacements


def _metric_label(value: float | None) -> str:
    if value is None:
        return "not_applicable"
    if value >= 0.75:
        return "high"
    if value >= 0.45:
        return "medium"
    return "low"


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_values(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:80] for item in value if isinstance(item, str)]


def _safe_strategy_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return RetrievalStrategy(value).value
    except ValueError:
        return None


def _safe_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace(" ", "_")
    if not text:
        return None
    return text[:80]


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return None


def _first_bool(*values: object) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
    return False


def _expected_strategy_hints(
    metadata_json: dict[str, object] | None,
) -> tuple[str | None, list[str]]:
    metadata = metadata_json or {}
    expected = _safe_strategy_value(metadata.get("expected_strategy"))
    raw_acceptable = metadata.get("acceptable_strategies")
    acceptable: list[str] = []
    if isinstance(raw_acceptable, list):
        for item in raw_acceptable:
            strategy = _safe_strategy_value(item)
            if strategy is not None and strategy not in acceptable:
                acceptable.append(strategy)
    return expected, acceptable


def _selected_strategies(payload: EvaluationRunCreateRequest) -> list[RetrievalStrategy]:
    return [
        RetrievalStrategy(strategy.value)
        for strategy in (payload.strategies or [payload.strategy_type])
    ]


def _strategy_values(config: dict[str, object]) -> list[str]:
    raw = config.get("strategies")
    values = [str(strategy) for strategy in raw] if isinstance(raw, list) else []
    enabled = {
        RetrievalStrategy.DENSE.value,
        RetrievalStrategy.SPARSE.value,
        RetrievalStrategy.HYBRID.value,
        RetrievalStrategy.AGENTIC_ROUTER.value,
    }
    filtered = [strategy for strategy in values if strategy in enabled]
    return filtered or [DEFAULT_RETRIEVAL_STRATEGY.value]


def _case_request_id(
    request_id: str | None,
    *,
    case_key: str,
    strategy_type: str,
) -> str | None:
    if request_id is None:
        return None
    derived = f"{request_id}:{strategy_type}:{case_key}"
    if len(derived) <= RETRIEVAL_RUN_REQUEST_ID_MAX_LENGTH:
        return derived
    digest = hashlib.sha256(derived.encode("utf-8")).hexdigest()[:12]
    suffix = f":{strategy_type}:{digest}"
    prefix_length = max(RETRIEVAL_RUN_REQUEST_ID_MAX_LENGTH - len(suffix), 0)
    return f"{request_id[:prefix_length]}{suffix}"


def _unique_case_count(items: list[EvaluationRunItem]) -> int:
    keys = {
        item.case_key or f"id:{item.evaluation_case_id}"
        for item in items
        if item.case_key or item.evaluation_case_id is not None
    }
    return len(keys) if keys else len(items)


def _metric_summary(results_by_item: dict[int, list[EvaluationResult]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for results in results_by_item.values():
        for result in results:
            if result.metric_name == "case_metadata":
                continue
            value = _result_numeric_value(result)
            if value is None:
                continue
            values.setdefault(result.metric_name, []).append(value)
    return {
        metric_name: (
            _percentile(scores, 0.95)
            if metric_name == "p95_latency"
            else round(sum(scores) / len(scores), 6)
        )
        for metric_name, scores in sorted(values.items())
        if scores
    }


def _strategy_comparison(
    items: list[EvaluationRunItem],
    results_by_item: dict[int, list[EvaluationResult]],
) -> list[StrategyComparisonMetric]:
    items_by_id = {item.evaluation_run_item_id: item for item in items}
    values: dict[tuple[str, str], list[float]] = {}
    not_applicable: dict[tuple[str, str], int] = {}
    failed_count: dict[str, int] = {}
    for item in items:
        if item.status == "failed":
            failed_count[item.strategy_type] = failed_count.get(item.strategy_type, 0) + 1
    for item_id, results in results_by_item.items():
        item_for_result = items_by_id.get(item_id)
        if item_for_result is None:
            continue
        for result in results:
            if result.metric_name == "case_metadata":
                continue
            key = (item_for_result.strategy_type, result.metric_name)
            value = _result_numeric_value(result)
            if value is None:
                not_applicable[key] = not_applicable.get(key, 0) + 1
                continue
            values.setdefault(key, []).append(value)

    metrics: list[StrategyComparisonMetric] = []
    all_keys = sorted(set(values).union(not_applicable))
    for strategy_type, metric_name in all_keys:
        series = sorted(values.get((strategy_type, metric_name), []))
        average = round(sum(series) / len(series), 6) if series else None
        metrics.append(
            StrategyComparisonMetric(
                strategy_type=cast(RetrievalStrategy, strategy_type),
                metric_name=metric_name,
                average=average,
                p50=_percentile(series, 0.50) if series else None,
                p95=_percentile(series, 0.95) if series else None,
                count=len(series),
                failed_count=failed_count.get(strategy_type, 0),
                not_applicable_count=not_applicable.get((strategy_type, metric_name), 0),
            )
        )
    return metrics


def _result_numeric_value(result: EvaluationResult) -> float | None:
    if result.metric_name == "p95_latency":
        return float(result.metric_value) if result.metric_value is not None else None
    if result.metric_score is not None:
        return float(result.metric_score)
    if result.metric_value is not None:
        return float(result.metric_value)
    return None


def _metric_snapshot(metric_by_name: dict[str, EvaluationResult]) -> dict[str, object]:
    snapshot: dict[str, object] = {}
    for name, result in sorted(metric_by_name.items()):
        if name == "case_metadata":
            continue
        value = _result_numeric_value(result)
        if value is not None:
            snapshot[name] = round(value, 6)
    return snapshot


def _metric_score(metric_by_name: dict[str, EvaluationResult], name: str) -> float | None:
    result = metric_by_name.get(name)
    if result is None or result.metric_score is None:
        return None
    return float(result.metric_score)


def _metric_value(metric_by_name: dict[str, EvaluationResult], name: str) -> float | None:
    result = metric_by_name.get(name)
    if result is None or result.metric_value is None:
        return None
    return float(result.metric_value)


def _failure_reasons(
    item: EvaluationRunItem,
    metric_by_name: dict[str, EvaluationResult],
    settings: Settings,
) -> list[tuple[str, EvaluationFailureSeverity, list[str]]]:
    reasons: list[tuple[str, EvaluationFailureSeverity, list[str]]] = []
    no_context = _metric_score(metric_by_name, "no_context_rate")
    if item.error_code == "no_context_found" or no_context == 1.0:
        reasons.append(("no_context", EvaluationFailureSeverity.HIGH, ["no_context_found"]))
    recall = _metric_score(metric_by_name, "recall_at_k")
    if recall is not None and recall < settings.evaluation_failure_low_recall_threshold:
        reasons.append(("low_recall", EvaluationFailureSeverity.MEDIUM, ["recall_below_threshold"]))
    mrr = _metric_score(metric_by_name, "mrr")
    if mrr is not None and mrr < settings.evaluation_failure_low_mrr_threshold:
        reasons.append(("low_mrr", EvaluationFailureSeverity.MEDIUM, ["mrr_below_threshold"]))
    citation = _metric_score(metric_by_name, "citation_coverage")
    if (
        citation is not None
        and citation < settings.evaluation_failure_low_citation_coverage_threshold
    ):
        reasons.append(
            (
                "low_citation_coverage",
                EvaluationFailureSeverity.MEDIUM,
                ["citation_coverage_below_threshold"],
            )
        )
    groundedness = _metric_score(metric_by_name, "groundedness")
    if (
        groundedness is not None
        and groundedness < settings.evaluation_failure_low_groundedness_threshold
    ):
        reasons.append(
            ("low_groundedness", EvaluationFailureSeverity.MEDIUM, ["groundedness_below_threshold"])
        )
    faithfulness = _metric_score(metric_by_name, "faithfulness")
    if (
        faithfulness is not None
        and faithfulness < settings.evaluation_failure_low_faithfulness_threshold
    ):
        reasons.append(
            ("low_faithfulness", EvaluationFailureSeverity.MEDIUM, ["faithfulness_below_threshold"])
        )
    strategy_accuracy = _metric_score(metric_by_name, "strategy_selection_accuracy")
    if strategy_accuracy == 0.0:
        reasons.append(
            (
                "strategy_selection_incorrect",
                EvaluationFailureSeverity.MEDIUM,
                ["strategy_selection_mismatch"],
            )
        )
    if _metric_score(metric_by_name, "budget_exhausted_rate") == 1.0:
        reasons.append(
            ("budget_exhausted", EvaluationFailureSeverity.HIGH, ["retrieval_budget_exhausted"])
        )
    latency = item.latency_ms or _metric_value(metric_by_name, "p95_latency")
    if latency is not None and latency > settings.evaluation_failure_high_latency_ms:
        reasons.append(("high_latency", EvaluationFailureSeverity.LOW, ["latency_above_threshold"]))
    if item.error_code in {"retrieval_failed", "internal_error"}:
        reasons.append(
            ("retrieval_exception", EvaluationFailureSeverity.HIGH, [str(item.error_code)])
        )
    if item.error_code == "generation_failed":
        reasons.append(
            ("generation_exception", EvaluationFailureSeverity.HIGH, ["generation_failed"])
        )
    if item.error_code == "citation_build_failed":
        reasons.append(
            ("citation_build_failed", EvaluationFailureSeverity.HIGH, ["citation_build_failed"])
        )
    if (
        item.status == "failed"
        and _metric_score(metric_by_name, "fallback_rate") == 1.0
        and item.error_code
    ):
        reasons.append(("fallback_failed", EvaluationFailureSeverity.HIGH, ["fallback_failed"]))
    return _dedupe_failures(reasons)


def _dedupe_failures(
    failures: list[tuple[str, EvaluationFailureSeverity, list[str]]],
) -> list[tuple[str, EvaluationFailureSeverity, list[str]]]:
    by_type: dict[str, tuple[str, EvaluationFailureSeverity, list[str]]] = {}
    for failure_type, severity, reason_codes in failures:
        existing = by_type.get(failure_type)
        if existing is None or _severity_rank(severity) > _severity_rank(existing[1]):
            by_type[failure_type] = (failure_type, severity, reason_codes)
    return list(by_type.values())


def _question_hash(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _promotion_key(
    *,
    run: EvaluationRun,
    item: EvaluationRunItem,
    case_key: str | None,
    question_hash: str,
    failure_type: str,
) -> str:
    config = _config(run)
    dataset_identity = run.evaluation_dataset_id or cast(str, config["dataset_name"])
    base = ":".join(
        [
            str(dataset_identity),
            str(case_key or item.evaluation_case_id),
            item.strategy_type,
            failure_type,
            question_hash,
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _promotion_tags(source_tags: list[str], recommended_tags: list[str]) -> list[str]:
    tags: list[str] = []
    for tag in [*source_tags, *recommended_tags]:
        safe = str(tag).strip().lower().replace(" ", "_")[:80]
        if safe and safe not in tags:
            tags.append(safe)
    return tags[:20]


def _primary_failure_candidates(
    candidates: list[EvaluationFailureCandidate],
) -> list[EvaluationFailureCandidate]:
    by_item: dict[int, EvaluationFailureCandidate] = {}
    for candidate in candidates:
        existing = by_item.get(candidate.evaluation_run_item_id)
        if existing is None or _failure_candidate_priority(candidate) < _failure_candidate_priority(
            existing
        ):
            by_item[candidate.evaluation_run_item_id] = candidate
    return sorted(by_item.values(), key=lambda candidate: candidate.evaluation_run_item_id)


def _failure_candidate_priority(candidate: EvaluationFailureCandidate) -> tuple[int, str, str]:
    return (
        -_severity_rank(candidate.severity),
        candidate.failure_type,
        candidate.promotion_key,
    )


def _promotion_metadata(
    candidate: EvaluationFailureCandidate,
    source_metadata_json: dict[str, object] | None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": "failure_promoted",
        "source_evaluation_run_id": candidate.evaluation_run_id,
        "source_evaluation_run_item_id": candidate.evaluation_run_item_id,
        "source_evaluation_case_id": candidate.evaluation_case_id,
        "source_strategy_type": candidate.strategy_type.value,
        "failure_type": candidate.failure_type,
        "failure_reason_codes": candidate.failure_reason_codes,
        "metric_snapshot": candidate.metric_snapshot,
        "promotion_key": candidate.promotion_key,
        "question_hash": candidate.question_hash,
    }
    expected_strategy, acceptable_strategies = _expected_strategy_hints(source_metadata_json)
    if expected_strategy is not None:
        metadata["expected_strategy"] = expected_strategy
    if acceptable_strategies:
        metadata["acceptable_strategies"] = acceptable_strategies
    return metadata


def _failure_summary(candidates: list[EvaluationFailureCandidate]) -> dict[str, object]:
    by_type: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for candidate in candidates:
        by_type[candidate.failure_type] = by_type.get(candidate.failure_type, 0) + 1
        by_strategy[candidate.strategy_type.value] = (
            by_strategy.get(candidate.strategy_type.value, 0) + 1
        )
        by_severity[candidate.severity.value] = by_severity.get(candidate.severity.value, 0) + 1
    return {
        "total_count": len(candidates),
        "by_type": by_type,
        "by_strategy": by_strategy,
        "by_severity": by_severity,
    }


def _severity_rank(severity: EvaluationFailureSeverity) -> int:
    return {
        EvaluationFailureSeverity.LOW: 1,
        EvaluationFailureSeverity.MEDIUM: 2,
        EvaluationFailureSeverity.HIGH: 3,
    }[severity]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    index = int(round((len(ordered) - 1) * percentile))
    return round(ordered[min(max(index, 0), len(ordered) - 1)], 6)


def _metric_summary_json(metrics: list[MetricValue]) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "metrics": {
            metric.metric_name: (
                metric.metric_value if metric.metric_value is not None else metric.metric_score
            )
            for metric in metrics
            if (metric.metric_score is not None or metric.metric_value is not None)
            and metric.metric_name != "case_metadata"
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
    strategies: list[str],
    metrics: list[str],
    case_limit: int | None,
    top_k: int | None,
    rerank_top_n: int | None,
) -> dict[str, object]:
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "strategy_type": strategy_type,
        "strategies": strategies,
        "metrics": metrics,
        "case_limit": case_limit,
        "top_k": top_k,
        "rerank_top_n": rerank_top_n,
        "runner_implementation": "phase2_strategy_evaluation_runner",
        "strategy_runner_enabled": True,
    }


def _strategy_metrics_summary_json(
    *,
    strategies: list[str],
    strategy_comparison: list[StrategyComparisonMetric],
    metric_summary: dict[str, float],
    case_count: int,
    succeeded_count: int,
    failed_count: int,
    failure_candidates: list[EvaluationFailureCandidate] | None = None,
) -> dict[str, object]:
    by_strategy: dict[str, dict[str, object]] = {}
    for comparison in strategy_comparison:
        entry = by_strategy.setdefault(
            comparison.strategy_type.value,
            {
                "metric_summary": {},
                "case_count": 0,
                "succeeded_count": 0,
                "failed_count": comparison.failed_count,
            },
        )
        case_value = entry.get("case_count")
        succeeded_value = entry.get("succeeded_count")
        failed_value = entry.get("failed_count")
        observed_count = (
            comparison.count + comparison.not_applicable_count + comparison.failed_count
        )
        entry["case_count"] = max(case_value if isinstance(case_value, int) else 0, observed_count)
        entry["succeeded_count"] = max(
            succeeded_value if isinstance(succeeded_value, int) else 0,
            comparison.count + comparison.not_applicable_count,
        )
        entry["failed_count"] = max(
            failed_value if isinstance(failed_value, int) else 0,
            comparison.failed_count,
        )
        if comparison.average is not None:
            cast(dict[str, float], entry["metric_summary"])[str(comparison.metric_name)] = (
                comparison.average
            )
    failure_summary = _failure_summary(failure_candidates or [])
    agentic_metrics = cast(
        dict[str, float],
        by_strategy.get(RetrievalStrategy.AGENTIC_ROUTER.value, {}).get("metric_summary", {}),
    )
    agentic_summary: dict[str, object] | None = None
    if RetrievalStrategy.AGENTIC_ROUTER.value in strategies or agentic_metrics:
        agentic_entry = by_strategy.get(RetrievalStrategy.AGENTIC_ROUTER.value, {})
        agentic_summary = {
            "strategy_type": RetrievalStrategy.AGENTIC_ROUTER.value,
            "case_count": agentic_entry.get("case_count", case_count),
            "fallback_rate": agentic_metrics.get("fallback_rate"),
            "budget_exhausted_rate": agentic_metrics.get("budget_exhausted_rate"),
            "strategy_selection_accuracy": agentic_metrics.get("strategy_selection_accuracy"),
            "sufficiency_score_avg": agentic_metrics.get("sufficiency_score_avg"),
            "retrieval_call_count_avg": agentic_metrics.get("retrieval_call_count_avg"),
            "no_context_rate": agentic_metrics.get("no_context_rate"),
            "p95_latency": agentic_metrics.get("p95_latency"),
        }
    payload: dict[str, object] = {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "strategies": strategies,
        "metric_summary": metric_summary,
        "strategy_metrics": by_strategy,
        "case_count": case_count,
        "succeeded_count": succeeded_count,
        "failed_count": failed_count,
    }
    if agentic_summary is not None:
        payload["agentic_summary"] = agentic_summary
    payload["failure_summary"] = failure_summary
    return payload


def _loaded_case_from_model(case: EvaluationCaseModel) -> LoadedEvaluationCase:
    return LoadedEvaluationCase(
        case=EvaluationCase(
            case_id=case.case_key,
            question=case.question,
            expected_keywords=tuple(_string_list(case.expected_keywords)),
            required_citation=case.required_citation,
            expected_answer=case.expected_answer,
            expected_document_ids=tuple(_int_list(case.expected_document_ids)),
            expected_chunk_ids=tuple(_int_list(case.expected_chunk_ids)),
        ),
        evaluation_case_id=case.evaluation_case_id,
        case_key=case.case_key,
        metadata_json=case.metadata_json,
        tags=_string_list(case.tags),
    )


def _assert_case_expected_signal(
    case: EvaluationCaseModel,
    values: dict[str, object],
) -> None:
    expected_answer = values.get("expected_answer", case.expected_answer)
    expected_keywords = values.get("expected_keywords", case.expected_keywords)
    if _string_list(expected_keywords) or (isinstance(expected_answer, str) and expected_answer):
        return
    raise ValidationFailed({"expected_signal": "expected_keywords or expected_answer is required"})


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _int_list(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if isinstance(item, int) and not isinstance(item, bool)]
