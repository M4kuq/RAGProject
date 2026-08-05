from __future__ import annotations

import hashlib
import json
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
_EXPECTED_SOURCE_CASE_COUNT = 40
_BASELINE_PROFILE = "separate_sources"
_CANDIDATE_PROFILE = "multi_fact_evidence_group_v1"
_TARGET_CASE_IDS = frozenset(f"local_dev_answerable_{index:02d}" for index in range(13, 25))


class EvaluationOracleEvidenceGroupingError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceGroupingMetrics:
    context_grouping_profile: str
    case_count: int
    auxiliary_pass_count: int
    required_facts_supported_pass_count: int
    citation_support_pass_count: int
    mean_context_utilization: float
    generation_gap_count: int
    pipeline_failure_count: int
    failure_case_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceGroupingCaseComparison:
    case_id: str
    tags: tuple[str, ...]
    question_hash: str
    required_fact_ids: tuple[str, ...]
    oracle_source_keys: tuple[str, ...]
    baseline_auxiliary_pass: bool
    candidate_auxiliary_pass: bool | None
    baseline_required_facts_supported: str
    candidate_required_facts_supported: str | None
    baseline_citation_support: str
    candidate_citation_support: str | None
    baseline_context_utilization: float
    candidate_context_utilization: float | None
    baseline_context_hash: str
    candidate_context_hash: str
    context_hash_changed: bool
    auxiliary_transition: str
    pipeline_failure_code: str | None


@dataclass(frozen=True)
class EvidenceGroupingScreeningSummary:
    schema_version: Literal["phase3.oracle_evidence_grouping_screening.v1"]
    source_evaluation_run_id: int
    source_screening_artifact_sha256: str
    dataset_name: str
    dataset_content_fingerprint: str
    corpus_fingerprint: str
    resolved_generation_model: str
    generation_prompt_profile: str
    generation_prompt_fingerprint: str
    generation_config_fingerprint: str
    generation_temperature: float
    generation_max_context_chars: int
    generation_max_output_chars: int
    generation_max_output_tokens: int | None
    source_expected_case_count: int
    target_case_count: int
    fixed_coordinate_fingerprint: str
    baseline_context_set_fingerprint: str
    candidate_context_set_fingerprint: str
    comparability_status: Literal["comparable"]
    baseline: EvidenceGroupingMetrics
    candidate: EvidenceGroupingMetrics
    auxiliary_pass_delta: int
    required_facts_supported_pass_delta: int
    citation_support_pass_delta: int
    mean_context_utilization_delta: float
    generation_gap_delta: int
    candidate_guardrails_non_worse: bool
    candidate_improves_baseline: bool
    selected_for_three_repeat_confirmation: bool
    injection_tag_case_count: int
    security_gate_claimed: Literal[False]
    decision_reason_codes: tuple[str, ...]
    cases: tuple[EvidenceGroupingCaseComparison, ...]

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


class EvaluationOracleEvidenceGroupingService:
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
        source_screening: Mapping[str, object],
        source_screening_artifact_sha256: str,
        expected_source_case_count: int = _EXPECTED_SOURCE_CASE_COUNT,
    ) -> EvidenceGroupingScreeningSummary:
        baseline_summary, replay = _validate_source_screening(
            source_screening,
            evaluation_run_id=evaluation_run_id,
            expected_source_case_count=expected_source_case_count,
        )
        try:
            candidate = self.oracle_service_factory(
                self.settings,
                generation_prompt_profile="baseline",
                context_grouping_profile=_CANDIDATE_PROFILE,
                case_ids=_TARGET_CASE_IDS,
            ).run(
                db,
                evaluation_run_id=evaluation_run_id,
                expected_case_count=expected_source_case_count,
                r_judge_replay=replay,
            )
        except EvaluationOracleContextError as exc:
            raise EvaluationOracleEvidenceGroupingError(f"oracle_grouping_candidate_{exc}") from exc
        return compare_evidence_grouping(
            baseline_summary=baseline_summary,
            candidate=candidate,
            source_screening_artifact_sha256=source_screening_artifact_sha256,
            expected_source_case_count=expected_source_case_count,
        )


