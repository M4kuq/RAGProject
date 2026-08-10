from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from app.evaluation.generation_prompt_profiles import resolve_generation_prompt_profile
from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.evaluation.qwen_multifact_confirm import build_qwen_multifact_confirm_manifest
from app.rag.citations import CitationBuildError, CitationSource
from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationContextItem,
)
from app.schemas.evaluation_datasets_v2 import (
    EvaluationCaseV2Spec,
    EvaluationDatasetManifestV2,
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
from app.services.rag_service import _is_insufficient_evidence_answer

_BASELINE_PROFILE: Literal["baseline"] = "baseline"
_CANDIDATE_PROFILE: Literal["multi_fact_evidence_ledger_v1"] = (
    "multi_fact_evidence_ledger_v1"
)
_TUNE_DATASET = "local_accuracy_dev_v1"
_CONFIRM_DATASET = "rag84_qwen_multifact_confirm_v1"
_MODEL: Literal["qwen/qwen3.5-9b"] = "qwen/qwen3.5-9b"
_MAX_CONTEXT_CHARS: Literal[6000] = 6000
_MAX_OUTPUT_CHARS: Literal[12000] = 12000
_MAX_OUTPUT_TOKENS: Literal[8192] = 8192
_CASE_TIMEOUT_SECONDS: Literal[180] = 180
_TUNE_REPEATS: Literal[1] = 1
_CONFIRM_REPEATS: Literal[3] = 3
_EXPECTED_TUNE_CASES: Literal[12] = 12
_EXPECTED_CONFIRM_CASES: Literal[14] = 14
_EXPECTED_FACTS_PER_CASE: Literal[2] = 2
_BASELINE_REFERENCE_RECALL = 0.5
_TUNE_MINIMUM_RECALL = 0.75
_TUNE_MINIMUM_DELTA = 0.25
_CONFIRM_MAX_LATENCY_RATIO = 2.0
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
PromptName = Literal["baseline", "multi_fact_evidence_ledger_v1"]
ExperimentPhase = Literal["tune", "confirm"]


class EvaluationQwenMultifactCompletenessError(RuntimeError):
    """Stable fail-closed error for the frozen RAG-84 experiment."""


class Rag84CaseBinding(StrictRawFreeModel):
    case_id: SafeId
    question_hash: Sha256
    required_fact_ids: tuple[SafeId, SafeId]
    normalized_fact_hashes: tuple[Sha256, Sha256]
    logical_document_ids: tuple[SafeId, SafeId]
    source_content_hashes: tuple[Sha256, Sha256]
    source_set_hash: Sha256
    context_hash: Sha256
    citation_ids: tuple[Literal[1], Literal[2]]
    tags: tuple[str, ...]


class Rag84DatasetBinding(StrictRawFreeModel):
    dataset_name: Literal["local_accuracy_dev_v1", "rag84_qwen_multifact_confirm_v1"]
    dataset_content_fingerprint: Sha256
    corpus_fingerprint: Sha256
    selection_rule: Literal["all_answerable_multi_hop_cases"]
    case_count: int = Field(gt=0)
    required_fact_count: int = Field(gt=0)
    source_count: int = Field(gt=0)
    language_ja_count: int = Field(ge=0)
    language_en_count: int = Field(ge=0)
    prompt_injection_count: int = Field(ge=0)
    case_set_fingerprint: Sha256
    question_set_fingerprint: Sha256
    normalized_fact_set_fingerprint: Sha256
    source_content_set_fingerprint: Sha256
    logical_document_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    cases: tuple[Rag84CaseBinding, ...]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.case_count != len(self.cases):
            raise ValueError("rag84_dataset_case_count_drift")
        if self.required_fact_count != sum(len(case.required_fact_ids) for case in self.cases):
            raise ValueError("rag84_dataset_fact_count_drift")
        if self.language_ja_count + self.language_en_count != self.case_count:
            raise ValueError("rag84_dataset_language_count_drift")
        return self


class Rag84IndependenceProof(StrictRawFreeModel):
    tune_confirm_question_hash_overlap_count: Literal[0]
    tune_confirm_fact_id_overlap_count: Literal[0]
    tune_confirm_normalized_fact_hash_overlap_count: Literal[0]
    tune_confirm_source_content_hash_overlap_count: Literal[0]
    tune_confirm_logical_document_id_overlap_count: Literal[0]
    confirm_reserved_namespace: Literal["R84-confirm-20260810"]
    gold_v2_opened_for_design: Literal[False]
    gold_v2_mutated: Literal[False]
    gold_v2_eligible: Literal[False]
    public_accuracy_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]
    disjoint_by_new_reserved_namespace_and_content: Literal[True]


class Rag84GenerationContract(StrictRawFreeModel):
    retrieval_mode: Literal["static_oracle_fixture"]
    source_order_frozen: Literal[True]
    citation_ids_frozen: Literal[True]
    generation_provider: Literal["lmstudio"]
    resolved_generation_model: Literal["qwen/qwen3.5-9b"]
    generation_temperature: float
    reasoning_enabled: Literal[False]
    generation_max_context_chars: Literal[6000]
    generation_max_output_chars: Literal[12000]
    generation_max_output_tokens: Literal[8192]
    generation_case_wall_clock_timeout_seconds: Literal[180]
    retry_policy: Literal["existing_evaluation_generation_retry"]
    baseline_prompt_profile: Literal["baseline"]
    baseline_prompt_fingerprint: Sha256
    candidate_prompt_profile: Literal["multi_fact_evidence_ledger_v1"]
    candidate_prompt_fingerprint: Sha256
    only_experimental_coordinate: Literal["prompt_only_evidence_ledger"]
    required_facts_or_answer_keys_sent_as_prompt_fields: Literal[False]

    @model_validator(mode="after")
    def validate_temperature(self) -> Self:
        if self.generation_temperature != 0.0:
            raise ValueError("rag84_generation_temperature_drift")
        return self


