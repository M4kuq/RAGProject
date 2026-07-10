from __future__ import annotations

import json

import pytest
from app.evaluation.gold_v2 import (
    GROUNDED_ANSWER_RUBRIC_VERSION,
    AuxiliaryJudgeDecision,
    GoldCaseV2,
    GoldDatasetV2,
    GoldV2ValidationError,
    HumanCalibrationRecord,
    HumanDisagreementCategory,
    JudgeOutcome,
    JudgeReasonCode,
    calibration_agreement,
    deterministic_audit_bucket,
    grounded_answer_pass,
    load_gold_v2_bundle,
    requires_human_review,
    validate_dataset_against_catalog,
)
from pydantic import ValidationError


def _decision_for(case: GoldCaseV2, *, confidence: float = 0.95) -> AuxiliaryJudgeDecision:
    return AuxiliaryJudgeDecision(
        case_id=case.case_id,
        rubric_version=GROUNDED_ANSWER_RUBRIC_VERSION,
        required_facts_supported=(
            JudgeOutcome.PASS if case.answerable else JudgeOutcome.NOT_APPLICABLE
        ),
        citation_support=(
            JudgeOutcome.PASS if case.required_citation else JudgeOutcome.NOT_APPLICABLE
        ),
        forbidden_claims_absent=JudgeOutcome.PASS,
        abstention_correct=(JudgeOutcome.NOT_APPLICABLE if case.answerable else JudgeOutcome.PASS),
        prompt_injection_resisted=(
            JudgeOutcome.PASS if "prompt_injection" in case.tags else JudgeOutcome.NOT_APPLICABLE
        ),
        confidence=confidence,
    )


def test_gold_v2_bundle_is_balanced_and_cross_validated() -> None:
    dataset, catalog, rubric = load_gold_v2_bundle()

    assert dataset.schema_version == "phase3.gold_dataset.v2"
    assert dataset.dataset.version == "v2"
    assert dataset.dataset.primary_metric == "grounded_answer_pass_rate"
    assert len(dataset.cases) == 50
    assert sum(case.answerable for case in dataset.cases) == 30
    assert sum(not case.answerable for case in dataset.cases) == 20
    assert sum("single_hop" in case.tags for case in dataset.cases) == 25
    assert sum("multi_hop" in case.tags for case in dataset.cases) == 25
    assert sum(case.expected_strategy == "hybrid" for case in dataset.cases) == 25
    assert sum(case.expected_strategy == "agentic_router" for case in dataset.cases) == 25
    assert sum("prompt_injection" in case.tags for case in dataset.cases) == 10
    assert sum("language:en" in case.tags for case in dataset.cases) == 25
    assert sum("language:ja" in case.tags for case in dataset.cases) == 25
    assert len(catalog.sources) == 15
    assert rubric.llm_judge_role == "auxiliary"
    assert rubric.human_review_policy.initial_human_review_rate == 1.0
    assert rubric.human_review_policy.routine_audit_rate == 0.15


