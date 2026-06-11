"""allow graph retrieval strategy

Revision ID: 0014_graph_retrieval_strategy
Revises: 0013_langchain_agentic
Create Date: 2026-06-11
"""

from __future__ import annotations

from alembic import op

revision = "0014_graph_retrieval_strategy"
down_revision = "0013_langchain_agentic"
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
    "langchain_agentic",
    "fallback_dense",
)
NEW_RETRIEVAL_STRATEGY_VALUES = (
    "dense",
    "sparse",
    "hybrid",
    "graph",
    "multi_query_dense",
    "multi_query_hybrid",
    "metadata_filtered",
    "version_aware",
    "agentic_router",
    "llm_tool_orchestrator",
    "langchain_agentic",
    "fallback_dense",
)
OLD_RETRIEVAL_SOURCE_VALUES = (
    "dense",
    "sparse",
    "hybrid",
    "rerank",
    "fallback_dense",
    "metadata_filter",
)
NEW_RETRIEVAL_SOURCE_VALUES = (
    "dense",
    "sparse",
    "hybrid",
    "graph",
    "rerank",
    "fallback_dense",
    "metadata_filter",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
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
    _replace_source_constraint(NEW_RETRIEVAL_SOURCE_VALUES)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    _rewrite_graph_rows()
    _replace_source_constraint(OLD_RETRIEVAL_SOURCE_VALUES)
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


def _rewrite_graph_rows() -> None:
    for table_name in (
        "retrieval_runs",
        "evaluation_runs",
        "evaluation_run_items",
        "evaluation_results",
    ):
        op.execute(
            f"UPDATE {table_name} SET strategy_type = 'hybrid' WHERE strategy_type = 'graph'",
        )
    op.execute(
        "UPDATE retrieval_run_items "
        "SET retrieval_source = 'hybrid' "
        "WHERE retrieval_source = 'graph'",
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


def _replace_source_constraint(source_values: tuple[str, ...]) -> None:
    op.drop_constraint(
        "ck_retrieval_run_items_source",
        "retrieval_run_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_retrieval_run_items_source",
        "retrieval_run_items",
        f"retrieval_source IS NULL OR retrieval_source IN ({_sql_literal_list(source_values)})",
    )


def _sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
