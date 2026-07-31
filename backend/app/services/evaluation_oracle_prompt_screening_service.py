from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from typing import Literal, cast

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.evaluation.generation_prompt_profiles import (
    GenerationPromptProfileName,
    generation_prompt_profile_names,
)
from app.services.evaluation_judge_replay_service import (
    EvaluationJudgeReplayError,
    EvaluationJudgeReplayService,
)
from app.services.evaluation_oracle_context_service import (
    EvaluationOracleContextError,
    EvaluationOracleContextService,
    OracleContextCaseOutcome,
    OracleContextDiagnosticSummary,
)


class EvaluationOraclePromptScreeningError(RuntimeError):
    pass


@dataclass(frozen=True)
class OraclePromptProfileResult:
    profile: GenerationPromptProfileName
    prompt_fingerprint: str
    auxiliary_pass_count: int
    auxiliary_pass_rate: float
    answerable_pass_count: int
    unanswerable_pass_count: int
    citation_support_pass_count: int
    required_facts_supported_pass_count: int
    prompt_injection_composite_pass_count: int
    prompt_injection_resisted_pass_count: int
    pipeline_failure_count: int
    answerable_failure_case_ids: tuple[str, ...]
    changed_case_ids_from_baseline: tuple[str, ...]
    safe_oracle_summary: dict[str, object]


@dataclass(frozen=True)
class OraclePromptScreeningSummary:
    schema_version: Literal["phase3.oracle_prompt_screening.v1"]
    source_evaluation_run_id: int
    dataset_name: str
    dataset_content_fingerprint: str
    corpus_fingerprint: str
    case_set_fingerprint: str
    generation_config_fingerprint: str
    resolved_generation_model: str
    generation_temperature: float
    generation_max_context_chars: int
    generation_max_output_chars: int
    generation_max_output_tokens: int | None
    expected_case_count: int
    replay_gate_passed: bool
    comparability_status: Literal["comparable"]
    baseline_profile: GenerationPromptProfileName
    selected_profile: GenerationPromptProfileName
    selected_for_a3_confirmation: GenerationPromptProfileName | None
    candidate_improves_baseline: bool
    candidate_guardrails_non_worse: bool
    screening_gate_passed: bool
    safe_judge_replay: dict[str, object]
    profiles: tuple[OraclePromptProfileResult, ...]

    def safe_dict(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


class EvaluationOraclePromptScreeningService:
    def __init__(
        self,
        settings: Settings,
        *,
        replay_service_factory: Callable[
            [Settings], EvaluationJudgeReplayService
        ] = EvaluationJudgeReplayService,
        oracle_service_factory: Callable[..., EvaluationOracleContextService] = (
            EvaluationOracleContextService
        ),
    ) -> None:
        self.settings = settings
        self.replay_service_factory = replay_service_factory
        self.oracle_service_factory = oracle_service_factory

    def run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        expected_case_count: int = 40,
        replay_count: int = 3,
    ) -> OraclePromptScreeningSummary:
        try:
            replay = self.replay_service_factory(self.settings).replay(
                db,
                evaluation_run_id=evaluation_run_id,
                repeats=replay_count,
                expected_case_count=expected_case_count,
            )
        except EvaluationJudgeReplayError as exc:
            raise EvaluationOraclePromptScreeningError(f"oracle_screening_{exc}") from exc
        if not replay.gate_passed:
            raise EvaluationOraclePromptScreeningError("oracle_screening_judge_replay_gate_failed")

        replay_payload = cast(
            dict[str, object],
            json.loads(json.dumps(replay.safe_dict())),
        )
        oracle_summaries: list[OracleContextDiagnosticSummary] = []
        for profile in generation_prompt_profile_names():
            try:
                summary = self.oracle_service_factory(
                    self.settings,
                    generation_prompt_profile=profile,
                ).run(
                    db,
                    evaluation_run_id=evaluation_run_id,
                    expected_case_count=expected_case_count,
                    r_judge_replay=replay_payload,
                )
            except EvaluationOracleContextError as exc:
                raise EvaluationOraclePromptScreeningError(
                    f"oracle_screening_{profile}_{exc}"
                ) from exc
            oracle_summaries.append(summary)

        _validate_comparability(
            tuple(oracle_summaries),
            evaluation_run_id=evaluation_run_id,
            expected_case_count=expected_case_count,
        )
        baseline_summary = oracle_summaries[0]
        profile_results = tuple(
            _profile_result(
                summary,
                baseline=baseline_summary,
            )
            for summary in oracle_summaries
        )
        selected = _select_profile(profile_results)
        baseline = profile_results[0]
        candidate_improves_baseline = (
            selected.profile != baseline.profile
            and selected.auxiliary_pass_count > baseline.auxiliary_pass_count
        )
        guardrails_non_worse = _guardrails_non_worse(
            candidate=selected,
            baseline=baseline,
        )
        selected_for_confirmation = (
            selected.profile if candidate_improves_baseline and guardrails_non_worse else None
        )
        return OraclePromptScreeningSummary(
            schema_version="phase3.oracle_prompt_screening.v1",
            source_evaluation_run_id=evaluation_run_id,
            dataset_name=baseline_summary.dataset_name,
            dataset_content_fingerprint=baseline_summary.dataset_content_fingerprint,
            corpus_fingerprint=baseline_summary.corpus_fingerprint,
            case_set_fingerprint=baseline_summary.case_set_fingerprint,
            generation_config_fingerprint=baseline_summary.generation_config_fingerprint,
            resolved_generation_model=baseline_summary.resolved_generation_model,
            generation_temperature=baseline_summary.generation_temperature,
            generation_max_context_chars=baseline_summary.generation_max_context_chars,
            generation_max_output_chars=baseline_summary.generation_max_output_chars,
            generation_max_output_tokens=baseline_summary.generation_max_output_tokens,
            expected_case_count=expected_case_count,
            replay_gate_passed=True,
            comparability_status="comparable",
            baseline_profile=baseline.profile,
            selected_profile=selected.profile,
            selected_for_a3_confirmation=selected_for_confirmation,
            candidate_improves_baseline=candidate_improves_baseline,
            candidate_guardrails_non_worse=guardrails_non_worse,
            screening_gate_passed=all(
                profile.pipeline_failure_count == 0 for profile in profile_results
            ),
            safe_judge_replay=replay_payload,
            profiles=profile_results,
        )


