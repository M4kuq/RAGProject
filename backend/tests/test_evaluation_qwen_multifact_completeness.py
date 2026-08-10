from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.services.evaluation_qwen_multifact_completeness_service as rag84_service
from app.evaluation.generation_prompt_profiles import resolve_generation_prompt_profile
from app.evaluation.local_accuracy_dev import build_local_accuracy_dev_manifest
from app.evaluation.qwen_multifact_confirm import build_qwen_multifact_confirm_manifest
from app.rag.generation import AnswerGenerator, GenerationRequest, GenerationResult
from app.services.evaluation_qwen_multifact_completeness_service import (
    EvaluationQwenMultifactCompletenessError,
    build_rag84_experiment_manifest,
    load_frozen_rag84_experiment_manifest,
    run_rag84_confirm,
    run_rag84_tune,
)

STACKED_BASE = "d49b8282de68ed4ba1a2d5806d9b64be67c1ae74"
LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag84_qwen_multifact_experiment_lock.json"
)


def test_confirm_fixture_is_static_balanced_and_disjoint_from_tune() -> None:
    tune = build_local_accuracy_dev_manifest()
    confirm = build_qwen_multifact_confirm_manifest()
    tune_cases = [case for case in tune.cases if case.answerable and "multi_hop" in case.tags]

    assert len(confirm.cases) == 14
    assert len(confirm.corpus_documents) == 28
    assert sum("language:ja" in case.tags for case in confirm.cases) == 7
    assert sum("language:en" in case.tags for case in confirm.cases) == 7
    assert sum("prompt_injection" in case.tags for case in confirm.cases) == 2
    assert all(case.answerable for case in confirm.cases)
    assert all(len(case.required_facts) == 2 for case in confirm.cases)
    assert all(len(case.expected_evidence) == 2 for case in confirm.cases)
    assert all(
        (case.metadata_json or {}).get("oracle_citation_ids") == [1, 2] for case in confirm.cases
    )
    assert {case.question for case in tune_cases}.isdisjoint(
        case.question for case in confirm.cases
    )
    assert {fact.fact_id for case in tune_cases for fact in case.required_facts}.isdisjoint(
        fact.fact_id for case in confirm.cases for fact in case.required_facts
    )
    assert {document.source_key for document in tune.corpus_documents}.isdisjoint(
        document.source_key for document in confirm.corpus_documents
    )


def test_frozen_manifest_binds_prompt_fixture_context_and_decision_rule() -> None:
    manifest = load_frozen_rag84_experiment_manifest(LOCK_PATH)

    assert manifest.stacked_base_commit == STACKED_BASE
    assert manifest.tune.case_count == 12
    assert manifest.tune.required_fact_count == 24
    assert manifest.confirm.case_count == 14
    assert manifest.confirm.required_fact_count == 28
    assert manifest.independence.tune_confirm_question_hash_overlap_count == 0
    assert manifest.independence.tune_confirm_normalized_fact_hash_overlap_count == 0
    assert manifest.independence.tune_confirm_source_content_hash_overlap_count == 0
    assert manifest.generation.only_experimental_coordinate == "prompt_only_evidence_ledger"
    assert manifest.generation.required_facts_or_answer_keys_sent_as_prompt_fields is False
    assert manifest.decision_rule.tune_candidate_minimum_required_fact_recall == 0.75
    assert manifest.decision_rule.confirm_repeats_per_profile == 3
    assert manifest.decision_rule.optimize_after_confirm is False
    assert manifest.gold_v2_access_allowed is False


def test_manifest_hash_drift_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    payload["candidate_prompt_fingerprint"] = "0" * 64
    drifted = tmp_path / "drifted.json"
    drifted.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        EvaluationQwenMultifactCompletenessError,
        match="rag84_manifest_runtime_drift",
    ):
        load_frozen_rag84_experiment_manifest(drifted)


def test_candidate_prompt_is_case_blind_and_baseline_is_unchanged() -> None:
    baseline = resolve_generation_prompt_profile("baseline")
    candidate = resolve_generation_prompt_profile("multi_fact_evidence_ledger_v1")
    confirm = build_qwen_multifact_confirm_manifest()

    assert baseline.system_instructions is None
    assert candidate.system_instructions is not None
    assert "evidence-ledger" in candidate.system_instructions
    assert "Do not output the ledger" in candidate.system_instructions
    assert all(
        fact.fact_id not in candidate.system_instructions
        for case in confirm.cases
        for fact in case.required_facts
    )
    assert all(
        case.expected_answer not in candidate.system_instructions
        for case in confirm.cases
        if case.expected_answer is not None
    )


