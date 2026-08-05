from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import cast

import pytest

from app.core.config import Settings
from app.services.evaluation_oracle_context_service import (
    EvaluationOracleContextService,
    OracleContextCaseOutcome,
    OracleContextDiagnosticSummary,
)
from app.services.evaluation_oracle_evidence_grouping_service import (
    EvaluationOracleEvidenceGroupingError,
    EvaluationOracleEvidenceGroupingService,
    compare_evidence_grouping,
)


def test_grouping_screening_selects_only_non_worse_measured_improvement() -> None:
    baseline = _baseline_summary()
    candidate = _candidate_summary(improved=True)

    summary = compare_evidence_grouping(
        baseline_summary=baseline,
        candidate=candidate,
        source_screening_artifact_sha256="f" * 64,
    )

    assert summary.comparability_status == "comparable"
    assert summary.target_case_count == 12
    assert summary.baseline.auxiliary_pass_count == 6
    assert summary.candidate.auxiliary_pass_count == 7
    assert summary.auxiliary_pass_delta == 1
    assert summary.generation_gap_delta == -1
    assert summary.mean_context_utilization_delta == 0.1
    assert summary.candidate_guardrails_non_worse is True
    assert summary.candidate_improves_baseline is True
    assert summary.selected_for_three_repeat_confirmation is True
    assert summary.security_gate_claimed is False
    assert all(case.context_hash_changed for case in summary.cases)

    rendered = json.dumps(summary.safe_dict(), sort_keys=True)
    assert '"question":' not in rendered
    assert '"answer_text":' not in rendered
    assert '"context_items":' not in rendered


def test_grouping_screening_rejects_guardrail_regression() -> None:
    candidate = _candidate_summary(improved=False)
    cases = list(candidate.cases)
    cases[0] = replace(
        cases[0],
        o_auxiliary_pass=False,
        o_required_facts_supported="fail",
        o_citation_support="fail",
        o_context_utilization=0.25,
    )

    summary = compare_evidence_grouping(
        baseline_summary=_baseline_summary(),
        candidate=replace(
            candidate,
            cases=tuple(cases),
            o_auxiliary_pass_count=5,
            o_mean_context_utilization=0.479167,
        ),
        source_screening_artifact_sha256="f" * 64,
    )

    assert summary.candidate_guardrails_non_worse is False
    assert summary.selected_for_three_repeat_confirmation is False
    assert "candidate_guardrail_regression" in summary.decision_reason_codes


def test_grouping_screening_fails_when_fixed_case_coordinate_changes() -> None:
    candidate = _candidate_summary(improved=True)
    cases = list(candidate.cases)
    cases[0] = replace(cases[0], question_hash="0" * 64)

    with pytest.raises(
        EvaluationOracleEvidenceGroupingError,
        match="oracle_grouping_case_coordinate_changed",
    ):
        compare_evidence_grouping(
            baseline_summary=_baseline_summary(),
            candidate=replace(candidate, cases=tuple(cases)),
            source_screening_artifact_sha256="f" * 64,
        )


def test_grouping_service_uses_fixed_candidate_and_full_source_validation() -> None:
    candidate = _candidate_summary(improved=True)
    captured: dict[str, object] = {}

    class FakeOracleService:
        def run(self, db: object, **kwargs: object) -> OracleContextDiagnosticSummary:
            captured["db"] = db
            captured.update(kwargs)
            return candidate

    def factory(settings: Settings, **kwargs: object) -> EvaluationOracleContextService:
        captured["settings"] = settings
        captured["factory_kwargs"] = kwargs
        return cast(EvaluationOracleContextService, FakeOracleService())

    source = {
        "schema_version": "phase3.oracle_prompt_screening.v1",
        "source_evaluation_run_id": 112,
        "dataset_name": "local_accuracy_dev_v1",
        "resolved_generation_model": "qwen/qwen3.5-9b",
        "generation_temperature": 0.0,
        "expected_case_count": 40,
        "replay_gate_passed": True,
        "screening_gate_passed": True,
        "baseline_profile": "baseline",
        "safe_judge_replay": {"gate_passed": True},
        "profiles": [{"profile": "baseline", "safe_oracle_summary": _baseline_summary()}],
    }
    service = EvaluationOracleEvidenceGroupingService(
        Settings(app_env="test"),
        oracle_service_factory=factory,
    )

    summary = service.run(
        object(),  # type: ignore[arg-type]
        evaluation_run_id=112,
        source_screening=source,
        source_screening_artifact_sha256="f" * 64,
    )

    assert summary.selected_for_three_repeat_confirmation is True
    assert captured["factory_kwargs"] == {
        "generation_prompt_profile": "baseline",
        "context_grouping_profile": "multi_fact_evidence_group_v1",
        "case_ids": frozenset(f"local_dev_answerable_{index:02d}" for index in range(13, 25)),
    }
    assert captured["expected_case_count"] == 40


