from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy import ForeignKeyConstraint, Table, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.errors import ResourceNotFound
from app.core.job_utils import sanitize_job_payload
from app.db import graph_models  # noqa: F401
from app.db.base import Base
from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphIndexRun,
    GraphRelation,
    GraphRetrievalPath,
)
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    User,
)
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE, PHASE3_GRAPH_SYSTEM_SETTINGS
from app.repositories.graph_repository import GraphRepository
from app.repositories.job_repository import JobRepository
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
from app.workers.worker_config import parse_enabled_job_types

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
    graph_relation_table = cast(Table, GraphRelation.__table__)
    source_chunk_fk: ForeignKeyConstraint | None = None
    for constraint in graph_relation_table.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        if any(column.name == "source_document_chunk_id" for column in constraint.columns):
            source_chunk_fk = constraint
            break
    assert source_chunk_fk is not None
    assert source_chunk_fk.ondelete == "CASCADE"


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
        db.add(
            RetrievalRunItem(
                retrieval_run_id=retrieval_run.retrieval_run_id,
                document_chunk_id=chunk.document_chunk_id,
                retrieval_score=Decimal("0.900000"),
                rank_order=1,
                selected_flag=True,
            )
        )
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
        found = repository.find_entity_by_canonical_name(
            db,
            canonical_name="graph schema",
            entity_type="concept",
        )
        assert found is not None
        assert found.graph_entity_id == source.graph_entity_id

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
        assert (
            len(repository.list_relations_for_entity(db, graph_entity_id=source.graph_entity_id))
            == 1
        )

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
        repeated_mention = repository.create_entity_mention(
            db,
            GraphEntityMentionCreate(
                graph_entity_id=source.graph_entity_id,
                document_chunk_id=chunk.document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash=HASH_B,
                mention_offset_start=6,
                mention_offset_end=11,
                confidence=Decimal("0.70000"),
            ),
        )
        assert repeated_mention.graph_entity_mention_id != mention.graph_entity_mention_id

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
            "reindex_policy": "replace_existing",
        }
        assert sanitize_job_payload(payload)["graph_index_run_id"] == run.graph_index_run_id

        service.mark_index_run_running(db, graph_index_run_id=run.graph_index_run_id)
        service.record_index_summary(
            db,
            graph_index_run_id=run.graph_index_run_id,
            summary=GraphIndexSummary(entity_count=1, relation_count=1, mention_count=2),
        )
        assert run.status == "succeeded"
        assert run.entity_count == 1
        assert run.relation_count == 1
        assert run.mention_count == 2
        with pytest.raises(ValueError):
            service.mark_index_run_running(db, graph_index_run_id=run.graph_index_run_id)
        with pytest.raises(ValueError):
            service.record_index_summary(
                db,
                graph_index_run_id=run.graph_index_run_id,
                summary=GraphIndexSummary(entity_count=1, relation_count=1, mention_count=2),
            )

        failed = repository.create_graph_index_run(
            db,
            GraphIndexRunCreate(document_version_id=version.document_version_id),
        )
        service.mark_index_run_failed(
            db,
            graph_index_run_id=failed.graph_index_run_id,
            error_code="extractor_failed",
            error_message="unsafe failure included source text",
        )
        assert failed.status == "failed"
        assert failed.error_message == "Job failed with a redacted error."
        with pytest.raises(ValueError):
            service.mark_index_run_failed(
                db,
                graph_index_run_id=failed.graph_index_run_id,
                error_code="extractor_failed",
            )
        queued = repository.create_graph_index_run(
            db,
            GraphIndexRunCreate(document_version_id=version.document_version_id),
        )
        with pytest.raises(ValueError):
            service.mark_index_run_failed(
                db,
                graph_index_run_id=queued.graph_index_run_id,
                error_code="secret=value",
                error_message="copied source passage",
            )

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
            db,
            retrieval_run_id=retrieval_run.retrieval_run_id,
        ) == [path]


