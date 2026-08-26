from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from app.rag.generation import (
    AnswerGenerationError,
    AnswerGenerator,
    GenerationRequest,
    GenerationResult,
)
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
    _sha256,
    build_rag88_experiment_lock,
    build_rag88_experiment_manifest,
    build_rag88_private_fixture,
    build_rag88_reference_catalog,
)
from app.services.evaluation_qwen_timeout_contract_validation_service import (
    RAG84_RESULT_SHA256,
    Rag90ExperimentLock,
    Rag90TimeoutEvidence,
    build_rag90_attempt_state,
    build_rag90_host_gate,
    build_rag90_lock,
    build_rag90_manifest,
    build_rag90_private_fixture,
    build_rag90_timeout_evidence,
    run_rag90_experiment,
)

TEST_RAG90_ENTROPY = b"rag90-test-private-entropy-v1-0001"
TEST_RAG88_ENTROPY = b"rag88-reference-private-entropy-v1"
TEST_RAG87_ENTROPY = b"rag87-reference-private-entropy-v1"
TEST_COMMIT = "1" * 40
RAG88_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag88_qwen_multifact_interference_repair_lock.json"
)
RAG90_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag90_qwen_timeout_contract_validation_lock.json"
)


def test_timeout_formula_is_censor_aware_and_rounds_to_six_minutes(monkeypatch) -> None:
    rag84_payload = {
        "raw_content_persisted": False,
        "runtime_stable_for_latency": False,
        "profiles": [
            {"profile": "baseline", "p95_latency_ms": 166329},
            {"profile": "multi_fact_evidence_ledger_v1", "p95_latency_ms": 158281},
        ],
        "observations": [{"latency_ms": value} for value in ([166329] + [80000] * 23)],
    }
    rag84_bytes = json.dumps(rag84_payload, sort_keys=True).encode()
    monkeypatch.setattr(
        "app.services.evaluation_qwen_timeout_contract_validation_service.RAG84_RESULT_SHA256",
        hashlib.sha256(rag84_bytes).hexdigest(),
    )
    fake_report = SimpleNamespace(
        direct_timeout_count=40,
        successful_standard_latency=SimpleNamespace(p95_ms=159420, maximum_ms=179741),
        physical_accounted_duration_ms=14460586,
    )
    monkeypatch.setattr(
        "app.services.evaluation_qwen_timeout_contract_validation_service."
        "build_rag89_timeout_diagnostic_report",
        lambda _result, _attempt: fake_report,
    )

    evidence = build_rag90_timeout_evidence(rag84_bytes, b"result", b"attempt")

    assert evidence.selected_timeout_seconds == 360
    assert evidence.derived_unrounded_seconds == 346.329
    assert evidence.formula.startswith("ceil_to_60s")


def test_new_fixture_is_hash_disjoint_and_only_timeout_behavior_changes() -> None:
    manifest, reference_lock = _manifest_and_reference_lock()

    assert manifest.independence.question_overlap_count == 0
    assert manifest.independence.normalized_required_fact_overlap_count == 0
    assert manifest.independence.source_content_overlap_count == 0
    assert manifest.independence.logical_source_identifier_overlap_count == 0
    assert manifest.core_manifest.generation.generation_case_wall_clock_timeout_seconds == 360
    assert reference_lock.generation.generation_case_wall_clock_timeout_seconds == 180
    assert manifest.core_manifest.decision_rule == reference_lock.decision_rule
    left = manifest.core_manifest.generation.model_dump()
    right = reference_lock.generation.model_dump()
    differing = {key for key in left if left[key] != right[key]}
    assert differing == {
        "generation_case_wall_clock_timeout_seconds",
        "execution_schedule_fingerprint",
    }


def test_rag88_repository_lock_bytes_remain_exact_after_timeout_parameterization() -> None:
    lock_bytes, payload = read_json_object(RAG88_LOCK_PATH)
    from app.services.evaluation_qwen_multifact_interference_repair_service import (
        Rag88ExperimentLock,
    )

    lock = Rag88ExperimentLock.model_validate(payload)

    assert model_bytes_match(lock_bytes, lock)
    assert lock.generation.generation_case_wall_clock_timeout_seconds == 180


def test_rag90_repository_lock_is_exact_raw_free_and_frozen() -> None:
    lock_bytes, payload = read_json_object(RAG90_LOCK_PATH)
    lock = Rag90ExperimentLock.model_validate(payload)

    assert model_bytes_match(lock_bytes, lock)
    assert lock.manifest.timeout_contract.evidence.selected_timeout_seconds == 360
    assert lock.manifest.timeout_contract.live_extension_allowed is False
    assert lock.manifest.core_manifest.generation.generation_max_output_tokens == 8192
    assert lock.manifest.core_manifest.decision_rule.candidate_p95_latency_ratio_maximum == 2.0


def test_fake_run_preserves_candidate_logic_and_separates_physical_latency() -> None:
    envelope = build_rag90_private_fixture(TEST_RAG90_ENTROPY)
    manifest, _ = _manifest_and_reference_lock(envelope=envelope)
    host_gate = _stable_host_gate()
    generator = _InterferenceFakeGenerator(envelope, repair_mode="fix")

    result = run_rag90_experiment(
        manifest,
        envelope,
        host_gate=host_gate,
        prelive_commit_sha=TEST_COMMIT,
        post_lm_inventory_provider=_stable_inventory,
        generator=generator,
    )

    assert result.validity_gate_passed is True
    assert result.baseline_sensitivity_gate_passed is True
    assert result.candidate_adoption_gate_passed is True
    assert result.conclusion == "candidate_adopted"
    assert result.phase_telemetry_summary.logical_observation_count == 144
    assert result.phase_telemetry_summary.physical_request_count == 144
    assert result.phase_telemetry_summary.standard_physical_request_latency.count == 108
    assert result.phase_telemetry_summary.repair_physical_request_latency.count == 36
    assert result.phase_telemetry_summary.physical_request_timeout_count == 0


def test_any_pipeline_failure_is_inconclusive_without_timeout_extension() -> None:
    envelope = build_rag90_private_fixture(TEST_RAG90_ENTROPY)
    manifest, _ = _manifest_and_reference_lock(envelope=envelope)

    result = run_rag90_experiment(
        manifest,
        envelope,
        host_gate=_stable_host_gate(),
        prelive_commit_sha=TEST_COMMIT,
        post_lm_inventory_provider=_stable_inventory,
        generator=_AlwaysFailGenerator(),
    )

    assert result.core_result.pipeline_failure_count > 0
    assert result.validity_gate_passed is False
    assert result.conclusion == "inconclusive"
    assert result.reason_codes == ("rag90_pipeline_failure",)
    assert manifest.timeout_contract.live_extension_allowed is False


def test_lock_attempt_and_result_models_are_raw_free() -> None:
    envelope = build_rag90_private_fixture(TEST_RAG90_ENTROPY)
    manifest, _ = _manifest_and_reference_lock(envelope=envelope)
    host_gate = _stable_host_gate()
    lock = build_rag90_lock(manifest)
    attempt = build_rag90_attempt_state(
        manifest,
        host_gate=host_gate,
        prelive_commit_sha=TEST_COMMIT,
    )
    result = run_rag90_experiment(
        manifest,
        envelope,
        host_gate=host_gate,
        prelive_commit_sha=TEST_COMMIT,
        post_lm_inventory_provider=_stable_inventory,
        generator=_InterferenceFakeGenerator(envelope, repair_mode="keep"),
    )
    forbidden = {
        "answer",
        "body",
        "chain_of_thought",
        "content",
        "context",
        "fact",
        "pass1_answer",
        "prompt",
        "question",
        "response",
        "revised_answer",
        "source",
        "statement",
        "text",
    }

    assert not _find_forbidden_key(lock.model_dump(mode="json"), forbidden)
    assert not _find_forbidden_key(attempt.model_dump(mode="json"), forbidden)
    assert not _find_forbidden_key(result.model_dump(mode="json"), forbidden)
    assert attempt.timeout_extension_or_rerun_allowed is False
    assert result.raw_content_persisted is False
    assert result.chain_of_thought_persisted is False


def _manifest_and_reference_lock(*, envelope=None):
    rag87 = build_rag87_private_fixture(TEST_RAG87_ENTROPY)
    catalog = build_rag88_reference_catalog(rag87)
    reference_envelope = build_rag88_private_fixture(TEST_RAG88_ENTROPY)
    reference_bytes = canonical_json_bytes(reference_envelope)
    reference_manifest = build_rag88_experiment_manifest(
        reference_envelope,
        private_input_sha256=hashlib.sha256(reference_bytes).hexdigest(),
        reference_catalog=catalog,
        stacked_base_commit=TEST_COMMIT,
    )
    reference_lock = build_rag88_experiment_lock(reference_manifest)
    envelope = envelope or build_rag90_private_fixture(TEST_RAG90_ENTROPY)
    private_bytes = canonical_json_bytes(envelope)
    manifest = build_rag90_manifest(
        envelope,
        private_input_sha256=hashlib.sha256(private_bytes).hexdigest(),
        reference_catalog=catalog,
        rag88_reference_envelope=reference_envelope,
        rag88_private_input_sha256=hashlib.sha256(reference_bytes).hexdigest(),
        rag88_lock=reference_lock,
        rag88_lock_sha256=hashlib.sha256(canonical_json_bytes(reference_lock)).hexdigest(),
        timeout_evidence=_timeout_evidence(),
        stacked_base_commit=TEST_COMMIT,
    )
    return manifest, reference_lock


def _timeout_evidence() -> Rag90TimeoutEvidence:
    return Rag90TimeoutEvidence(
        formula=(
            "ceil_to_60s(rag88_censor_boundary_s+max(rag84_baseline_p95_s,rag88_success_p95_s))"
        ),
        rounding_quantum_seconds=60,
        rag84_result_sha256=RAG84_RESULT_SHA256,
        rag84_baseline_p95_ms=166329,
        rag84_candidate_p95_ms=158281,
        rag84_success_maximum_ms=166329,
        rag84_observation_count=24,
        rag84_runtime_stable_for_latency=False,
        rag88_result_sha256=("dca0865277b40f1c05401ca3afc74798c3a69b01b7e8ea161c819e3b6d2954e9"),
        rag88_attempt_sha256=("ffac4be6bcc021791937835f105771a955159ee47b47ff6926fb9e22555591c7"),
        rag89_diagnostic_sha256=(
            "6222ece91f2ec4ca8fcbf394df50d64a3167dec7563b9c23bfa375115ce6be38"
        ),
        rag88_censor_boundary_ms=180000,
        rag88_direct_timeout_count=40,
        rag88_success_standard_p95_ms=159420,
        rag88_success_standard_maximum_ms=179741,
        rag88_physical_accounted_duration_ms=14460586,
        derived_unrounded_seconds=346.329,
        selected_timeout_seconds=360,
    )


def _stable_inventory() -> Rag86LMInventorySummary:
    return Rag86LMInventorySummary(
        available=True,
        full_inventory_fingerprint="a" * 64,
        target_model_id_fingerprint=_sha256("qwen/qwen3.5-9b"),
        target_entry_fingerprint="b" * 64,
        target_loaded_instance_count=1,
        target_loaded_context_length=12312,
        model_count=1,
        loaded_instance_count=1,
    )


def _stable_host_gate():
    return build_rag90_host_gate(
        _stable_inventory(),
        gpu_utilization_samples_percent=(0, 1, 0),
        concurrent_evaluation_process_count=0,
        concurrent_model_load_observed=False,
    )


class _InterferenceFakeGenerator(AnswerGenerator):
    def __init__(self, envelope, *, repair_mode: str, incomplete_group_count: int = 6) -> None:
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


class _AlwaysFailGenerator(AnswerGenerator):
    def generate(self, request: GenerationRequest) -> GenerationResult:
        del request
        raise AnswerGenerationError(error_category="timeout")


def _find_forbidden_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(
            key in forbidden or _find_forbidden_key(item, forbidden) for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_find_forbidden_key(item, forbidden) for item in value)
    return False
