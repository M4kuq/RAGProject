from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.rag.strategy import (
    DEFAULT_RETRIEVAL_STRATEGY,
    RAG_ASK_REQUEST_STRATEGY_VALUES,
    RAG_SEARCH_REQUEST_STRATEGY_VALUES,
    RETRIEVAL_SOURCE_VALUES,
    RETRIEVAL_STRATEGY_VALUES,
    RetrievalSource,
    RetrievalStrategy,
)
from app.schemas.evaluations import EvaluationRunCreateRequest
from app.schemas.rag import RagAskRequest, RagSearchRequest
from app.schemas.rag_strategy import (
    LatencyBreakdown,
    QueryPlanTrace,
    RetrievalSettingsSnapshot,
    RouterDecisionTrace,
    ScoreBreakdown,
    StrategyDecisionTrace,
    StrategyEvaluationMetricSpec,
)


def test_retrieval_strategy_enum_values_are_phase2_baseline() -> None:
    assert DEFAULT_RETRIEVAL_STRATEGY is RetrievalStrategy.DENSE
    assert RETRIEVAL_STRATEGY_VALUES == (
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
        "langgraph_agentic",
        "fallback_dense",
    )
    assert RETRIEVAL_SOURCE_VALUES == (
        "dense",
        "sparse",
        "hybrid",
        "graph",
        "rerank",
        "fallback_dense",
        "metadata_filter",
    )


def test_request_facing_strategy_values_exclude_internal_fallback_dense() -> None:
    assert RAG_SEARCH_REQUEST_STRATEGY_VALUES == (
        "dense",
        "sparse",
        "hybrid",
        "graph",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
    )
    assert RAG_ASK_REQUEST_STRATEGY_VALUES == (
        "dense",
        "hybrid",
        "graph",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    )
    assert "fallback_dense" not in RAG_SEARCH_REQUEST_STRATEGY_VALUES
    assert "fallback_dense" not in RAG_ASK_REQUEST_STRATEGY_VALUES


def test_request_model_schemas_exclude_internal_fallback_dense() -> None:
    assert _field_enum_values(RagSearchRequest.model_json_schema(), "strategy") == (
        "dense",
        "sparse",
        "hybrid",
        "graph",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
    )
    assert _field_enum_values(RagAskRequest.model_json_schema(), "strategy") == (
        "dense",
        "hybrid",
        "graph",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    )
    assert _field_enum_values(EvaluationRunCreateRequest.model_json_schema(), "strategy_type") == (
        "dense",
        "sparse",
        "hybrid",
        "graph",
        "graph_postgres",
        "graph_neo4j",
        "agentic_router",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    )
    assert "fallback_dense" not in _field_enum_values(
        EvaluationRunCreateRequest.model_json_schema(), "strategy_type"
    )


def test_python_enum_values_match_migration_check_values() -> None:
    migration_values = _migration_constants()
    assert migration_values["RETRIEVAL_STRATEGY_VALUES"] == RETRIEVAL_STRATEGY_VALUES
    assert migration_values["RETRIEVAL_SOURCE_VALUES"] == RETRIEVAL_SOURCE_VALUES


def test_llm_orchestrator_strategy_migration_downgrade_rewrites_rows() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0008_llm_tool_orchestrator_strategy.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "_rewrite_orchestrator_strategy_rows()" in source
    assert "WHERE strategy_type = 'llm_tool_orchestrator'" in source
    assert "SET strategy_type = 'agentic_router'" in source


def test_langchain_agentic_strategy_migration_downgrade_rewrites_rows() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0013_langchain_agentic_strategy.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "_rewrite_langchain_strategy_rows()" in source
    assert "_rewrite_langchain_strategy_json()" in source
    assert "WHERE strategy_type = 'langchain_agentic'" in source
    assert "SET strategy_type = 'llm_tool_orchestrator'" in source
    assert "evaluation_runs" in source
    assert "metrics_config" in source
    assert "jobs" in source
    assert "payload_json" in source
    assert "job_type = 'evaluation_run'" in source
    assert "jsonb_array_elements_text" in source


