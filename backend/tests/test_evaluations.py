Warning: truncated output (original token count: 56630)
Total output lines: 5859

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
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
from app.core.errors import ConflictError, ResourceNotFound
from app.core.security import hash_password
from app.db.base import Base
from app.db.evaluation_models import (
    EvaluationAuxiliaryJudgment,
    EvaluationHumanCalibration,
    EvaluationResult,
)
from app.db.graph_models import GraphRetrievalPath
from app.db.models import (
    AuditLog,
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
from app.evaluation.fixtures import (
    EvaluationCase,
    EvaluationFixtureError,
    evaluation_case_snapshot_hash,
    load_evaluation_cases,
)
from app.evaluation.gold_v2 import load_gold_v2_bundle
from app.evaluation.metrics import (
    EvaluationMetricInputs,
    RetrievedEvaluationItem,
    calculate_metrics,
)
from app.evaluation.rag_service import (
    RETRIEVAL_ONLY_EVALUATION_TARGET_STRATEGIES,
    DatabaseVectorSearchClient,
    EvaluationRagQuestionService,
    RagEvaluationResult,
    _evaluation_cache_namespace,
    _evaluation_target_settings,
    create_evaluation_rag_service,
)
from app.ingest.embedding import FakeEmbeddingAdapter
from app.main import create_app
from app.rag.generation import (
    AnswerGenerationError,
    FakeAnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    GenerationResult,
    TokenUsage,
)
from app.rag.rerank import FakeRerankerClient
from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate, VectorSearchClient
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluations import (
    DEFAULT_EVALUATION_METRICS,
    EvaluationCaseCreateRequest,
    EvaluationDatasetCreateRequest,
    EvaluationDatasetManifest,
    EvaluationFailureCandidate,
    EvaluationFailurePromotionRequest,
    EvaluationFailureSeverity,
    EvaluationMetricCategory,
    EvaluationMetricName,
    EvaluationRunCreateRequest,
    EvaluationRunRequestStrategy,
    StrategyComparisonMetric,
)
from app.schemas.rag import (
    RagAskCitation,
    RagAskConfidence,
    RagSearchResponse,
    RetrievalScoreSummary,
)
from app.services.evaluation_service import (
    EVALUATION_METRIC_ALIAS_BY_NAME,
    EVALUATION_METRIC_CATEGORY_BY_NAME,
    STRATEGY_METRIC_SPECS,
    EvaluationService,
    _failure_reasons,
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
    default_request = EvaluationRunCreateRequest()
    assert default_request.metrics == list(DEFAULT_EVALUATION_METRICS)

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
    assert by_name["case_metadata"].details["answer_generated"] is True
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
    assert answer_only_by_name["faithfulness"].metric_score is None
    assert (
        answer_only_by_name["faithfulness"].details["reason_code"]
        == "expected_keywords_not_configured"
    )
    assert answer_only_by_name["context_precision"].metric_score == 0.0
    assert "canonical answer" not in str(answer_only_by_name["faithfulness"].details)

    separated_metrics = calculate_metrics(
        EvaluationMetricInputs(
            case=EvaluationCase(
                case_id="separated_signals",
                question="Which components are required?",
                expected_keywords=("Qdrant",),
                required_citation=True,
                expected_chunk_ids=(11,),
                metadata_json={"expected_answer_slots": ["PostgreSQL", "Qdrant"]},
            ),
            answer_text="PostgreSQL stores relational state.",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=11,
                    source_label="architecture.md",
                    snippet="Qdrant stores retrieval vectors.",
                    old_version_flag=False,
                )
            ],
            confidence=None,
            retrieval_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=11,
                    logical_document_id=7,
                    rank_order=1,
                    snippet="Qdrant stores retrieval vectors.",
                )
            ],
        )
    )
    separated_by_name = {metric.metric_name: metric for metric in separated_metrics}
    assert separated_by_name["recall_at_k"].metric_score == 1.0
    assert separated_by_name["context_precision"].metric_score == 1.0
    assert separated_by_name["faithfulness"].metric_score == 0.0
    assert separated_by_name["answer_completeness"].metric_score == 0.5
    assert separated_by_name["citation_presence"].metric_score == 1.0
    assert separated_by_name["citation_correctness"].metric_score == 1.0
    assert separated_by_name["citation_coverage"].metric_score == 1.0
    assert "PostgreSQL" not in str(separated_by_name["answer_completeness"].details)
    assert "Qdrant" not in str(separated_by_name["answer_completeness"].details)

    snapshot_base = evaluation_case_snapshot_hash(
        question="Which components are required?",
        expected_answer=None,
        expected_keywords=("Qdrant",),
        expected_document_ids=(),
        expected_chunk_ids=(11,),
        required_citation=True,
        metadata_json={"expected_answer_slots": ["Qdrant"]},
    )
    snapshot_changed = evaluation_case_snapshot_hash(
        question="Which components are required?",
        expected_answer=None,
        expected_keywords=("Qdrant",),
        expected_document_ids=(),
        expected_chunk_ids=(11,),
        required_citation=True,
        metadata_json={"expected_answer_slots": ["PostgreSQL", "Qdrant"]},
    )
    assert snapshot_base != snapshot_changed

    with pytest.raises(ValueError):
        EvaluationRunCreateRequest(dataset_name="phase1.smoke", case_limit=1)


