from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.db.models import User
from app.rag.citations import CitationBuildError
from app.rag.confidence import ConfidenceInputs, calculate_confidence
from app.rag.context_budget import estimate_tokens
from app.rag.generation import AnswerGenerationError, GenerationRequest
from app.rag.graph_retrieval import (
    GraphRetrievalResult,
    GraphRetrievalSettings,
    GraphRetrievalStrategy,
    GraphSourceCandidate,
)
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
    _is_insufficient_evidence_answer,
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
        requested_strategy = RetrievalStrategy(payload.strategy.value)
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
        elif requested_strategy == RetrievalStrategy.GRAPH:
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
            strategy_decision = _build_graph_strategy_decision()
        else:
            return self.base.search(db, payload=payload, request_id=request_id)

        query_hash = _query_hash(payload.query)
        latency_tracker = LatencyTracker()
        retrieval_settings = _retrieval_settings_snapshot(
            settings=self.base.settings,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            strategy_type=requested_strategy,
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
            result = self._retrieve_graph(
                db,
                query=retrieval_query,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                latency_tracker=latency_tracker,
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
        except RetrievalError:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
            )
            raise RagSearchPipelineError("retrieval_failed", 503) from None
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
        requested_strategy = RetrievalStrategy(payload.strategy.value)
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
        elif requested_strategy == RetrievalStrategy.GRAPH:
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
            strategy_decision = _build_graph_strategy_decision()
        else:
            return self.base.ask(db, payload=payload, user=user, request_id=request_id)

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
        answer_generator = self.base._answer_generator_for_request(payload)

        top_k = self.base._effective_ask_top_k(payload.top_k)
        rerank_top_n = self.base._effective_ask_rerank_top_n(payload.rerank_top_n)
        filters = _retrieval_filters(payload)
        query_hash = _query_hash(payload.message)
        latency_tracker = LatencyTracker()
        retrieval_settings = _retrieval_settings_snapshot(
            settings=self.base.settings,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
            filters=filters,
            strategy_type=requested_strategy,
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
            result = self._retrieve_graph(
                db,
                query=retrieval_query,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                filters=filters,
                retrieval_run_id=run_id,
                latency_tracker=latency_tracker,
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
            with latency_tracker.span("generation_ms"):
                generation = answer_generator.generate(
                    GenerationRequest(
                        message=payload.message,
                        context_items=context_items,
                        max_output_chars=self.base.settings.generation_max_output_chars,
                    )
                )
            with latency_tracker.span("citation_build_ms"):
                parsed_generation, cited_sources = _validated_generation_or_fallback(
                    generation.content,
                    context_items=context_items,
                    prompt_citation_sources=prompt_citation_sources,
                )
                if _is_insufficient_evidence_answer(parsed_generation.answer_text):
                    self.base._mark_failed_safely(
                        db,
                        retrieval_run_id=run_id,
                        error_code="no_context_found",
                        latency_tracker=latency_tracker,
                        rollback=False,
                    )
                    raise RagAskPipelineError("no_context_found", 422)
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
        except RetrievalError:
            self.base._mark_failed_safely(
                db,
                retrieval_run_id=run_id,
                error_code="retrieval_failed",
                latency_tracker=latency_tracker,
            )
            raise RagAskPipelineError("retrieval_failed", 503) from None
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
    ) -> tuple[str, dict[str, object], dict[str, object]] | None:
        if not self.base.settings.graph_retrieval_enabled:
            return None
        query_plan_build = self.base.query_plan_builder.build(
            query,
            filters=filters,
            requested_strategy=requested_strategy,
        )
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
        strategy_decision = build_router_strategy_decision(decision=decision) or {}
        return query_plan_build.retrieval_query, query_plan, strategy_decision

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
    ) -> RetrievalPipelineResult:
        settings = _graph_retrieval_settings(self.base.settings)
        with latency_tracker.span("graph_search_ms"):
            graph_result = self.graph_strategy.search(
                db,
                query=query,
                top_k=top_k,
                filters=filters,
                settings=settings,
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


def _graph_retrieval_settings(settings: object) -> GraphRetrievalSettings:
    return GraphRetrievalSettings(
        enabled=bool(getattr(settings, "graph_retrieval_enabled", False)),
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


def _build_graph_strategy_decision() -> dict[str, object]:
    return TraceRedactor.safe_dict(
        {
            "schema_version": "phase2.trace.v1",
            "selected_strategy": RetrievalStrategy.GRAPH.value,
            "execution_strategy": RetrievalStrategy.GRAPH.value,
            "fallback_used": False,
            "router_enabled": False,
            "decision_source": "explicit_strategy",
            "decision_policy": "bounded_graph_path_search",
            "reason_codes": [
                "explicit_strategy_graph",
                "graph_retrieval_enabled",
            ],
        }
    )


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
