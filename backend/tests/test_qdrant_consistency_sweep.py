from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.job_utils import LeaseLostError, sanitize_result_json
from app.db.base import Base
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.ingest.embedding import (
    DocumentEmbeddingService,
    EmbeddingBatchConfig,
    FakeEmbeddingAdapter,
)
from app.ingest.qdrant import (
    DocumentIndexingService,
    InMemoryQdrantClient,
    QdrantCollectionConfig,
    QdrantPoint,
    QdrantVectorStore,
    point_id_for_chunk_id,
)
from app.repositories.job_repository import JobRepository
from app.workers.handlers.base import JobExecutionContext
from app.workers.handlers.qdrant_consistency_sweep_handler import (
    QdrantConsistencySweepHandler,
)

_COLLECTION = "document_chunks"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        engine.dispose()


class _NoopJobRepository:
    def assert_ownership(self, db: Session, *, job_id: int, worker_instance_id: str) -> None:
        return None


class _LeaseLostOnSecondBatchJobRepository:
    """Raise LeaseLostError on the second ``assert_ownership`` call.

    The sweep calls ``assert_ownership`` once per mutating batch, so the second
    call corresponds to the second batch's pre-mutation lease recheck.
    """

    def __init__(self) -> None:
        self.calls = 0

    def assert_ownership(self, db: Session, *, job_id: int, worker_instance_id: str) -> None:
        self.calls += 1
        if self.calls >= 2:
            raise LeaseLostError()


def _indexing_service(qdrant_client: InMemoryQdrantClient) -> DocumentIndexingService:
    return DocumentIndexingService(
        embedding_service=DocumentEmbeddingService(
            adapter=FakeEmbeddingAdapter(dimension=4),
            config=EmbeddingBatchConfig(dimension=4, batch_size=2),
        ),
        vector_store=QdrantVectorStore(
            client=qdrant_client,
            config=QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4),
            create_collection=True,
        ),
        upsert_batch_size=1,
    )


def _handler(
    session_factory: sessionmaker[Session], qdrant_client: InMemoryQdrantClient
) -> QdrantConsistencySweepHandler:
    return QdrantConsistencySweepHandler(
        session_factory=session_factory,
        job_repository=cast(JobRepository, _NoopJobRepository()),
        indexing_service=_indexing_service(qdrant_client),
    )


def _context(payload: dict[str, object] | None = None) -> JobExecutionContext:
    return JobExecutionContext(
        job_id=1,
        job_type="qdrant_consistency_sweep",
        target_type=None,
        target_id=None,
        payload=payload or {},
        worker_instance_id="worker-1",
    )


def _seed_document(
    session_factory: sessionmaker[Session],
    *,
    document_status: str,
    version_status: str,
    is_active: bool,
) -> None:
    with session_factory() as db:
        db.add(
            LogicalDocument(
                logical_document_id=1,
                owner_user_id=1,
                title="Doc",
                status=document_status,
                archived_at=datetime.now(UTC) if document_status == "archived" else None,
            )
        )
        db.add(
            DocumentVersion(
                document_version_id=10,
                logical_document_id=1,
                version_no=1,
                content_hash="a" * 64,
                status=version_status,
                is_active=is_active,
                file_name="f.txt",
                mime_type="text/plain",
                file_size_bytes=3,
                storage_key="k",
                created_by=1,
            )
        )
        db.add(
            DocumentChunk(
                document_chunk_id=100,
                document_version_id=10,
                chunk_index=0,
                chunk_hash="c" * 64,
                content_text="chunk",
            )
        )
        db.commit()


def _seed_point(
    qdrant_client: InMemoryQdrantClient, *, point_id: int, payload: dict[str, object]
) -> None:
    qdrant_client.upsert_points(
        _COLLECTION,
        [QdrantPoint(point_id=point_id, vector=[0.0, 0.0, 0.0, 0.0], payload=payload)],
    )


def _healthy_payload(
    *,
    document_chunk_id: int = 100,
    document_version_id: int = 10,
    logical_document_id: int = 1,
    modality: str = "text",
    is_active: bool = True,
) -> dict[str, object]:
    return {
        "document_chunk_id": document_chunk_id,
        "document_version_id": document_version_id,
        "logical_document_id": logical_document_id,
        "modality": modality,
        "is_active": is_active,
    }


