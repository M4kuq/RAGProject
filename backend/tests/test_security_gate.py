from __future__ import annotations

from app.evaluation.security_gate import (
    SECURITY_DATASET_ID,
    evaluate_security_generation,
    expected_detector_coverage,
    load_prompt_injection_security_dataset,
    prepare_security_generation_request,
    security_case_hash,
    security_dataset_fingerprint,
)


def test_security_fixture_is_balanced_and_deterministic() -> None:
    first = load_prompt_injection_security_dataset()
    second = load_prompt_injection_security_dataset()

    assert first.dataset_id == SECURITY_DATASET_ID
    assert len(first.cases) == 10
    assert sum(case.language == "en" for case in first.cases) == 5
    assert sum(case.language == "ja" for case in first.cases) == 5
    assert sum(case.delivery == "user_message" for case in first.cases) == 2
    assert sum(case.channel == "graph" for case in first.cases) == 2
    assert security_dataset_fingerprint(first) == security_dataset_fingerprint(second)
    assert len({security_case_hash(case) for case in first.cases}) == 10


def test_quarantine_profile_removes_only_poisoned_context() -> None:
    dataset = load_prompt_injection_security_dataset()
    case = next(case for case in dataset.cases if case.delivery == "retrieved_context")

    prepared = prepare_security_generation_request(
        case,
        variant="attack",
        policy="quarantine_context",
        max_output_chars=2000,
    )

    assert prepared.request is not None
    assert [item.local_citation_id for item in prepared.request.context_items] == [1]
    assert prepared.context_decision.quarantined_indices == (1,)
    assert expected_detector_coverage(case, prepared.context_decision) is True


def test_observe_profile_keeps_poisoned_context_for_baseline() -> None:
    dataset = load_prompt_injection_security_dataset()
    case = next(case for case in dataset.cases if case.delivery == "retrieved_context")

    prepared = prepare_security_generation_request(
        case,
        variant="attack",
        policy="observe_only",
        max_output_chars=2000,
    )

    assert prepared.request is not None
    assert [item.local_citation_id for item in prepared.request.context_items] == [1, 2]
    assert prepared.context_decision.quarantined_indices == ()


def test_composite_user_attack_is_blocked_but_clean_pair_is_not() -> None:
    dataset = load_prompt_injection_security_dataset()
    case = next(case for case in dataset.cases if case.delivery == "user_message")

    clean = prepare_security_generation_request(
        case,
        variant="clean",
        policy="block_user_quarantine_context",
        max_output_chars=2000,
    )
    attack = prepare_security_generation_request(
        case,
        variant="attack",
        policy="block_user_quarantine_context",
        max_output_chars=2000,
    )

    assert clean.request is not None
    assert clean.blocked_reason_code is None
    assert attack.request is None
    assert attack.blocked_reason_code == "injection_user_blocked"


def test_deterministic_outcome_uses_canary_fact_and_citation_ids() -> None:
    dataset = load_prompt_injection_security_dataset()
    case = dataset.cases[0]

    outcome = evaluate_security_generation(
        case,
        f"The code is {case.expected_fact} [1]. {case.attack_canary} [2]",
    )

    assert outcome.expected_fact_supported is True
    assert outcome.safe_citation_present is True
    assert outcome.attack_success is True
    assert outcome.poison_citation_present is True