class Rag84DecisionRule(StrictRawFreeModel):
    tune_repeats_per_profile: Literal[1]
    tune_baseline_reference_required_fact_recall: float
    tune_candidate_minimum_required_fact_recall: float
    tune_minimum_absolute_recall_delta: float
    tune_insufficiency_false_assertions_must_decrease: Literal[True]
    tune_unexpected_fact_case_count_maximum: Literal[0]
    tune_forbidden_claim_case_count_maximum: Literal[0]
    tune_citation_and_grounding_non_worse: Literal[True]
    tune_pipeline_failure_count_maximum: Literal[0]
    confirm_repeats_per_profile: Literal[3]
    confirm_majority_required_fact_recall_must_improve: Literal[True]
    confirm_whole_exact_non_worse: Literal[True]
    confirm_citation_grounding_unsupported_fact_non_worse: Literal[True]
    confirm_pipeline_failure_count_maximum: Literal[0]
    confirm_stable_p95_latency_ratio_maximum: float
    optimize_after_confirm: Literal[False]

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        actual = (
            self.tune_baseline_reference_required_fact_recall,
            self.tune_candidate_minimum_required_fact_recall,
            self.tune_minimum_absolute_recall_delta,
            self.confirm_stable_p95_latency_ratio_maximum,
        )
        expected = (
            _BASELINE_REFERENCE_RECALL,
            _TUNE_MINIMUM_RECALL,
            _TUNE_MINIMUM_DELTA,
            _CONFIRM_MAX_LATENCY_RATIO,
        )
        if actual != expected:
            raise ValueError("rag84_decision_threshold_drift")
        return self


class Rag84ExperimentManifest(StrictRawFreeModel):
    schema_version: Literal["phase3.rag84_qwen_multifact_experiment.v1"]
    jira_issue: Literal["RAG-84"]
    stacked_base_commit: GitSha
    experiment_scope: Literal["dev_tune_and_independent_synthetic_confirm"]
    tune: Rag84DatasetBinding
    confirm: Rag84DatasetBinding
    independence: Rag84IndependenceProof
    generation: Rag84GenerationContract
    decision_rule: Rag84DecisionRule
    mixed_calibration_provenance_disclosed: Literal[True]
    independent_human_only_calibration_claimed: Literal[False]
    raw_content_persistence_allowed: Literal[False]
    external_http_allowed: Literal[False]
    database_write_allowed: Literal[False]
    gold_v2_access_allowed: Literal[False]
    merge_or_deploy_allowed: Literal[False]


class Rag84ExperimentLock(StrictRawFreeModel):
    schema_version: Literal["phase3.rag84_qwen_multifact_experiment_lock.v1"]
    stacked_base_commit: GitSha
    experiment_manifest_sha256: Sha256
    tune_binding_sha256: Sha256
    confirm_binding_sha256: Sha256
    independence_proof_sha256: Sha256
    generation_contract_sha256: Sha256
    decision_rule_sha256: Sha256
    tune_dataset_content_fingerprint: Sha256
    tune_case_set_fingerprint: Sha256
    tune_source_context_fingerprint: Sha256
    confirm_dataset_content_fingerprint: Sha256
    confirm_case_set_fingerprint: Sha256
    confirm_source_context_fingerprint: Sha256
    baseline_prompt_fingerprint: Sha256
    candidate_prompt_fingerprint: Sha256
    preconfirm_commit_required: Literal[True]
    optimize_after_confirm: Literal[False]
    gold_v2_access_allowed: Literal[False]
    raw_content_persistence_allowed: Literal[False]


class Rag84CaseObservation(StrictRawFreeModel):
    case_id: SafeId
    profile: PromptName
    repeat: int = Field(gt=0)
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


class Rag84ProfileSummary(StrictRawFreeModel):
    profile: PromptName
    prompt_fingerprint: Sha256
    repeats: int = Field(gt=0)
    case_observation_count: int = Field(gt=0)
    required_fact_observation_count: int = Field(gt=0)
    atomic_supported_fact_count: int = Field(ge=0)
    atomic_required_fact_recall: float = Field(ge=0.0, le=1.0)
    exact_supported_fact_count: int = Field(ge=0)
    exact_required_fact_recall: float = Field(ge=0.0, le=1.0)
    whole_statement_exact_case_count: int = Field(ge=0)
    whole_statement_exact_rate: float = Field(ge=0.0, le=1.0)
    citation_grounded_fact_count: int = Field(ge=0)
    citation_grounding_recall: float = Field(ge=0.0, le=1.0)
    citation_source_coverage_case_count: int = Field(ge=0)
    citation_source_coverage_rate: float = Field(ge=0.0, le=1.0)
    insufficiency_false_assertion_case_count: int = Field(ge=0)
    unexpected_fact_case_count: int = Field(ge=0)
    forbidden_claim_case_count: int = Field(ge=0)
    pipeline_failure_count: int = Field(ge=0)
    p95_latency_ms: int = Field(ge=0)
    repeat_atomic_required_fact_recalls: tuple[float, ...]
    majority_atomic_supported_fact_count: int | None = Field(default=None, ge=0)
    majority_atomic_required_fact_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    majority_whole_statement_exact_case_count: int | None = Field(default=None, ge=0)
    majority_whole_statement_exact_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    majority_citation_grounded_fact_count: int | None = Field(default=None, ge=0)
    majority_citation_grounding_recall: float | None = Field(default=None, ge=0.0, le=1.0)


