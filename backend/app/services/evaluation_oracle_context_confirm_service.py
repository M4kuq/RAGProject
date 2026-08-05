from __future__ import annotations

import hashlib
import math
import random
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal, cast

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.evaluation_oracle_context_service import (
    EvaluationOracleContextError,
    EvaluationOracleContextService,
    OracleContextCaseOutcome,
    OracleContextDiagnosticSummary,
)

_ALLOWED_DATASET = "local_accuracy_dev_v1"
_EXPECTED_MODEL = "qwen/qwen3.5-9b"
_EXPECTED_REPEATS = 3
_BOOTSTRAP_ITERATIONS = 10_000


class EvaluationOracleContextConfirmError(RuntimeError):
    pass


@dataclass(frozen=True)
class OracleContextRepeatResult:
    repeat: int
    duration_seconds: float
    r_auxiliary_pass_count: int
    o_auxiliary_pass_count: int
    pipeline_failure_count: int
    gate_passed: bool


@dataclass(frozen=True)
class OracleContextStratum:
    stratum: str
    case_count: int
    r_auxiliary_pass_count: int
    o_majority_pass_count: int
    absolute_percentage_point_delta: float
    retrieval_missing_gap_count: int
    context_utilization_or_noise_gap_count: int
    unanswerable_context_rescue_count: int
    generation_or_answerability_gap_count: int


@dataclass(frozen=True)
class OracleContextConfirmCase:
    case_id: str
    answerable: bool
    tags: tuple[str, ...]
    required_fact_ids: tuple[str, ...]
    r_auxiliary_pass: bool
    o_repeat_auxiliary_passes: tuple[bool | None, ...]
    o_majority_pass: bool
    o_verdict_stable: bool
    gap_classification: Literal[
        "pass_both",
        "retrieval_missing",
        "context_utilization_or_noise",
        "unanswerable_context_rescue",
        "generation",
        "answerability",
        "oracle_regression",
    ]
    r_claim_recall: float | None
    o_mean_claim_recall: float | None
    r_context_utilization: float | None
    o_mean_context_utilization: float | None
    o_answer_hashes: tuple[str | None, ...]
    pipeline_failure_codes: tuple[str | None, ...]


@dataclass(frozen=True)
class OracleAtomicCalibrationSummary:
    calibration_status: Literal["not_supplied", "partial_hash_matched", "complete_hash_matched"]
    reviewer_type: str | None
    reviewed_observation_count: int
    hash_matched_observation_count: int
    unique_hash_matched_case_count: int
    answerable_hash_matched_observation_count: int
    semantic_supported_atomic_claim_count: int
    exact_string_supported_atomic_claim_count: int
    exact_string_false_negative_count: int
    exact_string_false_positive_count: int
    auxiliary_false_negative_count: int
    auxiliary_false_positive_count: int
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class OracleContextConfirmSummary:
    schema_version: Literal["phase3.oracle_context_confirm.v1"]
    source_evaluation_run_id: int
    dataset_name: str
    dataset_content_fingerprint: str
    corpus_fingerprint: str
    case_set_fingerprint: str
    generation_config_fingerprint: str
    r_judge_replay_fingerprint: str
    resolved_generation_model: str
    generation_prompt_profile: str
    generation_prompt_fingerprint: str
    generation_temperature: float
    generation_max_context_chars: int
    generation_max_output_chars: int
    generation_max_output_tokens: int | None
    repeats: int
    expected_case_count: int
    repeat_results: tuple[OracleContextRepeatResult, ...]
    total_duration_seconds: float
    majority_resolved_case_count: int
    stable_o_verdict_case_count: int
    unstable_o_verdict_case_count: int
    unstable_o_verdict_case_ids: tuple[str, ...]
    r_auxiliary_pass_count: int
    o_majority_pass_count: int
    r_auxiliary_pass_rate: float
    o_majority_pass_rate: float
    absolute_percentage_point_delta: float
    paired_bootstrap_iterations: int
    paired_bootstrap_confidence_interval_95: tuple[float, float]
    exact_mcnemar_p_value: float
    oracle_rescue_count: int
    oracle_regression_count: int
    retrieval_missing_gap_count: int
    context_utilization_or_noise_gap_count: int
    unanswerable_context_rescue_count: int
    answerable_generation_gap_count: int
    unanswerable_answerability_gap_count: int
    r_mean_claim_recall: float | None
    o_mean_claim_recall: float | None
    r_mean_context_utilization: float | None
    o_mean_context_utilization: float | None
    strata: tuple[OracleContextStratum, ...]
    atomic_calibration: OracleAtomicCalibrationSummary
    primary_failure_class: Literal[
        "retrieval_gap",
        "generation_answerability_gap",
        "evaluator_bias_candidate",
        "mixed_or_inconclusive",
    ]
    decision_reason_codes: tuple[str, ...]
    pipeline_failure_count: int
    gate_passed: bool
    cases: tuple[OracleContextConfirmCase, ...]

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


