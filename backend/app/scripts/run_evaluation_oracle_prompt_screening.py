from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.evaluation_oracle_prompt_screening_service import (
    EvaluationOraclePromptScreeningError,
    EvaluationOraclePromptScreeningService,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen local Judge replay and Oracle prompt screening."
    )
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--expected-case-count", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-local-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "production" or not args.confirm_local_only:
        raise SystemExit("oracle_prompt_screening_local_confirmation_required")

    try:
        with SessionLocal() as db:
            summary = EvaluationOraclePromptScreeningService(settings).run(
                db,
                evaluation_run_id=args.run_id,
                expected_case_count=args.expected_case_count,
                replay_count=args.repeats,
            )
    except EvaluationOraclePromptScreeningError as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True))
        return 2

    rendered = json.dumps(
        summary.safe_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if summary.screening_gate_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
