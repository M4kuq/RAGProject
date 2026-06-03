from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.errors import ResourceNotFound
from app.db import graph_models  # noqa: F401
from app.db.base import Base
from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphIndexRun,
    GraphRelation,
    GraphRetrievalPath,
)
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument, RetrievalRun, Role, User
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE, PHASE3_GRAPH_SYSTEM_SETTINGS
from app.repositories.graph_repository import GraphRepository
from app.schemas.graph import (
    GraphEntityCreate,
    GraphEntityMentionCreate,
    GraphIndexJobPayload,
    GraphIndexRunCreate,
    GraphIndexSummary,
    GraphRelationCreate,
    GraphRetrievalPathCreate,
)
from app.services.graph_index_service import GraphIndexService
from app.workers.worker_config import WorkerConfigError, parse_enabled_job_types

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


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


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("PostgreSQL graph schema assertions require a PostgreSQL DATABASE_URL")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        engine.dispose()
        pytest.skip("PostgreSQL graph schema assertions require a reachable database")
    yield engine
    engine.dispose()


def test_graph_orm_tables_do_not_store_raw_text_columns() -> None:
    tables = [
        GraphEntity.__table__,
        GraphRelation.__table__,
        GraphEntityMention.__table__,
        GraphIndexRun.__table__,
        GraphRetrievalPath.__table__,
    ]
    forbidden_exact = {
        "raw_document_text",
        "raw_chunk_text",
        "content_text",
        "raw_prompt",
        "full_context",
        "pii",
        "secret",
        "token",
    }
    for table in tables:
        assert forbidden_exact.isdisjoint(table.columns.keys())
    assert "evidence_text_hash" in GraphRelation.__table__.columns
    assert "mention_text_hash" in GraphEntityMention.__table__.columns
    assert "source_document_chunk_id" in GraphRelation.__table__.columns
    assert "source_chunk_ids_json" in GraphRetrievalPath.__table__.columns


