from __future__ import annotations

import json

import pytest

from app.rag.evidence_pack import (
    ContextCompressor,
    EvidenceCandidate,
    EvidencePackBuilder,
    EvidencePackPolicy,
    sanitize_context_compression_json,
)


def _candidate(
    item_id: int,
    chunk_id: int,
    text: str,
    *,
    source: str = "source-a",
    rank: int = 1,
    score: float = 0.9,
    citation_candidate: bool = True,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        retrieval_run_item_id=item_id,
        document_chunk_id=chunk_id,
        local_citation_id=item_id,
        text=text,
        source_label=f"{source}.md",
        section_title="Section",
        page_from=1,
        page_to=1,
        score=score,
        rerank_score=score,
        rank=rank,
        rerank_order=rank,
        source_group_key=source,
        citation_candidate=citation_candidate,
        retrieval_source="dense",
        logical_document_id=10,
        document_version_id=20,
    )


def test_evidence_pack_policy_validation() -> None:
    with pytest.raises(ValueError):
        EvidencePackPolicy(max_items=2, max_items_per_source=3)


def test_exact_and_normalized_duplicate_removal() -> None:
    pack = EvidencePackBuilder().build(
        [
            _candidate(1, 101, "Alpha policy text", rank=1),
            _candidate(2, 102, "Alpha policy text", rank=2),
            _candidate(3, 103, "alpha   policy   text", rank=3),
        ],
        policy=EvidencePackPolicy(max_items=5, max_items_per_source=5),
    )

    assert pack.selected_item_ids == [1]
    assert pack.trace.drops == {
        "exact_duplicate_removed": 1,
        "normalized_duplicate_removed": 1,
    }
    assert [item.drop_reason for item in pack.trace.dropped_item_refs] == [
        "exact_duplicate_removed",
        "normalized_duplicate_removed",
    ]


def test_near_duplicate_by_token_overlap() -> None:
    pack = EvidencePackBuilder().build(
        [
            _candidate(1, 101, "alpha beta gamma delta", rank=1),
            _candidate(2, 102, "alpha beta gamma delta epsilon", rank=2),
            _candidate(3, 103, "zeta eta theta iota", rank=3),
        ],
        policy=EvidencePackPolicy(
            max_items=5,
            max_items_per_source=5,
            near_duplicate_threshold=0.75,
        ),
    )

    assert pack.selected_item_ids == [1, 3]
    assert pack.trace.drops == {"near_duplicate_removed": 1}


def test_source_grouping_and_max_items_per_source() -> None:
    pack = EvidencePackBuilder().build(
        [
            _candidate(1, 101, "alpha one", source="source-a", rank=1),
            _candidate(2, 102, "alpha two", source="source-a", rank=2),
            _candidate(3, 103, "beta one", source="source-b", rank=3),
        ],
        policy=EvidencePackPolicy(max_items=5, max_items_per_source=1),
    )

    assert pack.selected_item_ids == [1, 3]
    assert pack.trace.drops == {"max_items_per_source": 1}
    assert pack.trace.output.evidence_group_count == 2
    assert {group.source_group_key for group in pack.trace.evidence_groups} == {
        "source-a",
        "source-b",
    }


def test_bounded_evidence_text_ratio_and_citation_mapping() -> None:
    pack = EvidencePackBuilder().build(
        [_candidate(10, 501, "a" * 100, source="source-a", rank=1)],
        policy=EvidencePackPolicy(
            max_items=2,
            max_items_per_source=2,
            max_chars_per_item=40,
            max_total_chars=40,
        ),
    )

    assert len(pack.items) == 1
    item = pack.items[0]
    assert item.retrieval_run_item_id == 10
    assert item.document_chunk_id == 501
    assert item.local_citation_id == 10
    assert item.evidence_text_for_generation == "a" * 40
    assert item.compression_method == "bounded_excerpt"
    assert pack.trace.output.output_char_count == 40
    assert pack.trace.output.compression_ratio == 0.4
    context_items = pack.to_generation_context_items()
    assert context_items[0].document_chunk_id == 501
    assert context_items[0].local_citation_id == 10
    assert context_items[0].text == "a" * 40


def test_context_compression_json_has_no_raw_text_fields() -> None:
    pack = EvidencePackBuilder().build(
        [
            _candidate(
                1,
                101,
                "raw chunk text with secret value must stay internal",
                source="source-a",
                rank=1,
            )
        ],
        policy=EvidencePackPolicy(),
    )
    dumped = json.dumps(pack.trace.model_dump(mode="json"), sort_keys=True)
    item_dumped = json.dumps([item.model_dump(mode="json") for item in pack.items], sort_keys=True)

    assert "raw chunk text" not in dumped
    assert "secret value" not in dumped
    assert "evidence_text_for_generation" not in dumped
    assert "raw chunk text" not in item_dumped
    assert "evidence_text_for_generation" not in item_dumped
    assert "full_context" not in dumped
    assert "raw_prompt" not in dumped
    assert "evidence_text_hash" in dumped


def test_context_compressor_returns_safe_log_counts() -> None:
    items, dropped, counts = ContextCompressor().compress(
        [
            _candidate(1, 101, "alpha", rank=1),
            _candidate(2, 102, "", rank=2),
        ],
        policy=EvidencePackPolicy(),
    )

    assert len(items) == 1
    assert [item.drop_reason for item in dropped] == ["missing_text"]
    assert counts == {"missing_text": 1}
    assert "alpha" not in json.dumps([item.model_dump(mode="json") for item in dropped])


def test_context_compression_sanitizer_allowlists_safe_trace_shape() -> None:
    sanitized = sanitize_context_compression_json(
        {
            "schema_version": "phase2.context_compression.v1",
            "enabled": True,
            "method": "deterministic_evidence_pack",
            "policy": {"max_items": 12, "max_items_per_source": 4},
            "input": {
                "candidate_context_items": 2,
                "selected_context_items": 1,
                "input_estimated_tokens": 20,
                "input_char_count": 80,
            },
            "output": {
                "evidence_group_count": 1,
                "evidence_item_count": 1,
                "output_estimated_tokens": 10,
                "output_char_count": 40,
                "compression_ratio": 0.5,
                "citation_candidate_count": 1,
            },
            "drops": {"raw_prompt": 1, "near_duplicate_removed": 1},
            "evidence_groups": [
                {
                    "source_group_key": r"C:\Users\kei01\private.md",
                    "source_label": r"C:\Users\kei01\private.md",
                    "item_count": 1,
                    "selected_item_count": 1,
                    "estimated_tokens": 10,
                    "evidence_item_refs": ["e1"],
                }
            ],
            "evidence_item_refs": [
                {
                    "evidence_item_id": "e1",
                    "retrieval_run_item_id": 1,
                    "document_chunk_id": 10,
                    "local_citation_id": 1,
                    "source_group_key": "source-a",
                    "evidence_text_hash": "a" * 64,
                    "original_char_count": 80,
                    "output_char_count": 40,
                    "estimated_tokens": 10,
                    "citation_candidate": True,
                    "compression_method": "bounded_excerpt",
                    "evidence_text_for_generation": "must not survive",
                }
            ],
            "dropped_item_refs": [],
            "raw_prompt": "must not survive",
        }
    )

    assert sanitized is not None
    dumped = json.dumps(sanitized, sort_keys=True)
    assert "near_duplicate_removed" in dumped
    assert "raw_prompt" not in dumped
    assert "evidence_text_for_generation" not in dumped
    assert r"C:\Users" not in dumped
    assert "redacted" in dumped
