from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ResourceNotFound
from app.db.models import EvaluationRun
from app.evaluation.rag_service import EvaluationRagQuestionService
from app.rag.strategy import RagSearchRequestStrategy, RetrievalStrategy
from app.schemas.common import PaginationParams
from app.schemas.rag import RagSearchRequest
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.job_service import JobService
from app.services.rag_service import RagSearchPipelineError, RagService, create_rag_service
from app.services.url_fetch_service import redact_url_for_display
from app.storage.file_storage import LocalFileStorage

from .errors import McpInvalidRequest, McpNotFound, McpToolExecutionError
from .redaction import redact_data, safe_metric_details, safe_source_label, truncate_text
from .schemas import (
    McpCompareStrategiesInput,
    McpGetDocumentStatusInput,
    McpGetEvaluationResultInput,
    McpGetJobStatusInput,
    McpGetRetrievalTraceInput,
    McpListDocumentsInput,
    McpListEvaluationRunsInput,
    McpRagAskInput,
    McpRagSearchInput,
)
from .settings import McpSettings, get_mcp_settings

SessionFactory: TypeAlias = sessionmaker[Session]
RagServiceFactory: TypeAlias = Callable[[Settings, Session], RagService]
T = TypeVar("T", bound=BaseModel)
RAW_CONTEXT_ANSWER_OMITTED = "[OMITTED: answer contained raw retrieved context]"
RAW_CONTEXT_OVERLAP_MIN_CHARS = 40


@dataclass(frozen=True)
class McpActorContext:
    actor_type: str = "mcp_local"
    actor_user_id: None = None