def compare_evidence_grouping(
    *,
    baseline_summary: Mapping[str, object],
    candidate: OracleContextDiagnosticSummary,
    source_screening_artifact_sha256: str,
    expected_source_case_count: int = _EXPECTED_SOURCE_CASE_COUNT,
) -> EvidenceGroupingScreeningSummary:
    if not _is_sha256(source_screening_artifact_sha256):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_source_artifact_hash_invalid")
    _validate_candidate_globals(
        baseline_summary,
        candidate,
        expected_source_case_count=expected_source_case_count,
    )
    baseline_cases = _target_baseline_cases(baseline_summary)
    candidate_cases = {case.case_id: case for case in candidate.cases}
    if set(candidate_cases) != _TARGET_CASE_IDS:
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_candidate_case_set_invalid")

    comparisons = tuple(
        _compare_case(baseline_cases[case_id], candidate_cases[case_id])
        for case_id in sorted(_TARGET_CASE_IDS)
    )
    baseline_metrics = _baseline_metrics(baseline_cases)
    candidate_metrics = _candidate_metrics(candidate.cases)
    mean_delta = round(
        candidate_metrics.mean_context_utilization - baseline_metrics.mean_context_utilization,
        6,
    )
    guardrails_non_worse = (
        candidate_metrics.pipeline_failure_count == 0
        and candidate_metrics.auxiliary_pass_count >= baseline_metrics.auxiliary_pass_count
        and candidate_metrics.required_facts_supported_pass_count
        >= baseline_metrics.required_facts_supported_pass_count
        and candidate_metrics.citation_support_pass_count
        >= baseline_metrics.citation_support_pass_count
        and mean_delta >= 0.0
    )
    improves = (
        candidate_metrics.auxiliary_pass_count > baseline_metrics.auxiliary_pass_count
        or candidate_metrics.required_facts_supported_pass_count
        > baseline_metrics.required_facts_supported_pass_count
        or candidate_metrics.citation_support_pass_count
        > baseline_metrics.citation_support_pass_count
        or mean_delta > 0.0
    )
    selected = guardrails_non_worse and improves
    reason_codes: list[str] = []
    if candidate_metrics.pipeline_failure_count:
        reason_codes.append("candidate_pipeline_failure")
    if not guardrails_non_worse:
        reason_codes.append("candidate_guardrail_regression")
    if improves:
        reason_codes.append("candidate_net_improvement")
    else:
        reason_codes.append("candidate_no_measured_improvement")
    reason_codes.append(
        "candidate_selected_for_three_repeat_confirmation"
        if selected
        else "candidate_not_selected_for_three_repeat_confirmation"
    )

    fixed_coordinate = {
        "dataset_content_fingerprint": candidate.dataset_content_fingerprint,
        "corpus_fingerprint": candidate.corpus_fingerprint,
        "generation_config_fingerprint": candidate.generation_config_fingerprint,
        "generation_prompt_fingerprint": candidate.generation_prompt_fingerprint,
        "resolved_generation_model": candidate.resolved_generation_model,
        "cases": [
            {
                "case_id": case.case_id,
                "question_hash": case.question_hash,
                "required_fact_ids": case.required_fact_ids,
                "oracle_source_keys": case.oracle_source_keys,
            }
            for case in comparisons
        ],
    }
    return EvidenceGroupingScreeningSummary(
        schema_version="phase3.oracle_evidence_grouping_screening.v1",
        source_evaluation_run_id=candidate.source_evaluation_run_id,
        source_screening_artifact_sha256=source_screening_artifact_sha256,
        dataset_name=candidate.dataset_name,
        dataset_content_fingerprint=candidate.dataset_content_fingerprint,
        corpus_fingerprint=candidate.corpus_fingerprint,
        resolved_generation_model=candidate.resolved_generation_model,
        generation_prompt_profile=candidate.generation_prompt_profile,
        generation_prompt_fingerprint=candidate.generation_prompt_fingerprint,
        generation_config_fingerprint=candidate.generation_config_fingerprint,
        generation_temperature=candidate.generation_temperature,
        generation_max_context_chars=candidate.generation_max_context_chars,
        generation_max_output_chars=candidate.generation_max_output_chars,
        generation_max_output_tokens=candidate.generation_max_output_tokens,
        source_expected_case_count=expected_source_case_count,
        target_case_count=len(comparisons),
        fixed_coordinate_fingerprint=_fingerprint(fixed_coordinate),
        baseline_context_set_fingerprint=_context_set_fingerprint(
            (case.case_id, case.baseline_context_hash) for case in comparisons
        ),
        candidate_context_set_fingerprint=_context_set_fingerprint(
            (case.case_id, case.candidate_context_hash) for case in comparisons
        ),
        comparability_status="comparable",
        baseline=baseline_metrics,
        candidate=candidate_metrics,
        auxiliary_pass_delta=(
            candidate_metrics.auxiliary_pass_count - baseline_metrics.auxiliary_pass_count
        ),
        required_facts_supported_pass_delta=(
            candidate_metrics.required_facts_supported_pass_count
            - baseline_metrics.required_facts_supported_pass_count
        ),
        citation_support_pass_delta=(
            candidate_metrics.citation_support_pass_count
            - baseline_metrics.citation_support_pass_count
        ),
        mean_context_utilization_delta=mean_delta,
        generation_gap_delta=(
            candidate_metrics.generation_gap_count - baseline_metrics.generation_gap_count
        ),
        candidate_guardrails_non_worse=guardrails_non_worse,
        candidate_improves_baseline=improves,
        selected_for_three_repeat_confirmation=selected,
        injection_tag_case_count=sum("prompt_injection" in case.tags for case in comparisons),
        security_gate_claimed=False,
        decision_reason_codes=tuple(reason_codes),
        cases=comparisons,
    )


