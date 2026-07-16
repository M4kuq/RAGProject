from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.orm import Session

import app.scripts.retrieval_eval_smoke as smoke_module
from app.core.config import Settings
from app.rag.retrieval import HttpQdrantSearchClient
from app.scripts.retrieval_eval_smoke import (
    SCHEMA_VERSION,
    SmokeError,
    SmokeThresholds,
    config_from_args,
    evaluate_thresholds,
    parse_metrics,
    parse_strategies,
    preflight_smoke,
    redact_for_artifact,
    render_markdown_summary,
)


def test_parse_strategies_dedupes_and_rejects_unsupported() -> None:
    assert parse_strategies(
        "dense, hybrid, dense, agentic_router, graph_postgres, graph_neo4j, "
        "langchain_agentic, langgraph_agentic"
    ) == [
        "dense",
        "hybrid",
        "agentic_router",
        "graph_postgres",
        "graph_neo4j",
        "langchain_agentic",
        "langgraph_agentic",
    ]
    assert parse_strategies("llm_tool_orchestrator,langchain_agentic,langgraph_agentic") == [
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    ]

    with pytest.raises(SmokeError, match="invalid_strategy:fallback_dense"):
        parse_strategies("dense,fallback_dense")


def test_parse_metrics_defaults_and_rejects_unknown() -> None:
    defaults = parse_metrics(None)
    assert "recall_at_k" in defaults
    assert "retrieval_call_count_avg" in defaults
    assert "answer_completeness" in defaults
    assert "citation_presence" in defaults
    assert "citation_correctness" in defaults

    assert parse_metrics("recall_at_k,mrr,recall_at_k") == ["recall_at_k", "mrr"]
    with pytest.raises(SmokeError, match="invalid_metric:raw_prompt"):
        parse_metrics("recall_at_k,raw_prompt")


def test_config_defaults_to_real_local_retrieval_strategies() -> None:
    config = config_from_args([])

    assert config.mode == "local"
    assert config.strategies == ["dense", "hybrid", "agentic_router"]


def test_config_parses_graph_quality_thresholds() -> None:
    config = config_from_args(
        [
            "--graph-path-relevance-min",
            "0.7",
            "--graph-citation-coverage-min",
            "0.8",
            "--multi-hop-answerability-min",
            "0.9",
        ]
    )

    assert config.thresholds.graph_path_relevance_min == 0.7
    assert config.thresholds.graph_citation_coverage_min == 0.8
    assert config.thresholds.multi_hop_answerability_min == 0.9


@pytest.mark.parametrize(
    ("option", "value", "error_code"),
    [
        ("--recall-at-k-min", "nan", "invalid_threshold:recall_at_k_min"),
        ("--p95-latency-ms-max", "inf", "invalid_threshold:p95_latency_ms_max"),
    ],
)
def test_config_rejects_non_finite_thresholds(
    option: str,
    value: str,
    error_code: str,
) -> None:
    with pytest.raises(SmokeError, match=error_code):
        config_from_args([option, value])


def test_preflight_blocks_fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    config = config_from_args(["--preflight-only"])
    settings = Settings(
        embedding_provider="fake",
        rerank_provider="fake",
        generation_provider="fake",
    )

    def ready_qdrant(
        settings: Settings,
        checks: list[dict[str, object]],
        reason_codes: list[str],
    ) -> None:
        del settings, reason_codes
        checks.append({"name": "qdrant", "status": "ready"})

    def skip_embedding(
        config: object,
        settings: Settings,
        checks: list[dict[str, object]],
        reason_codes: list[str],
    ) -> None:
        del config, settings, checks, reason_codes

    monkeypatch.setattr(smoke_module, "_check_qdrant", ready_qdrant)
    monkeypatch.setattr(smoke_module, "_check_embedding_backend", skip_embedding)

    result = preflight_smoke(config, settings)

    assert result.status == "blocked"
    assert "fake_embedding_provider_not_allowed" in result.reason_codes
    assert "fake_reranker_not_allowed" in result.reason_codes
    assert "fake_generator_not_allowed" not in result.reason_codes
    assert {
        "name": "generation_backend",
        "status": "not_applicable",
        "provider": "fake",
        "reason": "retrieval_only_smoke",
    } in result.checks


