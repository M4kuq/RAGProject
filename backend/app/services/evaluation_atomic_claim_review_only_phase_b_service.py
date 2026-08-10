from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindCandidateManifest,
    AtomicClaimBlindCandidateObservation,
    AtomicClaimBlindPhaseBResult,
    AtomicClaimBlindReviewCommitment,
    AtomicClaimBlindReviewManifest,
)
from app.services.evaluation_atomic_claim_contracts import (
    AtomicClaimReviewCalibrationSourceContract,
    SafeId,
    Sha256,
    StrictRawFreeModel,
    model_bytes_match,
    read_json_object,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    AtomicClaimReviewCalibrationRunManifest,
    AtomicClaimReviewCalibrationScopeManifest,
    load_review_input,
)
from app.services.evaluation_oracle_context_service import _normalize as _normalize_oracle_text

_CANDIDATE_ID: Literal["rag83_review_only_atomic_equivalence"] = (
    "rag83_review_only_atomic_equivalence"
)
_CANDIDATE_VERSION: Literal["deterministic_identifier_equivalence_v1"] = (
    "deterministic_identifier_equivalence_v1"
)
_REVIEW_RUN_ID = "rag83-review-only-20260810T055955Z"
_REVIEW_SCOPE_ID = "rag83_review_only_calibration_all_answerable_dev_v1"


class EvaluationAtomicClaimReviewOnlyPhaseBError(RuntimeError):
    """Stable fail-closed error for review-only candidate construction."""


class AtomicClaimMixedProvenanceAuditSidecar(StrictRawFreeModel):
    schema_version: Literal["phase3.oracle_atomic_claim_mixed_provenance_audit.v1"]
    review_run_id: SafeId
    reference_manifest_sha256: Sha256
    phase_a_commitment_sha256: Sha256
    candidate_manifest_sha256: Sha256
    phase_b_result_sha256: Sha256
    authoritative_claim_count: int = Field(gt=0)
    user_origin_label_count: int = Field(ge=0)
    codex_assisted_pending_fill_count: int = Field(ge=0)
    codex_assisted_correction_count: int = Field(ge=0)
    user_final_acceptance_claim_count: int = Field(gt=0)
    signed_manifest_reviewer_type: Literal["human"]
    signed_manifest_representation: Literal["human_only_fields"]
    independent_human_only_labels: Literal[False] = False
    signed_authority_modified: Literal[False] = False
    screening_only: Literal[True] = True
    public_accuracy_claim_allowed: Literal[False] = False
    gold_holdout_eligible: Literal[False] = False
    profile_promotion_allowed: Literal[False] = False
    reason_codes: tuple[
        Literal[
            "mixed_provenance_disclosed",
            "user_final_acceptance_recorded",
            "independent_human_only_claim_forbidden",
        ],
        ...,
    ]

    @model_validator(mode="after")
    def validate_provenance_counts(self) -> Self:
        attributed = (
            self.user_origin_label_count
            + self.codex_assisted_pending_fill_count
            + self.codex_assisted_correction_count
        )
        if attributed != self.authoritative_claim_count:
            raise ValueError("atomic_claim_mixed_provenance_count_drift")
        if self.user_final_acceptance_claim_count != self.authoritative_claim_count:
            raise ValueError("atomic_claim_mixed_provenance_acceptance_count_drift")
        expected_reasons = (
            "mixed_provenance_disclosed",
            "user_final_acceptance_recorded",
            "independent_human_only_claim_forbidden",
        )
        if self.reason_codes != expected_reasons:
            raise ValueError("atomic_claim_mixed_provenance_reason_drift")
        return self


