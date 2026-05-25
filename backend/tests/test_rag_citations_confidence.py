from __future__ import annotations

import pytest

from app.core.config import Settings
from app.rag.citations import (
    CitationBuildError,
    CitationSource,
    parse_generation_output,
    validate_generation_citations,
)
from app.rag.confidence import ConfidenceInputs, calculate_confidence
from app.schemas.rag import RetrievalScoreSummary


def test_marker_parser_validates_unique_markers_and_duplicate_references() -> None:
    source_map = [_source(1), _source(2)]
    parsed = parse_generation_output("claim[1] and another claim [1] then [2].")

    citations = validate_generation_citations(parsed, source_map=source_map)

    assert [marker.local_citation_id for marker in parsed.markers] == [1, 1, 2]
    assert parsed.unique_marker_ids == [1, 2]
    assert [citation.local_citation_id for citation in citations] == [1, 2]


def test_marker_validation_rejects_zero_unknown_and_oversized_markers() -> None:
    source_map = [_source(1)]

    with pytest.raises(CitationBuildError):
        validate_generation_citations(
            parse_generation_output("Alpha without a marker."),
            source_map=source_map,
        )

    with pytest.raises(CitationBuildError):
        validate_generation_citations(
            parse_generation_output("Alpha [2]."),
            source_map=source_map,
        )

    with pytest.raises(CitationBuildError):
        validate_generation_citations(
            parse_generation_output("Alpha [0]."),
            source_map=[_source(0), _source(1)],
        )

    with pytest.raises(CitationBuildError):
        parse_generation_output("Alpha [9999999].")

    with pytest.raises(CitationBuildError):
        validate_generation_citations(
            parse_generation_output("[1] [1]"),
            source_map=source_map,
        )


def test_confidence_scores_are_clamped_and_label_rules_are_deterministic() -> None:
    settings = Settings(app_env="test")
    high = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=3,
                qdrant_candidate_count=3,
                post_filter_candidate_count=3,
                selected_count=2,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=1.5,
                top3_avg_retrieval_score=0.8,
                top1_rerank_score=1.2,
            ),
            marker_count=3,
            unique_citation_count=2,
            selected_count=2,
        ),
        settings,
    )
    low = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=3,
                qdrant_candidate_count=1,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.1,
                top3_avg_retrieval_score=0.1,
                top1_rerank_score=0.1,
            ),
            marker_count=1,
            unique_citation_count=1,
            selected_count=3,
        ),
        settings,
    )

    assert high.answer_confidence <= 1.0
    assert high.groundedness_score == 1.0
    assert high.confidence_label == "High"
    assert low.confidence_label == "Low"


def test_confidence_does_not_penalize_concise_single_source_answers() -> None:
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=20,
                qdrant_candidate_count=20,
                post_filter_candidate_count=20,
                selected_count=5,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.65,
                top3_avg_retrieval_score=0.62,
                top1_rerank_score=0.65,
            ),
            marker_count=1,
            unique_citation_count=1,
            selected_count=5,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score == 1.0
    assert result.confidence_label == "High"


def _source(local_citation_id: int) -> CitationSource:
    return CitationSource(
        local_citation_id=local_citation_id,
        retrieval_run_item_id=local_citation_id + 100,
        document_chunk_id=local_citation_id + 500,
        source_label=f"source-{local_citation_id}.txt",
        snippet="preview",
        page_from=1,
        page_to=1,
        section_title=None,
    )
