"""allow langchain agentic strategy

Revision ID: 0013_langchain_agentic
Revises: 0012_graph_schema_index
Create Date: 2026-06-06
"""

from __future__ import annotations

from alembic import op

revision = "0013_langchain_agentic"
down_revision = "0012_graph_schema_index"
branch_labels = None
depends_on = None


OLD_RETRIEVAL_STRATEGY_VALUES = (
    "dense",
    "sparse",
    "hybrid",
    "multi_query_dense",
    "multi_query_hybrid",
    "metadata_filtered",
    "version_aware",
    "agentic_router",
    "llm_tool_orchestrator",
    "fallback_dense",
)
NEW_RETRIEVAL_STRATEGY_VALUES = (
    *OLD_RETRIEVAL_STRATEGY_VALUES[:-1],
    "langchain_agentic",
    OLD_RETRIEVAL_STRATEGY_VALUES[-1],
)


def upgrade() -> None:
    _replace_strategy_constraint(
        "retrieval_runs",
        "ck_retrieval_runs_strategy_type",
        NEW_RETRIEVAL_STRATEGY_VALUES,
    )
    _replace_strategy_constraint(
        "evaluation_runs",
        "ck_evaluation_runs_strategy_type",
        NEW_RETRIEVAL_STRATEGY_VALUES,
    )
    _replace_strategy_constraint(
        "evaluation_run_items",
        "ck_evaluation_run_items_strategy_type",
        NEW_RETRIEVAL_STRATEGY_VALUES,
    )
    _replace_strategy_constraint(
        "evaluation_results",
        "ck_evaluation_results_strategy_type",
        NEW_RETRIEVAL_STRATEGY_VALUES,
    )


def downgrade() -> None:
    _rewrite_langchain_strategy_rows()
    _replace_strategy_constraint(
        "retrieval_runs",
        "ck_retrieval_runs_strategy_type",
        OLD_RETRIEVAL_STRATEGY_VALUES,
    )
    _replace_strategy_constraint(
        "evaluation_runs",
        "ck_evaluation_runs_strategy_type",
        OLD_RETRIEVAL_STRATEGY_VALUES,
    )
    _replace_strategy_constraint(
        "evaluation_run_items",
        "ck_evaluation_run_items_strategy_type",
        OLD_RETRIEVAL_STRATEGY_VALUES,
    )
    _replace_strategy_constraint(
        "evaluation_results",
        "ck_evaluation_results_strategy_type",
        OLD_RETRIEVAL_STRATEGY_VALUES,
    )


def _rewrite_langchain_strategy_rows() -> None:
    for table_name in (
        "retrieval_runs",
        "evaluation_runs",
        "evaluation_run_items",
        "evaluation_results",
    ):
        op.execute(
            f"UPDATE {table_name} "
            "SET strategy_type = 'llm_tool_orchestrator' "
            "WHERE strategy_type = 'langchain_agentic'"
        )


def _replace_strategy_constraint(
    table_name: str,
    constraint_name: str,
    strategy_values: tuple[str, ...],
) -> None:
    op.drop_constraint(constraint_name, table_name, type_="check")
    op.create_check_constraint(
        constraint_name,
        table_name,
        f"strategy_type IN ({_sql_literal_list(strategy_values)})",
    )


def _sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