def _validate_source_screening(
    source: Mapping[str, object],
    *,
    evaluation_run_id: int,
    expected_source_case_count: int,
) -> tuple[Mapping[str, object], dict[str, object]]:
    if (
        source.get("schema_version") != "phase3.oracle_prompt_screening.v1"
        or source.get("source_evaluation_run_id") != evaluation_run_id
        or source.get("dataset_name") != _ALLOWED_DATASET
        or source.get("resolved_generation_model") != _EXPECTED_MODEL
        or source.get("generation_temperature") != 0.0
        or source.get("expected_case_count") != expected_source_case_count
        or source.get("replay_gate_passed") is not True
        or source.get("screening_gate_passed") is not True
        or source.get("baseline_profile") != "baseline"
    ):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_source_screening_invalid")
    replay = source.get("safe_judge_replay")
    if not isinstance(replay, dict) or replay.get("gate_passed") is not True:
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_source_replay_invalid")
    profiles = source.get("profiles")
    if not isinstance(profiles, list):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_source_profiles_invalid")
    baseline_profiles = [
        item for item in profiles if isinstance(item, dict) and item.get("profile") == "baseline"
    ]
    if len(baseline_profiles) != 1:
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_baseline_profile_invalid")
    baseline = baseline_profiles[0].get("safe_oracle_summary")
    if not isinstance(baseline, dict):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_baseline_summary_invalid")
    _target_baseline_cases(baseline)
    return baseline, replay


def _validate_candidate_globals(
    baseline: Mapping[str, object],
    candidate: OracleContextDiagnosticSummary,
    *,
    expected_source_case_count: int,
) -> None:
    comparable = {
        "source_evaluation_run_id": candidate.source_evaluation_run_id,
        "dataset_name": candidate.dataset_name,
        "dataset_content_fingerprint": candidate.dataset_content_fingerprint,
        "corpus_fingerprint": candidate.corpus_fingerprint,
        "generation_config_fingerprint": candidate.generation_config_fingerprint,
        "resolved_generation_model": candidate.resolved_generation_model,
        "generation_prompt_profile": candidate.generation_prompt_profile,
        "generation_prompt_fingerprint": candidate.generation_prompt_fingerprint,
        "generation_temperature": candidate.generation_temperature,
        "generation_max_context_chars": candidate.generation_max_context_chars,
        "generation_max_output_chars": candidate.generation_max_output_chars,
        "generation_max_output_tokens": candidate.generation_max_output_tokens,
        "r_judge_replay_fingerprint": candidate.r_judge_replay_fingerprint,
    }
    if (
        candidate.dataset_name != _ALLOWED_DATASET
        or candidate.resolved_generation_model != _EXPECTED_MODEL
        or candidate.generation_prompt_profile != "baseline"
        or candidate.generation_temperature != 0.0
        or candidate.expected_case_count != len(_TARGET_CASE_IDS)
        or len(candidate.cases) != len(_TARGET_CASE_IDS)
        or expected_source_case_count != _EXPECTED_SOURCE_CASE_COUNT
        or any(baseline.get(field) != value for field, value in comparable.items())
    ):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_candidate_not_comparable")


