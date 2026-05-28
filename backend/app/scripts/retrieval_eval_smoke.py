from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Literal, TypeVar, cast

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import Role, User
from app.db.session import SessionLocal
from app.evaluation.rag_service import EvaluationRagQuestionService, RagEvaluationResult
from app.ingest.embedding import create_embedding_adapter
from app.rag.generation import FakeAnswerGenerator
from app.rag.rerank import create_reranker
from app.rag.retrieval import HttpQdrantSearchClient
from app.rag.strategy import DEFAULT_RETRIEVAL_STRATEGY, RetrievalStrategy
from app.schemas.evaluations import (
    DEFAULT_EVALUATION_METRICS,
    EvaluationMetricName,
    EvaluationRunCreateRequest,
    EvaluationRunDetail,
    EvaluationRunRequestStrategy,
    EvaluationTriggerType,
)
from app.services.evaluation_service import EvaluationService
from app.services.rag_service import RagService

SCHEMA_VERSION = "phase2.ci_eval.v1"
DEFAULT_DATASET = "phase2_strategy_smoke"
DEFAULT_STRATEGIES = "dense,hybrid,agentic_router"
DEFAULT_MODE = "local"
DEFAULT_CASE_LIMIT = 5
DEFAULT_TOP_K = 10
DEFAULT_RERANK_TOP_N = 5
DEFAULT_TIMEOUT_SECONDS = 300
ALLOWED_STRATEGIES = {
    RetrievalStrategy.DENSE.value,
    RetrievalStrategy.SPARSE.value,
    RetrievalStrategy.HYBRID.value,
    RetrievalStrategy.AGENTIC_ROUTER.value,
}
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|credential|token|cookie|csrf|session)\s*[:=]\s*[^,\s;]+"
    r"|bearer\s+[A-Za-z0-9._-]+"
    r"|sk-[A-Za-z0-9]+"
)
_SAFE_ERROR_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_FORBIDDEN_KEYS = {
    "api_key",
    "answer_text",
    "content_text",
    "context_items",
    "context_sources",
    "cookie",
    "credential",
    "csrf",
    "full_answer",
    "full_context",
    "password",
    "prompt",
    "raw_chunk",
    "raw_context",
    "raw_prompt",
    "raw_text",
    "secret",
    "session",
    "token",
}
T = TypeVar("T")


class SmokeError(RuntimeError):
    pass


class _SmokeTimeout(BaseException):
    pass


@dataclass(frozen=True)
class SmokeThresholds:
    recall_at_k_min: float = 0.0
    mrr_min: float = 0.0
    citation_coverage_min: float = 0.0
    groundedness_min: float = 0.0
    faithfulness_min: float = 0.0
    no_context_rate_max: float = 1.0
    p95_latency_ms_max: float = 30000.0
    strategy_selection_accuracy_min: float = 0.0
    fallback_rate_max: float = 1.0
    budget_exhausted_rate_max: float = 1.0
    sufficiency_score_avg_min: float = 0.0
    retrieval_call_count_avg_max: float = 3.0


@dataclass(frozen=True)
class SmokeConfig:
    dataset: str
    strategies: list[str]
    mode: Literal["local"]
    threshold_mode: Literal["warn", "fail"]
    metrics: list[str]
    case_limit: int
    top_k: int
    rerank_top_n: int
    timeout_seconds: int
    output_json: Path
    output_md: Path
    trigger_type: EvaluationTriggerType
    thresholds: SmokeThresholds
    preflight_only: bool = False


@dataclass(frozen=True)
class ThresholdResult:
    passed: bool
    violations: list[dict[str, object]]
    warnings: list[str]


@dataclass(frozen=True)
class PreflightResult:
    status: Literal["ready", "blocked"]
    reason_codes: list[str]
    checks: list[dict[str, object]]


def parse_strategies(value: str) -> list[str]:
    strategies: list[str] = []
    for raw in value.split(","):
        strategy = raw.strip()
        if not strategy:
            continue
        if strategy not in ALLOWED_STRATEGIES:
            raise SmokeError(f"invalid_strategy:{strategy}")
        if strategy not in strategies:
            strategies.append(strategy)
    if not strategies:
        raise SmokeError("strategies_required")
    return strategies


