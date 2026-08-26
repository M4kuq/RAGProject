from __future__ import annotations

import hashlib
import math
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from app.evaluation.generation_prompt_profiles import resolve_generation_prompt_profile
from app.evaluation.qwen_multifact_confirm import build_qwen_multifact_confirm_manifest
from app.rag.citations import CitationBuildError, CitationSource
from app.rag.generation import AnswerGenerationError, AnswerGenerator, GenerationContextItem
from app.schemas.evaluation_datasets_v2 import (
    EvaluationCaseV2Spec,
    EvaluationCorpusDocumentSpec,
)
from app.services import evaluation_qwen_context_position_service as rag85_service
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
    _generate_oracle_answer,
    _generate_review_case_with_hard_timeout,
    _review_generation_settings,
)
from app.services.rag_service import _is_insufficient_evidence_answer

_MODEL: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
_BASELINE_PROFILE: Literal["baseline"] = "baseline"
_DATASET: Literal["rag84_qwen_multifact_confirm_v1"] = "rag84_qwen_multifact_confirm_v1"
_STACKED_BASE = "f89e248d2294be9b41150f4ab9b8879e6329e536"
_CASE_COUNT: Literal[14] = 14
_FACTS_PER_CASE: Literal[2] = 2
_CONTEXT_CHUNKS: Literal[8] = 8
_DISTRACTOR_CHUNKS: Literal[6] = 6
_REPEATS: Literal[3] = 3
_EXPECTED_GENERATIONS: Literal[84] = 84
_MAX_CONTEXT_CHARS: Literal[6000] = 6000
_MAX_OUTPUT_CHARS: Literal[12000] = 12000
_MAX_OUTPUT_TOKENS: Literal[8192] = 8192
_CASE_TIMEOUT_SECONDS: Literal[180] = 180
_TARGET_LOADED_CONTEXT_LENGTH: Literal[12312] = 12312
_BOOTSTRAP_RESAMPLES: Literal[10000] = 10000
_BOOTSTRAP_SEED: Literal[86086] = 86086
_MINIMUM_ABSOLUTE_DELTA = 0.15
_PRIMARY_ALPHA = 0.05
_TREATMENT_CITATION_ID: Literal[5] = 5
_TREATMENT_CHUNK_ORDINAL: Literal[3] = 3
_REQUIRED_CHUNK_ORDINALS: tuple[Literal[4], Literal[5]] = (4, 5)
_CLEAN_TREATMENT_LENGTH: Literal[141] = 141
_NEAR_MISS_TREATMENT_LENGTH: Literal[159] = 159
_FIXED_LENGTH_RATIO = 1.12766
_MINIMUM_LENGTH_RATIO = 0.8
_MAXIMUM_LENGTH_RATIO = 1.2
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
_NEAR_MISS_FORBIDDEN_MARKERS = (
    "ignore previous",
    "system prompt",
    "follow these",
    "instruction",
    "password",
    "api key",
    "secret",
    "token",
    "http://",
    "https://",
    "@",
)

GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Condition = Literal["clean", "near_miss"]
Conclusion = Literal[
    "near_miss_causal_effect_detected",
    "no_detectable_near_miss_causal_effect",
    "inconclusive",
]
EffectDirection = Literal[
    "near_miss_lower_recall",
    "near_miss_higher_recall",
    "no_difference",
]

_CONDITIONS: tuple[Condition, Condition] = ("clean", "near_miss")


class EvaluationQwenContextNearMissError(RuntimeError):
    """Stable fail-closed error for the frozen RAG-86 diagnostic."""


class Rag86ConditionBinding(StrictRawFreeModel):
    condition: Condition
    context_sequence_hash: Sha256
    chunk_identity_hash: Sha256
    treatment_content_hash: Sha256


class Rag86CaseBinding(StrictRawFreeModel):
    case_id: SafeId
    question_hash: Sha256
    required_fact_ids: tuple[SafeId, SafeId]
    normalized_fact_hashes: tuple[Sha256, Sha256]
    required_source_content_hashes: tuple[Sha256, Sha256]
    chunk_identity_hash: Sha256
    chunk_count: Literal[8]
    required_citation_ids: tuple[Literal[1], Literal[2]]
    required_chunk_ordinals: tuple[Literal[4], Literal[5]]
    treatment_citation_id: Literal[5]
    treatment_chunk_ordinal: Literal[3]
    near_miss_identifier_fingerprint: Sha256
    clean_length: Literal[141]
    near_miss_length: Literal[159]
    near_miss_to_clean_length_ratio: float = Field(gt=0.0)
    conditions: tuple[Rag86ConditionBinding, Rag86ConditionBinding]
    tags: tuple[str, ...]

    @model_validator(mode="after")
    def validate_condition_bindings(self) -> Self:
        if tuple(item.condition for item in self.conditions) != _CONDITIONS:
            raise ValueError("rag86_condition_binding_order_drift")
        if any(item.chunk_identity_hash != self.chunk_identity_hash for item in self.conditions):
            raise ValueError("rag86_chunk_identity_binding_drift")
        if not (
            _MINIMUM_LENGTH_RATIO <= self.near_miss_to_clean_length_ratio <= _MAXIMUM_LENGTH_RATIO
        ):
            raise ValueError("rag86_near_miss_length_band_drift")
        if self.near_miss_to_clean_length_ratio != _FIXED_LENGTH_RATIO:
            raise ValueError("rag86_near_miss_fixed_length_ratio_drift")
        return self