def test_graph_repository_and_service_lifecycle(
    graph_session_factory: sessionmaker[Session],
) -> None:
    repository = GraphRepository()
    service = GraphIndexService(repository)
    with graph_session_factory() as db:
        version, chunk = _seed_document_version_and_chunk(db)
        retrieval_run = RetrievalRun(status="running", started_at=datetime.now(UTC), top_k=5)
        db.add(retrieval_run)
        db.flush()

        source = repository.create_entity(
            db,
            GraphEntityCreate(
                canonical_name="Graph Schema",
                entity_type="concept",
                aliases_json=["schema"],
                metadata_json={"source_ref_count": 1},
            ),
        )
        target = repository.create_entity(
            db,
            GraphEntityCreate(canonical_name="Graph Index", entity_type="concept"),
        )
        assert repository.find_entity_by_canonical_name(
            db, canonical_name="graph schema", entity_type="concept"
        ).graph_entity_id == source.graph_entity_id

        relation = repository.create_relation(
            db,
            GraphRelationCreate(
                source_entity_id=source.graph_entity_id,
                target_entity_id=target.graph_entity_id,
                relation_type="supports",
                relation_label="supports",
                confidence=Decimal("0.80000"),
                source_document_chunk_id=chunk.document_chunk_id,
                evidence_text_hash=HASH_A,
                metadata_json={"source_ref_count": 1},
            ),
        )
        assert relation.evidence_text_hash == HASH_A
        assert len(repository.list_relations_for_entity(db, graph_entity_id=source.graph_entity_id)) == 1

        mention = repository.create_entity_mention(
            db,
            GraphEntityMentionCreate(
                graph_entity_id=source.graph_entity_id,
                document_chunk_id=chunk.document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash=HASH_B,
                mention_offset_start=0,
                mention_offset_end=5,
                confidence=Decimal("0.70000"),
            ),
        )
        assert mention.mention_text_hash == HASH_B

        run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
            extractor_version="pr46-skeleton",
        )
        payload = service.build_graph_index_job_payload(
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        assert payload == {
            "job_type": GRAPH_INDEX_BUILD_JOB_TYPE,
            "document_version_id": version.document_version_id,
            "graph_index_run_id": run.graph_index_run_id,
        }

        service.mark_index_run_running(db, graph_index_run_id=run.graph_index_run_id)
        service.record_index_summary(
            db,
            graph_index_run_id=run.graph_index_run_id,
            summary=GraphIndexSummary(entity_count=1, relation_count=1, mention_count=1),
        )
        assert run.status == "succeeded"
        assert run.entity_count == 1
        assert run.relation_count == 1
        assert run.mention_count == 1

        failed = repository.create_graph_index_run(
            db,
            GraphIndexRunCreate(document_version_id=version.document_version_id),
        )
        service.mark_index_run_failed(
            db,
            graph_index_run_id=failed.graph_index_run_id,
            error_code="extractor_failed",
            error_message="raw chunk text appeared in an unsafe failure",
        )
        assert failed.status == "failed"
        assert failed.error_message == "Job failed with a redacted error."

        path = repository.create_graph_retrieval_path(
            db,
            GraphRetrievalPathCreate(
                retrieval_run_id=retrieval_run.retrieval_run_id,
                path_json={
                    "path_ref": "p1",
                    "entity_ids": [source.graph_entity_id, target.graph_entity_id],
                    "relation_ids": [relation.graph_relation_id],
                },
                score_breakdown_json={"path_score": 0.9},
                source_chunk_ids_json=[chunk.document_chunk_id],
            ),
        )
        assert path.source_chunk_ids_json == [chunk.document_chunk_id]
        assert repository.list_graph_retrieval_paths_by_retrieval_run(
            db, retrieval_run_id=retrieval_run.retrieval_run_id
        ) == [path]


def test_graph_schemas_reject_raw_text_and_invalid_hashes() -> None:
    with pytest.raises(ValidationError):
        GraphRelationCreate(
            source_entity_id=1,
            target_entity_id=2,
            relation_type="supports",
            evidence_text_hash="not-a-hash",
        )
    with pytest.raises(ValidationError):
        GraphEntityMentionCreate(
            graph_entity_id=1,
            document_chunk_id=1,
            document_version_id=1,
            mention_offset_start=9,
            mention_offset_end=1,
        )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            metadata_json={"raw_chunk_text": "do not store this"},
        )
    assert GraphIndexJobPayload(document_version_id=1).job_type == GRAPH_INDEX_BUILD_JOB_TYPE


def test_graph_index_service_missing_version_raises(
    graph_session_factory: sessionmaker[Session],
) -> None:
    service = GraphIndexService()
    with graph_session_factory() as db:
        with pytest.raises(ResourceNotFound):
            service.create_index_run_for_document_version(db, document_version_id=999)


def test_graph_job_type_is_future_skeleton_not_worker_enabled_by_default() -> None:
    assert GRAPH_INDEX_BUILD_JOB_TYPE == "graph_index_build"
    with pytest.raises(WorkerConfigError):
        parse_enabled_job_types(GRAPH_INDEX_BUILD_JOB_TYPE)


def test_graph_system_settings_defaults_are_safe() -> None:
    values = {key: value for key, (value, _) in PHASE3_GRAPH_SYSTEM_SETTINGS.items()}
    assert values["rag.graph.enabled"] is False
    assert values["rag.graph.indexing.enabled"] is False
    assert values["rag.graph.extractor.default"] == "none"
    assert values["rag.graph.max_entities_per_chunk"] == 20
    assert values["rag.graph.max_relations_per_chunk"] == 40
    assert values["rag.graph.store_raw_evidence_text"] is False
    assert values["rag.graph.retrieval.enabled"] is False


