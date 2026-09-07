from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import random
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.evaluation.generation_prompt_profiles import resolve_generation_prompt_profile
from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.evaluation.qwen_multifact_confirm import build_qwen_multifact_confirm_manifest
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
    GenerationRequest,
    GenerationResult,
    OpenAICompatibleChatAnswerGenerator,
)
from app.schemas.evaluation_datasets_v2 import (
    EvaluationCaseV2Spec,
    EvaluationCorpusDocumentSpec,
    EvaluationCorpusFactSpec,
    EvaluationDatasetManifestV2,
    EvaluationExpectedEvidenceSpec,
    EvaluationRequiredFactSpec,
)
from app.schemas.evaluations import (
    EvaluationDatasetManifestInfo,
    EvaluationDatasetSourceType,
    EvaluationRunRequestStrategy,
)
from app.services.evaluation_atomic_claim_contracts import (
    SafeId,
    Sha256,
    StrictRawFreeModel,
    canonical_json_bytes,
    model_bytes_match,
    read_json_object,
)
from app.services.evaluation_atomic_claim_review_only_phase_b_service import (
    _identifier_tokens,
    _normalize_identifier_text,
    _strong_fact_identifier_tokens,
    _whole_statement_exact_match,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    _generate_review_case_with_hard_timeout,
    _review_generation_settings,
)
from app.services.evaluation_oracle_context_service import _generate_oracle_answer
from app.services.evaluation_qwen_confirm_fixture_sensitivity_service import (
    Rag87PrivateFixtureEnvelope,
    load_rag87_private_fixture,
)
from app.services.evaluation_qwen_context_near_miss_service import Rag86LMInventorySummary
from app.services.rag_service import (
    _is_insufficient_evidence_answer,
    _validate_generation_output_safety,
)

_MODEL: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
_BASELINE_PROFILE: Literal["baseline"] = "baseline"
_DATASET: Literal["rag88_qwen_multifact_interference_repair_v1"] = (
    "rag88_qwen_multifact_interference_repair_v1"
)
_STACKED_BASE = "2f50b22ba9aad7d1ca362112373500591d521c24"
_PRIVATE_SEED: Literal[88088] = 88088
_PRIVATE_NAMESPACE: Literal["R88-interference-repair-20260826"] = "R88-interference-repair-20260826"
_GROUP_COUNT: Literal[12] = 12
_CASE_COUNT: Literal[36] = 36
_SOURCE_COUNT: Literal[72] = 72
_FACT_COUNT: Literal[72] = 72
_REQUIRED_FACT_COUNT: Literal[24] = 24
_SOURCES_PER_GROUP: Literal[6] = 6
_REPEATS: Literal[3] = 3
_VARIANT_OBSERVATION_COUNT: Literal[144] = 144
_MODEL_CALL_COUNT: Literal[144] = 144
_REPAIR_CALL_COUNT: Literal[36] = 36
_MAX_CONTEXT_CHARS: Literal[6000] = 6000
_MAX_OUTPUT_CHARS: Literal[12000] = 12000
_MAX_OUTPUT_TOKENS: Literal[8192] = 8192
_CASE_TIMEOUT_SECONDS: Literal[180] = 180
_PROCESS_TERMINATE_GRACE_SECONDS = 5.0
_PROCESS_KILL_GRACE_SECONDS = 5.0
_TARGET_LOADED_CONTEXT_LENGTH: Literal[12312] = 12312
_MAJORITY_MINIMUM_REPEATS: Literal[2] = 2
_LATIN_ROTATION_OFFSETS: tuple[Literal[0], Literal[4], Literal[8]] = (0, 4, 8)
_BOOTSTRAP_RESAMPLES: Literal[10000] = 10_000
_BOOTSTRAP_SEED: Literal[88088] = 88088
_MINIMUM_ELIGIBLE_CASES: Literal[8] = 8
_MINIMUM_BASELINE_INCOMPLETE_CASES: Literal[6] = 6
_MINIMUM_INTERFERENCE_DROP = 0.5
_MAXIMUM_EXACT_P_VALUE = 0.05
_MINIMUM_CANDIDATE_JOINT_DELTA = 0.25
_MINIMUM_CANDIDATE_IMPROVED_CASES: Literal[3] = 3
_MAXIMUM_LATENCY_RATIO = 2.0
_INSUFFICIENCY_ASSERTION_MARKERS = (
    "根拠不足",
    "根拠が不足",
    "情報が不足",
    "十分な根拠がない",
    "十分な情報がない",
    "insufficient evidence",
    "insufficient context",
    "not enough evidence",
    "not enough context",
    "does not contain enough evidence",
)
_REPAIR_SYSTEM_INSTRUCTIONS = (
    "You are a completeness and citation repair stage. Inspect only the supplied user "
    "question, untrusted evidence context, and first-pass answer. Do not infer hidden answer "
    "keys or use outside knowledge. Return the requested JSON decision only. Do not expose "
    "chain-of-thought, analysis, or step-by-step reasoning."
)
_REPAIR_RESPONSE_FORMAT: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "generic_completeness_repair",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "decision": {"type": "string", "enum": ["keep", "revise"]},
                "coverage_status": {
                    "type": "string",
                    "enum": ["complete", "incomplete", "uncertain"],
                },
                "incorrect_insufficiency_detected": {"type": "boolean"},
                "citation_status": {
                    "type": "string",
                    "enum": ["complete", "incomplete", "uncertain"],
                },
                "revised_answer": {"type": "string"},
            },
            "required": [
                "decision",
                "coverage_status",
                "incorrect_insufficiency_detected",
                "citation_status",
                "revised_answer",
            ],
        },
    },
}

GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Variant = Literal["single_a", "single_b", "combined_baseline", "combined_candidate"]
Conclusion = Literal[
    "candidate_adopted",
    "candidate_rejected",
    "baseline_sensitivity_not_established",
    "inconclusive",
]


class EvaluationQwenMultifactInterferenceRepairError(RuntimeError):
    """Stable fail-closed error for the frozen RAG-88 experiment."""


class Rag88PrivateGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_id: SafeId
    language: Literal["ja", "en"]
    single_a_case_id: SafeId
    single_b_case_id: SafeId
    combined_case_id: SafeId
    source_ids: tuple[SafeId, SafeId, SafeId, SafeId, SafeId, SafeId]
    required_source_ids: tuple[SafeId, SafeId]


class Rag88PrivateFixtureEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase3.rag88_private_fixture.v1"]
    generation_seed: Literal[88088]
    namespace: Literal["R88-interference-repair-20260826"]
    dataset: EvaluationDatasetManifestV2
    groups: tuple[Rag88PrivateGroup, ...]


class Rag88GroupBinding(StrictRawFreeModel):
    group_id: SafeId
    language: Literal["ja", "en"]
    single_a_case_id: SafeId
    single_b_case_id: SafeId
    combined_case_id: SafeId
    single_a_question_hash: Sha256
    single_b_question_hash: Sha256
    combined_question_hash: Sha256
    required_fact_ids: tuple[SafeId, SafeId]
    normalized_fact_hashes: tuple[Sha256, Sha256]
    source_ids: tuple[SafeId, SafeId, SafeId, SafeId, SafeId, SafeId]
    required_source_ids: tuple[SafeId, SafeId]
    required_citation_ids: tuple[Literal[2], Literal[5]]
    source_content_hashes: tuple[Sha256, Sha256, Sha256, Sha256, Sha256, Sha256]
    source_set_hash: Sha256
    context_hash: Sha256
    context_length: int = Field(gt=0, le=6000)
    source_order_frozen: Literal[True]
    same_oracle_context_all_variants: Literal[True]


