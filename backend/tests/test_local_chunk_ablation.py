from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.evaluation.local_chunk_ablation import (
    build_chunk_ablation_inputs,
    build_profile_chunks,
    chunk_ablation_profiles,
    collection_name_for_profile,
)
from app.ingest.chunking import ChunkingConfig
from app.scripts.run_local_chunk_ablation import (
    ChunkAblationRunError,
    _select_finalists,
    _validate_local_settings,
)
from app.scripts.run_local_chunk_end_to_end import (
    _promotion_decision,
    _recommend_profile,
    _select_repeat_candidates,
)


def test_chunk_ablation_profiles_keep_the_production_default_as_c0() -> None:
    profiles = chunk_ablation_profiles()

    assert [profile.profile_id for profile in profiles] == ["C0", "C1", "C2", "C3", "C4"]
    assert profiles[0].config == ChunkingConfig()
    assert profiles[1].config.tokenizer_profile == "japanese_aware_v1"
    assert profiles[4].config.boundary_profile == "structure_v1"


def test_chunk_ablation_manifest_matches_implemented_profiles() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "experiments"
        / "manifests"
        / "local_rag_chunk_ablation_dev_v1.example.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "rag60.chunk_ablation.v1"
    assert payload["baseline_profile"] == "C0"
    assert payload["holdout_opened"] is False
    assert [profile["profile_id"] for profile in payload["profiles"]] == [
        profile.profile_id for profile in chunk_ablation_profiles()
    ]
    assert payload["safety"]["default_ingest_profile_changed"] is False
    assert payload["safety"]["raw_content_in_artifacts"] is False


def test_chunk_stress_fixture_is_dev_only_and_geometry_is_discriminating() -> None:
    dataset_fingerprint, stress_fingerprint, sources, cases = build_chunk_ablation_inputs()
    profiles = chunk_ablation_profiles()
    baseline_records, baseline_geometry = build_profile_chunks(profiles[0], sources)
    japanese_records, japanese_geometry = build_profile_chunks(profiles[1], sources)

    assert len(dataset_fingerprint) == 64
    assert len(stress_fingerprint) == 64
    assert len(sources) == 40
    assert len(cases) == 40
    assert baseline_geometry.corpus_fingerprint != japanese_geometry.corpus_fingerprint
    assert baseline_geometry.chunk_count != japanese_geometry.chunk_count
    assert baseline_geometry.source_count == japanese_geometry.source_count == 40
    assert all(record.source_key.startswith("local_dev_source_") for record in baseline_records)
    assert all(record.source_key.startswith("local_dev_source_") for record in japanese_records)


def test_chunk_ablation_cases_include_transient_judge_contract() -> None:
    _, _, _, cases = build_chunk_ablation_inputs()

    answerable = next(case for case in cases if case.answerable)
    unanswerable = next(case for case in cases if not case.answerable)

    assert answerable.case_id.startswith("local_dev_answerable_")
    assert answerable.required_citation is True
    assert answerable.required_facts
    assert "answerable" in answerable.tags
    assert unanswerable.required_citation is False
    assert unanswerable.required_facts == ()
    assert unanswerable.forbidden_claims


def test_collection_name_isolated_by_chunk_profile_and_embedding() -> None:
    common = {
        "base_name": "document_chunks",
        "dataset_fingerprint": "a" * 64,
        "corpus_fingerprint": "b" * 64,
        "embedding_model": "text-embedding-qwen3-embedding-4b",
        "embedding_dimension": 2560,
    }

    baseline = collection_name_for_profile(
        **common,
        profile_fingerprint="c" * 64,
    )
    candidate = collection_name_for_profile(
        **common,
        profile_fingerprint="d" * 64,
    )
    different_embedding = collection_name_for_profile(
        **{
            **common,
            "embedding_model": "different-embedding",
        },
        profile_fingerprint="c" * 64,
    )

    assert baseline != candidate
    assert baseline != different_embedding
    assert baseline.startswith("document_chunks_rag60_")


def test_finalists_must_beat_c0_without_guardrail_regression() -> None:
    common = {
        "pipeline_failure_count": 0,
        "dense_recall_at_k": 1.0,
        "retrieval_p95_latency_ms": 100.0,
        "case_metrics": [],
    }
    results = [
        {
            **common,
            "profile_id": "C0",
            "recall_at_n": 0.75,
            "fact_recall_at_n": 0.70,
            "mrr": 0.60,
            "no_context_rate": 0.0,
        },
        {
            **common,
            "profile_id": "C1",
            "recall_at_n": 0.80,
            "fact_recall_at_n": 0.75,
            "mrr": 0.65,
            "no_context_rate": 0.0,
        },
        {
            **common,
            "profile_id": "C2",
            "recall_at_n": 0.85,
            "fact_recall_at_n": 0.80,
            "mrr": 0.70,
            "no_context_rate": 0.0,
        },
        {
            **common,
            "profile_id": "C3",
            "recall_at_n": 0.90,
            "fact_recall_at_n": 0.90,
            "mrr": 0.80,
            "no_context_rate": 0.05,
        },
        {
            **common,
            "profile_id": "C4",
            "recall_at_n": 0.75,
            "fact_recall_at_n": 0.70,
            "mrr": 0.60,
            "no_context_rate": 0.0,
        },
    ]

    assert _select_finalists(results) == ["C2", "C1"]
    assert "question" not in json.dumps(results)


def test_end_to_end_repeat_candidate_requires_quality_and_guardrails() -> None:
    baseline = _end_to_end_result("C0", pass_rate=0.75)
    better = _end_to_end_result("C3", pass_rate=0.85)
    regressed = _end_to_end_result(
        "C1",
        pass_rate=0.90,
        unanswerable_rate=0.70,
    )

    assert _select_repeat_candidates([baseline, better, regressed]) == ["C3"]
    assert _recommend_profile([baseline, better], repeats=1) is None
    assert _recommend_profile([baseline, better], repeats=3) == "C3"
    assert (
        _promotion_decision(
            repeats=1,
            repeat_candidates=[],
            recommendation=None,
        )
        == "not_promoted_no_candidate_passed_single_repeat_gate"
    )
    assert (
        _promotion_decision(
            repeats=3,
            repeat_candidates=["C3"],
            recommendation="C3",
        )
        == "not_promoted_requires_manual_calibration"
    )


def test_local_settings_allow_known_docker_qdrant_but_reject_external_hosts() -> None:
    _validate_local_settings(
        SimpleNamespace(
            app_env="local",
            lmstudio_base_url="http://host.docker.internal:1234/v1",
            qdrant_url="http://ragproject-qdrant-1:6333",
        )
    )

    with pytest.raises(ChunkAblationRunError, match="qdrant_must_be_local"):
        _validate_local_settings(
            SimpleNamespace(
                app_env="local",
                lmstudio_base_url="http://host.docker.internal:1234/v1",
                qdrant_url="https://external.example.invalid",
            )
        )


def _end_to_end_result(
    profile_id: str,
    *,
    pass_rate: float,
    unanswerable_rate: float = 0.75,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "pipeline_failure_count": 0,
        "auxiliary_pass_rate_conservative": pass_rate,
        "unanswerable_auxiliary_pass_rate_conservative": unanswerable_rate,
        "prompt_injection_auxiliary_pass_rate_conservative": 0.75,
        "required_facts_supported_rate": 0.75,
        "citation_support_rate": 1.0,
        "end_to_end_p95_latency_ms": 100.0,
    }
