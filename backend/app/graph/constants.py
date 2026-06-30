from __future__ import annotations

from typing import Final, Literal

GRAPH_INDEX_BUILD_JOB_TYPE: Final[Literal["graph_index_build"]] = "graph_index_build"

GRAPH_INDEX_RUN_STATUSES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
    }
)

DEFAULT_GRAPH_EXTRACTOR_TYPE = "llm"
LLM_GRAPH_EXTRACTOR_TYPE = "llm"
LLM_GRAPH_EXTRACTOR_VERSION = "c2b-llm-v1"
GRAPH_EXTRACTION_RULE_BASED_COMPLETED = "graph_extraction_rule_based_completed"
GRAPH_EXTRACTION_LLM_COMPLETED = "graph_extraction_llm_completed"
GRAPH_EXTRACTION_LLM_PARTIAL_COMPLETED = "graph_extraction_llm_partial_completed"
GRAPH_EXTRACTION_LLM_UNAVAILABLE = "graph_extraction_llm_unavailable"
GRAPH_EXTRACTION_LLM_FAILED = "graph_extraction_llm_failed"
GRAPH_EXTRACTION_LLM_INVALID_RESPONSE = "graph_extraction_llm_invalid_response"
GRAPH_EXTRACTION_LLM_EMPTY_RESPONSE = "graph_extraction_llm_empty_response"
GRAPH_EXTRACTION_LLM_FALLBACK = "graph_extraction_llm_fallback"

PHASE3_GRAPH_SYSTEM_SETTINGS: dict[str, tuple[object, str]] = {
    "rag.graph.enabled": (False, "Enable Graph-RAG retrieval. PR-46 default is disabled."),
    "rag.graph.indexing.enabled": (
        False,
        "Enable graph index build jobs. PR-46 default is disabled.",
    ),
    "rag.graph.extractor.default": (
        DEFAULT_GRAPH_EXTRACTOR_TYPE,
        "Default graph extractor. C2b uses LLM extraction with rule_based fallback.",
    ),
    "rag.graph.extraction.provider": (
        None,
        "Optional graph extraction provider override; null reuses generation_provider.",
    ),
    "rag.graph.extraction.model_name": (
        None,
        "Optional graph extraction model override; null reuses generation_model_name.",
    ),
    "rag.graph.extraction.timeout_seconds": (
        60,
        "Timeout for one graph LLM extraction provider call.",
    ),
    "rag.graph.extraction.max_output_chars": (
        12000,
        "Maximum graph extraction LLM output characters per chunk.",
    ),
    "rag.graph.extraction.max_output_tokens": (
        2048,
        "Maximum graph extraction LLM output tokens per provider call.",
    ),
    "rag.graph.extraction.min_confidence": (
        0.5,
        "Minimum confidence for LLM graph extraction candidates.",
    ),
    "rag.graph.max_entities_per_chunk": (20, "Maximum entity candidates per chunk."),
    "rag.graph.max_relations_per_chunk": (40, "Maximum relation candidates per chunk."),
    "rag.graph.store_raw_evidence_text": (False, "Raw graph evidence text must not be stored."),
    "rag.graph.store.provider": (
        "neo4j",
        "GraphStore provider. Neo4j is the default read model; PostgreSQL remains source of truth.",
    ),
    "rag.graph.retrieval.enabled": (
        True,
        "Enable graph retrieval strategies by default.",
    ),
}

UNSAFE_GRAPH_METADATA_KEY_PARTS = frozenset(
    {
        "raw_document_text",
        "raw_chunk_text",
        "raw_prompt",
        "full_context",
        "chunk_text",
        "document_text",
        "evidence_text",
        "mention_text",
        "secret",
        "token",
        "credential",
        "password",
        "pii",
    }
)