def test_run_smoke_attaches_noop_trace_export_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_from_args(["--preflight-only"])
    settings = Settings(
        embedding_provider="fake",
        rerank_provider="fake",
        generation_provider="fake",
    )

    def ready_qdrant(
        settings: Settings,
        checks: list[dict[str, object]],
        reason_codes: list[str],
    ) -> None:
        del settings, reason_codes
        checks.append({"name": "qdrant", "status": "ready"})

    def skip_embedding(
        config: object,
        settings: Settings,
        checks: list[dict[str, object]],
        reason_codes: list[str],
    ) -> None:
        del config, settings, checks, reason_codes

    monkeypatch.setattr(smoke_module, "_check_qdrant", ready_qdrant)
    monkeypatch.setattr(smoke_module, "_check_embedding_backend", skip_embedding)

    artifact = smoke_module.run_smoke(config, settings)

    assert artifact["trace_export"] == {
        "schema_version": "phase2.trace_export.v1",
        "status": "skipped",
        "provider": "none",
        "reason_code": "disabled",
    }
    assert "fake_embedding_provider_not_allowed" in str(artifact)


def test_sparse_only_preflight_skips_vector_backend_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_from_args(["--strategies", "sparse", "--preflight-only"])
    settings = Settings(embedding_provider="fake", rerank_provider="fake")

    def unexpected_check(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("sparse-only smoke should not require vector backends")

    monkeypatch.setattr(smoke_module, "_check_qdrant", unexpected_check)
    monkeypatch.setattr(smoke_module, "_check_embedding_backend", unexpected_check)

    result = preflight_smoke(config, settings)

    assert result.status == "ready"
    assert result.reason_codes == []
    assert {
        "name": "embedding_provider",
        "status": "not_applicable",
        "provider": "fake",
        "reason": "sparse_only_smoke",
    } in result.checks
    assert {
        "name": "qdrant",
        "status": "not_applicable",
        "provider": "qdrant",
        "reason": "sparse_only_smoke",
    } in result.checks
    assert {
        "name": "sparse_backend",
        "status": "ready",
        "provider": "postgres_fts",
    } in result.checks


def test_actual_smoke_uses_search_only_http_qdrant_retrieval_client() -> None:
    service = smoke_module._create_smoke_rag_service(Settings(), cast(Session, object()))

    assert isinstance(service, smoke_module.SmokeEvaluationRagQuestionService)
    assert isinstance(service.service.vector_client, HttpQdrantSearchClient)


def test_smoke_langgraph_agentic_uses_ask_path_without_raising() -> None:
    # Finding 2: langgraph_agentic is ask-only and is absent from
    # RagSearchRequestStrategy, so the search-only evaluate_strategy override would
    # raise when constructing that enum. The smoke override must route ask-only
    # strategies through the base evaluate_question ask path and produce a result.
    from app.evaluation.rag_service import RagEvaluationResult
    from app.rag.strategy import RetrievalStrategy

    sentinel = RagEvaluationResult(
        retrieval_run_id=7,
        status="succeeded",
        answer_text="ask-path answer",
        citations=[],
        confidence=None,
        retrieval_score_summary=None,
        retrieved_items=[],
        context_sources_for_safety=[],
    )

    class _AskPathService(smoke_module.SmokeEvaluationRagQuestionService):
        def __init__(self) -> None:  # noqa: D401 - test stub, skip base __init__
            self.ask_calls: list[str] = []

        def _answer_question_with_langgraph_agentic(
            self,
            db: object,
            *,
            question: str,
            request_id: str | None,
            top_k: int | None,
            rerank_top_n: int | None,
        ) -> RagEvaluationResult:
            del db, request_id, top_k, rerank_top_n
            self.ask_calls.append(question)
            return sentinel

        def evaluate_strategy(self, *args: object, **kwargs: object) -> RagEvaluationResult:
            raise AssertionError("ask-only strategies must not use the search path")

    service = _AskPathService()

    result = service.evaluate_question(
        cast(Session, object()),
        question="who reports to whom in the org graph",
        request_id="ci-smoke:1:abc",
        strategy_type=RetrievalStrategy.LANGGRAPH_AGENTIC,
    )

    assert result is sentinel
    assert service.ask_calls == ["who reports to whom in the org graph"]


def test_smoke_search_strategy_still_uses_search_override() -> None:
    # A genuine search strategy keeps the search-only evaluate_strategy override.
    from app.evaluation.rag_service import RagEvaluationResult
    from app.rag.strategy import RetrievalStrategy

    sentinel = RagEvaluationResult(
        retrieval_run_id=3,
        status="succeeded",
        answer_text="",
        citations=[],
        confidence=None,
        retrieval_score_summary=None,
        retrieved_items=[],
        context_sources_for_safety=[],
    )

    class _SearchPathService(smoke_module.SmokeEvaluationRagQuestionService):
        def __init__(self) -> None:
            self.search_calls: list[str] = []

        def evaluate_strategy(
            self,
            db: object,
            *,
            question: str,
            request_id: str | None,
            strategy_type: RetrievalStrategy,
            top_k: int | None = None,
            rerank_top_n: int | None = None,
        ) -> RagEvaluationResult:
            del db, request_id, top_k, rerank_top_n
            self.search_calls.append(strategy_type.value)
            return sentinel

    service = _SearchPathService()

    result = service.evaluate_question(
        cast(Session, object()),
        question="what is qdrant",
        request_id="ci-smoke:1:def",
        strategy_type=RetrievalStrategy.HYBRID,
    )

    assert result is sentinel
    assert service.search_calls == ["hybrid"]


def test_timeout_wrapper_raises_before_blocking_call_returns() -> None:
    if not smoke_module._can_use_signal_timeout():
        pytest.skip("signal-based timeout is unavailable on this platform")
    started = time.perf_counter()

    with pytest.raises(SmokeError, match="timeout_exceeded"):
        smoke_module._run_with_timeout(0.01, lambda: time.sleep(0.2))

    assert time.perf_counter() - started < 0.5


def test_signal_timeout_marks_created_run_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = config_from_args(["--timeout-seconds", "1"])
    settings = Settings()

    class CreatedRun:
        evaluation_run_id = 123

    class FakeRun:
        pass

    class FakeSession:
        def __init__(self) -> None:
            self.commits = 0
            self.rollbacks = 0

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

    sessions: list[FakeSession] = []

    class FakeRepository:
        def __init__(self) -> None:
            self.failed_error_codes: list[str] = []

        def get_run(
            self,
            db: FakeSession,
            *,
            evaluation_run_id: int,
            for_update: bool,
        ) -> FakeRun:
            del db, evaluation_run_id, for_update
            return FakeRun()

        def mark_run_failed(
            self,
            db: FakeSession,
            *,
            run: FakeRun,
            error_code: str,
            error_message: object,
            finished_at: object,
        ) -> None:
            del db, run, error_message, finished_at
            self.failed_error_codes.append(error_code)

    repository = FakeRepository()

    class FakeEvaluationService:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            self.repository = repository

        def create_run(
            self,
            db: FakeSession,
            *,
            payload: object,
            user: object,
        ) -> CreatedRun:
            del db, payload, user
            return CreatedRun()

        def run_job(
            self,
            db: FakeSession,
            *,
            evaluation_run_id: int,
            request_id: str | None,
        ) -> dict[str, object]:
            del db, evaluation_run_id, request_id
            raise smoke_module._SmokeTimeout()

        def get_run_detail(
            self,
            db: FakeSession,
            *,
            evaluation_run_id: int,
        ) -> object:
            del db, evaluation_run_id
            raise AssertionError("timeout must not fetch a run detail")

    def session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(smoke_module, "SessionLocal", session_factory)
    monkeypatch.setattr(smoke_module, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(smoke_module, "_admin_user", lambda db: object())
    monkeypatch.setattr(
        smoke_module,
        "_resolve_dataset",
        lambda db, service, dataset: (dataset, None),
    )

    with pytest.raises(smoke_module._SmokeTimeout):
        smoke_module._run_evaluation_in_session(config, settings)

    assert repository.failed_error_codes == ["timeout_exceeded"]
    assert sessions[0].rollbacks == 1
    assert sessions[0].commits == 1


def test_no_signal_timeout_runs_evaluation_on_caller_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main_thread_id = threading.get_ident()
    config = config_from_args(["--timeout-seconds", "1"])
    settings = Settings()
    detail = object()

    class CreatedRun:
        evaluation_run_id = 123

    class ThreadBoundSession:
        def __init__(self) -> None:
            self.thread_id = -1

        def __enter__(self) -> ThreadBoundSession:
            self.thread_id = threading.get_ident()
            return self

        def __exit__(self, *exc_info: object) -> None:
            self.assert_current_thread()

        def assert_current_thread(self) -> None:
            assert threading.get_ident() == self.thread_id

    sessions: list[ThreadBoundSession] = []

    class FakeEvaluationService:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            self.repository = object()

        def create_run(
            self,
            db: ThreadBoundSession,
            *,
            payload: object,
            user: object,
        ) -> CreatedRun:
            del payload, user
            db.assert_current_thread()
            return CreatedRun()

        def run_job(
            self,
            db: ThreadBoundSession,
            *,
            evaluation_run_id: int,
            request_id: str | None,
        ) -> dict[str, object]:
            del evaluation_run_id, request_id
            db.assert_current_thread()
            return {"status": "succeeded"}

        def get_run_detail(
            self,
            db: ThreadBoundSession,
            *,
            evaluation_run_id: int,
        ) -> object:
            del evaluation_run_id
            db.assert_current_thread()
            return detail

    def session_factory() -> ThreadBoundSession:
        session = ThreadBoundSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(smoke_module, "_can_use_signal_timeout", lambda: False)
    monkeypatch.setattr(smoke_module, "SessionLocal", session_factory)
    monkeypatch.setattr(smoke_module, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(smoke_module, "_admin_user", lambda db: object())
    monkeypatch.setattr(
        smoke_module,
        "_resolve_dataset",
        lambda db, service, dataset: (dataset, None),
    )

    result = smoke_module._run_evaluation(
        config,
        settings,
        deadline=time.perf_counter() + 1,
    )

    assert result is detail
    assert len(sessions) == 1
    assert sessions[0].thread_id == main_thread_id


def test_main_writes_failed_artifact_when_evaluation_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    json_path = tmp_path / "retrieval_eval_smoke.json"
    md_path = tmp_path / "retrieval_eval_smoke.md"

    monkeypatch.setattr(
        smoke_module,
        "preflight_smoke",
        lambda config, settings: smoke_module.PreflightResult(
            status="ready",
            reason_codes=[],
            checks=[{"name": "qdrant", "status": "ready"}],
        ),
    )

    def fail_evaluation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise SmokeError("all_cases_failed")

    monkeypatch.setattr(smoke_module, "_run_evaluation", fail_evaluation)

    exit_code = smoke_module.main(
        [
            "--timeout-seconds",
            "1",
            "--output-json",
            str(json_path),
            "--output-md",
            str(md_path),
        ]
    )

    assert exit_code == 1
    artifact = json.loads(json_path.read_text(encoding="utf-8"))
    assert artifact["summary"]["status"] == "failed"
    assert artifact["summary"]["passed"] is False
    assert artifact["failure_summary"] == {"all_cases_failed": 1}
    assert artifact["threshold_result"]["passed"] is False
    assert "all_cases_failed" in md_path.read_text(encoding="utf-8")


def test_seed_script_can_skip_document_indexing(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.scripts.seed as seed_script

    calls: list[bool] = []

    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(seed_script, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        seed_script,
        "seed",
        lambda db, *, index_documents: calls.append(index_documents),
    )

    seed_script.main(["--skip-document-indexing"])

    assert calls == [False]


def test_seed_script_reads_deployed_admin_only_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.scripts.seed as seed_script

    calls: list[dict[str, object]] = []

    class FakeSession:
        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setenv("RAG_DEMO_ADMIN_EMAIL", "aws-admin@example.com")
    monkeypatch.setenv("RAG_DEMO_ADMIN_PASSWORD", "strong-deployed-password")
    monkeypatch.setattr(seed_script, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        seed_script,
        "seed",
        lambda db, **kwargs: calls.append(kwargs),
    )

    seed_script.main(["--skip-document-indexing", "--deployed-admin-from-env"])

    assert calls == [
        {
            "index_documents": False,
            "deployed_admin_email": "aws-admin@example.com",
            "deployed_admin_password": "strong-deployed-password",
        }
    ]


def test_threshold_warn_result_does_not_depend_on_mode() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 0},
        "metrics_by_strategy": [
            {
                "strategy": "dense",
                "metrics": {
                    "recall_at_k": {"average": 0.25},
                    "no_context_rate": {"average": 0.75},
                },
            }
        ],
    }
    result = evaluate_thresholds(
        artifact,
        SmokeThresholds(recall_at_k_min=0.5, no_context_rate_max=0.5),
        "warn",
    )

    assert result.passed is False
    assert [item["metric"] for item in result.violations] == [
        "recall_at_k",
        "no_context_rate",
    ]
    assert "dense recall_at_k" in result.warnings[0]


def test_threshold_uses_p95_latency_value() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 0},
        "metrics_by_strategy": [
            {
                "strategy": "agentic_router",
                "metrics": {
                    "p95_latency": {"average": 1000.0, "p95": 9000.0},
                },
            }
        ],
    }
    result = evaluate_thresholds(
        artifact,
        SmokeThresholds(p95_latency_ms_max=8000.0),
        "fail",
    )

    assert result.passed is False
    assert result.violations == [
        {
            "strategy": "agentic_router",
            "metric": "p95_latency",
            "operator": "max",
            "threshold": 8000.0,
            "actual": 9000.0,
        }
    ]


