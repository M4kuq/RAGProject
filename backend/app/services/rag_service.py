from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ClientMessageConflict, ConflictError, RequestInProgress
from app.db.models import ChatMessage, User
from app.ingest.embedding import (
    EmbeddingAdapter,
    EmbeddingAdapterError,
    create_embedding_adapter,
)
from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    create_answer_generator,
)
from app.rag.rerank import (
    RerankCandidate,
    RerankerClient,
    RerankError,
    RerankResult,
    create_reranker,
)
from app.rag.retrieval import (
    HttpQdrantSearchClient,
    RetrievalError,
    RetrievalFilters,
    VectorSearchClient,
)
from app.repositories.chat_repository import ChatRepository
from app.repositories.retrieval_repository import (
    CheckedRetrievalCandidate,
    RetrievalRepository,
    RetrievalRunItemInput,
)
from app.schemas.rag import (
    RagAskAssistantMessage,
    RagAskRequest,
    RagAskResponse,
    RagAskUserMessage,
    RagSearchItem,
    RagSearchRequest,
    RagSearchResponse,
    RetrievalScoreSummary,
)
from app.services.chat_service import ChatService

SCORE_QUANT = Decimal("0.000001")


class RagPipelineError(RuntimeError):
    def __init__(self, error_code: str, status_code: int) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.status_code = status_code


class RagSearchPipelineError(RagPipelineError):
    pass


class RagAskPipelineError(RagPipelineError):
    pass


@dataclass(frozen=True)
class RetrievalPipelineResult:
    summary: RetrievalScoreSummary
    items: list[RagSearchItem]
    selected_candidates: list[CheckedRetrievalCandidate]


