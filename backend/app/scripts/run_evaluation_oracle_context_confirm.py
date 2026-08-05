from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.evaluation_oracle_context_confirm_service import (
    EvaluationOracleContextConfirmError,
    EvaluationOracleContextConfirmService,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run three frozen baseline Oracle Context repeats and aggregate a raw-free "
            "retrieval/generation/evaluator diagnostic."
        )
    )
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--expected-case-count", type=int, default=40)
    parser.add_argument("--screening-artifact", type=Path, required=True)
    parser.add_argument("--manual-review", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-local-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "production" or not args.confirm_local_only:
        raise SystemExit("oracle_context_confirm_local_confirmation_required")

    try:
        screening = _read_object(args.screening_artifact)
        replay = screening.get("safe_judge_replay")
        if not isinstance(replay, dict):
            raise ValueError
        manual_review = _read_object(args.manual_review) if args.manual_review is not None else None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print(
            json.dumps(
                {"status": "blocked", "reason_code": "oracle_confirm_input_unreadable"},
                sort_keys=True,
            )
        )
        return 2

    try:
        with SessionLocal() as db:
            summary = EvaluationOracleContextConfirmService(settings).run(
                db,
                evaluation_run_id=args.run_id,
                expected_case_count=args.expected_case_count,
                r_judge_replay=replay,
                manual_review=manual_review,
            )
    except EvaluationOracleContextConfirmError as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True))
        return 2

    rendered = json.dumps(summary.safe_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if summary.gate_passed else 2


def _read_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
