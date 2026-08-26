from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.rag.generation import AnswerGenerator, GenerationRequest, GenerationResult
from app.services.evaluation_atomic_claim_contracts import (
    canonical_json_bytes,
    model_bytes_match,
    read_json_object,
)
from app.services.evaluation_qwen_confirm_fixture_sensitivity_service import (
    build_rag87_private_fixture,
)
from app.services.evaluation_qwen_context_near_miss_service import Rag86LMInventorySummary
from app.services.evaluation_qwen_multifact_interference_repair_service import (
    Rag88ExperimentLock,
    _execution_schedule,
    _repair_task,
    _sha256,
    build_rag88_attempt_state,
    build_rag88_experiment_lock,
    build_rag88_experiment_manifest,
    build_rag88_private_fixture,
    build_rag88_reference_catalog,
    load_frozen_rag88_experiment_manifest,
    run_rag88_experiment,
)

PRELIVE_COMMIT = "1" * 40
TEST_PRIVATE_ENTROPY = b"rag88-test-private-entropy-v1-0001"
TEST_RAG87_ENTROPY = b"rag87-reference-private-entropy-v1"
LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag88_qwen_multifact_interference_repair_lock.json"
)


def test_private_fixture_has_same_context_variants_and_new_reference_hashes() -> None:
    envelope, manifest = _fixture_and_manifest()

    assert len(envelope.groups) == 12
    assert len(envelope.dataset.cases) == 36
    assert len(envelope.dataset.corpus_documents) == 72
    assert manifest.independence.local_accuracy_dev_question_overlap_count == 0
    assert manifest.independence.rag84_question_overlap_count == 0
    assert manifest.independence.rag87_question_overlap_count == 0
    assert manifest.independence.reference_content_used_for_case_design is False
    assert all(item.same_oracle_context_all_variants for item in manifest.dataset.groups)
    assert {item.required_citation_ids for item in manifest.dataset.groups} == {(2, 5)}


def test_schedule_uses_prefixed_latin_case_rotations_and_144_calls() -> None:
    _, manifest = _fixture_and_manifest()
    schedule = _execution_schedule(manifest.dataset.groups)

    assert len(schedule) == 144
    first_group_by_repeat = {
        repeat: next(group_id for _, value, group_id, _ in schedule if value == repeat)
        for repeat in (1, 2, 3)
    }
    ordered = tuple(sorted(item.group_id for item in manifest.dataset.groups))
    assert first_group_by_repeat == {1: ordered[0], 2: ordered[4], 3: ordered[8]}
    assert sum(variant == "combined_candidate" for _, _, _, variant in schedule) == 36


def test_lock_is_raw_free_and_freezes_design_before_live() -> None:
    _, manifest = _fixture_and_manifest()
    lock = build_rag88_experiment_lock(manifest)
    payload = lock.model_dump(mode="json")

    assert lock.model_call_count == 144
    assert lock.decision_rule.minimum_eligible_case_count == 8
    assert lock.decision_rule.minimum_baseline_incomplete_case_count == 6
    assert lock.decision_rule.minimum_interference_drop == 0.5
    assert lock.decision_rule.bootstrap_resamples == 10_000
    assert lock.decision_rule.exact_p_value_maximum == 0.05
    assert lock.decision_rule.minimum_candidate_joint_completeness_delta == 0.25
    assert lock.decision_rule.candidate_p95_latency_ratio_maximum == 2.0
    assert lock.generation.combined_candidate_reuses_paired_baseline_pass1 is True
    assert lock.generation.evaluator_required_fact_sent_to_repair is False
    assert lock.generation.expected_answer_sent_to_repair is False
    assert lock.generation.evaluator_fact_id_sent_to_repair is False
    assert (
        _find_forbidden_key(
            payload,
            {
                "question",
                "source",
                "context",
                "answer",
                "fact",
                "statement",
                "body",
                "revised_answer",
                "pass1_answer",
            },
        )
        is False
    )


