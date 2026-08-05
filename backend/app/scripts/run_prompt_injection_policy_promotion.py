from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, cast

from app.core.config import Settings
from app.evaluation.security_gate import PromptInjectionSecurityCase, security_case_hash
from app.evaluation.security_gate_promotion import (
    PROMOTION_DATASET_ID,
    PROMOTION_DATASET_SCHEMA_VERSION,
    PROMOTION_EVALUATION_SCOPE,
    load_promotion_security_dataset,
    one_sided_exact_upper,
    promotion_gate_failures,
    screen_promotion_profiles,
)
from app.rag.generation import check_lmstudio_model_readiness, create_answer_generator
from app.rag.injection_detection import InjectionPolicyName
from app.scripts.run_prompt_injection_security_gate import (
    SecurityGateRunError,
    _aggregate_profile,
    _append_event,
    _assert_raw_free,
    _execute_case,
    _git_sha,
    _is_local_lmstudio_url,
    _run_id,
    _sha256,
    _utc_now,
)

_MODEL_ID = "qwen/qwen3.5-9b"
_MAX_OUTPUT_TOKENS = 512


def main() -> None:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    started_at = _utc_now()
    activity_path = output_dir / "activity.jsonl"
    _append_event(
        activity_path,
        {
            "event": "promotion_run_started",
            "run_id": run_id,
            "started_at": started_at,
            "evaluation_scope": PROMOTION_EVALUATION_SCOPE,
            "screening_only": args.screening_only,
            "reassessment": args.reassess_artifact is not None,
            "raw_content_persisted": False,
        },
    )
    try:
        if args.reassess_artifact is not None:
            summary = _reassess_summary(
                run_id=run_id,
                started_at=started_at,
                git_sha=args.git_sha or _git_sha(),
                source_path=args.reassess_artifact.resolve(),
            )
        elif args.screening_only:
            summary = _screening_summary(
                run_id=run_id,
                started_at=started_at,
                git_sha=args.git_sha or _git_sha(),
            )
        else:
            summary = _run_qwen(
                run_id=run_id,
                started_at=started_at,
                git_sha=args.git_sha or _git_sha(),
                activity_path=activity_path,
                repeats=args.repeats,
                local_confirmation=args.confirm_local_runtime,
            )
    except SecurityGateRunError as exc:
        _append_event(
            activity_path,
            {
                "event": "promotion_run_blocked",
                "run_id": run_id,
                "ended_at": _utc_now(),
                "reason_code": str(exc),
                "raw_content_persisted": False,
            },
        )
        raise SystemExit(2) from exc

    _assert_raw_free(summary)
    json_path = output_dir / f"{run_id}-prompt-injection-policy-promotion.json"
    markdown_path = output_dir / f"{run_id}-prompt-injection-policy-promotion.md"
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_markdown(markdown_path, summary)
    _append_event(
        activity_path,
        {
            "event": "promotion_run_completed",
            "run_id": run_id,
            "ended_at": summary["ended_at"],
            "promotion_decision": summary["promotion_decision"],
            "summary_sha256": _sha256(json_path.read_text(encoding="utf-8")),
            "raw_content_persisted": False,
        },
    )
    print(json_path)
    print(markdown_path)


def _screening_summary(*, run_id: str, started_at: str, git_sha: str) -> dict[str, object]:
    dataset = load_promotion_security_dataset()
    screening = screen_promotion_profiles(dataset)
    recommended = screening["recommended_profile"]
    return {
        "schema_version": "rag.security.prompt_injection.promotion.run.v1",
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "git_sha": git_sha,
        "evaluation_scope": PROMOTION_EVALUATION_SCOPE,
        "dataset_id": PROMOTION_DATASET_ID,
        "dataset_schema_version": PROMOTION_DATASET_SCHEMA_VERSION,
        "dataset_fingerprint": dataset.fingerprint,
        "case_set_fingerprint": _case_set_fingerprint(dataset.generation_cases),
        "source_dataset_ids": list(dataset.source_dataset_ids),
        "generation_case_count": len(dataset.generation_cases),
        "boundary_case_count": len(dataset.boundary_cases),
        "screening_only": True,
        "screening": screening,
        "profiles": [],
        "cases": [],
        "case_majorities": [],
        "repeats": 0,
        "external_content_used": False,
        "raw_content_persisted": False,
        "promotion_failure_reasons": [],
        "promotion_decision": (
            "screening_candidate_selected"
            if recommended is not None
            else "screening_no_candidate_selected"
        ),
    }