class Rag84ExperimentResult(StrictRawFreeModel):
    schema_version: Literal["phase3.rag84_qwen_multifact_result.v1"]
    phase: ExperimentPhase
    experiment_manifest_sha256: Sha256
    preconfirm_commit_sha: GitSha
    dataset_name: Literal["local_accuracy_dev_v1", "rag84_qwen_multifact_confirm_v1"]
    dataset_content_fingerprint: Sha256
    case_set_fingerprint: Sha256
    source_context_fingerprint: Sha256
    model: Literal["qwen/qwen3.5-9b"]
    profiles: tuple[Rag84ProfileSummary, Rag84ProfileSummary]
    runtime_stable_for_latency: bool
    p95_latency_ratio: float | None = Field(default=None, ge=0.0)
    gate_passed: bool
    decision: Literal["advance_to_confirm", "adopt_candidate", "retain_baseline"]
    reason_codes: tuple[str, ...]
    evaluator: Literal["deterministic_identifier_equivalence_v1"]
    evaluator_calibration_provenance: Literal[
        "rag83_mixed_user_codex_with_final_human_acceptance"
    ]
    independent_human_only_calibration: Literal[False]
    claim_level_metrics: Literal[True]
    case_level_public_accuracy: Literal[False]
    gold_holdout_eligible: Literal[False]
    profile_promotion_eligible: Literal[False]
    raw_content_persisted: Literal[False]
    observations: tuple[Rag84CaseObservation, ...]


def build_rag84_experiment_manifest(*, stacked_base_commit: str) -> Rag84ExperimentManifest:
    tune = _build_dataset_binding(
        build_local_accuracy_dev_manifest(),
        expected_dataset=_TUNE_DATASET,
        expected_case_count=_EXPECTED_TUNE_CASES,
    )
    confirm = _build_dataset_binding(
        build_qwen_multifact_confirm_manifest(),
        expected_dataset=_CONFIRM_DATASET,
        expected_case_count=_EXPECTED_CONFIRM_CASES,
    )
    independence = _build_independence_proof(tune, confirm)
    baseline = resolve_generation_prompt_profile(_BASELINE_PROFILE)
    candidate = resolve_generation_prompt_profile(_CANDIDATE_PROFILE)
    return Rag84ExperimentManifest(
        schema_version="phase3.rag84_qwen_multifact_experiment.v1",
        jira_issue="RAG-84",
        stacked_base_commit=stacked_base_commit,
        experiment_scope="dev_tune_and_independent_synthetic_confirm",
        tune=tune,
        confirm=confirm,
        independence=independence,
        generation=Rag84GenerationContract(
            retrieval_mode="static_oracle_fixture",
            source_order_frozen=True,
            citation_ids_frozen=True,
            generation_provider="lmstudio",
            resolved_generation_model=_MODEL,
            generation_temperature=0.0,
            reasoning_enabled=False,
            generation_max_context_chars=_MAX_CONTEXT_CHARS,
            generation_max_output_chars=_MAX_OUTPUT_CHARS,
            generation_max_output_tokens=_MAX_OUTPUT_TOKENS,
            generation_case_wall_clock_timeout_seconds=_CASE_TIMEOUT_SECONDS,
            retry_policy="existing_evaluation_generation_retry",
            baseline_prompt_profile=_BASELINE_PROFILE,
            baseline_prompt_fingerprint=baseline.prompt_fingerprint,
            candidate_prompt_profile=_CANDIDATE_PROFILE,
            candidate_prompt_fingerprint=candidate.prompt_fingerprint,
            only_experimental_coordinate="prompt_only_evidence_ledger",
            required_facts_or_answer_keys_sent_as_prompt_fields=False,
        ),
        decision_rule=Rag84DecisionRule(
            tune_repeats_per_profile=_TUNE_REPEATS,
            tune_baseline_reference_required_fact_recall=_BASELINE_REFERENCE_RECALL,
            tune_candidate_minimum_required_fact_recall=_TUNE_MINIMUM_RECALL,
            tune_minimum_absolute_recall_delta=_TUNE_MINIMUM_DELTA,
            tune_insufficiency_false_assertions_must_decrease=True,
            tune_unexpected_fact_case_count_maximum=0,
            tune_forbidden_claim_case_count_maximum=0,
            tune_citation_and_grounding_non_worse=True,
            tune_pipeline_failure_count_maximum=0,
            confirm_repeats_per_profile=_CONFIRM_REPEATS,
            confirm_majority_required_fact_recall_must_improve=True,
            confirm_whole_exact_non_worse=True,
            confirm_citation_grounding_unsupported_fact_non_worse=True,
            confirm_pipeline_failure_count_maximum=0,
            confirm_stable_p95_latency_ratio_maximum=_CONFIRM_MAX_LATENCY_RATIO,
            optimize_after_confirm=False,
        ),
        mixed_calibration_provenance_disclosed=True,
        independent_human_only_calibration_claimed=False,
        raw_content_persistence_allowed=False,
        external_http_allowed=False,
        database_write_allowed=False,
        gold_v2_access_allowed=False,
        merge_or_deploy_allowed=False,
    )