class Rag88DatasetBinding(StrictRawFreeModel):
    dataset_name: Literal["rag88_qwen_multifact_interference_repair_v1"]
    private_input_sha256: Sha256
    dataset_content_fingerprint: Sha256
    corpus_fingerprint: Sha256
    group_count: Literal[12]
    case_count: Literal[36]
    source_count: Literal[72]
    corpus_fact_count: Literal[72]
    required_fact_count: Literal[24]
    group_set_fingerprint: Sha256
    case_set_fingerprint: Sha256
    question_set_fingerprint: Sha256
    normalized_fact_set_fingerprint: Sha256
    source_content_set_fingerprint: Sha256
    logical_document_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    groups: tuple[Rag88GroupBinding, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if len(self.groups) != _GROUP_COUNT:
            raise ValueError("rag88_dataset_group_count_drift")
        if len({item.group_id for item in self.groups}) != _GROUP_COUNT:
            raise ValueError("rag88_dataset_group_identity_drift")
        return self


class Rag88ReferenceCatalog(StrictRawFreeModel):
    local_accuracy_dev_question_hashes: tuple[Sha256, ...]
    local_accuracy_dev_normalized_fact_hashes: tuple[Sha256, ...]
    local_accuracy_dev_source_content_hashes: tuple[Sha256, ...]
    local_accuracy_dev_logical_document_ids: tuple[SafeId, ...]
    rag84_confirm_question_hashes: tuple[Sha256, ...]
    rag84_confirm_normalized_fact_hashes: tuple[Sha256, ...]
    rag84_confirm_source_content_hashes: tuple[Sha256, ...]
    rag84_confirm_logical_document_ids: tuple[SafeId, ...]
    rag87_question_hashes: tuple[Sha256, ...]
    rag87_normalized_fact_hashes: tuple[Sha256, ...]
    rag87_source_content_hashes: tuple[Sha256, ...]
    rag87_logical_document_ids: tuple[SafeId, ...]
    catalog_fingerprint: Sha256
    raw_reference_content_persisted: Literal[False]


class Rag88IndependenceProof(StrictRawFreeModel):
    fixture_dataset: Literal["rag88_qwen_multifact_interference_repair_v1"]
    local_accuracy_dev_question_overlap_count: Literal[0]
    local_accuracy_dev_normalized_fact_overlap_count: Literal[0]
    local_accuracy_dev_source_content_overlap_count: Literal[0]
    local_accuracy_dev_logical_document_id_overlap_count: Literal[0]
    rag84_question_overlap_count: Literal[0]
    rag84_normalized_fact_overlap_count: Literal[0]
    rag84_source_content_overlap_count: Literal[0]
    rag84_logical_document_id_overlap_count: Literal[0]
    rag87_question_overlap_count: Literal[0]
    rag87_normalized_fact_overlap_count: Literal[0]
    rag87_source_content_overlap_count: Literal[0]
    rag87_logical_document_id_overlap_count: Literal[0]
    reference_catalog_fingerprint: Sha256
    private_fixture_generation_seed: Literal[88088]
    private_fixture_namespace: Literal["R88-interference-repair-20260826"]
    reference_content_used_for_case_design: Literal[False]
    reference_content_used_only_for_one_way_hash_check: Literal[True]
    gold_v2_opened: Literal[False]
    result_based_case_selection_allowed: Literal[False]
    failed_case_replacement_allowed: Literal[False]
    post_result_threshold_or_fixture_change_allowed: Literal[False]


class Rag88GenerationContract(StrictRawFreeModel):
    retrieval_mode: Literal["static_oracle_private_fixture"]
    source_order_frozen: Literal[True]
    same_oracle_context_all_variants: Literal[True]
    generation_provider: Literal["lmstudio"]
    resolved_generation_model: Literal["qwen/qwen3.5-9b"]
    generation_temperature: float
    reasoning_enabled: Literal[False]
    baseline_prompt_profile: Literal["baseline"]
    baseline_prompt_fingerprint: Sha256
    repair_prompt_fingerprint: Sha256
    repair_response_schema_fingerprint: Sha256
    repair_input_fields: tuple[
        Literal["question"], Literal["oracle_context"], Literal["pass1_answer"]
    ]
    evaluator_required_fact_sent_to_repair: Literal[False]
    expected_answer_sent_to_repair: Literal[False]
    evaluator_fact_id_sent_to_repair: Literal[False]
    chain_of_thought_requested: Literal[False]
    structured_decision_process_only: Literal[True]
    combined_candidate_reuses_paired_baseline_pass1: Literal[True]
    maximum_revision_count: Literal[1]
    generation_max_context_chars: Literal[6000]
    generation_max_output_chars: Literal[12000]
    generation_max_output_tokens: Literal[8192]
    generation_case_wall_clock_timeout_seconds: int = Field(ge=1, le=3600)
    lmstudio_loaded_context_length: Literal[12312]
    retry_policy: Literal["existing_evaluation_generation_retry"]
    repeats: Literal[3]
    group_count: Literal[12]
    variant_observation_count: Literal[144]
    model_call_count: Literal[144]
    repair_call_count: Literal[36]
    case_order_rotation_offsets: tuple[Literal[0], Literal[4], Literal[8]]
    execution_order: Literal["latin_case_rotation_then_paired_variant_calls"]
    execution_seed: Literal[88088]
    execution_schedule_fingerprint: Sha256

    @model_validator(mode="after")
    def validate_frozen_generation(self) -> Self:
        if self.generation_temperature != 0.0:
            raise ValueError("rag88_generation_temperature_drift")
        if self.case_order_rotation_offsets != _LATIN_ROTATION_OFFSETS:
            raise ValueError("rag88_latin_rotation_drift")
        return self


class Rag88DecisionRule(StrictRawFreeModel):
    eligibility_rule: Literal["single_a_and_single_b_case_majority_atomic_and_citation_grounded"]
    baseline_primary_metric: Literal[
        "eligible_case_paired_single_controls_minus_combined_joint_completeness"
    ]
    candidate_primary_metric: Literal[
        "eligible_case_combined_candidate_minus_baseline_joint_completeness"
    ]
    fact_majority_minimum_repeats: Literal[2]
    minimum_eligible_case_count: Literal[8]
    minimum_baseline_incomplete_case_count: Literal[6]
    minimum_interference_drop: float
    bootstrap_resamples: Literal[10000]
    bootstrap_seed: Literal[88088]
    bootstrap_confidence_level: float
    bootstrap_lower_bound_must_exceed_zero: Literal[True]
    exact_paired_test: Literal["two_sided_exact_binomial_on_discordant_pairs"]
    exact_p_value_maximum: float
    minimum_candidate_joint_completeness_delta: float
    minimum_candidate_improved_case_count: Literal[3]
    candidate_atomic_fact_recall_non_degradation_required: Literal[True]
    citation_grounding_non_degradation_required: Literal[True]
    citation_source_coverage_non_degradation_required: Literal[True]
    false_insufficiency_non_increase_required: Literal[True]
    unexpected_fact_case_count_maximum: Literal[0]
    forbidden_claim_case_count_maximum: Literal[0]
    unanswerable_guardrail_applicable: Literal[False]
    pipeline_failure_count_maximum: Literal[0]
    binding_drift_count_maximum: Literal[0]
    case_exclusion_count_maximum: Literal[0]
    case_replacement_count_maximum: Literal[0]
    candidate_p95_latency_ratio_maximum: float
    exact_target_model_id_must_match: Literal[True]
    exact_target_loaded_instance_count: Literal[1]
    exact_target_entry_pre_post_must_match: Literal[True]
    exact_target_context_length: Literal[12312]
    full_non_target_inventory_match_required: Literal[False]
    non_target_inventory_drift_recorded: Literal[True]
    baseline_sensitivity_required_for_candidate_adoption: Literal[True]
    failed_case_replacement_extra_repeat_or_rerun_allowed: Literal[False]
    threshold_adjustment_after_results_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        actual = (
            self.minimum_interference_drop,
            self.exact_p_value_maximum,
            self.minimum_candidate_joint_completeness_delta,
            self.candidate_p95_latency_ratio_maximum,
            self.bootstrap_confidence_level,
        )
        expected = (
            _MINIMUM_INTERFERENCE_DROP,
            _MAXIMUM_EXACT_P_VALUE,
            _MINIMUM_CANDIDATE_JOINT_DELTA,
            _MAXIMUM_LATENCY_RATIO,
            0.95,
        )
        if actual != expected:
            raise ValueError("rag88_decision_threshold_drift")
        return self


class Rag88ExperimentManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.rag88_interference_repair_experiment.v1"]
    jira_issue: Literal["RAG-88"]
    stacked_base_commit: GitSha
    experiment_scope: Literal["within_case_interference_and_generic_two_pass_repair"]
    dataset: Rag88DatasetBinding
    independence: Rag88IndependenceProof
    generation: Rag88GenerationContract
    decision_rule: Rag88DecisionRule
    raw_content_persistence_allowed: Literal[False]
    repository_external_private_input_required: Literal[True]
    external_non_loopback_http_allowed: Literal[False]
    database_write_allowed: Literal[False]
    gold_v2_access_allowed: Literal[False]
    case_exclusion_replacement_extra_repeat_or_rerun_allowed: Literal[False]
    merge_deploy_draft_removal_or_profile_promotion_allowed: Literal[False]


class Rag88ExperimentLock(StrictRawFreeModel):
    schema_version: Literal["phase3.rag88_interference_repair_lock.v1"]
    stacked_base_commit: GitSha
    experiment_manifest_sha256: Sha256
    dataset_binding_sha256: Sha256
    independence_proof_sha256: Sha256
    generation_contract_sha256: Sha256
    decision_rule_sha256: Sha256
    private_input_sha256: Sha256
    dataset_content_fingerprint: Sha256
    group_set_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    baseline_prompt_fingerprint: Sha256
    repair_prompt_fingerprint: Sha256
    repair_response_schema_fingerprint: Sha256
    execution_schedule_fingerprint: Sha256
    independence: Rag88IndependenceProof
    generation: Rag88GenerationContract
    decision_rule: Rag88DecisionRule
    prelive_commit_required: Literal[True]
    one_shot_attempt_marker_required: Literal[True]
    model_call_count: Literal[144]
    gold_v2_access_allowed: Literal[False]
    raw_content_persistence_allowed: Literal[False]


class Rag88AttemptState(StrictRawFreeModel):
    schema_version: Literal["phase3.rag88_interference_repair_attempt.v1"]
    status: Literal["started"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    prelive_commit_sha: GitSha
    pre_full_lm_inventory_fingerprint: Sha256
    pre_target_entry_fingerprint: Sha256
    expected_model_call_count: Literal[144]
    repeat_replacement_or_rerun_allowed: Literal[False]
    raw_content_persisted: Literal[False]


class Rag88VariantObservation(StrictRawFreeModel):
    group_id: SafeId
    repeat: int = Field(ge=1, le=3)
    variant: Variant
    execution_ordinal: int = Field(ge=1, le=144)
    answer_hash: Sha256 | None
    baseline_pass1_answer_hash: Sha256 | None = None
    context_hash: Sha256
    atomic_fact_matches: tuple[bool, bool]
    exact_fact_matches: tuple[bool, bool]
    citation_grounded_fact_matches: tuple[bool, bool]
    joint_complete: bool
    joint_citation_grounded: bool
    citation_source_coverage: bool
    insufficiency_false_assertion: bool
    unexpected_fact_count: int = Field(ge=0)
    forbidden_claim_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    repair_latency_ms: int | None = Field(default=None, ge=0)
    repair_decision: Literal["keep", "revise"] | None = None
    repair_coverage_status: Literal["complete", "incomplete", "uncertain"] | None = None
    repair_incorrect_insufficiency_detected: bool | None = None
    repair_citation_status: Literal["complete", "incomplete", "uncertain"] | None = None
    revision_performed: bool | None = None
    pipeline_failure_reason_code: str | None = None

    @model_validator(mode="after")
    def validate_candidate_fields(self) -> Self:
        candidate_fields = (
            self.baseline_pass1_answer_hash,
            self.repair_latency_ms,
            self.repair_decision,
            self.repair_coverage_status,
            self.repair_incorrect_insufficiency_detected,
            self.repair_citation_status,
            self.revision_performed,
        )
        if self.variant == "combined_candidate":
            if self.pipeline_failure_reason_code is None and any(
                value is None for value in candidate_fields
            ):
                raise ValueError("rag88_candidate_observation_incomplete")
        elif any(value is not None for value in candidate_fields):
            raise ValueError("rag88_non_candidate_repair_field_present")
        return self


class Rag88ExperimentSummary(StrictRawFreeModel):
    repeats: Literal[3]
    group_count: Literal[12]
    variant_observation_count: Literal[144]
    eligible_case_count: int = Field(ge=0, le=12)
    eligible_case_ids_fingerprint: Sha256
    baseline_incomplete_case_count: int = Field(ge=0, le=12)
    single_control_joint_completeness: float = Field(ge=0.0, le=1.0)
    combined_baseline_joint_completeness: float = Field(ge=0.0, le=1.0)
    combined_candidate_joint_completeness: float = Field(ge=0.0, le=1.0)
    baseline_interference_drop: float = Field(ge=-1.0, le=1.0)
    baseline_interference_bootstrap_ci95: tuple[float, float]
    baseline_interference_exact_p_value: float = Field(ge=0.0, le=1.0)
    candidate_joint_completeness_delta: float = Field(ge=-1.0, le=1.0)
    candidate_bootstrap_ci95: tuple[float, float]
    candidate_exact_p_value: float = Field(ge=0.0, le=1.0)
    candidate_improved_case_count: int = Field(ge=0, le=12)
    candidate_regressed_case_count: int = Field(ge=0, le=12)
    baseline_atomic_fact_recall: float = Field(ge=0.0, le=1.0)
    candidate_atomic_fact_recall: float = Field(ge=0.0, le=1.0)
    baseline_citation_grounding_recall: float = Field(ge=0.0, le=1.0)
    candidate_citation_grounding_recall: float = Field(ge=0.0, le=1.0)
    baseline_citation_source_coverage_rate: float = Field(ge=0.0, le=1.0)
    candidate_citation_source_coverage_rate: float = Field(ge=0.0, le=1.0)
    baseline_false_insufficiency_observation_count: int = Field(ge=0, le=36)
    candidate_false_insufficiency_observation_count: int = Field(ge=0, le=36)
    candidate_unexpected_fact_case_count: int = Field(ge=0, le=12)
    candidate_forbidden_claim_case_count: int = Field(ge=0, le=12)
    baseline_pipeline_failure_count: int = Field(ge=0, le=108)
    candidate_pipeline_failure_count: int = Field(ge=0, le=36)
    pipeline_failure_count: int = Field(ge=0, le=144)
    combined_baseline_p95_latency_ms: int = Field(ge=0)
    combined_candidate_p95_latency_ms: int = Field(ge=0)
    candidate_p95_latency_ratio: float = Field(ge=0.0)
    repair_keep_observation_count: int = Field(ge=0, le=36)
    repair_revision_observation_count: int = Field(ge=0, le=36)


class Rag88BaselineSensitivityChecks(StrictRawFreeModel):
    minimum_eligible_case_count_passed: bool
    minimum_baseline_incomplete_case_count_passed: bool
    minimum_interference_drop_passed: bool
    bootstrap_lower_bound_passed: bool
    exact_paired_test_passed: bool
    baseline_pipeline_passed: bool
    sensitivity_gate_passed: bool


class Rag88CandidateAdoptionChecks(StrictRawFreeModel):
    baseline_sensitivity_passed: bool
    minimum_joint_delta_passed: bool
    minimum_improved_case_count_passed: bool
    bootstrap_lower_bound_passed: bool
    exact_paired_test_passed: bool
    atomic_fact_recall_non_degradation_passed: bool
    citation_grounding_non_degradation_passed: bool
    citation_source_coverage_non_degradation_passed: bool
    false_insufficiency_non_increase_passed: bool
    unexpected_fact_guardrail_passed: bool
    forbidden_claim_guardrail_passed: bool
    candidate_pipeline_passed: bool
    latency_guardrail_passed: bool
    adoption_gate_passed: bool


class Rag88ExperimentResult(StrictRawFreeModel):
    schema_version: Literal["phase3.rag88_interference_repair_result.v1"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    prelive_commit_sha: GitSha
    dataset_name: Literal["rag88_qwen_multifact_interference_repair_v1"]
    dataset_content_fingerprint: Sha256
    group_set_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    model: Literal["qwen/qwen3.5-9b"]
    model_call_count: Literal[144]
    variant_observation_count: Literal[144]
    case_exclusion_count: Literal[0]
    case_replacement_count: Literal[0]
    binding_drift_count: Literal[0]
    pipeline_failure_count: int = Field(ge=0, le=144)
    pre_lm_inventory: Rag86LMInventorySummary
    post_lm_inventory: Rag86LMInventorySummary
    exact_target_stable: bool
    full_lm_inventory_stable: bool
    non_target_inventory_drift_observed: bool
    validity_gate_passed: bool
    baseline_sensitivity_gate_passed: bool
    candidate_adoption_gate_passed: bool
    candidate_metrics_descriptive_only: bool
    conclusion: Conclusion
    reason_codes: tuple[str, ...]
    summary: Rag88ExperimentSummary
    baseline_sensitivity_checks: Rag88BaselineSensitivityChecks
    candidate_adoption_checks: Rag88CandidateAdoptionChecks
    evaluator: Literal["deterministic_identifier_equivalence_v1"]
    diagnostic_only: Literal[True]
    gold_holdout_eligible: Literal[False]
    public_accuracy_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]
    baseline_retained: bool
    raw_content_persisted: Literal[False]
    chain_of_thought_persisted: Literal[False]
    observations: tuple[Rag88VariantObservation, ...]


class _RepairDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["keep", "revise"]
    coverage_status: Literal["complete", "incomplete", "uncertain"]
    incorrect_insufficiency_detected: bool
    citation_status: Literal["complete", "incomplete", "uncertain"]
    revised_answer: str

    @model_validator(mode="after")
    def validate_revision_shape(self) -> Self:
        if self.decision == "keep" and self.revised_answer != "":
            raise ValueError("rag88_repair_keep_must_not_rewrite")
        if self.decision == "revise" and not self.revised_answer.strip():
            raise ValueError("rag88_repair_revision_missing")
        return self


@dataclass(frozen=True)
class _AnswerMaterial:
    answer_text: str | None = None
    answer_outcome: Literal["answered", "abstained"] | None = None
    citation_ids: tuple[int, ...] = ()
    reason_code: str | None = None
    physical_request_latencies_ms: tuple[int, ...] = ()
    physical_request_timeout_count: int = 0


@dataclass(frozen=True)
class _RepairMaterial:
    payload: _RepairDecisionPayload | None = None
    reason_code: str | None = None
    physical_request_latencies_ms: tuple[int, ...] = ()
    physical_request_timeout_count: int = 0


class _TimedAnswerGenerator:
    """Record raw-free physical request durations without changing request content."""

    def __init__(self, inner: AnswerGenerator) -> None:
        self._inner = inner
        self.latencies_ms: list[int] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        try:
            return self._inner.generate(request)
        finally:
            self.latencies_ms.append(max(0, int(round((time.perf_counter() - started) * 1000))))


def build_rag88_private_fixture(private_entropy: bytes) -> Rag88PrivateFixtureEnvelope:
    if len(private_entropy) < 32:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_private_entropy_too_short")
    documents: list[EvaluationCorpusDocumentSpec] = []
    cases: list[EvaluationCaseV2Spec] = []
    groups: list[Rag88PrivateGroup] = []
    for ordinal in range(1, _GROUP_COUNT + 1):
        language: Literal["ja", "en"] = "ja" if ordinal <= 6 else "en"
        group_id = f"r88-group-{ordinal:02d}"
        unit = f"capsule-{_private_code(private_entropy, ordinal, 'unit', '')}"
        other_one = f"capsule-{_private_code(private_entropy, ordinal, 'other-a', '')}"
        other_two = f"capsule-{_private_code(private_entropy, ordinal, 'other-b', '')}"
        codes = (
            _private_code(private_entropy, ordinal, "distractor-seal", "DS"),
            _private_code(private_entropy, ordinal, "required-seal", "SM"),
            _private_code(private_entropy, ordinal, "transport-seal", "TS"),
            _private_code(private_entropy, ordinal, "distractor-audit", "DA"),
            _private_code(private_entropy, ordinal, "required-audit", "VA"),
            _private_code(private_entropy, ordinal, "lighting-audit", "LA"),
        )
        statements = _group_statements(
            language=language,
            unit=unit,
            other_one=other_one,
            other_two=other_two,
            codes=codes,
        )
        source_ids = tuple(
            f"r88-g{ordinal:02d}-source-{source_ordinal}"
            for source_ordinal in range(1, _SOURCES_PER_GROUP + 1)
        )
        for source_ordinal, (source_id, statement) in enumerate(
            zip(source_ids, statements, strict=True),
            start=1,
        ):
            fact_id = f"r88-g{ordinal:02d}-fact-{source_ordinal}"
            documents.append(
                EvaluationCorpusDocumentSpec(
                    source_key=source_id,
                    title=_source_title(
                        language=language,
                        group_id=group_id,
                        source_ordinal=source_ordinal,
                    ),
                    body=_source_body(
                        language=language,
                        statement=statement,
                        group_id=group_id,
                        source_ordinal=source_ordinal,
                    ),
                    facts=[EvaluationCorpusFactSpec(fact_id=fact_id, statement=statement)],
                )
            )
        fact_a = EvaluationRequiredFactSpec(
            fact_id=f"r88-g{ordinal:02d}-fact-2",
            statement=statements[1],
        )
        fact_b = EvaluationRequiredFactSpec(
            fact_id=f"r88-g{ordinal:02d}-fact-5",
            statement=statements[4],
        )
        question_a, question_b, question_combined = _group_questions(
            language=language,
            unit=unit,
        )
        forbidden_claims = [codes[index] for index in (0, 2, 3, 5)]
        common_tags = [f"language:{language}", "rag88", "oracle_context"]
        single_a_case_id = f"r88-g{ordinal:02d}-single-a"
        single_b_case_id = f"r88-g{ordinal:02d}-single-b"
        combined_case_id = f"r88-g{ordinal:02d}-combined"
        cases.extend(
            (
                EvaluationCaseV2Spec(
                    case_key=single_a_case_id,
                    question=question_a,
                    answerable=True,
                    expected_answer=statements[1],
                    required_facts=[fact_a],
                    expected_evidence=[
                        EvaluationExpectedEvidenceSpec(
                            source_key=source_ids[1],
                            fact_ids=[fact_a.fact_id],
                        )
                    ],
                    forbidden_claims=forbidden_claims,
                    required_citation=True,
                    expected_strategy=EvaluationRunRequestStrategy.HYBRID,
                    tags=[*common_tags, "single_hop", "variant:single_a"],
                ),
                EvaluationCaseV2Spec(
                    case_key=single_b_case_id,
                    question=question_b,
                    answerable=True,
                    expected_answer=statements[4],
                    required_facts=[fact_b],
                    expected_evidence=[
                        EvaluationExpectedEvidenceSpec(
                            source_key=source_ids[4],
                            fact_ids=[fact_b.fact_id],
                        )
                    ],
                    forbidden_claims=forbidden_claims,
                    required_citation=True,
                    expected_strategy=EvaluationRunRequestStrategy.HYBRID,
                    tags=[*common_tags, "single_hop", "variant:single_b"],
                ),
                EvaluationCaseV2Spec(
                    case_key=combined_case_id,
                    question=question_combined,
                    answerable=True,
                    expected_answer=f"{statements[1]} {statements[4]}",
                    required_facts=[fact_a, fact_b],
                    expected_evidence=[
                        EvaluationExpectedEvidenceSpec(
                            source_key=source_ids[1],
                            fact_ids=[fact_a.fact_id],
                        ),
                        EvaluationExpectedEvidenceSpec(
                            source_key=source_ids[4],
                            fact_ids=[fact_b.fact_id],
                        ),
                    ],
                    forbidden_claims=forbidden_claims,
                    required_citation=True,
                    expected_strategy=EvaluationRunRequestStrategy.HYBRID,
                    tags=[*common_tags, "multi_hop", "variant:combined"],
                ),
            )
        )
        groups.append(
            Rag88PrivateGroup(
                group_id=group_id,
                language=language,
                single_a_case_id=single_a_case_id,
                single_b_case_id=single_b_case_id,
                combined_case_id=combined_case_id,
                source_ids=source_ids,
                required_source_ids=(source_ids[1], source_ids[4]),
            )
        )
    envelope = Rag88PrivateFixtureEnvelope(
        schema_version="phase3.rag88_private_fixture.v1",
        generation_seed=_PRIVATE_SEED,
        namespace=_PRIVATE_NAMESPACE,
        dataset=EvaluationDatasetManifestV2(
            dataset=EvaluationDatasetManifestInfo(
                dataset_name=_DATASET,
                description="Independent private safe-synthetic within-case interference fixture.",
                version="v1",
                source_type=EvaluationDatasetSourceType.FIXTURE,
                metadata_json={
                    "case_groups": _GROUP_COUNT,
                    "variants": ["single_a", "single_b", "combined"],
                    "raw_content_repository_persistence": False,
                },
            ),
            corpus_documents=documents,
            cases=cases,
            metric_specs=[],
        ),
        groups=tuple(groups),
    )
    _validate_private_fixture(envelope)
    return envelope


def load_rag88_private_fixture(
    path: Path,
) -> tuple[bytes, Rag88PrivateFixtureEnvelope]:
    try:
        private_bytes = path.read_bytes()
        payload = json.loads(private_bytes)
        envelope = Rag88PrivateFixtureEnvelope.model_validate(payload)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_private_fixture_unreadable"
        ) from exc
    _validate_private_fixture(envelope)
    return private_bytes, envelope


def build_rag88_reference_catalog(
    rag87_envelope: Rag87PrivateFixtureEnvelope,
) -> Rag88ReferenceCatalog:
    local_manifest = build_local_accuracy_dev_manifest()
    rag84_confirm = build_qwen_multifact_confirm_manifest()
    local_sets = _reference_sets(local_manifest)
    rag84_sets = _reference_sets(rag84_confirm)
    rag87_sets = _reference_sets(rag87_envelope.dataset)
    catalog_payload: dict[str, object] = {
        "local_accuracy_dev": local_sets,
        "rag84_confirm": rag84_sets,
        "rag87": rag87_sets,
    }
    return Rag88ReferenceCatalog(
        local_accuracy_dev_question_hashes=local_sets[0],
        local_accuracy_dev_normalized_fact_hashes=local_sets[1],
        local_accuracy_dev_source_content_hashes=local_sets[2],
        local_accuracy_dev_logical_document_ids=local_sets[3],
        rag84_confirm_question_hashes=rag84_sets[0],
        rag84_confirm_normalized_fact_hashes=rag84_sets[1],
        rag84_confirm_source_content_hashes=rag84_sets[2],
        rag84_confirm_logical_document_ids=rag84_sets[3],
        rag87_question_hashes=rag87_sets[0],
        rag87_normalized_fact_hashes=rag87_sets[1],
        rag87_source_content_hashes=rag87_sets[2],
        rag87_logical_document_ids=rag87_sets[3],
        catalog_fingerprint=_sha256_bytes(canonical_json_bytes(catalog_payload)),
        raw_reference_content_persisted=False,
    )


def load_rag87_reference_fixture(path: Path) -> Rag87PrivateFixtureEnvelope:
    _, envelope = load_rag87_private_fixture(path)
    return envelope


def build_rag88_experiment_manifest(
    envelope: Rag88PrivateFixtureEnvelope,
    *,
    private_input_sha256: str,
    independence: Rag88IndependenceProof | None = None,
    reference_catalog: Rag88ReferenceCatalog | None = None,
    stacked_base_commit: str = _STACKED_BASE,
    case_timeout_seconds: int = _CASE_TIMEOUT_SECONDS,
) -> Rag88ExperimentManifest:
    _validate_git_sha(stacked_base_commit)
    _validate_private_fixture(envelope)
    dataset_binding = _build_dataset_binding(
        envelope,
        private_input_sha256=private_input_sha256,
    )
    if independence is None:
        if reference_catalog is None:
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_reference_catalog_required")
        independence = _build_independence_proof(dataset_binding, reference_catalog)
    baseline_profile = resolve_generation_prompt_profile(_BASELINE_PROFILE)
    schedule_fingerprint = _execution_schedule_fingerprint(dataset_binding.groups)
    generation = Rag88GenerationContract(
        retrieval_mode="static_oracle_private_fixture",
        source_order_frozen=True,
        same_oracle_context_all_variants=True,
        generation_provider="lmstudio",
        resolved_generation_model=_MODEL,
        generation_temperature=0.0,
        reasoning_enabled=False,
        baseline_prompt_profile=_BASELINE_PROFILE,
        baseline_prompt_fingerprint=baseline_profile.prompt_fingerprint,
        repair_prompt_fingerprint=_sha256(_REPAIR_SYSTEM_INSTRUCTIONS),
        repair_response_schema_fingerprint=_sha256_bytes(
            canonical_json_bytes(_REPAIR_RESPONSE_FORMAT)
        ),
        repair_input_fields=("question", "oracle_context", "pass1_answer"),
        evaluator_required_fact_sent_to_repair=False,
        expected_answer_sent_to_repair=False,
        evaluator_fact_id_sent_to_repair=False,
        chain_of_thought_requested=False,
        structured_decision_process_only=True,
        combined_candidate_reuses_paired_baseline_pass1=True,
        maximum_revision_count=1,
        generation_max_context_chars=_MAX_CONTEXT_CHARS,
        generation_max_output_chars=_MAX_OUTPUT_CHARS,
        generation_max_output_tokens=_MAX_OUTPUT_TOKENS,
        generation_case_wall_clock_timeout_seconds=case_timeout_seconds,
        lmstudio_loaded_context_length=_TARGET_LOADED_CONTEXT_LENGTH,
        retry_policy="existing_evaluation_generation_retry",
        repeats=_REPEATS,
        group_count=_GROUP_COUNT,
        variant_observation_count=_VARIANT_OBSERVATION_COUNT,
        model_call_count=_MODEL_CALL_COUNT,
        repair_call_count=_REPAIR_CALL_COUNT,
        case_order_rotation_offsets=_LATIN_ROTATION_OFFSETS,
        execution_order="latin_case_rotation_then_paired_variant_calls",
        execution_seed=_PRIVATE_SEED,
        execution_schedule_fingerprint=schedule_fingerprint,
    )
    decision_rule = Rag88DecisionRule(
        eligibility_rule=("single_a_and_single_b_case_majority_atomic_and_citation_grounded"),
        baseline_primary_metric=(
            "eligible_case_paired_single_controls_minus_combined_joint_completeness"
        ),
        candidate_primary_metric=(
            "eligible_case_combined_candidate_minus_baseline_joint_completeness"
        ),
        fact_majority_minimum_repeats=_MAJORITY_MINIMUM_REPEATS,
        minimum_eligible_case_count=_MINIMUM_ELIGIBLE_CASES,
        minimum_baseline_incomplete_case_count=_MINIMUM_BASELINE_INCOMPLETE_CASES,
        minimum_interference_drop=_MINIMUM_INTERFERENCE_DROP,
        bootstrap_resamples=_BOOTSTRAP_RESAMPLES,
        bootstrap_seed=_BOOTSTRAP_SEED,
        bootstrap_confidence_level=0.95,
        bootstrap_lower_bound_must_exceed_zero=True,
        exact_paired_test="two_sided_exact_binomial_on_discordant_pairs",
        exact_p_value_maximum=_MAXIMUM_EXACT_P_VALUE,
        minimum_candidate_joint_completeness_delta=_MINIMUM_CANDIDATE_JOINT_DELTA,
        minimum_candidate_improved_case_count=_MINIMUM_CANDIDATE_IMPROVED_CASES,
        candidate_atomic_fact_recall_non_degradation_required=True,
        citation_grounding_non_degradation_required=True,
        citation_source_coverage_non_degradation_required=True,
        false_insufficiency_non_increase_required=True,
        unexpected_fact_case_count_maximum=0,
        forbidden_claim_case_count_maximum=0,
        unanswerable_guardrail_applicable=False,
        pipeline_failure_count_maximum=0,
        binding_drift_count_maximum=0,
        case_exclusion_count_maximum=0,
        case_replacement_count_maximum=0,
        candidate_p95_latency_ratio_maximum=_MAXIMUM_LATENCY_RATIO,
        exact_target_model_id_must_match=True,
        exact_target_loaded_instance_count=1,
        exact_target_entry_pre_post_must_match=True,
        exact_target_context_length=_TARGET_LOADED_CONTEXT_LENGTH,
        full_non_target_inventory_match_required=False,
        non_target_inventory_drift_recorded=True,
        baseline_sensitivity_required_for_candidate_adoption=True,
        failed_case_replacement_extra_repeat_or_rerun_allowed=False,
        threshold_adjustment_after_results_allowed=False,
    )
    return Rag88ExperimentManifest(
        schema_version="phase3.rag88_interference_repair_experiment.v1",
        jira_issue="RAG-88",
        stacked_base_commit=stacked_base_commit,
        experiment_scope="within_case_interference_and_generic_two_pass_repair",
        dataset=dataset_binding,
        independence=independence,
        generation=generation,
        decision_rule=decision_rule,
        raw_content_persistence_allowed=False,
        repository_external_private_input_required=True,
        external_non_loopback_http_allowed=False,
        database_write_allowed=False,
        gold_v2_access_allowed=False,
        case_exclusion_replacement_extra_repeat_or_rerun_allowed=False,
        merge_deploy_draft_removal_or_profile_promotion_allowed=False,
    )


def build_rag88_experiment_lock(
    manifest: Rag88ExperimentManifest,
) -> Rag88ExperimentLock:
    return Rag88ExperimentLock(
        schema_version="phase3.rag88_interference_repair_lock.v1",
        stacked_base_commit=manifest.stacked_base_commit,
        experiment_manifest_sha256=_manifest_sha256(manifest),
        dataset_binding_sha256=_sha256_bytes(canonical_json_bytes(manifest.dataset)),
        independence_proof_sha256=_sha256_bytes(canonical_json_bytes(manifest.independence)),
        generation_contract_sha256=_sha256_bytes(canonical_json_bytes(manifest.generation)),
        decision_rule_sha256=_sha256_bytes(canonical_json_bytes(manifest.decision_rule)),
        private_input_sha256=manifest.dataset.private_input_sha256,
        dataset_content_fingerprint=manifest.dataset.dataset_content_fingerprint,
        group_set_fingerprint=manifest.dataset.group_set_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        baseline_prompt_fingerprint=manifest.generation.baseline_prompt_fingerprint,
        repair_prompt_fingerprint=manifest.generation.repair_prompt_fingerprint,
        repair_response_schema_fingerprint=(manifest.generation.repair_response_schema_fingerprint),
        execution_schedule_fingerprint=manifest.generation.execution_schedule_fingerprint,
        independence=manifest.independence,
        generation=manifest.generation,
        decision_rule=manifest.decision_rule,
        prelive_commit_required=True,
        one_shot_attempt_marker_required=True,
        model_call_count=_MODEL_CALL_COUNT,
        gold_v2_access_allowed=False,
        raw_content_persistence_allowed=False,
    )


def load_frozen_rag88_experiment_manifest(
    lock_path: Path,
    private_input_path: Path,
) -> tuple[Rag88ExperimentManifest, Rag88PrivateFixtureEnvelope]:
    lock_bytes, lock_payload = read_json_object(lock_path)
    lock = Rag88ExperimentLock.model_validate(lock_payload)
    if not model_bytes_match(lock_bytes, lock):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_lock_model_bytes_mismatch")
    private_bytes, envelope = load_rag88_private_fixture(private_input_path)
    private_sha = _sha256_bytes(private_bytes)
    if private_sha != lock.private_input_sha256:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_private_input_sha_mismatch")
    manifest = build_rag88_experiment_manifest(
        envelope,
        private_input_sha256=private_sha,
        independence=lock.independence,
        stacked_base_commit=lock.stacked_base_commit,
    )
    rebuilt_lock = build_rag88_experiment_lock(manifest)
    if rebuilt_lock != lock:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_frozen_lock_drift")
    return manifest, envelope


def build_rag88_attempt_state(
    manifest: Rag88ExperimentManifest,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag86LMInventorySummary,
) -> Rag88AttemptState:
    _validate_git_sha(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    if (
        pre_lm_inventory.full_inventory_fingerprint is None
        or pre_lm_inventory.target_entry_fingerprint is None
    ):
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_pre_lm_inventory_fingerprint_unavailable"
        )
    return Rag88AttemptState(
        schema_version="phase3.rag88_interference_repair_attempt.v1",
        status="started",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        private_input_sha256=manifest.dataset.private_input_sha256,
        prelive_commit_sha=prelive_commit_sha,
        pre_full_lm_inventory_fingerprint=(pre_lm_inventory.full_inventory_fingerprint),
        pre_target_entry_fingerprint=pre_lm_inventory.target_entry_fingerprint,
        expected_model_call_count=_MODEL_CALL_COUNT,
        repeat_replacement_or_rerun_allowed=False,
        raw_content_persisted=False,
    )


def _private_code(
    private_entropy: bytes,
    ordinal: int,
    label: str,
    prefix: str,
) -> str:
    digest = (
        hashlib.sha256(private_entropy + f"|{_PRIVATE_NAMESPACE}|{ordinal}|{label}".encode())
        .hexdigest()[:8]
        .upper()
    )
    return f"{prefix}-{digest}" if prefix else digest


def _group_statements(
    *,
    language: Literal["ja", "en"],
    unit: str,
    other_one: str,
    other_two: str,
    codes: tuple[str, str, str, str, str, str],
) -> tuple[str, str, str, str, str, str]:
    if language == "ja":
        return (
            f"管理カプセル {other_one} の封止材コードは {codes[0]} である。",
            f"管理カプセル {unit} の封止材コードは {codes[1]} である。",
            f"管理カプセル {unit} の輸送封印コードは {codes[2]} である。",
            f"管理カプセル {other_two} の換気監査枠は {codes[3]} である。",
            f"管理カプセル {unit} の換気監査枠は {codes[4]} である。",
            f"管理カプセル {unit} の照明監査枠は {codes[5]} である。",
        )
    return (
        f"Custody capsule {other_one} has seal-material code {codes[0]}.",
        f"Custody capsule {unit} has seal-material code {codes[1]}.",
        f"Custody capsule {unit} has transport-seal code {codes[2]}.",
        f"Custody capsule {other_two} has ventilation-audit slot {codes[3]}.",
        f"Custody capsule {unit} has ventilation-audit slot {codes[4]}.",
        f"Custody capsule {unit} has lighting-audit slot {codes[5]}.",
    )


def _group_questions(
    *,
    language: Literal["ja", "en"],
    unit: str,
) -> tuple[str, str, str]:
    if language == "ja":
        return (
            f"管理カプセル {unit} の封止材コードを、根拠の引用とともに答えてください。",
            f"管理カプセル {unit} の換気監査枠を、根拠の引用とともに答えてください。",
            (
                f"管理カプセル {unit} の封止材コードと換気監査枠を、"
                "それぞれの根拠の引用とともに答えてください。"
            ),
        )
    return (
        f"What is the seal-material code for custody capsule {unit}? Cite the evidence.",
        f"What is the ventilation-audit slot for custody capsule {unit}? Cite the evidence.",
        (
            "Give both the seal-material code and ventilation-audit slot for custody "
            f"capsule {unit}, citing the evidence for each."
        ),
    )


def _source_title(
    *,
    language: Literal["ja", "en"],
    group_id: str,
    source_ordinal: int,
) -> str:
    if language == "ja":
        return f"管理台帳 {group_id} 記録 {source_ordinal}"
    return f"Custody register {group_id} record {source_ordinal}"


def _source_body(
    *,
    language: Literal["ja", "en"],
    statement: str,
    group_id: str,
    source_ordinal: int,
) -> str:
    if language == "ja":
        return (
            f"{statement} この記録は {group_id} の保守台帳に属する。"
            f"項目 {source_ordinal} は独立確認済みであり、他の台帳項目を置き換えない。"
        )
    return (
        f"{statement} This record belongs to maintenance register {group_id}. "
        f"Entry {source_ordinal} was independently checked and does not replace other "
        "register entries."
    )


def _validate_private_fixture(envelope: Rag88PrivateFixtureEnvelope) -> None:
    dataset = envelope.dataset
    if dataset.dataset.dataset_name != _DATASET:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_dataset_name_drift")
    if (
        len(envelope.groups),
        len(dataset.cases),
        len(dataset.corpus_documents),
        sum(len(item.facts) for item in dataset.corpus_documents),
    ) != (_GROUP_COUNT, _CASE_COUNT, _SOURCE_COUNT, _FACT_COUNT):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_fixture_shape_drift")
    cases = {item.case_key: item for item in dataset.cases}
    documents = {item.source_key: item for item in dataset.corpus_documents}
    if len(cases) != _CASE_COUNT or len(documents) != _SOURCE_COUNT:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_fixture_identity_drift")
    seen_case_ids: set[str] = set()
    seen_source_ids: set[str] = set()
    for group in sorted(envelope.groups, key=lambda item: item.group_id):
        group_case_ids = (
            group.single_a_case_id,
            group.single_b_case_id,
            group.combined_case_id,
        )
        if seen_case_ids.intersection(group_case_ids):
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_group_case_reuse")
        seen_case_ids.update(group_case_ids)
        if seen_source_ids.intersection(group.source_ids):
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_group_source_reuse")
        seen_source_ids.update(group.source_ids)
        if group.required_source_ids != (group.source_ids[1], group.source_ids[4]):
            raise EvaluationQwenMultifactInterferenceRepairError(
                "rag88_required_source_position_drift"
            )
        try:
            single_a, single_b, combined = (cases[key] for key in group_case_ids)
            bodies = tuple(" ".join(documents[key].body.split()) for key in group.source_ids)
        except KeyError as exc:
            raise EvaluationQwenMultifactInterferenceRepairError(
                "rag88_group_binding_missing"
            ) from exc
        if (
            len(single_a.required_facts),
            len(single_b.required_facts),
            len(combined.required_facts),
        ) != (1, 1, 2):
            raise EvaluationQwenMultifactInterferenceRepairError(
                "rag88_variant_required_fact_shape_drift"
            )
        if (
            single_a.required_facts[0] != combined.required_facts[0]
            or single_b.required_facts[0] != combined.required_facts[1]
        ):
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_variant_fact_binding_drift")
        if sum(len(item) for item in bodies) > _MAX_CONTEXT_CHARS:
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_context_budget_exceeded")
    if len(seen_case_ids) != _CASE_COUNT or len(seen_source_ids) != _SOURCE_COUNT:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_fixture_coverage_drift")


def _reference_sets(
    manifest: EvaluationDatasetManifestV2,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(sorted({_sha256(case.question) for case in manifest.cases})),
        tuple(
            sorted(
                {
                    _sha256(_normalize_identifier_text(fact.statement))
                    for case in manifest.cases
                    for fact in case.required_facts
                }
            )
        ),
        tuple(sorted({_sha256(item.body) for item in manifest.corpus_documents})),
        tuple(sorted({item.source_key for item in manifest.corpus_documents})),
    )


def _build_dataset_binding(
    envelope: Rag88PrivateFixtureEnvelope,
    *,
    private_input_sha256: str,
) -> Rag88DatasetBinding:
    dataset = envelope.dataset
    documents = {item.source_key: item for item in dataset.corpus_documents}
    cases = {item.case_key: item for item in dataset.cases}
    group_bindings: list[Rag88GroupBinding] = []
    all_case_ids: list[str] = []
    all_question_hashes: list[str] = []
    all_fact_hashes: list[str] = []
    all_source_hashes: list[str] = []
    all_source_ids: list[str] = []
    all_context_hashes: list[str] = []
    for group in sorted(envelope.groups, key=lambda item: item.group_id):
        single_a = cases[group.single_a_case_id]
        single_b = cases[group.single_b_case_id]
        combined = cases[group.combined_case_id]
        bodies = tuple(" ".join(documents[key].body.split()) for key in group.source_ids)
        source_hashes = tuple(_sha256(documents[key].body) for key in group.source_ids)
        question_hashes = (
            _sha256(single_a.question),
            _sha256(single_b.question),
            _sha256(combined.question),
        )
        fact_hashes = tuple(
            _sha256(_normalize_identifier_text(item.statement)) for item in combined.required_facts
        )
        context_hash = _sha256("\n".join(bodies))
        group_bindings.append(
            Rag88GroupBinding(
                group_id=group.group_id,
                language=group.language,
                single_a_case_id=group.single_a_case_id,
                single_b_case_id=group.single_b_case_id,
                combined_case_id=group.combined_case_id,
                single_a_question_hash=question_hashes[0],
                single_b_question_hash=question_hashes[1],
                combined_question_hash=question_hashes[2],
                required_fact_ids=(
                    combined.required_facts[0].fact_id,
                    combined.required_facts[1].fact_id,
                ),
                normalized_fact_hashes=(fact_hashes[0], fact_hashes[1]),
                source_ids=group.source_ids,
                required_source_ids=group.required_source_ids,
                required_citation_ids=(2, 5),
                source_content_hashes=(
                    source_hashes[0],
                    source_hashes[1],
                    source_hashes[2],
                    source_hashes[3],
                    source_hashes[4],
                    source_hashes[5],
                ),
                source_set_hash=_fingerprint_set(source_hashes),
                context_hash=context_hash,
                context_length=sum(len(item) for item in bodies),
                source_order_frozen=True,
                same_oracle_context_all_variants=True,
            )
        )
        all_case_ids.extend(
            (group.single_a_case_id, group.single_b_case_id, group.combined_case_id)
        )
        all_question_hashes.extend(question_hashes)
        all_fact_hashes.extend(fact_hashes)
        all_source_hashes.extend(source_hashes)
        all_source_ids.extend(group.source_ids)
        all_context_hashes.append(context_hash)
    return Rag88DatasetBinding(
        dataset_name=_DATASET,
        private_input_sha256=private_input_sha256,
        dataset_content_fingerprint=_sha256_bytes(canonical_json_bytes(dataset)),
        corpus_fingerprint=_sha256_bytes(
            canonical_json_bytes(
                {
                    "corpus_documents": [
                        item.model_dump(mode="json") for item in dataset.corpus_documents
                    ]
                }
            )
        ),
        group_count=_GROUP_COUNT,
        case_count=_CASE_COUNT,
        source_count=_SOURCE_COUNT,
        corpus_fact_count=_FACT_COUNT,
        required_fact_count=_REQUIRED_FACT_COUNT,
        group_set_fingerprint=_fingerprint_set(tuple(item.group_id for item in group_bindings)),
        case_set_fingerprint=_fingerprint_set(all_case_ids),
        question_set_fingerprint=_fingerprint_set(all_question_hashes),
        normalized_fact_set_fingerprint=_fingerprint_set(all_fact_hashes),
        source_content_set_fingerprint=_fingerprint_set(all_source_hashes),
        logical_document_set_fingerprint=_fingerprint_set(all_source_ids),
        source_context_fingerprint=_fingerprint_set(all_context_hashes),
        groups=tuple(group_bindings),
    )


def _build_independence_proof(
    dataset: Rag88DatasetBinding,
    catalog: Rag88ReferenceCatalog,
) -> Rag88IndependenceProof:
    new_questions = {
        value
        for item in dataset.groups
        for value in (
            item.single_a_question_hash,
            item.single_b_question_hash,
            item.combined_question_hash,
        )
    }
    new_facts = {value for item in dataset.groups for value in item.normalized_fact_hashes}
    new_sources = {value for item in dataset.groups for value in item.source_content_hashes}
    new_ids = {value for item in dataset.groups for value in item.source_ids}
    reference_sets = (
        (
            set(catalog.local_accuracy_dev_question_hashes),
            set(catalog.local_accuracy_dev_normalized_fact_hashes),
            set(catalog.local_accuracy_dev_source_content_hashes),
            set(catalog.local_accuracy_dev_logical_document_ids),
        ),
        (
            set(catalog.rag84_confirm_question_hashes),
            set(catalog.rag84_confirm_normalized_fact_hashes),
            set(catalog.rag84_confirm_source_content_hashes),
            set(catalog.rag84_confirm_logical_document_ids),
        ),
        (
            set(catalog.rag87_question_hashes),
            set(catalog.rag87_normalized_fact_hashes),
            set(catalog.rag87_source_content_hashes),
            set(catalog.rag87_logical_document_ids),
        ),
    )
    overlaps = tuple(
        (
            len(new_questions & question_hashes),
            len(new_facts & fact_hashes),
            len(new_sources & source_hashes),
            len(new_ids & logical_ids),
        )
        for question_hashes, fact_hashes, source_hashes, logical_ids in reference_sets
    )
    if overlaps != ((0, 0, 0, 0),) * 3:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_reference_independence_drift")
    return Rag88IndependenceProof(
        fixture_dataset=_DATASET,
        local_accuracy_dev_question_overlap_count=0,
        local_accuracy_dev_normalized_fact_overlap_count=0,
        local_accuracy_dev_source_content_overlap_count=0,
        local_accuracy_dev_logical_document_id_overlap_count=0,
        rag84_question_overlap_count=0,
        rag84_normalized_fact_overlap_count=0,
        rag84_source_content_overlap_count=0,
        rag84_logical_document_id_overlap_count=0,
        rag87_question_overlap_count=0,
        rag87_normalized_fact_overlap_count=0,
        rag87_source_content_overlap_count=0,
        rag87_logical_document_id_overlap_count=0,
        reference_catalog_fingerprint=catalog.catalog_fingerprint,
        private_fixture_generation_seed=_PRIVATE_SEED,
        private_fixture_namespace=_PRIVATE_NAMESPACE,
        reference_content_used_for_case_design=False,
        reference_content_used_only_for_one_way_hash_check=True,
        gold_v2_opened=False,
        result_based_case_selection_allowed=False,
        failed_case_replacement_allowed=False,
        post_result_threshold_or_fixture_change_allowed=False,
    )


def _execution_schedule(
    groups: Sequence[Rag88GroupBinding],
) -> tuple[tuple[int, int, str, Variant], ...]:
    ordered = tuple(sorted(groups, key=lambda item: item.group_id))
    if len(ordered) != _GROUP_COUNT:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_schedule_group_count_drift")
    schedule: list[tuple[int, int, str, Variant]] = []
    execution_ordinal = 0
    for repeat, offset in enumerate(_LATIN_ROTATION_OFFSETS, start=1):
        rotated = ordered[offset:] + ordered[:offset]
        for position, group in enumerate(rotated):
            single_variants: tuple[Variant, Variant] = (
                ("single_a", "single_b")
                if (position + repeat) % 2 == 0
                else ("single_b", "single_a")
            )
            for variant in (*single_variants, "combined_baseline", "combined_candidate"):
                execution_ordinal += 1
                schedule.append((execution_ordinal, repeat, group.group_id, variant))
    if len(schedule) != _MODEL_CALL_COUNT:
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_execution_schedule_cardinality_drift"
        )
    return tuple(schedule)