def _reassess_summary(
    *,
    run_id: str,
    started_at: str,
    git_sha: str,
    source_path: Path,
) -> dict[str, object]:
    try:
        source_text = source_path.read_text(encoding="utf-8")
        parsed_source = json.loads(source_text)
    except (OSError, ValueError, TypeError) as exc:
        raise SecurityGateRunError("promotion_reassessment_source_invalid") from exc
    if not isinstance(parsed_source, dict):
        raise SecurityGateRunError("promotion_reassessment_source_invalid")
    source = cast(dict[str, object], parsed_source)
    _assert_raw_free(source)
    if source.get("raw_content_persisted") is not False:
        raise SecurityGateRunError("promotion_reassessment_source_not_raw_free")
    expected_contract = {
        "schema_version": "rag.security.prompt_injection.promotion.run.v1",
        "screening_only": False,
        "generation_provider": "lmstudio",
        "requested_model": _MODEL_ID,
        "resolved_model": _MODEL_ID,
        "temperature": 0.0,
        "reasoning": "off",
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        "repeats": 3,
        "external_content_used": False,
    }
    if any(source.get(key) != value for key, value in expected_contract.items()):
        raise SecurityGateRunError("promotion_reassessment_contract_mismatch")
    comparability = source.get("comparability")
    if not isinstance(comparability, dict) or comparability.get("status") != "comparable":
        raise SecurityGateRunError("promotion_reassessment_comparability_invalid")

    dataset = load_promotion_security_dataset()
    if source.get("dataset_fingerprint") != dataset.fingerprint:
        raise SecurityGateRunError("promotion_reassessment_dataset_mismatch")
    case_set_fingerprint = _case_set_fingerprint(dataset.generation_cases)
    if source.get("case_set_fingerprint") != case_set_fingerprint:
        raise SecurityGateRunError("promotion_reassessment_case_set_mismatch")
    if source.get("generation_case_count") != len(dataset.generation_cases) or source.get(
        "boundary_case_count"
    ) != len(dataset.boundary_cases):
        raise SecurityGateRunError("promotion_reassessment_case_count_mismatch")

    source_screening = source.get("screening")
    profile_items = source.get("profiles")
    repeats = source.get("repeats")
    if not isinstance(source_screening, dict) or not isinstance(profile_items, list):
        raise SecurityGateRunError("promotion_reassessment_source_shape_invalid")
    screening = screen_promotion_profiles(dataset)
    if source_screening.get("dataset_fingerprint") != screening.get(
        "dataset_fingerprint"
    ) or source_screening.get("recommended_profile") != screening.get("recommended_profile"):
        raise SecurityGateRunError("promotion_reassessment_screening_mismatch")
    profiles = {
        str(item.get("profile")): item
        for item in profile_items
        if isinstance(item, dict) and isinstance(item.get("profile"), str)
    }
    baseline = profiles.get("observe_only")
    candidate = profiles.get("block_user_quarantine_context")
    if (
        baseline is None
        or candidate is None
        or len(profile_items) != 2
        or set(profiles) != {"observe_only", "block_user_quarantine_context"}
        or not isinstance(repeats, int)
    ):
        raise SecurityGateRunError("promotion_reassessment_profiles_missing")

    try:
        failures = promotion_gate_failures(
            screening=screening,
            baseline=baseline,
            candidate=candidate,
            repeats=repeats,
        )
    except (TypeError, ValueError) as exc:
        raise SecurityGateRunError("promotion_reassessment_metrics_invalid") from exc
    return {
        "schema_version": "rag.security.prompt_injection.promotion.reassessment.v1",
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "git_sha": git_sha,
        "source_run_id": source.get("run_id"),
        "source_git_sha": source.get("git_sha"),
        "source_summary_sha256": _sha256(source_text),
        "evaluation_scope": PROMOTION_EVALUATION_SCOPE,
        "dataset_id": PROMOTION_DATASET_ID,
        "dataset_schema_version": PROMOTION_DATASET_SCHEMA_VERSION,
        "dataset_fingerprint": dataset.fingerprint,
        "case_set_fingerprint": case_set_fingerprint,
        "generation_case_count": len(dataset.generation_cases),
        "boundary_case_count": len(dataset.boundary_cases),
        "screening_only": False,
        "reassessment": True,
        "screening_recommended_profile": screening["recommended_profile"],
        "profiles": [baseline, candidate],
        "repeats": repeats,
        "requested_model": source.get("requested_model"),
        "resolved_model": source.get("resolved_model"),
        "temperature": source.get("temperature"),
        "reasoning": source.get("reasoning"),
        "max_output_tokens": source.get("max_output_tokens"),
        "comparability": comparability,
        "external_content_used": False,
        "raw_content_persisted": False,
        "promotion_failure_reasons": list(failures),
        "promotion_decision": (
            "security_candidate_ready_for_production_profile_review"
            if not failures
            else "not_promoted_security_gate_failed"
        ),
    }


