from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel, ValidationError

from app.rag.generation import GenerationRequest, GenerationResult
from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindCandidateManifest,
    AtomicClaimBlindReviewCommitment,
    AtomicClaimBlindReviewManifest,
    EvaluationAtomicClaimCalibrationError,
    evaluate_phase_b,
)
from app.services.evaluation_atomic_claim_contracts import (
    AtomicClaimReviewCalibrationSourceContract,
    canonical_json_bytes,
)
from app.services.evaluation_atomic_claim_review_only_phase_b_service import (
    EvaluationAtomicClaimReviewOnlyPhaseBError,
    build_mixed_provenance_audit_sidecar,
    build_review_only_candidate,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    EvaluationAtomicClaimReviewWorkflowError,
    create_review_session,
    generate_review_only_calibration_run,
)


class _FixedGenerator:
    def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        return GenerationResult(content="synthetic fixed answer [1]", usage=None)


def test_review_only_candidate_is_deterministic_label_blind_and_raw_free(
    tmp_path: Path,
) -> None:
    files = _signed_review_only_run(tmp_path)

    first = build_review_only_candidate(
        scope_manifest_path=files["scope"],
        run_manifest_path=files["run"],
        private_input_path=files["private"],
        phase_a_commitment_path=files["commitment"],
    )
    second = build_review_only_candidate(
        scope_manifest_path=files["scope"],
        run_manifest_path=files["run"],
        private_input_path=files["private"],
        phase_a_commitment_path=files["commitment"],
    )

    first_bytes = canonical_json_bytes(first)
    assert first_bytes == canonical_json_bytes(second)
    assert len(first.observations) == 36
    assert first.not_applicable_observations == ()
    assert first.pipeline_failure_count == 0
    assert isinstance(first.source, AtomicClaimReviewCalibrationSourceContract)
    assert first.source.review_run_id == "rag83-review-only-20260810T055955Z"
    assert (
        "reference_manifest_path" not in inspect.signature(build_review_only_candidate).parameters
    )
    rendered = first_bytes.decode("utf-8")
    for forbidden in (
        '"question"',
        '"source_text"',
        '"answer_text"',
        '"context_items"',
        '"required_fact"',
        '"reference_supported"',
        "synthetic fixed answer",
    ):
        assert forbidden not in rendered


def test_review_only_phase_b_scores_complete_claim_level_metrics(
    tmp_path: Path,
) -> None:
    files = _signed_review_only_run(tmp_path)
    reference_bytes, reference = _load(files["reference"], AtomicClaimBlindReviewManifest)
    commitment_bytes, commitment = _load(files["commitment"], AtomicClaimBlindReviewCommitment)
    candidate = build_review_only_candidate(
        scope_manifest_path=files["scope"],
        run_manifest_path=files["run"],
        private_input_path=files["private"],
        phase_a_commitment_path=files["commitment"],
    )
    candidate_bytes = canonical_json_bytes(candidate)

    result = evaluate_phase_b(
        reference_manifest_bytes=reference_bytes,
        reference=reference,
        commitment_bytes=commitment_bytes,
        commitment=commitment,
        candidate_manifest_bytes=candidate_bytes,
        candidate=candidate,
    )

    assert result.calibration.source_evaluation_run_id is None
    assert result.calibration.source_review_run_id == "rag83-review-only-20260810T055955Z"
    assert result.calibration.calibration_coverage == 1.0
    assert result.calibration.reference_claim_count == 36
    assert result.calibration.authoritative_reference_claim_count == 36
    assert result.calibration.not_applicable_observation_count == 0
    assert result.calibration.whole_statement_exact_metrics is not None
    assert result.calibration.atomic_equivalence_metrics is not None

    legacy_summary = result.calibration.model_dump(mode="json")
    for additive_field in (
        "source_review_run_id",
        "authoritative_reference_claim_count",
        "whole_statement_exact_metrics",
        "atomic_equivalence_metrics",
    ):
        legacy_summary.pop(additive_field)
    parsed_legacy = type(result.calibration).model_validate(legacy_summary)
    assert parsed_legacy.source_review_run_id is None
    assert parsed_legacy.authoritative_reference_claim_count is None

    result_bytes = canonical_json_bytes(result)
    sidecar = build_mixed_provenance_audit_sidecar(
        reference_manifest_bytes=reference_bytes,
        reference=reference,
        commitment_bytes=commitment_bytes,
        commitment=commitment,
        candidate_manifest_bytes=candidate_bytes,
        candidate=candidate,
        phase_b_result_bytes=result_bytes,
        phase_b_result=result,
        user_origin_label_count=14,
        codex_assisted_pending_fill_count=21,
        codex_assisted_correction_count=1,
        user_final_acceptance_claim_count=36,
    )
    assert sidecar.independent_human_only_labels is False
    assert sidecar.signed_authority_modified is False
    assert sidecar.user_final_acceptance_claim_count == 36