def _execution_schedule_fingerprint(groups: Sequence[Rag88GroupBinding]) -> str:
    schedule = [
        {
            "execution_ordinal": execution_ordinal,
            "repeat": repeat,
            "group_id": group_id,
            "variant": variant,
        }
        for execution_ordinal, repeat, group_id, variant in _execution_schedule(groups)
    ]
    return _sha256_bytes(canonical_json_bytes({"schedule": schedule}))


def run_rag88_experiment(
    manifest: Rag88ExperimentManifest,
    envelope: Rag88PrivateFixtureEnvelope,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag86LMInventorySummary,
    post_lm_inventory_provider: Callable[[], Rag86LMInventorySummary],
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    capture_physical_telemetry: bool = False,
    phase_telemetry_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag88ExperimentResult:
    _validate_git_sha(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    runtime_manifest = build_rag88_experiment_manifest(
        envelope,
        private_input_sha256=manifest.dataset.private_input_sha256,
        independence=manifest.independence,
        stacked_base_commit=manifest.stacked_base_commit,
        case_timeout_seconds=(manifest.generation.generation_case_wall_clock_timeout_seconds),
    )
    if runtime_manifest != manifest:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_runtime_manifest_drift")
    dataset = envelope.dataset
    cases = {item.case_key: item for item in dataset.cases}
    documents = {item.source_key: item for item in dataset.corpus_documents}
    document_ids = {
        item.source_key: index
        for index, item in enumerate(
            sorted(dataset.corpus_documents, key=lambda item: item.source_key),
            start=1,
        )
    }
    groups = {item.group_id: item for item in envelope.groups}
    bindings = {item.group_id: item for item in manifest.dataset.groups}
    if _execution_schedule_fingerprint(manifest.dataset.groups) != (
        manifest.generation.execution_schedule_fingerprint
    ):
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_execution_schedule_binding_drift"
        )
    observations: list[Rag88VariantObservation] = []
    baseline_materials: dict[tuple[int, str], tuple[_AnswerMaterial, int]] = {}
    for execution_ordinal, repeat, group_id, variant in _execution_schedule(
        manifest.dataset.groups
    ):
        group = groups.get(group_id)
        binding = bindings.get(group_id)
        if group is None or binding is None:
            raise EvaluationQwenMultifactInterferenceRepairError(
                "rag88_runtime_group_binding_missing"
            )
        context_items, citation_sources, context_hash = _prepare_group_context(
            group,
            documents=documents,
            document_ids=document_ids,
        )
        if context_hash != binding.context_hash:
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_context_binding_drift")
        combined_case = cases[group.combined_case_id]
        if variant == "combined_candidate":
            baseline_entry = baseline_materials.get((repeat, group_id))
            if baseline_entry is None:
                raise EvaluationQwenMultifactInterferenceRepairError(
                    "rag88_paired_baseline_pass1_missing"
                )
            baseline_material, baseline_latency_ms = baseline_entry
            observation = _run_candidate_variant(
                group=group,
                combined_case=combined_case,
                context_items=context_items,
                citation_sources=citation_sources,
                context_hash=context_hash,
                baseline_material=baseline_material,
                baseline_latency_ms=baseline_latency_ms,
                repeat=repeat,
                execution_ordinal=execution_ordinal,
                generator=generator,
                case_timeout_seconds=(
                    manifest.generation.generation_case_wall_clock_timeout_seconds
                ),
                capture_physical_telemetry=capture_physical_telemetry,
                phase_telemetry_callback=phase_telemetry_callback,
            )
        else:
            case_id = {
                "single_a": group.single_a_case_id,
                "single_b": group.single_b_case_id,
                "combined_baseline": group.combined_case_id,
            }[variant]
            observation, material = _run_standard_variant(
                variant=variant,
                case=cases[case_id],
                combined_case=combined_case,
                context_items=context_items,
                citation_sources=citation_sources,
                context_hash=context_hash,
                repeat=repeat,
                execution_ordinal=execution_ordinal,
                generator=generator,
                case_timeout_seconds=(
                    manifest.generation.generation_case_wall_clock_timeout_seconds
                ),
                capture_physical_telemetry=capture_physical_telemetry,
                phase_telemetry_callback=phase_telemetry_callback,
            )
            if variant == "combined_baseline":
                baseline_materials[(repeat, group_id)] = (
                    material,
                    observation.latency_ms,
                )
        observations.append(observation)
        if progress_callback is not None:
            progress_callback(
                {
                    "status": "rag88_generation_progress",
                    "completed_model_call_count": execution_ordinal,
                    "expected_model_call_count": _MODEL_CALL_COUNT,
                    "repeat": repeat,
                    "variant": variant,
                    "pipeline_failure_count": sum(
                        item.pipeline_failure_reason_code is not None for item in observations
                    ),
                }
            )
    if len(observations) != _VARIANT_OBSERVATION_COUNT:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_observation_count_drift")
    try:
        post_inventory = post_lm_inventory_provider()
    except Exception:
        post_inventory = Rag86LMInventorySummary(available=False)
    summary = _summarize_observations(observations)
    exact_target_failures = _exact_target_validity_failures(
        pre_lm_inventory,
        post_inventory,
    )
    validity_failures = (
        *(("rag88_pipeline_failure",) if summary.pipeline_failure_count else ()),
        *exact_target_failures,
    )
    exact_target_stable = not exact_target_failures
    full_inventory_stable = bool(
        pre_lm_inventory.available
        and post_inventory.available
        and pre_lm_inventory.full_inventory_fingerprint == post_inventory.full_inventory_fingerprint
    )
    baseline_checks = _baseline_sensitivity_checks(summary)
    sensitivity_passed = not exact_target_failures and baseline_checks.sensitivity_gate_passed
    candidate_checks = _candidate_adoption_checks(
        summary,
        baseline_sensitivity_passed=sensitivity_passed,
    )
    validity_passed = not validity_failures
    candidate_adopted = validity_passed and candidate_checks.adoption_gate_passed
    if not validity_passed:
        conclusion: Conclusion = "inconclusive"
        reason_codes = validity_failures
    elif not sensitivity_passed:
        conclusion = "baseline_sensitivity_not_established"
        reason_codes = _failed_baseline_reason_codes(baseline_checks)
    elif candidate_adopted:
        conclusion = "candidate_adopted"
        reason_codes = ("rag88_candidate_adoption_gate_passed",)
    else:
        conclusion = "candidate_rejected"
        reason_codes = _failed_candidate_reason_codes(candidate_checks)
    return Rag88ExperimentResult(
        schema_version="phase3.rag88_interference_repair_result.v1",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        private_input_sha256=manifest.dataset.private_input_sha256,
        prelive_commit_sha=prelive_commit_sha,
        dataset_name=_DATASET,
        dataset_content_fingerprint=manifest.dataset.dataset_content_fingerprint,
        group_set_fingerprint=manifest.dataset.group_set_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        model=_MODEL,
        model_call_count=_MODEL_CALL_COUNT,
        variant_observation_count=_VARIANT_OBSERVATION_COUNT,
        case_exclusion_count=0,
        case_replacement_count=0,
        binding_drift_count=0,
        pipeline_failure_count=summary.pipeline_failure_count,
        pre_lm_inventory=pre_lm_inventory,
        post_lm_inventory=post_inventory,
        exact_target_stable=exact_target_stable,
        full_lm_inventory_stable=full_inventory_stable,
        non_target_inventory_drift_observed=(exact_target_stable and not full_inventory_stable),
        validity_gate_passed=validity_passed,
        baseline_sensitivity_gate_passed=sensitivity_passed,
        candidate_adoption_gate_passed=candidate_adopted,
        candidate_metrics_descriptive_only=not sensitivity_passed,
        conclusion=conclusion,
        reason_codes=reason_codes,
        summary=summary,
        baseline_sensitivity_checks=baseline_checks,
        candidate_adoption_checks=candidate_checks,
        evaluator="deterministic_identifier_equivalence_v1",
        diagnostic_only=True,
        gold_holdout_eligible=False,
        public_accuracy_eligible=False,
        profile_promotion_eligible=False,
        baseline_retained=not candidate_adopted,
        raw_content_persisted=False,
        chain_of_thought_persisted=False,
        observations=tuple(observations),
    )


def _prepare_group_context(
    group: Rag88PrivateGroup,
    *,
    documents: Mapping[str, EvaluationCorpusDocumentSpec],
    document_ids: Mapping[str, int],
) -> tuple[tuple[GenerationContextItem, ...], tuple[CitationSource, ...], str]:
    remaining: int = _MAX_CONTEXT_CHARS
    context_items: list[GenerationContextItem] = []
    citation_sources: list[CitationSource] = []
    for local_citation_id, source_id in enumerate(group.source_ids, start=1):
        try:
            document = documents[source_id]
            document_id = document_ids[source_id]
        except KeyError as exc:
            raise EvaluationQwenMultifactInterferenceRepairError(
                "rag88_context_source_unbound"
            ) from exc
        normalized = " ".join(document.body.split())
        if not normalized or len(normalized) > remaining:
            raise EvaluationQwenMultifactInterferenceRepairError(
                "rag88_context_budget_or_content_drift"
            )
        remaining -= len(normalized)
        item = GenerationContextItem(
            document_chunk_id=document_id,
            source_label=source_id,
            text=normalized,
            local_citation_id=local_citation_id,
        )
        context_items.append(item)
        citation_sources.append(
            CitationSource(
                local_citation_id=local_citation_id,
                retrieval_run_item_id=document_id,
                document_chunk_id=document_id,
                source_label=source_id,
                snippet=normalized,
                page_from=None,
                page_to=None,
                section_title=document.title,
            )
        )
    context_hash = _sha256("\n".join(item.text for item in context_items))
    return tuple(context_items), tuple(citation_sources), context_hash


def _emit_phase_telemetry(
    callback: Callable[[dict[str, object]], None] | None,
    *,
    execution_ordinal: int,
    repeat: int,
    variant: Variant,
    logical_latency_ms: int,
    physical_request_latencies_ms: tuple[int, ...],
    physical_request_timeout_count: int,
    derived_from_paired_baseline: bool,
) -> None:
    if callback is None:
        return
    callback(
        {
            "execution_ordinal": execution_ordinal,
            "repeat": repeat,
            "variant": variant,
            "logical_latency_ms": logical_latency_ms,
            "physical_request_latencies_ms": physical_request_latencies_ms,
            "physical_request_count": len(physical_request_latencies_ms)
            + physical_request_timeout_count,
            "physical_request_timeout_count": physical_request_timeout_count,
            "derived_from_paired_baseline": derived_from_paired_baseline,
        }
    )


def _run_standard_variant(
    *,
    variant: Literal["single_a", "single_b", "combined_baseline"],
    case: EvaluationCaseV2Spec,
    combined_case: EvaluationCaseV2Spec,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
    context_hash: str,
    repeat: int,
    execution_ordinal: int,
    generator: AnswerGenerator | None,
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
    phase_telemetry_callback: Callable[[dict[str, object]], None] | None,
) -> tuple[Rag88VariantObservation, _AnswerMaterial]:
    started = time.perf_counter()
    material = _generate_standard_answer(
        case=case,
        context_items=context_items,
        citation_sources=citation_sources,
        generator=generator,
        case_timeout_seconds=case_timeout_seconds,
        capture_physical_telemetry=capture_physical_telemetry,
    )
    latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    _emit_phase_telemetry(
        phase_telemetry_callback,
        execution_ordinal=execution_ordinal,
        repeat=repeat,
        variant=variant,
        logical_latency_ms=latency_ms,
        physical_request_latencies_ms=material.physical_request_latencies_ms,
        physical_request_timeout_count=material.physical_request_timeout_count,
        derived_from_paired_baseline=False,
    )
    if (
        material.reason_code is not None
        or material.answer_text is None
        or material.answer_outcome is None
    ):
        return (
            _failed_observation(
                group_id=_group_id_from_case(case.case_key),
                repeat=repeat,
                variant=variant,
                execution_ordinal=execution_ordinal,
                context_hash=context_hash,
                latency_ms=latency_ms,
                reason_code=material.reason_code or "rag88_generation_worker_failed",
            ),
            material,
        )
    return (
        _evaluate_answer_observation(
            group_id=_group_id_from_case(case.case_key),
            repeat=repeat,
            variant=variant,
            execution_ordinal=execution_ordinal,
            question=case.question,
            combined_case=combined_case,
            context_items=context_items,
            context_hash=context_hash,
            material=material,
            latency_ms=latency_ms,
        ),
        material,
    )


def _run_candidate_variant(
    *,
    group: Rag88PrivateGroup,
    combined_case: EvaluationCaseV2Spec,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
    context_hash: str,
    baseline_material: _AnswerMaterial,
    baseline_latency_ms: int,
    repeat: int,
    execution_ordinal: int,
    generator: AnswerGenerator | None,
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
    phase_telemetry_callback: Callable[[dict[str, object]], None] | None,
) -> Rag88VariantObservation:
    if (
        baseline_material.reason_code is not None
        or baseline_material.answer_text is None
        or baseline_material.answer_outcome is None
    ):
        _emit_phase_telemetry(
            phase_telemetry_callback,
            execution_ordinal=execution_ordinal,
            repeat=repeat,
            variant="combined_candidate",
            logical_latency_ms=baseline_latency_ms,
            physical_request_latencies_ms=(),
            physical_request_timeout_count=0,
            derived_from_paired_baseline=True,
        )
        return _failed_observation(
            group_id=group.group_id,
            repeat=repeat,
            variant="combined_candidate",
            execution_ordinal=execution_ordinal,
            context_hash=context_hash,
            latency_ms=baseline_latency_ms,
            reason_code="rag88_candidate_pass1_unavailable",
        )
    started = time.perf_counter()
    repair = _generate_repair(
        question=combined_case.question,
        context_items=context_items,
        pass1_answer=baseline_material.answer_text,
        generator=generator,
        case_timeout_seconds=case_timeout_seconds,
        capture_physical_telemetry=capture_physical_telemetry,
    )
    repair_latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    total_latency_ms = baseline_latency_ms + repair_latency_ms
    _emit_phase_telemetry(
        phase_telemetry_callback,
        execution_ordinal=execution_ordinal,
        repeat=repeat,
        variant="combined_candidate",
        logical_latency_ms=total_latency_ms,
        physical_request_latencies_ms=repair.physical_request_latencies_ms,
        physical_request_timeout_count=repair.physical_request_timeout_count,
        derived_from_paired_baseline=False,
    )
    if repair.reason_code is not None or repair.payload is None:
        return _failed_observation(
            group_id=group.group_id,
            repeat=repeat,
            variant="combined_candidate",
            execution_ordinal=execution_ordinal,
            context_hash=context_hash,
            latency_ms=total_latency_ms,
            reason_code=repair.reason_code or "rag88_repair_worker_failed",
            baseline_pass1_answer_hash=_sha256(baseline_material.answer_text),
        )
    candidate_material = _candidate_answer_material(
        repair.payload,
        baseline=baseline_material,
        context_items=context_items,
        citation_sources=citation_sources,
    )
    if (
        candidate_material.reason_code is not None
        or candidate_material.answer_text is None
        or candidate_material.answer_outcome is None
    ):
        return _failed_observation(
            group_id=group.group_id,
            repeat=repeat,
            variant="combined_candidate",
            execution_ordinal=execution_ordinal,
            context_hash=context_hash,
            latency_ms=total_latency_ms,
            reason_code=(candidate_material.reason_code or "rag88_repair_answer_invalid"),
            baseline_pass1_answer_hash=_sha256(baseline_material.answer_text),
        )
    return _evaluate_answer_observation(
        group_id=group.group_id,
        repeat=repeat,
        variant="combined_candidate",
        execution_ordinal=execution_ordinal,
        question=combined_case.question,
        combined_case=combined_case,
        context_items=context_items,
        context_hash=context_hash,
        material=candidate_material,
        latency_ms=total_latency_ms,
        baseline_pass1_answer_hash=_sha256(baseline_material.answer_text),
        repair_latency_ms=repair_latency_ms,
        repair_payload=repair.payload,
    )


def _generate_standard_answer(
    *,
    case: EvaluationCaseV2Spec,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
    generator: AnswerGenerator | None,
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
) -> _AnswerMaterial:
    try:
        if generator is None and not capture_physical_telemetry and case_timeout_seconds == 180:
            generated = _generate_review_case_with_hard_timeout(
                question=case.question,
                context_items=context_items,
                citation_sources=citation_sources,
                system_instructions=None,
            )
            return _AnswerMaterial(
                answer_text=generated.answer_text,
                answer_outcome=generated.answer_outcome,
                citation_ids=generated.citation_ids,
                reason_code=(
                    f"rag88_{generated.reason_code}" if generated.reason_code is not None else None
                ),
            )
        if generator is None:
            return _generate_standard_answer_with_hard_timeout(
                case=case,
                context_items=context_items,
                citation_sources=citation_sources,
                case_timeout_seconds=case_timeout_seconds,
                capture_physical_telemetry=capture_physical_telemetry,
            )
        timed_generator = _TimedAnswerGenerator(generator) if capture_physical_telemetry else None
        effective_generator = timed_generator or generator
        generation = _generate_oracle_answer(
            _review_generation_settings(),
            generator=effective_generator,
            question=case.question,
            context_items=context_items,
            citation_sources=citation_sources,
            system_instructions=None,
        )
        return _AnswerMaterial(
            answer_text=generation.answer_text,
            answer_outcome=generation.answer_outcome,
            citation_ids=tuple(
                sorted(
                    {
                        citation_id
                        for citation in generation.citations
                        if isinstance(
                            (citation_id := citation.get("local_citation_id")),
                            int,
                        )
                    }
                )
            ),
            physical_request_latencies_ms=(
                tuple(timed_generator.latencies_ms) if timed_generator is not None else ()
            ),
        )
    except AnswerGenerationError as exc:
        return _AnswerMaterial(reason_code=f"rag88_generation_{exc.error_category or 'failed'}")
    except CitationBuildError as exc:
        return _AnswerMaterial(reason_code=f"rag88_generation_{exc.detail_code}")
    except Exception:
        return _AnswerMaterial(reason_code="rag88_generation_unexpected_error")


def _standard_generation_worker(
    send_connection: Connection,
    case: EvaluationCaseV2Spec,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
) -> None:
    try:
        generator = OpenAICompatibleChatAnswerGenerator(
            api_key="lm-studio",
            base_url="http://127.0.0.1:1234",
            model_name=_MODEL,
            timeout_seconds=case_timeout_seconds,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        result = _generate_standard_answer(
            case=case,
            context_items=context_items,
            citation_sources=citation_sources,
            generator=generator,
            case_timeout_seconds=case_timeout_seconds,
            capture_physical_telemetry=capture_physical_telemetry,
        )
    except Exception:
        result = _AnswerMaterial(reason_code="rag88_generation_unexpected_error")
    try:
        send_connection.send(result)
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        send_connection.close()


def _generate_standard_answer_with_hard_timeout(
    *,
    case: EvaluationCaseV2Spec,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
) -> _AnswerMaterial:
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_standard_generation_worker,
        args=(
            send_connection,
            case,
            context_items,
            citation_sources,
            case_timeout_seconds,
            capture_physical_telemetry,
        ),
        daemon=True,
    )
    try:
        process.start()
        send_connection.close()
        process.join(case_timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(_PROCESS_TERMINATE_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_PROCESS_KILL_GRACE_SECONDS)
            return _AnswerMaterial(
                reason_code="rag88_review_generation_case_wall_clock_timeout",
                physical_request_timeout_count=1,
            )
        if process.exitcode != 0 or not receive_connection.poll(1.0):
            return _AnswerMaterial(reason_code="rag88_generation_worker_failed")
        try:
            result = receive_connection.recv()
        except (EOFError, OSError):
            return _AnswerMaterial(reason_code="rag88_generation_worker_failed")
        if not isinstance(result, _AnswerMaterial):
            return _AnswerMaterial(reason_code="rag88_generation_worker_failed")
        return result
    except (OSError, RuntimeError):
        return _AnswerMaterial(reason_code="rag88_generation_worker_failed")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(_PROCESS_TERMINATE_GRACE_SECONDS)
        send_connection.close()
        receive_connection.close()


def _generate_repair(
    *,
    question: str,
    context_items: tuple[GenerationContextItem, ...],
    pass1_answer: str,
    generator: AnswerGenerator | None,
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
) -> _RepairMaterial:
    if generator is None:
        return _generate_repair_with_hard_timeout(
            question=question,
            context_items=context_items,
            pass1_answer=pass1_answer,
            case_timeout_seconds=case_timeout_seconds,
            capture_physical_telemetry=capture_physical_telemetry,
        )
    timed_generator = _TimedAnswerGenerator(generator) if capture_physical_telemetry else None
    result = _generate_repair_with_generator(
        timed_generator or generator,
        question=question,
        context_items=context_items,
        pass1_answer=pass1_answer,
    )
    if timed_generator is None:
        return result
    return _RepairMaterial(
        payload=result.payload,
        reason_code=result.reason_code,
        physical_request_latencies_ms=tuple(timed_generator.latencies_ms),
        physical_request_timeout_count=result.physical_request_timeout_count,
    )


def _generate_repair_with_generator(
    generator: AnswerGenerator,
    *,
    question: str,
    context_items: tuple[GenerationContextItem, ...],
    pass1_answer: str,
) -> _RepairMaterial:
    task = _repair_task(question=question, pass1_answer=pass1_answer)
    try:
        generation = generator.generate(
            GenerationRequest(
                message=question,
                context_items=context_items,
                max_output_chars=_MAX_OUTPUT_CHARS,
                system_instructions=_REPAIR_SYSTEM_INSTRUCTIONS,
                task_instructions=task,
                temperature=0.0,
                response_format=_REPAIR_RESPONSE_FORMAT,
            )
        )
        payload = _RepairDecisionPayload.model_validate(json.loads(generation.content))
        return _RepairMaterial(payload=payload)
    except AnswerGenerationError as exc:
        return _RepairMaterial(reason_code=f"rag88_repair_{exc.error_category or 'failed'}")
    except (ValueError, json.JSONDecodeError):
        return _RepairMaterial(reason_code="rag88_repair_schema_invalid")
    except Exception:
        return _RepairMaterial(reason_code="rag88_repair_unexpected_error")


def _repair_generation_worker(
    send_connection: Connection,
    question: str,
    context_items: tuple[GenerationContextItem, ...],
    pass1_answer: str,
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
) -> None:
    try:
        generator = OpenAICompatibleChatAnswerGenerator(
            api_key="lm-studio",
            base_url="http://127.0.0.1:1234",
            model_name=_MODEL,
            timeout_seconds=case_timeout_seconds,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        )
        timed_generator = _TimedAnswerGenerator(generator) if capture_physical_telemetry else None
        result = _generate_repair_with_generator(
            timed_generator or generator,
            question=question,
            context_items=context_items,
            pass1_answer=pass1_answer,
        )
        if timed_generator is not None:
            result = _RepairMaterial(
                payload=result.payload,
                reason_code=result.reason_code,
                physical_request_latencies_ms=tuple(timed_generator.latencies_ms),
                physical_request_timeout_count=result.physical_request_timeout_count,
            )
    except Exception:
        result = _RepairMaterial(reason_code="rag88_repair_unexpected_error")
    try:
        send_connection.send(result)
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        send_connection.close()


def _generate_repair_with_hard_timeout(
    *,
    question: str,
    context_items: tuple[GenerationContextItem, ...],
    pass1_answer: str,
    case_timeout_seconds: int,
    capture_physical_telemetry: bool,
) -> _RepairMaterial:
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=_repair_generation_worker,
        args=(
            send_connection,
            question,
            context_items,
            pass1_answer,
            case_timeout_seconds,
            capture_physical_telemetry,
        ),
        daemon=True,
    )
    try:
        process.start()
        send_connection.close()
        process.join(case_timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(_PROCESS_TERMINATE_GRACE_SECONDS)
            if process.is_alive():
                process.kill()
                process.join(_PROCESS_KILL_GRACE_SECONDS)
            return _RepairMaterial(
                reason_code="rag88_repair_wall_clock_timeout",
                physical_request_timeout_count=1,
            )
        if process.exitcode != 0 or not receive_connection.poll(1.0):
            return _RepairMaterial(reason_code="rag88_repair_worker_failed")
        try:
            result = receive_connection.recv()
        except (EOFError, OSError):
            return _RepairMaterial(reason_code="rag88_repair_worker_failed")
        if not isinstance(result, _RepairMaterial):
            return _RepairMaterial(reason_code="rag88_repair_worker_failed")
        return result
    except (OSError, RuntimeError):
        return _RepairMaterial(reason_code="rag88_repair_worker_failed")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(_PROCESS_TERMINATE_GRACE_SECONDS)
        send_connection.close()
        receive_connection.close()


def _repair_task(*, question: str, pass1_answer: str) -> str:
    return (
        "Inspect whether the first-pass answer covers every part explicitly requested by "
        "the user question using only the supplied evidence context. Also inspect whether "
        "it incorrectly claims insufficient evidence and whether factual statements use "
        "the matching shown citation markers. Do not use hidden expected answers, evaluator "
        "facts, evaluator fact identifiers, or outside knowledge. If the answer is complete "
        "and citations match, set decision to keep and revised_answer to the empty string. "
        "Otherwise set decision to revise and put one concise corrected final answer with "
        "citations in revised_answer. Do not return reasoning.\n\n"
        f"User question:\n{question}\n\nFirst-pass answer:\n{pass1_answer}"
    )


def _candidate_answer_material(
    payload: _RepairDecisionPayload,
    *,
    baseline: _AnswerMaterial,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
) -> _AnswerMaterial:
    if payload.decision == "keep":
        return baseline
    try:
        parsed = parse_generation_output(payload.revised_answer)
        if _is_insufficient_evidence_answer(parsed.answer_text):
            return _AnswerMaterial(
                answer_text=parsed.answer_text,
                answer_outcome="abstained",
                citation_ids=(),
            )
        _validate_generation_output_safety(
            parsed.answer_text,
            context_items=list(context_items),
        )
        cited_sources = validate_generation_citations(
            parsed,
            source_map=list(citation_sources),
        )
        return _AnswerMaterial(
            answer_text=parsed.answer_text,
            answer_outcome="answered",
            citation_ids=tuple(sorted({source.local_citation_id for source in cited_sources})),
        )
    except CitationBuildError as exc:
        return _AnswerMaterial(reason_code=f"rag88_repair_{exc.detail_code}")
    except Exception:
        return _AnswerMaterial(reason_code="rag88_repair_answer_invalid")


def _evaluate_answer_observation(
    *,
    group_id: str,
    repeat: int,
    variant: Variant,
    execution_ordinal: int,
    question: str,
    combined_case: EvaluationCaseV2Spec,
    context_items: tuple[GenerationContextItem, ...],
    context_hash: str,
    material: _AnswerMaterial,
    latency_ms: int,
    baseline_pass1_answer_hash: str | None = None,
    repair_latency_ms: int | None = None,
    repair_payload: _RepairDecisionPayload | None = None,
) -> Rag88VariantObservation:
    if material.answer_text is None or material.answer_outcome is None:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_answer_material_missing")
    selected_indices = {
        "single_a": (0,),
        "single_b": (1,),
        "combined_baseline": (0, 1),
        "combined_candidate": (0, 1),
    }[variant]
    answer_tokens = _identifier_tokens(material.answer_text)
    atomic_matches = [False, False]
    exact_matches = [False, False]
    citation_matches = [False, False]
    required_citation_ids = (2, 5)
    for fact_index in selected_indices:
        fact = combined_case.required_facts[fact_index]
        siblings = tuple(
            sibling.statement
            for sibling_index, sibling in enumerate(combined_case.required_facts)
            if sibling_index != fact_index
        )
        strong_tokens = _strong_fact_identifier_tokens(
            fact.statement,
            sibling_statements=siblings,
        )
        atomic_match = bool(strong_tokens) and strong_tokens.issubset(answer_tokens)
        exact_match = _whole_statement_exact_match(
            answer=material.answer_text,
            required_fact=fact.statement,
        )
        atomic_matches[fact_index] = atomic_match
        exact_matches[fact_index] = exact_match
        citation_matches[fact_index] = (
            atomic_match and required_citation_ids[fact_index] in material.citation_ids
        )
    context = tuple(item.text for item in context_items)
    allowed_identifiers = _identifier_tokens("\n".join((question, *context)))
    unexpected_identifiers = answer_tokens - allowed_identifiers
    normalized_answer = _normalize_identifier_text(material.answer_text)
    forbidden_count = sum(
        _normalize_identifier_text(claim) in normalized_answer
        for claim in combined_case.forbidden_claims
    )
    required_citations = {required_citation_ids[index] for index in selected_indices}
    return Rag88VariantObservation(
        group_id=group_id,
        repeat=repeat,
        variant=variant,
        execution_ordinal=execution_ordinal,
        answer_hash=_sha256(material.answer_text),
        baseline_pass1_answer_hash=baseline_pass1_answer_hash,
        context_hash=context_hash,
        atomic_fact_matches=(atomic_matches[0], atomic_matches[1]),
        exact_fact_matches=(exact_matches[0], exact_matches[1]),
        citation_grounded_fact_matches=(citation_matches[0], citation_matches[1]),
        joint_complete=all(atomic_matches[index] for index in selected_indices),
        joint_citation_grounded=all(citation_matches[index] for index in selected_indices),
        citation_source_coverage=required_citations.issubset(material.citation_ids),
        insufficiency_false_assertion=(
            material.answer_outcome == "abstained"
            or _contains_insufficiency_assertion(material.answer_text)
        ),
        unexpected_fact_count=len(unexpected_identifiers),
        forbidden_claim_count=forbidden_count,
        latency_ms=latency_ms,
        repair_latency_ms=repair_latency_ms,
        repair_decision=(repair_payload.decision if repair_payload else None),
        repair_coverage_status=(repair_payload.coverage_status if repair_payload else None),
        repair_incorrect_insufficiency_detected=(
            repair_payload.incorrect_insufficiency_detected if repair_payload else None
        ),
        repair_citation_status=(repair_payload.citation_status if repair_payload else None),
        revision_performed=(repair_payload.decision == "revise" if repair_payload else None),
        pipeline_failure_reason_code=None,
    )


def _failed_observation(
    *,
    group_id: str,
    repeat: int,
    variant: Variant,
    execution_ordinal: int,
    context_hash: str,
    latency_ms: int,
    reason_code: str,
    baseline_pass1_answer_hash: str | None = None,
) -> Rag88VariantObservation:
    return Rag88VariantObservation(
        group_id=group_id,
        repeat=repeat,
        variant=variant,
        execution_ordinal=execution_ordinal,
        answer_hash=None,
        baseline_pass1_answer_hash=baseline_pass1_answer_hash,
        context_hash=context_hash,
        atomic_fact_matches=(False, False),
        exact_fact_matches=(False, False),
        citation_grounded_fact_matches=(False, False),
        joint_complete=False,
        joint_citation_grounded=False,
        citation_source_coverage=False,
        insufficiency_false_assertion=False,
        unexpected_fact_count=0,
        forbidden_claim_count=0,
        latency_ms=latency_ms,
        pipeline_failure_reason_code=reason_code,
    )


def _summarize_observations(
    observations: Sequence[Rag88VariantObservation],
) -> Rag88ExperimentSummary:
    if len(observations) != _VARIANT_OBSERVATION_COUNT:
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_summary_observation_count_drift"
        )
    by_group_variant: dict[tuple[str, Variant], list[Rag88VariantObservation]] = {}
    for item in observations:
        by_group_variant.setdefault((item.group_id, item.variant), []).append(item)
    if len(by_group_variant) != _GROUP_COUNT * 4 or any(
        len(items) != _REPEATS for items in by_group_variant.values()
    ):
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_group_variant_repeat_shape_drift"
        )
    group_ids = tuple(sorted({item.group_id for item in observations}))
    majority_atomic = {
        key: tuple(
            sum(item.atomic_fact_matches[index] for item in items) >= _MAJORITY_MINIMUM_REPEATS
            for index in range(2)
        )
        for key, items in by_group_variant.items()
    }
    majority_grounded = {
        key: tuple(
            sum(item.citation_grounded_fact_matches[index] for item in items)
            >= _MAJORITY_MINIMUM_REPEATS
            for index in range(2)
        )
        for key, items in by_group_variant.items()
    }
    eligible = tuple(
        group_id
        for group_id in group_ids
        if majority_atomic[(group_id, "single_a")][0]
        and majority_grounded[(group_id, "single_a")][0]
        and majority_atomic[(group_id, "single_b")][1]
        and majority_grounded[(group_id, "single_b")][1]
    )
    baseline_joint = {
        group_id: all(majority_atomic[(group_id, "combined_baseline")]) for group_id in eligible
    }
    candidate_joint = {
        group_id: all(majority_atomic[(group_id, "combined_candidate")]) for group_id in eligible
    }
    single_values = [1 for _ in eligible]
    baseline_values = [int(baseline_joint[group_id]) for group_id in eligible]
    candidate_values = [int(candidate_joint[group_id]) for group_id in eligible]
    interference_deltas = [
        single - baseline for single, baseline in zip(single_values, baseline_values, strict=True)
    ]
    candidate_deltas = [
        candidate - baseline
        for candidate, baseline in zip(candidate_values, baseline_values, strict=True)
    ]
    baseline_ci = _paired_bootstrap_ci(interference_deltas, seed=_BOOTSTRAP_SEED)
    candidate_ci = _paired_bootstrap_ci(candidate_deltas, seed=_BOOTSTRAP_SEED + 1)
    baseline_p = _exact_paired_p_value(single_values, baseline_values)
    candidate_p = _exact_paired_p_value(candidate_values, baseline_values)
    eligible_set = set(eligible)
    baseline_observations = [
        item
        for item in observations
        if item.group_id in eligible_set and item.variant == "combined_baseline"
    ]
    candidate_observations = [
        item
        for item in observations
        if item.group_id in eligible_set and item.variant == "combined_candidate"
    ]
    baseline_atomic_count = sum(
        sum(majority_atomic[(group_id, "combined_baseline")]) for group_id in eligible
    )
    candidate_atomic_count = sum(
        sum(majority_atomic[(group_id, "combined_candidate")]) for group_id in eligible
    )
    baseline_grounded_count = sum(
        sum(majority_grounded[(group_id, "combined_baseline")]) for group_id in eligible
    )
    candidate_grounded_count = sum(
        sum(majority_grounded[(group_id, "combined_candidate")]) for group_id in eligible
    )
    denominator_facts = len(eligible) * 2
    denominator_observations = len(eligible) * _REPEATS
    baseline_p95 = _p95(item.latency_ms for item in baseline_observations)
    candidate_p95 = _p95(item.latency_ms for item in candidate_observations)
    latency_ratio = (
        round(candidate_p95 / baseline_p95, 6)
        if baseline_p95 > 0
        else (0.0 if candidate_p95 == 0 else 999_999.0)
    )
    candidate_unexpected_case_count = sum(
        sum(
            item.unexpected_fact_count > 0
            for item in by_group_variant[(group_id, "combined_candidate")]
        )
        >= _MAJORITY_MINIMUM_REPEATS
        for group_id in eligible
    )
    candidate_forbidden_case_count = sum(
        sum(
            item.forbidden_claim_count > 0
            for item in by_group_variant[(group_id, "combined_candidate")]
        )
        >= _MAJORITY_MINIMUM_REPEATS
        for group_id in eligible
    )
    baseline_pipeline_count = sum(
        item.pipeline_failure_reason_code is not None
        for item in observations
        if item.variant != "combined_candidate"
    )
    candidate_pipeline_count = sum(
        item.pipeline_failure_reason_code is not None
        for item in observations
        if item.variant == "combined_candidate"
    )
    repair_items = [
        item
        for item in observations
        if item.variant == "combined_candidate" and item.repair_decision is not None
    ]
    return Rag88ExperimentSummary(
        repeats=_REPEATS,
        group_count=_GROUP_COUNT,
        variant_observation_count=_VARIANT_OBSERVATION_COUNT,
        eligible_case_count=len(eligible),
        eligible_case_ids_fingerprint=_fingerprint_set(eligible),
        baseline_incomplete_case_count=sum(not value for value in baseline_joint.values()),
        single_control_joint_completeness=(1.0 if eligible else 0.0),
        combined_baseline_joint_completeness=_mean(baseline_values),
        combined_candidate_joint_completeness=_mean(candidate_values),
        baseline_interference_drop=_mean(interference_deltas),
        baseline_interference_bootstrap_ci95=baseline_ci,
        baseline_interference_exact_p_value=baseline_p,
        candidate_joint_completeness_delta=_mean(candidate_deltas),
        candidate_bootstrap_ci95=candidate_ci,
        candidate_exact_p_value=candidate_p,
        candidate_improved_case_count=sum(
            candidate > baseline
            for candidate, baseline in zip(candidate_values, baseline_values, strict=True)
        ),
        candidate_regressed_case_count=sum(
            candidate < baseline
            for candidate, baseline in zip(candidate_values, baseline_values, strict=True)
        ),
        baseline_atomic_fact_recall=_ratio(baseline_atomic_count, denominator_facts),
        candidate_atomic_fact_recall=_ratio(candidate_atomic_count, denominator_facts),
        baseline_citation_grounding_recall=_ratio(
            baseline_grounded_count,
            denominator_facts,
        ),
        candidate_citation_grounding_recall=_ratio(
            candidate_grounded_count,
            denominator_facts,
        ),
        baseline_citation_source_coverage_rate=_ratio(
            sum(item.citation_source_coverage for item in baseline_observations),
            denominator_observations,
        ),
        candidate_citation_source_coverage_rate=_ratio(
            sum(item.citation_source_coverage for item in candidate_observations),
            denominator_observations,
        ),
        baseline_false_insufficiency_observation_count=sum(
            item.insufficiency_false_assertion for item in baseline_observations
        ),
        candidate_false_insufficiency_observation_count=sum(
            item.insufficiency_false_assertion for item in candidate_observations
        ),
        candidate_unexpected_fact_case_count=candidate_unexpected_case_count,
        candidate_forbidden_claim_case_count=candidate_forbidden_case_count,
        baseline_pipeline_failure_count=baseline_pipeline_count,
        candidate_pipeline_failure_count=candidate_pipeline_count,
        pipeline_failure_count=baseline_pipeline_count + candidate_pipeline_count,
        combined_baseline_p95_latency_ms=baseline_p95,
        combined_candidate_p95_latency_ms=candidate_p95,
        candidate_p95_latency_ratio=latency_ratio,
        repair_keep_observation_count=sum(item.repair_decision == "keep" for item in repair_items),
        repair_revision_observation_count=sum(
            item.repair_decision == "revise" for item in repair_items
        ),
    )