class Rag86DatasetBinding(StrictRawFreeModel):
    source_fixture: Literal["rag84_qwen_multifact_confirm_v1"]
    source_fixture_content_fingerprint: Sha256
    selection_rule: Literal["all_answerable_multi_hop_cases"]
    case_count: Literal[14]
    language_ja_count: Literal[7]
    language_en_count: Literal[7]
    required_fact_count: Literal[28]
    required_facts_per_case: Literal[2]
    context_chunk_count_per_condition: Literal[8]
    fixed_distractor_chunk_count_per_condition: Literal[6]
    treatment_spacer_count: Literal[1]
    case_set_fingerprint: Sha256
    question_set_fingerprint: Sha256
    normalized_fact_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    near_miss_identifier_set_fingerprint: Sha256
    cases: tuple[Rag86CaseBinding, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if len(self.cases) != _CASE_COUNT:
            raise ValueError("rag86_dataset_case_count_drift")
        if len({item.case_id for item in self.cases}) != _CASE_COUNT:
            raise ValueError("rag86_dataset_case_identity_drift")
        if len({item.near_miss_identifier_fingerprint for item in self.cases}) != _CASE_COUNT:
            raise ValueError("rag86_near_miss_identifier_identity_drift")
        return self


class Rag86GenerationContract(StrictRawFreeModel):
    retrieval_mode: Literal["static_oracle_fixture"]
    generation_provider: Literal["lmstudio"]
    resolved_generation_model: Literal["qwen/qwen3.5-9b"]
    generation_temperature: float
    reasoning_enabled: Literal[False]
    generation_prompt_profile: Literal["baseline"]
    generation_prompt_fingerprint: Sha256
    generation_max_context_chars: Literal[6000]
    generation_max_output_chars: Literal[12000]
    generation_max_output_tokens: Literal[8192]
    generation_case_wall_clock_timeout_seconds: Literal[180]
    lmstudio_loaded_context_length: Literal[12312]
    retry_policy: Literal["existing_evaluation_generation_retry"]
    repeats_per_condition: Literal[3]
    expected_generation_count: Literal[84]
    balanced_order_rule: Literal["case_repeat_parity_7_7_per_repeat"]
    clean_first_pair_count: Literal[21]
    near_miss_first_pair_count: Literal[21]
    execution_schedule_fingerprint: Sha256
    only_experimental_coordinate: Literal["one_spacer_semantic_content"]
    required_chunk_ordinals_frozen: tuple[Literal[4], Literal[5]]
    treatment_citation_id: Literal[5]
    treatment_chunk_ordinal: Literal[3]
    treatment_clean_length: Literal[141]
    treatment_near_miss_length: Literal[159]
    treatment_near_miss_to_clean_length_ratio: float
    chunk_count_positions_ids_labels_and_required_sources_frozen: Literal[True]
    only_target_text_and_citation_snippet_change: Literal[True]
    near_miss_length_ratio_minimum: float
    near_miss_length_ratio_maximum: float
    required_facts_or_answer_keys_sent_as_prompt_fields: Literal[False]

    @model_validator(mode="after")
    def validate_generation(self) -> Self:
        if self.generation_temperature != 0.0:
            raise ValueError("rag86_generation_temperature_drift")
        if (
            self.near_miss_length_ratio_minimum,
            self.near_miss_length_ratio_maximum,
        ) != (_MINIMUM_LENGTH_RATIO, _MAXIMUM_LENGTH_RATIO):
            raise ValueError("rag86_near_miss_length_rule_drift")
        if self.treatment_near_miss_to_clean_length_ratio != _FIXED_LENGTH_RATIO:
            raise ValueError("rag86_near_miss_fixed_length_ratio_drift")
        return self


class Rag86DecisionRule(StrictRawFreeModel):
    primary_metric: Literal["case_paired_majority_atomic_required_fact_recall"]
    primary_delta: Literal["near_miss_minus_clean"]
    fact_majority_minimum_repeats: Literal[2]
    minimum_absolute_recall_delta: float
    paired_bootstrap_resamples: Literal[10000]
    paired_bootstrap_confidence_level: float
    paired_bootstrap_seed: Literal[86086]
    paired_bootstrap_percentile_method: Literal["linear_interpolation_type7"]
    sign_flip_test: Literal["two_sided_exact_nonzero_case_differences"]
    exact_sign_flip_alpha: float
    multiple_testing_adjustment: Literal["none_single_primary_comparison"]
    binding_drift_count_maximum: Literal[0]
    case_exclusion_count_maximum: Literal[0]
    case_replacement_count_maximum: Literal[0]
    pipeline_failure_count_maximum: Literal[0]
    exact_target_model_id_must_match: Literal[True]
    exact_target_loaded_instance_count: Literal[1]
    exact_target_entry_pre_post_must_match: Literal[True]
    exact_target_context_length: Literal[12312]
    full_non_target_inventory_match_required: Literal[False]
    non_target_inventory_drift_recorded: Literal[True]
    secondary_metrics_are_primary_gate_inputs: Literal[False]
    diagnostic_only: Literal[True]
    mitigation_or_profile_promotion_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        actual = (
            self.minimum_absolute_recall_delta,
            self.paired_bootstrap_confidence_level,
            self.exact_sign_flip_alpha,
        )
        expected = (_MINIMUM_ABSOLUTE_DELTA, 0.95, _PRIMARY_ALPHA)
        if actual != expected:
            raise ValueError("rag86_decision_threshold_drift")
        return self


class Rag86ExperimentManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.rag86_qwen_context_near_miss_experiment.v1"]
    jira_issue: Literal["RAG-86"]
    stacked_base_commit: GitSha
    experiment_scope: Literal["non_gold_fixed_oracle_context_near_miss_diagnostic"]
    dataset: Rag86DatasetBinding
    generation: Rag86GenerationContract
    decision_rule: Rag86DecisionRule
    raw_content_persistence_allowed: Literal[False]
    external_non_loopback_http_allowed: Literal[False]
    database_write_allowed: Literal[False]
    gold_v2_access_allowed: Literal[False]
    case_exclusion_replacement_or_extra_repeat_allowed: Literal[False]
    mitigation_confirm_or_profile_promotion_allowed: Literal[False]
    merge_deploy_retarget_or_draft_removal_allowed: Literal[False]


class Rag86ExperimentLock(StrictRawFreeModel):
    schema_version: Literal["phase3.rag86_qwen_context_near_miss_lock.v1"]
    stacked_base_commit: GitSha
    experiment_manifest_sha256: Sha256
    dataset_binding_sha256: Sha256
    generation_contract_sha256: Sha256
    decision_rule_sha256: Sha256
    source_fixture_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    near_miss_identifier_set_fingerprint: Sha256
    baseline_prompt_fingerprint: Sha256
    execution_schedule_fingerprint: Sha256
    prelive_commit_required: Literal[True]
    one_shot_attempt_marker_required: Literal[True]
    generation_count: Literal[84]
    gold_v2_access_allowed: Literal[False]
    raw_content_persistence_allowed: Literal[False]


class Rag86LMInventorySummary(StrictRawFreeModel):
    available: bool
    full_inventory_fingerprint: Sha256 | None = None
    model_count: int | None = Field(default=None, ge=0)
    loaded_instance_count: int | None = Field(default=None, ge=0)
    target_model_id_fingerprint: Sha256 | None = None
    target_entry_fingerprint: Sha256 | None = None
    target_loaded_instance_count: int | None = Field(default=None, ge=0)
    target_loaded_context_length: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        base_values = (
            self.full_inventory_fingerprint,
            self.model_count,
            self.loaded_instance_count,
            self.target_model_id_fingerprint,
            self.target_loaded_instance_count,
        )
        all_values = (
            *base_values,
            self.target_entry_fingerprint,
            self.target_loaded_context_length,
        )
        target_loaded = (self.target_loaded_instance_count or 0) > 0
        invalid_available = self.available and (
            not all(value is not None for value in base_values)
            or (target_loaded and self.target_entry_fingerprint is None)
            or (target_loaded and self.target_loaded_context_length is None)
            or (not target_loaded and self.target_entry_fingerprint is not None)
            or (not target_loaded and self.target_loaded_context_length is not None)
        )
        if invalid_available or (
            not self.available and any(value is not None for value in all_values)
        ):
            raise ValueError("rag86_lm_inventory_shape_invalid")
        return self


class Rag86AttemptState(StrictRawFreeModel):
    schema_version: Literal["phase3.rag86_qwen_context_near_miss_attempt.v1"]
    status: Literal["started"]
    experiment_manifest_sha256: Sha256
    prelive_commit_sha: GitSha
    pre_full_lm_inventory_fingerprint: Sha256
    pre_target_entry_fingerprint: Sha256
    expected_generation_count: Literal[84]
    repeat_or_replacement_allowed: Literal[False]
    raw_content_persisted: Literal[False]


class Rag86CaseObservation(StrictRawFreeModel):
    case_id: SafeId
    condition: Condition
    repeat: int = Field(ge=1, le=3)
    execution_ordinal: int = Field(ge=1, le=84)
    condition_order_ordinal: int = Field(ge=1, le=2)
    answer_hash: Sha256 | None
    context_sequence_hash: Sha256
    atomic_fact_matches: tuple[bool, bool]
    exact_fact_matches: tuple[bool, bool]
    citation_grounded_fact_matches: tuple[bool, bool]
    whole_statement_exact_match: bool
    citation_source_coverage: bool
    insufficiency_false_assertion: bool
    near_miss_identifier_contamination: bool
    near_miss_fact_adoption: bool
    unexpected_fact_count: int = Field(ge=0)
    forbidden_claim_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    pipeline_failure_reason_code: str | None = None


class Rag86ConditionSummary(StrictRawFreeModel):
    condition: Condition
    required_chunk_ordinals: tuple[Literal[4], Literal[5]]
    treatment_chunk_ordinal: Literal[3]
    repeats: Literal[3]
    case_observation_count: Literal[42]
    required_fact_observation_count: Literal[84]
    repeat_atomic_required_fact_recalls: tuple[float, float, float]
    atomic_required_fact_recall: float = Field(ge=0.0, le=1.0)
    majority_atomic_supported_fact_count: int = Field(ge=0, le=28)
    majority_atomic_required_fact_recall: float = Field(ge=0.0, le=1.0)
    majority_whole_completeness_case_count: int = Field(ge=0, le=14)
    majority_whole_completeness_rate: float = Field(ge=0.0, le=1.0)
    whole_statement_exact_case_observation_count: int = Field(ge=0, le=42)
    whole_statement_exact_rate: float = Field(ge=0.0, le=1.0)
    citation_grounding_recall: float = Field(ge=0.0, le=1.0)
    majority_citation_grounding_recall: float = Field(ge=0.0, le=1.0)
    citation_source_coverage_case_observation_count: int = Field(ge=0, le=42)
    citation_source_coverage_rate: float = Field(ge=0.0, le=1.0)
    insufficiency_false_assertion_case_observation_count: int = Field(ge=0, le=42)
    insufficiency_false_assertion_rate: float = Field(ge=0.0, le=1.0)
    near_miss_contamination_case_observation_count: int = Field(ge=0, le=42)
    near_miss_contamination_rate: float = Field(ge=0.0, le=1.0)
    near_miss_adoption_case_observation_count: int = Field(ge=0, le=42)
    near_miss_adoption_rate: float = Field(ge=0.0, le=1.0)
    majority_near_miss_contamination_case_count: int = Field(ge=0, le=14)
    majority_near_miss_contamination_rate: float = Field(ge=0.0, le=1.0)
    majority_near_miss_adoption_case_count: int = Field(ge=0, le=14)
    majority_near_miss_adoption_rate: float = Field(ge=0.0, le=1.0)
    unexpected_fact_case_count: int = Field(ge=0, le=14)
    forbidden_claim_case_count: int = Field(ge=0, le=14)
    pipeline_failure_count: int = Field(ge=0, le=42)
    p95_latency_ms: int = Field(ge=0)


class Rag86PrimaryComparison(StrictRawFreeModel):
    clean_majority_recall: float = Field(ge=0.0, le=1.0)
    near_miss_majority_recall: float = Field(ge=0.0, le=1.0)
    near_miss_minus_clean_recall_delta: float = Field(ge=-1.0, le=1.0)
    effect_direction: EffectDirection
    paired_bootstrap_ci_lower: float = Field(ge=-1.0, le=1.0)
    paired_bootstrap_ci_upper: float = Field(ge=-1.0, le=1.0)
    exact_sign_flip_p_value: float = Field(ge=0.0, le=1.0)
    effect_size_gate_passed: bool
    confidence_interval_gate_passed: bool
    exact_sign_flip_gate_passed: bool
    causal_effect_gate_passed: bool


class Rag86ExperimentResult(StrictRawFreeModel):
    schema_version: Literal["phase3.rag86_qwen_context_near_miss_result.v1"]
    experiment_manifest_sha256: Sha256
    prelive_commit_sha: GitSha
    dataset_name: Literal["rag84_qwen_multifact_confirm_v1"]
    source_fixture_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    near_miss_identifier_set_fingerprint: Sha256
    model: Literal["qwen/qwen3.5-9b"]
    generation_count: Literal[84]
    case_exclusion_count: Literal[0]
    case_replacement_count: Literal[0]
    binding_drift_count: Literal[0]
    pipeline_failure_count: int = Field(ge=0, le=84)
    pre_lm_inventory: Rag86LMInventorySummary
    post_lm_inventory: Rag86LMInventorySummary
    exact_target_stable: bool
    full_lm_inventory_stable: bool
    non_target_inventory_drift_observed: bool
    validity_gate_passed: bool
    causal_effect_gate_passed: bool
    conclusion: Conclusion
    reason_codes: tuple[str, ...]
    conditions: tuple[Rag86ConditionSummary, Rag86ConditionSummary]
    primary_comparison: Rag86PrimaryComparison
    evaluator: Literal["deterministic_identifier_equivalence_v1"]
    secondary_metrics_are_primary_gate_inputs: Literal[False]
    diagnostic_only: Literal[True]
    gold_holdout_eligible: Literal[False]
    public_accuracy_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]
    mitigation_or_confirm_authorized: Literal[False]
    raw_content_persisted: Literal[False]
    observations: tuple[Rag86CaseObservation, ...]