class EvaluationOracleContextConfirmService:
    def __init__(
        self,
        settings: Settings,
        *,
        oracle_service_factory: Callable[..., EvaluationOracleContextService] = (
            EvaluationOracleContextService
        ),
    ) -> None:
        self.settings = settings
        self.oracle_service_factory = oracle_service_factory

    def run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        expected_case_count: int,
        r_judge_replay: dict[str, object],
        repeats: int = _EXPECTED_REPEATS,
        manual_review: Mapping[str, object] | None = None,
    ) -> OracleContextConfirmSummary:
        if repeats != _EXPECTED_REPEATS:
            raise EvaluationOracleContextConfirmError("oracle_confirm_repeats_must_equal_three")
        summaries: list[OracleContextDiagnosticSummary] = []
        durations: list[float] = []
        for _repeat in range(1, repeats + 1):
            started = time.perf_counter()
            try:
                summary = self.oracle_service_factory(
                    self.settings,
                    generation_prompt_profile="baseline",
                ).run(
                    db,
                    evaluation_run_id=evaluation_run_id,
                    expected_case_count=expected_case_count,
                    r_judge_replay=r_judge_replay,
                )
            except EvaluationOracleContextError as exc:
                raise EvaluationOracleContextConfirmError(str(exc)) from exc
            durations.append(time.perf_counter() - started)
            summaries.append(summary)
        return aggregate_oracle_context_confirm(
            summaries,
            durations_seconds=durations,
            manual_review=manual_review,
        )


