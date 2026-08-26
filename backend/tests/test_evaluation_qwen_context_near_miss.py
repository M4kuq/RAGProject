from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

import app.scripts.run_evaluation_qwen_context_near_miss as rag86_cli
import app.services.evaluation_qwen_context_near_miss_service as rag86_service
from app.evaluation.qwen_multifact_confirm import build_qwen_multifact_confirm_manifest
from app.rag.generation import AnswerGenerator, GenerationRequest, GenerationResult
from app.schemas.evaluation_datasets_v2 import (
    EvaluationCaseV2Spec,
    EvaluationCorpusDocumentSpec,
    EvaluationDatasetManifestV2,
)
from app.services.evaluation_qwen_context_near_miss_service import (
    EvaluationQwenContextNearMissError,
    Rag86LMInventorySummary,
    build_rag86_attempt_state,
    build_rag86_experiment_manifest,
    load_frozen_rag86_experiment_manifest,
    run_rag86_diagnostic,
)

LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "fixtures"
    / "rag86_qwen_context_near_miss_lock.json"
)
PRELIVE_COMMIT = "1" * 40


def test_frozen_manifest_binds_exact_single_coordinate_and_non_gold_scope() -> None:
    manifest = load_frozen_rag86_experiment_manifest(LOCK_PATH)

    assert manifest.stacked_base_commit == "f89e248d2294be9b41150f4ab9b8879e6329e536"
    assert manifest.dataset.source_fixture == "rag84_qwen_multifact_confirm_v1"
    assert manifest.dataset.case_count == 14
    assert manifest.dataset.language_ja_count == 7
    assert manifest.dataset.language_en_count == 7
    assert manifest.dataset.required_fact_count == 28
    assert manifest.dataset.context_chunk_count_per_condition == 8
    assert manifest.dataset.fixed_distractor_chunk_count_per_condition == 6
    assert manifest.dataset.treatment_spacer_count == 1
    assert manifest.generation.resolved_generation_model == "qwen/qwen3.5-9b"
    assert manifest.generation.generation_prompt_profile == "baseline"
    assert manifest.generation.generation_temperature == 0.0
    assert manifest.generation.reasoning_enabled is False
    assert manifest.generation.lmstudio_loaded_context_length == 12_312
    assert manifest.generation.expected_generation_count == 84
    assert manifest.generation.clean_first_pair_count == 21
    assert manifest.generation.near_miss_first_pair_count == 21
    assert manifest.generation.required_chunk_ordinals_frozen == (4, 5)
    assert manifest.generation.treatment_citation_id == 5
    assert manifest.generation.treatment_chunk_ordinal == 3
    assert manifest.generation.treatment_clean_length == 141
    assert manifest.generation.treatment_near_miss_length == 159
    assert manifest.generation.treatment_near_miss_to_clean_length_ratio == 1.12766
    assert manifest.decision_rule.primary_delta == "near_miss_minus_clean"
    assert manifest.decision_rule.paired_bootstrap_resamples == 10_000
    assert manifest.decision_rule.paired_bootstrap_seed == 86_086
    assert manifest.decision_rule.minimum_absolute_recall_delta == 0.15
    assert manifest.decision_rule.exact_sign_flip_alpha == 0.05
    assert manifest.decision_rule.full_non_target_inventory_match_required is False
    assert manifest.gold_v2_access_allowed is False
    assert manifest.case_exclusion_replacement_or_extra_repeat_allowed is False
    assert len({case.near_miss_identifier_fingerprint for case in manifest.dataset.cases}) == 14
    assert all(case.clean_length == 141 for case in manifest.dataset.cases)
    assert all(case.near_miss_length == 159 for case in manifest.dataset.cases)
    assert all(case.near_miss_to_clean_length_ratio == 1.12766 for case in manifest.dataset.cases)


