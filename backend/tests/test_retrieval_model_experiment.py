from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import app.experiments.run_retrieval_model_experiment as experiment_cli
import app.scripts.retrieval_eval_smoke as smoke_module
from app.core.config import Settings
from app.experiments.availability import ModelAvailability, check_model_availability
from app.experiments.model_registry import lookup_model
from app.experiments.reporting import redact_experiment_artifact, render_markdown_report
from app.experiments.run_retrieval_model_experiment import main as experiment_main
from app.experiments.runner import (
    ExperimentEvaluationOutcome,
    ExperimentRunOptions,
    RetrievalModelExperimentRunner,
    check_dataset_availability,
    load_manifest,
    run_local_strategy_evaluation,
)
from app.experiments.schemas import (
    DownloadPolicy,
    ExperimentManifest,
    ExperimentMode,
    ExperimentModelCandidate,
    ModelKind,
)


class _AvailableLoader:
    def package_available(self) -> bool:
        return True

    def probe_embedding(self, model_id: str, *, local_files_only: bool) -> int | None:
        assert model_id == "sentence-transformers/all-MiniLM-L6-v2"
        assert local_files_only is True
        return 384

    def probe_reranker(self, model_id: str, *, local_files_only: bool) -> None:
        assert model_id == "cross-encoder/ms-marco-MiniLM-L6-v2"
        assert local_files_only is True


class _MissingLoader:
    def package_available(self) -> bool:
        return False

    def probe_embedding(self, model_id: str, *, local_files_only: bool) -> int | None:
        raise AssertionError("package missing should short-circuit")

    def probe_reranker(self, model_id: str, *, local_files_only: bool) -> None:
        raise AssertionError("package missing should short-circuit")


class _DownloadPolicyProbeLoader:
    def __init__(self) -> None:
        self.embedding_local_files_only: list[bool] = []

    def package_available(self) -> bool:
        return True

    def probe_embedding(self, model_id: str, *, local_files_only: bool) -> int | None:
        assert model_id == "sentence-transformers/all-MiniLM-L6-v2"
        self.embedding_local_files_only.append(local_files_only)
        return 384

    def probe_reranker(self, model_id: str, *, local_files_only: bool) -> None:
        raise AssertionError("reranker should not be probed")


class _MixedRequiredLoader:
    def package_available(self) -> bool:
        return True

    def probe_embedding(self, model_id: str, *, local_files_only: bool) -> int | None:
        del local_files_only
        if model_id == "sentence-transformers/all-MiniLM-L6-v2":
            return 384
        raise RuntimeError("model unavailable")

    def probe_reranker(self, model_id: str, *, local_files_only: bool) -> None:
        del model_id, local_files_only
        raise AssertionError("reranker should not be probed")