def aggregate_oracle_context_confirm(
    summaries: Sequence[OracleContextDiagnosticSummary],
    *,
    durations_seconds: Sequence[float] | None = None,
    manual_review: Mapping[str, object] | None = None,
) -> OracleContextConfirmSummary:
    if len(summaries) != _EXPECTED_REPEATS:
        raise EvaluationOracleContextConfirmError("oracle_confirm_repeats_must_equal_three")
    _validate_summary_contract(summaries)
    first = summaries[0]
    durations = tuple(durations_seconds or (0.0,) * _EXPECTED_REPEATS)
    if len(durations) != _EXPECTED_REPEATS or any(value < 0.0 for value in durations):
        raise EvaluationOracleContextConfirmError("oracle_confirm_duration_invalid")

    case_maps = [{case.case_id: case for case in summary.cases} for summary in summaries]
    case_ids = tuple(sorted(case_maps[0]))
    cases: list[OracleContextConfirmCase] = []
    paired_outcomes: list[tuple[bool, bool]] = []
    for case_id in case_ids:
        repeats = tuple(case_map[case_id] for case_map in case_maps)
        _validate_case_contract(repeats)
        o_passes = tuple(case.o_auxiliary_pass for case in repeats)
        o_majority = sum(value is True for value in o_passes) >= 2
        r_pass = repeats[0].r_auxiliary_pass
        paired_outcomes.append((r_pass, o_majority))
        cases.append(
            OracleContextConfirmCase(
                case_id=case_id,
                answerable=repeats[0].answerable,
                tags=repeats[0].tags,
                required_fact_ids=repeats[0].required_fact_ids,
                r_auxiliary_pass=r_pass,
                o_repeat_auxiliary_passes=o_passes,
                o_majority_pass=o_majority,
                o_verdict_stable=len(set(o_passes)) == 1,
                gap_classification=_gap_classification(repeats[0], o_majority),
                r_claim_recall=repeats[0].r_claim_recall,
                o_mean_claim_recall=_mean(case.o_claim_recall for case in repeats),
                r_context_utilization=repeats[0].r_context_utilization,
                o_mean_context_utilization=_mean(case.o_context_utilization for case in repeats),
                o_answer_hashes=tuple(case.o_answer_hash for case in repeats),
                pipeline_failure_codes=tuple(case.pipeline_failure_code for case in repeats),
            )
        )

    case_tuple = tuple(cases)
    unstable_case_ids = tuple(case.case_id for case in case_tuple if not case.o_verdict_stable)
    r_pass_count = sum(case.r_auxiliary_pass for case in case_tuple)
    o_pass_count = sum(case.o_majority_pass for case in case_tuple)
    retrieval_missing = _gap_count(case_tuple, "retrieval_missing")
    utilization_or_noise = _gap_count(case_tuple, "context_utilization_or_noise")
    unanswerable_rescues = _gap_count(case_tuple, "unanswerable_context_rescue")
    oracle_rescues = retrieval_missing + utilization_or_noise + unanswerable_rescues
    oracle_regressions = _gap_count(case_tuple, "oracle_regression")
    answerable_generation = sum(case.answerable and not case.o_majority_pass for case in case_tuple)
    unanswerable_answerability = sum(
        not case.answerable and not case.o_majority_pass for case in case_tuple
    )
    calibration = _calibrate_atomic_claims(
        case_maps=case_maps,
        manual_review=manual_review,
        expected_run_id=first.source_evaluation_run_id,
    )
    primary_failure_class, reason_codes = _primary_failure_class(
        retrieval_missing=retrieval_missing,
        utilization_or_noise=utilization_or_noise,
        answerable_generation=answerable_generation,
        unanswerable_answerability=unanswerable_answerability,
        calibration=calibration,
    )
    pipeline_failure_count = sum(
        case.pipeline_failure_code is not None for summary in summaries for case in summary.cases
    )
    repeat_results = tuple(
        OracleContextRepeatResult(
            repeat=index,
            duration_seconds=round(durations[index - 1], 3),
            r_auxiliary_pass_count=summary.r_auxiliary_pass_count,
            o_auxiliary_pass_count=summary.o_auxiliary_pass_count,
            pipeline_failure_count=summary.pipeline_failure_count,
            gate_passed=summary.gate_passed,
        )
        for index, summary in enumerate(summaries, 1)
    )
    return OracleContextConfirmSummary(
        schema_version="phase3.oracle_context_confirm.v1",
        source_evaluation_run_id=first.source_evaluation_run_id,
        dataset_name=first.dataset_name,
        dataset_content_fingerprint=first.dataset_content_fingerprint,
        corpus_fingerprint=first.corpus_fingerprint,
        case_set_fingerprint=first.case_set_fingerprint,
        generation_config_fingerprint=first.generation_config_fingerprint,
        r_judge_replay_fingerprint=first.r_judge_replay_fingerprint,
        resolved_generation_model=first.resolved_generation_model,
        generation_prompt_profile=first.generation_prompt_profile,
        generation_prompt_fingerprint=first.generation_prompt_fingerprint,
        generation_temperature=first.generation_temperature,
        generation_max_context_chars=first.generation_max_context_chars,
        generation_max_output_chars=first.generation_max_output_chars,
        generation_max_output_tokens=first.generation_max_output_tokens,
        repeats=_EXPECTED_REPEATS,
        expected_case_count=first.expected_case_count,
        repeat_results=repeat_results,
        total_duration_seconds=round(sum(durations), 3),
        majority_resolved_case_count=len(case_tuple),
        stable_o_verdict_case_count=len(case_tuple) - len(unstable_case_ids),
        unstable_o_verdict_case_count=len(unstable_case_ids),
        unstable_o_verdict_case_ids=unstable_case_ids,
        r_auxiliary_pass_count=r_pass_count,
        o_majority_pass_count=o_pass_count,
        r_auxiliary_pass_rate=_rate(r_pass_count, len(case_tuple)),
        o_majority_pass_rate=_rate(o_pass_count, len(case_tuple)),
        absolute_percentage_point_delta=round(
            100.0 * (_rate(o_pass_count, len(case_tuple)) - _rate(r_pass_count, len(case_tuple))),
            3,
        ),
        paired_bootstrap_iterations=_BOOTSTRAP_ITERATIONS,
        paired_bootstrap_confidence_interval_95=_paired_bootstrap_confidence_interval(
            paired_outcomes,
            seed=(
                f"{first.case_set_fingerprint}:{first.generation_config_fingerprint}:"
                f"{first.r_judge_replay_fingerprint}"
            ),
        ),
        exact_mcnemar_p_value=_exact_mcnemar_p_value(paired_outcomes),
        oracle_rescue_count=oracle_rescues,
        oracle_regression_count=oracle_regressions,
        retrieval_missing_gap_count=retrieval_missing,
        context_utilization_or_noise_gap_count=utilization_or_noise,
        unanswerable_context_rescue_count=unanswerable_rescues,
        answerable_generation_gap_count=answerable_generation,
        unanswerable_answerability_gap_count=unanswerable_answerability,
        r_mean_claim_recall=_mean(case.r_claim_recall for case in case_tuple),
        o_mean_claim_recall=_mean(case.o_mean_claim_recall for case in case_tuple),
        r_mean_context_utilization=_mean(case.r_context_utilization for case in case_tuple),
        o_mean_context_utilization=_mean(case.o_mean_context_utilization for case in case_tuple),
        strata=_strata(case_tuple),
        atomic_calibration=calibration,
        primary_failure_class=primary_failure_class,
        decision_reason_codes=reason_codes,
        pipeline_failure_count=pipeline_failure_count,
        gate_passed=(
            pipeline_failure_count == 0
            and len(case_tuple) == first.expected_case_count
            and all(result.gate_passed for result in repeat_results)
        ),
        cases=case_tuple,
    )


