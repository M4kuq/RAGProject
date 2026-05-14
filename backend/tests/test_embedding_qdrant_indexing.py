from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.ingest.embedding import (
    DocumentEmbeddingService,
    EmbeddingAdapterError,
    EmbeddingBatchConfig,
    FakeEmbeddingAdapter,
    LocalEmbeddingAdapter,
)
from app.ingest.qdrant import (
    DocumentIndexingService,
    InMemoryQdrantClient,
    QdrantCollectionConfig,
    QdrantPoint,
    QdrantStoreError,
    QdrantVectorStore,
    build_qdrant_payload,
    point_id_for_chunk_id,
)


def test_fake_embedding_is_deterministic_and_dimensioned() -> None:
    adapter = FakeEmbeddingAdapter(dimension=6)
    second_adapter = FakeEmbeddingAdapter(dimension=6)

    first = adapter.embed_texts(["alpha"])[0]
    second = adapter.embed_texts(["alpha"])[0]
    third = second_adapter.embed_texts(["alpha"])[0]
    different = adapter.embed_texts(["beta"])[0]

    assert first == second
    assert first == third
    assert first != different
    assert len(first) == 6
    assert math.isclose(sum(value * value for value in first), 1.0)


def test_embedding_service_batches_and_rejects_invalid_results() -> None:
    chunks = [_Chunk(1, "alpha"), _Chunk(2, "beta"), _Chunk(3, "gamma")]
    service = DocumentEmbeddingService(
        adapter=FakeEmbeddingAdapter(dimension=4),
        config=EmbeddingBatchConfig(dimension=4, batch_size=2),
    )

    vectors = service.embed_chunks(chunks)

    assert len(vectors) == 3
    assert all(len(vector) == 4 for vector in vectors)
    with pytest.raises(EmbeddingAdapterError) as empty_exc:
        service.embed_chunks([])
    assert empty_exc.value.error_code == "embedding_empty_result"
    mismatch = DocumentEmbeddingService(
        adapter=cast(FakeEmbeddingAdapter, _MismatchAdapter()),
        config=EmbeddingBatchConfig(dimension=4, batch_size=2),
    )
    with pytest.raises(EmbeddingAdapterError) as exc:
        mismatch.embed_chunks([_Chunk(1, "alpha")])
    assert exc.value.error_code == "embedding_dimension_mismatch"


def test_embedding_service_can_fetch_chunks_by_document_version() -> None:
    chunks = [_Chunk(1, "alpha"), _Chunk(2, "beta")]
    repository = _ChunkRepository(chunks)
    service = DocumentEmbeddingService(
        adapter=FakeEmbeddingAdapter(dimension=4),
        config=EmbeddingBatchConfig(dimension=4, batch_size=2),
    )

    loaded_chunks, vectors = service.embed_document_version_chunks(
        object(),
        repository=repository,
        document_version_id=20,
    )

    assert loaded_chunks == chunks
    assert repository.document_version_id == 20
    assert len(vectors) == 2


def test_local_embedding_adapter_import_failure_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> object:
        if name == "sentence_transformers":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    adapter = LocalEmbeddingAdapter(model_name="BAAI/bge-m3", dimension=1024)

    with pytest.raises(EmbeddingAdapterError) as exc:
        adapter.embed_texts(["alpha"])

    assert exc.value.error_code == "embedding_failed"


def test_qdrant_point_id_and_payload_are_deterministic_and_safe() -> None:
    logical_document = _LogicalDocument(logical_document_id=10, status="active")
    version = _Version(
        document_version_id=20,
        is_active=False,
        file_name=r"C:\unsafe\guide.md",
        content_hash="a" * 64,
    )
    chunk = _Chunk(30, "raw chunk text")

    payload = build_qdrant_payload(
        logical_document=logical_document,
        document_version=version,
        chunk=chunk,
        document_version_status="ready",
    )

    assert point_id_for_chunk_id(30) == 30
    assert payload["document_chunk_id"] == 30
    assert payload["document_version_status"] == "ready"
    assert "content_text" not in payload
    assert "source_label" not in payload
    assert "file_name" not in payload
    assert "content_hash" not in payload
    assert "section_title" not in payload
    assert "raw chunk text" not in str(payload)


def test_qdrant_collection_dimension_mismatch_is_detected() -> None:
    client = InMemoryQdrantClient()
    client.collections["document_chunks"] = 3
    store = QdrantVectorStore(
        client=client,
        config=QdrantCollectionConfig(name="document_chunks", vector_dimension=4),
        create_collection=True,
    )

    with pytest.raises(QdrantStoreError) as exc:
        store.ensure_collection()

    assert exc.value.error_code == "qdrant_collection_dimension_mismatch"