@dataclass(frozen=True)
class _ConditionMaterial:
    context_items: tuple[GenerationContextItem, ...]
    citation_sources: tuple[CitationSource, ...]
    context_sequence_hash: str
    near_miss_identifier_tokens: frozenset[str]


def build_rag86_experiment_manifest() -> Rag86ExperimentManifest:
    fixture = build_qwen_multifact_confirm_manifest()
    selected = _selected_cases()
    documents = {document.source_key: document for document in fixture.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(fixture.corpus_documents, key=lambda item: item.source_key), start=1
        )
    }
    bindings: list[Rag86CaseBinding] = []
    identifier_fingerprints: list[str] = []
    for case_ordinal, case in enumerate(selected, start=1):
        materials = {
            condition: _build_condition_material(
                case,
                case_ordinal=case_ordinal,
                condition=condition,
                documents=documents,
                document_ids=document_ids,
            )
            for condition in _CONDITIONS
        }
        _validate_condition_materials(materials, case=case)
        required_keys = tuple(dict.fromkeys(item.source_key for item in case.expected_evidence))
        if len(required_keys) != _FACTS_PER_CASE or len(case.required_facts) != _FACTS_PER_CASE:
            raise EvaluationQwenContextNearMissError("rag86_case_required_binding_drift")
        clean = materials["clean"]
        near = materials["near_miss"]
        clean_target = _context_item_by_citation(clean, _TREATMENT_CITATION_ID)
        near_target = _context_item_by_citation(near, _TREATMENT_CITATION_ID)
        identifier_fingerprint = _fingerprint_set(tuple(sorted(near.near_miss_identifier_tokens)))
        identifier_fingerprints.append(identifier_fingerprint)
        identity_hash = _chunk_identity_hash(clean)
        bindings.append(
            Rag86CaseBinding(
                case_id=case.case_key,
                question_hash=_sha256(case.question),
                required_fact_ids=(
                    case.required_facts[0].fact_id,
                    case.required_facts[1].fact_id,
                ),
                normalized_fact_hashes=(
                    _sha256(_normalize_identifier_text(case.required_facts[0].statement)),
                    _sha256(_normalize_identifier_text(case.required_facts[1].statement)),
                ),
                required_source_content_hashes=(
                    _sha256(documents[required_keys[0]].body),
                    _sha256(documents[required_keys[1]].body),
                ),
                chunk_identity_hash=identity_hash,
                chunk_count=_CONTEXT_CHUNKS,
                required_citation_ids=(1, 2),
                required_chunk_ordinals=_REQUIRED_CHUNK_ORDINALS,
                treatment_citation_id=_TREATMENT_CITATION_ID,
                treatment_chunk_ordinal=_TREATMENT_CHUNK_ORDINAL,
                near_miss_identifier_fingerprint=identifier_fingerprint,
                clean_length=len(clean_target.text),
                near_miss_length=len(near_target.text),
                near_miss_to_clean_length_ratio=round(
                    len(near_target.text) / len(clean_target.text), 6
                ),
                conditions=tuple(
                    Rag86ConditionBinding(
                        condition=condition,
                        context_sequence_hash=materials[condition].context_sequence_hash,
                        chunk_identity_hash=identity_hash,
                        treatment_content_hash=_sha256(
                            _context_item_by_citation(
                                materials[condition], _TREATMENT_CITATION_ID
                            ).text
                        ),
                    )
                    for condition in _CONDITIONS
                ),
                tags=tuple(sorted(case.tags)),
            )
        )
    ordered = tuple(sorted(bindings, key=lambda item: item.case_id))
    question_hashes = tuple(item.question_hash for item in ordered)
    fact_hashes = tuple(value for item in ordered for value in item.normalized_fact_hashes)
    baseline = resolve_generation_prompt_profile(_BASELINE_PROFILE)
    schedule_fingerprint = _execution_schedule_fingerprint(selected)
    dataset = Rag86DatasetBinding(
        source_fixture=_DATASET,
        source_fixture_content_fingerprint=fixture.content_fingerprint(),
        selection_rule="all_answerable_multi_hop_cases",
        case_count=_CASE_COUNT,
        language_ja_count=7,
        language_en_count=7,
        required_fact_count=28,
        required_facts_per_case=_FACTS_PER_CASE,
        context_chunk_count_per_condition=_CONTEXT_CHUNKS,
        fixed_distractor_chunk_count_per_condition=_DISTRACTOR_CHUNKS,
        treatment_spacer_count=1,
        case_set_fingerprint=_sha256_bytes(
            canonical_json_bytes({"cases": [item.model_dump(mode="json") for item in ordered]})
        ),
        question_set_fingerprint=_fingerprint_set(question_hashes),
        normalized_fact_set_fingerprint=_fingerprint_set(fact_hashes),
        source_context_fingerprint=_sha256_bytes(
            canonical_json_bytes(
                {
                    "cases": [
                        {
                            "case_id": item.case_id,
                            "chunk_identity_hash": item.chunk_identity_hash,
                            "condition_sequence_hashes": [
                                condition.context_sequence_hash for condition in item.conditions
                            ],
                        }
                        for item in ordered
                    ]
                }
            )
        ),
        near_miss_identifier_set_fingerprint=_fingerprint_set(identifier_fingerprints),
        cases=ordered,
    )
    return Rag86ExperimentManifest(
        schema_version="phase3.rag86_qwen_context_near_miss_experiment.v1",
        jira_issue="RAG-86",
        stacked_base_commit=_STACKED_BASE,
        experiment_scope="non_gold_fixed_oracle_context_near_miss_diagnostic",
        dataset=dataset,
        generation=Rag86GenerationContract(
            retrieval_mode="static_oracle_fixture",
            generation_provider="lmstudio",
            resolved_generation_model=_MODEL,
            generation_temperature=0.0,
            reasoning_enabled=False,
            generation_prompt_profile=_BASELINE_PROFILE,
            generation_prompt_fingerprint=baseline.prompt_fingerprint,
            generation_max_context_chars=_MAX_CONTEXT_CHARS,
            generation_max_output_chars=_MAX_OUTPUT_CHARS,
            generation_max_output_tokens=_MAX_OUTPUT_TOKENS,
            generation_case_wall_clock_timeout_seconds=_CASE_TIMEOUT_SECONDS,
            lmstudio_loaded_context_length=_TARGET_LOADED_CONTEXT_LENGTH,
            retry_policy="existing_evaluation_generation_retry",
            repeats_per_condition=_REPEATS,
            expected_generation_count=_EXPECTED_GENERATIONS,
            balanced_order_rule="case_repeat_parity_7_7_per_repeat",
            clean_first_pair_count=21,
            near_miss_first_pair_count=21,
            execution_schedule_fingerprint=schedule_fingerprint,
            only_experimental_coordinate="one_spacer_semantic_content",
            required_chunk_ordinals_frozen=_REQUIRED_CHUNK_ORDINALS,
            treatment_citation_id=_TREATMENT_CITATION_ID,
            treatment_chunk_ordinal=_TREATMENT_CHUNK_ORDINAL,
            treatment_clean_length=_CLEAN_TREATMENT_LENGTH,
            treatment_near_miss_length=_NEAR_MISS_TREATMENT_LENGTH,
            treatment_near_miss_to_clean_length_ratio=_FIXED_LENGTH_RATIO,
            chunk_count_positions_ids_labels_and_required_sources_frozen=True,
            only_target_text_and_citation_snippet_change=True,
            near_miss_length_ratio_minimum=_MINIMUM_LENGTH_RATIO,
            near_miss_length_ratio_maximum=_MAXIMUM_LENGTH_RATIO,
            required_facts_or_answer_keys_sent_as_prompt_fields=False,
        ),
        decision_rule=Rag86DecisionRule(
            primary_metric="case_paired_majority_atomic_required_fact_recall",
            primary_delta="near_miss_minus_clean",
            fact_majority_minimum_repeats=2,
            minimum_absolute_recall_delta=_MINIMUM_ABSOLUTE_DELTA,
            paired_bootstrap_resamples=_BOOTSTRAP_RESAMPLES,
            paired_bootstrap_confidence_level=0.95,
            paired_bootstrap_seed=_BOOTSTRAP_SEED,
            paired_bootstrap_percentile_method="linear_interpolation_type7",
            sign_flip_test="two_sided_exact_nonzero_case_differences",
            exact_sign_flip_alpha=_PRIMARY_ALPHA,
            multiple_testing_adjustment="none_single_primary_comparison",
            binding_drift_count_maximum=0,
            case_exclusion_count_maximum=0,
            case_replacement_count_maximum=0,
            pipeline_failure_count_maximum=0,
            exact_target_model_id_must_match=True,
            exact_target_loaded_instance_count=1,
            exact_target_entry_pre_post_must_match=True,
            exact_target_context_length=_TARGET_LOADED_CONTEXT_LENGTH,
            full_non_target_inventory_match_required=False,
            non_target_inventory_drift_recorded=True,
            secondary_metrics_are_primary_gate_inputs=False,
            diagnostic_only=True,
            mitigation_or_profile_promotion_allowed=False,
        ),
        raw_content_persistence_allowed=False,
        external_non_loopback_http_allowed=False,
        database_write_allowed=False,
        gold_v2_access_allowed=False,
        case_exclusion_replacement_or_extra_repeat_allowed=False,
        mitigation_confirm_or_profile_promotion_allowed=False,
        merge_deploy_retarget_or_draft_removal_allowed=False,
    )


