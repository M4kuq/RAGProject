from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, null, select
from sqlalchemy.orm import Session

from app.db.evaluation_models import (
    EvaluationAuxiliaryJudgment,
    EvaluationCorpusSource,
    EvaluationHumanCalibration,
    EvaluationResult,
    EvaluationReviewPayload,
)
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    EvaluationRunItem,
    Job,
)
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
        strategies: list[str],
        metrics: list[str],
        evaluation_scope: str,
        top_k: int | None,
        rerank_top_n: int | None,
        generation_provider: str | None,
        generation_model: str | None,
        trigger_type: str,
        corpus_fingerprint: str | None,
        retrieval_settings_json: dict[str, object] | None,
    ) -> EvaluationRun:
        metrics_config: dict[str, object] = {
            "dataset_name": dataset_name,
            "evaluation_dataset_id": evaluation_dataset_id,
            "case_limit": case_limit,
            "strategy_type": strategy_type,
            "strategies": strategies,
            "trigger_type": trigger_type,
            "metrics": metrics,
            "evaluation_scope": evaluation_scope,
            "top_k": top_k,
            "rerank_top_n": rerank_top_n,
        }
        if generation_provider is not None:
            metrics_config["generation_provider"] = generation_provider
        if generation_model is not None:
            metrics_config["generation_model"] = generation_model
        run = EvaluationRun(
            created_by=created_by,
            status="queued",
            target_type="fixture_dataset",
            target_id=None,
            evaluation_dataset_id=evaluation_dataset_id,
            strategy_type=strategy_type,
            trigger_type=trigger_type,
            corpus_fingerprint=corpus_fingerprint,
            retrieval_settings_json=retrieval_settings_json,
            metrics_config=metrics_config,
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
        manifest_schema_version: str = "phase2.evaluation_dataset.v1",
        content_fingerprint: str | None = None,
        corpus_fingerprint: str | None = None,
        corpus_mode: str = "shared_legacy",
        corpus_status: str = "shared_legacy",
    ) -> EvaluationDataset:
        dataset = EvaluationDataset(
            dataset_name=dataset_name,
            description=description,
            version=version,
            source_type=source_type,
            status=status,
            metadata_json=metadata_json,
            manifest_schema_version=manifest_schema_version,
            content_fingerprint=content_fingerprint,
            corpus_fingerprint=corpus_fingerprint,
            corpus_mode=corpus_mode,
            corpus_status=corpus_status,
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
            select(EvaluationDataset)
            .where(EvaluationDataset.dataset_name == dataset_name)
            .order_by(
                EvaluationDataset.created_at.desc(),
                EvaluationDataset.evaluation_dataset_id.desc(),
            )
        )

    def get_dataset_by_name_and_version(
        self,
        db: Session,
        *,
        dataset_name: str,
        version: str,
    ) -> EvaluationDataset | None:
        return db.scalar(
            select(EvaluationDataset).where(
                EvaluationDataset.dataset_name == dataset_name,
                EvaluationDataset.version == version,
            )
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
        description_provided: bool = False,
        metadata_json_provided: bool = False,
    ) -> None:
        if description_provided:
            dataset.description = description
        if version is not None:
            dataset.version = version
        if metadata_json_provided:
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

    def create_corpus_source(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        source_key: str,
        title: str,
        body_text: str,
        facts_json: list[dict[str, object]],
        content_hash: str,
    ) -> EvaluationCorpusSource:
        source = EvaluationCorpusSource(
            evaluation_dataset_id=evaluation_dataset_id,
            source_key=source_key,
            title=title,
            body_text=body_text,
            facts_json=facts_json,
            content_hash=content_hash,
            status="pending",
        )
        db.add(source)
        db.flush()
        return source

    def list_corpus_sources(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
    ) -> list[EvaluationCorpusSource]:
        return list(
            db.scalars(
                select(EvaluationCorpusSource)
                .where(EvaluationCorpusSource.evaluation_dataset_id == evaluation_dataset_id)
                .order_by(EvaluationCorpusSource.evaluation_corpus_source_id.asc())
            ).all()
        )

    def get_corpus_source_by_key(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int,
        source_key: str,
    ) -> EvaluationCorpusSource | None:
        return db.scalar(
            select(EvaluationCorpusSource).where(
                EvaluationCorpusSource.evaluation_dataset_id == evaluation_dataset_id,
                EvaluationCorpusSource.source_key == source_key,
            )
        )

    def count_chunks_for_version(
        self,
        db: Session,
        *,
        document_version_id: int | None,
        require_active: bool = True,
    ) -> int:
        if document_version_id is None:
            return 0
        query = (
            select(func.count())
            .select_from(DocumentChunk)
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .where(
                DocumentChunk.document_version_id == document_version_id,
                DocumentVersion.status == "ready",
            )
        )
        if require_active:
            query = query.where(DocumentVersion.is_active.is_(True))
        return int(db.scalar(query) or 0)

    def list_auxiliary_judgments(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> list[EvaluationAuxiliaryJudgment]:
        return list(
            db.scalars(
                select(EvaluationAuxiliaryJudgment)
                .join(
                    EvaluationRunItem,
                    EvaluationRunItem.evaluation_run_item_id
                    == EvaluationAuxiliaryJudgment.evaluation_run_item_id,
                )
                .where(EvaluationRunItem.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationAuxiliaryJudgment.evaluation_run_item_id.asc())
            ).all()
        )

    def get_auxiliary_judgment(
        self,
        db: Session,
        *,
        evaluation_run_item_id: int,
    ) -> EvaluationAuxiliaryJudgment | None:
        return db.scalar(
            select(EvaluationAuxiliaryJudgment).where(
                EvaluationAuxiliaryJudgment.evaluation_run_item_id == evaluation_run_item_id
            )
        )

    def upsert_auxiliary_judgment(
        self,
        db: Session,
        *,
        evaluation_run_item_id: int,
        values: dict[str, object],
        updated_at: datetime,
    ) -> EvaluationAuxiliaryJudgment:
        row = self.get_auxiliary_judgment(
            db,
            evaluation_run_item_id=evaluation_run_item_id,
        )
        if row is None:
            row = EvaluationAuxiliaryJudgment(
                evaluation_run_item_id=evaluation_run_item_id,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = updated_at
        db.flush()
        return row

    def list_review_payloads(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> dict[int, EvaluationReviewPayload]:
        rows = db.scalars(
            select(EvaluationReviewPayload)
            .join(
                EvaluationRunItem,
                EvaluationRunItem.evaluation_run_item_id
                == EvaluationReviewPayload.evaluation_run_item_id,
            )
            .where(EvaluationRunItem.evaluation_run_id == evaluation_run_id)
        ).all()
        return {row.evaluation_run_item_id: row for row in rows}

    def upsert_review_payload(
        self,
        db: Session,
        *,
        evaluation_run_item_id: int,
        values: dict[str, object],
        updated_at: datetime,
    ) -> EvaluationReviewPayload:
        row = db.scalar(
            select(EvaluationReviewPayload).where(
                EvaluationReviewPayload.evaluation_run_item_id == evaluation_run_item_id
            )
        )
        if row is None:
            row = EvaluationReviewPayload(
                evaluation_run_item_id=evaluation_run_item_id,
                **values,
            )
            db.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = updated_at
            row.purged_at = None
        db.flush()
        return row

    def purge_expired_review_payloads(
        self,
        db: Session,
        *,
        now: datetime,
    ) -> int:
        rows = list(
            db.scalars(
                select(EvaluationReviewPayload).where(
                    EvaluationReviewPayload.expires_at <= now,
                    EvaluationReviewPayload.purged_at.is_(None),
                )
            ).all()
        )
        for row in rows:
            row.answer_text = None
            row.context_json = null()
            row.citations_json = null()
            row.required_facts_json = null()
            row.purged_at = now
            row.updated_at = now
        if rows:
            db.flush()
        return len(rows)

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

    def get_item(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        evaluation_run_item_id: int,
        for_update: bool = False,
    ) -> EvaluationRunItem | None:
        statement = select(EvaluationRunItem).where(
            EvaluationRunItem.evaluation_run_id == evaluation_run_id,
            EvaluationRunItem.evaluation_run_item_id == evaluation_run_item_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def list_human_calibrations(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
    ) -> list[EvaluationHumanCalibration]:
        return list(
            db.scalars(
                select(EvaluationHumanCalibration)
                .join(
                    EvaluationRunItem,
                    EvaluationRunItem.evaluation_run_item_id
                    == EvaluationHumanCalibration.evaluation_run_item_id,
                )
                .where(EvaluationRunItem.evaluation_run_id == evaluation_run_id)
                .order_by(EvaluationHumanCalibration.evaluation_run_item_id.asc())
            ).all()
        )

    def upsert_human_calibration(
        self,
        db: Session,
        *,
        evaluation_run_item_id: int,
        case_id: str,
        rubric_version: str,
        required_facts_supported: str,
        citation_support: str,
        forbidden_claims_absent: str,
        abstention_correct: str,
        prompt_injection_resisted: str,
        human_required_facts_supported: str | None,
        human_citation_support: str | None,
        human_forbidden_claims_absent: str | None,
        human_abstention_correct: str | None,
        human_prompt_injection_resisted: str | None,
        auxiliary_confidence: Decimal,
        auxiliary_reason_codes: list[str],
        auxiliary_pass: bool,
        human_pass: bool,
        disagreement_category: str | None,
        human_reason_codes: list[str],
        reviewed_by: int,
        updated_at: datetime,
    ) -> EvaluationHumanCalibration:
        calibration = db.scalar(
            select(EvaluationHumanCalibration).where(
                EvaluationHumanCalibration.evaluation_run_item_id == evaluation_run_item_id
            )
        )
        if calibration is None:
            calibration = EvaluationHumanCalibration(
                evaluation_run_item_id=evaluation_run_item_id,
                case_id=case_id,
                rubric_version=rubric_version,
                required_facts_supported=required_facts_supported,
                citation_support=citation_support,
                forbidden_claims_absent=forbidden_claims_absent,
                abstention_correct=abstention_correct,
                prompt_injection_resisted=prompt_injection_resisted,
                human_required_facts_supported=human_required_facts_supported,
                human_citation_support=human_citation_support,
                human_forbidden_claims_absent=human_forbidden_claims_absent,
                human_abstention_correct=human_abstention_correct,
                human_prompt_injection_resisted=human_prompt_injection_resisted,
                auxiliary_confidence=auxiliary_confidence,
                auxiliary_reason_codes_json=auxiliary_reason_codes,
                auxiliary_pass=auxiliary_pass,
                human_pass=human_pass,
                disagreement_category=disagreement_category,
                human_reason_codes_json=human_reason_codes,
                reviewed_by=reviewed_by,
                updated_at=updated_at,
            )
            db.add(calibration)
        else:
            calibration.case_id = case_id
            calibration.rubric_version = rubric_version
            calibration.required_facts_supported = required_facts_supported
            calibration.citation_support = citation_support
            calibration.forbidden_claims_absent = forbidden_claims_absent
            calibration.abstention_correct = abstention_correct
            calibration.prompt_injection_resisted = prompt_injection_resisted
            calibration.human_required_facts_supported = human_required_facts_supported
            calibration.human_citation_support = human_citation_support
            calibration.human_forbidden_claims_absent = human_forbidden_claims_absent
            calibration.human_abstention_correct = human_abstention_correct
            calibration.human_prompt_injection_resisted = human_prompt_injection_resisted
            calibration.auxiliary_confidence = auxiliary_confidence
            calibration.auxiliary_reason_codes_json = auxiliary_reason_codes
            calibration.auxiliary_pass = auxiliary_pass
            calibration.human_pass = human_pass
            calibration.disagreement_category = disagreement_category
            calibration.human_reason_codes_json = human_reason_codes
            calibration.reviewed_by = reviewed_by
            calibration.updated_at = updated_at
        db.flush()
        return calibration

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
                delete(EvaluationHumanCalibration).where(
                    EvaluationHumanCalibration.evaluation_run_item_id.in_(item_ids)
                )
            )
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
        answer_outcome: str | None,
        retrieval_run_id: int | None,
        faithfulness_score: Decimal | None,
        groundedness_score: Decimal | None,
        citation_coverage: Decimal | None,
        latency_ms: int | None,
        generation_provider: str | None,
        generation_model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        estimated_cost_usd: Decimal | None,
        generation_latency_ms: int | None,
        latency_breakdown_json: dict[str, object] | None,
        metric_summary_json: dict[str, object] | None,
        error_code: str | None,
        error_detail_code: str | None,
        error_message: str | None,
    ) -> None:
        item.status = status
        item.answer_outcome = answer_outcome
        item.retrieval_run_id = retrieval_run_id
        item.faithfulness_score = faithfulness_score
        item.groundedness_score = groundedness_score
        item.citation_coverage = citation_coverage
        item.latency_ms = latency_ms
        item.generation_provider = generation_provider
        item.generation_model = generation_model
        item.input_tokens = input_tokens
        item.output_tokens = output_tokens
        item.total_tokens = total_tokens
        item.estimated_cost_usd = estimated_cost_usd
        item.generation_latency_ms = generation_latency_ms
        item.latency_breakdown_json = latency_breakdown_json
        item.metric_summary_json = metric_summary_json
        item.error_code = error_code
        item.error_detail_code = error_detail_code
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
