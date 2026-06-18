from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.graph_models import GraphIndexRun, GraphRetrievalPath
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalCacheEntry,
    RetrievalRunItem,
    SystemSetting,
)
from app.rag.retrieval import RetrievalFilters
from app.rag.strategy import RetrievalStrategy
from app.rag.trace import LatencyTracker, TraceRedactor
from app.schemas.graph import validate_safe_graph_metadata

RETRIEVAL_CACHE_SCHEMA_VERSION = "rag.retrieval_cache.v1"
RETRIEVAL_CACHE_KEY_VERSION = "rag.retrieval_cache.key.v1"
RETRIEVAL_CACHE_CORPUS_MARKER_SETTING = "rag.retrieval_cache.corpus_marker"
DEFAULT_RETRIEVAL_CACHE_NAMESPACE = "rag.retrieval"
_HASH_NONE = "0" * 64

CacheLookupStatus = Literal["hit", "miss", "stale"]
CacheExecutionStatus = Literal["hit", "miss", "stale", "bypass"]


@dataclass(frozen=True)
class CachedRetrievalItem:
    document_chunk_id: int
    retrieval_score: float
    rerank_score: float | None
    rank_order: int
    rerank_order: int | None
    selected_flag: bool
    retrieval_source: str
    score_breakdown_json: dict[str, object] | None = None


@dataclass(frozen=True)
class CachedGraphPathRef:
    path_json: dict[str, object]
    score_breakdown_json: dict[str, object]
    source_chunk_ids_json: list[int]


@dataclass(frozen=True)
class CachedRetrievalPayload:
    schema_version: str
    query_hash: str
    strategy_type: str
    retrieval_score_summary: dict[str, object]
    items: tuple[CachedRetrievalItem, ...]
    graph_paths: tuple[CachedGraphPathRef, ...] = ()
    no_context: bool = False
    cache_created_at: datetime | None = None
    ttl_seconds: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "query_hash": self.query_hash,
            "strategy_type": self.strategy_type,
            "retrieval_score_summary": TraceRedactor.safe_dict(self.retrieval_score_summary),
            "items": [
                {
                    "document_chunk_id": item.document_chunk_id,
                    "retrieval_score": _round_score(item.retrieval_score),
                    "rerank_score": (
                        _round_score(item.rerank_score) if item.rerank_score is not None else None
                    ),
                    "rank_order": item.rank_order,
                    "rerank_order": item.rerank_order,
                    "selected_flag": item.selected_flag,
                    "retrieval_source": TraceRedactor.safe_string(
                        item.retrieval_source,
                        max_length=50,
                    ),
                    "score_breakdown_json": (
                        TraceRedactor.safe_dict(item.score_breakdown_json)
                        if item.score_breakdown_json is not None
                        else None
                    ),
                }
                for item in self.items
            ],
            "graph_paths": [
                {
                    "path_json": TraceRedactor.safe_dict(path.path_json),
                    "score_breakdown_json": TraceRedactor.safe_dict(path.score_breakdown_json),
                    "source_chunk_ids_json": list(path.source_chunk_ids_json),
                }
                for path in self.graph_paths
            ],
            "no_context": self.no_context,
            "cache_created_at": self.cache_created_at.isoformat()
            if self.cache_created_at is not None
            else None,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> CachedRetrievalPayload | None:
        if value.get("schema_version") != RETRIEVAL_CACHE_SCHEMA_VERSION:
            return None
        query_hash = _safe_hash(value.get("query_hash"))
        strategy_type = _safe_string(value.get("strategy_type"), max_length=50)
        summary = _safe_dict(value.get("retrieval_score_summary"))
        raw_items = value.get("items")
        if (
            query_hash is None
            or strategy_type is None
            or summary is None
            or not isinstance(raw_items, list)
        ):
            return None
        items: list[CachedRetrievalItem] = []
        for raw_item in raw_items:
            item = _cached_item_from_json(raw_item)
            if item is None:
                return None
            items.append(item)
        raw_graph_paths = value.get("graph_paths", [])
        graph_paths: list[CachedGraphPathRef] = []
        if isinstance(raw_graph_paths, list):
            for raw_path in raw_graph_paths:
                path = _cached_graph_path_from_json(raw_path)
                if path is not None:
                    graph_paths.append(path)
        cache_created_at = _safe_datetime(value.get("cache_created_at"))
        ttl_seconds = _safe_positive_int(value.get("ttl_seconds"))
        return cls(
            schema_version=RETRIEVAL_CACHE_SCHEMA_VERSION,
            query_hash=query_hash,
            strategy_type=strategy_type,
            retrieval_score_summary=summary,
            items=tuple(items),
            graph_paths=tuple(graph_paths),
            no_context=bool(value.get("no_context", False)),
            cache_created_at=cache_created_at,
            ttl_seconds=ttl_seconds,
        )


