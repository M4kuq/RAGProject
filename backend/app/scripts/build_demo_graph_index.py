from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocumentVersion, LogicalDocument
from app.db.session import SessionLocal
from app.services.graph_index_service import GraphIndexService


@dataclass(frozen=True)
class DemoGraphIndexItem:
    document_version_id: int
    graph_index_run_id: int | None
    action: str
    entity_count: int = 0
    relation_count: int = 0
    mention_count: int = 0
    neo4j_projection_result_code: str | None = None
    neo4j_projected_entity_count: int = 0
    neo4j_projected_relation_count: int = 0
    neo4j_projected_mention_count: int = 0
    neo4j_projected_chunk_count: int = 0


@dataclass(frozen=True)
class DemoGraphIndexSummary:
    built_count: int
    would_build_count: int
    item_count: int
    items: tuple[DemoGraphIndexItem, ...]


def build_demo_graph_indexes(
    db: Session,
    *,
    dry_run: bool = False,
    limit: int | None = None,
    service: GraphIndexService | None = None,
) -> DemoGraphIndexSummary:
    graph_service = service or GraphIndexService()
    items: list[DemoGraphIndexItem] = []
    built_count = 0
    would_build_count = 0
    for document_version_id in _active_ready_document_version_ids(db, limit=limit):
        if dry_run:
            items.append(
                DemoGraphIndexItem(
                    document_version_id=document_version_id,
                    graph_index_run_id=None,
                    action="would_build",
                )
            )
            would_build_count += 1
            continue
        snapshot = graph_service.prepare_index_build(
            db,
            document_version_id=document_version_id,
        )
        extraction = graph_service.extract_from_snapshot(snapshot)
        run = graph_service.persist_extraction_result(
            db,
            snapshot=snapshot,
            result=extraction,
        )
        db.commit()
        projection = graph_service.project_neo4j_index_run(
            db,
            document_version_id=document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        projection_codes = list(getattr(projection, "reason_codes", ()))
        items.append(
            DemoGraphIndexItem(
                document_version_id=document_version_id,
                graph_index_run_id=run.graph_index_run_id,
                action="built",
                entity_count=int(run.entity_count or 0),
                relation_count=int(run.relation_count or 0),
                mention_count=int(run.mention_count or 0),
                neo4j_projection_result_code=(
                    str(projection_codes[0])
                    if bool(getattr(projection, "enabled", False)) and projection_codes
                    else None
                ),
                neo4j_projected_entity_count=int(getattr(projection, "projected_entities", 0)),
                neo4j_projected_relation_count=int(getattr(projection, "projected_relations", 0)),
                neo4j_projected_mention_count=int(getattr(projection, "projected_mentions", 0)),
                neo4j_projected_chunk_count=int(getattr(projection, "projected_chunks", 0)),
            )
        )
        built_count += 1
    return DemoGraphIndexSummary(
        built_count=built_count,
        would_build_count=would_build_count,
        item_count=len(items),
        items=tuple(items),
    )


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build PostgreSQL graph indexes for active ready documents and run optional "
            "Neo4j projection without printing raw document text."
        )
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")

    with SessionLocal() as db:
        try:
            summary = build_demo_graph_indexes(db, dry_run=args.dry_run, limit=args.limit)
            if args.dry_run:
                db.rollback()
        except Exception:
            db.rollback()
            raise
        print(json.dumps(asdict(summary), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