class McpServiceAdapter:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        mcp_settings: McpSettings | None = None,
        session_factory: SessionFactory | None = None,
        rag_service_factory: RagServiceFactory | None = None,
    ) -> None:
        self.settings = settings or Settings(_env_file=None)
        self.mcp_settings = mcp_settings or get_mcp_settings(self.settings)
        self.session_factory = session_factory or _default_session_factory(self.settings)
        self.rag_service_factory = rag_service_factory or _default_rag_service_factory
        self.actor = McpActorContext()
        self.document_service = DocumentService(
            storage=LocalFileStorage(base_dir=self.settings.storage_root),
        )
        self.job_service = JobService()
        self.evaluation_service = EvaluationService(settings=self.settings)

    @contextmanager
    def db_session(self) -> Iterator[Session]:
        with self.session_factory() as db:
            yield db

    def rag_search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpRagSearchInput, arguments)
        self._ensure_strategy_allowed(payload.strategy, ask=False)
        with self.db_session() as db:
            service = self._rag_service(db)
            try:
                response = service.search(
                    db,
                    payload=RagSearchRequest(
                        query=payload.query,
                        top_k=payload.top_k,
                        rerank_top_n=payload.rerank_top_n,
                        strategy=RagSearchRequestStrategy(payload.strategy),
                    ),
                    request_id="mcp-rag-search",
                )
            except RagSearchPipelineError as exc:
                raise McpToolExecutionError("RAG search failed", code=exc.error_code) from exc
            trace_summary = (
                _safe_retrieval_trace_summary(
                    service.get_retrieval_run_detail(
                        db,
                        retrieval_run_id=response.retrieval_run_id,
                    ).model_dump(mode="json"),
                    max_chars=self.mcp_settings.snippet_max_chars,
                )
                if self._include_trace_summary(payload.include_trace_summary)
                else None
            )
        data = response.model_dump(mode="json")
        data["strategy"] = payload.strategy
        data["items"] = [
            _safe_search_item(item, max_chars=self.mcp_settings.snippet_max_chars)
            for item in data["items"]
        ]
        if trace_summary is not None:
            data["trace_summary"] = trace_summary
        return _safe_output(data, self.mcp_settings.snippet_max_chars)

    def rag_search_hybrid(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rag_search({**arguments, "strategy": RetrievalStrategy.HYBRID.value})

    def rag_search_agentic(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rag_search(
            {**arguments, "strategy": RetrievalStrategy.AGENTIC_ROUTER.value},
        )

    def rag_ask(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpRagAskInput, arguments)
        self._ensure_strategy_allowed(payload.strategy, ask=True)
        with self.db_session() as db:
            service = EvaluationRagQuestionService(self._rag_service(db))
            result = service.evaluate_question(
                db,
                question=payload.question,
                request_id="mcp-rag-ask",
                strategy_type=RetrievalStrategy(payload.strategy),
                top_k=payload.top_k,
                rerank_top_n=payload.rerank_top_n,
            )
            trace_summary = (
                _safe_retrieval_trace_summary(
                    service.service.get_retrieval_run_detail(
                        db,
                        retrieval_run_id=result.retrieval_run_id,
                    ).model_dump(mode="json"),
                    max_chars=self.mcp_settings.snippet_max_chars,
                )
                if result.retrieval_run_id is not None
                and self._include_trace_summary(payload.include_trace_summary)
                else None
            )
        data: dict[str, Any] = {
            "retrieval_run_id": result.retrieval_run_id,
            "status": result.status,
            "strategy": payload.strategy,
            "answer": truncate_text(
                result.answer_text,
                max_chars=min(
                    self.settings.generation_max_output_chars,
                    self.mcp_settings.max_answer_chars,
                ),
            ),
            "citations": [
                {
                    **citation.model_dump(mode="json"),
                    "source_label": safe_source_label(citation.source_label) or "document",
                    "snippet": truncate_text(
                        citation.snippet,
                        max_chars=self.mcp_settings.snippet_max_chars,
                    ),
                }
                for citation in result.citations
            ],
            "confidence": (
                result.confidence.model_dump(mode="json") if result.confidence is not None else None
            ),
            "retrieval_score_summary": (
                result.retrieval_score_summary.model_dump(mode="json")
                if result.retrieval_score_summary is not None
                else None
            ),
            "error_code": result.error_code,
        }
        if not payload.include_citations:
            data["citations"] = []
        if not payload.include_confidence:
            data["confidence"] = None
        if trace_summary is not None:
            data["trace_summary"] = trace_summary
        return _safe_rag_ask_output(
            data,
            answer_max_chars=min(
                self.settings.generation_max_output_chars,
                self.mcp_settings.max_answer_chars,
            ),
            context_sources=result.context_sources_for_safety,
            snippet_max_chars=self.mcp_settings.snippet_max_chars,
        )

    def rag_ask_agentic(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rag_ask({**arguments, "strategy": RetrievalStrategy.AGENTIC_ROUTER.value})

    def rag_ask_hybrid(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rag_ask({**arguments, "strategy": RetrievalStrategy.HYBRID.value})

    def rag_get_retrieval_trace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpGetRetrievalTraceInput, arguments)
        with self.db_session() as db:
            try:
                detail = self._rag_service(db).get_retrieval_run_detail(
                    db,
                    retrieval_run_id=payload.retrieval_run_id,
                )
            except ResourceNotFound as exc:
                raise McpNotFound("retrieval run not found") from exc
        return _safe_retrieval_trace_summary(
            detail.model_dump(mode="json"),
            max_chars=self.mcp_settings.snippet_max_chars,
        )

    def rag_compare_strategies(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpCompareStrategiesInput, arguments)
        with self.db_session() as db:
            run = self._latest_evaluation_run(
                db,
                evaluation_dataset_id=payload.evaluation_dataset_id,
            )
            if run is None:
                raise McpNotFound("evaluation summary not found")
            detail = self.evaluation_service.get_run_detail(
                db,
                evaluation_run_id=run.evaluation_run_id,
            )
        summary = _safe_evaluation_summary(
            detail.model_dump(mode="json"),
            max_chars=self.mcp_settings.snippet_max_chars,
        )
        requested = list(payload.strategies)
        return _safe_output(
            {
                "mode": payload.mode,
                "evaluation_run_id": summary["evaluation_run_id"],
                "evaluation_dataset_id": summary.get("evaluation_dataset_id"),
                "dataset_name": summary.get("dataset_name"),
                "strategies": requested,
                "metrics": _strategy_metrics(summary, requested),
                "agentic_summary": summary.get("agentic_summary"),
                "failure_summary": summary.get("failure_summary"),
            },
            self.mcp_settings.snippet_max_chars,
        )

    def rag_get_evaluation_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpGetEvaluationResultInput, arguments)
        with self.db_session() as db:
            try:
                detail = self.evaluation_service.get_run_detail(
                    db,
                    evaluation_run_id=payload.evaluation_run_id,
                )
            except ResourceNotFound as exc:
                raise McpNotFound("evaluation run not found") from exc
        return _safe_evaluation_summary(
            detail.model_dump(mode="json"),
            max_chars=self.mcp_settings.snippet_max_chars,
        )

    def rag_strategies(self, _arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        strategies = [
            {
                "strategy": RetrievalStrategy.DENSE.value,
                "description": "Default vector retrieval.",
                "available": RetrievalStrategy.DENSE.value in self.mcp_settings.allowed_strategies,
                "mcp_tools": ["rag_search", "rag_ask"],
            },
            {
                "strategy": RetrievalStrategy.SPARSE.value,
                "description": "PostgreSQL full-text sparse retrieval.",
                "available": RetrievalStrategy.SPARSE.value in self.mcp_settings.allowed_strategies,
                "mcp_tools": ["rag_search"],
            },
            {
                "strategy": RetrievalStrategy.HYBRID.value,
                "description": "Dense + sparse fusion retrieval.",
                "available": RetrievalStrategy.HYBRID.value in self.mcp_settings.allowed_strategies,
                "mcp_tools": ["rag_search", "rag_search_hybrid", "rag_ask_hybrid"],
            },
            {
                "strategy": RetrievalStrategy.AGENTIC_ROUTER.value,
                "description": "Rule-based StrategyRouter plus bounded agentic retrieval loop.",
                "available": (
                    RetrievalStrategy.AGENTIC_ROUTER.value in self.mcp_settings.allowed_strategies
                ),
                "mcp_tools": ["rag_search", "rag_search_agentic", "rag_ask", "rag_ask_agentic"],
            },
        ]
        return _safe_output(
            {
                "schema_version": "phase2.mcp_strategies.v1",
                "local_only": self.mcp_settings.local_only,
                "transport": self.mcp_settings.transport,
                "write_tools_enabled": False,
                "evaluation_run_create_enabled": False,
                "strategies": strategies,
            },
            self.mcp_settings.snippet_max_chars,
        )

    def list_documents(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpListDocumentsInput, arguments)
        pagination = PaginationParams(page=payload.page, page_size=payload.page_size)
        with self.db_session() as db:
            items, meta = self.document_service.list_documents(
                db,
                status=payload.status,
                query=None,
                display_status=payload.display_status,
                pagination=pagination,
            )
        return _safe_output(
            {
                "items": [_safe_document(item.model_dump(mode="json")) for item in items],
                "pagination": meta.model_dump(mode="json"),
            },
            self.mcp_settings.snippet_max_chars,
        )

    def get_document_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpGetDocumentStatusInput, arguments)
        with self.db_session() as db:
            try:
                detail = self.document_service.get_document_detail(
                    db,
                    logical_document_id=payload.logical_document_id,
                )
            except ResourceNotFound as exc:
                raise McpNotFound("document not found") from exc
        return _safe_output(
            _safe_document(detail.model_dump(mode="json")),
            self.mcp_settings.snippet_max_chars,
        )

    def get_job_status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpGetJobStatusInput, arguments)
        with self.db_session() as db:
            try:
                detail = self.job_service.get_job_detail(db, job_id=payload.job_id)
            except ResourceNotFound as exc:
                raise McpNotFound("job not found") from exc
        data = detail.model_dump(mode="json")
        return _safe_output(data, self.mcp_settings.snippet_max_chars)

    def list_evaluation_runs(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpListEvaluationRunsInput, arguments)
        pagination = PaginationParams(page=payload.page, page_size=payload.page_size)
        with self.db_session() as db:
            items, meta = self.evaluation_service.list_runs(
                db,
                pagination=pagination,
                status=payload.status,
            )
        return _safe_output(
            {
                "items": [item.model_dump(mode="json") for item in items],
                "pagination": meta.model_dump(mode="json"),
            },
            self.mcp_settings.snippet_max_chars,
        )

    def get_evaluation_result(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpGetEvaluationResultInput, arguments)
        with self.db_session() as db:
            try:
                detail = self.evaluation_service.get_run_detail(
                    db,
                    evaluation_run_id=payload.evaluation_run_id,
                )
            except ResourceNotFound as exc:
                raise McpNotFound("evaluation run not found") from exc
        data = detail.model_dump(mode="json")
        data["items"] = [
            _safe_evaluation_item(item, self.mcp_settings.snippet_max_chars)
            for item in data["items"]
        ]
        return _safe_output(data, self.mcp_settings.snippet_max_chars)

    def _rag_service(self, db: Session) -> RagService:
        return self.rag_service_factory(self.settings, db)

    def _include_trace_summary(self, requested: bool | None) -> bool:
        if requested is None:
            return self.mcp_settings.include_trace_summary_default
        return requested

    def _ensure_strategy_allowed(self, strategy: str, *, ask: bool) -> None:
        if strategy not in self.mcp_settings.allowed_strategies:
            raise McpInvalidRequest("strategy is not allowed for MCP")
        if (
            strategy != RetrievalStrategy.DENSE.value
            and not self.mcp_settings.enable_advanced_rag_tools
        ):
            raise McpInvalidRequest("advanced RAG MCP tools are disabled")
        if ask and strategy not in {
            RetrievalStrategy.DENSE.value,
            RetrievalStrategy.HYBRID.value,
            RetrievalStrategy.AGENTIC_ROUTER.value,
        }:
            raise McpInvalidRequest("rag_ask supports dense, hybrid, or agentic_router only")

    def _latest_evaluation_run(
        self,
        db: Session,
        *,
        evaluation_dataset_id: int | None,
    ) -> EvaluationRun | None:
        statement = select(EvaluationRun).where(EvaluationRun.status == "succeeded")
        if evaluation_dataset_id is not None:
            statement = statement.where(
                EvaluationRun.evaluation_dataset_id == evaluation_dataset_id
            )
        return db.scalar(
            statement.order_by(
                EvaluationRun.created_at.desc(), EvaluationRun.evaluation_run_id.desc()
            )
        )


def _validate(model: type[T], arguments: dict[str, Any]) -> T:
    try:
        return cast(T, model.model_validate(arguments))
    except ValidationError as exc:
        raise McpInvalidRequest("invalid tool arguments") from exc


def _default_session_factory(settings: Settings) -> SessionFactory:
    connect_args = (
        {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    )
    engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _default_rag_service_factory(settings: Settings, db: Session) -> RagService:
    del db
    return create_rag_service(settings)


def _safe_document(data: dict[str, Any]) -> dict[str, Any]:
    for version_key in ("latest_version", "active_version"):
        if data.get(version_key) is not None:
            data[version_key] = _safe_version(data[version_key])
    if "versions" in data:
        data["versions"] = [_safe_version(version) for version in data["versions"]]
    return data


def _safe_version(data: dict[str, Any]) -> dict[str, Any]:
    safe = {
        key: value
        for key, value in data.items()
        if key
        in {
            "document_version_id",
            "version_no",
            "status",
            "is_active",
            "display_status",
            "file_name",
            "mime_type",
            "file_size_bytes",
            "page_count",
            "error_code",
            "chunk_count",
            "created_at",
            "updated_at",
            "logical_document_id",
        }
    }
    file_name = safe.get("file_name")
    safe["file_name"] = safe_source_label(file_name) if isinstance(file_name, str) else None
    return safe


def _safe_evaluation_item(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    data["metrics"] = [
        {
            "metric_name": metric.get("metric_name"),
            "metric_score": metric.get("metric_score"),
            "metric_label": truncate_text(str(metric.get("metric_label")), max_chars=max_chars)
            if metric.get("metric_label") is not None
            else None,
            "details": safe_metric_details(metric.get("details"), max_string_chars=max_chars)
            if isinstance(metric.get("details"), dict)
            else {},
        }
        for metric in data.get("metrics", [])
    ]
    return data


def _safe_search_item(item: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    return {
        "retrieval_run_item_id": item.get("retrieval_run_item_id"),
        "document_chunk_id": item.get("document_chunk_id"),
        "source_label": safe_source_label(str(item.get("source_label") or "")) or "document",
        "snippet": truncate_text(str(item.get("snippet") or ""), max_chars=max_chars),
        "page_from": item.get("page_from"),
        "page_to": item.get("page_to"),
        "retrieval_score": item.get("retrieval_score"),
        "rerank_score": item.get("rerank_score"),
        "rank_order": item.get("rank_order"),
        "rerank_order": item.get("rerank_order"),
        "selected_flag": item.get("selected_flag"),
        "payload_snapshot": _safe_payload_snapshot(item.get("payload_snapshot"), max_chars),
    }


def _safe_payload_snapshot(value: object, max_chars: int) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "logical_document_id",
        "document_version_id",
        "source_label",
        "version_no",
        "modality",
        "page_from",
        "page_to",
        "section_title",
        "source_type",
        "safe_source_url",
        "html_heading_path",
        "xml_path",
        "sheet_name",
        "row_from",
        "row_to",
        "slide_number",
        "parent_chunk_key",
        "child_chunk_key",
        "structure_type",
    }
    safe = {str(key): item for key, item in value.items() if str(key) in allowed_keys}
    source_url = value.get("safe_source_url") or value.get("source_url")
    if isinstance(source_url, str):
        safe["safe_source_url"] = _safe_url_for_mcp(source_url)
    return cast(dict[str, object], redact_data(safe, max_string_chars=max_chars))


def _safe_url_for_mcp(value: str) -> str:
    safe_url = redact_url_for_display(value)
    if safe_url == "redacted":
        return safe_url
    return truncate_text(safe_url, max_chars=500)


def _safe_retrieval_trace_summary(data: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    run = data.get("retrieval_run")
    items = data.get("items")
    run_data = run if isinstance(run, dict) else {}
    item_data = items if isinstance(items, list) else []
    score_summary = _safe_numeric_mapping(run_data.get("retrieval_score_summary"))
    strategy_decision = _safe_strategy_decision(run_data.get("strategy_decision_json"), max_chars)
    query_plan = _safe_query_plan(run_data.get("query_plan_json"), max_chars)
    latency = _safe_numeric_mapping(run_data.get("latency_breakdown_json"))
    selected_count = sum(
        1 for item in item_data if isinstance(item, dict) and item.get("selected_flag") is True
    )
    return _safe_output(
        {
            "retrieval_run_id": run_data.get("retrieval_run_id"),
            "status": run_data.get("status"),
            "origin_type": run_data.get("origin_type"),
            "strategy_type": run_data.get("strategy_type"),
            "error_code": run_data.get("error_code"),
            "query_hash": run_data.get("query_hash"),
            "top_k": run_data.get("top_k"),
            "query_plan": query_plan,
            "strategy_decision": strategy_decision,
            "score_summary": score_summary,
            "latency_summary": latency,
            "retrieval_settings": _safe_numeric_mapping(run_data.get("retrieval_settings_json")),
            "item_count": len(item_data),
            "selected_count": selected_count,
            "rerank_score_top1": run_data.get("rerank_score_top1"),
            "answer_confidence": run_data.get("answer_confidence"),
            "groundedness_score": run_data.get("groundedness_score"),
            "confidence_label": run_data.get("confidence_label"),
        },
        max_chars,
    )


def _safe_query_plan(value: object, max_chars: int) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "schema_version",
        "strategy_type",
        "query_mode",
        "query_hash",
        "intent",
        "ambiguity_score",
        "keyword_heavy_score",
        "version_specific_flag",
        "candidate_strategies",
        "recommended_strategy",
        "metadata_filter_candidates",
        "disabled_reason",
        "reason_codes",
        "safety_flags",
        "execution_strategy",
    }
    safe = {str(key): item for key, item in value.items() if str(key) in allowed_keys}
    return cast(dict[str, object], redact_data(safe, max_string_chars=max_chars))


def _safe_strategy_decision(value: object, max_chars: int) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "schema_version",
        "requested_strategy",
        "selected_strategy",
        "execution_strategy",
        "decision_source",
        "fallback_used",
        "fallback_reason",
        "fallback_strategy",
        "router_enabled",
        "confidence",
        "reason_codes",
        "disabled_candidates",
        "safety_flags",
        "retrieval_call_count",
        "budget_exhausted",
        "sufficiency_score",
        "sufficient",
        "sufficiency_reason_codes",
        "max_retrieval_calls",
    }
    safe = {str(key): item for key, item in value.items() if str(key) in allowed_keys}
    return cast(dict[str, object], redact_data(safe, max_string_chars=max_chars))


def _safe_numeric_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    safe: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if isinstance(item, bool | int | float) or item is None:
            safe[key_text] = item
        elif isinstance(item, str) and _is_safe_enum_like(item):
            safe[key_text] = item
        elif isinstance(item, list):
            string_values = [entry for entry in item if isinstance(entry, str)]
            if len(string_values) == len(item) and all(_is_safe_enum_like(entry) for entry in item):
                safe[key_text] = string_values
    return safe


def _is_safe_enum_like(value: str) -> bool:
    return bool(re.fullmatch(r"[a-zA-Z0-9_.:-]{1,120}", value))


def _safe_evaluation_summary(data: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    strategy_metrics_summary = data.get("strategy_metrics_summary_json")
    summary = strategy_metrics_summary if isinstance(strategy_metrics_summary, dict) else {}
    return _safe_output(
        {
            "evaluation_run_id": data.get("evaluation_run_id"),
            "job_id": data.get("job_id"),
            "evaluation_dataset_id": data.get("evaluation_dataset_id"),
            "dataset_name": data.get("dataset_name"),
            "strategy_type": data.get("strategy_type"),
            "strategies": data.get("strategies")
            if isinstance(data.get("strategies"), list)
            else [],
            "metric_names": data.get("metric_names")
            if isinstance(data.get("metric_names"), list)
            else [],
            "trigger_type": data.get("trigger_type"),
            "status": data.get("status"),
            "case_count": data.get("case_count"),
            "succeeded_count": data.get("succeeded_count"),
            "failed_count": data.get("failed_count"),
            "metric_summary": _safe_numeric_mapping(data.get("metric_summary")),
            "strategy_comparison": _safe_strategy_comparison(data.get("strategy_comparison")),
            "strategy_metrics": summary.get("strategy_metrics")
            if isinstance(summary.get("strategy_metrics"), dict)
            else {},
            "agentic_summary": summary.get("agentic_summary")
            if isinstance(summary.get("agentic_summary"), dict)
            else None,
            "failure_summary": summary.get("failure_summary")
            if isinstance(summary.get("failure_summary"), dict)
            else {},
            "error_code": data.get("error_code"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        },
        max_chars,
    )


def _safe_strategy_comparison(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "strategy_type": entry.get("strategy_type"),
                "metric_name": entry.get("metric_name"),
                "average": entry.get("average"),
                "p95": entry.get("p95"),
                "count": entry.get("count"),
                "not_applicable_count": entry.get("not_applicable_count"),
                "failed_count": entry.get("failed_count"),
            }
        )
    return items


def _strategy_metrics(
    summary: dict[str, Any], strategies: Sequence[str]
) -> list[dict[str, object]]:
    summary_by_strategy = summary.get("strategy_metrics")
    comparison = summary.get("strategy_comparison")
    metrics: list[dict[str, object]] = []
    for strategy in strategies:
        metric_summary: dict[str, object] = {}
        if isinstance(summary_by_strategy, dict):
            entry = summary_by_strategy.get(strategy)
            if isinstance(entry, dict) and isinstance(entry.get("metric_summary"), dict):
                metric_summary.update(cast(dict[str, object], entry["metric_summary"]))
        if isinstance(comparison, list):
            for item in comparison:
                if not isinstance(item, dict) or item.get("strategy_type") != strategy:
                    continue
                metric_name = item.get("metric_name")
                average = item.get("average")
                p95 = item.get("p95")
                if isinstance(metric_name, str) and average is not None:
                    metric_summary.setdefault(metric_name, average)
                if metric_name == "p95_latency" and p95 is not None:
                    metric_summary["p95_latency"] = p95
        if metric_summary:
            metrics.append({"strategy": strategy, "metric_summary": metric_summary})
    return metrics


def _safe_output(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    return redact_data(data, max_string_chars=max_chars)


def _safe_rag_ask_output(
    data: dict[str, Any],
    *,
    answer_max_chars: int,
    snippet_max_chars: int,
    context_sources: list[str] | None = None,
) -> dict[str, Any]:
    answer = data.get("answer")
    safe = _safe_output(
        {key: value for key, value in data.items() if key != "answer"},
        snippet_max_chars,
    )
    safe["answer"] = _safe_rag_answer(
        answer,
        answer_max_chars=answer_max_chars,
        context_sources=context_sources or [],
    )
    return safe


def _safe_rag_answer(
    answer: object,
    *,
    answer_max_chars: int,
    context_sources: list[str],
) -> object:
    if not isinstance(answer, str):
        return answer
    safe_answer = truncate_text(answer, max_chars=answer_max_chars)
    if _contains_raw_context_overlap(
        safe_answer,
        context_sources=context_sources,
    ):
        return RAW_CONTEXT_ANSWER_OMITTED
    return safe_answer


def _contains_raw_context_overlap(
    answer: str,
    *,
    context_sources: list[str],
) -> bool:
    normalized_answer = _normalize_text_for_overlap(answer)
    if not normalized_answer:
        return False
    for source in context_sources:
        normalized_source = _normalize_text_for_overlap(source)
        if len(normalized_source) < 24:
            continue
        threshold = min(RAW_CONTEXT_OVERLAP_MIN_CHARS, len(normalized_source))
        for start in range(0, len(normalized_source) - threshold + 1):
            if normalized_source[start : start + threshold] in normalized_answer:
                return True
    return False


def _normalize_text_for_overlap(value: str) -> str:
    return " ".join(value.casefold().split())
