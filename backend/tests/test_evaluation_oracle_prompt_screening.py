from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.services.evaluation_oracle_prompt_screening_service import (
    EvaluationOraclePromptScreeningService,
)


class _FakeReplay:
    gate_passed = True

    def safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": "phase3.judge_replay.v1",
            "source_evaluation_run_id": 112,
            "gate_passed": True,
        }


class _FakeReplayService:
    calls: list[tuple[int, int, int]] = []

    def __init__(self, settings: Settings) -> None:
        del settings

    def replay(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        repeats: int,
        expected_case_count: int,
    ) -> _FakeReplay:
        del db
        self.calls.append((evaluation_run_id, repeats, expected_case_count))
        return _FakeReplay()


class _FakeOracleService:
    calls: list[str] = []

    def __init__(
        self,
        settings: Settings,
        *,
        generation_prompt_profile: str,
    ) -> None:
        del settings
        self.profile = generation_prompt_profile

    def run(
        self,
        db: Session,
        *,
        evaluation_run_id: int,
        expected_case_count: int,
        r_judge_replay: dict[str, object],
    ) -> SimpleNamespace:
        del db
        assert evaluation_run_id == 112
        assert expected_case_count == 4
        assert r_judge_replay["gate_passed"] is True
        self.calls.append(self.profile)
        passes = {
            "baseline": (True, False, True, False),
            "multi_fact_coverage_v1": (True, True, True, False),
            "multi_fact_coverage_instruction_guard_v1": (True, True, True, False),
        }[self.profile]
        cases = (
            _case("answerable_01", answerable=True, passed=passes[0]),
            _case(
                "answerable_injection_02",
                answerable=True,
                passed=passes[1],
                prompt_injection=True,
                injection_resisted=self.profile == "multi_fact_coverage_instruction_guard_v1",
            ),
            _case("unanswerable_03", answerable=False, passed=passes[2]),
            _case("answerable_04", answerable=True, passed=passes[3]),
        )
        summary = SimpleNamespace(
            source_evaluation_run_id=112,
            dataset_name="local_accuracy_dev_v1",
            dataset_content_fingerprint="a" * 64,
            corpus_fingerprint="b" * 64,
            case_set_fingerprint="c" * 64,
            generation_config_fingerprint="d" * 64,
            r_judge_replay_fingerprint="e" * 64,
            r_judge_replay_count=3,
            resolved_generation_model="qwen/qwen3.5-9b",
            generation_prompt_profile=self.profile,
            generation_prompt_fingerprint={
                "baseline": "1" * 64,
                "multi_fact_coverage_v1": "2" * 64,
                "multi_fact_coverage_instruction_guard_v1": "3" * 64,
            }[self.profile],
            generation_temperature=0.0,
            generation_max_context_chars=12_000,
            generation_max_output_chars=12_000,
            generation_max_output_tokens=8192,
            expected_case_count=4,
            comparable_case_count=4,
            o_auxiliary_pass_count=sum(passes),
            o_auxiliary_pass_rate=sum(passes) / 4,
            pipeline_failure_count=0,
            gate_passed=True,
            cases=cases,
        )
        summary.safe_dict = lambda: {
            "generation_prompt_profile": self.profile,
            "generation_prompt_fingerprint": summary.generation_prompt_fingerprint,
            "cases": [vars(case) for case in cases],
        }
        return summary


def test_screening_runs_fixed_profiles_and_selects_guardrail_safe_candidate() -> None:
    _FakeReplayService.calls.clear()
    _FakeOracleService.calls.clear()
    service = EvaluationOraclePromptScreeningService(
        Settings(app_env="test"),
        replay_service_factory=_FakeReplayService,
        oracle_service_factory=_FakeOracleService,
    )

    summary = service.run(
        cast(Session, object()),
        evaluation_run_id=112,
        expected_case_count=4,
        replay_count=3,
    )

    assert _FakeReplayService.calls == [(112, 3, 4)]
    assert _FakeOracleService.calls == [
        "baseline",
        "multi_fact_coverage_v1",
        "multi_fact_coverage_instruction_guard_v1",
    ]
    assert summary.comparability_status == "comparable"
    assert summary.baseline_profile == "baseline"
    assert summary.selected_profile == "multi_fact_coverage_v1"
    assert summary.selected_for_a3_confirmation == "multi_fact_coverage_v1"
    assert summary.candidate_improves_baseline is True
    assert summary.candidate_guardrails_non_worse is True
    assert summary.screening_gate_passed is True
    selected = summary.profiles[1]
    assert selected.changed_case_ids_from_baseline == ("answerable_injection_02",)
    rendered = repr(summary.safe_dict())
    assert "answer_text" not in rendered
    assert "context_items" not in rendered


def _case(
    case_id: str,
    *,
    answerable: bool,
    passed: bool,
    prompt_injection: bool = False,
    injection_resisted: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        case_id=case_id,
        answerable=answerable,
        tags=("prompt_injection",) if prompt_injection else (),
        o_auxiliary_pass=passed,
        o_citation_support="pass" if answerable and passed else "fail",
        o_required_facts_supported="pass" if answerable and passed else "fail",
        o_prompt_injection_resisted=(
            "pass" if prompt_injection and injection_resisted else "not_applicable"
        ),
    )
