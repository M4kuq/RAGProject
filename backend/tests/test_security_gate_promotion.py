import json
from pathlib import Path

import pytest

from app.evaluation.security_gate_promotion import (
    load_promotion_security_dataset,
    one_sided_exact_upper,
    promotion_gate_failures,
    screen_promotion_profiles,
)
from app.scripts.run_prompt_injection_policy_promotion import (
    _case_set_fingerprint,
    _majority_profile,
    _reassess_summary,
)
from app.scripts.run_prompt_injection_security_gate import SecurityGateRunError


def test_promotion_dataset_combines_existing_and_additive_security_cases() -> None:
    dataset = load_promotion_security_dataset()

    assert len(dataset.generation_cases) == 30
    assert len(dataset.boundary_cases) == 14
    assert dataset.source_dataset_ids == (
        "prompt_injection_security_dev_v1",
        "prompt_injection_security_dev_v2",
        "prompt_injection_security_promotion_recipes_v1",
    )
    assert {case.language for case in dataset.generation_cases} == {"en", "ja", "zh", "ko", "es"}
    assert sum(case.delivery == "user_message" for case in dataset.generation_cases) >= 5
    assert sum(case.channel == "graph" for case in dataset.generation_cases) >= 6


def test_promotion_screening_requires_boundary_improvement_without_clean_actions() -> None:
    summary = screen_promotion_profiles(load_promotion_security_dataset())
    profile_items = summary["profiles"]
    assert isinstance(profile_items, list)
    profiles = {item["profile"]: item for item in profile_items}

    assert summary["recommended_profile"] == "block_user_quarantine_context"
    assert summary["selected_profiles"] == ["block_user_quarantine_context"]
    assert profiles["observe_only"]["total_control_bypass_count"] > 0
    assert profiles["quarantine_context"]["generation_control_bypass_count"] > 0
    assert profiles["block_user_quarantine_context"]["generation_control_bypass_count"] == 0
    assert profiles["block_user_quarantine_context"]["boundary_control_bypass_count"] == 0
    assert profiles["block_user_quarantine_context"]["clean_policy_action_count"] == 0
    assert profiles["block_user_quarantine_context"]["detector_gap_count"] == 0


def test_promotion_screening_summary_is_raw_free() -> None:
    dataset = load_promotion_security_dataset()
    serialized = json.dumps(screen_promotion_profiles(dataset), ensure_ascii=False, sort_keys=True)

    for case in dataset.generation_cases:
        assert case.clean_question not in serialized
        assert case.attack_question not in serialized
        assert case.safe_context.text not in serialized
        assert case.attack_canary not in serialized
        if case.attack_context is not None:
            assert case.attack_context.text not in serialized


def test_promotion_recipe_source_contains_no_generated_raw_fields() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "evaluation"
    stored_source = (root / "security_gate_promotion.py").read_text(encoding="utf-8") + (
        root / "fixtures" / "prompt_injection_security_promotion_recipes_v1.json"
    ).read_text(encoding="utf-8")
    derived = [
        case
        for case in load_promotion_security_dataset().generation_cases
        if "promotion_recipe" in case.tags
    ]

    assert len(derived) == 12
    for case in derived:
        raw_fields = [
            case.clean_question,
            case.attack_question,
            case.expected_fact,
            case.attack_canary,
            case.safe_context.text,
        ]
        if case.attack_context is not None:
            raw_fields.append(case.attack_context.text)
        assert all(raw not in stored_source for raw in raw_fields)


@pytest.mark.parametrize(
    ("successes", "trials", "minimum", "maximum"),
    [
        (0, 30, 0.09, 0.10),
        (1, 30, 0.13, 0.16),
        (30, 30, 1.0, 1.0),
    ],
)
def test_one_sided_exact_upper(
    successes: int,
    trials: int,
    minimum: float,
    maximum: float,
) -> None:
    value = one_sided_exact_upper(successes, trials)

    assert minimum <= value <= maximum


def test_promotion_gate_accepts_control_improvement_with_qwen_non_inferiority() -> None:
    screening = screen_promotion_profiles(load_promotion_security_dataset())
    baseline = _passing_profile("observe_only")
    candidate = _passing_profile("block_user_quarantine_context")
    baseline["majority_clean_utility_failure_count"] = 7
    candidate["majority_clean_utility_failure_count"] = 7
    baseline["majority_secure_attack_utility_failure_count"] = 5
    candidate["majority_secure_attack_utility_failure_count"] = 5

    assert (
        promotion_gate_failures(
            screening=screening,
            baseline=baseline,
            candidate=candidate,
            repeats=3,
        )
        == ()
    )


def test_promotion_gate_rejects_clean_utility_regression() -> None:
    screening = screen_promotion_profiles(load_promotion_security_dataset())
    baseline = _passing_profile("observe_only")
    candidate = _passing_profile("block_user_quarantine_context")
    baseline["majority_clean_utility_failure_count"] = 1
    candidate["majority_clean_utility_failure_count"] = 2

    assert "promotion_clean_utility_regressed" in promotion_gate_failures(
        screening=screening,
        baseline=baseline,
        candidate=candidate,
        repeats=3,
    )


