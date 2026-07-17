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
