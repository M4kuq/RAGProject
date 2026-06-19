from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument, RetrievalRun
from app.evaluation.metrics import RetrievedEvaluationItem
from app.ingest.embedding import (
    EmbeddingAdapterError,
    FakeEmbeddingAdapter,
    create_embedding_adapter,
)
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.confidence import ConfidenceInputs, calculate_confidence
from app.rag.context_budget import estimate_tokens
from app.rag.generation import (
    AnswerGenerationError,
    GenerationContextItem,
    GenerationRequest,
    create_answer_generator,
)
from app.rag.rerank import RerankError, create_reranker
from app.rag.retrieval import (
    RetrievalError,
    RetrievalFilters,
    VectorSearchCandidate,
    VectorSearchClient,
)
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RagSearchRequestStrategy, RetrievalStrategy
from app.rag.trace import LatencyTracker
from app.schemas.rag import (
    RagAskCitation,
    RagAskConfidence,
    RagSearchItem,
    RagSearchRequest,
    RetrievalScoreSummary,
)
from app.services.graph_rag_service import GraphRagService
from app.services.rag_service import (
    ContextCandidateRef,
    RagSearchPipelineError,
    RagService,
    _assemble_context,
    _citation_input,
    _citation_response,
    _confidence_response,
    _context_citation_sources,
    _context_refs_for_citation_sources,
    _decimal_score,
    _is_insufficient_evidence_answer,
    _optional_decimal_score,
    _prompt_citation_sources,
    _query_hash,
    _retrieval_settings_snapshot,
    _selected_context_refs,
    _summary_with_final_context_refs,
    _validate_generation_output_safety,
    build_langchain_agentic_query_plan,
    build_langchain_agentic_strategy_decision,
    build_langgraph_agentic_query_plan,
    build_langgraph_agentic_strategy_decision,
    build_llm_tool_orchestrator_query_plan,
    build_llm_tool_orchestrator_strategy_decision,
)

RETRIEVAL_CACHE_NAMESPACE_MAX_LENGTH = 80
RETRIEVAL_ONLY_EVALUATION_TARGET_STRATEGIES = frozenset(
    {
        RetrievalStrategy.DENSE,
        RetrievalStrategy.SPARSE,
        RetrievalStrategy.HYBRID,
        RetrievalStrategy.GRAPH,
        RetrievalStrategy.AGENTIC_ROUTER,
    }
)


@dataclass(frozen=True)
class RagEvaluationResult:
    retrieval_run_id: int | None
    status: Literal["succeeded", "failed"]
    answer_text: str
    citations: list[RagAskCitation]
    confidence: RagAskConfidence | None
    retrieval_score_summary: RetrievalScoreSummary | None
    retrieved_items: list[RetrievedEvaluationItem]
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
    return EvaluationRagQuestionService(service, graph_service=GraphRagService(service))