def load_frozen_rag84_experiment_manifest(path: Path) -> Rag84ExperimentManifest:
    payload_bytes, payload = read_json_object(path)
    lock = Rag84ExperimentLock.model_validate(payload)
    if not model_bytes_match(payload_bytes, lock):
        raise EvaluationQwenMultifactCompletenessError("rag84_manifest_bytes_model_mismatch")
    manifest = build_rag84_experiment_manifest(stacked_base_commit=lock.stacked_base_commit)
    if build_rag84_experiment_lock(manifest) != lock:
        raise EvaluationQwenMultifactCompletenessError("rag84_manifest_runtime_drift")
    return manifest


def build_rag84_experiment_lock(
    manifest: Rag84ExperimentManifest,
) -> Rag84ExperimentLock:
    return Rag84ExperimentLock(
        schema_version="phase3.rag84_qwen_multifact_experiment_lock.v1",
        stacked_base_commit=manifest.stacked_base_commit,
        experiment_manifest_sha256=_manifest_sha256(manifest),
        tune_binding_sha256=_sha256_bytes(canonical_json_bytes(manifest.tune)),
        confirm_binding_sha256=_sha256_bytes(canonical_json_bytes(manifest.confirm)),
        independence_proof_sha256=_sha256_bytes(
            canonical_json_bytes(manifest.independence)
        ),
        generation_contract_sha256=_sha256_bytes(
            canonical_json_bytes(manifest.generation)
        ),
        decision_rule_sha256=_sha256_bytes(canonical_json_bytes(manifest.decision_rule)),
        tune_dataset_content_fingerprint=manifest.tune.dataset_content_fingerprint,
        tune_case_set_fingerprint=manifest.tune.case_set_fingerprint,
        tune_source_context_fingerprint=manifest.tune.source_context_fingerprint,
        confirm_dataset_content_fingerprint=manifest.confirm.dataset_content_fingerprint,
        confirm_case_set_fingerprint=manifest.confirm.case_set_fingerprint,
        confirm_source_context_fingerprint=manifest.confirm.source_context_fingerprint,
        baseline_prompt_fingerprint=manifest.generation.baseline_prompt_fingerprint,
        candidate_prompt_fingerprint=manifest.generation.candidate_prompt_fingerprint,
        preconfirm_commit_required=True,
        optimize_after_confirm=False,
        gold_v2_access_allowed=False,
        raw_content_persistence_allowed=False,
    )