def _baseline_summary() -> dict[str, object]:
    candidate = _candidate_summary(improved=False)
    cases: list[dict[str, object]] = []
    for index, case in enumerate(candidate.cases):
        baseline_pass = index < 6
        cases.append(
            {
                **case.__dict__,
                "o_auxiliary_pass": baseline_pass,
                "o_required_facts_supported": "pass" if index != 10 else "fail",
                "o_citation_support": "pass" if index != 11 else "fail",
                "o_context_utilization": 0.5,
                "o_context_hash": "a" * 64,
                "pipeline_failure_code": None,
            }
        )
    return {
        "source_evaluation_run_id": candidate.source_evaluation_run_id,
        "dataset_name": candidate.dataset_name,
        "dataset_content_fingerprint": candidate.dataset_content_fingerprint,
        "corpus_fingerprint": candidate.corpus_fingerprint,
        "generation_config_fingerprint": candidate.generation_config_fingerprint,
        "r_judge_replay_fingerprint": candidate.r_judge_replay_fingerprint,
        "resolved_generation_model": candidate.resolved_generation_model,
        "generation_prompt_profile": candidate.generation_prompt_profile,
        "generation_prompt_fingerprint": candidate.generation_prompt_fingerprint,
        "generation_temperature": candidate.generation_temperature,
        "generation_max_context_chars": candidate.generation_max_context_chars,
        "generation_max_output_chars": candidate.generation_max_output_chars,
        "generation_max_output_tokens": candidate.generation_max_output_tokens,
        "expected_case_count": 40,
        "cases": cases,
    }


def _candidate_summary(*, improved: bool) -> OracleContextDiagnosticSummary:
    cases = tuple(
        _case(
            index=index,
            candidate_pass=index < (7 if improved else 6),
        )
        for index in range(12)
    )
    pass_count = sum(case.o_auxiliary_pass is True for case in cases)
    return OracleContextDiagnosticSummary(
        schema_version="phase3.oracle_context_diagnostic.v1",
        source_evaluation_run_id=112,
        dataset_name="local_accuracy_dev_v1",
        dataset_content_fingerprint="1" * 64,
        corpus_fingerprint="2" * 64,
        case_set_fingerprint="3" * 64,
        generation_config_fingerprint="4" * 64,
        r_judge_replay_fingerprint="5" * 64,
        r_judge_replay_count=3,
        resolved_generation_model="qwen/qwen3.5-9b",
        generation_prompt_profile="baseline",
        generation_prompt_fingerprint="6" * 64,
        generation_temperature=0.0,
        generation_max_context_chars=6000,
        generation_max_output_chars=12000,
        generation_max_output_tokens=8192,
        expected_case_count=12,
        comparable_case_count=12,
        comparability_status="comparable",
        r_auxiliary_pass_count=6,
        o_auxiliary_pass_count=pass_count,
        r_auxiliary_pass_rate=0.5,
        o_auxiliary_pass_rate=pass_count / 12,
        absolute_percentage_point_delta=0.0,
        answerable_retrieval_missing_gap_count=0,
        answerable_context_utilization_or_noise_gap_count=0,
        answerable_oracle_generation_gap_count=12 - pass_count,
        unanswerable_r_pass_count=0,
        unanswerable_o_pass_count=0,
        r_mean_claim_recall=1.0,
        o_mean_claim_recall=1.0,
        r_mean_context_utilization=0.5,
        o_mean_context_utilization=0.6,
        primary_next_target="test",
        pipeline_failure_count=0,
        gate_passed=True,
        cases=cases,
    )


def _case(*, index: int, candidate_pass: bool) -> OracleContextCaseOutcome:
    case_id = f"local_dev_answerable_{index + 13:02d}"
    return OracleContextCaseOutcome(
        case_id=case_id,
        question_hash=hashlib.sha256(f"question:{case_id}".encode()).hexdigest(),
        answerable=True,
        tags=("answerable", "multi_hop", "language:en"),
        required_fact_ids=(f"fact-{index}-a", f"fact-{index}-b"),
        oracle_source_keys=(f"source-{index}-a", f"source-{index}-b"),
        required_fact_count=2,
        r_retrieved_required_fact_count=2,
        r_used_required_fact_count=1,
        r_claim_recall=1.0,
        r_context_utilization=0.5,
        o_retrieved_required_fact_count=2,
        o_used_required_fact_count=2,
        o_claim_recall=1.0,
        o_context_utilization=0.6,
        r_auxiliary_pass=index < 6,
        o_auxiliary_pass=candidate_pass,
        r_answer_hash="7" * 64,
        r_context_hash="8" * 64,
        o_answer_hash=hashlib.sha256(f"answer:{case_id}".encode()).hexdigest(),
        o_context_hash=hashlib.sha256(f"grouped:{case_id}".encode()).hexdigest(),
        o_answer_outcome="answered",
        o_judge_attempt_count=1,
        o_judge_first_failure_code=None,
        o_judge_terminal_reason_code="judge_succeeded_first_attempt",
        o_judge_recovered_after_retry=False,
        o_required_facts_supported="pass",
        o_citation_support="pass",
        o_forbidden_claims_absent="pass",
        o_abstention_correct="not_applicable",
        o_prompt_injection_resisted="not_applicable",
        o_judge_confidence=0.9,
        o_judge_reason_codes=(),
        metric_reason_codes=(),
        pipeline_failure_code=None,
    )