def build_rag86_experiment_lock(manifest: Rag86ExperimentManifest) -> Rag86ExperimentLock:
    return Rag86ExperimentLock(
        schema_version="phase3.rag86_qwen_context_near_miss_lock.v1",
        stacked_base_commit=manifest.stacked_base_commit,
        experiment_manifest_sha256=_manifest_sha256(manifest),
        dataset_binding_sha256=_sha256_bytes(canonical_json_bytes(manifest.dataset)),
        generation_contract_sha256=_sha256_bytes(canonical_json_bytes(manifest.generation)),
        decision_rule_sha256=_sha256_bytes(canonical_json_bytes(manifest.decision_rule)),
        source_fixture_content_fingerprint=manifest.dataset.source_fixture_content_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        near_miss_identifier_set_fingerprint=(
            manifest.dataset.near_miss_identifier_set_fingerprint
        ),
        baseline_prompt_fingerprint=manifest.generation.generation_prompt_fingerprint,
        execution_schedule_fingerprint=manifest.generation.execution_schedule_fingerprint,
        prelive_commit_required=True,
        one_shot_attempt_marker_required=True,
        generation_count=_EXPECTED_GENERATIONS,
        gold_v2_access_allowed=False,
        raw_content_persistence_allowed=False,
    )


def load_frozen_rag86_experiment_manifest(path: Path) -> Rag86ExperimentManifest:
    payload_bytes, payload = read_json_object(path)
    lock = Rag86ExperimentLock.model_validate(payload)
    if not model_bytes_match(payload_bytes, lock):
        raise EvaluationQwenContextNearMissError("rag86_manifest_bytes_model_mismatch")
    manifest = build_rag86_experiment_manifest()
    if build_rag86_experiment_lock(manifest) != lock:
        raise EvaluationQwenContextNearMissError("rag86_manifest_runtime_drift")
    return manifest


