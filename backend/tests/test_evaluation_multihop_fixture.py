from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.evaluation.fixtures import _contains_sensitive_word, load_evaluation_cases
from app.graph.extraction import EntityExtractionService, GraphChunkRef, RelationExtractionService
from app.graph.normalization import GraphEntityNormalizer
from app.schemas.evaluations import EvaluationDatasetManifest

DATASET_NAME = "phase3_corpus_multi_hop"
EXPECTED_CASE_COUNT = 12
EXPECTED_PAPER_CASES = 4
EXPECTED_SYSTEM_DOC_CASES = 8
EMITTED_RELATION_TYPES = frozenset({"supports", "uses", "depends_on", "includes", "connects"})
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
FIXTURE_PATH = BACKEND_ROOT / "app" / "evaluation" / "fixtures" / f"{DATASET_NAME}.json"
PAPER_CORPUS_PATH = BACKEND_ROOT / "app" / "seed_data" / "llm_paper_corpus.md"
SELF_DOC_MANIFEST_PATH = REPO_ROOT / "docs" / "demo" / "corpus_manifest.json"


@dataclass(frozen=True)
class _ExtractedGraphSummary:
    entity_source_counts: dict[str, int]
    relation_types: frozenset[str]
    relation_edges: tuple[tuple[str, str, str], ...]


def test_phase3_corpus_multi_hop_fixture_loads_with_required_metadata() -> None:
    cases = load_evaluation_cases(DATASET_NAME)

    assert len(cases) == EXPECTED_CASE_COUNT
    assert len(cases) >= 12
    case_ids = [case.case_id for case in cases]
    assert len(case_ids) == len(set(case_ids))
    assert sum(1 for case in cases if "paper" in case.tags) == EXPECTED_PAPER_CASES
    assert sum(1 for case in cases if "system_docs" in case.tags) == EXPECTED_SYSTEM_DOC_CASES

    for case in cases:
        assert case.expected_keywords or case.expected_answer
        assert case.required_citation is True
        assert case.expected_document_ids == ()
        assert case.expected_chunk_ids == ()
        assert case.metadata_json is not None
        assert case.metadata_json.get("expected_strategy") == "graph"
        assert _non_empty_string_list(case.metadata_json.get("acceptable_strategies"))
        assert _non_empty_string_list(case.metadata_json.get("expected_entity_labels"))
        raw_relation_types = case.metadata_json.get("expected_relation_types")
        assert _string_list(raw_relation_types)
        assert isinstance(raw_relation_types, list)
        assert set(raw_relation_types).issubset(EMITTED_RELATION_TYPES)
        required_hop_count = case.metadata_json.get("required_hop_count")
        assert isinstance(required_hop_count, int)
        assert required_hop_count >= 1
        assert required_hop_count <= 2

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

    corpus_text = _combined_corpus_text()
    for case in manifest.cases:
        for keyword in case.expected_keywords:
            assert _normalized_text(keyword) in corpus_text, (
                f"{case.case_key} keyword is not grounded in the committed corpus: {keyword}"
            )


def test_phase3_corpus_multi_hop_entities_match_rule_based_extraction() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    manifest = EvaluationDatasetManifest.model_validate(payload)
    normalizer = GraphEntityNormalizer()
    extracted = _extract_committed_graph_summary()

    for case in manifest.cases:
        metadata = case.metadata_json or {}
        raw_labels = metadata.get("expected_entity_labels")
        raw_relation_types = metadata.get("expected_relation_types")
        assert _non_empty_string_list(raw_labels)
        assert _string_list(raw_relation_types)
        assert isinstance(raw_labels, list)
        assert isinstance(raw_relation_types, list)

        labels = list(raw_labels)
        relation_types = list(raw_relation_types)
        for label in labels:
            normalized = normalizer.normalize(label)
            assert normalized is not None, f"{case.case_key} label did not normalize: {label}"
            assert normalized.canonical_name == label
            assert label in extracted.entity_source_counts, (
                f"{case.case_key} label was not extracted from committed corpora: {label}"
            )

        assert set(relation_types).issubset(EMITTED_RELATION_TYPES)
        assert set(relation_types).issubset(extracted.relation_types)
        if relation_types:
            label_set = set(labels)
            for relation_type in relation_types:
                assert any(
                    edge_type == relation_type and source in label_set and target in label_set
                    for source, edge_type, target in extracted.relation_edges
                ), f"{case.case_key} relation is not attached to expected labels: {relation_type}"
        else:
            assert any(extracted.entity_source_counts[label] >= 2 for label in labels), (
                f"{case.case_key} has no relation expectation and no multi-source entity hub"
            )


def _non_empty_string_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


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


def _combined_corpus_text() -> str:
    texts = [PAPER_CORPUS_PATH.read_text(encoding="utf-8")]
    texts.extend(text for _, text in _iter_manifest_texts())
    return _normalized_text("\n".join(texts))


def _extract_committed_graph_summary() -> _ExtractedGraphSummary:
    chunks: list[GraphChunkRef] = []
    source_ids: list[str] = []

    for source_id, text in _iter_paper_blocks():
        chunks.append(_graph_chunk(len(chunks), text))
        source_ids.append(source_id)

    for source_id, text in _iter_manifest_texts():
        chunks.append(_graph_chunk(len(chunks), text))
        source_ids.append(source_id)

    entity_service = EntityExtractionService(max_entities_per_chunk=100)
    mentions = entity_service.extract(tuple(chunks))
    relation_service = RelationExtractionService(max_relations_per_chunk=200)
    relations = relation_service.extract(tuple(chunks), mentions)

    sources_by_label: dict[str, set[str]] = defaultdict(set)
    canonical_by_key: dict[tuple[str, str], str] = {}
    for mention in mentions:
        sources_by_label[mention.canonical_name].add(source_ids[mention.chunk_index])
        canonical_by_key[mention.entity_key] = mention.canonical_name

    relation_edges = tuple(
        (
            canonical_by_key.get(relation.source_key, relation.source_key[0]),
            relation.relation_type,
            canonical_by_key.get(relation.target_key, relation.target_key[0]),
        )
        for relation in relations
    )
    return _ExtractedGraphSummary(
        entity_source_counts={label: len(sources) for label, sources in sources_by_label.items()},
        relation_types=frozenset(relation.relation_type for relation in relations),
        relation_edges=relation_edges,
    )


def _graph_chunk(index: int, text: str) -> GraphChunkRef:
    return GraphChunkRef(
        document_chunk_id=100000 + index,
        document_version_id=1,
        chunk_index=index,
        chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        content_text=text,
    )


def _iter_paper_blocks() -> Iterator[tuple[str, str]]:
    text = PAPER_CORPUS_PATH.read_text(encoding="utf-8")
    headings = list(re.finditer(r"(?m)^###\s+(P\d{3})\s+(.+)$", text))
    for index, match in enumerate(headings):
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        yield match.group(1), text[start:end]


def _iter_manifest_texts() -> Iterator[tuple[str, str]]:
    payload = json.loads(SELF_DOC_MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        source_path = entry.get("source_path")
        assert isinstance(source_path, str)
        path = REPO_ROOT / source_path
        if path.is_file():
            yield source_path, path.read_text(encoding="utf-8")