def _target_baseline_cases(
    baseline: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    cases = baseline.get("cases")
    if not isinstance(cases, list):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_baseline_cases_invalid")
    selected = {
        item.get("case_id"): item
        for item in cases
        if isinstance(item, dict) and item.get("case_id") in _TARGET_CASE_IDS
    }
    if set(selected) != _TARGET_CASE_IDS:
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_baseline_case_set_invalid")
    for case in selected.values():
        tags = _string_tuple(case.get("tags"))
        if (
            case.get("answerable") is not True
            or case.get("required_fact_count") != 2
            or "multi_hop" not in tags
            or not _is_sha256(case.get("question_hash"))
            or not _is_sha256(case.get("o_context_hash"))
        ):
            raise EvaluationOracleEvidenceGroupingError("oracle_grouping_baseline_target_invalid")
    return cast(dict[str, Mapping[str, object]], selected)


def _compare_case(
    baseline: Mapping[str, object],
    candidate: OracleContextCaseOutcome,
) -> EvidenceGroupingCaseComparison:
    case_id = _required_string(baseline.get("case_id"))
    baseline_tags = _string_tuple(baseline.get("tags"))
    baseline_fact_ids = _string_tuple(baseline.get("required_fact_ids"))
    baseline_sources = _string_tuple(baseline.get("oracle_source_keys"))
    baseline_question_hash = _required_sha256(baseline.get("question_hash"))
    baseline_context_hash = _required_sha256(baseline.get("o_context_hash"))
    if (
        candidate.case_id != case_id
        or candidate.answerable is not True
        or candidate.tags != baseline_tags
        or candidate.required_fact_ids != baseline_fact_ids
        or candidate.oracle_source_keys != baseline_sources
        or candidate.question_hash != baseline_question_hash
        or candidate.required_fact_count != 2
        or candidate.o_context_hash == baseline_context_hash
    ):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_case_coordinate_changed")
    baseline_pass = _required_bool(baseline.get("o_auxiliary_pass"))
    candidate_pass = candidate.o_auxiliary_pass
    transition = (
        "pipeline_failure"
        if candidate_pass is None
        else f"{'pass' if baseline_pass else 'fail'}_to_{'pass' if candidate_pass else 'fail'}"
    )
    return EvidenceGroupingCaseComparison(
        case_id=case_id,
        tags=baseline_tags,
        question_hash=baseline_question_hash,
        required_fact_ids=baseline_fact_ids,
        oracle_source_keys=baseline_sources,
        baseline_auxiliary_pass=baseline_pass,
        candidate_auxiliary_pass=candidate_pass,
        baseline_required_facts_supported=_required_string(
            baseline.get("o_required_facts_supported")
        ),
        candidate_required_facts_supported=candidate.o_required_facts_supported,
        baseline_citation_support=_required_string(baseline.get("o_citation_support")),
        candidate_citation_support=candidate.o_citation_support,
        baseline_context_utilization=_required_float(baseline.get("o_context_utilization")),
        candidate_context_utilization=candidate.o_context_utilization,
        baseline_context_hash=baseline_context_hash,
        candidate_context_hash=_required_sha256(candidate.o_context_hash),
        context_hash_changed=True,
        auxiliary_transition=transition,
        pipeline_failure_code=candidate.pipeline_failure_code,
    )


def _baseline_metrics(
    cases: Mapping[str, Mapping[str, object]],
) -> EvidenceGroupingMetrics:
    values = tuple(cases[case_id] for case_id in sorted(cases))
    failures = tuple(
        _required_string(case.get("case_id"))
        for case in values
        if case.get("pipeline_failure_code") is not None
    )
    return EvidenceGroupingMetrics(
        context_grouping_profile=_BASELINE_PROFILE,
        case_count=len(values),
        auxiliary_pass_count=sum(_required_bool(case.get("o_auxiliary_pass")) for case in values),
        required_facts_supported_pass_count=sum(
            case.get("o_required_facts_supported") == "pass" for case in values
        ),
        citation_support_pass_count=sum(
            case.get("o_citation_support") == "pass" for case in values
        ),
        mean_context_utilization=_mean(
            _required_float(case.get("o_context_utilization")) for case in values
        ),
        generation_gap_count=sum(
            not _required_bool(case.get("o_auxiliary_pass")) for case in values
        ),
        pipeline_failure_count=len(failures),
        failure_case_ids=failures,
    )


def _candidate_metrics(
    cases: Sequence[OracleContextCaseOutcome],
) -> EvidenceGroupingMetrics:
    failures = tuple(case.case_id for case in cases if case.pipeline_failure_code is not None)
    return EvidenceGroupingMetrics(
        context_grouping_profile=_CANDIDATE_PROFILE,
        case_count=len(cases),
        auxiliary_pass_count=sum(case.o_auxiliary_pass is True for case in cases),
        required_facts_supported_pass_count=sum(
            case.o_required_facts_supported == "pass" for case in cases
        ),
        citation_support_pass_count=sum(case.o_citation_support == "pass" for case in cases),
        mean_context_utilization=_mean(
            case.o_context_utilization for case in cases if case.o_context_utilization is not None
        ),
        generation_gap_count=sum(case.o_auxiliary_pass is not True for case in cases),
        pipeline_failure_count=len(failures),
        failure_case_ids=failures,
    )


def _mean(values: Iterable[float]) -> float:
    materialized = tuple(values)
    if not materialized or any(not isinstance(value, (int, float)) for value in materialized):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_context_utilization_invalid")
    return round(sum(float(value) for value in materialized) / len(materialized), 6)


def _context_set_fingerprint(values: Iterable[tuple[str, str]]) -> str:
    return _fingerprint(list(values))


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_string_sequence_invalid")
    return tuple(value)


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_string_invalid")
    return value


def _required_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_boolean_invalid")
    return value


def _required_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_number_invalid")
    return float(value)


def _required_sha256(value: object) -> str:
    if not _is_sha256(value):
        raise EvaluationOracleEvidenceGroupingError("oracle_grouping_hash_invalid")
    return cast(str, value)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )
