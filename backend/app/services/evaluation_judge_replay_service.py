from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.evaluation_models import EvaluationReviewPayload
from app.db.models import EvaluationCase, EvaluationRun, EvaluationRunItem
from app.services.evaluation_judge_service import (
    EvaluationClaimJudgeError,
    EvaluationClaimJudgeService,
    EvaluationJudgeResult,
)

_EXPECTED_JUDGE_MODEL = "qwen/qwen3.5-9b"


class EvaluationJudgeReplayError(RuntimeError):
    pass


@dataclass(frozen=True)
class JudgeReplayCaseOutcome:
    repeat: int
    evaluation_run_item_id: int
    case_id: str
    status: Literal["succeeded", "failed"]
    auxiliary_pass: bool | None
    attempt_count: int
    first_failure_code: str | None
    terminal_reason_code: str
    recovered_after_retry: bool
    answer_hash: str
    context_hash: str
    decision_fingerprint: str | None


@dataclass(frozen=True)
class JudgeReplayRepeatSummary:
    repeat: int
    applicable_count: int
    judged_count: int
    judge_failure_count: int
    recovered_count: int
    first_attempt_success_count: int
    outcomes: tuple[JudgeReplayCaseOutcome, ...]


@dataclass(frozen=True)
class JudgeReplaySummary:
    schema_version: Literal["phase3.judge_replay.v1"]
    source_evaluation_run_id: int
    dataset_name: str
    dataset_content_fingerprint: str | None
    corpus_fingerprint: str
    resolved_generation_model: str
    judge_provider: str
    judge_model: str
    expected_case_count: int
    repeats: int
    answer_set_fingerprint: str
    repeat_summaries: tuple[JudgeReplayRepeatSummary, ...]
    stable_case_count: int
    unstable_case_ids: tuple[str, ...]
    gate_passed: bool

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


@dataclass(frozen=True)
class _ReplayInput:
    item: EvaluationRunItem
    case: EvaluationCase
    payload: EvaluationReviewPayload
    answerable: bool
    tags: tuple[str, ...]
    required_facts: tuple[dict[str, object], ...]
    forbidden_claims: tuple[str, ...]
    citations: tuple[dict[str, object], ...]
    context: tuple[str, ...]