class EvaluationRagQuestionService:
    def __init__(self, service: RagService, graph_service: GraphRagService | None = None) -> None:
        self.service = service
        self.graph_service = graph_service or GraphRagService(service)

    def evaluate_strategy_target(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        target: object,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
        evaluation_run_id: int | None = None,
        cache_attempt_id: str | None = None,
    ) -> RagEvaluationResult:
        raw_strategy = getattr(target, "retrieval_strategy", DEFAULT_RETRIEVAL_STRATEGY)
        strategy_type = (
            raw_strategy
            if isinstance(raw_strategy, RetrievalStrategy)
            else RetrievalStrategy(str(raw_strategy))
        )
        cache_mode = str(getattr(target, "cache_mode", "default"))
        graph_store_provider = getattr(target, "graph_store_provider", None)
        with _evaluation_target_settings(
            self.service,
            strategy_type=strategy_type,
            graph_store_provider=graph_store_provider
            if isinstance(graph_store_provider, str)
            else None,
            cache_mode=cache_mode,
            evaluation_run_id=evaluation_run_id,
            cache_attempt_id=cache_attempt_id,
        ):
            if strategy_type in RETRIEVAL_ONLY_EVALUATION_TARGET_STRATEGIES:
                return self.evaluate_strategy(
                    db,
                    question=question,
                    request_id=request_id,
                    strategy_type=strategy_type,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                )
            return self.answer_question_with_strategy(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )

    def _no_context_result_if_insufficient_answer(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        answer_text: str,
        retrieval_score_summary: RetrievalScoreSummary | None = None,
        latency_tracker: LatencyTracker | None = None,
        rollback: bool = True,
    ) -> RagEvaluationResult | None:
        if not _is_insufficient_evidence_answer(answer_text):
            return None
        self.service._mark_failed_safely(
            db,
            retrieval_run_id=retrieval_run_id,
            error_code="no_context_found",
            latency_tracker=latency_tracker,
            rollback=rollback,
        )
        return _failed_evaluation_result(
            retrieval_run_id,
            "no_context_found",
            retrieval_score_summary=retrieval_score_summary,
        )

    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        if strategy_type != DEFAULT_RETRIEVAL_STRATEGY:
            return self.answer_question_with_strategy(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )

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
            if no_context_result := self._no_context_result_if_insufficient_answer(
                db,
                retrieval_run_id=run_id,
                answer_text=parsed_generation.answer_text,
                retrieval_score_summary=result.summary,
            ):
                return no_context_result
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
                retrieved_items=[],
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

    def answer_question_with_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        if strategy_type == RetrievalStrategy.GRAPH:
            return self._answer_question_with_graph(
                db,
                question=question,
                request_id=request_id,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        if strategy_type == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
            return self._answer_question_with_llm_tool_orchestrator(
                db,
                question=question,
                request_id=request_id,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        if strategy_type == RetrievalStrategy.LANGCHAIN_AGENTIC:
            return self._answer_question_with_langchain_agentic(
                db,
                question=question,
                request_id=request_id,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        if strategy_type == RetrievalStrategy.LANGGRAPH_AGENTIC:
            return self._answer_question_with_langgraph_agentic(
                db,
                question=question,
                request_id=request_id,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        try:
            search_service: RagService | GraphRagService = (
                self.graph_service
                if strategy_type in {RetrievalStrategy.AGENTIC_ROUTER, RetrievalStrategy.GRAPH}
                else self.service
            )
            response = search_service.search(
                db,
                payload=RagSearchRequest(
                    query=question,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    strategy=RagSearchRequestStrategy(strategy_type.value),
                ),
                request_id=request_id,
            )
            if not response.items:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=response.retrieval_run_id,
                    error_code="no_context_found",
                )
                return _failed_evaluation_result(
                    response.retrieval_run_id,
                    "no_context_found",
                    retrieval_score_summary=response.retrieval_score_summary,
                )
            citation_sources = [
                _citation_source_from_search_item(index, item)
                for index, item in enumerate(response.items, start=1)
            ]
            context_items = _context_items_from_search_items(
                db,
                response.items,
                max_context_chars=self.service.settings.generation_max_context_chars,
            )
            prompt_citation_sources = _prompt_citation_sources(
                context_items=context_items,
                citation_sources=citation_sources,
            )
            if not context_items or not prompt_citation_sources:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=response.retrieval_run_id,
                    error_code="no_context_found",
                )
                return _failed_evaluation_result(
                    response.retrieval_run_id,
                    "no_context_found",
                    retrieval_score_summary=response.retrieval_score_summary,
                )
            generation = self.service.answer_generator.generate(
                GenerationRequest(
                    message=question,
                    context_items=context_items,
                    max_output_chars=self.service.settings.generation_max_output_chars,
                )
            )
            parsed_generation = parse_generation_output(generation.content)
            if no_context_result := self._no_context_result_if_insufficient_answer(
                db,
                retrieval_run_id=response.retrieval_run_id,
                answer_text=parsed_generation.answer_text,
                retrieval_score_summary=response.retrieval_score_summary,
            ):
                return no_context_result
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
                    _citation_input(source, retrieval_run_id=response.retrieval_run_id)
                    for source in cited_sources
                ],
            )
            citation_records = self.service.repository.list_citations_for_run(
                db,
                retrieval_run_id=response.retrieval_run_id,
            )
            confidence = calculate_confidence(
                ConfidenceInputs(
                    retrieval_score_summary=response.retrieval_score_summary,
                    marker_count=len(parsed_generation.markers),
                    unique_citation_count=len(cited_sources),
                    selected_count=len(prompt_citation_sources),
                ),
                self.service.settings,
            )
            run = self.service._require_run(db, response.retrieval_run_id)
            self.service.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=response.retrieval_score_summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(
                    response.retrieval_score_summary.top1_rerank_score
                ),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            db.refresh(run)
            return RagEvaluationResult(
                retrieval_run_id=response.retrieval_run_id,
                status="succeeded",
                answer_text=parsed_generation.answer_text,
                citations=[_citation_response(record) for record in citation_records],
                confidence=_confidence_response(run),
                retrieval_score_summary=response.retrieval_score_summary,
                retrieved_items=[_retrieved_item_from_search_item(item) for item in response.items],
                context_sources_for_safety=[item.text for item in context_items],
            )
        except CitationBuildError:
            _mark_latest_failed_safely(
                self.service,
                db,
                request_id=request_id,
                error_code="citation_build_failed",
            )
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                "citation_build_failed",
            )
        except AnswerGenerationError:
            _mark_latest_failed_safely(
                self.service,
                db,
                request_id=request_id,
                error_code="generation_failed",
            )
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                "generation_failed",
            )
        except RagSearchPipelineError as exc:
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                exc.error_code,
            )

    def _answer_question_with_graph(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None,
        rerank_top_n: int | None,
    ) -> RagEvaluationResult:
        try:
            response = self.graph_service.search(
                db,
                payload=RagSearchRequest(
                    query=question,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    strategy=RagSearchRequestStrategy.GRAPH,
                ),
                request_id=request_id,
            )
            if not response.items:
                if _graph_provider_unavailable(response.retrieval_score_summary):
                    return _failed_evaluation_result(
                        response.retrieval_run_id,
                        "graph_provider_skipped",
                        retrieval_score_summary=response.retrieval_score_summary,
                    )
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=response.retrieval_run_id,
                    error_code="no_context_found",
                )
                return _failed_evaluation_result(
                    response.retrieval_run_id,
                    "no_context_found",
                    retrieval_score_summary=response.retrieval_score_summary,
                )
            citation_sources = [
                _citation_source_from_search_item(index, item)
                for index, item in enumerate(response.items, start=1)
            ]
            context_items = _context_items_from_search_items(
                db,
                response.items,
                max_context_chars=self.service.settings.generation_max_context_chars,
            )
            prompt_citation_sources = _prompt_citation_sources(
                context_items=context_items,
                citation_sources=citation_sources,
            )
            if not context_items or not prompt_citation_sources:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=response.retrieval_run_id,
                    error_code="no_context_found",
                )
                return _failed_evaluation_result(
                    response.retrieval_run_id,
                    "no_context_found",
                    retrieval_score_summary=response.retrieval_score_summary,
                )
            generation = self.service.answer_generator.generate(
                GenerationRequest(
                    message=question,
                    context_items=context_items,
                    max_output_chars=self.service.settings.generation_max_output_chars,
                )
            )
            parsed_generation = parse_generation_output(generation.content)
            if no_context_result := self._no_context_result_if_insufficient_answer(
                db,
                retrieval_run_id=response.retrieval_run_id,
                answer_text=parsed_generation.answer_text,
                retrieval_score_summary=response.retrieval_score_summary,
            ):
                return no_context_result
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
                    _citation_input(source, retrieval_run_id=response.retrieval_run_id)
                    for source in cited_sources
                ],
            )
            citation_records = self.service.repository.list_citations_for_run(
                db,
                retrieval_run_id=response.retrieval_run_id,
            )
            confidence = calculate_confidence(
                ConfidenceInputs(
                    retrieval_score_summary=response.retrieval_score_summary,
                    marker_count=len(parsed_generation.markers),
                    unique_citation_count=len(cited_sources),
                    selected_count=len(prompt_citation_sources),
                ),
                self.service.settings,
            )
            run = self.service._require_run(db, response.retrieval_run_id)
            self.service.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=response.retrieval_score_summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(
                    response.retrieval_score_summary.top1_rerank_score
                ),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
            )
            db.commit()
            db.refresh(run)
            return RagEvaluationResult(
                retrieval_run_id=response.retrieval_run_id,
                status="succeeded",
                answer_text=parsed_generation.answer_text,
                citations=[_citation_response(record) for record in citation_records],
                confidence=_confidence_response(run),
                retrieval_score_summary=response.retrieval_score_summary,
                retrieved_items=[_retrieved_item_from_search_item(item) for item in response.items],
                context_sources_for_safety=[item.text for item in context_items],
            )
        except CitationBuildError:
            _mark_latest_failed_safely(
                self.service,
                db,
                request_id=request_id,
                error_code="citation_build_failed",
            )
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                "citation_build_failed",
            )
        except AnswerGenerationError:
            _mark_latest_failed_safely(
                self.service,
                db,
                request_id=request_id,
                error_code="generation_failed",
            )
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                "generation_failed",
            )
        except RagSearchPipelineError as exc:
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                exc.error_code,
            )

    def _answer_question_with_llm_tool_orchestrator(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None,
        rerank_top_n: int | None,
    ) -> RagEvaluationResult:
        if not self.service.settings.llm_orchestrator_enabled:
            return _failed_evaluation_result(None, "strategy_not_enabled")

        effective_top_k = self.service._effective_ask_top_k(top_k)
        effective_rerank_top_n = self.service._effective_ask_rerank_top_n(rerank_top_n)
        filters = RetrievalFilters()
        query_hash = _query_hash(question)
        query_plan_build = self.service.query_plan_builder.build(
            question,
            filters=filters,
            requested_strategy=RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
        )
        retrieval_query = query_plan_build.retrieval_query
        latency_tracker = LatencyTracker()
        run = self.service.repository.create_standalone_run(
            db,
            top_k=effective_top_k,
            query_hash=query_hash,
            request_id=request_id,
            started_at=datetime.now(UTC),
            strategy_type=RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
            query_plan_json=build_llm_tool_orchestrator_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            ),
            strategy_decision_json=build_llm_tool_orchestrator_strategy_decision(),
            retrieval_settings_json=_retrieval_settings_snapshot(
                settings=self.service.settings,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=filters,
                strategy_type=RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
            ),
        )
        db.commit()
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self.service._retrieve_llm_tool_orchestrator(
                db,
                query=retrieval_query,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                latency_tracker=latency_tracker,
            )
            context_budget_decision = self.service._apply_context_budget(
                db,
                retrieval_run_id=run_id,
                result=result,
                estimated_prompt_tokens=estimate_tokens(question),
            )
            selected_context_refs = _selected_context_refs(
                result.context_candidates,
                context_budget_decision,
            )
            selected_citation_sources = _context_citation_sources(
                selected_context_refs,
                snippet_max_chars=self.service.settings.citation_preview_max_chars,
            )
            with latency_tracker.span("evidence_pack_ms"):
                evidence_pack = self.service._build_evidence_pack(
                    db,
                    retrieval_run_id=run_id,
                    selected_context_refs=selected_context_refs,
                    selected_citation_sources=selected_citation_sources,
                    candidate_context_items=context_budget_decision.trace.items.candidate_count,
                )
            if result.no_context or not evidence_pack.items:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=result.summary,
                )
            with latency_tracker.span("context_assembly_ms"):
                context_items = evidence_pack.to_generation_context_items()
                prompt_citation_sources = _prompt_citation_sources(
                    context_items=context_items,
                    citation_sources=selected_citation_sources,
                )
                prompt_context_refs = _context_refs_for_citation_sources(
                    selected_context_refs,
                    prompt_citation_sources,
                )
                self.service._finalize_context_budget_after_assembly(
                    db,
                    retrieval_run_id=run_id,
                    decision=context_budget_decision,
                    prompt_context_refs=prompt_context_refs,
                )
                final_summary = _summary_with_final_context_refs(
                    result.summary,
                    prompt_context_refs,
                )
            with latency_tracker.span("generation_ms"):
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
            if _is_insufficient_evidence_answer(parsed_generation.answer_text):
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=final_summary,
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
                    retrieval_score_summary=final_summary,
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
                retrieval_score_summary=final_summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(final_summary.top1_rerank_score),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
                latency_breakdown_json=latency_tracker.snapshot(),
            )
            db.commit()
            db.refresh(run)
            return RagEvaluationResult(
                retrieval_run_id=run_id,
                status="succeeded",
                answer_text=parsed_generation.answer_text,
                citations=[_citation_response(record) for record in citation_records],
                confidence=_confidence_response(run),
                retrieval_score_summary=final_summary,
                retrieved_items=[
                    _retrieved_item_from_context_ref(
                        ref,
                        rank_order=rank_order,
                        snippet_max_chars=self.service.settings.search_snippet_max_chars,
                    )
                    for rank_order, ref in enumerate(selected_context_refs, start=1)
                ],
                context_sources_for_safety=[item.text for item in context_items],
            )
        except CitationBuildError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="citation_build_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "citation_build_failed")
        except AnswerGenerationError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "generation_failed")
        except (EmbeddingAdapterError, RetrievalError):
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "retrieval_failed")
        except RerankError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "rerank_failed")

    def evaluate_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        try:
            search_service: RagService | GraphRagService = (
                self.graph_service
                if strategy_type in {RetrievalStrategy.AGENTIC_ROUTER, RetrievalStrategy.GRAPH}
                else self.service
            )
            response = search_service.search(
                db,
                payload=RagSearchRequest(
                    query=question,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    strategy=RagSearchRequestStrategy(strategy_type.value),
                ),
                request_id=request_id,
            )
            citations = [
                _citation_from_search_item(index, item)
                for index, item in enumerate(response.items, start=1)
            ]
            retrieved_items = [_retrieved_item_from_search_item(item) for item in response.items]
            return RagEvaluationResult(
                retrieval_run_id=response.retrieval_run_id,
                status="succeeded",
                answer_text="",
                citations=citations,
                confidence=None,
                retrieval_score_summary=response.retrieval_score_summary,
                retrieved_items=retrieved_items,
                context_sources_for_safety=[],
                error_code=None if response.items else "no_context_found",
            )
        except RagSearchPipelineError as exc:
            return _failed_evaluation_result(
                _latest_retrieval_run_id(db, request_id=request_id),
                exc.error_code,
            )

    def _answer_question_with_langchain_agentic(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None,
        rerank_top_n: int | None,
    ) -> RagEvaluationResult:
        if not self.service.settings.langchain_agentic_enabled:
            return _failed_evaluation_result(None, "strategy_not_enabled")

        effective_top_k = self.service._effective_ask_top_k(top_k)
        effective_rerank_top_n = self.service._effective_ask_rerank_top_n(rerank_top_n)
        filters = RetrievalFilters()
        query_hash = _query_hash(question)
        query_plan_build = self.service.query_plan_builder.build(
            question,
            filters=filters,
            requested_strategy=RetrievalStrategy.LANGCHAIN_AGENTIC,
        )
        retrieval_query = query_plan_build.retrieval_query
        latency_tracker = LatencyTracker()
        run = self.service.repository.create_standalone_run(
            db,
            top_k=effective_top_k,
            query_hash=query_hash,
            request_id=request_id,
            started_at=datetime.now(UTC),
            strategy_type=RetrievalStrategy.LANGCHAIN_AGENTIC.value,
            query_plan_json=build_langchain_agentic_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            ),
            strategy_decision_json=build_langchain_agentic_strategy_decision(),
            retrieval_settings_json=_retrieval_settings_snapshot(
                settings=self.service.settings,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=filters,
                strategy_type=RetrievalStrategy.LANGCHAIN_AGENTIC,
            ),
        )
        db.commit()
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self.service._retrieve_langchain_agentic(
                db,
                query=retrieval_query,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                latency_tracker=latency_tracker,
            )
            context_budget_decision = self.service._apply_context_budget(
                db,
                retrieval_run_id=run_id,
                result=result,
                estimated_prompt_tokens=estimate_tokens(question),
            )
            selected_context_refs = _selected_context_refs(
                result.context_candidates,
                context_budget_decision,
            )
            selected_citation_sources = _context_citation_sources(
                selected_context_refs,
                snippet_max_chars=self.service.settings.citation_preview_max_chars,
            )
            with latency_tracker.span("evidence_pack_ms"):
                evidence_pack = self.service._build_evidence_pack(
                    db,
                    retrieval_run_id=run_id,
                    selected_context_refs=selected_context_refs,
                    selected_citation_sources=selected_citation_sources,
                    candidate_context_items=context_budget_decision.trace.items.candidate_count,
                )
            if result.no_context or not evidence_pack.items:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=result.summary,
                )
            with latency_tracker.span("context_assembly_ms"):
                context_items = evidence_pack.to_generation_context_items()
                prompt_citation_sources = _prompt_citation_sources(
                    context_items=context_items,
                    citation_sources=selected_citation_sources,
                )
                prompt_context_refs = _context_refs_for_citation_sources(
                    selected_context_refs,
                    prompt_citation_sources,
                )
                self.service._finalize_context_budget_after_assembly(
                    db,
                    retrieval_run_id=run_id,
                    decision=context_budget_decision,
                    prompt_context_refs=prompt_context_refs,
                )
                final_summary = _summary_with_final_context_refs(
                    result.summary,
                    prompt_context_refs,
                )
            with latency_tracker.span("generation_ms"):
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
            if _is_insufficient_evidence_answer(parsed_generation.answer_text):
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=final_summary,
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
                    retrieval_score_summary=final_summary,
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
                retrieval_score_summary=final_summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(final_summary.top1_rerank_score),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
                latency_breakdown_json=latency_tracker.snapshot(),
            )
            db.commit()
            db.refresh(run)
            return RagEvaluationResult(
                retrieval_run_id=run_id,
                status="succeeded",
                answer_text=parsed_generation.answer_text,
                citations=[_citation_response(record) for record in citation_records],
                confidence=_confidence_response(run),
                retrieval_score_summary=final_summary,
                retrieved_items=[
                    _retrieved_item_from_context_ref(
                        ref,
                        rank_order=rank_order,
                        snippet_max_chars=self.service.settings.search_snippet_max_chars,
                    )
                    for rank_order, ref in enumerate(selected_context_refs, start=1)
                ],
                context_sources_for_safety=[item.text for item in context_items],
            )
        except CitationBuildError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="citation_build_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "citation_build_failed")
        except AnswerGenerationError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "generation_failed")
        except (EmbeddingAdapterError, RetrievalError):
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "retrieval_failed")
        except RerankError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "rerank_failed")
        except Exception:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
                latency_tracker=latency_tracker,
            )
            raise

    def _answer_question_with_langgraph_agentic(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None,
        rerank_top_n: int | None,
    ) -> RagEvaluationResult:
        if not self.service.settings.langgraph_agentic_enabled:
            return _failed_evaluation_result(None, "strategy_not_enabled")

        effective_top_k = self.service._effective_ask_top_k(top_k)
        effective_rerank_top_n = self.service._effective_ask_rerank_top_n(rerank_top_n)
        filters = RetrievalFilters()
        query_hash = _query_hash(question)
        query_plan_build = self.service.query_plan_builder.build(
            question,
            filters=filters,
            requested_strategy=RetrievalStrategy.LANGGRAPH_AGENTIC,
        )
        retrieval_query = query_plan_build.retrieval_query
        latency_tracker = LatencyTracker()
        run = self.service.repository.create_standalone_run(
            db,
            top_k=effective_top_k,
            query_hash=query_hash,
            request_id=request_id,
            started_at=datetime.now(UTC),
            strategy_type=RetrievalStrategy.LANGGRAPH_AGENTIC.value,
            query_plan_json=build_langgraph_agentic_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            ),
            strategy_decision_json=build_langgraph_agentic_strategy_decision(),
            retrieval_settings_json=_retrieval_settings_snapshot(
                settings=self.service.settings,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=filters,
                strategy_type=RetrievalStrategy.LANGGRAPH_AGENTIC,
            ),
        )
        db.commit()
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self.service._retrieve_langgraph_agentic(
                db,
                query=retrieval_query,
                top_k=effective_top_k,
                rerank_top_n=effective_rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                latency_tracker=latency_tracker,
            )
            context_budget_decision = self.service._apply_context_budget(
                db,
                retrieval_run_id=run_id,
                result=result,
                estimated_prompt_tokens=estimate_tokens(question),
            )
            selected_context_refs = _selected_context_refs(
                result.context_candidates,
                context_budget_decision,
            )
            selected_citation_sources = _context_citation_sources(
                selected_context_refs,
                snippet_max_chars=self.service.settings.citation_preview_max_chars,
            )
            with latency_tracker.span("evidence_pack_ms"):
                evidence_pack = self.service._build_evidence_pack(
                    db,
                    retrieval_run_id=run_id,
                    selected_context_refs=selected_context_refs,
                    selected_citation_sources=selected_citation_sources,
                    candidate_context_items=context_budget_decision.trace.items.candidate_count,
                )
            if result.no_context or not evidence_pack.items:
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=result.summary,
                )
            with latency_tracker.span("context_assembly_ms"):
                context_items = evidence_pack.to_generation_context_items()
                prompt_citation_sources = _prompt_citation_sources(
                    context_items=context_items,
                    citation_sources=selected_citation_sources,
                )
                prompt_context_refs = _context_refs_for_citation_sources(
                    selected_context_refs,
                    prompt_citation_sources,
                )
                self.service._finalize_context_budget_after_assembly(
                    db,
                    retrieval_run_id=run_id,
                    decision=context_budget_decision,
                    prompt_context_refs=prompt_context_refs,
                )
                final_summary = _summary_with_final_context_refs(
                    result.summary,
                    prompt_context_refs,
                )
            with latency_tracker.span("generation_ms"):
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
            if _is_insufficient_evidence_answer(parsed_generation.answer_text):
                self.service._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                return _failed_evaluation_result(
                    run_id,
                    "no_context_found",
                    retrieval_score_summary=final_summary,
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
                    retrieval_score_summary=final_summary,
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
                retrieval_score_summary=final_summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(final_summary.top1_rerank_score),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
                latency_breakdown_json=latency_tracker.snapshot(),
            )
            db.commit()
            db.refresh(run)
            return RagEvaluationResult(
                retrieval_run_id=run_id,
                status="succeeded",
                answer_text=parsed_generation.answer_text,
                citations=[_citation_response(record) for record in citation_records],
                confidence=_confidence_response(run),
                retrieval_score_summary=final_summary,
                retrieved_items=[
                    _retrieved_item_from_context_ref(
                        ref,
                        rank_order=rank_order,
                        snippet_max_chars=self.service.settings.search_snippet_max_chars,
                    )
                    for rank_order, ref in enumerate(selected_context_refs, start=1)
                ],
                context_sources_for_safety=[item.text for item in context_items],
            )
        except CitationBuildError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="citation_build_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "citation_build_failed")
        except AnswerGenerationError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "generation_failed")
        except (EmbeddingAdapterError, RetrievalError):
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "retrieval_failed")
        except RerankError:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            return _failed_evaluation_result(run_id, "rerank_failed")
        except Exception:
            self.service._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
                latency_tracker=latency_tracker,
            )
            raise


