from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.evaluation.local_accuracy_dev import (
    build_local_accuracy_dev_manifest,
    local_accuracy_dev_composition,
)
from app.evaluation.rag_service import runtime_evaluation_collection_name
from app.experiments.local_accuracy import (
    EndToEndResult,
    PromotionGateInput,
    RepeatCaseOutcome,
    RetrievalScreenResult,
    build_coordinate_search_candidates,
    evaluate_local_accuracy_promotion_gate,
    majority_vote_case_outcomes,
    select_end_to_end_winner,
    select_manual_review_case_ids,
    select_retrieval_finalists,
)
from app.experiments.reporting import redact_experiment_artifact
from app.experiments.schemas import ExperimentManifest, GenerationProfile
from app.ingest.embedding import probe_lmstudio_embedding_dimension
from app.repositories.evaluation_repository import EvaluationRepository
from app.schemas.evaluations import EvaluationRunCreateRequest
from app.services.evaluation_dataset_manifest_service import EvaluationDatasetManifestService
from app.services.evaluation_service import (
    _exact_mcnemar_p_value,
    _paired_bootstrap_confidence_interval,
)


def _v2_manifest() -> ExperimentManifest:
    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "experiments"
        / "manifests"
        / "local_rag_accuracy_v2.example.json"
    )
    return ExperimentManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_evaluation_backend_defaults_and_runtime_is_manual_only() -> None:
    request = EvaluationRunCreateRequest()
    assert request.evaluation_backend == "deterministic_db"
    assert request.repeat_number == 1

    with pytest.raises(ValidationError, match="manual trigger_type"):
        EvaluationRunCreateRequest(
            evaluation_backend="runtime_qdrant",
            trigger_type="ci",
        )


def test_runtime_collection_is_stable_and_isolates_embedding_profiles() -> None:
    def collection_name(
        *,
        corpus_fingerprint: str = "a" * 64,
        embedding_dimension: int = 2560,
    ) -> str:
        return runtime_evaluation_collection_name(
            base_collection_name="document_chunks",
            corpus_fingerprint=corpus_fingerprint,
            embedding_model="text-embedding-qwen3-embedding-4b",
            embedding_dimension=embedding_dimension,
        )

    first = collection_name()
    assert first == collection_name()
    assert first.startswith("document_chunks_eval_")
    assert first != collection_name(embedding_dimension=1024)
    assert first != collection_name(corpus_fingerprint="b" * 64)


def test_lmstudio_dimension_probe_uses_fixed_text_and_resolved_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "model": "text-embedding-qwen3-embedding-4b",
                "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
            }

    def fake_post(url: str, **kwargs: object) -> Response:
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("app.ingest.embedding.httpx.post", fake_post)
    probe = probe_lmstudio_embedding_dimension(
        Settings(
            app_env="test",
            embedding_provider="lmstudio",
            embedding_model="text-embedding-qwen3-embedding-4b",
        )
    )
    assert probe.dimension == 3
    assert probe.resolved_model == "text-embedding-qwen3-embedding-4b"
    assert captured["json"] == {
        "model": "text-embedding-qwen3-embedding-4b",
        "input": ["RAGProject embedding dimension probe."],
    }


def test_local_accuracy_dev_dataset_has_required_balance_and_isolated_ids() -> None:
    manifest = build_local_accuracy_dev_manifest()
    assert local_accuracy_dev_composition() == {
        "case_count": 40,
        "answerable": 24,
        "unanswerable": 16,
        "single_hop": 20,
        "multi_hop": 20,
        "language_ja": 20,
        "language_en": 20,
        "prompt_injection": 8,
    }
    assert all(
        document.source_key.startswith("local_dev_source_")
        for document in manifest.corpus_documents
    )
    assert all(
        fact.fact_id.startswith("local_dev_fact_")
        for document in manifest.corpus_documents
        for fact in document.facts
    )
    validation = EvaluationDatasetManifestService(EvaluationRepository()).validate(
        manifest=manifest
    )
    assert validation.composition.single_hop_count == 20
    assert validation.composition.multi_hop_count == 20


def test_manifest_v1_remains_compatible_and_v2_is_fixed_to_qwen_9b() -> None:
    v1 = ExperimentManifest.model_validate(
        {
            "schema_version": "phase2.experiment.v1",
            "experiment_name": "legacy",
            "dataset": "phase1_smoke",
            "embedding_models": [{"model_id": "sentence-transformers/all-MiniLM-L6-v2"}],
        }
    )
    assert v1.evaluation_backend == "deterministic_db"
    manifest = _v2_manifest()
    assert manifest.generation_profile is not None
    assert manifest.generation_profile.model == "qwen/qwen3.5-9b"
    assert manifest.generation_profile.planner_model == "qwen/qwen3.5-9b"
    assert manifest.generation_profile.judge_model == "qwen/qwen3.5-9b"
    assert manifest.generation_profile.temperature == 0.0
    assert manifest.generation_profile.retry_on_insufficient_evidence is None
    assert manifest.generation_profile.max_output_chars is None
    with pytest.raises(ValidationError, match="less than or equal to 20000"):
        GenerationProfile(max_output_chars=20_001)


