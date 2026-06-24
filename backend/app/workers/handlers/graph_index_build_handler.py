from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ResourceNotFound
from app.core.job_utils import LeaseLostError
from app.db.session import SessionLocal
from app.graph.extraction import GraphExtractionResult
from app.repositories.job_repository import JobRepository
from app.schemas.graph import GraphIndexJobPayload
from app.services.graph_index_service import GraphIndexBuildSnapshot, GraphIndexService
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult

logger = logging.getLogger(__name__)

_SAFE_MESSAGES = {
    "validation_error": "Job payload is invalid.",
    "document_version_not_found": "Document version was not found.",
    "document_version_not_ready": "Document version is not ready.",
    "graph_index_run_not_found": "Graph index run was not found.",
    "graph_index_run_not_ready": "Graph index run is not ready.",
    "graph_extraction_failed": "Graph extraction failed.",
    "graph_normalization_failed": "Graph normalization failed.",
    "graph_relation_validation_failed": "Graph relation validation failed.",
    "graph_index_write_failed": "Graph index write failed.",
    "neo4j_connection_failed": "Neo4j projection failed.",
    "neo4j_driver_unavailable": "Neo4j projection failed.",
    "neo4j_not_configured": "Neo4j projection failed.",
    "neo4j_projection_failed": "Neo4j projection failed.",
    "internal_error": "Graph index build failed.",
}


class GraphIndexBuildHandler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        service_factory: Callable[[], GraphIndexService] = GraphIndexService,
        job_repository: JobRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.service_factory = service_factory
        self.job_repository = job_repository or JobRepository()

    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        payload = _payload(context.payload)
        if payload is None:
            return _failed("validation_error")
        if (
            context.target_type != "document_version"
            or context.target_id != payload.document_version_id
        ):
            return _failed("validation_error")

        service = self.service_factory()
        prepared = self._prepare(context, service, payload)
        if isinstance(prepared, JobHandlerResult):
            return prepared

        try:
            extraction_result = service.extract_from_snapshot(prepared)
        except LeaseLostError:
            raise
        except Exception:
            logger.error(
                "graph extraction failed",
                extra={
                    "job_id": context.job_id,
                    "document_version_id": prepared.document_version_id,
                    "graph_index_run_id": prepared.graph_index_run_id,
                    "error_code": "graph_extraction_failed",
                },
            )
            return self._fail_run(
                context,
                service,
                prepared.graph_index_run_id,
                "graph_extraction_failed",
            )

        return self._persist(context, service, prepared, extraction_result)

    def _prepare(
        self,
        context: JobExecutionContext,
        service: GraphIndexService,
        payload: GraphIndexJobPayload,
    ) -> GraphIndexBuildSnapshot | JobHandlerResult:
        db = self.session_factory()
        try:
            graph_index_run_id = payload.graph_index_run_id
            if graph_index_run_id is not None:
                run = service.repository.get_graph_index_run(
                    db,
                    graph_index_run_id,
                    for_update=True,
                )
                if run is None:
                    db.rollback()
                    return _failed("graph_index_run_not_found")
                if run.document_version_id != payload.document_version_id:
                    db.rollback()
                    return _failed("validation_error")
                if run.status == "succeeded":
                    self.job_repository.assert_ownership(
                        db,
                        job_id=context.job_id,
                        worker_instance_id=context.worker_instance_id,
                    )
                    document_version_id = payload.document_version_id
                    graph_index_run_id = run.graph_index_run_id
                    entity_count = run.entity_count
                    relation_count = run.relation_count
                    mention_count = run.mention_count
                    db.commit()
                    projection_result = self._project_neo4j_after_commit(
                        db,
                        service,
                        document_version_id=document_version_id,
                        graph_index_run_id=graph_index_run_id,
                    )
                    projection_error_code = _projection_error_code(projection_result)
                    if projection_error_code is not None:
                        return _failed(projection_error_code)
                    return JobHandlerResult.succeeded(
                        {
                            "document_version_id": document_version_id,
                            "graph_index_run_id": graph_index_run_id,
                            "entity_count": entity_count,
                            "relation_count": relation_count,
                            "mention_count": mention_count,
                            "status": "already_succeeded",
                            "result_code": "no_op",
                            **_projection_payload(projection_result),
                        }
                    )
                if run.status == "failed":
                    graph_index_run_id = None
                elif run.status not in {"queued", "running"}:
                    db.rollback()
                    return _failed("graph_index_run_not_ready")

            snapshot = service.prepare_index_build(
                db,
                document_version_id=payload.document_version_id,
                graph_index_run_id=graph_index_run_id,
                job_id=context.job_id,
                extractor_type=payload.extractor_type,
                extractor_version=payload.extractor_version,
            )
            self.job_repository.assert_ownership(
                db,
                job_id=context.job_id,
                worker_instance_id=context.worker_instance_id,
            )
            db.commit()
            return snapshot
        except LeaseLostError:
            db.rollback()
            raise
        except ResourceNotFound:
            db.rollback()
            return _failed("document_version_not_found")
        except ValueError:
            db.rollback()
            if payload.graph_index_run_id is not None:
                self._fail_run(
                    context,
                    service,
                    payload.graph_index_run_id,
                    "document_version_not_ready",
                )
            return _failed("document_version_not_ready")
        except Exception:
            db.rollback()
            logger.error(
                "graph index preparation failed",
                extra={
                    "job_id": context.job_id,
                    "document_version_id": payload.document_version_id,
                    "error_code": "internal_error",
                },
            )
            return _failed("internal_error")
        finally:
            db.close()

    def _persist(
        self,
        context: JobExecutionContext,
        service: GraphIndexService,
        snapshot: GraphIndexBuildSnapshot,
        extraction_result: GraphExtractionResult,
    ) -> JobHandlerResult:
        db = self.session_factory()
        try:
            self.job_repository.assert_ownership(
                db,
                job_id=context.job_id,
                worker_instance_id=context.worker_instance_id,
            )
            run = service.persist_extraction_result(
                db,
                snapshot=snapshot,
                result=extraction_result,
            )
            result_payload = {
                "document_version_id": snapshot.document_version_id,
                "graph_index_run_id": run.graph_index_run_id,
                "entity_count": run.entity_count,
                "relation_count": run.relation_count,
                "mention_count": run.mention_count,
                "status": "succeeded",
                "result_code": "indexed",
            }
            db.commit()
            projection_result = self._project_neo4j_after_commit(
                db,
                service,
                document_version_id=snapshot.document_version_id,
                graph_index_run_id=run.graph_index_run_id,
            )
            projection_error_code = _projection_error_code(projection_result)
            if projection_error_code is not None:
                return _failed(projection_error_code)
            result_payload.update(_projection_payload(projection_result))
            return JobHandlerResult.succeeded(result_payload)
        except LeaseLostError:
            db.rollback()
            raise
        except ValueError:
            db.rollback()
            return self._fail_run(
                context,
                service,
                snapshot.graph_index_run_id,
                "graph_relation_validation_failed",
            )
        except Exception:
            db.rollback()
            logger.error(
                "graph index write failed",
                extra={
                    "job_id": context.job_id,
                    "document_version_id": snapshot.document_version_id,
                    "graph_index_run_id": snapshot.graph_index_run_id,
                    "error_code": "graph_index_write_failed",
                },
            )
            return self._fail_run(
                context,
                service,
                snapshot.graph_index_run_id,
                "graph_index_write_failed",
            )
        finally:
            db.close()

    def _project_neo4j_after_commit(
        self,
        db: Session,
        service: GraphIndexService,
        *,
        document_version_id: int,
        graph_index_run_id: int,
    ) -> object | None:
        try:
            return service.project_neo4j_index_run(
                db,
                document_version_id=document_version_id,
                graph_index_run_id=graph_index_run_id,
            )
        except Exception:
            logger.warning(
                "neo4j projection skipped after graph index",
                extra={
                    "document_version_id": document_version_id,
                    "graph_index_run_id": graph_index_run_id,
                    "error_code": "neo4j_projection_failed",
                },
            )
            return None

    def _fail_run(
        self,
        context: JobExecutionContext,
        service: GraphIndexService,
        graph_index_run_id: int,
        error_code: str,
    ) -> JobHandlerResult:
        db = self.session_factory()
        try:
            run = service.repository.get_graph_index_run(
                db,
                graph_index_run_id,
                for_update=True,
            )
            if run is not None and run.status in {"queued", "running"}:
                self.job_repository.assert_ownership(
                    db,
                    job_id=context.job_id,
                    worker_instance_id=context.worker_instance_id,
                )
                service.mark_index_run_failed(
                    db,
                    graph_index_run_id=graph_index_run_id,
                    error_code=error_code,
                    error_message=_SAFE_MESSAGES.get(error_code),
                )
            db.commit()
        except LeaseLostError:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            logger.error(
                "graph index failure state update failed",
                extra={
                    "job_id": context.job_id,
                    "graph_index_run_id": graph_index_run_id,
                    "error_code": "internal_error",
                },
            )
        finally:
            db.close()
        return _failed(error_code)


