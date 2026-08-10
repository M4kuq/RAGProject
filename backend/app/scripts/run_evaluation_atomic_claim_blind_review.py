from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindCandidateManifest,
    AtomicClaimBlindPhaseBResult,
    AtomicClaimBlindReviewCommitment,
    AtomicClaimBlindReviewManifest,
    EvaluationAtomicClaimCalibrationError,
    build_phase_a_commitment,
    evaluate_phase_b,
)
from app.services.evaluation_atomic_claim_contracts import (
    print_blocked as _blocked,
)
from app.services.evaluation_atomic_claim_contracts import (
    read_json_object as _read_object,
)
from app.services.evaluation_atomic_claim_contracts import (
    write_raw_free_text as _write_safe_output,
)
from app.services.evaluation_atomic_claim_review_only_phase_b_service import (
    EvaluationAtomicClaimReviewOnlyPhaseBError,
    build_mixed_provenance_audit_sidecar,
    build_review_only_candidate,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Commit a candidate-blind raw-free per-claim reference before evaluating "
            "the additive RAG-81 atomic-equivalence coordinate."
        )
    )
    subparsers = parser.add_subparsers(dest="phase", required=True)

    phase_a = subparsers.add_parser("phase-a")
    phase_a.add_argument("--reference-manifest", type=Path, required=True)
    phase_a.add_argument("--output", type=Path, required=True)

    phase_b = subparsers.add_parser("phase-b")
    phase_b.add_argument("--reference-manifest", type=Path, required=True)
    phase_b.add_argument("--phase-a-commitment", type=Path, required=True)
    phase_b.add_argument("--candidate-manifest", type=Path, required=True)
    phase_b.add_argument("--output", type=Path)

    candidate_parser = subparsers.add_parser("build-review-only-candidate")
    candidate_parser.add_argument("--scope-manifest", type=Path, required=True)
    candidate_parser.add_argument("--run-manifest", type=Path, required=True)
    candidate_parser.add_argument("--private-input", type=Path, required=True)
    candidate_parser.add_argument("--phase-a-commitment", type=Path, required=True)
    candidate_parser.add_argument("--output", type=Path, required=True)

    sidecar = subparsers.add_parser("mixed-provenance-sidecar")
    sidecar.add_argument("--reference-manifest", type=Path, required=True)
    sidecar.add_argument("--phase-a-commitment", type=Path, required=True)
    sidecar.add_argument("--candidate-manifest", type=Path, required=True)
    sidecar.add_argument("--phase-b-result", type=Path, required=True)
    sidecar.add_argument("--user-origin-label-count", type=int, required=True)
    sidecar.add_argument("--codex-assisted-pending-fill-count", type=int, required=True)
    sidecar.add_argument("--codex-assisted-correction-count", type=int, required=True)
    sidecar.add_argument("--user-final-acceptance-claim-count", type=int, required=True)
    sidecar.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.phase == "phase-a":
            reference_bytes, reference_payload = _read_object(args.reference_manifest)
            reference = AtomicClaimBlindReviewManifest.model_validate(reference_payload)
            commitment = build_phase_a_commitment(reference_bytes, reference)
            rendered = commitment.model_dump_json(indent=2)
            _write_safe_output(args.output, rendered)
            print(rendered)
            return 0

        if args.phase == "build-review-only-candidate":
            candidate_result = build_review_only_candidate(
                scope_manifest_path=args.scope_manifest,
                run_manifest_path=args.run_manifest,
                private_input_path=args.private_input,
                phase_a_commitment_path=args.phase_a_commitment,
            )
            rendered = candidate_result.model_dump_json(indent=2)
            _write_safe_output(args.output, rendered)
            print(rendered)
            return 0

        if args.phase == "mixed-provenance-sidecar":
            reference_bytes, reference_payload = _read_object(args.reference_manifest)
            commitment_bytes, commitment_payload = _read_object(args.phase_a_commitment)
            candidate_bytes, candidate_payload = _read_object(args.candidate_manifest)
            phase_b_bytes, phase_b_payload = _read_object(args.phase_b_result)
            reference = AtomicClaimBlindReviewManifest.model_validate(reference_payload)
            commitment = AtomicClaimBlindReviewCommitment.model_validate(commitment_payload)
            candidate_manifest = AtomicClaimBlindCandidateManifest.model_validate(candidate_payload)
            phase_b_result = AtomicClaimBlindPhaseBResult.model_validate(phase_b_payload)
            audit = build_mixed_provenance_audit_sidecar(
                reference_manifest_bytes=reference_bytes,
                reference=reference,
                commitment_bytes=commitment_bytes,
                commitment=commitment,
                candidate_manifest_bytes=candidate_bytes,
                candidate=candidate_manifest,
                phase_b_result_bytes=phase_b_bytes,
                phase_b_result=phase_b_result,
                user_origin_label_count=args.user_origin_label_count,
                codex_assisted_pending_fill_count=(args.codex_assisted_pending_fill_count),
                codex_assisted_correction_count=args.codex_assisted_correction_count,
                user_final_acceptance_claim_count=(args.user_final_acceptance_claim_count),
            )
            rendered = audit.model_dump_json(indent=2)
            _write_safe_output(args.output, rendered)
            print(rendered)
            return 0

        reference_bytes, reference_payload = _read_object(args.reference_manifest)
        commitment_bytes, commitment_payload = _read_object(args.phase_a_commitment)
        candidate_bytes, candidate_payload = _read_object(args.candidate_manifest)
        reference = AtomicClaimBlindReviewManifest.model_validate(reference_payload)
        commitment = AtomicClaimBlindReviewCommitment.model_validate(commitment_payload)
        candidate = AtomicClaimBlindCandidateManifest.model_validate(candidate_payload)
        result = evaluate_phase_b(
            reference_manifest_bytes=reference_bytes,
            reference=reference,
            commitment_bytes=commitment_bytes,
            commitment=commitment,
            candidate_manifest_bytes=candidate_bytes,
            candidate=candidate,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _blocked("atomic_claim_blind_input_unreadable")
    except ValidationError:
        return _blocked("atomic_claim_blind_input_schema_invalid")
    except ValueError:
        return _blocked("atomic_claim_blind_input_not_object")
    except (
        EvaluationAtomicClaimCalibrationError,
        EvaluationAtomicClaimReviewOnlyPhaseBError,
    ) as exc:
        return _blocked(str(exc))

    rendered = result.model_dump_json(indent=2)
    if args.output is not None:
        _write_safe_output(args.output, rendered)
    print(rendered)
    return 0 if result.candidate_selected else 2


if __name__ == "__main__":
    raise SystemExit(main())
