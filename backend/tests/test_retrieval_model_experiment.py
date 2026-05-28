from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.experiments.availability import check_model_availability
from app.experiments.model_registry import lookup_model
from app.experiments.reporting import redact_experiment_artifact, render_markdown_report
from app.experiments.run_retrieval_model_experiment import main as experiment_main
from app.experiments.runner import (
    ExperimentEvaluationOutcome,
    ExperimentRunOptions,
    RetrievalModelExperimentRunner,
    check_dataset_availability,
    load_manifest,
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


def _options(mode: ExperimentMode = ExperimentMode.DRY_RUN) -> ExperimentRunOptions:
    return ExperimentRunOptions(
        mode=mode,
        download_policy=DownloadPolicy.IF_CACHED,
        case_limit=None,
        strategies=None,
        metrics=None,
        timeout_seconds=60,
        index_seed_documents=False,
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
