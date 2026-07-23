from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    ClientMessageConflict,
    ConflictError,
    RequestInProgress,
    ResourceNotFound,
)
from app.db.models import (
    ChatMessage,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    User,
)
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
from app.rag.agentic_planner import create_agentic_strategy_planner
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    ParsedGenerationOutput,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.confidence import (
    ConfidenceInputs,
    ConfidenceScores,
    calculate_confidence,
    has_high_retrieval_support,
)
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
    RAG_GENERATION_SUPPORTED_ANSWER_RETRY_INSTRUCTIONS,
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    GenerationResult,
    TokenUsage,
    _lmstudio_model_name,
    create_answer_generator,
)
from app.rag.hybrid import HybridRetrievalStrategy
from app.rag.injection_detection import (
    INJECTION_PATTERN_REASON_CODE,
    detect_injection_patterns,
)
from app.rag.insufficient import is_insufficient_evidence_answer as _is_insufficient_evidence_answer
from app.rag.langchain_agentic import (
    LangChainAgenticExecutionResult,
    LangChainAgenticRetrievalOrchestrator,
)
from app.rag.langgraph_agentic import (
    LangGraphAgenticExecutionResult,
    LangGraphAgenticRetrievalOrchestrator,
)
from app.rag.llm_orchestrator import (
    LLMToolCallingRetrievalOrchestrator,
    LLMToolOrchestratorExecutionResult,
)
from app.rag.pricing import estimate_cost_usd
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
from app.rag.retrieval_cache import (
    CachedGraphPathRef,
    CachedRetrievalPayload,
    RetrievalCacheContext,
    RetrievalCacheService,
    payload_from_run_items,
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
from app.repositories.graph_retrieval_repository import GraphRetrievalRepository
from app.repositories.retrieval_repository import (
    CheckedRetrievalCandidate,
    CitationInput,
    CitationRecord,
    RetrievalRepository,
    RetrievalRunItemInput,
)
from app.schemas.graph import GraphRetrievalPathCreate
from app.schemas.rag import (
    RagAskAssistantMessage,
    RagAskCitation,
    RagAskConfidence,
    RagAskGeneration,
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
    build_rag_ask_retrieval_summary,
)
from app.services.chat_service import ChatService
from app.services.url_fetch_service import redact_url_for_display

SCORE_QUANT = Decimal("0.000001")
SENSITIVE_OUTPUT_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*\S{8,}"
    r"|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
)
GENERATION_LABEL_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|credential|bearer|sk-[A-Za-z0-9_-]{8,})"
)
CITATION_MARKER_RE = re.compile(r"\[(\d{1,6})\]")
MODEL_KEY_SEPARATOR = ":"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _GenerationAttempt:
    generation: GenerationResult
    allow_validation_error_fallback: bool = False


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


@dataclass(frozen=True)
class GenerationSelection:
    provider: str
    model_name: str


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
        langgraph_agentic_orchestrator: LangGraphAgenticRetrievalOrchestrator | None = None,
        context_budget_manager: ContextBudgetManager | None = None,
        evidence_pack_builder: EvidencePackBuilder | None = None,
        trace_export_service: TraceExportService | None = None,
        retrieval_cache_service: RetrievalCacheService | None = None,
        graph_retrieval_repository: GraphRetrievalRepository | None = None,
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
        agentic_strategy_planner = create_agentic_strategy_planner(settings)
        self.strategy_router = strategy_router or StrategyRouter(settings, agentic_strategy_planner)
        self.agentic_executor = agentic_executor or AgenticRetrievalExecutor(
            settings,
            ContextSufficiencyChecker(settings),
            agentic_strategy_planner,
        )
        self.llm_tool_orchestrator = llm_tool_orchestrator or LLMToolCallingRetrievalOrchestrator(
            settings
        )
        self.langchain_agentic_orchestrator = (
            langchain_agentic_orchestrator
            or LangChainAgenticRetrievalOrchestrator(
                settings,
                planner=agentic_strategy_planner,
            )
        )
        self.langgraph_agentic_orchestrator = (
            langgraph_agentic_orchestrator
            or LangGraphAgenticRetrievalOrchestrator(
                settings,
                planner=agentic_strategy_planner,
            )
        )
        self.context_budget_manager = context_budget_manager or ContextBudgetManager()
        self.evidence_pack_builder = evidence_pack_builder or EvidencePackBuilder()
        self.trace_export_service = trace_export_service or TraceExportService(settings)
        self.retrieval_cache_service = retrieval_cache_service or RetrievalCacheService()
        self.graph_retrieval_repository = graph_retrieval_repository or GraphRetrievalRepository()
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

            def retrieve_uncached() -> RetrievalPipelineResult:
                retrieval_execution_strategy = _retrieval_execution_strategy(execution_strategy)
                if _should_use_agentic_loop(requested_strategy, router_decision):
                    return self._retrieve_agentic(
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
                if retrieval_execution_strategy == RetrievalStrategy.SPARSE:
                    return self._retrieve_sparse(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                if retrieval_execution_strategy == RetrievalStrategy.HYBRID:
                    return self._retrieve_hybrid(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                return self._retrieve_and_rerank(
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

            result = self._execute_retrieval_with_cache(
                db,
                query_hash=query_hash,
                requested_strategy=requested_strategy,
                execution_strategy=execution_strategy,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                request_kind="search",
                bypass=payload.cache_bypass,
                latency_tracker=latency_tracker,
                retrieve=retrieve_uncached,
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
        generation_selection = self._generation_selection_for_request(payload)
        answer_generator = self._answer_generator_for_selection(generation_selection)

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
            RetrievalStrategy.LANGGRAPH_AGENTIC,
        }:
            raise RagAskPipelineError("strategy_not_enabled", 409)
        if requested_strategy == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
            if not self.settings.llm_orchestrator_enabled:
                raise RagAskPipelineError("strategy_not_enabled", 409)
        elif requested_strategy == RetrievalStrategy.LANGCHAIN_AGENTIC:
            if not self.settings.langchain_agentic_enabled:
                raise RagAskPipelineError("strategy_not_enabled", 409)
        elif requested_strategy == RetrievalStrategy.LANGGRAPH_AGENTIC:
            if not self.settings.langgraph_agentic_enabled:
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
            generation_provider=generation_selection.provider,
            generation_model=generation_selection.model_name,
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
        elif requested_strategy == RetrievalStrategy.LANGGRAPH_AGENTIC:
            query_plan = build_langgraph_agentic_query_plan(
                query_hash=query_hash,
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = build_langgraph_agentic_strategy_decision()
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

            def retrieve_uncached() -> RetrievalPipelineResult:
                retrieval_execution_strategy = _retrieval_execution_strategy(execution_strategy)
                if requested_strategy == RetrievalStrategy.LLM_TOOL_ORCHESTRATOR:
                    return self._retrieve_llm_tool_orchestrator(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                if requested_strategy == RetrievalStrategy.LANGCHAIN_AGENTIC:
                    return self._retrieve_langchain_agentic(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                if requested_strategy == RetrievalStrategy.LANGGRAPH_AGENTIC:
                    return self._retrieve_langgraph_agentic(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                if _should_use_agentic_loop(requested_strategy, router_decision):
                    return self._retrieve_agentic(
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
                if retrieval_execution_strategy == RetrievalStrategy.SPARSE:
                    return self._retrieve_sparse(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                if retrieval_execution_strategy == RetrievalStrategy.HYBRID:
                    return self._retrieve_hybrid(
                        db,
                        query=retrieval_query,
                        top_k=top_k,
                        rerank_top_n=rerank_top_n,
                        filters=filters,
                        retrieval_run_id=run_id,
                        latency_tracker=latency_tracker,
                    )
                return self._retrieve_and_rerank(
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

            result = self._execute_retrieval_with_cache(
                db,
                query_hash=query_hash,
                requested_strategy=requested_strategy,
                execution_strategy=execution_strategy,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                request_kind="ask",
                bypass=payload.cache_bypass,
                latency_tracker=latency_tracker,
                retrieve=retrieve_uncached,
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
            generation_started = time.perf_counter()
            with latency_tracker.span("generation_ms"):
                generation_attempt = _generate_with_insufficient_evidence_retry(
                    answer_generator,
                    GenerationRequest(
                        message=payload.message,
                        context_items=context_items,
                        max_output_chars=self.settings.generation_max_output_chars,
                    ),
                    retrieval_score_summary=final_summary,
                    settings=self.settings,
                    latency_tracker=latency_tracker,
                )
                generation = generation_attempt.generation
            generation_metadata = self._generation_metadata(
                selection=generation_selection,
                generation=generation,
                latency_ms=_elapsed_ms(generation_started),
            )
            with latency_tracker.span("citation_build_ms"):
                (
                    parsed_generation,
                    cited_sources,
                    insufficient_evidence_fallback,
                ) = _validated_generation_or_fallback(
                    generation.content,
                    context_items=context_items,
                    prompt_citation_sources=prompt_citation_sources,
                    allow_insufficient_evidence_fallback=True,
                    allow_validation_error_fallback=(
                        generation_attempt.allow_validation_error_fallback
                    ),
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
                if insufficient_evidence_fallback:
                    confidence = _low_confidence_for_insufficient_evidence(
                        confidence,
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
                generation=generation_metadata,
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
                query=query,
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

    def _retrieve_langgraph_agentic(
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
        langgraph_result = self.langgraph_agentic_orchestrator.execute(
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
            agentic_result=langgraph_result.retrieval_result,
            latency_tracker=latency_tracker,
            trace_strategy=RetrievalStrategy.LANGGRAPH_AGENTIC,
        )
        self._update_langgraph_agentic_trace(
            db,
            retrieval_run_id=retrieval_run_id,
            langgraph_result=langgraph_result,
            tool_result_compression_json=_tool_result_compression_json_with_run_items(
                langgraph_result.tool_result_compression_trace,
                pipeline_result.context_candidates,
            ),
        )
        summary_payload = pipeline_result.summary.model_dump(mode="json")
        summary_payload.update(langgraph_result.summary_fields())
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
        # Preserve trace suppression per run: router paths with
        # router_store_decision_trace=False persist None. Do not resurrect those
        # traces, but do update explicit dense/hybrid/LLM traces that already
        # exist even when the router trace flag is disabled.
        if run.strategy_decision_json is None:
            return
        decision = dict(run.strategy_decision_json)
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

    def _update_langgraph_agentic_trace(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        langgraph_result: LangGraphAgenticExecutionResult,
        tool_result_compression_json: dict[str, object] | None,
    ) -> None:
        run = self._require_run(db, retrieval_run_id)
        decision = dict(run.strategy_decision_json or {})
        langgraph_fields = langgraph_result.decision_trace_fields()
        existing_reason_codes = decision.get("reason_codes")
        if isinstance(existing_reason_codes, list):
            reason_codes = [str(code) for code in existing_reason_codes]
        else:
            reason_codes = []
        for code in langgraph_result.reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
        decision.update(langgraph_fields)
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

    def _execute_retrieval_with_cache(
        self,
        db: Session,
        *,
        query_hash: str,
        requested_strategy: RetrievalStrategy,
        execution_strategy: RetrievalStrategy | None,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        request_kind: Literal["search", "ask"],
        bypass: bool,
        latency_tracker: LatencyTracker,
        retrieve: Any,
        cache_settings: Settings | None = None,
    ) -> RetrievalPipelineResult:
        effective_settings = cache_settings or self.settings
        cache_context = RetrievalCacheContext(
            query_hash=query_hash,
            strategy_type=requested_strategy,
            execution_strategy=execution_strategy,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            request_kind=request_kind,
        )
        execution = self.retrieval_cache_service.execute(
            db,
            settings=effective_settings,
            context=cache_context,
            bypass=bypass,
            cacheable=_is_cacheable_retrieval_strategy(requested_strategy),
            latency_tracker=latency_tracker,
            hydrate=lambda payload: self._hydrate_cached_retrieval_result(
                db,
                payload=payload,
                retrieval_run_id=retrieval_run_id,
                filters=filters,
            ),
            retrieve=retrieve,
            payload_from_result=lambda result: self._cache_payload_from_result(
                db,
                result=cast(RetrievalPipelineResult, result),
                query_hash=query_hash,
                strategy_type=requested_strategy.value,
                retrieval_run_id=retrieval_run_id,
            ),
        )
        self._update_cache_summary_safely(
            db,
            retrieval_run_id=retrieval_run_id,
            cache_summary=execution.summary,
        )
        return cast(RetrievalPipelineResult, execution.result)

    def _hydrate_cached_retrieval_result(
        self,
        db: Session,
        *,
        payload: CachedRetrievalPayload,
        retrieval_run_id: int,
        filters: RetrievalFilters,
    ) -> RetrievalPipelineResult | None:
        summary = RetrievalScoreSummary(**payload.retrieval_score_summary)
        if not payload.items:
            return RetrievalPipelineResult(
                summary=summary,
                items=[],
                selected_candidates=[],
                citation_sources=[],
                context_candidates=[],
                no_context=payload.no_context,
            )
        ordered_ids = [item.document_chunk_id for item in payload.items]
        rows = db.execute(
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
                DocumentChunk.document_chunk_id.in_(ordered_ids),
                DocumentChunk.modality == filters.modality,
                DocumentVersion.status == "ready",
                DocumentVersion.is_active.is_(True),
                LogicalDocument.status == "active",
            )
        ).all()
        rows_by_chunk_id = {
            chunk.document_chunk_id: (chunk, version, document) for chunk, version, document in rows
        }
        if len(rows_by_chunk_id) != len(set(ordered_ids)):
            return None
        if filters.logical_document_ids:
            allowed_ids = set(filters.logical_document_ids)
            if any(
                document.logical_document_id not in allowed_ids
                for _, _, document in rows_by_chunk_id.values()
            ):
                return None

        candidates: list[CheckedRetrievalCandidate] = []
        for item in payload.items:
            row = rows_by_chunk_id.get(item.document_chunk_id)
            if row is None:
                return None
            chunk, version, document = row
            candidates.append(
                CheckedRetrievalCandidate(
                    chunk=chunk,
                    document_version=version,
                    logical_document=document,
                    retrieval_score=item.retrieval_score,
                    rank_order=item.rank_order,
                    payload={},
                )
            )
        saved_items = self.repository.save_items(
            db,
            retrieval_run_id=retrieval_run_id,
            items=[
                RetrievalRunItemInput(
                    document_chunk_id=item.document_chunk_id,
                    retrieval_score=_decimal_score(item.retrieval_score),
                    rerank_score=(
                        _decimal_score(item.rerank_score) if item.rerank_score is not None else None
                    ),
                    rank_order=item.rank_order,
                    rerank_order=item.rerank_order,
                    selected_flag=item.selected_flag,
                    payload_snapshot=_payload_snapshot(candidate),
                    retrieval_source=item.retrieval_source,
                    score_breakdown_json=(
                        TraceRedactor.safe_dict(item.score_breakdown_json)
                        if item.score_breakdown_json is not None
                        else None
                    ),
                )
                for item, candidate in zip(payload.items, candidates, strict=True)
            ],
        )
        self._save_cached_graph_paths_safely(
            db,
            retrieval_run_id=retrieval_run_id,
            graph_paths=payload.graph_paths,
        )
        selected_pairs = [
            (candidate, saved_item, item)
            for candidate, saved_item, item in zip(
                candidates,
                saved_items,
                payload.items,
                strict=True,
            )
            if item.selected_flag
        ]
        return RetrievalPipelineResult(
            summary=summary,
            items=[
                _response_item(
                    candidate,
                    saved_item_id=saved_item.retrieval_run_item_id,
                    rerank_score=item.rerank_score,
                    rerank_order=item.rerank_order,
                    selected_flag=item.selected_flag,
                    snippet_max_chars=self.settings.search_snippet_max_chars,
                )
                for candidate, saved_item, item in zip(
                    candidates,
                    saved_items,
                    payload.items,
                    strict=True,
                )
            ],
            selected_candidates=[candidate for candidate, _, _ in selected_pairs],
            citation_sources=[
                _citation_source(
                    candidate,
                    saved_item=saved_item,
                    local_citation_id=local_id,
                    snippet_max_chars=self.settings.citation_preview_max_chars,
                )
                for local_id, (candidate, saved_item, _) in enumerate(
                    selected_pairs,
                    start=1,
                )
            ],
            context_candidates=[
                ContextCandidateRef(
                    candidate=candidate,
                    saved_item=saved_item,
                    rank=index,
                    rerank_score=item.rerank_score,
                    rerank_order=item.rerank_order,
                    citation_candidate=item.selected_flag,
                )
                for index, (candidate, saved_item, item) in enumerate(
                    zip(candidates, saved_items, payload.items, strict=True),
                    start=1,
                )
            ],
            no_context=payload.no_context,
        )

    def _cache_payload_from_result(
        self,
        db: Session,
        *,
        result: RetrievalPipelineResult,
        query_hash: str,
        strategy_type: str,
        retrieval_run_id: int,
    ) -> CachedRetrievalPayload | None:
        retrieval_score_summary = result.summary.model_dump(mode="json")
        if _has_transient_graph_reason(retrieval_score_summary):
            return None
        ordered_items = [ref.saved_item for ref in result.context_candidates]
        if not ordered_items and result.items:
            ordered_items = self.repository.list_items_for_run(
                db,
                retrieval_run_id=retrieval_run_id,
            )
        return payload_from_run_items(
            query_hash=query_hash,
            strategy_type=strategy_type,
            retrieval_score_summary=retrieval_score_summary,
            items=ordered_items,
            graph_paths=self.graph_retrieval_repository.list_graph_retrieval_paths(
                db,
                retrieval_run_id=retrieval_run_id,
            ),
            no_context=result.no_context,
        )

    def _save_cached_graph_paths_safely(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        graph_paths: tuple[CachedGraphPathRef, ...],
    ) -> None:
        if not graph_paths:
            return
        try:
            self.graph_retrieval_repository.save_graph_retrieval_paths(
                db,
                retrieval_run_id=retrieval_run_id,
                paths=[
                    GraphRetrievalPathCreate(
                        retrieval_run_id=retrieval_run_id,
                        path_json=path.path_json,
                        score_breakdown_json=path.score_breakdown_json,
                        source_chunk_ids_json=path.source_chunk_ids_json,
                    )
                    for path in graph_paths
                ],
            )
        except Exception:
            return

    def _update_cache_summary_safely(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        cache_summary: dict[str, object],
    ) -> None:
        run = self.repository.get_run(db, retrieval_run_id=retrieval_run_id)
        if run is None:
            return
        self.repository.update_retrieval_run_trace(
            db,
            run=run,
            cache_summary_json=TraceRedactor.safe_dict(cache_summary),
        )

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

    def _generation_selection_for_request(self, payload: RagAskRequest) -> GenerationSelection:
        if payload.model_key is None:
            provider = self.settings.generation_provider.lower()
            return GenerationSelection(
                provider=provider,
                model_name=_resolved_generation_model_name(
                    provider,
                    self.settings.generation_model_name,
                ),
            )
        provider, separator, model_name = payload.model_key.partition(MODEL_KEY_SEPARATOR)
        provider = provider.lower()
        model_name = model_name.strip()
        if provider == "google":
            provider = "gemini"
        if (
            provider not in {"lmstudio", "openai", "anthropic", "gemini", "nvidia", "bedrock"}
            or not separator
            or not model_name
        ):
            raise RagAskPipelineError("unsupported_model", 422)
        if provider == "lmstudio" and self.settings.generation_provider == "fake":
            return GenerationSelection(
                provider=self.settings.generation_provider.lower(),
                model_name=_resolved_generation_model_name(
                    self.settings.generation_provider,
                    self.settings.generation_model_name,
                ),
            )
        return GenerationSelection(
            provider=provider,
            model_name=_resolved_generation_model_name(provider, model_name),
        )

    def _answer_generator_for_selection(self, selection: GenerationSelection) -> AnswerGenerator:
        default_selection = GenerationSelection(
            provider=self.settings.generation_provider.lower(),
            model_name=_resolved_generation_model_name(
                self.settings.generation_provider,
                self.settings.generation_model_name,
            ),
        )
        if selection == default_selection:
            return self.answer_generator
        try:
            return create_answer_generator(
                self.settings,
                provider=selection.provider,
                model_name=selection.model_name,
            )
        except AnswerGenerationError as exc:
            raise RagAskPipelineError("unsupported_model", 422) from exc

    def _answer_generator_for_request(self, payload: RagAskRequest) -> AnswerGenerator:
        return self._answer_generator_for_selection(self._generation_selection_for_request(payload))

    def _generation_metadata(
        self,
        *,
        selection: GenerationSelection,
        generation: GenerationResult,
        latency_ms: int,
    ) -> RagAskGeneration:
        usage = generation.usage
        return RagAskGeneration(
            provider=_safe_generation_label(selection.provider, max_length=100),
            model=_safe_generation_label(selection.model_name, max_length=128),
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            total_tokens=usage.total_tokens if usage is not None else None,
            estimated_cost_usd=estimate_cost_usd(
                selection.provider,
                selection.model_name,
                usage,
                pricing_overrides=cast(
                    "dict[str, Any]",
                    self.settings.generation_pricing_overrides,
                ),
            ),
            latency_ms=latency_ms,
        )


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


def _resolved_generation_model_name(provider: str, model_name: str) -> str:
    normalized_provider = provider.lower()
    if normalized_provider == "lmstudio":
        return _lmstudio_model_name(model_name)
    return model_name.strip()


def _elapsed_ms(started_at: float) -> int:
    elapsed = int(round((time.perf_counter() - started_at) * 1000))
    return max(0, elapsed)


def _safe_generation_label(value: str, *, max_length: int) -> str:
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    if not safe:
        return "unknown"
    if GENERATION_LABEL_SECRET_RE.search(safe):
        return "redacted"
    return safe


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


def _is_cacheable_retrieval_strategy(strategy: RetrievalStrategy) -> bool:
    return strategy in {
        RetrievalStrategy.DENSE,
        RetrievalStrategy.SPARSE,
        RetrievalStrategy.HYBRID,
        RetrievalStrategy.GRAPH,
    }


def _has_transient_graph_reason(summary: dict[str, object]) -> bool:
    reason_codes: list[object] = []
    for field_name in ("graph_reason_codes", "graph_fallback_reason_codes"):
        field_value = summary.get(field_name)
        if isinstance(field_value, list):
            reason_codes.extend(field_value)
    for field_name in ("fallback_reason", "graph_fallback_reason"):
        field_value = summary.get(field_name)
        if isinstance(field_value, str):
            reason_codes.append(field_value)
    if not reason_codes:
        return False
    non_cacheable_codes = {
        "graph_store_provider_unavailable",
        "neo4j_connection_failed",
        "neo4j_driver_unavailable",
        "neo4j_not_configured",
        "neo4j_projection_empty",
        "neo4j_projection_incomplete",
        "neo4j_query_failed",
        "neo4j_to_postgres_fallback",
        "neo4j_unavailable",
    }
    if any(str(code) in non_cacheable_codes for code in reason_codes):
        return True
    transient_markers = ("timeout", "error", "failed", "failure")
    return any(
        any(marker in str(code).lower() for marker in transient_markers) for code in reason_codes
    )


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
        cache_summary_json=_safe_json_object(run.cache_summary_json),
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
    generation_provider: str | None = None,
    generation_model: str | None = None,
) -> dict[str, object]:
    snapshot = build_retrieval_settings_snapshot(
        settings=settings,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        filters=filters,
        strategy_type=strategy_type,
    )
    if generation_provider is not None:
        snapshot["generation_provider"] = _safe_generation_label(
            generation_provider,
            max_length=100,
        )
    if generation_model is not None:
        snapshot["generation_model"] = _safe_generation_label(generation_model, max_length=128)

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
    if strategy_type == RetrievalStrategy.LANGGRAPH_AGENTIC:
        snapshot.update(
            TraceRedactor.safe_dict(
                {
                    "orchestrator_provider": "langgraph",
                    "langgraph_agentic_enabled": settings.langgraph_agentic_enabled,
                    "max_tool_calls": settings.langgraph_agentic_max_tool_calls,
                    "max_search_calls": settings.langgraph_agentic_max_search_calls,
                    "timeout_seconds": settings.langgraph_agentic_timeout_seconds,
                    "max_query_chars": settings.langgraph_agentic_max_query_chars,
                    "max_tool_result_items": settings.langgraph_agentic_max_tool_result_items,
                    "max_snippet_chars": settings.langgraph_agentic_max_snippet_chars,
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


def build_langgraph_agentic_query_plan(
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
            "strategy_type": RetrievalStrategy.LANGGRAPH_AGENTIC.value,
            "query_mode": "langgraph_agentic_retrieval",
            "reason_codes": [
                "phase2_5_langgraph_agentic",
                "langgraph_state_graph",
                "langgraph_plan_execute_nodes",
                "langchain_structured_tools",
                "retrieval_only_tools",
                "bounded_loop",
            ],
            "candidate_strategies": [
                RetrievalStrategy.DENSE.value,
                RetrievalStrategy.SPARSE.value,
                RetrievalStrategy.HYBRID.value,
            ],
            "recommended_strategy": RetrievalStrategy.LANGGRAPH_AGENTIC.value,
        }
    )
    return TraceRedactor.safe_dict(base)


def build_langgraph_agentic_strategy_decision() -> dict[str, object]:
    return TraceRedactor.safe_dict(
        {
            "schema_version": "phase2.trace.v1",
            "selected_strategy": RetrievalStrategy.LANGGRAPH_AGENTIC.value,
            "execution_strategy": RetrievalStrategy.LANGGRAPH_AGENTIC.value,
            "fallback_used": False,
            "router_enabled": False,
            "decision_source": "langgraph_agentic",
            "decision_policy": "langgraph_bounded_retrieval_only_graph",
            "orchestrator_provider": "langgraph",
            "reason_codes": [
                "explicit_strategy_langgraph_agentic",
                "langgraph_state_graph",
                "langgraph_plan_execute_nodes",
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
    existing_planner_events = decision.get("planner_events")
    if not isinstance(existing_planner_events, list):
        existing_planner_events = []
    agentic_planner_events = agentic_fields.get("planner_events")
    if not isinstance(agentic_planner_events, list):
        agentic_planner_events = []
    router_fallback_used = bool(decision.get("fallback_used"))
    if agentic_result.fallback_used:
        decision.update(agentic_fields)
    else:
        for key, value in agentic_fields.items():
            if key in {"fallback_used", "fallback_reason", "fallback_strategy"}:
                continue
            decision[key] = value
        decision["fallback_used"] = router_fallback_used
    if agentic_planner_events:
        decision["planner_events"] = [*existing_planner_events, *agentic_planner_events]
    planner_events = decision.get("planner_events")
    if isinstance(planner_events, list) and planner_events:
        decision["llm_planner_used"] = any(
            isinstance(event, dict) and bool(event.get("llm_planner_used"))
            for event in planner_events
        )
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


def _generate_with_insufficient_evidence_retry(
    answer_generator: AnswerGenerator,
    request: GenerationRequest,
    *,
    retrieval_score_summary: RetrievalScoreSummary,
    settings: Settings,
    latency_tracker: LatencyTracker,
) -> _GenerationAttempt:
    generation = answer_generator.generate(request)
    if not _should_retry_insufficient_evidence_generation(
        generation.content,
        retrieval_score_summary=retrieval_score_summary,
        settings=settings,
    ):
        return _GenerationAttempt(generation)

    retry_request = _supported_answer_retry_request(request)
    retry_started = time.perf_counter()
    latency_tracker.record_count("generation_retry_count")
    try:
        retry_generation = answer_generator.generate(retry_request)
    except AnswerGenerationError:
        return _GenerationAttempt(generation)
    finally:
        latency_tracker.record_ms("generation_retry_ms", _elapsed_ms(retry_started))

    return _GenerationAttempt(
        generation=GenerationResult(
            content=retry_generation.content,
            usage=_combined_generation_usage(generation.usage, retry_generation.usage),
        ),
        allow_validation_error_fallback=True,
    )


def _should_retry_insufficient_evidence_generation(
    content: str,
    *,
    retrieval_score_summary: RetrievalScoreSummary,
    settings: Settings,
) -> bool:
    if not settings.generation_retry_on_insufficient_evidence:
        return False
    parsed_generation = parse_generation_output(content)
    return _is_insufficient_evidence_answer(
        parsed_generation.answer_text
    ) and has_high_retrieval_support(retrieval_score_summary)


def _supported_answer_retry_request(request: GenerationRequest) -> GenerationRequest:
    return GenerationRequest(
        message=request.message,
        context_items=request.context_items,
        max_output_chars=request.max_output_chars,
        system_instructions=RAG_GENERATION_SUPPORTED_ANSWER_RETRY_INSTRUCTIONS,
        temperature=0.0,
        response_format=request.response_format,
    )


def _combined_generation_usage(
    initial_usage: TokenUsage | None,
    retry_usage: TokenUsage | None,
) -> TokenUsage | None:
    if initial_usage is None and retry_usage is None:
        return None
    return TokenUsage(
        input_tokens=_combined_optional_int(
            initial_usage.input_tokens if initial_usage is not None else None,
            retry_usage.input_tokens if retry_usage is not None else None,
        ),
        output_tokens=_combined_optional_int(
            initial_usage.output_tokens if initial_usage is not None else None,
            retry_usage.output_tokens if retry_usage is not None else None,
        ),
        total_tokens=_combined_optional_int(
            initial_usage.total_tokens if initial_usage is not None else None,
            retry_usage.total_tokens if retry_usage is not None else None,
        ),
    )


def _combined_optional_int(first: int | None, second: int | None) -> int | None:
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)


def _validated_generation_or_fallback(
    content: str,
    *,
    context_items: list[GenerationContextItem],
    prompt_citation_sources: list[CitationSource],
    allow_insufficient_evidence_fallback: bool = False,
    allow_validation_error_fallback: bool = False,
) -> tuple[ParsedGenerationOutput, list[CitationSource], bool]:
    try:
        parsed_generation = parse_generation_output(content)
    except CitationBuildError:
        if allow_validation_error_fallback:
            return _validation_error_fallback(prompt_citation_sources)
        raise
    if _is_insufficient_evidence_answer(parsed_generation.answer_text):
        if allow_insufficient_evidence_fallback:
            fallback_generation, fallback_sources = _insufficient_citation_fallback(
                prompt_citation_sources
            )
            return fallback_generation, fallback_sources, True
        raise InsufficientEvidenceAnswerError()
    try:
        _validate_generation_output_safety(
            parsed_generation.answer_text,
            context_items=context_items,
        )
    except CitationBuildError:
        if allow_validation_error_fallback:
            return _validation_error_fallback(prompt_citation_sources)
        raise
    try:
        cited_sources = validate_generation_citations(
            parsed_generation,
            source_map=prompt_citation_sources,
        )
    except CitationBuildError:
        if allow_validation_error_fallback:
            return _validation_error_fallback(prompt_citation_sources)
        repaired_generation, repaired_sources = _repair_generation_citations(
            parsed_generation,
            prompt_citation_sources=prompt_citation_sources,
        )
        return repaired_generation, repaired_sources, False
    return parsed_generation, cited_sources, False


def _validation_error_fallback(
    prompt_citation_sources: list[CitationSource],
) -> tuple[ParsedGenerationOutput, list[CitationSource], bool]:
    fallback_generation, fallback_sources = _insufficient_citation_fallback(prompt_citation_sources)
    return fallback_generation, fallback_sources, True


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


def _low_confidence_for_insufficient_evidence(
    confidence: ConfidenceScores,
    settings: Settings,
) -> ConfidenceScores:
    low_ceiling = max(0.0, min(1.0, settings.confidence_medium_threshold) - 0.01)
    return ConfidenceScores(
        answer_confidence=round(min(confidence.answer_confidence, low_ceiling), 6),
        groundedness_score=confidence.groundedness_score,
        confidence_label="Low",
    )


def _ask_response(
    *,
    user_message: ChatMessage,
    assistant_message: ChatMessage,
    citation_records: list[CitationRecord],
    run: RetrievalRun,
    retrieval_run_id: int,
    replayed: bool,
    generation: RagAskGeneration | None = None,
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
        generation=generation,
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
    return build_rag_ask_retrieval_summary(
        retrieval_run_id=run.retrieval_run_id,
        strategy_type=run.strategy_type,
        strategy_decision=_safe_json_object(run.strategy_decision_json),
        retrieval_score_summary=_safe_json_object(run.retrieval_score_summary),
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
