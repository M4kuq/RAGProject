from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.scripts.run_evaluation_qwen_confirm_fixture_sensitivity as rag87_cli
from app.rag.generation import AnswerGenerator, GenerationRequest, GenerationResult
from app.services.evaluation_atomic_claim_contracts import (
    canonical_json_bytes,
    model_bytes_match,
    read_json_object,
    write_model_json,
)
from app.services.evaluation_qwen_confirm_fixture_sensitivity_service import (
    EvaluationQwenConfirmFixtureSensitivityError,
    Rag87ExperimentLock,
    Rag87ExperimentManifest,
    Rag87PrivateFixtureEnvelope,
    _sha256,
    _sha256_bytes,
    build_rag87_attempt_state,
    build_rag87_experiment_lock,
    build_rag87_experiment_manifest,
    build_rag87_private_fixture,
    load_frozen_rag87_experiment_manifest,
    run_rag87_diagnostic,
)
from app.services.evaluation_qwen_context_near_miss_service import (
    Rag86LMInventorySummary,
)

PRELIVE_COMMIT = "1" * 40
TEST_PRIVATE_ENTROPY = b"rag87-test-private-entropy-v1"
LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag87_qwen_confirm_fixture_sensitivity_lock.json"
)


def test_private_fixture_matches_tune_structure_without_content_overlap() -> None:
    envelope, manifest = _fixture_and_manifest()

    assert len(envelope.dataset.cases) == 12
    assert len(envelope.dataset.corpus_documents) == 24
    assert manifest.dataset.case_count == 12
    assert manifest.dataset.required_fact_count == 24
    assert manifest.dataset.source_count == 24
    assert manifest.dataset.structural_features.language_ja_count == 6
    assert manifest.dataset.structural_features.language_en_count == 6
    assert manifest.dataset.structural_features.prompt_injection_tag_count == 2
    assert manifest.independence.tune_confirm_question_hash_overlap_count == 0
    assert manifest.independence.tune_confirm_normalized_fact_hash_overlap_count == 0
    assert manifest.independence.tune_confirm_source_content_hash_overlap_count == 0
    assert manifest.independence.tune_confirm_logical_document_id_overlap_count == 0
    assert manifest.independence.structural_features_exact_match is True
    assert manifest.generation.expected_generation_count == 36
    assert manifest.generation.generation_temperature == 0.0
    assert manifest.generation.reasoning_enabled is False
    assert manifest.generation.generation_max_context_chars == 6000
    assert manifest.generation.generation_max_output_chars == 12000
    assert manifest.generation.generation_max_output_tokens == 8192
    assert manifest.decision_rule.majority_recall_minimum == 0.375
    assert manifest.decision_rule.majority_recall_maximum == 0.833333
    assert manifest.gold_v2_access_allowed is False
    assert manifest.raw_content_persistence_allowed is False


def test_lock_is_raw_free_and_binds_private_input() -> None:
    _, manifest = _fixture_and_manifest()
    lock = build_rag87_experiment_lock(manifest)

    assert lock.private_input_sha256 == manifest.dataset.private_input_sha256
    assert lock.tune_structural_features == lock.confirm_structural_features
    assert lock.structural_features_exact_match is True
    assert lock.prelive_commit_required is True
    assert lock.one_shot_attempt_marker_required is True
    assert lock.generation_count == 36
    payload = json.loads(lock.model_dump_json())
    assert not _find_forbidden_key(
        payload,
        ("question", "answer", "context", "fact", "source_text", "chunk_text"),
    )


def test_frozen_lock_reloads_only_matching_external_private_input(tmp_path: Path) -> None:
    envelope = build_rag87_private_fixture(TEST_PRIVATE_ENTROPY)
    private_path = tmp_path / "private.json"
    private_path.write_bytes(canonical_json_bytes(envelope))
    manifest = build_rag87_experiment_manifest(
        envelope,
        private_input_sha256=_sha256_bytes(private_path.read_bytes()),
    )
    lock_path = tmp_path / "lock.json"
    write_model_json(lock_path, build_rag87_experiment_lock(manifest))

    manifest, loaded = load_frozen_rag87_experiment_manifest(lock_path, private_path)

    assert loaded == envelope
    assert manifest.dataset.private_input_sha256 == _sha256_bytes(private_path.read_bytes())
    assert manifest.stacked_base_commit == "a62977d3ac1c8d411c1441f886db9b96011995dd"


def test_repository_lock_has_exact_raw_free_model_bytes() -> None:
    payload_bytes, payload = read_json_object(LOCK_PATH)
    lock = Rag87ExperimentLock.model_validate(payload)

    assert model_bytes_match(payload_bytes, lock)
    assert lock.raw_content_persistence_allowed is False


def test_cli_rejects_repository_output_and_never_overwrites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rag87_cli, "_path_inside_git_checkout", lambda _path: True)
    with pytest.raises(
        EvaluationQwenConfirmFixtureSensitivityError,
        match="rag87_repository_output_rejected",
    ):
        rag87_cli._validate_external_output_path(LOCK_PATH.parent / "result.json")

    monkeypatch.setattr(rag87_cli, "_path_inside_git_checkout", lambda _path: False)
    output = tmp_path / "attempt.json"
    _, manifest = _fixture_and_manifest()
    attempt = build_rag87_attempt_state(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )
    rag87_cli._write_model_exclusive(output, attempt)
    with pytest.raises(
        EvaluationQwenConfirmFixtureSensitivityError,
        match="rag87_output_already_exists",
    ):
        rag87_cli._write_model_exclusive(output, attempt)