class EvaluationJudgeReplayService:
    def __init__(
        self,
        settings: Settings,
        *,
        judge_factory: Callable[[Settings], EvaluationClaimJudgeService] | None = None,
    ) -> None:
        self.settings = settings
        self.judge_factory = judge_factory or EvaluationClaimJudgeService

    def replay(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        repeats: int,
        expected_case_count: int,
    ) -> JudgeReplaySummary:
        if repeats < 1 or repeats > 10:
            raise EvaluationJudgeReplayError("judge_replay_repeats_invalid")
        if expected_case_count < 1:
            raise EvaluationJudgeReplayError("judge_replay_expected_count_invalid")

        run = db.get(EvaluationRun, evaluation_run_id)
        if run is None:
            raise EvaluationJudgeReplayError("judge_replay_run_not_found")
        config = run.metrics_config if isinstance(run.metrics_config, dict) else {}
        retrieval = (
            run.retrieval_settings_json if isinstance(run.retrieval_settings_json, dict) else {}
        )
        dataset_name = config.get("dataset_name")
        if dataset_name != "local_accuracy_dev_v1":
            raise EvaluationJudgeReplayError("judge_replay_dataset_not_allowed")
        if config.get("evaluation_scope") != "end_to_end":
            raise EvaluationJudgeReplayError("judge_replay_scope_not_allowed")
        if run.trigger_type != "manual" or run.status != "succeeded":
            raise EvaluationJudgeReplayError("judge_replay_run_not_terminal_manual")
        resolved_model = retrieval.get("resolved_generation_model")
        if resolved_model != _EXPECTED_JUDGE_MODEL:
            raise EvaluationJudgeReplayError("judge_replay_model_mismatch")
        corpus_fingerprint = run.corpus_fingerprint
        if not isinstance(corpus_fingerprint, str) or not corpus_fingerprint:
            raise EvaluationJudgeReplayError("judge_replay_corpus_fingerprint_missing")

        replay_inputs = self._load_inputs(
            db,
            run=run,
            expected_case_count=expected_case_count,
        )
        judge = self.judge_factory(self.settings)
        repeat_summaries = tuple(
            self._run_repeat(judge, replay_inputs, repeat=repeat)
            for repeat in range(1, repeats + 1)
        )
        outcomes_by_case: dict[str, set[str | None]] = {}
        for repeat_summary in repeat_summaries:
            for outcome in repeat_summary.outcomes:
                outcomes_by_case.setdefault(outcome.case_id, set()).add(
                    outcome.decision_fingerprint
                )
        unstable_case_ids = tuple(
            case_id
            for case_id, fingerprints in sorted(outcomes_by_case.items())
            if len(fingerprints) != 1 or None in fingerprints
        )
        stable_case_count = expected_case_count - len(unstable_case_ids)
        full_coverage = all(
            summary.applicable_count == expected_case_count
            and summary.judged_count == expected_case_count
            and summary.judge_failure_count == 0
            for summary in repeat_summaries
        )
        return JudgeReplaySummary(
            schema_version="phase3.judge_replay.v1",
            source_evaluation_run_id=run.evaluation_run_id,
            dataset_name=dataset_name,
            dataset_content_fingerprint=_optional_hash(
                retrieval.get("dataset_content_fingerprint")
            ),
            corpus_fingerprint=corpus_fingerprint,
            resolved_generation_model=resolved_model,
            judge_provider=judge.provider,
            judge_model=judge.model,
            expected_case_count=expected_case_count,
            repeats=repeats,
            answer_set_fingerprint=_answer_set_fingerprint(replay_inputs),
            repeat_summaries=repeat_summaries,
            stable_case_count=stable_case_count,
            unstable_case_ids=unstable_case_ids,
            gate_passed=full_coverage and not unstable_case_ids,
        )

    def _load_inputs(
        self,
        db: Session,
        *,
        run: EvaluationRun,
        expected_case_count: int,
    ) -> tuple[_ReplayInput, ...]:
        items = tuple(
            db.scalars(
                select(EvaluationRunItem)
                .where(EvaluationRunItem.evaluation_run_id == run.evaluation_run_id)
                .order_by(EvaluationRunItem.evaluation_run_item_id.asc())
            ).all()
        )
        if len(items) != expected_case_count:
            raise EvaluationJudgeReplayError("judge_replay_case_count_mismatch")
        payloads = {
            payload.evaluation_run_item_id: payload
            for payload in db.scalars(
                select(EvaluationReviewPayload).where(
                    EvaluationReviewPayload.evaluation_run_item_id.in_(
                        [item.evaluation_run_item_id for item in items]
                    )
                )
            ).all()
        }
        now = datetime.now(UTC)
        replay_inputs: list[_ReplayInput] = []
        for item in items:
            if item.evaluation_case_id is None:
                raise EvaluationJudgeReplayError("judge_replay_case_reference_missing")
            case = db.get(EvaluationCase, item.evaluation_case_id)
            payload = payloads.get(item.evaluation_run_item_id)
            if case is None or payload is None:
                raise EvaluationJudgeReplayError("judge_replay_payload_missing")
            if (
                payload.purged_at is not None
                or payload.expires_at <= now
                or payload.answer_text is None
                or payload.context_json is None
                or payload.citations_json is None
                or payload.required_facts_json is None
            ):
                raise EvaluationJudgeReplayError("judge_replay_payload_unavailable")
            if item.answer_outcome not in {"answered", "abstained"}:
                raise EvaluationJudgeReplayError("judge_replay_answer_outcome_invalid")
            context = _string_tuple(payload.context_json)
            citations = _dict_tuple(payload.citations_json)
            required_facts = _dict_tuple(payload.required_facts_json)
            current_context_hash = _sha256("\x00".join(context))
            legacy_context_hash = _sha256("\\x00".join(context))
            if _sha256(payload.answer_text) != payload.answer_hash or payload.context_hash not in {
                current_context_hash,
                legacy_context_hash,
            }:
                raise EvaluationJudgeReplayError("judge_replay_payload_hash_mismatch")
            metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
            answerable = metadata.get("answerable")
            if not isinstance(answerable, bool):
                raise EvaluationJudgeReplayError("judge_replay_answerability_missing")
            replay_inputs.append(
                _ReplayInput(
                    item=item,
                    case=case,
                    payload=payload,
                    answerable=answerable,
                    tags=_string_tuple(case.tags),
                    required_facts=required_facts,
                    forbidden_claims=_string_tuple(metadata.get("forbidden_claims")),
                    citations=citations,
                    context=context,
                )
            )
        return tuple(replay_inputs)

    def _run_repeat(
        self,
        judge: EvaluationClaimJudgeService,
        replay_inputs: tuple[_ReplayInput, ...],
        *,
        repeat: int,
    ) -> JudgeReplayRepeatSummary:
        outcomes: list[JudgeReplayCaseOutcome] = []
        for replay_input in replay_inputs:
            try:
                result = judge.judge(
                    case_id=replay_input.case.case_key,
                    answerable=replay_input.answerable,
                    required_citation=replay_input.case.required_citation,
                    tags=list(replay_input.tags),
                    answer_outcome=cast(
                        Literal["answered", "abstained"],
                        replay_input.item.answer_outcome,
                    ),
                    answer_text=cast(str, replay_input.payload.answer_text),
                    citations=list(replay_input.citations),
                    context=list(replay_input.context),
                    required_facts=list(replay_input.required_facts),
                    forbidden_claims=list(replay_input.forbidden_claims),
                )
                outcomes.append(
                    JudgeReplayCaseOutcome(
                        repeat=repeat,
                        evaluation_run_item_id=(replay_input.item.evaluation_run_item_id),
                        case_id=replay_input.case.case_key,
                        status="succeeded",
                        auxiliary_pass=result.auxiliary_pass,
                        attempt_count=result.attempt_count,
                        first_failure_code=result.first_failure_code,
                        terminal_reason_code=result.terminal_reason_code,
                        recovered_after_retry=result.recovered_after_retry,
                        answer_hash=result.answer_hash,
                        context_hash=result.context_hash,
                        decision_fingerprint=_decision_fingerprint(result),
                    )
                )
            except EvaluationClaimJudgeError as exc:
                outcomes.append(
                    JudgeReplayCaseOutcome(
                        repeat=repeat,
                        evaluation_run_item_id=(replay_input.item.evaluation_run_item_id),
                        case_id=replay_input.case.case_key,
                        status="failed",
                        auxiliary_pass=None,
                        attempt_count=exc.attempt_count,
                        first_failure_code=exc.first_failure_code,
                        terminal_reason_code=exc.terminal_reason_code,
                        recovered_after_retry=False,
                        answer_hash=replay_input.payload.answer_hash,
                        context_hash=replay_input.payload.context_hash,
                        decision_fingerprint=None,
                    )
                )
        judged_count = sum(outcome.status == "succeeded" for outcome in outcomes)
        return JudgeReplayRepeatSummary(
            repeat=repeat,
            applicable_count=len(outcomes),
            judged_count=judged_count,
            judge_failure_count=len(outcomes) - judged_count,
            recovered_count=sum(outcome.recovered_after_retry for outcome in outcomes),
            first_attempt_success_count=sum(
                outcome.status == "succeeded" and outcome.attempt_count == 1 for outcome in outcomes
            ),
            outcomes=tuple(outcomes),
        )