def _run_qwen(
    *,
    run_id: str,
    started_at: str,
    git_sha: str,
    activity_path: Path,
    repeats: int,
    local_confirmation: bool,
) -> dict[str, object]:
    if not local_confirmation:
        raise SecurityGateRunError("security_gate_local_confirmation_required")
    if repeats not in {1, 3}:
        raise SecurityGateRunError("security_gate_repeats_invalid")
    dataset = load_promotion_security_dataset()
    screening = screen_promotion_profiles(dataset)
    recommended = screening["recommended_profile"]
    if recommended is None:
        raise SecurityGateRunError("promotion_screening_candidate_missing")
    candidate = cast(InjectionPolicyName, recommended)
    settings = Settings()
    if settings.app_env.lower() not in {"local", "test"}:
        raise SecurityGateRunError("security_gate_local_environment_required")
    if settings.generation_provider != "lmstudio":
        raise SecurityGateRunError("security_gate_lmstudio_required")
    if not _is_local_lmstudio_url(settings.lmstudio_base_url):
        raise SecurityGateRunError("security_gate_non_local_provider_rejected")
    readiness = check_lmstudio_model_readiness(settings, settings.generation_model_name)
    if not readiness.ready:
        raise SecurityGateRunError(f"generation_{readiness.reason_code}")
    if readiness.resolved_model != _MODEL_ID:
        raise SecurityGateRunError("generation_model_mismatch")
    generator = create_answer_generator(
        settings,
        provider="lmstudio",
        model_name=readiness.resolved_model,
        timeout_seconds=settings.lmstudio_timeout_seconds,
        max_output_tokens=_MAX_OUTPUT_TOKENS,
    )

    profiles: tuple[InjectionPolicyName, ...] = ("observe_only", candidate)
    outcomes: list[dict[str, object]] = []
    windows: dict[str, dict[str, str]] = {}
    for profile in profiles:
        windows[profile] = {"started_at": _utc_now()}
        _append_event(
            activity_path,
            {
                "event": "promotion_profile_started",
                "run_id": run_id,
                "profile": profile,
                "repeats": repeats,
                "case_count": len(dataset.generation_cases),
            },
        )
        for repeat in range(1, repeats + 1):
            for case in dataset.generation_cases:
                for variant in ("clean", "attack"):
                    outcome = _execute_case(
                        generator=generator,
                        case=case,
                        variant=cast(Literal["clean", "attack"], variant),
                        profile=profile,
                        repeat=repeat,
                    )
                    outcomes.append(outcome)
                    _append_event(
                        activity_path,
                        {"event": "promotion_case_completed", "run_id": run_id, **outcome},
                    )
        windows[profile]["ended_at"] = _utc_now()

    profile_summaries = []
    case_majorities: list[dict[str, object]] = []
    for profile in profiles:
        profile_outcomes = [item for item in outcomes if item["profile"] == profile]
        aggregate = _aggregate_profile(
            profile=profile,
            outcomes=profile_outcomes,
            repeats=repeats,
            window=windows[profile],
        )
        majority = _majority_profile(
            profile=profile,
            outcomes=profile_outcomes,
            repeats=repeats,
        )
        aggregate.update(cast(dict[str, object], majority["summary"]))
        profile_summaries.append(aggregate)
        case_majorities.extend(cast(list[dict[str, object]], majority["cases"]))

    baseline_summary = profile_summaries[0]
    candidate_summary = profile_summaries[1]
    failure_reasons = promotion_gate_failures(
        screening=screening,
        baseline=baseline_summary,
        candidate=candidate_summary,
        repeats=repeats,
    )
    summary = {
        "schema_version": "rag.security.prompt_injection.promotion.run.v1",
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "git_sha": git_sha,
        "source_fingerprint": _source_fingerprint(),
        "evaluation_scope": PROMOTION_EVALUATION_SCOPE,
        "dataset_id": PROMOTION_DATASET_ID,
        "dataset_schema_version": PROMOTION_DATASET_SCHEMA_VERSION,
        "dataset_fingerprint": dataset.fingerprint,
        "case_set_fingerprint": _case_set_fingerprint(dataset.generation_cases),
        "source_dataset_ids": list(dataset.source_dataset_ids),
        "generation_case_count": len(dataset.generation_cases),
        "boundary_case_count": len(dataset.boundary_cases),
        "screening_only": False,
        "screening": screening,
        "profiles": profile_summaries,
        "cases": outcomes,
        "case_majorities": case_majorities,
        "repeats": repeats,
        "generation_provider": "lmstudio",
        "requested_model": settings.generation_model_name,
        "resolved_model": readiness.resolved_model,
        "temperature": 0.0,
        "reasoning": "off",
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        "external_content_used": False,
        "raw_content_persisted": False,
        "promotion_failure_reasons": list(failure_reasons),
        "promotion_decision": (
            "security_candidate_ready_for_production_profile_review"
            if not failure_reasons
            else "not_promoted_security_gate_failed"
        ),
        "comparability": {
            "status": "comparable",
            "changed_field": "rag_injection_policy",
            "fixed_fields": [
                "dataset_fingerprint",
                "case_set_fingerprint",
                "resolved_model",
                "temperature",
                "reasoning",
                "max_output_tokens",
                "repeats",
            ],
        },
    }
    _assert_raw_free(summary)
    return summary


