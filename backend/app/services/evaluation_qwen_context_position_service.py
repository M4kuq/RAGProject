from __future__ import annotations

import hashlib
import math
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
_STACKED_BASE = "b2b9512f3f5e9ac56241660271000cb098f3aaee"
_CASE_COUNT: Literal[14] = 14
_FACTS_PER_CASE: Literal[2] = 2
_CONTEXT_CHUNKS: Literal[8] = 8
_DISTRACTOR_CHUNKS: Literal[6] = 6
_REPEATS: Literal[3] = 3
_EXPECTED_GENERATIONS: Literal[126] = 126
_MAX_CONTEXT_CHARS: Literal[6000] = 6000
_MAX_OUTPUT_CHARS: Literal[12000] = 12000
_MAX_OUTPUT_TOKENS: Literal[8192] = 8192
_CASE_TIMEOUT_SECONDS: Literal[180] = 180
_TARGET_LOADED_CONTEXT_LENGTH: Literal[12312] = 12312
_BOOTSTRAP_RESAMPLES: Literal[10000] = 10000
_BOOTSTRAP_SEED: Literal[85085] = 85085
_MINIMUM_ABSOLUTE_DELTA = 0.15
_HOLM_ALPHA = 0.05
_RAG84_BASELINE_REFERENCE_RECALL = 0.458333
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

GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Position = Literal["front", "middle", "end"]
Conclusion = Literal[
    "position_dependence_detected",
    "no_detectable_position_dependence",
    "inconclusive",
]

_POSITIONS: tuple[Position, ...] = ("front", "middle", "end")
_REQUIRED_ORDINALS: dict[Position, tuple[int, int]] = {
    "front": (1, 2),
    "middle": (4, 5),
    "end": (7, 8),
}
_LATIN_ROTATION: tuple[tuple[Position, Position, Position], ...] = (
    ("front", "middle", "end"),
    ("middle", "end", "front"),
    ("end", "front", "middle"),
)
_PAIR_ORDER: tuple[tuple[Position, Position], ...] = (
    ("front", "middle"),
    ("front", "end"),
    ("middle", "end"),
)


class EvaluationQwenContextPositionError(RuntimeError):
    """Stable fail-closed error for the frozen RAG-85 diagnostic."""


class Rag85ConditionBinding(StrictRawFreeModel):
    condition: Position
    required_chunk_ordinals: tuple[int, int]
    context_sequence_hash: Sha256


class Rag85CaseBinding(StrictRawFreeModel):
    case_id: SafeId
    question_hash: Sha256
    required_fact_ids: tuple[SafeId, SafeId]
    normalized_fact_hashes: tuple[Sha256, Sha256]
    required_source_content_hashes: tuple[Sha256, Sha256]
    chunk_set_hash: Sha256
    chunk_count: Literal[8]
    required_citation_ids: tuple[Literal[1], Literal[2]]
    conditions: tuple[Rag85ConditionBinding, Rag85ConditionBinding, Rag85ConditionBinding]
    tags: tuple[str, ...]

    @model_validator(mode="after")
    def validate_condition_bindings(self) -> Self:
        if tuple(item.condition for item in self.conditions) != _POSITIONS:
            raise ValueError("rag85_condition_binding_order_drift")
        for item in self.conditions:
            if item.required_chunk_ordinals != _REQUIRED_ORDINALS[item.condition]:
                raise ValueError("rag85_required_position_binding_drift")
        return self


