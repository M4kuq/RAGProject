from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.graph_models import GraphEntity, GraphEntityMention, GraphIndexRun, GraphRelation
from app.db.models import DocumentChunk, DocumentVersion, Job, LogicalDocument, Role, User
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE
from app.graph.extraction import EntityMentionCandidate, GraphExtractionResult, RelationCandidate
from app.repositories.job_repository import JobRepository
from app.services.graph_index_service import GraphIndexBuildSnapshot, GraphIndexService
from app.workers.handlers.graph_index_build_handler import GraphIndexBuildHandler
from app.workers.job_dispatcher import JobDispatcher
from app.workers.worker_config import WorkerConfig, parse_enabled_job_types
from app.workers.worker_main import WorkerRunner


@pytest.fixture
def graph_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_graph_index_build_persists_safe_rows_and_rebuilds_idempotently(
    graph_session_factory: sessionmaker[Session],
) -> None:
    service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            [
                "Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant.",
                "GraphIndexService connects Graph Repository. "
                "Contact admin@example.com must not be indexed.",
            ],
        )
        first_run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        first_snapshot = service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=first_run.graph_index_run_id,
        )
        first_result = service.extract_from_snapshot(first_snapshot)
        service.persist_extraction_result(db, snapshot=first_snapshot, result=first_result)
        db.commit()

        first_counts = _graph_counts(db, version.document_version_id)
        assert first_counts["entities"] >= 4
        assert first_counts["mentions"] >= 4
        assert first_counts["relations"] >= 2
        assert first_run.status == "succeeded"
        assert first_run.entity_count == first_counts["entities"]
        assert first_run.mention_count == first_counts["mentions"]
        assert first_run.relation_count == first_counts["relations"]

        entity_names = {row.canonical_name for row in db.scalars(select(GraphEntity)).all()}
        assert {"Graph Index", "Hybrid RAG", "Qdrant", "GraphIndexService"} <= entity_names
        assert "admin@example.com" not in entity_names

        for row in db.scalars(select(GraphEntity)).all():
            assert _metadata_is_safe(row.metadata_json)
        for row in db.scalars(select(GraphEntityMention)).all():
            assert row.mention_text_hash is not None
            assert row.mention_offset_start is not None
            assert row.mention_offset_end is not None
            assert _metadata_is_safe(row.metadata_json)
        for row in db.scalars(select(GraphRelation)).all():
            assert row.source_document_chunk_id is not None
            assert row.evidence_text_hash is not None
            assert _metadata_is_safe(row.metadata_json)

        second_run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        second_snapshot = service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=second_run.graph_index_run_id,
        )
        second_result = service.extract_from_snapshot(second_snapshot)
        service.persist_extraction_result(db, snapshot=second_snapshot, result=second_result)
        db.commit()

        assert _graph_counts(db, version.document_version_id) == first_counts
        assert second_run.status == "succeeded"
        assert len(db.scalars(select(GraphIndexRun)).all()) == 2


def test_graph_index_build_worker_is_registered_and_succeeds(
    graph_session_factory: sessionmaker[Session],
) -> None:
    assert parse_enabled_job_types(GRAPH_INDEX_BUILD_JOB_TYPE) == frozenset(
        {GRAPH_INDEX_BUILD_JOB_TYPE}
    )
    assert GRAPH_INDEX_BUILD_JOB_TYPE in JobDispatcher().supported_job_types

    job_repository = JobRepository()
    service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json=service.build_graph_index_job_payload(
                document_version_id=version.document_version_id,
                graph_index_run_id=run.graph_index_run_id,
                extractor_type="rule_based",
            ),
        )
        db.commit()
        job_id = job.job_id
        run_id = run.graph_index_run_id

    runner = WorkerRunner(
        config=_worker_config(enabled_job_types=frozenset({GRAPH_INDEX_BUILD_JOB_TYPE})),
        session_factory=graph_session_factory,
    )
    assert runner.run_once() == 1

    with graph_session_factory() as db:
        stored_job = db.get(Job, job_id)
        stored_run = db.get(GraphIndexRun, run_id)
        assert stored_job is not None
        assert stored_run is not None
        assert stored_job.status == "succeeded"
        assert stored_job.result_json is not None
        assert stored_job.result_json["graph_index_run_id"] == run_id
        assert stored_job.result_json["entity_count"] == stored_run.entity_count
        assert stored_run.status == "succeeded"
        assert stored_run.mention_count > 0


