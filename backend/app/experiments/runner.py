from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import User
from app.db.session import SessionLocal
from app.experiments.availability import (
    ModelAvailability,
    SentenceTransformersLoader,
    check_model_availability,
)
from app.experiments.local_accuracy import (
    EndToEndResult,
    LocalAccuracyCandidate,
    RetrievalScreenResult,
    build_coordinate_search_candidates,
    select_end_to_end_winner,
    select_retrieval_finalists,
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
    ExperimentRetrievalProfile,
    ModelKind,
    ModelProvider,
)
from app.ingest.embedding import (
    EmbeddingAdapterError,
    probe_lmstudio_embedding_dimension,
)
from app.schemas.evaluations import (
    EvaluationCacheMode,
    EvaluationRunCreateRequest,
    EvaluationTriggerType,
)
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
    download_policy_is_explicit: bool = False


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


_HF_OFFLINE_ENV_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


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
        if manifest.schema_version == "phase2.experiment.v2":
            return self._run_v2_local_accuracy(
                manifest,
                options,
                started=started,
                embeddings=embeddings,
                rerankers=rerankers,
            )
        reranker_matrix: list[ExperimentModelCandidate | None] = list(rerankers)
        if not reranker_matrix:
            reranker_matrix = [None]

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
        artifact: dict[str, object] = {
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
        policy = _download_policy_for_candidate(candidate, options)
        if candidate.provider == ModelProvider.LMSTUDIO:
            if model_type != ModelKind.EMBEDDING:
                return ModelAvailability(
                    model_id=candidate.model_id,
                    model_type=model_type,
                    provider=candidate.provider,
                    status="blocked" if candidate.required else "skipped",
                    reason_codes=("unsupported_provider",),
                    required=candidate.required,
                    download_policy=policy,
                    expected_dimension=candidate.expected_dimension,
                )
            if options.mode == ExperimentMode.VALIDATE:
                return ModelAvailability(
                    model_id=candidate.model_id,
                    model_type=model_type,
                    provider=candidate.provider,
                    status="skipped",
                    reason_codes=("availability_not_checked_in_validate_mode",),
                    required=candidate.required,
                    download_policy=policy,
                    expected_dimension=candidate.expected_dimension,
                )
            try:
                probe = probe_lmstudio_embedding_dimension(
                    self.settings,
                    model_name=candidate.model_id,
                )
            except EmbeddingAdapterError:
                return ModelAvailability(
                    model_id=candidate.model_id,
                    model_type=model_type,
                    provider=candidate.provider,
                    status="blocked" if candidate.required else "skipped",
                    reason_codes=("embedding_dimension_probe_failed",),
                    required=candidate.required,
                    download_policy=policy,
                    expected_dimension=candidate.expected_dimension,
                )
            if (
                candidate.expected_dimension is not None
                and candidate.expected_dimension != probe.dimension
            ):
                return ModelAvailability(
                    model_id=candidate.model_id,
                    model_type=model_type,
                    provider=candidate.provider,
                    status="blocked",
                    reason_codes=("embedding_dimension_mismatch",),
                    required=candidate.required,
                    download_policy=policy,
                    expected_dimension=candidate.expected_dimension,
                    actual_dimension=probe.dimension,
                )
            return ModelAvailability(
                model_id=candidate.model_id,
                model_type=model_type,
                provider=candidate.provider,
                status="available",
                reason_codes=("available",),
                required=candidate.required,
                download_policy=policy,
                expected_dimension=candidate.expected_dimension,
                actual_dimension=probe.dimension,
            )
        registered = lookup_model(candidate.model_id, model_type)
        return check_model_availability(
            candidate,
            model_type=model_type,
            registry_entry=registered,
            download_policy=policy,
            mode=options.mode,
            loader=self.loader,
        )

    def _run_v2_local_accuracy(
        self,
        manifest: ExperimentManifest,
        options: ExperimentRunOptions,
        *,
        started: float,
        embeddings: list[ExperimentModelCandidate],
        rerankers: list[ExperimentModelCandidate],
    ) -> dict[str, object]:
        results: list[dict[str, object]] = []
        availability_rows: list[dict[str, object]] = []
        availability_cache: dict[tuple[str, ModelKind], ModelAvailability] = {}
        tuning_candidates = build_coordinate_search_candidates(manifest)

        def availability(
            candidate: ExperimentModelCandidate,
            model_kind: ModelKind,
        ) -> ModelAvailability:
            key = (candidate.model_id, model_kind)
            if key not in availability_cache:
                availability_cache[key] = self._availability(
                    candidate,
                    model_kind,
                    options,
                )
                availability_rows.append(availability_cache[key].model_dump())
            return availability_cache[key]

        candidate_context: dict[
            str,
            tuple[
                ExperimentManifest,
                ExperimentModelCandidate,
                ExperimentModelCandidate | None,
                ModelAvailability,
                ModelAvailability | None,
            ],
        ] = {}
        screening_results: list[RetrievalScreenResult] = []
        for candidate in tuning_candidates:
            profile = _profile_for_tuning_candidate(manifest, candidate)
            embedding = next(
                item for item in embeddings if item.model_id == candidate.embedding_model
            )
            reranker = next(
                (item for item in rerankers if item.model_id == candidate.reranker_model),
                None,
            )
            embedding_availability = availability(embedding, ModelKind.EMBEDDING)
            reranker_availability = (
                availability(reranker, ModelKind.RERANKER) if reranker is not None else None
            )
            candidate_manifest = manifest.model_copy(
                update={
                    "evaluation_scope": "retrieval",
                    "repeats": 1,
                    "strategies": [profile.strategy],
                    "retrieval_profiles": [profile],
                    "embedding_models": [embedding],
                    "reranker_models": [reranker] if reranker is not None else [],
                }
            )
            candidate_context[candidate.candidate_id] = (
                candidate_manifest,
                embedding,
                reranker,
                embedding_availability,
                reranker_availability,
            )
            result = self._run_candidate_pair(
                candidate_manifest,
                replace(options, strategies=[profile.strategy]),
                embedding,
                reranker,
                embedding_availability,
                reranker_availability,
            )
            result.update(
                {
                    "stage": "retrieval_screening",
                    "candidate_id": candidate.candidate_id,
                    "profile_id": candidate.profile_id,
                    "supplemental": candidate.supplemental,
                    "repeat_number": 1,
                }
            )
            results.append(result)
            metric_values = _as_dict(result.get("metrics"))
            recall = _float_or_none(metric_values.get("recall_at_k"))
            mrr = _float_or_none(metric_values.get("mrr"))
            no_context = _float_or_none(metric_values.get("no_context_rate"))
            if recall is not None and mrr is not None and no_context is not None:
                screening_results.append(
                    RetrievalScreenResult(
                        candidate_id=candidate.candidate_id,
                        recall_at_k=recall,
                        mrr=mrr,
                        no_context_rate=no_context,
                        pipeline_failure_count=sum(
                            value
                            for key, value in _failure_summary(
                                result.get("failure_summary")
                            ).items()
                            if key == "pipeline_failure"
                        ),
                    )
                )

        non_supplemental_ids = {
            candidate.candidate_id for candidate in tuning_candidates if not candidate.supplemental
        }
        finalist_ids = select_retrieval_finalists(
            [result for result in screening_results if result.candidate_id in non_supplemental_ids],
            limit=3,
        )
        for candidate_id in finalist_ids:
            (
                retrieval_manifest,
                embedding,
                reranker,
                embedding_availability,
                reranker_availability,
            ) = candidate_context[candidate_id]
            for repeat_number in range(1, manifest.repeats + 1):
                end_to_end_manifest = retrieval_manifest.model_copy(
                    update={
                        "evaluation_scope": "end_to_end",
                        "repeats": repeat_number,
                    }
                )
                profile = end_to_end_manifest.retrieval_profiles[0]
                result = self._run_candidate_pair(
                    end_to_end_manifest,
                    replace(options, strategies=[profile.strategy]),
                    embedding,
                    reranker,
                    embedding_availability,
                    reranker_availability,
                )
                result.update(
                    {
                        "stage": "end_to_end",
                        "candidate_id": candidate_id,
                        "profile_id": profile.profile_id.split("__", 1)[0],
                        "supplemental": False,
                        "repeat_number": repeat_number,
                    }
                )
                results.append(result)

        provisional_winner_id = _provisional_end_to_end_winner(results)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        artifact = {
            "schema_version": EXPERIMENT_RESULT_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "experiment_name": manifest.experiment_name,
            "dataset": manifest.dataset,
            "mode": options.mode.value,
            "download_policy": options.download_policy.value,
            "case_limit": options.case_limit or manifest.case_limit,
            "strategies": manifest.strategies,
            "metrics": options.metrics or manifest.metrics,
            "timeout_seconds": options.timeout_seconds,
            "model_registry_version": MODEL_REGISTRY_VERSION,
            "model_registry": registry_as_artifact(),
            "dataset_check": check_dataset_availability(manifest.dataset, self.settings),
            "model_availability": availability_rows,
            "tuning_candidate_count": len(tuning_candidates),
            "retrieval_finalist_ids": finalist_ids,
            "provisional_end_to_end_winner_id": provisional_winner_id,
            "results": results,
            "summary": _summary(results, elapsed_ms),
            "known_limitations": _known_limitations(),
        }
        return cast(dict[str, object], redact_experiment_artifact(artifact))

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
            "embedding_required": embedding.required,
            "reranker_model_id": reranker.model_id if reranker else "none",
            "reranker_required": reranker.required if reranker else False,
            "required": embedding.required or (reranker.required if reranker else False),
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
    profile = manifest.retrieval_profiles[0] if manifest.retrieval_profiles else None
    experiment_settings = _settings_for_candidate(
        settings,
        embedding,
        reranker,
        embedding_availability,
        manifest.experiment_name,
        generation_profile=manifest.generation_profile,
        profile=profile,
    )
    if manifest.schema_version == "phase2.experiment.v2":
        return _run_runtime_qdrant_evaluation(
            manifest,
            options,
            experiment_settings,
            started=started,
        )
    from app.scripts.retrieval_eval_smoke import (
        SmokeConfig,
        SmokeThresholds,
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
    if options.index_seed_documents:
        preflight_artifact = _run_smoke_preserving_hf_env(
            replace(config, preflight_only=True),
            experiment_settings,
        )
        if _local_smoke_status(_as_dict(preflight_artifact.get("summary"))) == "blocked":
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return _outcome_from_smoke_artifact(preflight_artifact, elapsed_ms)
        try:
            with SessionLocal() as db:
                _seed_and_index(db, experiment_settings)
        except Exception:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return ExperimentEvaluationOutcome(
                status="blocked",
                metrics_by_strategy=[],
                metrics={},
                case_count=None,
                evaluation_run_id=None,
                failure_summary={},
                reason_codes=["seed_indexing_failed"],
                elapsed_ms=elapsed_ms,
            )
    artifact = _run_smoke_preserving_hf_env(config, experiment_settings)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return _outcome_from_smoke_artifact(artifact, elapsed_ms)


def _run_runtime_qdrant_evaluation(
    manifest: ExperimentManifest,
    options: ExperimentRunOptions,
    settings: Settings,
    *,
    started: float,
) -> ExperimentEvaluationOutcome:
    try:
        with SessionLocal() as db:
            service = EvaluationService(settings=settings)
            dataset = service.repository.get_dataset_by_name(
                db,
                dataset_name=manifest.dataset,
            )
            user = db.scalar(
                select(User).where(User.status == "active").order_by(User.user_id.asc()).limit(1)
            )
            if dataset is None or user is None:
                raise ExperimentError("runtime_dataset_or_user_unavailable")
            generation_profile = manifest.generation_profile
            if generation_profile is None:
                raise ExperimentError("generation_profile_missing")
            end_to_end = manifest.evaluation_scope == "end_to_end"
            payload = EvaluationRunCreateRequest(
                dataset_name=manifest.dataset,
                evaluation_dataset_id=dataset.evaluation_dataset_id,
                case_limit=options.case_limit or manifest.case_limit,
                strategies=cast(Any, options.strategies or manifest.strategies),
                metrics=cast(Any, options.metrics or manifest.metrics),
                cache_modes=[EvaluationCacheMode.DISABLED],
                top_k=settings.retrieval_top_k_default,
                rerank_top_n=settings.rerank_top_n_default,
                generation_provider=(generation_profile.provider if end_to_end else None),
                generation_model=generation_profile.model if end_to_end else None,
                trigger_type=EvaluationTriggerType.MANUAL,
                evaluation_scope=manifest.evaluation_scope,
                evaluation_backend=manifest.evaluation_backend,
                experiment_name=manifest.experiment_name,
                experiment_profile_id=(
                    manifest.retrieval_profiles[0].profile_id
                    if manifest.retrieval_profiles
                    else None
                ),
                repeat_number=manifest.repeats,
            )
            created = service.create_run(db, payload=payload, user=user)
            service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id=f"local-accuracy-{created.evaluation_run_id}",
            )
            detail = service.get_run_detail(
                db,
                evaluation_run_id=created.evaluation_run_id,
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            metrics: dict[str, float | int | None] = dict(detail.metric_summary)
            metrics["grounded_answer_pass_rate_provisional"] = (
                detail.grounded_answer_pass_rate_provisional
            )
            metrics["grounded_answer_pass_rate_calibrated"] = (
                detail.grounded_answer_pass_rate_calibrated
            )
            return ExperimentEvaluationOutcome(
                status=detail.status,
                metrics_by_strategy=[
                    comparison.model_dump(mode="json") for comparison in detail.strategy_comparison
                ],
                metrics=metrics,
                case_count=detail.case_count,
                evaluation_run_id=detail.evaluation_run_id,
                failure_summary={
                    "pipeline_failure": detail.pipeline_failed_count,
                    **{
                        candidate.failure_type: sum(
                            1
                            for item in detail.failure_candidates
                            if item.failure_type == candidate.failure_type
                        )
                        for candidate in detail.failure_candidates
                    },
                },
                reason_codes=([detail.error_code] if detail.error_code is not None else []),
                elapsed_ms=elapsed_ms,
            )
    except ExperimentError as exc:
        reason_code = exc.error_code
    except Exception as exc:
        reason_code = _safe_experiment_reason_code(getattr(exc, "error_code", None))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return ExperimentEvaluationOutcome(
        status="blocked",
        metrics_by_strategy=[],
        metrics={},
        case_count=None,
        evaluation_run_id=None,
        failure_summary={},
        reason_codes=[str(reason_code)],
        elapsed_ms=elapsed_ms,
    )


def _outcome_from_smoke_artifact(
    artifact: dict[str, object],
    elapsed_ms: int,
) -> ExperimentEvaluationOutcome:
    summary = _as_dict(artifact.get("summary"))
    status = _local_smoke_status(summary)
    metrics_by_strategy = _list_of_dicts(artifact.get("metrics_by_strategy"))
    return ExperimentEvaluationOutcome(
        status=status,
        metrics_by_strategy=metrics_by_strategy,
        metrics=_aggregate_metrics(metrics_by_strategy),
        case_count=_int_or_none(summary.get("case_count")),
        evaluation_run_id=_int_or_none(artifact.get("evaluation_run_id")),
        failure_summary=_failure_summary(artifact.get("failure_summary")),
        reason_codes=_artifact_reason_codes(artifact),
        elapsed_ms=elapsed_ms,
    )


def _run_smoke_preserving_hf_env(
    config: Any,
    settings: Settings,
) -> dict[str, object]:
    previous = {key: os.environ.get(key) for key in _HF_OFFLINE_ENV_VARS}
    try:
        from app.scripts.retrieval_eval_smoke import run_smoke

        return cast(dict[str, object], run_smoke(config, settings))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


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
    *,
    generation_profile: object | None = None,
    profile: ExperimentRetrievalProfile | None = None,
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
    collection_name = (
        settings.qdrant_collection_name
        if generation_profile is not None
        else f"{settings.qdrant_collection_name}_exp_{collection_hash}"
    )
    generation_provider = getattr(generation_profile, "provider", "fake")
    generation_model = getattr(
        generation_profile,
        "model",
        settings.generation_model_name,
    )
    return settings.model_copy(
        update={
            "embedding_provider": (
                "lmstudio" if embedding.provider == ModelProvider.LMSTUDIO else "local"
            ),
            "embedding_model": embedding.model_id,
            "embedding_vector_dimension": int(dimension),
            "rerank_provider": "local" if reranker is not None else "none",
            "reranker_model": reranker.model_id
            if reranker is not None
            else settings.reranker_model,
            "qdrant_collection_name": collection_name,
            "generation_provider": generation_provider,
            "generation_model_name": generation_model,
            "router_mode": (
                profile.router_mode
                if profile is not None and profile.router_mode is not None
                else settings.router_mode
            ),
            "router_llm_planner_model_name": generation_model,
            "retrieval_top_k_default": profile.top_k if profile else 10,
            "ask_top_k_default": profile.top_k if profile else 10,
            "rerank_top_n_default": profile.rerank_top_n if profile else 3,
            "ask_rerank_top_n_default": profile.rerank_top_n if profile else 3,
            "hybrid_dense_weight": profile.dense_weight if profile else 0.5,
            "hybrid_sparse_weight": profile.sparse_weight if profile else 0.5,
            "hybrid_rrf_k": profile.rrf_k if profile else 60,
            "router_sufficiency_top_score_threshold": (
                profile.agentic_sufficiency_threshold if profile else 0.2
            ),
            "graph_retrieval_max_depth": profile.graph_depth if profile else 2,
            "graph_router_min_signal_score": (
                profile.graph_router_signal_threshold if profile else 0.5
            ),
            "graph_store_provider": (
                "postgres"
                if profile is not None and profile.strategy == "graph_postgres"
                else settings.graph_store_provider
            ),
            "retrieval_cache_enabled": False,
            "trace_export_enabled": False,
            "trace_export_provider": "none",
        }
    )


def _profile_for_tuning_candidate(
    manifest: ExperimentManifest,
    candidate: LocalAccuracyCandidate,
) -> ExperimentRetrievalProfile:
    source = next(
        profile
        for profile in manifest.retrieval_profiles
        if profile.profile_id == candidate.profile_id
    )
    return source.model_copy(
        update={
            "profile_id": candidate.candidate_id,
            "top_k": candidate.top_k,
            "rerank_top_n": candidate.rerank_top_n,
            "dense_weight": candidate.dense_weight,
            "sparse_weight": candidate.sparse_weight,
            "rrf_k": candidate.rrf_k,
            "agentic_sufficiency_threshold": (candidate.agentic_sufficiency_threshold),
            "graph_depth": candidate.graph_depth,
            "graph_router_signal_threshold": (candidate.graph_router_signal_threshold),
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
    elif _has_required_blocked_result(results):
        status = "blocked"
    elif counts["blocked_count"] and counts["succeeded_count"] == 0 and counts["ready_count"] == 0:
        status = "blocked"
    elif counts["succeeded_count"]:
        status = "succeeded"
    elif counts["skipped_count"] and counts["ready_count"] == 0:
        status = "skipped"
    else:
        status = "ready"
    return {"status": status, **counts}


def _provisional_end_to_end_winner(
    results: Sequence[dict[str, object]],
) -> str | None:
    by_candidate: dict[str, dict[str, list[float]]] = {}
    for result in results:
        if result.get("stage") != "end_to_end" or result.get("status") != "succeeded":
            continue
        candidate_id = result.get("candidate_id")
        if not isinstance(candidate_id, str):
            continue
        metrics = _as_dict(result.get("metrics"))
        collected = by_candidate.setdefault(candidate_id, {})
        for metric_name in (
            "grounded_answer_pass_rate_provisional",
            "citation_correctness",
            "answer_completeness",
            "p95_latency",
        ):
            value = _float_or_none(metrics.get(metric_name))
            if value is not None:
                collected.setdefault(metric_name, []).append(value)
    candidates: list[EndToEndResult] = []
    for candidate_id, metrics in by_candidate.items():
        required = {metric_name: _mean(values) for metric_name, values in metrics.items() if values}
        if not all(
            metric_name in required
            for metric_name in (
                "grounded_answer_pass_rate_provisional",
                "citation_correctness",
                "answer_completeness",
                "p95_latency",
            )
        ):
            continue
        candidates.append(
            EndToEndResult(
                candidate_id=candidate_id,
                grounded_answer_pass_rate=required["grounded_answer_pass_rate_provisional"],
                citation_correctness=required["citation_correctness"],
                answer_completeness=required["answer_completeness"],
                p95_latency_ms=required["p95_latency"],
            )
        )
    return select_end_to_end_winner(candidates)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _count_status(results: Sequence[dict[str, object]], status: str) -> int:
    return sum(1 for item in results if item.get("status") == status)


def _has_required_blocked_result(results: Sequence[dict[str, object]]) -> bool:
    return any(item.get("status") == "blocked" and item.get("required") is True for item in results)


def _aggregate_metrics(metrics_by_strategy: Sequence[dict[str, object]]) -> dict[str, float | None]:
    collected: dict[str, list[float]] = {}
    for strategy in metrics_by_strategy:
        metrics = _as_dict(strategy.get("metrics"))
        for metric_name, value in metrics.items():
            value_key = "p95" if metric_name == "p95_latency" else "average"
            metric_value = _as_dict(value).get(value_key)
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
    summary = _as_dict(artifact.get("summary"))
    for key in ("reason_codes", "warnings", "blocked_reason_codes"):
        value = summary.get(key)
        if isinstance(value, list):
            reason_codes.extend(str(item) for item in value if isinstance(item, str))
    preflight = _as_dict(artifact.get("preflight"))
    value = preflight.get("reason_codes")
    if isinstance(value, list):
        reason_codes.extend(str(item) for item in value if isinstance(item, str))
    threshold = _as_dict(artifact.get("threshold_result"))
    value = threshold.get("warnings")
    if isinstance(value, list):
        reason_codes.extend(str(item) for item in value if isinstance(item, str))
    for violation in _list_of_dicts(threshold.get("violations")):
        metric = violation.get("metric")
        if isinstance(metric, str):
            reason_codes.append(f"threshold_violation:{metric}")
    return reason_codes


def _download_policy_for_candidate(
    candidate: ExperimentModelCandidate,
    options: ExperimentRunOptions,
) -> DownloadPolicy:
    if options.download_policy_is_explicit:
        return options.download_policy
    if candidate.download_policy == DownloadPolicy.OPT_IN_DOWNLOAD:
        return options.download_policy
    return candidate.download_policy or options.download_policy


def _local_smoke_status(summary: dict[str, object]) -> str:
    status = summary.get("status")
    if isinstance(status, str) and status:
        return status
    failed_count = _int_or_none(summary.get("failed_count")) or 0
    succeeded_count = _int_or_none(summary.get("succeeded_count")) or 0
    case_count = _int_or_none(summary.get("case_count")) or 0
    if failed_count:
        return "failed"
    if case_count > 0 and succeeded_count >= case_count:
        return "succeeded"
    return "unknown"


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


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _safe_experiment_reason_code(value: object) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if re.fullmatch(r"[a-z][a-z0-9_:-]{0,99}", normalized):
            return normalized
    return "runtime_evaluation_failed"