class Rag85DatasetBinding(StrictRawFreeModel):
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
    case_set_fingerprint: Sha256
    question_set_fingerprint: Sha256
    normalized_fact_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    cases: tuple[Rag85CaseBinding, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if len(self.cases) != _CASE_COUNT:
            raise ValueError("rag85_dataset_case_count_drift")
        if len({item.case_id for item in self.cases}) != _CASE_COUNT:
            raise ValueError("rag85_dataset_case_identity_drift")
        return self


class Rag85GenerationContract(StrictRawFreeModel):
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
    expected_generation_count: Literal[126]
    latin_rotation: tuple[
        tuple[Literal["front"], Literal["middle"], Literal["end"]],
        tuple[Literal["middle"], Literal["end"], Literal["front"]],
        tuple[Literal["end"], Literal["front"], Literal["middle"]],
    ]
    only_experimental_coordinate: Literal["required_chunk_block_position"]
    required_citation_ids_frozen: Literal[True]
    all_chunk_ids_labels_text_and_citation_ids_frozen: Literal[True]
    order_dependent_clipping_allowed: Literal[False]
    required_facts_or_answer_keys_sent_as_prompt_fields: Literal[False]

    @model_validator(mode="after")
    def validate_generation(self) -> Self:
        if self.generation_temperature != 0.0:
            raise ValueError("rag85_generation_temperature_drift")
        if self.latin_rotation != _LATIN_ROTATION:
            raise ValueError("rag85_latin_rotation_drift")
        return self


class Rag85DecisionRule(StrictRawFreeModel):
    primary_metric: Literal["case_paired_majority_atomic_required_fact_recall"]
    fact_majority_minimum_repeats: Literal[2]
    pair_order: tuple[
        tuple[Literal["front"], Literal["middle"]],
        tuple[Literal["front"], Literal["end"]],
        tuple[Literal["middle"], Literal["end"]],
    ]
    minimum_absolute_recall_delta: float
    paired_bootstrap_resamples: Literal[10000]
    paired_bootstrap_confidence_level: float
    paired_bootstrap_seed: Literal[85085]
    paired_bootstrap_percentile_method: Literal["linear_interpolation_type7"]
    sign_flip_test: Literal["two_sided_exact_nonzero_case_differences"]
    multiple_testing_adjustment: Literal["holm_three_comparisons"]
    holm_alpha: float
    binding_drift_count_maximum: Literal[0]
    case_exclusion_count_maximum: Literal[0]
    case_replacement_count_maximum: Literal[0]
    pipeline_failure_count_maximum: Literal[0]
    pre_post_lm_inventory_must_match: Literal[True]
    reference_recall: float
    reference_provenance: Literal["rag84_jira_comment_10093_tune_baseline"]
    reference_same_case_set: Literal[False]
    reference_drift_is_exclusion_rule: Literal[False]
    diagnostic_only: Literal[True]
    profile_promotion_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        actual = (
            self.minimum_absolute_recall_delta,
            self.paired_bootstrap_confidence_level,
            self.holm_alpha,
            self.reference_recall,
        )
        expected = (
            _MINIMUM_ABSOLUTE_DELTA,
            0.95,
            _HOLM_ALPHA,
            _RAG84_BASELINE_REFERENCE_RECALL,
        )
        if actual != expected or self.pair_order != _PAIR_ORDER:
            raise ValueError("rag85_decision_threshold_drift")
        return self


class Rag85ExperimentManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.rag85_qwen_context_position_experiment.v1"]
    jira_issue: Literal["RAG-85"]
    stacked_base_commit: GitSha
    experiment_scope: Literal["non_gold_fixed_oracle_context_position_diagnostic"]
    dataset: Rag85DatasetBinding
    generation: Rag85GenerationContract
    decision_rule: Rag85DecisionRule
    raw_content_persistence_allowed: Literal[False]
    external_non_loopback_http_allowed: Literal[False]
    database_write_allowed: Literal[False]
    gold_v2_access_allowed: Literal[False]
    case_exclusion_replacement_or_extra_repeat_allowed: Literal[False]
    merge_deploy_retarget_or_draft_removal_allowed: Literal[False]


class Rag85ExperimentLock(StrictRawFreeModel):
    schema_version: Literal["phase3.rag85_qwen_context_position_lock.v1"]
    stacked_base_commit: GitSha
    experiment_manifest_sha256: Sha256
    dataset_binding_sha256: Sha256
    generation_contract_sha256: Sha256
    decision_rule_sha256: Sha256
    source_fixture_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    baseline_prompt_fingerprint: Sha256
    prelive_commit_required: Literal[True]
    one_shot_attempt_marker_required: Literal[True]
    generation_count: Literal[126]
    gold_v2_access_allowed: Literal[False]
    raw_content_persistence_allowed: Literal[False]


class Rag85LMInventorySummary(StrictRawFreeModel):
    available: bool
    inventory_fingerprint: Sha256 | None = None
    model_count: int | None = Field(default=None, ge=0)
    loaded_instance_count: int | None = Field(default=None, ge=0)
    target_loaded_instance_count: int | None = Field(default=None, ge=0)
    target_loaded_context_length: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        base_values = (
            self.inventory_fingerprint,
            self.model_count,
            self.loaded_instance_count,
            self.target_loaded_instance_count,
        )
        all_values = (*base_values, self.target_loaded_context_length)
        invalid_available = self.available and (
            not all(value is not None for value in base_values)
            or (
                self.target_loaded_instance_count == 0
                and self.target_loaded_context_length is not None
            )
            or (
                self.target_loaded_instance_count is not None
                and self.target_loaded_instance_count > 0
                and self.target_loaded_context_length is None
            )
        )
        if invalid_available or (
            not self.available and any(value is not None for value in all_values)
        ):
            raise ValueError("rag85_lm_inventory_shape_invalid")
        return self


class Rag85AttemptState(StrictRawFreeModel):
    schema_version: Literal["phase3.rag85_qwen_context_position_attempt.v1"]
    status: Literal["started"]
    experiment_manifest_sha256: Sha256
    prelive_commit_sha: GitSha
    pre_lm_inventory_fingerprint: Sha256
    expected_generation_count: Literal[126]
    repeat_or_replacement_allowed: Literal[False]
    raw_content_persisted: Literal[False]


class Rag85CaseObservation(StrictRawFreeModel):
    case_id: SafeId
    condition: Position
    repeat: int = Field(ge=1, le=3)
    execution_ordinal: int = Field(ge=1, le=126)
    condition_order_ordinal: int = Field(ge=1, le=3)
    answer_hash: Sha256 | None
    context_sequence_hash: Sha256
    atomic_fact_matches: tuple[bool, bool]
    exact_fact_matches: tuple[bool, bool]
    citation_grounded_fact_matches: tuple[bool, bool]
    whole_statement_exact_match: bool
    citation_source_coverage: bool
    insufficiency_false_assertion: bool
    unexpected_fact_count: int = Field(ge=0)
    forbidden_claim_count: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    pipeline_failure_reason_code: str | None = None


class Rag85ConditionSummary(StrictRawFreeModel):
    condition: Position
    required_chunk_ordinals: tuple[int, int]
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
    unexpected_fact_case_count: int = Field(ge=0, le=14)
    forbidden_claim_case_count: int = Field(ge=0, le=14)
    pipeline_failure_count: int = Field(ge=0, le=42)
    p95_latency_ms: int = Field(ge=0)


class Rag85PairwiseComparison(StrictRawFreeModel):
    left_condition: Position
    right_condition: Position
    left_majority_recall: float = Field(ge=0.0, le=1.0)
    right_majority_recall: float = Field(ge=0.0, le=1.0)
    recall_delta: float = Field(ge=-1.0, le=1.0)
    paired_bootstrap_ci_lower: float = Field(ge=-1.0, le=1.0)
    paired_bootstrap_ci_upper: float = Field(ge=-1.0, le=1.0)
    exact_sign_flip_p_value: float = Field(ge=0.0, le=1.0)
    holm_adjusted_p_value: float = Field(ge=0.0, le=1.0)
    effect_size_gate_passed: bool
    confidence_interval_gate_passed: bool
    holm_gate_passed: bool
    position_dependence_gate_passed: bool


class Rag85ExperimentResult(StrictRawFreeModel):
    schema_version: Literal["phase3.rag85_qwen_context_position_result.v1"]
    experiment_manifest_sha256: Sha256
    prelive_commit_sha: GitSha
    dataset_name: Literal["rag84_qwen_multifact_confirm_v1"]
    source_fixture_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    model: Literal["qwen/qwen3.5-9b"]
    generation_count: Literal[126]
    case_exclusion_count: Literal[0]
    case_replacement_count: Literal[0]
    binding_drift_count: Literal[0]
    pipeline_failure_count: int = Field(ge=0, le=126)
    pre_lm_inventory: Rag85LMInventorySummary
    post_lm_inventory: Rag85LMInventorySummary
    lm_inventory_stable: bool
    validity_gate_passed: bool
    position_dependence_gate_passed: bool
    conclusion: Conclusion
    reason_codes: tuple[str, ...]
    rag84_baseline_reference_recall: float
    pooled_repeat_atomic_required_fact_recall: float = Field(ge=0.0, le=1.0)
    descriptive_reference_drift: float = Field(ge=-1.0, le=1.0)
    reference_same_case_set: Literal[False]
    reference_used_for_exclusion_or_replacement: Literal[False]
    conditions: tuple[
        Rag85ConditionSummary,
        Rag85ConditionSummary,
        Rag85ConditionSummary,
    ]
    pairwise_comparisons: tuple[
        Rag85PairwiseComparison,
        Rag85PairwiseComparison,
        Rag85PairwiseComparison,
    ]
    evaluator: Literal["deterministic_identifier_equivalence_v1"]
    secondary_metrics_are_primary_gate_inputs: Literal[False]
    diagnostic_only: Literal[True]
    gold_holdout_eligible: Literal[False]
    public_accuracy_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]
    raw_content_persisted: Literal[False]
    observations: tuple[Rag85CaseObservation, ...]


