from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.evaluation.generation_prompt_profiles import resolve_generation_prompt_profile
from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.rag.citations import CitationBuildError, CitationSource
from app.rag.generation import AnswerGenerationError, AnswerGenerator, GenerationContextItem
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
    _generate_oracle_answer,
    _generate_review_case_with_hard_timeout,
    _prepare_fixture_oracle_material,
    _review_generation_settings,
)
from app.services.evaluation_qwen_context_near_miss_service import Rag86LMInventorySummary
from app.services.evaluation_qwen_multifact_completeness_service import _selected_cases
from app.services.rag_service import _is_insufficient_evidence_answer

_MODEL: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
_BASELINE_PROFILE: Literal["baseline"] = "baseline"
_DATASET: Literal["rag87_qwen_confirm_fixture_sensitivity_v1"] = (
    "rag87_qwen_confirm_fixture_sensitivity_v1"
)
_STACKED_BASE = "a62977d3ac1c8d411c1441f886db9b96011995dd"
_PRIVATE_SEED: Literal[87087] = 87087
_PRIVATE_NAMESPACE: Literal["R87-sensitivity-20260826"] = "R87-sensitivity-20260826"
_CASE_COUNT: Literal[12] = 12
_FACTS_PER_CASE: Literal[2] = 2
_REPEATS: Literal[3] = 3
_EXPECTED_GENERATIONS: Literal[36] = 36
_MAX_CONTEXT_CHARS: Literal[6000] = 6000
_MAX_OUTPUT_CHARS: Literal[12000] = 12000
_MAX_OUTPUT_TOKENS: Literal[8192] = 8192
_CASE_TIMEOUT_SECONDS: Literal[180] = 180
_TARGET_LOADED_CONTEXT_LENGTH: Literal[12312] = 12312
_MAJORITY_MINIMUM_REPEATS: Literal[2] = 2
_MINIMUM_MAJORITY_RECALL = 0.375
_MAXIMUM_MAJORITY_RECALL = 0.833333
_MINIMUM_COMPLETE_CASES: Literal[2] = 2
_MINIMUM_FIRST_ONLY_INSUFFICIENCY_CASES: Literal[2] = 2
_MINIMUM_FACT_ORDINAL_RECALL_GAP = 0.166667
_MAXIMUM_REPEAT_RECALL_RANGE = 0.25
_QUESTION_LENGTHS = (37,) * 6 + (58,) * 6
_SOURCE_LENGTH_PAIRS = (
    (37, 64),
    (37, 64),
    (37, 64),
    (37, 64),
    (37, 64),
    (37, 119),
    (64, 64),
    (64, 64),
    (64, 64),
    (64, 119),
    (92, 64),
    (92, 119),
)
_FACT_LENGTH_PAIRS = ((19, 46),) * 8 + ((46, 46),) * 4
_HIGH_OVERLAP_CASE_ORDINALS = frozenset({9, 10, 11, 12})
_INJECTION_CASE_ORDINALS = frozenset({6, 7})
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
Conclusion = Literal[
    "fixture_sensitivity_established",
    "fixture_sensitivity_not_established",
    "inconclusive",
]


class EvaluationQwenConfirmFixtureSensitivityError(RuntimeError):
    """Stable fail-closed error for the frozen RAG-87 diagnostic."""


class Rag87PrivateFixtureEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["phase3.rag87_private_fixture.v1"]
    generation_seed: Literal[87087]
    namespace: Literal["R87-sensitivity-20260826"]
    dataset: EvaluationDatasetManifestV2


class Rag87StructuralFeatures(StrictRawFreeModel):
    case_count: Literal[12]
    multi_source_case_count: Literal[12]
    required_facts_per_case: tuple[Literal[2], ...]
    required_citations_per_case: tuple[Literal[2], ...]
    language_ja_count: Literal[6]
    language_en_count: Literal[6]
    prompt_injection_tag_count: Literal[2]
    question_lengths_sorted: tuple[int, ...]
    source_lengths_sorted: tuple[int, ...]
    context_lengths_sorted: tuple[int, ...]
    fact_lengths_sorted: tuple[int, ...]
    fact_start_distances_sorted: tuple[int, ...]
    cross_source_ascii_jaccard_ppm_sorted: tuple[int, ...]
    literal_fact_occurrence_count: Literal[24]
    first_source_then_second_source_count: Literal[12]
    feature_extractor: Literal["rag87_char_length_fact_start_ascii_token_jaccard_v1"]

    @model_validator(mode="after")
    def validate_frozen_distribution(self) -> Self:
        expected = (
            tuple(sorted(_QUESTION_LENGTHS)),
            tuple(sorted(value for pair in _SOURCE_LENGTH_PAIRS for value in pair)),
            tuple(sorted(sum(pair) for pair in _SOURCE_LENGTH_PAIRS)),
            tuple(sorted(value for pair in _FACT_LENGTH_PAIRS for value in pair)),
            (39,) * 6 + (66,) * 4 + (94,) * 2,
            (0,) * 8 + (454545,) * 4,
        )
        actual = (
            self.question_lengths_sorted,
            self.source_lengths_sorted,
            self.context_lengths_sorted,
            self.fact_lengths_sorted,
            self.fact_start_distances_sorted,
            self.cross_source_ascii_jaccard_ppm_sorted,
        )
        if actual != expected:
            raise ValueError("rag87_structural_feature_distribution_drift")
        if self.required_facts_per_case != (2,) * _CASE_COUNT:
            raise ValueError("rag87_required_fact_shape_drift")
        if self.required_citations_per_case != (2,) * _CASE_COUNT:
            raise ValueError("rag87_required_citation_shape_drift")
        return self


class Rag87CaseBinding(StrictRawFreeModel):
    case_id: SafeId
    language: Literal["ja", "en"]
    prompt_injection_tagged: bool
    question_hash: Sha256
    required_fact_ids: tuple[SafeId, SafeId]
    normalized_fact_hashes: tuple[Sha256, Sha256]
    logical_document_ids: tuple[SafeId, SafeId]
    source_content_hashes: tuple[Sha256, Sha256]
    source_set_hash: Sha256
    context_hash: Sha256
    question_length: int = Field(gt=0)
    source_lengths: tuple[int, int]
    fact_lengths: tuple[int, int]
    fact_start_distance: int = Field(gt=0)
    cross_source_ascii_jaccard_ppm: int = Field(ge=0, le=1_000_000)
    required_citation_count: Literal[2]
    tags: tuple[str, ...]


