from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument


def search_chunks(db: Session, query: str, limit: int = 5) -> list[tuple[DocumentChunk, float]]:
    terms = {t.lower() for t in query.split() if t.strip()}
    chunks = db.scalars(
        select(DocumentChunk)
        .join(
            DocumentVersion,
            DocumentVersion.document_version_id == DocumentChunk.document_version_id,
        )
        .join(
            LogicalDocument,
            LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
        )
        .where(
            LogicalDocument.status == "active",
            DocumentVersion.status == "ready",
            DocumentVersion.is_active.is_(True),
        )
        .order_by(DocumentChunk.document_chunk_id.asc())
        .limit(200)
    ).all()
    scored: list[tuple[DocumentChunk, float]] = []
    for chunk in chunks:
        haystack = chunk.content_text.lower()
        score = sum(1 for term in terms if term in haystack) / max(1, len(terms))
        if score > 0 or not terms:
            scored.append((chunk, max(score, 0.15)))
    return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]


def build_answer(query: str, hits: list[tuple[DocumentChunk, float]]) -> str:
    if not hits:
        return "根拠が見つからないため、この質問には回答できません。"
    snippets = " ".join(chunk.content_text[:180] for chunk, _ in hits[:2])
    return f"質問「{query}」に対する回答です。根拠文書では次の内容が確認できます: {snippets} [1]"
