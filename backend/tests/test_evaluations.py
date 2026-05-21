from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db import evaluation_models as _evaluation_models  # noqa: F401
from app.db.base import Base
from app.db.evaluation_models import EvaluationDatasetCase
from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument, Role, User
from app.evaluation.runner import EvaluationRunner, EvaluationRunError
from app.evaluation.rag_service import RagEvaluationResult
from app.evaluation.seed_data import seed_default_dataset
from app.main import create_app
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate, VectorSearchClient
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary
from app.services.evaluation_service import EvaluationService
from app.services.rag_service import RagService


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        session_secret="test-session-secret",
        storage_root=tmp_path / "uploads",
        embedding_provider="fake",
        embedding_fake_dimension=4,
        rerank_provider="fake",
        generation_provider="fake",
        evaluation_default_dataset_path=Path("data/evaluation/phase1_smoke.csv"),
    )


@pytest.fixture
def seeded_client(settings: Settings) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        _seed_document(db)
        db.commit()
    app = create_app(settings=settings, session_factory=session_factory)
    with TestClient(app) as client:
        yield client, session_factory
    engine.dispose()


def test_seed_default_dataset_creates_cases(settings: Settings) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        result = seed_default_dataset(db, settings)
        db.commit()

        assert result.created == 3
        assert result.skipped == 0
        assert db.query(EvaluationDatasetCase).count() == 3
        assert {case.case_id for case in db.query(EvaluationDatasetCase).all()} == {
            "phase1_security_controls",
            "phase1_ingest_pipeline",
            "phase1_citation_behavior",
        }
    engine.dispose()


def test_evaluation_runner_executes_cases(settings: Settings) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        _seed_document(db)
        seed_default_dataset(db, settings)
        db.commit()

        runner = EvaluationRunner(
            settings=settings,
            session_factory=session_factory,
            rag_service_factory=lambda _settings, _db: _FakeEvaluationRagService(),
        )
        summary = runner.run_default_dataset()

        assert summary.status == "succeeded"
        assert summary.succeeded_items == 3
        assert summary.failed_items == 0
        assert summary.metrics["faithfulness"]["score"] == pytest.approx(1.0)
        assert summary.metrics["groundedness"]["score"] == pytest.approx(1.0)
        assert summary.metrics["citation_coverage"]["score"] == pytest.approx(1.0)


def test_evaluation_runner_records_partial_failures(settings: Settings) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        _seed_document(db)
        seed_default_dataset(db, settings)
        db.commit()

        runner = EvaluationRunner(
            settings=settings,
            session_factory=session_factory,
            rag_service_factory=lambda _settings, _db: _PartiallyFailingRagService(),
        )
        summary = runner.run_default_dataset()

        assert summary.status == "succeeded"
        assert summary.total_items == 3
        assert summary.succeeded_items == 2
        assert summary.failed_items == 1
        assert summary.metrics["faithfulness"]["score"] == pytest.approx(1.0)
        assert summary.metrics["groundedness"]["score"] == pytest.approx(1.0)
        assert summary.metrics["citation_coverage"]["score"] == pytest.approx(1.0)


