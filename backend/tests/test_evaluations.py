from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.errors import ConflictError
from app.core.security import hash_password
from app.db.base import Base
from app.db.evaluation_models import EvaluationResult
from app.db.graph_models import GraphRetrievalPath
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    EvaluationDataset,
    EvaluationRun,
    EvaluationRunItem,
    Job,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    User,
)
from app.db.models import (
    EvaluationCase as EvaluationCaseModel,
)
from app.db.session import get_db
from app.evaluation.fixtures import EvaluationCase, EvaluationFixtureError, load_evaluation_cases
from app.evaluation.metrics import (
    EvaluationMetricInputs,
    RetrievedEvaluationItem,
    calculate_metrics,
)
from app.evaluation.rag_service import (
    DatabaseVectorSearchClient,
    EvaluationRagQuestionService,
    RagEvaluationResult,
    _evaluation_cache_namespace,
)
from app.ingest.embedding import FakeEmbeddingAdapter
from app.main import create_app
from app.rag.generation import FakeAnswerGenerator
from app.rag.rerank import FakeRerankerClient
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate, VectorSearchClient
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluations import (
    EvaluationCaseCreateRequest,
    EvaluationDatasetCreateRequest,
    EvaluationDatasetManifest,
    EvaluationFailureCandidate,
    EvaluationFailurePromotionRequest,
    EvaluationFailureSeverity,
    EvaluationRunCreateRequest,
    StrategyComparisonMetric,
)
from app.schemas.rag import (
    RagAskCitation,
    RagAskConfidence,
    RagSearchResponse,
    RetrievalScoreSummary,
)
from app.services.evaluation_service import (
    STRATEGY_METRIC_SPECS,
    EvaluationService,
    _filter_graph_paths_for_source_chunk_ids,
    _graph_path_relevance_metric,
    _promotion_metadata,
    _strategy_metrics_summary_json,
)
from app.services.rag_service import RagService
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

    answer_only_metrics = calculate_metrics(
        EvaluationMetricInputs(
            case=EvaluationCase(
                case_id="answer_only",
                question="What is the canonical answer?",
                expected_keywords=(),
                required_citation=False,
                expected_answer="canonical answer",
            ),
            answer_text="The canonical answer is present.",
            citations=[],
            confidence=None,
            retrieval_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
        )
    )
    answer_only_by_name = {metric.metric_name: metric for metric in answer_only_metrics}
    assert answer_only_by_name["faithfulness"].metric_score == 1.0
    assert answer_only_by_name["context_precision"].metric_score == 1.0
    assert "canonical answer" not in str(answer_only_by_name["faithfulness"].details)

    with pytest.raises(ValueError):
        EvaluationRunCreateRequest(dataset_name="phase1.smoke", case_limit=1)


def test_phase2_strategy_fixture_manifest_and_metric_specs_are_safe() -> None:
    cases = load_evaluation_cases("phase2_strategy_smoke", case_limit=5)
    assert [case.case_id for case in cases] == [
        "dense_seed_stack",
        "keyword_heavy_ci",
        "citation_required_confidence",
        "no_context_expected",
        "future_hybrid_candidate",
    ]
    assert cases[0].tags == ("dense", "baseline")
    assert cases[0].metadata_json == {"expected_strategy": "dense"}
    assert cases[4].metadata_json == {"expected_strategy": "hybrid"}
    graph_cases = load_evaluation_cases("phase3_graph_multi_hop", case_limit=5)
    assert [case.case_id for case in graph_cases] == [
        "graph_fastapi_postgres_qdrant",
        "graph_worker_cache_relations",
    ]
    assert graph_cases[0].metadata_json == {
        "expected_strategy": "graph",
        "acceptable_strategies": ["graph", "hybrid"],
        "expected_entity_labels": ["FastAPI", "PostgreSQL", "Qdrant"],
        "expected_relation_types": ["uses", "stores"],
        "required_hop_count": 2,
    }

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "evaluation"
        / "fixtures"
        / "phase2_strategy_smoke.json"
    )
    manifest = EvaluationDatasetManifest.model_validate_json(
        fixture_path.read_text(encoding="utf-8")
    )
    assert manifest.dataset.dataset_name == "phase2_strategy_smoke"
    assert {spec.metric_name.value for spec in STRATEGY_METRIC_SPECS} >= {
        "recall_at_k",
        "mrr",
        "citation_coverage",
        "groundedness",
        "faithfulness",
        "no_context_rate",
        "p95_latency",
        "strategy_selection_accuracy",
        "graph_path_relevance",
        "graph_citation_coverage",
        "multi_hop_answerability",
        "cache_hit_rate",
        "cache_saved_latency",
        "entity_relation_quality_summary",
    }
    graph_fixture_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "evaluation"
        / "fixtures"
        / "phase3_graph_multi_hop.json"
    )
    graph_manifest = EvaluationDatasetManifest.model_validate_json(
        graph_fixture_path.read_text(encoding="utf-8")
    )
    assert graph_manifest.dataset.dataset_name == "phase3_graph_multi_hop"
    dumped = manifest.model_dump_json()
    dumped += graph_manifest.model_dump_json()
    assert "raw prompt" not in dumped.lower()
    assert "full context" not in dumped.lower()
    assert "raw chunk" not in dumped.lower()
    assert "secret" not in dumped.lower()


def test_strategy_metrics_summary_uses_p95_value_for_p95_latency() -> None:
    payload = _strategy_metrics_summary_json(
        strategies=["hybrid"],
        strategy_comparison=[
            StrategyComparisonMetric(
                strategy_type=RetrievalStrategy.HYBRID,
                metric_name="p95_latency",
                average=90.8,
                p50=88.0,
                p95=104.0,
                count=5,
            ),
            StrategyComparisonMetric(
                strategy_type=RetrievalStrategy.HYBRID,
                metric_name="recall_at_k",
                average=0.6,
                p50=1.0,
                p95=1.0,
                count=5,
            ),
        ],
        metric_summary={"p95_latency": 104.0, "recall_at_k": 0.6},
        case_count=5,
        succeeded_count=5,
        failed_count=0,
    )

    strategy_metrics = payload["strategy_metrics"]
    assert isinstance(strategy_metrics, dict)
    hybrid_summary = strategy_metrics["hybrid"]
    assert isinstance(hybrid_summary, dict)
    strategy_summary = hybrid_summary["metric_summary"]
    assert isinstance(strategy_summary, dict)
    assert strategy_summary["p95_latency"] == 104.0
    assert strategy_summary["recall_at_k"] == 0.6


def test_fixture_metadata_drives_agentic_strategy_accuracy() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _AgenticEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name="phase2_strategy_smoke",
                    case_limit=1,
                    strategies=["agentic_router"],
                ),
                user=user,
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-fixture-agentic",
            )
            assert result["status"] == "succeeded"
            strategy_accuracy = db.scalar(
                select(EvaluationResult).where(
                    EvaluationResult.strategy_type == "agentic_router",
                    EvaluationResult.metric_name == "strategy_selection_accuracy",
                )
            )
            assert strategy_accuracy is not None
            assert strategy_accuracy.metric_score is not None
            assert float(strategy_accuracy.metric_score) == 1.0
            assert strategy_accuracy.metric_detail_json is not None
            assert strategy_accuracy.metric_detail_json["not_applicable"] is False
            assert strategy_accuracy.metric_detail_json["expected_strategy"] == "dense"
    finally:
        engine.dispose()


def test_agentic_strategy_accuracy_uses_selected_strategy_not_execution() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _AgenticEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="agentic_selection_accuracy_dataset",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="fallback_execution_is_accepted",
                    question="fallback accepted target retrieval",
                    expected_keywords=["target"],
                    required_citation=True,
                    metadata_json={"expected_strategy": "dense"},
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["agentic_router"],
                    case_limit=1,
                ),
                user=user,
            )
            service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-selected-strategy-only",
            )
            accuracy = db.scalar(
                select(EvaluationResult).where(
                    EvaluationResult.strategy_type == "agentic_router",
                    EvaluationResult.metric_name == "strategy_selection_accuracy",
                )
            )
            assert accuracy is not None
            assert accuracy.metric_score is not None
            assert float(accuracy.metric_score) == 0.0
            assert accuracy.metric_detail_json is not None
            assert accuracy.metric_detail_json["selected_strategy"] == "hybrid"
            assert accuracy.metric_detail_json["execution_strategy"] == "dense"
    finally:
        engine.dispose()


def test_promotion_skips_source_case_metadata_changed_after_run() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _AgenticEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="agentic_changed_source_dataset",
                    source_type="manual",
                ),
                user=user,
            )
            created_case = service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="changed_source",
                    question="missing target retrieval",
                    expected_keywords=["missing"],
                    required_citation=True,
                    metadata_json={"expected_strategy": "sparse"},
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["agentic_router"],
                    case_limit=1,
                ),
                user=user,
            )
            service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-promotion-source-change",
            )
            source_case = db.get(EvaluationCaseModel, created_case.evaluation_case_id)
            assert source_case is not None
            source_case.metadata_json = {"expected_strategy": "dense"}
            db.commit()

            promoted = service.promote_failures(
                db,
                evaluation_run_id=created.evaluation_run_id,
                payload=EvaluationFailurePromotionRequest(
                    target_dataset_id=dataset.evaluation_dataset_id,
                    failure_types=["no_context"],
                    min_severity="medium",
                    limit=10,
                ),
            )
            assert promoted.created_count == 0
            assert promoted.skipped_count == 1
            assert promoted.items[0].result_code == "source_case_changed"
    finally:
        engine.dispose()


