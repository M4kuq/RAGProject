from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath
from typing import Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ClientMessageConflict, ConflictError, RequestInProgress
from app.db.models import ChatMessage, RetrievalRun, RetrievalRunItem, User
from app.ingest.embedding import (
    EmbeddingAdapter,
    EmbeddingAdapterError,
    create_embedding_adapter,
)
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    ParsedGenerationOutput,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.confidence import ConfidenceInputs, calculate_confidence
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
    CitationInput,
    CitationRecord,
    RetrievalRepository,
    RetrievalRunItemInput,
)
from app.schemas.rag import (
    RagAskAssistantMessage,
    RagAskCitation,
    RagAskConfidence,
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
SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*\S{8,}"
    r"|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
)
CITATION_MARKER_RE = re.compile(r"\[(\d{1,6})\]")
MODEL_KEY_SEPARATOR = ":"


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
    citation_sources: list[CitationSource]


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
        answer_generator = self._answer_generator_for_request(payload)

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
                citation_sources=result.citation_sources,
                max_context_chars=self.settings.generation_max_context_chars,
            )
            prompt_citation_sources = _prompt_citation_sources(
                context_items=context_items,
                citation_sources=result.citation_sources,
            )
            generation = answer_generator.generate(
                GenerationRequest(
                    message=payload.message,
                    context_items=context_items,
                    max_output_chars=self.settings.generation_max_output_chars,
                )
            )
            parsed_generation, cited_sources = _validated_generation_or_fallback(
                generation.content,
                context_items=context_items,
                prompt_citation_sources=prompt_citation_sources,
            )
            assistant_message = self.chat_repository.create_message(
                db,
                chat_session_id=payload.chat_session_id,
                role="assistant",
                content=parsed_generation.answer_text,
                linked_retrieval_run_id=run_id,
            )
            self.repository.save_citations(
                db,
                citations=[
                    _citation_input(source, retrieval_run_id=run_id) for source in cited_sources
                ],
            )
            citation_records = self.repository.list_citations_for_run(
                db,
                retrieval_run_id=run_id,
            )
            confidence = calculate_confidence(
                ConfidenceInputs(
                    retrieval_score_summary=result.summary,
                    marker_count=len(parsed_generation.markers),
                    unique_citation_count=len(cited_sources),
                    selected_count=len(prompt_citation_sources),
                ),
                self.settings,
            )
            run = self._require_run(db, run_id)
            self.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=result.summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(result.summary.top1_rerank_score),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
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
                citation_records=citation_records,
                run=run,
                retrieval_run_id=run_id,
                replayed=False,
            )
        except CitationBuildError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="citation_build_failed",
            )
            raise RagAskPipelineError("citation_build_failed", 500) from None
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
            return RetrievalPipelineResult(
                summary=summary,
                items=[],
                selected_candidates=[],
                citation_sources=[],
            )

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
            key=lambda candidate: (
                rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order
            ),
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
            citation_sources=[
                _citation_source(
                    candidate,
                    saved_item=saved_item,
                    local_citation_id=local_id,
                    snippet_max_chars=self.settings.citation_preview_max_chars,
                )
                for local_id, (candidate, saved_item) in enumerate(
                    zip(
                        ordered_candidates[:selected_count],
                        saved_items[:selected_count],
                        strict=True,
                    ),
                    start=1,
                )
            ],
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
            citation_records=self.repository.list_citations_for_run(
                db,
                retrieval_run_id=run.retrieval_run_id,
            ),
            run=run,
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

    def _require_run(self, db: Session, retrieval_run_id: int) -> RetrievalRun:
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

    def _answer_generator_for_request(self, payload: RagAskRequest) -> AnswerGenerator:
        if payload.model_key is None:
            return self.answer_generator
        provider, separator, model_name = payload.model_key.partition(MODEL_KEY_SEPARATOR)
        provider = provider.lower()
        model_name = model_name.strip()
        if provider == "google":
            provider = "gemini"
        if (
            provider not in {"lmstudio", "openai", "anthropic", "gemini"}
            or not separator
            or not model_name
        ):
            raise RagAskPipelineError("unsupported_model", 422)
        if provider == "lmstudio" and self.settings.generation_provider == "fake":
            return self.answer_generator
        try:
            return create_answer_generator(
                self.settings,
                provider=provider,
                model_name=model_name,
            )
        except AnswerGenerationError as exc:
            raise RagAskPipelineError("unsupported_model", 422) from exc


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


def _citation_source(
    candidate: CheckedRetrievalCandidate,
    *,
    saved_item: RetrievalRunItem,
    local_citation_id: int,
    snippet_max_chars: int,
) -> CitationSource:
    return CitationSource(
        local_citation_id=local_citation_id,
        retrieval_run_item_id=saved_item.retrieval_run_item_id,
        document_chunk_id=candidate.chunk.document_chunk_id,
        source_label=_source_label(candidate),
        snippet=_snippet(candidate.chunk.content_text, max_chars=snippet_max_chars),
        page_from=candidate.chunk.page_from,
        page_to=candidate.chunk.page_to,
        section_title=_safe_display_text(candidate.chunk.section_title),
    )


