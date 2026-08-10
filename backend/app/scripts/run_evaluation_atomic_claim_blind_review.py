from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from app.services.evaluation_atomic_claim_contracts import (
    print_blocked as _blocked,
    read_json_object as _read_object,
    write_raw_free_text as _write_safe_output,
)

from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindCandidateManifest,
    AtomicClaimBlindReviewCommitment,
    AtomicClaimBlindReviewManifest,
    EvaluationAtomicClaimCalibrationError,
    build_phase_a_commitment,
    evaluate_phase_b,
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
    except EvaluationAtomicClaimCalibrationError as exc:
        return _blocked(str(exc))

    rendered = result.model_dump_json(indent=2)
    if args.output is not None:
        _write_safe_output(args.output, rendered)
    print(rendered)
    return 0 if result.candidate_selected else 2



if __name__ == "__main__":
    raise SystemExit(main())