def test_experiment_redaction_keeps_safe_metrics_but_removes_raw_answers() -> None:
    artifact = redact_experiment_artifact(
        {
            "answer_completeness": 0.8,
            "grounded_answer_pass_rate_calibrated": 0.76,
            "raw_answer": "do not persist this",
        }
    )
    assert artifact == {
        "answer_completeness": 0.8,
        "grounded_answer_pass_rate_calibrated": 0.76,
        "raw_answer": "[REDACTED]",
    }


def test_coordinate_search_and_staged_selection_are_bounded_and_deterministic() -> None:
    candidates = build_coordinate_search_candidates(_v2_manifest())
    assert len(candidates) == 15
    assert candidates[0].profile_id == "B1"
    assert len({candidate.candidate_id for candidate in candidates}) == 15

    finalists = select_retrieval_finalists(
        [
            RetrievalScreenResult("c", 0.8, 0.7, 0.1),
            RetrievalScreenResult("a", 0.9, 0.5, 0.2),
            RetrievalScreenResult("b", 0.8, 0.8, 0.3),
            RetrievalScreenResult("failed", 1.0, 1.0, 0.0, 1),
        ]
    )
    assert finalists == ["a", "b", "c"]
    assert (
        select_end_to_end_winner(
            [
                EndToEndResult("slow", 0.8, 0.9, 0.9, 5000),
                EndToEndResult("fast", 0.8, 0.9, 0.9, 2000),
            ]
        )
        == "fast"
    )


def test_coordinate_search_base_candidate_preserves_profile_retrieval_inputs() -> None:
    manifest = _v2_manifest()
    manifest.retrieval_profiles[0] = manifest.retrieval_profiles[0].model_copy(
        update={"top_k": 20, "rerank_top_n": 5}
    )
    candidates = build_coordinate_search_candidates(manifest)
    assert candidates[0].profile_id == manifest.retrieval_profiles[0].profile_id
    assert candidates[0].top_k == 20
    assert candidates[0].rerank_top_n == 5


def test_paired_statistics_are_reproducible_and_mcnemar_is_exact() -> None:
    pairs = [(False, True)] * 4 + [(True, False)] + [(True, True)] * 5
    first = _paired_bootstrap_confidence_interval(
        pairs,
        iterations=10_000,
        seed="fixture",
    )
    second = _paired_bootstrap_confidence_interval(
        pairs,
        iterations=10_000,
        seed="fixture",
    )
    assert first == second
    assert _exact_mcnemar_p_value(pairs) == pytest.approx(0.375)


def test_review_sampling_majority_vote_and_promotion_gate() -> None:
    outcomes = [
        RepeatCaseOutcome("case-a", 1, True),
        RepeatCaseOutcome("case-a", 2, False, result_changed=True),
        RepeatCaseOutcome("case-a", 3, True),
        RepeatCaseOutcome("case-b", 1, False),
        RepeatCaseOutcome("case-b", 2, False, hard_gate_failed=True),
        RepeatCaseOutcome("case-b", 3, True, judge_confidence=0.2),
    ]
    assert majority_vote_case_outcomes(outcomes) == {"case-a": True, "case-b": False}
    selected = select_manual_review_case_ids(outcomes, stable_sample_rate=0.0)
    assert ("case-a", 1) in selected
    assert ("case-a", 2) in selected
    assert ("case-b", 2) in selected
    assert ("case-b", 3) in selected

    passed = evaluate_local_accuracy_promotion_gate(
        PromotionGateInput(
            baseline_mean_pass_rate=0.70,
            candidate_mean_pass_rate=0.76,
            baseline_unanswerable_accuracy=0.8,
            candidate_unanswerable_accuracy=0.8,
            baseline_prompt_injection_resistance=0.9,
            candidate_prompt_injection_resistance=0.9,
            baseline_citation_correctness=0.8,
            candidate_citation_correctness=0.81,
            baseline_answer_completeness=0.8,
            candidate_answer_completeness=0.8,
            baseline_p95_latency_ms=1000,
            candidate_p95_latency_ms=2000,
            pipeline_failure_count=0,
        )
    )
    assert passed.promote is True
    assert passed.absolute_percentage_point_delta == pytest.approx(6.0)
