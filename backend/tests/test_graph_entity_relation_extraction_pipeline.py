from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.db.graph_models import GraphEntity, GraphEntityMention, GraphIndexRun, GraphRelation
from app.db.models import DocumentChunk, DocumentVersion, Job, LogicalDocument, Role, User
from app.graph.constants import GRAPH_INDEX_BUILD_JOB_TYPE
from app.graph.extraction import (
    EntityMentionCandidate,
    GraphChunkRef,
    GraphExtractionResult,
    RelationCandidate,
)
from app.graph.llm_extraction import LLMGraphExtractor
from app.graph.neo4j_backend import Neo4jClient, Neo4jConnectionConfig
from app.rag.generation import GenerationRequest, GenerationResult, TokenUsage
from app.repositories.graph_repository import GraphRepository
from app.repositories.job_repository import JobRepository
from app.scripts.queue_graph_index_builds import queue_graph_index_build_jobs
from app.services import neo4j_projection_service as neo4j_projection_module
from app.services.graph_index_service import GraphIndexBuildSnapshot, GraphIndexService
from app.services.neo4j_projection_service import Neo4jProjectionResult, Neo4jProjectionService
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


class _RecordingNeo4jDriver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.write_transactions: list[list[str]] = []

    def execute_query(self, query: str, **kwargs: object) -> list[dict[str, object]]:
        self.calls.append(
            (
                query,
                {
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"database_", "result_transformer_"}
                },
            )
        )
        return []

    def session(self, **kwargs: object) -> _RecordingNeo4jSession:
        assert kwargs == {"database": "neo4j"}
        return _RecordingNeo4jSession(self)


class _FlakyNeo4jDriver(_RecordingNeo4jDriver):
    def __init__(self, *, execute_failures: int) -> None:
        super().__init__()
        self.execute_failures = execute_failures

    def execute_query(self, query: str, **kwargs: object) -> list[dict[str, object]]:
        if self.execute_failures > 0:
            self.execute_failures -= 1
            raise RuntimeError("neo4j is not ready")
        return super().execute_query(query, **kwargs)


class _RecordingNeo4jSession:
    def __init__(self, driver: _RecordingNeo4jDriver) -> None:
        self.driver = driver

    def __enter__(self) -> _RecordingNeo4jSession:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def execute_write(self, callback: Callable[[_RecordingNeo4jTransaction], None]) -> None:
        transaction = _RecordingNeo4jTransaction(self.driver)
        callback(transaction)
        self.driver.write_transactions.append(transaction.queries)


class _RecordingNeo4jTransaction:
    def __init__(self, driver: _RecordingNeo4jDriver) -> None:
        self.driver = driver
        self.queries: list[str] = []

    def run(self, query: str, **kwargs: object) -> _RecordingNeo4jResult:
        self.queries.append(query)
        self.driver.calls.append((query, dict(kwargs)))
        return _RecordingNeo4jResult()


class _RecordingNeo4jResult:
    def consume(self) -> None:
        return None


class _StaticGraphAnswerGenerator:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        return GenerationResult(
            content=json.dumps(self.payload),
            usage=TokenUsage(input_tokens=7, output_tokens=5, total_tokens=12),
        )


class _RecordingNeo4jProjectionService:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def project_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
    ) -> Neo4jProjectionResult:
        del db
        self.calls.append((document_version_id, graph_index_run_id))
        return Neo4jProjectionResult(
            enabled=True,
            projected_entities=1,
            projected_relations=2,
            projected_mentions=3,
            projected_chunks=4,
            reason_codes=("neo4j_projection_completed",),
        )


class _SequenceNeo4jProjectionService:
    def __init__(self, results: list[Neo4jProjectionResult]) -> None:
        self.calls: list[tuple[int, int | None]] = []
        self.results = results

    def project_document_version(
        self,
        db: Session,
        *,
        document_version_id: int,
        graph_index_run_id: int | None = None,
    ) -> Neo4jProjectionResult:
        del db
        self.calls.append((document_version_id, graph_index_run_id))
        assert self.results
        return self.results.pop(0)


