from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import app.scripts.run_evaluation_qwen_context_position as rag85_cli
import app.services.evaluation_qwen_context_position_service as rag85_service
from app.evaluation.qwen_multifact_confirm import build_qwen_multifact_confirm_manifest
from app.rag.generation import AnswerGenerator, GenerationRequest, GenerationResult
from app.services.evaluation_qwen_context_position_service import (
    EvaluationQwenContextPositionError,
    Rag85LMInventorySummary,
    build_rag85_attempt_state,
    build_rag85_experiment_manifest,
    load_frozen_rag85_experiment_manifest,
    run_rag85_diagnostic,
)

LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag85_qwen_context_position_lock.json"
)
PRELIVE_COMMIT = "1" * 40


def test_frozen_manifest_binds_single_position_coordinate_and_non_gold_scope() -> None:
    manifest = load_frozen_rag85_experiment_manifest(LOCK_PATH)

    assert manifest.stacked_base_commit == "b2b9512f3f5e9ac56241660271000cb098f3aaee"
    assert manifest.dataset.source_fixture == "rag84_qwen_multifact_confirm_v1"
    assert manifest.dataset.case_count == 14
    assert manifest.dataset.language_ja_count == 7
    assert manifest.dataset.language_en_count == 7
    assert manifest.dataset.required_fact_count == 28
    assert manifest.dataset.context_chunk_count_per_condition == 8
    assert manifest.dataset.fixed_distractor_chunk_count_per_condition == 6
    assert manifest.generation.generation_prompt_profile == "baseline"
    assert manifest.generation.resolved_generation_model == "qwen/qwen3.5-9b"
    assert manifest.generation.generation_temperature == 0.0
    assert manifest.generation.reasoning_enabled is False
    assert manifest.generation.expected_generation_count == 126
    assert manifest.generation.latin_rotation == (
        ("front", "middle", "end"),
        ("middle", "end", "front"),
        ("end", "front", "middle"),
    )
    assert manifest.decision_rule.paired_bootstrap_resamples == 10_000
    assert manifest.decision_rule.paired_bootstrap_seed == 85_085
    assert manifest.decision_rule.minimum_absolute_recall_delta == 0.15
    assert manifest.decision_rule.holm_alpha == 0.05
    assert manifest.decision_rule.reference_drift_is_exclusion_rule is False
    assert manifest.gold_v2_access_allowed is False
    assert manifest.case_exclusion_replacement_or_extra_repeat_allowed is False


def test_each_case_uses_same_eight_chunk_set_and_only_moves_required_block() -> None:
    fixture = build_qwen_multifact_confirm_manifest()
    documents = {document.source_key: document for document in fixture.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(fixture.corpus_documents, key=lambda item: item.source_key), start=1
        )
    }

    for case_ordinal, case in enumerate(rag85_service._selected_cases(), start=1):
        materials = {
            position: rag85_service._build_position_material(
                case,
                case_ordinal=case_ordinal,
                position=position,
                documents=documents,
                document_ids=document_ids,
            )
            for position in rag85_service._POSITIONS
        }
        rag85_service._validate_position_materials(materials)
        set_hashes = {
            rag85_service._material_chunk_set_hash(material) for material in materials.values()
        }
        assert len(set_hashes) == 1
        assert tuple(
            index
            for index, item in enumerate(materials["front"].context_items, start=1)
            if item.local_citation_id in {1, 2}
        ) == (1, 2)
        assert tuple(
            index
            for index, item in enumerate(materials["middle"].context_items, start=1)
            if item.local_citation_id in {1, 2}
        ) == (4, 5)
        assert tuple(
            index
            for index, item in enumerate(materials["end"].context_items, start=1)
            if item.local_citation_id in {1, 2}
        ) == (7, 8)
        assert all(
            sum(len(item.text) for item in material.context_items) <= 6000
            for material in materials.values()
        )