def test_each_case_changes_only_target_text_and_citation_snippet() -> None:
    fixture, documents, document_ids = _fixture_material_inputs()
    del fixture

    for case_ordinal, case in enumerate(rag86_service._selected_cases(), start=1):
        materials = _materials_for_case(
            case,
            case_ordinal=case_ordinal,
            documents=documents,
            document_ids=document_ids,
        )
        rag86_service._validate_condition_materials(materials, case=case)
        clean = materials["clean"]
        near = materials["near_miss"]
        assert rag86_service._chunk_identity_hash(clean) == rag86_service._chunk_identity_hash(near)
        assert tuple(item.local_citation_id for item in clean.context_items) == (
            3,
            4,
            5,
            1,
            2,
            6,
            7,
            8,
        )
        assert tuple(
            index
            for index, item in enumerate(clean.context_items, start=1)
            if item.local_citation_id in {1, 2}
        ) == (4, 5)
        assert tuple(
            index
            for index, (left, right) in enumerate(
                zip(clean.context_items, near.context_items, strict=True), start=1
            )
            if left.text != right.text
        ) == (3,)
        assert tuple(
            source.local_citation_id
            for source, other in zip(clean.citation_sources, near.citation_sources, strict=True)
            if source.snippet != other.snippet
        ) == (5,)
        clean_target = rag86_service._context_item_by_citation(clean, 5)
        near_target = rag86_service._context_item_by_citation(near, 5)
        assert len(clean_target.text) == 141
        assert len(near_target.text) == 159
        assert clean_target.text.split(". ", 1)[0] == near_target.text.split(". ", 1)[0]


def test_negative_controls_reject_second_coordinate_length_and_collision_drift() -> None:
    _, documents, document_ids = _fixture_material_inputs()
    case = rag86_service._selected_cases()[0]
    materials = _materials_for_case(
        case,
        case_ordinal=1,
        documents=documents,
        document_ids=document_ids,
    )
    near = materials["near_miss"]
    changed_items = list(near.context_items)
    changed_items[0] = replace(changed_items[0], text=f"{changed_items[0].text} drift")
    drifted = replace(near, context_items=tuple(changed_items))
    with pytest.raises(EvaluationQwenContextNearMissError):
        rag86_service._validate_condition_materials(
            {"clean": materials["clean"], "near_miss": drifted},
            case=case,
        )

    target_items = tuple(
        replace(item, text=f"{item.text}x") if item.local_citation_id == 5 else item
        for item in near.context_items
    )
    target_sources = tuple(
        replace(source, snippet=f"{source.snippet}x") if source.local_citation_id == 5 else source
        for source in near.citation_sources
    )
    length_drifted = replace(
        near,
        context_items=target_items,
        citation_sources=target_sources,
        context_sequence_hash=rag86_service._context_sequence_hash(target_items),
    )
    with pytest.raises(
        EvaluationQwenContextNearMissError,
        match="rag86_treatment_length_drift",
    ):
        rag86_service._validate_condition_materials(
            {"clean": materials["clean"], "near_miss": length_drifted},
            case=case,
        )

    collision_case = case.model_copy(update={"question": f"{case.question} NM86-01"})
    with pytest.raises(
        EvaluationQwenContextNearMissError,
        match="rag86_near_miss_identifier_collision",
    ):
        rag86_service._validate_condition_materials(materials, case=collision_case)


def test_execution_order_is_balanced_by_repeat() -> None:
    for repeat in range(1, 4):
        orders = tuple(
            rag86_service._condition_order(repeat=repeat, case_ordinal=case_ordinal)
            for case_ordinal in range(1, 15)
        )
        assert sum(order[0] == "clean" for order in orders) == 7
        assert sum(order[0] == "near_miss" for order in orders) == 7


def test_deterministic_paired_bootstrap_and_exact_sign_flip() -> None:
    differences = (-0.5,) * 14

    assert rag86_service._paired_bootstrap_ci(differences) == (-0.5, -0.5)
    assert rag86_service._exact_sign_flip_p_value(differences) == 2 / (2**14)
    assert rag86_service._exact_sign_flip_p_value((0.0,) * 14) == 1.0


def test_one_shot_run_applies_primary_gate_and_raw_free_output() -> None:
    manifest = build_rag86_experiment_manifest()
    inventory = _stable_inventory()
    generator = _NearMissSensitiveFakeGenerator(mode="effect")

    result = run_rag86_diagnostic(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=generator,
    )

    assert len(generator.near_miss_calls) == 84
    assert sum(generator.near_miss_calls) == 42
    assert generator.near_miss_calls[:4] == (False, True, True, False)
    assert result.generation_count == 84
    assert result.pipeline_failure_count == 0
    assert result.validity_gate_passed is True
    assert result.causal_effect_gate_passed is True
    assert result.conclusion == "near_miss_causal_effect_detected"
    assert result.primary_comparison.clean_majority_recall == 1.0
    assert result.primary_comparison.near_miss_majority_recall == 0.5
    assert result.primary_comparison.near_miss_minus_clean_recall_delta == -0.5
    assert result.primary_comparison.paired_bootstrap_ci_lower == -0.5
    assert result.primary_comparison.paired_bootstrap_ci_upper == -0.5
    assert result.primary_comparison.exact_sign_flip_p_value == 2 / (2**14)

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