def test_rerank_failure_promotes_retrieval_exception_as_primary() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _RerankFailingRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="rerank_failure_dataset",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="rerank_failure_case",
                    question="rerank target retrieval",
                    expected_keywords=["target"],
                    required_citation=True,
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["dense"],
                    case_limit=1,
                ),
                user=user,
            )
            with pytest.raises(EvaluationFixtureError, match="all_cases_failed"):
                service.run_job(
                    db,
                    evaluation_run_id=created.evaluation_run_id,
                    request_id="test-rerank-failure",
                )
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            failure_types = {candidate.failure_type for candidate in detail.failure_candidates}
            assert "retrieval_exception" in failure_types
            assert "no_context" in failure_types

            target = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="rerank_failure_target",
                    source_type="manual",
                ),
                user=user,
            )
            promoted = service.promote_failures(
                db,
                evaluation_run_id=created.evaluation_run_id,
                payload=EvaluationFailurePromotionRequest(
                    target_dataset_id=target.evaluation_dataset_id,
                    min_severity="medium",
                    limit=10,
                ),
            )
            assert promoted.created_count == 1
            promoted_case = db.get(EvaluationCaseModel, promoted.items[0].promoted_case_id)
            assert promoted_case is not None
            assert promoted_case.metadata_json is not None
            assert promoted_case.metadata_json["failure_type"] == "retrieval_exception"
            assert promoted_case.metadata_json["failure_reason_codes"] == ["rerank_failed"]
    finally:
        engine.dispose()


