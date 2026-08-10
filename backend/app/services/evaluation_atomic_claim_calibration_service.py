from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, Self, cast

from pydantic import Field, model_validator

from app.services.evaluation_atomic_claim_contracts import (
    AtomicClaimLegacyHashBinding,
    AtomicClaimLegacyNotApplicableBinding,
    AtomicClaimSourceContract,
    SafeId,
    Sha256,
    StrictRawFreeModel,
)

_ALLOWED_DATASET = "local_accuracy_dev_v1"
_EXPECTED_MODEL = "qwen/qwen3.5-9b"
_LEGACY_REVIEW_SCHEMA = "phase3.oracle_codex_assisted_review.v1"

_StrictModel = StrictRawFreeModel


class AtomicClaimReferenceDecision(AtomicClaimLegacyHashBinding):
    reference_supported: bool


class AtomicClaimReviewManifest(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_review.v1"]
    source: AtomicClaimSourceContract
    reviewer_provenance: SafeId
    reviewer_version: SafeId
    review_status: Literal["requires_human_signoff", "human_signed_off"]
    requires_human_signoff: bool
    raw_content_persisted: Literal[False] = False
    decisions: tuple[AtomicClaimReferenceDecision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision_uniqueness(self) -> Self:
        identities = [decision.identity for decision in self.decisions]
        if len(identities) != len(set(identities)):
            raise ValueError("atomic_claim_reference_identity_duplicate")
        hash_bound = [decision.hash_bound_identity for decision in self.decisions]
        if len(hash_bound) != len(set(hash_bound)):
            raise ValueError("atomic_claim_reference_hash_binding_duplicate")
        if self.review_status == "requires_human_signoff" and not self.requires_human_signoff:
            raise ValueError("atomic_claim_human_signoff_state_invalid")
        if self.review_status == "human_signed_off" and self.requires_human_signoff:
            raise ValueError("atomic_claim_human_signoff_state_invalid")
        return self


class AtomicClaimCandidateObservation(AtomicClaimLegacyHashBinding):
    segmentation_status: Literal["presegmented_reference_claim"]
    whole_statement_exact_match: bool
    atomic_equivalence_match: bool


class AtomicClaimNotApplicableObservation(AtomicClaimLegacyNotApplicableBinding):
    reason: Literal["unanswerable", "abstention"]


class AtomicClaimCandidateManifest(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_candidate.v1"]
    source: AtomicClaimSourceContract
    candidate_id: SafeId
    candidate_version: SafeId
    candidate_dimension: Literal["semantic_equivalence_only"]
    raw_content_persisted: Literal[False] = False
    pipeline_failure_count: int = Field(ge=0)
    observations: tuple[AtomicClaimCandidateObservation, ...] = ()
    not_applicable_observations: tuple[AtomicClaimNotApplicableObservation, ...] = ()

    @model_validator(mode="after")
    def validate_observation_uniqueness_and_applicability(self) -> Self:
        identities = [observation.identity for observation in self.observations]
        if len(identities) != len(set(identities)):
            raise ValueError("atomic_claim_candidate_identity_duplicate")
        hash_bound = [observation.hash_bound_identity for observation in self.observations]
        if len(hash_bound) != len(set(hash_bound)):
            raise ValueError("atomic_claim_candidate_hash_binding_duplicate")
        not_applicable = [
            observation.observation_identity for observation in self.not_applicable_observations
        ]
        if len(not_applicable) != len(set(not_applicable)):
            raise ValueError("atomic_claim_not_applicable_identity_duplicate")
        applicable_cases = {
            (observation.case_id, observation.answer_hash, observation.context_hash)
            for observation in self.observations
        }
        if applicable_cases.intersection(not_applicable):
            raise ValueError("atomic_claim_not_applicable_overlap")
        return self


class AtomicClaimCalibrationSummary(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_calibration.v1"]
    source_evaluation_run_id: int
    dataset_name: str
    candidate_id: str
    candidate_manifest_sha256: Sha256 | None
    reference_manifest_sha256: Sha256 | None
    reference_schema_version: str | None
    reference_provenance: str | None
    requires_human_signoff: bool
    calibration_status: Literal[
        "insufficient_hash_bound_claim_labels",
        "partial_hash_bound_claim_labels",
        "complete_hash_bound_claim_labels",
    ]
    calibration_coverage: float
    reference_claim_count: int
    hash_matched_claim_count: int
    not_applicable_observation_count: int
    unanswerable_or_abstention_misapplication_count: int
    whole_statement_exact_false_negative_count: int | None
    whole_statement_exact_false_positive_count: int | None
    atomic_equivalence_false_negative_count: int | None
    atomic_equivalence_false_positive_count: int | None
    false_negative_reduction_count: int | None
    false_positive_delta_count: int | None
    pipeline_failure_count: int
    candidate_selected: bool
    decision: Literal[
        "not_calibrated", "screening_candidate_rejected", "screening_candidate_selected"
    ]
    screening_only: Literal[True]
    primary_metric_status: Literal["calibrated_grounded_answer_pass_rate_unchanged"]
    segmentation_status: Literal["presegmented_reference_claim"]
    reason_codes: tuple[str, ...]

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], self.model_dump(mode="json"))


def inspect_reference_payload(
    payload: Mapping[str, object],
) -> tuple[AtomicClaimReviewManifest | None, str | None, str | None, bool]:
    """Parse only the new strict schema; classify the legacy aggregate without expanding it."""

    schema_version = payload.get("schema_version")
    if schema_version == "phase3.oracle_atomic_claim_review.v1":
        manifest = AtomicClaimReviewManifest.model_validate(payload)
        return (
            manifest,
            manifest.schema_version,
            manifest.reviewer_provenance,
            manifest.requires_human_signoff,
        )
    if schema_version == _LEGACY_REVIEW_SCHEMA:
        provenance = payload.get("reviewer_type")
        review_status = payload.get("review_status")
        safe_provenance = (
            provenance
            if isinstance(provenance, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", provenance)
            else None
        )
        requires_signoff = review_status != "human_signed_off"
        return None, _LEGACY_REVIEW_SCHEMA, safe_provenance, requires_signoff
    raise EvaluationAtomicClaimCalibrationError("atomic_claim_reference_schema_unsupported")


def evaluate_atomic_claim_candidate(
    candidate: AtomicClaimCandidateManifest,
    *,
    reference: AtomicClaimReviewManifest | None,
    candidate_manifest_sha256: Sha256 | None = None,
    reference_manifest_sha256: Sha256 | None = None,
    reference_schema_version: str | None = None,
    reference_provenance: str | None = None,
    requires_human_signoff: bool = True,
) -> AtomicClaimCalibrationSummary:
    if candidate.source.dataset_name != _ALLOWED_DATASET:
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_dataset_not_allowed")
    if candidate.source.resolved_generation_model != _EXPECTED_MODEL:
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_model_mismatch")
    if reference is None:
        reason_codes = ["insufficient_hash_bound_claim_labels"]
        if reference_schema_version == _LEGACY_REVIEW_SCHEMA:
            reason_codes.append("legacy_observation_aggregate_not_per_claim")
        if candidate.pipeline_failure_count:
            reason_codes.append("pipeline_failure_observed")
        reason_codes.extend(
            (
                "unanswerable_abstention_not_applicable",
                "whole_statement_exact_primary_unchanged",
                "requires_human_signoff",
            )
        )
        return AtomicClaimCalibrationSummary(
            schema_version="phase3.oracle_atomic_claim_calibration.v1",
            source_evaluation_run_id=candidate.source.source_evaluation_run_id,
            dataset_name=candidate.source.dataset_name,
            candidate_id=candidate.candidate_id,
            candidate_manifest_sha256=candidate_manifest_sha256,
            reference_manifest_sha256=reference_manifest_sha256,
            reference_schema_version=reference_schema_version,
            reference_provenance=reference_provenance,
            requires_human_signoff=requires_human_signoff,
            calibration_status="insufficient_hash_bound_claim_labels",
            calibration_coverage=0.0,
            reference_claim_count=0,
            hash_matched_claim_count=0,
            not_applicable_observation_count=len(candidate.not_applicable_observations),
            unanswerable_or_abstention_misapplication_count=0,
            whole_statement_exact_false_negative_count=None,
            whole_statement_exact_false_positive_count=None,
            atomic_equivalence_false_negative_count=None,
            atomic_equivalence_false_positive_count=None,
            false_negative_reduction_count=None,
            false_positive_delta_count=None,
            pipeline_failure_count=candidate.pipeline_failure_count,
            candidate_selected=False,
            decision="not_calibrated",
            screening_only=True,
            primary_metric_status="calibrated_grounded_answer_pass_rate_unchanged",
            segmentation_status="presegmented_reference_claim",
            reason_codes=tuple(reason_codes),
        )

    if candidate.source != reference.source:
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_source_fingerprint_drift")

    reference_by_identity = {decision.identity: decision for decision in reference.decisions}
    candidate_by_identity = {
        observation.identity: observation for observation in candidate.observations
    }
    unknown_identities = set(candidate_by_identity).difference(reference_by_identity)
    if unknown_identities:
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_candidate_identity_unbound")

    for identity in set(candidate_by_identity).intersection(reference_by_identity):
        decision = reference_by_identity[identity]
        observation = candidate_by_identity[identity]
        if (
            observation.answer_hash != decision.answer_hash
            or observation.context_hash != decision.context_hash
        ):
            raise EvaluationAtomicClaimCalibrationError("atomic_claim_answer_context_hash_drift")

    matched_identities = tuple(
        identity for identity in reference_by_identity if identity in candidate_by_identity
    )
    matched_pairs = tuple(
        (reference_by_identity[identity], candidate_by_identity[identity])
        for identity in matched_identities
    )
    reference_count = len(reference.decisions)
    matched_count = len(matched_pairs)
    coverage = round(matched_count / reference_count, 6)

    whole_fn = sum(
        decision.reference_supported and not observation.whole_statement_exact_match
        for decision, observation in matched_pairs
    )
    whole_fp = sum(
        not decision.reference_supported and observation.whole_statement_exact_match
        for decision, observation in matched_pairs
    )
    atomic_fn = sum(
        decision.reference_supported and not observation.atomic_equivalence_match
        for decision, observation in matched_pairs
    )
    atomic_fp = sum(
        not decision.reference_supported and observation.atomic_equivalence_match
        for decision, observation in matched_pairs
    )
    fn_reduction = whole_fn - atomic_fn
    fp_delta = atomic_fp - whole_fp
    complete = matched_count == reference_count
    candidate_selected = (
        complete
        and matched_count > 0
        and fn_reduction > 0
        and fp_delta <= 0
        and candidate.pipeline_failure_count == 0
    )

    reasons: list[str] = []
    if not complete:
        reasons.append("claim_label_coverage_incomplete")
    if fn_reduction <= 0:
        reasons.append("atomic_equivalence_fn_not_reduced")
    else:
        reasons.append("atomic_equivalence_fn_reduced")
    if fp_delta > 0:
        reasons.append("atomic_equivalence_fp_increased")
    else:
        reasons.append("atomic_equivalence_fp_not_increased")
    if candidate.pipeline_failure_count:
        reasons.append("pipeline_failure_observed")
    reasons.extend(
        (
            "unanswerable_abstention_not_applicable",
            "whole_statement_exact_primary_unchanged",
            "screening_only",
        )
    )
    if reference.requires_human_signoff:
        reasons.append("requires_human_signoff")

    return AtomicClaimCalibrationSummary(
        schema_version="phase3.oracle_atomic_claim_calibration.v1",
        source_evaluation_run_id=candidate.source.source_evaluation_run_id,
        dataset_name=candidate.source.dataset_name,
        candidate_id=candidate.candidate_id,
        candidate_manifest_sha256=candidate_manifest_sha256,
        reference_manifest_sha256=reference_manifest_sha256,
        reference_schema_version=reference.schema_version,
        reference_provenance=reference.reviewer_provenance,
        requires_human_signoff=reference.requires_human_signoff,
        calibration_status=(
            "complete_hash_bound_claim_labels" if complete else "partial_hash_bound_claim_labels"
        ),
        calibration_coverage=coverage,
        reference_claim_count=reference_count,
        hash_matched_claim_count=matched_count,
        not_applicable_observation_count=len(candidate.not_applicable_observations),
        unanswerable_or_abstention_misapplication_count=0,
        whole_statement_exact_false_negative_count=whole_fn,
        whole_statement_exact_false_positive_count=whole_fp,
        atomic_equivalence_false_negative_count=atomic_fn,
        atomic_equivalence_false_positive_count=atomic_fp,
        false_negative_reduction_count=fn_reduction,
        false_positive_delta_count=fp_delta,
        pipeline_failure_count=candidate.pipeline_failure_count,
        candidate_selected=candidate_selected,
        decision=(
            "screening_candidate_selected" if candidate_selected else "screening_candidate_rejected"
        ),
        screening_only=True,
        primary_metric_status="calibrated_grounded_answer_pass_rate_unchanged",
        segmentation_status="presegmented_reference_claim",
        reason_codes=tuple(reasons),
    )