def test_graph_schemas_reject_unsafe_text_and_invalid_hashes() -> None:
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
            metadata_json={"items": [{"raw_chunk_text": "blocked"}]},
        )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            metadata_json={"raw_chunk_text_hash": "not-a-hash"},
        )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            metadata_json={"note": "api_key=redacted"},
        )
    for unsafe_key in ("api_key", "apikey"):
        with pytest.raises(ValidationError):
            GraphEntityCreate(
                canonical_name="Unsafe",
                entity_type="concept",
                metadata_json={unsafe_key: "redacted"},
            )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            aliases_json=["unsafe label with password=value"],
        )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            description="unsafe label with secret=value",
        )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="password=value",
            entity_type="concept",
        )
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="raw chunk label",
        )
    with pytest.raises(ValidationError):
        GraphRelationCreate(
            source_entity_id=1,
            target_entity_id=2,
            relation_type="supports",
            relation_label="unsafe label with token=value",
        )
    with pytest.raises(ValidationError):
        GraphRelationCreate(
            source_entity_id=1,
            target_entity_id=2,
            relation_type="secret=value",
        )
    with pytest.raises(ValidationError):
        GraphIndexRunCreate(extractor_type="secret=value")
    with pytest.raises(ValidationError):
        GraphIndexRunCreate(extractor_version="raw chunk text")
    with pytest.raises(ValidationError):
        GraphRetrievalPathCreate(
            retrieval_run_id=1,
            path_json={"path_ref": "p1"},
            source_chunk_ids_json=[1, 0],
        )
    assert GraphIndexJobPayload(document_version_id=1).job_type == GRAPH_INDEX_BUILD_JOB_TYPE
    assert GraphEntityCreate(
        canonical_name="Safe",
        entity_type="concept",
        metadata_json={"raw_chunk_text_hash": HASH_A},
    ).metadata_json == {"raw_chunk_text_hash": HASH_A}


def test_graph_schemas_reject_boolean_ids() -> None:
    with pytest.raises(ValidationError):
        GraphRelationCreate(source_entity_id=True, target_entity_id=2, relation_type="supports")
    with pytest.raises(ValidationError):
        GraphEntityMentionCreate(graph_entity_id=True, document_chunk_id=1, document_version_id=1)
    with pytest.raises(ValidationError):
        GraphRetrievalPathCreate(retrieval_run_id=True, path_json={"path_ref": "p1"})
    with pytest.raises(ValidationError):
        GraphRetrievalPathCreate(
            retrieval_run_id=1,
            path_json={"path_ref": "p1"},
            source_chunk_ids_json=[True],
        )
    with pytest.raises(ValidationError):
        GraphIndexJobPayload(document_version_id=True)


def test_graph_entity_mentions_require_chunk_version_match(
    graph_session_factory: sessionmaker[Session],
) -> None:
    repository = GraphRepository()
    with graph_session_factory() as db:
        version, chunk = _seed_document_version_and_chunk(db)
        other_version, _ = _seed_document_version_and_chunk(
            db,
            version_no=2,
            status="ready",
            is_active=False,
            logical_document_id=version.logical_document_id,
            created_by=version.created_by,
        )
        entity = repository.create_entity(
            db,
            GraphEntityCreate(canonical_name="Graph Entity", entity_type="concept"),
        )
        with pytest.raises(ValueError):
            repository.create_entity_mention(
                db,
                GraphEntityMentionCreate(
                    graph_entity_id=entity.graph_entity_id,
                    document_chunk_id=chunk.document_chunk_id,
                    document_version_id=other_version.document_version_id,
                    mention_text_hash=HASH_B,
                    mention_offset_start=0,
                    mention_offset_end=5,
                ),
            )


def test_graph_entity_mentions_without_hash_offsets_are_idempotent(
    graph_session_factory: sessionmaker[Session],
) -> None:
    repository = GraphRepository()
    with graph_session_factory() as db:
        version, chunk = _seed_document_version_and_chunk(db)
        entity = repository.create_entity(
            db,
            GraphEntityCreate(canonical_name="Nullable Mention", entity_type="concept"),
        )
        mention_data = GraphEntityMentionCreate(
            graph_entity_id=entity.graph_entity_id,
            document_chunk_id=chunk.document_chunk_id,
            document_version_id=version.document_version_id,
        )
        repository.create_entity_mention(db, mention_data)
        with pytest.raises(IntegrityError):
            repository.create_entity_mention(db, mention_data)


def test_graph_relation_without_source_chunk_is_idempotent(
    graph_session_factory: sessionmaker[Session],
) -> None:
    repository = GraphRepository()
    with graph_session_factory() as db:
        source = repository.create_entity(
            db,
            GraphEntityCreate(canonical_name="Source", entity_type="concept"),
        )
        target = repository.create_entity(
            db,
            GraphEntityCreate(canonical_name="Target", entity_type="concept"),
        )
        relation_data = GraphRelationCreate(
            source_entity_id=source.graph_entity_id,
            target_entity_id=target.graph_entity_id,
            relation_type="supports",
        )
        repository.create_relation(db, relation_data)
        with pytest.raises(IntegrityError):
            repository.create_relation(db, relation_data)


