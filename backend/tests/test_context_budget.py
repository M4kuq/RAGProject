from __future__ import annotations

import json

import pytest

from app.rag.context_budget import (
    ContextBudgetCandidate,
    ContextBudgetManager,
    ContextBudgetPolicy,
    estimate_tokens,
    sanitize_context_budget_json,
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
) -> ContextBudgetCandidate:
    return ContextBudgetCandidate(
        retrieval_run_item_id=item_id,
        document_chunk_id=chunk_id,
        source_label=f"{source}.md",
        section_title="Section",
        page_from=1,
        page_to=1,
        score=score,
        rank=rank,
        rerank_score=score,
        rerank_order=rank,
        text=text,
        citation_candidate=citation_candidate,
        source_group_key=source,
        retrieval_source="dense",
    )


def test_token_estimate_is_deterministic() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 17) == 5


def test_context_budget_policy_validation() -> None:
    with pytest.raises(ValueError):
        ContextBudgetPolicy(max_context_tokens=10, reserve_answer_tokens=10)
    with pytest.raises(ValueError):
        ContextBudgetPolicy(
            max_context_tokens=10,
            reserve_answer_tokens=0,
            max_tokens_per_item=11,
        )
    with pytest.raises(ValueError):
        ContextBudgetPolicy(max_context_items=1, min_citation_candidates=2)


def test_context_budget_selects_within_budget_and_counts_sources() -> None:
    decision = ContextBudgetManager().apply(
        [
            _candidate(1, 101, "a" * 40, source="source-a", rank=1, score=0.9),
            _candidate(2, 102, "b" * 40, source="source-b", rank=2, score=0.8),
        ],
        policy=ContextBudgetPolicy(
            max_context_tokens=30,
            reserve_answer_tokens=0,
            max_context_items=3,
            max_tokens_per_item=30,
        ),
        estimated_prompt_tokens=7,
    )

    trace = decision.trace
    assert decision.selected_item_ids == [1, 2]
    assert trace.usage.estimated_prompt_tokens == 7
    assert trace.usage.estimated_context_tokens == 20
    assert trace.usage.estimated_total_input_tokens == 27
    assert trace.items.selected_count == 2
    assert trace.items.dropped_count == 0
    assert trace.items.source_count == 2
    assert trace.items.citation_candidate_count == 2


def test_context_budget_drops_over_budget_and_max_items() -> None:
    decision = ContextBudgetManager().apply(
        [
            _candidate(1, 101, "a" * 40, rank=1),
            _candidate(2, 102, "b" * 80, rank=2),
            _candidate(3, 103, "c" * 40, rank=3),
        ],
        policy=ContextBudgetPolicy(
            max_context_tokens=25,
            reserve_answer_tokens=0,
            max_context_items=1,
            max_tokens_per_item=25,
        ),
    )

    assert decision.selected_item_ids == [1]
    assert decision.trace.drop_reasons == {
        "max_items_exceeded": 2,
    }
    assert decision.trace.items.selected_count == 1
    assert decision.trace.items.dropped_count == 2

    over_budget = ContextBudgetManager().apply(
        [
            _candidate(1, 101, "a" * 40, rank=1),
            _candidate(2, 102, "b" * 80, rank=2),
        ],
        policy=ContextBudgetPolicy(
            max_context_tokens=20,
            reserve_answer_tokens=0,
            max_context_items=3,
            max_tokens_per_item=20,
        ),
    )
    assert over_budget.selected_item_ids == [1]
    assert over_budget.trace.drop_reasons == {"over_budget": 1}
    assert over_budget.trace.usage.budget_exhausted is True


def test_context_budget_reserves_answer_tokens_from_effective_context_limit() -> None:
    decision = ContextBudgetManager().apply(
        [
            _candidate(1, 101, "a" * 80, rank=1),
            _candidate(2, 102, "b" * 80, rank=2),
        ],
        policy=ContextBudgetPolicy(
            max_context_tokens=30,
            reserve_answer_tokens=10,
            max_context_items=3,
            max_tokens_per_item=30,
        ),
    )

    assert decision.selected_item_ids == [1]
    assert decision.trace.usage.estimated_context_tokens == 20
    assert decision.trace.usage.remaining_context_tokens == 0
    assert decision.trace.drop_reasons == {"over_budget": 1}


