from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models import big_int, jsonb, pg_check


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
    )

    evaluation_result_id: Mapped[int] = mapped_column(big_int(), primary_key=True)
    evaluation_run_item_id: Mapped[int] = mapped_column(big_int(), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    metric_score: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    metric_label: Mapped[str | None] = mapped_column(String(100))
    details_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


Index("ix_evaluation_results_item", EvaluationResult.evaluation_run_item_id)
Index(
    "ix_evaluation_results_metric_score",
    EvaluationResult.metric_name,
    EvaluationResult.metric_score,
)