class Rag87DatasetBinding(StrictRawFreeModel):
    dataset_name: Literal["rag87_qwen_confirm_fixture_sensitivity_v1"]
    private_input_sha256: Sha256
    dataset_content_fingerprint: Sha256
    corpus_fingerprint: Sha256
    case_count: Literal[12]
    required_fact_count: Literal[24]
    source_count: Literal[24]
    case_set_fingerprint: Sha256
    question_set_fingerprint: Sha256
    normalized_fact_set_fingerprint: Sha256
    source_content_set_fingerprint: Sha256
    logical_document_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    structural_features: Rag87StructuralFeatures
    structural_features_fingerprint: Sha256
    cases: tuple[Rag87CaseBinding, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if len(self.cases) != _CASE_COUNT:
            raise ValueError("rag87_dataset_case_count_drift")
        if len({item.case_id for item in self.cases}) != _CASE_COUNT:
            raise ValueError("rag87_dataset_case_identity_drift")
        return self


class Rag87IndependenceProof(StrictRawFreeModel):
    tune_fixture_dataset: Literal["local_accuracy_dev_v1"]
    confirm_fixture_dataset: Literal["rag87_qwen_confirm_fixture_sensitivity_v1"]
    tune_confirm_question_hash_overlap_count: Literal[0]
    tune_confirm_normalized_fact_hash_overlap_count: Literal[0]
    tune_confirm_source_content_hash_overlap_count: Literal[0]
    tune_confirm_logical_document_id_overlap_count: Literal[0]
    private_fixture_generation_seed: Literal[87087]
    private_fixture_namespace: Literal["R87-sensitivity-20260826"]
    tune_structural_features_fingerprint: Sha256
    confirm_structural_features_fingerprint: Sha256
    structural_features_exact_match: Literal[True]
    raw_tune_content_copied: Literal[False]
    gold_v2_opened_for_design: Literal[False]
    gold_v2_mutated: Literal[False]
    result_based_case_selection_allowed: Literal[False]
    post_result_fixture_rebuild_allowed: Literal[False]


class Rag87GenerationContract(StrictRawFreeModel):
    retrieval_mode: Literal["static_oracle_private_fixture"]
    source_order_frozen: Literal[True]
    citation_ids_frozen: Literal[True]
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
    repeats: Literal[3]
    expected_generation_count: Literal[36]
    execution_order: Literal["repeat_then_case_key_ascending"]
    execution_seed: Literal[87087]
    execution_schedule_fingerprint: Sha256
    baseline_only: Literal[True]
    candidate_comparison_allowed: Literal[False]
    required_facts_or_answer_keys_sent_as_prompt_fields: Literal[False]

    @model_validator(mode="after")
    def validate_temperature(self) -> Self:
        if self.generation_temperature != 0.0:
            raise ValueError("rag87_generation_temperature_drift")
        return self


class Rag87DecisionRule(StrictRawFreeModel):
    primary_metric: Literal["case_majority_atomic_required_fact_recall"]
    fact_majority_minimum_repeats: Literal[2]
    majority_recall_minimum: float
    majority_recall_maximum: float
    majority_complete_case_count_minimum: Literal[2]
    first_only_with_majority_insufficiency_case_count_minimum: Literal[2]
    first_minus_second_majority_recall_minimum: float
    repeat_recall_range_maximum: float
    pipeline_failure_count_maximum: Literal[0]
    binding_drift_count_maximum: Literal[0]
    case_exclusion_count_maximum: Literal[0]
    case_replacement_count_maximum: Literal[0]
    exact_target_model_id_must_match: Literal[True]
    exact_target_loaded_instance_count: Literal[1]
    exact_target_entry_pre_post_must_match: Literal[True]
    exact_target_context_length: Literal[12312]
    full_non_target_inventory_match_required: Literal[False]
    non_target_inventory_drift_recorded: Literal[True]
    failed_fixture_replacement_allowed: Literal[False]
    threshold_adjustment_after_results_allowed: Literal[False]
    follow_on_two_pass_requires_sensitivity_established: Literal[True]

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        actual = (
            self.majority_recall_minimum,
            self.majority_recall_maximum,
            self.first_minus_second_majority_recall_minimum,
            self.repeat_recall_range_maximum,
        )
        expected = (
            _MINIMUM_MAJORITY_RECALL,
            _MAXIMUM_MAJORITY_RECALL,
            _MINIMUM_FACT_ORDINAL_RECALL_GAP,
            _MAXIMUM_REPEAT_RECALL_RANGE,
        )
        if actual != expected:
            raise ValueError("rag87_decision_threshold_drift")
        return self


class Rag87ExperimentManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.rag87_fixture_sensitivity_experiment.v1"]
    jira_issue: Literal["RAG-87"]
    stacked_base_commit: GitSha
    experiment_scope: Literal["baseline_only_independent_confirm_fixture_sensitivity"]
    dataset: Rag87DatasetBinding
    independence: Rag87IndependenceProof
    generation: Rag87GenerationContract
    decision_rule: Rag87DecisionRule
    raw_content_persistence_allowed: Literal[False]
    repository_external_private_input_required: Literal[True]
    external_non_loopback_http_allowed: Literal[False]
    database_write_allowed: Literal[False]
    gold_v2_access_allowed: Literal[False]
    case_exclusion_replacement_extra_repeat_or_rerun_allowed: Literal[False]
    merge_deploy_draft_removal_or_profile_promotion_allowed: Literal[False]


class Rag87ExperimentLock(StrictRawFreeModel):
    schema_version: Literal["phase3.rag87_fixture_sensitivity_lock.v1"]
    stacked_base_commit: GitSha
    experiment_manifest_sha256: Sha256
    dataset_binding_sha256: Sha256
    independence_proof_sha256: Sha256
    generation_contract_sha256: Sha256
    decision_rule_sha256: Sha256
    private_input_sha256: Sha256
    dataset_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    baseline_prompt_fingerprint: Sha256
    execution_schedule_fingerprint: Sha256
    tune_structural_features: Rag87StructuralFeatures
    confirm_structural_features: Rag87StructuralFeatures
    structural_features_exact_match: Literal[True]
    prelive_commit_required: Literal[True]
    one_shot_attempt_marker_required: Literal[True]
    generation_count: Literal[36]
    gold_v2_access_allowed: Literal[False]
    raw_content_persistence_allowed: Literal[False]


class Rag87AttemptState(StrictRawFreeModel):
    schema_version: Literal["phase3.rag87_fixture_sensitivity_attempt.v1"]
    status: Literal["started"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    prelive_commit_sha: GitSha
    pre_full_lm_inventory_fingerprint: Sha256
    pre_target_entry_fingerprint: Sha256
    expected_generation_count: Literal[36]
    repeat_replacement_or_rerun_allowed: Literal[False]
    raw_content_persisted: Literal[False]


class Rag87CaseObservation(StrictRawFreeModel):
    case_id: SafeId
    repeat: int = Field(ge=1, le=3)
    execution_ordinal: int = Field(ge=1, le=36)
    answer_hash: Sha256 | None
    context_hash: Sha256
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


class Rag87SensitivitySummary(StrictRawFreeModel):
    repeats: Literal[3]
    case_count: Literal[12]
    case_observation_count: Literal[36]
    required_fact_observation_count: Literal[72]
    repeat_atomic_required_fact_recalls: tuple[float, float, float]
    repeat_atomic_required_fact_recall_range: float = Field(ge=0.0, le=1.0)
    atomic_required_fact_recall: float = Field(ge=0.0, le=1.0)
    majority_atomic_supported_fact_count: int = Field(ge=0, le=24)
    majority_atomic_required_fact_recall: float = Field(ge=0.0, le=1.0)
    first_fact_majority_recall: float = Field(ge=0.0, le=1.0)
    second_fact_majority_recall: float = Field(ge=0.0, le=1.0)
    first_minus_second_majority_recall: float = Field(ge=-1.0, le=1.0)
    majority_complete_case_count: int = Field(ge=0, le=12)
    majority_first_only_case_count: int = Field(ge=0, le=12)
    first_only_with_majority_insufficiency_case_count: int = Field(ge=0, le=12)
    majority_citation_grounding_recall: float = Field(ge=0.0, le=1.0)
    citation_source_coverage_case_observation_count: int = Field(ge=0, le=36)
    citation_source_coverage_rate: float = Field(ge=0.0, le=1.0)
    insufficiency_false_assertion_case_observation_count: int = Field(ge=0, le=36)
    unexpected_fact_case_count: int = Field(ge=0, le=12)
    forbidden_claim_case_count: int = Field(ge=0, le=12)
    pipeline_failure_count: int = Field(ge=0, le=36)
    p95_latency_ms: int = Field(ge=0)


class Rag87SensitivityChecks(StrictRawFreeModel):
    majority_recall_floor_passed: bool
    majority_recall_ceiling_passed: bool
    majority_complete_case_count_passed: bool
    first_only_with_insufficiency_count_passed: bool
    fact_ordinal_recall_gap_passed: bool
    repeat_stability_passed: bool
    sensitivity_gate_passed: bool


class Rag87ExperimentResult(StrictRawFreeModel):
    schema_version: Literal["phase3.rag87_fixture_sensitivity_result.v1"]
    experiment_manifest_sha256: Sha256
    private_input_sha256: Sha256
    prelive_commit_sha: GitSha
    dataset_name: Literal["rag87_qwen_confirm_fixture_sensitivity_v1"]
    dataset_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    structural_features_fingerprint: Sha256
    model: Literal["qwen/qwen3.5-9b"]
    generation_count: Literal[36]
    case_exclusion_count: Literal[0]
    case_replacement_count: Literal[0]
    binding_drift_count: Literal[0]
    pipeline_failure_count: int = Field(ge=0, le=36)
    pre_lm_inventory: Rag86LMInventorySummary
    post_lm_inventory: Rag86LMInventorySummary
    exact_target_stable: bool
    full_lm_inventory_stable: bool
    non_target_inventory_drift_observed: bool
    validity_gate_passed: bool
    sensitivity_gate_passed: bool
    conclusion: Conclusion
    reason_codes: tuple[str, ...]
    summary: Rag87SensitivitySummary
    sensitivity_checks: Rag87SensitivityChecks
    evaluator: Literal["deterministic_identifier_equivalence_v1"]
    baseline_only: Literal[True]
    candidate_comparison_performed: Literal[False]
    diagnostic_only: Literal[True]
    gold_holdout_eligible: Literal[False]
    public_accuracy_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]
    follow_on_two_pass_authorized: bool
    raw_content_persisted: Literal[False]
    observations: tuple[Rag87CaseObservation, ...]


def build_rag87_private_fixture(private_entropy: bytes) -> Rag87PrivateFixtureEnvelope:
    if len(private_entropy) < 16:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_private_entropy_too_short")
    documents: list[EvaluationCorpusDocumentSpec] = []
    cases: list[EvaluationCaseV2Spec] = []
    for ordinal in range(1, _CASE_COUNT + 1):
        source_lengths = _SOURCE_LENGTH_PAIRS[ordinal - 1]
        fact_lengths = _FACT_LENGTH_PAIRS[ordinal - 1]
        language: Literal["ja", "en"] = "ja" if ordinal <= 6 else "en"
        high_overlap = ordinal in _HIGH_OVERLAP_CASE_ORDINALS
        fact_one, fact_two, subject_one, subject_two = _fact_pair(
            ordinal=ordinal,
            language=language,
            high_overlap=high_overlap,
            lengths=fact_lengths,
            private_entropy=private_entropy,
        )
        injection_tagged = ordinal in _INJECTION_CASE_ORDINALS
        source_one, source_two = _source_pair(
            ordinal=ordinal,
            facts=(fact_one, fact_two),
            lengths=source_lengths,
            injection_tagged=injection_tagged,
        )
        source_one_key = f"R87-SEN-{ordinal:02d}-S1"
        source_two_key = f"R87-SEN-{ordinal:02d}-S2"
        fact_one_id = f"R87-SEN-{ordinal:02d}-F1"
        fact_two_id = f"R87-SEN-{ordinal:02d}-F2"
        documents.extend(
            (
                EvaluationCorpusDocumentSpec(
                    source_key=source_one_key,
                    title=f"R87 sensitivity record {ordinal:02d} A",
                    body=source_one,
                    facts=[EvaluationCorpusFactSpec(fact_id=fact_one_id, statement=fact_one)],
                ),
                EvaluationCorpusDocumentSpec(
                    source_key=source_two_key,
                    title=f"R87 sensitivity record {ordinal:02d} B",
                    body=source_two,
                    facts=[EvaluationCorpusFactSpec(fact_id=fact_two_id, statement=fact_two)],
                ),
            )
        )
        question = _question(
            ordinal=ordinal,
            language=language,
            high_overlap=high_overlap,
            target_length=_QUESTION_LENGTHS[ordinal - 1],
            subjects=(subject_one, subject_two),
        )
        tags = ["answerable", "multi_hop", f"language:{language}"]
        if injection_tagged:
            tags.append("prompt_injection")
        cases.append(
            EvaluationCaseV2Spec(
                case_key=f"R87-SEN-{ordinal:02d}",
                question=question,
                answerable=True,
                expected_answer=f"{fact_one} {fact_two}",
                required_facts=[
                    EvaluationRequiredFactSpec(fact_id=fact_one_id, statement=fact_one),
                    EvaluationRequiredFactSpec(fact_id=fact_two_id, statement=fact_two),
                ],
                expected_evidence=[
                    EvaluationExpectedEvidenceSpec(
                        source_key=source_one_key,
                        fact_ids=[fact_one_id],
                        locator="record-a",
                    ),
                    EvaluationExpectedEvidenceSpec(
                        source_key=source_two_key,
                        fact_ids=[fact_two_id],
                        locator="record-b",
                    ),
                ],
                forbidden_claims=[
                    f"R87-Z{ordinal:02d}{_private_digit(private_entropy, ordinal, 'z')} value 99"
                ],
                required_citation=True,
                expected_strategy=EvaluationRunRequestStrategy.AGENTIC_ROUTER,
                tags=tags,
                metadata_json={
                    "required_hop_count": 2,
                    "expected_answer_slots": [fact_one, fact_two],
                    "private_confirm_only": True,
                    "generation_seed": _PRIVATE_SEED,
                },
            )
        )
    dataset = EvaluationDatasetManifestV2(
        dataset=EvaluationDatasetManifestInfo(
            dataset_name=_DATASET,
            description="Repository-external RAG-87 independent confirm fixture.",
            version="v1",
            source_type=EvaluationDatasetSourceType.FIXTURE,
            metadata_json={
                "private_input": True,
                "generation_seed": _PRIVATE_SEED,
                "namespace": _PRIVATE_NAMESPACE,
            },
        ),
        corpus_documents=documents,
        cases=cases,
        metric_specs=[],
    )
    envelope = Rag87PrivateFixtureEnvelope(
        schema_version="phase3.rag87_private_fixture.v1",
        generation_seed=_PRIVATE_SEED,
        namespace=_PRIVATE_NAMESPACE,
        dataset=dataset,
    )
    _validate_private_fixture(envelope)
    return envelope


def load_rag87_private_fixture(path: Path) -> tuple[bytes, Rag87PrivateFixtureEnvelope]:
    try:
        payload_bytes, payload = read_json_object(path)
        envelope = Rag87PrivateFixtureEnvelope.model_validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_private_fixture_unreadable"
        ) from exc
    if payload_bytes != canonical_json_bytes(envelope):
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_private_fixture_canonical_bytes_mismatch"
        )
    _validate_private_fixture(envelope)
    return payload_bytes, envelope


