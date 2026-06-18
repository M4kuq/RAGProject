from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db import graph_models as _graph_models  # noqa: F401
from app.db.base import Base
from app.db.graph_models import GraphRetrievalPath
from app.db.models import (
    ChatSession,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    Role,
    User,
)
from app.ingest.embedding import FakeEmbeddingAdapter
from app.rag.generation import FakeAnswerGenerator
from app.rag.rerank import FakeRerankerClient, RerankCandidate, RerankerClient, RerankResult
from app.rag.retrieval import RetrievalError, RetrievalFilters, VectorSearchCandidate
from app.rag.retrieval_cache import (
    CacheKeyBuilder,
    InMemoryCacheStore,
    RetrievalCacheContext,
    RetrievalCacheService,
    payload_from_run_items,
)
from app.rag.strategy import RagSearchRequestStrategy, RetrievalStrategy
from app.schemas.rag import RagAskRequest, RagSearchRequest
from app.services.rag_service import RagService


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as db:
        _seed_minimal_data(db)
        db.commit()
    try:
        yield factory
    finally:
        engine.dispose()


def test_retrieval_cache_hit_rebuilds_ask_citation_from_current_chunk(
    session_factory: sessionmaker[Session],
) -> None:
    store = InMemoryCacheStore()
    vector_client = _CountingVectorClient([_candidate(100, 0.91, 1)])
    service = _service(
        store=store,
        vector_client=vector_client,
        settings=_settings(retrieval_cache_enabled=True),
    )

    with session_factory() as db:
        user = db.get(User, 1)
        assert user is not None
        first = service.ask(
            db,
            payload=RagAskRequest(
                chat_session_id=1,
                client_message_id="cache-hit-1",
                message="Where is alpha policy?",
                top_k=1,
                rerank_top_n=1,
            ),
            user=user,
            request_id="req-cache-hit-1",
        )
        assert first.citations
        assert "Alpha retrieval cache source text" in first.citations[0].snippet
        first_run = db.get(RetrievalRun, first.retrieval_run_id)
        assert first_run is not None
        assert first_run.cache_summary_json is not None
        assert first_run.cache_summary_json["status"] == "miss"
        assert len(vector_client.query_vectors) == 1

        payload_dump = json.dumps(
            [entry.payload.to_json() for entry in store.entries.values()],
            sort_keys=True,
        )
        assert "Where is alpha policy" not in payload_dump
        assert "Alpha retrieval cache source text" not in payload_dump
        assert "content_text" not in payload_dump
        assert "snippet" not in payload_dump.lower()

        chunk = db.get(DocumentChunk, 100)
        assert chunk is not None
        chunk.content_text = "Updated source text rebuilt from the database on cache hit."
        db.commit()

        second = service.ask(
            db,
            payload=RagAskRequest(
                chat_session_id=1,
                client_message_id="cache-hit-2",
                message="Where is alpha policy?",
                top_k=1,
                rerank_top_n=1,
            ),
            user=user,
            request_id="req-cache-hit-2",
        )
        assert len(vector_client.query_vectors) == 1
        assert second.citations
        assert "Updated source text rebuilt" in second.citations[0].snippet
        second_run = db.get(RetrievalRun, second.retrieval_run_id)
        assert second_run is not None
        assert second_run.cache_summary_json is not None
        assert second_run.cache_summary_json["status"] == "hit"


def test_retrieval_cache_hit_preserves_rerank_order(
    session_factory: sessionmaker[Session],
) -> None:
    store = InMemoryCacheStore()
    vector_client = _CountingVectorClient(
        [
            _candidate(100, 0.41, 1),
            _candidate(101, 0.39, 2),
        ]
    )
    service = _service(
        store=store,
        vector_client=vector_client,
        reranker=_FixedOrderReranker([101, 100]),
        settings=_settings(retrieval_cache_enabled=True),
    )

    with session_factory() as db:
        _add_chunk(
            db,
            document_chunk_id=101,
            chunk_index=1,
            chunk_hash="c" * 64,
            content_text="Beta chunk should become first after rerank.",
        )
        db.commit()

        first = service.search(
            db,
            payload=RagSearchRequest(
                query="rerank beta first",
                top_k=2,
                rerank_top_n=2,
            ),
            request_id="req-rerank-order-1",
        )
        assert [item.document_chunk_id for item in first.items] == [101, 100]
        assert len(vector_client.query_vectors) == 1

        stored_payload = next(iter(store.entries.values())).payload
        assert [item.document_chunk_id for item in stored_payload.items] == [101, 100]

        vector_client.fail = True
        second = service.search(
            db,
            payload=RagSearchRequest(
                query="rerank beta first",
                top_k=2,
                rerank_top_n=2,
            ),
            request_id="req-rerank-order-2",
        )
        assert [item.document_chunk_id for item in second.items] == [101, 100]
        assert len(vector_client.query_vectors) == 1
        second_run = db.get(RetrievalRun, second.retrieval_run_id)
        assert second_run is not None
        assert second_run.cache_summary_json is not None
        assert second_run.cache_summary_json["status"] == "hit"