def _citation_from_search_item(index: int, item: RagSearchItem) -> RagAskCitation:
    return RagAskCitation(
        citation_id=index,
        local_citation_id=index,
        document_chunk_id=item.document_chunk_id,
        source_label=item.source_label,
        snippet=item.snippet,
        page_from=item.page_from,
        page_to=item.page_to,
        old_version_flag=False,
    )


def _retrieved_item_from_search_item(item: RagSearchItem) -> RetrievedEvaluationItem:
    return RetrievedEvaluationItem(
        document_chunk_id=item.document_chunk_id,
        logical_document_id=_safe_int(item.payload_snapshot.get("logical_document_id")),
        rank_order=item.rank_order,
        snippet=item.snippet,
    )


def _retrieved_item_from_context_ref(
    ref: ContextCandidateRef,
    *,
    rank_order: int,
    snippet_max_chars: int,
) -> RetrievedEvaluationItem:
    return RetrievedEvaluationItem(
        document_chunk_id=ref.candidate.chunk.document_chunk_id,
        logical_document_id=ref.candidate.logical_document.logical_document_id,
        rank_order=rank_order,
        snippet=_clean_context_text(ref.candidate.chunk.content_text)[:snippet_max_chars],
    )


def _context_items_from_search_items(
    db: Session,
    items: list[RagSearchItem],
    *,
    max_context_chars: int,
) -> list[GenerationContextItem]:
    chunk_ids = [item.document_chunk_id for item in items]
    chunks = {
        chunk.document_chunk_id: chunk
        for chunk in db.scalars(
            select(DocumentChunk).where(DocumentChunk.document_chunk_id.in_(chunk_ids))
        ).all()
    }
    remaining = max_context_chars
    context_items: list[GenerationContextItem] = []
    for index, item in enumerate(items, start=1):
        if remaining <= 0:
            break
        chunk = chunks.get(item.document_chunk_id)
        if chunk is None:
            continue
        text = _clean_context_text(chunk.content_text)
        if not text:
            continue
        clipped = text[:remaining]
        remaining -= len(clipped)
        context_items.append(
            GenerationContextItem(
                document_chunk_id=item.document_chunk_id,
                source_label=item.source_label,
                text=clipped,
                local_citation_id=index,
                page_from=item.page_from,
                page_to=item.page_to,
            )
        )
    return context_items