def test_negative_control_rejects_chunk_text_or_citation_binding_drift() -> None:
    fixture = build_qwen_multifact_confirm_manifest()
    documents = {document.source_key: document for document in fixture.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(fixture.corpus_documents, key=lambda item: item.source_key), start=1
        )
    }
    case = rag85_service._selected_cases()[0]
    materials = {
        position: rag85_service._build_position_material(
            case,
            case_ordinal=1,
            position=position,
            documents=documents,
            document_ids=document_ids,
        )
        for position in rag85_service._POSITIONS
    }
    middle = materials["middle"]
    changed_item = replace(middle.context_items[0], text=middle.context_items[0].text + " drift")
    materials["middle"] = replace(
        middle,
        context_items=(changed_item, *middle.context_items[1:]),
    )

    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_citation_source_binding_drift",
    ):
        rag85_service._validate_position_materials(materials)

    materials["middle"] = middle
    changed_citation = replace(middle.context_items[0], local_citation_id=99)
    materials["middle"] = replace(
        middle,
        context_items=(changed_citation, *middle.context_items[1:]),
    )
    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_context_citation_id_drift",
    ):
        rag85_service._validate_position_materials(materials)


def test_manifest_hash_drift_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    payload["source_context_fingerprint"] = "0" * 64
    drifted = tmp_path / "drifted.json"
    drifted.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_manifest_runtime_drift",
    ):
        load_frozen_rag85_experiment_manifest(drifted)


def test_deterministic_paired_bootstrap_sign_flip_and_holm() -> None:
    differences = (0.5,) * 14

    assert rag85_service._paired_bootstrap_ci(differences) == (0.5, 0.5)
    exact_p = 2 / (2**14)
    assert rag85_service._exact_sign_flip_p_value(differences) == exact_p
    assert rag85_service._holm_adjust((exact_p, exact_p, exact_p)) == (
        3 * exact_p,
        3 * exact_p,
        3 * exact_p,
    )
    assert rag85_service._exact_sign_flip_p_value((0.0,) * 14) == 1.0


def test_one_shot_run_applies_latin_rotation_primary_gate_and_raw_free_output() -> None:
    manifest = build_rag85_experiment_manifest()
    inventory = _stable_inventory()
    generator = _PositionSensitiveFakeGenerator(mode="position_sensitive")

    result = run_rag85_diagnostic(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=generator,
    )

    assert len(generator.call_orders) == 126
    assert generator.call_orders[:3] == (
        (1, 2, 3, 4, 5, 6, 7, 8),
        (3, 4, 5, 1, 2, 6, 7, 8),
        (3, 4, 5, 6, 7, 8, 1, 2),
    )
    assert generator.call_orders[42:45] == (
        (3, 4, 5, 1, 2, 6, 7, 8),
        (3, 4, 5, 6, 7, 8, 1, 2),
        (1, 2, 3, 4, 5, 6, 7, 8),
    )
    assert result.generation_count == 126
    assert result.pipeline_failure_count == 0
    assert result.validity_gate_passed is True
    assert result.position_dependence_gate_passed is True
    assert result.conclusion == "position_dependence_detected"
    summaries = {item.condition: item for item in result.conditions}
    assert summaries["front"].majority_atomic_required_fact_recall == 1.0
    assert summaries["middle"].majority_atomic_required_fact_recall == 0.5
    assert summaries["end"].majority_atomic_required_fact_recall == 0.0
    assert all(item.position_dependence_gate_passed for item in result.pairwise_comparisons)
    assert result.reference_used_for_exclusion_or_replacement is False

    payload = json.loads(result.model_dump_json())
    forbidden_key_fragments = (
        "question",
        "answer_text",
        "context_items",
        "required_fact",
        "required_facts",
        "expected_answer",
        "source_text",
        "chunk_text",
    )
    assert not _find_forbidden_key(payload, forbidden_key_fragments)
    assert result.raw_content_persisted is False


def test_valid_null_result_is_no_detectable_dependence_not_failure() -> None:
    manifest = build_rag85_experiment_manifest()
    inventory = _stable_inventory()

    result = run_rag85_diagnostic(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_PositionSensitiveFakeGenerator(mode="flat"),
    )

    assert result.validity_gate_passed is True
    assert result.position_dependence_gate_passed is False
    assert result.conclusion == "no_detectable_position_dependence"
    assert all(item.recall_delta == 0.0 for item in result.pairwise_comparisons)
    assert all(item.holm_adjusted_p_value == 1.0 for item in result.pairwise_comparisons)


