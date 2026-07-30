from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.evaluation_judge_replay_service import (
    EvaluationJudgeReplayError,
    EvaluationJudgeReplayService,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay the local auxiliary Judge without regenerating answers."
    )
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--expected-case-count", type=int, default=40)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-local-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "production" or not args.confirm_local_only:
        raise SystemExit("judge_replay_local_confirmation_required")

    try:
        with SessionLocal() as db:
            summary = EvaluationJudgeReplayService(settings).replay(
                db,
                evaluation_run_id=args.run_id,
                repeats=args.repeats,
                expected_case_count=args.expected_case_count,
            )
    except EvaluationJudgeReplayError as exc:
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
    return 0 if summary.gate_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