def test_failed_only_strategy_is_preserved_in_summary() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _AgenticOnlyFailingRagService(),
                settings=Settings(app_env="test"),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    strategies=["dense", "agentic_router"],
                    case_limit=1,
                ),
                user=user,
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-failed-only-strategy",
            )
            assert result["status"] == "succeeded"
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.strategy_metrics_summary_json is not None
            agentic_summary = run.strategy_metrics_summary_json["strategy_metrics"][
                "agentic_router"
            ]
            assert agentic_summary["case_count"] == 1
            assert agentic_summary["succeeded_count"] == 0
            assert agentic_summary["failed_count"] == 1
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert any(
                metric.strategy_type == "agentic_router"
                and metric.metric_name == "evaluation_item_status"
                and metric.failed_count == 1
                for metric in detail.strategy_comparison
            )
    finally:
        engine.dispose()


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
    comparison = client.get(
        f"/api/v1/evaluations/runs/{body['data']['evaluation_run_id']}/strategy-comparison"
    )
    missing = client.get("/api/v1/evaluations/runs/999")

    assert listing.status_code == 200
    assert listing.json()["data"][0]["dataset_name"] == "phase1_smoke"
    assert listing.json()["data"][0]["case_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["data"]["case_count"] == 1
    assert detail.json()["data"]["items"] == []
    assert comparison.status_code == 200
    assert comparison.json()["data"]["strategies"] == ["dense"]
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


def test_evaluation_dataset_case_api_import_export_and_safe_validation(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = evaluation_client

    assert client.get("/api/v1/evaluations/datasets").status_code == 401

    _login_as(client, "viewer@example.com")
    viewer_csrf = _session_csrf(client)
    viewer_create = client.post(
        "/api/v1/evaluations/datasets",
        json={"dataset_name": "viewer_dataset", "description": "viewer should fail"},
        headers={"X-CSRF-Token": viewer_csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert viewer_create.status_code == 403

    _logout(client)
    _login_as(client, "admin@example.com")
    csrf = _session_csrf(client)
    dataset_response = client.post(
        "/api/v1/evaluations/datasets",
        json={
            "dataset_name": "phase2_manual",
            "description": "Manual Phase2 strategy dataset.",
            "version": "v1",
            "source_type": "manual",
            "metadata_json": {"owner": "phase2"},
        },
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert dataset_response.status_code == 201
    dataset_id = dataset_response.json()["data"]["evaluation_dataset_id"]

    case_response = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/cases",
        json={
            "case_key": "dense_case",
            "question": "What vector database is used by the Phase1 RAG stack?",
            "expected_keywords": ["Qdrant"],
            "expected_document_ids": [],
            "expected_chunk_ids": [],
            "required_citation": True,
            "tags": ["dense"],
            "metadata_json": {"expected_strategy": "dense"},
        },
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert case_response.status_code == 201
    case_id = case_response.json()["data"]["evaluation_case_id"]

    listing = client.get("/api/v1/evaluations/datasets")
    dataset_detail = client.get(f"/api/v1/evaluations/datasets/{dataset_id}")
    cases = client.get(f"/api/v1/evaluations/datasets/{dataset_id}/cases")
    case_detail = client.get(f"/api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}")
    nested_mismatch = client.get(f"/api/v1/evaluations/datasets/999/cases/{case_id}")

    assert listing.status_code == 200
    assert dataset_detail.status_code == 200
    assert dataset_detail.json()["data"]["case_count"] == 1
    assert cases.status_code == 200
    assert cases.json()["data"][0]["case_key"] == "dense_case"
    assert case_detail.status_code == 200
    assert nested_mismatch.status_code == 404

    clear_dataset_fields = client.patch(
        f"/api/v1/evaluations/datasets/{dataset_id}",
        json={"description": None, "metadata_json": None},
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    invalid_case_update = client.patch(
        f"/api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}",
        json={"expected_keywords": [], "expected_answer": None},
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert clear_dataset_fields.status_code == 200
    assert clear_dataset_fields.json()["data"]["description"] is None
    assert clear_dataset_fields.json()["data"]["metadata_json"] is None
    assert invalid_case_update.status_code == 422
    assert invalid_case_update.json()["error"]["code"] == "validation_error"

    run_response = client.post(
        "/api/v1/evaluations/runs",
        json={
            "evaluation_dataset_id": dataset_id,
            "strategy_type": "dense",
            "case_limit": 1,
            "trigger_type": "manual",
        },
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert run_response.status_code == 202
    run_detail = client.get(
        f"/api/v1/evaluations/runs/{run_response.json()['data']['evaluation_run_id']}"
    )
    assert run_detail.status_code == 200
    assert run_detail.json()["data"]["evaluation_dataset_id"] == dataset_id
    assert run_detail.json()["data"]["strategy_type"] == "dense"
    assert run_detail.json()["data"]["case_count"] == 1

    with session_factory() as db:
        run = db.get(EvaluationRun, run_response.json()["data"]["evaluation_run_id"])
        assert run is not None
        assert run.evaluation_dataset_id == dataset_id
        assert run.strategy_type == "dense"
        assert run.trigger_type == "manual"
        assert run.retrieval_settings_json == {
            "schema_version": "phase2.evaluation.v1",
            "strategy_type": "dense",
            "strategies": ["dense"],
            "metrics": [
                "recall_at_k",
                "mrr",
                "citation_coverage",
                "groundedness",
                "faithfulness",
                "no_context_rate",
                "p95_latency",
                "strategy_selection_accuracy",
                "fallback_rate",
                "budget_exhausted_rate",
                "sufficiency_score_avg",
                "retrieval_call_count_avg",
                "graph_path_relevance",
                "graph_citation_coverage",
                "multi_hop_answerability",
                "cache_hit_rate",
                "cache_saved_latency",
                "entity_relation_quality_summary",
            ],
            "cache_modes": ["default"],
            "strategy_targets": [
                {
                    "schema_version": "phase3.evaluation_target.v1",
                    "comparison_label": "dense",
                    "retrieval_strategy": "dense",
                    "cache_mode": "default",
                }
            ],
            "case_limit": 1,
            "top_k": None,
            "rerank_top_n": None,
            "runner_implementation": "phase3_graph_cache_strategy_evaluation_runner",
            "strategy_runner_enabled": True,
        }

    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "evaluation"
        / "fixtures"
        / "phase2_strategy_smoke.json"
    )
    manifest = json.loads(fixture_path.read_text(encoding="utf-8"))
    imported = client.post(
        "/api/v1/evaluations/datasets/import",
        json=manifest,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    imported_again = client.post(
        "/api/v1/evaluations/datasets/import",
        json=manifest,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert imported.status_code == 200
    assert imported_again.status_code == 200
    assert imported.json()["data"]["case_count"] == 5
    assert imported_again.json()["data"]["case_count"] == 5

    imported_dataset_id = imported.json()["data"]["evaluation_dataset_id"]
    exported = client.get(f"/api/v1/evaluations/datasets/{imported_dataset_id}/export")
    assert exported.status_code == 200
    exported_text = exported.text.lower()
    assert "phase2_strategy_smoke" in exported_text
    assert "raw prompt" not in exported_text
    assert "full context" not in exported_text
    assert "raw chunk" not in exported_text
    assert "secret" not in exported_text

    unsafe_manifest = dict(manifest)
    unsafe_manifest["cases"] = [
        dict(manifest["cases"][0]) | {"question": "Use OPENAI_API_KEY=sk-test here"}
    ]
    unsafe = client.post(
        "/api/v1/evaluations/datasets/import",
        json=unsafe_manifest,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert unsafe.status_code == 422

    archived_case = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}/archive",
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    archived_dataset = client.post(
        f"/api/v1/evaluations/datasets/{dataset_id}/archive",
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert archived_case.status_code == 200
    assert archived_case.json()["data"]["status"] == "archived"
    assert archived_dataset.status_code == 200
    assert archived_dataset.json()["data"]["status"] == "archived"

    with session_factory() as db:
        assert db.query(EvaluationDataset).filter_by(dataset_name="phase2_strategy_smoke").count()
        assert (
            db.query(EvaluationCaseModel)
            .filter_by(evaluation_dataset_id=imported_dataset_id)
            .count()
            == 5
        )


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
            assert run.strategy_type == "dense"
            assert run.strategy_metrics_summary_json is not None
            items = db.scalars(select(EvaluationRunItem)).all()
            results = db.scalars(select(EvaluationResult)).all()
            assert len(items) == 2
            assert all(item.status == "succeeded" for item in items)
            assert all(item.strategy_type == "dense" for item in items)
            assert all(item.metric_summary_json for item in items)
            assert {result.metric_name for result in results} >= {
                "faithfulness",
                "groundedness",
                "citation_coverage",
                "strategy_selection_accuracy",
            }
            assert all(result.strategy_type == "dense" for result in results)
            assert all(
                result.metric_detail_json == result.details_json
                for result in results
                if result.metric_name != "case_metadata"
            )
            response = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert response.case_count == 2
            assert response.items[0].case_id == "phase1_seed_stack"
            assert response.items[0].strategy_type == "dense"
            assert "raw prompt" not in response.model_dump_json().lower()
            assert "full context" not in response.model_dump_json().lower()
    finally:
        engine.dispose()


def test_evaluation_service_runs_persistent_dataset_cases() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _FakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="persistent_strategy",
                    description="Persistent strategy dataset.",
                    version="v1",
                    source_type="manual",
                    metadata_json={"owner": "phase2"},
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="persistent_dense",
                    question="What vector database is used by the Phase1 RAG stack?",
                    expected_answer=(
                        "Qdrant and deterministic fake adapters support "
                        "citation-aware retrieval traces."
                    ),
                    required_citation=True,
                    tags=["dense"],
                    metadata_json={"expected_strategy": "dense"},
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    dataset_name="persistent_strategy",
                    case_limit=1,
                    strategy_type="dense",
                ),
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
                request_id="test-persistent-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            item = db.scalar(select(EvaluationRunItem))
            assert item is not None
            assert item.evaluation_case_id is not None
            assert item.case_key == "persistent_dense"
            assert item.strategy_type == "dense"
            assert item.latency_breakdown_json is not None
            response = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            faithfulness = next(
                metric
                for metric in response.items[0].metrics
                if metric.metric_name == "faithfulness"
            )
            assert faithfulness.metric_score == 1.0
    finally:
        engine.dispose()


def test_evaluation_service_runs_dense_sparse_hybrid_strategy_comparison() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _StrategyAwareFakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="compare_strategy",
                    description="Compare dense sparse hybrid.",
                    version="v1",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="compare_case",
                    question="Which retrieval strategy finds the target chunk?",
                    expected_keywords=["target"],
                    expected_document_ids=[10],
                    expected_chunk_ids=[100],
                    required_citation=True,
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["dense", "sparse", "hybrid"],
                    metrics=[
                        "recall_at_k",
                        "mrr",
                        "citation_coverage",
                        "groundedness",
                        "faithfulness",
                        "no_context_rate",
                        "p95_latency",
                    ],
                    top_k=5,
                    rerank_top_n=3,
                    case_limit=1,
                ),
                user=user,
            )
            assert created.strategies == [
                RetrievalStrategy.DENSE,
                RetrievalStrategy.SPARSE,
                RetrievalStrategy.HYBRID,
            ]

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _StrategyAwareFakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-strategy-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "succeeded"
            assert run.strategy_metrics_summary_json is not None
            items = db.scalars(
                select(EvaluationRunItem).order_by(EvaluationRunItem.evaluation_run_item_id)
            ).all()
            assert [item.strategy_type for item in items] == ["dense", "sparse", "hybrid"]
            assert all(item.retrieval_run_id is not None for item in items)
            assert run.strategy_metrics_summary_json["case_count"] == 3
            assert run.strategy_metrics_summary_json["succeeded_count"] == 3
            retrieval_runs = db.scalars(
                select(RetrievalRun).order_by(RetrievalRun.retrieval_run_id)
            ).all()
            assert [retrieval.strategy_type for retrieval in retrieval_runs] == [
                "dense",
                "sparse",
                "hybrid",
            ]
            results = db.scalars(select(EvaluationResult)).all()
            assert {result.metric_name for result in results} >= {
                "recall_at_k",
                "mrr",
                "citation_coverage",
                "groundedness",
                "faithfulness",
                "no_context_rate",
                "p95_latency",
            }
            assert all(
                "target chunk" not in json.dumps(result.metric_detail_json).lower()
                for result in results
            )
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert detail.strategies == [
                RetrievalStrategy.DENSE,
                RetrievalStrategy.SPARSE,
                RetrievalStrategy.HYBRID,
            ]
            assert detail.case_count == 3
            assert detail.succeeded_count == 3
            assert detail.failed_count == 0
            comparison = service.get_strategy_comparison(
                db,
                evaluation_run_id=created.evaluation_run_id,
            )
            assert {metric.strategy_type for metric in comparison.metrics} >= {
                RetrievalStrategy.DENSE,
                RetrievalStrategy.SPARSE,
                RetrievalStrategy.HYBRID,
            }
            dense_recall = next(
                metric
                for metric in comparison.metrics
                if metric.strategy_type == RetrievalStrategy.DENSE
                and metric.metric_name == "recall_at_k"
            )
            assert dense_recall.average == 1.0
    finally:
        engine.dispose()


def test_evaluation_service_runs_graph_provider_and_cache_comparison_safely() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _GraphCacheAwareEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="graph_cache_compare",
                    description="Compare graph providers and cache modes.",
                    version="v1",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="graph_cache_case",
                    question="Which graph provider should be compared without raw output?",
                    expected_keywords=["graph"],
                    expected_chunk_ids=[100],
                    required_citation=True,
                    metadata_json={
                        "expected_strategy": "graph",
                        "acceptable_strategies": ["graph", "hybrid"],
                        "expected_entity_labels": ["FastAPI", "PostgreSQL", "Qdrant"],
                        "expected_relation_types": ["uses", "stores"],
                        "required_hop_count": 2,
                    },
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=[
                        "dense",
                        "hybrid",
                        "agentic_router",
                        "graph",
                        "graph_postgres",
                        "graph_neo4j",
                    ],
                    cache_modes=["warm", "disabled"],
                    metrics=[
                        "recall_at_k",
                        "citation_coverage",
                        "faithfulness",
                        "no_context_rate",
                        "p95_latency",
                        "fallback_rate",
                        "graph_path_relevance",
                        "graph_citation_coverage",
                        "multi_hop_answerability",
                        "cache_hit_rate",
                        "cache_saved_latency",
                        "entity_relation_quality_summary",
                    ],
                    case_limit=1,
                ),
                user=user,
            )
            assert "graph_postgres__cache_cold" in created.strategies
            assert "graph_neo4j__cache_warm" in created.strategies
            assert created.strategies.count("graph_postgres__cache_cold") == 1
            assert created.strategies[:3] == [
                "dense__cache_disabled",
                "dense__cache_cold",
                "dense__cache_warm",
            ]

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _GraphCacheAwareEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-graph-cache-eval",
            )
            assert result["status"] == "succeeded"
            assert result["case_count"] == 15
            assert result["succeeded_count"] == 15
            assert result["failed_count"] == 0

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _GraphCacheAwareEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "succeeded"
            assert run.strategy_metrics_summary_json is not None
            strategy_metrics = run.strategy_metrics_summary_json["strategy_metrics"]
            assert "graph_postgres__cache_cold" in strategy_metrics
            assert "graph_neo4j__cache_warm" in strategy_metrics
            assert "agentic_summary" in run.strategy_metrics_summary_json
            assert run.strategy_metrics_summary_json["provider_comparison"]["postgres"]
            assert run.strategy_metrics_summary_json["provider_comparison"]["neo4j"]
            assert (
                "graph_postgres__cache_cold"
                in run.strategy_metrics_summary_json["provider_comparison"]["postgres"][
                    "metric_summary_by_label"
                ]
            )
            assert set(run.strategy_metrics_summary_json["cache_comparison"]) == {
                "disabled",
                "cold",
                "warm",
            }
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert detail.failed_count == 0
            assert not any(
                str(candidate.metric_snapshot.get("evaluation_strategy_label", "")).startswith(
                    "graph_neo4j"
                )
                for candidate in detail.failure_candidates
            )
            assert {metric.comparison_label for metric in detail.strategy_comparison} >= {
                "graph_postgres__cache_cold",
                "graph_neo4j__cache_warm",
            }
            graph_relevance = next(
                metric
                for metric in detail.strategy_comparison
                if metric.comparison_label == "graph_postgres__cache_cold"
                and metric.metric_name == "graph_path_relevance"
            )
            assert graph_relevance.average == 1.0
            neo4j_graph_metric = next(
                metric
                for metric in detail.strategy_comparison
                if metric.comparison_label == "graph_neo4j__cache_warm"
                and metric.metric_name == "graph_path_relevance"
            )
            assert neo4j_graph_metric.not_applicable_count == 1
            neo4j_no_context = next(
                metric
                for metric in detail.strategy_comparison
                if metric.comparison_label == "graph_neo4j__cache_warm"
                and metric.metric_name == "no_context_rate"
            )
            assert neo4j_no_context.average is None
            assert neo4j_no_context.not_applicable_count == 1
            neo4j_fallback = next(
                metric
                for metric in detail.strategy_comparison
                if metric.comparison_label == "graph_neo4j__cache_warm"
                and metric.metric_name == "fallback_rate"
            )
            assert neo4j_fallback.average is None
            assert neo4j_fallback.not_applicable_count == 1
            cache_hit = next(
                metric
                for metric in detail.strategy_comparison
                if metric.comparison_label == "graph_postgres__cache_warm"
                and metric.metric_name == "cache_hit_rate"
            )
            assert cache_hit.average == 1.0
            persisted_details = json.dumps(
                [result.metric_detail_json for result in db.scalars(select(EvaluationResult)).all()]
            ).lower()
            assert "which graph provider" not in persisted_details
            assert "safe target citation preview" not in persisted_details
            assert "raw output" not in persisted_details
            assert "graph_store_provider" in persisted_details
    finally:
        engine.dispose()


def test_graph_path_relevance_is_not_applicable_without_expected_graph_hints() -> None:
    metric = _graph_path_relevance_metric(
        graph_paths=[
            GraphRetrievalPath(
                path_json={
                    "safe_entity_labels": ["FastAPI"],
                    "relation_types": ["uses"],
                    "depth": 1,
                },
                source_chunk_ids_json=[100],
            )
        ],
        metadata_json={},
        provider="postgres",
        reason_codes=[],
    )

    assert metric.metric_score is None
    assert metric.metric_label == "not_applicable"
    assert metric.details["reason_code"] == "graph_relevance_hints_missing"


def test_graph_path_metrics_only_use_selected_source_chunks() -> None:
    selected_path = GraphRetrievalPath(
        path_json={"safe_entity_labels": ["FastAPI"]},
        source_chunk_ids_json=[100, 101],
    )
    unselected_path = GraphRetrievalPath(
        path_json={"safe_entity_labels": ["Neo4j"]},
        source_chunk_ids_json=[200],
    )
    empty_source_path = GraphRetrievalPath(
        path_json={"safe_entity_labels": ["Qdrant"]},
        source_chunk_ids_json=[],
    )

    filtered = _filter_graph_paths_for_source_chunk_ids(
        [selected_path, unselected_path, empty_source_path],
        {101},
    )

    assert filtered == [selected_path]


def test_evaluation_cache_namespace_preserves_eval_suffix_when_long() -> None:
    suffix = "123456789.case_target_cache_warm"
    namespace = "retrieval-cache-namespace-" + ("long-segment-" * 8)
    result = _evaluation_cache_namespace(namespace, suffix)

    assert len(result) <= 80
    assert result.endswith(f".eval.{suffix}")
    assert result != f"{namespace}.eval.{suffix}"


def test_failure_promotion_metadata_preserves_safe_graph_hints() -> None:
    candidate = EvaluationFailureCandidate(
        evaluation_run_id=1,
        evaluation_run_item_id=2,
        evaluation_case_id=3,
        case_key="graph_case",
        question_hash="a" * 64,
        strategy_type=RetrievalStrategy.GRAPH,
        failure_type="graph_path_relevance_low",
        severity=EvaluationFailureSeverity.MEDIUM,
        failure_reason_codes=["graph_path_relevance_low"],
        metric_snapshot={"metric_name": "graph_path_relevance"},
        recommended_tags=["strategy:graph"],
        promotion_key="graph-case-key",
    )

    metadata = _promotion_metadata(
        candidate,
        {
            "expected_strategy": "graph",
            "acceptable_strategies": ["graph", "hybrid"],
            "expected_entity_labels": ["FastAPI", "PostgreSQL", "FastAPI"],
            "expected_relation_types": ["uses", "stores"],
            "required_hop_count": 2,
        },
    )

    assert metadata["expected_entity_labels"] == ["FastAPI", "PostgreSQL"]
    assert metadata["expected_relation_types"] == ["uses", "stores"]
    assert metadata["required_hop_count"] == 2


def test_evaluation_cache_namespace_isolated_per_case_target() -> None:
    engine, session_factory = _session_factory()
    attempts: list[tuple[str, str | None]] = []

    class _RecordingCacheAttemptRagService(_GraphCacheAwareEvaluationRagService):
        def evaluate_strategy_target(
            self,
            db: Session,
            *,
            question: str,
            request_id: str | None,
            target: object,
            top_k: int | None = None,
            rerank_top_n: int | None = None,
            evaluation_run_id: int | None = None,
            cache_attempt_id: str | None = None,
        ) -> RagEvaluationResult:
            attempts.append((_target_cache_mode_value(target), cache_attempt_id))
            return super().evaluate_strategy_target(
                db,
                question=question,
                request_id=request_id,
                target=target,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
                evaluation_run_id=evaluation_run_id,
                cache_attempt_id=cache_attempt_id,
            )

    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _RecordingCacheAttemptRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="graph_cache_namespace",
                    source_type="manual",
                ),
                user=user,
            )
            for case_key in ("same_question_a", "same_question_b"):
                service.create_case(
                    db,
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    payload=EvaluationCaseCreateRequest(
                        case_key=case_key,
                        question="shared graph cache question",
                        expected_keywords=["graph"],
                        expected_chunk_ids=[100],
                        required_citation=True,
                        metadata_json={
                            "expected_entity_labels": ["FastAPI"],
                            "expected_relation_types": ["uses"],
                        },
                    ),
                )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["graph_postgres"],
                    cache_modes=["warm"],
                    metrics=["cache_hit_rate", "cache_saved_latency", "p95_latency"],
                    case_limit=2,
                ),
                user=user,
            )
            assert created.strategies == [
                "graph_postgres__cache_cold",
                "graph_postgres__cache_warm",
            ]

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _RecordingCacheAttemptRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-cache-namespace",
            )
            assert result["status"] == "succeeded"

        assert [mode for mode, _ in attempts] == ["cold", "warm", "cold", "warm"]
        assert attempts[0][1] == attempts[1][1]
        assert attempts[2][1] == attempts[3][1]
        assert attempts[0][1] != attempts[2][1]
    finally:
        engine.dispose()


def test_evaluation_service_preserves_retrieval_only_runner_for_default_search_targets() -> None:
    engine, session_factory = _session_factory()
    calls: list[str] = []

    class _RecordingSearchEvaluationRagService(_StrategyAwareFakeEvaluationRagService):
        def evaluate_strategy_target(
            self,
            db: Session,
            *,
            question: str,
            request_id: str | None,
            target: object,
            top_k: int | None = None,
            rerank_top_n: int | None = None,
            evaluation_run_id: int | None = None,
            cache_attempt_id: str | None = None,
        ) -> RagEvaluationResult:
            del evaluation_run_id, cache_attempt_id
            calls.append("target")
            strategy = getattr(target, "retrieval_strategy", RetrievalStrategy.DENSE)
            if not isinstance(strategy, RetrievalStrategy):
                strategy = RetrievalStrategy(str(strategy))
            return self.evaluate_question(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )

        def evaluate_strategy(
            self,
            db: Session,
            *,
            question: str,
            request_id: str | None,
            strategy_type: RetrievalStrategy,
            top_k: int | None = None,
            rerank_top_n: int | None = None,
        ) -> RagEvaluationResult:
            calls.append("strategy")
            return self.evaluate_question(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )

    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _RecordingSearchEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="retrieval_only_runner",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="dense_runner",
                    question="Which runner should dense evaluation use?",
                    expected_keywords=["dense"],
                    expected_chunk_ids=[100],
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["dense"],
                    metrics=["recall_at_k"],
                    case_limit=1,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _RecordingSearchEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-retrieval-only-runner",
            )

        assert result["status"] == "succeeded"
        assert calls == ["strategy"]
    finally:
        engine.dispose()


def test_evaluation_runner_honors_metric_selection_and_bounds_request_ids() -> None:
    engine, session_factory = _session_factory()
    long_case_key = "case_" + ("x" * 115)
    long_request_id = "request-" + ("r" * 90)
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _StrategyAwareFakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="custom_metric_strategy",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key=long_case_key,
                    question="Which retrieval strategy finds the target?",
                    expected_keywords=["target"],
                    expected_document_ids=[10],
                    expected_chunk_ids=[100],
                    required_citation=True,
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["dense", "sparse"],
                    metrics=["recall_at_k"],
                    case_limit=1,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _StrategyAwareFakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id=long_request_id,
            )
            assert result["case_count"] == 2
            assert result["succeeded_count"] == 2

        with session_factory() as db:
            detail = EvaluationService(
                rag_service_factory=lambda settings, db: _StrategyAwareFakeEvaluationRagService(),
                settings=Settings(app_env="test"),
            ).get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert detail.case_count == 2
            assert detail.metric_names == ["recall_at_k"]
            results = db.scalars(select(EvaluationResult)).all()
            assert {result.metric_name for result in results} == {"recall_at_k"}
            retrieval_runs = db.scalars(select(RetrievalRun)).all()
            assert len(retrieval_runs) == 2
            assert all(run.request_id is not None for run in retrieval_runs)
            assert all(len(str(run.request_id)) <= 100 for run in retrieval_runs)
            assert len({run.request_id for run in retrieval_runs}) == 2
    finally:
        engine.dispose()


def test_evaluation_service_executes_real_dense_sparse_hybrid_retrieval_paths() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            logical = LogicalDocument(
                owner_user_id=user.user_id,
                title="Strategy runner source",
                status="active",
            )
            db.add(logical)
            db.flush()
            version = DocumentVersion(
                logical_document_id=logical.logical_document_id,
                version_no=1,
                content_hash="b" * 64,
                status="ready",
                is_active=True,
                file_name="strategy-runner.md",
                mime_type="text/markdown",
                file_size_bytes=100,
                created_by=user.user_id,
            )
            db.add(version)
            db.flush()
            chunk = DocumentChunk(
                document_version_id=version.document_version_id,
                chunk_index=0,
                chunk_hash=hashlib.sha256(b"alpha target retrieval").hexdigest(),
                content_text="alpha target retrieval qdrant deterministic citation",
                modality="text",
            )
            db.add(chunk)
            db.flush()

            service = EvaluationService(
                settings=Settings(
                    app_env="test",
                    embedding_provider="fake",
                    rerank_provider="fake",
                    sparse_enabled=True,
                    hybrid_enabled=True,
                )
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="real_strategy_paths",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="real_paths_case",
                    question="alpha target retrieval",
                    expected_keywords=["alpha", "target"],
                    expected_document_ids=[logical.logical_document_id],
                    expected_chunk_ids=[chunk.document_chunk_id],
                    required_citation=True,
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["dense", "sparse", "hybrid"],
                    case_limit=1,
                    top_k=5,
                    rerank_top_n=3,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                settings=Settings(
                    app_env="test",
                    embedding_provider="fake",
                    rerank_provider="fake",
                    sparse_enabled=True,
                    hybrid_enabled=True,
                )
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-real-strategy-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            items = db.scalars(
                select(EvaluationRunItem).order_by(EvaluationRunItem.evaluation_run_item_id)
            ).all()
            assert [item.strategy_type for item in items] == ["dense", "sparse", "hybrid"]
            retrieval_runs = db.scalars(
                select(RetrievalRun).order_by(RetrievalRun.retrieval_run_id)
            ).all()
            assert [run.strategy_type for run in retrieval_runs] == [
                "dense",
                "sparse",
                "hybrid",
            ]
            assert all(run.query_plan_json for run in retrieval_runs)
            assert "alpha target retrieval" not in json.dumps(
                [run.query_plan_json for run in retrieval_runs]
            )
    finally:
        engine.dispose()


def test_evaluation_strategy_runner_treats_no_context_as_metric_outcome() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _NoContextStrategyRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="no_context_strategy_dataset",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="no_context_case",
                    question="zzzznevermatchlexical",
                    expected_keywords=["zzzznevermatchlexical"],
                    required_citation=True,
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["sparse"],
                    case_limit=1,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _NoContextStrategyRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-no-context-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "succeeded"
            items = db.scalars(select(EvaluationRunItem)).all()
            assert len(items) == 1
            assert items[0].status == "succeeded"
            assert items[0].error_code is None
            no_context = db.scalar(
                select(EvaluationResult).where(
                    EvaluationResult.evaluation_run_item_id == items[0].evaluation_run_item_id,
                    EvaluationResult.metric_name == "no_context_rate",
                )
            )
            assert no_context is not None
            assert no_context.metric_score is not None
            assert float(no_context.metric_score) == 1.0
            assert run.strategy_metrics_summary_json is not None
            assert run.strategy_metrics_summary_json["failed_count"] == 0
    finally:
        engine.dispose()


def test_agentic_evaluation_metrics_and_failure_promotion_are_idempotent() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _AgenticEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="agentic_eval_dataset",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="agentic_expected_hybrid",
                    question="hybrid target retrieval",
                    expected_keywords=["hybrid", "target"],
                    expected_document_ids=[10],
                    expected_chunk_ids=[100],
                    required_citation=True,
                    metadata_json={
                        "expected_strategy": "hybrid",
                        "acceptable_strategies": ["hybrid", "dense"],
                    },
                ),
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="agentic_missing_context",
                    question="missing target retrieval",
                    expected_keywords=["missing"],
                    required_citation=True,
                    metadata_json={
                        "expected_strategy": "sparse",
                        "acceptable_strategies": ["sparse", "hybrid"],
                    },
                ),
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="agentic_strategy_mismatch",
                    question="dense target retrieval",
                    expected_keywords=["target"],
                    required_citation=True,
                    metadata_json={
                        "expected_strategy": "sparse",
                        "acceptable_strategies": ["sparse", "hybrid"],
                    },
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=["dense", "sparse", "hybrid", "agentic_router"],
                    case_limit=3,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _AgenticEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-agentic-eval",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            service = EvaluationService(settings=Settings(app_env="test"))
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.strategy_metrics_summary_json is not None
            assert run.strategy_metrics_summary_json["agentic_summary"]["fallback_rate"] == 1.0
            assert (
                run.strategy_metrics_summary_json["agentic_summary"]["budget_exhausted_rate"]
                == 0.333333
            )
            assert (
                run.strategy_metrics_summary_json["agentic_summary"]["strategy_selection_accuracy"]
                == 0.5
            )
            items = db.scalars(
                select(EvaluationRunItem).order_by(EvaluationRunItem.evaluation_run_item_id)
            ).all()
            assert len(items) == 12
            assert {item.strategy_type for item in items} == {
                "dense",
                "sparse",
                "hybrid",
                "agentic_router",
            }
            agentic_results = db.scalars(
                select(EvaluationResult).where(EvaluationResult.strategy_type == "agentic_router")
            ).all()
            assert {result.metric_name for result in agentic_results} >= {
                "strategy_selection_accuracy",
                "fallback_rate",
                "budget_exhausted_rate",
                "sufficiency_score_avg",
                "retrieval_call_count_avg",
            }
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert any(
                metric.strategy_type == "agentic_router" and metric.metric_name == "fallback_rate"
                for metric in detail.strategy_comparison
            )
            assert any(
                candidate.failure_type == "strategy_selection_incorrect"
                for candidate in detail.failure_candidates
            )
            eligible_candidates = [
                candidate
                for candidate in detail.failure_candidates
                if candidate.severity.value in {"medium", "high"}
            ]
            unique_candidate_item_ids = {
                candidate.evaluation_run_item_id for candidate in eligible_candidates
            }
            assert len(eligible_candidates) > len(unique_candidate_item_ids)
            admin_user = db.scalar(select(User).where(User.email == "admin@example.com"))
            assert admin_user is not None
            bulk_target = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="agentic_bulk_failure_target",
                    source_type="manual",
                ),
                user=admin_user,
            )
            bulk_promoted = service.promote_failures(
                db,
                evaluation_run_id=created.evaluation_run_id,
                payload=EvaluationFailurePromotionRequest(
                    target_dataset_id=bulk_target.evaluation_dataset_id,
                    min_severity="medium",
                    limit=50,
                ),
            )
            assert bulk_promoted.created_count == len(unique_candidate_item_ids)
            selected_candidate = next(
                candidate
                for candidate in eligible_candidates
                if candidate.failure_type == "strategy_selection_incorrect"
            )
            keyed_target = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="agentic_keyed_failure_target",
                    source_type="manual",
                ),
                user=admin_user,
            )
            keyed_promoted = service.promote_failures(
                db,
                evaluation_run_id=created.evaluation_run_id,
                payload=EvaluationFailurePromotionRequest(
                    target_dataset_id=keyed_target.evaluation_dataset_id,
                    promotion_keys=[selected_candidate.promotion_key],
                    min_severity="medium",
                    limit=10,
                ),
            )
            assert keyed_promoted.created_count == 1
            assert keyed_promoted.items[0].promotion_key == selected_candidate.promotion_key
            assert keyed_promoted.items[0].failure_type == "strategy_selection_incorrect"
            promoted = service.promote_failures(
                db,
                evaluation_run_id=created.evaluation_run_id,
                payload=EvaluationFailurePromotionRequest(
                    target_dataset_id=dataset.evaluation_dataset_id,
                    failure_types=["strategy_selection_incorrect"],
                    min_severity="medium",
                    limit=10,
                ),
            )
            assert promoted.created_count == 1
            promoted_again = service.promote_failures(
                db,
                evaluation_run_id=created.evaluation_run_id,
                payload=EvaluationFailurePromotionRequest(
                    target_dataset_id=dataset.evaluation_dataset_id,
                    failure_types=["strategy_selection_incorrect"],
                    min_severity="medium",
                    limit=10,
                ),
            )
            assert promoted_again.created_count == 0
            assert promoted_again.skipped_count == 1
            promoted_case = db.get(EvaluationCaseModel, promoted.items[0].promoted_case_id)
            assert promoted_case is not None
            assert promoted_case.metadata_json is not None
            assert promoted_case.metadata_json["source"] == "failure_promoted"
            assert promoted_case.metadata_json["expected_strategy"] == "sparse"
            assert promoted_case.metadata_json["acceptable_strategies"] == [
                "sparse",
                "hybrid",
            ]
            assert "raw chunk" not in json.dumps(promoted_case.metadata_json).lower()
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
            assert items[1].case_key == "phase1_seed_ci"
            assert run.strategy_metrics_summary_json is not None
            dense_summary = run.strategy_metrics_summary_json["strategy_metrics"]["dense"]
            assert dense_summary["case_count"] == 2
            assert dense_summary["succeeded_count"] == 1
            assert dense_summary["failed_count"] == 1
    finally:
        engine.dispose()


def test_evaluation_runner_compares_tool_langchain_and_langgraph_agentic_strategies() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(settings=Settings(app_env="test"))
            dataset = service.create_dataset(
                db,
                payload=EvaluationDatasetCreateRequest(
                    dataset_name="tool_agentic_strategy_compare",
                    source_type="manual",
                ),
                user=user,
            )
            service.create_case(
                db,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                payload=EvaluationCaseCreateRequest(
                    case_key="tool_agentic_case",
                    question="Compare tool agentic retrieval",
                    expected_keywords=["target"],
                    expected_document_ids=[10],
                    expected_chunk_ids=[100],
                    required_citation=True,
                ),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    strategies=[
                        "llm_tool_orchestrator",
                        "langchain_agentic",
                        "langgraph_agentic",
                    ],
                    case_limit=1,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: _ToolAgenticEvaluationRagService(),
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-tool-agentic-compare",
            )
            assert result["status"] == "succeeded"

        with session_factory() as db:
            service = EvaluationService(settings=Settings(app_env="test"))
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.strategy_metrics_summary_json is not None
            assert run.strategy_metrics_summary_json["case_count"] == 3
            strategy_metrics = run.strategy_metrics_summary_json["strategy_metrics"]
            assert set(strategy_metrics) >= {
                "llm_tool_orchestrator",
                "langchain_agentic",
                "langgraph_agentic",
            }
            assert (
                strategy_metrics["llm_tool_orchestrator"]["metric_summary"]["fallback_rate"] == 0.0
            )
            assert strategy_metrics["langchain_agentic"]["metric_summary"]["fallback_rate"] == 0.0
            assert strategy_metrics["langgraph_agentic"]["metric_summary"]["fallback_rate"] == 0.0
            items = db.scalars(
                select(EvaluationRunItem).order_by(EvaluationRunItem.evaluation_run_item_id)
            ).all()
            assert [item.strategy_type for item in items] == [
                "llm_tool_orchestrator",
                "langchain_agentic",
                "langgraph_agentic",
            ]
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            assert detail.strategies == [
                RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
                RetrievalStrategy.LANGCHAIN_AGENTIC,
                RetrievalStrategy.LANGGRAPH_AGENTIC,
            ]
            assert {metric.strategy_type for metric in detail.strategy_comparison} >= {
                RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
                RetrievalStrategy.LANGCHAIN_AGENTIC,
                RetrievalStrategy.LANGGRAPH_AGENTIC,
            }
    finally:
        engine.dispose()


def test_langchain_agentic_evaluation_marks_retrieval_run_failed_on_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory = _session_factory()
    try:
        service = RagService(
            settings=Settings(app_env="test"),
            embedding_adapter=FakeEmbeddingAdapter(dimension=4),
            vector_client=_FakeVectorClient(),
            reranker=FakeRerankerClient(),
            answer_generator=FakeAnswerGenerator(),
        )

        def fail_langchain_retrieval(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise RuntimeError("synthetic_langchain_failure")

        monkeypatch.setattr(service, "_retrieve_langchain_agentic", fail_langchain_retrieval)
        evaluator = EvaluationRagQuestionService(service)

        with session_factory() as db:
            with pytest.raises(RuntimeError, match="synthetic_langchain_failure"):
                evaluator.evaluate_question(
                    db,
                    question="Compare LangChain failure handling",
                    request_id="test-langchain-internal-error",
                    strategy_type=RetrievalStrategy.LANGCHAIN_AGENTIC,
                )

            run = db.scalar(
                select(RetrievalRun).where(
                    RetrievalRun.request_id == "test-langchain-internal-error"
                )
            )
            assert run is not None
            assert run.status == "failed"
            assert run.error_code == "internal_error"
            assert run.finished_at is not None
    finally:
        engine.dispose()


def test_agentic_router_evaluation_uses_graph_service_search() -> None:
    engine, session_factory = _session_factory()

    class _RecordingGraphSearchService:
        def __init__(self) -> None:
            self.strategies: list[str] = []

        def search(
            self,
            db: Session,
            *,
            payload: Any,
            request_id: str | None,
        ) -> RagSearchResponse:
            query = str(payload.query)
            strategy_value = str(payload.strategy.value)
            self.strategies.append(strategy_value)
            run = _create_fake_retrieval_run(
                db,
                question=query,
                status="succeeded",
                request_id=request_id,
                strategy_type=strategy_value,
                retrieval_score_summary={
                    "requested_top_k": 5,
                    "qdrant_candidate_count": 0,
                    "post_filter_candidate_count": 0,
                    "selected_count": 0,
                    "excluded_by_rdb_check_count": 0,
                },
            )
            db.commit()
            db.refresh(run)
            return RagSearchResponse(
                retrieval_run_id=run.retrieval_run_id,
                status="succeeded",
                retrieval_score_summary=RetrievalScoreSummary(
                    requested_top_k=5,
                    qdrant_candidate_count=0,
                    post_filter_candidate_count=0,
                    selected_count=0,
                    excluded_by_rdb_check_count=0,
                ),
                items=[],
            )

    try:
        graph_service = _RecordingGraphSearchService()
        service = RagService(
            settings=Settings(app_env="test"),
            embedding_adapter=FakeEmbeddingAdapter(dimension=4),
            vector_client=_FakeVectorClient(),
            reranker=FakeRerankerClient(),
            answer_generator=FakeAnswerGenerator(),
        )
        evaluator = EvaluationRagQuestionService(service, graph_service=cast(Any, graph_service))
        with session_factory() as db:
            result = evaluator.answer_question_with_strategy(
                db,
                question="agentic graph routing",
                request_id="test-agentic-graph-routing",
                strategy_type=RetrievalStrategy.AGENTIC_ROUTER,
            )
            run = db.scalar(
                select(RetrievalRun).where(RetrievalRun.request_id == "test-agentic-graph-routing")
            )

        assert graph_service.strategies == [RetrievalStrategy.AGENTIC_ROUTER.value]
        assert result.status == "failed"
        assert result.error_code == "no_context_found"
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "no_context_found"
    finally:
        engine.dispose()


def test_cached_search_target_uses_retrieval_only_evaluation_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory = _session_factory()
    search_calls: list[tuple[str, bool, str]] = []
    service = RagService(
        settings=Settings(app_env="test"),
        embedding_adapter=FakeEmbeddingAdapter(dimension=4),
        vector_client=_FakeVectorClient(),
        reranker=FakeRerankerClient(),
        answer_generator=FakeAnswerGenerator(),
    )
    evaluator = EvaluationRagQuestionService(service)

    def fail_answer_path(*args: Any, **kwargs: Any) -> RagEvaluationResult:
        del args, kwargs
        raise AssertionError("cached search targets must stay on evaluate_strategy")

    def fake_search(
        db: Session,
        *,
        payload: Any,
        request_id: str | None,
    ) -> RagSearchResponse:
        search_calls.append(
            (
                str(payload.strategy.value),
                service.settings.retrieval_cache_enabled,
                service.settings.retrieval_cache_namespace,
            )
        )
        run = _create_fake_retrieval_run(
            db,
            question=str(payload.query),
            status="succeeded",
            request_id=request_id,
            strategy_type=str(payload.strategy.value),
            cache_summary_json={
                "schema_version": "phase3.retrieval_cache.v1",
                "enabled": True,
                "status": "miss",
                "reason": "evaluation_cache_mode",
            },
        )
        db.commit()
        db.refresh(run)
        return RagSearchResponse(
            retrieval_run_id=run.retrieval_run_id,
            status="succeeded",
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=0,
                post_filter_candidate_count=0,
                selected_count=0,
                excluded_by_rdb_check_count=0,
            ),
            items=[],
        )

    monkeypatch.setattr(evaluator, "answer_question_with_strategy", fail_answer_path)
    monkeypatch.setattr(service, "search", fake_search)

    try:
        target = SimpleNamespace(
            retrieval_strategy=RetrievalStrategy.DENSE,
            graph_store_provider=None,
            cache_mode="warm",
        )
        with session_factory() as db:
            result = evaluator.evaluate_strategy_target(
                db,
                question="cached dense search target",
                request_id="test-cached-search-target",
                target=target,
                evaluation_run_id=42,
                cache_attempt_id="case-cache",
            )

        assert result.status == "succeeded"
        assert result.answer_text == ""
        assert result.error_code == "no_context_found"
        assert search_calls == [
            (
                RetrievalStrategy.DENSE.value,
                True,
                "rag.retrieval.eval.42.case-cache",
            )
        ]
    finally:
        engine.dispose()


def test_langchain_agentic_evaluation_populates_retrieved_items_for_metrics() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            logical = LogicalDocument(
                owner_user_id=user.user_id,
                title="LangChain evaluation source",
                status="active",
            )
            db.add(logical)
            db.flush()
            version = DocumentVersion(
                logical_document_id=logical.logical_document_id,
                version_no=1,
                content_hash="c" * 64,
                status="ready",
                is_active=True,
                file_name="langchain-eval.md",
                mime_type="text/markdown",
                file_size_bytes=100,
                created_by=user.user_id,
            )
            db.add(version)
            db.flush()
            chunk = DocumentChunk(
                document_version_id=version.document_version_id,
                chunk_index=0,
                chunk_hash=hashlib.sha256(b"langchain eval recall marker").hexdigest(),
                content_text="langchain eval recall marker qdrant citation evidence",
                modality="text",
            )
            db.add(chunk)
            db.flush()
            logical_document_id = logical.logical_document_id
            document_chunk_id = chunk.document_chunk_id
            db.commit()

            service = RagService(
                settings=Settings(
                    app_env="test",
                    embedding_provider="fake",
                    embedding_fake_dimension=4,
                    retrieval_top_k_default=1,
                    retrieval_top_k_max=5,
                    rerank_provider="fake",
                    rerank_top_n_default=1,
                    rerank_top_n_max=5,
                    qdrant_collection_name="document_chunks",
                    generation_provider="fake",
                    langchain_agentic_enabled=True,
                    sparse_enabled=False,
                    hybrid_enabled=False,
                ),
                embedding_adapter=FakeEmbeddingAdapter(dimension=4),
                vector_client=_FakeVectorClient(
                    [
                        VectorSearchCandidate(
                            document_chunk_id=document_chunk_id,
                            retrieval_score=0.91,
                            qdrant_order=1,
                            payload={},
                        )
                    ]
                ),
                reranker=FakeRerankerClient(),
                answer_generator=FakeAnswerGenerator(),
            )
            evaluator = EvaluationRagQuestionService(service)

            result = evaluator.evaluate_question(
                db,
                question="langchain eval recall marker",
                request_id="test-langchain-retrieved-items",
                strategy_type=RetrievalStrategy.LANGCHAIN_AGENTIC,
            )

        assert result.status == "succeeded"
        assert result.retrieved_items == [
            RetrievedEvaluationItem(
                document_chunk_id=document_chunk_id,
                logical_document_id=logical_document_id,
                rank_order=1,
                snippet="langchain eval recall marker qdrant citation evidence",
            )
        ]
        metrics = calculate_metrics(
            EvaluationMetricInputs(
                case=EvaluationCase(
                    case_id="langchain_retrieved_items",
                    question="langchain eval recall marker",
                    expected_keywords=(),
                    required_citation=True,
                    expected_document_ids=(logical_document_id,),
                    expected_chunk_ids=(document_chunk_id,),
                ),
                answer_text=result.answer_text,
                citations=result.citations,
                confidence=result.confidence,
                retrieval_summary=result.retrieval_score_summary,
                retrieved_items=result.retrieved_items,
            )
        )
        by_name = {metric.metric_name: metric for metric in metrics}
        assert by_name["recall_at_k"].metric_score == 1.0
        assert by_name["mrr"].metric_score == 1.0
    finally:
        engine.dispose()


def test_evaluation_create_allows_agentic_strategies_and_rejects_fallback_dense() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(settings=Settings(app_env="test"))
            with pytest.raises(ValueError):
                EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    case_limit=1,
                    strategy_type="fallback_dense",
                )
            with pytest.raises(ValueError):
                EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    case_limit=1,
                    strategies=["fallback_dense"],
                )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    case_limit=1,
                    strategies=[
                        "dense",
                        "sparse",
                        "hybrid",
                        "agentic_router",
                        "llm_tool_orchestrator",
                        "langchain_agentic",
                        "langgraph_agentic",
                    ],
                ),
                user=user,
            )
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.status == "queued"
            assert run.metrics_config is not None
            assert run.metrics_config["strategies"] == [
                "dense",
                "sparse",
                "hybrid",
                "agentic_router",
                "llm_tool_orchestrator",
                "langchain_agentic",
                "langgraph_agentic",
            ]
    finally:
        engine.dispose()


def test_evaluation_api_allows_agentic_strategies_and_rejects_fallback_dense_strategies(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = evaluation_client
    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)

    accepted = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "strategies": [
                "dense",
                "llm_tool_orchestrator",
                "langchain_agentic",
                "langgraph_agentic",
            ],
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert accepted.status_code == 202
    assert accepted.json()["data"]["strategies"] == [
        "dense",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    ]

    for payload in (
        {"dataset_name": "phase1_smoke", "case_limit": 1, "strategy_type": "fallback_dense"},
        {"dataset_name": "phase1_smoke", "case_limit": 1, "strategies": ["fallback_dense"]},
    ):
        response = client.post(
            "/api/v1/evaluations/runs",
            json=payload,
            headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


def test_evaluation_failure_candidate_and_promotion_api(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = evaluation_client
    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)

    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert user is not None
        service = EvaluationService(
            rag_service_factory=lambda settings, db: _AgenticEvaluationRagService(),
            settings=Settings(app_env="test"),
        )
        dataset = service.create_dataset(
            db,
            payload=EvaluationDatasetCreateRequest(
                dataset_name="agentic_api_dataset",
                source_type="manual",
            ),
            user=user,
        )
        service.create_case(
            db,
            evaluation_dataset_id=dataset.evaluation_dataset_id,
            payload=EvaluationCaseCreateRequest(
                case_key="agentic_api_missing",
                question="missing target retrieval",
                expected_keywords=["missing"],
                required_citation=True,
                metadata_json={"expected_strategy": "sparse"},
            ),
        )
        created = service.create_run(
            db,
            payload=EvaluationRunCreateRequest(
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                strategies=["agentic_router"],
                case_limit=1,
            ),
            user=user,
        )
        service.run_job(
            db,
            evaluation_run_id=created.evaluation_run_id,
            request_id="test-agentic-api-eval",
        )
        dataset_id = dataset.evaluation_dataset_id
        run_id = created.evaluation_run_id

    candidates = client.get(f"/api/v1/evaluations/runs/{run_id}/failure-candidates")
    assert candidates.status_code == 200
    assert candidates.json()["data"]["candidates"]

    missing_csrf = client.post(
        f"/api/v1/evaluations/runs/{run_id}/promote-failures",
        json={"target_dataset_id": dataset_id, "failure_types": ["no_context"]},
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert missing_csrf.status_code == 403

    promoted = client.post(
        f"/api/v1/evaluations/runs/{run_id}/promote-failures",
        json={"target_dataset_id": dataset_id, "failure_types": ["no_context"]},
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert promoted.status_code == 200
    assert promoted.json()["data"]["created_count"] == 1
    promoted_again = client.post(
        f"/api/v1/evaluations/runs/{run_id}/promote-failures",
        json={"target_dataset_id": dataset_id, "failure_types": ["no_context"]},
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert promoted_again.status_code == 200
    assert promoted_again.json()["data"]["created_count"] == 0
    assert promoted_again.json()["data"]["skipped_count"] == 1


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


def test_evaluation_service_translates_unique_integrity_errors_to_conflict() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                repository=_IntegrityErrorEvaluationRepository(),
                settings=Settings(app_env="test"),
            )

            with pytest.raises(ConflictError):
                service.create_dataset(
                    db,
                    payload=EvaluationDatasetCreateRequest(
                        dataset_name="race_dataset",
                        description="Concurrent create race.",
                    ),
                    user=user,
                )

            dataset = EvaluationDataset(
                dataset_name="race_dataset",
                description="Existing dataset.",
                version="v1",
                source_type="manual",
                status="active",
                created_by=user.user_id,
            )
            db.add(dataset)
            db.commit()
            with pytest.raises(ConflictError):
                service.create_case(
                    db,
                    evaluation_dataset_id=dataset.evaluation_dataset_id,
                    payload=EvaluationCaseCreateRequest(
                        case_key="race_case",
                        question="What should conflict?",
                        expected_keywords=["conflict"],
                    ),
                )
    finally:
        engine.dispose()


class _IntegrityErrorEvaluationRepository(EvaluationRepository):
    def create_dataset(self, *args: Any, **kwargs: Any) -> EvaluationDataset:
        raise IntegrityError("insert evaluation dataset", {}, Exception("unique"))

    def create_case(self, *args: Any, **kwargs: Any) -> EvaluationCaseModel:
        raise IntegrityError("insert evaluation case", {}, Exception("unique"))


class _FakeVectorClient(VectorSearchClient):
    def __init__(self, candidates: Sequence[VectorSearchCandidate] = ()) -> None:
        self.candidates = list(candidates)

    def search(
        self,
        *,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        filters: RetrievalFilters,
    ) -> list[VectorSearchCandidate]:
        return self.candidates[:limit]


class _FakeEvaluationRagService:
    vector_client = _FakeVectorClient()

    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        del top_k, rerank_top_n
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
            strategy_type=strategy_type.value,
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
            retrieved_items=[],
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
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
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
                strategy_type=strategy_type.value,
            )
            return RagEvaluationResult(
                retrieval_run_id=retrieval_run.retrieval_run_id,
                status="failed",
                answer_text="",
                citations=[],
                confidence=None,
                retrieval_score_summary=None,
                retrieved_items=[],
                context_sources_for_safety=[],
                error_code="no_context_found",
            )
        return super().evaluate_question(
            db,
            question=question,
            request_id=request_id,
            strategy_type=strategy_type,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
        )


class _NoContextStrategyRagService(_FakeEvaluationRagService):
    def evaluate_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        del top_k, rerank_top_n
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
            strategy_type=strategy_type.value,
        )
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text="",
            citations=[],
            confidence=None,
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=0,
                post_filter_candidate_count=0,
                selected_count=0,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[],
            context_sources_for_safety=[],
            error_code="no_context_found",
        )


class _RerankFailingRagService(_FakeEvaluationRagService):
    def evaluate_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        del top_k, rerank_top_n
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="failed",
            request_id=request_id,
            error_code="rerank_failed",
            strategy_type=strategy_type.value,
        )
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="failed",
            answer_text="",
            citations=[],
            confidence=None,
            retrieval_score_summary=None,
            retrieved_items=[],
            context_sources_for_safety=[],
            error_code="rerank_failed",
        )


class _StrategyAwareFakeEvaluationRagService(_FakeEvaluationRagService):
    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        del top_k, rerank_top_n
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
            strategy_type=strategy_type.value,
        )
        snippet = f"{strategy_type.value} safe target citation preview."
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text="",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=100,
                    source_label="strategy-seed.md",
                    snippet=snippet,
                    old_version_flag=False,
                )
            ],
            confidence=None,
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=100,
                    logical_document_id=10,
                    rank_order=1,
                    snippet=snippet,
                )
            ],
            context_sources_for_safety=[],
        )