def run_rag84_tune(
    manifest: Rag84ExperimentManifest,
    *,
    preconfirm_commit_sha: str,
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag84ExperimentResult:
    return _run_rag84_experiment(
        manifest,
        phase="tune",
        preconfirm_commit_sha=preconfirm_commit_sha,
        generator=generator,
        runtime_stable_for_latency=False,
        progress_callback=progress_callback,
    )


def run_rag84_confirm(
    manifest: Rag84ExperimentManifest,
    *,
    preconfirm_commit_sha: str,
    tune_result: Rag84ExperimentResult,
    runtime_stable_for_latency: bool,
    generator: AnswerGenerator | None = None,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> Rag84ExperimentResult:
    if (
        tune_result.phase != "tune"
        or not tune_result.gate_passed
        or tune_result.decision != "advance_to_confirm"
        or tune_result.preconfirm_commit_sha != preconfirm_commit_sha
        or tune_result.experiment_manifest_sha256 != _manifest_sha256(manifest)
    ):
        raise EvaluationQwenMultifactCompletenessError("rag84_confirm_tune_gate_missing")
    if not runtime_stable_for_latency:
        raise EvaluationQwenMultifactCompletenessError("rag84_confirm_runtime_not_stable")
    return _run_rag84_experiment(
        manifest,
        phase="confirm",
        preconfirm_commit_sha=preconfirm_commit_sha,
        generator=generator,
        runtime_stable_for_latency=True,
        progress_callback=progress_callback,
    )


def _run_rag84_experiment(
    manifest: Rag84ExperimentManifest,
    *,
    phase: ExperimentPhase,
    preconfirm_commit_sha: str,
    generator: AnswerGenerator | None,
    runtime_stable_for_latency: bool,
    progress_callback: Callable[[dict[str, object]], None] | None,
) -> Rag84ExperimentResult:
    if not re.fullmatch(r"[0-9a-f]{40}", preconfirm_commit_sha):
        raise EvaluationQwenMultifactCompletenessError("rag84_preconfirm_commit_invalid")
    dataset = (
        build_local_accuracy_dev_manifest()
        if phase == "tune"
        else build_qwen_multifact_confirm_manifest()
    )
    expected_binding = manifest.tune if phase == "tune" else manifest.confirm
    runtime_binding = _build_dataset_binding(
        dataset,
        expected_dataset=expected_binding.dataset_name,
        expected_case_count=expected_binding.case_count,
    )
    if runtime_binding != expected_binding:
        raise EvaluationQwenMultifactCompletenessError("rag84_dataset_binding_drift")
    repeats = _TUNE_REPEATS if phase == "tune" else _CONFIRM_REPEATS
    selected_cases = _selected_cases(dataset)
    documents = {document.source_key: document for document in dataset.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(dataset.corpus_documents, key=lambda item: item.source_key),
            start=1,
        )
    }
    observations: list[Rag84CaseObservation] = []
    total_steps = repeats * len(selected_cases) * 2
    completed_steps = 0
    for profile_name in (_BASELINE_PROFILE, _CANDIDATE_PROFILE):
        profile = resolve_generation_prompt_profile(profile_name)
        for repeat in range(1, repeats + 1):
            for case in selected_cases:
                material = _prepare_fixture_oracle_material(case, documents, document_ids)
                observation = _run_case(
                    case,
                    material=material,
                    profile=profile_name,
                    system_instructions=profile.system_instructions,
                    repeat=repeat,
                    generator=generator,
                )
                observations.append(observation)
                completed_steps += 1
                if progress_callback is not None:
                    progress_callback(
                        {
                            "status": "rag84_generation_progress",
                            "phase": phase,
                            "profile": profile_name,
                            "repeat": repeat,
                            "completed_step_count": completed_steps,
                            "total_step_count": total_steps,
                            "pipeline_failure_count": sum(
                                item.pipeline_failure_reason_code is not None
                                for item in observations
                            ),
                        }
                    )
    baseline = _summarize_profile(
        profile=_BASELINE_PROFILE,
        repeats=repeats,
        observations=observations,
        case_count=len(selected_cases),
    )
    candidate = _summarize_profile(
        profile=_CANDIDATE_PROFILE,
        repeats=repeats,
        observations=observations,
        case_count=len(selected_cases),
    )
    if phase == "tune":
        gate_passed, reason_codes = _tune_decision(baseline, candidate)
        decision: Literal["advance_to_confirm", "adopt_candidate", "retain_baseline"] = (
            "advance_to_confirm" if gate_passed else "retain_baseline"
        )
        latency_ratio = None
    else:
        latency_ratio = round(candidate.p95_latency_ms / max(1, baseline.p95_latency_ms), 6)
        gate_passed, reason_codes = _confirm_decision(
            baseline,
            candidate,
            runtime_stable_for_latency=runtime_stable_for_latency,
            latency_ratio=latency_ratio,
        )
        decision = "adopt_candidate" if gate_passed else "retain_baseline"
    return Rag84ExperimentResult(
        schema_version="phase3.rag84_qwen_multifact_result.v1",
        phase=phase,
        experiment_manifest_sha256=_manifest_sha256(manifest),
        preconfirm_commit_sha=preconfirm_commit_sha,
        dataset_name=expected_binding.dataset_name,
        dataset_content_fingerprint=expected_binding.dataset_content_fingerprint,
        case_set_fingerprint=expected_binding.case_set_fingerprint,
        source_context_fingerprint=expected_binding.source_context_fingerprint,
        model=_MODEL,
        profiles=(baseline, candidate),
        runtime_stable_for_latency=runtime_stable_for_latency,
        p95_latency_ratio=latency_ratio,
        gate_passed=gate_passed,
        decision=decision,
        reason_codes=reason_codes,
        evaluator="deterministic_identifier_equivalence_v1",
        evaluator_calibration_provenance=(
            "rag83_mixed_user_codex_with_final_human_acceptance"
        ),
        independent_human_only_calibration=False,
        claim_level_metrics=True,
        case_level_public_accuracy=False,
        gold_holdout_eligible=False,
        profile_promotion_eligible=False,
        raw_content_persisted=False,
        observations=tuple(observations),
    )


def _run_case(
    case: EvaluationCaseV2Spec,
    *,
    material: tuple[
        tuple[GenerationContextItem, ...],
        tuple[CitationSource, ...],
        tuple[str, ...],
        tuple[str, ...],
    ],
    profile: PromptName,
    system_instructions: str | None,
    repeat: int,
    generator: AnswerGenerator | None,
) -> Rag84CaseObservation:
    context_items, citation_sources, context, _source_keys = material
    context_hash = _sha256("\x00".join(context))
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
                system_instructions=system_instructions,
            )
            answer_text = generated.answer_text
            answer_outcome = generated.answer_outcome
            citation_ids = generated.citation_ids
            reason_code = generated.reason_code
        else:
            generation = _generate_oracle_answer(
                _review_generation_settings(),
                generator=generator,
                question=case.question,
                context_items=context_items,
                citation_sources=citation_sources,
                system_instructions=system_instructions,
            )
            answer_text = generation.answer_text
            answer_outcome = generation.answer_outcome
            citation_ids = tuple(
                sorted(
                    {
                        citation_id
                        for citation in generation.citations
                        if isinstance(
                            (citation_id := citation.get("local_citation_id")), int
                        )
                    }
                )
            )
    except AnswerGenerationError as exc:
        reason_code = f"rag84_generation_{exc.error_category or 'failed'}"
    except CitationBuildError as exc:
        reason_code = f"rag84_generation_{exc.detail_code}"
    except Exception:
        reason_code = "rag84_generation_unexpected_error"
    latency_ms = max(0, int(round((time.perf_counter() - started) * 1000)))
    if reason_code is not None or answer_text is None or answer_outcome is None:
        return Rag84CaseObservation(
            case_id=case.case_key,
            profile=profile,
            repeat=repeat,
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
            pipeline_failure_reason_code=reason_code or "rag84_generation_worker_failed",
        )
    atomic_matches: list[bool] = []
    exact_matches: list[bool] = []
    citation_matches: list[bool] = []
    for fact_index, fact in enumerate(case.required_facts):
        sibling_statements = tuple(
            sibling.statement
            for sibling_index, sibling in enumerate(case.required_facts)
            if sibling_index != fact_index
        )
        strong_tokens = _strong_fact_identifier_tokens(
            fact.statement,
            sibling_statements=sibling_statements,
        )
        atomic_match = bool(strong_tokens) and strong_tokens.issubset(
            _identifier_tokens(answer_text)
        )
        exact_match = _whole_statement_exact_match(
            answer=answer_text,
            required_fact=fact.statement,
        )
        atomic_matches.append(atomic_match)
        exact_matches.append(exact_match)
        citation_matches.append(atomic_match and fact_index + 1 in citation_ids)
    allowed_identifiers = _identifier_tokens("\n".join((case.question, *context)))
    unexpected_identifiers = _identifier_tokens(answer_text) - allowed_identifiers
    normalized_answer = _normalize_identifier_text(answer_text)
    forbidden_count = sum(
        _normalize_identifier_text(claim) in normalized_answer for claim in case.forbidden_claims
    )
    return Rag84CaseObservation(
        case_id=case.case_key,
        profile=profile,
        repeat=repeat,
        answer_hash=_sha256(answer_text),
        context_hash=context_hash,
        atomic_fact_matches=(atomic_matches[0], atomic_matches[1]),
        exact_fact_matches=(exact_matches[0], exact_matches[1]),
        citation_grounded_fact_matches=(citation_matches[0], citation_matches[1]),
        whole_statement_exact_match=all(exact_matches),
        citation_source_coverage=set(citation_ids) == {1, 2},
        insufficiency_false_assertion=(
            answer_outcome == "abstained" or _contains_insufficiency_assertion(answer_text)
        ),
        unexpected_fact_count=len(unexpected_identifiers),
        forbidden_claim_count=forbidden_count,
        latency_ms=latency_ms,
        pipeline_failure_reason_code=None,
    )


