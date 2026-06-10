from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import ResourceNotFound
from app.db.graph_models import GraphIndexRun
from app.db.models import DocumentVersion
from app.graph.constants import DEFAULT_GRAPH_EXTRACTOR_TYPE, GRAPH_INDEX_BUILD_JOB_TYPE
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph import GraphIndexJobPayload, GraphIndexRunCreate, GraphIndexSummary


class GraphIndexService:
    """Lifecycle skeleton for graph index runs.

    PR-46 intentionally does not perform entity/relation extraction. PR-47 should connect an
    extractor implementation to this lifecycle and keep raw evidence text out of persisted state.
    """

    def __init__(self, repository: GraphRepository | None = None) -> None:
        self.repository = repository or GraphRepository()

    def create_index_run_for_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        job_id: int | None = None,
        extractor_type: str = DEFAULT_GRAPH_EXTRACTOR_TYPE,
        extractor_version: str | None = None,
        metadata_json: dict[str, object] | None = None,
    ) -> GraphIndexRun:
        version = db.get(DocumentVersion, document_version_id)
        if version is None:
            raise ResourceNotFound()
        if version.status != "ready":
            raise ValueError("document_version_id must reference a ready document version")
        return self.repository.create_graph_index_run(
            db,
            GraphIndexRunCreate(
                document_version_id=document_version_id,
                job_id=job_id,
                extractor_type=extractor_type,
                extractor_version=extractor_version,
                metadata_json=metadata_json or {},
            ),
        )

    def build_graph_index_job_payload(
        self,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
    ) -> dict[str, object]:
        return GraphIndexJobPayload(
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            document_version_id=document_version_id,
            graph_index_run_id=graph_index_run_id,
        ).model_dump(exclude_none=True)

    def mark_index_run_running(self, db: Session, *, graph_index_run_id: int) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
        if run is None:
            raise ResourceNotFound()
        self.repository.mark_graph_index_run_running(db, run=run)
        return run

    def record_index_summary(
        self,
        db: Session,
        *,
        graph_index_run_id: int,
        summary: GraphIndexSummary,
    ) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
        if run is None:
            raise ResourceNotFound()
        self.repository.mark_graph_index_run_succeeded(
            db,
            run=run,
            entity_count=summary.entity_count,
            relation_count=summary.relation_count,
            mention_count=summary.mention_count,
        )
        return run

    def mark_index_run_failed(
        self,
        db: Session,
        *,
        graph_index_run_id: int,
        error_code: str,
        error_message: str | None = None,
    ) -> GraphIndexRun:
        run = self.repository.get_graph_index_run(db, graph_index_run_id, for_update=True)
        if run is None:
            raise ResourceNotFound()
        self.repository.mark_graph_index_run_failed(
            db,
            run=run,
            error_code=error_code,
            error_message=error_message,
        )
        return run
