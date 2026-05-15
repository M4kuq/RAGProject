from __future__ import annotations

from contextlib import suppress
import os
from uuid import uuid4

import httpx
import pytest

from app.ingest.qdrant import (
    HttpQdrantClient,
    QdrantCollectionConfig,
    QdrantPoint,
    QdrantVectorStore,
)

pytestmark = pytest.mark.skipif(
    os.getenv("QDRANT_INTEGRATION") != "1",
    reason="Set QDRANT_INTEGRATION=1 when a reachable Qdrant service is available.",
)


def test_qdrant_http_collection_upsert_payload_sync_and_cleanup() -> None:
    collection_name = f"test_pr11_{uuid4().hex}"
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    client = HttpQdrantClient(url=qdrant_url)
    store = QdrantVectorStore(
        client=client,
        config=QdrantCollectionConfig(name=collection_name, vector_dimension=4),
        create_collection=True,
    )

    try:
        store.ensure_collection()
        assert client.collection_vector_size(collection_name) == 4

        store.upsert(
            [
                QdrantPoint(
                    point_id=1,
                    vector=[0.1, 0.2, 0.3, 0.4],
                    payload={"document_version_id": 42, "document_chunk_id": 1},
                )
            ],
            batch_size=1,
        )
        store.sync_payload(
            document_version_id=42,
            payload={
                "is_active": False,
                "logical_document_status": "archived",
                "document_version_status": "ready",
            },
        )
        store.cleanup(document_version_id=42, point_ids=[1])
    finally:
        with suppress(httpx.HTTPError):
            httpx.delete(
                f"{qdrant_url.rstrip('/')}/collections/{collection_name}",
                timeout=5.0,
            )