def parse_metrics(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return [metric.value for metric in DEFAULT_EVALUATION_METRICS]
    known = {metric.value for metric in EvaluationMetricName}
    metrics: list[str] = []
    for raw in value.split(","):
        metric = raw.strip()
        if not metric:
            continue
        if metric not in known:
            raise SmokeError(f"invalid_metric:{metric}")
        if metric not in metrics:
            metrics.append(metric)
    if not metrics:
        raise SmokeError("metrics_required")
    return metrics


def parse_thresholds(args: argparse.Namespace) -> SmokeThresholds:
    values = {}
    for field in fields(SmokeThresholds):
        value = getattr(args, field.name)
        if not math.isfinite(value) or value < 0:
            raise SmokeError(f"invalid_threshold:{field.name}")
        values[field.name] = float(value)
    return SmokeThresholds(**values)


def run_smoke(config: SmokeConfig, settings: Settings | None = None) -> dict[str, object]:
    started = time.perf_counter()
    settings = settings or get_settings()
    preflight = preflight_smoke(config, settings)
    if config.preflight_only or preflight.status == "blocked":
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return cast(
            dict[str, object],
            redact_for_artifact(build_preflight_artifact(config, preflight, elapsed_ms)),
        )
    deadline = time.perf_counter() + config.timeout_seconds
    try:
        detail = _run_evaluation(config, settings, deadline=deadline)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return cast(
            dict[str, object],
            redact_for_artifact(
                build_failure_artifact(
                    config,
                    reason_code=_failure_reason_code(exc),
                    elapsed_ms=elapsed_ms,
                    preflight=preflight,
                )
            ),
        )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if elapsed_ms > config.timeout_seconds * 1000:
        return cast(
            dict[str, object],
            redact_for_artifact(
                build_failure_artifact(
                    config,
                    reason_code="timeout_exceeded",
                    elapsed_ms=elapsed_ms,
                    preflight=preflight,
                )
            ),
        )
    artifact = build_artifact(config, detail, elapsed_ms)
    threshold_result = evaluate_thresholds(artifact, config.thresholds, config.threshold_mode)
    artifact["threshold_result"] = {
        "passed": threshold_result.passed,
        "mode": config.threshold_mode,
        "violations": threshold_result.violations,
        "warnings": threshold_result.warnings,
    }
    summary = _dict_or_empty(artifact.get("summary"))
    summary["passed"] = threshold_result.passed
    summary["warnings"] = threshold_result.warnings
    artifact["summary"] = summary
    return cast(dict[str, object], redact_for_artifact(artifact))


def preflight_smoke(config: SmokeConfig, settings: Settings) -> PreflightResult:
    checks: list[dict[str, object]] = []
    reason_codes: list[str] = []
    requires_vector_retrieval = _requires_vector_retrieval(config, settings)
    requires_rerank = _requires_rerank(config)
    if requires_vector_retrieval:
        _reject_fake_provider(
            checks,
            reason_codes,
            name="embedding_provider",
            value=settings.embedding_provider,
            reason_code="fake_embedding_provider_not_allowed",
        )
    else:
        _note_backend_not_applicable(
            checks,
            name="embedding_provider",
            provider=settings.embedding_provider,
            reason="sparse_only_smoke",
        )
    if requires_rerank:
        _reject_fake_provider(
            checks,
            reason_codes,
            name="rerank_provider",
            value=settings.rerank_provider,
            reason_code="fake_reranker_not_allowed",
        )
    else:
        _note_backend_not_applicable(
            checks,
            name="rerank_provider",
            provider=settings.rerank_provider,
            reason="strategy_does_not_rerank",
        )
    _note_generation_backend_not_applicable(checks, settings)
    _check_sparse_settings(config, settings, checks, reason_codes)
    if requires_vector_retrieval:
        _check_qdrant(settings, checks, reason_codes)
        _check_embedding_backend(config, settings, checks, reason_codes)
    else:
        _note_backend_not_applicable(
            checks,
            name="qdrant",
            provider="qdrant",
            reason="sparse_only_smoke",
        )
    if requires_rerank and settings.rerank_provider == "local":
        _check_rerank_backend(settings, checks, reason_codes)
    elif requires_rerank and settings.rerank_provider == "none":
        checks.append({"name": "rerank_backend", "status": "ready", "provider": "none"})
    return PreflightResult(
        status="blocked" if reason_codes else "ready",
        reason_codes=reason_codes,
        checks=checks,
    )


def build_preflight_artifact(
    config: SmokeConfig,
    preflight: PreflightResult,
    elapsed_ms: int,
) -> dict[str, object]:
    warnings = [f"preflight_blocked:{reason_code}" for reason_code in preflight.reason_codes]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {"name": config.dataset, "evaluation_dataset_id": None},
        "strategies": config.strategies,
        "mode": config.mode,
        "threshold_mode": config.threshold_mode,
        "trigger_type": config.trigger_type.value,
        "case_limit": config.case_limit,
        "top_k": config.top_k,
        "rerank_top_n": config.rerank_top_n,
        "timeout_seconds": config.timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "evaluation_run_id": None,
        "summary": {
            "case_count": 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "strategy_count": len(config.strategies),
            "status": preflight.status,
            "blocked": preflight.status == "blocked",
            "blocked_reason_codes": preflight.reason_codes,
            "passed": True,
            "warnings": warnings,
        },
        "metrics_by_strategy": [],
        "failure_summary": {},
        "agentic_summary": None,
        "thresholds": config.thresholds.__dict__,
        "threshold_result": {
            "passed": True,
            "mode": config.threshold_mode,
            "violations": [],
            "warnings": warnings,
        },
        "preflight": {
            "status": preflight.status,
            "reason_codes": preflight.reason_codes,
            "checks": preflight.checks,
        },
        "known_limitations": _known_limitations(),
    }


def build_failure_artifact(
    config: SmokeConfig,
    *,
    reason_code: str,
    elapsed_ms: int,
    preflight: PreflightResult | None = None,
) -> dict[str, object]:
    warning = f"evaluation_failed:{reason_code}"
    artifact: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {"name": config.dataset, "evaluation_dataset_id": None},
        "strategies": config.strategies,
        "mode": config.mode,
        "threshold_mode": config.threshold_mode,
        "trigger_type": config.trigger_type.value,
        "case_limit": config.case_limit,
        "top_k": config.top_k,
        "rerank_top_n": config.rerank_top_n,
        "timeout_seconds": config.timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "evaluation_run_id": None,
        "summary": {
            "case_count": 0,
            "succeeded_count": 0,
            "failed_count": 1,
            "strategy_count": len(config.strategies),
            "status": "failed",
            "blocked": False,
            "blocked_reason_codes": [],
            "passed": False,
            "warnings": [warning],
        },
        "metrics_by_strategy": [],
        "failure_summary": {reason_code: 1},
        "agentic_summary": None,
        "thresholds": config.thresholds.__dict__,
        "threshold_result": {
            "passed": False,
            "mode": config.threshold_mode,
            "violations": [
                {
                    "strategy": "all",
                    "metric": "evaluation_status",
                    "operator": "eq",
                    "threshold": "succeeded",
                    "actual": reason_code,
                }
            ],
            "warnings": [warning],
        },
        "known_limitations": _known_limitations(),
    }
    if preflight is not None:
        artifact["preflight"] = {
            "status": preflight.status,
            "reason_codes": preflight.reason_codes,
            "checks": preflight.checks,
        }
    return artifact


def _run_evaluation(
    config: SmokeConfig,
    settings: Settings,
    *,
    deadline: float,
) -> EvaluationRunDetail:
    return _run_with_timeout(
        _remaining_timeout_seconds(deadline),
        lambda: _run_evaluation_in_session(config, settings),
    )


def _run_evaluation_in_session(
    config: SmokeConfig,
    settings: Settings,
) -> EvaluationRunDetail:
    with SessionLocal() as db:
        service = EvaluationService(
            settings=settings,
            rag_service_factory=_create_smoke_rag_service,
        )
        user = _admin_user(db)
        dataset_name, evaluation_dataset_id = _resolve_dataset(db, service, config.dataset)
        payload = EvaluationRunCreateRequest(
            dataset_name=dataset_name,
            evaluation_dataset_id=evaluation_dataset_id,
            strategies=[EvaluationRunRequestStrategy(strategy) for strategy in config.strategies],
            metrics=[EvaluationMetricName(metric) for metric in config.metrics],
            case_limit=config.case_limit,
            top_k=config.top_k,
            rerank_top_n=config.rerank_top_n,
            trigger_type=config.trigger_type,
        )
        created = service.create_run(db, payload=payload, user=user)
        request_id = _request_id(created.evaluation_run_id, config.strategies)
        try:
            service.run_job(
                db,
                evaluation_run_id=created.evaluation_run_id,
                request_id=request_id,
            )
        except _SmokeTimeout:
            _mark_run_failed_after_timeout(db, service, created.evaluation_run_id)
            raise
        return service.get_run_detail(db, evaluation_run_id=created.evaluation_run_id)


class SmokeEvaluationRagQuestionService(EvaluationRagQuestionService):
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
        return self.evaluate_strategy(
            db,
            question=question,
            request_id=request_id,
            strategy_type=strategy_type,
            top_k=top_k,
            rerank_top_n=rerank_top_n,
        )


def _create_smoke_rag_service(
    settings: Settings,
    db: Session,
) -> EvaluationRagQuestionService:
    del db
    service = RagService(
        settings=settings,
        embedding_adapter=create_embedding_adapter(settings),
        vector_client=HttpQdrantSearchClient(
            url=settings.qdrant_url,
            timeout_seconds=settings.qdrant_timeout_seconds,
        ),
        reranker=create_reranker(settings),
        answer_generator=FakeAnswerGenerator(),
    )
    return SmokeEvaluationRagQuestionService(service)