def test_manifest_parse_and_validation(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")

    manifest = load_manifest(manifest_path)

    assert manifest.schema_version == "phase2.experiment.v1"
    assert manifest.strategies == ["dense", "hybrid"]
    assert manifest.metrics == ["recall_at_k", "mrr", "no_context_rate"]


def test_manifest_rejects_secret_like_model_id() -> None:
    payload = _manifest_payload()
    embedding_models = payload["embedding_models"]
    assert isinstance(embedding_models, list)
    first_model = embedding_models[0]
    assert isinstance(first_model, dict)
    first_model["model_id"] = "sk-secret-token"

    with pytest.raises(ValueError):
        ExperimentManifest.model_validate(payload)


def test_manifest_rejects_case_limit_above_evaluation_runner_limit() -> None:
    payload = _manifest_payload()
    payload["case_limit"] = 51

    with pytest.raises(ValueError):
        ExperimentManifest.model_validate(payload)


def test_model_registry_lookup() -> None:
    model = lookup_model("sentence-transformers/all-MiniLM-L6-v2", ModelKind.EMBEDDING)

    assert model is not None
    assert model.expected_dimension == 384
    assert lookup_model("sentence-transformers/all-MiniLM-L6-v2", ModelKind.RERANKER) is None


def test_availability_if_cached_available_without_download() -> None:
    candidate = ExperimentModelCandidate(
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        expected_dimension=384,
    )

    result = check_model_availability(
        candidate,
        model_type=ModelKind.EMBEDDING,
        registry_entry=lookup_model(candidate.model_id, ModelKind.EMBEDDING),
        download_policy=DownloadPolicy.IF_CACHED,
        mode=ExperimentMode.DRY_RUN,
        loader=_AvailableLoader(),
    )

    assert result.status == "available"
    assert result.actual_dimension == 384
    assert result.reason_codes == ("available",)


def test_availability_missing_optional_model_is_skipped() -> None:
    candidate = ExperimentModelCandidate(
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        required=False,
    )

    result = check_model_availability(
        candidate,
        model_type=ModelKind.EMBEDDING,
        registry_entry=lookup_model(candidate.model_id, ModelKind.EMBEDDING),
        download_policy=DownloadPolicy.IF_CACHED,
        mode=ExperimentMode.DRY_RUN,
        loader=_MissingLoader(),
    )

    assert result.status == "skipped"
    assert result.reason_codes == ("sentence_transformers_unavailable",)


def test_required_missing_model_blocks_experiment() -> None:
    manifest = ExperimentManifest.model_validate(
        _manifest_payload(required_embedding=True, include_reranker=False)
    )
    runner = RetrievalModelExperimentRunner(settings=Settings(), loader=_MissingLoader())

    artifact = runner.run(manifest, _options())

    assert artifact["summary"]["status"] == "blocked"  # type: ignore[index]
    assert artifact["results"][0]["status"] == "blocked"  # type: ignore[index]


def test_dry_run_ready_with_available_models() -> None:
    manifest = ExperimentManifest.model_validate(_manifest_payload())
    runner = RetrievalModelExperimentRunner(settings=Settings(), loader=_AvailableLoader())

    artifact = runner.run(manifest, _options())

    assert artifact["schema_version"] == "phase2.st_experiment_result.v1"
    assert artifact["summary"]["ready_count"] == 1  # type: ignore[index]
    assert artifact["results"][0]["status"] == "ready"  # type: ignore[index]
    assert "dataset_check" in artifact
    assert "raw prompt" not in str(artifact).lower()


def test_local_mode_uses_evaluation_executor_and_summarizes_metrics() -> None:
    manifest = ExperimentManifest.model_validate(_manifest_payload())

    def executor(*args: object, **kwargs: object) -> ExperimentEvaluationOutcome:
        del args, kwargs
        return ExperimentEvaluationOutcome(
            status="succeeded",
            metrics_by_strategy=[
                {
                    "strategy": "dense",
                    "metrics": {
                        "recall_at_k": {"average": 0.75},
                        "mrr": {"average": 0.5},
                    },
                }
            ],
            metrics={"recall_at_k": 0.75, "mrr": 0.5},
            case_count=2,
            evaluation_run_id=123,
            failure_summary={},
            reason_codes=[],
            elapsed_ms=10,
        )

    runner = RetrievalModelExperimentRunner(
        settings=Settings(),
        loader=_AvailableLoader(),
        evaluation_executor=executor,
    )

    artifact = runner.run(
        manifest,
        _options(mode=ExperimentMode.LOCAL),
    )

    first_result = artifact["results"][0]  # type: ignore[index]
    assert first_result["status"] == "succeeded"
    assert first_result["evaluation_run_id"] == 123
    assert first_result["metrics"]["recall_at_k"] == 0.75


def test_explicit_cli_download_policy_overrides_manifest_default() -> None:
    payload = _manifest_payload(include_reranker=False)
    embedding_models = payload["embedding_models"]
    assert isinstance(embedding_models, list)
    first_model = embedding_models[0]
    assert isinstance(first_model, dict)
    first_model["download_policy"] = "if-cached"
    manifest = ExperimentManifest.model_validate(payload)
    loader = _DownloadPolicyProbeLoader()

    def executor(*args: object, **kwargs: object) -> ExperimentEvaluationOutcome:
        del args, kwargs
        return ExperimentEvaluationOutcome(
            status="succeeded",
            metrics_by_strategy=[],
            metrics={},
            case_count=1,
            evaluation_run_id=123,
            failure_summary={},
            reason_codes=[],
            elapsed_ms=10,
        )

    runner = RetrievalModelExperimentRunner(
        settings=Settings(),
        loader=loader,
        evaluation_executor=executor,
    )

    artifact = runner.run(
        manifest,
        _options(
            mode=ExperimentMode.LOCAL,
            download_policy=DownloadPolicy.OPT_IN_DOWNLOAD,
            download_policy_is_explicit=True,
        ),
    )

    assert loader.embedding_local_files_only == [False]
    assert artifact["model_availability"][0]["download_policy"] == "opt-in-download"  # type: ignore[index]


def test_local_smoke_summary_without_status_counts_as_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_smoke(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "summary": {
                "case_count": 2,
                "succeeded_count": 2,
                "failed_count": 0,
                "passed": True,
            },
            "evaluation_run_id": 456,
            "metrics_by_strategy": [
                {
                    "strategy": "dense",
                    "metrics": {
                        "recall_at_k": {"average": 0.75},
                        "p95_latency": {"average": 1000.0, "p95": 9000.0},
                    },
                }
            ],
            "failure_summary": {},
        }

    monkeypatch.setattr(smoke_module, "run_smoke", fake_smoke)

    outcome = run_local_strategy_evaluation(
        ExperimentManifest.model_validate(_manifest_payload(include_reranker=False)),
        _options(mode=ExperimentMode.LOCAL),
        Settings(),
        ExperimentModelCandidate(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            expected_dimension=384,
        ),
        None,
        ModelAvailability(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            model_type=ModelKind.EMBEDDING,
            provider=ExperimentModelCandidate(
                model_id="sentence-transformers/all-MiniLM-L6-v2"
            ).provider,
            status="available",
            reason_codes=("available",),
            required=False,
            download_policy=DownloadPolicy.IF_CACHED,
            expected_dimension=384,
            actual_dimension=384,
        ),
    )

    assert outcome.status == "succeeded"
    assert outcome.metrics["recall_at_k"] == 0.75
    assert outcome.metrics["p95_latency"] == 9000.0


def test_local_smoke_preflight_reasons_are_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_smoke(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {
            "summary": {
                "status": "blocked",
                "case_count": 0,
                "blocked_reason_codes": ["qdrant_unavailable"],
                "warnings": ["preflight_blocked:qdrant_unavailable"],
            },
            "preflight": {
                "status": "blocked",
                "reason_codes": ["qdrant_unavailable"],
            },
            "metrics_by_strategy": [],
            "failure_summary": {},
        }

    monkeypatch.setattr(smoke_module, "run_smoke", fake_smoke)

    outcome = run_local_strategy_evaluation(
        ExperimentManifest.model_validate(_manifest_payload(include_reranker=False)),
        _options(mode=ExperimentMode.LOCAL),
        Settings(),
        ExperimentModelCandidate(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            expected_dimension=384,
        ),
        None,
        ModelAvailability(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            model_type=ModelKind.EMBEDDING,
            provider=ExperimentModelCandidate(
                model_id="sentence-transformers/all-MiniLM-L6-v2"
            ).provider,
            status="available",
            reason_codes=("available",),
            required=False,
            download_policy=DownloadPolicy.IF_CACHED,
            expected_dimension=384,
            actual_dimension=384,
        ),
    )

    assert outcome.status == "blocked"
    assert "qdrant_unavailable" in outcome.reason_codes
    assert "preflight_blocked:qdrant_unavailable" in outcome.reason_codes


def test_local_mode_all_skipped_is_not_ready() -> None:
    manifest = ExperimentManifest.model_validate(_manifest_payload(include_reranker=False))
    runner = RetrievalModelExperimentRunner(settings=Settings(), loader=_MissingLoader())

    artifact = runner.run(manifest, _options(mode=ExperimentMode.LOCAL))

    assert artifact["summary"]["status"] == "skipped"  # type: ignore[index]
    assert artifact["results"][0]["status"] == "skipped"  # type: ignore[index]


def test_cli_local_skipped_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")

    class FakeRunner:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def run(
            self,
            manifest: ExperimentManifest,
            options: ExperimentRunOptions,
        ) -> dict[str, object]:
            assert manifest.experiment_name == "phase2_retrieval_model_comparison"
            assert options.mode == ExperimentMode.LOCAL
            return {"summary": {"status": "skipped"}}

    monkeypatch.setattr(experiment_cli, "RetrievalModelExperimentRunner", FakeRunner)

    result = experiment_cli.main(
        [
            "--manifest",
            str(manifest_path),
            "--mode",
            "local",
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-md",
            str(tmp_path / "result.md"),
        ]
    )

    assert result == 2


def test_required_blocked_candidate_keeps_overall_status_blocked() -> None:
    payload = _manifest_payload(include_reranker=False)
    payload["embedding_models"] = [
        {
            "model_id": "BAAI/bge-small-en-v1.5",
            "provider": "sentence_transformers",
            "enabled": True,
            "required": True,
            "expected_dimension": 384,
        },
        {
            "model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "provider": "sentence_transformers",
            "enabled": True,
            "required": False,
            "expected_dimension": 384,
        },
    ]
    manifest = ExperimentManifest.model_validate(payload)

    def executor(*args: object, **kwargs: object) -> ExperimentEvaluationOutcome:
        del args, kwargs
        return ExperimentEvaluationOutcome(
            status="succeeded",
            metrics_by_strategy=[],
            metrics={},
            case_count=1,
            evaluation_run_id=123,
            failure_summary={},
            reason_codes=[],
            elapsed_ms=10,
        )

    runner = RetrievalModelExperimentRunner(
        settings=Settings(),
        loader=_MixedRequiredLoader(),
        evaluation_executor=executor,
    )

    artifact = runner.run(manifest, _options(mode=ExperimentMode.LOCAL))

    assert artifact["summary"]["status"] == "blocked"  # type: ignore[index]
    assert artifact["summary"]["succeeded_count"] == 1  # type: ignore[index]
    assert artifact["summary"]["blocked_count"] == 1  # type: ignore[index]


def test_smoke_preflight_blocks_before_seed_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_smoke(config: Any, settings: Settings) -> dict[str, object]:
        del settings
        assert config.preflight_only is True
        return {
            "summary": {
                "status": "blocked",
                "case_count": 0,
                "blocked_reason_codes": ["qdrant_unavailable"],
            },
            "preflight": {
                "status": "blocked",
                "reason_codes": ["qdrant_unavailable"],
            },
            "metrics_by_strategy": [],
            "failure_summary": {},
        }

    monkeypatch.setattr(smoke_module, "run_smoke", fake_smoke)
    monkeypatch.setattr(
        "app.experiments.runner.SessionLocal",
        lambda: pytest.fail("seed indexing should not run after blocked preflight"),
    )

    outcome = run_local_strategy_evaluation(
        ExperimentManifest.model_validate(_manifest_payload(include_reranker=False)),
        _options(mode=ExperimentMode.LOCAL, index_seed_documents=True),
        Settings(),
        ExperimentModelCandidate(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            expected_dimension=384,
        ),
        None,
        _available_embedding_availability(),
    )

    assert outcome.status == "blocked"
    assert "qdrant_unavailable" in outcome.reason_codes


def test_run_smoke_restores_huggingface_offline_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "original")

    def fake_smoke(config: object, settings: Settings) -> dict[str, object]:
        del config, settings
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return {
            "summary": {
                "case_count": 1,
                "succeeded_count": 1,
                "failed_count": 0,
            },
            "metrics_by_strategy": [],
            "failure_summary": {},
        }

    monkeypatch.setattr(smoke_module, "run_smoke", fake_smoke)

    outcome = run_local_strategy_evaluation(
        ExperimentManifest.model_validate(_manifest_payload(include_reranker=False)),
        _options(mode=ExperimentMode.LOCAL),
        Settings(),
        ExperimentModelCandidate(
            model_id="sentence-transformers/all-MiniLM-L6-v2",
            expected_dimension=384,
        ),
        None,
        _available_embedding_availability(),
    )

    assert outcome.status == "succeeded"
    assert "HF_HUB_OFFLINE" not in os.environ
    assert os.environ["TRANSFORMERS_OFFLINE"] == "original"


def test_local_mode_executor_failure_is_safe_result() -> None:
    manifest = ExperimentManifest.model_validate(_manifest_payload(include_reranker=False))

    def executor(*args: object, **kwargs: object) -> ExperimentEvaluationOutcome:
        del args, kwargs
        raise RuntimeError("raw prompt should not leak")

    runner = RetrievalModelExperimentRunner(
        settings=Settings(),
        loader=_AvailableLoader(),
        evaluation_executor=executor,
    )

    artifact = runner.run(
        manifest,
        _options(mode=ExperimentMode.LOCAL),
    )

    first_result = artifact["results"][0]  # type: ignore[index]
    assert first_result["status"] == "failed"
    assert first_result["reason_codes"] == ["available", "evaluation_execution_failed"]
    assert "raw prompt should not leak" not in str(artifact)


def test_artifact_redaction_removes_raw_text_and_paths() -> None:
    redacted = redact_experiment_artifact(
        {
            "safe": "value",
            "raw_prompt": "tell me secret",
            "cache_path": r"C:\Users\kei01\.cache\huggingface",
            "nested": {"chunk_text": "raw chunk"},
        }
    )

    assert redacted["safe"] == "value"  # type: ignore[index]
    assert redacted["raw_prompt"] == "[REDACTED]"  # type: ignore[index]
    assert redacted["cache_path"] == "[REDACTED]"  # type: ignore[index]
    assert redacted["nested"]["chunk_text"] == "[REDACTED]"  # type: ignore[index]


def test_markdown_report_uses_safe_summary_only() -> None:
    markdown = render_markdown_report(
        {
            "schema_version": "phase2.st_experiment_result.v1",
            "experiment_name": "exp",
            "dataset": "phase2_strategy_smoke",
            "mode": "dry-run",
            "summary": {"status": "ready", "total_runs": 1},
            "results": [
                {
                    "embedding_model_id": "sentence-transformers/all-MiniLM-L6-v2",
                    "reranker_model_id": "none",
                    "status": "ready",
                    "case_count": 2,
                    "metrics": {"recall_at_k": 0.5},
                    "reason_codes": [],
                }
            ],
        }
    )

    assert "SentenceTransformers Experiment Report" in markdown
    assert "sentence-transformers/all-MiniLM-L6-v2" in markdown
    assert "tell me secret" not in markdown.lower()


def test_cli_dry_run_writes_artifacts(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    output_json = tmp_path / "result.json"
    output_md = tmp_path / "result.md"
    payload = _manifest_payload(include_reranker=False)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    result = experiment_main(
        [
            "--manifest",
            str(manifest_path),
            "--mode",
            "dry-run",
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    )

    assert result == 0
    artifact = json.loads(output_json.read_text(encoding="utf-8"))
    assert artifact["schema_version"] == "phase2.st_experiment_result.v1"
    assert output_md.read_text(encoding="utf-8").startswith(
        "# SentenceTransformers Experiment Report"
    )


def test_cli_rejects_invalid_strategy(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")

    result = experiment_main(
        [
            "--manifest",
            str(manifest_path),
            "--mode",
            "dry-run",
            "--strategies",
            "dense,fallback_dense",
        ]
    )

    assert result == 2


def test_cli_rejects_case_limit_above_evaluation_runner_limit(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")

    result = experiment_main(
        [
            "--manifest",
            str(manifest_path),
            "--mode",
            "dry-run",
            "--case-limit",
            "51",
        ]
    )

    assert result == 2


def test_wrappers_do_not_override_manifest_download_policy_by_default() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    shell_wrapper_path = repo_root / "scripts" / "run_retrieval_model_experiment.sh"
    ps_wrapper_path = repo_root / "scripts" / "run_retrieval_model_experiment.ps1"
    if not shell_wrapper_path.exists() or not ps_wrapper_path.exists():
        pytest.skip("repository-level wrapper scripts are not copied into backend test image")

    shell_wrapper = shell_wrapper_path.read_text(encoding="utf-8")
    ps_wrapper = ps_wrapper_path.read_text(encoding="utf-8")

    assert 'DOWNLOAD_POLICY="${DOWNLOAD_POLICY:-}"' in shell_wrapper
    assert '--download-policy "${DOWNLOAD_POLICY}"' not in shell_wrapper
    assert '[string]$DownloadPolicy = ""' in ps_wrapper
    assert 'if ($DownloadPolicy -ne "")' in ps_wrapper


def test_dataset_check_reports_unavailable_db_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenSession:
        def __enter__(self) -> object:
            raise RuntimeError("db unavailable")

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("app.experiments.runner.SessionLocal", lambda: BrokenSession())

    result = check_dataset_availability("phase2_strategy_smoke", Settings())

    assert result == {"status": "not_checked", "reason_codes": ["dataset_check_unavailable"]}


def test_dataset_check_does_not_create_missing_sqlite_file(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    result = check_dataset_availability(
        "phase2_strategy_smoke",
        Settings(database_url=f"sqlite:///{db_path}"),
    )

    assert result == {"status": "not_checked", "reason_codes": ["dataset_check_unavailable"]}
    assert not db_path.exists()


def _options(
    mode: ExperimentMode = ExperimentMode.DRY_RUN,
    *,
    download_policy: DownloadPolicy = DownloadPolicy.IF_CACHED,
    download_policy_is_explicit: bool = False,
    index_seed_documents: bool = False,
) -> ExperimentRunOptions:
    return ExperimentRunOptions(
        mode=mode,
        download_policy=download_policy,
        case_limit=None,
        strategies=None,
        metrics=None,
        timeout_seconds=60,
        index_seed_documents=index_seed_documents,
        download_policy_is_explicit=download_policy_is_explicit,
    )


def _available_embedding_availability() -> ModelAvailability:
    candidate = ExperimentModelCandidate(model_id="sentence-transformers/all-MiniLM-L6-v2")
    return ModelAvailability(
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        model_type=ModelKind.EMBEDDING,
        provider=candidate.provider,
        status="available",
        reason_codes=("available",),
        required=False,
        download_policy=DownloadPolicy.IF_CACHED,
        expected_dimension=384,
        actual_dimension=384,
    )


def _manifest_payload(
    *,
    required_embedding: bool = False,
    include_reranker: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "phase2.experiment.v1",
        "experiment_name": "phase2_retrieval_model_comparison",
        "dataset": "phase2_strategy_smoke",
        "case_limit": 2,
        "strategies": ["dense", "hybrid"],
        "embedding_models": [
            {
                "model_id": "sentence-transformers/all-MiniLM-L6-v2",
                "provider": "sentence_transformers",
                "enabled": True,
                "required": required_embedding,
                "expected_dimension": 384,
            }
        ],
        "reranker_models": [],
        "metrics": ["recall_at_k", "mrr", "no_context_rate"],
        "mode": "local_opt_in",
    }
    if include_reranker:
        payload["reranker_models"] = [
            {
                "model_id": "cross-encoder/ms-marco-MiniLM-L6-v2",
                "provider": "sentence_transformers",
                "enabled": True,
                "required": False,
            }
        ]
    return payload
