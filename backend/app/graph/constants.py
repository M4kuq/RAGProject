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

DEFAULT_GRAPH_EXTRACTOR_TYPE = "none"

PHASE3_GRAPH_SYSTEM_SETTINGS: dict[str, tuple[object, str]] = {
    "rag.graph.enabled": (False, "Enable Graph-RAG retrieval. PR-46 default is disabled."),
    "rag.graph.indexing.enabled": (
        False,
        "Enable graph index build jobs. PR-46 default is disabled.",
    ),
    "rag.graph.extractor.default": (
        DEFAULT_GRAPH_EXTRACTOR_TYPE,
        "Default graph extractor. PR-47 connects extractors.",
    ),
    "rag.graph.max_entities_per_chunk": (20, "Maximum entity candidates per chunk."),
    "rag.graph.max_relations_per_chunk": (40, "Maximum relation candidates per chunk."),
    "rag.graph.store_raw_evidence_text": (False, "Raw graph evidence text must not be stored."),
    "rag.graph.retrieval.enabled": (
        False,
        "Enable graph retrieval strategies. PR-48 connects retrieval.",
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