def _validate_summary_contract(summaries: Sequence[OracleContextDiagnosticSummary]) -> None:
    first = summaries[0]
    if first.dataset_name != _ALLOWED_DATASET:
        raise EvaluationOracleContextConfirmError("oracle_confirm_dataset_not_allowed")
    if first.resolved_generation_model != _EXPECTED_MODEL:
        raise EvaluationOracleContextConfirmError("oracle_confirm_model_mismatch")
    if first.generation_prompt_profile != "baseline" or first.generation_temperature != 0.0:
        raise EvaluationOracleContextConfirmError("oracle_confirm_generation_contract_mismatch")
    fields = (
        "source_evaluation_run_id",
        "dataset_name",
        "dataset_content_fingerprint",
        "corpus_fingerprint",
        "case_set_fingerprint",
        "generation_config_fingerprint",
        "r_judge_replay_fingerprint",
        "resolved_generation_model",
        "generation_prompt_profile",
        "generation_prompt_fingerprint",
        "generation_temperature",
        "generation_max_context_chars",
        "generation_max_output_chars",
        "generation_max_output_tokens",
        "expected_case_count",
        "r_auxiliary_pass_count",
    )
    for summary in summaries[1:]:
        mismatches = [field for field in fields if getattr(summary, field) != getattr(first, field)]
        if mismatches:
            raise EvaluationOracleContextConfirmError("oracle_confirm_summary_not_comparable")
    expected_case_ids = {case.case_id for case in first.cases}
    if len(expected_case_ids) != first.expected_case_count:
        raise EvaluationOracleContextConfirmError("oracle_confirm_case_set_invalid")
    for summary in summaries:
        if {case.case_id for case in summary.cases} != expected_case_ids:
            raise EvaluationOracleContextConfirmError("oracle_confirm_case_set_mismatch")


