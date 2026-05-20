from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.evaluation_models import EvaluationResult
from app.db.models import EvaluationRun, EvaluationRunItem, Job


@dataclass(frozen=True)
class EvaluationResultInput:
    metric_name: str
    metric_score: Decimal | None
    metric_label: str | None
    details_json: dict[str, object] | None


class EvaluationRepository:
    def create_run(
        self,
        db: Session,
        *,
        created_by: int,
        dataset_name: str,
        case_limit: int | None,
    ) -> EvaluationRun:
        run = EvaluationRun(
            created_by=created_by,
            status="queued",
            target_type="fixture_dataset",
            target_id=None,
            metrics_config={
                "dataset_name": dataset_name,
                "case_limit": case_limit,
                "metrics": [
                    "faithfulness",
                    "groundedness",
                    "citation_coverage",
                    "context_precision",
                ],
            },
        )
        db.add(run)
        db.flush()
        return run

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

    def list_runs(self, db: Session, *, offset: int, limit: int) -> tuple[list[EvaluationRun], int]:
        total = int(db.scalar(select(func.count()).select_from(EvaluationRun)) or 0)
        rows = list(
            db.scalars(
                select(EvaluationRun)
                .order_by(EvaluationRun.created_at.desc(), EvaluationRun.evaluation_run_id.desc())
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
    ) -> EvaluationRunItem:
        item = EvaluationRunItem(evaluation_run_id=evaluation_run_id, status=status)
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
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        item.status = status
        item.retrieval_run_id = retrieval_run_id
        item.faithfulness_score = faithfulness_score
        item.groundedness_score = groundedness_score
        item.citation_coverage = citation_coverage
        item.latency_ms = latency_ms
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
                metric_label=result.metric_label,
                details_json=result.details_json,
            )
            for result in results
        ]
        db.add_all(rows)
        db.flush()
        return rows
