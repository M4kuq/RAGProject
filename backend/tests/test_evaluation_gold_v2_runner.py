from __future__ import annotations

from collections.abc import Iterator, Sequence

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.evaluation_models import EvaluationResult
from app.db.models import EvaluationRun, EvaluationRunItem, Role, User
from app.evaluation.fixtures import EvaluationCase, load_evaluation_cases
from app.evaluation.gold_v2 import load_gold_v2_bundle
from app.evaluation.metrics import RetrievedEvaluationItem
from app.evaluation.rag_service import RagEvaluationResult
from app.observability.trace_export import build_evaluation_trace_export_payload
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.schemas.evaluations import (
    EvaluationMetricName,
    EvaluationRunCreateRequest,
    EvaluationRunRequestStrategy,
)
from app.schemas.rag import RagAskCitation, RagAskConfidence, RetrievalScoreSummary
from app.services.evaluation_service import EvaluationService

GOLD_V2_DATASET_NAME = "gold_answer_quality_v2"
TEST_PASSWORD = "password"


class _DeterministicGoldRagService:
    def __init__(self, cases: Sequence[EvaluationCase]) -> None:
        self._cases = {case.question: (index, case) for index, case in enumerate(cases, start=1)}
        self.call_count = 0

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
        del db, request_id, strategy_type, rerank_top_n
        case_index, case = self._cases[question]
        self.call_count += 1
        answer = case.expected_answer or " ".join(case.expected_keywords)
        citations = (
            [
                RagAskCitation(
                    citation_id=1,
                    local_citation_id=1,
                    document_chunk_id=case_index,
                    source_label="gold-v2-reference",
                    snippet=answer,
                    old_version_flag=False,
                )
            ]
            if case.required_citation
            else []
        )
        return RagEvaluationResult(
            retrieval_run_id=None,
            status="succeeded",
            answer_text=answer,
            citations=citations,
            confidence=RagAskConfidence(
                answer_confidence=1.0,
                groundedness_score=1.0,
                confidence_label="High",
            ),
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=top_k or 5,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
            ),
            retrieved_items=[
                RetrievedEvaluationItem(
                    document_chunk_id=case_index,
                    logical_document_id=case_index,
                    rank_order=1,
                    snippet=answer,
                )
            ],
            context_sources_for_safety=[],
        )


def test_gold_v2_adapter_preserves_all_cases_and_runner_contract() -> None:
    cases = load_evaluation_cases(GOLD_V2_DATASET_NAME)
    limited = load_evaluation_cases(GOLD_V2_DATASET_NAME, case_limit=7)
    metadata = [case.metadata_json or {} for case in cases]

    assert len(cases) == 50
    assert len(limited) == 7
    assert len({case.case_id for case in cases}) == 50
    assert sum("answerable" in case.tags for case in cases) == 30
    assert sum("unanswerable" in case.tags for case in cases) == 20
    assert sum(item.get("expected_strategy") == "hybrid" for item in metadata) == 25
    assert sum(item.get("expected_strategy") == "agentic_router" for item in metadata) == 25
    assert all(case.expected_answer for case in cases)
    assert all(case.expected_keywords for case in cases)