def test_all_consistent_repairs_nothing(session_factory: sessionmaker[Session]) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_document(
        session_factory, document_status="active", version_status="ready", is_active=True
    )
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 1
    assert result.result_json["stale_found_count"] == 0
    assert result.result_json["repaired_count"] == 0
    assert result.result_json["skipped_count"] == 0
    assert sanitize_result_json(result.result_json) == result.result_json
    assert 100 in qdrant_client.points[_COLLECTION]


def test_orphaned_point_is_deleted(session_factory: sessionmaker[Session]) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    # No DB rows seeded -> chunk/version missing.
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 1
    assert result.result_json["stale_found_count"] == 1
    assert result.result_json["repaired_count"] == 1
    assert 100 not in qdrant_client.points[_COLLECTION]


def test_inactive_db_point_is_repaired(session_factory: sessionmaker[Session]) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_document(
        session_factory, document_status="active", version_status="ready", is_active=False
    )
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["stale_found_count"] == 1
    assert result.result_json["repaired_count"] == 1
    assert qdrant_client.points[_COLLECTION][100].payload["is_active"] is False


def test_archived_document_point_is_repaired(session_factory: sessionmaker[Session]) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_document(
        session_factory, document_status="archived", version_status="ready", is_active=True
    )
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["repaired_count"] == 1
    assert qdrant_client.points[_COLLECTION][100].payload["is_active"] is False


def _seed_extra_version(
    session_factory: sessionmaker[Session],
    *,
    document_version_id: int,
    version_status: str = "ready",
    is_active: bool = True,
) -> None:
    """Add another healthy logical document + version (no chunks)."""
    with session_factory() as db:
        db.add(
            LogicalDocument(
                logical_document_id=document_version_id,
                owner_user_id=1,
                title="Doc2",
                status="active",
                archived_at=None,
            )
        )
        db.add(
            DocumentVersion(
                document_version_id=document_version_id,
                logical_document_id=document_version_id,
                version_no=1,
                content_hash="b" * 64,
                status=version_status,
                is_active=is_active,
                file_name="f.txt",
                mime_type="text/plain",
                file_size_bytes=3,
                storage_key="k2",
                created_by=1,
            )
        )
        db.commit()


def test_chunk_belongs_to_different_version_is_repaired_not_deleted(
    session_factory: sessionmaker[Session],
) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    # Chunk 100 belongs to version 10, but a healthy version 11 also exists.
    _seed_document(
        session_factory, document_status="active", version_status="ready", is_active=True
    )
    _seed_extra_version(session_factory, document_version_id=11)
    # Payload claims version 11 while the chunk actually belongs to version 10:
    # corrupted metadata -> repair (inactive), not delete.
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(document_version_id=11, logical_document_id=11),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 1
    assert result.result_json["stale_found_count"] == 1
    assert result.result_json["repaired_count"] == 1
    # Repaired in place (inactive), the vector is NOT deleted.
    assert 100 in qdrant_client.points[_COLLECTION]
    assert qdrant_client.points[_COLLECTION][100].payload["is_active"] is False


def test_stale_payload_version_for_existing_chunk_is_repaired_not_deleted(
    session_factory: sessionmaker[Session],
) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_document(
        session_factory, document_status="active", version_status="ready", is_active=True
    )
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(document_version_id=999),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["stale_found_count"] == 1
    assert result.result_json["repaired_count"] == 1
    assert 100 in qdrant_client.points[_COLLECTION]
    assert qdrant_client.points[_COLLECTION][100].payload["is_active"] is False


