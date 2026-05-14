from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from app.core.config import Settings


class EmbeddingAdapterError(RuntimeError):
    def __init__(
        self,
        error_code: str = "embedding_failed",
        message: str = "Embedding generation failed.",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


class EmbeddingAdapter(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...


class ChunkEmbeddingRepository(Protocol):
    def list_chunks_for_embedding(
        self, db: object, *, document_version_id: int
    ) -> list[object]: ...


@dataclass(frozen=True)
class EmbeddingBatchConfig:
    dimension: int
    batch_size: int

    def __post_init__(self) -> None:
        if self.dimension < 1:
            raise ValueError("embedding dimension must be positive")
        if self.batch_size < 1:
            raise ValueError("embedding batch size must be positive")


class FakeEmbeddingAdapter:
    def __init__(self, *, dimension: int, model_name: str = "fake-ci-embedding") -> None:
        if dimension < 1:
            raise ValueError("fake embedding dimension must be positive")
        self._dimension = dimension
        self.model_name = model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        _validate_texts(texts)
        return [_fake_unit_vector(text, self._dimension) for text in texts]


class LocalEmbeddingAdapter:
    def __init__(self, *, model_name: str, dimension: int) -> None:
        if dimension < 1:
            raise ValueError("local embedding dimension must be positive")
        self.model_name = model_name
        self._dimension = dimension
        self._model: object | None = None

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        _validate_texts(texts)
        model = self._load_model()
        try:
            encoded = model.encode(  # type: ignore[attr-defined]
                list(texts),
                normalize_embeddings=True,
            )
        except Exception as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
        vectors = _to_vector_list(encoded)
        _validate_vectors(vectors, expected_count=len(texts), dimension=self._dimension)
        return vectors

    def _load_model(self) -> object:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
        self._model = SentenceTransformer(self.model_name)
        return self._model


class DocumentEmbeddingService:
    def __init__(self, *, adapter: EmbeddingAdapter, config: EmbeddingBatchConfig) -> None:
        self.adapter = adapter
        self.config = config
        if adapter.dimension != config.dimension:
            raise EmbeddingAdapterError("embedding_dimension_mismatch")

    def embed_chunks(self, chunks: Sequence[object]) -> list[list[float]]:
        if not chunks:
            raise EmbeddingAdapterError("embedding_empty_result")
        texts = [str(getattr(chunk, "content_text", "")) for chunk in chunks]
        _validate_texts(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.config.batch_size):
            batch = texts[start : start + self.config.batch_size]
            try:
                batch_vectors = self.adapter.embed_texts(batch)
            except EmbeddingAdapterError:
                raise
            except Exception as exc:
                raise EmbeddingAdapterError("embedding_failed") from exc
            _validate_vectors(
                batch_vectors,
                expected_count=len(batch),
                dimension=self.config.dimension,
            )
            vectors.extend(batch_vectors)
        _validate_vectors(vectors, expected_count=len(texts), dimension=self.config.dimension)
        if not vectors:
            raise EmbeddingAdapterError("embedding_empty_result")
        return vectors

    def embed_document_version_chunks(
        self,
        db: object,
        *,
        repository: ChunkEmbeddingRepository,
        document_version_id: int,
    ) -> tuple[list[object], list[list[float]]]:
        chunks = repository.list_chunks_for_embedding(db, document_version_id=document_version_id)
        return chunks, self.embed_chunks(chunks)


def create_embedding_adapter(settings: Settings) -> EmbeddingAdapter:
    provider = settings.embedding_provider.lower()
    dimension = settings.effective_embedding_dimension
    if provider == "fake":
        return FakeEmbeddingAdapter(dimension=dimension, model_name=settings.embedding_model)
    if provider == "local":
        return LocalEmbeddingAdapter(model_name=settings.embedding_model, dimension=dimension)
    raise EmbeddingAdapterError("embedding_failed")


def create_document_embedding_service(settings: Settings) -> DocumentEmbeddingService:
    return DocumentEmbeddingService(
        adapter=create_embedding_adapter(settings),
        config=EmbeddingBatchConfig(
            dimension=settings.effective_embedding_dimension,
            batch_size=settings.embedding_batch_size,
        ),
    )


def _validate_texts(texts: Sequence[str]) -> None:
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingAdapterError("embedding_empty_result")


def _validate_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    dimension: int,
) -> None:
    if len(vectors) != expected_count:
        raise EmbeddingAdapterError("embedding_empty_result")
    for vector in vectors:
        if len(vector) != dimension:
            raise EmbeddingAdapterError("embedding_dimension_mismatch")
        if not all(isinstance(value, (float, int)) and math.isfinite(value) for value in vector):
            raise EmbeddingAdapterError("embedding_failed")


def _fake_unit_vector(text: str, dimension: int) -> list[float]:
    values: list[float] = []
    counter = 0
    seed = text.encode("utf-8")
    while len(values) < dimension:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
        for offset in range(0, len(digest), 4):
            if len(values) >= dimension:
                break
            raw = int.from_bytes(digest[offset : offset + 4], "big")
            values.append((raw / 0xFFFFFFFF) * 2.0 - 1.0)
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _to_vector_list(value: object) -> list[list[float]]:
    if hasattr(value, "tolist"):
        value = value.tolist()  # type: ignore[no-untyped-call]
    if not isinstance(value, Sequence):
        raise EmbeddingAdapterError("embedding_failed")
    vectors: list[list[float]] = []
    for row in value:
        if hasattr(row, "tolist"):
            row = row.tolist()  # type: ignore[no-untyped-call]
        if not isinstance(row, Sequence) or isinstance(row, (bytes, bytearray, str)):
            raise EmbeddingAdapterError("embedding_failed")
        vectors.append([float(cast(Any, item)) for item in cast(Sequence[object], row)])
    return vectors