def test_qdrant_upsert_and_cleanup_fake_success_and_failure() -> None:
    client = InMemoryQdrantClient()
    store = QdrantVectorStore(
        client=client,
        config=QdrantCollectionConfig(name="document_chunks", vector_dimension=4),
        create_collection=True,
    )
    store.ensure_collection()
    point = QdrantPoint(
        point_id=1,
        vector=[0.1, 0.2, 0.3, 0.4],
        payload={"document_version_id": 2, "document_chunk_id": 1},
    )

    store.upsert([point], batch_size=1)
    assert client.points["document_chunks"][1] == point
    store.cleanup(document_version_id=2, point_ids=[1])
    assert client.points["document_chunks"] == {}
    store.upsert([point], batch_size=1)
    store.cleanup(document_version_id=2, point_ids=[])
    assert client.points["document_chunks"] == {}
    store.upsert([point], batch_size=1)
    store.sync_payload(
        document_version_id=2,
        payload={
            "is_active": True,
            "logical_document_status": "active",
            "document_version_status": "ready",
        },
    )
    assert client.points["document_chunks"][1].payload["is_active"] is True

    client.fail_upsert = True
    with pytest.raises(QdrantStoreError) as upsert_exc:
        store.upsert([point], batch_size=1)
    assert upsert_exc.value.error_code == "qdrant_upsert_failed"

    client.fail_upsert = False
    client.fail_delete = True
    with pytest.raises(QdrantStoreError) as cleanup_exc:
        store.cleanup(document_version_id=2, point_ids=[1])
    assert cleanup_exc.value.error_code == "qdrant_cleanup_failed"


def test_document_indexing_service_upserts_points_with_fake_embedding() -> None:
    client = InMemoryQdrantClient()
    service = DocumentIndexingService(
        embedding_service=DocumentEmbeddingService(
            adapter=FakeEmbeddingAdapter(dimension=4),
            config=EmbeddingBatchConfig(dimension=4, batch_size=2),
        ),
        vector_store=QdrantVectorStore(
            client=client,
            config=QdrantCollectionConfig(name="document_chunks", vector_dimension=4),
            create_collection=True,
        ),
        upsert_batch_size=1,
    )

    result = service.index_chunks(
        logical_document=_LogicalDocument(logical_document_id=10, status="active"),
        document_version=_Version(
            document_version_id=20,
            is_active=False,
            file_name="guide.md",
            content_hash="a" * 64,
        ),
        chunks=[_Chunk(1, "alpha"), _Chunk(2, "beta")],
    )

    assert result.indexed_count == 2
    assert sorted(client.points["document_chunks"]) == [1, 2]


def test_embedding_and_qdrant_settings_validation() -> None:
    settings = Settings(
        embedding_provider="fake",
        embedding_fake_dimension=5,
        embedding_vector_dimension=1024,
        embedding_batch_size=2,
        qdrant_distance="cosine",
    )

    assert settings.effective_embedding_dimension == 5
    assert settings.qdrant_distance == "Cosine"
    with pytest.raises(ValueError):
        Settings(embedding_provider="remote")
    with pytest.raises(ValueError):
        Settings(qdrant_distance="manhattan")
    with pytest.raises(ValueError):
        Settings(embedding_batch_size=0)
    with pytest.raises(ValueError):
        Settings(qdrant_upsert_batch_size=0)
    with pytest.raises(ValueError):
        Settings(qdrant_timeout_seconds=0)


class _MismatchAdapter:
    @property
    def dimension(self) -> int:
        return 4

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 2.0]]


class _ChunkRepository:
    def __init__(self, chunks: Sequence[object]) -> None:
        self.chunks = chunks
        self.document_version_id: int | None = None

    def list_chunks_for_embedding(self, db: object, *, document_version_id: int) -> list[object]:
        self.document_version_id = document_version_id
        return list(self.chunks)


@dataclass(frozen=True)
class _LogicalDocument:
    logical_document_id: int
    status: str


@dataclass(frozen=True)
class _Version:
    document_version_id: int
    is_active: bool
    file_name: str
    content_hash: str


@dataclass(frozen=True)
class _Chunk:
    document_chunk_id: int
    content_text: str
    document_version_id: int = 20
    chunk_index: int = 0
    chunk_hash: str = "b" * 64
    token_count: int | None = 2
    char_count: int | None = 10
    page_from: int | None = 1
    page_to: int | None = 1
    section_title: str | None = "Intro"
    modality: str = "text"
    created_at: datetime = datetime(2026, 5, 13, tzinfo=UTC)