def test_graph_index_build_worker_retries_failed_run_with_new_run(
    graph_session_factory: sessionmaker[Session],
) -> None:
    job_repository = JobRepository()
    service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        failed_run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        service.mark_index_run_failed(
            db,
            graph_index_run_id=failed_run.graph_index_run_id,
            error_code="graph_extraction_failed",
            error_message="unsafe raw chunk text",
        )
        job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json=service.build_graph_index_job_payload(
                document_version_id=version.document_version_id,
                graph_index_run_id=failed_run.graph_index_run_id,
            ),
        )
        db.commit()
        job_id = job.job_id
        failed_run_id = failed_run.graph_index_run_id

    runner = WorkerRunner(
        config=_worker_config(enabled_job_types=frozenset({GRAPH_INDEX_BUILD_JOB_TYPE})),
        session_factory=graph_session_factory,
    )
    assert runner.run_once() == 1

    with graph_session_factory() as db:
        stored_job = db.get(Job, job_id)
        runs = list(
            db.scalars(
                select(GraphIndexRun).order_by(GraphIndexRun.graph_index_run_id.asc())
            ).all()
        )
        assert stored_job is not None
        assert stored_job.status == "succeeded"
        assert len(runs) == 2
        assert runs[0].graph_index_run_id == failed_run_id
        assert runs[0].status == "failed"
        assert runs[1].status == "succeeded"
        assert stored_job.result_json is not None
        assert stored_job.result_json["graph_index_run_id"] == runs[1].graph_index_run_id


def test_graph_index_build_failure_marks_run_failed_without_raw_text(
    graph_session_factory: sessionmaker[Session],
) -> None:
    class FailingGraphIndexService(GraphIndexService):
        def extract_from_snapshot(self, snapshot: GraphIndexBuildSnapshot) -> GraphExtractionResult:
            del snapshot
            raise RuntimeError("raw chunk text and secret=value must not leak")

    job_repository = JobRepository()
    service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(db, ["Graph Index supports Hybrid RAG."])
        run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json=service.build_graph_index_job_payload(
                document_version_id=version.document_version_id,
                graph_index_run_id=run.graph_index_run_id,
            ),
        )
        db.commit()
        job_id = job.job_id
        run_id = run.graph_index_run_id

    dispatcher = JobDispatcher(
        {
            GRAPH_INDEX_BUILD_JOB_TYPE: GraphIndexBuildHandler(
                session_factory=graph_session_factory,
                service_factory=FailingGraphIndexService,
            )
        }
    )
    runner = WorkerRunner(
        config=_worker_config(enabled_job_types=frozenset({GRAPH_INDEX_BUILD_JOB_TYPE})),
        session_factory=graph_session_factory,
        dispatcher=dispatcher,
    )
    assert runner.run_once() == 1

    with graph_session_factory() as db:
        stored_job = db.get(Job, job_id)
        stored_run = db.get(GraphIndexRun, run_id)
        assert stored_job is not None
        assert stored_run is not None
        assert stored_job.status == "failed"
        assert stored_job.error_code == "graph_extraction_failed"
        assert stored_job.error_message == "Graph extraction failed."
        assert "raw chunk" not in (stored_job.error_message or "")
        assert stored_run.status == "failed"
        assert stored_run.error_code == "graph_extraction_failed"
        assert stored_run.error_message == "Graph extraction failed."