def test_secondary_near_miss_contamination_and_adoption_are_not_primary_inputs() -> None:
    inventory = _stable_inventory()
    result = run_rag86_diagnostic(
        build_rag86_experiment_manifest(),
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_NearMissSensitiveFakeGenerator(mode="contamination"),
    )

    summaries = {item.condition: item for item in result.conditions}
    assert summaries["clean"].near_miss_contamination_rate == 0.0
    assert summaries["near_miss"].near_miss_contamination_rate == 1.0
    assert summaries["near_miss"].near_miss_adoption_rate == 1.0
    assert result.secondary_metrics_are_primary_gate_inputs is False
    assert result.causal_effect_gate_passed is False


def test_valid_null_result_is_no_detectable_effect() -> None:
    inventory = _stable_inventory()
    result = run_rag86_diagnostic(
        build_rag86_experiment_manifest(),
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=inventory,
        post_lm_inventory_provider=lambda: inventory,
        generator=_NearMissSensitiveFakeGenerator(mode="flat"),
    )

    assert result.validity_gate_passed is True
    assert result.causal_effect_gate_passed is False
    assert result.conclusion == "no_detectable_near_miss_causal_effect"
    assert result.primary_comparison.near_miss_minus_clean_recall_delta == 0.0
    assert result.primary_comparison.exact_sign_flip_p_value == 1.0


def test_non_target_inventory_drift_is_recorded_only() -> None:
    pre = _stable_inventory()
    post = pre.model_copy(update={"full_inventory_fingerprint": "b" * 64})
    result = run_rag86_diagnostic(
        build_rag86_experiment_manifest(),
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=pre,
        post_lm_inventory_provider=lambda: post,
        generator=_NearMissSensitiveFakeGenerator(mode="flat"),
    )

    assert result.validity_gate_passed is True
    assert result.exact_target_stable is True
    assert result.full_lm_inventory_stable is False
    assert result.non_target_inventory_drift_observed is True
    assert result.conclusion == "no_detectable_near_miss_causal_effect"


def test_exact_target_entry_drift_is_inconclusive() -> None:
    pre = _stable_inventory()
    post = pre.model_copy(update={"target_entry_fingerprint": "b" * 64})
    result = run_rag86_diagnostic(
        build_rag86_experiment_manifest(),
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=pre,
        post_lm_inventory_provider=lambda: post,
        generator=_NearMissSensitiveFakeGenerator(mode="flat"),
    )

    assert result.validity_gate_passed is False
    assert result.exact_target_stable is False
    assert result.conclusion == "inconclusive"
    assert result.reason_codes == ("rag86_target_entry_drift",)
    assert result.case_exclusion_count == 0
    assert result.case_replacement_count == 0


def test_attempt_state_requires_available_exact_target_inventory() -> None:
    manifest = build_rag86_experiment_manifest()
    attempt = build_rag86_attempt_state(
        manifest,
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )

    assert attempt.expected_generation_count == 84
    assert attempt.repeat_or_replacement_allowed is False
    with pytest.raises(
        EvaluationQwenContextNearMissError,
        match="rag86_target_model_not_exactly_once_loaded",
    ):
        build_rag86_attempt_state(
            manifest,
            prelive_commit_sha=PRELIVE_COMMIT,
            pre_lm_inventory=_stable_inventory().model_copy(
                update={
                    "target_entry_fingerprint": None,
                    "target_loaded_instance_count": 0,
                    "target_loaded_context_length": None,
                }
            ),
        )

    with pytest.raises(ValueError, match="rag86_lm_inventory_shape_invalid"):
        Rag86LMInventorySummary(available=False, model_count=1)


def test_cli_inventory_summary_is_hash_only_and_exact_target_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {
                        "id": "qwen/qwen3.5-9b",
                        "state": "loaded",
                        "loaded_context_length": 12_312,
                    },
                    {
                        "id": "another/model",
                        "state": "not-loaded",
                        "loaded_context_length": None,
                    },
                ]
            }

    monkeypatch.setattr(rag86_cli.httpx, "get", lambda *_args, **_kwargs: _Response())
    inventory = rag86_cli._fetch_lm_inventory()

    assert inventory.available is True
    assert inventory.model_count == 2
    assert inventory.loaded_instance_count == 1
    assert inventory.target_loaded_instance_count == 1
    assert inventory.target_loaded_context_length == 12_312
    assert inventory.full_inventory_fingerprint is not None
    assert inventory.target_entry_fingerprint is not None
    assert "qwen" not in inventory.model_dump_json()