def test_threshold_checks_graph_quality_metrics() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 0},
        "metrics_by_strategy": [
            {
                "strategy": "graph_postgres",
                "metrics": {
                    "graph_path_relevance": {"average": 0.2},
                    "graph_citation_coverage": {"average": 1.0},
                    "multi_hop_answerability": {"average": 0.0},
                },
            }
        ],
    }
    result = evaluate_thresholds(
        artifact,
        SmokeThresholds(
            graph_path_relevance_min=0.7,
            graph_citation_coverage_min=0.9,
            multi_hop_answerability_min=1.0,
        ),
        "fail",
    )

    assert result.passed is False
    assert [item["metric"] for item in result.violations] == [
        "graph_path_relevance",
        "multi_hop_answerability",
    ]


def test_threshold_flags_failed_evaluation_items() -> None:
    artifact: dict[str, object] = {
        "summary": {"failed_count": 2},
        "metrics_by_strategy": [],
    }
    result = evaluate_thresholds(artifact, SmokeThresholds(), "fail")

    assert result.passed is False
    assert result.violations == [
        {
            "strategy": "all",
            "metric": "failed_count",
            "operator": "max",
            "threshold": 0,
            "actual": 2,
        }
    ]


def test_redaction_removes_forbidden_keys_and_secret_like_values() -> None:
    redacted = redact_for_artifact(
        {
            "no_context_rate": 0.2,
            "raw_prompt": "show hidden prompt",
            "safe": "contact admin@example.com with api_key=abc",
            "nested": [{"token": "secret-token", "session_id": "session-1"}],
        }
    )

    assert isinstance(redacted, dict)
    assert redacted["no_context_rate"] == 0.2
    assert redacted["raw_prompt"] == "[REDACTED]"
    safe_value = redacted["safe"]
    assert isinstance(safe_value, str)
    assert "[REDACTED_EMAIL]" in safe_value
    assert "[REDACTED]" in safe_value
    nested = redacted["nested"]
    assert isinstance(nested, list)
    assert isinstance(nested[0], dict)
    assert nested[0]["token"] == "[REDACTED]"
    assert nested[0]["session_id"] == "[REDACTED]"