def _baseline_sensitivity_checks(
    summary: Rag88ExperimentSummary,
) -> Rag88BaselineSensitivityChecks:
    checks = {
        "minimum_eligible_case_count_passed": (
            summary.eligible_case_count >= _MINIMUM_ELIGIBLE_CASES
        ),
        "minimum_baseline_incomplete_case_count_passed": (
            summary.baseline_incomplete_case_count >= _MINIMUM_BASELINE_INCOMPLETE_CASES
        ),
        "minimum_interference_drop_passed": (
            summary.baseline_interference_drop >= _MINIMUM_INTERFERENCE_DROP
        ),
        "bootstrap_lower_bound_passed": (summary.baseline_interference_bootstrap_ci95[0] > 0.0),
        "exact_paired_test_passed": (
            summary.baseline_interference_exact_p_value <= _MAXIMUM_EXACT_P_VALUE
        ),
        "baseline_pipeline_passed": summary.baseline_pipeline_failure_count == 0,
    }
    return Rag88BaselineSensitivityChecks(
        **checks,
        sensitivity_gate_passed=all(checks.values()),
    )


def _candidate_adoption_checks(
    summary: Rag88ExperimentSummary,
    *,
    baseline_sensitivity_passed: bool,
) -> Rag88CandidateAdoptionChecks:
    checks = {
        "baseline_sensitivity_passed": baseline_sensitivity_passed,
        "minimum_joint_delta_passed": (
            summary.candidate_joint_completeness_delta >= _MINIMUM_CANDIDATE_JOINT_DELTA
        ),
        "minimum_improved_case_count_passed": (
            summary.candidate_improved_case_count >= _MINIMUM_CANDIDATE_IMPROVED_CASES
        ),
        "bootstrap_lower_bound_passed": summary.candidate_bootstrap_ci95[0] > 0.0,
        "exact_paired_test_passed": (summary.candidate_exact_p_value <= _MAXIMUM_EXACT_P_VALUE),
        "atomic_fact_recall_non_degradation_passed": (
            summary.candidate_atomic_fact_recall >= summary.baseline_atomic_fact_recall
        ),
        "citation_grounding_non_degradation_passed": (
            summary.candidate_citation_grounding_recall
            >= summary.baseline_citation_grounding_recall
        ),
        "citation_source_coverage_non_degradation_passed": (
            summary.candidate_citation_source_coverage_rate
            >= summary.baseline_citation_source_coverage_rate
        ),
        "false_insufficiency_non_increase_passed": (
            summary.candidate_false_insufficiency_observation_count
            <= summary.baseline_false_insufficiency_observation_count
        ),
        "unexpected_fact_guardrail_passed": (summary.candidate_unexpected_fact_case_count == 0),
        "forbidden_claim_guardrail_passed": (summary.candidate_forbidden_claim_case_count == 0),
        "candidate_pipeline_passed": summary.candidate_pipeline_failure_count == 0,
        "latency_guardrail_passed": (summary.candidate_p95_latency_ratio <= _MAXIMUM_LATENCY_RATIO),
    }
    return Rag88CandidateAdoptionChecks(
        **checks,
        adoption_gate_passed=all(checks.values()),
    )