def _validate_case_contract(cases: Sequence[OracleContextCaseOutcome]) -> None:
    first = cases[0]
    fields = (
        "case_id",
        "question_hash",
        "answerable",
        "tags",
        "required_fact_ids",
        "oracle_source_keys",
        "required_fact_count",
        "r_retrieved_required_fact_count",
        "r_used_required_fact_count",
        "r_claim_recall",
        "r_context_utilization",
        "r_auxiliary_pass",
        "r_answer_hash",
        "r_context_hash",
        "o_context_hash",
    )
    for case in cases[1:]:
        if any(getattr(case, field) != getattr(first, field) for field in fields):
            raise EvaluationOracleContextConfirmError("oracle_confirm_case_not_comparable")


def _gap_classification(
    case: OracleContextCaseOutcome,
    o_majority_pass: bool,
) -> Literal[
    "pass_both",
    "retrieval_missing",
    "context_utilization_or_noise",
    "unanswerable_context_rescue",
    "generation",
    "answerability",
    "oracle_regression",
]:
    if case.r_auxiliary_pass and o_majority_pass:
        return "pass_both"
    if not case.r_auxiliary_pass and o_majority_pass:
        if not case.answerable:
            return "unanswerable_context_rescue"
        if case.answerable and case.r_claim_recall is not None and case.r_claim_recall < 1.0:
            return "retrieval_missing"
        return "context_utilization_or_noise"
    if o_majority_pass is False:
        if case.r_auxiliary_pass:
            return "oracle_regression"
        return "generation" if case.answerable else "answerability"
    raise AssertionError("unreachable")


def _strata(cases: Sequence[OracleContextConfirmCase]) -> tuple[OracleContextStratum, ...]:
    selectors: tuple[tuple[str, Callable[[OracleContextConfirmCase], bool]], ...] = (
        ("answerable", lambda case: case.answerable),
        ("unanswerable", lambda case: not case.answerable),
        ("single_hop", lambda case: "single_hop" in case.tags),
        ("multi_hop", lambda case: "multi_hop" in case.tags),
        ("language:ja", lambda case: "language:ja" in case.tags),
        ("language:en", lambda case: "language:en" in case.tags),
    )
    result: list[OracleContextStratum] = []
    for name, selector in selectors:
        selected = tuple(case for case in cases if selector(case))
        if not selected:
            raise EvaluationOracleContextConfirmError("oracle_confirm_stratum_empty")
        r_count = sum(case.r_auxiliary_pass for case in selected)
        o_count = sum(case.o_majority_pass for case in selected)
        result.append(
            OracleContextStratum(
                stratum=name,
                case_count=len(selected),
                r_auxiliary_pass_count=r_count,
                o_majority_pass_count=o_count,
                absolute_percentage_point_delta=round(
                    100.0 * (_rate(o_count, len(selected)) - _rate(r_count, len(selected))),
                    3,
                ),
                retrieval_missing_gap_count=_gap_count(selected, "retrieval_missing"),
                context_utilization_or_noise_gap_count=_gap_count(
                    selected, "context_utilization_or_noise"
                ),
                unanswerable_context_rescue_count=_gap_count(
                    selected, "unanswerable_context_rescue"
                ),
                generation_or_answerability_gap_count=sum(
                    case.gap_classification in {"generation", "answerability", "oracle_regression"}
                    for case in selected
                ),
            )
        )
    return tuple(result)