def test_baseline_one_shot_can_establish_fixture_sensitivity() -> None:
    envelope, manifest = _fixture_and_manifest()
    inventory = _stable_inventory()
    generator = _SensitivityFakeGenerator(envelope, mode="established")

    result = run_rag87_diagnostic(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=generator,
    )

    assert generator.call_count == 36
    assert result.generation_count == 36
    assert result.pipeline_failure_count == 0
    assert result.validity_gate_passed is True
    assert result.sensitivity_gate_passed is True
    assert result.conclusion == "fixture_sensitivity_established"
    assert result.summary.majority_atomic_supported_fact_count == 10
    assert result.summary.majority_atomic_required_fact_recall == 0.416667
    assert result.summary.majority_complete_case_count == 2
    assert result.summary.majority_first_only_case_count == 6
    assert result.summary.first_only_with_majority_insufficiency_case_count == 6
    assert result.summary.first_minus_second_majority_recall == 0.5
    assert result.summary.repeat_atomic_required_fact_recall_range == 0.0
    assert result.follow_on_two_pass_authorized is True
    assert result.raw_content_persisted is False


def test_ceiling_baseline_is_formally_not_sensitive_without_replacement() -> None:
    envelope, manifest = _fixture_and_manifest()
    inventory = _stable_inventory()

    result = run_rag87_diagnostic(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_SensitivityFakeGenerator(envelope, mode="ceiling"),
    )

    assert result.validity_gate_passed is True
    assert result.sensitivity_gate_passed is False
    assert result.conclusion == "fixture_sensitivity_not_established"
    assert result.summary.majority_atomic_required_fact_recall == 1.0
    assert result.sensitivity_checks.majority_recall_ceiling_passed is False
    assert "rag87_majority_recall_above_ceiling" in result.reason_codes
    assert result.case_exclusion_count == 0
    assert result.case_replacement_count == 0
    assert result.follow_on_two_pass_authorized is False


def test_exact_target_drift_is_inconclusive_and_blocks_follow_on() -> None:
    envelope, manifest = _fixture_and_manifest()
    pre = _stable_inventory()
    post = pre.model_copy(update={"target_entry_fingerprint": "b" * 64})

    result = run_rag87_diagnostic(
        manifest,
        envelope,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=pre,
        post_lm_inventory_provider=lambda: post,
        generator=_SensitivityFakeGenerator(envelope, mode="established"),
    )

    assert result.validity_gate_passed is False
    assert result.sensitivity_gate_passed is False
    assert result.conclusion == "inconclusive"
    assert result.reason_codes == ("rag87_target_entry_drift",)
    assert result.follow_on_two_pass_authorized is False


def test_attempt_state_binds_one_shot_and_rejects_repeat_replacement() -> None:
    _, manifest = _fixture_and_manifest()
    attempt = build_rag87_attempt_state(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )

    assert attempt.status == "started"
    assert attempt.expected_generation_count == 36
    assert attempt.repeat_replacement_or_rerun_allowed is False
    assert attempt.raw_content_persisted is False


def _fixture_and_manifest() -> tuple[
    Rag87PrivateFixtureEnvelope,
    Rag87ExperimentManifest,
]:
    envelope = build_rag87_private_fixture(TEST_PRIVATE_ENTROPY)
    private_bytes = canonical_json_bytes(envelope)
    manifest = build_rag87_experiment_manifest(
        envelope,
        private_input_sha256=_sha256_bytes(private_bytes),
    )
    return envelope, manifest


def _stable_inventory() -> Rag86LMInventorySummary:
    return Rag86LMInventorySummary(
        available=True,
        full_inventory_fingerprint="a" * 64,
        model_count=6,
        loaded_instance_count=1,
        target_model_id_fingerprint=_sha256("qwen/qwen3.5-9b"),
        target_entry_fingerprint="c" * 64,
        target_loaded_instance_count=1,
        target_loaded_context_length=12_312,
    )


class _SensitivityFakeGenerator(AnswerGenerator):
    def __init__(self, envelope: Rag87PrivateFixtureEnvelope, *, mode: str) -> None:
        self.fact_by_source = {
            document.source_key: document.facts[0].statement
            for document in envelope.dataset.corpus_documents
        }
        self.mode = mode
        self.call_count = 0

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.call_count += 1
        case_ordinal = int(request.context_items[0].source_label.split("-")[2])
        facts = {
            item.local_citation_id: self.fact_by_source[item.source_label]
            for item in request.context_items
        }
        if self.mode == "ceiling" or case_ordinal <= 2:
            content = f"{facts[1]} [1] {facts[2]} [2]"
        elif case_ordinal <= 8:
            content = f"{facts[1]} [1] insufficient evidence for the second record"
        else:
            content = "insufficient evidence"
        return GenerationResult(content=content, usage=None)


def _find_forbidden_key(value: object, fragments: tuple[str, ...]) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in fragments:
                return True
            if _find_forbidden_key(nested, fragments):
                return True
    elif isinstance(value, list):
        return any(_find_forbidden_key(item, fragments) for item in value)
    return False
