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
        False,
        "Hybrid retrieval is disabled until the Phase2 hybrid retrieval PR.",
    ),
    "rag.hybrid.fusion_method": (
        DEFAULT_FUSION_METHOD.value,
        "Default future hybrid fusion method.",
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
}


def sql_literal_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)