def test_graph_retrieval_path_requires_retrieval_run_items(
    graph_session_factory: sessionmaker[Session],
) -> None:
    repository = GraphRepository()
    with graph_session_factory() as db:
        version, chunk = _seed_document_version_and_chunk(db)
        _, other_chunk = _seed_document_version_and_chunk(
            db,
            version_no=2,
            status="ready",
            is_active=False,
            logical_document_id=version.logical_document_id,
            created_by=version.created_by,
        )
        retrieval_run = RetrievalRun(status="running", started_at=datetime.now(UTC), top_k=5)
        db.add(retrieval_run)
        db.flush()
        db.add(
            RetrievalRunItem(
                retrieval_run_id=retrieval_run.retrieval_run_id,
                document_chunk_id=chunk.document_chunk_id,
                retrieval_score=Decimal("0.500000"),
                rank_order=1,
            )
        )
        db.flush()

        with pytest.raises(ValueError):
            repository.create_graph_retrieval_path(
                db,
                GraphRetrievalPathCreate(
                    retrieval_run_id=retrieval_run.retrieval_run_id,
                    path_json={"path_ref": "p1"},
                    source_chunk_ids_json=[other_chunk.document_chunk_id],
                ),
            )


def test_graph_index_service_missing_version_raises(
    graph_session_factory: sessionmaker[Session],
) -> None:
    service = GraphIndexService()
    with graph_session_factory() as db:
        with pytest.raises(ResourceNotFound):
            service.create_index_run_for_document_version(db, document_version_id=999)


def test_graph_index_service_requires_ready_version(
    graph_session_factory: sessionmaker[Session],
) -> None:
    service = GraphIndexService()
    with graph_session_factory() as db:
        version, _ = _seed_document_version_and_chunk(db, status="processing", is_active=False)
        with pytest.raises(ValueError):
            service.create_index_run_for_document_version(
                db,
                document_version_id=version.document_version_id,
            )


def test_graph_job_type_is_worker_supported() -> None:
    assert GRAPH_INDEX_BUILD_JOB_TYPE == "graph_index_build"
    assert parse_enabled_job_types(GRAPH_INDEX_BUILD_JOB_TYPE) == frozenset(
        {GRAPH_INDEX_BUILD_JOB_TYPE}
    )


def test_default_worker_acquires_graph_jobs(
    graph_session_factory: sessionmaker[Session],
) -> None:
    job_repository = JobRepository()
    with graph_session_factory() as db:
        graph_job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            payload_json={"document_version_id": 1, "graph_index_run_id": 1},
        )
        supported_job = job_repository.create_job(db, job_type="temporary_chat_cleanup")
        acquired = job_repository.acquire_jobs(
            db,
            worker_instance_id="worker-1",
            enabled_job_types=None,
            lease_duration=timedelta(seconds=30),
            batch_size=10,
        )
        assert [job.job_id for job in acquired] == [graph_job.job_id, supported_job.job_id]
        assert graph_job.status == "running"


def test_graph_system_settings_defaults_are_safe() -> None:
    values = {key: value for key, (value, _) in PHASE3_GRAPH_SYSTEM_SETTINGS.items()}
    assert values["rag.graph.enabled"] is False
    assert values["rag.graph.indexing.enabled"] is False
    assert values["rag.graph.extractor.default"] == "none"
    assert values["rag.graph.max_entities_per_chunk"] == 20
    assert values["rag.graph.max_relations_per_chunk"] == 40
    assert values["rag.graph.store_raw_evidence_text"] is False
    assert values["rag.graph.store.provider"] == "postgres"
    assert values["rag.graph.retrieval.enabled"] is False


def test_graph_postgres_schema_constraints_indexes_and_seed_settings(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0017_retrieval_cache_foundation"

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
        "uq_graph_entity_mentions_entity_chunk_hash_offsets",
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
        "ux_graph_relations_source_target_type_no_chunk",
        "ix_graph_entity_mentions_entity",
        "ix_graph_entity_mentions_chunk",
        "ix_graph_entity_mentions_version",
        "ux_graph_entity_mentions_entity_chunk_hash_offsets_coalesced",
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
        assert value is not None
        assert isinstance(value, type(expected))


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


def _seed_document_version_and_chunk(
    db: Session,
    *,
    version_no: int = 1,
    status: str = "ready",
    is_active: bool = True,
    logical_document_id: int | None = None,
    created_by: int | None = None,
) -> tuple[DocumentVersion, DocumentChunk]:
    if logical_document_id is None or created_by is None:
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
        logical_document_id = logical.logical_document_id
        created_by = user.user_id
    version = DocumentVersion(
        logical_document_id=logical_document_id,
        version_no=version_no,
        content_hash=_hash_for_version(version_no),
        status=status,
        is_active=is_active,
        file_name=f"graph-test-v{version_no}.txt",
        mime_type="text/plain",
        file_size_bytes=12,
        created_by=created_by,
    )
    db.add(version)
    db.flush()
    chunk = DocumentChunk(
        document_version_id=version.document_version_id,
        chunk_index=0,
        chunk_hash=_hash_for_version(version_no + 100),
        content_text="graph test chunk",
        modality="text",
    )
    db.add(chunk)
    db.flush()
    return version, chunk


def _hash_for_version(version_no: int) -> str:
    return f"{version_no:064x}"[-64:]


def _scalar_set(engine: Engine, sql: str) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(sql)).scalars())