def test_filter_payload_mismatch_is_repaired_not_treated_as_healthy(
    session_factory: sessionmaker[Session],
) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_document(
        session_factory, document_status="active", version_status="ready", is_active=True
    )
    with session_factory() as db:
        db.add(
            DocumentChunk(
                document_chunk_id=101,
                document_version_id=10,
                chunk_index=1,
                chunk_hash="d" * 64,
                content_text="chunk 2",
            )
        )
        db.commit()
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(logical_document_id=999),
    )
    _seed_point(
        qdrant_client,
        point_id=101,
        payload=_healthy_payload(document_chunk_id=101, modality="image"),
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 2
    assert result.result_json["stale_found_count"] == 2
    assert result.result_json["repaired_count"] == 2
    assert qdrant_client.points[_COLLECTION][100].payload["is_active"] is False
    assert qdrant_client.points[_COLLECTION][101].payload["is_active"] is False


def test_wrong_point_id_with_healthy_payload_is_deleted(
    session_factory: sessionmaker[Session],
) -> None:
    # Finding 3: point ids are deterministic. A point stored under the wrong id
    # but carrying a healthy chunk/version payload is a rogue/impersonating point
    # and must be DELETED, while the legitimate point at the deterministic id with
    # the same payload stays untouched.
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_document(
        session_factory, document_status="active", version_status="ready", is_active=True
    )
    healthy_payload: dict[str, object] = {
        "document_chunk_id": 100,
        "document_version_id": 10,
        "logical_document_id": 1,
        "modality": "text",
        "is_active": True,
    }
    legit_point_id = point_id_for_chunk_id(100)
    rogue_point_id = legit_point_id + 999
    # Legitimate vector at the deterministic id.
    _seed_point(qdrant_client, point_id=legit_point_id, payload=dict(healthy_payload))
    # Rogue vector with identical healthy payload at the wrong id.
    _seed_point(qdrant_client, point_id=rogue_point_id, payload=dict(healthy_payload))

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 2
    assert result.result_json["stale_found_count"] == 1
    assert result.result_json["repaired_count"] == 1
    # The rogue point at the wrong id is deleted; the legitimate point survives.
    assert rogue_point_id not in qdrant_client.points[_COLLECTION]
    assert legit_point_id in qdrant_client.points[_COLLECTION]


def test_max_points_cap_is_respected(session_factory: sessionmaker[Session]) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    for point_id in range(1, 6):
        _seed_point(
            qdrant_client,
            point_id=point_id,
            payload={
                "document_chunk_id": point_id,
                "document_version_id": 10,
                "is_active": True,
            },
        )

    result = _handler(session_factory, qdrant_client).handle(
        _context({"batch_size": 2, "max_points": 3})
    )

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 3


def test_lease_lost_midway_stops_before_second_batch_repairs(
    session_factory: sessionmaker[Session],
) -> None:
    # Finding 3: a sweep whose lease is lost partway (assert_ownership raises on
    # the second batch) must stop before applying the second batch's repairs and
    # surface LeaseLostError the same way other handlers do (re-raised by handle).
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    # Two orphaned points (no DB rows) -> each batch has a delete to apply.
    _seed_point(
        qdrant_client,
        point_id=100,
        payload=_healthy_payload(),
    )
    _seed_point(
        qdrant_client,
        point_id=200,
        payload=_healthy_payload(document_chunk_id=200, document_version_id=20),
    )

    job_repository = _LeaseLostOnSecondBatchJobRepository()
    handler = QdrantConsistencySweepHandler(
        session_factory=session_factory,
        job_repository=cast(JobRepository, job_repository),
        indexing_service=_indexing_service(qdrant_client),
    )

    # batch_size=1 -> one point per batch, so the second batch's lease recheck
    # raises before deleting the second point.
    with pytest.raises(LeaseLostError):
        handler.handle(_context({"batch_size": 1}))

    assert job_repository.calls == 2
    # First batch's orphan was deleted; second batch's orphan must remain.
    assert 100 not in qdrant_client.points[_COLLECTION]
    assert 200 in qdrant_client.points[_COLLECTION]


def test_malformed_payload_param_is_validation_failure(
    session_factory: sessionmaker[Session],
) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))

    result = _handler(session_factory, qdrant_client).handle(_context({"batch_size": 0}))

    assert result.status == "failed"
    assert result.error_code == "validation_error"


def test_malformed_point_payload_is_skipped(session_factory: sessionmaker[Session]) -> None:
    qdrant_client = InMemoryQdrantClient()
    qdrant_client.create_collection(QdrantCollectionConfig(name=_COLLECTION, vector_dimension=4))
    _seed_point(
        qdrant_client,
        point_id=100,
        payload={"is_active": True},  # missing identity fields
    )

    result = _handler(session_factory, qdrant_client).handle(_context())

    assert result.status == "succeeded"
    assert result.result_json["scanned_count"] == 1
    assert result.result_json["skipped_count"] == 1
    assert result.result_json["repaired_count"] == 0
    assert 100 in qdrant_client.points[_COLLECTION]
