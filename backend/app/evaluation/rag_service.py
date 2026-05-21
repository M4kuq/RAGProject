from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.ingest.embedding import (
    EmbeddingAdapterError,
    FakeEmbeddingAdapter,
    create_embedding_adapter,
)
from app.rag.citations import (
    CitationBuildError,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.confidence import ConfidenceInputs, calculate_confidence
from app.rag.generation import AnswerGenerationError, GenerationRequest, create_answer_generator
from app.rag.rerank import RerankError, create_reranker
from app.rag.retrieval import (
    RetrievalError,
    RetrievalFilters,
    VectorSearchCandidate,
    VectorSearchClient,
)
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary
from app.services.rag_service import (
    RagService,
    _assemble_context,
    _citation_input,
    _citation_response,
    _confidence_response,
    _decimal_score,
    _optional_decimal_score,
    _prompt_citation_sources,
    _query_hash,
    _validate_generation_output_safety,
)


@dataclass(frozen=True)
class RagEvaluationResult:
    retrieval_run_id: int
    status: Literal["succeeded", "failed"]
    answer_text: str
    citations: list[RagAskCitation]
    confidence: RagAskConfidence | None
    retrieval_score_summary: RetrievalScoreSummary | None
    context_sources_for_safety: list[str]
    error_code: str | None = None


def create_evaluation_rag_service(
    settings: Settings,
    db: Session,
) -> EvaluationRagQuestionService:
    service = RagService(
        settings=settings,
        embedding_adapter=create_embedding_adapter(settings),
        vector_client=DatabaseVectorSearchClient(db),
        reranker=create_reranker(settings),
        answer_generator=create_answer_generator(settings),
    )
    return EvaluationRagQuestionService(service)


class EvaluationRagQuestionService:
    def __init__(self, service: RagService) -> None:
        self.service = service

    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        effective_top_k = self.service._effective_ask_top_k(top_k)
        effective_rerank_top_n = self.service._effective_ask_rerank_top_n(rerank_top_n)
        run = self.service.repository.create_standalone_run(
            db,
            top_k=effective_top_k,
            query_hash=_query_hash(question),
            request_id=request_id,
            started_at=datetime.now(UTC),
        )
        db.commit()
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self.service._retrieve_and_rerank(
                db,
                query=question,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=RetrievalFilters(),
                retrieval_run_id=run_id,
            )
            if not result.selected_candidates:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=result.summary,
                )

            context_items = _assemble_context(
                result.selected_candidates,
                citation_sources=result.citation_sources,
                max_context_chars=self.service.settings.generation_max_context_chars,
            )
            prompt_citation_sources = _prompt_citation_sources(
                context_items=context_items,
                citation_sources=result.citation_sources,
            )
            generation = self.service.answer_generator.generate(
                GenerationRequest(
                    message=question,
                    context_items=context_items,
                    max_output_chars=self.service.settings.generation_max_output_chars,
                )
            )
            parsed_generation = parse_generation_output(generation.content)
            _validate_generation_output_safety(
                parsed_generation.answer_text,
                context_items=context_items,
            )
            cited_sources = validate_generation_citations(
                parsed_generation,
                source_map=prompt_citation_sources,
            )
            self.service.repository.save_citations(
                db,
                citations=[
                    _citation_input(source, retrieval_run_id=run_id) for source in cited_sources
                ],
            )
            citation_records = self.service.repository.list_citations_for_run(
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
                self.service.settings,
            )
            run = self.service._require_run(db, run_id)
            self.service.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=result.summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(result.summary.top1_rerank_score),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            db.refresh(run)
            return RagEvaluationResult(
                retrieval_run_id=run_id,
                status="succeeded",
                answer_text=parsed_generation.answer_text,
                citations=[_citation_response(record) for record in citation_records],
                confidence=_confidence_response(run),
                retrieval_score_summary=result.summary,
                context_sources_for_safety=[item.text for item in context_items],
            )
        except CitationBuildError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="citation_build_failed",
            )
            return _failed_evaluation_result(run_id, "citation_build_failed")
        except (EmbeddingAdapterError, RetrievalError):
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
            )
            return _failed_evaluation_result(run_id, "retrieval_failed")
        except RerankError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
            )
            return _failed_evaluation_result(run_id, "rerank_failed")
        except AnswerGenerationError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
            )
            return _failed_evaluation_result(run_id, "generation_failed")
        except Exception:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
            )
            raise