def test_graph_index_build_rejects_candidates_outside_snapshot(
    graph_session_factory: sessionmaker[Session],
) -> None:
    service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(db, ["Graph Index supports Hybrid RAG."])
        run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        snapshot = service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        db.commit()

        chunk = snapshot.chunks[0]
        mention = EntityMentionCandidate(
            canonical_name="Graph Index",
            entity_type="concept",
            aliases=(),
            document_chunk_id=chunk.document_chunk_id,
            document_version_id=chunk.document_version_id,
            chunk_index=chunk.chunk_index,
            mention_text_hash="a" * 64,
            mention_offset_start=0,
            mention_offset_end=11,
            confidence=Decimal("0.80000"),
            metadata_json={"rule_id": "test"},
        )
        result = GraphExtractionResult(
            entity_mentions=(mention,),
            relations=(
                RelationCandidate(
                    source_key=mention.entity_key,
                    target_key=("hybrid rag", "technology"),
                    relation_type="supports",
                    relation_label="supports",
                    confidence=Decimal("0.70000"),
                    source_document_chunk_id=999999,
                    evidence_text_hash="b" * 64,
                    metadata_json={"rule_id": "test"},
                ),
            ),
        )

        with pytest.raises(ValueError):
            service.persist_extraction_result(db, snapshot=snapshot, result=result)
        db.rollback()

        assert _graph_counts(db, version.document_version_id) == {
            "entities": 0,
            "mentions": 0,
            "relations": 0,
        }


def _seed_ready_version(db: Session, chunk_texts: list[str]) -> DocumentVersion:
    role = Role(role_name=f"role-{uuid.uuid4().hex[:8]}", description="Graph test")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email=f"graph-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Graph Test",
        status="active",
    )
    db.add(user)
    db.flush()
    logical = LogicalDocument(owner_user_id=user.user_id, title="Graph Test", status="active")
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash="1".zfill(64),
        status="ready",
        is_active=True,
        file_name="graph-test.txt",
        mime_type="text/plain",
        file_size_bytes=sum(len(text) for text in chunk_texts),
        created_by=user.user_id,
    )
    db.add(version)
    db.flush()
    for index, text in enumerate(chunk_texts):
        db.add(
            DocumentChunk(
                document_version_id=version.document_version_id,
                chunk_index=index,
                chunk_hash=f"{index + 100:064x}"[-64:],
                content_text=text,
                char_count=len(text),
                modality="text",
            )
        )
    db.flush()
    return version


def _graph_counts(db: Session, document_version_id: int) -> dict[str, int]:
    chunk_ids = [
        row.document_chunk_id
        for row in db.scalars(
            select(DocumentChunk).where(DocumentChunk.document_version_id == document_version_id)
        ).all()
    ]
    entity_ids = {
        row.graph_entity_id
        for row in db.scalars(
            select(GraphEntityMention).where(
                GraphEntityMention.document_version_id == document_version_id
            )
        ).all()
    }
    return {
        "entities": len(entity_ids),
        "mentions": len(
            db.scalars(
                select(GraphEntityMention).where(
                    GraphEntityMention.document_version_id == document_version_id
                )
            ).all()
        ),
        "relations": len(
            db.scalars(
                select(GraphRelation).where(GraphRelation.source_document_chunk_id.in_(chunk_ids))
            ).all()
        ),
    }


def _metadata_is_safe(value: dict[str, object]) -> bool:
    serialized = str(value).lower()
    forbidden = ("raw_chunk_text", "raw document", "chunk_text", "evidence_text", "mention_text")
    return all(item not in serialized for item in forbidden)


def _worker_config(*, enabled_job_types: frozenset[str] | None) -> WorkerConfig:
    return WorkerConfig(
        poll_interval_seconds=0,
        batch_size=1,
        lease_duration=timedelta(minutes=5),
        lease_renew_interval_seconds=60,
        shutdown_grace_seconds=30,
        enabled_job_types=enabled_job_types,
        worker_instance_id="worker-1",
    )