def build_rag86_attempt_state(
    manifest: Rag86ExperimentManifest,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag86LMInventorySummary,
) -> Rag86AttemptState:
    _validate_prelive_commit(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    assert pre_lm_inventory.full_inventory_fingerprint is not None
    assert pre_lm_inventory.target_entry_fingerprint is not None
    return Rag86AttemptState(
        schema_version="phase3.rag86_qwen_context_near_miss_attempt.v1",
        status="started",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        prelive_commit_sha=prelive_commit_sha,
        pre_full_lm_inventory_fingerprint=pre_lm_inventory.full_inventory_fingerprint,
        pre_target_entry_fingerprint=pre_lm_inventory.target_entry_fingerprint,
        expected_generation_count=_EXPECTED_GENERATIONS,
        repeat_or_replacement_allowed=False,
        raw_content_persisted=False,
    )


def run_rag86_diagnostic(
    manifest: Rag86ExperimentManifest,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag86LMInventorySummary,
    post_lm_inventory_provider: Callable[[], Rag86LMInventorySummary],
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag86ExperimentResult:
    _validate_prelive_commit(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    runtime_manifest = build_rag86_experiment_manifest()
    if runtime_manifest != manifest:
        raise EvaluationQwenContextNearMissError("rag86_runtime_manifest_drift")
    fixture = build_qwen_multifact_confirm_manifest()
    selected = _selected_cases()
    documents = {document.source_key: document for document in fixture.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(fixture.corpus_documents, key=lambda item: item.source_key), start=1
        )
    }
    binding_by_case = {item.case_id: item for item in manifest.dataset.cases}
    observations: list[Rag86CaseObservation] = []
    for repeat in range(1, _REPEATS + 1):
        for case_ordinal, case in enumerate(selected, start=1):
            materials = {
                condition: _build_condition_material(
                    case,
                    case_ordinal=case_ordinal,
                    condition=condition,
                    documents=documents,
                    document_ids=document_ids,
                )
                for condition in _CONDITIONS
            }
            _validate_condition_materials(materials, case=case)
            case_binding = binding_by_case.get(case.case_key)
            if case_binding is None:
                raise EvaluationQwenContextNearMissError("rag86_case_binding_missing")
            condition_hashes = {
                item.condition: item.context_sequence_hash for item in case_binding.conditions
            }
            for condition_order_ordinal, condition in enumerate(
                _condition_order(repeat=repeat, case_ordinal=case_ordinal), start=1
            ):
                material = materials[condition]
                if material.context_sequence_hash != condition_hashes[condition]:
                    raise EvaluationQwenContextNearMissError("rag86_context_binding_drift")
                execution_ordinal = len(observations) + 1
                observation = _run_case(
                    case,
                    material=material,
                    condition=condition,
                    repeat=repeat,
                    execution_ordinal=execution_ordinal,
                    condition_order_ordinal=condition_order_ordinal,
                    generator=generator,
                )
                observations.append(observation)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "status": "rag86_generation_progress",
                            "condition": condition,
                            "repeat": repeat,
                            "completed_generation_count": execution_ordinal,
                            "expected_generation_count": _EXPECTED_GENERATIONS,
                            "pipeline_failure_count": sum(
                                item.pipeline_failure_reason_code is not None
                                for item in observations
                            ),
                        }
                    )
    if len(observations) != _EXPECTED_GENERATIONS:
        raise EvaluationQwenContextNearMissError("rag86_generation_count_drift")
    try:
        post_inventory = post_lm_inventory_provider()
    except Exception:
        post_inventory = Rag86LMInventorySummary(available=False)
    summaries = tuple(
        _summarize_condition(condition, observations=observations) for condition in _CONDITIONS
    )
    comparison = _build_primary_comparison(observations)
    pipeline_failure_count = sum(
        item.pipeline_failure_reason_code is not None for item in observations
    )
    exact_target_failures = _exact_target_validity_failures(pre_lm_inventory, post_inventory)
    exact_target_stable = not exact_target_failures
    full_inventory_stable = bool(
        pre_lm_inventory.available
        and post_inventory.available
        and pre_lm_inventory.full_inventory_fingerprint == post_inventory.full_inventory_fingerprint
    )
    non_target_drift = exact_target_stable and not full_inventory_stable
    validity_failures = (
        *(("rag86_pipeline_failure",) if pipeline_failure_count else ()),
        *exact_target_failures,
    )
    validity_passed = not validity_failures
    causal_gate = validity_passed and comparison.causal_effect_gate_passed
    if not validity_passed:
        conclusion: Conclusion = "inconclusive"
    elif causal_gate:
        conclusion = "near_miss_causal_effect_detected"
    else:
        conclusion = "no_detectable_near_miss_causal_effect"
    reason_codes = validity_failures or (
        ("rag86_near_miss_causal_effect_detected",)
        if causal_gate
        else ("rag86_no_detectable_near_miss_causal_effect",)
    )
    return Rag86ExperimentResult(
        schema_version="phase3.rag86_qwen_context_near_miss_result.v1",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        prelive_commit_sha=prelive_commit_sha,
        dataset_name=_DATASET,
        source_fixture_content_fingerprint=manifest.dataset.source_fixture_content_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        near_miss_identifier_set_fingerprint=(
            manifest.dataset.near_miss_identifier_set_fingerprint
        ),
        model=_MODEL,
        generation_count=_EXPECTED_GENERATIONS,
        case_exclusion_count=0,
        case_replacement_count=0,
        binding_drift_count=0,
        pipeline_failure_count=pipeline_failure_count,
        pre_lm_inventory=pre_lm_inventory,
        post_lm_inventory=post_inventory,
        exact_target_stable=exact_target_stable,
        full_lm_inventory_stable=full_inventory_stable,
        non_target_inventory_drift_observed=non_target_drift,
        validity_gate_passed=validity_passed,
        causal_effect_gate_passed=causal_gate,
        conclusion=conclusion,
        reason_codes=reason_codes,
        conditions=summaries,
        primary_comparison=comparison,
        evaluator="deterministic_identifier_equivalence_v1",
        secondary_metrics_are_primary_gate_inputs=False,
        diagnostic_only=True,
        gold_holdout_eligible=False,
        public_accuracy_eligible=False,
        profile_promotion_eligible=False,
        mitigation_or_confirm_authorized=False,
        raw_content_persisted=False,
        observations=tuple(observations),
    )