def test_repository_lock_has_exact_raw_free_model_bytes() -> None:
    payload_bytes, payload = read_json_object(LOCK_PATH)
    lock = Rag88ExperimentLock.model_validate(payload)

    assert lock.schema_version == "phase3.rag88_interference_repair_lock.v1"
    assert lock.model_call_count == 144
    assert model_bytes_match(payload_bytes, lock)


def test_frozen_lock_loads_the_exact_private_fixture(tmp_path: Path) -> None:
    envelope, manifest = _fixture_and_manifest()
    lock = build_rag88_experiment_lock(manifest)
    private_path = tmp_path / "private.json"
    lock_path = tmp_path / "lock.json"
    private_path.write_bytes(canonical_json_bytes(envelope))
    lock_path.write_text(
        lock.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="",
    )

    loaded_manifest, loaded_envelope = load_frozen_rag88_experiment_manifest(
        lock_path,
        private_path,
    )

    assert loaded_manifest == manifest
    assert loaded_envelope == envelope


def test_repair_task_contains_only_generic_runtime_inputs() -> None:
    task = _repair_task(question="Q-RUNTIME", pass1_answer="A-RUNTIME")

    assert "Q-RUNTIME" in task
    assert "A-RUNTIME" in task
    assert "required_fact" not in task
    assert "expected_answer" not in task
    assert "step-by-step" not in task


def test_interference_and_repair_can_pass_all_frozen_gates() -> None:
    envelope, manifest = _fixture_and_manifest()
    inventory = _stable_inventory()
    result = run_rag88_experiment(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_InterferenceFakeGenerator(envelope, repair_mode="fix"),
    )

    assert result.conclusion == "candidate_adopted"
    assert result.validity_gate_passed is True
    assert result.baseline_sensitivity_gate_passed is True
    assert result.candidate_adoption_gate_passed is True
    assert result.summary.eligible_case_count == 12
    assert result.summary.baseline_incomplete_case_count == 6
    assert result.summary.baseline_interference_drop == 0.5
    assert result.summary.baseline_interference_exact_p_value == 0.03125
    assert result.summary.candidate_joint_completeness_delta == 0.5
    assert result.summary.candidate_exact_p_value == 0.03125
    assert result.pipeline_failure_count == 0
    assert len(result.observations) == 144


def test_candidate_is_rejected_when_repair_does_not_improve_sensitive_baseline() -> None:
    envelope, manifest = _fixture_and_manifest()
    inventory = _stable_inventory()
    result = run_rag88_experiment(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_InterferenceFakeGenerator(envelope, repair_mode="keep"),
    )

    assert result.baseline_sensitivity_gate_passed is True
    assert result.conclusion == "candidate_rejected"
    assert result.candidate_adoption_gate_passed is False
    assert result.baseline_retained is True
    assert "rag88_candidate_joint_delta_below_minimum" in result.reason_codes


def test_candidate_is_descriptive_when_baseline_sensitivity_is_absent() -> None:
    envelope, manifest = _fixture_and_manifest()
    inventory = _stable_inventory()
    result = run_rag88_experiment(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_InterferenceFakeGenerator(
            envelope,
            repair_mode="keep",
            incomplete_group_count=0,
        ),
    )

    assert result.conclusion == "baseline_sensitivity_not_established"
    assert result.baseline_sensitivity_gate_passed is False
    assert result.candidate_metrics_descriptive_only is True
    assert result.candidate_adoption_gate_passed is False
    assert result.baseline_retained is True


def test_exact_target_drift_is_inconclusive_without_rerun_authority() -> None:
    envelope, manifest = _fixture_and_manifest()
    pre = _stable_inventory()
    post = pre.model_copy(update={"target_entry_fingerprint": "f" * 64})
    result = run_rag88_experiment(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=pre,
        post_lm_inventory_provider=lambda: post,
        generator=_InterferenceFakeGenerator(envelope, repair_mode="fix"),
    )

    assert result.conclusion == "inconclusive"
    assert result.validity_gate_passed is False
    assert result.exact_target_stable is False
    assert "rag88_target_entry_drift" in result.reason_codes
    assert manifest.decision_rule.failed_case_replacement_extra_repeat_or_rerun_allowed is False