def test_inventory_drift_is_inconclusive_without_exclusion() -> None:
    manifest = build_rag85_experiment_manifest()
    pre = _stable_inventory()
    post = pre.model_copy(update={"inventory_fingerprint": "b" * 64})

    result = run_rag85_diagnostic(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=pre,
        post_lm_inventory_provider=lambda: post,
        generator=_PositionSensitiveFakeGenerator(mode="flat"),
    )

    assert result.validity_gate_passed is False
    assert result.conclusion == "inconclusive"
    assert result.reason_codes == ("rag85_lm_inventory_drift",)
    assert result.case_exclusion_count == 0
    assert result.case_replacement_count == 0


def test_attempt_state_requires_available_exact_target_inventory() -> None:
    manifest = build_rag85_experiment_manifest()
    attempt = build_rag85_attempt_state(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )

    assert attempt.expected_generation_count == 126
    assert attempt.repeat_or_replacement_allowed is False
    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_target_model_not_exactly_once_loaded",
    ):
        build_rag85_attempt_state(
            manifest,
            prelive_commit_sha=PRELIVE_COMMIT,
            pre_lm_inventory=_stable_inventory().model_copy(
                update={"target_loaded_instance_count": 0}
            ),
        )

    with pytest.raises(ValueError, match="rag85_lm_inventory_shape_invalid"):
        Rag85LMInventorySummary(available=False, model_count=1)


def test_cli_inventory_summary_is_hash_only_and_exact_model_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "models": [
                    {"key": "qwen/qwen3.5-9b", "loaded_instances": [{"id": "opaque"}]},
                    {"key": "another/model", "loaded_instances": []},
                ]
            }

    monkeypatch.setattr(rag85_cli.httpx, "get", lambda *_args, **_kwargs: _Response())

    inventory = rag85_cli._fetch_lm_inventory()

    assert inventory.available is True
    assert inventory.model_count == 2
    assert inventory.loaded_instance_count == 1
    assert inventory.target_loaded_instance_count == 1
    assert inventory.inventory_fingerprint is not None
    assert "qwen" not in inventory.model_dump_json()


def test_cli_rejects_repository_local_or_existing_one_shot_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rag85_cli, "_path_inside_git_checkout", lambda _path: True)
    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_repository_output_rejected",
    ):
        rag85_cli._validate_output_path(LOCK_PATH.parent / "result.json")

    monkeypatch.setattr(rag85_cli, "_path_inside_git_checkout", lambda _path: False)
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_output_already_exists",
    ):
        rag85_cli._validate_output_path(existing)


def test_cli_exclusive_writer_never_overwrites_attempt_marker(tmp_path: Path) -> None:
    marker = tmp_path / "attempt.json"
    attempt = build_rag85_attempt_state(
        build_rag85_experiment_manifest(),
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )

    rag85_cli._write_model_exclusive(marker, attempt)

    assert json.loads(marker.read_text(encoding="utf-8"))["status"] == "started"
    with pytest.raises(
        EvaluationQwenContextPositionError,
        match="rag85_output_already_exists",
    ):
        rag85_cli._write_model_exclusive(marker, attempt)


def _stable_inventory() -> Rag85LMInventorySummary:
    return Rag85LMInventorySummary(
        available=True,
        inventory_fingerprint="a" * 64,
        model_count=6,
        loaded_instance_count=1,
        target_loaded_instance_count=1,
    )


class _PositionSensitiveFakeGenerator(AnswerGenerator):
    def __init__(self, *, mode: str) -> None:
        fixture = build_qwen_multifact_confirm_manifest()
        self.fact_by_source = {
            document.source_key: document.facts[0].statement
            for document in fixture.corpus_documents
        }
        self.mode = mode
        self.call_orders: tuple[tuple[int, ...], ...] = ()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        order = tuple(item.local_citation_id or 0 for item in request.context_items)
        self.call_orders = (*self.call_orders, order)
        required = {
            item.local_citation_id: self.fact_by_source[item.source_label]
            for item in request.context_items
            if item.local_citation_id in {1, 2}
        }
        if self.mode == "flat" or order.index(1) == 3:
            content = f"{required[1]} [1]"
        elif order.index(1) == 0:
            content = f"{required[1]} [1] {required[2]} [2]"
        else:
            content = "Position-control spacer [3]."
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
