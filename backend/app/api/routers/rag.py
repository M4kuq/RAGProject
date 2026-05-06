from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import current_user, pagination_params, require_admin, require_csrf
from app.api.responses import paginate, success_response
from app.db.models import ChatMessage, Citation, RetrievalRun, RetrievalRunItem, User
from app.db.session import get_db
from app.rag.fake_pipeline import build_answer, search_chunks
from app.schemas.common import PaginationParams
from app.services.chat_service import ChatService

router = APIRouter(dependencies=[Depends(require_csrf)])


class AskRequest(BaseModel):
    chat_session_id: int | None = None
    question: str = Field(min_length=1)
    client_message_id: str | None = None


def chat_service() -> ChatService:
    return ChatService()


@router.post("/ask")
def ask(
    payload: AskRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    service: ChatService = Depends(chat_service),
) -> dict[str, object]:
    if payload.chat_session_id:
        service.ensure_session_can_append_messages(
            db,
            user=user,
            chat_session_id=payload.chat_session_id,
        )
    hits = search_chunks(db, payload.question)
    if not hits:
        raise HTTPException(status_code=422, detail="no_context_found")
    request_message_id = None
    if payload.chat_session_id:
        user_message = ChatMessage(
            chat_session_id=payload.chat_session_id,
            role="user",
            content=payload.question,
            client_message_id=payload.client_message_id,
        )
        db.add(user_message)
        db.flush()
        request_message_id = user_message.chat_message_id
    run = RetrievalRun(
        chat_session_id=payload.chat_session_id,
        request_message_id=request_message_id,
        status="succeeded",
        top_k=len(hits),
        retrieval_score_summary={
            "candidate_count": len(hits),
            "post_final_check_count": len(hits),
            "selected_count": min(len(hits), 3),
            "excluded_count": 0,
            "top1_retrieval_score": hits[0][1],
            "top3_avg_retrieval_score": sum(score for _, score in hits[:3]) / min(len(hits), 3),
            "top1_rerank_score": None,
        },
        answer_confidence=0.75,
        groundedness_score=0.75,
        confidence_label="Medium",
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    answer = build_answer(payload.question, hits)
    assistant = None
    if payload.chat_session_id:
        assistant = ChatMessage(
            chat_session_id=payload.chat_session_id,
            role="assistant",
            content=answer,
            linked_retrieval_run_id=run.retrieval_run_id,
        )
        db.add(assistant)
    citations = []
    for index, (chunk, _) in enumerate(hits[:3], start=1):
        db.add(
            RetrievalRunItem(
                retrieval_run_id=run.retrieval_run_id,
                document_chunk_id=chunk.document_chunk_id,
                retrieval_score=hits[index - 1][1],
                rank_order=index,
                selected_flag=True,
                payload_snapshot={
                    "document_version_id": chunk.document_version_id,
                    "page_from": chunk.page_from,
                    "page_to": chunk.page_to,
                    "section_title": chunk.section_title,
                    "modality": chunk.modality,
                },
            )
        )
        db.flush()
        citation = Citation(
            retrieval_run_id=run.retrieval_run_id,
            document_chunk_id=chunk.document_chunk_id,
            snippet=chunk.content_text[:240],
            page_from=chunk.page_from,
            page_to=chunk.page_to,
            display_label=f"chunk:{chunk.document_chunk_id}",
            rank_order=index,
        )
        db.add(citation)
        citations.append(citation)
    db.commit()
    return success_response(
        {
            "answer": answer,
            "assistant_message_id": assistant.chat_message_id if assistant else None,
            "retrieval_run_id": run.retrieval_run_id,
            "citations": [
                {
                    "rank_order": c.rank_order,
                    "snippet": c.snippet,
                    "display_label": c.display_label,
                }
                for c in citations
            ],
            "confidence": {"label": "Medium", "reason": "fake deterministic Phase1 adapter"},
        },
        request,
    )


@router.post("/search")
def search(
    payload: AskRequest,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    pagination: PaginationParams = Depends(pagination_params),
) -> dict[str, object]:
    hits = search_chunks(db, payload.question)
    page_hits, page_meta = paginate(hits, pagination)
    return success_response(
        [
            {
                "document_chunk_id": chunk.document_chunk_id,
                "snippet": chunk.content_text[:240],
                "score": score,
            }
            for chunk, score in page_hits
        ],
        request,
        pagination=page_meta,
    )