def test_metrics_mark_answer_and_citation_not_applicable_without_generated_answer() -> None:
    metrics = calculate_metrics(
        EvaluationMetricInputs(
            case=EvaluationCase(
                case_id="retrieval_only",
                question="Which vector database is used?",
                expected_keywords=("Qdrant",),
                required_citation=True,
                expected_chunk_ids=(11,),
                metadata_json={"expected_answer_slots": ["Qdrant"]},
            ),
            answer_text="",
            citations=[
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=11,
                    source_label="architecture.md",
                    snippet="Qdrant stores retrieval vectors.",
                    old_version_flag=False,
                )
            ],
            confidence=None,
            retrieval_summary=RetrievalScoreSummary(
                requested_top_k=5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=11,
                    logical_document_id=7,
                    rank_order=1,
                    snippet="Qdrant stores retrieval vectors.",
                )
            ],
        )
    )
    by_name = {metric.metric_name: metric for metric in metrics}
    assert by_name["case_metadata"].details["answer_generated"] is False

    for metric_name in (
        "faithfulness",
        "answer_completeness",
        "groundedness",
        "citation_coverage",
        "citation_presence",
        "citation_correctness",
    ):
        assert by_name[metric_name].metric_score is None
        assert by_name[metric_name].metric_label == "not_applicable"
        assert by_name[metric_name].details["reason_code"] == "answer_not_generated"
    assert by_name["recall_at_k"].metric_score == 1.0
    assert by_name["context_precision"].metric_score == 1.0
    assert by_name["no_context_rate"].metric_score == 0.0


def test_run_detail_normalizes_legacy_retrieval_answer_metrics_to_not_applicable() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            run = _seed_comparison_run(
                db,
                created_by=user.user_id,
                dataset_name="phase1_smoke",
                items=[_comparison_item("phase1_seed_stack", "succeeded")],
                metrics={
                    "recall_at_k": Decimal("1"),
                    "faithfulness": Decimal("0"),
                    "groundedness": Decimal("0"),
                    "citation_coverage": Decimal("0"),
                },
            )
            item = db.scalar(
                select(EvaluationRunItem).where(
                    EvaluationRunItem.evaluation_run_id == run.evaluation_run_id
                )
            )
            assert item is not None
            item.faithfulness_score = Decimal("0")
            item.groundedness_score = Decimal("0")
            item.citation_coverage = Decimal("0")
            db.commit()
            run_id = run.evaluation_run_id

        with session_factory() as db:
            detail = EvaluationService(settings=Settings(app_env="test")).get_run_detail(
                db,
                evaluation_run_id=run_id,
            )

        assert detail.evaluation_scope == "retrieval"
        assert detail.metric_summary == {"recall_at_k": 1.0}
        assert detail.items[0].faithfulness_score is None
        assert detail.items[0].groundedness_score is None
        assert detail.items[0].citation_coverage is None
        metric_by_name = {metric.metric_name: metric for metric in detail.items[0].metrics}
        for metric_name in ("faithfulness", "groundedness", "citation_coverage"):
            metric = metric_by_name[metric_name]
            assert metric.metric_score is None
            assert metric.metric_label == "not_applicable"
            assert metric.details is not None
            assert metric.details["reason_code"] == "answer_not_generated"
        comparison_by_name = {metric.metric_name: metric for metric in detail.strategy_comparison}
        assert comparison_by_name["faithfulness"].average is None
        assert comparison_by_name["faithfulness"].not_applicable_count == 1
        assert not {
            "low_faithfulness",
            "low_groundedness",
            "low_citation_coverage",
        }.intersection(candidate.failure_type for candidate in detail.failure_candidates)
    finally:
        engine.dispose()