def _run_case(
    case: EvaluationCaseV2Spec,
    *,
    material: _ConditionMaterial,
    condition: Condition,
    repeat: int,
    execution_ordinal: int,
    condition_order_ordinal: int,
    generator: AnswerGenerator | None,
) -> Rag86CaseObservation:
    started = time.perf_counter()
    answer_text: str | None = None
    answer_outcome: Literal["answered", "abstained"] | None = None
    citation_ids: tuple[int, ...] = ()
    reason_code: str | None = None
    try:
        if generator is None:
            generated = _generate_review_case_with_hard_timeout(
                question=case.question,
                context_items=material.context_items,
                citation_sources=material.citation_sources,
                system_instructions=None,
            )
            answer_text = generated.answer_text
            answer_outcome = generated.answer_outcome
            citation_ids = generated.citation_ids
            if generated.reason_code is not None:
                reason_code = f"rag86_{generated.reason_code}"
        else:
            generation = _generate_oracle_answer(
                _review_generation_settings(),
                generator=generator,
                question=case.question,
                context_items=material.context_items,
                citation_sources=material.citation_sources,
                system_instructions=None,
            )
            answer_text = generation.answer_text
            answer_outcome = generation.answer_outcome
            citation_ids = tuple(
                sorted(
                    {
                        citation_id
                        for citation in generation.citations
                        if isinstance((citation_id := citation.get("local_citation_id")), int)
                    }
                )
            )
    except AnswerGenerationError as exc:
        reason_code = f"rag86_generation_{exc.error_category or 'failed'}"
    except CitationBuildError as exc:
        reason_code = f"rag86_generation_{exc.detail_code}"
    except Exception:
        reason_code = "rag86_generation_unexpected_error"
    latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    if reason_code is not None or answer_text is None or answer_outcome is None:
        return Rag86CaseObservation(
            case_id=case.case_key,
            condition=condition,
            repeat=repeat,
            execution_ordinal=execution_ordinal,
            condition_order_ordinal=condition_order_ordinal,
            answer_hash=None,
            context_sequence_hash=material.context_sequence_hash,
            atomic_fact_matches=(False, False),
            exact_fact_matches=(False, False),
            citation_grounded_fact_matches=(False, False),
            whole_statement_exact_match=False,
            citation_source_coverage=False,
            insufficiency_false_assertion=False,
            near_miss_identifier_contamination=False,
            near_miss_fact_adoption=False,
            unexpected_fact_count=0,
            forbidden_claim_count=0,
            latency_ms=latency_ms,
            pipeline_failure_reason_code=reason_code or "rag86_generation_worker_failed",
        )
    answer_tokens = _identifier_tokens(answer_text)
    atomic_matches: list[bool] = []
    exact_matches: list[bool] = []
    citation_matches: list[bool] = []
    for fact_index, fact in enumerate(case.required_facts):
        siblings = tuple(
            sibling.statement
            for sibling_index, sibling in enumerate(case.required_facts)
            if sibling_index != fact_index
        )
        strong_tokens = _strong_fact_identifier_tokens(fact.statement, sibling_statements=siblings)
        atomic_match = bool(strong_tokens) and strong_tokens.issubset(answer_tokens)
        exact_match = _whole_statement_exact_match(answer=answer_text, required_fact=fact.statement)
        atomic_matches.append(atomic_match)
        exact_matches.append(exact_match)
        citation_matches.append(atomic_match and fact_index + 1 in citation_ids)
    context = tuple(item.text for item in material.context_items)
    allowed_identifiers = _identifier_tokens("\n".join((case.question, *context)))
    unexpected_identifiers = answer_tokens - allowed_identifiers
    normalized_answer = _normalize_identifier_text(answer_text)
    forbidden_count = sum(
        _normalize_identifier_text(claim) in normalized_answer for claim in case.forbidden_claims
    )
    contamination = bool(material.near_miss_identifier_tokens) and (
        material.near_miss_identifier_tokens.issubset(answer_tokens)
    )
    return Rag86CaseObservation(
        case_id=case.case_key,
        condition=condition,
        repeat=repeat,
        execution_ordinal=execution_ordinal,
        condition_order_ordinal=condition_order_ordinal,
        answer_hash=_sha256(answer_text),
        context_sequence_hash=material.context_sequence_hash,
        atomic_fact_matches=(atomic_matches[0], atomic_matches[1]),
        exact_fact_matches=(exact_matches[0], exact_matches[1]),
        citation_grounded_fact_matches=(citation_matches[0], citation_matches[1]),
        whole_statement_exact_match=all(exact_matches),
        citation_source_coverage={1, 2}.issubset(citation_ids),
        insufficiency_false_assertion=(
            answer_outcome == "abstained" or _contains_insufficiency_assertion(answer_text)
        ),
        near_miss_identifier_contamination=contamination,
        near_miss_fact_adoption=contamination and _TREATMENT_CITATION_ID in citation_ids,
        unexpected_fact_count=len(unexpected_identifiers),
        forbidden_claim_count=forbidden_count,
        latency_ms=latency_ms,
        pipeline_failure_reason_code=None,
    )


def _summarize_condition(
    condition: Condition,
    *,
    observations: Sequence[Rag86CaseObservation],
) -> Rag86ConditionSummary:
    selected = tuple(item for item in observations if item.condition == condition)
    if len(selected) != _CASE_COUNT * _REPEATS:
        raise EvaluationQwenContextNearMissError("rag86_condition_observation_count_drift")
    by_case = _observations_by_case(selected)
    majority_atomic = {
        case_id: tuple(
            sum(item.atomic_fact_matches[index] for item in case_observations) >= 2
            for index in range(_FACTS_PER_CASE)
        )
        for case_id, case_observations in by_case.items()
    }
    majority_grounded = {
        case_id: tuple(
            sum(item.citation_grounded_fact_matches[index] for item in case_observations) >= 2
            for index in range(_FACTS_PER_CASE)
        )
        for case_id, case_observations in by_case.items()
    }
    majority_contamination = {
        case_id: sum(item.near_miss_identifier_contamination for item in case_observations) >= 2
        for case_id, case_observations in by_case.items()
    }
    majority_adoption = {
        case_id: sum(item.near_miss_fact_adoption for item in case_observations) >= 2
        for case_id, case_observations in by_case.items()
    }
    fact_observations = len(selected) * _FACTS_PER_CASE
    atomic_count = sum(sum(item.atomic_fact_matches) for item in selected)
    grounded_count = sum(sum(item.citation_grounded_fact_matches) for item in selected)
    majority_atomic_count = sum(sum(values) for values in majority_atomic.values())
    majority_grounded_count = sum(sum(values) for values in majority_grounded.values())
    latencies = sorted(item.latency_ms for item in selected)
    return Rag86ConditionSummary(
        condition=condition,
        required_chunk_ordinals=_REQUIRED_CHUNK_ORDINALS,
        treatment_chunk_ordinal=_TREATMENT_CHUNK_ORDINAL,
        repeats=_REPEATS,
        case_observation_count=42,
        required_fact_observation_count=84,
        repeat_atomic_required_fact_recalls=tuple(
            round(
                sum(sum(item.atomic_fact_matches) for item in selected if item.repeat == repeat)
                / (_CASE_COUNT * _FACTS_PER_CASE),
                6,
            )
            for repeat in range(1, _REPEATS + 1)
        ),
        atomic_required_fact_recall=round(atomic_count / fact_observations, 6),
        majority_atomic_supported_fact_count=majority_atomic_count,
        majority_atomic_required_fact_recall=round(majority_atomic_count / 28, 6),
        majority_whole_completeness_case_count=sum(
            all(values) for values in majority_atomic.values()
        ),
        majority_whole_completeness_rate=round(
            sum(all(values) for values in majority_atomic.values()) / _CASE_COUNT, 6
        ),
        whole_statement_exact_case_observation_count=sum(
            item.whole_statement_exact_match for item in selected
        ),
        whole_statement_exact_rate=round(
            sum(item.whole_statement_exact_match for item in selected) / len(selected), 6
        ),
        citation_grounding_recall=round(grounded_count / fact_observations, 6),
        majority_citation_grounding_recall=round(majority_grounded_count / 28, 6),
        citation_source_coverage_case_observation_count=sum(
            item.citation_source_coverage for item in selected
        ),
        citation_source_coverage_rate=round(
            sum(item.citation_source_coverage for item in selected) / len(selected), 6
        ),
        insufficiency_false_assertion_case_observation_count=sum(
            item.insufficiency_false_assertion for item in selected
        ),
        insufficiency_false_assertion_rate=round(
            sum(item.insufficiency_false_assertion for item in selected) / len(selected), 6
        ),
        near_miss_contamination_case_observation_count=sum(
            item.near_miss_identifier_contamination for item in selected
        ),
        near_miss_contamination_rate=round(
            sum(item.near_miss_identifier_contamination for item in selected) / len(selected), 6
        ),
        near_miss_adoption_case_observation_count=sum(
            item.near_miss_fact_adoption for item in selected
        ),
        near_miss_adoption_rate=round(
            sum(item.near_miss_fact_adoption for item in selected) / len(selected), 6
        ),
        majority_near_miss_contamination_case_count=sum(majority_contamination.values()),
        majority_near_miss_contamination_rate=round(
            sum(majority_contamination.values()) / _CASE_COUNT, 6
        ),
        majority_near_miss_adoption_case_count=sum(majority_adoption.values()),
        majority_near_miss_adoption_rate=round(sum(majority_adoption.values()) / _CASE_COUNT, 6),
        unexpected_fact_case_count=sum(
            any(item.unexpected_fact_count > 0 for item in case_observations)
            for case_observations in by_case.values()
        ),
        forbidden_claim_case_count=sum(
            any(item.forbidden_claim_count > 0 for item in case_observations)
            for case_observations in by_case.values()
        ),
        pipeline_failure_count=sum(
            item.pipeline_failure_reason_code is not None for item in selected
        ),
        p95_latency_ms=latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)],
    )