@dataclass(frozen=True)
class _PositionMaterial:
    context_items: tuple[GenerationContextItem, ...]
    citation_sources: tuple[CitationSource, ...]
    context_sequence_hash: str


def build_rag85_experiment_manifest() -> Rag85ExperimentManifest:
    fixture = build_qwen_multifact_confirm_manifest()
    selected = _selected_cases()
    documents = {document.source_key: document for document in fixture.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(fixture.corpus_documents, key=lambda item: item.source_key), start=1
        )
    }
    bindings: list[Rag85CaseBinding] = []
    for case_ordinal, case in enumerate(selected, start=1):
        materials = {
            position: _build_position_material(
                case,
                case_ordinal=case_ordinal,
                position=position,
                documents=documents,
                document_ids=document_ids,
            )
            for position in _POSITIONS
        }
        _validate_position_materials(materials)
        required_keys = tuple(dict.fromkeys(item.source_key for item in case.expected_evidence))
        if len(required_keys) != _FACTS_PER_CASE or len(case.required_facts) != _FACTS_PER_CASE:
            raise EvaluationQwenContextPositionError("rag85_case_required_binding_drift")
        chunk_set_hash = _material_chunk_set_hash(materials["front"])
        bindings.append(
            Rag85CaseBinding(
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
                chunk_set_hash=chunk_set_hash,
                chunk_count=_CONTEXT_CHUNKS,
                required_citation_ids=(1, 2),
                conditions=tuple(
                    Rag85ConditionBinding(
                        condition=position,
                        required_chunk_ordinals=_REQUIRED_ORDINALS[position],
                        context_sequence_hash=materials[position].context_sequence_hash,
                    )
                    for position in _POSITIONS
                ),
                tags=tuple(sorted(case.tags)),
            )
        )
    ordered = tuple(sorted(bindings, key=lambda item: item.case_id))
    question_hashes = tuple(item.question_hash for item in ordered)
    fact_hashes = tuple(value for item in ordered for value in item.normalized_fact_hashes)
    baseline = resolve_generation_prompt_profile(_BASELINE_PROFILE)
    dataset = Rag85DatasetBinding(
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
                            "chunk_set_hash": item.chunk_set_hash,
                            "condition_sequence_hashes": [
                                condition.context_sequence_hash for condition in item.conditions
                            ],
                        }
                        for item in ordered
                    ]
                }
            )
        ),
        cases=ordered,
    )
    return Rag85ExperimentManifest(
        schema_version="phase3.rag85_qwen_context_position_experiment.v1",
        jira_issue="RAG-85",
        stacked_base_commit=_STACKED_BASE,
        experiment_scope="non_gold_fixed_oracle_context_position_diagnostic",
        dataset=dataset,
        generation=Rag85GenerationContract(
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
            latin_rotation=_LATIN_ROTATION,
            only_experimental_coordinate="required_chunk_block_position",
            required_citation_ids_frozen=True,
            all_chunk_ids_labels_text_and_citation_ids_frozen=True,
            order_dependent_clipping_allowed=False,
            required_facts_or_answer_keys_sent_as_prompt_fields=False,
        ),
        decision_rule=Rag85DecisionRule(
            primary_metric="case_paired_majority_atomic_required_fact_recall",
            fact_majority_minimum_repeats=2,
            pair_order=_PAIR_ORDER,
            minimum_absolute_recall_delta=_MINIMUM_ABSOLUTE_DELTA,
            paired_bootstrap_resamples=_BOOTSTRAP_RESAMPLES,
            paired_bootstrap_confidence_level=0.95,
            paired_bootstrap_seed=_BOOTSTRAP_SEED,
            paired_bootstrap_percentile_method="linear_interpolation_type7",
            sign_flip_test="two_sided_exact_nonzero_case_differences",
            multiple_testing_adjustment="holm_three_comparisons",
            holm_alpha=_HOLM_ALPHA,
            binding_drift_count_maximum=0,
            case_exclusion_count_maximum=0,
            case_replacement_count_maximum=0,
            pipeline_failure_count_maximum=0,
            pre_post_lm_inventory_must_match=True,
            reference_recall=_RAG84_BASELINE_REFERENCE_RECALL,
            reference_provenance="rag84_jira_comment_10093_tune_baseline",
            reference_same_case_set=False,
            reference_drift_is_exclusion_rule=False,
            diagnostic_only=True,
            profile_promotion_allowed=False,
        ),
        raw_content_persistence_allowed=False,
        external_non_loopback_http_allowed=False,
        database_write_allowed=False,
        gold_v2_access_allowed=False,
        case_exclusion_replacement_or_extra_repeat_allowed=False,
        merge_deploy_retarget_or_draft_removal_allowed=False,
    )