@dataclass(frozen=True)
class RetrievalCacheKey:
    cache_key: str
    cache_namespace: str
    strategy_type: str
    query_hash: str
    retrieval_settings_hash: str
    rerank_settings_hash: str
    embedding_model: str
    rerank_model: str
    active_document_fingerprint: str
    graph_index_fingerprint: str
    graph_store_provider: str
    top_k: int
    rerank_top_n: int
    user_visible_scope: str
    schema_version: str = RETRIEVAL_CACHE_SCHEMA_VERSION

    def to_metadata(self) -> dict[str, object]:
        return {
            "cache_namespace": self.cache_namespace,
            "strategy_type": self.strategy_type,
            "query_hash": self.query_hash,
            "retrieval_settings_hash": self.retrieval_settings_hash,
            "rerank_settings_hash": self.rerank_settings_hash,
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
            "active_document_fingerprint": self.active_document_fingerprint,
            "graph_index_fingerprint": self.graph_index_fingerprint,
            "graph_store_provider": self.graph_store_provider,
            "top_k": self.top_k,
            "rerank_top_n": self.rerank_top_n,
            "user_visible_scope": self.user_visible_scope,
            "schema_version": self.schema_version,
            "cache_key_hash": self.cache_key,
        }


@dataclass(frozen=True)
class RetrievalCacheEntryRecord:
    cache_key: RetrievalCacheKey
    payload: CachedRetrievalPayload
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CacheLookupResult:
    status: CacheLookupStatus
    entry: RetrievalCacheEntryRecord | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CacheExecutionResult:
    result: Any
    summary: dict[str, object]


@dataclass(frozen=True)
class RetrievalCacheContext:
    query_hash: str
    strategy_type: RetrievalStrategy
    top_k: int
    rerank_top_n: int
    filters: RetrievalFilters
    request_kind: Literal["search", "ask"]
    execution_strategy: RetrievalStrategy | None = None


class CacheStore(Protocol):
    def lookup(
        self,
        db: Session,
        *,
        cache_key: RetrievalCacheKey,
        now: datetime,
    ) -> CacheLookupResult: ...

    def store(
        self,
        db: Session,
        *,
        cache_key: RetrievalCacheKey,
        payload: CachedRetrievalPayload,
        ttl_seconds: int,
        now: datetime,
    ) -> None: ...