def build_review_only_candidate(
    *,
    scope_manifest_path: Path,
    run_manifest_path: Path,
    private_input_path: Path,
    phase_a_commitment_path: Path,
) -> AtomicClaimBlindCandidateManifest:
    scope_bytes, scope_payload = read_json_object(scope_manifest_path)
    run_bytes, run_payload = read_json_object(run_manifest_path)
    commitment_bytes, commitment_payload = read_json_object(phase_a_commitment_path)
    scope = AtomicClaimReviewCalibrationScopeManifest.model_validate(scope_payload)
    run_manifest = AtomicClaimReviewCalibrationRunManifest.model_validate(run_payload)
    commitment = AtomicClaimBlindReviewCommitment.model_validate(commitment_payload)
    if not model_bytes_match(scope_bytes, scope):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_scope_bytes_model_mismatch"
        )
    if not model_bytes_match(run_bytes, run_manifest):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_run_bytes_model_mismatch"
        )
    if not model_bytes_match(commitment_bytes, commitment):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_commitment_bytes_model_mismatch"
        )
    if hashlib.sha256(run_bytes).hexdigest() != scope.run_manifest_sha256:
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_run_manifest_hash_drift"
        )
    loaded = load_review_input(scope_manifest_path, private_input_path)
    if (
        run_manifest.source != scope.source
        or loaded.source != scope.source
        or commitment.source != scope.source
    ):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_source_drift"
        )
    if (
        loaded.review_scope_id != scope.review_scope_id
        or commitment.review_scope_id != scope.review_scope_id
        or commitment.review_scope_fingerprint != loaded.review_scope_fingerprint
    ):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_scope_drift"
        )
    if commitment.reference_claim_count != len(loaded.claims):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_claim_count_drift"
        )
    if commitment.review_status != "human_signed_off" or commitment.requires_human_signoff:
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_human_signoff_required"
        )
    if scope.source.pipeline_failure_count:
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_pipeline_failure"
        )
    if (
        scope.source.review_run_id != _REVIEW_RUN_ID
        or scope.review_scope_id != _REVIEW_SCOPE_ID
        or scope.source.selected_case_count != 24
        or scope.source.succeeded_case_count != 24
        or scope.target_case_count != 24
        or scope.target_observation_count != 24
    ):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_review_candidate_fixed_authority_drift"
        )

    fixtures = {
        case.case_key: case for case in build_local_accuracy_dev_manifest().cases if case.answerable
    }
    observations: list[AtomicClaimBlindCandidateObservation] = []
    for claim in loaded.claims:
        case = fixtures.get(claim.binding.case_id)
        if case is None:
            raise EvaluationAtomicClaimReviewOnlyPhaseBError(
                "atomic_claim_review_candidate_case_unbound"
            )
        fact_by_id = {fact.fact_id: fact for fact in case.required_facts}
        fact = fact_by_id.get(claim.binding.required_fact_id)
        if fact is None or _normalize_identifier_text(fact.statement) != _normalize_identifier_text(
            claim.required_fact
        ):
            raise EvaluationAtomicClaimReviewOnlyPhaseBError(
                "atomic_claim_review_candidate_fact_unbound"
            )
        strong_tokens = _strong_fact_identifier_tokens(
            fact.statement,
            sibling_statements=tuple(
                sibling.statement
                for sibling in case.required_facts
                if sibling.fact_id != fact.fact_id
            ),
        )
        if not strong_tokens:
            raise EvaluationAtomicClaimReviewOnlyPhaseBError(
                "atomic_claim_review_candidate_identifier_ambiguous"
            )
        source_text = "\n".join(f"{title}\n{body}" for title, body in claim.source_evidence)
        context_text = "\n".join(claim.context_items)
        source_tokens = _identifier_tokens(source_text)
        context_tokens = _identifier_tokens(context_text)
        if not strong_tokens.issubset(source_tokens) or not strong_tokens.issubset(context_tokens):
            raise EvaluationAtomicClaimReviewOnlyPhaseBError(
                "atomic_claim_review_candidate_grounding_missing"
            )
        whole_match = _whole_statement_exact_match(
            answer=claim.answer,
            required_fact=claim.required_fact,
        )
        atomic_match = strong_tokens.issubset(_identifier_tokens(claim.answer))
        observations.append(
            AtomicClaimBlindCandidateObservation(
                **claim.binding.model_dump(mode="json"),
                segmentation_status="presegmented_reference_claim",
                whole_statement_exact_match=whole_match,
                atomic_equivalence_match=atomic_match,
            )
        )

    return AtomicClaimBlindCandidateManifest(
        schema_version="phase3.oracle_atomic_claim_blind_candidate.v1",
        source=scope.source,
        review_scope_id=scope.review_scope_id,
        review_scope_fingerprint=loaded.review_scope_fingerprint,
        phase_a_reference_manifest_sha256=commitment.reference_manifest_sha256,
        candidate_id=_CANDIDATE_ID,
        candidate_version=_CANDIDATE_VERSION,
        candidate_dimension="semantic_equivalence_only",
        raw_content_persisted=False,
        pipeline_failure_count=scope.source.pipeline_failure_count,
        observations=tuple(observations),
        not_applicable_observations=(),
    )


