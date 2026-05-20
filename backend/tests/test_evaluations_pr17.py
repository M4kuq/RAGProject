from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import require_admin, require_csrf
from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.evaluation_models import EvaluationResult
from app.db.models import (
    EvaluationRun,
    EvaluationRunItem,
    Job,
    RetrievalRun,
    Role,
    User,
)
from app.db.session import get_db
from app.evaluation.fixtures import load_evaluation_cases
from app.evaluation.metrics import EvaluationMetricInputs, calculate_metrics
from app.evaluation.rag_service import RagEvaluationResult
from app.main import create_app
from app.schemas.evaluations import EvaluationRunCreateRequest
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary
from app.services.evaluation_service import EvaluationService
from app.workers.handlers.base import JobExecutionContext
from app.workers.handlers.evaluation_run_handler import EvaluationRunHandler


def test_fixture_loader_and_metric_clamp() -> None:
    case = load_evaluation_cases("phase1_smoke", case_limit=1)[0]
    metrics = calculate_metrics(
        EvaluationMetricInputs(
            case=case,
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


def test_evaluation_api_admin_create_list_detail_and_safe_response() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            admin = _seed_admin(db)

        def override_db() -> Iterator[Session]:
            with session_factory() as db:
                yield db

        app = create_app()
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_csrf] = lambda: None
        app.dependency_overrides[require_admin] = lambda: admin
        try:
            client = TestClient(app)
            created = client.post(
                "/api/v1/evaluations/runs",
                json={"dataset_name": "phase1_smoke", "case_limit": 1},
            )
            run_id = created.json()["data"]["evaluation_run_id"]
            listing = client.get("/api/v1/evaluations/runs")
            detail = client.get(f"/api/v1/evaluations/runs/{run_id}")
            missing = client.get("/api/v1/evaluations/runs/999")
            invalid = client.post(
                "/api/v1/evaluations/runs",
                json={"dataset_name": "../phase1_smoke", "case_limit": 1},
            )
        finally:
            app.dependency_overrides.clear()

        assert created.status_code == 202
        assert created.json()["data"]["status"] == "queued"
        assert created.json()["data"]["job_id"] >= 1
        assert listing.status_code == 200
        assert listing.json()["data"][0]["case_count"] == 1
        assert detail.status_code == 200
        assert detail.json()["data"]["items"] == []
        assert missing.status_code == 404
        assert invalid.status_code == 422
        assert "token" not in listing.text.lower()
        assert "secret" not in detail.text.lower()

        with session_factory() as db:
            run = db.scalar(select(EvaluationRun))
            job = db.scalar(select(Job))
            assert run is not None
            assert run.status == "queued"
            assert job is not None
            assert job.job_type == "evaluation_run"
            assert job.target_type == "evaluation_run"
    finally:
        engine.dispose()


def test_evaluation_service_runner_persists_items_results_and_safe_details() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = _service(_FakeEvaluationRagService())
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(dataset_name="phase1_smoke", case_limit=2),
                user=user,
            )

        with session_factory() as db:
            result = _service(_FakeEvaluationRagService()).run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "succeeded"
            assert len(db.scalars(select(EvaluationRunItem)).all()) == 2
            results = db.scalars(select(EvaluationResult)).all()
            assert {result.metric_name for result in results} >= {
                "faithfulness",
                "groundedness",
                "citation_coverage",
                "context_precision",
            }
            response = _service(_FakeEvaluationRagService()).get_run_detail(
                db,
                evaluation_run_id=created.evaluation_run_id,
            )
            assert response.case_count == 2
            assert "raw prompt" not in response.model_dump_json().lower()
            assert "full context" not in response.model_dump_json().lower()
    finally:
        engine.dispose()


def test_evaluation_handler_keeps_case_failure_on_succeeded_run() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            created = _service(_PartiallyFailingRagService()).create_run(
                db,
                payload=EvaluationRunCreateRequest(dataset_name="phase1_smoke", case_limit=2),
                user=user,
            )

        handler_service = _service(_PartiallyFailingRagService())
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
    finally:
        engine.dispose()


class _FakeEvaluationRagService:
    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        retrieval_run = _create_fake_retrieval_run(db, question=question, request_id=request_id)
        answer = "Qdrant and deterministic fake adapters support citation-aware retrieval traces."
        snippet = "Qdrant, deterministic fake adapters, and citation-aware retrieval traces."
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text=answer,
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=1,
                    source_label="phase1-seed.md",
                    snippet=snippet,
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

    def evaluate_question(self, db: Session, **kwargs: Any) -> RagEvaluationResult:
        self.calls += 1
        if self.calls == 2:
            request_id = kwargs.get("request_id")
            retrieval_run = _create_fake_retrieval_run(
                db,
                question=str(kwargs.get("question", "")),
                request_id=request_id if isinstance(request_id, str) else None,
                status="failed",
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
        return super().evaluate_question(db, **kwargs)


def _service(fake: _FakeEvaluationRagService) -> EvaluationService:
    return EvaluationService(
        rag_service_factory=lambda settings, db: fake,
        settings=Settings(app_env="test"),
    )


def _create_fake_retrieval_run(
    db: Session,
    *,
    question: str,
    request_id: str | None,
    status: str = "succeeded",
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
        password_hash=hash_password("password"),
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
