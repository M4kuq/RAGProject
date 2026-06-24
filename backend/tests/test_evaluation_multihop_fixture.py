from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.evaluation.fixtures import _contains_sensitive_word, load_evaluation_cases
from app.schemas.evaluations import EvaluationDatasetManifest

DATASET_NAME = "phase3_corpus_multi_hop"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
FIXTURE_PATH = BACKEND_ROOT / "app" / "evaluation" / "fixtures" / f"{DATASET_NAME}.json"
CORPUS_PATHS = (
    REPO_ROOT / "backend" / "app" / "seed_data" / "llm_paper_corpus.md",
    REPO_ROOT / "docs" / "demo" / "llm_paper_corpus.md",
    REPO_ROOT / "docs" / "demo" / "corpus_manifest.json",
    REPO_ROOT / "docs" / "demo" / "corpus_neo4j_demo.md",
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "DDL.md",
    REPO_ROOT / "docs" / "phase2" / "agentic_strategy_evaluation.md",
    REPO_ROOT / "docs" / "phase3" / "README.md",
    REPO_ROOT / "docs" / "phase3" / "graph_rag_architecture.md",
    REPO_ROOT / "docs" / "phase3" / "graph_retrieval_strategy.md",
    REPO_ROOT / "docs" / "phase3" / "graph_indexing_design.md",
    REPO_ROOT / "docs" / "phase3" / "graph_evaluation_design.md",
    REPO_ROOT / "docs" / "phase3" / "graph_citation_design.md",
    REPO_ROOT / "docs" / "phase3" / "neo4j_optional_backend.md",
    REPO_ROOT / "docs" / "phase3" / "retrieval_cache_foundation.md",
    REPO_ROOT / "docs" / "phase3" / "security_redaction_policy.md",
)


def test_phase3_corpus_multi_hop_fixture_loads_with_required_metadata() -> None:
    cases = load_evaluation_cases(DATASET_NAME)

    assert len(cases) == 14
    assert len(cases) >= 12
    case_ids = [case.case_id for case in cases]
    assert len(case_ids) == len(set(case_ids))
    assert sum(1 for case in cases if "paper" in case.tags) == 8
    assert sum(1 for case in cases if "system_docs" in case.tags) == 6

    for case in cases:
        assert case.expected_keywords or case.expected_answer
        assert case.required_citation is True
        assert case.expected_document_ids == ()
        assert case.expected_chunk_ids == ()
        assert case.metadata_json is not None
        assert case.metadata_json.get("expected_strategy") == "graph"
        assert _non_empty_string_list(case.metadata_json.get("acceptable_strategies"))
        assert _non_empty_string_list(case.metadata_json.get("expected_entity_labels"))
        assert _non_empty_string_list(case.metadata_json.get("expected_relation_types"))
        required_hop_count = case.metadata_json.get("required_hop_count")
        assert isinstance(required_hop_count, int)
        assert required_hop_count >= 1

        assert not _contains_sensitive_word(case.case_id)
        assert not _contains_sensitive_word(case.question)
        if case.expected_answer is not None:
            assert not _contains_sensitive_word(case.expected_answer)
        for keyword in case.expected_keywords:
            assert not _contains_sensitive_word(keyword)
        for value in _string_values(case.metadata_json):
            assert not _contains_sensitive_word(value)


def test_phase3_corpus_multi_hop_manifest_is_safe_and_corpus_grounded() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    manifest = EvaluationDatasetManifest.model_validate(payload)

    assert manifest.dataset.dataset_name == DATASET_NAME
    assert manifest.schema_version == "phase2.evaluation_dataset.v1"
    assert all(case.status == "active" for case in manifest.cases)
    assert all(case.required_citation for case in manifest.cases)
    assert all(not case.expected_document_ids for case in manifest.cases)
    assert all(not case.expected_chunk_ids for case in manifest.cases)

    fixture_dump = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    assert "raw chunk" not in fixture_dump
    assert "full context" not in fixture_dump
    assert "api_key" not in fixture_dump
    assert "apikey" not in fixture_dump
    assert "password" not in fixture_dump
    assert "token" not in fixture_dump

    corpus_text = _normalized_text(
        "\n".join(path.read_text(encoding="utf-8") for path in CORPUS_PATHS)
    )
    for case in manifest.cases:
        for keyword in case.expected_keywords:
            assert _normalized_text(keyword) in corpus_text, (
                f"{case.case_key} keyword is not grounded in the committed corpus: {keyword}"
            )


def _non_empty_string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)


def _string_values(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _string_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _string_values(nested)


def _normalized_text(value: str) -> str:
    return " ".join(value.split()).casefold()