def test_retrieval_cache_disabled_and_request_bypass_trace(
    session_factory: sessionmaker[Session],
) -> None:
    disabled_store = InMemoryCacheStore()
    disabled_vector = _CountingVectorClient([_candidate(100, 0.91, 1)])
    disabled_service = _service(
        store=disabled_store,
        vector_client=disabled_vector,
        settings=_settings(retrieval_cache_enabled=False),
    )
    with session_factory() as db:
        response = disabled_service.search(
            db,
            payload=RagSearchRequest(query="alpha", top_k=1, rerank_top_n=1),
            request_id="req-disabled",
        )
        run = db.get(RetrievalRun, response.retrieval_run_id)
        assert run is not None
        assert run.cache_summary_json == {
            "schema_version": "rag.retrieval_cache.v1",
            "status": "bypass",
            "enabled": False,
            "reason": "disabled",
        }
        assert disabled_store.entries == {}

    bypass_store = InMemoryCacheStore()
    bypass_vector = _CountingVectorClient([_candidate(100, 0.91, 1)])
    bypass_service = _service(
        store=bypass_store,
        vector_client=bypass_vector,
        settings=_settings(retrieval_cache_enabled=True),
    )
    with session_factory() as db:
        response = bypass_service.search(
            db,
            payload=RagSearchRequest(
                query="alpha",
                top_k=1,
                rerank_top_n=1,
                cache_bypass=True,
            ),
            request_id="req-bypass",
        )
        run = db.get(RetrievalRun, response.retrieval_run_id)
        assert run is not None
        assert run.cache_summary_json is not None
        assert run.cache_summary_json["status"] == "bypass"
        assert run.cache_summary_json["reason"] == "request_bypass"
        assert bypass_store.entries == {}


def test_agentic_router_bypasses_cache_to_preserve_execution_trace(
    session_factory: sessionmaker[Session],
) -> None:
    store = InMemoryCacheStore()
    vector_client = _CountingVectorClient([_candidate(100, 0.91, 1)])
    service = _service(
        store=store,
        vector_client=vector_client,
        settings=_settings(retrieval_cache_enabled=True, router_enabled=False),
    )

    with session_factory() as db:
        for request_id in ("req-agentic-bypass-1", "req-agentic-bypass-2"):
            response = service.search(
                db,
                payload=RagSearchRequest(
                    query="alpha",
                    top_k=1,
                    rerank_top_n=1,
                    strategy=RagSearchRequestStrategy.AGENTIC_ROUTER,
                ),
                request_id=request_id,
            )
            run = db.get(RetrievalRun, response.retrieval_run_id)
            assert run is not None
            assert run.cache_summary_json is not None
            assert run.cache_summary_json["status"] == "bypass"
            assert run.cache_summary_json["reason"] == "strategy_not_cacheable"
            assert run.strategy_decision_json is not None
            assert run.strategy_decision_json["fallback_reason"] == "router_disabled"

        assert len(vector_client.query_vectors) == 2
        assert store.entries == {}


def test_retrieval_cache_stale_entry_runs_retrieval_again(
    session_factory: sessionmaker[Session],
) -> None:
    now = datetime(2026, 6, 18, tzinfo=UTC)

    def clock() -> datetime:
        return now

    store = InMemoryCacheStore()
    vector_client = _CountingVectorClient([_candidate(100, 0.91, 1)])
    service = _service(
        store=store,
        vector_client=vector_client,
        settings=_settings(retrieval_cache_enabled=True, retrieval_cache_ttl_seconds=1),
        clock=clock,
    )

    with session_factory() as db:
        first = service.search(
            db,
            payload=RagSearchRequest(query="alpha", top_k=1, rerank_top_n=1),
            request_id="req-stale-1",
        )
        first_run = db.get(RetrievalRun, first.retrieval_run_id)
        assert first_run is not None
        assert first_run.cache_summary_json is not None
        assert first_run.cache_summary_json["status"] == "miss"
        assert len(vector_client.query_vectors) == 1

    now = now + timedelta(seconds=2)
    with session_factory() as db:
        second = service.search(
            db,
            payload=RagSearchRequest(query="alpha", top_k=1, rerank_top_n=1),
            request_id="req-stale-2",
        )
        second_run = db.get(RetrievalRun, second.retrieval_run_id)
        assert second_run is not None
        assert second_run.cache_summary_json is not None
        assert second_run.cache_summary_json["status"] == "stale"
        assert second_run.cache_summary_json["reason"] == "ttl_expired"
        assert len(vector_client.query_vectors) == 2