def test_review_only_candidate_fails_closed_on_private_or_authority_drift(
    tmp_path: Path,
) -> None:
    files = _signed_review_only_run(tmp_path)
    private_payload = json.loads(files["private"].read_text(encoding="utf-8"))
    private_payload["scope_manifest_sha256"] = "0" * 64
    drifted_private = tmp_path / "drifted-private.json"
    drifted_private.write_bytes(canonical_json_bytes(private_payload))

    with pytest.raises(
        EvaluationAtomicClaimReviewWorkflowError,
        match="atomic_claim_review_private_scope_hash_drift",
    ):
        build_review_only_candidate(
            scope_manifest_path=files["scope"],
            run_manifest_path=files["run"],
            private_input_path=drifted_private,
            phase_a_commitment_path=files["commitment"],
        )

    scope_payload = json.loads(files["scope"].read_text(encoding="utf-8"))
    mutations = (
        ("dataset_name", "other_dataset"),
        ("resolved_generation_model", "other/model"),
        ("generation_max_output_tokens", 4096),
        ("dataset_content_fingerprint", "f" * 64),
    )
    for field_name, value in mutations:
        drifted_scope_payload = json.loads(json.dumps(scope_payload))
        drifted_scope_payload["source"][field_name] = value
        drifted_scope = tmp_path / f"drifted-scope-{field_name}.json"
        drifted_scope.write_bytes(canonical_json_bytes(drifted_scope_payload))
        with pytest.raises(ValidationError):
            build_review_only_candidate(
                scope_manifest_path=drifted_scope,
                run_manifest_path=files["run"],
                private_input_path=files["private"],
                phase_a_commitment_path=files["commitment"],
            )

    candidate = build_review_only_candidate(
        scope_manifest_path=files["scope"],
        run_manifest_path=files["run"],
        private_input_path=files["private"],
        phase_a_commitment_path=files["commitment"],
    )
    wrong_scope_candidate = candidate.model_dump(mode="json")
    wrong_scope_candidate["review_scope_id"] = "rag79_run112_existing_review_subset"
    with pytest.raises(ValidationError, match="blind_review_scope_source_mismatch"):
        AtomicClaimBlindCandidateManifest.model_validate(wrong_scope_candidate)


