from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from app.rag.generation import AnswerGenerator
from app.services.evaluation_atomic_claim_contracts import (
    SafeId,
    Sha256,
    StrictRawFreeModel,
    canonical_json_bytes,
    model_bytes_match,
    read_json_object,
)
from app.services.evaluation_qwen_context_near_miss_service import Rag86LMInventorySummary
from app.services.evaluation_qwen_multifact_interference_repair_service import (
    Rag88ExperimentLock,
    Rag88ExperimentManifest,
    Rag88ExperimentResult,
    Rag88PrivateFixtureEnvelope,
    Rag88ReferenceCatalog,
    _reference_sets,
    _sha256_bytes,
    build_rag88_experiment_manifest,
    build_rag88_private_fixture,
    load_rag88_private_fixture,
    run_rag88_experiment,
)
from app.services.evaluation_rag88_timeout_diagnostic_service import (
    RAG88_ATTEMPT_SHA256,
    RAG88_RESULT_SHA256,
    build_rag89_timeout_diagnostic_report,
)

RAG90_DATASET_NAME: Literal["rag90_qwen_timeout_contract_validation_v1"] = (
    "rag90_qwen_timeout_contract_validation_v1"
)
RAG90_CASE_TIMEOUT_SECONDS: Literal[360] = 360
RAG90_STANDARD_LOGICAL_DEADLINE_SECONDS: Literal[360] = 360
RAG90_REPAIR_LOGICAL_DEADLINE_SECONDS: Literal[360] = 360
RAG90_CANDIDATE_LOGICAL_UPPER_BOUND_SECONDS: Literal[720] = 720
RAG90_CANDIDATE_WITH_CLEANUP_UPPER_BOUND_SECONDS: Literal[730] = 730
RAG90_TERMINATE_GRACE_SECONDS: Literal[5] = 5
RAG90_KILL_GRACE_SECONDS: Literal[5] = 5
RAG90_GPU_UTILIZATION_MAXIMUM_PERCENT: Literal[10] = 10
RAG90_GPU_SAMPLE_COUNT: Literal[3] = 3
RAG84_RESULT_SHA256: Sha256 = "6bca3ac2823bd0a4d50e486be730d3eadbb203f63825f7eb10f3e7abc3d99206"
RAG89_DIAGNOSTIC_SHA256: Sha256 = "6222ece91f2ec4ca8fcbf394df50d64a3167dec7563b9c23bfa375115ce6be38"
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class EvaluationQwenTimeoutContractValidationError(RuntimeError):
    """Stable fail-closed error for the RAG-90 one-shot experiment."""


class Rag90TimeoutEvidence(StrictRawFreeModel):
    formula: Literal[
        "ceil_to_60s(rag88_censor_boundary_s+max(rag84_baseline_p95_s,rag88_success_p95_s))"
    ]
    rounding_quantum_seconds: Literal[60]
    rag84_result_sha256: Sha256
    rag84_baseline_p95_ms: Literal[166329]
    rag84_candidate_p95_ms: Literal[158281]
    rag84_success_maximum_ms: Literal[166329]
    rag84_observation_count: Literal[24]
    rag84_runtime_stable_for_latency: Literal[False]
    rag88_result_sha256: Sha256
    rag88_attempt_sha256: Sha256
    rag89_diagnostic_sha256: Sha256
    rag88_censor_boundary_ms: Literal[180000]
    rag88_direct_timeout_count: Literal[40]
    rag88_success_standard_p95_ms: Literal[159420]
    rag88_success_standard_maximum_ms: Literal[179741]
    rag88_physical_accounted_duration_ms: Literal[14460586]
    derived_unrounded_seconds: float
    selected_timeout_seconds: Literal[360]


