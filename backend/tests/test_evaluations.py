from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.evaluation_models import EvaluationResult
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    EvaluationRun,
    EvaluationRunItem,
    Job,
    LogicalDocument,
    RetrievalRun,
    Role,
    User,
)
from app.db.session import get_db
from app.evaluation.fixtures import load_evaluation_cases
from app.evaluation.metrics import EvaluationMetricInputs, calculate_metrics
from app.evaluation.rag_service import DatabaseVectorSearchClient, RagEvaluationResult
from app.ingest.embedding import FakeEmbeddingAdapter
from app.main import create_app
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate, VectorSearchClient
from app.schemas.evaluations import EvaluationRunCreateRequest
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary
from app.services.evaluation_service import EvaluationService
from app.workers.handlers.base import JobExecutionContext
from app.workers.handlers.evaluation_run_handler import EvaluationRunHandler

ALLOWED_ORIGIN = "http://localhost:5173"
TEST_PASSWORD = "password"


@pytest.fixture
def evaluation_client() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    password_hash = hash_password(TEST_PASSWORD)
    with session_factory() as db:
        admin_role = Role(role_name="admin", description="Admin")
        viewer_role = Role(role_name="viewer", description="Viewer")
        db.add_all([admin_role, viewer_role])
        db.flush()
        db.add_all(
            [
                User(
                    role_id=admin_role.role_id,
                    email="admin@example.com",
                    display_name="Admin",
                    password_hash=password_hash,
                    status="active",
                ),
                User(
                    role_id=viewer_role.role_id,
                    email="viewer@example.com",
                    display_name="Viewer",
                    password_hash=password_hash,
                    status="active",
                ),
            ]
        )
        db.commit()

    def override_db() -> Iterator[Session]:
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    try:
        yield TestClient(app), session_factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_fixture_loader_and_metric_clamp() -> None:
    cases = load_evaluation_cases("phase1_smoke", case_limit=1)
    assert [case.case_id for case in cases] == ["phase1_seed_stack"]

    metrics = calculate_metrics(
        EvaluationMetricInputs(
            case=cases[0],
            answer_text="Qdrant is used.",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    snippet="Qdrant appears in a safe citation preview.",
                    old_version_flag=False,
                )
            ],
            confidence=RagAskConfidence(
                answer_confidence=1.0,
                groundedness_score=1.0,
                confidence_label="High",
            ),
            retrieval_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
        )
    )

    by_name = {metric.metric_name: metric for metric in metrics}
    assert by_name["faithfulness"].metric_score == 1.0
    assert by_name["groundedness"].metric_score == 1.0
    assert by_name["citation_coverage"].metric_score == 1.0
    assert by_name["context_precision"].metric_score == 1.0
    assert "Qdrant" not in str(by_name["faithfulness"].details)


def test_database_vector_search_client_uses_ready_active_chunks() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            admin = _seed_admin(db)
            logical = LogicalDocument(
                owner_user_id=admin.user_id,
                title="Evaluation seed",
                status="active",
            )
            db.add(logical)
            db.flush()
            version = DocumentVersion(
                logical_document_id=logical.logical_document_id,
                version_no=1,
                content_hash="a" * 64,
                status="ready",
                is_active=True,
                file_name="evaluation-seed.md",
                mime_type="text/markdown",
                file_size_bytes=10,
                created_by=admin.user_id,
            )
            db.add(version)
            db.flush()
            distractor_texts = [
                f"Architecture note {index} without the requested evaluation signal."
                for index in range(6)
            ]
            target_text = "Qdrant deterministic fake adapters citation retrieval traces"
            chunks = [
                DocumentChunk(
                    document_version_id=version.document_version_id,
                    chunk_index=index,
                    chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    content_text=text,
                    modality="text",
                )
                for index, text in enumerate([*distractor_texts, target_text])
            ]
            db.add_all(chunks)
            db.commit()
            target_chunk_id = chunks[-1].document_chunk_id
            query_vector = FakeEmbeddingAdapter(dimension=8).embed_texts([target_text])[0]

            candidates = DatabaseVectorSearchClient(db).search(
                collection_name="unused",
                query_vector=query_vector,
                limit=1,
                filters=RetrievalFilters(),
            )

        assert [candidate.document_chunk_id for candidate in candidates] == [target_chunk_id]
    finally:
        engine.dispose()