def _calibrate_atomic_claims(
    *,
    case_maps: Sequence[dict[str, OracleContextCaseOutcome]],
    manual_review: Mapping[str, object] | None,
    expected_run_id: int,
) -> OracleAtomicCalibrationSummary:
    if manual_review is None:
        return OracleAtomicCalibrationSummary(
            calibration_status="not_supplied",
            reviewer_type=None,
            reviewed_observation_count=0,
            hash_matched_observation_count=0,
            unique_hash_matched_case_count=0,
            answerable_hash_matched_observation_count=0,
            semantic_supported_atomic_claim_count=0,
            exact_string_supported_atomic_claim_count=0,
            exact_string_false_negative_count=0,
            exact_string_false_positive_count=0,
            auxiliary_false_negative_count=0,
            auxiliary_false_positive_count=0,
            reason_codes=("manual_calibration_not_supplied",),
        )
    if manual_review.get("schema_version") != "phase3.oracle_codex_assisted_review.v1":
        raise EvaluationOracleContextConfirmError("oracle_confirm_manual_review_schema_invalid")
    if manual_review.get("dataset_name") != _ALLOWED_DATASET:
        raise EvaluationOracleContextConfirmError("oracle_confirm_manual_review_dataset_mismatch")
    if manual_review.get("source_evaluation_run_id") != expected_run_id:
        raise EvaluationOracleContextConfirmError(
            "oracle_confirm_manual_review_source_run_mismatch"
        )
    decisions = manual_review.get("decisions")
    if not isinstance(decisions, list):
        raise EvaluationOracleContextConfirmError("oracle_confirm_manual_review_decisions_invalid")
    reviewer_type = manual_review.get("reviewer_type")
    if not isinstance(reviewer_type, str) or not reviewer_type:
        raise EvaluationOracleContextConfirmError("oracle_confirm_manual_reviewer_invalid")

    baseline_by_hash: dict[tuple[str, str], Mapping[str, object]] = {}
    for decision in decisions:
        if not isinstance(decision, Mapping) or decision.get("profile") != "baseline":
            continue
        case_id = decision.get("case_id")
        answer_hash = decision.get("answer_hash")
        if not isinstance(case_id, str) or not _is_sha256(answer_hash):
            raise EvaluationOracleContextConfirmError(
                "oracle_confirm_manual_review_decision_invalid"
            )
        baseline_by_hash[(case_id, cast(str, answer_hash))] = decision

    matched: list[tuple[OracleContextCaseOutcome, Mapping[str, object]]] = []
    for case_map in case_maps:
        for case in case_map.values():
            if case.o_answer_hash is None:
                continue
            decision = baseline_by_hash.get((case.case_id, case.o_answer_hash))
            if decision is not None:
                matched.append((case, decision))

    semantic_claims = 0
    exact_claims = 0
    false_negative_claims = 0
    false_positive_claims = 0
    auxiliary_false_negative = 0
    auxiliary_false_positive = 0
    answerable_observations = 0
    for case, decision in matched:
        manual_pass = decision.get("codex_review_pass")
        if not isinstance(manual_pass, bool):
            raise EvaluationOracleContextConfirmError(
                "oracle_confirm_manual_review_decision_invalid"
            )
        if case.o_auxiliary_pass is False and manual_pass:
            auxiliary_false_negative += 1
        if case.o_auxiliary_pass is True and not manual_pass:
            auxiliary_false_positive += 1
        if not case.answerable:
            continue
        answerable_observations += 1
        utilization = decision.get("manual_context_utilization")
        if not isinstance(utilization, int | float) or not 0.0 <= float(utilization) <= 1.0:
            raise EvaluationOracleContextConfirmError("oracle_confirm_manual_utilization_invalid")
        semantic_count = round(float(utilization) * case.required_fact_count)
        exact_count = case.o_used_required_fact_count or 0
        semantic_claims += semantic_count
        exact_claims += exact_count
        false_negative_claims += max(0, semantic_count - exact_count)
        false_positive_claims += max(0, exact_count - semantic_count)

    reviewed_count = len(baseline_by_hash) * len(case_maps)
    matched_count = len(matched)
    status: Literal["partial_hash_matched", "complete_hash_matched"] = (
        "complete_hash_matched"
        if reviewed_count > 0 and matched_count == reviewed_count
        else "partial_hash_matched"
    )
    reasons = ["calibration_is_codex_assisted_not_human_signoff"]
    if matched_count < reviewed_count:
        reasons.append("manual_review_answer_hash_drift")
    if false_negative_claims > 0:
        reasons.append("exact_statement_match_false_negative_observed")
    return OracleAtomicCalibrationSummary(
        calibration_status=status,
        reviewer_type=reviewer_type,
        reviewed_observation_count=reviewed_count,
        hash_matched_observation_count=matched_count,
        unique_hash_matched_case_count=len({case.case_id for case, _decision in matched}),
        answerable_hash_matched_observation_count=answerable_observations,
        semantic_supported_atomic_claim_count=semantic_claims,
        exact_string_supported_atomic_claim_count=exact_claims,
        exact_string_false_negative_count=false_negative_claims,
        exact_string_false_positive_count=false_positive_claims,
        auxiliary_false_negative_count=auxiliary_false_negative,
        auxiliary_false_positive_count=auxiliary_false_positive,
        reason_codes=tuple(reasons),
    )


def _primary_failure_class(
    *,
    retrieval_missing: int,
    utilization_or_noise: int,
    answerable_generation: int,
    unanswerable_answerability: int,
    calibration: OracleAtomicCalibrationSummary,
) -> tuple[
    Literal[
        "retrieval_gap",
        "generation_answerability_gap",
        "evaluator_bias_candidate",
        "mixed_or_inconclusive",
    ],
    tuple[str, ...],
]:
    generation_total = answerable_generation + unanswerable_answerability
    evaluator_errors = (
        calibration.auxiliary_false_negative_count + calibration.auxiliary_false_positive_count
    )
    reasons = [
        f"retrieval_missing_gap_count:{retrieval_missing}",
        f"context_utilization_or_noise_gap_count:{utilization_or_noise}",
        f"generation_answerability_gap_count:{generation_total}",
        f"calibrated_auxiliary_disagreement_count:{evaluator_errors}",
    ]
    if retrieval_missing > max(generation_total, utilization_or_noise):
        return "retrieval_gap", tuple(reasons)
    if generation_total > retrieval_missing:
        return "generation_answerability_gap", tuple(reasons)
    if evaluator_errors > 0 and generation_total == 0 and retrieval_missing == 0:
        return "evaluator_bias_candidate", tuple(reasons)
    return "mixed_or_inconclusive", tuple(reasons)


def _gap_count(cases: Sequence[OracleContextConfirmCase], classification: str) -> int:
    return sum(case.gap_classification == classification for case in cases)


def _paired_bootstrap_confidence_interval(
    pairs: Sequence[tuple[bool, bool]],
    *,
    seed: str,
    iterations: int = _BOOTSTRAP_ITERATIONS,
) -> tuple[float, float]:
    if not pairs or iterations < 1:
        raise EvaluationOracleContextConfirmError("oracle_confirm_paired_statistics_invalid")
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).digest())
    deltas: list[float] = []
    for _ in range(iterations):
        delta = 0
        for _ in range(len(pairs)):
            r_pass, o_pass = pairs[rng.randrange(len(pairs))]
            delta += int(o_pass) - int(r_pass)
        deltas.append(100.0 * delta / len(pairs))
    deltas.sort()
    return (
        round(deltas[math.floor((iterations - 1) * 0.025)], 6),
        round(deltas[math.ceil((iterations - 1) * 0.975)], 6),
    )


def _exact_mcnemar_p_value(pairs: Sequence[tuple[bool, bool]]) -> float:
    counts = Counter(pairs)
    r_only = counts[(True, False)]
    o_only = counts[(False, True)]
    discordant = r_only + o_only
    if discordant == 0:
        return 1.0
    tail = min(r_only, o_only)
    probability = sum(math.comb(discordant, value) for value in range(tail + 1)) / (2**discordant)
    return round(min(1.0, 2.0 * probability), 12)


def _mean(values: Iterable[float | None]) -> float | None:
    materialized = [value for value in values if value is not None]
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _rate(numerator: int, denominator: int) -> float:
    if denominator < 1:
        raise EvaluationOracleContextConfirmError("oracle_confirm_denominator_invalid")
    return round(numerator / denominator, 6)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )
