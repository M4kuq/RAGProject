from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.config import get_settings
from app.experiments.runner import (
    ExperimentError,
    ExperimentRunOptions,
    RetrievalModelExperimentRunner,
    load_manifest,
    write_experiment_artifacts,
)
from app.experiments.schemas import ALLOWED_EXPERIMENT_STRATEGIES, DownloadPolicy, ExperimentMode
from app.schemas.evaluations import EvaluationMetricName

DEFAULT_MANIFEST = Path("app/experiments/manifests/phase2_retrieval_models.example.json")
DEFAULT_OUTPUT_JSON = Path("../artifacts/experiments/retrieval_model_comparison.json")
DEFAULT_OUTPUT_MD = Path("../artifacts/experiments/retrieval_model_comparison.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a local opt-in SentenceTransformers retrieval model experiment."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ExperimentMode],
        default=ExperimentMode.DRY_RUN.value,
    )
    parser.add_argument(
        "--download-policy",
        choices=[policy.value for policy in DownloadPolicy],
        default=None,
    )
    parser.add_argument("--case-limit", type=int, default=None)
    parser.add_argument("--strategies", type=str, default=None)
    parser.add_argument("--metrics", type=str, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--skip-seed-indexing", action="store_true")
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        options = ExperimentRunOptions(
            mode=ExperimentMode(args.mode),
            download_policy=DownloadPolicy(args.download_policy)
            if args.download_policy is not None
            else DownloadPolicy.IF_CACHED,
            case_limit=_positive_int_or_none(args.case_limit, "case_limit"),
            strategies=_parse_strategies(args.strategies),
            metrics=_parse_metrics(args.metrics),
            timeout_seconds=_positive_int(args.timeout_seconds, "timeout_seconds"),
            index_seed_documents=not args.skip_seed_indexing,
            download_policy_is_explicit=args.download_policy is not None,
        )
        runner = RetrievalModelExperimentRunner(settings=get_settings())
        artifact = runner.run(manifest, options)
        write_experiment_artifacts(
            artifact,
            output_json=args.output_json,
            output_md=args.output_md,
        )
    except ExperimentError as exc:
        print(f"experiment_failed:{exc.error_code}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"experiment_failed:{_safe_error(exc)}", file=sys.stderr)
        return 2

    summary = artifact.get("summary")
    status = summary.get("status") if isinstance(summary, dict) else None
    print(f"experiment_status={status}")
    if options.mode == ExperimentMode.LOCAL and status in {"blocked", "failed", "skipped"}:
        return 2
    return 0


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _parse_strategies(value: str | None) -> list[str] | None:
    strategies = _parse_csv(value)
    if strategies is None:
        return None
    invalid = [strategy for strategy in strategies if strategy not in ALLOWED_EXPERIMENT_STRATEGIES]
    if invalid:
        raise ValueError(f"invalid_strategy:{invalid[0]}")
    return strategies


def _parse_metrics(value: str | None) -> list[str] | None:
    metrics = _parse_csv(value)
    if metrics is None:
        return None
    allowed = {metric.value for metric in EvaluationMetricName}
    invalid = [metric for metric in metrics if metric not in allowed]
    if invalid:
        raise ValueError(f"invalid_metric:{invalid[0]}")
    return metrics


def _positive_int_or_none(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    return _positive_int(value, name)


def _positive_int(value: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"invalid_{name}")
    return value


def _safe_error(exc: Exception) -> str:
    text = str(exc) or exc.__class__.__name__
    return text[:120].replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
