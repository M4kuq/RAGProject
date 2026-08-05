from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.services.evaluation_oracle_context_confirm_service import (
    EvaluationOracleContextConfirmError,
    aggregate_oracle_context_confirm,
)
from app.services.evaluation_oracle_context_service import (
    OracleContextCaseOutcome,
    OracleContextDiagnosticSummary,
)


def test_oracle_confirm_aggregates_majority_strata_gaps_and_statistics() -> None:
    summaries = _summaries()

    result = aggregate_oracle_context_confirm(
        summaries,
        durations_seconds=(10.0, 11.0, 12.0),
    )

    assert result.repeats == 3
    assert result.expected_case_count == 6
    assert result.r_auxiliary_pass_count == 2
    assert result.o_majority_pass_count == 3
    assert result.absolute_percentage_point_delta == pytest.approx(16.667)
    assert result.retrieval_missing_gap_count == 1
    assert result.context_utilization_or_noise_gap_count == 1
    assert result.unanswerable_context_rescue_count == 0
    assert result.oracle_rescue_count == 2
    assert result.oracle_regression_count == 1
    assert result.answerable_generation_gap_count == 2
    assert result.unanswerable_answerability_gap_count == 1
    assert result.stable_o_verdict_case_count == 5
    assert result.unstable_o_verdict_case_ids == ("case-2",)
    assert result.primary_failure_class == "generation_answerability_gap"
    assert result.pipeline_failure_count == 0
    assert result.gate_passed is True
    assert {stratum.stratum for stratum in result.strata} == {
        "answerable",
        "unanswerable",
        "single_hop",
        "multi_hop",
        "language:ja",
        "language:en",
    }
    assert result.atomic_calibration.calibration_status == "not_supplied"
    assert result.paired_bootstrap_iterations == 10_000
    assert 0.0 <= result.exact_mcnemar_p_value <= 1.0


def test_oracle_confirm_hash_binds_manual_review_and_classifies_string_bias() -> None:
    summaries = _summaries()
    manual_review = {
        "schema_version": "phase3.oracle_codex_assisted_review.v1",
        "dataset_name": "local_accuracy_dev_v1",
        "source_evaluation_run_id": 112,
        "reviewer_type": "codex_assisted_manual_content_review",
        "decisions": [
            {
                "profile": "baseline",
                "case_id": "case-1",
                "answer_hash": "a" * 64,
                "codex_review_pass": True,
                "manual_context_utilization": 1.0,
            },
            {
                "profile": "baseline",
                "case_id": "case-3",
                "answer_hash": "c" * 64,
                "codex_review_pass": False,
                "manual_context_utilization": 0.5,
            },
        ],
    }

    result = aggregate_oracle_context_confirm(summaries, manual_review=manual_review)
    calibration = result.atomic_calibration

    assert calibration.calibration_status == "complete_hash_matched"
    assert calibration.reviewed_observation_count == 6
    assert calibration.hash_matched_observation_count == 6
    assert calibration.unique_hash_matched_case_count == 2
    assert calibration.semantic_supported_atomic_claim_count == 6
    assert calibration.exact_string_supported_atomic_claim_count == 0
    assert calibration.exact_string_false_negative_count == 6
    assert calibration.auxiliary_false_negative_count == 0
    assert calibration.auxiliary_false_positive_count == 0
    assert "exact_statement_match_false_negative_observed" in calibration.reason_codes


def test_oracle_confirm_does_not_transfer_manual_review_after_answer_hash_drift() -> None:
    summaries = list(_summaries())
    changed_cases = list(summaries[2].cases)
    changed_cases[0] = replace(changed_cases[0], o_answer_hash="f" * 64)
    summaries[2] = replace(summaries[2], cases=tuple(changed_cases))
    review = {
        "schema_version": "phase3.oracle_codex_assisted_review.v1",
        "dataset_name": "local_accuracy_dev_v1",
        "source_evaluation_run_id": 112,
        "reviewer_type": "codex_assisted_manual_content_review",
        "decisions": [
            {
                "profile": "baseline",
                "case_id": "case-1",
                "answer_hash": "a" * 64,
                "codex_review_pass": True,
                "manual_context_utilization": 1.0,
            }
        ],
    }

    result = aggregate_oracle_context_confirm(summaries, manual_review=review)

    assert result.atomic_calibration.calibration_status == "partial_hash_matched"
    assert result.atomic_calibration.hash_matched_observation_count == 2
    assert "manual_review_answer_hash_drift" in result.atomic_calibration.reason_codes


def test_oracle_confirm_rejects_cross_repeat_config_drift() -> None:
    summaries = list(_summaries())
    summaries[1] = replace(summaries[1], generation_max_output_tokens=4096)

    with pytest.raises(
        EvaluationOracleContextConfirmError,
        match="oracle_confirm_summary_not_comparable",
    ):
        aggregate_oracle_context_confirm(summaries)


def test_oracle_confirm_output_is_raw_free() -> None:
    rendered = json.dumps(
        aggregate_oracle_context_confirm(_summaries()).safe_dict(),
        ensure_ascii=False,
        sort_keys=True,
    )

    for forbidden_key in (
        '"question"',
        '"answer_text"',
        '"context"',
        '"chunk"',
        '"expected_answer"',
        '"fact_statement"',
    ):
        assert forbidden_key not in rendered