def test_evaluation_metric_catalog_classifies_every_metric_once() -> None:
    catalog = EvaluationService.get_metric_catalog()
    metric_names = [item.metric_name for item in catalog.metrics]

    assert catalog.schema_version == "phase3.evaluation_metric_taxonomy.v1"
    assert len(metric_names) == 23
    assert len(metric_names) == len(set(metric_names))
    assert set(metric_names) == set(EvaluationMetricName)
    assert set(EVALUATION_METRIC_CATEGORY_BY_NAME) == set(EvaluationMetricName)
    counts = {
        category: sum(item.category == category for item in catalog.metrics)
        for category in EvaluationMetricCategory
    }
    assert counts == {
        EvaluationMetricCategory.RETRIEVAL: 4,
        EvaluationMetricCategory.ANSWER: 4,
        EvaluationMetricCategory.CITATION: 3,
        EvaluationMetricCategory.ROUTING: 5,
        EvaluationMetricCategory.GRAPH: 4,
        EvaluationMetricCategory.PERFORMANCE: 3,
    }
    assert EVALUATION_METRIC_ALIAS_BY_NAME == {
        EvaluationMetricName.CITATION_COVERAGE: EvaluationMetricName.CITATION_PRESENCE
    }
    citation_coverage = next(
        item
        for item in catalog.metrics
        if item.metric_name == EvaluationMetricName.CITATION_COVERAGE
    )
    assert citation_coverage.category == EvaluationMetricCategory.CITATION
    assert citation_coverage.alias_of == EvaluationMetricName.CITATION_PRESENCE
    assert all(
        item.display_name and item.description and item.value_unit for item in catalog.metrics
    )
    assert all(
        item.plain_language_summary
        and item.applicable_scopes
        and 0 <= item.display_priority < len(catalog.metrics)
        for item in catalog.metrics
    )
    assert len({item.display_priority for item in catalog.metrics}) == len(catalog.metrics)
    primary_by_scope = {
        scope: [
            item.metric_name
            for item in sorted(catalog.metrics, key=lambda item: item.display_priority)
            if scope in item.primary_scopes
        ]
        for scope in ("retrieval", "answer", "end_to_end")
    }
    assert primary_by_scope["retrieval"] == ["recall_at_k", "mrr", "context_precision"]
    assert (
        primary_by_scope["answer"]
        == primary_by_scope["end_to_end"]
        == ["claim_faithfulness", "answer_completeness", "citation_correctness"]
    )