def build_rag87_experiment_manifest(
    envelope: Rag87PrivateFixtureEnvelope,
    *,
    private_input_sha256: str,
    stacked_base_commit: str = _STACKED_BASE,
) -> Rag87ExperimentManifest:
    _validate_git_sha(stacked_base_commit)
    tune_manifest = build_local_accuracy_dev_manifest()
    tune_cases = _selected_cases(tune_manifest)
    tune_documents = {item.source_key: item for item in tune_manifest.corpus_documents}
    tune_features = _structural_features(tune_cases, tune_documents)
    confirm_documents = {item.source_key: item for item in envelope.dataset.corpus_documents}
    confirm_cases = tuple(sorted(envelope.dataset.cases, key=lambda item: item.case_key))
    confirm_features = _structural_features(confirm_cases, confirm_documents)
    if tune_features != confirm_features:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_structural_feature_match_failed")
    dataset = _build_dataset_binding(
        envelope.dataset,
        private_input_sha256=private_input_sha256,
        structural_features=confirm_features,
    )
    independence = _build_independence_proof(
        tune_cases=tune_cases,
        tune_documents=tune_documents,
        confirm=dataset,
        tune_features=tune_features,
    )
    prompt = resolve_generation_prompt_profile(_BASELINE_PROFILE)
    return Rag87ExperimentManifest(
        schema_version="phase3.rag87_fixture_sensitivity_experiment.v1",
        jira_issue="RAG-87",
        stacked_base_commit=stacked_base_commit,
        experiment_scope="baseline_only_independent_confirm_fixture_sensitivity",
        dataset=dataset,
        independence=independence,
        generation=Rag87GenerationContract(
            retrieval_mode="static_oracle_private_fixture",
            source_order_frozen=True,
            citation_ids_frozen=True,
            generation_provider="lmstudio",
            resolved_generation_model=_MODEL,
            generation_temperature=0.0,
            reasoning_enabled=False,
            generation_prompt_profile=_BASELINE_PROFILE,
            generation_prompt_fingerprint=prompt.prompt_fingerprint,
            generation_max_context_chars=_MAX_CONTEXT_CHARS,
            generation_max_output_chars=_MAX_OUTPUT_CHARS,
            generation_max_output_tokens=_MAX_OUTPUT_TOKENS,
            generation_case_wall_clock_timeout_seconds=_CASE_TIMEOUT_SECONDS,
            lmstudio_loaded_context_length=_TARGET_LOADED_CONTEXT_LENGTH,
            retry_policy="existing_evaluation_generation_retry",
            repeats=_REPEATS,
            expected_generation_count=_EXPECTED_GENERATIONS,
            execution_order="repeat_then_case_key_ascending",
            execution_seed=_PRIVATE_SEED,
            execution_schedule_fingerprint=_execution_schedule_fingerprint(confirm_cases),
            baseline_only=True,
            candidate_comparison_allowed=False,
            required_facts_or_answer_keys_sent_as_prompt_fields=False,
        ),
        decision_rule=Rag87DecisionRule(
            primary_metric="case_majority_atomic_required_fact_recall",
            fact_majority_minimum_repeats=_MAJORITY_MINIMUM_REPEATS,
            majority_recall_minimum=_MINIMUM_MAJORITY_RECALL,
            majority_recall_maximum=_MAXIMUM_MAJORITY_RECALL,
            majority_complete_case_count_minimum=_MINIMUM_COMPLETE_CASES,
            first_only_with_majority_insufficiency_case_count_minimum=(
                _MINIMUM_FIRST_ONLY_INSUFFICIENCY_CASES
            ),
            first_minus_second_majority_recall_minimum=(_MINIMUM_FACT_ORDINAL_RECALL_GAP),
            repeat_recall_range_maximum=_MAXIMUM_REPEAT_RECALL_RANGE,
            pipeline_failure_count_maximum=0,
            binding_drift_count_maximum=0,
            case_exclusion_count_maximum=0,
            case_replacement_count_maximum=0,
            exact_target_model_id_must_match=True,
            exact_target_loaded_instance_count=1,
            exact_target_entry_pre_post_must_match=True,
            exact_target_context_length=_TARGET_LOADED_CONTEXT_LENGTH,
            full_non_target_inventory_match_required=False,
            non_target_inventory_drift_recorded=True,
            failed_fixture_replacement_allowed=False,
            threshold_adjustment_after_results_allowed=False,
            follow_on_two_pass_requires_sensitivity_established=True,
        ),
        raw_content_persistence_allowed=False,
        repository_external_private_input_required=True,
        external_non_loopback_http_allowed=False,
        database_write_allowed=False,
        gold_v2_access_allowed=False,
        case_exclusion_replacement_extra_repeat_or_rerun_allowed=False,
        merge_deploy_draft_removal_or_profile_promotion_allowed=False,
    )