def _failed_baseline_reason_codes(
    checks: Rag88BaselineSensitivityChecks,
) -> tuple[str, ...]:
    mapping = (
        (checks.minimum_eligible_case_count_passed, "rag88_eligible_case_count_below_minimum"),
        (
            checks.minimum_baseline_incomplete_case_count_passed,
            "rag88_baseline_incomplete_case_count_below_minimum",
        ),
        (checks.minimum_interference_drop_passed, "rag88_interference_drop_too_small"),
        (checks.bootstrap_lower_bound_passed, "rag88_interference_bootstrap_includes_zero"),
        (checks.exact_paired_test_passed, "rag88_interference_exact_test_not_significant"),
        (checks.baseline_pipeline_passed, "rag88_baseline_pipeline_failure"),
    )
    return tuple(reason for passed, reason in mapping if not passed) or (
        "rag88_baseline_sensitivity_not_established",
    )


def _failed_candidate_reason_codes(
    checks: Rag88CandidateAdoptionChecks,
) -> tuple[str, ...]:
    mapping = (
        (checks.minimum_joint_delta_passed, "rag88_candidate_joint_delta_below_minimum"),
        (
            checks.minimum_improved_case_count_passed,
            "rag88_candidate_improved_case_count_below_minimum",
        ),
        (checks.bootstrap_lower_bound_passed, "rag88_candidate_bootstrap_includes_zero"),
        (checks.exact_paired_test_passed, "rag88_candidate_exact_test_not_significant"),
        (
            checks.atomic_fact_recall_non_degradation_passed,
            "rag88_candidate_atomic_fact_recall_regression",
        ),
        (
            checks.citation_grounding_non_degradation_passed,
            "rag88_candidate_citation_grounding_regression",
        ),
        (
            checks.citation_source_coverage_non_degradation_passed,
            "rag88_candidate_citation_source_coverage_regression",
        ),
        (
            checks.false_insufficiency_non_increase_passed,
            "rag88_candidate_false_insufficiency_increase",
        ),
        (checks.unexpected_fact_guardrail_passed, "rag88_candidate_unexpected_fact"),
        (checks.forbidden_claim_guardrail_passed, "rag88_candidate_forbidden_claim"),
        (checks.candidate_pipeline_passed, "rag88_candidate_pipeline_failure"),
        (checks.latency_guardrail_passed, "rag88_candidate_latency_above_2x"),
    )
    return tuple(reason for passed, reason in mapping if not passed) or (
        "rag88_candidate_rejected",
    )