def test_evaluation_metric_catalog_api_is_admin_only(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = evaluation_client
    path = "/api/v1/evaluations/metric-catalog"

    assert client.get(path).status_code == 401

    _login_as(client, "viewer@example.com")
    viewer_response = client.get(path)
    assert viewer_response.status_code == 403
    assert viewer_response.json()["error"]["code"] == "permission_denied"

    _logout(client)
    _login_as(client, "admin@example.com")
    response = client.get(path)

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["schema_version"] == "phase3.evaluation_metric_taxonomy.v1"
    assert len(payload["metrics"]) == 23
    by_name = {item["metric_name"]: item for item in payload["metrics"]}
    assert by_name["citation_coverage"]["category"] == "citation"
    assert by_name["citation_coverage"]["alias_of"] == "citation_presence"
    assert by_name["faithfulness"]["display_name"] == "期待回答シグナル一致率（旧Faithfulness）"
    assert by_name["faithfulness"]["importance"] == "diagnostic"
    assert by_name["faithfulness"]["primary_scopes"] == []
    assert "キーワード未設定時はN/A" in by_name["faithfulness"]["plain_language_summary"]
    assert by_name["claim_faithfulness"]["method"] == "local_judge"
    assert "ローカルLLMによる自動判定" in by_name["claim_faithfulness"]["plain_language_summary"]
    assert by_name["recall_at_k"]["plain_language_summary"]
    assert by_name["p95_latency"]["higher_is_better"] is False
    assert by_name["p95_latency"]["value_unit"] == "ms"
    assert TEST_PASSWORD.lower() not in response.text.lower()


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
        "expected_answer_slots": ["PostgreSQL", "Qdrant"],
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
    assert manifest.dataset.metadata_json is not None
    assert manifest.dataset.metadata_json["evaluation_scope"] == "retrieval"
    assert {spec.metric_name.value for spec in STRATEGY_METRIC_SPECS} >= {
        "recall_at_k",
        "mrr",
        "citation_coverage",
        "citation_presence",
        "citation_correctness",
        "groundedness",
        "faithfulness",
        "answer_completeness",
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
    assert graph_manifest.dataset.version == "v2"
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
    assert viewer_csrf_token.lower() not in listing.text.lower()
    assert csrf_token.lower() not in listing.text.lower()
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
                "citation_presence",
                "citation_correctness",
                "groundedness",
                "faithfulness",
                "answer_completeness",
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
                "claim_faithfulness",
            ],
            "cache_modes": ["default"],
            "evaluation_scope": "retrieval",
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


def test_v2_dataset_validate_import_readiness_and_preflight_guard(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = evaluation_client
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "evaluation"
        / "fixtures"
        / "gold_answer_quality_v2_evaluation_dataset.json"
    )
    manifest = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert client.post("/api/v1/evaluations/datasets/validate", json=manifest).status_code == 401
    _login_as(client, "viewer@example.com")
    assert client.post("/api/v1/evaluations/datasets/validate", json=manifest).status_code == 403

    _logout(client)
    _login_as(client, "admin@example.com")
    csrf = _session_csrf(client)
    validated = client.post("/api/v1/evaluations/datasets/validate", json=manifest)
    assert validated.status_code == 200
    validation = validated.json()["data"]
    assert validation["manifest_schema_version"] == "phase3.evaluation_dataset.v2"
    assert validation["composition"] == {
        "case_count": 50,
        "source_count": 15,
        "fact_count": 45,
        "answerable_count": 30,
        "unanswerable_count": 20,
        "language_ja_count": 25,
        "language_en_count": 25,
        "single_hop_count": 25,
        "multi_hop_count": 25,
        "prompt_injection_count": 10,
    }
    assert len(validation["corpus_fingerprint"]) == 64

    missing_csrf = client.post(
        "/api/v1/evaluations/datasets/import",
        json=manifest,
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert missing_csrf.status_code == 403
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
    assert imported.json()["data"]["result_code"] == "created"
    assert imported_again.status_code == 200
    assert imported_again.json()["data"]["result_code"] == "unchanged"
    dataset_id = imported.json()["data"]["evaluation_dataset_id"]

    readiness = client.get(f"/api/v1/evaluations/datasets/{dataset_id}/corpus/readiness")
    assert readiness.status_code == 200
    readiness_data = readiness.json()["data"]
    assert readiness_data["ready"] is False
    assert readiness_data["run_allowed"] is False
    assert readiness_data["source_count"] == 15
    assert readiness_data["fact_count"] == 45

    rejected_run = client.post(
        "/api/v1/evaluations/runs",
        json={
            "evaluation_dataset_id": dataset_id,
            "strategies": ["hybrid"],
            "evaluation_scope": "end_to_end",
        },
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert rejected_run.status_code == 409
    assert rejected_run.json()["error"]["code"] == "evaluation_corpus_not_ready"

    changed = json.loads(json.dumps(manifest))
    changed["dataset"]["description"] = "Changed content requires a new version."
    conflict = client.post(
        "/api/v1/evaluations/datasets/import",
        json=changed,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "dataset_version_conflict"


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


def test_evaluation_create_persists_requested_generation_config_and_summary() -> None:
    engine, session_factory = _session_factory()
    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(settings=Settings(app_env="test"))
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    case_limit=1,
                    strategies=[EvaluationRunRequestStrategy.LLM_TOOL_ORCHESTRATOR],
                    generation_provider="OpenAI",
                    generation_model="gpt-4.1-mini",
                ),
                user=user,
            )
            run = db.get(EvaluationRun, created.evaluation_run_id)
            assert run is not None
            assert run.metrics_config is not None
            assert run.metrics_config["generation_provider"] == "openai"
            assert run.metrics_config["generation_model"] == "gpt-4.1-mini"

            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)

        assert detail.requested_generation_provider == "openai"
        assert detail.requested_generation_model == "gpt-4.1-mini"
        assert detail.generation_providers == []
        assert detail.generation_models == []
    finally:
        engine.dispose()


def test_evaluation_run_job_passes_requested_generation_selection_to_factory() -> None:
    engine, session_factory = _session_factory()
    recorded: list[tuple[str | None, str | None]] = []

    def factory(
        settings: Settings,
        db: Session,
        *,
        generation_provider: str | None = None,
        generation_model: str | None = None,
    ) -> _FakeEvaluationRagService:
        del settings, db
        recorded.append((generation_provider, generation_model))
        return _FakeEvaluationRagService()

    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=factory, settings=Settings(app_env="test")
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    case_limit=1,
                    strategies=[EvaluationRunRequestStrategy.DENSE],
                    evaluation_scope="end_to_end",
                    generation_provider="lmstudio",
                    generation_model="eval-model-a",
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=factory, settings=Settings(app_env="test")
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-requested-generation",
            )
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)

        assert result["status"] == "succeeded"
        assert recorded == [("lmstudio", "eval-model-a")]
        assert detail.requested_generation_provider == "lmstudio"
        assert detail.requested_generation_model == "eval-model-a"
    finally:
        engine.dispose()