def _majority_profile(
    *,
    profile: InjectionPolicyName,
    outcomes: Sequence[dict[str, object]],
    repeats: int,
) -> dict[str, object]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in outcomes:
        grouped[(cast(str, item["case_hash"]), cast(str, item["variant"]))].append(item)
    cases: list[dict[str, object]] = []
    threshold = repeats // 2 + 1
    for (case_hash, variant), items in sorted(grouped.items()):
        if len(items) != repeats:
            raise SecurityGateRunError("promotion_repeat_group_incomplete")
        first = items[0]
        cases.append(
            {
                "profile": profile,
                "case_hash": case_hash,
                "variant": variant,
                "language": first["language"],
                "delivery": first["delivery"],
                "channel": first["channel"],
                "obfuscated": first["obfuscated"],
                "attack_success": _majority(items, "attack_success", threshold=threshold),
                "clean_utility_pass": _majority(items, "clean_utility_pass", threshold=threshold),
                "secure_attack_utility_pass": _majority(
                    items,
                    "secure_attack_utility_pass",
                    threshold=threshold,
                ),
                "clean_policy_action": _majority(
                    items,
                    "clean_policy_action",
                    threshold=threshold,
                ),
                "poison_citation_present": _majority(
                    items,
                    "poison_citation_present",
                    threshold=threshold,
                ),
                "abstained": _majority(items, "abstained", threshold=threshold),
            }
        )
    attacks = [item for item in cases if item["variant"] == "attack"]
    clean = [item for item in cases if item["variant"] == "clean"]
    attack_success_count = sum(item["attack_success"] is True for item in attacks)
    summary = {
        "majority_attack_case_count": len(attacks),
        "majority_attack_success_count": attack_success_count,
        "majority_attack_success_rate": _rate(attack_success_count, len(attacks)),
        "attack_success_exact_upper_95": one_sided_exact_upper(
            attack_success_count,
            len(attacks),
        ),
        "majority_clean_utility_failure_count": sum(
            item["clean_utility_pass"] is not True for item in clean
        ),
        "majority_secure_attack_utility_failure_count": sum(
            item["secure_attack_utility_pass"] is not True for item in attacks
        ),
        "majority_clean_policy_action_count": sum(
            item["clean_policy_action"] is True for item in clean
        ),
        "majority_poison_citation_count": sum(
            item["poison_citation_present"] is True for item in attacks
        ),
        "majority_clean_abstention_count": sum(item["abstained"] is True for item in clean),
    }
    return {"summary": summary, "cases": cases}


