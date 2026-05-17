from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.ingest.embedding import (
    EmbeddingAdapter,
    EmbeddingAdapterError,
    create_embedding_adapter,
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
from app.repositories.retrieval_repository import (
    CheckedRetrievalCandidate,
    RetrievalRepository,
    RetrievalRunItemInput,
)
from app.schemas.rag import (
    RagSearchItem,
    RagSearchRequest,
    RagSearchResponse,
    RetrievalScoreSummary,
)

SCORE_QUANT = Decimal("0.000001")


class RagSearchPipelineError(RuntimeError):
    def __init__(self, error_code: str, status_code: int) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.status_code = status_code


class RagService:
    def __init__(
        self,
        *,
        settings: Settings,
        embedding_adapter: EmbeddingAdapter,
        vector_client: VectorSearchClient,
        reranker: RerankerClient,
        repository: RetrievalRepository | None = None,
    ) -> None:
        self.settings = settings
        self.embedding_adapter = embedding_adapter
        self.vector_client = vector_client
        self.reranker = reranker
        self.repository = repository or RetrievalRepository()

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
            query_vector = self._embed_query(payload.query)
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
                run = self._require_run(db, run_id)
                self.repository.mark_succeeded(
                    db,
                    run=run,
                    retrieval_score_summary=summary.model_dump(mode="json"),
                    rerank_score_top1=None,
                    finished_at=datetime.now(UTC),
                )
                db.commit()
                return RagSearchResponse(
                    retrieval_run_id=run_id,
                    status="succeeded",
                    retrieval_score_summary=summary,
                    items=[],
                )

            rerank_results = self.reranker.rerank(
                query=payload.query,
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
                retrieval_run_id=run_id,
                items=item_inputs,
            )
            top1_rerank_score = min(
                1.0,
                max(
                    0.0,
                    rerank_by_chunk_id[ordered_candidates[0].chunk.document_chunk_id].rerank_score,
                ),
            )
            summary = _score_summary(
                requested_top_k=top_k,
                qdrant_candidate_count=len(vector_candidates),
                checked_candidates=checked_candidates,
                selected_count=selected_count,
                top1_rerank_score=top1_rerank_score,
            )
            run = self._require_run(db, run_id)
            self.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=summary.model_dump(mode="json"),
                rerank_score_top1=_decimal_score(top1_rerank_score),
                finished_at=datetime.now(UTC),
            )
            db.commit()
            return RagSearchResponse(
                retrieval_run_id=run_id,
                status="succeeded",
                retrieval_score_summary=summary,
                items=[
                    _response_item(
                        candidate,
                        saved_item_id=saved_item.retrieval_run_item_id,
                        rerank_score=rerank_by_chunk_id[
                            candidate.chunk.document_chunk_id
                        ].rerank_score,
                        rerank_order=rerank_by_chunk_id[
                            candidate.chunk.document_chunk_id
                        ].rerank_order,
                        selected_flag=index <= selected_count,
                        snippet_max_chars=self.settings.search_snippet_max_chars,
                    )
                    for index, (candidate, saved_item) in enumerate(
                        zip(ordered_candidates, saved_items, strict=True),
                        start=1,
                    )
                ],
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

    def _effective_top_k(self, requested: int | None) -> int:
        value = requested or self.settings.retrieval_top_k_default
        return min(value, self.settings.retrieval_top_k_max, 20)

    def _effective_rerank_top_n(self, requested: int | None) -> int:
        value = requested or self.settings.rerank_top_n_default
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
    )


def _retrieval_filters(payload: RagSearchRequest) -> RetrievalFilters:
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
        top1_retrieval_score=(_round_score(retrieval_scores[0]) if retrieval_scores else None),
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


def _snippet(text: str, *, max_chars: int) -> str:
    cleaned = " ".join(text.replace("\x00", " ").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[: max_chars - 3]}..."


def _sanitize_label(value: str) -> str:
    printable = "".join(char if char.isprintable() else " " for char in value)
    return " ".join(printable.split())


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