class PostgresCacheStore:
    def lookup(
        self,
        db: Session,
        *,
        cache_key: RetrievalCacheKey,
        now: datetime,
    ) -> CacheLookupResult:
        row = db.scalar(
            select(RetrievalCacheEntry).where(RetrievalCacheEntry.cache_key == cache_key.cache_key)
        )
        if row is None:
            return CacheLookupResult(status="miss", reason="not_found")
        payload = CachedRetrievalPayload.from_json(row.payload_json or {})
        if payload is None:
            return CacheLookupResult(status="stale", reason="payload_schema_mismatch")
        entry = RetrievalCacheEntryRecord(
            cache_key=cache_key,
            payload=payload,
            created_at=_aware_utc(row.created_at),
            expires_at=_aware_utc(row.expires_at),
        )
        if entry.expires_at <= now:
            return CacheLookupResult(status="stale", entry=entry, reason="ttl_expired")
        row.last_accessed_at = now
        db.flush()
        return CacheLookupResult(status="hit", entry=entry)

    def store(
        self,
        db: Session,
        *,
        cache_key: RetrievalCacheKey,
        payload: CachedRetrievalPayload,
        ttl_seconds: int,
        now: datetime,
    ) -> None:
        expires_at = now + timedelta(seconds=ttl_seconds)
        _prune_expired_entries(
            db,
            cache_namespace=cache_key.cache_namespace,
            now=now,
        )
        payload_with_metadata = CachedRetrievalPayload(
            schema_version=payload.schema_version,
            query_hash=payload.query_hash,
            strategy_type=payload.strategy_type,
            retrieval_score_summary=payload.retrieval_score_summary,
            items=payload.items,
            graph_paths=payload.graph_paths,
            no_context=payload.no_context,
            cache_created_at=now,
            ttl_seconds=ttl_seconds,
        )
        row = db.scalar(
            select(RetrievalCacheEntry).where(RetrievalCacheEntry.cache_key == cache_key.cache_key)
        )
        if row is None:
            row = RetrievalCacheEntry(
                cache_namespace=cache_key.cache_namespace,
                cache_key=cache_key.cache_key,
                schema_version=cache_key.schema_version,
                strategy_type=cache_key.strategy_type,
                query_hash=cache_key.query_hash,
                retrieval_settings_hash=cache_key.retrieval_settings_hash,
                rerank_settings_hash=cache_key.rerank_settings_hash,
                embedding_model=cache_key.embedding_model,
                rerank_model=cache_key.rerank_model,
                active_document_fingerprint=cache_key.active_document_fingerprint,
                graph_index_fingerprint=cache_key.graph_index_fingerprint,
                graph_store_provider=cache_key.graph_store_provider,
                top_k=cache_key.top_k,
                rerank_top_n=cache_key.rerank_top_n,
                user_visible_scope=cache_key.user_visible_scope,
                payload_json=payload_with_metadata.to_json(),
                expires_at=expires_at,
                last_accessed_at=now,
            )
            db.add(row)
        else:
            row.cache_namespace = cache_key.cache_namespace
            row.schema_version = cache_key.schema_version
            row.strategy_type = cache_key.strategy_type
            row.query_hash = cache_key.query_hash
            row.retrieval_settings_hash = cache_key.retrieval_settings_hash
            row.rerank_settings_hash = cache_key.rerank_settings_hash
            row.embedding_model = cache_key.embedding_model
            row.rerank_model = cache_key.rerank_model
            row.active_document_fingerprint = cache_key.active_document_fingerprint
            row.graph_index_fingerprint = cache_key.graph_index_fingerprint
            row.graph_store_provider = cache_key.graph_store_provider
            row.top_k = cache_key.top_k
            row.rerank_top_n = cache_key.rerank_top_n
            row.user_visible_scope = cache_key.user_visible_scope
            row.payload_json = payload_with_metadata.to_json()
            row.expires_at = expires_at
            row.last_accessed_at = now
            row.updated_at = now
        db.flush()


class InMemoryCacheStore:
    def __init__(self) -> None:
        self.entries: dict[str, RetrievalCacheEntryRecord] = {}

    def lookup(
        self,
        db: Session,
        *,
        cache_key: RetrievalCacheKey,
        now: datetime,
    ) -> CacheLookupResult:
        entry = self.entries.get(cache_key.cache_key)
        if entry is None:
            return CacheLookupResult(status="miss", reason="not_found")
        if entry.expires_at <= now:
            return CacheLookupResult(status="stale", entry=entry, reason="ttl_expired")
        return CacheLookupResult(status="hit", entry=entry)

    def store(
        self,
        db: Session,
        *,
        cache_key: RetrievalCacheKey,
        payload: CachedRetrievalPayload,
        ttl_seconds: int,
        now: datetime,
    ) -> None:
        del db
        self.entries = {key: entry for key, entry in self.entries.items() if entry.expires_at > now}
        payload_with_metadata = CachedRetrievalPayload(
            schema_version=payload.schema_version,
            query_hash=payload.query_hash,
            strategy_type=payload.strategy_type,
            retrieval_score_summary=payload.retrieval_score_summary,
            items=payload.items,
            graph_paths=payload.graph_paths,
            no_context=payload.no_context,
            cache_created_at=now,
            ttl_seconds=ttl_seconds,
        )
        self.entries[cache_key.cache_key] = RetrievalCacheEntryRecord(
            cache_key=cache_key,
            payload=payload_with_metadata,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )


class CacheKeyBuilder:
    def build(
        self,
        db: Session,
        *,
        settings: Settings,
        context: RetrievalCacheContext,
    ) -> RetrievalCacheKey:
        graph_store_provider = TraceRedactor.safe_string(
            settings.graph_store_provider,
            max_length=50,
        )
        namespace = _cache_namespace(settings)
        user_visible_scope = _user_visible_scope(context.filters)
        retrieval_settings_hash = _retrieval_settings_hash(
            db,
            settings=settings,
            context=context,
            user_visible_scope=user_visible_scope,
        )
        rerank_settings_hash = _rerank_settings_hash(settings)
        active_document_fingerprint = _active_document_fingerprint(
            db,
            filters=context.filters,
        )
        graph_index_fingerprint = (
            _graph_index_fingerprint(
                db,
                filters=context.filters,
            )
            if _strategy_uses_graph_index(context)
            else _HASH_NONE
        )
        embedding_model = TraceRedactor.safe_string(
            f"{settings.embedding_provider}:{settings.embedding_model}:"
            f"{settings.effective_embedding_dimension}",
            max_length=255,
        )
        rerank_model = TraceRedactor.safe_string(
            f"{settings.rerank_provider}:{settings.reranker_model}",
            max_length=255,
        )
        key_material = {
            "cache_namespace": namespace,
            "strategy_type": context.strategy_type.value,
            "query_hash": context.query_hash,
            "retrieval_settings_hash": retrieval_settings_hash,
            "rerank_settings_hash": rerank_settings_hash,
            "embedding_model": embedding_model,
            "rerank_model": rerank_model,
            "active_document_fingerprint": active_document_fingerprint,
            "graph_index_fingerprint": graph_index_fingerprint,
            "graph_store_provider": graph_store_provider,
            "top_k": context.top_k,
            "rerank_top_n": context.rerank_top_n,
            "user_visible_scope": user_visible_scope,
            "schema_version": RETRIEVAL_CACHE_SCHEMA_VERSION,
            "key_version": RETRIEVAL_CACHE_KEY_VERSION,
        }
        cache_key = _hash_json(key_material)
        return RetrievalCacheKey(
            cache_key=cache_key,
            cache_namespace=namespace,
            strategy_type=context.strategy_type.value,
            query_hash=context.query_hash,
            retrieval_settings_hash=retrieval_settings_hash,
            rerank_settings_hash=rerank_settings_hash,
            embedding_model=embedding_model,
            rerank_model=rerank_model,
            active_document_fingerprint=active_document_fingerprint,
            graph_index_fingerprint=graph_index_fingerprint,
            graph_store_provider=graph_store_provider,
            top_k=context.top_k,
            rerank_top_n=context.rerank_top_n,
            user_visible_scope=user_visible_scope,
        )