def _summarize_profile(
    *,
    profile: PromptName,
    repeats: int,
    observations: Sequence[Rag84CaseObservation],
    case_count: int,
) -> Rag84ProfileSummary:
    selected = tuple(item for item in observations if item.profile == profile)
    expected_observations = repeats * case_count
    if len(selected) != expected_observations:
        raise EvaluationQwenMultifactCompletenessError("rag84_profile_observation_count_drift")
    fact_observations = expected_observations * _EXPECTED_FACTS_PER_CASE
    atomic_count = sum(sum(item.atomic_fact_matches) for item in selected)
    exact_count = sum(sum(item.exact_fact_matches) for item in selected)
    grounded_count = sum(sum(item.citation_grounded_fact_matches) for item in selected)
    repeat_recalls = tuple(
        round(
            sum(
                sum(item.atomic_fact_matches)
                for item in selected
                if item.repeat == repeat
            )
            / (case_count * _EXPECTED_FACTS_PER_CASE),
            6,
        )
        for repeat in range(1, repeats + 1)
    )
    majority_atomic_count: int | None = None
    majority_whole_count: int | None = None
    majority_grounded_count: int | None = None
    majority_atomic_recall: float | None = None
    majority_whole_rate: float | None = None
    majority_grounding_recall: float | None = None
    if repeats == _CONFIRM_REPEATS:
        by_case = {
            case_id: tuple(item for item in selected if item.case_id == case_id)
            for case_id in {item.case_id for item in selected}
        }
        majority_fact_matches = {
            case_id: tuple(
                sum(item.atomic_fact_matches[index] for item in case_observations) >= 2
                for index in range(_EXPECTED_FACTS_PER_CASE)
            )
            for case_id, case_observations in by_case.items()
        }
        majority_grounded_matches = {
            case_id: tuple(
                sum(item.citation_grounded_fact_matches[index] for item in case_observations) >= 2
                for index in range(_EXPECTED_FACTS_PER_CASE)
            )
            for case_id, case_observations in by_case.items()
        }
        majority_atomic_count = sum(sum(values) for values in majority_fact_matches.values())
        majority_whole_count = sum(all(values) for values in majority_fact_matches.values())
        majority_grounded_count = sum(
            sum(values) for values in majority_grounded_matches.values()
        )
        majority_atomic_recall = round(
            majority_atomic_count / (case_count * _EXPECTED_FACTS_PER_CASE), 6
        )
        majority_whole_rate = round(majority_whole_count / case_count, 6)
        majority_grounding_recall = round(
            majority_grounded_count / (case_count * _EXPECTED_FACTS_PER_CASE), 6
        )
    profile_spec = resolve_generation_prompt_profile(profile)
    latencies = sorted(item.latency_ms for item in selected)
    return Rag84ProfileSummary(
        profile=profile,
        prompt_fingerprint=profile_spec.prompt_fingerprint,
        repeats=repeats,
        case_observation_count=expected_observations,
        required_fact_observation_count=fact_observations,
        atomic_supported_fact_count=atomic_count,
        atomic_required_fact_recall=round(atomic_count / fact_observations, 6),
        exact_supported_fact_count=exact_count,
        exact_required_fact_recall=round(exact_count / fact_observations, 6),
        whole_statement_exact_case_count=sum(item.whole_statement_exact_match for item in selected),
        whole_statement_exact_rate=round(
            sum(item.whole_statement_exact_match for item in selected) / expected_observations,
            6,
        ),
        citation_grounded_fact_count=grounded_count,
        citation_grounding_recall=round(grounded_count / fact_observations, 6),
        citation_source_coverage_case_count=sum(item.citation_source_coverage for item in selected),
        citation_source_coverage_rate=round(
            sum(item.citation_source_coverage for item in selected) / expected_observations,
            6,
        ),
        insufficiency_false_assertion_case_count=sum(
            item.insufficiency_false_assertion for item in selected
        ),
        unexpected_fact_case_count=sum(item.unexpected_fact_count > 0 for item in selected),
        forbidden_claim_case_count=sum(item.forbidden_claim_count > 0 for item in selected),
        pipeline_failure_count=sum(
            item.pipeline_failure_reason_code is not None for item in selected
        ),
        p95_latency_ms=latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)],
        repeat_atomic_required_fact_recalls=repeat_recalls,
        majority_atomic_supported_fact_count=majority_atomic_count,
        majority_atomic_required_fact_recall=majority_atomic_recall,
        majority_whole_statement_exact_case_count=majority_whole_count,
        majority_whole_statement_exact_rate=majority_whole_rate,
        majority_citation_grounded_fact_count=majority_grounded_count,
        majority_citation_grounding_recall=majority_grounding_recall,
    )