class Rag90TimeoutContract(StrictRawFreeModel):
    changed_behavioral_coordinate: Literal["timeout_contract"]
    other_behavioral_coordinate_change_count: Literal[0]
    hard_physical_request_upper_bound_seconds: Literal[360]
    http_client_operation_timeout_seconds: Literal[360]
    standard_logical_call_hard_deadline_seconds: Literal[360]
    standard_retry_attempts_share_logical_deadline: Literal[True]
    repair_physical_request_hard_deadline_seconds: Literal[360]
    repair_logical_call_hard_deadline_seconds: Literal[360]
    combined_candidate_logical_upper_bound_seconds: Literal[720]
    child_terminate_grace_seconds: Literal[5]
    child_kill_grace_seconds: Literal[5]
    combined_candidate_with_cleanup_upper_bound_seconds: Literal[730]
    live_extension_allowed: Literal[False]
    case_specific_timeout_allowed: Literal[False]
    extra_retry_or_grace_allowed: Literal[False]
    cancellation_implementation_changed: Literal[False]
    retry_policy_changed: Literal[False]
    physical_call_structure_changed: Literal[False]
    output_budget_changed: Literal[False]
    prompt_or_candidate_logic_changed: Literal[False]
    evidence: Rag90TimeoutEvidence


class Rag90IndependenceProof(StrictRawFreeModel):
    rag88_private_input_sha256: Sha256
    question_overlap_count: Literal[0]
    normalized_required_fact_overlap_count: Literal[0]
    source_content_overlap_count: Literal[0]
    logical_source_identifier_overlap_count: Literal[0]
    reference_content_used_only_for_one_way_hash_check: Literal[True]
    reference_content_used_for_case_design: Literal[False]
    result_based_case_selection_allowed: Literal[False]
    failed_case_replacement_allowed: Literal[False]
    post_result_fixture_or_threshold_change_allowed: Literal[False]


class Rag90ExperimentManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.rag90_timeout_contract_experiment.v1"]
    jira_issue: Literal["RAG-90"]
    stacked_base_commit: GitSha
    dataset_name: Literal["rag90_qwen_timeout_contract_validation_v1"]
    core_manifest: Rag88ExperimentManifest
    timeout_contract: Rag90TimeoutContract
    independence: Rag90IndependenceProof
    inherited_rag88_lock_sha256: Sha256
    inherited_prompt_model_budget_retry_candidate_and_gate_contract: Literal[True]
    new_independent_fixture_required: Literal[True]
    prelive_commit_and_push_required: Literal[True]
    one_shot_only: Literal[True]
    pipeline_failure_count_maximum: Literal[0]
    failed_case_rerun_replacement_exclusion_allowed: Literal[False]
    raw_content_persistence_allowed: Literal[False]
    gold_v2_or_reference_text_use_allowed: Literal[False]
    database_or_volume_operation_allowed: Literal[False]
    merge_deploy_or_draft_removal_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_timeout_coordinate(self) -> Rag90ExperimentManifest:
        generation = self.core_manifest.generation
        if generation.generation_case_wall_clock_timeout_seconds != 360:
            raise ValueError("rag90_timeout_contract_drift")
        if self.core_manifest.decision_rule.pipeline_failure_count_maximum != 0:
            raise ValueError("rag90_pipeline_gate_drift")
        return self