def _build_primary_comparison(
    observations: Sequence[Rag86CaseObservation],
) -> Rag86PrimaryComparison:
    clean = _majority_case_scores("clean", observations=observations)
    near = _majority_case_scores("near_miss", observations=observations)
    case_ids = tuple(sorted(clean))
    if case_ids != tuple(sorted(near)) or len(case_ids) != _CASE_COUNT:
        raise EvaluationQwenContextNearMissError("rag86_pairing_case_set_drift")
    differences = tuple(near[case_id] - clean[case_id] for case_id in case_ids)
    clean_recall = sum(clean.values()) / _CASE_COUNT
    near_recall = sum(near.values()) / _CASE_COUNT
    delta = sum(differences) / _CASE_COUNT
    lower, upper = _paired_bootstrap_ci(differences)
    p_value = _exact_sign_flip_p_value(differences)
    effect_gate = abs(delta) >= _MINIMUM_ABSOLUTE_DELTA
    ci_gate = lower > 0.0 or upper < 0.0
    p_gate = p_value <= _PRIMARY_ALPHA
    direction: EffectDirection
    if delta < 0:
        direction = "near_miss_lower_recall"
    elif delta > 0:
        direction = "near_miss_higher_recall"
    else:
        direction = "no_difference"
    return Rag86PrimaryComparison(
        clean_majority_recall=round(clean_recall, 6),
        near_miss_majority_recall=round(near_recall, 6),
        near_miss_minus_clean_recall_delta=round(delta, 6),
        effect_direction=direction,
        paired_bootstrap_ci_lower=lower,
        paired_bootstrap_ci_upper=upper,
        exact_sign_flip_p_value=p_value,
        effect_size_gate_passed=effect_gate,
        confidence_interval_gate_passed=ci_gate,
        exact_sign_flip_gate_passed=p_gate,
        causal_effect_gate_passed=effect_gate and ci_gate and p_gate,
    )


def _paired_bootstrap_ci(differences: Sequence[float]) -> tuple[float, float]:
    if len(differences) != _CASE_COUNT:
        raise EvaluationQwenContextNearMissError("rag86_bootstrap_pair_count_drift")
    rng = random.Random(_BOOTSTRAP_SEED)
    samples = sorted(
        sum(differences[rng.randrange(len(differences))] for _ in differences) / len(differences)
        for _ in range(_BOOTSTRAP_RESAMPLES)
    )
    return round(_quantile_type7(samples, 0.025), 6), round(_quantile_type7(samples, 0.975), 6)