def build_artifact(
    config: SmokeConfig,
    detail: EvaluationRunDetail,
    elapsed_ms: int,
) -> dict[str, object]:
    metrics_by_strategy = _metrics_by_strategy(detail)
    failure_summary = _failure_summary(detail)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "name": detail.dataset_name,
            "evaluation_dataset_id": detail.evaluation_dataset_id,
        },
        "strategies": config.strategies,
        "mode": config.mode,
        "threshold_mode": config.threshold_mode,
        "trigger_type": config.trigger_type.value,
        "case_limit": config.case_limit,
        "top_k": config.top_k,
        "rerank_top_n": config.rerank_top_n,
        "timeout_seconds": config.timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "evaluation_run_id": detail.evaluation_run_id,
        "summary": {
            "case_count": detail.case_count,
            "succeeded_count": detail.succeeded_count,
            "failed_count": detail.failed_count,
            "strategy_count": len(detail.strategies),
            "passed": True,
            "warnings": [],
        },
        "metrics_by_strategy": metrics_by_strategy,
        "failure_summary": failure_summary,
        "agentic_summary": _safe_json(detail.strategy_metrics_summary_json or {}).get(
            "agentic_summary"
        ),
        "thresholds": config.thresholds.__dict__,
        "known_limitations": _known_limitations(),
    }


def evaluate_thresholds(
    artifact: dict[str, object],
    thresholds: SmokeThresholds,
    threshold_mode: Literal["warn", "fail"],
) -> ThresholdResult:
    del threshold_mode
    rules = {
        "recall_at_k": ("min", thresholds.recall_at_k_min),
        "mrr": ("min", thresholds.mrr_min),
        "citation_coverage": ("min", thresholds.citation_coverage_min),
        "groundedness": ("min", thresholds.groundedness_min),
        "faithfulness": ("min", thresholds.faithfulness_min),
        "no_context_rate": ("max", thresholds.no_context_rate_max),
        "p95_latency": ("max", thresholds.p95_latency_ms_max),
        "strategy_selection_accuracy": ("min", thresholds.strategy_selection_accuracy_min),
        "fallback_rate": ("max", thresholds.fallback_rate_max),
        "budget_exhausted_rate": ("max", thresholds.budget_exhausted_rate_max),
        "sufficiency_score_avg": ("min", thresholds.sufficiency_score_avg_min),
        "retrieval_call_count_avg": ("max", thresholds.retrieval_call_count_avg_max),
    }
    violations: list[dict[str, object]] = []
    summary = _dict_or_empty(artifact.get("summary"))
    failed_count = _int_or_none(summary.get("failed_count")) or 0
    if failed_count > 0:
        violations.append(
            {
                "strategy": "all",
                "metric": "failed_count",
                "operator": "max",
                "threshold": 0,
                "actual": failed_count,
            }
        )
    for strategy in _list_of_dicts(artifact.get("metrics_by_strategy")):
        strategy_name = _safe_string(strategy.get("strategy"), fallback="unknown")
        metrics = strategy.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for metric_name, (operator, threshold) in rules.items():
            metric = metrics.get(metric_name)
            if not isinstance(metric, dict):
                continue
            value_key = "p95" if metric_name == "p95_latency" else "average"
            metric_value = _float_or_none(metric.get(value_key))
            if metric_value is None:
                continue
            failed = metric_value < threshold if operator == "min" else metric_value > threshold
            if failed:
                violations.append(
                    {
                        "strategy": strategy_name,
                        "metric": metric_name,
                        "operator": operator,
                        "threshold": threshold,
                        "actual": round(metric_value, 6),
                    }
                )
    warnings = [
        (
            f"{item['strategy']} {item['metric']} {item['operator']} "
            f"{item['threshold']} actual={item['actual']}"
        )
        for item in violations
    ]
    return ThresholdResult(passed=not violations, violations=violations, warnings=warnings)


