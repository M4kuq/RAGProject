from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphRelation,
    GraphRetrievalPath,
)
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    User,
)
from app.rag.graph_retrieval import (
    GRAPH_PATH_SCHEMA_VERSION,
    GRAPH_SCORE_SCHEMA_VERSION,
    GraphRetrievalSettings,
    GraphRetrievalStrategy,
    graph_query_signal_score,
)
from app.rag.retrieval import RetrievalFilters
from app.repositories.graph_retrieval_repository import GraphRetrievalRepository


@pytest.fixture
def graph_retrieval_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_graph_retrieval_finds_bounded_paths_and_safe_scores(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        chunk_ids = _seed_graph(db)
        strategy = GraphRetrievalStrategy()

        result = strategy.search(
            db,
            query="How does FastAPI use PostgreSQL in the RAGProject architecture?",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(
                enabled=True,
                max_start_entities=5,
                max_depth=2,
                max_paths=8,
                max_relations_per_entity=2,
                max_source_chunks=10,
                min_entity_match_score=0.2,
            ),
        )

        assert result.entity_lookup_count >= 2
        assert result.relation_count <= 6
        assert result.path_count <= 8
        assert result.source_candidate_count >= 1
        assert result.graph_candidates[0].document_chunk_id in chunk_ids
        assert result.graph_candidates[0].payload["retrieval_source"] == "graph"
        assert any(
            path.depth == 2
            for candidate in result.graph_candidates
            for path in candidate.graph_path_candidates
        )
        assert (
            result.graph_candidates[0].score_breakdown_json["schema_version"]
            == GRAPH_SCORE_SCHEMA_VERSION
        )
        assert result.graph_candidates[0].score_breakdown_json["retrieval_source"] == "graph"
        assert result.graph_candidates[0].score_breakdown_json["path_depth"] <= 2
        serialized = str(result).lower()
        assert "raw chunk text" not in serialized
        assert "secret" not in serialized
        assert "full_context" not in serialized


def test_graph_retrieval_returns_no_context_when_disabled_or_unmatched(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        _seed_graph(db)
        strategy = GraphRetrievalStrategy()

        disabled = strategy.search(
            db,
            query="FastAPI PostgreSQL",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=False),
        )
        unmatched = strategy.search(
            db,
            query="unmatched entity name",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=True),
        )

        assert disabled.no_context is True
        assert disabled.reason_codes == ("graph_disabled",)
        assert unmatched.no_context is True
        assert unmatched.reason_codes == ("no_entity_matches",)


def test_graph_retrieval_path_records_are_safe_and_link_to_retrieval_items(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        _seed_graph(db)
        strategy = GraphRetrievalStrategy()
        repository = GraphRetrievalRepository()
        result = strategy.search(
            db,
            query="FastAPI uses PostgreSQL",
            top_k=2,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=True, min_entity_match_score=0.2),
        )
        assert result.graph_candidates
        run = RetrievalRun(status="running", top_k=2, strategy_type="dense")
        db.add(run)
        db.flush()
        for rank, candidate in enumerate(result.graph_candidates, start=1):
            db.add(
                RetrievalRunItem(
                    retrieval_run_id=run.retrieval_run_id,
                    document_chunk_id=candidate.document_chunk_id,
                    retrieval_score=Decimal(str(candidate.retrieval_score)),
                    rank_order=rank,
                    selected_flag=True,
                    payload_snapshot=candidate.payload,
                    retrieval_source="dense",
                    score_breakdown_json=candidate.score_breakdown_json,
                )
            )
        db.flush()

        saved = repository.save_graph_retrieval_paths(
            db,
            retrieval_run_id=run.retrieval_run_id,
            paths=strategy.path_records(
                retrieval_run_id=run.retrieval_run_id,
                candidates=result.graph_candidates,
            ),
        )
        db.commit()

        assert saved
        stored = db.scalars(select(GraphRetrievalPath)).all()
        assert len(stored) == len(saved)
        assert stored[0].path_json["schema_version"] == GRAPH_PATH_SCHEMA_VERSION
        assert stored[0].path_json["strategy_type"] == "graph"
        assert stored[0].source_chunk_ids_json
        payload_dump = str(stored[0].path_json).lower()
        assert "raw chunk" not in payload_dump
        assert "full context" not in payload_dump
        assert "prompt" not in payload_dump