def test_gold_v2_fixture_contains_no_secret_shaped_values() -> None:
    dataset, catalog, rubric = load_gold_v2_bundle()
    serialized = json.dumps(
        {
            "dataset": dataset.model_dump(mode="json"),
            "catalog": catalog.model_dump(mode="json"),
            "rubric": rubric.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()

    assert "sk-" not in serialized
    assert "bearer example" not in serialized
    assert "api_key=" not in serialized
    assert "password=" not in serialized


def test_dataset_schema_rejects_duplicates_and_secret_values() -> None:
    dataset, _, _ = load_gold_v2_bundle()
    duplicate_payload = dataset.model_dump(mode="json")
    duplicate_payload["cases"][1]["question"] = duplicate_payload["cases"][0]["question"]
    with pytest.raises(ValidationError, match="questions must be unique"):
        GoldDatasetV2.model_validate(duplicate_payload)

    unsafe_case = dataset.cases[0].model_dump(mode="json")
    unsafe_case["question"] = "Use api_key=not-a-real-value for this case."
    with pytest.raises(ValidationError, match="secret-shaped"):
        GoldCaseV2.model_validate(unsafe_case)


def test_catalog_validation_rejects_missing_sources_and_fact_mismatch() -> None:
    dataset, catalog, _ = load_gold_v2_bundle()
    missing_source_payload = dataset.model_dump(mode="json")
    missing_source_payload["cases"][0]["expected_evidence"][0]["source_key"] = "missing_source"
    missing_source_dataset = GoldDatasetV2.model_validate(missing_source_payload)
    with pytest.raises(GoldV2ValidationError, match="evidence_source_missing"):
        validate_dataset_against_catalog(missing_source_dataset, catalog)

    mismatch_payload = dataset.model_dump(mode="json")
    mismatch_payload["cases"][0]["required_facts"][0]["statement"] = (
        "A different but syntactically safe statement."
    )
    mismatch_dataset = GoldDatasetV2.model_validate(mismatch_payload)
    with pytest.raises(GoldV2ValidationError, match="required_fact_mismatch"):
        validate_dataset_against_catalog(mismatch_dataset, catalog)


def test_grounded_answer_pass_uses_hard_gates_for_answerable_and_unanswerable() -> None:
    dataset, _, _ = load_gold_v2_bundle()
    answerable = next(
        case for case in dataset.cases if case.answerable and "prompt_injection" not in case.tags
    )
    answerable_decision = _decision_for(answerable)
    assert grounded_answer_pass(answerable, answerable_decision) is True

    forbidden_failure = answerable_decision.model_copy(
        update={"forbidden_claims_absent": JudgeOutcome.FAIL}
    )
    assert grounded_answer_pass(answerable, forbidden_failure) is False

    unanswerable = next(
        case for case in dataset.cases if not case.answerable and not case.required_citation
    )
    unanswerable_decision = _decision_for(unanswerable)
    assert grounded_answer_pass(unanswerable, unanswerable_decision) is True

    failed_abstention = unanswerable_decision.model_copy(
        update={"abstention_correct": JudgeOutcome.FAIL}
    )
    assert grounded_answer_pass(unanswerable, failed_abstention) is False


def test_prompt_injection_dimension_and_safe_decision_contract() -> None:
    dataset, _, _ = load_gold_v2_bundle()
    injection_case = next(case for case in dataset.cases if "prompt_injection" in case.tags)
    decision = _decision_for(injection_case)
    assert grounded_answer_pass(injection_case, decision) is True

    failed_resistance = decision.model_copy(update={"prompt_injection_resisted": JudgeOutcome.FAIL})
    assert grounded_answer_pass(injection_case, failed_resistance) is False

    unsafe_payload = decision.model_dump(mode="json")
    unsafe_payload["raw_answer"] = "must not be accepted"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AuxiliaryJudgeDecision.model_validate(unsafe_payload)


def test_human_review_policy_is_deterministic_and_covers_required_paths() -> None:
    dataset, _, rubric = load_gold_v2_bundle()
    case = next(
        item for item in dataset.cases if item.answerable and "prompt_injection" not in item.tags
    )
    decision = _decision_for(case)
    fingerprint = "evaluation-run-candidate-001"

    assert requires_human_review(
        case,
        decision,
        rubric,
        initial_calibration=True,
        changed_from_baseline=False,
        evaluation_fingerprint=fingerprint,
    )
    assert requires_human_review(
        case,
        decision,
        rubric,
        initial_calibration=False,
        changed_from_baseline=True,
        evaluation_fingerprint=fingerprint,
    )
    low_confidence = _decision_for(case, confidence=0.79)
    assert requires_human_review(
        case,
        low_confidence,
        rubric,
        initial_calibration=False,
        changed_from_baseline=False,
        evaluation_fingerprint=fingerprint,
    )

    bucket = deterministic_audit_bucket(case.case_id, fingerprint)
    assert 0.0 <= bucket < 1.0
    assert requires_human_review(
        case,
        decision,
        rubric,
        initial_calibration=False,
        changed_from_baseline=False,
        evaluation_fingerprint=fingerprint,
    ) == (bucket < rubric.human_review_policy.routine_audit_rate)


def test_human_calibration_records_require_disagreement_categories() -> None:
    agreement = HumanCalibrationRecord(
        case_id="gold_v2_001",
        rubric_version=GROUNDED_ANSWER_RUBRIC_VERSION,
        auxiliary_pass=True,
        human_pass=True,
    )
    disagreement = HumanCalibrationRecord(
        case_id="gold_v2_002",
        rubric_version=GROUNDED_ANSWER_RUBRIC_VERSION,
        auxiliary_pass=False,
        human_pass=True,
        disagreement_category=HumanDisagreementCategory.AUXILIARY_FALSE_NEGATIVE,
        reason_codes=[JudgeReasonCode.JUDGE_UNCERTAIN],
    )

    assert calibration_agreement([agreement, disagreement]) == 0.5
    assert calibration_agreement([]) is None

    with pytest.raises(ValidationError, match="disagreement_category is required"):
        HumanCalibrationRecord(
            case_id="gold_v2_003",
            rubric_version=GROUNDED_ANSWER_RUBRIC_VERSION,
            auxiliary_pass=False,
            human_pass=True,
        )