def test_promotion_gate_rejects_secure_attack_utility_regression() -> None:
    screening = screen_promotion_profiles(load_promotion_security_dataset())
    baseline = _passing_profile("observe_only")
    candidate = _passing_profile("block_user_quarantine_context")
    baseline["majority_secure_attack_utility_failure_count"] = 1
    candidate["majority_secure_attack_utility_failure_count"] = 2

    assert "promotion_secure_attack_utility_regressed" in promotion_gate_failures(
        screening=screening,
        baseline=baseline,
        candidate=candidate,
        repeats=3,
    )


def test_reassessment_reuses_raw_free_aggregate_without_generation(tmp_path: Path) -> None:
    dataset = load_promotion_security_dataset()
    screening = screen_promotion_profiles(dataset)
    baseline = _passing_profile("observe_only")
    candidate = _passing_profile("block_user_quarantine_context")
    baseline["majority_clean_utility_failure_count"] = 7
    candidate["majority_clean_utility_failure_count"] = 7
    baseline["majority_secure_attack_utility_failure_count"] = 5
    candidate["majority_secure_attack_utility_failure_count"] = 5
    source = {
        "schema_version": "rag.security.prompt_injection.promotion.run.v1",
        "run_id": "source-run",
        "git_sha": "source-sha",
        "dataset_fingerprint": dataset.fingerprint,
        "case_set_fingerprint": _case_set_fingerprint(dataset.generation_cases),
        "generation_case_count": len(dataset.generation_cases),
        "boundary_case_count": len(dataset.boundary_cases),
        "screening_only": False,
        "screening": screening,
        "profiles": [baseline, candidate],
        "repeats": 3,
        "generation_provider": "lmstudio",
        "requested_model": "qwen/qwen3.5-9b",
        "resolved_model": "qwen/qwen3.5-9b",
        "temperature": 0.0,
        "reasoning": "off",
        "max_output_tokens": 512,
        "comparability": {"status": "comparable"},
        "external_content_used": False,
        "raw_content_persisted": False,
    }
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    result = _reassess_summary(
        run_id="reassessment-run",
        started_at="2026-08-04T00:00:00Z",
        git_sha="reassessment-sha",
        source_path=source_path,
    )

    assert result["promotion_failure_reasons"] == []
    assert result["promotion_decision"] == (
        "security_candidate_ready_for_production_profile_review"
    )
    assert result["raw_content_persisted"] is False
    assert "cases" not in result


def test_reassessment_rejects_model_contract_drift(tmp_path: Path) -> None:
    dataset = load_promotion_security_dataset()
    source = {
        "schema_version": "rag.security.prompt_injection.promotion.run.v1",
        "screening_only": False,
        "generation_provider": "lmstudio",
        "requested_model": "qwen/qwen3.5-9b",
        "resolved_model": "different-model",
        "temperature": 0.0,
        "reasoning": "off",
        "max_output_tokens": 512,
        "repeats": 3,
        "external_content_used": False,
        "raw_content_persisted": False,
        "dataset_fingerprint": dataset.fingerprint,
        "case_set_fingerprint": _case_set_fingerprint(dataset.generation_cases),
    }
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(
        SecurityGateRunError,
        match="promotion_reassessment_contract_mismatch",
    ):
        _reassess_summary(
            run_id="reassessment-run",
            started_at="2026-08-04T00:00:00Z",
            git_sha="reassessment-sha",
            source_path=source_path,
        )


def test_majority_profile_uses_case_level_three_repeat_vote() -> None:
    outcomes = []
    for repeat, success in enumerate((False, True, False), start=1):
        outcomes.append(_outcome(repeat=repeat, variant="attack", attack_success=success))
        outcomes.append(_outcome(repeat=repeat, variant="clean", attack_success=None))

    result = _majority_profile(
        profile="block_user_quarantine_context",
        outcomes=outcomes,
        repeats=3,
    )

    summary = result["summary"]
    assert isinstance(summary, dict)
    assert summary["majority_attack_success_count"] == 0
    assert summary["majority_clean_utility_failure_count"] == 0


def _passing_profile(profile: str) -> dict[str, object]:
    return {
        "profile": profile,
        "majority_attack_success_count": 0,
        "attack_success_exact_upper_95": one_sided_exact_upper(0, 30),
        "majority_clean_utility_failure_count": 0,
        "majority_secure_attack_utility_failure_count": 0,
        "majority_clean_policy_action_count": 0,
        "majority_poison_citation_count": 0,
        "majority_clean_abstention_count": 0,
        "pipeline_failure_count": 0,
        "p95_latency_ms": 100.0,
    }


def _outcome(
    *,
    repeat: int,
    variant: str,
    attack_success: bool | None,
) -> dict[str, object]:
    return {
        "profile": "block_user_quarantine_context",
        "repeat": repeat,
        "case_hash": "case-hash",
        "variant": variant,
        "language": "en",
        "delivery": "retrieved_context",
        "channel": "dense",
        "obfuscated": False,
        "attack_success": attack_success,
        "clean_utility_pass": True if variant == "clean" else False,
        "secure_attack_utility_pass": True if variant == "attack" else False,
        "clean_policy_action": False,
        "poison_citation_present": False if variant == "attack" else None,
        "abstained": False,
    }
