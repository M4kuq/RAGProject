from __future__ import annotations

import hashlib


def normalize_chunk_text(value: str) -> str:
    return " ".join(value.split()).strip()


def chunk_hash(
    *,
    normalized_chunk_text: str,
    document_version_id: int,
    chunk_index: int,
) -> str:
    payload = f"{normalized_chunk_text}{document_version_id}{chunk_index}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
