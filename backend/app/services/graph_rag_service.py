from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError
from app.db.models import User
from app.ingest.embedding import EmbeddingAdapterError
from app.rag.citations import CitationBuildError
from app.rag.confidence import ConfidenceInputs, calculate_confidence
from app.rag.context_budget import estimate_tokens
from app.rag.generation import AnswerGenerationError, GenerationRequest
from app.rag.graph_retrieval import (
    GraphRetrievalResult,
    GraphRetrievalSettings,
    GraphRetrievalStrategy,
    GraphSourceCandidate,
    GraphStoreProvider,
    graph_query_signal_score,
)
from app.rag.rerank import RerankError
from app.rag.retrieval import RetrievalError, RetrievalFilters
from app.rag.strategy import RetrievalSource, RetrievalStrategy
from app.rag.trace import (
    LatencyTracker,
    TraceRedactor,
    build_router_query_plan,
    build_router_strategy_decision,
)
from app.repositories.graph_retrieval_repository import GraphRetrievalRepository
from app.repositories.retrieval_repository import CheckedRetrievalCandidate, RetrievalRunItemInput
from app.schemas.rag import (
    RagAskRequest,
    RagAskResponse,
    RagSearchRequest,
    RagSearchResponse,
    RetrievalScoreSummary,
)
from app.services.rag_service import (
    ContextCandidateRef,
    InsufficientEvidenceAnswerError,
    RagAskPipelineError,
    RagSearchPipelineError,
    RagService,
    RetrievalPipelineResult,
    _ask_response,
    _citation_input,
    _citation_source,
    _context_citation_sources,
    _context_refs_for_citation_sources,
    _decimal_score,
    _elapsed_ms,
    _low_confidence_for_insufficient_evidence,
    _optional_decimal_score,
    _payload_snapshot,
    _prompt_citation_sources,
    _query_hash,
    _response_item,
    _retrieval_filters,
    _retrieval_settings_snapshot,
    _score_summary,
    _selected_context_refs,
    _summary_with_final_context_refs,
    _validated_generation_or_fallback,
)

GRAPH_NO_EVIDENCE_FALLBACK_REASON_CODE = "graph_no_evidence_fallback"
GRAPH_FALLBACK_HYBRID_REASON_CODE = "graph_fallback_hybrid"
GRAPH_FALLBACK_DENSE_REASON_CODE = "graph_fallback_dense"
GRAPH_FALLBACK_HYBRID_DISABLED_REASON_CODE = "graph_fallback_hybrid_disabled"
GRAPH_POSTGRES_REQUEST_VALUE = "graph_postgres"
GRAPH_NEO4J_REQUEST_VALUE = "graph_neo4j"


@dataclass(frozen=True)
class _GraphRequest:
    canonical_strategy: RetrievalStrategy
    request_label: str
    provider_override: GraphStoreProvider | None = None
    force_enabled: bool = False

    @property
    def is_explicit_graph(self) -> bool:
        return self.canonical_strategy == RetrievalStrategy.GRAPH


@dataclass(frozen=True)
class _GraphFallbackSelection:
    strategy: Literal["dense", "hybrid"]
    reason_codes: tuple[str, ...] = ()


