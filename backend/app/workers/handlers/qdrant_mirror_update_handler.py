from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.job_utils import LeaseLostError
from app.db.session import SessionLocal
from app.ingest.qdrant import (
    DocumentIndexingService,
    QdrantStoreError,
    create_document_indexing_service,
)
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult

_SAFE_MESSAGES = {
    "validation_error": "Job payload is invalid.",
    "document_version_not_found": "Document version was not found.",
    "qdrant_upsert_failed": "Qdrant payload sync failed.",
    "internal_error": "Qdrant mirror update failed.",
}

_ACTIONS = {"sync_payload", "mark_inactive"}


@dataclass(frozen=True)
class _VersionMirrorSnapshot:
    document_version_id: int
    logical_document_status: str
    document_version_status: str
    is_active: bool
    chunk_count: int


class QdrantMirrorUpdateHandler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session] = SessionLocal,
        repository: DocumentRepository | None = None,
        job_repository: JobRepository | None = None,
        settings: Settings | None = None,
        indexing_service: DocumentIndexingService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.repository = repository or DocumentRepository()
        self.job_repository = job_repository or JobRepository()
        self.settings = settings or get_settings()
        self.indexing_service = indexing_service or create_document_indexing_service(self.settings)

    def handle(self, context: JobExecutionContext) -> JobHandlerResult:
        action = context.payload.get("mirror_action")
        logical_document_id = context.payload.get("logical_document_id")
        document_version_id = context.payload.get("document_version_id")
        if (
            action not in _ACTIONS
            or not _is_positive_int(logical_document_id)
            or (document_version_id is not None and not _is_positive_int(document_version_id))
            or context.target_type != "logical_document"
            or context.target_id != logical_document_id
        ):
            return _failed("validation_error")
        logical_document_id = cast(int, logical_document_id)
        document_version_id = cast(int | None, document_version_id)

        snapshots_or_result = self._load_snapshots(
            context,
            logical_document_id=logical_document_id,
            document_version_id=document_version_id,
        )
        if isinstance(snapshots_or_result, JobHandlerResult):
            return snapshots_or_result

        synced_count = 0
        try:
            for snapshot in snapshots_or_result:
                if snapshot.chunk_count == 0:
                    continue
                self.indexing_service.sync_document_payload(
                    document_version_id=snapshot.document_version_id,
                    logical_document_status=snapshot.logical_document_status,
                    document_version_status=snapshot.document_version_status,
                    is_active=snapshot.is_active,
                )
                synced_count += 1
        except QdrantStoreError as exc:
            return _failed(exc.error_code)
        except Exception:
            return _failed("internal_error")

        return JobHandlerResult.succeeded(
            {
                "logical_document_id": logical_document_id,
                "mirror_action": action,
                "synced_version_count": synced_count,
            }
        )

    def _load_snapshots(
        self,
        context: JobExecutionContext,
        *,
        logical_document_id: int,
        document_version_id: int | None,
    ) -> list[_VersionMirrorSnapshot] | JobHandlerResult:
        db = self.session_factory()
        try:
            document = self.repository.get_document(db, logical_document_id=logical_document_id)
            if document is None:
                return _failed("document_version_not_found")
            self.job_repository.assert_ownership(
                db,
                job_id=context.job_id,
                worker_instance_id=context.worker_instance_id,
            )
            versions, _ = self.repository.list_versions(
                db,
                logical_document_id=logical_document_id,
                pagination=None,
            )
            if document_version_id is not None and all(
                version.document_version_id != document_version_id for version in versions
            ):
                return _failed("document_version_not_found")
            return [
                _VersionMirrorSnapshot(
                    document_version_id=version.document_version_id,
                    logical_document_status=document.status,
                    document_version_status=version.status,
                    is_active=version.is_active,
                    chunk_count=self.repository.count_chunks(
                        db,
                        document_version_id=version.document_version_id,
                    ),
                )
                for version in versions
            ]
        except LeaseLostError:
            raise
        except Exception:
            return _failed("internal_error")
        finally:
            db.close()


def _failed(error_code: str) -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code=error_code,
        error_message=_SAFE_MESSAGES.get(error_code, _SAFE_MESSAGES["internal_error"]),
    )


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
