from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentVersion, Job, LogicalDocument
from app.db.session import SessionLocal
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE
from app.graph.job_settings import graph_extractor_type_override
from app.repositories.job_repository import JobRepository
from app.services.graph_index_service import GraphIndexService


@dataclass(frozen=True)
class GraphIndexQueueItem:
    document_version_id: int
    job_id: int | None
    action: str


@dataclass(frozen=True)
class GraphIndexQueueSummary:
    queued_count: int
    would_queue_count: int
    skipped_count: int
    items: tuple[GraphIndexQueueItem, ...]


def queue_graph_index_build_jobs(
    db: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    job_repository: JobRepository | None = None,
    graph_index_service: GraphIndexService | None = None,
) -> GraphIndexQueueSummary:
    job_repo = job_repository or JobRepository()
    graph_service = graph_index_service or GraphIndexService()
    items: list[GraphIndexQueueItem] = []
    queued_count = 0
    would_queue_count = 0
    skipped_count = 0

    for document_version_id in _active_ready_document_version_ids(db, limit=limit):
        if _has_active_graph_index_job(db, document_version_id=document_version_id):
            items.append(
                GraphIndexQueueItem(
                    document_version_id=document_version_id,
                    job_id=None,
                    action="skipped_active_job",
                )
            )
            skipped_count += 1
            continue
        if dry_run:
            items.append(
                GraphIndexQueueItem(
                    document_version_id=document_version_id,
                    job_id=None,
                    action="would_queue",
                )
            )
            would_queue_count += 1
            continue
        job = job_repo.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=document_version_id,
            payload_json=graph_service.build_graph_index_job_payload(
                document_version_id=document_version_id,
                extractor_type=graph_extractor_type_override(db),
            ),
            priority=80,
        )
        items.append(
            GraphIndexQueueItem(
                document_version_id=document_version_id,
                job_id=job.job_id,
                action="queued",
            )
        )
        queued_count += 1

    return GraphIndexQueueSummary(
        queued_count=queued_count,
        would_queue_count=would_queue_count,
        skipped_count=skipped_count,
        items=tuple(items),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Queue safe graph_index_build jobs for active ready document versions."
    )
    parser.add_argument("--dry-run", action="store_true", help="Report jobs without queueing them.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum document versions to inspect.",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")

    with SessionLocal() as db:
        summary = queue_graph_index_build_jobs(db, dry_run=args.dry_run, limit=args.limit)
        if args.dry_run:
            db.rollback()
        else:
            db.commit()
        print(json.dumps(_summary_payload(summary), sort_keys=True))
    return 0


def _active_ready_document_version_ids(db: Session, *, limit: int | None) -> list[int]:
    statement = (
        select(DocumentVersion.document_version_id)
        .join(
            LogicalDocument,
            LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
        )
        .where(
            LogicalDocument.status == "active",
            DocumentVersion.status == "ready",
            DocumentVersion.is_active.is_(True),
        )
        .order_by(DocumentVersion.document_version_id.asc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.scalars(statement).all())


def _has_active_graph_index_job(db: Session, *, document_version_id: int) -> bool:
    return (
        db.scalar(
            select(Job.job_id)
            .where(
                Job.job_type == GRAPH_INDEX_BUILD_JOB_TYPE,
                Job.target_type == "document_version",
                Job.target_id == document_version_id,
                Job.status.in_(("queued", "running")),
            )
            .limit(1)
        )
        is not None
    )


def _summary_payload(summary: GraphIndexQueueSummary) -> dict[str, object]:
    return {
        "queued_count": summary.queued_count,
        "would_queue_count": summary.would_queue_count,
        "skipped_count": summary.skipped_count,
        "items": [asdict(item) for item in summary.items],
    }


if __name__ == "__main__":
    raise SystemExit(main())
