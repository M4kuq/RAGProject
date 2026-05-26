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
    FALLBACK_DENSE = "fallback_dense"


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


@dataclass(frozen=True)
class StrategyTraceSettings:
    enabled: bool = True
    store_query_plan: bool = True
    store_latency_breakdown: bool = True


DEFAULT_RETRIEVAL_STRATEGY: Final = RetrievalStrategy.DENSE
DEFAULT_FUSION_METHOD: Final = FusionMethod.RRF
DEFAULT_ROUTER_FALLBACK_STRATEGY: Final = RouterFallbackStrategy.DENSE

RETRIEVAL_STRATEGY_VALUES: Final = tuple(strategy.value for strategy in RetrievalStrategy)
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
        False,
        "Strategy router is disabled until the Phase2 router PR.",
    ),
    "rag.router.max_retrieval_calls": (
        1,
        "Maximum retrieval calls while router support is disabled.",
    ),
    "rag.router.fallback_strategy": (
        DEFAULT_ROUTER_FALLBACK_STRATEGY.value,
        "Fallback strategy for future router failures.",
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
}


def sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