def _validate_comparability(
    summaries: tuple[OracleContextDiagnosticSummary, ...],
    *,
    evaluation_run_id: int,
    expected_case_count: int,
) -> None:
    if len(summaries) != len(generation_prompt_profile_names()):
        raise EvaluationOraclePromptScreeningError("oracle_screening_profile_count_mismatch")
    baseline = summaries[0]
    comparable_fields = (
        "source_evaluation_run_id",
        "dataset_name",
        "dataset_content_fingerprint",
        "corpus_fingerprint",
        "case_set_fingerprint",
        "generation_config_fingerprint",
        "resolved_generation_model",
        "generation_temperature",
        "generation_max_context_chars",
        "generation_max_output_chars",
        "generation_max_output_tokens",
        "expected_case_count",
        "comparable_case_count",
        "r_judge_replay_fingerprint",
        "r_judge_replay_count",
    )
    expected_profiles = generation_prompt_profile_names()
    if (
        baseline.source_evaluation_run_id != evaluation_run_id
        or baseline.expected_case_count != expected_case_count
        or baseline.comparable_case_count != expected_case_count
    ):
        raise EvaluationOraclePromptScreeningError("oracle_screening_source_not_comparable")
    for index, summary in enumerate(summaries):
        if (
            summary.generation_prompt_profile != expected_profiles[index]
            or not summary.gate_passed
            or any(
                getattr(summary, field) != getattr(baseline, field) for field in comparable_fields
            )
        ):
            raise EvaluationOraclePromptScreeningError("oracle_screening_profile_not_comparable")
    if len({summary.generation_prompt_fingerprint for summary in summaries}) != len(summaries):
        raise EvaluationOraclePromptScreeningError(
            "oracle_screening_prompt_fingerprint_not_distinct"
        )


def _profile_result(
    summary: OracleContextDiagnosticSummary,
    *,
    baseline: OracleContextDiagnosticSummary,
) -> OraclePromptProfileResult:
    baseline_by_case = {outcome.case_id: outcome for outcome in baseline.cases}
    changed_case_ids = tuple(
        outcome.case_id
        for outcome in summary.cases
        if outcome.o_auxiliary_pass != baseline_by_case[outcome.case_id].o_auxiliary_pass
    )
    injection_cases = tuple(
        outcome for outcome in summary.cases if "prompt_injection" in outcome.tags
    )
    return OraclePromptProfileResult(
        profile=cast(GenerationPromptProfileName, summary.generation_prompt_profile),
        prompt_fingerprint=summary.generation_prompt_fingerprint,
        auxiliary_pass_count=summary.o_auxiliary_pass_count,
        auxiliary_pass_rate=summary.o_auxiliary_pass_rate,
        answerable_pass_count=_pass_count(
            outcome for outcome in summary.cases if outcome.answerable
        ),
        unanswerable_pass_count=_pass_count(
            outcome for outcome in summary.cases if not outcome.answerable
        ),
        citation_support_pass_count=sum(
            outcome.o_citation_support == "pass" for outcome in summary.cases
        ),
        required_facts_supported_pass_count=sum(
            outcome.o_required_facts_supported == "pass" for outcome in summary.cases
        ),
        prompt_injection_composite_pass_count=_pass_count(injection_cases),
        prompt_injection_resisted_pass_count=sum(
            outcome.o_prompt_injection_resisted == "pass" for outcome in injection_cases
        ),
        pipeline_failure_count=summary.pipeline_failure_count,
        answerable_failure_case_ids=tuple(
            outcome.case_id
            for outcome in summary.cases
            if outcome.answerable and outcome.o_auxiliary_pass is False
        ),
        changed_case_ids_from_baseline=changed_case_ids,
        safe_oracle_summary=summary.safe_dict(),
    )


def _select_profile(
    profiles: tuple[OraclePromptProfileResult, ...],
) -> OraclePromptProfileResult:
    return max(
        profiles,
        key=lambda profile: (
            profile.auxiliary_pass_count,
            profile.citation_support_pass_count,
            profile.required_facts_supported_pass_count,
            -generation_prompt_profile_names().index(profile.profile),
        ),
    )


def _guardrails_non_worse(
    *,
    candidate: OraclePromptProfileResult,
    baseline: OraclePromptProfileResult,
) -> bool:
    return (
        candidate.answerable_pass_count >= baseline.answerable_pass_count
        and candidate.unanswerable_pass_count >= baseline.unanswerable_pass_count
        and candidate.citation_support_pass_count >= baseline.citation_support_pass_count
        and candidate.required_facts_supported_pass_count
        >= baseline.required_facts_supported_pass_count
        and candidate.prompt_injection_composite_pass_count
        >= baseline.prompt_injection_composite_pass_count
        and candidate.prompt_injection_resisted_pass_count
        >= baseline.prompt_injection_resisted_pass_count
        and candidate.pipeline_failure_count == 0
    )


def _pass_count(outcomes: Iterable[OracleContextCaseOutcome]) -> int:
    return sum(outcome.o_auxiliary_pass is True for outcome in outcomes)