def build_rag85_experiment_lock(manifest: Rag85ExperimentManifest) -> Rag85ExperimentLock:
    return Rag85ExperimentLock(
        schema_version="phase3.rag85_qwen_context_position_lock.v1",
        stacked_base_commit=manifest.stacked_base_commit,
        experiment_manifest_sha256=_manifest_sha256(manifest),
        dataset_binding_sha256=_sha256_bytes(canonical_json_bytes(manifest.dataset)),
        generation_contract_sha256=_sha256_bytes(canonical_json_bytes(manifest.generation)),
        decision_rule_sha256=_sha256_bytes(canonical_json_bytes(manifest.decision_rule)),
        source_fixture_content_fingerprint=manifest.dataset.source_fixture_content_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        baseline_prompt_fingerprint=manifest.generation.generation_prompt_fingerprint,
        prelive_commit_required=True,
        one_shot_attempt_marker_required=True,
        generation_count=_EXPECTED_GENERATIONS,
        gold_v2_access_allowed=False,
        raw_content_persistence_allowed=False,
    )


def load_frozen_rag85_experiment_manifest(path: Path) -> Rag85ExperimentManifest:
    payload_bytes, payload = read_json_object(path)
    lock = Rag85ExperimentLock.model_validate(payload)
    if not model_bytes_match(payload_bytes, lock):
        raise EvaluationQwenContextPositionError("rag85_manifest_bytes_model_mismatch")
    manifest = build_rag85_experiment_manifest()
    if build_rag85_experiment_lock(manifest) != lock:
        raise EvaluationQwenContextPositionError("rag85_manifest_runtime_drift")
    return manifest