def test_cli_uses_one_shot_safe_git_and_output_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert rag86_cli._git_args("rev-parse", "HEAD")[:3] == [
        "git",
        "-c",
        f"safe.directory={rag86_cli._REPOSITORY_ROOT}",
    ]
    monkeypatch.setattr(rag86_cli, "_path_inside_git_checkout", lambda _path: True)
    with pytest.raises(
        EvaluationQwenContextNearMissError,
        match="rag86_repository_output_rejected",
    ):
        rag86_cli._validate_output_path(LOCK_PATH.parent / "result.json")

    monkeypatch.setattr(rag86_cli, "_path_inside_git_checkout", lambda _path: False)
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(
        EvaluationQwenContextNearMissError,
        match="rag86_output_already_exists",
    ):
        rag86_cli._validate_output_path(existing)


def test_cli_exclusive_writer_never_overwrites_attempt_marker(tmp_path: Path) -> None:
    marker = tmp_path / "attempt.json"
    attempt = build_rag86_attempt_state(
        build_rag86_experiment_manifest(),
        prelive_commit_sha=PRELIVE_COMMIT,
        pre_lm_inventory=_stable_inventory(),
    )

    rag86_cli._write_model_exclusive(marker, attempt)

    assert json.loads(marker.read_text(encoding="utf-8"))["status"] == "started"
    with pytest.raises(
        EvaluationQwenContextNearMissError,
        match="rag86_output_already_exists",
    ):
        rag86_cli._write_model_exclusive(marker, attempt)


def _fixture_material_inputs() -> tuple[
    EvaluationDatasetManifestV2,
    dict[str, EvaluationCorpusDocumentSpec],
    dict[str, int],
]:
    fixture = build_qwen_multifact_confirm_manifest()
    documents = {document.source_key: document for document in fixture.corpus_documents}
    document_ids = {
        document.source_key: index
        for index, document in enumerate(
            sorted(fixture.corpus_documents, key=lambda item: item.source_key), start=1
        )
    }
    return fixture, documents, document_ids


def _materials_for_case(
    case: EvaluationCaseV2Spec,
    *,
    case_ordinal: int,
    documents: dict[str, EvaluationCorpusDocumentSpec],
    document_ids: dict[str, int],
) -> dict[rag86_service.Condition, rag86_service._ConditionMaterial]:
    return {
        condition: rag86_service._build_condition_material(
            case,
            case_ordinal=case_ordinal,
            condition=condition,
            documents=documents,
            document_ids=document_ids,
        )
        for condition in rag86_service._CONDITIONS
    }


def _stable_inventory() -> Rag86LMInventorySummary:
    return Rag86LMInventorySummary(
        available=True,
        full_inventory_fingerprint="a" * 64,
        model_count=6,
        loaded_instance_count=1,
        target_model_id_fingerprint=rag86_service._sha256("qwen/qwen3.5-9b"),
        target_entry_fingerprint="c" * 64,
        target_loaded_instance_count=1,
        target_loaded_context_length=12_312,
    )


class _NearMissSensitiveFakeGenerator(AnswerGenerator):
    def __init__(self, *, mode: str) -> None:
        fixture = build_qwen_multifact_confirm_manifest()
        self.fact_by_source = {
            document.source_key: document.facts[0].statement
            for document in fixture.corpus_documents
        }
        self.mode = mode
        self.near_miss_calls: tuple[bool, ...] = ()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        required = {
            item.local_citation_id: self.fact_by_source[item.source_label]
            for item in request.context_items
            if item.local_citation_id in {1, 2}
        }
        target = next(item for item in request.context_items if item.local_citation_id == 5)
        decoy_match = re.search(r"NM86-\d{2}", target.text, flags=re.IGNORECASE)
        is_near = decoy_match is not None
        self.near_miss_calls = (*self.near_miss_calls, is_near)
        if self.mode == "effect" and is_near:
            content = f"{required[1]} [1]"
        else:
            content = f"{required[1]} [1] {required[2]} [2]"
        if self.mode == "contamination" and decoy_match is not None:
            content = f"{content} {decoy_match.group(0)} [5]"
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
