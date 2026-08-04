from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from app.evaluation.security_gate_phase2 import (
    PHASE2_SECURITY_EVALUATION_SCOPE,
    evaluate_phase2_control,
    load_phase2_security_dataset,
    phase2_security_dataset_fingerprint,
)
from app.rag.injection_detection import InjectionPolicyName

_PROFILES: tuple[InjectionPolicyName, ...] = (
    "observe_only",
    "block_user_quarantine_context",
)
_CANDIDATE_POLICY: InjectionPolicyName = "block_user_quarantine_context"
_BANNED_RAW_KEYS = {
    "question",
    "clean_question",
    "clean_input",
    "attack_input",
    "context",
    "context_text",
    "answer",
    "answer_text",
    "content",
    "chunk_text",
    "attack_canary",
    "expected_fact",
}


class Phase2SecurityGateRunError(RuntimeError):
    pass


def main() -> None:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    activity_path = output_dir / "activity.jsonl"
    started_at = _utc_now()
    _append_event(
        activity_path,
        {
            "event": "run_started",
            "run_id": run_id,
            "started_at": started_at,
            "evaluation_scope": PHASE2_SECURITY_EVALUATION_SCOPE,
            "external_model_used": False,
            "raw_content_persisted": False,
        },
    )

    dataset = load_phase2_security_dataset()
    outcomes: list[dict[str, object]] = []
    for profile in _PROFILES:
        for case in dataset.cases:
            outcome = asdict(evaluate_phase2_control(case, policy=profile))
            outcome["profile"] = profile
            outcome["reason_codes"] = list(cast(tuple[str, ...], outcome["reason_codes"]))
            outcomes.append(outcome)
            _append_event(
                activity_path,
                {"event": "case_completed", "run_id": run_id, **outcome},
            )

    summary = _summary(
        run_id=run_id,
        started_at=started_at,
        git_sha=args.git_sha or _git_sha(),
        dataset_fingerprint=phase2_security_dataset_fingerprint(dataset),
        outcomes=outcomes,
    )
    _assert_raw_free(summary)
    json_path = output_dir / f"{run_id}-prompt-injection-security-phase2.json"
    markdown_path = output_dir / f"{run_id}-prompt-injection-security-phase2.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(markdown_path, summary)
    _append_event(
        activity_path,
        {
            "event": "run_completed",
            "run_id": run_id,
            "ended_at": summary["ended_at"],
            "gate_decision": summary["gate_decision"],
            "summary_sha256": _sha256(json_path.read_text(encoding="utf-8")),
            "raw_content_persisted": False,
        },
    )
    print(json_path)
    print(markdown_path)


def _summary(
    *,
    run_id: str,
    started_at: str,
    git_sha: str,
    dataset_fingerprint: str,
    outcomes: Sequence[dict[str, object]],
) -> dict[str, object]:
    baseline_outcomes = [item for item in outcomes if item["profile"] == "observe_only"]
    candidate_outcomes = [item for item in outcomes if item["profile"] == _CANDIDATE_POLICY]
    bypass_count = sum(item["control_bypass"] is True for item in candidate_outcomes)
    clean_failure_count = sum(item["clean_utility_pass"] is not True for item in candidate_outcomes)
    detector_failure_count = sum(
        item["expected_detector_covered"] is not True for item in candidate_outcomes
    )
    baseline_bypass_rate = _rate(baseline_outcomes, "control_bypass")
    candidate_bypass_rate = _rate(candidate_outcomes, "control_bypass")
    strict_improvement = candidate_bypass_rate < baseline_bypass_rate
    passed = (
        bypass_count == clean_failure_count == detector_failure_count == 0 and strict_improvement
    )
    return {
        "schema_version": "rag.security.prompt_injection.phase2.run.v1",
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "git_sha": git_sha,
        "source_fingerprint": _source_fingerprint(),
        "evaluation_scope": PHASE2_SECURITY_EVALUATION_SCOPE,
        "dataset_id": "prompt_injection_security_dev_v2",
        "dataset_schema_version": "rag.security.prompt_injection.v2",
        "dataset_fingerprint": dataset_fingerprint,
        "case_count": len(candidate_outcomes),
        "outcome_count": len(outcomes),
        "candidate_policy": _CANDIDATE_POLICY,
        "external_model_used": False,
        "raw_content_persisted": False,
        "judge_type": "deterministic_production_boundary_contract",
        "control_bypass_count": bypass_count,
        "control_bypass_rate": candidate_bypass_rate,
        "baseline_control_bypass_rate": baseline_bypass_rate,
        "absolute_percentage_point_delta": (candidate_bypass_rate - baseline_bypass_rate) * 100.0,
        "strict_improvement": strict_improvement,
        "zero_bypass_exact_upper_95": (
            1.0 - (0.05 ** (1.0 / len(candidate_outcomes)))
            if candidate_outcomes and bypass_count == 0
            else None
        ),
        "clean_utility_rate": _rate(candidate_outcomes, "clean_utility_pass"),
        "detector_expected_coverage_rate": _rate(
            candidate_outcomes,
            "expected_detector_covered",
        ),
        "reason_code_counts": dict(
            sorted(
                Counter(
                    code
                    for item in candidate_outcomes
                    for code in cast(list[str], item["reason_codes"])
                ).items()
            )
        ),
        "profiles": {
            profile: _profile_metrics([item for item in outcomes if item["profile"] == profile])
            for profile in _PROFILES
        },
        "by_family": _group_metrics(candidate_outcomes, "family"),
        "by_boundary": _group_metrics(candidate_outcomes, "boundary"),
        "cases": list(outcomes),
        "gate_decision": (
            "phase2_control_candidate_passed" if passed else "phase2_control_gate_failed"
        ),
        "promotion_decision": (
            "security_boundary_candidate_ready_for_review_default_unchanged"
            if passed
            else "not_promoted_phase2_control_gate_failed"
        ),
        "comparability": {
            "status": "comparable_within_security_scope",
            "changed_field": "rag_injection_policy",
            "not_comparable_to": ["gold_answer_quality_v2", "local_accuracy_dev_v1"],
        },
    }