class RetrievalCacheService:
    def __init__(
        self,
        *,
        store: CacheStore | None = None,
        key_builder: CacheKeyBuilder | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store or PostgresCacheStore()
        self.key_builder = key_builder or CacheKeyBuilder()
        self.clock = clock or (lambda: datetime.now(UTC))

    def execute(
        self,
        db: Session,
        *,
        settings: Settings,
        context: RetrievalCacheContext,
        bypass: bool,
        cacheable: bool,
        latency_tracker: LatencyTracker,
        hydrate: Callable[[CachedRetrievalPayload], Any | None],
        retrieve: Callable[[], Any],
        payload_from_result: Callable[[Any], CachedRetrievalPayload | None],
    ) -> CacheExecutionResult:
        if not settings.retrieval_cache_enabled:
            result = retrieve()
            return CacheExecutionResult(
                result=result,
                summary=_cache_summary(status="bypass", reason="disabled"),
            )
        if bypass:
            result = retrieve()
            return CacheExecutionResult(
                result=result,
                summary=_cache_summary(status="bypass", reason="request_bypass"),
            )
        if not cacheable:
            result = retrieve()
            return CacheExecutionResult(
                result=result,
                summary=_cache_summary(status="bypass", reason="strategy_not_cacheable"),
            )

        try:
            with latency_tracker.span("retrieval_cache_key_ms"):
                cache_key = self.key_builder.build(db, settings=settings, context=context)
        except Exception:
            result = retrieve()
            return CacheExecutionResult(
                result=result,
                summary=_cache_summary(status="bypass", reason="key_build_failed"),
            )

        now = self.clock()
        lookup = CacheLookupResult(status="miss", reason="lookup_failed")
        try:
            with latency_tracker.span("retrieval_cache_lookup_ms"):
                with db.begin_nested():
                    lookup = self.store.lookup(db, cache_key=cache_key, now=now)
        except Exception:
            lookup = CacheLookupResult(status="miss", reason="lookup_failed")

        if lookup.status == "hit" and lookup.entry is not None:
            try:
                with latency_tracker.span("retrieval_cache_hydrate_ms"):
                    with db.begin_nested():
                        result = hydrate(lookup.entry.payload)
                if result is not None:
                    return CacheExecutionResult(
                        result=result,
                        summary=_cache_summary(status="hit", cache_key=cache_key),
                    )
            except Exception:
                lookup = CacheLookupResult(
                    status="stale",
                    entry=lookup.entry,
                    reason="hydrate_failed",
                )
            else:
                lookup = CacheLookupResult(
                    status="stale",
                    entry=lookup.entry,
                    reason="hydrate_empty",
                )

        result = retrieve()
        store_reason: str | None = None
        try:
            with latency_tracker.span("retrieval_cache_store_ms"):
                with db.begin_nested():
                    payload = payload_from_result(result)
                    if payload is None:
                        store_reason = "store_bypassed"
                    else:
                        self.store.store(
                            db,
                            cache_key=cache_key,
                            payload=payload,
                            ttl_seconds=settings.retrieval_cache_ttl_seconds,
                            now=self.clock(),
                        )
        except Exception:
            store_reason = "store_failed"

        reason = lookup.reason
        if store_reason is not None:
            reason = f"{reason or lookup.status};{store_reason}"
        return CacheExecutionResult(
            result=result,
            summary=_cache_summary(
                status=lookup.status,
                reason=reason,
                cache_key=cache_key,
            ),
        )


def payload_from_run_items(
    *,
    query_hash: str,
    strategy_type: str,
    retrieval_score_summary: dict[str, object],
    items: list[RetrievalRunItem],
    graph_paths: list[GraphRetrievalPath],
    no_context: bool,
) -> CachedRetrievalPayload:
    return CachedRetrievalPayload(
        schema_version=RETRIEVAL_CACHE_SCHEMA_VERSION,
        query_hash=query_hash,
        strategy_type=strategy_type,
        retrieval_score_summary=TraceRedactor.safe_dict(retrieval_score_summary),
        items=tuple(_cache_item_from_run_item(item) for item in items),
        graph_paths=tuple(
            safe_path
            for path in graph_paths
            if (safe_path := _cache_graph_path_from_row(path)) is not None
        ),
        no_context=no_context,
    )


def touch_retrieval_cache_corpus_marker(
    db: Session,
    *,
    updated_at: datetime,
) -> None:
    marker_updated_at = _aware_utc(updated_at)
    marker_value = {
        "version": 1,
        "updated_at": marker_updated_at.isoformat(),
    }
    row = db.get(SystemSetting, RETRIEVAL_CACHE_CORPUS_MARKER_SETTING)
    if row is None:
        db.add(
            SystemSetting(
                setting_key=RETRIEVAL_CACHE_CORPUS_MARKER_SETTING,
                setting_value=marker_value,
                description=("Bumped when retrieval-visible active document corpus state changes."),
                created_at=marker_updated_at,
                updated_at=marker_updated_at,
            )
        )
    else:
        row.setting_value = marker_value
        row.updated_at = marker_updated_at
    db.flush()


def _prune_expired_entries(
    db: Session,
    *,
    cache_namespace: str,
    now: datetime,
) -> None:
    db.execute(
        delete(RetrievalCacheEntry).where(
            RetrievalCacheEntry.cache_namespace == cache_namespace,
            RetrievalCacheEntry.expires_at <= now,
        )
    )


def _cache_item_from_run_item(item: RetrievalRunItem) -> CachedRetrievalItem:
    return CachedRetrievalItem(
        document_chunk_id=item.document_chunk_id,
        retrieval_score=float(item.retrieval_score),
        rerank_score=float(item.rerank_score) if item.rerank_score is not None else None,
        rank_order=item.rank_order,
        rerank_order=item.rerank_order,
        selected_flag=item.selected_flag,
        retrieval_source=str(item.retrieval_source or ""),
        score_breakdown_json=TraceRedactor.safe_dict(item.score_breakdown_json or {}),
    )


def _cache_graph_path_from_row(path: GraphRetrievalPath) -> CachedGraphPathRef | None:
    try:
        path_json = validate_safe_graph_metadata(TraceRedactor.safe_dict(path.path_json))
        score_breakdown_json = validate_safe_graph_metadata(
            TraceRedactor.safe_dict(path.score_breakdown_json or {})
        )
        source_chunk_ids_json = [
            int(chunk_id)
            for chunk_id in path.source_chunk_ids_json or []
            if not isinstance(chunk_id, bool) and isinstance(chunk_id, int) and chunk_id > 0
        ]
    except (TypeError, ValueError):
        return None
    return CachedGraphPathRef(
        path_json=path_json,
        score_breakdown_json=score_breakdown_json,
        source_chunk_ids_json=source_chunk_ids_json,
    )


def _cache_summary(
    *,
    status: CacheExecutionStatus,
    reason: str | None = None,
    cache_key: RetrievalCacheKey | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": RETRIEVAL_CACHE_SCHEMA_VERSION,
        "status": status,
        "enabled": status != "bypass" or reason != "disabled",
    }
    if reason:
        payload["reason"] = TraceRedactor.safe_string(reason, max_length=120)
    if cache_key is not None:
        payload.update(cache_key.to_metadata())
    return TraceRedactor.safe_dict(payload)


def _retrieval_settings_hash(
    db: Session,
    *,
    settings: Settings,
    context: RetrievalCacheContext,
    user_visible_scope: str,
) -> str:
    payload = {
        "request_kind": context.request_kind,
        "strategy_type": context.strategy_type.value,
        "execution_strategy": (
            context.execution_strategy.value if context.execution_strategy is not None else None
        ),
        "filters": {
            "modality": context.filters.modality,
            "logical_document_ids": list(context.filters.logical_document_ids or ()),
        },
        "user_visible_scope": user_visible_scope,
        "qdrant_collection_name": settings.qdrant_collection_name,
        "qdrant_distance": (
            settings.qdrant_distance if _strategy_uses_vector_distance(context) else None
        ),
        "hybrid_enabled": settings.hybrid_enabled,
        "hybrid_fusion_method": settings.hybrid_fusion_method,
        "hybrid_rrf_k": settings.hybrid_rrf_k,
        "hybrid_dense_weight": settings.hybrid_dense_weight,
        "hybrid_sparse_weight": settings.hybrid_sparse_weight,
        "hybrid_candidate_multiplier": settings.hybrid_candidate_multiplier,
        "sparse_enabled": settings.sparse_enabled,
        "sparse_provider": settings.sparse_provider,
        "sparse_language": settings.sparse_language,
        "sparse_min_query_terms": settings.sparse_min_query_terms,
        "sparse_max_query_terms": settings.sparse_max_query_terms,
        "sparse_score_normalization": settings.sparse_score_normalization,
        "query_analyzer_enabled": settings.query_analyzer_enabled,
        "query_planner_enabled": settings.query_planner_enabled,
        "query_planner_apply_rewrite_to_retrieval": (
            settings.query_planner_apply_rewrite_to_retrieval
        ),
        "query_planner_max_sub_queries": settings.query_planner_max_sub_queries,
        "query_planner_max_preview_chars": settings.query_planner_max_preview_chars,
        "query_planner_store_query_preview": settings.query_planner_store_query_preview,
        "query_planner_redact_pii": settings.query_planner_redact_pii,
        "router_enabled": settings.router_enabled,
        "router_mode": settings.router_mode,
        "router_llm_planner_model_name": settings.router_llm_planner_model_name,
        "router_llm_planner_timeout_seconds": settings.router_llm_planner_timeout_seconds,
        "router_llm_planner_max_output_tokens": settings.router_llm_planner_max_output_tokens,
        "router_allow_agentic_search": settings.router_allow_agentic_search,
        "router_allow_agentic_ask": settings.router_allow_agentic_ask,
        "router_keyword_heavy_threshold": settings.router_keyword_heavy_threshold,
        "router_ambiguity_threshold": settings.router_ambiguity_threshold,
        "router_max_retrieval_calls": settings.router_max_retrieval_calls,
        "router_max_fallback_calls": settings.router_max_fallback_calls,
        "router_sufficiency_min_candidates": settings.router_sufficiency_min_candidates,
        "router_sufficiency_min_selected": settings.router_sufficiency_min_selected,
        "router_sufficiency_top_score_threshold": (settings.router_sufficiency_top_score_threshold),
        "router_enable_fallback_hybrid": settings.router_enable_fallback_hybrid,
        "router_enable_fallback_dense": settings.router_enable_fallback_dense,
        "router_no_context_after_budget_exhausted": (
            settings.router_no_context_after_budget_exhausted
        ),
        "router_fallback_strategy": settings.router_fallback_strategy,
        "graph_retrieval_enabled": settings.graph_retrieval_enabled,
        "graph_retrieval_max_start_entities": settings.graph_retrieval_max_start_entities,
        "graph_retrieval_max_depth": settings.graph_retrieval_max_depth,
        "graph_retrieval_max_paths": settings.graph_retrieval_max_paths,
        "graph_retrieval_max_relations_per_entity": (
            settings.graph_retrieval_max_relations_per_entity
        ),
        "graph_retrieval_max_source_chunks": settings.graph_retrieval_max_source_chunks,
        "graph_retrieval_timeout_ms": settings.graph_retrieval_timeout_ms,
        "graph_retrieval_fallback_strategy": settings.graph_retrieval_fallback_strategy,
        "graph_retrieval_min_entity_match_score": settings.graph_retrieval_min_entity_match_score,
        "graph_router_enabled": settings.graph_router_enabled,
        "graph_router_min_signal_score": settings.graph_router_min_signal_score,
        "system_settings_hash": _system_settings_hash(db),
    }
    return _hash_json(payload)


def _rerank_settings_hash(settings: Settings) -> str:
    return _hash_json(
        {
            "rerank_provider": settings.rerank_provider,
            "reranker_model": settings.reranker_model,
            "rerank_score_min": settings.rerank_score_min,
            "rerank_score_max": settings.rerank_score_max,
        }
    )


def _active_document_fingerprint(db: Session, *, filters: RetrievalFilters) -> str:
    if not filters.logical_document_ids:
        return _active_corpus_marker(db, filters=filters)
    statement = (
        select(
            LogicalDocument.logical_document_id,
            LogicalDocument.status,
            LogicalDocument.updated_at,
            DocumentVersion.document_version_id,
            DocumentVersion.version_no,
            DocumentVersion.content_hash,
            DocumentVersion.status,
            DocumentVersion.is_active,
            DocumentVersion.updated_at,
            func.count(DocumentChunk.document_chunk_id),
            func.max(DocumentChunk.created_at),
            func.min(DocumentChunk.chunk_hash),
            func.max(DocumentChunk.chunk_hash),
        )
        .join(
            DocumentVersion,
            DocumentVersion.logical_document_id == LogicalDocument.logical_document_id,
        )
        .join(
            DocumentChunk,
            DocumentChunk.document_version_id == DocumentVersion.document_version_id,
        )
        .where(
            LogicalDocument.status == "active",
            DocumentVersion.status == "ready",
            DocumentVersion.is_active.is_(True),
            DocumentChunk.modality == filters.modality,
        )
        .group_by(
            LogicalDocument.logical_document_id,
            LogicalDocument.status,
            LogicalDocument.updated_at,
            DocumentVersion.document_version_id,
            DocumentVersion.version_no,
            DocumentVersion.content_hash,
            DocumentVersion.status,
            DocumentVersion.is_active,
            DocumentVersion.updated_at,
        )
        .order_by(
            LogicalDocument.logical_document_id.asc(),
            DocumentVersion.document_version_id.asc(),
        )
    )
    if filters.logical_document_ids:
        statement = statement.where(
            LogicalDocument.logical_document_id.in_(filters.logical_document_ids)
        )
    rows = db.execute(statement).all()
    if not rows:
        return _HASH_NONE
    return _hash_json([_json_ready_tuple(row) for row in rows])


def _active_corpus_marker(db: Session, *, filters: RetrievalFilters) -> str:
    row = db.execute(
        select(SystemSetting.setting_value, SystemSetting.updated_at).where(
            SystemSetting.setting_key == RETRIEVAL_CACHE_CORPUS_MARKER_SETTING
        )
    ).one_or_none()
    if row is None:
        return _HASH_NONE
    return _hash_json(
        {
            "scope": "active_corpus_marker",
            "modality": filters.modality,
            "setting_key": RETRIEVAL_CACHE_CORPUS_MARKER_SETTING,
            "setting_value": row[0],
            "updated_at": row[1],
        }
    )


def _strategy_uses_graph_index(context: RetrievalCacheContext) -> bool:
    return (
        context.strategy_type == RetrievalStrategy.GRAPH
        or context.execution_strategy == RetrievalStrategy.GRAPH
    )


def _strategy_uses_vector_distance(context: RetrievalCacheContext) -> bool:
    vector_strategies = {
        RetrievalStrategy.DENSE,
        RetrievalStrategy.HYBRID,
        RetrievalStrategy.FALLBACK_DENSE,
    }
    return (
        context.strategy_type in vector_strategies
        or context.execution_strategy in vector_strategies
    )


def _graph_index_fingerprint(db: Session, *, filters: RetrievalFilters) -> str:
    active_version_statement = (
        select(DocumentVersion.document_version_id)
        .join(
            LogicalDocument,
            LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
        )
        .where(
            LogicalDocument.status == "active",
            DocumentVersion.status == "ready",
            DocumentVersion.is_active.is_(True),
        )
    )
    if filters.logical_document_ids:
        active_version_statement = active_version_statement.where(
            LogicalDocument.logical_document_id.in_(filters.logical_document_ids)
        )
    active_version_ids = tuple(db.scalars(active_version_statement).all())
    if not active_version_ids:
        return _HASH_NONE
    rows = db.execute(
        select(
            GraphIndexRun.document_version_id,
            GraphIndexRun.graph_index_run_id,
            GraphIndexRun.status,
            GraphIndexRun.extractor_type,
            GraphIndexRun.extractor_version,
            GraphIndexRun.entity_count,
            GraphIndexRun.relation_count,
            GraphIndexRun.mention_count,
            GraphIndexRun.updated_at,
        )
        .where(GraphIndexRun.document_version_id.in_(active_version_ids))
        .order_by(
            GraphIndexRun.document_version_id.asc(),
            GraphIndexRun.graph_index_run_id.asc(),
        )
    ).all()
    if not rows:
        return _HASH_NONE
    return _hash_json([_json_ready_tuple(row) for row in rows])


def _system_settings_hash(db: Session) -> str:
    rows = db.execute(
        select(SystemSetting.setting_key, SystemSetting.setting_value, SystemSetting.updated_at)
        .where(
            SystemSetting.setting_key.like("rag.%"),
            SystemSetting.setting_key != RETRIEVAL_CACHE_CORPUS_MARKER_SETTING,
        )
        .order_by(SystemSetting.setting_key.asc())
    ).all()
    if not rows:
        return _HASH_NONE
    return _hash_json([_json_ready_tuple(row) for row in rows])


def _user_visible_scope(filters: RetrievalFilters) -> str:
    return _hash_json(
        {
            "modality": filters.modality,
            "logical_document_ids": list(filters.logical_document_ids or ()),
        }
    )


def _cache_namespace(settings: Settings) -> str:
    namespace = TraceRedactor.safe_string(settings.retrieval_cache_namespace, max_length=80)
    return namespace or DEFAULT_RETRIEVAL_CACHE_NAMESPACE


def _hash_json(value: object) -> str:
    payload = json.dumps(_json_ready(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_ready(value: object) -> object:
    if isinstance(value, datetime):
        return _aware_utc(value).isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Mapping):
        return {str(key): _json_ready(nested) for key, nested in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return value


def _json_ready_tuple(row: Any) -> list[object]:
    return [_json_ready(item) for item in tuple(row)]


def _cached_item_from_json(value: object) -> CachedRetrievalItem | None:
    if not isinstance(value, Mapping):
        return None
    document_chunk_id = _safe_positive_int(value.get("document_chunk_id"))
    retrieval_score = _safe_score(value.get("retrieval_score"))
    rank_order = _safe_positive_int(value.get("rank_order"))
    retrieval_source = _safe_string(value.get("retrieval_source"), max_length=50)
    if (
        document_chunk_id is None
        or retrieval_score is None
        or rank_order is None
        or retrieval_source is None
    ):
        return None
    rerank_score = _safe_score(value.get("rerank_score"))
    rerank_order = _safe_positive_int(value.get("rerank_order"))
    return CachedRetrievalItem(
        document_chunk_id=document_chunk_id,
        retrieval_score=retrieval_score,
        rerank_score=rerank_score,
        rank_order=rank_order,
        rerank_order=rerank_order,
        selected_flag=bool(value.get("selected_flag", False)),
        retrieval_source=retrieval_source,
        score_breakdown_json=_safe_dict(value.get("score_breakdown_json")),
    )


def _cached_graph_path_from_json(value: object) -> CachedGraphPathRef | None:
    if not isinstance(value, Mapping):
        return None
    path_json = _safe_dict(value.get("path_json"))
    score_breakdown_json = _safe_dict(value.get("score_breakdown_json")) or {}
    source_chunk_ids = value.get("source_chunk_ids_json")
    if path_json is None or not isinstance(source_chunk_ids, list):
        return None
    safe_ids = []
    for chunk_id in source_chunk_ids:
        safe_id = _safe_positive_int(chunk_id)
        if safe_id is not None:
            safe_ids.append(safe_id)
    return CachedGraphPathRef(
        path_json=path_json,
        score_breakdown_json=score_breakdown_json,
        source_chunk_ids_json=safe_ids,
    )


def _safe_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return TraceRedactor.safe_dict(value)


def _safe_hash(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if len(normalized) == 64 and all(char in "0123456789abcdef" for char in normalized):
        return normalized
    return None


def _safe_string(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    return safe or None


def _safe_score(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    score = float(value)
    if score != score or score in {float("inf"), float("-inf")}:
        return None
    return _round_score(score)


def _safe_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _safe_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _round_score(value: float) -> float:
    return round(float(value), 6)
