from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar, cast

from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.errors import ResourceNotFound
from app.evaluation.rag_service import EvaluationRagQuestionService
from app.schemas.common import PaginationParams
from app.schemas.rag import RagSearchRequest
from app.services.document_service import DocumentService
from app.services.evaluation_service import EvaluationService
from app.services.job_service import JobService
from app.services.rag_service import RagSearchPipelineError, RagService, create_rag_service
from app.storage.file_storage import LocalFileStorage

from .errors import McpInvalidRequest, McpNotFound, McpToolExecutionError
from .redaction import redact_data, safe_metric_details, safe_source_label, truncate_text
from .schemas import (
    McpGetDocumentStatusInput,
    McpGetEvaluationResultInput,
    McpGetJobStatusInput,
    McpListDocumentsInput,
    McpListEvaluationRunsInput,
    McpRagAskInput,
    McpRagSearchInput,
)
from .settings import McpSettings, get_mcp_settings

SessionFactory: TypeAlias = sessionmaker[Session]
RagServiceFactory: TypeAlias = Callable[[Settings, Session], RagService]
T = TypeVar("T", bound=BaseModel)


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
        with self.db_session() as db:
            service = self._rag_service(db)
            try:
                response = service.search(
                    db,
                    payload=RagSearchRequest(
                        query=payload.query,
                        top_k=payload.top_k,
                        rerank_top_n=payload.rerank_top_n,
                    ),
                    request_id="mcp-rag-search",
                )
            except RagSearchPipelineError as exc:
                raise McpToolExecutionError("RAG search failed", code=exc.error_code) from exc
        data = response.model_dump(mode="json")
        data["items"] = [
            {
                **item,
                "source_label": safe_source_label(str(item["source_label"])) or "document",
                "snippet": truncate_text(
                    str(item["snippet"]),
                    max_chars=self.mcp_settings.snippet_max_chars,
                ),
                "payload_snapshot": redact_data(
                    item.get("payload_snapshot", {}),
                    max_string_chars=self.mcp_settings.snippet_max_chars,
                ),
            }
            for item in data["items"]
        ]
        return _safe_output(data, self.mcp_settings.snippet_max_chars)

    def rag_ask(self, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = _validate(McpRagAskInput, arguments)
        with self.db_session() as db:
            service = EvaluationRagQuestionService(self._rag_service(db))
            result = service.evaluate_question(
                db,
                question=payload.question,
                request_id="mcp-rag-ask",
                top_k=payload.top_k,
                rerank_top_n=payload.rerank_top_n,
            )
        data: dict[str, Any] = {
            "retrieval_run_id": result.retrieval_run_id,
            "status": result.status,
            "answer": truncate_text(
                result.answer_text,
                max_chars=self.settings.generation_max_output_chars,
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
        return _safe_output(data, self.mcp_settings.snippet_max_chars)

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


def _safe_output(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    return redact_data(data, max_string_chars=max_chars)