def build_rag85_attempt_state(
    manifest: Rag85ExperimentManifest,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag85LMInventorySummary,
) -> Rag85AttemptState:
    _validate_prelive_commit(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    assert pre_lm_inventory.inventory_fingerprint is not None
    return Rag85AttemptState(
        schema_version="phase3.rag85_qwen_context_position_attempt.v1",
        status="started",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        prelive_commit_sha=prelive_commit_sha,
        pre_lm_inventory_fingerprint=pre_lm_inventory.inventory_fingerprint,
        expected_generation_count=_EXPECTED_GENERATIONS,
        repeat_or_replacement_allowed=False,
        raw_content_persisted=False,
    )


def run_rag85_diagnostic(
    manifest: Rag85ExperimentManifest,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag85LMInventorySummary,
    post_lm_inventory_provider: Callable[[], Rag85LMInventorySummary],
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag85ExperimentResult:
    _validate_prelive_commit(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    runtime_manifest = build_rag85_experiment_manifest()
    if runtime_manifest != manifest:
        raise EvaluationQwenContextPositionError("rag85_runtime_manifest_drift")
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
    observations: list[Rag85CaseObservation] = []
    for repeat, order in enumerate(_LATIN_ROTATION, start=1):
        for case_ordinal, case in enumerate(selected, start=1):
            materials = {
                position: _build_position_material(
                    case,
                    case_ordinal=case_ordinal,
                    position=position,
                    documents=documents,
                    document_ids=document_ids,
                )
                for position in _POSITIONS
            }
            _validate_position_materials(materials)
            case_binding = binding_by_case.get(case.case_key)
            if case_binding is None:
                raise EvaluationQwenContextPositionError("rag85_case_binding_missing")
            condition_hashes = {
                item.condition: item.context_sequence_hash for item in case_binding.conditions
            }
            for condition_order_ordinal, position in enumerate(order, start=1):
                material = materials[position]
                if material.context_sequence_hash != condition_hashes[position]:
                    raise EvaluationQwenContextPositionError("rag85_context_binding_drift")
                execution_ordinal = len(observations) + 1
                observation = _run_case(
                    case,
                    material=material,
                    condition=position,
                    repeat=repeat,
                    execution_ordinal=execution_ordinal,
                    condition_order_ordinal=condition_order_ordinal,
                    generator=generator,
                )
                observations.append(observation)
                if progress_callback is not None:
                    progress_callback(
                        {
                            "status": "rag85_generation_progress",
                            "condition": position,
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
        raise EvaluationQwenContextPositionError("rag85_generation_count_drift")
    try:
        post_inventory = post_lm_inventory_provider()
    except Exception:
        post_inventory = Rag85LMInventorySummary(available=False)
    summaries = tuple(
        _summarize_condition(position, observations=observations) for position in _POSITIONS
    )
    comparisons = _build_pairwise_comparisons(observations)
    pipeline_failure_count = sum(
        item.pipeline_failure_reason_code is not None for item in observations
    )
    lm_stable = bool(
        pre_lm_inventory.available
        and post_inventory.available
        and pre_lm_inventory.inventory_fingerprint == post_inventory.inventory_fingerprint
        and pre_lm_inventory.target_loaded_instance_count == 1
        and post_inventory.target_loaded_instance_count == 1
    )
    validity_checks = (
        (pipeline_failure_count == 0, "rag85_pipeline_failure"),
        (lm_stable, "rag85_lm_inventory_drift"),
    )
    validity_failures = tuple(reason for passed, reason in validity_checks if not passed)
    validity_passed = not validity_failures
    position_gate = validity_passed and any(
        item.position_dependence_gate_passed for item in comparisons
    )
    if not validity_passed:
        conclusion: Conclusion = "inconclusive"
    elif position_gate:
        conclusion = "position_dependence_detected"
    else:
        conclusion = "no_detectable_position_dependence"
    pooled_recall = round(
        sum(sum(item.atomic_fact_matches) for item in observations)
        / (_EXPECTED_GENERATIONS * _FACTS_PER_CASE),
        6,
    )
    reason_codes = validity_failures or (
        ("rag85_position_dependence_detected",)
        if position_gate
        else ("rag85_no_detectable_position_dependence",)
    )
    return Rag85ExperimentResult(
        schema_version="phase3.rag85_qwen_context_position_result.v1",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        prelive_commit_sha=prelive_commit_sha,
        dataset_name=_DATASET,
        source_fixture_content_fingerprint=manifest.dataset.source_fixture_content_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        model=_MODEL,
        generation_count=_EXPECTED_GENERATIONS,
        case_exclusion_count=0,
        case_replacement_count=0,
        binding_drift_count=0,
        pipeline_failure_count=pipeline_failure_count,
        pre_lm_inventory=pre_lm_inventory,
        post_lm_inventory=post_inventory,
        lm_inventory_stable=lm_stable,
        validity_gate_passed=validity_passed,
        position_dependence_gate_passed=position_gate,
        conclusion=conclusion,
        reason_codes=reason_codes,
        rag84_baseline_reference_recall=_RAG84_BASELINE_REFERENCE_RECALL,
        pooled_repeat_atomic_required_fact_recall=pooled_recall,
        descriptive_reference_drift=round(pooled_recall - _RAG84_BASELINE_REFERENCE_RECALL, 6),
        reference_same_case_set=False,
        reference_used_for_exclusion_or_replacement=False,
        conditions=summaries,
        pairwise_comparisons=comparisons,
        evaluator="deterministic_identifier_equivalence_v1",
        secondary_metrics_are_primary_gate_inputs=False,
        diagnostic_only=True,
        gold_holdout_eligible=False,
        public_accuracy_eligible=False,
        profile_promotion_eligible=False,
        raw_content_persisted=False,
        observations=tuple(observations),
    )


def _run_case(
    case: EvaluationCaseV2Spec,
    *,
    material: _PositionMaterial,
    condition: Position,
    repeat: int,
    execution_ordinal: int,
    condition_order_ordinal: int,
    generator: AnswerGenerator | None,
) -> Rag85CaseObservation:
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
                reason_code = f"rag85_{generated.reason_code}"
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
        reason_code = f"rag85_generation_{exc.error_category or 'failed'}"
    except CitationBuildError as exc:
        reason_code = f"rag85_generation_{exc.detail_code}"
    except Exception:
        reason_code = "rag85_generation_unexpected_error"
    latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    if reason_code is not None or answer_text is None or answer_outcome is None:
        return Rag85CaseObservation(
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
            unexpected_fact_count=0,
            forbidden_claim_count=0,
            latency_ms=latency_ms,
            pipeline_failure_reason_code=reason_code or "rag85_generation_worker_failed",
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
    return Rag85CaseObservation(
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
        unexpected_fact_count=len(unexpected_identifiers),
        forbidden_claim_count=forbidden_count,
        latency_ms=latency_ms,
        pipeline_failure_reason_code=None,
    )


def _summarize_condition(
    condition: Position,
    *,
    observations: Sequence[Rag85CaseObservation],
) -> Rag85ConditionSummary:
    selected = tuple(item for item in observations if item.condition == condition)
    if len(selected) != _CASE_COUNT * _REPEATS:
        raise EvaluationQwenContextPositionError("rag85_condition_observation_count_drift")
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
    fact_observations = len(selected) * _FACTS_PER_CASE
    atomic_count = sum(sum(item.atomic_fact_matches) for item in selected)
    grounded_count = sum(sum(item.citation_grounded_fact_matches) for item in selected)
    majority_atomic_count = sum(sum(values) for values in majority_atomic.values())
    majority_grounded_count = sum(sum(values) for values in majority_grounded.values())
    latencies = sorted(item.latency_ms for item in selected)
    return Rag85ConditionSummary(
        condition=condition,
        required_chunk_ordinals=_REQUIRED_ORDINALS[condition],
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


def _build_pairwise_comparisons(
    observations: Sequence[Rag85CaseObservation],
) -> tuple[Rag85PairwiseComparison, Rag85PairwiseComparison, Rag85PairwiseComparison]:
    scores = {
        position: _majority_case_scores(position, observations=observations)
        for position in _POSITIONS
    }
    preliminary: list[Rag85PairwiseComparison] = []
    for left, right in _PAIR_ORDER:
        case_ids = tuple(sorted(scores[left]))
        if case_ids != tuple(sorted(scores[right])) or len(case_ids) != _CASE_COUNT:
            raise EvaluationQwenContextPositionError("rag85_pairing_case_set_drift")
        differences = tuple(scores[left][case_id] - scores[right][case_id] for case_id in case_ids)
        left_recall = sum(scores[left].values()) / _CASE_COUNT
        right_recall = sum(scores[right].values()) / _CASE_COUNT
        delta = sum(differences) / _CASE_COUNT
        lower, upper = _paired_bootstrap_ci(differences)
        raw_p = _exact_sign_flip_p_value(differences)
        preliminary.append(
            Rag85PairwiseComparison(
                left_condition=left,
                right_condition=right,
                left_majority_recall=round(left_recall, 6),
                right_majority_recall=round(right_recall, 6),
                recall_delta=round(delta, 6),
                paired_bootstrap_ci_lower=lower,
                paired_bootstrap_ci_upper=upper,
                exact_sign_flip_p_value=raw_p,
                holm_adjusted_p_value=1.0,
                effect_size_gate_passed=abs(delta) >= _MINIMUM_ABSOLUTE_DELTA,
                confidence_interval_gate_passed=lower > 0.0 or upper < 0.0,
                holm_gate_passed=False,
                position_dependence_gate_passed=False,
            )
        )
    adjusted = _holm_adjust(tuple(item.exact_sign_flip_p_value for item in preliminary))
    finalized = tuple(
        item.model_copy(
            update={
                "holm_adjusted_p_value": adjusted[index],
                "holm_gate_passed": adjusted[index] <= _HOLM_ALPHA,
                "position_dependence_gate_passed": (
                    item.effect_size_gate_passed
                    and item.confidence_interval_gate_passed
                    and adjusted[index] <= _HOLM_ALPHA
                ),
            }
        )
        for index, item in enumerate(preliminary)
    )
    return finalized  # type: ignore[return-value]


def _paired_bootstrap_ci(differences: Sequence[float]) -> tuple[float, float]:
    if len(differences) != _CASE_COUNT:
        raise EvaluationQwenContextPositionError("rag85_bootstrap_pair_count_drift")
    rng = random.Random(_BOOTSTRAP_SEED)
    samples = sorted(
        sum(differences[rng.randrange(len(differences))] for _ in differences) / len(differences)
        for _ in range(_BOOTSTRAP_RESAMPLES)
    )
    return round(_quantile_type7(samples, 0.025), 6), round(_quantile_type7(samples, 0.975), 6)


def _quantile_type7(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise EvaluationQwenContextPositionError("rag85_bootstrap_samples_missing")
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


def _holm_adjust(p_values: Sequence[float]) -> tuple[float, ...]:
    indexed = sorted(enumerate(p_values), key=lambda item: (item[1], item[0]))
    adjusted = [1.0] * len(p_values)
    running_max = 0.0
    total = len(p_values)
    for rank, (original_index, value) in enumerate(indexed):
        running_max = max(running_max, min(1.0, (total - rank) * value))
        adjusted[original_index] = running_max
    return tuple(adjusted)


def _majority_case_scores(
    condition: Position,
    *,
    observations: Sequence[Rag85CaseObservation],
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
    observations: Sequence[Rag85CaseObservation],
) -> dict[str, tuple[Rag85CaseObservation, ...]]:
    case_ids = {item.case_id for item in observations}
    if len(case_ids) != _CASE_COUNT:
        raise EvaluationQwenContextPositionError("rag85_observation_case_count_drift")
    grouped = {
        case_id: tuple(item for item in observations if item.case_id == case_id)
        for case_id in case_ids
    }
    if any(len(items) != _REPEATS for items in grouped.values()):
        raise EvaluationQwenContextPositionError("rag85_observation_repeat_count_drift")
    return grouped


def _build_position_material(
    case: EvaluationCaseV2Spec,
    *,
    case_ordinal: int,
    position: Position,
    documents: dict[str, EvaluationCorpusDocumentSpec],
    document_ids: dict[str, int],
) -> _PositionMaterial:
    source_keys = tuple(dict.fromkeys(item.source_key for item in case.expected_evidence))
    if len(source_keys) != _FACTS_PER_CASE:
        raise EvaluationQwenContextPositionError("rag85_required_source_count_drift")
    required_items: list[GenerationContextItem] = []
    required_sources: list[CitationSource] = []
    for citation_id, source_key in enumerate(source_keys, start=1):
        try:
            document = documents[source_key]
            document_id = document_ids[source_key]
            body = document.body
            title = document.title
        except (KeyError, AttributeError) as exc:
            raise EvaluationQwenContextPositionError("rag85_required_source_unbound") from exc
        normalized = " ".join(str(body).split())
        if not normalized:
            raise EvaluationQwenContextPositionError("rag85_required_source_empty")
        required_items.append(
            GenerationContextItem(
                document_chunk_id=document_id,
                source_label=source_key,
                text=normalized,
                local_citation_id=citation_id,
            )
        )
        required_sources.append(
            CitationSource(
                local_citation_id=citation_id,
                retrieval_run_item_id=document_id,
                document_chunk_id=document_id,
                source_label=source_key,
                snippet=normalized,
                page_from=None,
                page_to=None,
                section_title=str(title),
            )
        )
    distractor_items: list[GenerationContextItem] = []
    distractor_sources: list[CitationSource] = []
    for distractor_ordinal in range(1, _DISTRACTOR_CHUNKS + 1):
        citation_id = distractor_ordinal + _FACTS_PER_CASE
        chunk_id = 850_000 + case_ordinal * 10 + distractor_ordinal
        source_label = f"rag85_spacer_{case_ordinal:02d}_{distractor_ordinal:02d}"
        text = (
            f"Position-control spacer {distractor_ordinal} for synthetic diagnostic case "
            f"{case_ordinal:02d}. This safe spacer is not evidence for the requested "
            "beacon code or audit interval."
        )
        distractor_items.append(
            GenerationContextItem(
                document_chunk_id=chunk_id,
                source_label=source_label,
                text=text,
                local_citation_id=citation_id,
            )
        )
        distractor_sources.append(
            CitationSource(
                local_citation_id=citation_id,
                retrieval_run_item_id=chunk_id,
                document_chunk_id=chunk_id,
                source_label=source_label,
                snippet=text,
                page_from=None,
                page_to=None,
                section_title="RAG-85 safe synthetic position-control spacer",
            )
        )
    if position == "front":
        ordered_items = (*required_items, *distractor_items)
    elif position == "middle":
        ordered_items = (
            *distractor_items[:3],
            *required_items,
            *distractor_items[3:],
        )
    else:
        ordered_items = (*distractor_items, *required_items)
    if sum(len(item.text) for item in ordered_items) > _MAX_CONTEXT_CHARS:
        raise EvaluationQwenContextPositionError("rag85_context_budget_exhausted")
    context_sequence_hash = _sha256_bytes(
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
                    for index, item in enumerate(ordered_items, start=1)
                ]
            }
        )
    )
    return _PositionMaterial(
        context_items=tuple(ordered_items),
        citation_sources=tuple((*required_sources, *distractor_sources)),
        context_sequence_hash=context_sequence_hash,
    )


def _validate_position_materials(materials: dict[Position, _PositionMaterial]) -> None:
    if set(materials) != set(_POSITIONS):
        raise EvaluationQwenContextPositionError("rag85_condition_set_drift")
    expected_set_hash: str | None = None
    for position in _POSITIONS:
        material = materials[position]
        if len(material.context_items) != _CONTEXT_CHUNKS:
            raise EvaluationQwenContextPositionError("rag85_context_chunk_count_drift")
        citation_ids = tuple(item.local_citation_id for item in material.context_items)
        if set(citation_ids) != set(range(1, _CONTEXT_CHUNKS + 1)):
            raise EvaluationQwenContextPositionError("rag85_context_citation_id_drift")
        required_ordinals = tuple(
            index
            for index, item in enumerate(material.context_items, start=1)
            if item.local_citation_id in {1, 2}
        )
        if required_ordinals != _REQUIRED_ORDINALS[position]:
            raise EvaluationQwenContextPositionError("rag85_required_position_drift")
        sources_by_id = {item.local_citation_id: item for item in material.citation_sources}
        if set(sources_by_id) != set(range(1, _CONTEXT_CHUNKS + 1)):
            raise EvaluationQwenContextPositionError("rag85_citation_source_set_drift")
        for item in material.context_items:
            source = sources_by_id.get(item.local_citation_id or -1)
            if source is None or (
                source.document_chunk_id != item.document_chunk_id
                or source.source_label != item.source_label
                or source.snippet != item.text
            ):
                raise EvaluationQwenContextPositionError("rag85_citation_source_binding_drift")
        actual_set_hash = _material_chunk_set_hash(material)
        if expected_set_hash is None:
            expected_set_hash = actual_set_hash
        elif actual_set_hash != expected_set_hash:
            raise EvaluationQwenContextPositionError("rag85_chunk_set_drift")


def _material_chunk_set_hash(material: _PositionMaterial) -> str:
    return _sha256_bytes(
        canonical_json_bytes(
            {
                "chunks": sorted(
                    (
                        {
                            "citation_id": item.local_citation_id,
                            "document_chunk_id": item.document_chunk_id,
                            "source_label_hash": _sha256(item.source_label),
                            "content_hash": _sha256(item.text),
                        }
                        for item in material.context_items
                    ),
                    key=lambda item: int(item["citation_id"] or 0),
                )
            }
        )
    )


def _selected_cases() -> tuple[EvaluationCaseV2Spec, ...]:
    fixture = build_qwen_multifact_confirm_manifest()
    selected = tuple(
        sorted(
            (case for case in fixture.cases if case.answerable and "multi_hop" in case.tags),
            key=lambda item: item.case_key,
        )
    )
    if len(selected) != _CASE_COUNT:
        raise EvaluationQwenContextPositionError("rag85_fixture_case_count_drift")
    return selected


def _validate_prelive_commit(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvaluationQwenContextPositionError("rag85_prelive_commit_invalid")


def _validate_pre_inventory(inventory: Rag85LMInventorySummary) -> None:
    if not inventory.available:
        raise EvaluationQwenContextPositionError("rag85_pre_lm_inventory_unavailable")
    if inventory.target_loaded_instance_count != 1:
        raise EvaluationQwenContextPositionError("rag85_target_model_not_exactly_once_loaded")
    if inventory.target_loaded_context_length != _TARGET_LOADED_CONTEXT_LENGTH:
        raise EvaluationQwenContextPositionError("rag85_target_model_context_length_drift")


def _manifest_sha256(manifest: Rag85ExperimentManifest) -> str:
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