def test_evaluation_runner_requires_dataset(settings: Settings) -> None:
    missing_settings = settings.model_copy(
        update={"evaluation_default_dataset_path": Path("missing.csv")},
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        db.commit()

    runner = EvaluationRunner(
        settings=missing_settings,
        session_factory=session_factory,
        rag_service_factory=lambda _settings, _db: _FakeEvaluationRagService(),
    )

    with pytest.raises(EvaluationRunError, match="dataset_not_found"):
        runner.run_default_dataset()
    engine.dispose()


def test_evaluation_runner_rejects_empty_dataset(settings: Settings, tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.csv"
    empty_path.write_text("case_id,question,expected_keywords\n", encoding="utf-8")
    empty_settings = settings.model_copy(update={"evaluation_default_dataset_path": empty_path})
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        db.commit()

    runner = EvaluationRunner(
        settings=empty_settings,
        session_factory=session_factory,
        rag_service_factory=lambda _settings, _db: _FakeEvaluationRagService(),
    )

    with pytest.raises(EvaluationRunError, match="dataset_empty"):
        runner.run_default_dataset()
    engine.dispose()


def test_evaluation_runner_setup_failure_marks_run_failed(settings: Settings) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        _seed_auth(db)
        _seed_document(db)
        seed_default_dataset(db, settings)
        db.commit()

    runner = EvaluationRunner(
        settings=settings,
        session_factory=session_factory,
        rag_service_factory=_failing_rag_service_factory,
    )

    with pytest.raises(RuntimeError, match="synthetic evaluation setup failure"):
        runner.run_default_dataset()

    with session_factory() as db:
        service = EvaluationService(settings)
        runs, _ = service.list_runs(db)
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert runs[0].error_code == "evaluation_setup_failed"


def test_evaluation_service_lists_and_reads_runs(
    seeded_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = seeded_client
    login_as_admin(client)
    response = client.get("/api/v1/evaluations/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []

    with session_factory() as db:
        _create_evaluation_result(db)
        db.commit()

    response = client.get("/api/v1/evaluations/runs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["evaluation_run_id"] == 1
    assert payload["items"][0]["metrics"]["faithfulness"]["score"] == "0.9000"

    detail_response = client.get("/api/v1/evaluations/runs/1")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["evaluation_run_id"] == 1
    assert detail["items"][0]["metrics"][0]["metric_name"] == "faithfulness"


def test_evaluation_run_detail_not_found(
    seeded_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = seeded_client
    login_as_admin(client)
    response = client.get("/api/v1/evaluations/runs/999")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "evaluation_run_not_found"


def test_evaluation_run_list_requires_admin(
    seeded_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = seeded_client
    response = client.get("/api/v1/evaluations/runs")
    assert response.status_code == 401


def test_evaluation_run_detail_requires_admin(
    seeded_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = seeded_client
    response = client.get("/api/v1/evaluations/runs/1")
    assert response.status_code == 401


def login_as_admin(client: TestClient) -> None:
    assert client.get("/api/v1/auth/csrf").status_code == 204
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
        headers={"X-CSRF-Token": "test-csrf-token"},
    )
    assert response.status_code == 200


def _seed_auth(db: Session) -> None:
    role = Role(role_name="admin", description="Admin")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email="admin@example.com",
        display_name="Admin",
        password_hash="$2b$12$6C/KI/08/64PYTqmT7TKaOs8n7dK2wr9Uwup5oF.PPqayydh0N8sG",
        status="active",
    )
    db.add(user)


def _seed_document(db: Session) -> None:
    document = LogicalDocument(
        logical_document_id=1,
        owner_user_id=1,
        title="phase1-seed.md",
        status="active",
    )
    db.add(document)
    version = DocumentVersion(
        document_version_id=1,
        logical_document_id=1,
        version_no=1,
        content_hash="a" * 64,
        status="ready",
        is_active=True,
        file_name="phase1-seed.md",
        mime_type="text/markdown",
        file_size_bytes=1024,
        storage_key="test/phase1-seed.md",
        page_count=1,
        created_by=1,
    )
    db.add(version)
    db.add(
        DocumentChunk(
            document_chunk_id=1,
            document_version_id=1,
            chunk_index=0,
            chunk_hash="b" * 64,
            content_text=(
                "Qdrant indexes deterministic fake adapters for citation-aware retrieval traces. "
                "The ingest pipeline uses job leases, extraction, chunking, embeddings, and indexing. "
                "Security controls include CSRF, sessions, RBAC, and safe admin APIs."
            ),
            token_count=32,
            char_count=200,
            page_from=1,
            page_to=1,
            section_title="Phase1",
            modality="text",
        ),
    )


def _create_evaluation_result(db: Session) -> None:
    from app.db.models import EvaluationRun, EvaluationRunItem
    from app.db.evaluation_models import EvaluationResult

    run = EvaluationRun(
        evaluation_run_id=1,
        created_by=1,
        status="succeeded",
        target_type="fixture_dataset",
        metrics_config={"dataset_name": "phase1_smoke", "case_limit": 1},
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    item = EvaluationRunItem(
        evaluation_run_item_id=1,
        evaluation_run_id=1,
        retrieval_run_id=None,
        status="succeeded",
        faithfulness_score="0.9",
        groundedness_score="0.8",
        citation_coverage="1.0",
        latency_ms=12,
    )
    db.add(item)
    db.flush()
    db.add(
        EvaluationResult(
            evaluation_run_item_id=item.evaluation_run_item_id,
            metric_name="faithfulness",
            metric_score="0.9",
            metric_label="High",
            details_json={"case_id": "phase1_security_controls"},
        ),
    )


class _FakeVectorClient(VectorSearchClient):
    def search(
        self,
        *,
        collection_name: str,
        query_vector: list[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        return []


class _FakeEvaluationRagService:
    vector_client = _FakeVectorClient()

    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
        )
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text=(
                "Qdrant and deterministic fake adapters support citation-aware retrieval traces."
            ),
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    snippet=(
                        "Qdrant, deterministic fake adapters, and citation-aware retrieval traces."
                    ),
                    old_version_flag=False,
                )
            ],
            confidence=RagAskConfidence(
                answer_confidence=0.9,
                groundedness_score=0.9,
                confidence_label="High",
            ),
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
            context_sources_for_safety=[],
        )


class _PartiallyFailingRagService(_FakeEvaluationRagService):
    def __init__(self) -> None:
        self.calls = 0

    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        self.calls += 1
        if self.calls == 2:
            retrieval_run = _create_fake_retrieval_run(
                db,
                question=question,
                status="failed",
                request_id=request_id,
                error_code="no_context_found",
            )
            return RagEvaluationResult(
                retrieval_run_id=retrieval_run.retrieval_run_id,
                status="failed",
                answer_text="",
                citations=[],
                confidence=None,
                retrieval_score_summary=None,
                context_sources_for_safety=[],
                error_code="no_context_found",
            )
        return super().evaluate_question(
            db,
            question=question,
            request_id=request_id,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
        )


def _failing_rag_service_factory(settings: Settings, db: Session) -> _FakeEvaluationRagService:
    raise RuntimeError("synthetic evaluation setup failure")


def _create_fake_retrieval_run(
    db: Session,
    *,
    question: str,
    status: str,
    request_id: str | None,
    error_code: str | None = None,
):
    from app.db.models import RetrievalRun

    run = RetrievalRun(
        query_hash=f"eval-{abs(hash((question, request_id))) % 10_000_000}",
        status=status,
        top_k=5,
        filters_json={},
        request_id=request_id,
        error_code=error_code,
    )
    db.add(run)
    db.flush()
    return run