def build_rag87_experiment_lock(
    manifest: Rag87ExperimentManifest,
) -> Rag87ExperimentLock:
    tune_features = _tune_structural_features()
    return Rag87ExperimentLock(
        schema_version="phase3.rag87_fixture_sensitivity_lock.v1",
        stacked_base_commit=manifest.stacked_base_commit,
        experiment_manifest_sha256=_manifest_sha256(manifest),
        dataset_binding_sha256=_sha256_bytes(canonical_json_bytes(manifest.dataset)),
        independence_proof_sha256=_sha256_bytes(canonical_json_bytes(manifest.independence)),
        generation_contract_sha256=_sha256_bytes(canonical_json_bytes(manifest.generation)),
        decision_rule_sha256=_sha256_bytes(canonical_json_bytes(manifest.decision_rule)),
        private_input_sha256=manifest.dataset.private_input_sha256,
        dataset_content_fingerprint=manifest.dataset.dataset_content_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        baseline_prompt_fingerprint=manifest.generation.generation_prompt_fingerprint,
        execution_schedule_fingerprint=manifest.generation.execution_schedule_fingerprint,
        tune_structural_features=tune_features,
        confirm_structural_features=manifest.dataset.structural_features,
        structural_features_exact_match=True,
        prelive_commit_required=True,
        one_shot_attempt_marker_required=True,
        generation_count=_EXPECTED_GENERATIONS,
        gold_v2_access_allowed=False,
        raw_content_persistence_allowed=False,
    )


def load_frozen_rag87_experiment_manifest(
    lock_path: Path,
    private_input_path: Path,
) -> tuple[Rag87ExperimentManifest, Rag87PrivateFixtureEnvelope]:
    try:
        lock_bytes, lock_payload = read_json_object(lock_path)
        lock = Rag87ExperimentLock.model_validate(lock_payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_lock_unreadable") from exc
    if not model_bytes_match(lock_bytes, lock):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_lock_bytes_model_mismatch")
    private_bytes, envelope = load_rag87_private_fixture(private_input_path)
    private_hash = _sha256_bytes(private_bytes)
    if private_hash != lock.private_input_sha256:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_private_fixture_hash_drift")
    manifest = build_rag87_experiment_manifest(
        envelope,
        private_input_sha256=private_hash,
        stacked_base_commit=lock.stacked_base_commit,
    )
    if build_rag87_experiment_lock(manifest) != lock:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_lock_runtime_drift")
    return manifest, envelope


def build_rag87_attempt_state(
    manifest: Rag87ExperimentManifest,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag86LMInventorySummary,
) -> Rag87AttemptState:
    _validate_git_sha(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    if pre_lm_inventory.full_inventory_fingerprint is None:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_pre_inventory_fingerprint_missing"
        )
    if pre_lm_inventory.target_entry_fingerprint is None:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_pre_target_entry_fingerprint_missing"
        )
    return Rag87AttemptState(
        schema_version="phase3.rag87_fixture_sensitivity_attempt.v1",
        status="started",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        private_input_sha256=manifest.dataset.private_input_sha256,
        prelive_commit_sha=prelive_commit_sha,
        pre_full_lm_inventory_fingerprint=pre_lm_inventory.full_inventory_fingerprint,
        pre_target_entry_fingerprint=pre_lm_inventory.target_entry_fingerprint,
        expected_generation_count=_EXPECTED_GENERATIONS,
        repeat_replacement_or_rerun_allowed=False,
        raw_content_persisted=False,
    )


def _fit_text(value: str, target_length: int) -> str:
    if len(value) > target_length:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_private_fixture_text_too_long")
    return value + ("。" * (target_length - len(value)))