def test_gold_v2_runs_and_aggregates_50_cases_without_persisting_raw_contract(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cases = load_evaluation_cases(GOLD_V2_DATASET_NAME)
    gold_dataset, _, _ = load_gold_v2_bundle()
    rag_service = _DeterministicGoldRagService(cases)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    try:
        with session_factory() as db:
            user = _seed_admin(db)
            service = EvaluationService(
                rag_service_factory=lambda settings, db: rag_service,
                settings=Settings(app_env="test"),
            )
            created = service.create_run(
                db,
                payload=EvaluationRunCreateRequest(
                    dataset_name=GOLD_V2_DATASET_NAME,
                    case_limit=50,
                    metrics=[
                        EvaluationMetricName.FAITHFULNESS,
                        EvaluationMetricName.ANSWER_COMPLETENESS,
                        EvaluationMetricName.CITATION_PRESENCE,
                    ],
                    strategies=[EvaluationRunRequestStrategy.HYBRID],
                ),
                user=user,
            )

        with session_factory() as db:
            service = EvaluationService(
                rag_service_factory=lambda settings, db: rag_service,
                settings=Settings(app_env="test"),
            )
            result = service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id="gold-v2-deterministic-contract",
            )
            detail = service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)
            run = db.get(EvaluationRun, created.evaluation_run_id)
            items = db.scalars(select(EvaluationRunItem)).all()
            stored_results = db.scalars(select(EvaluationResult)).all()

            assert run is not None
            assert result == {
                "status": "succeeded",
                "evaluation_run_id": created.evaluation_run_id,
                "case_count": 50,
                "succeeded_count": 50,
                "failed_count": 0,
            }
            assert rag_service.call_count == 50
            assert detail.case_count == 50
            assert detail.succeeded_count == 50
            assert detail.failed_count == 0
            assert detail.metric_summary["faithfulness"] == 1.0
            assert detail.metric_summary["answer_completeness"] == 1.0
            assert detail.metric_summary["citation_presence"] == 1.0
            assert len(items) == 50
            assert len({item.case_key for item in items}) == 50
            assert run.strategy_metrics_summary_json is not None
            assert run.strategy_metrics_summary_json["case_count"] == 50
            assert run.strategy_metrics_summary_json["succeeded_count"] == 50
            assert run.strategy_metrics_summary_json["failed_count"] == 0

            persisted_payload = {
                "run": {
                    "metrics_config": run.metrics_config,
                    "retrieval_settings": run.retrieval_settings_json,
                    "strategy_summary": run.strategy_metrics_summary_json,
                    "error_message": run.error_message,
                },
                "items": [
                    {
                        "case_key": item.case_key,
                        "metric_summary": item.metric_summary_json,
                        "error_message": item.error_message,
                    }
                    for item in items
                ],
                "results": [
                    {
                        "metric_name": stored.metric_name,
                        "metric_label": stored.metric_label,
                        "details": stored.details_json,
                        "metric_detail": stored.metric_detail_json,
                    }
                    for stored in stored_results
                ],
                "api_detail": detail.model_dump(mode="json"),
                "trace": build_evaluation_trace_export_payload(
                    detail,
                    Settings(app_env="test"),
                ).payload,
            }
            persisted_strings = tuple(_string_values(persisted_payload))

        observed_strings = persisted_strings + tuple(
            record.getMessage() for record in caplog.records
        )
        caplog.clear()
        for case in cases:
            _assert_redacted(observed_strings, case.question, label="question")
            _assert_redacted(observed_strings, case.expected_answer, label="reference answer")
            for expected_signal in case.expected_keywords:
                _assert_redacted(observed_strings, expected_signal, label="expected signal")
        for gold_case in gold_dataset.cases:
            for forbidden_claim in gold_case.forbidden_claims:
                _assert_redacted(observed_strings, forbidden_claim, label="forbidden claim")
            for evidence in gold_case.expected_evidence:
                _assert_redacted(
                    observed_strings,
                    evidence.source_key,
                    label="expected evidence source key",
                )
                for fact_id in evidence.fact_ids:
                    _assert_redacted(
                        observed_strings,
                        fact_id,
                        label="expected evidence fact id",
                    )
        _assert_redacted(
            observed_strings,
            TEST_PASSWORD,
            label="secret-like test value",
        )
    finally:
        engine.dispose()


def _assert_redacted(observed_strings: Sequence[str], value: str | None, *, label: str) -> None:
    if value and any(value in observed for observed in observed_strings):
        pytest.fail(f"{label} was persisted", pytrace=False)


def _string_values(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for child in value.values():
            yield from _string_values(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            yield from _string_values(child)


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

