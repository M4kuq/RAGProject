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
    ScrolledPoint,
    create_document_indexing_service,
)
from app.repositories.document_repository import DocumentRepository
from app.repositories.job_repository import JobRepository
from app.workers.handlers.base import JobExecutionContext, JobHandlerResult

_SAFE_MESSAGES = {
    "validation_error": "Job payload is invalid.",
    "qdrant_unavailable": "Qdrant is unavailable.",
    "qdrant_upsert_failed": "Qdrant payload sync failed.",
    "qdrant_cleanup_failed": "Qdrant cleanup failed.",
    "internal_error": "Qdrant consistency sweep failed.",
}

_DEFAULT_BATCH_SIZE = 200
_MAX_BATCH_SIZE = 1000
_DEFAULT_MAX_POINTS = 5000
_MAX_MAX_POINTS = 100_000


@dataclass
class _SweepCounts:
    scanned: int = 0
    stale_found: int = 0
    repaired: int = 0
    skipped: int = 0


class QdrantConsistencySweepHandler:
    """Reconcile Qdrant points against Postgres source-of-truth state.

    The sweep scrolls points in the configured collection and, for each point,
    compares its payload (``document_chunk_id`` / ``document_version_id`` /
    ``is_active``) against Postgres. Points whose chunk row is missing or whose
    version no longer exists are orphans and get deleted. Points whose chunk
    belongs to a different version than the payload claims have corrupted
    metadata and are repaired (set inactive), not deleted. Points whose version
    is archived/inactive in Postgres but still flagged active in Qdrant are
    repaired by setting ``is_active=False``. Repairs reuse the indexing
    service's delete / set-payload helpers rather than raw client calls.

    This job is **manually enqueued** (by ops scripts or an operator) in
    Phase 1. It is intentionally NOT scheduled automatically.

    Payload params:
        ``batch_size`` (default 200, max 1000): scroll page size.
        ``max_points`` (default 5000, max 100000): safety cap on points
        scanned per run.
    """

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
        params = self._validate_payload(context)
        if isinstance(params, JobHandlerResult):
            return params
        batch_size, max_points = params

        counts = _SweepCounts()
        offset: int | str | None = None
        try:
            while counts.scanned < max_points:
                remaining = max_points - counts.scanned
                limit = min(batch_size, remaining)
                result = self.indexing_service.scroll_points(limit=limit, offset=offset)
                if not result.points:
                    break
                self._process_batch(result.points, counts)
                offset = result.next_offset
                if offset is None:
                    break
        except LeaseLostError:
            raise
        except QdrantStoreError as exc:
            return _failed(exc.error_code)
        except Exception:
            return _failed("internal_error")

        return JobHandlerResult.succeeded(
            {
                "scanned": counts.scanned,
                "stale_found": counts.stale_found,
                "repaired": counts.repaired,
                "skipped": counts.skipped,
            }
        )

    def _process_batch(self, points: list[ScrolledPoint], counts: _SweepCounts) -> None:
        chunk_ids: set[int] = set()
        version_ids: set[int] = set()
        for point in points:
            counts.scanned += 1
            chunk_id = _payload_positive_int(point.payload, "document_chunk_id")
            version_id = _payload_positive_int(point.payload, "document_version_id")
            if chunk_id is None or version_id is None:
                counts.skipped += 1
                continue
            chunk_ids.add(chunk_id)
            version_ids.add(version_id)

        db = self.session_factory()
        try:
            chunk_version_ids = self.repository.chunk_version_ids(
                db, document_chunk_ids=list(chunk_ids)
            )
            version_states = self.repository.version_index_states(
                db, document_version_ids=list(version_ids)
            )
        finally:
            db.close()

        delete_ids: list[int] = []
        repair_ids: list[int] = []
        for point in points:
            chunk_id = _payload_positive_int(point.payload, "document_chunk_id")
            version_id = _payload_positive_int(point.payload, "document_version_id")
            if chunk_id is None or version_id is None:
                continue
            if chunk_id not in chunk_version_ids or version_id not in version_states:
                delete_ids.append(point.point_id)
                continue
            if chunk_version_ids[chunk_id] != version_id:
                # The chunk exists but belongs to a different version than the
                # payload claims: corrupted payload metadata. Mark it inactive
                # (a reversible repair) rather than delete -- the underlying
                # vector may still be valid for its real version, and deleting
                # would permanently lose it until re-ingest.
                if bool(point.payload.get("is_active")):
                    repair_ids.append(point.point_id)
                else:
                    counts.stale_found += 1
                continue
            version_status, is_active, document_status = version_states[version_id]
            should_be_inactive = (
                document_status == "archived" or version_status == "archived" or not is_active
            )
            point_is_active = bool(point.payload.get("is_active"))
            if should_be_inactive and point_is_active:
                repair_ids.append(point.point_id)

        counts.stale_found += len(delete_ids) + len(repair_ids)
        if delete_ids:
            self.indexing_service.delete_points_by_ids(point_ids=delete_ids)
            counts.repaired += len(delete_ids)
        if repair_ids:
            self.indexing_service.mark_points_inactive(point_ids=repair_ids)
            counts.repaired += len(repair_ids)

    def _validate_payload(self, context: JobExecutionContext) -> tuple[int, int] | JobHandlerResult:
        if context.job_type != "qdrant_consistency_sweep":
            return _failed("validation_error")
        batch_size = self._bounded_int(
            context.payload.get("batch_size"),
            default=_DEFAULT_BATCH_SIZE,
            maximum=_MAX_BATCH_SIZE,
        )
        max_points = self._bounded_int(
            context.payload.get("max_points"),
            default=_DEFAULT_MAX_POINTS,
            maximum=_MAX_MAX_POINTS,
        )
        if batch_size is None or max_points is None:
            return _failed("validation_error")
        return batch_size, max_points

    def _bounded_int(self, value: object, *, default: int, maximum: int) -> int | None:
        if value is None:
            return default
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            return None
        return min(value, maximum)


def _failed(error_code: str) -> JobHandlerResult:
    return JobHandlerResult.failed(
        error_code=error_code,
        error_message=_SAFE_MESSAGES.get(error_code, _SAFE_MESSAGES["internal_error"]),
    )


def _payload_positive_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return cast(int, value)