def test_graph_index_build_persists_safe_rows_and_rebuilds_idempotently(
    graph_session_factory: sessionmaker[Session],
) -> None:
    repository = _RecordingGraphRepository()
    service = GraphIndexService(repository=repository)
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
        assert first_run.extractor_type == "llm"
        assert first_run.extractor_version == "c2b-llm-v1"
        assert first_run.metadata_json["extractor_result_code"] == "graph_extraction_llm_completed"
        assert first_run.entity_count == first_counts["entities"]
        assert first_run.mention_count == first_counts["mentions"]
        assert first_run.relation_count == first_counts["relations"]

        entity_names = {row.canonical_name for row in db.scalars(select(GraphEntity)).all()}
        assert {"Graph Index", "Hybrid RAG", "Qdrant", "GraphIndexService"} <= entity_names
        assert "admin@example.com" not in entity_names

        for entity_row in db.scalars(select(GraphEntity)).all():
            assert _metadata_is_safe(entity_row.metadata_json)
        for mention_row in db.scalars(select(GraphEntityMention)).all():
            assert mention_row.mention_text_hash is not None
            assert mention_row.mention_offset_start is not None
            assert mention_row.mention_offset_end is not None
            assert _metadata_is_safe(mention_row.metadata_json)
        for relation_row in db.scalars(select(GraphRelation)).all():
            assert relation_row.source_document_chunk_id is not None
            assert relation_row.evidence_text_hash is not None
            assert _metadata_is_safe(relation_row.metadata_json)

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
        assert repository.document_version_locks == [
            version.document_version_id,
            version.document_version_id,
        ]
        assert len(repository.entity_key_lock_sets) == 2
        assert all(entity_keys for entity_keys in repository.entity_key_lock_sets)


def test_graph_extraction_settings_default_to_llm() -> None:
    settings = Settings(_env_file=None, app_env="test")

    assert settings.graph_extractor_type == "llm"
    assert settings.graph_extraction_provider is None
    assert settings.graph_extraction_model_name is None
    assert settings.graph_extraction_timeout_seconds == 60
    assert settings.graph_extraction_max_entities_per_chunk == 20
    assert settings.graph_extraction_max_relations_per_chunk == 40


def test_llm_graph_extractor_grounds_offsets_and_drops_hallucinations() -> None:
    chunk = _chunk_ref(
        "Graph Index supports Hybrid RAG. Alice Smith maintains Graph Index.",
    )
    extractor = LLMGraphExtractor(
        settings=Settings(_env_file=None, app_env="test", generation_provider="fake"),
        answer_generator=_StaticGraphAnswerGenerator(
            {
                "entities": [
                    {
                        "mention": "Graph Index",
                        "canonical_name": "Graph Index",
                        "entity_type": "concept",
                        "aliases": [],
                        "confidence": 0.92,
                    },
                    {
                        "mention": "Hybrid RAG",
                        "canonical_name": "Hybrid RAG",
                        "entity_type": "technology",
                        "aliases": [],
                        "confidence": 0.88,
                    },
                    {
                        "mention": "Alice Smith",
                        "canonical_name": "Alice Smith",
                        "entity_type": "person",
                        "aliases": [],
                        "confidence": 0.77,
                    },
                    {
                        "mention": "Ghost System",
                        "canonical_name": "Ghost System",
                        "entity_type": "system",
                        "aliases": [],
                        "confidence": 0.99,
                    },
                ],
                "relations": [
                    {
                        "source": "Graph Index",
                        "target": "Hybrid RAG",
                        "relation_type": "supports",
                        "evidence": "Graph Index supports Hybrid RAG.",
                        "confidence": 0.81,
                    },
                    {
                        "source": "Graph Index",
                        "target": "Ghost System",
                        "relation_type": "uses",
                        "evidence": "Graph Index uses Ghost System.",
                        "confidence": 0.99,
                    },
                ],
            }
        ),
    )

    result = extractor.extract((chunk,))

    names = {mention.canonical_name for mention in result.entity_mentions}
    assert names == {"Graph Index", "Hybrid RAG", "Alice Smith"}
    graph_index_mention = next(
        mention for mention in result.entity_mentions if mention.canonical_name == "Graph Index"
    )
    assert graph_index_mention.mention_offset_start == 0
    assert graph_index_mention.mention_offset_end == len("Graph Index")
    assert graph_index_mention.mention_text_hash == hashlib.sha256(b"Graph Index").hexdigest()
    assert len(result.relations) == 1
    assert result.relations[0].relation_type == "supports"
    assert (
        result.relations[0].evidence_text_hash
        == hashlib.sha256(b"Graph Index supports Hybrid RAG.").hexdigest()
    )
    assert result.extractor_type == "llm"
    assert result.metadata_json["extractor_result_code"] == "graph_extraction_llm_completed"
    assert result.metadata_json["graph_extraction_input_token_count"] == 7
    serialized = str(
        [
            result.metadata_json,
            *[mention.metadata_json for mention in result.entity_mentions],
            *[relation.metadata_json for relation in result.relations],
        ]
    )
    assert "Graph Index supports Hybrid RAG" not in serialized
    assert "Alice Smith maintains" not in serialized
    assert "raw" not in serialized.lower()
    assert "prompt" not in serialized.lower()