def test_graph_postgres_schema_constraints_indexes_and_seed_settings(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0012_graph_schema_index"

    expected_tables = {
        "graph_entities",
        "graph_relations",
        "graph_entity_mentions",
        "graph_index_runs",
        "graph_retrieval_paths",
    }
    actual_tables = _scalar_set(
        pg_engine,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """,
    )
    assert expected_tables <= actual_tables

    expected_constraints = {
        "ck_graph_entities_name",
        "ck_graph_entities_type",
        "ck_graph_relations_no_self",
        "ck_graph_relations_confidence",
        "ck_graph_relations_evidence_hash",
        "ck_graph_entity_mentions_hash",
        "ck_graph_entity_mentions_offset_order",
        "ck_graph_index_runs_status",
        "ck_graph_index_runs_entity_count",
        "ck_graph_index_runs_failed_error_code",
        "ck_graph_paths_path_object",
    }
    actual_constraints = _scalar_set(
        pg_engine,
        """
        SELECT conname
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        """,
    )
    assert expected_constraints <= actual_constraints

    expected_indexes = {
        "ux_graph_entities_lower_name_type",
        "ix_graph_entities_entity_type",
        "ix_graph_entities_aliases_json",
        "ix_graph_relations_source_type",
        "ix_graph_relations_target_type",
        "ix_graph_relations_source_chunk",
        "ix_graph_entity_mentions_entity",
        "ix_graph_entity_mentions_chunk",
        "ix_graph_entity_mentions_version",
        "ix_graph_index_runs_document_status",
        "ix_graph_retrieval_paths_retrieval_run",
    }
    actual_indexes = _scalar_set(
        pg_engine,
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'",
    )
    assert expected_indexes <= actual_indexes

    for setting_key, (expected, _) in PHASE3_GRAPH_SYSTEM_SETTINGS.items():
        with pg_engine.connect() as conn:
            value = conn.execute(
                text("SELECT setting_value FROM system_settings WHERE setting_key = :setting_key"),
                {"setting_key": setting_key},
            ).scalar_one()
        assert value == expected


def test_graph_postgres_constraints_reject_invalid_values(pg_engine: Engine) -> None:
    suffix = uuid.uuid4().hex
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            source_id = conn.execute(
                text(
                    """
                    INSERT INTO graph_entities (canonical_name, entity_type)
                    VALUES (:canonical_name, 'concept')
                    RETURNING graph_entity_id
                    """
                ),
                {"canonical_name": f"source-{suffix}"},
            ).scalar_one()
            target_id = conn.execute(
                text(
                    """
                    INSERT INTO graph_entities (canonical_name, entity_type)
                    VALUES (:canonical_name, 'concept')
                    RETURNING graph_entity_id
                    """
                ),
                {"canonical_name": f"target-{suffix}"},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO graph_relations (
                        source_entity_id, target_entity_id, relation_type, confidence,
                        evidence_text_hash
                    )
                    VALUES (:source_id, :target_id, 'supports', 0.5, :hash)
                    """
                ),
                {"source_id": source_id, "target_id": target_id, "hash": HASH_C},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO graph_relations (
                            source_entity_id, target_entity_id, relation_type, confidence
                        )
                        VALUES (:source_id, :target_id, 'supports', 2.0)
                        """
                    ),
                    {"source_id": source_id, "target_id": target_id},
                )
        finally:
            transaction.rollback()


def _seed_document_version_and_chunk(db: Session) -> tuple[DocumentVersion, DocumentChunk]:
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
        content_hash=HASH_A,
        status="ready",
        is_active=True,
        file_name="graph-test.txt",
        mime_type="text/plain",
        file_size_bytes=12,
        created_by=user.user_id,
    )
    db.add(version)
    db.flush()
    chunk = DocumentChunk(
        document_version_id=version.document_version_id,
        chunk_index=0,
        chunk_hash=HASH_B,
        content_text="graph test chunk",
        modality="text",
    )
    db.add(chunk)
    db.flush()
    return version, chunk


def _scalar_set(engine: Engine, sql: str) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(sql)).scalars())