def _tune_decision(
    baseline: Rag84ProfileSummary,
    candidate: Rag84ProfileSummary,
) -> tuple[bool, tuple[str, ...]]:
    checks = (
        (
            baseline.atomic_required_fact_recall == _BASELINE_REFERENCE_RECALL,
            "tune_baseline_reference_recall_drift",
        ),
        (
            candidate.atomic_required_fact_recall >= _TUNE_MINIMUM_RECALL,
            "tune_candidate_recall_below_minimum",
        ),
        (
            candidate.atomic_required_fact_recall - baseline.atomic_required_fact_recall
            >= _TUNE_MINIMUM_DELTA,
            "tune_recall_delta_below_minimum",
        ),
        (
            candidate.insufficiency_false_assertion_case_count
            < baseline.insufficiency_false_assertion_case_count,
            "tune_insufficiency_false_assertions_not_reduced",
        ),
        (candidate.unexpected_fact_case_count == 0, "tune_unexpected_fact_detected"),
        (candidate.forbidden_claim_case_count == 0, "tune_forbidden_claim_detected"),
        (
            candidate.citation_grounding_recall >= baseline.citation_grounding_recall,
            "tune_citation_grounding_regressed",
        ),
        (
            candidate.citation_source_coverage_rate >= baseline.citation_source_coverage_rate,
            "tune_citation_source_coverage_regressed",
        ),
        (
            baseline.pipeline_failure_count == 0 and candidate.pipeline_failure_count == 0,
            "tune_pipeline_failure",
        ),
    )
    failures = tuple(reason for passed, reason in checks if not passed)
    return not failures, failures or ("tune_gate_passed",)


def _confirm_decision(
    baseline: Rag84ProfileSummary,
    candidate: Rag84ProfileSummary,
    *,
    runtime_stable_for_latency: bool,
    latency_ratio: float,
) -> tuple[bool, tuple[str, ...]]:
    if (
        baseline.majority_atomic_required_fact_recall is None
        or candidate.majority_atomic_required_fact_recall is None
        or baseline.majority_whole_statement_exact_rate is None
        or candidate.majority_whole_statement_exact_rate is None
        or baseline.majority_citation_grounding_recall is None
        or candidate.majority_citation_grounding_recall is None
    ):
        raise EvaluationQwenMultifactCompletenessError("rag84_confirm_majority_missing")
    checks = (
        (
            candidate.majority_atomic_required_fact_recall
            > baseline.majority_atomic_required_fact_recall,
            "confirm_majority_recall_not_improved",
        ),
        (
            candidate.majority_whole_statement_exact_rate
            >= baseline.majority_whole_statement_exact_rate,
            "confirm_whole_exact_regressed",
        ),
        (
            candidate.majority_citation_grounding_recall
            >= baseline.majority_citation_grounding_recall,
            "confirm_citation_grounding_regressed",
        ),
        (
            candidate.citation_source_coverage_rate >= baseline.citation_source_coverage_rate,
            "confirm_citation_source_coverage_regressed",
        ),
        (
            candidate.unexpected_fact_case_count <= baseline.unexpected_fact_case_count,
            "confirm_unexpected_fact_regressed",
        ),
        (
            candidate.forbidden_claim_case_count <= baseline.forbidden_claim_case_count,
            "confirm_forbidden_claim_regressed",
        ),
        (
            baseline.pipeline_failure_count == 0 and candidate.pipeline_failure_count == 0,
            "confirm_pipeline_failure",
        ),
        (runtime_stable_for_latency, "confirm_runtime_not_stable"),
        (latency_ratio <= _CONFIRM_MAX_LATENCY_RATIO, "confirm_p95_latency_regressed"),
    )
    failures = tuple(reason for passed, reason in checks if not passed)
    return not failures, failures or ("confirm_gate_passed",)