def build_mixed_provenance_audit_sidecar(
    *,
    reference_manifest_bytes: bytes,
    reference: AtomicClaimBlindReviewManifest,
    commitment_bytes: bytes,
    commitment: AtomicClaimBlindReviewCommitment,
    candidate_manifest_bytes: bytes,
    candidate: AtomicClaimBlindCandidateManifest,
    phase_b_result_bytes: bytes,
    phase_b_result: AtomicClaimBlindPhaseBResult,
    user_origin_label_count: int,
    codex_assisted_pending_fill_count: int,
    codex_assisted_correction_count: int,
    user_final_acceptance_claim_count: int,
) -> AtomicClaimMixedProvenanceAuditSidecar:
    for payload_bytes, model, reason in (
        (
            reference_manifest_bytes,
            reference,
            "atomic_claim_mixed_provenance_reference_bytes_model_mismatch",
        ),
        (
            commitment_bytes,
            commitment,
            "atomic_claim_mixed_provenance_commitment_bytes_model_mismatch",
        ),
        (
            candidate_manifest_bytes,
            candidate,
            "atomic_claim_mixed_provenance_candidate_bytes_model_mismatch",
        ),
        (
            phase_b_result_bytes,
            phase_b_result,
            "atomic_claim_mixed_provenance_phase_b_bytes_model_mismatch",
        ),
    ):
        if not model_bytes_match(payload_bytes, model):
            raise EvaluationAtomicClaimReviewOnlyPhaseBError(reason)

    reference_hash = hashlib.sha256(reference_manifest_bytes).hexdigest()
    commitment_hash = hashlib.sha256(commitment_bytes).hexdigest()
    candidate_hash = hashlib.sha256(candidate_manifest_bytes).hexdigest()
    if (
        phase_b_result.reference_manifest_sha256 != reference_hash
        or phase_b_result.phase_a_commitment_sha256 != commitment_hash
        or phase_b_result.candidate_manifest_sha256 != candidate_hash
    ):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_mixed_provenance_phase_b_binding_drift"
        )
    if (
        reference.review_status != "human_signed_off"
        or reference.requires_human_signoff
        or reference.reviewer_type != "human"
        or not isinstance(reference.source, AtomicClaimReviewCalibrationSourceContract)
    ):
        raise EvaluationAtomicClaimReviewOnlyPhaseBError(
            "atomic_claim_mixed_provenance_signoff_invalid"
        )

    return AtomicClaimMixedProvenanceAuditSidecar(
        schema_version="phase3.oracle_atomic_claim_mixed_provenance_audit.v1",
        review_run_id=reference.source.review_run_id,
        reference_manifest_sha256=reference_hash,
        phase_a_commitment_sha256=commitment_hash,
        candidate_manifest_sha256=candidate_hash,
        phase_b_result_sha256=hashlib.sha256(phase_b_result_bytes).hexdigest(),
        authoritative_claim_count=len(reference.decisions),
        user_origin_label_count=user_origin_label_count,
        codex_assisted_pending_fill_count=codex_assisted_pending_fill_count,
        codex_assisted_correction_count=codex_assisted_correction_count,
        user_final_acceptance_claim_count=user_final_acceptance_claim_count,
        signed_manifest_reviewer_type="human",
        signed_manifest_representation="human_only_fields",
        independent_human_only_labels=False,
        signed_authority_modified=False,
        screening_only=True,
        public_accuracy_claim_allowed=False,
        gold_holdout_eligible=False,
        profile_promotion_allowed=False,
        reason_codes=(
            "mixed_provenance_disclosed",
            "user_final_acceptance_recorded",
            "independent_human_only_claim_forbidden",
        ),
    )


def _whole_statement_exact_match(*, answer: str, required_fact: str) -> bool:
    normalized_answer = _normalize_oracle_text(answer).casefold()
    normalized_fact = _normalize_oracle_text(required_fact).casefold()
    return normalized_fact in normalized_answer


def _strong_fact_identifier_tokens(
    statement: str,
    *,
    sibling_statements: tuple[str, ...],
) -> frozenset[str]:
    own = _identifier_tokens(statement)
    sibling = frozenset[str]().union(*(_identifier_tokens(item) for item in sibling_statements))
    unique = own - sibling
    return unique or own


def _identifier_tokens(value: str) -> frozenset[str]:
    normalized = _normalize_identifier_text(value)
    return frozenset(
        token
        for token in re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", normalized)
        if any(character.isdigit() for character in token) and len(token) >= 2
    )


def _normalize_identifier_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
