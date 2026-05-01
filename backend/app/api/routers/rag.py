from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin, require_csrf
from app.db.models import Citation, ChatMessage, RetrievalRun, User
from app.db.session import get_db
from app.rag.fake_pipeline import build_answer, search_chunks

router = APIRouter(dependencies=[Depends(require_csrf)])


class AskRequest(BaseModel):
    chat_session_id: int | None = None
    question: str = Field(min_length=1)
    client_message_id: str | None = None


@router.post("/ask")
def ask(payload: AskRequest, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    hits = search_chunks(db, payload.question)
    if not hits:
        raise HTTPException(status_code=422, detail="no_context_found")
    run = RetrievalRun(
        chat_session_id=payload.chat_session_id,
        origin_type="chat",
        query_text=payload.question,
        status="succeeded",
        retrieval_score_summary={"top_score": hits[0][1], "hit_count": len(hits)},
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    answer = build_answer(payload.question, hits)
    assistant = None
    if payload.chat_session_id:
        user_message = ChatMessage(
            chat_session_id=payload.chat_session_id,
            role="user",
            content=payload.question,
            client_message_id=payload.client_message_id,
        )
        db.add(user_message)
        db.flush()
        assistant = ChatMessage(
            chat_session_id=payload.chat_session_id,
            role="assistant",
            content=answer,
            linked_retrieval_run_id=run.retrieval_run_id,
        )
        db.add(assistant)
    citations = []
    for index, (chunk, _) in enumerate(hits[:3], start=1):
        citation = Citation(
            retrieval_run_id=run.retrieval_run_id,
            document_chunk_id=chunk.document_chunk_id,
            marker=f"[{index}]",
            snippet=chunk.content[:240],
            source_label=f"chunk:{chunk.document_chunk_id}",
        )
        db.add(citation)
        citations.append(citation)
    db.commit()
    return {
        "data": {
            "answer": answer,
            "assistant_message_id": assistant.chat_message_id if assistant else None,
            "retrieval_run_id": run.retrieval_run_id,
            "citations": [{"marker": c.marker, "snippet": c.snippet, "source_label": c.source_label} for c in citations],
            "confidence": {"label": "medium", "reason": "fake deterministic Phase1 adapter"},
        },
        "meta": {},
    }


@router.post("/search")
def search(payload: AskRequest, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict[str, object]:
    hits = search_chunks(db, payload.question)
    return {
        "data": [
            {"document_chunk_id": chunk.document_chunk_id, "snippet": chunk.content[:240], "score": score}
            for chunk, score in hits
        ],
        "meta": {"pagination": {"page": 1, "page_size": 20, "total": len(hits)}},
    }