def test_cache_key_changes_for_router_and_planner_controls(
    session_factory: sessionmaker[Session],
) -> None:
    builder = CacheKeyBuilder()
    query_hash = hashlib.sha256(b"router planner controls").hexdigest()
    context = RetrievalCacheContext(
        query_hash=query_hash,
        strategy_type=RetrievalStrategy.DENSE,
        execution_strategy=RetrievalStrategy.DENSE,
        top_k=3,
        rerank_top_n=2,
        filters=RetrievalFilters(logical_document_ids=(1,)),
        request_kind="search",
    )
    base_settings = _settings(retrieval_cache_enabled=True)
    changed_settings = [
        _settings(retrieval_cache_enabled=True, query_analyzer_enabled=False),
        _settings(retrieval_cache_enabled=True, query_planner_enabled=False),
        _settings(
            retrieval_cache_enabled=True,
            query_planner_apply_rewrite_to_retrieval=True,
        ),
        _settings(retrieval_cache_enabled=True, router_max_retrieval_calls=3),
        _settings(retrieval_cache_enabled=True, router_sufficiency_top_score_threshold=0.9),
        _settings(retrieval_cache_enabled=True, router_enable_fallback_hybrid=False),
    ]

    with session_factory() as db:
        base_key = builder.build(db, settings=base_settings, context=context)
        for settings in changed_settings:
            changed_key = builder.build(db, settings=settings, context=context)
            assert changed_key.cache_key != base_key.cache_key
            assert changed_key.retrieval_settings_hash != base_key.retrieval_settings_hash


def test_cache_key_uses_provider_and_hashes_query(
    session_factory: sessionmaker[Session],
) -> None:
    builder = CacheKeyBuilder()
    raw_query = "alpha raw query must not be stored"
    query_hash = hashlib.sha256(raw_query.encode("utf-8")).hexdigest()
    with session_factory() as db:
        postgres_key = builder.build(
            db,
            settings=_settings(retrieval_cache_enabled=True, graph_store_provider="postgres"),
            context=RetrievalCacheContext(
                query_hash=query_hash,
                strategy_type=RetrievalStrategy.GRAPH,
                execution_strategy=RetrievalStrategy.GRAPH,
                top_k=3,
                rerank_top_n=2,
                filters=RetrievalFilters(logical_document_ids=(1,)),
                request_kind="search",
            ),
        )
        neo4j_key = builder.build(
            db,
            settings=_settings(retrieval_cache_enabled=True, graph_store_provider="neo4j"),
            context=RetrievalCacheContext(
                query_hash=query_hash,
                strategy_type=RetrievalStrategy.GRAPH,
                execution_strategy=RetrievalStrategy.GRAPH,
                top_k=3,
                rerank_top_n=2,
                filters=RetrievalFilters(logical_document_ids=(1,)),
                request_kind="search",
            ),
        )

    assert postgres_key.cache_key != neo4j_key.cache_key
    assert postgres_key.graph_store_provider == "postgres"
    assert neo4j_key.graph_store_provider == "neo4j"
    metadata_dump = json.dumps(postgres_key.to_metadata(), sort_keys=True)
    assert raw_query not in metadata_dump
    assert postgres_key.query_hash == query_hash
    for field in (
        "cache_namespace",
        "strategy_type",
        "query_hash",
        "retrieval_settings_hash",
        "rerank_settings_hash",
        "embedding_model",
        "rerank_model",
        "active_document_fingerprint",
        "graph_index_fingerprint",
        "graph_store_provider",
        "top_k",
        "rerank_top_n",
        "user_visible_scope",
        "schema_version",
    ):
        assert field in postgres_key.to_metadata()


def test_retrieval_cache_payload_drops_unsafe_graph_path_metadata() -> None:
    safe_path = GraphRetrievalPath(
        retrieval_run_id=1,
        path_json={
            "nodes": [{"entity_id": 1, "label_hash": hashlib.sha256(b"alpha").hexdigest()}],
        },
        score_breakdown_json={"path_score": 0.7},
        source_chunk_ids_json=[100],
    )
    unsafe_path = GraphRetrievalPath(
        retrieval_run_id=1,
        path_json={"evidence_text": "raw graph evidence must not be cached"},
        score_breakdown_json={},
        source_chunk_ids_json=[100],
    )

    payload = payload_from_run_items(
        query_hash=hashlib.sha256(b"query").hexdigest(),
        strategy_type=RetrievalStrategy.GRAPH.value,
        retrieval_score_summary={
            "requested_top_k": 1,
            "qdrant_candidate_count": 0,
            "post_filter_candidate_count": 0,
            "selected_count": 0,
            "excluded_by_rdb_check_count": 0,
        },
        items=[],
        graph_paths=[safe_path, unsafe_path],
        no_context=True,
    )

    payload_dump = json.dumps(payload.to_json(), sort_keys=True)
    assert len(payload.graph_paths) == 1
    assert "raw graph evidence" not in payload_dump
    assert "evidence_text" not in payload_dump


