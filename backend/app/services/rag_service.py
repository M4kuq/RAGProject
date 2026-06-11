from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    ClientMessageConflict,
    ConflictError,
    RequestInProgress,
    ResourceNotFound,
)
from app.db.models import ChatMessage, RetrievalRun, RetrievalRunItem, User
from app.ingest.embedding import (
    EmbeddingAdapter,
    EmbeddingAdapterError,
    create_embedding_adapter,
)
from app.observability.trace_export import TraceExportService
from app.rag.agentic import (
    AgenticRetrievalExecutor,
    AgenticRetrievalResult,
    ContextSufficiencyChecker,
    RetrievalAttemptResult,
)
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    ParsedGenerationOutput,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.confidence import ConfidenceInputs, calculate_confidence
from app.rag.context_budget import (
    ContextBudgetCandidate,
    ContextBudgetDecision,
    ContextBudgetManager,
    ContextBudgetPolicy,
    ContextBudgetStrategySummary,
    estimate_tokens,
    finalize_context_budget_selection,
    sanitize_context_budget_json,
)
from app.rag.evidence_pack import (
    EvidenceCandidate,
    EvidencePack,
    EvidencePackBuilder,
    EvidencePackPolicy,
    sanitize_context_compression_json,
)
from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    create_answer_generator,
)
from app.rag.hybrid import HybridRetrievalStrategy
from app.rag.injection_detection import (
    INJECTION_PATTERN_REASON_CODE,
    detect_injection_patterns,
)
from app.rag.langchain_agentic import (
    LangChainAgenticExecutionResult,
    LangChainAgenticRetrievalOrchestrator,
)
from app.rag.llm_orchestrator import (
    LLMToolCallingRetrievalOrchestrator,
    LLMToolOrchestratorExecutionResult,
)
from app.rag.query_planner import QueryPlanBuilder
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
    VectorSearchCandidate,
    VectorSearchClient,
)
from app.rag.router import StrategyRouter
from app.rag.sparse import SparseRetrievalStrategy, normalize_sparse_query
from app.rag.strategy import (
    DEFAULT_RETRIEVAL_STRATEGY,
    FusionMethod,
    RetrievalSource,
    RetrievalStrategy,
)
from app.rag.tool_result_compression import (
    ToolResultCompressionTrace,
    attach_retrieval_run_item_ids,
    sanitize_tool_result_compression_json,
)
from app.rag.trace import (
    LatencyTracker,
    TraceRedactor,
    build_default_dense_query_plan,
    build_default_dense_strategy_decision,
    build_dense_score_breakdown,
    build_hybrid_query_plan,
    build_hybrid_score_breakdown,
    build_hybrid_strategy_decision,
    build_retrieval_settings_snapshot,
    build_router_query_plan,
    build_router_strategy_decision,
    build_sparse_query_plan,
    build_sparse_score_breakdown,
    build_sparse_strategy_decision,
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
    RagAskRetrievalSummary,
    RagAskUserMessage,
    RagSearchItem,
    RagSearchRequest,
    RagSearchResponse,
    RetrievalRunDebugItem,
    RetrievalRunDebugListResponse,
    RetrievalRunDebugResponse,
    RetrievalRunDebugSummary,
    RetrievalScoreSummary,
)
from app.services.chat_service import ChatService
from app.services.url_fetch_service import redact_url_for_display

SCORE_QUANT = Decimal("0.000001")
SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*\S{8,}"
    r"|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
)
CITATION_MARKER_RE = re.compile(r"\[(\d{1,6})\]")
MODEL_KEY_SEPARATOR = ":"
logger = logging.getLogger(__name__)


class RagPipelineError(RuntimeError):
    def __init__(self, error_code: str, status_code: int) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.status_code = status_code


class RagSearchPipelineError(RagPipelineError):
    pass


class RagAskPipelineError(RagPipelineError):
    pass


class InsufficientEvidenceAnswerError(Exception):
    pass


@dataclass(frozen=True)
class RetrievalPipelineResult:
    summary: RetrievalScoreSummary
    items: list[RagSearchItem]
    selected_candidates: list[CheckedRetrievalCandidate]
    citation_sources: list[CitationSource]
    context_candidates: list[ContextCandidateRef]
    no_context: bool = False