class RagService:
    def __init__(
        self,
        *,
        settings: Settings,
        embedding_adapter: EmbeddingAdapter,
        vector_client: VectorSearchClient,
        reranker: RerankerClient,
        answer_generator: AnswerGenerator | None = None,
        repository: RetrievalRepository | None = None,
        chat_repository: ChatRepository | None = None,
    ) -> None:
        self.settings = settings
        self.embedding_adapter = embedding_adapter
        self.vector_client = vector_client
        self.reranker = reranker
        self.answer_generator = answer_generator or create_answer_generator(settings)
        self.repository = repository or RetrievalRepository()
        self.chat_repository = chat_repository or ChatRepository()
        self.chat_service = ChatService(self.chat_repository)

    def search(
        self,
        db: Session,
        *,
        payload: RagSearchRequest,
        request_id: str | None,
    ) -> RagSearchResponse:
        top_k = self._effective_top_k(payload.top_k)
        rerank_top_n = self._effective_rerank_top_n(payload.rerank_top_n)
        filters = _retrieval_filters(payload)
        run = self.repository.create_standalone_run(
            db,
            top_k=top_k,
            query_hash=_query_hash(payload.query),
            request_id=request_id,
            started_at=datetime.now(UTC),
        )
        db.commit()
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self._retrieve_and_rerank(
                db,
                query=payload.query,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
            )
            run = self._require_run(db, run_id)
            self.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=result.summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(result.summary.top1_rerank_score),
                finished_at=datetime.now(UTC),
            )
            db.commit()
            return RagSearchResponse(
                retrieval_run_id=run_id,
                status="succeeded",
                retrieval_score_summary=result.summary,
                items=result.items,
            )
        except (EmbeddingAdapterError, RetrievalError):
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
            )
            raise RagSearchPipelineError("retrieval_failed", 503) from None
        except RerankError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
            )
            raise RagSearchPipelineError("rerank_failed", 503) from None
        except Exception:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
            )
            raise

    def ask(
        self,
        db: Session,
        *,
        payload: RagAskRequest,
        user: User,
        request_id: str | None,
    ) -> RagAskResponse:
        session = self.chat_service.ensure_session_can_append_messages(
            db,
            user=user,
            chat_session_id=payload.chat_session_id,
        )
        existing = self.chat_repository.get_user_message_by_client_message_id(
            db,
            chat_session_id=payload.chat_session_id,
            client_message_id=payload.client_message_id,
            for_update=True,
        )
        if existing is not None:
            return self._classify_duplicate(db, payload=payload, existing=existing)

        top_k = self._effective_ask_top_k(payload.top_k)
        rerank_top_n = self._effective_ask_rerank_top_n(payload.rerank_top_n)
        now = datetime.now(UTC)
        try:
            user_message = self.chat_repository.create_message(
                db,
                chat_session_id=payload.chat_session_id,
                role="user",
                content=payload.message,
                client_message_id=payload.client_message_id,
            )
            run = self.repository.create_chat_run(
                db,
                chat_session_id=payload.chat_session_id,
                request_message_id=user_message.chat_message_id,
                top_k=top_k,
                query_hash=_query_hash(payload.message),
                request_id=request_id,
                started_at=now,
            )
            self.chat_repository.touch_session(db, session=session, updated_at=now)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            existing_after_race = self.chat_repository.get_user_message_by_client_message_id(
                db,
                chat_session_id=payload.chat_session_id,
                client_message_id=payload.client_message_id,
            )
            if existing_after_race is not None:
                return self._classify_duplicate(
                    db,
                    payload=payload,
                    existing=existing_after_race,
                )
            raise ConflictError() from exc

        db.refresh(user_message)
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self._retrieve_and_rerank(
                db,
                query=payload.message,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=_retrieval_filters(payload),
                retrieval_run_id=run_id,
            )
            if not result.selected_candidates:
                self._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                )
                raise RagAskPipelineError("no_context_found", 422)

            context_items = _assemble_context(
                result.selected_candidates,
                max_context_chars=self.settings.generation_max_context_chars,
            )
            generation = self.answer_generator.generate(
                GenerationRequest(
                    message=payload.message,
                    context_items=context_items,
                    max_output_chars=self.settings.generation_max_output_chars,
                )
            )
            assistant_message = self.chat_repository.create_message(
                db,
                chat_session_id=payload.chat_session_id,
                role="assistant",
                content=generation.content,
                linked_retrieval_run_id=run_id,
            )
            run = self._require_run(db, run_id)
            self.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=result.summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(result.summary.top1_rerank_score),
                finished_at=datetime.now(UTC),
            )
            self.chat_repository.touch_session(
                db,
                session=session,
                updated_at=datetime.now(UTC),
            )
            db.commit()
            db.refresh(assistant_message)
            db.refresh(user_message)
            return _ask_response(
                user_message=user_message,
                assistant_message=assistant_message,
                retrieval_run_id=run_id,
                replayed=False,
            )
        except RagAskPipelineError:
            raise
        except (EmbeddingAdapterError, RetrievalError):
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
            )
            raise RagAskPipelineError("retrieval_failed", 503) from None
        except RerankError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
            )
            raise RagAskPipelineError("rerank_failed", 503) from None
        except AnswerGenerationError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
            )
            raise RagAskPipelineError("generation_failed", 503) from None
        except Exception:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
            )
            raise

    def _retrieve_and_rerank(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
    ) -> RetrievalPipelineResult:
        query_vector = self._embed_query(query)
        vector_candidates = self.vector_client.search(
            collection_name=self.settings.qdrant_collection_name,
            query_vector=query_vector,
            limit=top_k,
            filters=filters,
        )
        checked_candidates = self.repository.final_check_candidates(
            db,
            candidates=vector_candidates,
            filters=filters,
        )
        if not checked_candidates:
            summary = _score_summary(
                requested_top_k=top_k,
                qdrant_candidate_count=len(vector_candidates),
                checked_candidates=[],
                selected_count=0,
                top1_rerank_score=None,
            )
            return RetrievalPipelineResult(summary=summary, items=[], selected_candidates=[])

        rerank_results = self.reranker.rerank(
            query=query,
            candidates=[
                RerankCandidate(
                    document_chunk_id=candidate.chunk.document_chunk_id,
                    text=candidate.chunk.content_text,
                    retrieval_score=candidate.retrieval_score,
                )
                for candidate in checked_candidates
            ],
        )
        rerank_by_chunk_id = _validated_rerank_results(
            rerank_results,
            checked_candidates=checked_candidates,
        )

        ordered_candidates = sorted(
            checked_candidates,
            key=lambda candidate: rerank_by_chunk_id[
                candidate.chunk.document_chunk_id
            ].rerank_order,
        )
        selected_count = min(rerank_top_n, len(ordered_candidates))
        item_inputs = [
            _run_item_input(
                candidate,
                rerank_score=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_score,
                rerank_order=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order,
                selected_flag=index <= selected_count,
            )
            for index, candidate in enumerate(ordered_candidates, start=1)
        ]
        saved_items = self.repository.save_items(
            db,
            retrieval_run_id=retrieval_run_id,
            items=item_inputs,
        )
        top1_rerank_score = rerank_by_chunk_id[
            ordered_candidates[0].chunk.document_chunk_id
        ].rerank_score
        summary = _score_summary(
            requested_top_k=top_k,
            qdrant_candidate_count=len(vector_candidates),
            checked_candidates=checked_candidates,
            selected_count=selected_count,
            top1_rerank_score=top1_rerank_score,
        )
        return RetrievalPipelineResult(
            summary=summary,
            items=[
                _response_item(
                    candidate,
                    saved_item_id=saved_item.retrieval_run_item_id,
                    rerank_score=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_score,
                    rerank_order=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order,
                    selected_flag=index <= selected_count,
                    snippet_max_chars=self.settings.search_snippet_max_chars,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(ordered_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
            selected_candidates=ordered_candidates[:selected_count],
        )

    def _classify_duplicate(
        self,
        db: Session,
        *,
        payload: RagAskRequest,
        existing: ChatMessage,
    ) -> RagAskResponse:
        if existing.content != payload.message:
            raise ClientMessageConflict()
        run = self.repository.get_latest_run_for_request_message(
            db,
            chat_session_id=payload.chat_session_id,
            request_message_id=existing.chat_message_id,
            for_update=True,
        )
        if run is None or run.status == "running":
            raise RequestInProgress()
        if run.status == "failed":
            raise ConflictError()
        assistant = self.chat_repository.get_assistant_message_for_retrieval_run(
            db,
            chat_session_id=payload.chat_session_id,
            retrieval_run_id=run.retrieval_run_id,
        )
        if assistant is None:
            raise ConflictError()
        return _ask_response(
            user_message=existing,
            assistant_message=assistant,
            retrieval_run_id=run.retrieval_run_id,
            replayed=True,
        )

    def _effective_top_k(self, requested: int | None) -> int:
        value = requested or self.settings.retrieval_top_k_default
        return min(value, self.settings.retrieval_top_k_max, 20)

    def _effective_rerank_top_n(self, requested: int | None) -> int:
        value = requested or self.settings.rerank_top_n_default
        return min(value, self.settings.rerank_top_n_max, 20)

    def _effective_ask_top_k(self, requested: int | None) -> int:
        value = requested or self.settings.ask_top_k_default
        return min(value, self.settings.retrieval_top_k_max, 20)

    def _effective_ask_rerank_top_n(self, requested: int | None) -> int:
        value = requested or self.settings.ask_rerank_top_n_default
        return min(value, self.settings.rerank_top_n_max, 20)

    def _embed_query(self, query: str) -> list[float]:
        try:
            vectors = self.embedding_adapter.embed_texts([query])
        except EmbeddingAdapterError:
            raise
        except Exception as exc:
            raise EmbeddingAdapterError("embedding_failed") from exc
        if len(vectors) != 1 or len(vectors[0]) != self.settings.effective_embedding_dimension:
            raise EmbeddingAdapterError("embedding_dimension_mismatch")
        return [float(value) for value in vectors[0]]

    def _require_run(self, db: Session, retrieval_run_id: int):
        run = self.repository.get_run(db, retrieval_run_id=retrieval_run_id)
        if run is None:
            raise RuntimeError("retrieval_run_missing")
        return run

    def _mark_failed_safely(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        error_code: str,
    ) -> None:
        db.rollback()
        run = self.repository.get_run(db, retrieval_run_id=retrieval_run_id)
        if run is None:
            return
        self.repository.mark_failed(
            db,
            run=run,
            error_code=error_code,
            finished_at=datetime.now(UTC),
        )
        db.commit()


def create_rag_service(settings: Settings) -> RagService:
    return RagService(
        settings=settings,
        embedding_adapter=create_embedding_adapter(settings),
        vector_client=HttpQdrantSearchClient(
            url=settings.qdrant_url,
            timeout_seconds=settings.qdrant_timeout_seconds,
        ),
        reranker=create_reranker(settings),
        answer_generator=create_answer_generator(settings),
    )


def _retrieval_filters(payload: RagSearchRequest | RagAskRequest) -> RetrievalFilters:
    if payload.filters is None:
        return RetrievalFilters()
    return RetrievalFilters(
        logical_document_ids=tuple(payload.filters.logical_document_ids or ()),
        modality=payload.filters.modality,
    )


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _score_summary(
    *,
    requested_top_k: int,
    qdrant_candidate_count: int,
    checked_candidates: list[CheckedRetrievalCandidate],
    selected_count: int,
    top1_rerank_score: float | None,
) -> RetrievalScoreSummary:
    retrieval_scores = [candidate.retrieval_score for candidate in checked_candidates]
    return RetrievalScoreSummary(
        requested_top_k=requested_top_k,
        qdrant_candidate_count=qdrant_candidate_count,
        post_filter_candidate_count=len(checked_candidates),
        selected_count=selected_count,
        excluded_by_rdb_check_count=qdrant_candidate_count - len(checked_candidates),
        top1_retrieval_score=_round_score(retrieval_scores[0]) if retrieval_scores else None,
        top3_avg_retrieval_score=(
            _round_score(sum(retrieval_scores[:3]) / min(3, len(retrieval_scores)))
            if retrieval_scores
            else None
        ),
        top1_rerank_score=(
            _round_score(top1_rerank_score) if top1_rerank_score is not None else None
        ),
    )


def _validated_rerank_results(
    results: list[RerankResult],
    *,
    checked_candidates: list[CheckedRetrievalCandidate],
) -> dict[int, RerankResult]:
    expected_ids = {candidate.chunk.document_chunk_id for candidate in checked_candidates}
    if len(results) != len(expected_ids):
        raise RerankError()

    normalized: dict[int, RerankResult] = {}
    orders: list[int] = []
    for result in results:
        if result.document_chunk_id not in expected_ids:
            raise RerankError()
        if result.document_chunk_id in normalized:
            raise RerankError()
        rerank_order = _positive_rerank_order(result.rerank_order)
        orders.append(rerank_order)
        normalized[result.document_chunk_id] = RerankResult(
            document_chunk_id=result.document_chunk_id,
            rerank_score=_unit_score(result.rerank_score),
            rerank_order=rerank_order,
        )

    if sorted(orders) != list(range(1, len(expected_ids) + 1)):
        raise RerankError()
    return normalized


def _positive_rerank_order(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RerankError()
    if value < 1:
        raise RerankError()
    return value


def _unit_score(value: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise RerankError() from exc
    if not math.isfinite(score):
        raise RerankError()
    return min(1.0, max(0.0, score))


def _run_item_input(
    candidate: CheckedRetrievalCandidate,
    *,
    rerank_score: float,
    rerank_order: int,
    selected_flag: bool,
) -> RetrievalRunItemInput:
    return RetrievalRunItemInput(
        document_chunk_id=candidate.chunk.document_chunk_id,
        retrieval_score=_decimal_score(candidate.retrieval_score),
        rerank_score=_decimal_score(rerank_score),
        rank_order=candidate.rank_order,
        rerank_order=rerank_order,
        selected_flag=selected_flag,
        payload_snapshot=_payload_snapshot(candidate),
    )


def _response_item(
    candidate: CheckedRetrievalCandidate,
    *,
    saved_item_id: int,
    rerank_score: float,
    rerank_order: int,
    selected_flag: bool,
    snippet_max_chars: int,
) -> RagSearchItem:
    return RagSearchItem(
        retrieval_run_item_id=saved_item_id,
        document_chunk_id=candidate.chunk.document_chunk_id,
        source_label=_source_label(candidate),
        snippet=_snippet(candidate.chunk.content_text, max_chars=snippet_max_chars),
        page_from=candidate.chunk.page_from,
        page_to=candidate.chunk.page_to,
        retrieval_score=_round_score(candidate.retrieval_score),
        rerank_score=_round_score(rerank_score),
        rank_order=candidate.rank_order,
        rerank_order=rerank_order,
        selected_flag=selected_flag,
        payload_snapshot=_payload_snapshot(candidate),
    )


def _payload_snapshot(candidate: CheckedRetrievalCandidate) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "logical_document_id": candidate.logical_document.logical_document_id,
        "document_version_id": candidate.document_version.document_version_id,
        "source_label": _source_label(candidate),
        "version_no": candidate.document_version.version_no,
        "modality": candidate.chunk.modality,
    }
    _add_optional(snapshot, "page_from", candidate.chunk.page_from)
    _add_optional(snapshot, "page_to", candidate.chunk.page_to)
    _add_optional(snapshot, "section_title", candidate.chunk.section_title)
    return snapshot


def _source_label(candidate: CheckedRetrievalCandidate) -> str:
    raw_label = candidate.document_version.file_name or candidate.logical_document.title
    normalized = _sanitize_label(raw_label.replace("\\", "/"))
    label = _sanitize_label(PurePosixPath(normalized).name)
    fallback = _sanitize_label(candidate.logical_document.title)
    safe_label = label or fallback or f"document:{candidate.logical_document.logical_document_id}"
    return safe_label[:255]


def _assemble_context(
    candidates: list[CheckedRetrievalCandidate],
    *,
    max_context_chars: int,
) -> list[GenerationContextItem]:
    remaining = max_context_chars
    items: list[GenerationContextItem] = []
    for candidate in candidates:
        if remaining <= 0:
            break
        text = _clean_context_text(candidate.chunk.content_text)
        if not text:
            continue
        clipped = text[:remaining]
        remaining -= len(clipped)
        items.append(
            GenerationContextItem(
                document_chunk_id=candidate.chunk.document_chunk_id,
                source_label=_source_label(candidate),
                text=clipped,
                page_from=candidate.chunk.page_from,
                page_to=candidate.chunk.page_to,
            )
        )
    return items


def _ask_response(
    *,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    retrieval_run_id: int,
    replayed: bool,
) -> RagAskResponse:
    return RagAskResponse(
        chat_session_id=user_message.chat_session_id,
        user_message=RagAskUserMessage(
            chat_message_id=user_message.chat_message_id,
            chat_session_id=user_message.chat_session_id,
            role="user",
            content=user_message.content,
            client_message_id=user_message.client_message_id or "",
            created_at=_aware_utc(user_message.created_at),
        ),
        assistant_message=RagAskAssistantMessage(
            chat_message_id=assistant_message.chat_message_id,
            chat_session_id=assistant_message.chat_session_id,
            role="assistant",
            content=assistant_message.content,
            linked_retrieval_run_id=assistant_message.linked_retrieval_run_id or retrieval_run_id,
            created_at=_aware_utc(assistant_message.created_at),
        ),
        retrieval_run_id=retrieval_run_id,
        replayed=replayed,
    )


def _snippet(text: str, *, max_chars: int) -> str:
    cleaned = _clean_context_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 3]}..."


def _clean_context_text(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())


def _sanitize_label(value: str) -> str:
    printable = "".join(char if char.isprintable() else " " for char in value)
    return " ".join(printable.split())


def _optional_decimal_score(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return _decimal_score(value)


def _decimal_score(value: float) -> Decimal:
    return Decimal(str(_round_score(value))).quantize(
        SCORE_QUANT,
        rounding=ROUND_HALF_UP,
    )


def _round_score(value: float) -> float:
    return round(float(value), 6)


def _add_optional(payload: dict[str, object], key: str, value: object) -> None:
    if value is not None:
        payload[key] = value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
