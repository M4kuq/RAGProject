from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.evaluation_models import EvaluationResult
from app.db.models import EvaluationCase, EvaluationDataset, EvaluationRun, EvaluationRunItem, Job
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY


@dataclass(frozen=True)
class EvaluationResultInput:
    metric_name: str
    metric_score: Decimal | None
    metric_value: Decimal | None
    metric_label: str | None
    details_json: dict[str, object] | None
    metric_detail_json: dict[str, object] | None
    strategy_type: str


class EvaluationRepository:
    def create_run(
        self,
        db: Session,
        *,
        created_by: int,
        dataset_name: str,
        evaluation_dataset_id: int | None,
        case_limit: int | None,
        strategy_type: str,
        trigger_type: str,
        retrieval_settings_json: dict[str, object] | None,
    ) -> EvaluationRun:
        run = EvaluationRun(
            created_by=created_by,
            status="queued",
            target_type="fixture_dataset",
            target_id=None,
            evaluation_dataset_id=evaluation_dataset_id,
            strategy_type=strategy_type,
            trigger_type=trigger_type,
            retrieval_settings_json=retrieval_settings_json,
            metrics_config={
                "dataset_name": dataset_name,
                "evaluation_dataset_id": evaluation_dataset_id,
                "case_limit": case_limit,
                "strategy_type": strategy_type,
                "trigger_type": trigger_type,
                "metrics": [
                    "recall_at_k",
                    "mrr",
                    "faithfulness",
                    "groundedness",
                    "citation_coverage",
                    "context_precision",
                    "no_context_rate",
                    "p95_latency",
                    "strategy_selection_accuracy",
                ],
            },
        )
        db.add(run)
        db.flush()
        return run

    def create_dataset(
        self,
        db: Session,
        *,
        dataset_name: str,
        description: str | None,
        version: str,
        source_type: str,
        status: str,
        metadata_json: dict[str, object] | None,
        created_by: int | None,
    ) -> EvaluationDataset:
        dataset = EvaluationDataset(
            dataset_name=dataset_name,
            description=description,
            version=version,
            source_type=source_type,
            status=status,
            metadata_json=metadata_json,
            created_by=created_by,
        )
        db.add(dataset)
        db.flush()
        return dataset

    def get_dataset(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> EvaluationDataset | None:
        return db.get(EvaluationDataset, evaluation_dataset_id)

    def get_dataset_by_name(self, db: Session, *, dataset_name: str) -> EvaluationDataset | None:
        return db.scalar(
            select(EvaluationDataset).where(EvaluationDataset.dataset_name == dataset_name)
        )

    def list_datasets(
        self,
        db: Session,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
    ) -> tuple[list[EvaluationDataset], int]:
        base = select(EvaluationDataset)
        if status is not None:
            base = base.where(EvaluationDataset.status == status)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = list(
            db.scalars(
                base.order_by(
                    EvaluationDataset.created_at.desc(),
                    EvaluationDataset.evaluation_dataset_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return rows, total

    def update_dataset(
        self,
        db: Session,
        *,
        dataset: EvaluationDataset,
        description: str | None = None,
        version: str | None = None,
        metadata_json: dict[str, object] | None = None,
        updated_at: datetime,
    ) -> None:
        if description is not None:
            dataset.description = description
        if version is not None:
            dataset.version = version
        if metadata_json is not None:
            dataset.metadata_json = metadata_json
        dataset.updated_at = updated_at
        db.flush()

    def archive_dataset(
        self,
        db: Session,
        *,
        dataset: EvaluationDataset,
        updated_at: datetime,
    ) -> None:
        dataset.status = "archived"
        dataset.updated_at = updated_at
        db.flush()

    def create_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        case_key: str,
        question: str,
        expected_answer: str | None,
        expected_keywords: list[str],
        expected_document_ids: list[int],
        expected_chunk_ids: list[int],
        required_citation: bool,
        tags: list[str],
        metadata_json: dict[str, object] | None,
        status: str,
    ) -> EvaluationCase:
        case = EvaluationCase(
            evaluation_dataset_id=evaluation_dataset_id,
            case_key=case_key,
            question=question,
            expected_answer=expected_answer,
            expected_keywords=expected_keywords,
            expected_document_ids=expected_document_ids,
            expected_chunk_ids=expected_chunk_ids,
            required_citation=required_citation,
            tags=tags,
            metadata_json=metadata_json,
            status=status,
        )
        db.add(case)
        db.flush()
        return case

    def get_case(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        evaluation_case_id: int,
    ) -> EvaluationCase | None:
        return db.scalar(
            select(EvaluationCase).where(
                EvaluationCase.evaluation_dataset_id == evaluation_dataset_id,
                EvaluationCase.evaluation_case_id == evaluation_case_id,
            )
        )

    def get_case_by_key(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        case_key: str,
    ) -> EvaluationCase | None:
        return db.scalar(
            select(EvaluationCase).where(
                EvaluationCase.evaluation_dataset_id == evaluation_dataset_id,
                EvaluationCase.case_key == case_key,
            )
        )

    def list_cases(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        offset: int = 0,
        limit: int | None = None,
        status: str | None = None,
    ) -> tuple[list[EvaluationCase], int]:
        base = select(EvaluationCase).where(
            EvaluationCase.evaluation_dataset_id == evaluation_dataset_id
        )
        if status is not None:
            base = base.where(EvaluationCase.status == status)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows_stmt = base.order_by(EvaluationCase.evaluation_case_id.asc()).offset(offset)
        if limit is not None:
            rows_stmt = rows_stmt.limit(limit)
        return list(db.scalars(rows_stmt).all()), total

    def update_case(
        self,
        db: Session,
        *,
        case: EvaluationCase,
        values: dict[str, object],
        updated_at: datetime,
    ) -> None:
        for key, value in values.items():
            setattr(case, key, value)
        case.updated_at = updated_at
        db.flush()

    def archive_case(
        self,
        db: Session,
        *,
        case: EvaluationCase,
        updated_at: datetime,
    ) -> None:
        case.status = "archived"
        case.updated_at = updated_at
        db.flush()

    def count_cases(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        status: str | None = None,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(EvaluationCase)
            .where(EvaluationCase.evaluation_dataset_id == evaluation_dataset_id)
        )
        if status is not None:
            stmt = stmt.where(EvaluationCase.status == status)
        return int(db.scalar(stmt) or 0)

    def get_run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        for_update: bool = False,
    ) -> EvaluationRun | None:
        statement = select(EvaluationRun).where(
            EvaluationRun.evaluation_run_id == evaluation_run_id
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def list_runs(
        self,
        db: Session,
        *,
        offset: int,
        limit: int,
        status: str | None = None,
    ) -> tuple[list[EvaluationRun], int]:
        base = select(EvaluationRun)
        if status is not None:
            base = base.where(EvaluationRun.status == status)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = list(
            db.scalars(
                base.order_by(
                    EvaluationRun.created_at.desc(),
                    EvaluationRun.evaluation_run_id.desc(),
                )
                .offset(offset)
                .limit(limit)
            ).all()
        )
        return rows, total

    def find_job_for_run(self, db: Session, *, evaluation_run_id: int) -> Job | None:
        return db.scalar(
            select(Job)
            .where(
                Job.job_type == "evaluation_run",
                Job.target_type == "evaluation_run",
                Job.target_id == evaluation_run_id,
            )
            .order_by(Job.created_at.desc(), Job.job_id.desc())
        )

    def list_items(self, db: Session, *, evaluation_run_id: int) -> list[EvaluationRunItem]:
        return list(
            db.scalars(
                select(EvaluationRunItem)
                .where(EvaluationRunItem.evaluation_run_id == evaluation_run_id)
                .order_by(
                    EvaluationRunItem.evaluation_run_item_id.asc(),
                )
            ).all()
        )

    def list_results(
        self,
        db: Session,
        *,
        evaluation_run_item_ids: Iterable[int],
    ) -> dict[int, list[EvaluationResult]]:
        ids = list(evaluation_run_item_ids)
        if not ids:
            return {}
        rows = list(
            db.scalars(
                select(EvaluationResult)
                .where(EvaluationResult.evaluation_run_item_id.in_(ids))
                .order_by(
                    EvaluationResult.evaluation_run_item_id.asc(),
                    EvaluationResult.metric_name.asc(),
                )
            ).all()
        )
        results: dict[int, list[EvaluationResult]] = {}
        for row in rows:
            results.setdefault(row.evaluation_run_item_id, []).append(row)
        return results

    def mark_run_running(self, db: Session, *, run: EvaluationRun, started_at: datetime) -> None:
        run.status = "running"
        run.error_code = None
        run.error_message = None
        run.started_at = started_at
        run.finished_at = None
        run.updated_at = started_at
        db.flush()

    def mark_run_succeeded(self, db: Session, *, run: EvaluationRun, finished_at: datetime) -> None:
        run.status = "succeeded"
        run.error_code = None
        run.error_message = None
        run.finished_at = finished_at
        run.updated_at = finished_at
        db.flush()

    def mark_run_failed(
        self,
        db: Session,
        *,
        run: EvaluationRun,
        error_code: str,
        error_message: str | None,
        finished_at: datetime,
    ) -> None:
        run.status = "failed"
        run.error_code = error_code
        run.error_message = error_message
        run.finished_at = finished_at
        run.updated_at = finished_at
        db.flush()

    def delete_items_and_results(self, db: Session, *, evaluation_run_id: int) -> None:
        item_ids = [
            row[0]
            for row in db.execute(
                select(EvaluationRunItem.evaluation_run_item_id).where(
                    EvaluationRunItem.evaluation_run_id == evaluation_run_id
                )
            ).all()
        ]
        if item_ids:
            db.execute(
                delete(EvaluationResult).where(
                    EvaluationResult.evaluation_run_item_id.in_(item_ids)
                )
            )
        db.execute(
            delete(EvaluationRunItem).where(
                EvaluationRunItem.evaluation_run_id == evaluation_run_id
            )
        )
        db.flush()

    def create_item(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        status: str,
        strategy_type: str = DEFAULT_RETRIEVAL_STRATEGY.value,
        evaluation_case_id: int | None = None,
        case_key: str | None = None,
    ) -> EvaluationRunItem:
        item = EvaluationRunItem(
            evaluation_run_id=evaluation_run_id,
            status=status,
            strategy_type=strategy_type,
            evaluation_case_id=evaluation_case_id,
            case_key=case_key,
        )
        db.add(item)
        db.flush()
        return item

    def finish_item(
        self,
        db: Session,
        *,
        item: EvaluationRunItem,
        status: str,
        retrieval_run_id: int | None,
        faithfulness_score: Decimal | None,
        groundedness_score: Decimal | None,
        citation_coverage: Decimal | None,
        latency_ms: int | None,
        latency_breakdown_json: dict[str, object] | None,
        metric_summary_json: dict[str, object] | None,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        item.status = status
        item.retrieval_run_id = retrieval_run_id
        item.faithfulness_score = faithfulness_score
        item.groundedness_score = groundedness_score
        item.citation_coverage = citation_coverage
        item.latency_ms = latency_ms
        item.latency_breakdown_json = latency_breakdown_json
        item.metric_summary_json = metric_summary_json
        item.error_code = error_code
        item.error_message = error_message
        db.flush()

    def save_results(
        self,
        db: Session,
        *,
        evaluation_run_item_id: int,
        results: list[EvaluationResultInput],
    ) -> list[EvaluationResult]:
        rows = [
            EvaluationResult(
                evaluation_run_item_id=evaluation_run_item_id,
                metric_name=result.metric_name,
                metric_score=result.metric_score,
                metric_value=result.metric_value,
                metric_label=result.metric_label,
                details_json=result.details_json,
                metric_detail_json=result.metric_detail_json,
                strategy_type=result.strategy_type,
            )
            for result in results
        ]
        db.add_all(rows)
        db.flush()
        return rows