def _fact_pair(
    *,
    ordinal: int,
    language: Literal["ja", "en"],
    high_overlap: bool,
    lengths: tuple[int, int],
    private_entropy: bytes,
) -> tuple[str, str, str, str]:
    digits = tuple(
        _private_digit(private_entropy, ordinal, label)
        for label in ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k")
    )
    if high_overlap:
        tokens = tuple(
            f"{label}{ordinal:02d}{digit}"
            for label, digit in zip(
                ("n", "r", "a", "b", "c", "p", "q", "s", "t", "u", "v"),
                digits,
                strict=True,
            )
        )
        common = " ".join(tokens[:5])
        fact_one = " ".join((common, *tokens[5:7]))
        fact_two = " ".join((common, *tokens[7:]))
        subject_one = tokens[0].upper()
        subject_two = subject_one
    elif language == "ja":
        subject_one = f"A{ordinal:02d}{digits[0]}"
        subject_two = f"B{ordinal:02d}{digits[1]}"
        value_one = 20 + digits[2]
        value_two = 40 + digits[3]
        rule = f"R{ordinal:02d}{digits[4]}"
        fact_one = f"{subject_one}の値は{value_one}ms"
        fact_two = f"{subject_two}の間隔は{value_two}日で{rule}に従う"
    else:
        subject_one = f"A{ordinal:02d}{digits[0]}"
        subject_two = f"B{ordinal:02d}{digits[1]}"
        value_one = 20 + digits[2]
        value_two = 40 + digits[3]
        rule = f"R{ordinal:02d}{digits[4]}"
        fact_one = f"{subject_one} rate {value_one} ms"
        fact_two = f"{subject_two} interval {value_two} days under {rule}"
    return (
        _fit_text(fact_one, lengths[0]),
        _fit_text(fact_two, lengths[1]),
        subject_one,
        subject_two,
    )


def _source_pair(
    *,
    ordinal: int,
    facts: tuple[str, str],
    lengths: tuple[int, int],
    injection_tagged: bool,
) -> tuple[str, str]:
    del ordinal, injection_tagged
    prefix = "※" * 18
    source_one = _fit_text(f"{prefix}{facts[0]}", lengths[0])
    source_two = _fit_text(f"{prefix}{facts[1]}", lengths[1])
    return source_one, source_two


def _question(
    *,
    ordinal: int,
    language: Literal["ja", "en"],
    high_overlap: bool,
    target_length: int,
    subjects: tuple[str, str],
) -> str:
    if high_overlap:
        value = f"Give both records for {subjects[0]}; include the second record"
    elif language == "ja":
        value = f"{subjects[0]}と{subjects[1]}の値と間隔を両方答えてください"
    else:
        value = f"Give both records for {subjects[0]} and {subjects[1]}; include the second"
    return _fit_text(value, target_length)


def _private_digit(private_entropy: bytes, ordinal: int, label: str) -> int:
    digest = hashlib.sha256(
        private_entropy
        + b"\x00"
        + _PRIVATE_NAMESPACE.encode("ascii")
        + b"\x00"
        + str(_PRIVATE_SEED).encode("ascii")
        + b"\x00"
        + f"{ordinal}:{label}".encode("ascii")
    ).digest()
    return digest[0] % 10


def _validate_private_fixture(envelope: Rag87PrivateFixtureEnvelope) -> None:
    dataset = envelope.dataset
    if dataset.dataset.dataset_name != _DATASET:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_private_fixture_dataset_name_drift"
        )
    documents = {item.source_key: item for item in dataset.corpus_documents}
    cases = tuple(sorted(dataset.cases, key=lambda item: item.case_key))
    if len(documents) != _CASE_COUNT * 2 or len(cases) != _CASE_COUNT:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_private_fixture_cardinality_drift"
        )
    features = _structural_features(cases, documents)
    if features != Rag87StructuralFeatures(
        case_count=12,
        multi_source_case_count=12,
        required_facts_per_case=(2,) * 12,
        required_citations_per_case=(2,) * 12,
        language_ja_count=6,
        language_en_count=6,
        prompt_injection_tag_count=2,
        question_lengths_sorted=tuple(sorted(_QUESTION_LENGTHS)),
        source_lengths_sorted=tuple(
            sorted(value for pair in _SOURCE_LENGTH_PAIRS for value in pair)
        ),
        context_lengths_sorted=tuple(sorted(sum(pair) for pair in _SOURCE_LENGTH_PAIRS)),
        fact_lengths_sorted=tuple(sorted(value for pair in _FACT_LENGTH_PAIRS for value in pair)),
        fact_start_distances_sorted=(39,) * 6 + (66,) * 4 + (94,) * 2,
        cross_source_ascii_jaccard_ppm_sorted=(0,) * 8 + (454545,) * 4,
        literal_fact_occurrence_count=24,
        first_source_then_second_source_count=12,
        feature_extractor="rag87_char_length_fact_start_ascii_token_jaccard_v1",
    ):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_private_fixture_structural_drift")


def _structural_features(
    cases: Sequence[EvaluationCaseV2Spec],
    documents: dict[str, EvaluationCorpusDocumentSpec],
) -> Rag87StructuralFeatures:
    question_lengths: list[int] = []
    source_lengths: list[int] = []
    context_lengths: list[int] = []
    fact_lengths: list[int] = []
    fact_start_distances: list[int] = []
    jaccard_values: list[int] = []
    facts_per_case: list[Literal[2]] = []
    citations_per_case: list[Literal[2]] = []
    language_ja_count = 0
    language_en_count = 0
    injection_count = 0
    literal_occurrence_count = 0
    ordered_count = 0
    for case in sorted(cases, key=lambda item: item.case_key):
        if len(case.required_facts) != 2 or len(case.expected_evidence) != 2:
            raise EvaluationQwenConfirmFixtureSensitivityError(
                "rag87_structural_required_shape_drift"
            )
        source_keys = tuple(item.source_key for item in case.expected_evidence)
        try:
            bodies = tuple(documents[key].body for key in source_keys)
        except KeyError as exc:
            raise EvaluationQwenConfirmFixtureSensitivityError(
                "rag87_structural_source_missing"
            ) from exc
        facts = tuple(item.statement for item in case.required_facts)
        if any(body.count(fact) != 1 for body, fact in zip(bodies, facts, strict=True)):
            raise EvaluationQwenConfirmFixtureSensitivityError(
                "rag87_structural_literal_fact_occurrence_drift"
            )
        if facts[0] in bodies[1] or facts[1] in bodies[0]:
            raise EvaluationQwenConfirmFixtureSensitivityError(
                "rag87_structural_cross_source_fact_drift"
            )
        joined = "\n\n".join(bodies)
        first_start = joined.find(facts[0])
        second_start = joined.find(facts[1])
        if first_start < 0 or second_start <= first_start:
            raise EvaluationQwenConfirmFixtureSensitivityError(
                "rag87_structural_source_order_drift"
            )
        cross_scores = []
        for fact, sibling_body in ((facts[0], bodies[1]), (facts[1], bodies[0])):
            fact_tokens = set(re.findall(r"[a-z0-9]+", fact.lower()))
            sibling_tokens = set(re.findall(r"[a-z0-9]+", sibling_body.lower()))
            union = fact_tokens | sibling_tokens
            cross_scores.append(len(fact_tokens & sibling_tokens) / len(union) if union else 0.0)
        tags = set(case.tags)
        language_ja_count += "language:ja" in tags
        language_en_count += "language:en" in tags
        injection_count += "prompt_injection" in tags
        question_lengths.append(len(case.question))
        source_lengths.extend(len(body) for body in bodies)
        context_lengths.append(sum(len(body) for body in bodies))
        fact_lengths.extend(len(fact) for fact in facts)
        fact_start_distances.append(second_start - first_start)
        jaccard_values.append(round(max(cross_scores) * 1_000_000))
        facts_per_case.append(2)
        citations_per_case.append(2)
        literal_occurrence_count += 2
        ordered_count += 1
    return Rag87StructuralFeatures(
        case_count=12,
        multi_source_case_count=12,
        required_facts_per_case=tuple(facts_per_case),
        required_citations_per_case=tuple(citations_per_case),
        language_ja_count=language_ja_count,
        language_en_count=language_en_count,
        prompt_injection_tag_count=injection_count,
        question_lengths_sorted=tuple(sorted(question_lengths)),
        source_lengths_sorted=tuple(sorted(source_lengths)),
        context_lengths_sorted=tuple(sorted(context_lengths)),
        fact_lengths_sorted=tuple(sorted(fact_lengths)),
        fact_start_distances_sorted=tuple(sorted(fact_start_distances)),
        cross_source_ascii_jaccard_ppm_sorted=tuple(sorted(jaccard_values)),
        literal_fact_occurrence_count=literal_occurrence_count,
        first_source_then_second_source_count=ordered_count,
        feature_extractor="rag87_char_length_fact_start_ascii_token_jaccard_v1",
    )


