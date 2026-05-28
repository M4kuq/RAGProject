from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.experiments.availability import (
    ModelAvailability,
    SentenceTransformersLoader,
    check_model_availability,
)
from app.experiments.model_registry import (
    MODEL_REGISTRY_VERSION,
    lookup_model,
    registry_as_artifact,
)
from app.experiments.reporting import redact_experiment_artifact, render_markdown_report
from app.experiments.schemas import (
    EXPERIMENT_RESULT_SCHEMA_VERSION,
    DownloadPolicy,
    ExperimentManifest,
    ExperimentMode,
    ExperimentModelCandidate,
    ModelKind,
)
from app.schemas.evaluations import EvaluationTriggerType
from app.services.evaluation_service import EvaluationService
from app.services.seed import index_seed_documents, seed


class ExperimentError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class ExperimentRunOptions:
    mode: ExperimentMode
    download_policy: DownloadPolicy
    case_limit: int | None
    strategies: list[str] | None
    metrics: list[str] | None
    timeout_seconds: int
    index_seed_documents: bool


@dataclass(frozen=True)
class ExperimentEvaluationOutcome:
    status: str
    metrics_by_strategy: list[dict[str, object]]
    metrics: dict[str, float | int | None]
    case_count: int | None
    evaluation_run_id: int | None
    failure_summary: dict[str, int]
    reason_codes: list[str]
    elapsed_ms: int


EvaluationExecutor = Callable[
    [
        ExperimentManifest,
        ExperimentRunOptions,
        Settings,
        ExperimentModelCandidate,
        ExperimentModelCandidate | None,
        ModelAvailability,
    ],
    ExperimentEvaluationOutcome,
]


def load_manifest(path: Path) -> ExperimentManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExperimentError("manifest_read_failed") from exc
    except json.JSONDecodeError as exc:
        raise ExperimentError("manifest_invalid_json") from exc
    try:
        return ExperimentManifest.model_validate(payload)
    except ValidationError as exc:
        raise ExperimentError("manifest_validation_failed") from exc


class RetrievalModelExperimentRunner:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        loader: SentenceTransformersLoader | None = None,
        evaluation_executor: EvaluationExecutor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.loader = loader
        self.evaluation_executor = evaluation_executor or run_local_strategy_evaluation

    def run(
        self,
        manifest: ExperimentManifest,
        options: ExperimentRunOptions,
    ) -> dict[str, object]:
        started = time.perf_counter()
        results: list[dict[str, object]] = []
        availability_rows: list[dict[str, object]] = []
        embeddings = [candidate for candidate in manifest.embedding_models if candidate.enabled]
        rerankers = [candidate for candidate in manifest.reranker_models if candidate.enabled]
        reranker_matrix: list[ExperimentModelCandidate | None] = (
            list(rerankers) if rerankers else [None]
        )

        for embedding in embeddings:
            embedding_availability = self._availability(
                embedding,
                ModelKind.EMBEDDING,
                options,
            )
            availability_rows.append(embedding_availability.model_dump())
            for reranker in reranker_matrix:
                reranker_availability: ModelAvailability | None = None
                if reranker is not None:
                    reranker_availability = self._availability(
                        reranker,
                        ModelKind.RERANKER,
                        options,
                    )
                    availability_rows.append(reranker_availability.model_dump())
                results.append(
                    self._run_candidate_pair(
                        manifest,
                        options,
                        embedding,
                        reranker,
                        embedding_availability,
                        reranker_availability,
                    )
                )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        dataset_check = check_dataset_availability(manifest.dataset, self.settings)
        artifact = {
            "schema_version": EXPERIMENT_RESULT_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "experiment_name": manifest.experiment_name,
            "dataset": manifest.dataset,
            "mode": options.mode.value,
            "download_policy": options.download_policy.value,
            "case_limit": options.case_limit or manifest.case_limit,
            "strategies": options.strategies or manifest.strategies,
            "metrics": options.metrics or manifest.metrics,
            "timeout_seconds": options.timeout_seconds,
            "model_registry_version": MODEL_REGISTRY_VERSION,
            "model_registry": registry_as_artifact(),
            "dataset_check": dataset_check,
            "model_availability": availability_rows,
            "results": results,
            "summary": _summary(results, elapsed_ms),
            "known_limitations": _known_limitations(),
        }
        return cast(dict[str, object], redact_experiment_artifact(artifact))

    def _availability(
        self,
        candidate: ExperimentModelCandidate,
        model_type: ModelKind,
        options: ExperimentRunOptions,
    ) -> ModelAvailability:
        registered = lookup_model(candidate.model_id, model_type)
        policy = candidate.download_policy or options.download_policy
        return check_model_availability(
            candidate,
            model_type=model_type,
            registry_entry=registered,
            download_policy=policy,
            mode=options.mode,
            loader=self.loader,
        )

    def _run_candidate_pair(
        self,
        manifest: ExperimentManifest,
        options: ExperimentRunOptions,
        embedding: ExperimentModelCandidate,
        reranker: ExperimentModelCandidate | None,
        embedding_availability: ModelAvailability,
        reranker_availability: ModelAvailability | None,
    ) -> dict[str, object]:
        statuses = [embedding_availability.status]
        reason_codes = list(embedding_availability.reason_codes)
        if reranker_availability is not None:
            statuses.append(reranker_availability.status)
            reason_codes.extend(reranker_availability.reason_codes)
        base: dict[str, object] = {
            "embedding_model_id": embedding.model_id,
            "embedding_expected_dimension": embedding_availability.expected_dimension,
            "embedding_actual_dimension": embedding_availability.actual_dimension,
            "reranker_model_id": reranker.model_id if reranker else "none",
            "reason_codes": sorted(set(reason_codes)),
            "case_count": None,
            "evaluation_run_id": None,
            "metrics": {},
            "metrics_by_strategy": [],
            "failure_summary": {},
        }
        if "blocked" in statuses:
            return {**base, "status": "blocked"}
        if "skipped" in statuses:
            return {**base, "status": "skipped"}
        if options.mode in {ExperimentMode.VALIDATE, ExperimentMode.DRY_RUN}:
            return {**base, "status": "ready"}
        try:
            outcome = self.evaluation_executor(
                manifest,
                options,
                self.settings,
                embedding,
                reranker,
                embedding_availability,
            )
        except Exception:
            return {
                **base,
                "status": "failed",
                "reason_codes": sorted(set([*reason_codes, "evaluation_execution_failed"])),
            }
        return {
            **base,
            "status": outcome.status,
            "reason_codes": sorted(set([*reason_codes, *outcome.reason_codes])),
            "case_count": outcome.case_count,
            "evaluation_run_id": outcome.evaluation_run_id,
            "metrics": outcome.metrics,
            "metrics_by_strategy": outcome.metrics_by_strategy,
            "failure_summary": outcome.failure_summary,
            "elapsed_ms": outcome.elapsed_ms,
        }