def _citation_input(source: CitationSource, *, retrieval_run_id: int) -> CitationInput:
    return CitationInput(
        retrieval_run_id=retrieval_run_id,
        document_chunk_id=source.document_chunk_id,
        snippet=source.snippet,
        page_from=source.page_from,
        page_to=source.page_to,
        display_label=source.source_label,
        rank_order=source.local_citation_id,
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
    _add_optional(snapshot, "section_title", _safe_display_text(candidate.chunk.section_title))
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
    citation_sources: list[CitationSource],
    max_context_chars: int,
) -> list[GenerationContextItem]:
    remaining = max_context_chars
    items: list[GenerationContextItem] = []
    for candidate, source in zip(candidates, citation_sources, strict=True):
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
                local_citation_id=source.local_citation_id,
                page_from=candidate.chunk.page_from,
                page_to=candidate.chunk.page_to,
            )
        )
    return items


def _prompt_citation_sources(
    *,
    context_items: list[GenerationContextItem],
    citation_sources: list[CitationSource],
) -> list[CitationSource]:
    included_ids = {
        item.local_citation_id for item in context_items if item.local_citation_id is not None
    }
    return [source for source in citation_sources if source.local_citation_id in included_ids]


def _validate_generation_output_safety(
    answer_text: str,
    *,
    context_items: list[GenerationContextItem],
) -> None:
    if SENSITIVE_OUTPUT_RE.search(answer_text):
        raise CitationBuildError("citation_build_failed")
    normalized_answer = _clean_context_text(answer_text)
    for item in context_items:
        context_text = _clean_context_text(item.text)
        if len(context_text) >= 80 and context_text in normalized_answer:
            raise CitationBuildError("citation_build_failed")


def _validated_generation_or_fallback(
    content: str,
    *,
    context_items: list[GenerationContextItem],
    prompt_citation_sources: list[CitationSource],
) -> tuple[ParsedGenerationOutput, list[CitationSource]]:
    parsed_generation = parse_generation_output(content)
    _validate_generation_output_safety(
        parsed_generation.answer_text,
        context_items=context_items,
    )
    try:
        cited_sources = validate_generation_citations(
            parsed_generation,
            source_map=prompt_citation_sources,
        )
    except CitationBuildError:
        return _repair_generation_citations(
            parsed_generation,
            prompt_citation_sources=prompt_citation_sources,
        )
    return parsed_generation, cited_sources


def _repair_generation_citations(
    parsed_generation: ParsedGenerationOutput,
    *,
    prompt_citation_sources: list[CitationSource],
) -> tuple[ParsedGenerationOutput, list[CitationSource]]:
    if not prompt_citation_sources:
        raise CitationBuildError("citation_build_failed")
    first_source = prompt_citation_sources[0]
    answer_without_markers = CITATION_MARKER_RE.sub(
        "",
        parsed_generation.answer_text,
    ).strip()
    if not answer_without_markers:
        return _insufficient_citation_fallback(prompt_citation_sources)
    valid_ids = {source.local_citation_id for source in prompt_citation_sources}
    fallback_marker = f"[{first_source.local_citation_id}]"
    if parsed_generation.markers:
        repaired_text = CITATION_MARKER_RE.sub(
            lambda match: match.group(0) if int(match.group(1)) in valid_ids else fallback_marker,
            parsed_generation.answer_text,
        )
    else:
        repaired_text = f"{parsed_generation.answer_text} {fallback_marker}"
    repaired_generation = parse_generation_output(repaired_text)
    cited_sources = validate_generation_citations(
        repaired_generation,
        source_map=prompt_citation_sources,
    )
    return repaired_generation, cited_sources


def _insufficient_citation_fallback(
    prompt_citation_sources: list[CitationSource],
) -> tuple[ParsedGenerationOutput, list[CitationSource]]:
    if not prompt_citation_sources:
        raise CitationBuildError("citation_build_failed")
    first_source = prompt_citation_sources[0]
    fallback = (
        "検索された文書には、この質問に直接答えるための十分な根拠がありません "
        f"[{first_source.local_citation_id}]。"
    )
    parsed_generation = parse_generation_output(fallback)
    cited_sources = validate_generation_citations(
        parsed_generation,
        source_map=prompt_citation_sources,
    )
    return parsed_generation, cited_sources


def _ask_response(
    *,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    citation_records: list[CitationRecord],
    run: RetrievalRun,
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
        citations=[_citation_response(record) for record in citation_records],
        confidence=_confidence_response(run),
        retrieval_run_id=retrieval_run_id,
        replayed=replayed,
    )


def _citation_response(record: CitationRecord) -> RagAskCitation:
    return RagAskCitation(
        citation_id=record.citation.citation_id,
        local_citation_id=record.citation.rank_order,
        document_chunk_id=record.citation.document_chunk_id,
        source_label=record.citation.display_label,
        snippet=record.citation.snippet,
        page_from=record.citation.page_from,
        page_to=record.citation.page_to,
        section_title=_safe_display_text(record.chunk.section_title),
        old_version_flag=_old_version_flag(record),
    )


def _confidence_response(run: RetrievalRun) -> RagAskConfidence:
    if (
        run.answer_confidence is None
        or run.groundedness_score is None
        or run.confidence_label is None
    ):
        raise RuntimeError("retrieval_run_confidence_missing")
    if run.confidence_label not in {"High", "Medium", "Low"}:
        raise RuntimeError("retrieval_run_confidence_missing")
    label = cast(Literal["High", "Medium", "Low"], run.confidence_label)
    return RagAskConfidence(
        answer_confidence=_round_score(float(run.answer_confidence)),
        groundedness_score=_round_score(float(run.groundedness_score)),
        confidence_label=label,
    )


def _old_version_flag(record: CitationRecord) -> bool:
    return (
        record.document_version.status != "ready"
        or not record.document_version.is_active
        or record.logical_document.status != "active"
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


def _safe_display_text(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = _sanitize_label(value)
    if not sanitized:
        return None
    return sanitized[:255]


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