def _tune_structural_features() -> Rag87StructuralFeatures:
    manifest = build_local_accuracy_dev_manifest()
    return _structural_features(
        _selected_cases(manifest),
        {item.source_key: item for item in manifest.corpus_documents},
    )


def _build_dataset_binding(
    dataset: EvaluationDatasetManifestV2,
    *,
    private_input_sha256: str,
    structural_features: Rag87StructuralFeatures,
) -> Rag87DatasetBinding:
    documents = {item.source_key: item for item in dataset.corpus_documents}
    case_bindings: list[Rag87CaseBinding] = []
    question_hashes: list[str] = []
    fact_hashes: list[str] = []
    content_hashes: list[str] = []
    document_ids: list[str] = []
    context_hashes: list[str] = []
    for case in sorted(dataset.cases, key=lambda item: item.case_key):
        evidence = tuple(case.expected_evidence)
        source_keys = tuple(item.source_key for item in evidence)
        bodies = tuple(documents[key].body for key in source_keys)
        facts = tuple(item.statement for item in case.required_facts)
        language: Literal["ja", "en"] = "ja" if "language:ja" in case.tags else "en"
        question_hash = _sha256(case.question)
        normalized_hashes = tuple(_sha256(_normalize_identifier_text(item)) for item in facts)
        source_hashes = tuple(_sha256(item) for item in bodies)
        context_hash = _sha256("\n".join(bodies))
        fact_start_distance = (
            len(bodies[0]) + 2 + bodies[1].find(facts[1]) - bodies[0].find(facts[0])
        )
        tokens_one = set(re.findall(r"[a-z0-9]+", facts[0].lower()))
        tokens_two_source = set(re.findall(r"[a-z0-9]+", bodies[1].lower()))
        tokens_two = set(re.findall(r"[a-z0-9]+", facts[1].lower()))
        tokens_one_source = set(re.findall(r"[a-z0-9]+", bodies[0].lower()))
        scores = []
        for left, right in ((tokens_one, tokens_two_source), (tokens_two, tokens_one_source)):
            union = left | right
            scores.append(len(left & right) / len(union) if union else 0.0)
        case_bindings.append(
            Rag87CaseBinding(
                case_id=case.case_key,
                language=language,
                prompt_injection_tagged="prompt_injection" in case.tags,
                question_hash=question_hash,
                required_fact_ids=(case.required_facts[0].fact_id, case.required_facts[1].fact_id),
                normalized_fact_hashes=(normalized_hashes[0], normalized_hashes[1]),
                logical_document_ids=(source_keys[0], source_keys[1]),
                source_content_hashes=(source_hashes[0], source_hashes[1]),
                source_set_hash=_fingerprint_set(source_hashes),
                context_hash=context_hash,
                question_length=len(case.question),
                source_lengths=(len(bodies[0]), len(bodies[1])),
                fact_lengths=(len(facts[0]), len(facts[1])),
                fact_start_distance=fact_start_distance,
                cross_source_ascii_jaccard_ppm=round(max(scores) * 1_000_000),
                required_citation_count=2,
                tags=tuple(sorted(case.tags)),
            )
        )
        question_hashes.append(question_hash)
        fact_hashes.extend(normalized_hashes)
        content_hashes.extend(source_hashes)
        document_ids.extend(source_keys)
        context_hashes.append(context_hash)
    return Rag87DatasetBinding(
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
        case_count=12,
        required_fact_count=24,
        source_count=24,
        case_set_fingerprint=_fingerprint_set(tuple(item.case_id for item in case_bindings)),
        question_set_fingerprint=_fingerprint_set(question_hashes),
        normalized_fact_set_fingerprint=_fingerprint_set(fact_hashes),
        source_content_set_fingerprint=_fingerprint_set(content_hashes),
        logical_document_set_fingerprint=_fingerprint_set(document_ids),
        source_context_fingerprint=_fingerprint_set(context_hashes),
        structural_features=structural_features,
        structural_features_fingerprint=_sha256_bytes(canonical_json_bytes(structural_features)),
        cases=tuple(case_bindings),
    )


def _build_independence_proof(
    *,
    tune_cases: Sequence[EvaluationCaseV2Spec],
    tune_documents: dict[str, EvaluationCorpusDocumentSpec],
    confirm: Rag87DatasetBinding,
    tune_features: Rag87StructuralFeatures,
) -> Rag87IndependenceProof:
    tune_question_hashes = {_sha256(item.question) for item in tune_cases}
    tune_fact_hashes = {
        _sha256(_normalize_identifier_text(fact.statement))
        for case in tune_cases
        for fact in case.required_facts
    }
    tune_source_keys = {
        evidence.source_key for case in tune_cases for evidence in case.expected_evidence
    }
    tune_source_hashes = {_sha256(tune_documents[key].body) for key in tune_source_keys}
    confirm_question_hashes = {item.question_hash for item in confirm.cases}
    confirm_fact_hashes = {value for item in confirm.cases for value in item.normalized_fact_hashes}
    confirm_source_hashes = {
        value for item in confirm.cases for value in item.source_content_hashes
    }
    confirm_document_ids = {value for item in confirm.cases for value in item.logical_document_ids}
    overlaps = (
        len(tune_question_hashes & confirm_question_hashes),
        len(tune_fact_hashes & confirm_fact_hashes),
        len(tune_source_hashes & confirm_source_hashes),
        len(tune_source_keys & confirm_document_ids),
    )
    if overlaps != (0, 0, 0, 0):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_tune_confirm_independence_drift")
    return Rag87IndependenceProof(
        tune_fixture_dataset="local_accuracy_dev_v1",
        confirm_fixture_dataset=_DATASET,
        tune_confirm_question_hash_overlap_count=0,
        tune_confirm_normalized_fact_hash_overlap_count=0,
        tune_confirm_source_content_hash_overlap_count=0,
        tune_confirm_logical_document_id_overlap_count=0,
        private_fixture_generation_seed=_PRIVATE_SEED,
        private_fixture_namespace=_PRIVATE_NAMESPACE,
        tune_structural_features_fingerprint=_sha256_bytes(canonical_json_bytes(tune_features)),
        confirm_structural_features_fingerprint=confirm.structural_features_fingerprint,
        structural_features_exact_match=True,
        raw_tune_content_copied=False,
        gold_v2_opened_for_design=False,
        gold_v2_mutated=False,
        result_based_case_selection_allowed=False,
        post_result_fixture_rebuild_allowed=False,
    )