def _group_metrics(
    outcomes: Sequence[dict[str, object]],
    key: str,
) -> dict[str, dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for item in outcomes:
        groups.setdefault(str(item[key]), []).append(item)
    return {
        name: {
            "case_count": len(items),
            "control_bypass_rate": _rate(items, "control_bypass"),
            "clean_utility_rate": _rate(items, "clean_utility_pass"),
            "detector_expected_coverage_rate": _rate(items, "expected_detector_covered"),
        }
        for name, items in sorted(groups.items())
    }


def _profile_metrics(outcomes: Sequence[dict[str, object]]) -> dict[str, object]:
    return {
        "case_count": len(outcomes),
        "control_bypass_count": sum(item["control_bypass"] is True for item in outcomes),
        "control_bypass_rate": _rate(outcomes, "control_bypass"),
        "clean_utility_rate": _rate(outcomes, "clean_utility_pass"),
        "detector_expected_coverage_rate": _rate(
            outcomes,
            "expected_detector_covered",
        ),
    }


def _rate(items: Sequence[dict[str, object]], key: str) -> float:
    if not items:
        return 0.0
    return sum(item.get(key) is True for item in items) / len(items)


def _append_event(path: Path, payload: dict[str, object]) -> None:
    _assert_raw_free(payload)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _assert_raw_free(value: object, *, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _BANNED_RAW_KEYS:
                raise Phase2SecurityGateRunError(f"raw_content_key_rejected:{path}.{key}")
            _assert_raw_free(item, path=f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _assert_raw_free(item, path=f"{path}[{index}]")


def _source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "evaluation" / "security_gate_phase2.py",
        root / "evaluation" / "fixtures" / "prompt_injection_security_dev_v2.json",
        root / "rag" / "injection_detection.py",
        root / "rag" / "llm_orchestrator.py",
        root / "rag" / "model_cascade_guard.py",
        Path(__file__).resolve(),
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _write_markdown(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# RAG-31 prompt-injection Phase 2 control gate",
        "",
        f"- Run: {summary['run_id']}",
        f"- Dataset fingerprint: {summary['dataset_fingerprint']}",
        f"- Cases: {summary['case_count']}",
        (
            "- Control bypass baseline -> candidate: "
            f"{float(cast(float, summary['baseline_control_bypass_rate'])):.3%} -> "
            f"{float(cast(float, summary['control_bypass_rate'])):.3%}"
        ),
        f"- Clean utility: {float(cast(float, summary['clean_utility_rate'])):.3%}",
        (
            "- Detector coverage: "
            f"{float(cast(float, summary['detector_expected_coverage_rate'])):.3%}"
        ),
        "- External model used: false",
        "- Raw prompt, chunk, answer, canary, fact persisted: false",
        f"- Gate: {summary['gate_decision']}",
        f"- Promotion: {summary['promotion_decision']}",
        "",
        "| Policy | Cases | Bypass | Clean utility | Detector coverage |",
        "|---|---:|---:|---:|---:|",
    ]
    profile_metrics = cast(dict[str, dict[str, object]], summary["profiles"])
    for name, metrics in profile_metrics.items():
        lines.append(
            f"| {name} | {metrics['case_count']} | "
            f"{float(cast(float, metrics['control_bypass_rate'])):.3%} | "
            f"{float(cast(float, metrics['clean_utility_rate'])):.3%} | "
            f"{float(cast(float, metrics['detector_expected_coverage_rate'])):.3%} |"
        )
    lines.extend(
        [
            "",
            "Candidate boundary detail:",
            "",
            "| Boundary | Cases | Bypass | Clean utility | Detector coverage |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    boundary_metrics = cast(dict[str, dict[str, object]], summary["by_boundary"])
    for name, metrics in boundary_metrics.items():
        lines.append(
            f"| {name} | {metrics['case_count']} | "
            f"{float(cast(float, metrics['control_bypass_rate'])):.3%} | "
            f"{float(cast(float, metrics['clean_utility_rate'])):.3%} | "
            f"{float(cast(float, metrics['detector_expected_coverage_rate'])):.3%} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Phase2SecurityGateRunError("git_sha_unavailable") from exc
    return result.stdout.strip()


def _run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{_sha256(f'{timestamp}:{time.time_ns()}')[:8]}"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the synthetic-only, raw-free RAG-31 Phase2 control gate."
    )
    parser.add_argument("--git-sha", default="")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/rag31-security-gate-phase2"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