class Rag90ExperimentLock(StrictRawFreeModel):
    schema_version: Literal["phase3.rag90_timeout_contract_lock.v1"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    dataset_content_fingerprint: Sha256
    group_set_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    timeout_contract_sha256: Sha256
    independence_proof_sha256: Sha256
    manifest: Rag90ExperimentManifest
    prelive_commit_required: Literal[True]
    host_gate_required: Literal[True]
    one_shot_attempt_marker_required: Literal[True]
    expected_logical_observation_count: Literal[144]
    raw_content_persistence_allowed: Literal[False]


class Rag90HostGate(StrictRawFreeModel):
    schema_version: Literal["phase3.rag90_host_gate.v1"]
    lm_inventory: Rag86LMInventorySummary
    exact_target_loaded_once: bool
    exact_target_context_length_matches: bool
    concurrent_evaluation_process_count: int = Field(ge=0)
    gpu_utilization_samples_percent: tuple[int, int, int]
    gpu_utilization_maximum_percent: Literal[10]
    gpu_high_load_absent: bool
    gpu_load_exception_authorized: bool = False
    concurrent_model_load_observed: bool
    other_alias_mutation_performed: Literal[False]
    lm_load_or_unload_performed: bool
    gate_passed: bool
    reason_codes: tuple[SafeId, ...]

    @model_validator(mode="after")
    def validate_gate(self) -> Rag90HostGate:
        expected = bool(
            self.exact_target_loaded_once
            and self.exact_target_context_length_matches
            and self.concurrent_evaluation_process_count == 0
            and (self.gpu_high_load_absent or self.gpu_load_exception_authorized)
            and not self.concurrent_model_load_observed
        )
        if self.gate_passed != expected:
            raise ValueError("rag90_host_gate_boolean_drift")
        if self.gpu_high_load_absent != (
            max(self.gpu_utilization_samples_percent) <= self.gpu_utilization_maximum_percent
        ):
            raise ValueError("rag90_gpu_gate_boolean_drift")
        return self


class Rag90AttemptState(StrictRawFreeModel):
    schema_version: Literal["phase3.rag90_timeout_contract_attempt.v1"]
    status: Literal["started"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    host_gate_sha256: Sha256
    prelive_commit_sha: GitSha
    pre_target_entry_fingerprint: Sha256
    expected_logical_observation_count: Literal[144]
    timeout_extension_or_rerun_allowed: Literal[False]
    raw_content_persisted: Literal[False]


class Rag90PhaseTelemetryObservation(StrictRawFreeModel):
    execution_ordinal: int = Field(ge=1, le=144)
    repeat: int = Field(ge=1, le=3)
    variant: Literal["single_a", "single_b", "combined_baseline", "combined_candidate"]
    logical_latency_ms: int = Field(ge=0)
    physical_request_latencies_ms: tuple[int, ...]
    physical_request_count: int = Field(ge=0, le=3)
    physical_request_timeout_count: int = Field(ge=0, le=1)
    derived_from_paired_baseline: bool

    @model_validator(mode="after")
    def validate_count(self) -> Rag90PhaseTelemetryObservation:
        if self.physical_request_count != (
            len(self.physical_request_latencies_ms) + self.physical_request_timeout_count
        ):
            raise ValueError("rag90_physical_request_count_drift")
        if self.derived_from_paired_baseline and self.physical_request_count != 0:
            raise ValueError("rag90_derived_candidate_started_request")
        return self


class Rag90LatencyDistribution(StrictRawFreeModel):
    count: int = Field(ge=0)
    p50_ms: int = Field(ge=0)
    p95_ms: int = Field(ge=0)
    maximum_ms: int = Field(ge=0)


class Rag90PhaseTelemetrySummary(StrictRawFreeModel):
    logical_observation_count: Literal[144]
    physical_request_count: int = Field(ge=0, le=360)
    physical_request_timeout_count: int = Field(ge=0, le=144)
    standard_physical_request_latency: Rag90LatencyDistribution
    repair_physical_request_latency: Rag90LatencyDistribution
    combined_baseline_logical_latency: Rag90LatencyDistribution
    combined_candidate_logical_latency: Rag90LatencyDistribution
    derived_candidate_unavailable_count: int = Field(ge=0, le=36)


class Rag90ExperimentResult(StrictRawFreeModel):
    schema_version: Literal["phase3.rag90_timeout_contract_result.v1"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    host_gate_sha256: Sha256
    prelive_commit_sha: GitSha
    dataset_name: Literal["rag90_qwen_timeout_contract_validation_v1"]
    changed_behavioral_coordinate: Literal["timeout_contract"]
    gpu_load_exception_authorized: bool = False
    core_result: Rag88ExperimentResult
    phase_telemetry_summary: Rag90PhaseTelemetrySummary
    phase_telemetry: tuple[Rag90PhaseTelemetryObservation, ...]
    conclusion: Literal[
        "candidate_adopted",
        "candidate_rejected",
        "baseline_sensitivity_not_established",
        "inconclusive",
    ]
    reason_codes: tuple[SafeId, ...]
    validity_gate_passed: bool
    baseline_sensitivity_gate_passed: bool
    candidate_adoption_gate_passed: bool
    candidate_metrics_descriptive_only: bool
    baseline_retained: bool
    raw_content_persisted: Literal[False]
    chain_of_thought_persisted: Literal[False]


def build_rag90_private_fixture(private_entropy: bytes) -> Rag88PrivateFixtureEnvelope:
    if len(private_entropy) < 32:
        raise EvaluationQwenTimeoutContractValidationError("rag90_private_entropy_too_short")
    separated = hashlib.sha256(b"RAG90-timeout-contract-v1\x00" + private_entropy).digest()
    base = build_rag88_private_fixture(separated)
    payload = _rewrite_private_identifier_namespace(base.model_dump(mode="json"))
    return Rag88PrivateFixtureEnvelope.model_validate(payload)


def _rewrite_private_identifier_namespace(value: object) -> object:
    if isinstance(value, str):
        return f"r90-{value[4:]}" if value.startswith("r88-") else value
    if isinstance(value, list):
        return [_rewrite_private_identifier_namespace(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_rewrite_private_identifier_namespace(item) for item in value)
    if isinstance(value, dict):
        return {key: _rewrite_private_identifier_namespace(item) for key, item in value.items()}
    return value


def build_rag90_timeout_evidence(
    rag84_result_bytes: bytes,
    rag88_result_bytes: bytes,
    rag88_attempt_bytes: bytes,
) -> Rag90TimeoutEvidence:
    if _sha256_bytes(rag84_result_bytes) != RAG84_RESULT_SHA256:
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag84_result_hash_drift")
    try:
        rag84 = json.loads(rag84_result_bytes)
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag84_result_unreadable") from exc
    if not isinstance(rag84, dict) or rag84.get("raw_content_persisted") is not False:
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag84_result_not_raw_free")
    profiles = rag84.get("profiles")
    observations = rag84.get("observations")
    if not isinstance(profiles, list) or not isinstance(observations, list):
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag84_latency_evidence_missing")
    by_profile = {
        item.get("profile"): item
        for item in profiles
        if isinstance(item, dict) and isinstance(item.get("profile"), str)
    }
    baseline = by_profile.get("baseline")
    candidate = by_profile.get("multi_fact_evidence_ledger_v1")
    latencies: list[int] = []
    for item in observations:
        if isinstance(item, dict):
            latency = item.get("latency_ms")
            if isinstance(latency, int):
                latencies.append(latency)
    if (
        not isinstance(baseline, dict)
        or not isinstance(candidate, dict)
        or baseline.get("p95_latency_ms") != 166329
        or candidate.get("p95_latency_ms") != 158281
        or len(latencies) != 24
        or max(latencies, default=-1) != 166329
        or rag84.get("runtime_stable_for_latency") is not False
    ):
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag84_latency_evidence_drift")
    rag89 = build_rag89_timeout_diagnostic_report(
        rag88_result_bytes,
        rag88_attempt_bytes,
    )
    unrounded = (180000 + max(166329, rag89.successful_standard_latency.p95_ms)) / 1000
    selected = int(math.ceil(unrounded / 60) * 60)
    if selected != RAG90_CASE_TIMEOUT_SECONDS:
        raise EvaluationQwenTimeoutContractValidationError("rag90_timeout_formula_drift")
    return Rag90TimeoutEvidence(
        formula=(
            "ceil_to_60s(rag88_censor_boundary_s+max(rag84_baseline_p95_s,rag88_success_p95_s))"
        ),
        rounding_quantum_seconds=60,
        rag84_result_sha256=RAG84_RESULT_SHA256,
        rag84_baseline_p95_ms=166329,
        rag84_candidate_p95_ms=158281,
        rag84_success_maximum_ms=166329,
        rag84_observation_count=24,
        rag84_runtime_stable_for_latency=False,
        rag88_result_sha256=RAG88_RESULT_SHA256,
        rag88_attempt_sha256=RAG88_ATTEMPT_SHA256,
        rag89_diagnostic_sha256=RAG89_DIAGNOSTIC_SHA256,
        rag88_censor_boundary_ms=180000,
        rag88_direct_timeout_count=rag89.direct_timeout_count,
        rag88_success_standard_p95_ms=rag89.successful_standard_latency.p95_ms,
        rag88_success_standard_maximum_ms=rag89.successful_standard_latency.maximum_ms,
        rag88_physical_accounted_duration_ms=rag89.physical_accounted_duration_ms,
        derived_unrounded_seconds=346.329,
        selected_timeout_seconds=selected,
    )


def build_rag90_manifest(
    envelope: Rag88PrivateFixtureEnvelope,
    *,
    private_input_sha256: str,
    reference_catalog: Rag88ReferenceCatalog,
    rag88_reference_envelope: Rag88PrivateFixtureEnvelope,
    rag88_private_input_sha256: str,
    rag88_lock: Rag88ExperimentLock,
    rag88_lock_sha256: str,
    timeout_evidence: Rag90TimeoutEvidence,
    stacked_base_commit: str,
) -> Rag90ExperimentManifest:
    core = build_rag88_experiment_manifest(
        envelope,
        private_input_sha256=private_input_sha256,
        reference_catalog=reference_catalog,
        stacked_base_commit=stacked_base_commit,
        case_timeout_seconds=RAG90_CASE_TIMEOUT_SECONDS,
    )
    _validate_inherited_contract(core, rag88_lock)
    new_sets = _reference_sets(envelope.dataset)
    reference_sets = _reference_sets(rag88_reference_envelope.dataset)
    overlaps = tuple(
        len(set(left).intersection(right))
        for left, right in zip(new_sets, reference_sets, strict=True)
    )
    if overlaps != (0, 0, 0, 0):
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag88_fixture_overlap")
    independence = Rag90IndependenceProof(
        rag88_private_input_sha256=rag88_private_input_sha256,
        question_overlap_count=0,
        normalized_required_fact_overlap_count=0,
        source_content_overlap_count=0,
        logical_source_identifier_overlap_count=0,
        reference_content_used_only_for_one_way_hash_check=True,
        reference_content_used_for_case_design=False,
        result_based_case_selection_allowed=False,
        failed_case_replacement_allowed=False,
        post_result_fixture_or_threshold_change_allowed=False,
    )
    timeout_contract = Rag90TimeoutContract(
        changed_behavioral_coordinate="timeout_contract",
        other_behavioral_coordinate_change_count=0,
        hard_physical_request_upper_bound_seconds=360,
        http_client_operation_timeout_seconds=360,
        standard_logical_call_hard_deadline_seconds=360,
        standard_retry_attempts_share_logical_deadline=True,
        repair_physical_request_hard_deadline_seconds=360,
        repair_logical_call_hard_deadline_seconds=360,
        combined_candidate_logical_upper_bound_seconds=720,
        child_terminate_grace_seconds=5,
        child_kill_grace_seconds=5,
        combined_candidate_with_cleanup_upper_bound_seconds=730,
        live_extension_allowed=False,
        case_specific_timeout_allowed=False,
        extra_retry_or_grace_allowed=False,
        cancellation_implementation_changed=False,
        retry_policy_changed=False,
        physical_call_structure_changed=False,
        output_budget_changed=False,
        prompt_or_candidate_logic_changed=False,
        evidence=timeout_evidence,
    )
    return Rag90ExperimentManifest(
        schema_version="phase3.rag90_timeout_contract_experiment.v1",
        jira_issue="RAG-90",
        stacked_base_commit=stacked_base_commit,
        dataset_name=RAG90_DATASET_NAME,
        core_manifest=core,
        timeout_contract=timeout_contract,
        independence=independence,
        inherited_rag88_lock_sha256=rag88_lock_sha256,
        inherited_prompt_model_budget_retry_candidate_and_gate_contract=True,
        new_independent_fixture_required=True,
        prelive_commit_and_push_required=True,
        one_shot_only=True,
        pipeline_failure_count_maximum=0,
        failed_case_rerun_replacement_exclusion_allowed=False,
        raw_content_persistence_allowed=False,
        gold_v2_or_reference_text_use_allowed=False,
        database_or_volume_operation_allowed=False,
        merge_deploy_or_draft_removal_allowed=False,
    )


def _validate_inherited_contract(
    core: Rag88ExperimentManifest,
    rag88_lock: Rag88ExperimentLock,
) -> None:
    if core.decision_rule != rag88_lock.decision_rule:
        raise EvaluationQwenTimeoutContractValidationError("rag90_decision_rule_drift")
    left = core.generation.model_dump(mode="json")
    right = rag88_lock.generation.model_dump(mode="json")
    permitted = {
        "generation_case_wall_clock_timeout_seconds",
        "execution_schedule_fingerprint",
    }
    for key in set(left).union(right).difference(permitted):
        if left.get(key) != right.get(key):
            raise EvaluationQwenTimeoutContractValidationError(
                "rag90_non_timeout_generation_contract_drift"
            )
    if right.get("generation_case_wall_clock_timeout_seconds") != 180:
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag88_timeout_reference_drift")
    if left.get("generation_case_wall_clock_timeout_seconds") != 360:
        raise EvaluationQwenTimeoutContractValidationError("rag90_selected_timeout_drift")


def build_rag90_lock(manifest: Rag90ExperimentManifest) -> Rag90ExperimentLock:
    core = manifest.core_manifest
    return Rag90ExperimentLock(
        schema_version="phase3.rag90_timeout_contract_lock.v1",
        experiment_manifest_sha256=_sha256_bytes(canonical_json_bytes(manifest)),
        private_input_sha256=core.dataset.private_input_sha256,
        dataset_content_fingerprint=core.dataset.dataset_content_fingerprint,
        group_set_fingerprint=core.dataset.group_set_fingerprint,
        case_set_fingerprint=core.dataset.case_set_fingerprint,
        source_context_fingerprint=core.dataset.source_context_fingerprint,
        timeout_contract_sha256=_sha256_bytes(canonical_json_bytes(manifest.timeout_contract)),
        independence_proof_sha256=_sha256_bytes(canonical_json_bytes(manifest.independence)),
        manifest=manifest,
        prelive_commit_required=True,
        host_gate_required=True,
        one_shot_attempt_marker_required=True,
        expected_logical_observation_count=144,
        raw_content_persistence_allowed=False,
    )


def load_frozen_rag90_manifest(
    lock_path: Path,
    private_input_path: Path,
) -> tuple[Rag90ExperimentManifest, Rag88PrivateFixtureEnvelope]:
    lock_bytes, payload = read_json_object(lock_path)
    lock = Rag90ExperimentLock.model_validate(payload)
    if not model_bytes_match(lock_bytes, lock):
        raise EvaluationQwenTimeoutContractValidationError("rag90_lock_model_bytes_mismatch")
    private_bytes, envelope = load_rag88_private_fixture(private_input_path)
    if _sha256_bytes(private_bytes) != lock.private_input_sha256:
        raise EvaluationQwenTimeoutContractValidationError("rag90_private_input_hash_drift")
    core = lock.manifest.core_manifest
    rebuilt = build_rag88_experiment_manifest(
        envelope,
        private_input_sha256=lock.private_input_sha256,
        independence=core.independence,
        stacked_base_commit=core.stacked_base_commit,
        case_timeout_seconds=RAG90_CASE_TIMEOUT_SECONDS,
    )
    if rebuilt != core or build_rag90_lock(lock.manifest) != lock:
        raise EvaluationQwenTimeoutContractValidationError("rag90_frozen_manifest_drift")
    return lock.manifest, envelope


def build_rag90_host_gate(
    inventory: Rag86LMInventorySummary,
    *,
    gpu_utilization_samples_percent: tuple[int, int, int],
    concurrent_evaluation_process_count: int,
    concurrent_model_load_observed: bool,
    gpu_load_exception_authorized: bool = False,
    task_owned_model_load_performed: bool = False,
) -> Rag90HostGate:
    exact_once = inventory.available and inventory.target_loaded_instance_count == 1
    context_matches = inventory.target_loaded_context_length == 12312
    gpu_ok = max(gpu_utilization_samples_percent) <= RAG90_GPU_UTILIZATION_MAXIMUM_PERCENT
    reasons: list[SafeId] = []
    if not exact_once:
        reasons.append("rag90_exact_target_not_loaded_once")
    if not context_matches:
        reasons.append("rag90_exact_target_context_drift")
    if concurrent_evaluation_process_count:
        reasons.append("rag90_concurrent_evaluation_observed")
    if not gpu_ok and not gpu_load_exception_authorized:
        reasons.append("rag90_gpu_high_load_observed")
    if concurrent_model_load_observed:
        reasons.append("rag90_concurrent_model_load_observed")
    return Rag90HostGate(
        schema_version="phase3.rag90_host_gate.v1",
        lm_inventory=inventory,
        exact_target_loaded_once=bool(exact_once),
        exact_target_context_length_matches=context_matches,
        concurrent_evaluation_process_count=concurrent_evaluation_process_count,
        gpu_utilization_samples_percent=gpu_utilization_samples_percent,
        gpu_utilization_maximum_percent=10,
        gpu_high_load_absent=gpu_ok,
        gpu_load_exception_authorized=gpu_load_exception_authorized,
        concurrent_model_load_observed=concurrent_model_load_observed,
        other_alias_mutation_performed=False,
        lm_load_or_unload_performed=task_owned_model_load_performed,
        gate_passed=not reasons,
        reason_codes=tuple(reasons),
    )


def build_rag90_attempt_state(
    manifest: Rag90ExperimentManifest,
    *,
    host_gate: Rag90HostGate,
    prelive_commit_sha: str,
) -> Rag90AttemptState:
    if not host_gate.gate_passed:
        raise EvaluationQwenTimeoutContractValidationError("rag90_host_gate_not_passed")
    target_fingerprint = host_gate.lm_inventory.target_entry_fingerprint
    if target_fingerprint is None:
        raise EvaluationQwenTimeoutContractValidationError(
            "rag90_host_gate_target_fingerprint_missing"
        )
    return Rag90AttemptState(
        schema_version="phase3.rag90_timeout_contract_attempt.v1",
        status="started",
        experiment_manifest_sha256=_sha256_bytes(canonical_json_bytes(manifest)),
        private_input_sha256=manifest.core_manifest.dataset.private_input_sha256,
        host_gate_sha256=_sha256_bytes(canonical_json_bytes(host_gate)),
        prelive_commit_sha=prelive_commit_sha,
        pre_target_entry_fingerprint=target_fingerprint,
        expected_logical_observation_count=144,
        timeout_extension_or_rerun_allowed=False,
        raw_content_persisted=False,
    )


def run_rag90_experiment(
    manifest: Rag90ExperimentManifest,
    envelope: Rag88PrivateFixtureEnvelope,
    *,
    host_gate: Rag90HostGate,
    prelive_commit_sha: str,
    post_lm_inventory_provider: Callable[[], Rag86LMInventorySummary],
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag90ExperimentResult:
    if not host_gate.gate_passed:
        raise EvaluationQwenTimeoutContractValidationError("rag90_host_gate_not_passed")
    telemetry_payloads: list[dict[str, object]] = []
    core_result = run_rag88_experiment(
        manifest.core_manifest,
        envelope,
        prelive_commit_sha=prelive_commit_sha,
        pre_lm_inventory=host_gate.lm_inventory,
        post_lm_inventory_provider=post_lm_inventory_provider,
        generator=generator,
        progress_callback=progress_callback,
        capture_physical_telemetry=True,
        phase_telemetry_callback=telemetry_payloads.append,
    )
    telemetry = tuple(
        Rag90PhaseTelemetryObservation.model_validate(item) for item in telemetry_payloads
    )
    if len(telemetry) != 144 or tuple(item.execution_ordinal for item in telemetry) != tuple(
        range(1, 145)
    ):
        raise EvaluationQwenTimeoutContractValidationError("rag90_phase_telemetry_count_drift")
    summary = _summarize_phase_telemetry(telemetry)
    if core_result.pipeline_failure_count:
        reason_codes: tuple[SafeId, ...] = ("rag90_pipeline_failure",)
    elif not core_result.exact_target_stable:
        reason_codes = ("rag90_exact_target_drift",)
    elif core_result.conclusion == "baseline_sensitivity_not_established":
        reason_codes = ("rag90_baseline_sensitivity_not_established",)
    elif core_result.conclusion == "candidate_adopted":
        reason_codes = ("rag90_candidate_adopted",)
    elif core_result.conclusion == "candidate_rejected":
        reason_codes = ("rag90_candidate_rejected",)
    else:
        reason_codes = ("rag90_inconclusive",)
    return Rag90ExperimentResult(
        schema_version="phase3.rag90_timeout_contract_result.v1",
        experiment_manifest_sha256=_sha256_bytes(canonical_json_bytes(manifest)),
        private_input_sha256=manifest.core_manifest.dataset.private_input_sha256,
        host_gate_sha256=_sha256_bytes(canonical_json_bytes(host_gate)),
        prelive_commit_sha=prelive_commit_sha,
        dataset_name=RAG90_DATASET_NAME,
        changed_behavioral_coordinate="timeout_contract",
        gpu_load_exception_authorized=host_gate.gpu_load_exception_authorized,
        core_result=core_result,
        phase_telemetry_summary=summary,
        phase_telemetry=telemetry,
        conclusion=core_result.conclusion,
        reason_codes=reason_codes,
        validity_gate_passed=core_result.validity_gate_passed,
        baseline_sensitivity_gate_passed=core_result.baseline_sensitivity_gate_passed,
        candidate_adoption_gate_passed=core_result.candidate_adoption_gate_passed,
        candidate_metrics_descriptive_only=core_result.candidate_metrics_descriptive_only,
        baseline_retained=core_result.baseline_retained,
        raw_content_persisted=False,
        chain_of_thought_persisted=False,
    )


def _summarize_phase_telemetry(
    items: Sequence[Rag90PhaseTelemetryObservation],
) -> Rag90PhaseTelemetrySummary:
    standard = [
        latency
        for item in items
        if item.variant != "combined_candidate"
        for latency in item.physical_request_latencies_ms
    ]
    repair = [
        latency
        for item in items
        if item.variant == "combined_candidate"
        for latency in item.physical_request_latencies_ms
    ]
    baseline = [item.logical_latency_ms for item in items if item.variant == "combined_baseline"]
    candidate = [item.logical_latency_ms for item in items if item.variant == "combined_candidate"]
    return Rag90PhaseTelemetrySummary(
        logical_observation_count=144,
        physical_request_count=sum(item.physical_request_count for item in items),
        physical_request_timeout_count=sum(item.physical_request_timeout_count for item in items),
        standard_physical_request_latency=_latency_distribution(standard),
        repair_physical_request_latency=_latency_distribution(repair),
        combined_baseline_logical_latency=_latency_distribution(baseline),
        combined_candidate_logical_latency=_latency_distribution(candidate),
        derived_candidate_unavailable_count=sum(
            item.derived_from_paired_baseline for item in items
        ),
    )


def _latency_distribution(values: Sequence[int]) -> Rag90LatencyDistribution:
    if not values:
        return Rag90LatencyDistribution(count=0, p50_ms=0, p95_ms=0, maximum_ms=0)
    ordered = sorted(values)
    return Rag90LatencyDistribution(
        count=len(ordered),
        p50_ms=_nearest_rank(ordered, 0.50),
        p95_ms=_nearest_rank(ordered, 0.95),
        maximum_ms=ordered[-1],
    )


def _nearest_rank(ordered: Sequence[int], quantile: float) -> int:
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))]
