"""Raw-free, non-live diagnosis for the fixed RAG-88 timeout result."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Annotated, Literal, cast

from pydantic import Field, StringConstraints, model_validator

from app.services.evaluation_atomic_claim_contracts import (
    SafeId,
    Sha256,
    StrictRawFreeModel,
)

RAG88_SOURCE_CODE_COMMIT: Literal["6f526ec27a3c79da0d7642501348393aa89b198b"] = (
    "6f526ec27a3c79da0d7642501348393aa89b198b"
)
RAG88_PRELIVE_COMMIT: Literal["f222ea8538feba8872108012e37571d9aaef5ecf"] = (
    "f222ea8538feba8872108012e37571d9aaef5ecf"
)
RAG88_RESULT_SHA256: Literal["dca0865277b40f1c05401ca3afc74798c3a69b01b7e8ea161c819e3b6d2954e9"] = (
    "dca0865277b40f1c05401ca3afc74798c3a69b01b7e8ea161c819e3b6d2954e9"
)
RAG88_ATTEMPT_SHA256: Literal[
    "ffac4be6bcc021791937835f105771a955159ee47b47ff6926fb9e22555591c7"
] = "ffac4be6bcc021791937835f105771a955159ee47b47ff6926fb9e22555591c7"

_DIRECT_TIMEOUT = "rag88_review_generation_case_wall_clock_timeout"
_DERIVED_UNAVAILABLE = "rag88_candidate_pass1_unavailable"
_LOGICAL_CALLS = 144
_STANDARD_LOGICAL_CALLS = 108
_CANDIDATE_LOGICAL_CALLS = 36
_STANDARD_PHYSICAL_REQUEST_UPPER = 3
_CANDIDATE_PHYSICAL_REQUEST_UPPER = 1

Variant = Literal["single_a", "single_b", "combined_baseline", "combined_candidate"]
Confidence = Literal["high", "medium", "insufficient"]
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]

_FORBIDDEN_RAW_KEYS = frozenset(
    {
        "answer",
        "body",
        "chain_of_thought",
        "content",
        "context",
        "fact",
        "pass1_answer",
        "prompt",
        "question",
        "response",
        "revised_answer",
        "source",
        "statement",
        "text",
    }
)


class Rag89TimeoutDiagnosticError(RuntimeError):
    """Raised when the fixed raw-free evidence does not match the diagnosis contract."""


class Rag89FailureDistribution(StrictRawFreeModel):
    repeat: int = Field(ge=1, le=3)
    variant: Variant
    reason_code: SafeId
    count: int = Field(gt=0, le=36)


class Rag89LatencyDistribution(StrictRawFreeModel):
    count: int = Field(ge=0, le=144)
    minimum_ms: int = Field(ge=0)
    p50_ms: int = Field(ge=0)
    p75_ms: int = Field(ge=0)
    p90_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    maximum_ms: int = Field(ge=0)


class Rag89RepeatDistribution(StrictRawFreeModel):
    repeat: int = Field(ge=1, le=3)
    direct_timeout_count: int = Field(ge=0, le=36)
    derived_unavailable_count: int = Field(ge=0, le=12)
    physical_accounted_duration_ms: int = Field(ge=0)


class Rag89PhysicalCallTopology(StrictRawFreeModel):
    logical_call_count: Literal[144]
    standard_logical_call_count: Literal[108]
    candidate_logical_call_count: Literal[36]
    candidate_reuses_paired_baseline_pass1: Literal[True]
    candidate_pass1_duplicate_request_count: Literal[0]
    standard_first_request_count: Literal[108]
    candidate_repair_request_count: int = Field(ge=0, le=36)
    minimum_physical_request_count: int = Field(ge=108, le=360)
    maximum_physical_request_count: int = Field(ge=108, le=360)
    exact_physical_request_count_known: Literal[False]
    missing_counter_reason_code: Literal["rag89_standard_retry_counter_absent"]
    standard_physical_request_upper_per_logical_call: Literal[3]
    candidate_physical_request_upper_per_logical_call: Literal[1]
    retry_backoff_present: Literal[False]


class Rag89TimeoutScope(StrictRawFreeModel):
    parent_wall_clock_seconds: Literal[180]
    parent_scope: Literal["one_standard_logical_call_or_one_repair_logical_call"]
    parent_included_phases: tuple[SafeId, ...]
    http_timeout_kind: Literal["per_connect_read_write_pool_operation"]
    http_connect_seconds: Literal[180]
    http_read_seconds: Literal[180]
    http_write_seconds: Literal[180]
    http_pool_seconds: Literal[180]
    retry_attempts_share_parent_deadline: Literal[True]
    candidate_whole_flow_has_single_deadline: Literal[False]
    phase_at_timeout_observed: Literal[False]


class Rag89CancellationAssessment(StrictRawFreeModel):
    parent_termination_sequence: tuple[SafeId, ...]
    child_process_alive_after_cleanup: Literal[False]
    cooperative_http_cancellation_present: Literal[False]
    per_request_http_client_reused: Literal[False]
    application_pool_or_session_reused_across_calls: Literal[False]
    operating_system_socket_close_expected_on_process_exit: Literal[True]
    provider_generation_stops_on_disconnect_observed: Literal[False]
    provider_behavior_reason_code: Literal["rag89_provider_disconnect_behavior_unobserved"]


class Rag89RootCauseAssessment(StrictRawFreeModel):
    category: Literal[
        "application_timeout_cancellation_bug",
        "physical_call_amplification_design",
        "fixed_budget_measured_throughput_mismatch",
        "lmstudio_provider_behavior",
    ]
    confidence: Confidence
    established: bool
    reason_code: SafeId


class Rag89TimeoutDiagnosticReport(StrictRawFreeModel):
    schema_version: Literal["phase3.rag89_rag88_timeout_diagnostic.v1"]
    source_code_commit_sha: GitSha
    source_prelive_commit_sha: GitSha
    source_result_sha256: Sha256
    source_attempt_sha256: Sha256
    observation_count: Literal[144]
    direct_timeout_count: Literal[40]
    derived_candidate_unavailable_count: Literal[11]
    derived_pairs_immediately_follow_baseline_count: Literal[11]
    derived_pairs_copy_baseline_latency_count: Literal[11]
    pipeline_failure_count: Literal[51]
    direct_timeout_ordinals: tuple[int, ...]
    derived_unavailable_ordinals: tuple[int, ...]
    failure_distribution: tuple[Rag89FailureDistribution, ...]
    repeat_distribution: tuple[Rag89RepeatDistribution, ...]
    direct_timeout_latency: Rag89LatencyDistribution
    successful_standard_latency: Rag89LatencyDistribution
    candidate_repair_latency: Rag89LatencyDistribution
    successful_standard_at_or_above_150_seconds: int = Field(ge=0, le=108)
    successful_standard_at_or_above_170_seconds: int = Field(ge=0, le=108)
    physical_accounted_duration_ms: int = Field(ge=0)
    physical_calls: Rag89PhysicalCallTopology
    timeout_scope: Rag89TimeoutScope
    cancellation: Rag89CancellationAssessment
    root_cause_assessments: tuple[Rag89RootCauseAssessment, ...]
    unresolved_reason_codes: tuple[SafeId, ...]
    next_live_single_coordinate: Literal["timeout_contract"]
    next_live_requires_independent_fixture: Literal[True]
    next_live_requires_pregistration_and_one_shot: Literal[True]
    rag88_rerun_allowed: Literal[False]
    raw_content_persisted: Literal[False]
    chain_of_thought_persisted: Literal[False]

    @model_validator(mode="after")
    def validate_fixed_counts(self) -> Rag89TimeoutDiagnosticReport:
        if len(self.direct_timeout_ordinals) != self.direct_timeout_count:
            raise ValueError("rag89_direct_timeout_ordinal_count_drift")
        if len(self.derived_unavailable_ordinals) != self.derived_candidate_unavailable_count:
            raise ValueError("rag89_derived_ordinal_count_drift")
        return self


def build_rag89_timeout_diagnostic_report(
    result_bytes: bytes,
    attempt_bytes: bytes,
    *,
    source_code_commit_sha: str = RAG88_SOURCE_CODE_COMMIT,
    enforce_fixed_artifact_hashes: bool = True,
) -> Rag89TimeoutDiagnosticReport:
    """Project the fixed RAG-88 evidence into an aggregate-only diagnosis."""

    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    attempt_sha256 = hashlib.sha256(attempt_bytes).hexdigest()
    if enforce_fixed_artifact_hashes and result_sha256 != RAG88_RESULT_SHA256:
        raise Rag89TimeoutDiagnosticError("rag89_result_artifact_hash_drift")
    if enforce_fixed_artifact_hashes and attempt_sha256 != RAG88_ATTEMPT_SHA256:
        raise Rag89TimeoutDiagnosticError("rag89_attempt_artifact_hash_drift")
    result = _load_raw_free_object(result_bytes, reason_code="rag89_result_invalid")
    attempt = _load_raw_free_object(attempt_bytes, reason_code="rag89_attempt_invalid")
    if source_code_commit_sha != RAG88_SOURCE_CODE_COMMIT:
        raise Rag89TimeoutDiagnosticError("rag89_source_code_commit_drift")
    if result.get("prelive_commit_sha") != RAG88_PRELIVE_COMMIT:
        raise Rag89TimeoutDiagnosticError("rag89_result_prelive_commit_drift")
    if attempt.get("prelive_commit_sha") != RAG88_PRELIVE_COMMIT:
        raise Rag89TimeoutDiagnosticError("rag89_attempt_prelive_commit_drift")
    if result.get("raw_content_persisted") is not False:
        raise Rag89TimeoutDiagnosticError("rag89_result_raw_content_flag_drift")
    if result.get("chain_of_thought_persisted") is not False:
        raise Rag89TimeoutDiagnosticError("rag89_result_cot_flag_drift")
    if attempt.get("raw_content_persisted") is not False:
        raise Rag89TimeoutDiagnosticError("rag89_attempt_raw_content_flag_drift")
    if attempt.get("expected_model_call_count") != _LOGICAL_CALLS:
        raise Rag89TimeoutDiagnosticError("rag89_attempt_logical_count_drift")
    if attempt.get("repeat_replacement_or_rerun_allowed") is not False:
        raise Rag89TimeoutDiagnosticError("rag89_attempt_rerun_flag_drift")

    raw_observations = result.get("observations")
    if not isinstance(raw_observations, list) or len(raw_observations) != _LOGICAL_CALLS:
        raise Rag89TimeoutDiagnosticError("rag89_observation_count_drift")
    observations = tuple(_safe_observation(item) for item in raw_observations)
    if result.get("model_call_count") != _LOGICAL_CALLS:
        raise Rag89TimeoutDiagnosticError("rag89_result_logical_count_drift")

    standard = tuple(item for item in observations if item[2] != "combined_candidate")
    candidate = tuple(item for item in observations if item[2] == "combined_candidate")
    if len(standard) != _STANDARD_LOGICAL_CALLS or len(candidate) != _CANDIDATE_LOGICAL_CALLS:
        raise Rag89TimeoutDiagnosticError("rag89_variant_cardinality_drift")
    direct = tuple(item for item in observations if item[5] == _DIRECT_TIMEOUT)
    derived = tuple(item for item in observations if item[5] == _DERIVED_UNAVAILABLE)
    if len(direct) != 40 or len(derived) != 11:
        raise Rag89TimeoutDiagnosticError("rag89_fixed_failure_count_drift")
    if result.get("pipeline_failure_count") != 51:
        raise Rag89TimeoutDiagnosticError("rag89_pipeline_failure_count_drift")
    unexpected_reasons = {
        item[5]
        for item in observations
        if item[5] not in {None, _DIRECT_TIMEOUT, _DERIVED_UNAVAILABLE}
    }
    if unexpected_reasons:
        raise Rag89TimeoutDiagnosticError("rag89_unexpected_failure_reason")

    by_ordinal = {item[0]: item for item in observations}
    immediate_pairs = 0
    copied_latency_pairs = 0
    for item in derived:
        baseline = by_ordinal.get(item[0] - 1)
        if (
            baseline is not None
            and baseline[1] == item[1]
            and baseline[2] == "combined_baseline"
            and baseline[3] == item[3]
            and baseline[5] == _DIRECT_TIMEOUT
        ):
            immediate_pairs += 1
            if baseline[4] == item[4]:
                copied_latency_pairs += 1
    if immediate_pairs != 11 or copied_latency_pairs != 11:
        raise Rag89TimeoutDiagnosticError("rag89_candidate_derivation_drift")

    failure_counter: Counter[tuple[int, Variant, str]] = Counter(
        (item[1], item[2], cast(str, item[5])) for item in observations if item[5] is not None
    )
    failure_distribution = tuple(
        Rag89FailureDistribution(
            repeat=repeat,
            variant=variant,
            reason_code=reason,
            count=count,
        )
        for (repeat, variant, reason), count in sorted(failure_counter.items())
    )

    successful_standard = tuple(item for item in standard if item[5] is None)
    repairs = tuple(item for item in candidate if item[6] is not None)
    repeat_distribution = tuple(
        Rag89RepeatDistribution(
            repeat=repeat,
            direct_timeout_count=sum(item[1] == repeat for item in direct),
            derived_unavailable_count=sum(item[1] == repeat for item in derived),
            physical_accounted_duration_ms=(
                sum(item[4] for item in standard if item[1] == repeat)
                + sum(cast(int, item[6]) for item in repairs if item[1] == repeat)
            ),
        )
        for repeat in (1, 2, 3)
    )
    physical_accounted_duration_ms = sum(item[4] for item in standard) + sum(
        cast(int, item[6]) for item in repairs
    )
    candidate_repair_request_count = len(candidate) - len(derived)

    return Rag89TimeoutDiagnosticReport(
        schema_version="phase3.rag89_rag88_timeout_diagnostic.v1",
        source_code_commit_sha=source_code_commit_sha,
        source_prelive_commit_sha=RAG88_PRELIVE_COMMIT,
        source_result_sha256=result_sha256,
        source_attempt_sha256=attempt_sha256,
        observation_count=144,
        direct_timeout_count=40,
        derived_candidate_unavailable_count=11,
        derived_pairs_immediately_follow_baseline_count=11,
        derived_pairs_copy_baseline_latency_count=11,
        pipeline_failure_count=51,
        direct_timeout_ordinals=tuple(item[0] for item in direct),
        derived_unavailable_ordinals=tuple(item[0] for item in derived),
        failure_distribution=failure_distribution,
        repeat_distribution=repeat_distribution,
        direct_timeout_latency=_latency_distribution(item[4] for item in direct),
        successful_standard_latency=_latency_distribution(item[4] for item in successful_standard),
        candidate_repair_latency=_latency_distribution(cast(int, item[6]) for item in repairs),
        successful_standard_at_or_above_150_seconds=sum(
            item[4] >= 150_000 for item in successful_standard
        ),
        successful_standard_at_or_above_170_seconds=sum(
            item[4] >= 170_000 for item in successful_standard
        ),
        physical_accounted_duration_ms=physical_accounted_duration_ms,
        physical_calls=Rag89PhysicalCallTopology(
            logical_call_count=144,
            standard_logical_call_count=108,
            candidate_logical_call_count=36,
            candidate_reuses_paired_baseline_pass1=True,
            candidate_pass1_duplicate_request_count=0,
            standard_first_request_count=108,
            candidate_repair_request_count=candidate_repair_request_count,
            minimum_physical_request_count=108 + candidate_repair_request_count,
            maximum_physical_request_count=(
                108 * _STANDARD_PHYSICAL_REQUEST_UPPER + candidate_repair_request_count
            ),
            exact_physical_request_count_known=False,
            missing_counter_reason_code="rag89_standard_retry_counter_absent",
            standard_physical_request_upper_per_logical_call=3,
            candidate_physical_request_upper_per_logical_call=1,
            retry_backoff_present=False,
        ),
        timeout_scope=Rag89TimeoutScope(
            parent_wall_clock_seconds=180,
            parent_scope="one_standard_logical_call_or_one_repair_logical_call",
            parent_included_phases=(
                "process_spawn",
                "request_build",
                "connect_pool_write_headers",
                "provider_generation",
                "full_body_read",
                "parse_and_citation_validation",
                "retry_without_backoff",
                "pipe_delivery",
            ),
            http_timeout_kind="per_connect_read_write_pool_operation",
            http_connect_seconds=180,
            http_read_seconds=180,
            http_write_seconds=180,
            http_pool_seconds=180,
            retry_attempts_share_parent_deadline=True,
            candidate_whole_flow_has_single_deadline=False,
            phase_at_timeout_observed=False,
        ),
        cancellation=Rag89CancellationAssessment(
            parent_termination_sequence=("join_180", "terminate", "join_5", "kill_if_alive"),
            child_process_alive_after_cleanup=False,
            cooperative_http_cancellation_present=False,
            per_request_http_client_reused=False,
            application_pool_or_session_reused_across_calls=False,
            operating_system_socket_close_expected_on_process_exit=True,
            provider_generation_stops_on_disconnect_observed=False,
            provider_behavior_reason_code="rag89_provider_disconnect_behavior_unobserved",
        ),
        root_cause_assessments=(
            Rag89RootCauseAssessment(
                category="fixed_budget_measured_throughput_mismatch",
                confidence="high",
                established=True,
                reason_code="rag89_40_deadlines_and_success_tail_near_180s",
            ),
            Rag89RootCauseAssessment(
                category="physical_call_amplification_design",
                confidence="medium",
                established=True,
                reason_code="rag89_up_to_three_standard_requests_retry_counter_absent",
            ),
            Rag89RootCauseAssessment(
                category="application_timeout_cancellation_bug",
                confidence="medium",
                established=False,
                reason_code="rag89_child_is_force_terminated_and_not_reused",
            ),
            Rag89RootCauseAssessment(
                category="lmstudio_provider_behavior",
                confidence="insufficient",
                established=False,
                reason_code="rag89_provider_phase_and_disconnect_not_observed",
            ),
        ),
        unresolved_reason_codes=(
            "rag89_standard_retry_counter_absent",
            "rag89_timeout_phase_unobserved",
            "rag89_provider_disconnect_behavior_unobserved",
        ),
        next_live_single_coordinate="timeout_contract",
        next_live_requires_independent_fixture=True,
        next_live_requires_pregistration_and_one_shot=True,
        rag88_rerun_allowed=False,
        raw_content_persisted=False,
        chain_of_thought_persisted=False,
    )


def _load_raw_free_object(payload_bytes: bytes, *, reason_code: str) -> dict[str, object]:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Rag89TimeoutDiagnosticError(reason_code) from exc
    if not isinstance(payload, dict) or _contains_forbidden_raw_key(payload):
        raise Rag89TimeoutDiagnosticError(reason_code)
    return cast(dict[str, object], payload)


def _contains_forbidden_raw_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in _FORBIDDEN_RAW_KEYS:
                return True
            if _contains_forbidden_raw_key(nested):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_raw_key(item) for item in value)
    return False


def _safe_observation(
    value: object,
) -> tuple[int, int, Variant, str, int, str | None, int | None]:
    if not isinstance(value, Mapping):
        raise Rag89TimeoutDiagnosticError("rag89_observation_invalid")
    execution_ordinal = value.get("execution_ordinal")
    repeat = value.get("repeat")
    variant = value.get("variant")
    group_id = value.get("group_id")
    latency_ms = value.get("latency_ms")
    failure = value.get("pipeline_failure_reason_code")
    repair_latency_ms = value.get("repair_latency_ms")
    if not isinstance(execution_ordinal, int) or not 1 <= execution_ordinal <= 144:
        raise Rag89TimeoutDiagnosticError("rag89_observation_ordinal_invalid")
    if not isinstance(repeat, int) or repeat not in {1, 2, 3}:
        raise Rag89TimeoutDiagnosticError("rag89_observation_repeat_invalid")
    if variant not in {"single_a", "single_b", "combined_baseline", "combined_candidate"}:
        raise Rag89TimeoutDiagnosticError("rag89_observation_variant_invalid")
    if not isinstance(group_id, str) or not group_id:
        raise Rag89TimeoutDiagnosticError("rag89_observation_group_invalid")
    if not isinstance(latency_ms, int) or latency_ms < 0:
        raise Rag89TimeoutDiagnosticError("rag89_observation_latency_invalid")
    if failure is not None and not isinstance(failure, str):
        raise Rag89TimeoutDiagnosticError("rag89_observation_failure_invalid")
    if repair_latency_ms is not None and (
        not isinstance(repair_latency_ms, int) or repair_latency_ms < 0
    ):
        raise Rag89TimeoutDiagnosticError("rag89_observation_repair_latency_invalid")
    return (
        execution_ordinal,
        repeat,
        cast(Variant, variant),
        group_id,
        latency_ms,
        failure,
        repair_latency_ms,
    )


def _latency_distribution(values: Iterable[int]) -> Rag89LatencyDistribution:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return Rag89LatencyDistribution(
            count=0,
            minimum_ms=0,
            p50_ms=0,
            p75_ms=0,
            p90_ms=0,
            p95_ms=0,
            maximum_ms=0,
        )
    return Rag89LatencyDistribution(
        count=len(ordered),
        minimum_ms=ordered[0],
        p50_ms=_nearest_rank(ordered, 0.50),
        p75_ms=_nearest_rank(ordered, 0.75),
        p90_ms=_nearest_rank(ordered, 0.90),
        p95_ms=_nearest_rank(ordered, 0.95),
        maximum_ms=ordered[-1],
    )


def _nearest_rank(ordered: Sequence[int], quantile: float) -> int:
    rank = max(1, math.ceil(len(ordered) * quantile))
    return ordered[min(rank - 1, len(ordered) - 1)]
