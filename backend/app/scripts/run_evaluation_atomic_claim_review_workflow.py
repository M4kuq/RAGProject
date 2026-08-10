from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.evaluation_atomic_claim_contracts import (
    print_blocked as _blocked,
)
from app.services.evaluation_atomic_claim_contracts import (
    write_model_json,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    EvaluationAtomicClaimReviewWorkflowError,
    create_review_server,
    create_review_session,
    prepare_review_scope_from_paths,
    validate_review_input,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and run the candidate-blind local RAG-79 per-claim review."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-scope")
    prepare.add_argument("--source-contract", type=Path, required=True)
    prepare.add_argument("--run112-summary", type=Path, required=True)
    prepare.add_argument("--legacy-review-manifest", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate-input")
    validate.add_argument("--scope-manifest", type=Path, required=True)
    validate.add_argument("--private-input", type=Path, required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--scope-manifest", type=Path, required=True)
    serve.add_argument("--private-input", type=Path, required=True)
    serve.add_argument("--output-dir", type=Path, required=True)
    serve.add_argument("--reviewer-provenance", required=True)
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--confirm-local-only", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "prepare-scope":
            scope = prepare_review_scope_from_paths(
                args.source_contract,
                args.run112_summary,
                args.legacy_review_manifest,
            )
            write_model_json(args.output, scope)
            print(
                json.dumps(
                    {
                        "status": "scope_ready",
                        "schema_version": scope.schema_version,
                        "target_observation_count": scope.target_observation_count,
                        "target_case_count": scope.target_case_count,
                        "target_scope_fingerprint": scope.target_scope_fingerprint,
                        "candidate_results_present": False,
                        "candidate_identifiers_present": False,
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "validate-input":
            print(
                json.dumps(
                    validate_review_input(
                        args.scope_manifest,
                        args.private_input,
                    ),
                    sort_keys=True,
                )
            )
            return 0

        if not args.confirm_local_only:
            raise EvaluationAtomicClaimReviewWorkflowError(
                "atomic_claim_review_local_confirmation_required"
            )
        session = create_review_session(
            scope_manifest_path=args.scope_manifest,
            private_input_path=args.private_input,
            output_dir=args.output_dir,
            reviewer_provenance=args.reviewer_provenance,
        )
        server = create_review_server(session, port=args.port)
        print(
            json.dumps(
                {
                    "status": "review_server_ready",
                    "url": server.local_url,
                    "bind_host": "127.0.0.1",
                    "external_calls_allowed": False,
                    "raw_content_persisted": False,
                },
                sort_keys=True,
            )
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except EvaluationAtomicClaimReviewWorkflowError as exc:
        return _blocked(str(exc))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _blocked("atomic_claim_review_input_unreadable")


if __name__ == "__main__":
    raise SystemExit(main())