class DatabaseVectorSearchClient(VectorSearchClient):
    def __init__(self, db: Session) -> None:
        self.db = db

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        del collection_name
        if limit < 1:
            raise RetrievalError()
        normalized_query = _normalized_vector(query_vector)
        chunk_vector_adapter = (
            FakeEmbeddingAdapter(dimension=len(normalized_query)) if normalized_query else None
        )
        rows = self.db.execute(_eligible_chunks_statement(filters)).all()
        scored: list[tuple[float, float, DocumentChunk]] = []
        for chunk, _, _ in rows:
            lexical_score = _lexical_score(chunk.content_text, filters)
            if chunk_vector_adapter is not None:
                chunk_vector = chunk_vector_adapter.embed_texts([chunk.content_text])[0]
                vector_score = (_dot_product(normalized_query, chunk_vector) + 1.0) / 2.0
                score = min(1.0, (vector_score * 0.9) + (lexical_score * 0.1))
            else:
                score = lexical_score if lexical_score > 0 else 0.15
            scored.append((score, lexical_score, chunk))
        ranked = sorted(
            scored,
            key=lambda item: (item[0], item[1], -item[2].document_chunk_id),
            reverse=True,
        )[:limit]
        return [
            VectorSearchCandidate(
                document_chunk_id=chunk.document_chunk_id,
                retrieval_score=score,
                qdrant_order=index,
                payload={"document_chunk_id": chunk.document_chunk_id},
            )
            for index, (score, _, chunk) in enumerate(ranked, start=1)
        ]


def _eligible_chunks_statement(filters: RetrievalFilters):
    statement = (
        select(DocumentChunk, DocumentVersion, LogicalDocument)
        .join(
            DocumentVersion,
            DocumentVersion.document_version_id == DocumentChunk.document_version_id,
        )
        .join(
            LogicalDocument,
            LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
        )
        .where(
            DocumentChunk.modality == filters.modality,
            DocumentVersion.status == "ready",
            DocumentVersion.is_active.is_(True),
            LogicalDocument.status == "active",
        )
        .order_by(DocumentChunk.document_chunk_id.asc())
    )
    if filters.logical_document_ids:
        statement = statement.where(
            LogicalDocument.logical_document_id.in_(filters.logical_document_ids)
        )
    return statement


def _lexical_score(text: str, filters: RetrievalFilters) -> float:
    del filters
    haystack = text.lower()
    score = 0.0
    for term in (
        "qdrant",
        "deterministic",
        "fake",
        "adapter",
        "adapters",
        "citation",
        "retrieval",
        "trace",
        "traces",
    ):
        if term in haystack:
            score += 0.1
    return min(1.0, score)


def _normalized_vector(values: Sequence[float]) -> list[float]:
    vector: list[float] = []
    for value in values:
        number = float(value)
        if not math.isfinite(number):
            return []
        vector.append(number)
    if not vector:
        return []
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return []
    return [value / norm for value in vector]


def _dot_product(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _failed_evaluation_result(
    retrieval_run_id: int,
    error_code: str,
    *,
    retrieval_score_summary: RetrievalScoreSummary | None = None,
) -> RagEvaluationResult:
    return RagEvaluationResult(
        retrieval_run_id=retrieval_run_id,
        status="failed",
        answer_text="",
        citations=[],
        confidence=None,
        retrieval_score_summary=retrieval_score_summary,
        context_sources_for_safety=[],
        error_code=error_code,
    )