def test_evaluation_run_job_preserves_lmstudio_url_generation_model() -> None:
    engine, session_factory = _session_factory()
    recorded: list[tuple[str | None, str | None]] = []
    model_url = "https://huggingface.co/lmstudio-community/qwen3.5-9b-gguf"

    def factory(
        settings: Settings,
        db: Session,
        *,
        generation_provider: str | None = None,
        generation_model: str | None = None,
    ) -> _FakeEvaluationRagService:
        del settings, db
        recorded.append((generation_provider, generation_model))
        return _FakeEvaluationRagService()

    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=factory, settings=Settings(app_env="test")
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name="phase1_smoke",
                    case_limit=1,
                    strategies=[EvaluationRunRequestStrategy.LLM_TOOL_ORCHESTRATOR],
                    generation_provider="lmstudio",
                    generation_model=model_url,
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=factory, settings=Settings(app_env="test")
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="test-lmstudio-url-generation",
            )
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)

        assert result["status"] == "succeeded"
        assert recorded == [("lmstudio", model_url)]
        assert detail.requested_generation_provider == "lmstudio"
        assert detail.requested_generation_model == model_url
    finally:
        engine.dispose()


def test_evaluation_run_job_uses_default_generation_selection_when_unspecified() -> None:
    engine, session_factory = _session_factory()
    recorded: list[tuple[str | None, str | None]] = []

    def factory(
        settings: Settings,
        db: Session,
        *,
        generation_provider: str | None = None,
        generation_model: str | None = None,
    ) -> _FakeEvaluationRagService:
        del settings, db
  …26630 tokens truncated…ll marker qdrant citation evidence",
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
    assert accepted.json()["data"]["evaluation_scope"] == "end_to_end"
    assert accepted.json()["data"]["strategies"] == [
        "dense",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    ]
    dense_end_to_end = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "strategies": ["dense"],
            "evaluation_scope": "end_to_end",
            "generation_provider": "lmstudio",
            "generation_model": "qwen3.5-9b",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert dense_end_to_end.status_code == 202
    assert dense_end_to_end.json()["data"]["evaluation_scope"] == "end_to_end"
    assert dense_end_to_end.json()["data"]["strategies"] == ["dense"]

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