def _payload(value: object) -> GraphIndexJobPayload | None:
    try:
        return GraphIndexJobPayload.model_validate(value)
    except ValidationError:
        return None


def _failed(error_code: str) -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code=error_code,
        error_message=_SAFE_MESSAGES.get(error_code, _SAFE_MESSAGES["internal_error"]),
    )


def _projection_payload(result: object | None) -> dict[str, object]:
    if result is None:
        return {"neo4j_projection_result_code": "neo4j_projection_failed"}
    if not bool(getattr(result, "enabled", False)):
        return {}
    reason_codes = list(getattr(result, "reason_codes", ()))
    return {
        "neo4j_projection_result_code": str(
            reason_codes[0] if reason_codes else "neo4j_projection_completed"
        ),
        "neo4j_projected_entity_count": int(getattr(result, "projected_entities", 0)),
        "neo4j_projected_relation_count": int(getattr(result, "projected_relations", 0)),
        "neo4j_projected_mention_count": int(getattr(result, "projected_mentions", 0)),
        "neo4j_projected_chunk_count": int(getattr(result, "projected_chunks", 0)),
    }


def _projection_error_code(result: object | None) -> str | None:
    if result is None:
        return "neo4j_projection_failed"
    if not bool(getattr(result, "enabled", False)):
        return None
    reason_codes = list(getattr(result, "reason_codes", ()))
    primary_code = str(reason_codes[0] if reason_codes else "neo4j_projection_failed")
    if primary_code == "neo4j_projection_completed":
        return None
    if primary_code in _SAFE_MESSAGES:
        return primary_code
    return "neo4j_projection_failed"
