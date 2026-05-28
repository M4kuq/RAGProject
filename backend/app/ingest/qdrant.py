from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, cast

import httpx

from app.core.config import Settings
from app.ingest.embedding import (
    DocumentEmbeddingService,
    EmbeddingAdapterError,
    create_document_embedding_service,
)

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|\s)(?:export\s+)?"
    r"([A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*)"
    r"\s*[:=]\s*\S+"
)
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


class QdrantStoreError(RuntimeError):
    def __init__(
        self,
        error_code: str = "qdrant_upsert_failed",
        message: str = "Qdrant operation failed.",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True)
class QdrantCollectionConfig:
    name: str
    vector_dimension: int
    distance: str = "Cosine"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Qdrant collection name must not be empty")
        if self.vector_dimension < 1:
            raise ValueError("Qdrant vector dimension must be positive")


@dataclass(frozen=True)
class QdrantPoint:
    point_id: int
    vector: list[float]
    payload: dict[str, object]


@dataclass(frozen=True)
class DocumentIndexingResult:
    indexed_count: int


class QdrantClient(Protocol):
    def collection_vector_size(self, collection_name: str) -> int | None: ...

    def create_collection(self, config: QdrantCollectionConfig) -> None: ...

    def upsert_points(self, collection_name: str, points: Sequence[QdrantPoint]) -> None: ...

    def delete_points(self, collection_name: str, point_ids: Sequence[int]) -> None: ...

    def delete_by_document_version(
        self, collection_name: str, document_version_id: int
    ) -> None: ...

    def set_payload_by_document_version(
        self,
        collection_name: str,
        document_version_id: int,
        payload: dict[str, object],
    ) -> None: ...