def test_evaluation_api_rejects_unknown_provider_and_secret_like_generation_model(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _ = evaluation_client
    _login_as(client, "admin@example.com")
    csrf_token = _session_csrf(client)

    unknown_provider = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "generation_provider": "remote",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert unknown_provider.status_code == 422
    assert unknown_provider.json()["error"]["code"] == "validation_error"
    fake_provider = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "strategies": ["dense"],
            "evaluation_scope": "end_to_end",
            "generation_provider": "fake",
            "generation_model": "fake-rag-answer",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert fake_provider.status_code == 422
    assert fake_provider.json()["error"]["code"] == "validation_error"

    provider_without_model = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "strategies": ["llm_tool_orchestrator"],
            "generation_provider": "openai",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert provider_without_model.status_code == 422
    assert provider_without_model.json()["error"]["code"] == "validation_error"

    retrieval_only_generation_selection = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "strategies": ["dense"],
            "generation_model": "gpt-4.1-mini",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert retrieval_only_generation_selection.status_code == 422
    assert retrieval_only_generation_selection.json()["error"]["code"] == "validation_error"

    secret_model = client.post(
        "/api/v1/evaluations/runs",
        json={
            "dataset_name": "phase1_smoke",
            "case_limit": 1,
            "strategies": ["llm_tool_orchestrator"],
            "generation_provider": "openai",
            "generation_model": "sk-test-secret-token-1234567890",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
    )
    assert secret_model.status_code == 422
    serialized = secret_model.text.lower()
    assert "sk-test-secret-token" not in serialized
    assert "1234567890" not in serialized

    for reserved_label in ("redacted", "unknown"):
        reserved_model = client.post(
            "/api/v1/evaluations/runs",
            json={
                "dataset_name": "phase1_smoke",
                "case_limit": 1,
                "strategies": ["llm_tool_orchestrator"],
                "generation_provider": "openai",
                "generation_model": reserved_label,
            },
            headers={"X-CSRF-Token": csrf_token, "Origin": ALLOWED_ORIGIN},
        )
        assert reserved_model.status_code == 422
        assert reserved_model.json()["error"]["code"] == "validation_error"


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


class _UsageEvaluationRagService(_FakeEvaluationRagService):
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
        result = super().evaluate_question(
            db,
            question=question,
            request_id=request_id,
            strategy_type=strategy_type,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
        )
        if self.calls == 1:
            return replace(
                result,
                generation_provider="lmstudio",
                generation_model="qwen3.5-9b",
                input_tokens=100,
                output_tokens=50,
                total_tokens=150,
                estimated_cost_usd=0.123456,
                generation_latency_ms=40,
            )
        return replace(
            result,
            generation_provider="openai",
            generation_model="sk-test-secret-token-1234567890",
            input_tokens=200,
            output_tokens=25,
            total_tokens=225,
            estimated_cost_usd=0.654321,
            generation_latency_ms=80,
        )


class _MissingUsageEvaluationRagService(_FakeEvaluationRagService):
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
        result = super().evaluate_question(
            db,
            question=question,
            request_id=request_id,
            strategy_type=strategy_type,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
        )
        return replace(
            result,
            generation_provider="fake",
            generation_model="unknown-model",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            estimated_cost_usd=None,
            generation_latency_ms=None,
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
                generation_provider="openai",
                generation_model="sk-failed-secret-token",
                input_tokens=999,
                output_tokens=999,
                total_tokens=1998,
                estimated_cost_usd=9.99,
                generation_latency_ms=999,
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


def _comparison_item(
    case_id: str,
    status: str,
    *,
    include_case_metadata: bool = True,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "status": status,
        "include_case_metadata": include_case_metadata,
    }


def test_human_calibration_api_is_safe_csrf_protected_and_idempotent(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = evaluation_client
    with session_factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert admin is not None
        run = _seed_comparison_run(
            db,
            created_by=admin.user_id,
            dataset_name="gold_answer_quality_v2",
            items=[_comparison_item("gold_v2_001", "succeeded")],
            metrics={"groundedness": Decimal("1")},
        )
        db.commit()
        item = db.scalar(
            select(EvaluationRunItem).where(
                EvaluationRunItem.evaluation_run_id == run.evaluation_run_id
            )
        )
        assert item is not None
        db.add(
            EvaluationAuxiliaryJudgment(
                evaluation_run_item_id=item.evaluation_run_item_id,
                status="succeeded",
                rubric_version="phase3.grounded_answer_judge.v1",
                judge_provider="lmstudio",
                judge_model="qwen3.5-9b",
                required_facts_supported="pass",
                citation_support="pass",
                forbidden_claims_absent="pass",
                abstention_correct="not_applicable",
                prompt_injection_resisted="not_applicable",
                confidence=Decimal("0.9500"),
                reason_codes_json=[],
                auxiliary_pass=True,
                claim_faithfulness=Decimal("1"),
                failure_code=None,
                answer_hash="a" * 64,
                context_hash="b" * 64,
            )
        )
        db.commit()
        run_id = run.evaluation_run_id
        item_id = item.evaluation_run_item_id

    _login_as(client, "admin@example.com")
    listing = client.get(f"/api/v1/evaluations/runs/{run_id}/human-calibrations")
    assert listing.status_code == 200
    assert listing.json()["data"]["eligible_count"] == 1
    assert listing.json()["data"]["reviewed_count"] == 0
    target = listing.json()["data"]["targets"][0]
    assert target["evaluation_run_item_id"] == item_id
    assert target["case_id"] == "gold_v2_001"
    assert target["strategy_type"] == "dense"
    assert target["status"] == "succeeded"
    assert target["answerable"] is True
    assert target["required_citation"] is True
    assert target["prompt_injection"] is False
    assert target["judge_status"] == "succeeded"
    assert target["auxiliary_decision"]["case_id"] == "gold_v2_001"
    assert target["claim_faithfulness"] == 1.0
    assert target["review_payload_available"] is False

    payload = _human_calibration_payload("gold_v2_001")
    missing_csrf = client.put(
        f"/api/v1/evaluations/runs/{run_id}/human-calibrations/{item_id}",
        json=payload,
        headers={"Origin": ALLOWED_ORIGIN},
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["error"]["code"] == "csrf_missing"

    csrf = _session_csrf(client)
    created = client.put(
        f"/api/v1/evaluations/runs/{run_id}/human-calibrations/{item_id}",
        json=payload,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert created.status_code == 200
    created_data = created.json()["data"]
    assert created_data["human_calibration"]["auxiliary_pass"] is True
    assert created_data["human_calibration"]["human_pass"] is True
    calibration_id = created_data["evaluation_human_calibration_id"]

    failed_payload = _human_calibration_payload(
        "gold_v2_001",
        required_facts_supported="fail",
        human_pass=False,
        disagreement_category="auxiliary_false_positive",
        reason_codes=["missing_required_fact"],
    )
    updated = client.put(
        f"/api/v1/evaluations/runs/{run_id}/human-calibrations/{item_id}",
        json=failed_payload,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert updated.status_code == 200
    updated_data = updated.json()["data"]
    assert updated_data["evaluation_human_calibration_id"] == calibration_id
    assert updated_data["human_calibration"]["auxiliary_pass"] is True
    assert updated_data["human_calibration"]["human_pass"] is False
    assert updated_data["human_calibration"]["disagreement_category"] == "auxiliary_false_positive"

    refreshed = client.get(f"/api/v1/evaluations/runs/{run_id}/human-calibrations")
    assert refreshed.status_code == 200
    refreshed_data = refreshed.json()["data"]
    assert refreshed_data["reviewed_count"] == 1
    assert refreshed_data["agreement_rate"] == 0.0
    assert len(refreshed_data["records"]) == 1

    gold_case = load_gold_v2_bundle()[0].cases[0]
    assert gold_case.question not in refreshed.text
    assert gold_case.reference_answer not in refreshed.text
    with session_factory() as db:
        calibrations = db.scalars(select(EvaluationHumanCalibration)).all()
        assert len(calibrations) == 1
        audit_row = db.scalar(
            select(AuditLog)
            .where(AuditLog.action_type == "evaluation.human_calibration_saved")
            .order_by(AuditLog.audit_log_id.desc())
        )
        assert audit_row is not None
        assert audit_row.metadata_json is not None
        assert audit_row.metadata_json["case_id"] == "gold_v2_001"
        assert "question" not in audit_row.metadata_json
        assert "answer" not in audit_row.metadata_json
        assert "context" not in audit_row.metadata_json


def test_human_calibration_api_enforces_admin_and_supports_any_dataset_contract(
    evaluation_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, session_factory = evaluation_client
    with session_factory() as db:
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        assert admin is not None
        gold_run = _seed_comparison_run(
            db,
            created_by=admin.user_id,
            dataset_name="gold_answer_quality_v2",
            items=[_comparison_item("gold_v2_001", "succeeded")],
            metrics={"groundedness": Decimal("1")},
        )
        other_run = _seed_comparison_run(
            db,
            created_by=admin.user_id,
            dataset_name="phase1_smoke",
            items=[_comparison_item("phase1_seed_stack", "succeeded")],
            metrics={"groundedness": Decimal("1")},
        )
        db.commit()
        gold_item = db.scalar(
            select(EvaluationRunItem).where(
                EvaluationRunItem.evaluation_run_id == gold_run.evaluation_run_id
            )
        )
        assert gold_item is not None
        gold_run_id = gold_run.evaluation_run_id
        other_run_id = other_run.evaluation_run_id
        gold_item_id = gold_item.evaluation_run_item_id

    _login_as(client, "viewer@example.com")
    forbidden = client.get(f"/api/v1/evaluations/runs/{gold_run_id}/human-calibrations")
    assert forbidden.status_code == 403
    _logout(client)
    _login_as(client, "admin@example.com")

    generic_dataset = client.get(f"/api/v1/evaluations/runs/{other_run_id}/human-calibrations")
    assert generic_dataset.status_code == 200
    assert generic_dataset.json()["data"]["eligible_count"] == 1
    assert generic_dataset.json()["data"]["targets"][0]["case_id"] == "phase1_seed_stack"

    csrf = _session_csrf(client)
    invalid_shape = _human_calibration_payload("gold_v2_002")
    invalid = client.put(
        f"/api/v1/evaluations/runs/{gold_run_id}/human-calibrations/{gold_item_id}",
        json=invalid_shape,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert invalid.status_code == 422

    missing_disagreement = _human_calibration_payload(
        "gold_v2_001",
        human_pass=False,
    )
    inconsistent = client.put(
        f"/api/v1/evaluations/runs/{gold_run_id}/human-calibrations/{gold_item_id}",
        json=missing_disagreement,
        headers={"X-CSRF-Token": csrf, "Origin": ALLOWED_ORIGIN},
    )
    assert inconsistent.status_code == 422


def _human_calibration_payload(
    case_id: str,
    *,
    required_facts_supported: str = "pass",
    human_pass: bool = True,
    disagreement_category: str | None = None,
    reason_codes: list[str] | None = None,
) -> dict[str, object]:
    del case_id
    return {
        "human_dimensions": {
            "required_facts_supported": required_facts_supported,
            "citation_support": "pass",
            "forbidden_claims_absent": "pass",
            "abstention_correct": "not_applicable",
            "prompt_injection_resisted": "not_applicable",
        },
        "human_pass": human_pass,
        "disagreement_category": disagreement_category,
        "human_reason_codes": reason_codes or [],
    }


def _seed_comparison_run(
    db: Session,
    *,
    created_by: int,
    dataset_name: str,
    items: Sequence[dict[str, object]],
    metrics: dict[str, Decimal],
) -> EvaluationRun:
    now = datetime.now(UTC)
    metric_names = list(metrics)
    run = EvaluationRun(
        created_by=created_by,
        status="succeeded",
        target_type="fixture_dataset",
        evaluation_dataset_id=None,
        strategy_type="dense",
        trigger_type="manual",
        metrics_config={
            "dataset_name": dataset_name,
            "evaluation_dataset_id": None,
            "case_limit": len(items),
            "strategy_type": "dense",
            "strategies": ["dense"],
            "trigger_type": "manual",
            "metrics": metric_names,
            "top_k": None,
            "rerank_top_n": None,
        },
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.flush()
    target = {
        "schema_version": "phase3.evaluation_target.v1",
        "comparison_label": "dense",
        "retrieval_strategy": "dense",
        "cache_mode": "default",
    }
    for item_spec in items:
        case_id = str(item_spec["case_id"])
        status = str(item_spec["status"])
        include_case_metadata = bool(item_spec.get("include_case_metadata", True))
        question_hash = hashlib.sha256(f"{case_id}:question".encode()).hexdigest()
        case_snapshot_hash = hashlib.sha256(f"{case_id}:snapshot".encode()).hexdigest()
        item = EvaluationRunItem(
            evaluation_run_id=run.evaluation_run_id,
            status=status,
            strategy_type="dense",
            case_key=case_id,
            error_code="comparison_failed" if status == "failed" else None,
            metric_summary_json={
                "schema_version": "phase2.evaluation.v1",
                "case_snapshot": {
                    "question_hash": question_hash,
                    "case_snapshot_hash": case_snapshot_hash,
                },
                "evaluation_target": target,
                "metrics": {
                    name: float(value) for name, value in metrics.items() if name != "p95_latency"
                },
            },
        )
        db.add(item)
        db.flush()
        details = {
            "case_id": case_id,
            "question_hash": question_hash,
            "case_snapshot_hash": case_snapshot_hash,
            "evaluation_target": target,
        }
        if include_case_metadata:
            db.add(
                EvaluationResult(
                    evaluation_run_item_id=item.evaluation_run_item_id,
                    metric_name="case_metadata",
                    metric_score=None,
                    metric_value=None,
                    metric_label=case_id,
                    details_json=details,
                    metric_detail_json=details,
                    strategy_type="dense",
                )
            )
        for metric_name, value in metrics.items():
            db.add(
                EvaluationResult(
                    evaluation_run_item_id=item.evaluation_run_item_id,
                    metric_name=metric_name,
                    metric_score=None if metric_name == "p95_latency" else value,
                    metric_value=value,
                    metric_label=None,
                    details_json={"evaluation_target": target},
                    metric_detail_json={"evaluation_target": target},
                    strategy_type="dense",
                )
            )
    db.flush()
    return run


def _set_generation_for_run(
    db: Session,
    run: EvaluationRun,
    *,
    provider: str,
    model: str,
    cost: Decimal,
    total_tokens: int,
    latency_ms: int,
) -> list[EvaluationRunItem]:
    items = db.scalars(
        select(EvaluationRunItem)
        .where(EvaluationRunItem.evaluation_run_id == run.evaluation_run_id)
        .order_by(EvaluationRunItem.evaluation_run_item_id)
    ).all()
    for item in items:
        item.generation_provider = provider
        item.generation_model = model
        item.input_tokens = total_tokens // 2
        item.output_tokens = total_tokens - item.input_tokens
        item.total_tokens = total_tokens
        item.estimated_cost_usd = cost
        item.generation_latency_ms = latency_ms
    return list(items)


def _replace_case_hashes_for_item(
    db: Session,
    item: EvaluationRunItem,
    *,
    question_hash: str,
    case_snapshot_hash: str,
) -> None:
    summary = dict(item.metric_summary_json) if isinstance(item.metric_summary_json, dict) else {}
    raw_snapshot = summary.get("case_snapshot")
    snapshot = dict(raw_snapshot) if isinstance(raw_snapshot, dict) else {}
    snapshot["question_hash"] = question_hash
    snapshot["case_snapshot_hash"] = case_snapshot_hash
    summary["case_snapshot"] = snapshot
    item.metric_summary_json = summary

    results = db.scalars(
        select(EvaluationResult).where(
            EvaluationResult.evaluation_run_item_id == item.evaluation_run_item_id
        )
    ).all()
    for result in results:
        if result.metric_name != "case_metadata":
            continue
        for attribute in ("details_json", "metric_detail_json"):
            raw_payload = getattr(result, attribute)
            payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
            payload["question_hash"] = question_hash
            payload["case_snapshot_hash"] = case_snapshot_hash
            setattr(result, attribute, payload)


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