def test_evaluation_api_admin_create_list_detail_and_rbac(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = evaluation_client

    assert client.get("/api/v1/evaluations/runs").status_code == 401

    _login_as(client, "viewer@example.com")
    viewer_forbidden = client.get("/api/v1/evaluations/runs")
    assert viewer_forbidden.status_code == 403
    assert viewer_forbidden.json()["error"]["code"] == "permission_denied"
    viewer_csrf_token = _session_csrf(client)
    viewer_create_forbidden = client.post(
        "/api/v1/evaluations/runs",
        json={"dataset_name": "phase1_smoke", "case_limit": 1},
        headers={"X-CSRF-Token": viewer_csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert viewer_create_forbidden.status_code == 403
    assert viewer_create_forbidden.json()["error"]["code"] == "permission_denied"

    _logout(client)
    _login_as(client, "admin@example.com")
    missing_csrf = client.post(
        "/api/v1/evaluations/runs",
        json={"dataset_name": "phase1_smoke", "case_limit": 1},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_missing"

    csrf_token = _session_csrf(client)
    created = client.post(
        "/api/v1/evaluations/runs",
        json={"dataset_name": "phase1_smoke", "case_limit": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["data"]["status"] == "queued"
    assert body["data"]["job_id"] >= 1

    listing = client.get("/api/v1/evaluations/runs")
    detail = client.get(f"/api/v1/evaluations/runs/{body['data']['evaluation_run_id']}")
    missing = client.get("/api/v1/evaluations/runs/999")

    assert listing.status_code == 200
    assert listing.json()["data"][0]["dataset_name"] == "phase1_smoke"
    assert listing.json()["data"][0]["case_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["data"]["case_count"] == 1
    assert detail.json()["data"]["items"] == []
    assert missing.status_code == 404
    assert "token" not in listing.text.lower()
    assert "secret" not in detail.text.lower()

    invalid_dataset = client.post(
        "/api/v1/evaluations/runs",
        json={"dataset_name": "../phase1_smoke", "case_limit": 1},
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert invalid_dataset.status_code == 422

    with session_factory() as db:
        run = db.scalar(select(EvaluationRun))
        job = db.scalar(select(Job))
        assert run is not None
        assert run.status == "queued"
        assert job is not None
        assert job.job_type == "evaluation_run"
        assert job.target_type == "evaluation_run"
        assert job.target_id == run.evaluation_run_id


def test_evaluation_summary_uses_planned_case_count_while_running() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(settings=Settings(app_env="test"))
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(dataset_name="phase1_smoke", case_limit=2),
                user=user,
            )
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            run.status = "running"
            run.started_at = datetime.now(UTC)
            db.add(
                EvaluationRunItem(
                    evaluation_run_id=created.evaluation_run_id,
                    status="running",
                )
            )
            db.commit()

            response = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)

        assert response.case_count == 2
        assert len(response.items) == 1
    finally:
        engine.dispose()


def test_evaluation_service_runner_persists_items_results_and_safe_details() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _FakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(dataset_name="phase1_smoke", case_limit=2),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _FakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "succeeded"
            items = db.scalars(select(EvaluationRunItem)).all()
            results = db.scalars(select(EvaluationResult)).all()
            assert len(items) == 2
            assert all(item.status == "succeeded" for item in items)
            assert {result.metric_name for result in results} >= {
                "faithfulness",
                "groundedness",
                "citation_coverage",
                "context_precision",
            }
            response = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert response.case_count == 2
            assert response.items[0].case_id == "phase1_seed_stack"
            assert "raw prompt" not in response.model_dump_json().lower()
            assert "full context" not in response.model_dump_json().lower()
    finally:
        engine.dispose()


def test_evaluation_handler_processes_job_and_case_failure() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _PartiallyFailingRagService(),
                settings=Settings(app_env="test"),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(dataset_name="phase1_smoke", case_limit=2),
                user=user,
            )

        handler_service = EvaluationService(
            rag_service_factory=lambda settings, db: _PartiallyFailingRagService(),
            settings=Settings(app_env="test"),
        )
        handler = EvaluationRunHandler(
            session_factory=session_factory,
            service_factory=lambda: handler_service,
        )
        result = handler.handle(
            JobExecutionContext(
                job_id=10,
                job_type="evaluation_run",
                target_type="evaluation_run",
                target_id=created.evaluation_run_id,
                payload={"evaluation_run_id": created.evaluation_run_id},
                worker_instance_id="worker-1",
            )
        )

        assert result.status == "succeeded"
        with session_factory() as db:
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "succeeded"
            items = db.scalars(
                select(EvaluationRunItem).order_by(EvaluationRunItem.evaluation_run_item_id)
            ).all()
            assert [item.status for item in items] == ["succeeded", "failed"]
            assert items[1].error_code == "no_context_found"
            failed_metadata = db.scalar(
                select(EvaluationResult).where(
                    EvaluationResult.evaluation_run_item_id == items[1].evaluation_run_item_id,
                    EvaluationResult.metric_name == "case_metadata",
                )
            )
            assert failed_metadata is not None
            assert failed_metadata.details_json == {
                "case_id": "phase1_seed_ci",
                "expected_keyword_count": 2,
                "required_citation": True,
                "error_code": "no_context_found",
            }
    finally:
        engine.dispose()


def test_evaluation_service_marks_run_failed_when_setup_fails() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=_failing_rag_service_factory,
                settings=Settings(app_env="test"),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(dataset_name="phase1_smoke", case_limit=1),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=_failing_rag_service_factory,
                settings=Settings(app_env="test"),
            )
            with pytest.raises(RuntimeError):
                service.run_job(
                    db,
                    evaluation_run_id=created.evaluation_run_id,
                    request_id="test-eval",
                )

        with session_factory() as db:
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "failed"
            assert run.error_code == "internal_error"
            assert run.finished_at is not None
    finally:
        engine.dispose()


class _FakeVectorClient(VectorSearchClient):
    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
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
) -> RetrievalRun:
    now = datetime.now(UTC)
    run = RetrievalRun(
        status=status,
        error_code=error_code,
        started_at=now,
        finished_at=now,
        top_k=5,
        query_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),
        retrieval_score_summary={
            "requested_top_k": 5,
            "qdrant_candidate_count": 1,
            "post_filter_candidate_count": 1,
            "selected_count": 1 if status == "succeeded" else 0,
            "excluded_by_rdb_check_count": 0,
        },
        answer_confidence=0.9 if status == "succeeded" else None,
        groundedness_score=0.9 if status == "succeeded" else None,
        confidence_label="High" if status == "succeeded" else None,
        request_id=request_id,
    )
    db.add(run)
    db.flush()
    return run


def _session_factory() -> tuple[Any, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _seed_admin(db: Session) -> User:
    role = Role(role_name="admin", description="Admin")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email="admin@example.com",
        display_name="Admin",
        password_hash=hash_password(TEST_PASSWORD),
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login_as(client: TestClient, email: str) -> dict[str, Any]:
    csrf = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert csrf.status_code == 200
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": TEST_PASSWORD},
        headers={"X-CSRF-Token": csrf.json()["data"]["csrf_token"], "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
    return response.json()


def _session_csrf(client: TestClient) -> str:
    response = client.get("/api/v1/auth/csrf", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    return str(response.json()["data"]["csrf_token"])


def _logout(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": _session_csrf(client), "Origin": ALLOWED_ORIGIN},
    )
    assert response.status_code == 200
