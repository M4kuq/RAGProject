from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.rag.fake_pipeline import search_chunks


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


def test_fake_pipeline_search_uses_only_active_ready_versions(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db:
        db.add_all(
            [
                LogicalDocument(logical_document_id=1, owner_user_id=1, title="Active"),
                LogicalDocument(logical_document_id=2, owner_user_id=1, title="Inactive"),
                LogicalDocument(logical_document_id=3, owner_user_id=1, title="Failed"),
                LogicalDocument(
                    logical_document_id=4,
                    owner_user_id=1,
                    title="Archived",
                    status="archived",
                    archived_at=datetime.now(UTC),
                ),
            ]
        )
        db.add_all(
            [
                _version(10, 1, "ready", True, "a"),
                _version(20, 2, "ready", False, "b"),
                _version(30, 3, "failed", False, "c", error_code="embedding_failed"),
                _version(40, 4, "ready", True, "d"),
            ]
        )
        db.add_all(
            [
                _chunk(100, 10, "target active ready"),
                _chunk(200, 20, "target inactive ready"),
                _chunk(300, 30, "target failed"),
                _chunk(400, 40, "target archived"),
            ]
        )
        db.commit()

        hits = search_chunks(db, "target", limit=10)

    assert [chunk.document_chunk_id for chunk, _score in hits] == [100]


def _version(
    document_version_id: int,
    logical_document_id: int,
    status: str,
    is_active: bool,
    hash_prefix: str,
    *,
    error_code: str | None = None,
) -> DocumentVersion:
    return DocumentVersion(
        document_version_id=document_version_id,
        logical_document_id=logical_document_id,
        version_no=1,
        content_hash=hash_prefix * 64,
        status=status,
        is_active=is_active,
        error_code=error_code,
        file_name=f"{hash_prefix}.txt",
        mime_type="text/plain",
        file_size_bytes=10,
        storage_key=hash_prefix,
        created_by=1,
    )


def _chunk(document_chunk_id: int, document_version_id: int, text: str) -> DocumentChunk:
    return DocumentChunk(
        document_chunk_id=document_chunk_id,
        document_version_id=document_version_id,
        chunk_index=0,
        chunk_hash=f"{document_chunk_id:064x}",
        content_text=text,
    )