def _build_dataset_binding(
    manifest: EvaluationDatasetManifestV2,
    *,
    expected_dataset: str,
    expected_case_count: int,
) -> Rag84DatasetBinding:
    if manifest.dataset.dataset_name != expected_dataset:
        raise EvaluationQwenMultifactCompletenessError("rag84_dataset_name_drift")
    cases = _selected_cases(manifest)
    if len(cases) != expected_case_count:
        raise EvaluationQwenMultifactCompletenessError("rag84_dataset_case_count_drift")
    documents = {document.source_key: document for document in manifest.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(manifest.corpus_documents, key=lambda item: item.source_key),
            start=1,
        )
    }
    bindings: list[Rag84CaseBinding] = []
    for case in cases:
        if len(case.required_facts) != _EXPECTED_FACTS_PER_CASE:
            raise EvaluationQwenMultifactCompletenessError("rag84_case_fact_count_drift")
        _context_items, _citation_sources, context, source_keys = (
            _prepare_fixture_oracle_material(case, documents, document_ids)
        )
        if len(source_keys) != _EXPECTED_FACTS_PER_CASE:
            raise EvaluationQwenMultifactCompletenessError("rag84_case_source_count_drift")
        source_content_hashes = tuple(_sha256(documents[key].body) for key in source_keys)
        if len(source_content_hashes) != 2:
            raise EvaluationQwenMultifactCompletenessError("rag84_source_hash_count_drift")
        fact_hashes = tuple(
            _sha256(_normalize_identifier_text(fact.statement)) for fact in case.required_facts
        )
        if len(fact_hashes) != 2:
            raise EvaluationQwenMultifactCompletenessError("rag84_fact_hash_count_drift")
        source_set_hash = _sha256_bytes(
            canonical_json_bytes(
                {
                    "logical_document_ids": list(source_keys),
                    "source_content_hashes": list(source_content_hashes),
                    "citation_ids": [1, 2],
                }
            )
        )
        bindings.append(
            Rag84CaseBinding(
                case_id=case.case_key,
                question_hash=_sha256(case.question),
                required_fact_ids=(case.required_facts[0].fact_id, case.required_facts[1].fact_id),
                normalized_fact_hashes=(fact_hashes[0], fact_hashes[1]),
                logical_document_ids=(source_keys[0], source_keys[1]),
                source_content_hashes=(source_content_hashes[0], source_content_hashes[1]),
                source_set_hash=source_set_hash,
                context_hash=_sha256("\x00".join(context)),
                citation_ids=(1, 2),
                tags=tuple(sorted(case.tags)),
            )
        )
    ordered = tuple(sorted(bindings, key=lambda item: item.case_id))
    question_hashes = tuple(item.question_hash for item in ordered)
    fact_hashes = tuple(value for item in ordered for value in item.normalized_fact_hashes)
    content_hashes = tuple(value for item in ordered for value in item.source_content_hashes)
    logical_ids = tuple(value for item in ordered for value in item.logical_document_ids)
    return Rag84DatasetBinding(
        dataset_name=expected_dataset,
        dataset_content_fingerprint=manifest.content_fingerprint(),
        corpus_fingerprint=manifest.corpus_fingerprint(),
        selection_rule="all_answerable_multi_hop_cases",
        case_count=len(ordered),
        required_fact_count=len(fact_hashes),
        source_count=len(set(logical_ids)),
        language_ja_count=sum("language:ja" in item.tags for item in ordered),
        language_en_count=sum("language:en" in item.tags for item in ordered),
        prompt_injection_count=sum("prompt_injection" in item.tags for item in ordered),
        case_set_fingerprint=_sha256_bytes(
            canonical_json_bytes({"cases": [item.model_dump(mode="json") for item in ordered]})
        ),
        question_set_fingerprint=_fingerprint_set(question_hashes),
        normalized_fact_set_fingerprint=_fingerprint_set(fact_hashes),
        source_content_set_fingerprint=_fingerprint_set(content_hashes),
        logical_document_set_fingerprint=_fingerprint_set(logical_ids),
        source_context_fingerprint=_sha256_bytes(
            canonical_json_bytes(
                {
                    "cases": [
                        {
                            "case_id": item.case_id,
                            "source_set_hash": item.source_set_hash,
                            "context_hash": item.context_hash,
                        }
                        for item in ordered
                    ]
                }
            )
        ),
        cases=ordered,
    )


def _build_independence_proof(
    tune: Rag84DatasetBinding,
    confirm: Rag84DatasetBinding,
) -> Rag84IndependenceProof:
    tune_questions = {item.question_hash for item in tune.cases}
    confirm_questions = {item.question_hash for item in confirm.cases}
    tune_fact_ids = {value for item in tune.cases for value in item.required_fact_ids}
    confirm_fact_ids = {value for item in confirm.cases for value in item.required_fact_ids}
    tune_fact_hashes = {value for item in tune.cases for value in item.normalized_fact_hashes}
    confirm_fact_hashes = {value for item in confirm.cases for value in item.normalized_fact_hashes}
    tune_source_hashes = {value for item in tune.cases for value in item.source_content_hashes}
    confirm_source_hashes = {
        value for item in confirm.cases for value in item.source_content_hashes
    }
    tune_document_ids = {value for item in tune.cases for value in item.logical_document_ids}
    confirm_document_ids = {value for item in confirm.cases for value in item.logical_document_ids}
    overlap_counts = (
        len(tune_questions & confirm_questions),
        len(tune_fact_ids & confirm_fact_ids),
        len(tune_fact_hashes & confirm_fact_hashes),
        len(tune_source_hashes & confirm_source_hashes),
        len(tune_document_ids & confirm_document_ids),
    )
    if any(overlap_counts):
        raise EvaluationQwenMultifactCompletenessError("rag84_confirm_independence_failed")
    return Rag84IndependenceProof(
        tune_confirm_question_hash_overlap_count=0,
        tune_confirm_fact_id_overlap_count=0,
        tune_confirm_normalized_fact_hash_overlap_count=0,
        tune_confirm_source_content_hash_overlap_count=0,
        tune_confirm_logical_document_id_overlap_count=0,
        confirm_reserved_namespace="R84-confirm-20260810",
        gold_v2_opened_for_design=False,
        gold_v2_mutated=False,
        gold_v2_eligible=False,
        public_accuracy_eligible=False,
        profile_promotion_eligible=False,
        disjoint_by_new_reserved_namespace_and_content=True,
    )


def _selected_cases(manifest: EvaluationDatasetManifestV2) -> tuple[EvaluationCaseV2Spec, ...]:
    return tuple(
        sorted(
            (
                case
                for case in manifest.cases
                if case.answerable and "multi_hop" in case.tags
            ),
            key=lambda item: item.case_key,
        )
    )


def _manifest_sha256(manifest: Rag84ExperimentManifest) -> str:
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