def render_markdown_summary(artifact: dict[str, object]) -> str:
    summary = _dict_or_empty(artifact.get("summary"))
    threshold = _dict_or_empty(artifact.get("threshold_result"))
    status = _artifact_status(artifact)
    lines = [
        "# Retrieval Evaluation Smoke",
        "",
        f"- schema_version: `{SCHEMA_VERSION}`",
        f"- dataset: `{_safe_dataset_name(artifact)}`",
        f"- strategies: `{', '.join(_string_list(artifact.get('strategies')))}`",
        f"- mode: `{_safe_string(artifact.get('mode'))}`",
        f"- threshold_mode: `{_safe_string(artifact.get('threshold_mode'))}`",
        f"- status: `{status}`",
        f"- cases: `{summary.get('succeeded_count', 0)}/{summary.get('case_count', 0)} succeeded`",
        "",
        "## Metrics",
        "",
        "| strategy | metric | average | p50 | p95 | count | failed | n/a |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for strategy in _list_of_dicts(artifact.get("metrics_by_strategy")):
        strategy_name = _safe_string(strategy.get("strategy"), fallback="unknown")
        metrics = strategy.get("metrics")
        if not isinstance(metrics, dict):
            continue
        for metric_name in sorted(metrics):
            metric = metrics[metric_name]
            if not isinstance(metric, dict):
                continue
            lines.append(
                "| "
                + " | ".join(
                    [
                        strategy_name,
                        metric_name,
                        _fmt(metric.get("average")),
                        _fmt(metric.get("p50")),
                        _fmt(metric.get("p95")),
                        str(metric.get("count", 0)),
                        str(metric.get("failed_count", 0)),
                        str(metric.get("not_applicable_count", 0)),
                    ]
                )
                + " |"
            )
    warnings = _string_list(threshold.get("warnings"))
    lines.extend(["", "## Thresholds", ""])
    if warnings:
        lines.append("Threshold warnings:")
        lines.extend(f"- `{warning}`" for warning in warnings)
    else:
        lines.append("No threshold warnings.")
    preflight = _dict_or_empty(artifact.get("preflight"))
    if preflight:
        lines.extend(["", "## Preflight", ""])
        lines.append(f"- status: `{_safe_string(preflight.get('status'), fallback='unknown')}`")
        reason_codes = _string_list(preflight.get("reason_codes"))
        if reason_codes:
            lines.append(f"- reason_codes: `{', '.join(reason_codes)}`")
    failure_summary = _dict_or_empty(artifact.get("failure_summary"))
    if failure_summary:
        lines.extend(["", "## Failure Summary", ""])
        for failure_type, count in sorted(failure_summary.items()):
            lines.append(f"- `{failure_type}`: `{count}`")
    lines.extend(
        [
            "",
            "## Privacy",
            "",
            "This summary intentionally excludes raw prompts, full context, raw chunk text, "
            "PII, tokens, and secrets.",
        ]
    )
    return "\n".join(lines) + "\n"


def redact_for_artifact(value: object) -> object:
    if isinstance(value, dict):
        safe: dict[str, object] = {}
        for key, child in value.items():
            key_text = str(key)
            if _is_forbidden_key(key_text):
                safe[key_text] = "[REDACTED]"
            else:
                safe[key_text] = redact_for_artifact(child)
        return safe
    if isinstance(value, list):
        return [redact_for_artifact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def write_outputs(config: SmokeConfig, artifact: dict[str, object]) -> None:
    config.output_json.parent.mkdir(parents=True, exist_ok=True)
    config.output_md.parent.mkdir(parents=True, exist_ok=True)
    config.output_json.write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config.output_md.write_text(render_markdown_summary(artifact), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    try:
        config = config_from_args(argv)
        artifact = run_smoke(config)
        write_outputs(config, artifact)
        threshold = _dict_or_empty(artifact.get("threshold_result"))
        warnings = _string_list(threshold.get("warnings"))
        status = _artifact_status(artifact)
        print(
            "retrieval_eval_smoke "
            f"status={status} "
            f"dataset={_safe_dataset_name(artifact)} "
            f"strategies={','.join(config.strategies)} "
            f"warnings={len(warnings)} "
            f"output_json={config.output_json}"
        )
        if warnings:
            for warning in warnings:
                print(f"threshold_warning {warning}")
        if (
            status == "threshold_violation"
            and config.threshold_mode == "fail"
            and not threshold.get("passed", True)
        ):
            return 2
        if status == "failed":
            return 1
        return 0
    except SmokeError as exc:
        print(f"retrieval_eval_smoke_error code={_redact_string(str(exc))}", file=sys.stderr)
        return 1
    except Exception:
        print("retrieval_eval_smoke_error code=internal_error", file=sys.stderr)
        return 1


def config_from_args(argv: list[str] | None = None) -> SmokeConfig:
    parser = argparse.ArgumentParser(description="Run a safe deterministic retrieval eval smoke.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--strategies", default=DEFAULT_STRATEGIES)
    parser.add_argument("--metrics", default=None)
    parser.add_argument("--mode", choices=["local"], default=DEFAULT_MODE)
    parser.add_argument("--threshold-mode", choices=["warn", "fail"], default="warn")
    parser.add_argument("--case-limit", type=int, default=DEFAULT_CASE_LIMIT)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--rerank-top-n", type=int, default=DEFAULT_RERANK_TOP_N)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("artifacts/retrieval_eval_smoke.json"),
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=Path("artifacts/retrieval_eval_smoke.md"),
    )
    parser.add_argument(
        "--trigger-type",
        choices=[EvaluationTriggerType.CI.value, EvaluationTriggerType.SCHEDULED.value],
        default=EvaluationTriggerType.CI.value,
    )
    parser.add_argument("--preflight-only", action="store_true")
    for field in fields(SmokeThresholds):
        parser.add_argument(
            "--" + field.name.replace("_", "-"),
            type=float,
            default=field.default,
        )
    args = parser.parse_args(argv)
    if args.case_limit < 1 or args.case_limit > 50:
        raise SmokeError("invalid_case_limit")
    if args.top_k < 1 or args.top_k > 20:
        raise SmokeError("invalid_top_k")
    if args.rerank_top_n < 1 or args.rerank_top_n > 20:
        raise SmokeError("invalid_rerank_top_n")
    if args.timeout_seconds < 1:
        raise SmokeError("invalid_timeout_seconds")
    config = SmokeConfig(
        dataset=args.dataset,
        strategies=parse_strategies(args.strategies),
        mode=cast(Literal["local"], args.mode),
        threshold_mode=cast(Literal["warn", "fail"], args.threshold_mode),
        metrics=parse_metrics(args.metrics),
        case_limit=args.case_limit,
        top_k=args.top_k,
        rerank_top_n=args.rerank_top_n,
        timeout_seconds=args.timeout_seconds,
        output_json=args.output_json,
        output_md=args.output_md,
        trigger_type=EvaluationTriggerType(args.trigger_type),
        thresholds=parse_thresholds(args),
        preflight_only=bool(args.preflight_only),
    )
    return config


def _remaining_timeout_seconds(deadline: float) -> float:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise SmokeError("timeout_exceeded")
    return remaining


def _run_with_timeout(timeout_seconds: float, func: Callable[[], T]) -> T:
    if timeout_seconds <= 0:
        raise SmokeError("timeout_exceeded")
    if _can_use_signal_timeout():
        return _run_with_signal_timeout(timeout_seconds, func)
    return func()


def _can_use_signal_timeout() -> bool:
    return (
        threading.current_thread() is threading.main_thread()
        and hasattr(signal, "setitimer")
        and hasattr(signal, "SIGALRM")
    )


def _run_with_signal_timeout(timeout_seconds: float, func: Callable[[], T]) -> T:
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        raise _SmokeTimeout()

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        return func()
    except _SmokeTimeout as exc:
        raise SmokeError("timeout_exceeded") from exc
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)


def _requires_vector_retrieval(config: SmokeConfig, settings: Settings) -> bool:
    strategies = set(config.strategies)
    return (
        RetrievalStrategy.DENSE.value in strategies
        or RetrievalStrategy.AGENTIC_ROUTER.value in strategies
        or (RetrievalStrategy.HYBRID.value in strategies and settings.hybrid_dense_weight > 0)
    )


def _requires_sparse_retrieval(config: SmokeConfig, settings: Settings) -> bool:
    strategies = set(config.strategies)
    return RetrievalStrategy.SPARSE.value in strategies or (
        RetrievalStrategy.HYBRID.value in strategies and settings.hybrid_sparse_weight > 0
    )


def _requires_rerank(config: SmokeConfig) -> bool:
    strategies = set(config.strategies)
    return bool(
        strategies
        & {
            RetrievalStrategy.DENSE.value,
            RetrievalStrategy.AGENTIC_ROUTER.value,
        }
    )


def _check_sparse_settings(
    config: SmokeConfig,
    settings: Settings,
    checks: list[dict[str, object]],
    reason_codes: list[str],
) -> None:
    if not _requires_sparse_retrieval(config, settings):
        return
    if settings.sparse_enabled:
        checks.append({"name": "sparse_backend", "status": "ready", "provider": "postgres_fts"})
        return
    checks.append({"name": "sparse_backend", "status": "blocked", "provider": "postgres_fts"})
    reason_codes.append("sparse_retrieval_disabled")


def _note_backend_not_applicable(
    checks: list[dict[str, object]],
    *,
    name: str,
    provider: str,
    reason: str,
) -> None:
    checks.append(
        {
            "name": name,
            "status": "not_applicable",
            "provider": provider,
            "reason": reason,
        }
    )


def _reject_fake_provider(
    checks: list[dict[str, object]],
    reason_codes: list[str],
    *,
    name: str,
    value: str,
    reason_code: str,
) -> None:
    status = "blocked" if value.lower() == "fake" else "ready"
    checks.append({"name": name, "status": status, "provider": value.lower()})
    if status == "blocked":
        reason_codes.append(reason_code)


def _mark_run_failed_after_timeout(
    db: Session,
    service: EvaluationService,
    evaluation_run_id: int,
) -> None:
    try:
        db.rollback()
        run = service.repository.get_run(db, evaluation_run_id=evaluation_run_id, for_update=True)
        if run is None:
            return
        service.repository.mark_run_failed(
            db,
            run=run,
            error_code="timeout_exceeded",
            error_message=None,
            finished_at=datetime.now(UTC),
        )
        db.commit()
    except Exception:
        db.rollback()


def _failure_reason_code(exc: BaseException) -> str:
    value = str(exc).strip()
    if value and _SAFE_ERROR_CODE_RE.fullmatch(value):
        return value
    if isinstance(exc, SmokeError):
        return "smoke_failed"
    return "evaluation_run_failed"


def _note_generation_backend_not_applicable(
    checks: list[dict[str, object]],
    settings: Settings,
) -> None:
    checks.append(
        {
            "name": "generation_backend",
            "status": "not_applicable",
            "provider": settings.generation_provider,
            "reason": "retrieval_only_smoke",
        }
    )


def _check_qdrant(
    settings: Settings,
    checks: list[dict[str, object]],
    reason_codes: list[str],
) -> None:
    try:
        response = httpx.get(
            f"{settings.qdrant_url.rstrip('/')}/healthz",
            timeout=min(settings.qdrant_timeout_seconds, 5.0),
        )
    except httpx.HTTPError:
        checks.append({"name": "qdrant", "status": "blocked"})
        reason_codes.append("qdrant_unavailable")
        return
    if response.status_code >= 400:
        checks.append({"name": "qdrant", "status": "blocked"})
        reason_codes.append("qdrant_unavailable")
        return
    checks.append({"name": "qdrant", "status": "ready"})


def _check_embedding_backend(
    config: SmokeConfig,
    settings: Settings,
    checks: list[dict[str, object]],
    reason_codes: list[str],
) -> None:
    del config
    if settings.embedding_provider == "local":
        _check_local_embedding_model(settings, checks, reason_codes)
        return
    if settings.embedding_provider == "lmstudio":
        _check_lmstudio_embedding_backend(settings, checks, reason_codes)
        return
    if settings.embedding_provider != "fake":
        checks.append(
            {
                "name": "embedding_backend",
                "status": "blocked",
                "provider": settings.embedding_provider,
            }
        )
        reason_codes.append("embedding_provider_unavailable")


def _check_local_embedding_model(
    settings: Settings,
    checks: list[dict[str, object]],
    reason_codes: list[str],
) -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        checks.append(
            {
                "name": "embedding_backend",
                "status": "blocked",
                "provider": "local",
                "model": settings.embedding_model,
            }
        )
        reason_codes.append("local_embedding_dependency_missing")
        return
    try:
        model = SentenceTransformer(settings.embedding_model)
        vectors = model.encode(["retrieval smoke"], normalize_embeddings=True)
        if len(vectors) != 1:
            raise SmokeError("local_embedding_model_unavailable")
    except Exception:
        checks.append(
            {
                "name": "embedding_backend",
                "status": "blocked",
                "provider": "local",
                "model": settings.embedding_model,
            }
        )
        reason_codes.append("local_embedding_model_or_cache_unavailable")
        return
    checks.append(
        {
            "name": "embedding_backend",
            "status": "ready",
            "provider": "local",
            "model": settings.embedding_model,
        }
    )


def _check_lmstudio_embedding_backend(
    settings: Settings,
    checks: list[dict[str, object]],
    reason_codes: list[str],
) -> None:
    try:
        response = httpx.post(
            f"{settings.lmstudio_base_url}/embeddings",
            headers={"Authorization": f"Bearer {settings.lmstudio_api_key}"},
            json={"model": settings.embedding_model, "input": ["retrieval smoke"]},
            timeout=min(settings.lmstudio_timeout_seconds, 5.0),
        )
    except httpx.HTTPError:
        checks.append({"name": "embedding_backend", "status": "blocked", "provider": "lmstudio"})
        reason_codes.append("lmstudio_embedding_unavailable")
        return
    if response.status_code >= 400:
        checks.append({"name": "embedding_backend", "status": "blocked", "provider": "lmstudio"})
        reason_codes.append("lmstudio_embedding_unavailable")
        return
    checks.append({"name": "embedding_backend", "status": "ready", "provider": "lmstudio"})


def _check_rerank_backend(
    settings: Settings,
    checks: list[dict[str, object]],
    reason_codes: list[str],
) -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        from sentence_transformers import CrossEncoder
    except Exception:
        checks.append({"name": "rerank_backend", "status": "blocked", "provider": "local"})
        reason_codes.append("local_reranker_dependency_missing")
        return
    try:
        CrossEncoder(settings.reranker_model)
    except Exception:
        checks.append({"name": "rerank_backend", "status": "blocked", "provider": "local"})
        reason_codes.append("local_reranker_model_or_cache_unavailable")
        return
    checks.append({"name": "rerank_backend", "status": "ready", "provider": "local"})


def _known_limitations() -> list[str]:
    return [
        "workflow smoke uses real local retrieval with PostgreSQL, Qdrant, "
        "and indexed demo documents",
        "model/cache preflight blocks instead of falling back to fake adapters",
        "answer generation is not exercised",
        "no external LLM judge",
        "no LangSmith export",
        "no production trace sampling",
    ]


def _admin_user(db: Session) -> User:
    user = db.scalar(
        select(User)
        .join(Role, Role.role_id == User.role_id)
        .where(User.status == "active", Role.role_name == "admin")
        .order_by(User.user_id.asc())
    )
    if user is None:
        raise SmokeError("seed_admin_user_missing")
    return user


def _resolve_dataset(
    db: Session,
    service: EvaluationService,
    dataset: str,
) -> tuple[str, int | None]:
    dataset = dataset.strip()
    if not dataset:
        raise SmokeError("dataset_required")
    if dataset.isdigit():
        dataset_id = int(dataset)
        model = service.repository.get_dataset(db, evaluation_dataset_id=dataset_id)
        if model is None:
            raise SmokeError("dataset_not_found")
        if model.status != "active":
            raise SmokeError("dataset_archived")
        return model.dataset_name, model.evaluation_dataset_id
    model = service.repository.get_dataset_by_name(db, dataset_name=dataset)
    if model is not None:
        if model.status != "active":
            raise SmokeError("dataset_archived")
        return model.dataset_name, model.evaluation_dataset_id
    return dataset, None


def _request_id(evaluation_run_id: int, strategies: list[str]) -> str:
    digest = hashlib.sha256(",".join(strategies).encode("utf-8")).hexdigest()[:12]
    return f"ci-smoke:{evaluation_run_id}:{digest}"


def _metrics_by_strategy(detail: EvaluationRunDetail) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for metric in detail.strategy_comparison:
        strategy = metric.strategy_type.value
        entry = grouped.setdefault(strategy, {"strategy": strategy, "metrics": {}})
        metrics = entry["metrics"]
        if not isinstance(metrics, dict):
            continue
        metrics[str(metric.metric_name)] = {
            "average": metric.average,
            "p50": metric.p50,
            "p95": metric.p95,
            "count": metric.count,
            "failed_count": metric.failed_count,
            "not_applicable_count": metric.not_applicable_count,
        }
    return [grouped[strategy] for strategy in sorted(grouped)]


def _failure_summary(detail: EvaluationRunDetail) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in detail.failure_candidates:
        counts[candidate.failure_type] = counts.get(candidate.failure_type, 0) + 1
    return counts


def _safe_json(value: object) -> dict[str, object]:
    redacted = redact_for_artifact(value)
    return redacted if isinstance(redacted, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_or_empty(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _safe_string(value: object, *, fallback: str = "") -> str:
    if not isinstance(value, str) or not value:
        return fallback
    return _redact_string(value)


def _safe_dataset_name(artifact: dict[str, object]) -> str:
    dataset = artifact.get("dataset")
    if isinstance(dataset, dict):
        name = dataset.get("name")
        if isinstance(name, str):
            return _redact_string(name)
    return "unknown"


def _artifact_status(artifact: dict[str, object]) -> str:
    summary = _dict_or_empty(artifact.get("summary"))
    status = summary.get("status")
    if isinstance(status, str) and status:
        return _redact_string(status)
    threshold = _dict_or_empty(artifact.get("threshold_result"))
    return "passed" if threshold.get("passed", True) else "threshold_violation"


def _float_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _fmt(value: object) -> str:
    number = _float_or_none(value)
    if number is None:
        return "N/A"
    return f"{number:.4f}"


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _FORBIDDEN_KEYS:
        return True
    return any(
        part in lowered
        for part in (
            "api_key",
            "chunk_text",
            "content_text",
            "credential",
            "csrf",
            "cookie",
            "full_context",
            "password",
            "raw_chunk",
            "raw_context",
            "raw_prompt",
            "secret",
            "session",
            "token",
        )
    )


def _redact_string(value: str) -> str:
    value = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = _SECRET_VALUE_RE.sub("[REDACTED]", value)
    return value


if __name__ == "__main__":
    raise SystemExit(main())