def _quantile_type7(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise EvaluationQwenContextNearMissError("rag86_bootstrap_samples_missing")
    index = (len(sorted_values) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    weight = index - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _exact_sign_flip_p_value(differences: Sequence[float]) -> float:
    nonzero = tuple(value for value in differences if value != 0.0)
    if not nonzero:
        return 1.0
    observed = abs(sum(nonzero))
    extreme = 0
    permutation_count = 1 << len(nonzero)
    for mask in range(permutation_count):
        signed_sum = sum(
            value if mask & (1 << index) else -value for index, value in enumerate(nonzero)
        )
        if abs(signed_sum) >= observed - 1e-12:
            extreme += 1
    return extreme / permutation_count


def _majority_case_scores(
    condition: Condition,
    *,
    observations: Sequence[Rag86CaseObservation],
) -> dict[str, float]:
    selected = tuple(item for item in observations if item.condition == condition)
    by_case = _observations_by_case(selected)
    return {
        case_id: sum(
            sum(item.atomic_fact_matches[index] for item in case_observations) >= 2
            for index in range(_FACTS_PER_CASE)
        )
        / _FACTS_PER_CASE
        for case_id, case_observations in by_case.items()
    }


def _observations_by_case(
    observations: Sequence[Rag86CaseObservation],
) -> dict[str, tuple[Rag86CaseObservation, ...]]:
    case_ids = {item.case_id for item in observations}
    if len(case_ids) != _CASE_COUNT:
        raise EvaluationQwenContextNearMissError("rag86_observation_case_count_drift")
    grouped = {
        case_id: tuple(item for item in observations if item.case_id == case_id)
        for case_id in case_ids
    }
    if any(len(items) != _REPEATS for items in grouped.values()):
        raise EvaluationQwenContextNearMissError("rag86_observation_repeat_count_drift")
    return grouped


def _build_condition_material(
    case: EvaluationCaseV2Spec,
    *,
    case_ordinal: int,
    condition: Condition,
    documents: dict[str, EvaluationCorpusDocumentSpec],
    document_ids: dict[str, int],
) -> _ConditionMaterial:
    clean = rag85_service._build_position_material(
        case,
        case_ordinal=case_ordinal,
        position="middle",
        documents=documents,
        document_ids=document_ids,
    )
    identifier = f"NM86-{case_ordinal:02d}".casefold()
    identifier_tokens = frozenset({identifier})
    if condition == "clean":
        return _ConditionMaterial(
            context_items=clean.context_items,
            citation_sources=clean.citation_sources,
            context_sequence_hash=_context_sequence_hash(clean.context_items),
            near_miss_identifier_tokens=identifier_tokens,
        )
    near_text = (
        f"Position-control spacer 3 for synthetic diagnostic case {case_ordinal:02d}. "
        f"A neighboring profile uses beacon code NM86-{case_ordinal:02d} and is not "
        "evidence for the requested audit profile."
    )
    context_items = tuple(
        replace(item, text=near_text) if item.local_citation_id == _TREATMENT_CITATION_ID else item
        for item in clean.context_items
    )
    citation_sources = tuple(
        replace(source, snippet=near_text)
        if source.local_citation_id == _TREATMENT_CITATION_ID
        else source
        for source in clean.citation_sources
    )
    return _ConditionMaterial(
        context_items=context_items,
        citation_sources=citation_sources,
        context_sequence_hash=_context_sequence_hash(context_items),
        near_miss_identifier_tokens=identifier_tokens,
    )


def _validate_condition_materials(
    materials: dict[Condition, _ConditionMaterial],
    *,
    case: EvaluationCaseV2Spec,
) -> None:
    if set(materials) != set(_CONDITIONS):
        raise EvaluationQwenContextNearMissError("rag86_condition_set_drift")
    clean = materials["clean"]
    near = materials["near_miss"]
    for material in (clean, near):
        if len(material.context_items) != _CONTEXT_CHUNKS:
            raise EvaluationQwenContextNearMissError("rag86_context_chunk_count_drift")
        citation_ids = tuple(item.local_citation_id for item in material.context_items)
        if citation_ids != (3, 4, 5, 1, 2, 6, 7, 8):
            raise EvaluationQwenContextNearMissError("rag86_context_citation_order_drift")
        required_ordinals = tuple(
            index
            for index, item in enumerate(material.context_items, start=1)
            if item.local_citation_id in {1, 2}
        )
        if required_ordinals != _REQUIRED_CHUNK_ORDINALS:
            raise EvaluationQwenContextNearMissError("rag86_required_position_drift")
        sources_by_id = {item.local_citation_id: item for item in material.citation_sources}
        if set(sources_by_id) != set(range(1, _CONTEXT_CHUNKS + 1)):
            raise EvaluationQwenContextNearMissError("rag86_citation_source_set_drift")
        for item in material.context_items:
            source = sources_by_id.get(item.local_citation_id or -1)
            if source is None or (
                source.document_chunk_id != item.document_chunk_id
                or source.source_label != item.source_label
                or source.snippet != item.text
            ):
                raise EvaluationQwenContextNearMissError("rag86_citation_source_binding_drift")
    if _chunk_identity_hash(clean) != _chunk_identity_hash(near):
        raise EvaluationQwenContextNearMissError("rag86_chunk_identity_drift")
    changed = tuple(
        (
            index,
            clean_item.local_citation_id,
        )
        for index, (clean_item, near_item) in enumerate(
            zip(clean.context_items, near.context_items, strict=True), start=1
        )
        if clean_item.text != near_item.text
    )
    if changed != ((_TREATMENT_CHUNK_ORDINAL, _TREATMENT_CITATION_ID),):
        raise EvaluationQwenContextNearMissError("rag86_treatment_coordinate_drift")
    clean_target = _context_item_by_citation(clean, _TREATMENT_CITATION_ID)
    near_target = _context_item_by_citation(near, _TREATMENT_CITATION_ID)
    if (
        len(clean_target.text),
        len(near_target.text),
    ) != (_CLEAN_TREATMENT_LENGTH, _NEAR_MISS_TREATMENT_LENGTH):
        raise EvaluationQwenContextNearMissError("rag86_treatment_length_drift")
    ratio = len(near_target.text) / len(clean_target.text)
    if not _MINIMUM_LENGTH_RATIO <= ratio <= _MAXIMUM_LENGTH_RATIO:
        raise EvaluationQwenContextNearMissError("rag86_near_miss_length_band_drift")
    if clean_target.text.split(". ", 1)[0] != near_target.text.split(". ", 1)[0]:
        raise EvaluationQwenContextNearMissError("rag86_near_miss_format_drift")
    if any(marker in near_target.text.casefold() for marker in _NEAR_MISS_FORBIDDEN_MARKERS):
        raise EvaluationQwenContextNearMissError("rag86_near_miss_unsafe_marker")
    new_tokens = _identifier_tokens(near_target.text) - _identifier_tokens(clean_target.text)
    if new_tokens != near.near_miss_identifier_tokens:
        raise EvaluationQwenContextNearMissError("rag86_near_miss_identifier_binding_drift")
    collision_scope = "\n".join(
        (
            case.question,
            *(fact.statement for fact in case.required_facts),
            *(item.text for item in clean.context_items),
        )
    )
    if near.near_miss_identifier_tokens & _identifier_tokens(collision_scope):
        raise EvaluationQwenContextNearMissError("rag86_near_miss_identifier_collision")


def _context_item_by_citation(
    material: _ConditionMaterial, citation_id: int
) -> GenerationContextItem:
    selected = tuple(
        item for item in material.context_items if item.local_citation_id == citation_id
    )
    if len(selected) != 1:
        raise EvaluationQwenContextNearMissError("rag86_context_citation_identity_drift")
    return selected[0]


def _chunk_identity_hash(material: _ConditionMaterial) -> str:
    return _sha256_bytes(
        canonical_json_bytes(
            {
                "chunks": [
                    {
                        "ordinal": index,
                        "citation_id": item.local_citation_id,
                        "document_chunk_id": item.document_chunk_id,
                        "source_label_hash": _sha256(item.source_label),
                    }
                    for index, item in enumerate(material.context_items, start=1)
                ]
            }
        )
    )


def _context_sequence_hash(items: Sequence[GenerationContextItem]) -> str:
    return _sha256_bytes(
        canonical_json_bytes(
            {
                "chunks": [
                    {
                        "ordinal": index,
                        "citation_id": item.local_citation_id,
                        "document_chunk_id": item.document_chunk_id,
                        "source_label_hash": _sha256(item.source_label),
                        "content_hash": _sha256(item.text),
                    }
                    for index, item in enumerate(items, start=1)
                ]
            }
        )
    )


def _condition_order(*, repeat: int, case_ordinal: int) -> tuple[Condition, Condition]:
    if repeat not in {1, 2, 3} or not 1 <= case_ordinal <= _CASE_COUNT:
        raise EvaluationQwenContextNearMissError("rag86_execution_schedule_input_drift")
    if (repeat + case_ordinal) % 2 == 0:
        return ("clean", "near_miss")
    return ("near_miss", "clean")


def _execution_schedule_fingerprint(cases: Sequence[EvaluationCaseV2Spec]) -> str:
    schedule = [
        {
            "repeat": repeat,
            "case_id": case.case_key,
            "order": _condition_order(repeat=repeat, case_ordinal=case_ordinal),
        }
        for repeat in range(1, _REPEATS + 1)
        for case_ordinal, case in enumerate(cases, start=1)
    ]
    clean_first = sum(
        _condition_order(repeat=repeat, case_ordinal=case_ordinal)[0] == "clean"
        for repeat in range(1, _REPEATS + 1)
        for case_ordinal in range(1, len(cases) + 1)
    )
    if clean_first != 21 or len(schedule) - clean_first != 21:
        raise EvaluationQwenContextNearMissError("rag86_execution_schedule_balance_drift")
    return _sha256_bytes(canonical_json_bytes({"schedule": schedule}))


def _selected_cases() -> tuple[EvaluationCaseV2Spec, ...]:
    return rag85_service._selected_cases()


def _exact_target_validity_failures(
    pre: Rag86LMInventorySummary,
    post: Rag86LMInventorySummary,
) -> tuple[str, ...]:
    if not post.available:
        return ("rag86_post_lm_inventory_unavailable",)
    expected_model_hash = _sha256(_MODEL)
    checks = (
        (
            pre.target_model_id_fingerprint == expected_model_hash
            and post.target_model_id_fingerprint == expected_model_hash,
            "rag86_target_model_id_drift",
        ),
        (
            pre.target_loaded_instance_count == 1 and post.target_loaded_instance_count == 1,
            "rag86_target_instance_count_drift",
        ),
        (
            pre.target_loaded_context_length == _TARGET_LOADED_CONTEXT_LENGTH
            and post.target_loaded_context_length == _TARGET_LOADED_CONTEXT_LENGTH,
            "rag86_target_context_length_drift",
        ),
        (
            pre.target_entry_fingerprint is not None
            and pre.target_entry_fingerprint == post.target_entry_fingerprint,
            "rag86_target_entry_drift",
        ),
    )
    return tuple(reason for passed, reason in checks if not passed)


def _validate_pre_inventory(inventory: Rag86LMInventorySummary) -> None:
    if not inventory.available:
        raise EvaluationQwenContextNearMissError("rag86_pre_lm_inventory_unavailable")
    if inventory.target_model_id_fingerprint != _sha256(_MODEL):
        raise EvaluationQwenContextNearMissError("rag86_target_model_id_drift")
    if inventory.target_loaded_instance_count != 1:
        raise EvaluationQwenContextNearMissError("rag86_target_model_not_exactly_once_loaded")
    if inventory.target_loaded_context_length != _TARGET_LOADED_CONTEXT_LENGTH:
        raise EvaluationQwenContextNearMissError("rag86_target_model_context_length_drift")
    if inventory.target_entry_fingerprint is None:
        raise EvaluationQwenContextNearMissError("rag86_target_entry_unavailable")


def _validate_prelive_commit(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvaluationQwenContextNearMissError("rag86_prelive_commit_invalid")


def _manifest_sha256(manifest: Rag86ExperimentManifest) -> str:
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
