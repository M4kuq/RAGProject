from __future__ import annotations

import hashlib
import json

from app.core.config import Settings
from app.rag.query_planner import (
    QueryAnalyzer,
    QueryPlanBuilder,
    QueryPlanner,
    normalize_query,
    rewrite_query,
)
from app.rag.retrieval import RetrievalFilters
from app.rag.strategy import QueryIntent, RetrievalStrategy


def test_query_analyzer_classifies_intent_deterministically() -> None:
    analyzer = QueryAnalyzer()

    assert (
        analyzer.analyze("v2 and v1 changes", filters=RetrievalFilters()).intent
        == QueryIntent.VERSION_SPECIFIC
    )
    assert (
        analyzer.analyze("Compare dense vs sparse retrieval", filters=RetrievalFilters()).intent
        == QueryIntent.COMPARISON
    )
    assert (
        analyzer.analyze("How to configure rag search", filters=RetrievalFilters()).intent
        == QueryIntent.PROCEDURAL
    )
    assert (
        analyzer.analyze("Summarize Phase2 evaluation", filters=RetrievalFilters()).intent
        == QueryIntent.SUMMARIZATION
    )
    assert (
        analyzer.analyze("HTTP 500 error in worker", filters=RetrievalFilters()).intent
        == QueryIntent.TROUBLESHOOTING
    )
    assert (
        analyzer.analyze("What is rerank", filters=RetrievalFilters()).intent
        == QueryIntent.DEFINITION
    )


def test_query_analyzer_detects_ambiguity_keyword_heavy_and_version_specific() -> None:
    analyzer = QueryAnalyzer()
    analysis = analyzer.analyze(
        "これ v2 API_ERROR /api/v1/rag/search .md",
        filters=RetrievalFilters(logical_document_ids=(1,), modality="text"),
    )

    assert (
        analysis.query_hash
        == hashlib.sha256("これ v2 API_ERROR /api/v1/rag/search .md".encode()).hexdigest()
    )
    assert analysis.version_specific_flag is True
    assert analysis.normalized_query_preview is None
    assert "deictic_reference" in analysis.ambiguity_flags
    assert analysis.ambiguity_score > 0
    assert analysis.keyword_heavy_score >= 0.5
    assert "api_endpoint" in analysis.keyword_signals
    assert "file_extension" in analysis.keyword_signals
    assert analysis.metadata_filter_hints
    assert RetrievalStrategy.VERSION_AWARE in analysis.recommended_candidate_strategies
    assert RetrievalStrategy.HYBRID in analysis.recommended_candidate_strategies


def test_query_analyzer_does_not_treat_plain_lowercase_words_as_error_codes() -> None:
    analyzer = QueryAnalyzer()
    analysis = analyzer.analyze("alpha target retrieval", filters=RetrievalFilters())

    assert "error_or_code_token" not in analysis.keyword_signals
    assert "deictic_reference" not in analysis.ambiguity_flags


def test_query_planner_rewrites_and_generates_safe_sub_queries() -> None:
    analyzer = QueryAnalyzer()
    planner = QueryPlanner(max_sub_queries=3)
    raw_query = "  Compare dense   vs sparse retrieval  "
    analysis = analyzer.analyze(raw_query, filters=RetrievalFilters())

    plan = planner.plan(
        raw_query,
        analysis=analysis,
        requested_strategy=RetrievalStrategy.DENSE,
    )

    assert normalize_query(raw_query) == "Compare dense vs sparse retrieval"
    assert rewrite_query(raw_query) == "Compare dense vs sparse retrieval"
    assert plan.rewrite_applied is True
    assert (
        plan.rewritten_query_hash
        == hashlib.sha256(b"Compare dense vs sparse retrieval").hexdigest()
    )
    assert len(plan.sub_queries) == 2
    assert [sub_query.reason_code for sub_query in plan.sub_queries] == [
        "comparison_component",
        "comparison_component",
    ]
    assert plan.recommended_strategy in plan.candidate_strategies
    assert "router_not_executed" in plan.safety_flags
    assert "rewrite_not_applied_to_retrieval" in plan.safety_flags


def test_query_plan_builder_redacts_pii_and_does_not_apply_rewrite_by_default() -> None:
    settings = Settings(
        app_env="test",
        query_planner_apply_rewrite_to_retrieval=False,
        query_planner_max_preview_chars=40,
    )
    builder = QueryPlanBuilder(settings)
    raw_query = "  alpha OPENAI_API_KEY=sk-secret person@example.com +1 555 111 2222  "

    built = builder.build(
        raw_query,
        filters=RetrievalFilters(),
        requested_strategy=RetrievalStrategy.DENSE,
    )

    assert built.retrieval_query == raw_query
    dumped = json.dumps(built.trace_metadata, ensure_ascii=False)
    assert "sk-secret" not in dumped
    assert "person@example.com" not in dumped
    assert "555 111 2222" not in dumped
    assert "redacted" in dumped
    assert built.analysis is not None
    assert built.analysis.query_hash == hashlib.sha256(raw_query.encode("utf-8")).hexdigest()


def test_query_plan_builder_disabled_fallback_is_safe() -> None:
    settings = Settings(
        app_env="test",
        query_analyzer_enabled=False,
        query_planner_enabled=False,
    )
    builder = QueryPlanBuilder(settings)

    built = builder.build(
        "alpha policy",
        filters=RetrievalFilters(),
        requested_strategy=RetrievalStrategy.HYBRID,
    )

    assert built.analysis is None
    assert built.planner is None
    assert built.retrieval_query == "alpha policy"
    assert built.trace_metadata == {
        "analysis_enabled": False,
        "planner_enabled": False,
        "disabled_reason": "query_analyzer_disabled",
    }