@dataclass(frozen=True)
class ContextCandidateRef:
    candidate: CheckedRetrievalCandidate
    saved_item: RetrievalRunItem
    rank: int
    rerank_score: float | None
    rerank_order: int | None
    citation_candidate: bool


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
        sparse_strategy: SparseRetrievalStrategy | None = None,
        hybrid_strategy: HybridRetrievalStrategy | None = None,
        query_plan_builder: QueryPlanBuilder | None = None,
        strategy_router: StrategyRouter | None = None,
        agentic_executor: AgenticRetrievalExecutor | None = None,
        llm_tool_orchestrator: LLMToolCallingRetrievalOrchestrator | None = None,
        langchain_agentic_orchestrator: LangChainAgenticRetrievalOrchestrator | None = None,
        context_budget_manager: ContextBudgetManager | None = None,
        evidence_pack_builder: EvidencePackBuilder | None = None,
        trace_export_service: TraceExportService | None = None,
    ) -> None:
        self.settings = settings
        self.embedding_adapter = embedding_adapter
        self.vector_client = vector_client
        self.reranker = reranker
        self.answer_generator = answer_generator or create_answer_generator(settings)
        self.repository = repository or RetrievalRepository()
        self.chat_repository = chat_repository or ChatRepository()
        self.sparse_strategy = sparse_strategy or SparseRetrievalStrategy()
        self.hybrid_strategy = hybrid_strategy or HybridRetrievalStrategy()
        self.query_plan_builder = query_plan_builder or QueryPlanBuilder(settings)
        self.strategy_router = strategy_router or StrategyRouter(settings)
        self.agentic_executor = agentic_executor or AgenticRetrievalExecutor(
            settings,
            ContextSufficiencyChecker(settings),
        )
        self.llm_tool_orchestrator = llm_tool_orchestrator or LLMToolCallingRetrievalOrchestrator(
            settings
        )
        self.langchain_agentic_orchestrator = (
            langchain_agentic_orchestrator or LangChainAgenticRetrievalOrchestrator(settings)
        )
        self.context_budget_manager = context_budget_manager or ContextBudgetManager()
        self.evidence_pack_builder = evidence_pack_builder or EvidencePackBuilder()
        self.trace_export_service = trace_export_service or TraceExportService(settings)
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
        requested_strategy = RetrievalStrategy(payload.strategy.value)
        supported_strategies = {
            RetrievalStrategy.DENSE,
            RetrievalStrategy.SPARSE,
            RetrievalStrategy.HYBRID,
            RetrievalStrategy.AGENTIC_ROUTER,
        }
        if requested_strategy not in supported_strategies:
            raise RagSearchPipelineError("strategy_not_enabled", 409)
        query_hash = _query_hash(payload.query)
        query_plan_build = self.query_plan_builder.build(
            payload.query,
            filters=filters,
            requested_strategy=requested_strategy,
        )
        retrieval_query = query_plan_build.retrieval_query
        latency_tracker = LatencyTracker()
        router_decision = None
        execution_strategy = requested_strategy
        if requested_strategy == RetrievalStrategy.AGENTIC_ROUTER:
            with latency_tracker.span("strategy_router_ms"):
                router_decision = self.strategy_router.route(
                    query_plan=query_plan_build,
                    requested_strategy=requested_strategy,
                    request_kind="search",
                )
            execution_strategy = router_decision.execution_strategy
            if not _is_executable_router_strategy(execution_strategy):
                router_decision = self.strategy_router.fallback_decision(
                    requested_strategy=requested_strategy,
                    fallback_reason="execution_strategy_unavailable",
                    reason_codes=["execution_strategy_unavailable", "fallback_dense"],
                )
                execution_strategy = router_decision.execution_strategy
        else:
            self._ensure_direct_strategy_enabled(requested_strategy)
        retrieval_settings = _retrieval_settings_snapshot(
            settings=self.settings,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            strategy_type=requested_strategy,
        )
        if requested_strategy == RetrievalStrategy.AGENTIC_ROUTER:
            assert router_decision is not None
            query_plan = build_router_query_plan(
                query_hash=query_hash,
                filters=filters,
                execution_strategy=execution_strategy,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_router_strategy_decision(decision=router_decision)
        elif requested_strategy == RetrievalStrategy.SPARSE:
            normalized_sparse_query = normalize_sparse_query(
                retrieval_query,
                max_terms=self.settings.sparse_max_query_terms,
            )
            query_plan = build_sparse_query_plan(
                query_hash=query_hash,
                filters=filters,
                normalized_term_count=len(normalized_sparse_query.terms),
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_sparse_strategy_decision()
        elif requested_strategy == RetrievalStrategy.HYBRID:
            normalized_sparse_query = normalize_sparse_query(
                retrieval_query,
                max_terms=self.settings.sparse_max_query_terms,
            )
            fusion_method = _fusion_method(self.settings)
            query_plan = build_hybrid_query_plan(
                query_hash=query_hash,
                filters=filters,
                normalized_term_count=len(normalized_sparse_query.terms),
                fusion_method=fusion_method,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_hybrid_strategy_decision(fusion_method=fusion_method)
        else:
            query_plan = build_default_dense_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_default_dense_strategy_decision()
        run = self.repository.create_standalone_run(
            db,
            top_k=top_k,
            query_hash=query_hash,
            request_id=request_id,
            started_at=datetime.now(UTC),
            strategy_type=requested_strategy.value,
            query_plan_json=query_plan,
            strategy_decision_json=strategy_decision,
            retrieval_settings_json=retrieval_settings,
        )
        db.commit()
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            retrieval_execution_strategy = _retrieval_execution_strategy(execution_strategy)
            if _should_use_agentic_loop(requested_strategy, router_decision):
                result = self._retrieve_agentic(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    initial_strategy=execution_strategy,
                    query_intent=(
                        query_plan_build.analysis.intent
                        if query_plan_build.analysis is not None
                        else None
                    ),
                    latency_tracker=latency_tracker,
                )
            elif retrieval_execution_strategy == RetrievalStrategy.SPARSE:
                result = self._retrieve_sparse(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                )
            elif retrieval_execution_strategy == RetrievalStrategy.HYBRID:
                result = self._retrieve_hybrid(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                )
            else:
                result = self._retrieve_and_rerank(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                    retrieval_source=RetrievalSource.FALLBACK_DENSE
                    if execution_strategy == RetrievalStrategy.FALLBACK_DENSE
                    else RetrievalSource.DENSE,
                )
            run = self._require_run(db, run_id)
            self.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=result.summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(result.summary.top1_rerank_score),
                finished_at=datetime.now(UTC),
                latency_breakdown_json=latency_tracker.snapshot(),
            )
            db.commit()
            self._export_retrieval_trace_safely(db, retrieval_run_id=run_id)
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
                latency_tracker=latency_tracker,
            )
            raise RagSearchPipelineError("retrieval_failed", 503) from None
        except RerankError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
            )
            raise RagSearchPipelineError("rerank_failed", 503) from None
        except Exception:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
                latency_tracker=latency_tracker,
            )
            raise

    def get_retrieval_run_detail(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> RetrievalRunDebugResponse:
        run = self.repository.get_run(db, retrieval_run_id=retrieval_run_id)
        if run is None:
            raise ResourceNotFound()
        items = self.repository.list_items_for_run(db, retrieval_run_id=retrieval_run_id)
        return RetrievalRunDebugResponse(
            retrieval_run=_retrieval_run_debug_summary(run),
            items=[_retrieval_run_debug_item(item) for item in items],
        )

    def list_retrieval_run_debug_history(
        self,
        db: Session,
        *,
        limit: int,
    ) -> RetrievalRunDebugListResponse:
        safe_limit = min(100, max(1, limit))
        runs = self.repository.list_recent_runs(db, limit=safe_limit)
        return RetrievalRunDebugListResponse(
            items=[_retrieval_run_debug_summary(run) for run in runs]
        )

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
        filters = _retrieval_filters(payload)
        requested_strategy = RetrievalStrategy(payload.strategy.value)
        if requested_strategy not in {
            RetrievalStrategy.DENSE,
            RetrievalStrategy.HYBRID,
            RetrievalStrategy.AGENTIC_ROUTER,
            RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
            RetrievalStrategy.LANGCHAIN_AGENTIC,
        }:
            raise RagAskPipelineError("strategy_not_enabled", 409)
        if requested_strategy == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
            if not self.settings.llm_orchestrator_enabled:
                raise RagAskPipelineError("strategy_not_enabled", 409)
        elif requested_strategy == RetrievalStrategy.LANGCHAIN_AGENTIC:
            if not self.settings.langchain_agentic_enabled:
                raise RagAskPipelineError("strategy_not_enabled", 409)
        elif requested_strategy != RetrievalStrategy.AGENTIC_ROUTER:
            try:
                self._ensure_direct_strategy_enabled(requested_strategy)
            except RagSearchPipelineError as exc:
                raise RagAskPipelineError(exc.error_code, exc.status_code) from exc
        query_hash = _query_hash(payload.message)
        query_plan_build = self.query_plan_builder.build(
            payload.message,
            filters=filters,
            requested_strategy=requested_strategy,
        )
        retrieval_query = query_plan_build.retrieval_query
        latency_tracker = LatencyTracker()
        router_decision = None
        execution_strategy = requested_strategy
        if requested_strategy == RetrievalStrategy.AGENTIC_ROUTER:
            with latency_tracker.span("strategy_router_ms"):
                router_decision = self.strategy_router.route(
                    query_plan=query_plan_build,
                    requested_strategy=requested_strategy,
                    request_kind="ask",
                )
            execution_strategy = router_decision.execution_strategy
            if not _is_executable_router_strategy(execution_strategy):
                router_decision = self.strategy_router.fallback_decision(
                    requested_strategy=requested_strategy,
                    fallback_reason="execution_strategy_unavailable",
                    reason_codes=["execution_strategy_unavailable", "fallback_dense"],
                )
                execution_strategy = router_decision.execution_strategy
        retrieval_settings = _retrieval_settings_snapshot(
            settings=self.settings,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            strategy_type=requested_strategy,
        )
        if requested_strategy == RetrievalStrategy.AGENTIC_ROUTER:
            assert router_decision is not None
            query_plan = build_router_query_plan(
                query_hash=query_hash,
                filters=filters,
                execution_strategy=execution_strategy,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_router_strategy_decision(decision=router_decision)
        elif requested_strategy == RetrievalStrategy.HYBRID:
            fusion_method = _fusion_method(self.settings)
            normalized_sparse_query = normalize_sparse_query(
                retrieval_query,
                max_terms=self.settings.sparse_max_query_terms,
            )
            query_plan = build_hybrid_query_plan(
                query_hash=query_hash,
                filters=filters,
                normalized_term_count=len(normalized_sparse_query.terms),
                fusion_method=fusion_method,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_hybrid_strategy_decision(fusion_method=fusion_method)
        elif requested_strategy == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
            query_plan = build_llm_tool_orchestrator_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_llm_tool_orchestrator_strategy_decision()
        elif requested_strategy == RetrievalStrategy.LANGCHAIN_AGENTIC:
            query_plan = build_langchain_agentic_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_langchain_agentic_strategy_decision()
        else:
            query_plan = build_default_dense_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_default_dense_strategy_decision()
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
                query_hash=query_hash,
                request_id=request_id,
                started_at=now,
                strategy_type=requested_strategy.value,
                query_plan_json=query_plan,
                strategy_decision_json=strategy_decision,
                retrieval_settings_json=retrieval_settings,
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
            retrieval_execution_strategy = _retrieval_execution_strategy(execution_strategy)
            if requested_strategy == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
                result = self._retrieve_llm_tool_orchestrator(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                )
            elif requested_strategy == RetrievalStrategy.LANGCHAIN_AGENTIC:
                result = self._retrieve_langchain_agentic(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                )
            elif _should_use_agentic_loop(requested_strategy, router_decision):
                result = self._retrieve_agentic(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    initial_strategy=execution_strategy,
                    query_intent=(
                        query_plan_build.analysis.intent
                        if query_plan_build.analysis is not None
                        else None
                    ),
                    latency_tracker=latency_tracker,
                )
            elif retrieval_execution_strategy == RetrievalStrategy.SPARSE:
                result = self._retrieve_sparse(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                )
            elif retrieval_execution_strategy == RetrievalStrategy.HYBRID:
                result = self._retrieve_hybrid(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                )
            else:
                result = self._retrieve_and_rerank(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                    retrieval_source=RetrievalSource.FALLBACK_DENSE
                    if execution_strategy == RetrievalStrategy.FALLBACK_DENSE
                    else RetrievalSource.DENSE,
                )
            context_budget_decision = self._apply_context_budget(
                db,
                retrieval_run_id=run_id,
                result=result,
                estimated_prompt_tokens=estimate_tokens(payload.message),
            )
            selected_context_refs = _selected_context_refs(
                result.context_candidates,
                context_budget_decision,
            )
            selected_citation_sources = _context_citation_sources(
                selected_context_refs,
                snippet_max_chars=self.settings.citation_preview_max_chars,
            )
            with latency_tracker.span("evidence_pack_ms"):
                evidence_pack = self._build_evidence_pack(
                    db,
                    retrieval_run_id=run_id,
                    selected_context_refs=selected_context_refs,
                    selected_citation_sources=selected_citation_sources,
                    candidate_context_items=context_budget_decision.trace.items.candidate_count,
                )
            if result.no_context or not evidence_pack.items:
                self._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                raise RagAskPipelineError("no_context_found", 422)

            with latency_tracker.span("context_assembly_ms"):
                context_items = evidence_pack.to_generation_context_items()
                self._record_injection_patterns(
                    db,
                    retrieval_run_id=run_id,
                    context_texts=[item.text for item in context_items],
                )
                prompt_citation_sources = _prompt_citation_sources(
                    context_items=context_items,
                    citation_sources=selected_citation_sources,
                )
                prompt_context_refs = _context_refs_for_citation_sources(
                    selected_context_refs,
                    prompt_citation_sources,
                )
                context_budget_decision = self._finalize_context_budget_after_assembly(
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
                generation = answer_generator.generate(
                    GenerationRequest(
                        message=payload.message,
                        context_items=context_items,
                        max_output_chars=self.settings.generation_max_output_chars,
                    )
                )
            with latency_tracker.span("citation_build_ms"):
                parsed_generation, cited_sources = _validated_generation_or_fallback(
                    generation.content,
                    context_items=context_items,
                    prompt_citation_sources=prompt_citation_sources,
                )
                if requested_strategy in {
                    RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
                    RetrievalStrategy.LANGCHAIN_AGENTIC,
                } and _is_insufficient_evidence_answer(parsed_generation.answer_text):
                    self._mark_failed_safely(
                        db,
                        retrieval_run_id=run_id,
                        error_code="no_context_found",
                        latency_tracker=latency_tracker,
                        rollback=False,
                    )
                    raise RagAskPipelineError("no_context_found", 422)
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
            with latency_tracker.span("confidence_ms"):
                confidence = calculate_confidence(
                    ConfidenceInputs(
                        retrieval_score_summary=final_summary,
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
                retrieval_score_summary=final_summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(final_summary.top1_rerank_score),
                answer_confidence=_decimal_score(confidence.answer_confidence),
                groundedness_score=_decimal_score(confidence.groundedness_score),
                confidence_label=confidence.confidence_label,
                finished_at=datetime.now(UTC),
                latency_breakdown_json=latency_tracker.snapshot(),
            )
            self.chat_repository.touch_session(
                db,
                session=session,
                updated_at=datetime.now(UTC),
            )
            db.commit()
            self._export_retrieval_trace_safely(db, retrieval_run_id=run_id)
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
        except InsufficientEvidenceAnswerError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="no_context_found",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            raise RagAskPipelineError("no_context_found", 422) from None
        except CitationBuildError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="citation_build_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            raise RagAskPipelineError("citation_build_failed", 500) from None
        except RagAskPipelineError:
            raise
        except (EmbeddingAdapterError, RetrievalError):
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
            )
            raise RagAskPipelineError("retrieval_failed", 503) from None
        except RerankError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
            )
            raise RagAskPipelineError("rerank_failed", 503) from None
        except AnswerGenerationError:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            raise RagAskPipelineError("generation_failed", 503) from None
        except Exception:
            self._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
                latency_tracker=latency_tracker,
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
        latency_tracker: LatencyTracker | None = None,
        retrieval_source: RetrievalSource = RetrievalSource.DENSE,
    ) -> RetrievalPipelineResult:
        if latency_tracker is None:
            latency_tracker = LatencyTracker()
        with latency_tracker.span("query_embedding_ms"):
            query_vector = self._embed_query(query)
        with latency_tracker.span("qdrant_search_ms"):
            vector_candidates = self.vector_client.search(
                collection_name=self.settings.qdrant_collection_name,
                query_vector=query_vector,
                limit=top_k,
                filters=filters,
            )
        with latency_tracker.span("rdb_final_check_ms"):
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
                context_candidates=[],
            )

        with latency_tracker.span("rerank_ms"):
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
                final_rank=index,
                selected_flag=index <= selected_count,
                retrieval_source=retrieval_source,
            )
            for index, candidate in enumerate(ordered_candidates, start=1)
        ]
        with latency_tracker.span("retrieval_items_persist_ms"):
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
            context_candidates=[
                ContextCandidateRef(
                    candidate=candidate,
                    saved_item=saved_item,
                    rank=index,
                    rerank_score=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_score,
                    rerank_order=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order,
                    citation_candidate=index <= selected_count,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(ordered_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
        )

    def _retrieve_sparse(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker | None = None,
    ) -> RetrievalPipelineResult:
        if latency_tracker is None:
            latency_tracker = LatencyTracker()
        with latency_tracker.span("sparse_search_ms"):
            sparse_candidates = self.sparse_strategy.search(
                db,
                query=query,
                top_k=top_k,
                filters=filters,
                settings=self.settings,
            )
        with latency_tracker.span("rdb_final_check_ms"):
            checked_candidates = self.repository.final_check_candidates(
                db,
                candidates=sparse_candidates,
                filters=filters,
            )
        if not checked_candidates:
            summary = _score_summary(
                requested_top_k=top_k,
                qdrant_candidate_count=0,
                sparse_candidate_count=len(sparse_candidates),
                checked_candidates=[],
                selected_count=0,
                top1_rerank_score=None,
            )
            return RetrievalPipelineResult(
                summary=summary,
                items=[],
                selected_candidates=[],
                citation_sources=[],
                context_candidates=[],
            )

        selected_count = min(rerank_top_n, len(checked_candidates))
        item_inputs = [
            _sparse_run_item_input(
                candidate,
                final_rank=index,
                selected_flag=index <= selected_count,
            )
            for index, candidate in enumerate(checked_candidates, start=1)
        ]
        with latency_tracker.span("retrieval_items_persist_ms"):
            saved_items = self.repository.save_items(
                db,
                retrieval_run_id=retrieval_run_id,
                items=item_inputs,
            )
        summary = _score_summary(
            requested_top_k=top_k,
            qdrant_candidate_count=0,
            sparse_candidate_count=len(sparse_candidates),
            checked_candidates=checked_candidates,
            selected_count=selected_count,
            top1_rerank_score=None,
        )
        return RetrievalPipelineResult(
            summary=summary,
            items=[
                _response_item(
                    candidate,
                    saved_item_id=saved_item.retrieval_run_item_id,
                    rerank_score=None,
                    rerank_order=None,
                    selected_flag=index <= selected_count,
                    snippet_max_chars=self.settings.search_snippet_max_chars,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(checked_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
            selected_candidates=checked_candidates[:selected_count],
            citation_sources=[
                _citation_source(
                    candidate,
                    saved_item=saved_item,
                    local_citation_id=local_id,
                    snippet_max_chars=self.settings.citation_preview_max_chars,
                )
                for local_id, (candidate, saved_item) in enumerate(
                    zip(
                        checked_candidates[:selected_count],
                        saved_items[:selected_count],
                        strict=True,
                    ),
                    start=1,
                )
            ],
            context_candidates=[
                ContextCandidateRef(
                    candidate=candidate,
                    saved_item=saved_item,
                    rank=index,
                    rerank_score=None,
                    rerank_order=None,
                    citation_candidate=index <= selected_count,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(checked_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
        )

    def _retrieve_hybrid(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker | None = None,
    ) -> RetrievalPipelineResult:
        if latency_tracker is None:
            latency_tracker = LatencyTracker()
        candidate_limit = _hybrid_candidate_limit(top_k, self.settings)
        fusion_method = _fusion_method(self.settings)
        dense_candidates: list[VectorSearchCandidate] = []
        sparse_candidates: list[VectorSearchCandidate] = []
        if _hybrid_uses_dense(self.settings):
            with latency_tracker.span("query_embedding_ms"):
                query_vector = self._embed_query(query)
            with latency_tracker.span("qdrant_search_ms"):
                dense_candidates = self.vector_client.search(
                    collection_name=self.settings.qdrant_collection_name,
                    query_vector=query_vector,
                    limit=candidate_limit,
                    filters=filters,
                )
        if _hybrid_uses_sparse(self.settings):
            with latency_tracker.span("sparse_search_ms"):
                sparse_candidates = self.sparse_strategy.search(
                    db,
                    query=query,
                    top_k=candidate_limit,
                    filters=filters,
                    settings=self.settings,
                )
        with latency_tracker.span("fusion_ms"):
            fused_candidates = self.hybrid_strategy.fuse(
                dense_candidates=dense_candidates,
                sparse_candidates=sparse_candidates,
                fusion_method=fusion_method,
                limit=candidate_limit,
                rrf_k=self.settings.hybrid_rrf_k,
                dense_weight=self.settings.hybrid_dense_weight,
                sparse_weight=self.settings.hybrid_sparse_weight,
            )
        with latency_tracker.span("rdb_final_check_ms"):
            checked_candidates = self.repository.final_check_candidates(
                db,
                candidates=fused_candidates,
                filters=filters,
            )
        ranked_candidates = _order_by_source_affinity(query, checked_candidates)
        visible_candidates = ranked_candidates[:top_k]
        selected_count = min(rerank_top_n, len(visible_candidates))
        excluded_by_rdb_check_count = max(0, len(fused_candidates) - len(checked_candidates))
        if not visible_candidates:
            summary = _score_summary(
                requested_top_k=top_k,
                qdrant_candidate_count=len(dense_candidates),
                sparse_candidate_count=len(sparse_candidates),
                hybrid_candidate_count=len(fused_candidates),
                checked_candidates=[],
                selected_count=0,
                top1_rerank_score=None,
                fusion_method=fusion_method.value,
                excluded_by_rdb_check_count=excluded_by_rdb_check_count,
            )
            return RetrievalPipelineResult(
                summary=summary,
                items=[],
                selected_candidates=[],
                citation_sources=[],
                context_candidates=[],
            )

        with latency_tracker.span("retrieval_items_persist_ms"):
            saved_items = self.repository.save_items(
                db,
                retrieval_run_id=retrieval_run_id,
                items=[
                    _hybrid_run_item_input(
                        candidate,
                        final_rank=index,
                        selected_flag=index <= selected_count,
                        fusion_method=fusion_method,
                    )
                    for index, candidate in enumerate(visible_candidates, start=1)
                ],
            )
        summary = _score_summary(
            requested_top_k=top_k,
            qdrant_candidate_count=len(dense_candidates),
            sparse_candidate_count=len(sparse_candidates),
            hybrid_candidate_count=len(fused_candidates),
            checked_candidates=checked_candidates,
            selected_count=selected_count,
            top1_rerank_score=None,
            fusion_method=fusion_method.value,
            excluded_by_rdb_check_count=excluded_by_rdb_check_count,
        )
        return RetrievalPipelineResult(
            summary=summary,
            items=[
                _response_item(
                    candidate,
                    saved_item_id=saved_item.retrieval_run_item_id,
                    rerank_score=None,
                    rerank_order=None,
                    selected_flag=index <= selected_count,
                    snippet_max_chars=self.settings.search_snippet_max_chars,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(visible_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
            selected_candidates=visible_candidates[:selected_count],
            citation_sources=[
                _citation_source(
                    candidate,
                    saved_item=saved_item,
                    local_citation_id=local_id,
                    snippet_max_chars=self.settings.citation_preview_max_chars,
                )
                for local_id, (candidate, saved_item) in enumerate(
                    zip(
                        visible_candidates[:selected_count],
                        saved_items[:selected_count],
                        strict=True,
                    ),
                    start=1,
                )
            ],
            context_candidates=[
                ContextCandidateRef(
                    candidate=candidate,
                    saved_item=saved_item,
                    rank=index,
                    rerank_score=None,
                    rerank_order=None,
                    citation_candidate=index <= selected_count,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(visible_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
        )

    def _retrieve_agentic(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        initial_strategy: RetrievalStrategy,
        query_intent: Any,
        latency_tracker: LatencyTracker,
    ) -> RetrievalPipelineResult:
        with latency_tracker.span("agentic_total_ms"):
            agentic_result = self.agentic_executor.execute(
                initial_strategy=initial_strategy,
                intent=query_intent,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                retrieve=lambda strategy, role: self._execute_agentic_attempt(
                    db,
                    query=query,
                    top_k=top_k,
                    filters=filters,
                    strategy=strategy,
                    role=role,
                    latency_tracker=latency_tracker,
                ),
                latency_tracker=latency_tracker,
            )
            pipeline_result = self._persist_agentic_result(
                db,
                query=query,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                retrieval_run_id=retrieval_run_id,
                agentic_result=agentic_result,
                latency_tracker=latency_tracker,
            )
        self._update_agentic_trace(
            db,
            retrieval_run_id=retrieval_run_id,
            agentic_result=agentic_result,
        )
        return pipeline_result

    def _retrieve_llm_tool_orchestrator(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker,
    ) -> RetrievalPipelineResult:
        orchestrator_result = self.llm_tool_orchestrator.execute(
            query=query,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            retrieval_run_id=retrieval_run_id,
            retrieve=lambda strategy, role, tool_query: self._execute_agentic_attempt(
                db,
                query=tool_query,
                top_k=top_k,
                filters=filters,
                strategy=strategy,
                role=role,
                latency_tracker=latency_tracker,
            ),
            inspect_trace=lambda: self._llm_orchestrator_trace_summary(
                retrieval_run_id=retrieval_run_id,
                latency_tracker=latency_tracker,
            ),
            latency_tracker=latency_tracker,
        )
        pipeline_result = self._persist_agentic_result(
            db,
            query=query,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            retrieval_run_id=retrieval_run_id,
            agentic_result=orchestrator_result.retrieval_result,
            latency_tracker=latency_tracker,
            trace_strategy=RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
        )
        self._update_llm_orchestrator_trace(
            db,
            retrieval_run_id=retrieval_run_id,
            orchestrator_result=orchestrator_result,
            tool_result_compression_json=_tool_result_compression_json_with_run_items(
                orchestrator_result.tool_result_compression_trace,
                pipeline_result.context_candidates,
            ),
        )
        summary_payload = pipeline_result.summary.model_dump(mode="json")
        summary_payload.update(orchestrator_result.summary_fields())
        return RetrievalPipelineResult(
            summary=RetrievalScoreSummary(**summary_payload),
            items=pipeline_result.items,
            selected_candidates=pipeline_result.selected_candidates,
            citation_sources=pipeline_result.citation_sources,
            context_candidates=pipeline_result.context_candidates,
            no_context=pipeline_result.no_context,
        )

    def _retrieve_langchain_agentic(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker,
    ) -> RetrievalPipelineResult:
        langchain_result = self.langchain_agentic_orchestrator.execute(
            query=query,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            retrieve=lambda strategy, role, tool_query: self._execute_agentic_attempt(
                db,
                query=tool_query,
                top_k=top_k,
                filters=filters,
                strategy=strategy,
                role=role,
                latency_tracker=latency_tracker,
            ),
            latency_tracker=latency_tracker,
        )
        pipeline_result = self._persist_agentic_result(
            db,
            query=query,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            retrieval_run_id=retrieval_run_id,
            agentic_result=langchain_result.retrieval_result,
            latency_tracker=latency_tracker,
            trace_strategy=RetrievalStrategy.LANGCHAIN_AGENTIC,
        )
        self._update_langchain_agentic_trace(
            db,
            retrieval_run_id=retrieval_run_id,
            langchain_result=langchain_result,
            tool_result_compression_json=_tool_result_compression_json_with_run_items(
                langchain_result.tool_result_compression_trace,
                pipeline_result.context_candidates,
            ),
        )
        summary_payload = pipeline_result.summary.model_dump(mode="json")
        summary_payload.update(langchain_result.summary_fields())
        return RetrievalPipelineResult(
            summary=RetrievalScoreSummary(**summary_payload),
            items=pipeline_result.items,
            selected_candidates=pipeline_result.selected_candidates,
            citation_sources=pipeline_result.citation_sources,
            context_candidates=pipeline_result.context_candidates,
            no_context=pipeline_result.no_context,
        )

    def _execute_agentic_attempt(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        strategy: RetrievalStrategy,
        role: str,
        latency_tracker: LatencyTracker,
    ) -> RetrievalAttemptResult:
        execution_strategy = _retrieval_execution_strategy(strategy)
        if execution_strategy == RetrievalStrategy.SPARSE:
            return self._collect_sparse_attempt(
                db,
                query=query,
                top_k=top_k,
                filters=filters,
                strategy=strategy,
                role=role,
                latency_tracker=latency_tracker,
            )
        if execution_strategy == RetrievalStrategy.HYBRID:
            return self._collect_hybrid_attempt(
                db,
                query=query,
                top_k=top_k,
                filters=filters,
                strategy=strategy,
                role=role,
                latency_tracker=latency_tracker,
            )
        return self._collect_dense_attempt(
            db,
            query=query,
            top_k=top_k,
            filters=filters,
            strategy=strategy,
            role=role,
            latency_tracker=latency_tracker,
        )

    def _collect_dense_attempt(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        strategy: RetrievalStrategy,
        role: str,
        latency_tracker: LatencyTracker,
    ) -> RetrievalAttemptResult:
        with latency_tracker.span("query_embedding_ms"):
            query_vector = self._embed_query(query)
        with latency_tracker.span("qdrant_search_ms"):
            vector_candidates = self.vector_client.search(
                collection_name=self.settings.qdrant_collection_name,
                query_vector=query_vector,
                limit=top_k,
                filters=filters,
            )
        with latency_tracker.span("rdb_final_check_ms"):
            checked_candidates = self.repository.final_check_candidates(
                db,
                candidates=vector_candidates,
                filters=filters,
            )
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=checked_candidates,
            qdrant_candidate_count=len(vector_candidates),
            excluded_by_rdb_check_count=max(0, len(vector_candidates) - len(checked_candidates)),
            role=role,
        )

    def _collect_sparse_attempt(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        strategy: RetrievalStrategy,
        role: str,
        latency_tracker: LatencyTracker,
    ) -> RetrievalAttemptResult:
        with latency_tracker.span("sparse_search_ms"):
            sparse_candidates = self.sparse_strategy.search(
                db,
                query=query,
                top_k=top_k,
                filters=filters,
                settings=self.settings,
            )
        with latency_tracker.span("rdb_final_check_ms"):
            checked_candidates = self.repository.final_check_candidates(
                db,
                candidates=sparse_candidates,
                filters=filters,
            )
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=checked_candidates,
            qdrant_candidate_count=0,
            sparse_candidate_count=len(sparse_candidates),
            excluded_by_rdb_check_count=max(0, len(sparse_candidates) - len(checked_candidates)),
            role=role,
        )

    def _collect_hybrid_attempt(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        strategy: RetrievalStrategy,
        role: str,
        latency_tracker: LatencyTracker,
    ) -> RetrievalAttemptResult:
        candidate_limit = _hybrid_candidate_limit(top_k, self.settings)
        fusion_method = _fusion_method(self.settings)
        dense_candidates: list[VectorSearchCandidate] = []
        sparse_candidates: list[VectorSearchCandidate] = []
        if _hybrid_uses_dense(self.settings):
            with latency_tracker.span("query_embedding_ms"):
                query_vector = self._embed_query(query)
            with latency_tracker.span("qdrant_search_ms"):
                dense_candidates = self.vector_client.search(
                    collection_name=self.settings.qdrant_collection_name,
                    query_vector=query_vector,
                    limit=candidate_limit,
                    filters=filters,
                )
        if _hybrid_uses_sparse(self.settings):
            with latency_tracker.span("sparse_search_ms"):
                sparse_candidates = self.sparse_strategy.search(
                    db,
                    query=query,
                    top_k=candidate_limit,
                    filters=filters,
                    settings=self.settings,
                )
        with latency_tracker.span("fusion_ms"):
            fused_candidates = self.hybrid_strategy.fuse(
                dense_candidates=dense_candidates,
                sparse_candidates=sparse_candidates,
                fusion_method=fusion_method,
                limit=candidate_limit,
                rrf_k=self.settings.hybrid_rrf_k,
                dense_weight=self.settings.hybrid_dense_weight,
                sparse_weight=self.settings.hybrid_sparse_weight,
            )
        with latency_tracker.span("rdb_final_check_ms"):
            checked_candidates = self.repository.final_check_candidates(
                db,
                candidates=fused_candidates,
                filters=filters,
            )
        visible_candidates = _order_by_source_affinity(query, checked_candidates)[:top_k]
        return RetrievalAttemptResult(
            strategy=strategy,
            candidates=visible_candidates,
            qdrant_candidate_count=len(dense_candidates),
            sparse_candidate_count=len(sparse_candidates),
            hybrid_candidate_count=len(fused_candidates),
            excluded_by_rdb_check_count=max(0, len(fused_candidates) - len(checked_candidates)),
            role=role,
        )

    def _persist_agentic_result(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        retrieval_run_id: int,
        agentic_result: AgenticRetrievalResult,
        latency_tracker: LatencyTracker,
        trace_strategy: RetrievalStrategy = RetrievalStrategy.AGENTIC_ROUTER,
    ) -> RetrievalPipelineResult:
        final_candidates = _order_by_source_affinity(query, agentic_result.final_candidates)
        if not final_candidates:
            summary = _agentic_score_summary(
                requested_top_k=top_k,
                checked_candidates=[],
                selected_count=0,
                top1_rerank_score=None,
                agentic_result=agentic_result,
            )
            return RetrievalPipelineResult(
                summary=summary,
                items=[],
                selected_candidates=[],
                citation_sources=[],
                context_candidates=[],
                no_context=True,
            )

        if agentic_result.no_context:
            item_inputs = [
                _agentic_run_item_input(
                    candidate,
                    rerank_score=None,
                    rerank_order=None,
                    final_rank=index,
                    selected_flag=False,
                    agentic_result=agentic_result,
                    trace_strategy=trace_strategy,
                )
                for index, candidate in enumerate(final_candidates, start=1)
            ]
            with latency_tracker.span("retrieval_items_persist_ms"):
                saved_items = self.repository.save_items(
                    db,
                    retrieval_run_id=retrieval_run_id,
                    items=item_inputs,
                )
            summary = _agentic_score_summary(
                requested_top_k=top_k,
                checked_candidates=final_candidates,
                selected_count=0,
                top1_rerank_score=None,
                agentic_result=agentic_result,
            )
            return RetrievalPipelineResult(
                summary=summary,
                items=[
                    _response_item(
                        candidate,
                        saved_item_id=saved_item.retrieval_run_item_id,
                        rerank_score=None,
                        rerank_order=None,
                        selected_flag=False,
                        snippet_max_chars=self.settings.search_snippet_max_chars,
                    )
                    for candidate, saved_item in zip(
                        final_candidates,
                        saved_items,
                        strict=True,
                    )
                ],
                selected_candidates=[],
                citation_sources=[],
                context_candidates=[
                    ContextCandidateRef(
                        candidate=candidate,
                        saved_item=saved_item,
                        rank=index,
                        rerank_score=None,
                        rerank_order=None,
                        citation_candidate=False,
                    )
                    for index, (candidate, saved_item) in enumerate(
                        zip(final_candidates, saved_items, strict=True),
                        start=1,
                    )
                ],
                no_context=True,
            )

        with latency_tracker.span("rerank_after_merge_ms"):
            rerank_results = self.reranker.rerank(
                query=query,
                candidates=[
                    RerankCandidate(
                        document_chunk_id=candidate.chunk.document_chunk_id,
                        text=candidate.chunk.content_text,
                        retrieval_score=candidate.retrieval_score,
                    )
                    for candidate in final_candidates
                ],
            )
            rerank_by_chunk_id = _validated_rerank_results(
                rerank_results,
                checked_candidates=final_candidates,
            )

        ordered_candidates = sorted(
            final_candidates,
            key=lambda candidate: (
                rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order
            ),
        )
        ordered_candidates = _order_by_source_affinity(query, ordered_candidates)
        selected_count = (
            0 if agentic_result.no_context else min(rerank_top_n, len(ordered_candidates))
        )
        item_inputs = [
            _agentic_run_item_input(
                candidate,
                rerank_score=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_score,
                rerank_order=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order,
                final_rank=index,
                selected_flag=index <= selected_count,
                agentic_result=agentic_result,
                trace_strategy=trace_strategy,
            )
            for index, candidate in enumerate(ordered_candidates, start=1)
        ]
        with latency_tracker.span("retrieval_items_persist_ms"):
            saved_items = self.repository.save_items(
                db,
                retrieval_run_id=retrieval_run_id,
                items=item_inputs,
            )
        top1_rerank_score = rerank_by_chunk_id[
            ordered_candidates[0].chunk.document_chunk_id
        ].rerank_score
        summary = _agentic_score_summary(
            requested_top_k=top_k,
            checked_candidates=final_candidates,
            selected_count=selected_count,
            top1_rerank_score=top1_rerank_score,
            agentic_result=agentic_result,
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
            context_candidates=[
                ContextCandidateRef(
                    candidate=candidate,
                    saved_item=saved_item,
                    rank=index,
                    rerank_score=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_score,
                    rerank_order=rerank_by_chunk_id[candidate.chunk.document_chunk_id].rerank_order,
                    citation_candidate=index <= selected_count,
                )
                for index, (candidate, saved_item) in enumerate(
                    zip(ordered_candidates, saved_items, strict=True),
                    start=1,
                )
            ],
            no_context=agentic_result.no_context,
        )

    def _record_injection_patterns(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        context_texts: list[str],
    ) -> None:
        """Observability only: flag prompt-injection patterns in selected chunks.

        Records ``injection_pattern_detected`` into the retrieval run's strategy
        decision ``reason_codes`` when any selected chunk text matches a known
        injection pattern. Does NOT alter retrieval or generation behavior.
        """
        if not any(detect_injection_patterns(text) for text in context_texts):
            return
        run = self._require_run(db, retrieval_run_id)
        decision = dict(run.strategy_decision_json or {})
        existing_reason_codes = decision.get("reason_codes")
        if isinstance(existing_reason_codes, list):
            reason_codes = [str(code) for code in existing_reason_codes]
        else:
            reason_codes = []
        if INJECTION_PATTERN_REASON_CODE not in reason_codes:
            reason_codes.append(INJECTION_PATTERN_REASON_CODE)
        decision["reason_codes"] = reason_codes
        self.repository.update_retrieval_run_trace(
            db,
            run=run,
            strategy_decision_json=TraceRedactor.safe_dict(decision),
        )

    def _update_agentic_trace(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        agentic_result: AgenticRetrievalResult,
    ) -> None:
        run = self._require_run(db, retrieval_run_id)
        strategy_decision = _agentic_strategy_decision(
            run.strategy_decision_json,
            agentic_result=agentic_result,
        )
        if strategy_decision is None:
            return
        self.repository.update_retrieval_run_trace(
            db,
            run=run,
            strategy_decision_json=strategy_decision,
        )

    def _update_llm_orchestrator_trace(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        orchestrator_result: LLMToolOrchestratorExecutionResult,
        tool_result_compression_json: dict[str, object] | None,
    ) -> None:
        run = self._require_run(db, retrieval_run_id)
        decision = dict(run.strategy_decision_json or {})
        orchestrator_fields = orchestrator_result.decision_trace_fields()
        existing_reason_codes = decision.get("reason_codes")
        if isinstance(existing_reason_codes, list):
            reason_codes = [str(code) for code in existing_reason_codes]
        else:
            reason_codes = []
        for code in orchestrator_result.reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
        decision.update(orchestrator_fields)
        decision["reason_codes"] = reason_codes
        update_payload: dict[str, object] | None = None
        if (
            self.settings.tool_result_compression_store_debug_trace
            and tool_result_compression_json is not None
        ):
            update_payload = tool_result_compression_json
        self.repository.update_retrieval_run_trace(
            db,
            run=run,
            strategy_decision_json=TraceRedactor.safe_dict(decision),
            tool_result_compression_json=update_payload,
        )
        if tool_result_compression_json is not None:
            _log_tool_result_compression(
                run=run,
                trace=tool_result_compression_json,
                event=(
                    "rag.tool_result_compression.skipped"
                    if not tool_result_compression_json.get("enabled")
                    else (
                        "rag.tool_result_compression.rejected"
                        if _tool_result_oversized_rejected(tool_result_compression_json)
                        else "rag.tool_result_compression.applied"
                    )
                ),
            )

    def _update_langchain_agentic_trace(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        langchain_result: LangChainAgenticExecutionResult,
        tool_result_compression_json: dict[str, object] | None,
    ) -> None:
        run = self._require_run(db, retrieval_run_id)
        decision = dict(run.strategy_decision_json or {})
        langchain_fields = langchain_result.decision_trace_fields()
        existing_reason_codes = decision.get("reason_codes")
        if isinstance(existing_reason_codes, list):
            reason_codes = [str(code) for code in existing_reason_codes]
        else:
            reason_codes = []
        for code in langchain_result.reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
        decision.update(langchain_fields)
        decision["reason_codes"] = reason_codes
        update_payload: dict[str, object] | None = None
        if (
            self.settings.tool_result_compression_store_debug_trace
            and tool_result_compression_json is not None
        ):
            update_payload = tool_result_compression_json
        self.repository.update_retrieval_run_trace(
            db,
            run=run,
            strategy_decision_json=TraceRedactor.safe_dict(decision),
            tool_result_compression_json=update_payload,
        )
        if tool_result_compression_json is not None:
            _log_tool_result_compression(
                run=run,
                trace=tool_result_compression_json,
                event=(
                    "rag.tool_result_compression.skipped"
                    if not tool_result_compression_json.get("enabled")
                    else (
                        "rag.tool_result_compression.rejected"
                        if _tool_result_oversized_rejected(tool_result_compression_json)
                        else "rag.tool_result_compression.applied"
                    )
                ),
            )

    def _llm_orchestrator_trace_summary(
        self,
        *,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker,
    ) -> dict[str, object]:
        latency = latency_tracker.snapshot()
        return TraceRedactor.safe_dict(
            {
                "retrieval_run_id": retrieval_run_id,
                "strategy_type": RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
                "status": "running",
                "latency_summary": {
                    "total_ms": latency.get("total_ms"),
                    "retrieval_ms": latency.get("retrieval_ms"),
                    "llm_orchestrator_ms": latency.get("llm_orchestrator_ms"),
                },
            }
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

    def _apply_context_budget(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        result: RetrievalPipelineResult,
        estimated_prompt_tokens: int,
    ) -> ContextBudgetDecision:
        run = self._require_run(db, retrieval_run_id)
        policy = _context_budget_policy(self.settings)
        if result.no_context:
            policy = policy.model_copy(update={"min_citation_candidates": 0})
        decision = self.context_budget_manager.apply(
            _context_budget_candidates(result.context_candidates),
            policy=policy,
            estimated_prompt_tokens=estimated_prompt_tokens,
            strategy=_context_budget_strategy(run),
        )
        selected_item_ids = set(decision.selected_item_ids)
        self.repository.update_context_selection(
            db,
            retrieval_run_id=retrieval_run_id,
            selected_item_ids=selected_item_ids,
        )
        if policy.store_debug_trace:
            self.repository.update_retrieval_run_trace(
                db,
                run=run,
                context_budget_json=decision.trace.model_dump(mode="json", exclude_none=True),
            )
        _log_context_budget(
            run=run,
            decision=decision,
            event="rag.context_budget.skipped"
            if not policy.enabled
            else (
                "rag.context_budget.exhausted"
                if decision.trace.usage.budget_exhausted
                else "rag.context_budget.applied"
            ),
        )
        return decision

    def _finalize_context_budget_after_assembly(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        decision: ContextBudgetDecision,
        prompt_context_refs: list[ContextCandidateRef],
    ) -> ContextBudgetDecision:
        final_decision = finalize_context_budget_selection(
            decision,
            selected_item_ids={ref.saved_item.retrieval_run_item_id for ref in prompt_context_refs},
        )
        if final_decision is decision:
            return decision
        run = self._require_run(db, retrieval_run_id)
        selected_item_ids = set(final_decision.selected_item_ids)
        self.repository.update_context_selection(
            db,
            retrieval_run_id=retrieval_run_id,
            selected_item_ids=selected_item_ids,
        )
        if self.settings.context_budget_store_debug_trace:
            self.repository.update_retrieval_run_trace(
                db,
                run=run,
                context_budget_json=final_decision.trace.model_dump(mode="json", exclude_none=True),
            )
        _log_context_budget(
            run=run,
            decision=final_decision,
            event=(
                "rag.context_budget.exhausted"
                if final_decision.trace.usage.budget_exhausted
                else "rag.context_budget.applied"
            ),
        )
        return final_decision

    def _build_evidence_pack(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        selected_context_refs: list[ContextCandidateRef],
        selected_citation_sources: list[CitationSource],
        candidate_context_items: int,
    ) -> EvidencePack:
        run = self._require_run(db, retrieval_run_id)
        policy = _evidence_pack_policy(self.settings)
        candidates = _evidence_candidates(selected_context_refs, selected_citation_sources)
        try:
            pack = self.evidence_pack_builder.build(
                candidates,
                policy=policy,
                candidate_context_items=candidate_context_items,
            )
        except Exception:
            _log_evidence_pack_failed(
                run=run,
                input_item_count=len(candidates),
                event="rag.evidence_pack.failed",
            )
            raise
        if policy.store_debug_trace:
            self.repository.update_retrieval_run_trace(
                db,
                run=run,
                context_compression_json=pack.trace.model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            )
        _log_evidence_pack(
            run=run,
            pack=pack,
            event="rag.evidence_pack.built" if policy.enabled else "rag.evidence_pack.skipped",
        )
        return pack

    def _ensure_direct_strategy_enabled(self, strategy_type: RetrievalStrategy) -> None:
        if strategy_type == RetrievalStrategy.HYBRID:
            if not self.settings.hybrid_enabled:
                raise RagSearchPipelineError("strategy_not_enabled", 409)
            if _hybrid_uses_sparse(self.settings) and not self.settings.sparse_enabled:
                raise RagSearchPipelineError("strategy_not_enabled", 409)
            return
        if strategy_type == RetrievalStrategy.SPARSE:
            if not self.settings.sparse_enabled:
                raise RagSearchPipelineError("strategy_not_enabled", 409)
            return
        if strategy_type != RetrievalStrategy.DENSE:
            raise RagSearchPipelineError("strategy_not_enabled", 409)

    def _mark_failed_safely(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        error_code: str,
        latency_tracker: LatencyTracker | None = None,
        rollback: bool = True,
    ) -> None:
        if rollback:
            db.rollback()
        run = self.repository.get_run(db, retrieval_run_id=retrieval_run_id)
        if run is None:
            return
        self.repository.mark_failed(
            db,
            run=run,
            error_code=error_code,
            finished_at=datetime.now(UTC),
            latency_breakdown_json=(
                latency_tracker.snapshot() if latency_tracker is not None else None
            ),
        )
        db.commit()
        self._export_retrieval_trace_safely(db, retrieval_run_id=retrieval_run_id)

    def _export_retrieval_trace_safely(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> None:
        try:
            self.trace_export_service.export_retrieval_run(
                db,
                retrieval_run_id=retrieval_run_id,
            )
        except Exception:
            return

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


def _retrieval_execution_strategy(strategy: RetrievalStrategy) -> RetrievalStrategy:
    if strategy == RetrievalStrategy.FALLBACK_DENSE:
        return RetrievalStrategy.DENSE
    if strategy in {RetrievalStrategy.DENSE, RetrievalStrategy.SPARSE, RetrievalStrategy.HYBRID}:
        return strategy
    return RetrievalStrategy.DENSE


def _is_executable_router_strategy(strategy: RetrievalStrategy) -> bool:
    return strategy in {
        RetrievalStrategy.DENSE,
        RetrievalStrategy.SPARSE,
        RetrievalStrategy.HYBRID,
        RetrievalStrategy.FALLBACK_DENSE,
    }


def _should_use_agentic_loop(
    requested_strategy: RetrievalStrategy,
    router_decision: Any,
) -> bool:
    return (
        requested_strategy == RetrievalStrategy.AGENTIC_ROUTER
        and router_decision is not None
        and bool(router_decision.router_enabled)
    )


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _score_summary(
    *,
    requested_top_k: int,
    qdrant_candidate_count: int,
    sparse_candidate_count: int | None = None,
    hybrid_candidate_count: int | None = None,
    checked_candidates: list[CheckedRetrievalCandidate],
    selected_count: int,
    top1_rerank_score: float | None,
    fusion_method: str | None = None,
    excluded_by_rdb_check_count: int | None = None,
) -> RetrievalScoreSummary:
    source_candidate_count = (
        hybrid_candidate_count
        if hybrid_candidate_count is not None
        else sparse_candidate_count
        if sparse_candidate_count is not None
        else qdrant_candidate_count
    )
    retrieval_scores = [candidate.retrieval_score for candidate in checked_candidates]
    summary_payload: dict[str, object] = {
        "requested_top_k": requested_top_k,
        "qdrant_candidate_count": qdrant_candidate_count,
        "sparse_candidate_count": sparse_candidate_count,
        "post_filter_candidate_count": len(checked_candidates),
        "selected_count": selected_count,
        "excluded_by_rdb_check_count": (
            excluded_by_rdb_check_count
            if excluded_by_rdb_check_count is not None
            else source_candidate_count - len(checked_candidates)
        ),
        "top1_retrieval_score": _round_score(retrieval_scores[0]) if retrieval_scores else None,
        "top3_avg_retrieval_score": (
            _round_score(sum(retrieval_scores[:3]) / min(3, len(retrieval_scores)))
            if retrieval_scores
            else None
        ),
        "top1_rerank_score": (
            _round_score(top1_rerank_score) if top1_rerank_score is not None else None
        ),
    }
    if hybrid_candidate_count is not None:
        summary_payload["hybrid_candidate_count"] = hybrid_candidate_count
    if fusion_method is not None:
        summary_payload["fusion_method"] = fusion_method
    return RetrievalScoreSummary(**summary_payload)


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
    final_rank: int,
    selected_flag: bool,
    retrieval_source: RetrievalSource = RetrievalSource.DENSE,
) -> RetrievalRunItemInput:
    return RetrievalRunItemInput(
        document_chunk_id=candidate.chunk.document_chunk_id,
        retrieval_score=_decimal_score(candidate.retrieval_score),
        rerank_score=_decimal_score(rerank_score),
        rank_order=candidate.rank_order,
        rerank_order=rerank_order,
        selected_flag=selected_flag,
        payload_snapshot=_payload_snapshot(candidate),
        retrieval_source=retrieval_source.value,
        score_breakdown_json=_score_breakdown(
            candidate,
            rerank_score=rerank_score,
            rerank_order=rerank_order,
            final_rank=final_rank,
            selected_flag=selected_flag,
            retrieval_source=retrieval_source,
        ),
    )


def _sparse_run_item_input(
    candidate: CheckedRetrievalCandidate,
    *,
    final_rank: int,
    selected_flag: bool,
) -> RetrievalRunItemInput:
    return RetrievalRunItemInput(
        document_chunk_id=candidate.chunk.document_chunk_id,
        retrieval_score=_decimal_score(candidate.retrieval_score),
        rerank_score=None,
        rank_order=candidate.rank_order,
        rerank_order=None,
        selected_flag=selected_flag,
        payload_snapshot=_payload_snapshot(candidate),
        retrieval_source=RetrievalSource.SPARSE.value,
        score_breakdown_json=_sparse_score_breakdown(
            candidate,
            final_rank=final_rank,
            selected_flag=selected_flag,
        ),
    )


def _hybrid_run_item_input(
    candidate: CheckedRetrievalCandidate,
    *,
    final_rank: int,
    selected_flag: bool,
    fusion_method: FusionMethod,
) -> RetrievalRunItemInput:
    return RetrievalRunItemInput(
        document_chunk_id=candidate.chunk.document_chunk_id,
        retrieval_score=_decimal_score(candidate.retrieval_score),
        rerank_score=None,
        rank_order=candidate.rank_order,
        rerank_order=None,
        selected_flag=selected_flag,
        payload_snapshot=_payload_snapshot(candidate),
        retrieval_source=RetrievalSource.HYBRID.value,
        score_breakdown_json=_hybrid_score_breakdown(
            candidate,
            final_rank=final_rank,
            selected_flag=selected_flag,
            fusion_method=fusion_method,
        ),
    )


def _agentic_run_item_input(
    candidate: CheckedRetrievalCandidate,
    *,
    rerank_score: float | None,
    rerank_order: int | None,
    final_rank: int,
    selected_flag: bool,
    agentic_result: AgenticRetrievalResult,
    trace_strategy: RetrievalStrategy = RetrievalStrategy.AGENTIC_ROUTER,
) -> RetrievalRunItemInput:
    retrieval_source = _agentic_item_source(candidate)
    return RetrievalRunItemInput(
        document_chunk_id=candidate.chunk.document_chunk_id,
        retrieval_score=_decimal_score(candidate.retrieval_score),
        rerank_score=_decimal_score(rerank_score) if rerank_score is not None else None,
        rank_order=candidate.rank_order,
        rerank_order=rerank_order,
        selected_flag=selected_flag,
        payload_snapshot=_payload_snapshot(candidate),
        retrieval_source=retrieval_source.value,
        score_breakdown_json=_agentic_score_breakdown(
            candidate,
            rerank_score=rerank_score,
            rerank_order=rerank_order,
            final_rank=final_rank,
            selected_flag=selected_flag,
            agentic_result=agentic_result,
            item_source=retrieval_source,
            trace_strategy=trace_strategy,
        ),
    )


def _response_item(
    candidate: CheckedRetrievalCandidate,
    *,
    saved_item_id: int,
    rerank_score: float | None,
    rerank_order: int | None,
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
        rerank_score=_round_score(rerank_score) if rerank_score is not None else None,
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
        source_type=_citation_source_type(candidate),
        source_url=_citation_source_url(candidate),
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
        source_type=source.source_type,
        source_url=source.source_url,
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
    for key, value in _safe_chunk_metadata(candidate.chunk.metadata_json).items():
        _add_optional(snapshot, key, value)
    return snapshot


def _retrieval_run_debug_summary(run: RetrievalRun) -> RetrievalRunDebugSummary:
    confidence_label = (
        run.confidence_label if run.confidence_label in {"High", "Medium", "Low"} else None
    )
    return RetrievalRunDebugSummary(
        retrieval_run_id=run.retrieval_run_id,
        origin_type="chat" if run.chat_session_id is not None else "standalone",
        chat_session_id=run.chat_session_id,
        request_message_id=run.request_message_id,
        status=run.status,
        strategy_type=RetrievalStrategy(run.strategy_type),
        error_code=run.error_code,
        query_hash=run.query_hash,
        top_k=run.top_k,
        retrieval_score_summary=_safe_json_object(run.retrieval_score_summary),
        query_plan_json=_safe_json_object(run.query_plan_json),
        strategy_decision_json=_safe_json_object(run.strategy_decision_json),
        latency_breakdown_json=_safe_json_object(run.latency_breakdown_json),
        retrieval_settings_json=_safe_json_object(run.retrieval_settings_json),
        context_budget_json=sanitize_context_budget_json(run.context_budget_json),
        context_compression_json=sanitize_context_compression_json(run.context_compression_json),
        tool_result_compression_json=sanitize_tool_result_compression_json(
            run.tool_result_compression_json
        ),
        rerank_score_top1=_optional_rounded_float(run.rerank_score_top1),
        answer_confidence=_optional_rounded_float(run.answer_confidence),
        groundedness_score=_optional_rounded_float(run.groundedness_score),
        confidence_label=confidence_label,
        started_at=_aware_utc(run.started_at) if run.started_at is not None else None,
        finished_at=_aware_utc(run.finished_at) if run.finished_at is not None else None,
        created_at=_aware_utc(run.created_at),
    )


def _retrieval_run_debug_item(item: RetrievalRunItem) -> RetrievalRunDebugItem:
    payload_snapshot = _safe_json_object(item.payload_snapshot)
    score_breakdown = _safe_json_object(item.score_breakdown_json)
    return RetrievalRunDebugItem(
        retrieval_run_item_id=item.retrieval_run_item_id,
        document_chunk_id=item.document_chunk_id,
        retrieval_score=_round_score(float(item.retrieval_score)),
        rerank_score=_optional_rounded_float(item.rerank_score),
        rank_order=item.rank_order,
        rerank_order=item.rerank_order,
        selected_flag=item.selected_flag,
        retrieval_source=_safe_optional_string(item.retrieval_source, max_length=50),
        payload_snapshot=payload_snapshot,
        score_breakdown_json=score_breakdown,
        source_label=_safe_snapshot_string(payload_snapshot, "source_label", max_length=255),
        page_from=_safe_snapshot_int(payload_snapshot, "page_from"),
        page_to=_safe_snapshot_int(payload_snapshot, "page_to"),
        old_version_flag=_safe_snapshot_bool(payload_snapshot, "old_version_flag"),
        created_at=_aware_utc(item.created_at),
    )


def _retrieval_settings_snapshot(
    *,
    settings: Settings,
    top_k: int,
    rerank_top_n: int,
    filters: RetrievalFilters,
    strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
) -> dict[str, object]:
    snapshot = build_retrieval_settings_snapshot(
        settings=settings,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        filters=filters,
        strategy_type=strategy_type,
    )
    if strategy_type == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
        snapshot.update(
            TraceRedactor.safe_dict(
                {
                    "llm_orchestrator_enabled": settings.llm_orchestrator_enabled,
                    "max_tool_calls": settings.llm_orchestrator_max_tool_calls,
                    "max_search_calls": settings.llm_orchestrator_max_search_calls,
                    "timeout_seconds": settings.llm_orchestrator_timeout_seconds,
                    "max_query_chars": settings.llm_orchestrator_max_query_chars,
                    "max_tool_result_items": settings.llm_orchestrator_max_tool_result_items,
                    "max_snippet_chars": settings.llm_orchestrator_max_snippet_chars,
                    "allow_trace_inspection": settings.llm_orchestrator_allow_trace_inspection,
                    "allow_admin_tools": False,
                    "tool_result_compression_enabled": settings.tool_result_compression_enabled,
                    "tool_result_max_items_per_tool": (
                        settings.tool_result_compression_max_items_per_tool
                    ),
                    "tool_result_max_total_items_per_turn": (
                        settings.tool_result_compression_max_total_items_per_turn
                    ),
                    "tool_result_max_snippet_chars": (
                        settings.tool_result_compression_max_snippet_chars
                    ),
                    "tool_result_max_tokens_per_tool": (
                        settings.tool_result_compression_max_tokens_per_tool
                    ),
                    "tool_result_max_total_tokens": (
                        settings.tool_result_compression_max_total_tool_result_tokens
                    ),
                }
            )
        )
    if strategy_type == RetrievalStrategy.LANGCHAIN_AGENTIC:
        snapshot.update(
            TraceRedactor.safe_dict(
                {
                    "orchestrator_provider": "langchain",
                    "langchain_agentic_enabled": settings.langchain_agentic_enabled,
                    "max_tool_calls": settings.langchain_agentic_max_tool_calls,
                    "max_search_calls": settings.langchain_agentic_max_search_calls,
                    "timeout_seconds": settings.langchain_agentic_timeout_seconds,
                    "max_query_chars": settings.langchain_agentic_max_query_chars,
                    "max_tool_result_items": settings.langchain_agentic_max_tool_result_items,
                    "max_snippet_chars": settings.langchain_agentic_max_snippet_chars,
                    "allow_admin_tools": False,
                    "tool_result_compression_enabled": settings.tool_result_compression_enabled,
                    "tool_result_max_items_per_tool": (
                        settings.tool_result_compression_max_items_per_tool
                    ),
                    "tool_result_max_total_items_per_turn": (
                        settings.tool_result_compression_max_total_items_per_turn
                    ),
                    "tool_result_max_snippet_chars": (
                        settings.tool_result_compression_max_snippet_chars
                    ),
                    "tool_result_max_tokens_per_tool": (
                        settings.tool_result_compression_max_tokens_per_tool
                    ),
                    "tool_result_max_total_tokens": (
                        settings.tool_result_compression_max_total_tool_result_tokens
                    ),
                }
            )
        )
    return snapshot


def build_llm_tool_orchestrator_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    plan_metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    base = build_default_dense_query_plan(
        query_hash=query_hash,
        filters=filters,
        plan_metadata=_llm_orchestrator_plan_metadata(plan_metadata),
    )
    base.update(
        {
            "strategy_type": RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
            "query_mode": "llm_tool_calling_retrieval",
            "reason_codes": [
                "phase2_5_llm_tool_orchestrator",
                "retrieval_only_tools",
                "bounded_loop",
            ],
            "candidate_strategies": [
                RetrievalStrategy.DENSE.value,
                RetrievalStrategy.SPARSE.value,
                RetrievalStrategy.HYBRID.value,
            ],
            "recommended_strategy": RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
        }
    )
    return TraceRedactor.safe_dict(base)


def build_llm_tool_orchestrator_strategy_decision() -> dict[str, object]:
    return TraceRedactor.safe_dict(
        {
            "schema_version": "phase2.trace.v1",
            "selected_strategy": RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
            "execution_strategy": RetrievalStrategy.LLM_TOOL_ORCHESTRATOR.value,
            "fallback_used": False,
            "router_enabled": False,
            "decision_source": "llm_tool_calling",
            "decision_policy": "bounded_retrieval_only_tools",
            "reason_codes": [
                "explicit_strategy_llm_tool_orchestrator",
                "retrieval_only_tools",
            ],
        }
    )


def build_langchain_agentic_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    plan_metadata: dict[str, Any] | None = None,
) -> dict[str, object]:
    base = build_default_dense_query_plan(
        query_hash=query_hash,
        filters=filters,
        plan_metadata=_llm_orchestrator_plan_metadata(plan_metadata),
    )
    base.update(
        {
            "strategy_type": RetrievalStrategy.LANGCHAIN_AGENTIC.value,
            "query_mode": "langchain_agentic_retrieval",
            "reason_codes": [
                "phase2_5_langchain_agentic",
                "langchain_runnable_planner",
                "langchain_structured_tools",
                "retrieval_only_tools",
                "bounded_loop",
            ],
            "candidate_strategies": [
                RetrievalStrategy.DENSE.value,
                RetrievalStrategy.SPARSE.value,
                RetrievalStrategy.HYBRID.value,
            ],
            "recommended_strategy": RetrievalStrategy.LANGCHAIN_AGENTIC.value,
        }
    )
    return TraceRedactor.safe_dict(base)


def build_langchain_agentic_strategy_decision() -> dict[str, object]:
    return TraceRedactor.safe_dict(
        {
            "schema_version": "phase2.trace.v1",
            "selected_strategy": RetrievalStrategy.LANGCHAIN_AGENTIC.value,
            "execution_strategy": RetrievalStrategy.LANGCHAIN_AGENTIC.value,
            "fallback_used": False,
            "router_enabled": False,
            "decision_source": "langchain_agentic",
            "decision_policy": "langchain_bounded_retrieval_only_tools",
            "orchestrator_provider": "langchain",
            "reason_codes": [
                "explicit_strategy_langchain_agentic",
                "langchain_runnable_planner",
                "langchain_structured_tools",
                "retrieval_only_tools",
            ],
        }
    )


def _llm_orchestrator_plan_metadata(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {
        str(key): _clean_llm_orchestrator_plan_metadata(nested)
        for key, nested in value.items()
        if not str(key).endswith("_preview") and str(key) != "query_preview"
    }


def _clean_llm_orchestrator_plan_metadata(value: object) -> object:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            if key_text.endswith("_preview") or key_text == "query_preview":
                continue
            cleaned[key_text] = _clean_llm_orchestrator_plan_metadata(nested)
        return cleaned
    if isinstance(value, list):
        return [_clean_llm_orchestrator_plan_metadata(item) for item in value]
    return value


def _fusion_method(settings: Settings) -> FusionMethod:
    return FusionMethod(settings.hybrid_fusion_method)


_SOURCE_AFFINITY_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")
_SOURCE_AFFINITY_STOP_WORDS = {
    "and",
    "compare",
    "comparison",
    "please",
    "search",
    "retrieval",
    "method",
    "methods",
    "strategy",
    "strategies",
}


def _order_by_source_affinity(
    query: str,
    candidates: list[CheckedRetrievalCandidate],
) -> list[CheckedRetrievalCandidate]:
    tokens = _source_affinity_tokens(query)
    if not tokens or len(candidates) < 2:
        return candidates
    scored = [
        (_source_affinity_score(candidate, tokens), index, candidate)
        for index, candidate in enumerate(candidates)
    ]
    if max(score for score, _, _ in scored) <= 0:
        return candidates
    return [
        candidate
        for _, _, candidate in sorted(
            scored,
            key=lambda item: (-item[0], item[1]),
        )
    ]


def _source_affinity_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _SOURCE_AFFINITY_TOKEN_RE.finditer(query):
        token = match.group(0).strip("._-").lower()
        if len(token) < 4 or token in _SOURCE_AFFINITY_STOP_WORDS or token in seen:
            continue
        tokens.append(token)
        seen.add(token)
        if len(tokens) >= 8:
            break
    return tokens


def _source_affinity_score(
    candidate: CheckedRetrievalCandidate,
    tokens: list[str],
) -> int:
    file_name = (candidate.document_version.file_name or "").lower()
    title = (candidate.logical_document.title or "").lower()
    section_title = (candidate.chunk.section_title or "").lower()
    combined = f"{file_name} {title} {section_title}"
    score = 0
    for token in tokens:
        if token not in combined:
            continue
        score += 1
        if token in title:
            score += 2
        if token in file_name:
            score += 1
        if token in section_title:
            score += 1
    return score


def _hybrid_uses_dense(settings: Settings) -> bool:
    return settings.hybrid_dense_weight > 0


def _hybrid_uses_sparse(settings: Settings) -> bool:
    return settings.hybrid_sparse_weight > 0


def _hybrid_candidate_limit(top_k: int, settings: Settings) -> int:
    return min(top_k * settings.hybrid_candidate_multiplier, 50)


def _score_breakdown(
    candidate: CheckedRetrievalCandidate,
    *,
    rerank_score: float,
    rerank_order: int,
    final_rank: int,
    selected_flag: bool,
    retrieval_source: RetrievalSource = RetrievalSource.DENSE,
) -> dict[str, object]:
    return build_dense_score_breakdown(
        dense_score=candidate.retrieval_score,
        rank_order=candidate.rank_order,
        rerank_score=rerank_score,
        rerank_order=rerank_order,
        final_rank=final_rank,
        selected_flag=selected_flag,
        retrieval_source=retrieval_source,
    )


def _sparse_score_breakdown(
    candidate: CheckedRetrievalCandidate,
    *,
    final_rank: int,
    selected_flag: bool,
) -> dict[str, object]:
    return build_sparse_score_breakdown(
        sparse_score=candidate.retrieval_score,
        rank_order=candidate.rank_order,
        final_rank=final_rank,
        selected_flag=selected_flag,
    )


def _hybrid_score_breakdown(
    candidate: CheckedRetrievalCandidate,
    *,
    final_rank: int,
    selected_flag: bool,
    fusion_method: FusionMethod,
) -> dict[str, object]:
    return build_hybrid_score_breakdown(
        dense_score=_payload_float(candidate, "dense_score"),
        sparse_score=_payload_float(candidate, "sparse_score"),
        fused_score=candidate.retrieval_score,
        rank_order=candidate.rank_order,
        final_rank=final_rank,
        selected_flag=selected_flag,
        fusion_method=fusion_method,
        dense_rank=_payload_int(candidate, "dense_rank"),
        sparse_rank=_payload_int(candidate, "sparse_rank"),
    )


def _agentic_score_breakdown(
    candidate: CheckedRetrievalCandidate,
    *,
    rerank_score: float | None,
    rerank_order: int | None,
    final_rank: int,
    selected_flag: bool,
    agentic_result: AgenticRetrievalResult,
    item_source: RetrievalSource,
    trace_strategy: RetrievalStrategy = RetrievalStrategy.AGENTIC_ROUTER,
) -> dict[str, object]:
    dense_score = _payload_float(candidate, "dense_score")
    sparse_score = _payload_float(candidate, "sparse_score")
    fused_score = _payload_float(candidate, "fused_score")
    if dense_score is None and item_source in {
        RetrievalSource.DENSE,
        RetrievalSource.FALLBACK_DENSE,
    }:
        dense_score = candidate.retrieval_score
    if sparse_score is None and item_source == RetrievalSource.SPARSE:
        sparse_score = candidate.retrieval_score
    if fused_score is None and item_source == RetrievalSource.HYBRID:
        fused_score = candidate.retrieval_score
    payload: dict[str, object] = {
        "schema_version": "phase2.trace.v1",
        "retrieval_source": trace_strategy.value,
        "item_retrieval_source": item_source.value,
        "sources": _payload_string_list(candidate, "agentic_sources"),
        "initial_strategy": agentic_result.initial_strategy.value,
        "fallback_used": agentic_result.fallback_used,
        "fallback_strategy": (
            agentic_result.fallback_strategies[-1].value
            if agentic_result.fallback_strategies
            else None
        ),
        "fallback_reason": agentic_result.fallback_reason,
        "dense_score": _round_score(dense_score) if dense_score is not None else None,
        "sparse_score": _round_score(sparse_score) if sparse_score is not None else None,
        "fused_score": _round_score(fused_score) if fused_score is not None else None,
        "rerank_score": _round_score(rerank_score) if rerank_score is not None else None,
        "rank_order": candidate.rank_order,
        "rerank_order": rerank_order,
        "final_rank": final_rank,
        "selected_flag": selected_flag,
    }
    return TraceRedactor.safe_dict(payload)


def _agentic_item_source(candidate: CheckedRetrievalCandidate) -> RetrievalSource:
    source = candidate.payload.get("agentic_primary_source")
    if source == RetrievalSource.SPARSE.value:
        return RetrievalSource.SPARSE
    if source == RetrievalSource.HYBRID.value:
        return RetrievalSource.HYBRID
    if source == RetrievalSource.FALLBACK_DENSE.value:
        return RetrievalSource.FALLBACK_DENSE
    return RetrievalSource.DENSE


def _payload_string_list(candidate: CheckedRetrievalCandidate, key: str) -> list[str]:
    value = candidate.payload.get(key)
    if not isinstance(value, list):
        return []
    safe_values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        safe = TraceRedactor.safe_string(item, max_length=80)
        if safe:
            safe_values.append(safe)
    return safe_values[:10]


def _agentic_score_summary(
    *,
    requested_top_k: int,
    checked_candidates: list[CheckedRetrievalCandidate],
    selected_count: int,
    top1_rerank_score: float | None,
    agentic_result: AgenticRetrievalResult,
) -> RetrievalScoreSummary:
    summary = _score_summary(
        requested_top_k=requested_top_k,
        qdrant_candidate_count=agentic_result.qdrant_candidate_count,
        sparse_candidate_count=agentic_result.sparse_candidate_count,
        hybrid_candidate_count=agentic_result.hybrid_candidate_count,
        checked_candidates=checked_candidates,
        selected_count=selected_count,
        top1_rerank_score=top1_rerank_score,
        excluded_by_rdb_check_count=agentic_result.excluded_by_rdb_check_count,
    ).model_dump(mode="json")
    summary.update(agentic_result.summary_fields())
    return RetrievalScoreSummary(**summary)


def _agentic_strategy_decision(
    base_decision: dict[str, Any] | None,
    *,
    agentic_result: AgenticRetrievalResult,
) -> dict[str, object] | None:
    if base_decision is None:
        return None
    decision: dict[str, object] = dict(base_decision)
    agentic_fields = agentic_result.decision_trace_fields()
    router_fallback_used = bool(decision.get("fallback_used"))
    if agentic_result.fallback_used:
        decision.update(agentic_fields)
    else:
        for key, value in agentic_fields.items():
            if key in {"fallback_used", "fallback_reason", "fallback_strategy"}:
                continue
            decision[key] = value
        decision["fallback_used"] = router_fallback_used
    reason_codes = decision.get("reason_codes")
    if isinstance(reason_codes, list):
        merged_reason_codes = [str(code) for code in reason_codes]
    else:
        merged_reason_codes = []
    final_decision = agentic_result.final_decision
    if final_decision is not None:
        for code in final_decision.reason_codes:
            if code not in merged_reason_codes:
                merged_reason_codes.append(code)
    if agentic_result.fallback_used and "agentic_fallback_executed" not in merged_reason_codes:
        merged_reason_codes.append("agentic_fallback_executed")
    decision["reason_codes"] = merged_reason_codes
    return TraceRedactor.safe_dict(decision)


def _payload_float(candidate: CheckedRetrievalCandidate, key: str) -> float | None:
    value = candidate.payload.get(key)
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        return None
    score = float(value)
    if not math.isfinite(score):
        return None
    return score


def _payload_int(candidate: CheckedRetrievalCandidate, key: str) -> int | None:
    value = candidate.payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def _source_label(candidate: CheckedRetrievalCandidate) -> str:
    raw_label = candidate.document_version.file_name or candidate.logical_document.title
    normalized = _sanitize_label(raw_label.replace("\\", "/"))
    label = _sanitize_label(PurePosixPath(normalized).name)
    fallback = _sanitize_label(candidate.logical_document.title)
    metadata = _safe_chunk_metadata(candidate.chunk.metadata_json)
    safe_label = (
        _url_source_label(metadata)
        or label
        or fallback
        or f"document:{candidate.logical_document.logical_document_id}"
    )
    metadata_label = _metadata_source_suffix_from_safe(metadata)
    if metadata_label:
        return f"{safe_label} / {metadata_label}"[:255]
    return safe_label[:255]


def _citation_source_type(candidate: CheckedRetrievalCandidate) -> str:
    metadata = _safe_chunk_metadata(candidate.chunk.metadata_json)
    if metadata.get("source_type") == "url" and _url_source_label(metadata):
        return "external_url"
    return "upload"


def _citation_source_url(candidate: CheckedRetrievalCandidate) -> str | None:
    metadata = _safe_chunk_metadata(candidate.chunk.metadata_json)
    if metadata.get("source_type") != "url":
        return None
    source_url = metadata.get("source_url")
    if not isinstance(source_url, str):
        return None
    safe_url = _safe_url_metadata_string(source_url)
    if not safe_url or safe_url == "redacted":
        return None
    return safe_url


def _metadata_source_suffix(value: object) -> str | None:
    return _metadata_source_suffix_from_safe(_safe_chunk_metadata(value))


def _metadata_source_suffix_from_safe(metadata: dict[str, object]) -> str | None:
    structure_type = metadata.get("structure_type")
    if structure_type == "excel_sheet":
        sheet_name = _metadata_str(metadata, "sheet_name")
        row_from = metadata.get("row_from")
        row_to = metadata.get("row_to")
        parts = []
        if sheet_name:
            parts.append(f"Sheet: {sheet_name}")
        if isinstance(row_from, int) and isinstance(row_to, int):
            parts.append(f"Rows {row_from}-{row_to}" if row_from != row_to else f"Row {row_from}")
        return " / ".join(parts) or None
    if structure_type == "powerpoint_slide":
        slide_number = metadata.get("slide_number")
        slide_title = _metadata_str(metadata, "slide_title")
        parts = []
        if isinstance(slide_number, int):
            parts.append(f"Slide {slide_number}")
        if slide_title:
            parts.append(f"Title: {slide_title}")
        return " / ".join(parts) or None
    if structure_type == "html_section":
        heading_path = _metadata_str(metadata, "heading_path")
        element_type = _metadata_str(metadata, "element_type")
        return heading_path or element_type
    if structure_type == "xml_element":
        xml_path = _metadata_str(metadata, "xml_path")
        element_name = _metadata_str(metadata, "element_name")
        return xml_path or element_name
    return None


def _url_source_label(metadata: dict[str, object]) -> str | None:
    if metadata.get("source_type") != "url":
        return None
    source_url = metadata.get("source_url")
    if not isinstance(source_url, str):
        return None
    safe_url = _safe_url_metadata_string(source_url)
    if not safe_url or safe_url == "redacted":
        return None
    return safe_url


def _safe_url_metadata_string(value: str) -> str:
    safe_url = redact_url_for_display(value)
    if safe_url == "redacted" or SENSITIVE_OUTPUT_RE.search(safe_url):
        return "redacted"
    return safe_url[:200]


def _metadata_str(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if not isinstance(value, str):
        return None
    return _safe_display_text(value)


def _safe_chunk_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "parent_child_schema_version",
        "structure_type",
        "chunk_level",
        "parent_chunk_key",
        "child_chunk_key",
        "parent_title",
        "sheet_name",
        "row_from",
        "row_to",
        "column_from",
        "column_to",
        "table_index",
        "slide_number",
        "slide_title",
        "shape_count",
        "table_count",
        "html_title",
        "heading_path",
        "element_type",
        "element_index",
        "xml_root",
        "xml_path",
        "element_name",
        "source_type",
        "source_url",
    }
    safe: dict[str, object] = {}
    for key, item in value.items():
        if key not in allowed_keys:
            continue
        if isinstance(item, str):
            redacted = (
                _safe_url_metadata_string(item)
                if key == "source_url"
                else TraceRedactor.safe_string(item, max_length=120)
            )
            if redacted:
                safe[key] = redacted
        elif isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int | float):
            safe[key] = item
    return safe


def _context_budget_policy(settings: Settings) -> ContextBudgetPolicy:
    return ContextBudgetPolicy(
        enabled=settings.context_budget_enabled,
        max_context_tokens=settings.context_budget_max_context_tokens,
        reserve_answer_tokens=settings.context_budget_reserve_answer_tokens,
        max_context_items=settings.context_budget_max_context_items,
        max_tokens_per_item=settings.context_budget_max_tokens_per_item,
        min_citation_candidates=settings.context_budget_min_citation_candidates,
        drop_low_score_first=settings.context_budget_drop_low_score_first,
        preserve_source_diversity=settings.context_budget_preserve_source_diversity,
        token_estimator="heuristic",
        store_debug_trace=settings.context_budget_store_debug_trace,
    )


def _evidence_pack_policy(settings: Settings) -> EvidencePackPolicy:
    enabled = settings.evidence_pack_enabled
    if enabled:
        max_items = settings.evidence_pack_max_items
        max_items_per_source = settings.evidence_pack_max_items_per_source
        max_chars_per_item = settings.evidence_pack_max_chars_per_item
        max_total_chars = min(
            settings.evidence_pack_max_total_chars,
            settings.generation_max_context_chars,
        )
    else:
        max_items = settings.context_budget_max_context_items
        max_items_per_source = settings.context_budget_max_context_items
        max_chars_per_item = settings.generation_max_context_chars
        max_total_chars = settings.generation_max_context_chars
    return EvidencePackPolicy(
        enabled=enabled,
        max_items=max_items,
        max_items_per_source=max_items_per_source,
        max_chars_per_item=max_chars_per_item,
        max_total_chars=max_total_chars,
        near_duplicate_threshold=settings.evidence_pack_near_duplicate_threshold,
        preserve_citation_candidates=settings.evidence_pack_preserve_citation_candidates,
        group_by_source=settings.evidence_pack_group_by_source,
        store_debug_trace=settings.evidence_pack_store_debug_trace,
    )


def _context_budget_candidates(
    refs: list[ContextCandidateRef],
) -> list[ContextBudgetCandidate]:
    return [
        ContextBudgetCandidate(
            retrieval_run_item_id=ref.saved_item.retrieval_run_item_id,
            document_chunk_id=ref.candidate.chunk.document_chunk_id,
            source_label=_source_label(ref.candidate),
            section_title=_safe_display_text(ref.candidate.chunk.section_title),
            page_from=ref.candidate.chunk.page_from,
            page_to=ref.candidate.chunk.page_to,
            score=_round_score(ref.candidate.retrieval_score),
            rank=ref.rank,
            rerank_score=_round_score(ref.rerank_score) if ref.rerank_score is not None else None,
            rerank_order=ref.rerank_order,
            text=_clean_context_text(ref.candidate.chunk.content_text),
            citation_candidate=ref.citation_candidate,
            source_group_key=f"logical_document:{ref.candidate.logical_document.logical_document_id}",
            retrieval_source=ref.saved_item.retrieval_source,
        )
        for ref in refs
    ]


def _evidence_candidates(
    refs: list[ContextCandidateRef],
    citation_sources: list[CitationSource],
) -> list[EvidenceCandidate]:
    source_by_run_item_id = {source.retrieval_run_item_id: source for source in citation_sources}
    candidates: list[EvidenceCandidate] = []
    for ref in refs:
        source = source_by_run_item_id.get(ref.saved_item.retrieval_run_item_id)
        if source is None:
            continue
        candidates.append(
            EvidenceCandidate(
                retrieval_run_item_id=ref.saved_item.retrieval_run_item_id,
                document_chunk_id=ref.candidate.chunk.document_chunk_id,
                local_citation_id=source.local_citation_id,
                text=_clean_context_text(ref.candidate.chunk.content_text),
                source_label=_source_label(ref.candidate),
                section_title=_safe_display_text(ref.candidate.chunk.section_title),
                page_from=ref.candidate.chunk.page_from,
                page_to=ref.candidate.chunk.page_to,
                score=_round_score(ref.candidate.retrieval_score),
                rerank_score=_round_score(ref.rerank_score)
                if ref.rerank_score is not None
                else None,
                rank=ref.rank,
                rerank_order=ref.rerank_order,
                source_group_key=(
                    f"logical_document:{ref.candidate.logical_document.logical_document_id}"
                ),
                citation_candidate=ref.citation_candidate,
                retrieval_source=ref.saved_item.retrieval_source,
                logical_document_id=ref.candidate.logical_document.logical_document_id,
                document_version_id=ref.candidate.document_version.document_version_id,
            )
        )
    return candidates


def _context_budget_strategy(run: RetrievalRun) -> ContextBudgetStrategySummary:
    decision = run.strategy_decision_json if isinstance(run.strategy_decision_json, dict) else {}
    tools_used = decision.get("tools_used")
    return ContextBudgetStrategySummary(
        strategy_type=run.strategy_type,
        selected_strategy=_safe_string_value(decision.get("selected_strategy")),
        execution_strategy=_safe_string_value(decision.get("execution_strategy")),
        tools_used=tools_used if isinstance(tools_used, list) else [],
    )


def _selected_context_refs(
    refs: list[ContextCandidateRef],
    decision: ContextBudgetDecision,
) -> list[ContextCandidateRef]:
    selected_ids = set(decision.selected_item_ids)
    return [ref for ref in refs if ref.saved_item.retrieval_run_item_id in selected_ids]


def _context_citation_sources(
    refs: list[ContextCandidateRef],
    *,
    snippet_max_chars: int,
) -> list[CitationSource]:
    return [
        _citation_source(
            ref.candidate,
            saved_item=ref.saved_item,
            local_citation_id=local_id,
            snippet_max_chars=snippet_max_chars,
        )
        for local_id, ref in enumerate(refs, start=1)
    ]


def _context_refs_for_citation_sources(
    refs: list[ContextCandidateRef],
    citation_sources: list[CitationSource],
) -> list[ContextCandidateRef]:
    included_ids = {
        source.retrieval_run_item_id
        for source in citation_sources
        if source.retrieval_run_item_id is not None
    }
    return [ref for ref in refs if ref.saved_item.retrieval_run_item_id in included_ids]


def _summary_with_final_context_refs(
    summary: RetrievalScoreSummary,
    refs: list[ContextCandidateRef],
) -> RetrievalScoreSummary:
    payload = summary.model_dump(mode="json")
    retrieval_scores = [ref.candidate.retrieval_score for ref in refs]
    payload["selected_count"] = len(refs)
    payload["top1_retrieval_score"] = (
        _round_score(retrieval_scores[0]) if retrieval_scores else None
    )
    payload["top3_avg_retrieval_score"] = (
        _round_score(sum(retrieval_scores[:3]) / min(3, len(retrieval_scores)))
        if retrieval_scores
        else None
    )
    top1_rerank_score = next(
        (ref.rerank_score for ref in refs if ref.rerank_score is not None),
        None,
    )
    payload["top1_rerank_score"] = (
        _round_score(top1_rerank_score) if top1_rerank_score is not None else None
    )
    return RetrievalScoreSummary(**payload)


def _tool_result_compression_json_with_run_items(
    trace: ToolResultCompressionTrace | None,
    refs: list[ContextCandidateRef],
) -> dict[str, object] | None:
    if trace is None:
        return None
    item_id_by_chunk_id = {
        ref.candidate.chunk.document_chunk_id: ref.saved_item.retrieval_run_item_id for ref in refs
    }
    return attach_retrieval_run_item_ids(
        trace.model_dump(mode="json", exclude_none=True),
        item_id_by_chunk_id=item_id_by_chunk_id,
    )


def _log_context_budget(
    *,
    run: RetrievalRun,
    decision: ContextBudgetDecision,
    event: str,
) -> None:
    trace = decision.trace
    strategy = trace.strategy
    payload: dict[str, object] = {
        "request_id": run.request_id,
        "retrieval_run_id": run.retrieval_run_id,
        "strategy_type": run.strategy_type,
        "selected_strategy": strategy.selected_strategy if strategy else None,
        "execution_strategy": strategy.execution_strategy if strategy else None,
        "candidate_count": trace.items.candidate_count,
        "selected_count": trace.items.selected_count,
        "dropped_count": trace.items.dropped_count,
        "estimated_context_tokens": trace.usage.estimated_context_tokens,
        "remaining_context_tokens": trace.usage.remaining_context_tokens,
        "budget_exhausted": trace.usage.budget_exhausted,
        "drop_reason_counts": trace.drop_reasons,
    }
    logger.info(event, extra={"rag_context_budget": payload})


def _log_evidence_pack(
    *,
    run: RetrievalRun,
    pack: EvidencePack,
    event: str,
) -> None:
    trace = pack.trace
    payload: dict[str, object] = {
        "request_id": run.request_id,
        "retrieval_run_id": run.retrieval_run_id,
        "strategy_type": run.strategy_type,
        "selected_strategy": _safe_strategy_field(run, "selected_strategy"),
        "execution_strategy": _safe_strategy_field(run, "execution_strategy"),
        "input_item_count": trace.input.selected_context_items,
        "output_item_count": trace.output.evidence_item_count,
        "evidence_group_count": trace.output.evidence_group_count,
        "compression_ratio": trace.output.compression_ratio,
        "drop_reason_counts": trace.drops,
        "estimated_input_tokens": trace.input.input_estimated_tokens,
        "estimated_output_tokens": trace.output.output_estimated_tokens,
    }
    logger.info(event, extra={"rag_evidence_pack": payload})


def _log_evidence_pack_failed(
    *,
    run: RetrievalRun,
    input_item_count: int,
    event: str,
) -> None:
    payload: dict[str, object] = {
        "request_id": run.request_id,
        "retrieval_run_id": run.retrieval_run_id,
        "strategy_type": run.strategy_type,
        "selected_strategy": _safe_strategy_field(run, "selected_strategy"),
        "execution_strategy": _safe_strategy_field(run, "execution_strategy"),
        "input_item_count": max(0, input_item_count),
        "output_item_count": 0,
        "evidence_group_count": 0,
        "compression_ratio": 0.0,
        "drop_reason_counts": {},
        "estimated_input_tokens": 0,
        "estimated_output_tokens": 0,
    }
    logger.info(event, extra={"rag_evidence_pack": payload})


def _log_tool_result_compression(
    *,
    run: RetrievalRun,
    trace: dict[str, object],
    event: str,
) -> None:
    raw_summary = trace.get("summary")
    summary: dict[str, object] = raw_summary if isinstance(raw_summary, dict) else {}
    raw_drop_reasons = trace.get("drop_reasons")
    drop_reasons: dict[str, object] = raw_drop_reasons if isinstance(raw_drop_reasons, dict) else {}
    first_tool = _first_tool_result_trace(trace)
    payload: dict[str, object] = {
        "request_id": run.request_id,
        "retrieval_run_id": run.retrieval_run_id,
        "strategy_type": run.strategy_type,
        "tool_name": first_tool.get("tool_name"),
        "tool_call_id": first_tool.get("tool_call_id"),
        "original_item_count": summary.get("original_item_count"),
        "output_item_count": summary.get("output_item_count"),
        "dropped_item_count": summary.get("dropped_item_count"),
        "estimated_tokens_before": summary.get("estimated_tokens_before"),
        "estimated_tokens_after": summary.get("estimated_tokens_after"),
        "compression_ratio": summary.get("compression_ratio"),
        "drop_reason_counts": drop_reasons,
        "budget_exhausted": summary.get("budget_exhausted"),
    }
    logger.info(event, extra={"rag_tool_result_compression": TraceRedactor.safe_dict(payload)})


def _first_tool_result_trace(trace: dict[str, object]) -> dict[str, object]:
    by_tool = trace.get("by_tool")
    if not isinstance(by_tool, list):
        return {}
    for item in by_tool:
        if isinstance(item, dict):
            return item
    return {}


def _tool_result_oversized_rejected(trace: dict[str, object]) -> bool:
    raw_summary = trace.get("summary")
    summary: dict[str, object] = raw_summary if isinstance(raw_summary, dict) else {}
    return bool(summary.get("oversized_rejected_count"))


def _safe_strategy_field(run: RetrievalRun, key: str) -> str | None:
    decision = run.strategy_decision_json if isinstance(run.strategy_decision_json, dict) else {}
    return _safe_string_value(decision.get(key))


def _safe_string_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    safe = TraceRedactor.safe_string(value, max_length=100)
    return safe or None


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
    if _is_insufficient_evidence_answer(parsed_generation.answer_text):
        raise InsufficientEvidenceAnswerError()
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
        retrieval_summary=_retrieval_summary_response(run),
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


def _retrieval_summary_response(run: RetrievalRun) -> RagAskRetrievalSummary:
    decision = _safe_json_object(run.strategy_decision_json) or {}
    tools_used_value = decision.get("tools_used")
    tools_used = (
        [str(item) for item in tools_used_value if isinstance(item, str)]
        if isinstance(tools_used_value, list)
        else []
    )
    return RagAskRetrievalSummary(
        retrieval_run_id=run.retrieval_run_id,
        strategy_type=RetrievalStrategy(run.strategy_type),
        selected_strategy=_optional_safe_string(decision.get("selected_strategy")),
        execution_strategy=_optional_safe_string(decision.get("execution_strategy")),
        tools_used=tools_used,
        fallback_used=decision.get("fallback_used")
        if isinstance(decision.get("fallback_used"), bool)
        else None,
        no_context=decision.get("no_context")
        if isinstance(decision.get("no_context"), bool)
        else None,
    )


def _optional_safe_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    safe = _safe_display_text(value)
    return safe or None


def _is_insufficient_evidence_answer(answer_text: str) -> bool:
    normalized = " ".join(answer_text.lower().split())
    return any(
        phrase in normalized
        for phrase in (
            "十分な根拠がありません",
            "十分な根拠がない",
            "十分な情報がありません",
            "根拠が不足",
            "検索された引用では、この質問への回答を確定できません",
            "insufficient evidence",
            "insufficient context",
            "not enough evidence",
            "not enough context",
            "no sufficient evidence",
            "no usable context",
        )
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


def _optional_rounded_float(value: object) -> float | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    score = float(value)
    if not math.isfinite(score):
        return None
    return _round_score(score)


def _safe_json_object(value: dict[str, Any] | None) -> dict[str, object] | None:
    if value is None:
        return None
    return TraceRedactor.safe_dict(value)


def _safe_optional_string(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    return safe or None


def _safe_snapshot_string(
    payload_snapshot: dict[str, object] | None,
    key: str,
    *,
    max_length: int,
) -> str | None:
    if payload_snapshot is None:
        return None
    return _safe_optional_string(payload_snapshot.get(key), max_length=max_length)


def _safe_snapshot_int(payload_snapshot: dict[str, object] | None, key: str) -> int | None:
    if payload_snapshot is None:
        return None
    value = payload_snapshot.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_snapshot_bool(payload_snapshot: dict[str, object] | None, key: str) -> bool | None:
    if payload_snapshot is None:
        return None
    value = payload_snapshot.get(key)
    return value if isinstance(value, bool) else None


def _add_optional(payload: dict[str, object], key: str, value: object) -> None:
    if value is not None:
        payload[key] = value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