class HttpQdrantClient:
    def __init__(self, *, url: str, timeout_seconds: float = 5.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def collection_vector_size(self, collection_name: str) -> int | None:
        response = self._request("GET", f"/collections/{collection_name}", ok_404=True)
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError as exc:
            raise QdrantStoreError("qdrant_unavailable") from exc
        return _extract_vector_size(payload)

    def create_collection(self, config: QdrantCollectionConfig) -> None:
        try:
            self._request(
                "PUT",
                f"/collections/{config.name}",
                json={
                    "vectors": {
                        "size": config.vector_dimension,
                        "distance": config.distance,
                    }
                },
            )
        except QdrantStoreError as exc:
            raise QdrantStoreError("qdrant_collection_create_failed") from exc

    def upsert_points(self, collection_name: str, points: Sequence[QdrantPoint]) -> None:
        self._request(
            "PUT",
            f"/collections/{collection_name}/points",
            params={"wait": "true"},
            json={
                "points": [
                    {
                        "id": point.point_id,
                        "vector": point.vector,
                        "payload": point.payload,
                    }
                    for point in points
                ]
            },
        )

    def delete_points(self, collection_name: str, point_ids: Sequence[int]) -> None:
        if not point_ids:
            return
        self._request(
            "POST",
            f"/collections/{collection_name}/points/delete",
            params={"wait": "true"},
            json={"points": list(point_ids)},
        )

    def delete_by_document_version(self, collection_name: str, document_version_id: int) -> None:
        self._request(
            "POST",
            f"/collections/{collection_name}/points/delete",
            params={"wait": "true"},
            json={
                "filter": {
                    "must": [
                        {
                            "key": "document_version_id",
                            "match": {"value": document_version_id},
                        }
                    ]
                }
            },
        )

    def set_payload_by_document_version(
        self,
        collection_name: str,
        document_version_id: int,
        payload: dict[str, object],
    ) -> None:
        self._request(
            "POST",
            f"/collections/{collection_name}/points/payload",
            params={"wait": "true"},
            json={
                "payload": payload,
                "filter": {
                    "must": [
                        {
                            "key": "document_version_id",
                            "match": {"value": document_version_id},
                        }
                    ]
                },
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        ok_404: bool = False,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
    ) -> httpx.Response | None:
        try:
            response = httpx.request(
                method,
                f"{self.url}{path}",
                params=params,
                json=json,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise QdrantStoreError("qdrant_unavailable") from exc
        if ok_404 and response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise QdrantStoreError("qdrant_unavailable")
        return response


class InMemoryQdrantClient:
    def __init__(self) -> None:
        self.collections: dict[str, int] = {}
        self.points: dict[str, dict[int, QdrantPoint]] = {}
        self.fail_create = False
        self.fail_upsert = False
        self.fail_delete = False

    def collection_vector_size(self, collection_name: str) -> int | None:
        return self.collections.get(collection_name)

    def create_collection(self, config: QdrantCollectionConfig) -> None:
        if self.fail_create:
            raise QdrantStoreError("qdrant_collection_create_failed")
        existing = self.collections.get(config.name)
        if existing is not None and existing != config.vector_dimension:
            raise QdrantStoreError("qdrant_collection_dimension_mismatch")
        self.collections[config.name] = config.vector_dimension
        self.points.setdefault(config.name, {})

    def upsert_points(self, collection_name: str, points: Sequence[QdrantPoint]) -> None:
        if self.fail_upsert:
            raise QdrantStoreError("qdrant_upsert_failed")
        if collection_name not in self.collections:
            raise QdrantStoreError("qdrant_unavailable")
        stored = self.points.setdefault(collection_name, {})
        for point in points:
            stored[point.point_id] = point

    def delete_points(self, collection_name: str, point_ids: Sequence[int]) -> None:
        if self.fail_delete:
            raise QdrantStoreError("qdrant_cleanup_failed")
        stored = self.points.setdefault(collection_name, {})
        for point_id in point_ids:
            stored.pop(point_id, None)

    def delete_by_document_version(self, collection_name: str, document_version_id: int) -> None:
        if self.fail_delete:
            raise QdrantStoreError("qdrant_cleanup_failed")
        stored = self.points.setdefault(collection_name, {})
        for point_id, point in list(stored.items()):
            if point.payload.get("document_version_id") == document_version_id:
                stored.pop(point_id, None)

    def set_payload_by_document_version(
        self,
        collection_name: str,
        document_version_id: int,
        payload: dict[str, object],
    ) -> None:
        if self.fail_upsert:
            raise QdrantStoreError("qdrant_upsert_failed")
        stored = self.points.setdefault(collection_name, {})
        for point in stored.values():
            if point.payload.get("document_version_id") == document_version_id:
                point.payload.update(payload)


class QdrantVectorStore:
    def __init__(
        self,
        *,
        client: QdrantClient,
        config: QdrantCollectionConfig,
        create_collection: bool,
    ) -> None:
        self.client = client
        self.config = config
        self.create_collection = create_collection

    def ensure_collection(self) -> None:
        try:
            vector_size = self.client.collection_vector_size(self.config.name)
        except QdrantStoreError:
            raise
        except Exception as exc:
            raise QdrantStoreError("qdrant_unavailable") from exc
        if vector_size is None:
            if not self.create_collection:
                raise QdrantStoreError("qdrant_unavailable")
            self.client.create_collection(self.config)
            vector_size = self.client.collection_vector_size(self.config.name)
        if vector_size != self.config.vector_dimension:
            raise QdrantStoreError("qdrant_collection_dimension_mismatch")

    def upsert(self, points: Sequence[QdrantPoint], *, batch_size: int) -> None:
        if not points:
            raise QdrantStoreError("qdrant_upsert_failed")
        if batch_size < 1:
            raise QdrantStoreError("qdrant_upsert_failed")
        for point in points:
            if len(point.vector) != self.config.vector_dimension:
                raise QdrantStoreError("qdrant_collection_dimension_mismatch")
        try:
            for start in range(0, len(points), batch_size):
                self.client.upsert_points(
                    self.config.name,
                    points[start : start + batch_size],
                )
        except QdrantStoreError as exc:
            if exc.error_code == "qdrant_collection_dimension_mismatch":
                raise
            raise QdrantStoreError("qdrant_upsert_failed") from exc
        except Exception as exc:
            raise QdrantStoreError("qdrant_upsert_failed") from exc

    def cleanup(self, *, document_version_id: int, point_ids: Sequence[int]) -> None:
        try:
            if point_ids:
                self.client.delete_points(self.config.name, point_ids)
            else:
                self.client.delete_by_document_version(self.config.name, document_version_id)
        except QdrantStoreError as exc:
            raise QdrantStoreError("qdrant_cleanup_failed") from exc
        except Exception as exc:
            raise QdrantStoreError("qdrant_cleanup_failed") from exc

    def sync_payload(self, *, document_version_id: int, payload: dict[str, object]) -> None:
        try:
            self.client.set_payload_by_document_version(
                self.config.name,
                document_version_id,
                payload,
            )
        except QdrantStoreError as exc:
            raise QdrantStoreError("qdrant_upsert_failed") from exc
        except Exception as exc:
            raise QdrantStoreError("qdrant_upsert_failed") from exc


class DocumentIndexingService:
    def __init__(
        self,
        *,
        embedding_service: DocumentEmbeddingService,
        vector_store: QdrantVectorStore,
        upsert_batch_size: int,
    ) -> None:
        if upsert_batch_size < 1:
            raise ValueError("Qdrant upsert batch size must be positive")
        self.embedding_service = embedding_service
        self.vector_store = vector_store
        self.upsert_batch_size = upsert_batch_size

    def index_chunks(
        self,
        *,
        logical_document: object,
        document_version: object,
        chunks: Sequence[object],
    ) -> DocumentIndexingResult:
        try:
            vectors = self.embedding_service.embed_chunks(chunks)
        except EmbeddingAdapterError:
            raise
        except Exception as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
        if len(vectors) != len(chunks):
            raise EmbeddingAdapterError("embedding_empty_result")
        self.vector_store.ensure_collection()
        points = [
            build_qdrant_point(
                logical_document=logical_document,
                document_version=document_version,
                chunk=chunk,
                vector=vector,
                document_version_status="ready",
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.vector_store.upsert(points, batch_size=self.upsert_batch_size)
        return DocumentIndexingResult(indexed_count=len(points))

    def cleanup_document_points(
        self,
        *,
        document_version_id: int,
        document_chunk_ids: Sequence[int],
    ) -> None:
        point_ids = [
            point_id_for_chunk_id(document_chunk_id) for document_chunk_id in document_chunk_ids
        ]
        self.vector_store.cleanup(document_version_id=document_version_id, point_ids=point_ids)

    def sync_document_payload(
        self,
        *,
        document_version_id: int,
        logical_document_status: str,
        document_version_status: str,
        is_active: bool,
    ) -> None:
        self.vector_store.sync_payload(
            document_version_id=document_version_id,
            payload={
                "logical_document_status": logical_document_status,
                "document_version_status": document_version_status,
                "is_active": is_active,
            },
        )


def create_document_indexing_service(settings: Settings) -> DocumentIndexingService:
    collection_config = QdrantCollectionConfig(
        name=settings.qdrant_collection_name,
        vector_dimension=settings.effective_embedding_dimension,
        distance=settings.qdrant_distance,
    )
    return DocumentIndexingService(
        embedding_service=create_document_embedding_service(settings),
        vector_store=QdrantVectorStore(
            client=HttpQdrantClient(
                url=settings.qdrant_url,
                timeout_seconds=settings.qdrant_timeout_seconds,
            ),
            config=collection_config,
            create_collection=settings.qdrant_create_collection,
        ),
        upsert_batch_size=settings.qdrant_upsert_batch_size,
    )


def build_qdrant_point(
    *,
    logical_document: object,
    document_version: object,
    chunk: object,
    vector: Sequence[float],
    document_version_status: str,
) -> QdrantPoint:
    chunk_obj = cast(Any, chunk)
    document_chunk_id = _positive_int(chunk_obj.document_chunk_id)
    return QdrantPoint(
        point_id=point_id_for_chunk_id(document_chunk_id),
        vector=[float(value) for value in vector],
        payload=build_qdrant_payload(
            logical_document=logical_document,
            document_version=document_version,
            chunk=chunk,
            document_version_status=document_version_status,
        ),
    )


def point_id_for_chunk_id(document_chunk_id: int) -> int:
    return _positive_int(document_chunk_id)


def build_qdrant_payload(
    *,
    logical_document: object,
    document_version: object,
    chunk: object,
    document_version_status: str,
) -> dict[str, object]:
    logical_document_obj = cast(Any, logical_document)
    document_version_obj = cast(Any, document_version)
    chunk_obj = cast(Any, chunk)
    payload: dict[str, object] = {
        "logical_document_id": _positive_int(logical_document_obj.logical_document_id),
        "document_version_id": _positive_int(document_version_obj.document_version_id),
        "document_chunk_id": _positive_int(chunk_obj.document_chunk_id),
        "chunk_index": int(chunk_obj.chunk_index),
        "modality": str(chunk_obj.modality),
        "is_active": bool(document_version_obj.is_active),
        "logical_document_status": str(logical_document_obj.status),
        "document_version_status": document_version_status,
    }
    _add_optional(payload, "page_from", chunk_obj.page_from)
    _add_optional(payload, "page_to", chunk_obj.page_to)
    metadata = _safe_chunk_metadata(getattr(chunk_obj, "metadata_json", None))
    if metadata:
        for key, value in metadata.items():
            payload[key] = value
    created_at = chunk_obj.created_at
    if isinstance(created_at, datetime):
        payload["created_at"] = created_at.isoformat()
    return payload


def _extract_vector_size(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    config = result.get("config", {})
    if not isinstance(config, dict):
        return None
    params = config.get("params", {})
    if not isinstance(params, dict):
        return None
    vectors = params.get("vectors")
    if isinstance(vectors, dict) and isinstance(vectors.get("size"), int):
        return cast(int, vectors["size"])
    if isinstance(vectors, dict):
        for value in vectors.values():
            if isinstance(value, dict) and isinstance(value.get("size"), int):
                return cast(int, value["size"])
    return None


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise QdrantStoreError("document_indexing_failed")
    return value


def _add_optional(payload: dict[str, object], key: str, value: object) -> None:
    if value is not None:
        payload[key] = value


def _safe_chunk_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "structure_type",
        "sheet_name",
        "row_from",
        "row_to",
        "column_from",
        "column_to",
        "table_index",
        "slide_number",
        "slide_title",
        "html_title",
        "heading_path",
        "element_type",
        "element_index",
        "xml_root",
        "xml_path",
        "element_name",
        "source_type",
    }
    safe: dict[str, object] = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if isinstance(item, str):
            redacted = _safe_metadata_string(item)
            if redacted:
                safe[key] = redacted
        elif isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int | float):
            safe[key] = item
    return safe


def _safe_metadata_string(value: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    if (
        _SECRET_ASSIGNMENT_RE.search(normalized)
        or _URL_RE.search(normalized)
        or _EMAIL_RE.search(normalized)
    ):
        return "redacted"
    return normalized[:120]