def run_local_strategy_evaluation(
    manifest: ExperimentManifest,
    options: ExperimentRunOptions,
    settings: Settings,
    embedding: ExperimentModelCandidate,
    reranker: ExperimentModelCandidate | None,
    embedding_availability: ModelAvailability,
) -> ExperimentEvaluationOutcome:
    started = time.perf_counter()
    experiment_settings = _settings_for_candidate(
        settings,
        embedding,
        reranker,
        embedding_availability,
        manifest.experiment_name,
    )
    if options.index_seed_documents:
        with SessionLocal() as db:
            _seed_and_index(db, experiment_settings)
    from app.scripts.retrieval_eval_smoke import (
        SmokeConfig,
        SmokeThresholds,
        run_smoke,
    )

    config = SmokeConfig(
        dataset=manifest.dataset,
        strategies=options.strategies or manifest.strategies,
        mode="local",
        threshold_mode="warn",
        metrics=options.metrics or manifest.metrics,
        case_limit=options.case_limit or manifest.case_limit,
        top_k=10,
        rerank_top_n=5,
        timeout_seconds=options.timeout_seconds,
        output_json=Path("artifacts/experiments/retrieval_model_comparison.json"),
        output_md=Path("artifacts/experiments/retrieval_model_comparison.md"),
        trigger_type=EvaluationTriggerType.MANUAL,
        thresholds=SmokeThresholds(),
        preflight_only=False,
    )
    artifact = run_smoke(config, experiment_settings)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    summary = _as_dict(artifact.get("summary"))
    status = str(summary.get("status") or "unknown")
    metrics_by_strategy = _list_of_dicts(artifact.get("metrics_by_strategy"))
    return ExperimentEvaluationOutcome(
        status="succeeded" if status == "succeeded" else status,
        metrics_by_strategy=metrics_by_strategy,
        metrics=_aggregate_metrics(metrics_by_strategy),
        case_count=_int_or_none(summary.get("case_count")),
        evaluation_run_id=_int_or_none(artifact.get("evaluation_run_id")),
        failure_summary=_failure_summary(artifact.get("failure_summary")),
        reason_codes=_artifact_reason_codes(artifact),
        elapsed_ms=elapsed_ms,
    )


def write_experiment_artifacts(
    artifact: dict[str, object],
    *,
    output_json: Path,
    output_md: Path,
) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output_md.write_text(render_markdown_report(artifact), encoding="utf-8")