def _citation_source_from_search_item(index: int, item: RagSearchItem) -> CitationSource:
    payload = item.payload_snapshot
    source_type = "external_url" if payload.get("source_type") == "url" else "upload"
    source_url = payload.get("safe_source_url") or payload.get("source_url")
    return CitationSource(
        local_citation_id=index,
        retrieval_run_item_id=item.retrieval_run_item_id,
        document_chunk_id=item.document_chunk_id,
        source_label=item.source_label,
        snippet=item.snippet,
        page_from=item.page_from,
        page_to=item.page_to,
        section_title=_safe_string(payload.get("section_title")),
        source_type=source_type,
        source_url=source_url if isinstance(source_url, str) else None,
    )


def _clean_context_text(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())


def _safe_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.replace("\x00", " ").split())
    return cleaned or None


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _latest_retrieval_run_id(db: Session, *, request_id: str | None) -> int | None:
    if request_id is None:
        return None
    run = db.scalar(
        select(RetrievalRun)
        .where(RetrievalRun.request_id == request_id)
        .order_by(RetrievalRun.created_at.desc(), RetrievalRun.retrieval_run_id.desc())
    )
    return run.retrieval_run_id if run is not None else None


@contextmanager
def _evaluation_target_settings(
    service: RagService,
    *,
    strategy_type: RetrievalStrategy,
    graph_store_provider: str | None,
    cache_mode: str,
    evaluation_run_id: int | None,
    cache_attempt_id: str | None,
):
    settings = service.settings
    overrides: dict[str, object] = {}
    try:
        if strategy_type == RetrievalStrategy.GRAPH:
            overrides["graph_retrieval_enabled"] = True
            overrides["graph_store_provider"] = (
                graph_store_provider
                if graph_store_provider in {"postgres", "neo4j"}
                else "postgres"
            )
        if cache_mode == "disabled":
            overrides["retrieval_cache_enabled"] = False
        elif cache_mode in {"cold", "warm"}:
            overrides["retrieval_cache_enabled"] = True
            if evaluation_run_id is not None:
                namespace = settings.retrieval_cache_namespace
                suffix = f"{evaluation_run_id}.{cache_attempt_id or 'single'}"
                overrides["retrieval_cache_namespace"] = _evaluation_cache_namespace(
                    namespace,
                    suffix,
                )
        if overrides:
            service.settings = settings.model_copy(update=overrides)
        yield
    finally:
        service.settings = settings