def test_graph_index_service_falls_back_to_rule_based_when_llm_provider_unavailable(
    graph_session_factory: sessionmaker[Session],
) -> None:
    service = GraphIndexService(
        settings=Settings(
            _env_file=None,
            app_env="test",
            graph_extractor_type="llm",
            graph_extraction_provider="openai",
            generation_provider="fake",
        )
    )
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        run = service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        snapshot = service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )

        result = service.extract_from_snapshot(snapshot)
        persisted = service.persist_extraction_result(db, snapshot=snapshot, result=result)
        db.commit()

        assert result.extractor_type == "rule_based"
        assert result.metadata_json["extractor_result_code"] == "graph_extraction_llm_fallback"
        assert result.metadata_json["fallback_reason_code"] == "graph_extraction_llm_unavailable"
        assert persisted.extractor_type == "rule_based"
        assert persisted.extractor_version == "pr47-rule-based-v1"
        assert persisted.metadata_json["fallback_reason_code"] == (
            "graph_extraction_llm_unavailable"
        )
        assert persisted.status == "succeeded"
        assert persisted.mention_count > 0


def test_graph_index_worker_records_actual_extractor_after_llm_fallback(
    graph_session_factory: sessionmaker[Session],
) -> None:
    job_repository = JobRepository()
    settings = Settings(
        _env_file=None,
        app_env="test",
        graph_extractor_type="llm",
        graph_extraction_provider="openai",
        generation_provider="fake",
    )
    service = GraphIndexService(settings=settings)
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
            ),
        )
        db.commit()
        job_id = job.job_id
        run_id = run.graph_index_run_id

    dispatcher = JobDispatcher(
        {
            GRAPH_INDEX_BUILD_JOB_TYPE: GraphIndexBuildHandler(
                session_factory=graph_session_factory,
                service_factory=lambda: GraphIndexService(settings=settings),
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
        assert stored_job.status == "succeeded"
        assert stored_job.result_json is not None
        assert stored_job.result_json["extractor_type"] == "rule_based"
        assert stored_job.result_json["graph_extraction_result_code"] == (
            "graph_extraction_llm_fallback"
        )
        assert stored_job.result_json["graph_extraction_fallback_reason"] == (
            "graph_extraction_llm_unavailable"
        )
        assert "Graph Index supports" not in str(stored_job.result_json)
        assert stored_run.extractor_type == "rule_based"


def test_neo4j_projection_service_projects_safe_rows_idempotently(
    graph_session_factory: sessionmaker[Session],
) -> None:
    graph_service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            [
                "Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant.",
                "GraphIndexService connects Graph Repository. "
                "Contact admin@example.com must not be indexed.",
            ],
        )
        run = graph_service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        snapshot = graph_service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        graph_service.persist_extraction_result(
            db,
            snapshot=snapshot,
            result=graph_service.extract_from_snapshot(snapshot),
        )
        aliased_entity = db.scalar(select(GraphEntity).order_by(GraphEntity.graph_entity_id.asc()))
        assert aliased_entity is not None
        aliased_entity.aliases_json = ["HRAG"]
        db.commit()

        fake_driver = _RecordingNeo4jDriver()
        projection_service = Neo4jProjectionService(
            client=Neo4jClient(
                config=Neo4jConnectionConfig(
                    uri="bolt://neo4j.local:7687",
                    user="neo4j",
                    password="configured-test-password",
                ),
                driver=fake_driver,
            ),
            projection_enabled=True,
        )
        first = projection_service.project_document_version(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        second = projection_service.project_document_version(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )

        assert first.reason_codes == ("neo4j_projection_completed",)
        assert second.reason_codes == first.reason_codes
        assert second.projected_entities == first.projected_entities
        assert second.projected_relations == first.projected_relations
        assert second.projected_mentions == first.projected_mentions
        assert second.projected_chunks == first.projected_chunks
        assert any("MERGE (entity:RAGGraphEntity" in query for query, _ in fake_driver.calls)
        assert any(
            "MERGE (source)-[relation:GRAPH_RELATION" in query for query, _ in fake_driver.calls
        )
        assert len(fake_driver.write_transactions) == 2
        assert all(len(batch) == 7 for batch in fake_driver.write_transactions)
        assert all(
            any("DELETE mention" in query for query in batch)
            and any("MERGE (entity:RAGGraphEntity" in query for query in batch)
            for batch in fake_driver.write_transactions
        )
        entity_payloads = [
            parameters["entities"]
            for query, parameters in fake_driver.calls
            if "UNWIND $entities AS row" in query
        ]
        chunk_payloads = [
            parameters["chunks"]
            for query, parameters in fake_driver.calls
            if "UNWIND $chunks AS row" in query
        ]
        assert entity_payloads
        assert chunk_payloads
        projected_entities = cast(list[dict[str, object]], entity_payloads[-1])
        projected_chunks = cast(list[dict[str, object]], chunk_payloads[-1])
        assert any(row["aliases"] == ["HRAG"] for row in projected_entities)
        assert all("logical_document_id" in row for row in projected_chunks)
        assert all(row["modality"] == "text" for row in projected_chunks)
        assert all(row["document_version_status"] == "ready" for row in projected_chunks)
        assert all(row["document_version_is_active"] is True for row in projected_chunks)
        assert all(row["logical_document_status"] == "active" for row in projected_chunks)

        parameter_dump = str([parameters for _, parameters in fake_driver.calls]).lower()
        assert "admin@example.com" not in parameter_dump
        assert "graphindexservice connects" not in parameter_dump
        assert "contact " not in parameter_dump
        assert "raw chunk" not in parameter_dump
        assert "secret" not in parameter_dump


def test_neo4j_projection_service_retries_startup_connection_failure(
    graph_session_factory: sessionmaker[Session],
) -> None:
    graph_service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        run = graph_service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        snapshot = graph_service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        graph_service.persist_extraction_result(
            db,
            snapshot=snapshot,
            result=graph_service.extract_from_snapshot(snapshot),
        )

        fake_driver = _FlakyNeo4jDriver(execute_failures=1)
        projection_service = Neo4jProjectionService(
            client=Neo4jClient(
                config=Neo4jConnectionConfig(
                    uri="bolt://neo4j.local:7687",
                    user="neo4j",
                    password="configured-test-password",
                ),
                driver=fake_driver,
            ),
            projection_enabled=True,
            connect_retry_attempts=2,
            connect_retry_delay_seconds=0,
        )

        result = projection_service.project_document_version(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )

        assert result.reason_codes == ("neo4j_projection_completed",)
        assert fake_driver.execute_failures == 0
        assert fake_driver.write_transactions


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


def test_queue_graph_index_build_jobs_targets_active_ready_versions(
    graph_session_factory: sessionmaker[Session],
) -> None:
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )

        dry_run = queue_graph_index_build_jobs(db, dry_run=True)
        assert dry_run.queued_count == 0
        assert dry_run.would_queue_count == 1
        assert dry_run.skipped_count == 0
        assert dry_run.items[0].document_version_id == version.document_version_id
        assert dry_run.items[0].job_id is None

        queued = queue_graph_index_build_jobs(db)
        db.commit()

        assert queued.queued_count == 1
        assert queued.would_queue_count == 0
        assert queued.skipped_count == 0
        assert queued.items[0].action == "queued"
        assert queued.items[0].job_id is not None
        stored_job = db.get(Job, queued.items[0].job_id)
        assert stored_job is not None
        assert stored_job.job_type == GRAPH_INDEX_BUILD_JOB_TYPE
        assert stored_job.target_type == "document_version"
        assert stored_job.target_id == version.document_version_id
        assert stored_job.payload_json == {
            "job_type": GRAPH_INDEX_BUILD_JOB_TYPE,
            "document_version_id": version.document_version_id,
            "reindex_policy": "replace_existing",
        }
        assert "Graph Index supports" not in str(stored_job.payload_json)

        skipped = queue_graph_index_build_jobs(db)
        assert skipped.queued_count == 0
        assert skipped.skipped_count == 1
        assert skipped.items[0].action == "skipped_active_job"


def test_graph_index_build_worker_triggers_optional_neo4j_projection_after_commit(
    graph_session_factory: sessionmaker[Session],
) -> None:
    projection_service = _RecordingNeo4jProjectionService()
    job_repository = JobRepository()
    graph_service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        run = graph_service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json=graph_service.build_graph_index_job_payload(
                document_version_id=version.document_version_id,
                graph_index_run_id=run.graph_index_run_id,
            ),
        )
        db.commit()
        job_id = job.job_id
        run_id = run.graph_index_run_id
        version_id = version.document_version_id

    dispatcher = JobDispatcher(
        {
            GRAPH_INDEX_BUILD_JOB_TYPE: GraphIndexBuildHandler(
                session_factory=graph_session_factory,
                service_factory=lambda: GraphIndexService(
                    neo4j_projection_service=projection_service
                ),
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
        assert stored_job is not None
        assert stored_job.result_json is not None
        assert projection_service.calls == [(version_id, run_id)]
        assert (
            stored_job.result_json["neo4j_projection_result_code"] == "neo4j_projection_completed"
        )
        assert stored_job.result_json["neo4j_projected_entity_count"] == 1


def test_graph_index_build_worker_records_projection_failure_metadata_then_retries(
    graph_session_factory: sessionmaker[Session],
) -> None:
    projection_service = _SequenceNeo4jProjectionService(
        [
            Neo4jProjectionResult(
                enabled=True,
                reason_codes=("neo4j_connection_failed",),
            ),
            Neo4jProjectionResult(
                enabled=True,
                projected_entities=1,
                projected_relations=2,
                projected_mentions=3,
                projected_chunks=4,
                reason_codes=("neo4j_projection_completed",),
            ),
        ]
    )
    job_repository = JobRepository()
    graph_service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        run = graph_service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        payload = graph_service.build_graph_index_job_payload(
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        first_job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json=payload,
        )
        db.commit()
        first_job_id = first_job.job_id
        run_id = run.graph_index_run_id
        version_id = version.document_version_id

    dispatcher = JobDispatcher(
        {
            GRAPH_INDEX_BUILD_JOB_TYPE: GraphIndexBuildHandler(
                session_factory=graph_session_factory,
                service_factory=lambda: GraphIndexService(
                    neo4j_projection_service=projection_service
                ),
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
        stored_first_job = db.get(Job, first_job_id)
        stored_run = db.get(GraphIndexRun, run_id)
        assert stored_first_job is not None
        assert stored_run is not None
        assert stored_first_job.status == "succeeded"
        assert stored_first_job.error_code is None
        assert stored_first_job.result_json is not None
        assert stored_first_job.result_json["neo4j_projection_result_code"] == (
            "neo4j_connection_failed"
        )
        assert stored_run.status == "succeeded"
        retry_job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version_id,
            payload_json=payload,
        )
        db.commit()
        retry_job_id = retry_job.job_id

    assert runner.run_once() == 1

    with graph_session_factory() as db:
        stored_retry_job = db.get(Job, retry_job_id)
        assert stored_retry_job is not None
        assert stored_retry_job.status == "succeeded"
        assert stored_retry_job.result_json is not None
        assert stored_retry_job.result_json["status"] == "already_succeeded"
        assert (
            stored_retry_job.result_json["neo4j_projection_result_code"]
            == "neo4j_projection_completed"
        )
        assert projection_service.calls == [(version_id, run_id), (version_id, run_id)]


def test_graph_index_build_worker_retries_projection_for_already_succeeded_run(
    graph_session_factory: sessionmaker[Session],
) -> None:
    projection_service = _RecordingNeo4jProjectionService()
    job_repository = JobRepository()
    graph_service = GraphIndexService()
    with graph_session_factory() as db:
        version = _seed_ready_version(
            db,
            ["Graph Index supports Hybrid RAG. Hybrid RAG uses Qdrant."],
        )
        run = graph_service.create_index_run_for_document_version(
            db,
            document_version_id=version.document_version_id,
        )
        snapshot = graph_service.prepare_index_build(
            db,
            document_version_id=version.document_version_id,
            graph_index_run_id=run.graph_index_run_id,
        )
        graph_service.persist_extraction_result(
            db,
            snapshot=snapshot,
            result=graph_service.extract_from_snapshot(snapshot),
        )
        job = job_repository.create_job(
            db,
            job_type=GRAPH_INDEX_BUILD_JOB_TYPE,
            target_type="document_version",
            target_id=version.document_version_id,
            payload_json=graph_service.build_graph_index_job_payload(
                document_version_id=version.document_version_id,
                graph_index_run_id=run.graph_index_run_id,
            ),
        )
        db.commit()
        job_id = job.job_id
        run_id = run.graph_index_run_id
        version_id = version.document_version_id

    dispatcher = JobDispatcher(
        {
            GRAPH_INDEX_BUILD_JOB_TYPE: GraphIndexBuildHandler(
                session_factory=graph_session_factory,
                service_factory=lambda: GraphIndexService(
                    neo4j_projection_service=projection_service
                ),
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
        assert stored_job is not None
        assert stored_job.result_json is not None
        assert projection_service.calls == [(version_id, run_id)]
        assert stored_job.result_json["status"] == "already_succeeded"
        assert stored_job.result_json["result_code"] == "no_op"
        assert (
            stored_job.result_json["neo4j_projection_result_code"] == "neo4j_projection_completed"
        )


def test_graph_index_service_closes_temporary_neo4j_projection_service(
    graph_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = []

    class _ClosingProjectionService:
        def __init__(self) -> None:
            self.closed = False
            instances.append(self)

        def project_document_version(
            self,
            db: Session,
            *,
            document_version_id: int,
            graph_index_run_id: int | None = None,
        ) -> Neo4jProjectionResult:
            del db, document_version_id, graph_index_run_id
            return Neo4jProjectionResult(
                enabled=True,
                reason_codes=("neo4j_projection_completed",),
            )

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        neo4j_projection_module,
        "Neo4jProjectionService",
        _ClosingProjectionService,
    )

    service = GraphIndexService()
    with graph_session_factory() as db:
        service.project_neo4j_index_run(
            db,
            document_version_id=1,
            graph_index_run_id=2,
        )

    assert len(instances) == 1
    closed_instance = cast(_ClosingProjectionService, instances[0])
    assert closed_instance.closed is True


def test_neo4j_docker_path_installs_default_extra() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dockerfile = (repo_root / "backend" / "Dockerfile").read_text(encoding="utf-8")
    compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    ci_compose = (repo_root / "docker-compose.ci.yml").read_text(encoding="utf-8")
    docs = (repo_root / "docs" / "phase3" / "neo4j_optional_backend.md").read_text(encoding="utf-8")

    assert 'ARG BACKEND_UV_EXTRA_ARGS=""' in dockerfile
    assert "uv sync --frozen --no-install-project --no-dev $BACKEND_UV_EXTRA_ARGS" in dockerfile
    assert "BACKEND_UV_EXTRA_ARGS: ${BACKEND_UV_EXTRA_ARGS:---extra neo4j}" in compose
    assert compose.count("neo4j:\n        condition: service_healthy") >= 3
    assert ci_compose.count("neo4j:\n        condition: service_healthy") >= 4
    assert 'BACKEND_UV_EXTRA_ARGS="--extra neo4j"' in docs


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
            db.scalars(select(GraphIndexRun).order_by(GraphIndexRun.graph_index_run_id.asc())).all()
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


def _chunk_ref(text: str) -> GraphChunkRef:
    return GraphChunkRef(
        document_chunk_id=1,
        document_version_id=1,
        chunk_index=0,
        chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content_text=text,
    )


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


class _RecordingGraphRepository(GraphRepository):
    def __init__(self) -> None:
        self.document_version_locks: list[int] = []
        self.entity_key_lock_sets: list[set[tuple[str, str]]] = []

    def acquire_graph_index_document_version_lock(
        self,
        db: Session,
        *,
        document_version_id: int,
    ) -> None:
        self.document_version_locks.append(document_version_id)
        super().acquire_graph_index_document_version_lock(
            db,
            document_version_id=document_version_id,
        )

    def acquire_graph_entity_key_locks(
        self,
        db: Session,
        *,
        keys: set[tuple[str, str]],
    ) -> None:
        self.entity_key_lock_sets.append(set(keys))
        super().acquire_graph_entity_key_locks(db, keys=keys)