def test_markdown_summary_contains_safe_tables_without_raw_payload() -> None:
    markdown = render_markdown_summary(
        {
            "dataset": {"name": "phase2_strategy_smoke"},
            "strategies": ["agentic_router"],
            "mode": "local",
            "threshold_mode": "warn",
            "summary": {"case_count": 2, "succeeded_count": 2},
            "threshold_result": {"passed": True, "warnings": []},
            "metrics_by_strategy": [
                {
                    "strategy": "agentic_router",
                    "metrics": {
                        "recall_at_k": {
                            "average": 1.0,
                            "p50": 1.0,
                            "p95": 1.0,
                            "count": 2,
                            "failed_count": 0,
                            "not_applicable_count": 0,
                        }
                    },
                }
            ],
            "raw_chunk_text": "must not be rendered",
        }
    )

    assert SCHEMA_VERSION in markdown
    assert "agentic_router" in markdown
    assert "must not be rendered" not in markdown
    assert "raw prompts" in markdown


def test_retrieval_eval_workflow_is_manual_scheduled_and_secret_free() -> None:
    workflow_path = Path("../.github/workflows/retrieval-eval-smoke.yml")
    if not workflow_path.exists():
        pytest.skip("workflow file is not copied into the backend Docker test image")
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "GITHUB_STEP_SUMMARY" in workflow
    assert "secrets." not in workflow
    assert "SESSION_SECRET:" not in workflow
    assert "POSTGRES_PASSWORD:" not in workflow
    assert "pull_request:" not in workflow
    assert "- local" in workflow
    assert "qdrant:" in workflow
    assert "Install and cache local embedding prerequisites" in workflow
    assert "sentence-transformers/all-MiniLM-L6-v2" in workflow
    assert "--output-json ../artifacts/retrieval_eval_smoke_preflight.json" in workflow
    assert "mv ../artifacts/retrieval_eval_smoke_preflight.json" in workflow
    assert "vector_required=${vector_required}" in workflow
    assert "SMOKE_VECTOR_REQUIRED" in workflow
    assert "--skip-document-indexing" in workflow
    assert "SMOKE_MODE: ${{ github.event.inputs.mode || 'local' }}" in workflow
    assert "EMBEDDING_PROVIDER: fake" not in workflow
    assert "RERANK_PROVIDER: fake" not in workflow
    assert "GENERATION_PROVIDER: fake" not in workflow
    assert "GENERATION_PROVIDER: ollama" not in workflow

    for wrapper_path in [
        Path("../scripts/run_retrieval_eval_smoke.ps1"),
        Path("../scripts/run_retrieval_eval_smoke.sh"),
    ]:
        if not wrapper_path.exists():
            pytest.skip("local wrapper scripts are not copied into the backend Docker test image")
        wrapper = wrapper_path.read_text(encoding="utf-8")
        assert 'uv run --with "sentence-transformers>=2.7.0,<4" python' in wrapper