def check_dataset_availability(dataset: str, settings: Settings) -> dict[str, object]:
    sqlite_path = _sqlite_database_path(settings.database_url)
    if sqlite_path is not None and not sqlite_path.exists():
        return {"status": "not_checked", "reason_codes": ["dataset_check_unavailable"]}
    try:
        with SessionLocal() as db:
            service = EvaluationService(settings=settings)
            if dataset.isdigit():
                model = service.repository.get_dataset(db, evaluation_dataset_id=int(dataset))
            else:
                model = service.repository.get_dataset_by_name(db, dataset_name=dataset)
            if model is None:
                return {"status": "not_found", "reason_codes": ["dataset_not_found"]}
            if model.status != "active":
                return {"status": "blocked", "reason_codes": ["dataset_not_active"]}
            return {
                "status": "ready",
                "reason_codes": [],
                "evaluation_dataset_id": model.evaluation_dataset_id,
                "dataset_name": model.dataset_name,
            }
    except Exception:
        return {"status": "not_checked", "reason_codes": ["dataset_check_unavailable"]}


def _sqlite_database_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    path = database_url.removeprefix(prefix)
    if path in {":memory:", ""}:
        return None
    return Path(path)


def _settings_for_candidate(
    settings: Settings,
    embedding: ExperimentModelCandidate,
    reranker: ExperimentModelCandidate | None,
    embedding_availability: ModelAvailability,
    experiment_name: str,
) -> Settings:
    dimension = (
        embedding_availability.actual_dimension
        or embedding_availability.expected_dimension
        or embedding.expected_dimension
        or settings.embedding_vector_dimension
    )
    reranker_id = reranker.model_id if reranker else "none"
    collection_key = f"{experiment_name}:{embedding.model_id}:{reranker_id}"
    collection_hash = hashlib.sha256(collection_key.encode()).hexdigest()[:12]
    return settings.model_copy(
        update={
            "embedding_provider": "local",
            "embedding_model": embedding.model_id,
            "embedding_vector_dimension": int(dimension),
            "rerank_provider": "local" if reranker is not None else "none",
            "reranker_model": reranker.model_id
            if reranker is not None
            else settings.reranker_model,
            "qdrant_collection_name": f"{settings.qdrant_collection_name}_exp_{collection_hash}",
            "generation_provider": "fake",
            "trace_export_enabled": False,
            "trace_export_provider": "none",
        }
    )


def _seed_and_index(db: Session, settings: Settings) -> None:
    seed(db, index_documents=False)
    index_seed_documents(db, settings=settings)


def _summary(results: Sequence[dict[str, object]], elapsed_ms: int) -> dict[str, object]:
    counts = {
        "total_runs": len(results),
        "succeeded_count": _count_status(results, "succeeded"),
        "ready_count": _count_status(results, "ready"),
        "skipped_count": _count_status(results, "skipped"),
        "blocked_count": _count_status(results, "blocked"),
        "failed_count": _count_status(results, "failed"),
        "elapsed_ms": elapsed_ms,
    }
    if counts["failed_count"]:
        status = "failed"
    elif counts["blocked_count"] and counts["succeeded_count"] == 0 and counts["ready_count"] == 0:
        status = "blocked"
    elif counts["succeeded_count"]:
        status = "succeeded"
    else:
        status = "ready"
    return {"status": status, **counts}


def _count_status(results: Sequence[dict[str, object]], status: str) -> int:
    return sum(1 for item in results if item.get("status") == status)


def _aggregate_metrics(metrics_by_strategy: Sequence[dict[str, object]]) -> dict[str, float | None]:
    collected: dict[str, list[float]] = {}
    for strategy in metrics_by_strategy:
        metrics = _as_dict(strategy.get("metrics"))
        for metric_name, value in metrics.items():
            metric_value = _as_dict(value).get("average")
            if isinstance(metric_value, int | float):
                collected.setdefault(str(metric_name), []).append(float(metric_value))
    return {
        metric: (sum(values) / len(values) if values else None)
        for metric, values in sorted(collected.items())
    }


def _failure_summary(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, int):
            result[key] = item
    return result


def _artifact_reason_codes(artifact: dict[str, object]) -> list[str]:
    reason_codes: list[str] = []
    for key in ("reason_codes", "warnings"):
        value = artifact.get(key)
        if isinstance(value, list):
            reason_codes.extend(str(item) for item in value if isinstance(item, str))
    threshold = _as_dict(artifact.get("threshold_result"))
    for violation in _list_of_dicts(threshold.get("violations")):
        metric = violation.get("metric")
        if isinstance(metric, str):
            reason_codes.append(f"threshold_violation:{metric}")
    return reason_codes


def _known_limitations() -> list[str]:
    return [
        "local mode is opt-in and may load cached public SentenceTransformers models",
        "normal CI uses validation/unit coverage only and does not download heavy models",
        "production embedding or reranker settings are not changed by the harness",
        "artifacts contain aggregate metrics and safe model metadata only",
    ]


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
