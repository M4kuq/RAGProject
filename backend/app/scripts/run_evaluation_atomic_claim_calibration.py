from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from app.services.evaluation_atomic_claim_calibration_service import (
    AtomicClaimCandidateManifest,
    EvaluationAtomicClaimCalibrationError,
    evaluate_atomic_claim_candidate,
    inspect_reference_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a raw-free, hash-bound per-claim reference and evaluate one additive "
            "semantic-equivalence screening coordinate."
        )
    )
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--reference-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        candidate_bytes, candidate_payload = _read_object(args.candidate_manifest)
        candidate = AtomicClaimCandidateManifest.model_validate(candidate_payload)
        reference = None
        reference_schema = None
        reference_provenance = None
        requires_human_signoff = True
        reference_hash = None
        if args.reference_manifest is not None:
            reference_bytes, reference_payload = _read_object(args.reference_manifest)
            reference_hash = hashlib.sha256(reference_bytes).hexdigest()
            (
                reference,
                reference_schema,
                reference_provenance,
                requires_human_signoff,
            ) = inspect_reference_payload(reference_payload)
        summary = evaluate_atomic_claim_candidate(
            candidate,
            reference=reference,
            candidate_manifest_sha256=hashlib.sha256(candidate_bytes).hexdigest(),
            reference_manifest_sha256=reference_hash,
            reference_schema_version=reference_schema,
            reference_provenance=reference_provenance,
            requires_human_signoff=requires_human_signoff,
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _blocked("atomic_claim_input_unreadable")
    except ValidationError:
        return _blocked("atomic_claim_input_schema_invalid")
    except EvaluationAtomicClaimCalibrationError as exc:
        return _blocked(str(exc))

    rendered = summary.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if summary.candidate_selected else 2


def _read_object(path: Path) -> tuple[bytes, dict[str, object]]:
    payload_bytes = path.read_bytes()
    payload = json.loads(payload_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError
    return payload_bytes, payload


def _blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
