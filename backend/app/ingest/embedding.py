from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

import httpx

from app.core.config import Settings

TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "for",
    "how",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "which",
    "why",
    "with",
}
DEMO_FEATURE_BUCKETS = {
    "phase1": 0,
    "postgresql": 1,
    "postgres": 1,
    "qdrant": 1,
    "vector": 2,
    "database": 2,
    "db": 2,
    "rag": 3,
    "retrieval": 3,
    "search": 3,
    "fastapi": 3,
    "react": 3,
    "docker": 3,
    "compose": 3,
    "backend": 3,
    "frontend": 3,
    "worker": 3,
    "citation": 4,
    "citations": 4,
    "cited": 4,
    "confidence": 4,
    "groundedness": 4,
    "mcp": 5,
    "stdio": 5,
    "local": 5,
    "ci": 6,
    "fake": 6,
    "deterministic": 6,
    "qwen": 7,
    "qwen2.5-vl": 7,
    "qwen3": 7,
    "deepseek-r1": 7,
    "kimi": 7,
    "gpt-3": 7,
    "instructgpt": 7,
    "attention": 7,
    "transformer": 7,
    "self-rag": 7,
    "graphrag": 7,
    "flashattention": 7,
    "vllm": 7,
    "code": 7,
    "benchmark": 7,
}


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
            vectors = _to_vector_list(encoded)
        except Exception as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
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


class LMStudioEmbeddingAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model_name: str,
        dimension: int,
        timeout_seconds: float,
    ) -> None:
        if dimension < 1:
            raise ValueError("lmstudio embedding dimension must be positive")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model_name = model_name
        self._dimension = dimension
        self.timeout_seconds = timeout_seconds

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        _validate_texts(texts)
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model_name, "input": list(texts)},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
        if response.status_code >= 400:
            raise EmbeddingAdapterError("embedding_failed")
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
        vectors = _extract_lmstudio_embeddings(payload, expected_count=len(texts))
        _validate_vectors(vectors, expected_count=len(texts), dimension=self._dimension)
        return vectors


class DocumentEmbeddingService:
    def __init__(self, *, adapter: EmbeddingAdapter, config: EmbeddingBatchConfig) -> None:
        self.adapter = adapter
        self.config = config
        if adapter.dimension != config.dimension:
            raise EmbeddingAdapterError("embedding_dimension_mismatch")

    def embed_chunks(self, chunks: Sequence[object]) -> list[list[float]]:
        if not chunks:
            raise EmbeddingAdapterError("embedding_empty_result")
        texts = [_chunk_text(chunk) for chunk in chunks]
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
    if provider == "lmstudio":
        return LMStudioEmbeddingAdapter(
            api_key=settings.lmstudio_api_key,
            base_url=settings.lmstudio_base_url,
            model_name=settings.embedding_model,
            dimension=dimension,
            timeout_seconds=settings.lmstudio_timeout_seconds,
        )
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


def _chunk_text(chunk: object) -> str:
    text = getattr(chunk, "content_text", None)
    if not isinstance(text, str):
        raise EmbeddingAdapterError("embedding_empty_result")
    return text


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
    values = [0.0] * dimension
    expanded_text = _expand_demo_terms(text.lower())
    tokens = [token for token in TOKEN_RE.findall(expanded_text) if token not in STOPWORDS]
    if not tokens:
        tokens = [text.lower()]
    for token in tokens:
        if token in DEMO_FEATURE_BUCKETS:
            index = DEMO_FEATURE_BUCKETS[token] % dimension
            weight = 3.0
        else:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimension
            weight = 0.02
        values[index] += weight
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _expand_demo_terms(text: str) -> str:
    extra_terms: list[str] = []
    if any(term in text for term in ("技術スタック", "技術構成", "システム構成")):
        extra_terms.extend(
            [
                "phase1",
                "rag",
                "qdrant",
                "vector",
                "database",
                "postgresql",
                "docker",
                "compose",
                "fastapi",
                "react",
                "backend",
                "frontend",
                "worker",
                "citation",
                "confidence",
                "mcp",
            ]
        )
    if "ベクトル" in text or "vector database" in text:
        extra_terms.extend(["qdrant", "vector", "database", "retrieval"])
    if "引用" in text:
        extra_terms.extend(["citation", "citations"])
    if "信頼度" in text or "confidence" in text:
        extra_terms.append("confidence")
    if "評価" in text or "evaluation" in text:
        extra_terms.extend(["ci", "deterministic"])
    if not extra_terms:
        return text
    return f"{text} {' '.join(extra_terms)}"


def _extract_lmstudio_embeddings(payload: object, *, expected_count: int) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise EmbeddingAdapterError("embedding_failed")
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise EmbeddingAdapterError("embedding_empty_result")
    if all(isinstance(item, dict) and isinstance(item.get("index"), int) for item in data):
        data = sorted(data, key=_embedding_index)
    vectors: list[list[float]] = []
    for item in data:
        if not isinstance(item, dict):
            raise EmbeddingAdapterError("embedding_failed")
        embedding = item.get("embedding")
        if not isinstance(embedding, Sequence) or isinstance(embedding, (bytes, bytearray, str)):
            raise EmbeddingAdapterError("embedding_failed")
        try:
            vectors.append([float(cast(Any, value)) for value in embedding])
        except (TypeError, ValueError) as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
    return vectors


def _embedding_index(item: object) -> int:
    if not isinstance(item, dict):
        raise EmbeddingAdapterError("embedding_failed")
    index = item.get("index")
    if not isinstance(index, int):
        raise EmbeddingAdapterError("embedding_failed")
    return index


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
        try:
            vectors.append([float(cast(Any, item)) for item in cast(Sequence[object], row)])
        except (TypeError, ValueError) as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
    return vectors