class _GraphCacheAwareEvaluationRagService(_StrategyAwareFakeEvaluationRagService):
    def evaluate_strategy_target(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        target: object,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
        evaluation_run_id: int | None = None,
        cache_attempt_id: str | None = None,
    ) -> RagEvaluationResult:
        del top_k, rerank_top_n, evaluation_run_id, cache_attempt_id
        strategy = getattr(target, "retrieval_strategy", RetrievalStrategy.DENSE)
        if not isinstance(strategy, RetrievalStrategy):
            strategy = RetrievalStrategy(str(strategy))
        provider = getattr(target, "graph_store_provider", None)
        cache_mode = _target_cache_mode_value(target)
        cache_summary = {
            "schema_version": "phase3.retrieval_cache.v1",
            "enabled": cache_mode != "disabled",
            "status": "hit"
            if cache_mode == "warm"
            else "miss"
            if cache_mode == "cold"
            else "bypass",
            "reason": "cache_disabled" if cache_mode == "disabled" else "evaluation_cache_mode",
        }
        if strategy == RetrievalStrategy.GRAPH and provider == "neo4j":
            return self._neo4j_skipped_result(
                db,
                question=question,
                request_id=request_id,
                cache_summary=cache_summary,
            )
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
            strategy_type=strategy.value,
            cache_summary_json=cache_summary,
            retrieval_score_summary={
                "requested_top_k": 5,
                "qdrant_candidate_count": 1,
                "post_filter_candidate_count": 1,
                "selected_count": 1,
                "excluded_by_rdb_check_count": 0,
                "graph_store_provider": provider if provider in {"postgres", "neo4j"} else None,
                "graph_entity_lookup_count": 3 if strategy == RetrievalStrategy.GRAPH else 0,
                "graph_relation_count": 2 if strategy == RetrievalStrategy.GRAPH else 0,
                "graph_source_candidate_count": 1 if strategy == RetrievalStrategy.GRAPH else 0,
                "fallback_used": False,
            },
        )
        if strategy == RetrievalStrategy.GRAPH:
            db.add(
                RetrievalRunItem(
                    retrieval_run_id=retrieval_run.retrieval_run_id,
                    document_chunk_id=100,
                    retrieval_score=0.91,
                    rank_order=1,
                    selected_flag=True,
                    retrieval_source="graph",
                    score_breakdown_json={"selected_flag": True},
                )
            )
            db.add(
                GraphRetrievalPath(
                    retrieval_run_id=retrieval_run.retrieval_run_id,
                    path_json={
                        "schema_version": "phase3.graph_path.v2",
                        "provider": "postgres",
                        "safe_entity_labels": ["FastAPI", "PostgreSQL", "Qdrant"],
                        "relation_types": ["uses", "stores"],
                        "depth": 2,
                    },
                    score_breakdown_json={"combined_score": 0.91},
                    source_chunk_ids_json=[100],
                )
            )
            db.flush()
        snippet = f"{strategy.value} safe target citation preview."
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text="Graph evaluation selects a safe provider comparison.",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=100,
                    source_label="graph-eval.md",
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
                graph_store_provider=provider if provider in {"postgres", "neo4j"} else None,
                graph_entity_lookup_count=3 if strategy == RetrievalStrategy.GRAPH else 0,
                graph_relation_count=2 if strategy == RetrievalStrategy.GRAPH else 0,
                graph_source_candidate_count=1 if strategy == RetrievalStrategy.GRAPH else 0,
                fallback_used=False,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=100,
                    logical_document_id=10,
                    rank_order=1,
                    snippet=snippet,
                )
            ],
            context_sources_for_safety=[],
        )

    def _neo4j_skipped_result(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        cache_summary: dict[str, object],
    ) -> RagEvaluationResult:
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="failed",
            request_id=request_id,
            error_code="graph_provider_skipped",
            strategy_type=RetrievalStrategy.GRAPH.value,
            cache_summary_json=cache_summary,
            retrieval_score_summary={
                "requested_top_k": 5,
                "qdrant_candidate_count": 0,
                "post_filter_candidate_count": 0,
                "selected_count": 0,
                "excluded_by_rdb_check_count": 0,
                "graph_store_provider": "neo4j",
                "graph_reason_codes": [
                    "graph_store_provider_unavailable",
                    "neo4j_not_configured",
                ],
            },
        )
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="failed",
            answer_text="",
            citations=[],
            confidence=None,
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=0,
                post_filter_candidate_count=0,
                selected_count=0,
                excluded_by_rdb_check_count=0,
                graph_store_provider="neo4j",
                graph_reason_codes=[
                    "graph_store_provider_unavailable",
                    "neo4j_not_configured",
                ],
            ),
            retrieved_items=[],
            context_sources_for_safety=[],
            error_code="graph_provider_skipped",
        )


class _AgenticEvaluationRagService(_StrategyAwareFakeEvaluationRagService):
    def evaluate_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        if strategy_type != RetrievalStrategy.AGENTIC_ROUTER:
            return self.evaluate_question(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )

        selected_strategy = (
            "hybrid"
            if ("hybrid" in question.lower() or "fallback accepted" in question.lower())
            else "dense"
        )
        execution_strategy = (
            "dense" if "fallback accepted" in question.lower() else selected_strategy
        )
        has_context = "missing" not in question.lower()
        strategy_decision_json = None
        if has_context:
            strategy_decision_json = {
                "schema_version": "phase2.router.v1",
                "requested_strategy": "agentic_router",
                "selected_strategy": selected_strategy,
                "execution_strategy": execution_strategy,
                "fallback_used": True,
                "fallback_strategy": "dense",
                "fallback_reason": "insufficient_context",
                "budget_exhausted": False,
                "retrieval_call_count": 2,
                "max_retrieval_calls": 2,
                "sufficiency_score": 0.82,
                "sufficiency_reason_codes": ["sufficient_after_fallback"],
            }
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
            strategy_type=strategy_type.value,
            strategy_decision_json=strategy_decision_json,
            retrieval_score_summary={
                "requested_top_k": 5,
                "qdrant_candidate_count": 1 if has_context else 0,
                "post_filter_candidate_count": 1 if has_context else 0,
                "selected_count": 1 if has_context else 0,
                "excluded_by_rdb_check_count": 0,
                "sufficiency_score": 0.82 if has_context else 0.1,
                "retrieval_call_count": 2,
                "max_retrieval_calls": 2,
                "fallback_used": True,
                "budget_exhausted": not has_context,
            },
        )
        snippet = "agentic_router hybrid safe target citation preview." if has_context else ""
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text="",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=100,
                    source_label="strategy-seed.md",
                    snippet=snippet,
                    old_version_flag=False,
                )
            ]
            if has_context
            else [],
            confidence=None,
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1 if has_context else 0,
                post_filter_candidate_count=1 if has_context else 0,
                selected_count=1 if has_context else 0,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=100,
                    logical_document_id=10,
                    rank_order=1,
                    snippet=snippet,
                )
            ]
            if has_context
            else [],
            context_sources_for_safety=[],
            error_code=None if has_context else "no_context_found",
        )


class _ToolAgenticEvaluationRagService(_StrategyAwareFakeEvaluationRagService):
    def evaluate_question(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy = DEFAULT_RETRIEVAL_STRATEGY,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        del top_k, rerank_top_n
        if strategy_type not in {
            RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
            RetrievalStrategy.LANGCHAIN_AGENTIC,
            RetrievalStrategy.LANGGRAPH_AGENTIC,
        }:
            return super().evaluate_question(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy_type,
            )

        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="succeeded",
            request_id=request_id,
            strategy_type=strategy_type.value,
            strategy_decision_json={
                "requested_strategy": strategy_type.value,
                "selected_strategy": strategy_type.value,
                "execution_strategy": strategy_type.value,
                "orchestrator_provider": (
                    "langchain"
                    if strategy_type == RetrievalStrategy.LANGCHAIN_AGENTIC
                    else "langgraph"
                    if strategy_type == RetrievalStrategy.LANGGRAPH_AGENTIC
                    else "llm"
                ),
                "fallback_used": False,
                "budget_exhausted": False,
                "retrieval_call_count": 1,
                "max_retrieval_calls": 8,
                "sufficiency_score": None,
                "sufficiency_reason_codes": [],
            },
            retrieval_score_summary={
                "requested_top_k": 5,
                "qdrant_candidate_count": 1,
                "post_filter_candidate_count": 1,
                "selected_count": 1,
                "excluded_by_rdb_check_count": 0,
                "retrieval_call_count": 1,
                "fallback_used": False,
                "budget_exhausted": False,
            },
        )
        snippet = f"{strategy_type.value} safe target citation preview."
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="succeeded",
            answer_text="",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=100,
                    source_label="strategy-seed.md",
                    snippet=snippet,
                    old_version_flag=False,
                )
            ],
            confidence=None,
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=100,
                    logical_document_id=10,
                    rank_order=1,
                    snippet=snippet,
                )
            ],
            context_sources_for_safety=[],
        )

    def evaluate_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        if strategy_type in {
            RetrievalStrategy.LLM_TOOL_ORCHESTRATOR,
            RetrievalStrategy.LANGCHAIN_AGENTIC,
            RetrievalStrategy.LANGGRAPH_AGENTIC,
        }:
            raise AssertionError("ask-only evaluation strategies must use evaluate_question")
        return self.evaluate_question(
            db,
            question=question,
            request_id=request_id,
            strategy_type=strategy_type,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
        )


class _AgenticOnlyFailingRagService(_FakeEvaluationRagService):
    def evaluate_strategy(
        self,
        db: Session,
        *,
        question: str,
        request_id: str | None,
        strategy_type: RetrievalStrategy,
        top_k: int | None = None,
        rerank_top_n: int | None = None,
    ) -> RagEvaluationResult:
        if strategy_type != RetrievalStrategy.AGENTIC_ROUTER:
            return self.evaluate_question(
                db,
                question=question,
                request_id=request_id,
                strategy_type=strategy_type,
                top_k=top_k,
                rerank_top_n=rerank_top_n,
            )
        retrieval_run = _create_fake_retrieval_run(
            db,
            question=question,
            status="failed",
            request_id=request_id,
            error_code="no_context_found",
            strategy_type=strategy_type.value,
        )
        return RagEvaluationResult(
            retrieval_run_id=retrieval_run.retrieval_run_id,
            status="failed",
            answer_text="",
            citations=[],
            confidence=None,
            retrieval_score_summary=None,
            retrieved_items=[],
            context_sources_for_safety=[],
            error_code="no_context_found",
        )


def _failing_rag_service_factory(settings: Settings, db: Session) -> _FakeEvaluationRagService:
    raise RuntimeError("synthetic evaluation setup failure")


def _target_cache_mode_value(target: object) -> str:
    cache_mode = getattr(target, "cache_mode", "default")
    value = getattr(cache_mode, "value", cache_mode)
    return str(value)


def _create_fake_retrieval_run(
    db: Session,
    *,
    question: str,
    status: str,
    request_id: str | None,
    error_code: str | None = None,
    strategy_type: str = "dense",
    strategy_decision_json: dict[str, object] | None = None,
    retrieval_score_summary: dict[str, object] | None = None,
    cache_summary_json: dict[str, object] | None = None,
) -> RetrievalRun:
    now = datetime.now(UTC)
    run = RetrievalRun(
        status=status,
        error_code=error_code,
        started_at=now,
        finished_at=now,
        top_k=5,
        query_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),
        retrieval_score_summary=retrieval_score_summary
        or {
            "requested_top_k": 5,
            "qdrant_candidate_count": 1,
            "post_filter_candidate_count": 1,
            "selected_count": 1 if status == "succeeded" else 0,
            "excluded_by_rdb_check_count": 0,
        },
        strategy_decision_json=strategy_decision_json,
        cache_summary_json=cache_summary_json,
        answer_confidence=0.9 if status == "succeeded" else None,
        groundedness_score=0.9 if status == "succeeded" else None,
        confidence_label="High" if status == "succeeded" else None,
        request_id=request_id,
        strategy_type=strategy_type,
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