def _paired_bootstrap_ci(values: Sequence[int], *, seed: int) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    count = len(values)
    samples = sorted(
        sum(values[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(_BOOTSTRAP_RESAMPLES)
    )
    lower_index = math.floor(0.025 * (_BOOTSTRAP_RESAMPLES - 1))
    upper_index = math.ceil(0.975 * (_BOOTSTRAP_RESAMPLES - 1))
    return (round(samples[lower_index], 6), round(samples[upper_index], 6))


def _exact_paired_p_value(left: Sequence[int], right: Sequence[int]) -> float:
    if len(left) != len(right):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_exact_pair_cardinality_drift")
    improvements = sum(a > b for a, b in zip(left, right, strict=True))
    regressions = sum(a < b for a, b in zip(left, right, strict=True))
    discordant = improvements + regressions
    if discordant == 0:
        return 1.0
    tail = min(improvements, regressions)
    probability = (
        2.0 * sum(math.comb(discordant, value) for value in range(tail + 1)) / (2**discordant)
    )
    return round(min(1.0, probability), 6)


def _mean(values: Sequence[int]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _p95(values: Iterable[int]) -> int:
    ordered: list[int] = sorted(values)
    if not ordered:
        return 0
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _group_id_from_case(case_id: str) -> str:
    match = re.fullmatch(r"(r(?:88|90)-g\d{2})-(?:single-a|single-b|combined)", case_id)
    if match is None:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_case_group_identity_invalid")
    return re.sub(r"^(r(?:88|90))-g", r"\1-group-", match.group(1))


def _exact_target_validity_failures(
    pre: Rag86LMInventorySummary,
    post: Rag86LMInventorySummary,
) -> tuple[str, ...]:
    if not post.available:
        return ("rag88_post_lm_inventory_unavailable",)
    expected_model_hash = _sha256(_MODEL)
    checks = (
        (
            pre.target_model_id_fingerprint == expected_model_hash
            and post.target_model_id_fingerprint == expected_model_hash,
            "rag88_target_model_id_drift",
        ),
        (
            pre.target_loaded_instance_count == 1 and post.target_loaded_instance_count == 1,
            "rag88_target_instance_count_drift",
        ),
        (
            pre.target_loaded_context_length == _TARGET_LOADED_CONTEXT_LENGTH
            and post.target_loaded_context_length == _TARGET_LOADED_CONTEXT_LENGTH,
            "rag88_target_context_length_drift",
        ),
        (
            pre.target_entry_fingerprint is not None
            and pre.target_entry_fingerprint == post.target_entry_fingerprint,
            "rag88_target_entry_drift",
        ),
    )
    return tuple(reason for passed, reason in checks if not passed)


def _validate_pre_inventory(inventory: Rag86LMInventorySummary) -> None:
    if not inventory.available:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_pre_lm_inventory_unavailable")
    if inventory.target_model_id_fingerprint != _sha256(_MODEL):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_target_model_id_drift")
    if inventory.target_loaded_instance_count != 1:
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_target_model_not_exactly_once_loaded"
        )
    if inventory.target_loaded_context_length != _TARGET_LOADED_CONTEXT_LENGTH:
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_target_model_context_length_drift"
        )
    if inventory.target_entry_fingerprint is None:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_target_entry_unavailable")


def _validate_git_sha(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_git_sha_invalid")


def _manifest_sha256(manifest: Rag88ExperimentManifest) -> str:
    return _sha256_bytes(canonical_json_bytes(manifest))


def _fingerprint_set(values: Sequence[str]) -> str:
    return _sha256_bytes(canonical_json_bytes({"values": sorted(set(values))}))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains_insufficiency_assertion(value: str) -> bool:
    normalized = _normalize_identifier_text(value)
    return _is_insufficient_evidence_answer(value) or any(
        marker in normalized for marker in _INSUFFICIENCY_ASSERTION_MARKERS
    )