class GraphRagService:
    def __init__(
        self,
        base_service: RagService,
        *,
        graph_strategy: GraphRetrievalStrategy | None = None,
        graph_repository: GraphRetrievalRepository | None = None,
    ) -> None:
        self.base = base_service
        self.graph_strategy = graph_strategy or GraphRetrievalStrategy()
        self.graph_repository = graph_repository or GraphRetrievalRepository()

    def search(
        self,
        db: Session,
        *,
        payload: RagSearchRequest,
        request_id: str | None,
    ) -> RagSearchResponse:
        top_k = self.base._effective_top_k(payload.top_k)
        rerank_top_n = self.base._effective_rerank_top_n(payload.rerank_top_n)
        filters = _retrieval_filters(payload)
        graph_request = _graph_request_from_value(payload.strategy.value)
        requested_strategy = graph_request.canonical_strategy
        if requested_strategy == RetrievalStrategy.AGENTIC_ROUTER:
            routed = self._graph_router_selection(
                query=payload.query,
                filters=filters,
                requested_strategy=requested_strategy,
                request_kind="search",
            )
            if routed is None:
                return self.base.search(db, payload=payload, request_id=request_id)
            retrieval_query, query_plan, strategy_decision = routed
        elif graph_request.is_explicit_graph:
            self._ensure_graph_enabled_for_search()
            query_plan_build = self.base.query_plan_builder.build(
                payload.query,
                filters=filters,
                requested_strategy=requested_strategy,
            )
            retrieval_query = query_plan_build.retrieval_query
            query_plan = _build_graph_query_plan(
                query_hash=_query_hash(payload.query),
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = _build_graph_strategy_decision(
                selected_strategy=graph_request.request_label,
                graph_store_provider=graph_request.provider_override,
                graph_retrieval_effective_enabled=True,
                store_decision_trace=self.base.settings.router_store_decision_trace,
            )
        else:
            return self.base.search(db, payload=payload, request_id=request_id)

        allow_base_fallback = (
            requested_strategy == RetrievalStrategy.AGENTIC_ROUTER
            or graph_request.is_explicit_graph
        )
        query_hash = _query_hash(payload.query)
        latency_tracker = LatencyTracker()
        retrieval_settings = _graph_settings_snapshot(
            settings=self.base.settings,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            strategy_type=requested_strategy,
            graph_store_provider=graph_request.provider_override,
            force_graph_enabled=graph_request.force_enabled,
        )
        run = self.base.repository.create_standalone_run(
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
            result = self.base._execute_retrieval_with_cache(
                db,
                query_hash=query_hash,
                requested_strategy=requested_strategy,
                execution_strategy=RetrievalStrategy.GRAPH,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                request_kind="search",
                bypass=payload.cache_bypass,
                latency_tracker=latency_tracker,
                cache_settings=_cache_settings_for_graph_provider(
                    self.base.settings,
                    graph_request.provider_override,
                ),
                retrieve=lambda: self._retrieve_graph_or_base_fallback(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                    allow_base_fallback=allow_base_fallback,
                    selected_strategy=(
                        graph_request.request_label if graph_request.is_explicit_graph else None
                    ),
                    graph_store_provider=graph_request.provider_override,
                    force_graph_enabled=graph_request.force_enabled,
                ),
            )
            run = self.base._require_run(db, run_id)
            if graph_request.is_explicit_graph:
                self._sync_explicit_graph_base_fallback_run(
                    db,
                    retrieval_run_id=run_id,
                    result=result,
                )
                run = self.base._require_run(db, run_id)
            self.base.repository.mark_succeeded(
                db,
                run=run,
                retrieval_score_summary=result.summary.model_dump(mode="json"),
                rerank_score_top1=_optional_decimal_score(result.summary.top1_rerank_score),
                finished_at=datetime.now(UTC),
                latency_breakdown_json=latency_tracker.snapshot(),
            )
            db.commit()
            self.base._export_retrieval_trace_safely(db, retrieval_run_id=run_id)
            return RagSearchResponse(
                retrieval_run_id=run_id,
                status="succeeded",
                retrieval_score_summary=result.summary,
                items=result.items,
            )
        except (EmbeddingAdapterError, RetrievalError):
            # The base fallback retrieval paths (dense/hybrid) can raise
            # EmbeddingAdapterError when embedding the query fails; map it to the
            # same retrieval_failed (503) contract the base service produces so
            # graph fallback does not surface it as an unclassified 500.
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
            )
            raise RagSearchPipelineError("retrieval_failed", 503) from None
        except RerankError:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
            )
            raise RagSearchPipelineError("rerank_failed", 503) from None
        except Exception:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
                latency_tracker=latency_tracker,
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
        graph_request = _graph_request_from_value(payload.strategy.value)
        requested_strategy = graph_request.canonical_strategy
        if requested_strategy == RetrievalStrategy.AGENTIC_ROUTER:
            routed = self._graph_router_selection(
                query=payload.message,
                filters=_retrieval_filters(payload),
                requested_strategy=requested_strategy,
                request_kind="ask",
            )
            if routed is None:
                return self.base.ask(db, payload=payload, user=user, request_id=request_id)
            retrieval_query, query_plan, strategy_decision = routed
        elif not graph_request.is_explicit_graph:
            return self.base.ask(db, payload=payload, user=user, request_id=request_id)

        # Resolve the duplicate-message replay BEFORE gating new graph executions on
        # the feature flag, so a completed strategy=graph ask can still be replayed
        # by client_message_id after graph_retrieval_enabled has been turned off.
        session = self.base.chat_service.ensure_session_can_append_messages(
            db,
            user=user,
            chat_session_id=payload.chat_session_id,
        )
        existing = self.base.chat_repository.get_user_message_by_client_message_id(
            db,
            chat_session_id=payload.chat_session_id,
            client_message_id=payload.client_message_id,
            for_update=True,
        )
        if existing is not None:
            return self.base._classify_duplicate(db, payload=payload, existing=existing)

        if graph_request.is_explicit_graph:
            self._ensure_graph_enabled_for_ask()
            filters = _retrieval_filters(payload)
            query_plan_build = self.base.query_plan_builder.build(
                payload.message,
                filters=filters,
                requested_strategy=requested_strategy,
            )
            retrieval_query = query_plan_build.retrieval_query
            query_plan = _build_graph_query_plan(
                query_hash=_query_hash(payload.message),
                filters=filters,
                plan_metadata=query_plan_build.trace_metadata,
            )
            strategy_decision = _build_graph_strategy_decision(
                selected_strategy=graph_request.request_label,
                graph_store_provider=graph_request.provider_override,
                graph_retrieval_effective_enabled=True,
                store_decision_trace=self.base.settings.router_store_decision_trace,
            )
        allow_base_fallback = (
            requested_strategy == RetrievalStrategy.AGENTIC_ROUTER
            or graph_request.is_explicit_graph
        )
        generation_selection = self.base._generation_selection_for_request(payload)
        answer_generator = self.base._answer_generator_for_selection(generation_selection)

        top_k = self.base._effective_ask_top_k(payload.top_k)
        rerank_top_n = self.base._effective_ask_rerank_top_n(payload.rerank_top_n)
        filters = _retrieval_filters(payload)
        query_hash = _query_hash(payload.message)
        latency_tracker = LatencyTracker()
        retrieval_settings = _graph_settings_snapshot(
            settings=self.base.settings,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            strategy_type=requested_strategy,
            graph_store_provider=graph_request.provider_override,
            force_graph_enabled=graph_request.force_enabled,
        )
        now = datetime.now(UTC)
        try:
            user_message = self.base.chat_repository.create_message(
                db,
                chat_session_id=payload.chat_session_id,
                role="user",
                content=payload.message,
                client_message_id=payload.client_message_id,
            )
            run = self.base.repository.create_chat_run(
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
            self.base.chat_repository.touch_session(db, session=session, updated_at=now)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            existing_after_race = self.base.chat_repository.get_user_message_by_client_message_id(
                db,
                chat_session_id=payload.chat_session_id,
                client_message_id=payload.client_message_id,
            )
            if existing_after_race is not None:
                return self.base._classify_duplicate(
                    db,
                    payload=payload,
                    existing=existing_after_race,
                )
            raise ConflictError() from exc

        db.refresh(user_message)
        db.refresh(run)
        run_id = run.retrieval_run_id

        try:
            result = self.base._execute_retrieval_with_cache(
                db,
                query_hash=query_hash,
                requested_strategy=requested_strategy,
                execution_strategy=RetrievalStrategy.GRAPH,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                request_kind="ask",
                bypass=payload.cache_bypass,
                latency_tracker=latency_tracker,
                cache_settings=_cache_settings_for_graph_provider(
                    self.base.settings,
                    graph_request.provider_override,
                ),
                retrieve=lambda: self._retrieve_graph_or_base_fallback(
                    db,
                    query=retrieval_query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=run_id,
                    latency_tracker=latency_tracker,
                    allow_base_fallback=allow_base_fallback,
                    selected_strategy=(
                        graph_request.request_label if graph_request.is_explicit_graph else None
                    ),
                    graph_store_provider=graph_request.provider_override,
                    force_graph_enabled=graph_request.force_enabled,
                ),
            )
            if graph_request.is_explicit_graph:
                self._sync_explicit_graph_base_fallback_run(
                    db,
                    retrieval_run_id=run_id,
                    result=result,
                )
            context_budget_decision = self.base._apply_context_budget(
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
                snippet_max_chars=self.base.settings.citation_preview_max_chars,
            )
            with latency_tracker.span("evidence_pack_ms"):
                evidence_pack = self.base._build_evidence_pack(
                    db,
                    retrieval_run_id=run_id,
                    selected_context_refs=selected_context_refs,
                    selected_citation_sources=selected_citation_sources,
                    candidate_context_items=context_budget_decision.trace.items.candidate_count,
                )
            if result.no_context or not evidence_pack.items:
                self.base._mark_failed_safely(
                    db,
                    retrieval_run_id=run_id,
                    error_code="no_context_found",
                    latency_tracker=latency_tracker,
                    rollback=False,
                )
                raise RagAskPipelineError("no_context_found", 422)

            with latency_tracker.span("context_assembly_ms"):
                context_items = evidence_pack.to_generation_context_items()
                self.base._record_injection_patterns(
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
                context_budget_decision = self.base._finalize_context_budget_after_assembly(
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
                generation = answer_generator.generate(
                    GenerationRequest(
                        message=payload.message,
                        context_items=context_items,
                        max_output_chars=self.base.settings.generation_max_output_chars,
                    )
                )
            generation_metadata = self.base._generation_metadata(
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
                )
                assistant_message = self.base.chat_repository.create_message(
                    db,
                    chat_session_id=payload.chat_session_id,
                    role="assistant",
                    content=parsed_generation.answer_text,
                    linked_retrieval_run_id=run_id,
                )
                self.base.repository.save_citations(
                    db,
                    citations=[
                        _citation_input(source, retrieval_run_id=run_id) for source in cited_sources
                    ],
                )
                citation_records = self.base.repository.list_citations_for_run(
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
                    self.base.settings,
                )
                if insufficient_evidence_fallback:
                    confidence = _low_confidence_for_insufficient_evidence(
                        confidence,
                        self.base.settings,
                    )
            run = self.base._require_run(db, run_id)
            self.base.repository.mark_succeeded(
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
            self.base.chat_repository.touch_session(
                db,
                session=session,
                updated_at=datetime.now(UTC),
            )
            db.commit()
            self.base._export_retrieval_trace_safely(db, retrieval_run_id=run_id)
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
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="no_context_found",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            raise RagAskPipelineError("no_context_found", 422) from None
        except CitationBuildError:
            self.base._mark_failed_safely(
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
            # The base fallback retrieval paths (dense/hybrid) can raise
            # EmbeddingAdapterError when embedding the query fails; map it to the
            # same retrieval_failed (503) contract the base service produces so
            # graph fallback does not surface it as an unclassified 500.
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
            )
            raise RagAskPipelineError("retrieval_failed", 503) from None
        except RerankError:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="rerank_failed",
                latency_tracker=latency_tracker,
            )
            raise RagAskPipelineError("rerank_failed", 503) from None
        except AnswerGenerationError:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="generation_failed",
                latency_tracker=latency_tracker,
                rollback=False,
            )
            raise RagAskPipelineError("generation_failed", 503) from None
        except Exception:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="internal_error",
                latency_tracker=latency_tracker,
            )
            raise

    def _graph_router_selection(
        self,
        *,
        query: str,
        filters: RetrievalFilters,
        requested_strategy: RetrievalStrategy,
        request_kind: Literal["search", "ask"],
    ) -> tuple[str, dict[str, object], dict[str, object] | None] | None:
        if not self.base.settings.graph_retrieval_enabled:
            return None
        if not self.base.settings.graph_router_enabled:
            return None
        if not self._router_gates_allow_agentic(request_kind):
            # The agentic router is gated off (router_enabled / agentic_search /
            # agentic_ask). Mirror StrategyRouter and do not take the graph
            # shortcut; delegate to the base service unchanged.
            return None
        query_plan_build = self.base.query_plan_builder.build(
            query,
            filters=filters,
            requested_strategy=requested_strategy,
        )
        graph_signal_score = graph_query_signal_score(query)
        if graph_signal_score >= self.base.settings.graph_router_min_signal_score:
            plan_metadata = dict(query_plan_build.trace_metadata)
            plan_metadata["graph_query_signal_score"] = graph_signal_score
            query_plan = build_router_query_plan(
                query_hash=_query_hash(query),
                filters=filters,
                execution_strategy=RetrievalStrategy.GRAPH,
                plan_metadata=plan_metadata,
            )
            strategy_decision = _build_graph_strategy_decision(
                decision_source="graph_signal_router",
                router_enabled=True,
                confidence=min(0.92, 0.55 + graph_signal_score / 2),
                reason_codes=[
                    "graph_query_signal",
                    "graph_retrieval_enabled",
                    "graph_router_enabled",
                ],
                store_decision_trace=self.base.settings.router_store_decision_trace,
            )
            return query_plan_build.retrieval_query, query_plan, strategy_decision
        decision = self.base.strategy_router.route(
            query_plan=query_plan_build,
            requested_strategy=requested_strategy,
            request_kind=request_kind,
        )
        if decision.execution_strategy != RetrievalStrategy.GRAPH:
            return None
        query_plan = build_router_query_plan(
            query_hash=_query_hash(query),
            filters=filters,
            execution_strategy=RetrievalStrategy.GRAPH,
            plan_metadata=query_plan_build.trace_metadata,
        )
        router_strategy_decision = build_router_strategy_decision(decision=decision)
        return query_plan_build.retrieval_query, query_plan, router_strategy_decision

    def _router_gates_allow_agentic(self, request_kind: Literal["search", "ask"]) -> bool:
        settings = self.base.settings
        if not settings.router_enabled:
            return False
        if request_kind == "search" and not settings.router_allow_agentic_search:
            return False
        if request_kind == "ask" and not settings.router_allow_agentic_ask:
            return False
        return True

    def _retrieve_graph_or_base_fallback(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker,
        allow_base_fallback: bool,
        selected_strategy: str | None,
        graph_store_provider: GraphStoreProvider | None,
        force_graph_enabled: bool,
    ) -> RetrievalPipelineResult:
        result = self._retrieve_graph(
            db,
            query=query,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            retrieval_run_id=retrieval_run_id,
            latency_tracker=latency_tracker,
            graph_store_provider=graph_store_provider,
            force_graph_enabled=force_graph_enabled,
        )
        if result.no_context and allow_base_fallback:
            # The router forced graph retrieval but it produced no candidates.
            # Fall back to the base execution path (as if graph had not been
            # selected) instead of failing with no_context_found. Honor the
            # configured fallback strategy after applying the same enabled
            # checks as direct retrieval; disabled hybrid downgrades to dense.
            fallback_selection = self._graph_fallback_selection()
            fallback_strategy = fallback_selection.strategy
            graph_summary = result.summary.model_dump(mode="json")
            self._record_graph_fallback_reason(
                db,
                retrieval_run_id=retrieval_run_id,
                fallback_strategy=fallback_strategy,
                graph_summary=graph_summary,
                additional_reason_codes=fallback_selection.reason_codes,
            )
            if fallback_strategy == "hybrid":
                fallback_result = self.base._retrieve_hybrid(
                    db,
                    query=query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=retrieval_run_id,
                    latency_tracker=latency_tracker,
                )
            else:
                fallback_result = self.base._retrieve_and_rerank(
                    db,
                    query=query,
                    top_k=top_k,
                    rerank_top_n=rerank_top_n,
                    filters=filters,
                    retrieval_run_id=retrieval_run_id,
                    latency_tracker=latency_tracker,
                )
            return _result_with_graph_fallback_summary(
                fallback_result,
                fallback_strategy=fallback_strategy,
                selected_strategy=selected_strategy,
                execution_strategy=fallback_strategy,
                graph_summary=graph_summary,
                additional_reason_codes=fallback_selection.reason_codes,
            )
        return result

    def _graph_fallback_strategy(self) -> str:
        strategy = str(
            getattr(self.base.settings, "graph_retrieval_fallback_strategy", "hybrid")
        ).lower()
        return "hybrid" if strategy == "hybrid" else "dense"

    def _graph_fallback_selection(self) -> _GraphFallbackSelection:
        fallback_strategy = self._graph_fallback_strategy()
        if fallback_strategy != "hybrid":
            return _GraphFallbackSelection(strategy="dense")
        try:
            self.base._ensure_direct_strategy_enabled(RetrievalStrategy.HYBRID)
        except RagSearchPipelineError:
            return _GraphFallbackSelection(
                strategy="dense",
                reason_codes=(GRAPH_FALLBACK_HYBRID_DISABLED_REASON_CODE,),
            )
        return _GraphFallbackSelection(strategy="hybrid")

    def _record_graph_fallback_reason(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        fallback_strategy: str,
        graph_summary: dict[str, object],
        additional_reason_codes: tuple[str, ...] = (),
    ) -> None:
        run = self.base._require_run(db, retrieval_run_id)
        # Preserve trace suppression: when decision-trace storage is disabled the
        # decision builder stored None. Do not resurrect a trace here -- keep it
        # None instead of converting None -> {} and persisting reason codes.
        if not self.base.settings.router_store_decision_trace:
            return
        decision = dict(run.strategy_decision_json or {})
        existing_reason_codes = decision.get("reason_codes")
        if isinstance(existing_reason_codes, list):
            reason_codes = [str(code) for code in existing_reason_codes]
        else:
            reason_codes = []
        fallback_reason_code = (
            GRAPH_FALLBACK_HYBRID_REASON_CODE
            if fallback_strategy == "hybrid"
            else GRAPH_FALLBACK_DENSE_REASON_CODE
        )
        for code in _safe_string_list(graph_summary.get("graph_reason_codes")):
            if code not in reason_codes:
                reason_codes.append(code)
        for code in additional_reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
        for code in (GRAPH_NO_EVIDENCE_FALLBACK_REASON_CODE, fallback_reason_code):
            if code not in reason_codes:
                reason_codes.append(code)
        decision["reason_codes"] = reason_codes
        graph_store_provider = _safe_optional_string(graph_summary.get("graph_store_provider"))
        if graph_store_provider is not None:
            decision["graph_store_provider"] = graph_store_provider
        graph_fallback_reason_codes = _safe_string_list(
            graph_summary.get("graph_fallback_reason_codes")
        )
        if graph_fallback_reason_codes:
            decision["graph_fallback_reason_codes"] = graph_fallback_reason_codes
        # Mark the fallback in the persisted decision so retrieval debug and
        # evaluation metrics (which compute fallback rate from these fields) see it.
        # Mirror the base StrategyRouter decision key names: ``fallback_used`` and
        # ``fallback_strategy`` (here the actual graph fallback used -- dense or
        # hybrid), plus ``fallback_reason`` for the no-evidence trigger.
        decision["execution_strategy"] = fallback_strategy
        decision["fallback_used"] = True
        decision["fallback_strategy"] = fallback_strategy
        decision["fallback_reason"] = GRAPH_NO_EVIDENCE_FALLBACK_REASON_CODE
        self.base.repository.update_retrieval_run_trace(
            db,
            run=run,
            strategy_decision_json=TraceRedactor.safe_dict(decision),
        )

    def _sync_explicit_graph_base_fallback_run(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        result: RetrievalPipelineResult,
    ) -> None:
        fallback_strategy = _base_fallback_strategy_from_summary(result.summary)
        if fallback_strategy is None:
            return
        run = self.base._require_run(db, retrieval_run_id)
        run.strategy_type = fallback_strategy
        if isinstance(run.retrieval_settings_json, dict):
            settings_payload = dict(run.retrieval_settings_json)
            settings_payload["strategy_type"] = fallback_strategy
            settings_payload["requested_strategy"] = RetrievalStrategy.GRAPH.value
            run.retrieval_settings_json = TraceRedactor.safe_dict(settings_payload)
        if self.base.settings.router_store_decision_trace and isinstance(
            run.strategy_decision_json,
            dict,
        ):
            summary_payload = result.summary.model_dump(mode="json")
            decision = dict(run.strategy_decision_json)
            decision["execution_strategy"] = fallback_strategy
            decision["fallback_used"] = True
            decision["fallback_strategy"] = fallback_strategy
            fallback_reason = _safe_optional_string(summary_payload.get("fallback_reason"))
            if fallback_reason is not None:
                decision["fallback_reason"] = fallback_reason
            reason_codes = _safe_string_list(decision.get("reason_codes"))
            for field_name in ("graph_reason_codes", "graph_fallback_reason_codes"):
                for code in _safe_string_list(summary_payload.get(field_name)):
                    if code not in reason_codes:
                        reason_codes.append(code)
            decision["reason_codes"] = reason_codes
            for field_name in (
                "graph_store_provider",
                "graph_requested_provider",
                "graph_fallback_reason_codes",
            ):
                value = summary_payload.get(field_name)
                if isinstance(value, list):
                    decision[field_name] = _safe_string_list(value)
                elif (safe_value := _safe_optional_string(value)) is not None:
                    decision[field_name] = safe_value
            run.strategy_decision_json = TraceRedactor.safe_dict(decision)
        db.flush()

    def _record_graph_execution_summary(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        graph_result: GraphRetrievalResult,
        requested_provider: GraphStoreProvider | None,
    ) -> None:
        if not self.base.settings.router_store_decision_trace:
            return
        run = self.base._require_run(db, retrieval_run_id)
        if run.strategy_decision_json is None:
            return
        decision = dict(run.strategy_decision_json)
        decision["graph_store_provider"] = graph_result.provider.value
        if requested_provider is not None:
            decision["graph_requested_provider"] = requested_provider.value
        decision["graph_fallback_used"] = graph_result.fallback_used
        decision["graph_reason_codes"] = list(graph_result.reason_codes)
        existing_reason_codes = decision.get("reason_codes")
        reason_codes = (
            [str(code) for code in existing_reason_codes if isinstance(code, str)]
            if isinstance(existing_reason_codes, list)
            else []
        )
        for code in graph_result.reason_codes:
            if code not in reason_codes:
                reason_codes.append(code)
        fallback_reason_codes = _safe_string_list(
            graph_result.score_breakdown.get("fallback_reason_codes")
        )
        if fallback_reason_codes:
            decision["graph_fallback_reason_codes"] = fallback_reason_codes
        if graph_result.fallback_used:
            decision["fallback_used"] = True
            if _safe_optional_string(decision.get("fallback_strategy")) is None:
                decision["fallback_strategy"] = f"graph_{graph_result.provider.value}"
            primary_fallback_reason = _primary_graph_fallback_reason(fallback_reason_codes)
            if (
                primary_fallback_reason is not None
                and _safe_optional_string(decision.get("fallback_reason")) is None
            ):
                decision["fallback_reason"] = primary_fallback_reason
        decision["reason_codes"] = reason_codes
        self.base.repository.update_retrieval_run_trace(
            db,
            run=run,
            strategy_decision_json=TraceRedactor.safe_dict(decision),
        )

    def _retrieve_graph(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        rerank_top_n: int,
        filters: RetrievalFilters,
        retrieval_run_id: int,
        latency_tracker: LatencyTracker,
        graph_store_provider: GraphStoreProvider | None,
        force_graph_enabled: bool,
    ) -> RetrievalPipelineResult:
        settings = _graph_retrieval_settings(
            self.base.settings,
            graph_store_provider=graph_store_provider,
            force_enabled=force_graph_enabled,
        )
        with latency_tracker.span("graph_search_ms"):
            graph_result = self.graph_strategy.search(
                db,
                query=query,
                top_k=top_k,
                filters=filters,
                settings=settings,
            )
        self._record_graph_execution_summary(
            db,
            retrieval_run_id=retrieval_run_id,
            graph_result=graph_result,
            requested_provider=graph_store_provider,
        )
        graph_candidates = list(graph_result.graph_candidates)
        graph_by_chunk_id = {
            candidate.document_chunk_id: candidate for candidate in graph_candidates
        }
        with latency_tracker.span("rdb_final_check_ms"):
            checked_candidates = self.base.repository.final_check_candidates(
                db,
                candidates=[candidate.to_vector_candidate() for candidate in graph_candidates],
                filters=filters,
            )
        visible_candidates = checked_candidates[:top_k]
        selected_count = min(rerank_top_n, len(visible_candidates))
        if not visible_candidates:
            summary = _graph_score_summary(
                requested_top_k=top_k,
                graph_result=graph_result,
                checked_candidates=[],
                selected_count=0,
            )
            return RetrievalPipelineResult(
                summary=summary,
                items=[],
                selected_candidates=[],
                citation_sources=[],
                context_candidates=[],
                no_context=True,
            )

        with latency_tracker.span("retrieval_items_persist_ms"):
            saved_items = self.base.repository.save_items(
                db,
                retrieval_run_id=retrieval_run_id,
                items=[
                    _graph_run_item_input(
                        candidate,
                        graph_candidate=graph_by_chunk_id.get(candidate.chunk.document_chunk_id),
                        final_rank=index,
                        selected_flag=index <= selected_count,
                    )
                    for index, candidate in enumerate(visible_candidates, start=1)
                ],
            )
            path_candidates = tuple(
                graph_by_chunk_id[candidate.chunk.document_chunk_id]
                for candidate in visible_candidates
                if candidate.chunk.document_chunk_id in graph_by_chunk_id
            )
            self.graph_repository.save_graph_retrieval_paths(
                db,
                retrieval_run_id=retrieval_run_id,
                paths=self.graph_strategy.path_records(
                    retrieval_run_id=retrieval_run_id,
                    candidates=path_candidates,
                ),
            )
        summary = _graph_score_summary(
            requested_top_k=top_k,
            graph_result=graph_result,
            checked_candidates=visible_candidates,
            selected_count=selected_count,
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
                    snippet_max_chars=self.base.settings.search_snippet_max_chars,
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
                    snippet_max_chars=self.base.settings.citation_preview_max_chars,
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

    def _ensure_graph_enabled_for_search(self) -> None:
        if not self.base.settings.graph_retrieval_enabled:
            raise RagSearchPipelineError("strategy_not_enabled", 409)

    def _ensure_graph_enabled_for_ask(self) -> None:
        if not self.base.settings.graph_retrieval_enabled:
            raise RagAskPipelineError("strategy_not_enabled", 409)


def _graph_request_from_value(strategy_value: str) -> _GraphRequest:
    if strategy_value == RetrievalStrategy.AGENTIC_ROUTER.value:
        return _GraphRequest(
            canonical_strategy=RetrievalStrategy.AGENTIC_ROUTER,
            request_label=strategy_value,
        )
    if strategy_value == GRAPH_POSTGRES_REQUEST_VALUE:
        return _GraphRequest(
            canonical_strategy=RetrievalStrategy.GRAPH,
            request_label=strategy_value,
            provider_override=GraphStoreProvider.POSTGRES,
        )
    if strategy_value == GRAPH_NEO4J_REQUEST_VALUE:
        return _GraphRequest(
            canonical_strategy=RetrievalStrategy.GRAPH,
            request_label=strategy_value,
            provider_override=GraphStoreProvider.NEO4J,
        )
    if strategy_value == RetrievalStrategy.GRAPH.value:
        return _GraphRequest(
            canonical_strategy=RetrievalStrategy.GRAPH,
            request_label=strategy_value,
        )
    return _GraphRequest(
        canonical_strategy=RetrievalStrategy(strategy_value),
        request_label=strategy_value,
    )


def _cache_settings_for_graph_provider(
    settings: Settings,
    provider: GraphStoreProvider | None,
) -> Settings:
    if provider is None or settings.graph_store_provider == provider.value:
        return settings
    return settings.model_copy(update={"graph_store_provider": provider.value})


def _safe_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    safe_values: list[str] = []
    for item in value:
        safe = _safe_optional_string(item)
        if safe is not None:
            safe_values.append(safe)
    return safe_values


def _safe_optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    safe = TraceRedactor.safe_string(value, max_length=100)
    return safe or None


def _primary_graph_fallback_reason(reason_codes: list[str]) -> str | None:
    for code in reason_codes:
        if code != "graph_store_provider_unavailable":
            return code
    return reason_codes[0] if reason_codes else None


def _result_with_graph_fallback_summary(
    result: RetrievalPipelineResult,
    *,
    fallback_strategy: str,
    selected_strategy: str | None = None,
    execution_strategy: str | None = None,
    graph_summary: dict[str, object],
    additional_reason_codes: tuple[str, ...] = (),
) -> RetrievalPipelineResult:
    summary_payload = result.summary.model_dump(mode="json")
    fallback_reason_code = (
        GRAPH_FALLBACK_HYBRID_REASON_CODE
        if fallback_strategy == "hybrid"
        else GRAPH_FALLBACK_DENSE_REASON_CODE
    )
    graph_reason_codes = _safe_string_list(graph_summary.get("graph_reason_codes"))
    for code in additional_reason_codes:
        if code not in graph_reason_codes:
            graph_reason_codes.append(code)
    for code in (GRAPH_NO_EVIDENCE_FALLBACK_REASON_CODE, fallback_reason_code):
        if code not in graph_reason_codes:
            graph_reason_codes.append(code)
    summary_payload.update(
        {
            "fallback_used": True,
            "fallback_strategy": fallback_strategy,
            "fallback_reason": GRAPH_NO_EVIDENCE_FALLBACK_REASON_CODE,
            "execution_strategy": execution_strategy or fallback_strategy,
            "graph_reason_codes": graph_reason_codes,
            "graph_fallback_reason_codes": _safe_string_list(
                graph_summary.get("graph_fallback_reason_codes")
            ),
        }
    )
    if selected_strategy is not None:
        summary_payload["selected_strategy"] = selected_strategy
    if fallback_strategy in {"dense", "hybrid"}:
        summary_payload["strategy_type"] = fallback_strategy
    graph_store_provider = _safe_optional_string(graph_summary.get("graph_store_provider"))
    if graph_store_provider is not None:
        summary_payload["graph_store_provider"] = graph_store_provider
    return RetrievalPipelineResult(
        summary=RetrievalScoreSummary(**summary_payload),
        items=result.items,
        selected_candidates=result.selected_candidates,
        citation_sources=result.citation_sources,
        context_candidates=result.context_candidates,
        no_context=result.no_context,
    )


def _base_fallback_strategy_from_summary(summary: RetrievalScoreSummary) -> str | None:
    payload = summary.model_dump(mode="json")
    if payload.get("fallback_used") is not True:
        return None
    strategy = _safe_optional_string(payload.get("execution_strategy")) or _safe_optional_string(
        payload.get("fallback_strategy")
    )
    return strategy if strategy in {"dense", "hybrid"} else None


def _graph_retrieval_settings(
    settings: object,
    *,
    graph_store_provider: GraphStoreProvider | None = None,
    force_enabled: bool = False,
) -> GraphRetrievalSettings:
    return GraphRetrievalSettings(
        enabled=force_enabled or bool(getattr(settings, "graph_retrieval_enabled", False)),
        provider=(
            graph_store_provider
            if graph_store_provider is not None
            else str(getattr(settings, "graph_store_provider", GraphStoreProvider.POSTGRES.value))
        ),
        max_start_entities=int(getattr(settings, "graph_retrieval_max_start_entities", 5)),
        max_depth=int(getattr(settings, "graph_retrieval_max_depth", 2)),
        max_paths=int(getattr(settings, "graph_retrieval_max_paths", 20)),
        max_relations_per_entity=int(
            getattr(settings, "graph_retrieval_max_relations_per_entity", 20)
        ),
        max_source_chunks=int(getattr(settings, "graph_retrieval_max_source_chunks", 20)),
        timeout_ms=int(getattr(settings, "graph_retrieval_timeout_ms", 3000)),
        fallback_strategy=str(getattr(settings, "graph_retrieval_fallback_strategy", "hybrid")),
        min_entity_match_score=float(
            getattr(settings, "graph_retrieval_min_entity_match_score", 0.5)
        ),
    )


def _graph_settings_snapshot(
    *,
    settings: Settings,
    top_k: int,
    rerank_top_n: int,
    filters: RetrievalFilters,
    strategy_type: RetrievalStrategy,
    graph_store_provider: GraphStoreProvider | None = None,
    force_graph_enabled: bool = False,
) -> dict[str, object]:
    snapshot = _retrieval_settings_snapshot(
        settings=settings,
        top_k=top_k,
        rerank_top_n=rerank_top_n,
        filters=filters,
        strategy_type=strategy_type,
    )
    graph_settings = _graph_retrieval_settings(
        settings,
        graph_store_provider=graph_store_provider,
        force_enabled=force_graph_enabled,
    ).bounded()
    graph_store_provider = (
        graph_settings.provider
        if isinstance(graph_settings.provider, GraphStoreProvider)
        else GraphStoreProvider.POSTGRES
    )
    snapshot.update(
        TraceRedactor.safe_dict(
            {
                "graph_retrieval_enabled": bool(settings.graph_retrieval_enabled),
                "graph_retrieval_effective_enabled": bool(graph_settings.enabled),
                "graph_store_provider": graph_store_provider.value,
                "graph_retrieval_max_depth": graph_settings.max_depth,
                "graph_retrieval_max_paths": graph_settings.max_paths,
                "graph_retrieval_max_relations_per_entity": (
                    graph_settings.max_relations_per_entity
                ),
                "graph_retrieval_max_source_chunks": graph_settings.max_source_chunks,
                "graph_retrieval_max_start_entities": graph_settings.max_start_entities,
                "graph_retrieval_timeout_ms": graph_settings.timeout_ms,
                "graph_retrieval_min_entity_match_score": graph_settings.min_entity_match_score,
            }
        )
    )
    return snapshot


def _build_graph_query_plan(
    *,
    query_hash: str,
    filters: RetrievalFilters,
    plan_metadata: dict[str, object] | None,
) -> dict[str, object]:
    metadata = plan_metadata if isinstance(plan_metadata, dict) else {}
    sub_query_count = metadata.get("sub_query_count", 0)
    if not isinstance(sub_query_count, int):
        sub_query_count = 0
    return TraceRedactor.safe_dict(
        {
            "schema_version": "phase2.trace.v1",
            "strategy_type": RetrievalStrategy.GRAPH.value,
            "query_mode": "graph_path_search",
            "query_hash": query_hash,
            "rewrite_applied": bool(metadata.get("rewrite_applied", False)),
            "sub_query_count": sub_query_count,
            "metadata_filter_applied": bool(filters.logical_document_ids),
            "metadata_filter_count": len(filters.logical_document_ids or ()),
            "logical_document_filter_count": len(filters.logical_document_ids or ()),
            "reason_codes": [
                "phase3_graph_retrieval",
                "bounded_graph_path_search",
            ],
            "candidate_strategies": [RetrievalStrategy.GRAPH.value],
            "recommended_strategy": RetrievalStrategy.GRAPH.value,
            "analysis": metadata.get("analysis"),
            "planner": metadata.get("planner"),
            "graph_query_signal_score": metadata.get("graph_query_signal_score"),
        }
    )


def _build_graph_strategy_decision(
    *,
    selected_strategy: str = RetrievalStrategy.GRAPH.value,
    graph_store_provider: GraphStoreProvider | None = None,
    graph_retrieval_effective_enabled: bool | None = None,
    decision_source: str = "explicit_strategy",
    router_enabled: bool = False,
    confidence: float | None = None,
    reason_codes: list[str] | None = None,
    store_decision_trace: bool = True,
) -> dict[str, object] | None:
    # Mirror build_router_strategy_decision: when decision-trace storage is
    # disabled, persist None instead of a decision payload.
    if not store_decision_trace:
        return None
    payload: dict[str, object] = {
        "schema_version": "phase2.trace.v1",
        "selected_strategy": selected_strategy,
        "execution_strategy": RetrievalStrategy.GRAPH.value,
        "fallback_used": False,
        "router_enabled": router_enabled,
        "decision_source": decision_source,
        "decision_policy": "bounded_graph_path_search",
        "reason_codes": reason_codes
        or [
            "explicit_strategy_graph",
            "graph_retrieval_enabled",
        ],
    }
    if graph_store_provider is not None:
        payload["graph_requested_provider"] = graph_store_provider.value
        payload["graph_store_provider"] = graph_store_provider.value
    if graph_retrieval_effective_enabled is not None:
        payload["graph_retrieval_effective_enabled"] = graph_retrieval_effective_enabled
    if confidence is not None:
        payload["confidence"] = confidence
    return TraceRedactor.safe_dict(payload)


def _graph_score_summary(
    *,
    requested_top_k: int,
    graph_result: GraphRetrievalResult,
    checked_candidates: list[CheckedRetrievalCandidate],
    selected_count: int,
) -> RetrievalScoreSummary:
    summary_payload = _score_summary(
        requested_top_k=requested_top_k,
        qdrant_candidate_count=0,
        checked_candidates=checked_candidates,
        selected_count=selected_count,
        top1_rerank_score=None,
        excluded_by_rdb_check_count=max(
            0,
            graph_result.source_candidate_count - len(checked_candidates),
        ),
    ).model_dump(mode="json")
    summary_payload.update(graph_result.summary_fields())
    return RetrievalScoreSummary(**summary_payload)


def _graph_run_item_input(
    candidate: CheckedRetrievalCandidate,
    *,
    graph_candidate: GraphSourceCandidate | None,
    final_rank: int,
    selected_flag: bool,
) -> RetrievalRunItemInput:
    payload = _payload_snapshot(candidate)
    score_breakdown: dict[str, object] = {
        "schema_version": "phase3.graph_score.v1",
        "retrieval_source": RetrievalSource.GRAPH.value,
        "source_chunk_score": candidate.retrieval_score,
    }
    if graph_candidate is not None:
        payload.update(TraceRedactor.safe_dict(graph_candidate.payload))
        score_breakdown.update(TraceRedactor.safe_dict(graph_candidate.score_breakdown_json))
    score_breakdown.update({"final_rank": final_rank, "selected_flag": selected_flag})
    return RetrievalRunItemInput(
        document_chunk_id=candidate.chunk.document_chunk_id,
        retrieval_score=_decimal_score(candidate.retrieval_score),
        rerank_score=None,
        rank_order=candidate.rank_order,
        rerank_order=None,
        selected_flag=selected_flag,
        payload_snapshot=TraceRedactor.safe_dict(payload),
        retrieval_source=RetrievalSource.GRAPH.value,
        score_breakdown_json=TraceRedactor.safe_dict(score_breakdown),
    )