def test_context_budget_promotes_extra_candidates_to_minimum_when_budget_allows() -> None:
    decision = ContextBudgetManager().apply(
        [
            _candidate(1, 101, "a" * 20, rank=1, citation_candidate=True),
            _candidate(2, 102, "b" * 20, rank=2, citation_candidate=False),
            _candidate(3, 103, "c" * 20, rank=3, citation_candidate=False),
        ],
        policy=ContextBudgetPolicy(
            max_context_tokens=30,
            reserve_answer_tokens=0,
            max_context_items=3,
            max_tokens_per_item=30,
            min_citation_candidates=2,
        ),
    )

    assert decision.selected_item_ids == [1, 2]
    assert decision.trace.selected_item_refs[1].reason == "min_citation_candidates"
    assert decision.trace.drop_reasons == {"not_selected_by_rerank": 1}


def test_context_budget_preserves_source_diversity_when_enabled() -> None:
    decision = ContextBudgetManager().apply(
        [
            _candidate(1, 101, "a" * 20, source="source-a", rank=1, score=0.95),
            _candidate(2, 102, "b" * 20, source="source-a", rank=2, score=0.94),
            _candidate(3, 103, "c" * 20, source="source-b", rank=3, score=0.5),
        ],
        policy=ContextBudgetPolicy(
            max_context_tokens=10,
            reserve_answer_tokens=0,
            max_context_items=2,
            max_tokens_per_item=10,
        ),
    )

    assert decision.selected_item_ids == [1, 3]
    assert decision.trace.selected_item_refs[1].reason == "source_diversity"


def test_context_budget_trace_has_no_raw_text_fields() -> None:
    decision = ContextBudgetManager().apply(
        [
            _candidate(
                1,
                101,
                "raw chunk text must stay internal",
                source="source-a",
                rank=1,
            ),
            _candidate(
                2,
                102,
                "not selected raw chunk text",
                source="source-b",
                rank=2,
                citation_candidate=False,
            ),
        ],
        policy=ContextBudgetPolicy(),
    )
    dumped = json.dumps(decision.trace.model_dump(mode="json"), sort_keys=True)

    assert "raw chunk text" not in dumped
    assert "full_context" not in dumped
    assert "raw_prompt" not in dumped
    assert "secret" not in dumped
    assert "not_selected_by_rerank" in dumped


def test_context_budget_sanitizer_allowlists_safe_trace_shape() -> None:
    sanitized = sanitize_context_budget_json(
        {
            "schema_version": "phase2.context_budget.v1",
            "enabled": True,
            "budget": {
                "max_context_tokens": 6000,
                "reserve_answer_tokens": 1000,
                "max_context_items": 12,
                "max_tokens_per_item": 1200,
                "min_citation_candidates": 1,
                "token_estimator": "heuristic",
                "preserve_source_diversity": True,
                "drop_low_score_first": True,
            },
            "usage": {
                "estimated_prompt_tokens": 3,
                "estimated_context_tokens": 4,
                "estimated_total_input_tokens": 7,
                "reserve_answer_tokens": 1000,
                "remaining_context_tokens": 5996,
                "budget_exhausted": False,
            },
            "items": {
                "candidate_count": 1,
                "selected_count": 1,
                "dropped_count": 0,
                "citation_candidate_count": 1,
                "source_count": 1,
            },
            "drop_reasons": {"raw_prompt": 1, "over_budget": 0},
            "sources": {"source_count": 0, "by_source": []},
            "selected_item_refs": [
                {
                    "retrieval_run_item_id": 1,
                    "document_chunk_id": 10,
                    "source_label": r"C:\Users\kei01\private.md",
                    "estimated_tokens": 4,
                    "char_count": 16,
                    "raw_chunk_text": "must not survive",
                }
            ],
            "raw_prompt": "must not survive",
        }
    )

    assert sanitized is not None
    dumped = json.dumps(sanitized, sort_keys=True)
    assert "max_context_tokens" in dumped
    assert "estimated_context_tokens" in dumped
    assert "raw_prompt" not in dumped
    assert "raw_chunk_text" not in dumped
    assert r"C:\Users" not in dumped
    assert "redacted" in dumped