def test_graph_query_signal_score_detects_relation_queries() -> None:
    assert graph_query_signal_score("How does FastAPI depend on PostgreSQL?") > 0.5
    assert graph_query_signal_score("simple keyword") < 0.5


def _seed_graph(db: Session) -> set[int]:
    role = Role(role_name=f"graph-role-{uuid.uuid4().hex[:8]}", description="Graph retrieval")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email=f"graph-retrieval-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Graph Retrieval",
        status="active",
    )
    db.add(user)
    db.flush()
    logical = LogicalDocument(owner_user_id=user.user_id, title="Graph Retrieval", status="active")
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash="1".zfill(64),
        status="ready",
        is_active=True,
        file_name="graph-retrieval.txt",
        mime_type="text/plain",
        file_size_bytes=100,
        created_by=user.user_id,
    )
    db.add(version)
    db.flush()
    chunks = [
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=0,
            chunk_hash="a" * 64,
            content_text="FastAPI uses PostgreSQL for RAGProject metadata.",
            char_count=49,
            modality="text",
        ),
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=1,
            chunk_hash="b" * 64,
            content_text="RAGProject connects FastAPI and Qdrant for retrieval.",
            char_count=54,
            modality="text",
        ),
    ]
    db.add_all(chunks)
    db.flush()
    entities = {
        "FastAPI": GraphEntity(canonical_name="FastAPI", entity_type="technology", aliases_json=[]),
        "PostgreSQL": GraphEntity(canonical_name="PostgreSQL", entity_type="technology", aliases_json=[]),
        "RAGProject": GraphEntity(canonical_name="RAGProject", entity_type="artifact", aliases_json=[]),
        "Qdrant": GraphEntity(canonical_name="Qdrant", entity_type="technology", aliases_json=[]),
    }
    db.add_all(entities.values())
    db.flush()
    db.add_all(
        [
            GraphEntityMention(
                graph_entity_id=entities["FastAPI"].graph_entity_id,
                document_chunk_id=chunks[0].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash="c" * 64,
                confidence=Decimal("0.90000"),
            ),
            GraphEntityMention(
                graph_entity_id=entities["PostgreSQL"].graph_entity_id,
                document_chunk_id=chunks[0].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash="d" * 64,
                confidence=Decimal("0.90000"),
            ),
            GraphEntityMention(
                graph_entity_id=entities["Qdrant"].graph_entity_id,
                document_chunk_id=chunks[1].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash="e" * 64,
                confidence=Decimal("0.90000"),
            ),
        ]
    )
    db.add_all(
        [
            GraphRelation(
                source_entity_id=entities["FastAPI"].graph_entity_id,
                target_entity_id=entities["PostgreSQL"].graph_entity_id,
                relation_type="uses",
                relation_label="uses",
                confidence=Decimal("0.85000"),
                source_document_chunk_id=chunks[0].document_chunk_id,
                evidence_text_hash="f" * 64,
                metadata_json={"rule_id": "test"},
            ),
            GraphRelation(
                source_entity_id=entities["PostgreSQL"].graph_entity_id,
                target_entity_id=entities["Qdrant"].graph_entity_id,
                relation_type="feeds",
                relation_label="feeds",
                confidence=Decimal("0.80000"),
                source_document_chunk_id=chunks[1].document_chunk_id,
                evidence_text_hash="9" * 64,
                metadata_json={"rule_id": "test"},
            ),
            GraphRelation(
                source_entity_id=entities["RAGProject"].graph_entity_id,
                target_entity_id=entities["Qdrant"].graph_entity_id,
                relation_type="uses",
                relation_label="uses",
                confidence=Decimal("0.75000"),
                source_document_chunk_id=chunks[1].document_chunk_id,
                evidence_text_hash="0" * 64,
                metadata_json={"rule_id": "test"},
            ),
        ]
    )
    db.commit()
    return {chunk.document_chunk_id for chunk in chunks}