def run_rag87_diagnostic(
    manifest: Rag87ExperimentManifest,
    envelope: Rag87PrivateFixtureEnvelope,
    *,
    prelive_commit_sha: str,
    pre_lm_inventory: Rag86LMInventorySummary,
    post_lm_inventory_provider: Callable[[], Rag86LMInventorySummary],
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag87ExperimentResult:
    _validate_git_sha(prelive_commit_sha)
    _validate_pre_inventory(pre_lm_inventory)
    runtime_manifest = build_rag87_experiment_manifest(
        envelope,
        private_input_sha256=manifest.dataset.private_input_sha256,
        stacked_base_commit=manifest.stacked_base_commit,
    )
    if runtime_manifest != manifest:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_runtime_manifest_drift")
    cases = tuple(sorted(envelope.dataset.cases, key=lambda item: item.case_key))
    documents = {item.source_key: item for item in envelope.dataset.corpus_documents}
    document_ids = {
        item.source_key: index
        for index, item in enumerate(
            sorted(envelope.dataset.corpus_documents, key=lambda item: item.source_key),
            start=1,
        )
    }
    bindings = {item.case_id: item for item in manifest.dataset.cases}
    observations: list[Rag87CaseObservation] = []
    for repeat in range(1, _REPEATS + 1):
        for case in cases:
            binding = bindings.get(case.case_key)
            if binding is None:
                raise EvaluationQwenConfirmFixtureSensitivityError("rag87_case_binding_missing")
            context_items, citation_sources, context, _ = _prepare_fixture_oracle_material(
                case,
                documents,
                document_ids,
            )
            context_hash = _sha256("\n".join(context))
            if context_hash != binding.context_hash:
                raise EvaluationQwenConfirmFixtureSensitivityError("rag87_context_binding_drift")
            execution_ordinal = len(observations) + 1
            observation = _run_case(
                case,
                context_items=context_items,
                citation_sources=citation_sources,
                context_hash=context_hash,
                repeat=repeat,
                execution_ordinal=execution_ordinal,
                generator=generator,
            )
            observations.append(observation)
            if progress_callback is not None:
                progress_callback(
                    {
                        "status": "rag87_generation_progress",
                        "repeat": repeat,
                        "completed_generation_count": execution_ordinal,
                        "expected_generation_count": _EXPECTED_GENERATIONS,
                        "pipeline_failure_count": sum(
                            item.pipeline_failure_reason_code is not None for item in observations
                        ),
                    }
                )
    if len(observations) != _EXPECTED_GENERATIONS:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_generation_count_drift")
    try:
        post_inventory = post_lm_inventory_provider()
    except Exception:
        post_inventory = Rag86LMInventorySummary(available=False)
    summary = _summarize_observations(observations)
    pipeline_failure_count = summary.pipeline_failure_count
    exact_target_failures = _exact_target_validity_failures(
        pre_lm_inventory,
        post_inventory,
    )
    validity_failures = (
        *(("rag87_pipeline_failure",) if pipeline_failure_count else ()),
        *exact_target_failures,
    )
    exact_target_stable = not exact_target_failures
    full_inventory_stable = bool(
        pre_lm_inventory.available
        and post_inventory.available
        and pre_lm_inventory.full_inventory_fingerprint == post_inventory.full_inventory_fingerprint
    )
    checks = _sensitivity_checks(summary)
    validity_passed = not validity_failures
    sensitivity_passed = validity_passed and checks.sensitivity_gate_passed
    if not validity_passed:
        conclusion: Conclusion = "inconclusive"
        reason_codes = validity_failures
    elif sensitivity_passed:
        conclusion = "fixture_sensitivity_established"
        reason_codes = ("rag87_fixture_sensitivity_established",)
    else:
        conclusion = "fixture_sensitivity_not_established"
        reason_codes = _failed_sensitivity_reason_codes(checks)
    return Rag87ExperimentResult(
        schema_version="phase3.rag87_fixture_sensitivity_result.v1",
        experiment_manifest_sha256=_manifest_sha256(manifest),
        private_input_sha256=manifest.dataset.private_input_sha256,
        prelive_commit_sha=prelive_commit_sha,
        dataset_name=_DATASET,
        dataset_content_fingerprint=manifest.dataset.dataset_content_fingerprint,
        case_set_fingerprint=manifest.dataset.case_set_fingerprint,
        source_context_fingerprint=manifest.dataset.source_context_fingerprint,
        structural_features_fingerprint=manifest.dataset.structural_features_fingerprint,
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
        non_target_inventory_drift_observed=(exact_target_stable and not full_inventory_stable),
        validity_gate_passed=validity_passed,
        sensitivity_gate_passed=sensitivity_passed,
        conclusion=conclusion,
        reason_codes=reason_codes,
        summary=summary,
        sensitivity_checks=checks,
        evaluator="deterministic_identifier_equivalence_v1",
        baseline_only=True,
        candidate_comparison_performed=False,
        diagnostic_only=True,
        gold_holdout_eligible=False,
        public_accuracy_eligible=False,
        profile_promotion_eligible=False,
        follow_on_two_pass_authorized=sensitivity_passed,
        raw_content_persisted=False,
        observations=tuple(observations),
    )


def _run_case(
    case: EvaluationCaseV2Spec,
    *,
    context_items: tuple[GenerationContextItem, ...],
    citation_sources: tuple[CitationSource, ...],
    context_hash: str,
    repeat: int,
    execution_ordinal: int,
    generator: AnswerGenerator | None,
) -> Rag87CaseObservation:
    started = time.perf_counter()
    answer_text: str | None = None
    answer_outcome: Literal["answered", "abstained"] | None = None
    citation_ids: tuple[int, ...] = ()
    reason_code: str | None = None
    try:
        if generator is None:
            generated = _generate_review_case_with_hard_timeout(
                question=case.question,
                context_items=context_items,
                citation_sources=citation_sources,
                system_instructions=None,
            )
            answer_text = generated.answer_text
            answer_outcome = generated.answer_outcome
            citation_ids = generated.citation_ids
            if generated.reason_code is not None:
                reason_code = f"rag87_{generated.reason_code}"
        else:
            generation = _generate_oracle_answer(
                _review_generation_settings(),
                generator=generator,
                question=case.question,
                context_items=context_items,
                citation_sources=citation_sources,
                system_instructions=None,
            )
            answer_text = generation.answer_text
            answer_outcome = generation.answer_outcome
            citation_ids = tuple(
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
            )
    except AnswerGenerationError as exc:
        reason_code = f"rag87_generation_{exc.error_category or 'failed'}"
    except CitationBuildError as exc:
        reason_code = f"rag87_generation_{exc.detail_code}"
    except Exception:
        reason_code = "rag87_generation_unexpected_error"
    latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    if reason_code is not None or answer_text is None or answer_outcome is None:
        return Rag87CaseObservation(
            case_id=case.case_key,
            repeat=repeat,
            execution_ordinal=execution_ordinal,
            answer_hash=None,
            context_hash=context_hash,
            atomic_fact_matches=(False, False),
            exact_fact_matches=(False, False),
            citation_grounded_fact_matches=(False, False),
            whole_statement_exact_match=False,
            citation_source_coverage=False,
            insufficiency_false_assertion=False,
            unexpected_fact_count=0,
            forbidden_claim_count=0,
            latency_ms=latency_ms,
            pipeline_failure_reason_code=(reason_code or "rag87_generation_worker_failed"),
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
        strong_tokens = _strong_fact_identifier_tokens(
            fact.statement,
            sibling_statements=siblings,
        )
        atomic_match = bool(strong_tokens) and strong_tokens.issubset(answer_tokens)
        exact_match = _whole_statement_exact_match(
            answer=answer_text,
            required_fact=fact.statement,
        )
        atomic_matches.append(atomic_match)
        exact_matches.append(exact_match)
        citation_matches.append(atomic_match and fact_index + 1 in citation_ids)
    context = tuple(item.text for item in context_items)
    allowed_identifiers = _identifier_tokens("\n".join((case.question, *context)))
    unexpected_identifiers = answer_tokens - allowed_identifiers
    normalized_answer = _normalize_identifier_text(answer_text)
    forbidden_count = sum(
        _normalize_identifier_text(claim) in normalized_answer for claim in case.forbidden_claims
    )
    return Rag87CaseObservation(
        case_id=case.case_key,
        repeat=repeat,
        execution_ordinal=execution_ordinal,
        answer_hash=_sha256(answer_text),
        context_hash=context_hash,
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


def _summarize_observations(
    observations: Sequence[Rag87CaseObservation],
) -> Rag87SensitivitySummary:
    if len(observations) != _EXPECTED_GENERATIONS:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_observation_count_drift")
    by_case: dict[str, list[Rag87CaseObservation]] = {}
    for item in observations:
        by_case.setdefault(item.case_id, []).append(item)
    if len(by_case) != _CASE_COUNT or any(len(items) != _REPEATS for items in by_case.values()):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_case_repeat_shape_drift")
    majority_atomic = {
        case_id: tuple(
            sum(item.atomic_fact_matches[index] for item in items) >= _MAJORITY_MINIMUM_REPEATS
            for index in range(_FACTS_PER_CASE)
        )
        for case_id, items in by_case.items()
    }
    majority_grounded = {
        case_id: tuple(
            sum(item.citation_grounded_fact_matches[index] for item in items)
            >= _MAJORITY_MINIMUM_REPEATS
            for index in range(_FACTS_PER_CASE)
        )
        for case_id, items in by_case.items()
    }
    majority_insufficiency = {
        case_id: sum(item.insufficiency_false_assertion for item in items)
        >= _MAJORITY_MINIMUM_REPEATS
        for case_id, items in by_case.items()
    }
    majority_count = sum(sum(values) for values in majority_atomic.values())
    first_count = sum(values[0] for values in majority_atomic.values())
    second_count = sum(values[1] for values in majority_atomic.values())
    complete_count = sum(all(values) for values in majority_atomic.values())
    first_only = {case_id for case_id, values in majority_atomic.items() if values == (True, False)}
    repeat_recalls = tuple(
        round(
            sum(sum(item.atomic_fact_matches) for item in observations if item.repeat == repeat)
            / (_CASE_COUNT * _FACTS_PER_CASE),
            6,
        )
        for repeat in range(1, _REPEATS + 1)
    )
    latencies = sorted(item.latency_ms for item in observations)
    majority_grounded_count = sum(sum(values) for values in majority_grounded.values())
    return Rag87SensitivitySummary(
        repeats=3,
        case_count=12,
        case_observation_count=36,
        required_fact_observation_count=72,
        repeat_atomic_required_fact_recalls=repeat_recalls,
        repeat_atomic_required_fact_recall_range=round(
            max(repeat_recalls) - min(repeat_recalls),
            6,
        ),
        atomic_required_fact_recall=round(
            sum(sum(item.atomic_fact_matches) for item in observations) / 72,
            6,
        ),
        majority_atomic_supported_fact_count=majority_count,
        majority_atomic_required_fact_recall=round(majority_count / 24, 6),
        first_fact_majority_recall=round(first_count / 12, 6),
        second_fact_majority_recall=round(second_count / 12, 6),
        first_minus_second_majority_recall=round((first_count - second_count) / 12, 6),
        majority_complete_case_count=complete_count,
        majority_first_only_case_count=len(first_only),
        first_only_with_majority_insufficiency_case_count=sum(
            majority_insufficiency[case_id] for case_id in first_only
        ),
        majority_citation_grounding_recall=round(majority_grounded_count / 24, 6),
        citation_source_coverage_case_observation_count=sum(
            item.citation_source_coverage for item in observations
        ),
        citation_source_coverage_rate=round(
            sum(item.citation_source_coverage for item in observations) / 36,
            6,
        ),
        insufficiency_false_assertion_case_observation_count=sum(
            item.insufficiency_false_assertion for item in observations
        ),
        unexpected_fact_case_count=sum(
            sum(item.unexpected_fact_count > 0 for item in items) >= _MAJORITY_MINIMUM_REPEATS
            for items in by_case.values()
        ),
        forbidden_claim_case_count=sum(
            sum(item.forbidden_claim_count > 0 for item in items) >= _MAJORITY_MINIMUM_REPEATS
            for items in by_case.values()
        ),
        pipeline_failure_count=sum(
            item.pipeline_failure_reason_code is not None for item in observations
        ),
        p95_latency_ms=latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)],
    )


def _sensitivity_checks(summary: Rag87SensitivitySummary) -> Rag87SensitivityChecks:
    checks = {
        "majority_recall_floor_passed": summary.majority_atomic_supported_fact_count >= 9,
        "majority_recall_ceiling_passed": summary.majority_atomic_supported_fact_count <= 20,
        "majority_complete_case_count_passed": (
            summary.majority_complete_case_count >= _MINIMUM_COMPLETE_CASES
        ),
        "first_only_with_insufficiency_count_passed": (
            summary.first_only_with_majority_insufficiency_case_count
            >= _MINIMUM_FIRST_ONLY_INSUFFICIENCY_CASES
        ),
        "fact_ordinal_recall_gap_passed": (
            summary.first_minus_second_majority_recall >= _MINIMUM_FACT_ORDINAL_RECALL_GAP
        ),
        "repeat_stability_passed": (
            summary.repeat_atomic_required_fact_recall_range <= _MAXIMUM_REPEAT_RECALL_RANGE
        ),
    }
    return Rag87SensitivityChecks(
        **checks,
        sensitivity_gate_passed=all(checks.values()),
    )


def _failed_sensitivity_reason_codes(
    checks: Rag87SensitivityChecks,
) -> tuple[str, ...]:
    mapping = (
        (checks.majority_recall_floor_passed, "rag87_majority_recall_below_floor"),
        (checks.majority_recall_ceiling_passed, "rag87_majority_recall_above_ceiling"),
        (
            checks.majority_complete_case_count_passed,
            "rag87_complete_case_count_below_minimum",
        ),
        (
            checks.first_only_with_insufficiency_count_passed,
            "rag87_first_only_insufficiency_count_below_minimum",
        ),
        (checks.fact_ordinal_recall_gap_passed, "rag87_fact_ordinal_recall_gap_too_small"),
        (checks.repeat_stability_passed, "rag87_repeat_recall_range_too_large"),
    )
    failures = tuple(reason for passed, reason in mapping if not passed)
    return failures or ("rag87_fixture_sensitivity_not_established",)


def _execution_schedule_fingerprint(
    cases: Sequence[EvaluationCaseV2Spec],
) -> str:
    schedule = [
        {
            "execution_ordinal": (repeat - 1) * len(cases) + case_ordinal,
            "repeat": repeat,
            "case_id": case.case_key,
        }
        for repeat in range(1, _REPEATS + 1)
        for case_ordinal, case in enumerate(cases, start=1)
    ]
    if len(schedule) != _EXPECTED_GENERATIONS:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_execution_schedule_cardinality_drift"
        )
    return _sha256_bytes(canonical_json_bytes({"schedule": schedule}))