def test_phase_b_rejects_arbitrary_review_only_run(tmp_path: Path) -> None:
    files = _signed_review_only_run(tmp_path)
    reference_bytes, reference = _load(files["reference"], AtomicClaimBlindReviewManifest)
    commitment_bytes, commitment = _load(files["commitment"], AtomicClaimBlindReviewCommitment)
    candidate = build_review_only_candidate(
        scope_manifest_path=files["scope"],
        run_manifest_path=files["run"],
        private_input_path=files["private"],
        phase_a_commitment_path=files["commitment"],
    )
    assert isinstance(reference.source, AtomicClaimReviewCalibrationSourceContract)
    wrong_source = reference.source.model_copy(update={"review_run_id": "rag83-review-only-other"})
    with pytest.raises(
        EvaluationAtomicClaimCalibrationError,
        match="atomic_claim_review_only_phase_b_authority_unsupported",
    ):
        evaluate_phase_b(
            reference_manifest_bytes=reference_bytes,
            reference=reference.model_copy(update={"source": wrong_source}),
            commitment_bytes=commitment_bytes,
            commitment=commitment,
            candidate_manifest_bytes=canonical_json_bytes(candidate),
            candidate=candidate,
        )

    run_payload = json.loads(files["run"].read_text(encoding="utf-8"))
    run_payload["source"]["review_run_id"] = "rag83-review-only-other"
    drifted_run = tmp_path / "drifted-run.json"
    drifted_run.write_bytes(canonical_json_bytes(run_payload))
    with pytest.raises(
        EvaluationAtomicClaimReviewOnlyPhaseBError,
        match="atomic_claim_review_candidate_run_manifest_hash_drift",
    ):
        build_review_only_candidate(
            scope_manifest_path=files["scope"],
            run_manifest_path=drifted_run,
            private_input_path=files["private"],
            phase_a_commitment_path=files["commitment"],
        )


def test_mixed_provenance_rejects_count_drift(tmp_path: Path) -> None:
    files = _signed_review_only_run(tmp_path)
    reference_bytes, reference = _load(files["reference"], AtomicClaimBlindReviewManifest)
    commitment_bytes, commitment = _load(files["commitment"], AtomicClaimBlindReviewCommitment)
    candidate = build_review_only_candidate(
        scope_manifest_path=files["scope"],
        run_manifest_path=files["run"],
        private_input_path=files["private"],
        phase_a_commitment_path=files["commitment"],
    )
    candidate_bytes = canonical_json_bytes(candidate)
    result = evaluate_phase_b(
        reference_manifest_bytes=reference_bytes,
        reference=reference,
        commitment_bytes=commitment_bytes,
        commitment=commitment,
        candidate_manifest_bytes=candidate_bytes,
        candidate=candidate,
    )

    with pytest.raises(ValueError, match="atomic_claim_mixed_provenance_count_drift"):
        build_mixed_provenance_audit_sidecar(
            reference_manifest_bytes=reference_bytes,
            reference=reference,
            commitment_bytes=commitment_bytes,
            commitment=commitment,
            candidate_manifest_bytes=candidate_bytes,
            candidate=candidate,
            phase_b_result_bytes=canonical_json_bytes(result),
            phase_b_result=result,
            user_origin_label_count=13,
            codex_assisted_pending_fill_count=21,
            codex_assisted_correction_count=1,
            user_final_acceptance_claim_count=36,
        )


def _signed_review_only_run(tmp_path: Path) -> dict[str, Path]:
    raw_free = tmp_path / "raw-free"
    generated = generate_review_only_calibration_run(
        review_run_id="rag83-review-only-20260810T055955Z",
        raw_free_output_dir=raw_free,
        private_input_path=tmp_path / "private" / "input.json",
        generator=_FixedGenerator(),
        started_at_utc=datetime(2026, 8, 10, tzinfo=UTC),
    )
    session = create_review_session(
        scope_manifest_path=generated.scope_manifest_path,
        private_input_path=generated.private_input_path,
        output_dir=raw_free,
        reviewer_provenance="human:test-reviewer",
    )
    for index in range(generated.claim_count):
        session.vote(index, "supported")
    session.finalize(confirm_human_signoff=True)
    return {
        "run": generated.run_manifest_path,
        "scope": generated.scope_manifest_path,
        "private": generated.private_input_path,
        "reference": raw_free / "rag83-reference-manifest.json",
        "commitment": raw_free / "rag83-phase-a-commitment.json",
    }


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _load(path: Path, model_type: type[_ModelT]) -> tuple[bytes, _ModelT]:
    payload_bytes = path.read_bytes()
    payload = json.loads(payload_bytes)
    model = model_type.model_validate(payload)
    return payload_bytes, model