def _decision_fingerprint(result: EvaluationJudgeResult) -> str:
    decision = result.decision
    payload = {
        "required_facts_supported": decision.required_facts_supported.value,
        "citation_support": decision.citation_support.value,
        "forbidden_claims_absent": decision.forbidden_claims_absent.value,
        "abstention_correct": decision.abstention_correct.value,
        "prompt_injection_resisted": decision.prompt_injection_resisted.value,
        "reason_codes": sorted(code.value for code in decision.reason_codes),
        "auxiliary_pass": result.auxiliary_pass,
    }
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _answer_set_fingerprint(replay_inputs: tuple[_ReplayInput, ...]) -> str:
    payload = [
        {
            "case_id": replay_input.case.case_key,
            "answer_hash": replay_input.payload.answer_hash,
            "context_hash": replay_input.payload.context_hash,
        }
        for replay_input in replay_inputs
    ]
    return _sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _optional_hash(value: object) -> str | None:
    return value if isinstance(value, str) and len(value) == 64 else None


def _dict_tuple(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise EvaluationJudgeReplayError("judge_replay_payload_shape_invalid")
    return tuple(cast(dict[str, object], item) for item in value)


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise EvaluationJudgeReplayError("judge_replay_payload_shape_invalid")
    return tuple(cast(str, item) for item in value)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