def test_langgraph_agentic_strategy_migration_downgrade_rewrites_rows() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0015_langgraph_agentic_strategy.py"
    )
    source = migration.read_text(encoding="utf-8")

    assert "_rewrite_langgraph_strategy_rows()" in source
    assert "_rewrite_langgraph_strategy_json()" in source
    assert "WHERE strategy_type = 'langgraph_agentic'" in source
    assert "SET strategy_type = 'langchain_agentic'" in source
    assert "evaluation_runs" in source
    assert "metrics_config" in source
    assert "jobs" in source
    assert "payload_json" in source
    assert "job_type = 'evaluation_run'" in source
    assert "jsonb_array_elements_text" in source


def test_phase2_trace_dtos_are_json_serializable_and_redacted() -> None:
    payloads = [
        QueryPlanTrace(
            strategy_type=RetrievalStrategy.DENSE,
            query_hash="a" * 64,
            sub_query_count=0,
            metadata_filter_count=0,
            reason_codes=["phase1_default_dense"],
        ).model_dump(mode="json"),
        StrategyDecisionTrace(reason_codes=["router_disabled"]).model_dump(mode="json"),
        RouterDecisionTrace(
            requested_strategy=RetrievalStrategy.AGENTIC_ROUTER,
            selected_strategy=RetrievalStrategy.HYBRID,
            execution_strategy=RetrievalStrategy.HYBRID,
            confidence=0.72,
            reason_codes=["keyword_heavy"],
        ).model_dump(mode="json"),
        LatencyBreakdown(retrieval_ms=10, rerank_ms=5, total_ms=15).model_dump(mode="json"),
        RetrievalSettingsSnapshot(
            top_k=5,
            rerank_top_n=5,
            logical_document_filter_count=0,
        ).model_dump(mode="json"),
        StrategyEvaluationMetricSpec(
            metric_name="recall_at_k",
            display_name="Recall@k",
            description="Fraction of expected references retrieved.",
        ).model_dump(mode="json"),
    ]

    dumped = json.dumps(payloads)
    assert "raw_prompt" not in dumped
    assert "raw_chunk_text" not in dumped
    assert "content_text" not in dumped
    assert "secret" not in dumped


def test_score_breakdown_does_not_allow_raw_text_fields() -> None:
    breakdown = ScoreBreakdown(
        retrieval_source=RetrievalSource.DENSE,
        dense_score=0.91,
        rerank_score=0.88,
        rank_order=1,
        rerank_order=1,
        selected_flag=True,
    )
    dumped = breakdown.model_dump(mode="json", exclude_none=True)

    assert dumped == {
        "schema_version": "phase2.trace.v1",
        "retrieval_source": "dense",
        "dense_score": 0.91,
        "rerank_score": 0.88,
        "rank_order": 1,
        "rerank_order": 1,
        "selected_flag": True,
    }
    assert "raw_chunk_text" not in dumped
    with pytest.raises(ValueError):
        ScoreBreakdown(
            retrieval_source=RetrievalSource.DENSE,
            dense_score=0.91,
            rank_order=1,
            selected_flag=True,
            raw_chunk_text="do not persist this",
        )


def _migration_constants() -> dict[str, tuple[str, ...]]:
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0015_langgraph_agentic_strategy.py"
    )
    tree = ast.parse(migration.read_text(encoding="utf-8"))
    constants: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id == "NEW_RETRIEVAL_STRATEGY_VALUES":
            constants["RETRIEVAL_STRATEGY_VALUES"] = tuple(ast.literal_eval(node.value))
            continue
        if node.targets[0].id == "NEW_RETRIEVAL_SOURCE_VALUES":
            constants["RETRIEVAL_SOURCE_VALUES"] = tuple(ast.literal_eval(node.value))
            continue
    return constants


def _field_enum_values(schema: dict[str, object], field_name: str) -> tuple[str, ...]:
    properties = schema["properties"]
    assert isinstance(properties, dict)
    field_schema = properties[field_name]
    assert isinstance(field_schema, dict)
    ref = field_schema.get("$ref")
    if ref is None:
        ref = field_schema["items"]["$ref"]
    assert isinstance(ref, str)
    definition_name = ref.rsplit("/", maxsplit=1)[-1]
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    definition = definitions[definition_name]
    assert isinstance(definition, dict)
    enum_values = definition["enum"]
    assert isinstance(enum_values, list)
    return tuple(str(value) for value in enum_values)