def _exact_target_validity_failures(
    pre: Rag86LMInventorySummary,
    post: Rag86LMInventorySummary,
) -> tuple[str, ...]:
    if not post.available:
        return ("rag87_post_lm_inventory_unavailable",)
    expected_model_hash = _sha256(_MODEL)
    checks = (
        (
            pre.target_model_id_fingerprint == expected_model_hash
            and post.target_model_id_fingerprint == expected_model_hash,
            "rag87_target_model_id_drift",
        ),
        (
            pre.target_loaded_instance_count == 1 and post.target_loaded_instance_count == 1,
            "rag87_target_instance_count_drift",
        ),
        (
            pre.target_loaded_context_length == _TARGET_LOADED_CONTEXT_LENGTH
            and post.target_loaded_context_length == _TARGET_LOADED_CONTEXT_LENGTH,
            "rag87_target_context_length_drift",
        ),
        (
            pre.target_entry_fingerprint is not None
            and pre.target_entry_fingerprint == post.target_entry_fingerprint,
            "rag87_target_entry_drift",
        ),
    )
    return tuple(reason for passed, reason in checks if not passed)


def _validate_pre_inventory(inventory: Rag86LMInventorySummary) -> None:
    if not inventory.available:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_pre_lm_inventory_unavailable")
    if inventory.target_model_id_fingerprint != _sha256(_MODEL):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_target_model_id_drift")
    if inventory.target_loaded_instance_count != 1:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_target_model_not_exactly_once_loaded"
        )
    if inventory.target_loaded_context_length != _TARGET_LOADED_CONTEXT_LENGTH:
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_target_model_context_length_drift"
        )
    if inventory.target_entry_fingerprint is None:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_target_entry_unavailable")


def _validate_git_sha(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_git_sha_invalid")


def _manifest_sha256(manifest: Rag87ExperimentManifest) -> str:
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