def test_tune_and_confirm_apply_frozen_gates_without_raw_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"value": 0.0}

    def _stable_clock() -> float:
        clock["value"] += 0.001
        return clock["value"]

    monkeypatch.setattr(rag84_service.time, "perf_counter", _stable_clock)
    manifest = build_rag84_experiment_manifest(stacked_base_commit=STACKED_BASE)
    generator = _EvidenceLedgerFakeGenerator()

    tune = run_rag84_tune(
        manifest,
        preconfirm_commit_sha="1" * 40,
        generator=generator,
    )

    assert tune.gate_passed is True
    assert tune.decision == "advance_to_confirm"
    assert tune.profiles[0].atomic_required_fact_recall == 0.5
    assert tune.profiles[1].atomic_required_fact_recall == 1.0
    assert tune.profiles[0].insufficiency_false_assertion_case_count == 9
    assert tune.profiles[1].insufficiency_false_assertion_case_count == 0
    assert tune.profiles[1].unexpected_fact_case_count == 0
    assert tune.profiles[1].pipeline_failure_count == 0

    confirm = run_rag84_confirm(
        manifest,
        preconfirm_commit_sha="1" * 40,
        tune_result=tune,
        runtime_stable_for_latency=True,
        generator=generator,
    )

    assert confirm.gate_passed is True
    assert confirm.decision == "adopt_candidate"
    assert confirm.profiles[0].majority_atomic_required_fact_recall == 0.5
    assert confirm.profiles[1].majority_atomic_required_fact_recall == 1.0
    assert confirm.profiles[1].majority_citation_grounding_recall == 1.0
    rendered = confirm.model_dump_json()
    for raw_field in (
        '"question"',
        '"answer_text"',
        '"context_items"',
        '"required_fact"',
        '"expected_answer"',
        '"source_text"',
    ):
        assert raw_field not in rendered
    assert confirm.independent_human_only_calibration is False
    assert generator.leaked_prompt_field_count == 0


def test_confirm_requires_passing_tune_and_stable_runtime() -> None:
    manifest = build_rag84_experiment_manifest(stacked_base_commit=STACKED_BASE)
    tune = run_rag84_tune(
        manifest,
        preconfirm_commit_sha="2" * 40,
        generator=_EvidenceLedgerFakeGenerator(),
    )

    with pytest.raises(
        EvaluationQwenMultifactCompletenessError,
        match="rag84_confirm_runtime_not_stable",
    ):
        run_rag84_confirm(
            manifest,
            preconfirm_commit_sha="2" * 40,
            tune_result=tune,
            runtime_stable_for_latency=False,
            generator=_EvidenceLedgerFakeGenerator(),
        )


class _EvidenceLedgerFakeGenerator(AnswerGenerator):
    def __init__(self) -> None:
        manifests = (
            build_local_accuracy_dev_manifest(),
            build_qwen_multifact_confirm_manifest(),
        )
        self.fact_by_source = {
            document.source_key: document.facts[0].statement
            for manifest in manifests
            for document in manifest.corpus_documents
        }
        self.baseline_tune_calls = 0
        self.leaked_prompt_field_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        candidate = bool(
            request.system_instructions
            and "evidence-ledger instruction" in request.system_instructions
        )
        facts = [self.fact_by_source[item.source_label] for item in request.context_items]
        assert len(facts) == 2
        if request.system_instructions:
            self.leaked_prompt_field_count += sum(
                fact_id in request.system_instructions for fact_id in self._all_fact_ids()
            )
        if candidate:
            content = f"{facts[0]} [1] {facts[1]} [2]"
        else:
            content = f"{facts[0]} [1]"
            if request.context_items[0].source_label.startswith("local_dev_source_"):
                self.baseline_tune_calls += 1
                if self.baseline_tune_calls <= 9:
                    content += " 根拠不足です。"
        return GenerationResult(content=content, usage=None)

    def _all_fact_ids(self) -> tuple[str, ...]:
        return tuple(
            fact.fact_id
            for manifest in (
                build_local_accuracy_dev_manifest(),
                build_qwen_multifact_confirm_manifest(),
            )
            for case in manifest.cases
            for fact in case.required_facts
        )