def _evaluation_cache_namespace(namespace: str, suffix: str) -> str:
    eval_suffix = f".eval.{suffix}"
    if len(namespace) + len(eval_suffix) <= RETRIEVAL_CACHE_NAMESPACE_MAX_LENGTH:
        return f"{namespace}{eval_suffix}"
    namespace_hash = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:8]
    hash_component = f".{namespace_hash}"
    prefix_budget = RETRIEVAL_CACHE_NAMESPACE_MAX_LENGTH - len(eval_suffix) - len(hash_component)
    if prefix_budget <= 0:
        return f"{namespace_hash}{eval_suffix}"[-RETRIEVAL_CACHE_NAMESPACE_MAX_LENGTH:]
    prefix = namespace[:prefix_budget].rstrip(".")
    return f"{prefix}{hash_component}{eval_suffix}"


def _graph_provider_unavailable(summary: RetrievalScoreSummary) -> bool:
    payload = summary.model_dump(mode="json")
    reason_codes = payload.get("graph_reason_codes")
    return isinstance(reason_codes, list) and "graph_store_provider_unavailable" in reason_codes


def _mark_latest_failed_safely(
    service: RagService,
    db: Session,
    *,
    request_id: str | None,
    error_code: str,
) -> None:
    retrieval_run_id = _latest_retrieval_run_id(db, request_id=request_id)
    if retrieval_run_id is None:
        return
    service._mark_failed_safely(
        db,
        retrieval_run_id=retrieval_run_id,
        error_code=error_code,
    )


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
    retrieval_run_id: int | None,
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
        retrieved_items=[],
        context_sources_for_safety=[],
        error_code=error_code,
    )