def _majority(
    items: Sequence[dict[str, object]],
    key: str,
    *,
    threshold: int,
) -> bool | None:
    applicable = [item[key] for item in items if item[key] is not None]
    if not applicable:
        return None
    return sum(value is True for value in applicable) >= threshold


def _case_set_fingerprint(cases: Sequence[PromptInjectionSecurityCase]) -> str:
    case_hashes = sorted(security_case_hash(case) for case in cases)
    return _sha256("\x00".join(case_hashes))


def _source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = (
        root / "evaluation" / "security_gate.py",
        root / "evaluation" / "security_gate_phase2.py",
        root / "evaluation" / "security_gate_promotion.py",
        root / "evaluation" / "fixtures" / "prompt_injection_security_dev_v1.json",
        root / "evaluation" / "fixtures" / "prompt_injection_security_dev_v2.json",
        root / "evaluation" / "fixtures" / "prompt_injection_security_promotion_recipes_v1.json",
        root / "rag" / "injection_detection.py",
        Path(__file__).resolve(),
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _write_markdown(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "# Prompt injection policy promotion",
        "",
        f"- Run ID: `{summary['run_id']}`",
        f"- Dataset: `{summary['dataset_id']}`",
        f"- Dataset fingerprint: `{summary['dataset_fingerprint']}`",
        f"- Screening only: `{summary['screening_only']}`",
        f"- Repeats: `{summary['repeats']}`",
        f"- Promotion decision: `{summary['promotion_decision']}`",
        f"- Raw content persisted: `{summary['raw_content_persisted']}`",
    ]
    failures = cast(list[str], summary["promotion_failure_reasons"])
    if failures:
        lines.extend(("", "## Promotion failures", ""))
        lines.extend(f"- `{reason}`" for reason in failures)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the raw-free prompt injection policy promotion gate."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--screening-only", action="store_true")
    mode.add_argument("--reassess-artifact", type=Path)
    parser.add_argument("--confirm-local-runtime", action="store_true")
    parser.add_argument("--repeats", type=int, choices=(1, 3), default=3)
    parser.add_argument("--git-sha", default="")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/rag72-security-promotion"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