def _service(
    *,
    store: InMemoryCacheStore,
    vector_client: _CountingVectorClient,
    settings: Settings,
    reranker: RerankerClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> RagService:
    return RagService(
        settings=settings,
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=vector_client,
        reranker=reranker or FakeRerankerClient(),
        answer_generator=FakeAnswerGenerator(),
        retrieval_cache_service=RetrievalCacheService(
            store=store,
            clock=clock,
        ),
    )


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "app_env": "test",
        "embedding_provider": "fake",
        "embedding_fake_dimension": 4,
        "retrieval_top_k_default": 1,
        "retrieval_top_k_max": 5,
        "rerank_provider": "fake",
        "rerank_top_n_default": 1,
        "rerank_top_n_max": 5,
        "ask_top_k_default": 1,
        "ask_rerank_top_n_default": 1,
        "qdrant_collection_name": "document_chunks",
        "search_snippet_max_chars": 120,
        "citation_preview_max_chars": 120,
        "generation_provider": "fake",
        "context_budget_enabled": False,
        "evidence_pack_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**cast(Any, defaults))


def _seed_minimal_data(db: Session) -> None:
    role = Role(role_id=1, role_name="admin", description="admin")
    user = User(
        user_id=1,
        role_id=1,
        email="admin@example.com",
        display_name="Admin",
        password_hash="test",
    )
    session = ChatSession(chat_session_id=1, user_id=1, title="cache test")
    document = LogicalDocument(
        logical_document_id=1,
        owner_user_id=1,
        title="Cache Source",
        status="active",
    )
    version = DocumentVersion(
        document_version_id=10,
        logical_document_id=1,
        version_no=1,
        content_hash="a" * 64,
        status="ready",
        is_active=True,
        file_name="cache-source.md",
        mime_type="text/markdown",
        file_size_bytes=100,
        created_by=1,
    )
    chunk = DocumentChunk(
        document_chunk_id=100,
        document_version_id=10,
        chunk_index=0,
        chunk_hash="b" * 64,
        content_text="Alpha retrieval cache source text for citation reconstruction.",
        token_count=8,
        char_count=64,
        page_from=1,
        page_to=1,
        section_title="Overview",
        modality="text",
    )
    db.add_all([role, user, session, document, version, chunk])


def _add_chunk(
    db: Session,
    *,
    document_chunk_id: int,
    chunk_index: int,
    chunk_hash: str,
    content_text: str,
) -> None:
    db.add(
        DocumentChunk(
            document_chunk_id=document_chunk_id,
            document_version_id=10,
            chunk_index=chunk_index,
            chunk_hash=chunk_hash,
            content_text=content_text,
            token_count=8,
            char_count=len(content_text),
            page_from=1,
            page_to=1,
            section_title="Overview",
            modality="text",
        )
    )


def _candidate(document_chunk_id: int, score: float, order: int) -> VectorSearchCandidate:
    return VectorSearchCandidate(
        document_chunk_id=document_chunk_id,
        retrieval_score=score,
        qdrant_order=order,
        payload={},
    )


class _CountingVectorClient:
    def __init__(self, candidates: list[VectorSearchCandidate]) -> None:
        self.candidates = candidates
        self.query_vectors: list[list[float]] = []
        self.fail = False

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        del collection_name, filters
        if self.fail:
            raise RetrievalError()
        self.query_vectors.append([float(value) for value in query_vector])
        return self.candidates[:limit]


class _FixedOrderReranker:
    def __init__(self, chunk_order: Sequence[int]) -> None:
        self.chunk_order = list(chunk_order)

    def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankResult]:
        del query
        candidate_ids = {candidate.document_chunk_id for candidate in candidates}
        ordered_ids = [chunk_id for chunk_id in self.chunk_order if chunk_id in candidate_ids]
        return [
            RerankResult(
                document_chunk_id=chunk_id,
                rerank_score=round(1.0 - (index * 0.1), 6),
                rerank_order=index + 1,
            )
            for index, chunk_id in enumerate(ordered_ids)
        ]
