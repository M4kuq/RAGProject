from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.graph_models import GraphEntity, GraphEntityMention, GraphRelation
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument, Role, User
from app.rag.retrieval import RetrievalFilters
from app.repositories.graph_retrieval_repository import GraphRetrievalRepository


@dataclass(frozen=True)
class ReviewGraphSeed:
    document_version_id: int
    fastapi_entity_id: int
    qdrant_entity_id: int
    chunk_ids: list[int]
    qdrant_relation_id: int


@pytest.fixture
def graph_review_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        engine.dispose()


def test_graph_entity_lookup_escapes_like_terms_before_row_limit(
    graph_review_session_factory: sessionmaker[Session],
) -> None:
    with graph_review_session_factory() as db:
        exact = GraphEntity(
            canonical_name="foo_bar",
            entity_type="technology",
            aliases_json=[],
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        noisy_entities = [
            GraphEntity(
                canonical_name=f"foo{index}bar",
                entity_type="technology",
                aliases_json=[],
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            for index in range(120)
        ]
        db.add_all([exact, *noisy_entities])
        db.commit()

        results = GraphRetrievalRepository().lookup_entities(
            db,
            query_terms=("foo_bar",),
            limit=1,
            min_match_score=0.5,
        )

        assert [result.entity.graph_entity_id for result in results] == [exact.graph_entity_id]


def test_graph_relation_lookup_does_not_starve_frontier_entity_behind_hub(
    graph_review_session_factory: sessionmaker[Session],
) -> None:
    with graph_review_session_factory() as db:
        seed = _seed_review_graph(db)
        rows = GraphRetrievalRepository().list_relations_for_entity_ids(
            db,
            entity_ids={seed.fastapi_entity_id, seed.qdrant_entity_id},
            max_relations_per_entity=1,
            filters=RetrievalFilters(),
        )

        relation_ids = {row.relation.graph_relation_id for row in rows}
        assert seed.qdrant_relation_id in relation_ids


def test_graph_mention_lookup_allocates_fallback_budget_per_entity(
    graph_review_session_factory: sessionmaker[Session],
) -> None:
    with graph_review_session_factory() as db:
        seed = _seed_review_graph(db)
        repository = GraphRetrievalRepository()

        rows = repository.list_mentions_for_entity_ids(
            db,
            entity_ids={seed.fastapi_entity_id, seed.qdrant_entity_id},
            filters=RetrievalFilters(),
            max_source_chunks=2,
        )

        assert len(rows) == 2
        assert {row.graph_entity_id for row in rows} == {
            seed.fastapi_entity_id,
            seed.qdrant_entity_id,
        }


def _seed_review_graph(db: Session) -> ReviewGraphSeed:
    role = Role(
        role_name=f"graph-review-{uuid.uuid4().hex[:8]}",
        description="Graph review edge cases",
    )
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email=f"graph-review-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Graph Review",
        status="active",
    )
    db.add(user)
    db.flush()
    logical_document = LogicalDocument(
        owner_user_id=user.user_id,
        title="Graph Review",
        status="active",
    )
    db.add(logical_document)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical_document.logical_document_id,
        version_no=1,
        content_hash="1".zfill(64),
        status="ready",
        is_active=True,
        file_name="graph-review.txt",
        mime_type="text/plain",
        file_size_bytes=100,
        created_by=user.user_id,
    )
    db.add(version)
    db.flush()
    chunks = [
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=index,
            chunk_hash=str(index + 1).zfill(64),
            content_text=f"Graph review chunk {index}",
            char_count=20,
            modality="text",
        )
        for index in range(4)
    ]
    fastapi = GraphEntity(
        canonical_name="FastAPI",
        entity_type="technology",
        aliases_json=[],
    )
    qdrant = GraphEntity(
        canonical_name="Qdrant",
        entity_type="technology",
        aliases_json=[],
    )
    hub_target = GraphEntity(
        canonical_name="HubTarget",
        entity_type="technology",
        aliases_json=[],
    )
    qdrant_target = GraphEntity(
        canonical_name="QdrantTarget",
        entity_type="technology",
        aliases_json=[],
    )
    db.add_all([*chunks, fastapi, qdrant, hub_target, qdrant_target])
    db.flush()
    db.add_all(
        [
            GraphEntityMention(
                graph_entity_id=fastapi.graph_entity_id,
                document_chunk_id=chunks[index].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash=str(index + 1) * 64,
                confidence=Decimal("0.90000"),
            )
            for index in range(3)
        ]
    )
    db.add(
        GraphEntityMention(
            graph_entity_id=qdrant.graph_entity_id,
            document_chunk_id=chunks[3].document_chunk_id,
            document_version_id=version.document_version_id,
            mention_text_hash="4" * 64,
            confidence=Decimal("0.90000"),
        )
    )
    qdrant_relation = GraphRelation(
        source_entity_id=qdrant.graph_entity_id,
        target_entity_id=qdrant_target.graph_entity_id,
        relation_type="stores",
        relation_label="stores",
        confidence=Decimal("0.10000"),
        source_document_chunk_id=chunks[3].document_chunk_id,
        evidence_text_hash="5" * 64,
        metadata_json={"rule_id": "test"},
    )
    db.add_all(
        [
            GraphRelation(
                source_entity_id=fastapi.graph_entity_id,
                target_entity_id=hub_target.graph_entity_id,
                relation_type="uses",
                relation_label="uses",
                confidence=Decimal("0.99000"),
                source_document_chunk_id=chunks[0].document_chunk_id,
                evidence_text_hash="6" * 64,
                metadata_json={"rule_id": "test"},
            ),
            qdrant_relation,
            GraphRelation(
                source_entity_id=fastapi.graph_entity_id,
                target_entity_id=qdrant_target.graph_entity_id,
                relation_type="feeds",
                relation_label="feeds",
                confidence=Decimal("0.50000"),
                source_document_chunk_id=chunks[1].document_chunk_id,
                evidence_text_hash="7" * 64,
                metadata_json={"rule_id": "test"},
            ),
        ]
    )
    db.commit()
    return ReviewGraphSeed(
        document_version_id=version.document_version_id,
        fastapi_entity_id=fastapi.graph_entity_id,
        qdrant_entity_id=qdrant.graph_entity_id,
        chunk_ids=[chunk.document_chunk_id for chunk in chunks],
        qdrant_relation_id=qdrant_relation.graph_relation_id,
    )
