from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class RetrievalStrategy(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    MULTI_QUERY_DENSE = "multi_query_dense"
    MULTI_QUERY_HYBRID = "multi_query_hybrid"
    METADATA_FILTERED = "metadata_filtered"
    VERSION_AWARE = "version_aware"
    AGENTIC_ROUTER = "agentic_router"
    LLM_TOOL_ORCHESTRATOR = "llm_tool_orchestrator"
    FALLBACK_DENSE = "fallback_dense"


class RagSearchRequestStrategy(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    AGENTIC_ROUTER = "agentic_router"


class RagAskRequestStrategy(StrEnum):
    DENSE = "dense"
    HYBRID = "hybrid"
    AGENTIC_ROUTER = "agentic_router"
    LLM_TOOL_ORCHESTRATOR = "llm_tool_orchestrator"


class RetrievalSource(StrEnum):
    DENSE = "dense"
    SPARSE = "sparse"
    HYBRID = "hybrid"
    RERANK = "rerank"
    FALLBACK_DENSE = "fallback_dense"
    METADATA_FILTER = "metadata_filter"


class FusionMethod(StrEnum):
    RRF = "rrf"
    WEIGHTED = "weighted"


class RouterFallbackStrategy(StrEnum):
    DENSE = "dense"
    FALLBACK_DENSE = "fallback_dense"


class QueryIntent(StrEnum):
    FACTUAL_LOOKUP = "factual_lookup"
    PROCEDURAL = "procedural"
    COMPARISON = "comparison"
    SUMMARIZATION = "summarization"
    TROUBLESHOOTING = "troubleshooting"
    DEFINITION = "definition"
    VERSION_SPECIFIC = "version_specific"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StrategyTraceSettings:
    enabled: bool = True
    store_query_plan: bool = True
    store_latency_breakdown: bool = True


DEFAULT_RETRIEVAL_STRATEGY: Final = RetrievalStrategy.DENSE
DEFAULT_RAG_SEARCH_REQUEST_STRATEGY: Final = RagSearchRequestStrategy.DENSE
DEFAULT_RAG_ASK_REQUEST_STRATEGY: Final = RagAskRequestStrategy.DENSE
DEFAULT_FUSION_METHOD: Final = FusionMethod.RRF
DEFAULT_ROUTER_FALLBACK_STRATEGY: Final = RouterFallbackStrategy.FALLBACK_DENSE

RETRIEVAL_STRATEGY_VALUES: Final = tuple(strategy.value for strategy in RetrievalStrategy)
RAG_SEARCH_REQUEST_STRATEGY_VALUES: Final = tuple(
    strategy.value for strategy in RagSearchRequestStrategy
)
RAG_ASK_REQUEST_STRATEGY_VALUES: Final = tuple(strategy.value for strategy in RagAskRequestStrategy)
RETRIEVAL_SOURCE_VALUES: Final = tuple(source.value for source in RetrievalSource)

PHASE2_RETRIEVAL_SYSTEM_SETTINGS: Final[dict[str, tuple[object, str]]] = {
    "rag.default_strategy": (
        DEFAULT_RETRIEVAL_STRATEGY.value,
        "Default retrieval strategy. Phase1 behavior remains dense.",
    ),
    "rag.hybrid.enabled": (
        True,
        "Enable standalone dense+sparse hybrid retrieval for Phase2 PR-24.",
    ),
    "rag.hybrid.fusion_method": (
        DEFAULT_FUSION_METHOD.value,
        "Default hybrid fusion method.",
    ),
    "rag.hybrid.rrf_k": (
        60,
        "RRF rank constant for hybrid retrieval.",
    ),
    "rag.hybrid.dense_weight": (
        0.5,
        "Dense score weight for hybrid weighted fusion.",
    ),
    "rag.hybrid.sparse_weight": (
        0.5,
        "Sparse score weight for hybrid weighted fusion.",
    ),
    "rag.hybrid.candidate_multiplier": (
        2,
        "Candidate overfetch multiplier for hybrid retrieval final check.",
    ),
    "rag.router.enabled": (
        True,
        "Enable explicit StrategyRouter execution for Phase2 PR-28.",
    ),
    "rag.router.mode": (
        "rule_based",
        "StrategyRouter mode. PR-28 supports deterministic rule_based routing only.",
    ),
    "rag.router.allow_agentic_search": (
        True,
        "Allow explicit /rag/search strategy=agentic_router requests.",
    ),
    "rag.router.allow_agentic_ask": (
        True,
        "Allow explicit /rag/ask strategy=agentic_router requests while keeping default ask dense.",
    ),
    "rag.router.keyword_heavy_threshold": (
        0.65,
        "Keyword-heavy threshold for rule-based StrategyRouter hybrid selection.",
    ),
    "rag.router.ambiguity_threshold": (
        0.75,
        "Ambiguity threshold for rule-based StrategyRouter fallback handling.",
    ),
    "rag.router.max_retrieval_calls": (
        2,
        "Maximum bounded retrieval calls for PR-29 AgenticRetrievalExecutor.",
    ),
    "rag.router.max_fallback_calls": (
        1,
        "Maximum fallback retrieval calls within the PR-29 bounded loop.",
    ),
    "rag.router.sufficiency_min_candidates": (
        1,
        "Minimum post-final-check candidates required by ContextSufficiencyChecker.",
    ),
    "rag.router.sufficiency_min_selected": (
        1,
        "Minimum selected candidates required by ContextSufficiencyChecker.",
    ),
    "rag.router.sufficiency_top_score_threshold": (
        0.2,
        "Minimum top retrieval score for deterministic context sufficiency.",
    ),
    "rag.router.enable_fallback_hybrid": (
        True,
        "Allow hybrid fallback retrieval inside the bounded agentic loop.",
    ),
    "rag.router.enable_fallback_dense": (
        True,
        "Allow dense/fallback_dense retrieval inside the bounded agentic loop.",
    ),
    "rag.router.no_context_after_budget_exhausted": (
        True,
        "Treat insufficient context after budget exhaustion as no_context for ask.",
    ),
    "rag.router.fallback_strategy": (
        DEFAULT_ROUTER_FALLBACK_STRATEGY.value,
        "Fallback strategy for router failures.",
    ),
    "rag.router.store_decision_trace": (
        True,
        "Store redacted StrategyRouter decision trace.",
    ),
    "rag.llm_orchestrator.enabled": (
        True,
        "Enable explicit /rag/ask strategy=llm_tool_orchestrator requests.",
    ),
    "rag.llm_orchestrator.max_tool_calls": (
        5,
        "Maximum bounded retrieval-only tool calls for the LLM orchestrator.",
    ),
    "rag.llm_orchestrator.max_search_calls": (
        3,
        "Maximum dense/sparse/hybrid search tool calls for the LLM orchestrator.",
    ),
    "rag.llm_orchestrator.timeout_seconds": (
        30,
        "Wall-clock timeout for the LLM tool-calling retrieval loop.",
    ),
    "rag.llm_orchestrator.allow_admin_tools": (
        False,
        "Keep admin/write tools unavailable to the LLM retrieval orchestrator.",
    ),
    "rag.context_budget.enabled": (
        True,
        "Enable safe context budget selection before RAG answer generation.",
    ),
    "rag.context_budget.max_context_tokens": (
        6000,
        "Maximum estimated context tokens passed to answer generation.",
    ),
    "rag.context_budget.reserve_answer_tokens": (
        1000,
        "Reserved answer-token estimate kept out of context selection.",
    ),
    "rag.context_budget.max_context_items": (
        12,
        "Maximum context items passed to answer generation.",
    ),
    "rag.context_budget.max_tokens_per_item": (
        1200,
        "Maximum estimated tokens allowed for one context item; "
        "PR-40 drops rather than compresses.",
    ),
    "rag.context_budget.min_citation_candidates": (
        1,
        "Minimum citation candidate target for context selection when budget allows.",
    ),
    "rag.context_budget.drop_low_score_first": (
        True,
        "Preserve higher-ranked or higher-scored context items when budget is tight.",
    ),
    "rag.context_budget.preserve_source_diversity": (
        True,
        "Prefer one context item per source before adding additional items from the same source.",
    ),
    "rag.context_budget.token_estimator": (
        "heuristic",
        "Deterministic PR-40 token estimate method; heuristic is ceil(chars / 4).",
    ),
    "rag.context_budget.store_debug_trace": (
        True,
        "Persist safe context budget summaries in retrieval_runs.context_budget_json.",
    ),
    "rag.trace.enabled": (
        True,
        "Store redacted retrieval trace metadata.",
    ),
    "rag.trace.store_query_plan": (
        True,
        "Allow storing redacted query-plan trace metadata.",
    ),
    "rag.trace.store_latency_breakdown": (
        True,
        "Allow storing redacted latency breakdown metadata.",
    ),
    "rag.trace.external_export_enabled": (
        False,
        "Enable optional redacted trace export to an external observability provider.",
    ),
    "rag.trace.external_export_provider": (
        "none",
        "External trace export provider. PR-32 supports none and optional LangSmith.",
    ),
    "rag.trace.external_export_include_retrieval": (
        True,
        "Allow exporting minimized retrieval trace summaries when external export is enabled.",
    ),
    "rag.trace.external_export_include_evaluation": (
        True,
        "Allow exporting minimized evaluation summaries when external export is enabled.",
    ),
    "rag.trace.external_export_include_previews": (
        False,
        "Keep query/text previews out of external trace exports by default.",
    ),
    "rag.sparse.enabled": (
        True,
        "Enable standalone sparse lexical retrieval for Phase2 PR-23.",
    ),
    "rag.sparse.provider": (
        "postgres_fts",
        "Sparse retrieval provider. PR-23 uses PostgreSQL full-text search.",
    ),
    "rag.sparse.language": (
        "simple",
        "PostgreSQL text search configuration for sparse retrieval.",
    ),
    "rag.sparse.min_query_terms": (
        1,
        "Minimum normalized lexical terms required for sparse retrieval.",
    ),
    "rag.sparse.max_query_terms": (
        32,
        "Maximum normalized lexical terms retained for sparse retrieval.",
    ),
    "rag.sparse.score_normalization": (
        "max",
        "Sparse score normalization method.",
    ),
    "rag.query_analyzer.enabled": (
        True,
        "Enable deterministic rule-based query analysis for Phase2 PR-27.",
    ),
    "rag.query_planner.enabled": (
        True,
        "Enable deterministic rule-based query planning for Phase2 PR-27.",
    ),
    "rag.query_planner.apply_rewrite_to_retrieval": (
        False,
        "Keep retrieval behavior unchanged unless query rewrite application is explicitly enabled.",
    ),
    "rag.query_planner.max_sub_queries": (
        3,
        "Maximum planned sub-query previews stored in safe query plan trace.",
    ),
    "rag.query_planner.max_preview_chars": (
        160,
        "Maximum safe query preview characters stored in query plan trace.",
    ),
    "rag.query_planner.store_query_preview": (
        True,
        "Store bounded and redacted query previews for admin debug only.",
    ),
    "rag.query_planner.redact_pii": (
        True,
        "Enable PII-redacted derived query previews; false disables preview persistence.",
    ),
}


def sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
