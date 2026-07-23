from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import big_int, jsonb, pg_check
from app.rag.strategy import (
    DEFAULT_RETRIEVAL_STRATEGY,
    RETRIEVAL_STRATEGY_VALUES,
    sql_literal_list,
)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_run_item_id"],
            ["evaluation_run_items.evaluation_run_item_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "evaluation_run_item_id",
            "metric_name",
            name="uq_evaluation_results_item_metric",
        ),
        pg_check("btrim(metric_name) <> ''", "ck_evaluation_results_metric_name"),
        CheckConstraint(
            "metric_score IS NULL OR (metric_score >= 0 AND metric_score <= 1)",
            name="ck_evaluation_results_score",
        ),
        CheckConstraint(
            f"strategy_type IN ({sql_literal_list(RETRIEVAL_STRATEGY_VALUES)})",
            name="ck_evaluation_results_strategy_type",
        ),
    )

    evaluation_result_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_run_item_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    metric_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    metric_label: Mapped[str | None] = mapped_column(String(100))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    metric_detail_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    strategy_type: Mapped[str] = mapped_column(
        String(50),
        server_default=text(f"'{DEFAULT_RETRIEVAL_STRATEGY.value}'"),
        default=DEFAULT_RETRIEVAL_STRATEGY.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("ix_evaluation_results_item", EvaluationResult.evaluation_run_item_id)
Index(
    "ix_evaluation_results_metric_score",
    EvaluationResult.metric_name,
    EvaluationResult.metric_score,
)


class EvaluationHumanCalibration(Base):
    __tablename__ = "evaluation_human_calibrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_run_item_id"],
            ["evaluation_run_items.evaluation_run_item_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["reviewed_by"], ["users.user_id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "evaluation_run_item_id",
            name="uq_eval_human_calibrations_run_item",
        ),
        pg_check("btrim(case_id) <> ''", "ck_eval_human_calibrations_case_id"),
        CheckConstraint(
            "rubric_version = 'phase3.grounded_answer_judge.v1'",
            name="ck_eval_human_calibrations_rubric",
        ),
        CheckConstraint(
            "required_facts_supported IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND citation_support IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND forbidden_claims_absent IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND abstention_correct IN ('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND prompt_injection_resisted IN "
            "('pass', 'fail', 'uncertain', 'not_applicable')",
            name="ck_eval_human_calibrations_outcomes",
        ),
        CheckConstraint(
            "auxiliary_confidence >= 0 AND auxiliary_confidence <= 1",
            name="ck_eval_human_calibrations_confidence",
        ),
        CheckConstraint(
            "disagreement_category IS NULL OR disagreement_category IN "
            "('auxiliary_false_positive', 'auxiliary_false_negative', "
            "'rubric_ambiguity', 'gold_case_defect')",
            name="ck_eval_human_calibrations_disagreement",
        ),
        CheckConstraint(
            "(auxiliary_pass = human_pass AND disagreement_category IS NULL) OR "
            "(auxiliary_pass <> human_pass AND disagreement_category IS NOT NULL)",
            name="ck_eval_human_calibrations_verdict_consistency",
        ),
        CheckConstraint(
            "(human_required_facts_supported IS NULL "
            "AND human_citation_support IS NULL "
            "AND human_forbidden_claims_absent IS NULL "
            "AND human_abstention_correct IS NULL "
            "AND human_prompt_injection_resisted IS NULL) OR "
            "(human_required_facts_supported IN "
            "('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND human_citation_support IN "
            "('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND human_forbidden_claims_absent IN "
            "('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND human_abstention_correct IN "
            "('pass', 'fail', 'uncertain', 'not_applicable') "
            "AND human_prompt_injection_resisted IN "
            "('pass', 'fail', 'uncertain', 'not_applicable'))",
            name="ck_eval_human_calibrations_human_outcomes",
        ),
    )

    evaluation_human_calibration_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_run_item_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    case_id: Mapped[str] = mapped_column(String(120), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(64), nullable=False)
    required_facts_supported: Mapped[str] = mapped_column(String(20), nullable=False)
    citation_support: Mapped[str] = mapped_column(String(20), nullable=False)
    forbidden_claims_absent: Mapped[str] = mapped_column(String(20), nullable=False)
    abstention_correct: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_injection_resisted: Mapped[str] = mapped_column(String(20), nullable=False)
    human_required_facts_supported: Mapped[str | None] = mapped_column(String(20))
    human_citation_support: Mapped[str | None] = mapped_column(String(20))
    human_forbidden_claims_absent: Mapped[str | None] = mapped_column(String(20))
    human_abstention_correct: Mapped[str | None] = mapped_column(String(20))
    human_prompt_injection_resisted: Mapped[str | None] = mapped_column(String(20))
    auxiliary_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    auxiliary_reason_codes_json: Mapped[list[str]] = mapped_column(jsonb(), nullable=False)
    auxiliary_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    human_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    disagreement_category: Mapped[str | None] = mapped_column(String(40))
    human_reason_codes_json: Mapped[list[str]] = mapped_column(jsonb(), nullable=False)
    reviewed_by: Mapped[int] = mapped_column(big_int(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index(
    "ix_eval_human_calibrations_reviewer",
    EvaluationHumanCalibration.reviewed_by,
)


class EvaluationCorpusSource(Base):
    __tablename__ = "evaluation_corpus_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_dataset_id"],
            ["evaluation_datasets.evaluation_dataset_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["logical_document_id"],
            ["logical_documents.logical_document_id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.document_version_id"],
            ondelete="SET NULL",
        ),
        ForeignKeyConstraint(["ingest_job_id"], ["jobs.job_id"], ondelete="SET NULL"),
        UniqueConstraint(
            "evaluation_dataset_id",
            "source_key",
            name="uq_evaluation_corpus_sources_dataset_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'preparing', 'ready', 'failed')",
            name="ck_evaluation_corpus_sources_status",
        ),
        pg_check("btrim(source_key) <> ''", "ck_evaluation_corpus_sources_key"),
        pg_check(
            "content_hash ~ '^[0-9a-f]{64}$'",
            "ck_evaluation_corpus_sources_hash",
        ),
    )

    evaluation_corpus_source_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_dataset_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    source_key: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    facts_json: Mapped[list[dict[str, Any]]] = mapped_column(jsonb(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    logical_document_id: Mapped[int | None] = mapped_column(big_int())
    document_version_id: Mapped[int | None] = mapped_column(big_int())
    ingest_job_id: Mapped[int | None] = mapped_column(big_int())
    status: Mapped[str] = mapped_column(
        String(30), server_default=text("'pending'"), default="pending", nullable=False
    )
    failure_code: Mapped[str | None] = mapped_column(String(100))
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index(
    "ix_evaluation_corpus_sources_dataset_status",
    EvaluationCorpusSource.evaluation_dataset_id,
    EvaluationCorpusSource.status,
)


class EvaluationAuxiliaryJudgment(Base):
    __tablename__ = "evaluation_auxiliary_judgments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_run_item_id"],
            ["evaluation_run_items.evaluation_run_item_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "evaluation_run_item_id",
            name="uq_eval_auxiliary_judgments_run_item",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_eval_auxiliary_judgments_status",
        ),
        CheckConstraint(
            "status = 'failed' OR "
            "(required_facts_supported IS NOT NULL "
            "AND citation_support IS NOT NULL "
            "AND forbidden_claims_absent IS NOT NULL "
            "AND abstention_correct IS NOT NULL "
            "AND prompt_injection_resisted IS NOT NULL "
            "AND confidence IS NOT NULL AND auxiliary_pass IS NOT NULL)",
            name="ck_eval_auxiliary_judgments_succeeded_shape",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_eval_auxiliary_judgments_confidence",
        ),
        CheckConstraint(
            "claim_faithfulness IS NULL OR (claim_faithfulness >= 0 AND claim_faithfulness <= 1)",
            name="ck_eval_auxiliary_judgments_claim_faithfulness",
        ),
    )

    evaluation_auxiliary_judgment_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_run_item_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(64), nullable=False)
    judge_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    judge_model: Mapped[str] = mapped_column(String(128), nullable=False)
    required_facts_supported: Mapped[str | None] = mapped_column(String(20))
    citation_support: Mapped[str | None] = mapped_column(String(20))
    forbidden_claims_absent: Mapped[str | None] = mapped_column(String(20))
    abstention_correct: Mapped[str | None] = mapped_column(String(20))
    prompt_injection_resisted: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    reason_codes_json: Mapped[list[str]] = mapped_column(jsonb(), nullable=False)
    auxiliary_pass: Mapped[bool | None] = mapped_column(Boolean)
    claim_faithfulness: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    answer_hash: Mapped[str | None] = mapped_column(String(64))
    context_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvaluationReviewPayload(Base):
    __tablename__ = "evaluation_review_payloads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["evaluation_run_item_id"],
            ["evaluation_run_items.evaluation_run_item_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "evaluation_run_item_id",
            name="uq_evaluation_review_payloads_run_item",
        ),
        CheckConstraint(
            "(purged_at IS NULL) OR "
            "(answer_text IS NULL AND context_json IS NULL "
            "AND citations_json IS NULL AND required_facts_json IS NULL)",
            name="ck_evaluation_review_payloads_purged",
        ),
    )

    evaluation_review_payload_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_run_item_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    answer_text: Mapped[str | None] = mapped_column(Text)
    context_json: Mapped[list[str] | None] = mapped_column(jsonb())
    citations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb())
    required_facts_json: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb())
    answer_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("ix_evaluation_review_payloads_expires_at", EvaluationReviewPayload.expires_at)