def _summaries() -> tuple[OracleContextDiagnosticSummary, ...]:
    repeat_passes = (
        (True, True, False, True, False, False),
        (True, False, False, True, False, False),
        (True, True, False, True, False, False),
    )
    return tuple(
        _summary(repeat=repeat, o_passes=passes) for repeat, passes in enumerate(repeat_passes, 1)
    )


def _summary(
    *,
    repeat: int,
    o_passes: tuple[bool, ...],
) -> OracleContextDiagnosticSummary:
    del repeat
    cases = (
        _case(
            "case-1",
            True,
            ("answerable", "single_hop", "language:ja"),
            False,
            o_passes[0],
            0.0,
            "a",
        ),
        _case(
            "case-2",
            True,
            ("answerable", "single_hop", "language:en"),
            False,
            o_passes[1],
            1.0,
            "b",
        ),
        _case(
            "case-3",
            True,
            ("answerable", "multi_hop", "language:ja"),
            False,
            o_passes[2],
            1.0,
            "c",
            fact_count=2,
        ),
        _case(
            "case-4",
            False,
            ("unanswerable", "multi_hop", "language:en"),
            True,
            o_passes[3],
            None,
            "d",
        ),
        _case(
            "case-5",
            False,
            ("unanswerable", "single_hop", "language:ja"),
            False,
            o_passes[4],
            None,
            "e",
        ),
        _case(
            "case-6",
            True,
            ("answerable", "multi_hop", "language:en"),
            True,
            o_passes[5],
            1.0,
            "f",
            fact_count=2,
        ),
    )
    return OracleContextDiagnosticSummary(
        schema_version="phase3.oracle_context_diagnostic.v1",
        source_evaluation_run_id=112,
        dataset_name="local_accuracy_dev_v1",
        dataset_content_fingerprint="1" * 64,
        corpus_fingerprint="2" * 64,
        case_set_fingerprint="3" * 64,
        generation_config_fingerprint="4" * 64,
        r_judge_replay_fingerprint="5" * 64,
        r_judge_replay_count=3,
        resolved_generation_model="qwen/qwen3.5-9b",
        generation_prompt_profile="baseline",
        generation_prompt_fingerprint="6" * 64,
        generation_temperature=0.0,
        generation_max_context_chars=6000,
        generation_max_output_chars=12000,
        generation_max_output_tokens=8192,
        expected_case_count=6,
        comparable_case_count=6,
        comparability_status="comparable",
        r_auxiliary_pass_count=2,
        o_auxiliary_pass_count=sum(o_passes),
        r_auxiliary_pass_rate=2 / 6,
        o_auxiliary_pass_rate=sum(o_passes) / 6,
        absolute_percentage_point_delta=0.0,
        answerable_retrieval_missing_gap_count=0,
        answerable_context_utilization_or_noise_gap_count=0,
        answerable_oracle_generation_gap_count=0,
        unanswerable_r_pass_count=0,
        unanswerable_o_pass_count=1,
        r_mean_claim_recall=0.75,
        o_mean_claim_recall=1.0,
        r_mean_context_utilization=0.0,
        o_mean_context_utilization=0.0,
        primary_next_target="test",
        pipeline_failure_count=0,
        gate_passed=True,
        cases=cases,
    )


def _case(
    case_id: str,
    answerable: bool,
    tags: tuple[str, ...],
    r_pass: bool,
    o_pass: bool,
    r_claim_recall: float | None,
    hash_prefix: str,
    *,
    fact_count: int = 1,
) -> OracleContextCaseOutcome:
    required_fact_ids = tuple(f"fact-{index}" for index in range(fact_count)) if answerable else ()
    return OracleContextCaseOutcome(
        case_id=case_id,
        question_hash="9" * 64,
        answerable=answerable,
        tags=tags,
        required_fact_ids=required_fact_ids,
        oracle_source_keys=(f"source-{case_id}",),
        required_fact_count=len(required_fact_ids),
        r_retrieved_required_fact_count=(
            round((r_claim_recall or 0.0) * fact_count) if answerable else None
        ),
        r_used_required_fact_count=0 if answerable else None,
        r_claim_recall=r_claim_recall,
        r_context_utilization=0.0 if answerable and r_claim_recall else None,
        o_retrieved_required_fact_count=fact_count if answerable else None,
        o_used_required_fact_count=0 if answerable else None,
        o_claim_recall=1.0 if answerable else None,
        o_context_utilization=0.0 if answerable else None,
        r_auxiliary_pass=r_pass,
        o_auxiliary_pass=o_pass,
        r_answer_hash="8" * 64,
        r_context_hash="7" * 64,
        o_answer_hash=hash_prefix * 64,
        o_context_hash="6" * 64,
        o_answer_outcome="answered" if answerable else "abstained",
        o_judge_attempt_count=1,
        o_judge_first_failure_code=None,
        o_judge_terminal_reason_code="judge_succeeded_first_attempt",
        o_judge_recovered_after_retry=False,
        o_required_facts_supported="pass" if answerable and o_pass else "fail",
        o_citation_support="pass" if answerable and o_pass else "fail",
        o_forbidden_claims_absent="pass",
        o_abstention_correct="pass" if not answerable and o_pass else "not_applicable",
        o_prompt_injection_resisted="not_applicable",
        o_judge_confidence=0.9,
        o_judge_reason_codes=(),
        metric_reason_codes=(),
        pipeline_failure_code=None,
    )