def test_attempt_state_binds_one_shot_and_exact_target() -> None:
    _, manifest = _fixture_and_manifest()
    attempt = build_rag88_attempt_state(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )

    assert attempt.status == "started"
    assert attempt.expected_model_call_count == 144
    assert attempt.repeat_replacement_or_rerun_allowed is False
    assert attempt.raw_content_persisted is False


def _fixture_and_manifest():
    envelope = build_rag88_private_fixture(TEST_PRIVATE_ENTROPY)
    rag87 = build_rag87_private_fixture(TEST_RAG87_ENTROPY)
    catalog = build_rag88_reference_catalog(rag87)
    private_bytes = canonical_json_bytes(envelope)
    manifest = build_rag88_experiment_manifest(
        envelope,
        private_input_sha256=hashlib.sha256(private_bytes).hexdigest(),
        reference_catalog=catalog,
    )
    return envelope, manifest


def _stable_inventory() -> Rag86LMInventorySummary:
    return Rag86LMInventorySummary(
        available=True,
        full_inventory_fingerprint="a" * 64,
        target_model_id_fingerprint=_sha256("qwen/qwen3.5-9b"),
        target_entry_fingerprint="b" * 64,
        target_loaded_instance_count=1,
        target_loaded_context_length=12312,
        model_count=7,
        loaded_instance_count=2,
    )


class _InterferenceFakeGenerator(AnswerGenerator):
    def __init__(
        self,
        envelope,
        *,
        repair_mode: str,
        incomplete_group_count: int = 6,
    ) -> None:
        self.repair_mode = repair_mode
        self.incomplete_group_count = incomplete_group_count
        self.cases = {item.case_key: item for item in envelope.dataset.cases}
        self.question_to_case = {item.question: item for item in envelope.dataset.cases}
        self.group_by_source = {group.source_ids[0]: group for group in envelope.groups}

    def generate(self, request: GenerationRequest) -> GenerationResult:
        group = self.group_by_source[request.context_items[0].source_label]
        combined = self.cases[group.combined_case_id]
        group_ordinal = int(group.group_id.rsplit("-", 1)[1])
        incomplete = group_ordinal <= self.incomplete_group_count
        answer_a = f"{combined.required_facts[0].statement} [2]"
        answer_b = f"{combined.required_facts[1].statement} [5]"
        if request.task_instructions is not None:
            if self.repair_mode == "fix" and incomplete:
                payload = {
                    "decision": "revise",
                    "coverage_status": "incomplete",
                    "incorrect_insufficiency_detected": False,
                    "citation_status": "complete",
                    "revised_answer": f"{answer_a} {answer_b}",
                }
            else:
                payload = {
                    "decision": "keep",
                    "coverage_status": "complete" if not incomplete else "incomplete",
                    "incorrect_insufficiency_detected": False,
                    "citation_status": "complete",
                    "revised_answer": "",
                }
            return GenerationResult(content=json.dumps(payload, ensure_ascii=False))
        case = self.question_to_case[request.message]
        if case.case_key == group.single_a_case_id:
            return GenerationResult(content=answer_a)
        if case.case_key == group.single_b_case_id:
            return GenerationResult(content=answer_b)
        return GenerationResult(content=answer_a if incomplete else f"{answer_a} {answer_b}")


def _find_forbidden_key(value: object, forbidden_keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in forbidden_keys:
                return True
            if _find_forbidden_key(nested, forbidden_keys):
                return True
    elif isinstance(value, list):
        return any(_find_forbidden_key(item, forbidden_keys) for item in value)
    return False
