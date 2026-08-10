from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Literal, Self, cast

from pydantic import Field, ValidationError, model_validator

from app.services.evaluation_atomic_claim_contracts import (
    AtomicClaimFullHashBinding,
    AtomicClaimFullNotApplicableBinding,
    SafeId,
    Sha256,
    StrictRawFreeModel,
    model_bytes_match,
)

from app.services.evaluation_atomic_claim_calibration_service import (
    AtomicClaimCalibrationSummary,
    AtomicClaimCandidateManifest,
    AtomicClaimCandidateObservation,
    AtomicClaimNotApplicableObservation,
    AtomicClaimReferenceDecision,
    AtomicClaimReviewManifest,
    AtomicClaimSourceContract,
    EvaluationAtomicClaimCalibrationError,
    evaluate_atomic_claim_candidate,
)

_StrictModel = StrictRawFreeModel


class AtomicClaimBlindReferenceDecision(AtomicClaimFullHashBinding):
    reference_supported: bool


class AtomicClaimBlindReviewManifest(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_blind_review.v1"]
    source: AtomicClaimSourceContract
    review_scope_id: Literal["rag79_run112_existing_review_subset"]
    review_scope_fingerprint: Sha256
    reviewer_provenance: SafeId
    reviewer_type: Literal["human", "codex_assisted", "automatic"]
    review_tool: SafeId
    review_tool_version: SafeId
    reviewed_at_utc: datetime
    review_status: Literal["requires_human_signoff", "human_signed_off"]
    requires_human_signoff: bool
    human_signoff_provenance: SafeId | None = None
    human_signoff_at_utc: datetime | None = None
    candidate_results_observed: Literal[False] = False
    candidate_identifiers_present: Literal[False] = False
    raw_content_persisted: Literal[False] = False
    decisions: tuple[AtomicClaimBlindReferenceDecision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_blind_reference(self) -> Self:
        if not _is_utc_aware(self.reviewed_at_utc):
            raise ValueError("blind_review_timestamp_not_utc_aware")
        identities = [decision.identity for decision in self.decisions]
        if len(identities) != len(set(identities)):
            raise ValueError("blind_review_claim_identity_duplicate")
        expected_scope = compute_review_scope_fingerprint(self.source, self.decisions)
        if self.review_scope_fingerprint != expected_scope:
            raise ValueError("blind_review_scope_fingerprint_mismatch")
        if self.review_status == "requires_human_signoff":
            if not self.requires_human_signoff:
                raise ValueError("blind_review_signoff_state_invalid")
            if self.human_signoff_provenance is not None or self.human_signoff_at_utc is not None:
                raise ValueError("blind_review_signoff_state_invalid")
        else:
            if self.requires_human_signoff:
                raise ValueError("blind_review_signoff_state_invalid")
            if self.human_signoff_provenance is None or self.human_signoff_at_utc is None:
                raise ValueError("blind_review_signoff_evidence_missing")
            if not _is_utc_aware(self.human_signoff_at_utc):
                raise ValueError("blind_review_signoff_timestamp_not_utc_aware")
            if self.human_signoff_at_utc < self.reviewed_at_utc:
                raise ValueError("blind_review_signoff_precedes_review")
        return self


class AtomicClaimBlindReviewCommitment(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_blind_commitment.v1"]
    source: AtomicClaimSourceContract
    review_scope_id: Literal["rag79_run112_existing_review_subset"]
    review_scope_fingerprint: Sha256
    reference_manifest_sha256: Sha256
    reference_claim_count: int = Field(gt=0)
    reviewer_provenance: SafeId
    reviewer_type: Literal["human", "codex_assisted", "automatic"]
    review_tool: SafeId
    review_tool_version: SafeId
    reviewed_at_utc: datetime
    committed_at_utc: datetime
    review_status: Literal["requires_human_signoff", "human_signed_off"]
    requires_human_signoff: bool
    candidate_results_observed: Literal[False] = False
    candidate_identifiers_present: Literal[False] = False
    raw_content_persisted: Literal[False] = False

    @model_validator(mode="after")
    def validate_commitment_timestamp(self) -> Self:
        if not _is_utc_aware(self.reviewed_at_utc) or not _is_utc_aware(self.committed_at_utc):
            raise ValueError("blind_review_commitment_timestamp_not_utc_aware")
        if self.committed_at_utc < self.reviewed_at_utc:
            raise ValueError("blind_review_commitment_precedes_review")
        if self.review_status == "requires_human_signoff" and not self.requires_human_signoff:
            raise ValueError("blind_review_signoff_state_invalid")
        if self.review_status == "human_signed_off" and self.requires_human_signoff:
            raise ValueError("blind_review_signoff_state_invalid")
        return self


class AtomicClaimBlindCandidateObservation(AtomicClaimFullHashBinding):
    segmentation_status: Literal["presegmented_reference_claim"]
    whole_statement_exact_match: bool
    atomic_equivalence_match: bool


class AtomicClaimBlindNotApplicableObservation(AtomicClaimFullNotApplicableBinding):
    reason: Literal["unanswerable", "abstention"]


class AtomicClaimBlindCandidateManifest(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_blind_candidate.v1"]
    source: AtomicClaimSourceContract
    review_scope_id: Literal["rag79_run112_existing_review_subset"]
    review_scope_fingerprint: Sha256
    phase_a_reference_manifest_sha256: Sha256
    candidate_id: SafeId
    candidate_version: SafeId
    candidate_dimension: Literal["semantic_equivalence_only"]
    raw_content_persisted: Literal[False] = False
    pipeline_failure_count: int = Field(ge=0)
    observations: tuple[AtomicClaimBlindCandidateObservation, ...] = ()
    not_applicable_observations: tuple[AtomicClaimBlindNotApplicableObservation, ...] = ()

    @model_validator(mode="after")
    def validate_candidate_uniqueness_and_applicability(self) -> Self:
        identities = [observation.identity for observation in self.observations]
        if len(identities) != len(set(identities)):
            raise ValueError("blind_candidate_claim_identity_duplicate")
        not_applicable = [
            observation.observation_identity for observation in self.not_applicable_observations
        ]
        if len(not_applicable) != len(set(not_applicable)):
            raise ValueError("blind_candidate_not_applicable_identity_duplicate")
        applicable = {
            (observation.case_id, observation.answer_hash, observation.context_hash)
            for observation in self.observations
        }
        if applicable.intersection(not_applicable):
            raise ValueError("blind_candidate_not_applicable_overlap")
        return self


class AtomicClaimBlindPhaseBResult(_StrictModel):
    schema_version: Literal["phase3.oracle_atomic_claim_blind_phase_b.v1"]
    phase_a_commitment_sha256: Sha256
    reference_manifest_sha256: Sha256
    candidate_manifest_sha256: Sha256
    review_scope_id: str
    review_scope_fingerprint: Sha256
    reviewer_provenance: str
    reviewer_type: str
    review_tool: str
    review_tool_version: str
    reviewed_at_utc: datetime
    review_status: str
    requires_human_signoff: bool
    reference_label_authority: Literal["human_calibrated", "auxiliary_requires_human_signoff"]
    calibration: AtomicClaimCalibrationSummary
    candidate_selected: bool
    screening_only: Literal[True]
    public_accuracy_claim_allowed: Literal[False] = False
    profile_promotion_allowed: Literal[False] = False
    reason_codes: tuple[str, ...]

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], self.model_dump(mode="json"))


def compute_review_scope_fingerprint(
    source: AtomicClaimSourceContract,
    decisions: tuple[AtomicClaimBlindReferenceDecision, ...],
) -> str:
    claims = sorted(
        (decision.scope_binding() for decision in decisions),
        key=lambda item: (
            str(item["case_id"]),
            str(item["required_fact_id"]),
            int(cast(int, item["claim_ordinal"])),
        ),
    )
    payload = {
        "source": source.model_dump(mode="json"),
        "claims": claims,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_phase_a_commitment(
    reference_manifest_bytes: bytes,
    reference: AtomicClaimBlindReviewManifest,
    *,
    committed_at_utc: datetime | None = None,
) -> AtomicClaimBlindReviewCommitment:
    if not model_bytes_match(reference_manifest_bytes, reference):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_reference_bytes_model_mismatch")
    committed_at = committed_at_utc or datetime.now(UTC)
    return AtomicClaimBlindReviewCommitment(
        schema_version="phase3.oracle_atomic_claim_blind_commitment.v1",
        source=reference.source,
        review_scope_id=reference.review_scope_id,
        review_scope_fingerprint=reference.review_scope_fingerprint,
        reference_manifest_sha256=hashlib.sha256(reference_manifest_bytes).hexdigest(),
        reference_claim_count=len(reference.decisions),
        reviewer_provenance=reference.reviewer_provenance,
        reviewer_type=reference.reviewer_type,
        review_tool=reference.review_tool,
        review_tool_version=reference.review_tool_version,
        reviewed_at_utc=reference.reviewed_at_utc,
        committed_at_utc=committed_at,
        review_status=reference.review_status,
        requires_human_signoff=reference.requires_human_signoff,
        candidate_results_observed=False,
        candidate_identifiers_present=False,
        raw_content_persisted=False,
    )


def evaluate_phase_b(
    *,
    reference_manifest_bytes: bytes,
    reference: AtomicClaimBlindReviewManifest,
    commitment_bytes: bytes,
    commitment: AtomicClaimBlindReviewCommitment,
    candidate_manifest_bytes: bytes,
    candidate: AtomicClaimBlindCandidateManifest,
) -> AtomicClaimBlindPhaseBResult:
    reference_hash = hashlib.sha256(reference_manifest_bytes).hexdigest()
    commitment_hash = hashlib.sha256(commitment_bytes).hexdigest()
    candidate_hash = hashlib.sha256(candidate_manifest_bytes).hexdigest()

    if not model_bytes_match(reference_manifest_bytes, reference):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_reference_bytes_model_mismatch")
    if not model_bytes_match(commitment_bytes, commitment):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_commitment_bytes_model_mismatch")
    if not model_bytes_match(candidate_manifest_bytes, candidate):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_candidate_bytes_model_mismatch")
    if reference_hash != commitment.reference_manifest_sha256:
        raise EvaluationAtomicClaimCalibrationError(
            "atomic_claim_phase_a_reference_manifest_hash_mismatch"
        )
    if reference.source != commitment.source or candidate.source != reference.source:
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_source_fingerprint_drift")
    if (
        reference.review_scope_id != commitment.review_scope_id
        or candidate.review_scope_id != reference.review_scope_id
        or reference.review_scope_fingerprint != commitment.review_scope_fingerprint
        or candidate.review_scope_fingerprint != reference.review_scope_fingerprint
    ):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_review_scope_drift")
    if candidate.phase_a_reference_manifest_sha256 != reference_hash:
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_candidate_not_bound_to_phase_a")
    if commitment.reference_claim_count != len(reference.decisions):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_phase_a_claim_count_drift")
    if (
        commitment.reviewer_provenance != reference.reviewer_provenance
        or commitment.reviewer_type != reference.reviewer_type
        or commitment.review_tool != reference.review_tool
        or commitment.review_tool_version != reference.review_tool_version
        or commitment.reviewed_at_utc != reference.reviewed_at_utc
        or commitment.review_status != reference.review_status
        or commitment.requires_human_signoff != reference.requires_human_signoff
    ):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_phase_a_provenance_drift")

    reference_by_identity = {decision.identity: decision for decision in reference.decisions}
    candidate_by_identity = {
        observation.identity: observation for observation in candidate.observations
    }
    if set(reference_by_identity) != set(candidate_by_identity):
        raise EvaluationAtomicClaimCalibrationError("atomic_claim_phase_b_coverage_incomplete")

    for identity, decision in reference_by_identity.items():
        observation = candidate_by_identity[identity]
        if (
            observation.question_hash != decision.question_hash
            or observation.source_hash != decision.source_hash
            or observation.answer_hash != decision.answer_hash
            or observation.context_hash != decision.context_hash
            or observation.required_fact_hash != decision.required_fact_hash
        ):
            raise EvaluationAtomicClaimCalibrationError("atomic_claim_phase_b_hash_binding_drift")

    rag81_reference = AtomicClaimReviewManifest(
        schema_version="phase3.oracle_atomic_claim_review.v1",
        source=reference.source,
        reviewer_provenance=reference.reviewer_provenance,
        reviewer_version=reference.review_tool_version,
        review_status=reference.review_status,
        requires_human_signoff=reference.requires_human_signoff,
        raw_content_persisted=False,
        decisions=tuple(
            AtomicClaimReferenceDecision(
                case_id=decision.case_id,
                answer_hash=decision.answer_hash,
                context_hash=decision.context_hash,
                required_fact_id=decision.required_fact_id,
                claim_ordinal=decision.claim_ordinal,
                reference_supported=decision.reference_supported,
            )
            for decision in reference.decisions
        ),
    )
    rag81_candidate = AtomicClaimCandidateManifest(
        schema_version="phase3.oracle_atomic_claim_candidate.v1",
        source=candidate.source,
        candidate_id=candidate.candidate_id,
        candidate_version=candidate.candidate_version,
        candidate_dimension=candidate.candidate_dimension,
        raw_content_persisted=False,
        pipeline_failure_count=candidate.pipeline_failure_count,
        observations=tuple(
            AtomicClaimCandidateObservation(
                case_id=observation.case_id,
                answer_hash=observation.answer_hash,
                context_hash=observation.context_hash,
                required_fact_id=observation.required_fact_id,
                claim_ordinal=observation.claim_ordinal,
                segmentation_status=observation.segmentation_status,
                whole_statement_exact_match=observation.whole_statement_exact_match,
                atomic_equivalence_match=observation.atomic_equivalence_match,
            )
            for observation in candidate.observations
        ),
        not_applicable_observations=tuple(
            AtomicClaimNotApplicableObservation(
                case_id=observation.case_id,
                answer_hash=observation.answer_hash,
                context_hash=observation.context_hash,
                reason=observation.reason,
            )
            for observation in candidate.not_applicable_observations
        ),
    )
    calibration = evaluate_atomic_claim_candidate(
        rag81_candidate,
        reference=rag81_reference,
        candidate_manifest_sha256=candidate_hash,
        reference_manifest_sha256=reference_hash,
        reference_schema_version=reference.schema_version,
        reference_provenance=reference.reviewer_provenance,
        requires_human_signoff=reference.requires_human_signoff,
    )
    if reference.requires_human_signoff and calibration.candidate_selected:
        calibration = calibration.model_copy(
            update={
                "candidate_selected": False,
                "decision": "screening_candidate_rejected",
                "reason_codes": tuple(
                    dict.fromkeys((*calibration.reason_codes, "requires_human_signoff"))
                ),
            }
        )

    authority = (
        "auxiliary_requires_human_signoff"
        if reference.requires_human_signoff
        else "human_calibrated"
    )
    reasons = [
        "phase_a_blind_commitment_verified",
        "phase_b_complete_hash_binding_verified",
        "whole_statement_exact_primary_unchanged",
        "screening_only",
        "public_accuracy_claim_forbidden",
        "profile_promotion_forbidden",
    ]
    if reference.requires_human_signoff:
        reasons.append("requires_human_signoff")

    return AtomicClaimBlindPhaseBResult(
        schema_version="phase3.oracle_atomic_claim_blind_phase_b.v1",
        phase_a_commitment_sha256=commitment_hash,
        reference_manifest_sha256=reference_hash,
        candidate_manifest_sha256=candidate_hash,
        review_scope_id=reference.review_scope_id,
        review_scope_fingerprint=reference.review_scope_fingerprint,
        reviewer_provenance=reference.reviewer_provenance,
        reviewer_type=reference.reviewer_type,
        review_tool=reference.review_tool,
        review_tool_version=reference.review_tool_version,
        reviewed_at_utc=reference.reviewed_at_utc,
        review_status=reference.review_status,
        requires_human_signoff=reference.requires_human_signoff,
        reference_label_authority=authority,
        calibration=calibration,
        candidate_selected=calibration.candidate_selected,
        screening_only=True,
        public_accuracy_claim_allowed=False,
        profile_promotion_allowed=False,
        reason_codes=tuple(reasons),
    )


def _is_utc_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)
