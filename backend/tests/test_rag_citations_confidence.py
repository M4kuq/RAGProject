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


def test_marker_id_beyond_provided_context_count_is_rejected() -> None:
    # Only two context items are provided (N == 2); a marker id of 3 is out of
    # bounds and must be rejected rather than silently dropped.
    source_map = [_source(1), _source(2)]

    with pytest.raises(CitationBuildError):
        validate_generation_citations(
            parse_generation_output("Alpha [3]."),
            source_map=source_map,
        )

    # The marker exactly at N is still accepted.
    citations = validate_generation_citations(
        parse_generation_output("Alpha [2]."),
        source_map=source_map,
    )
    assert [citation.local_citation_id for citation in citations] == [2]


def test_duplicate_markers_are_deduped_in_order() -> None:
    source_map = [_source(1), _source(2)]
    parsed = parse_generation_output("a [2] b [1] c [2] d [1].")

    citations = validate_generation_citations(parsed, source_map=source_map)

    assert [marker.local_citation_id for marker in parsed.markers] == [2, 1, 2, 1]
    assert parsed.unique_marker_ids == [2, 1]
    assert [citation.local_citation_id for citation in citations] == [2, 1]


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
                selected_count=1,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.65,
                top3_avg_retrieval_score=0.62,
                top1_rerank_score=0.65,
            ),
            marker_count=1,
            unique_citation_count=1,
            selected_count=1,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score == 1.0
    assert result.confidence_label == "High"


def test_groundedness_keeps_supported_answer_with_uncited_caveat_confident() -> None:
    source_map = [_source(1)]
    parsed = parse_generation_output(
        "Alpha policy requires owner approval [1]. "
        "There is insufficient evidence for the requested launch number."
    )
    cited_sources = validate_generation_citations(parsed, source_map=source_map)

    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=20,
                qdrant_candidate_count=20,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.82,
                top3_avg_retrieval_score=0.82,
                top1_rerank_score=0.82,
            ),
            marker_count=len(parsed.markers),
            unique_citation_count=len(cited_sources),
            selected_count=1,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score == 1.0
    assert result.confidence_label == "High"


@pytest.mark.parametrize(
    ("summary_kwargs", "selected_count"),
    [
        (
            {
                "requested_top_k": 20,
                "qdrant_candidate_count": 40,
                "sparse_candidate_count": 0,
                "post_filter_candidate_count": 5,
                "selected_count": 5,
                "excluded_by_rdb_check_count": 0,
                "top1_retrieval_score": 0.953125,
                "top3_avg_retrieval_score": 0.933449,
                "top1_rerank_score": None,
                "fusion_method": "rrf",
            },
            5,
        ),
        (
            {
                "requested_top_k": 20,
                "qdrant_candidate_count": 0,
                "post_filter_candidate_count": 1,
                "selected_count": 1,
                "excluded_by_rdb_check_count": 0,
                "top1_retrieval_score": 0.73,
                "top3_avg_retrieval_score": 0.73,
                "top1_rerank_score": None,
                "graph_store_provider": "neo4j",
                "graph_path_count": 1,
                "graph_source_candidate_count": 1,
            },
            1,
        ),
    ],
)
def test_confidence_uses_retrieval_support_when_rerank_is_absent(
    summary_kwargs: dict[str, object],
    selected_count: int,
) -> None:
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(**summary_kwargs),
            marker_count=1,
            unique_citation_count=1,
            selected_count=selected_count,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score == 1.0
    assert result.confidence_label == "High"
    assert result.answer_confidence > 0.75


@pytest.mark.parametrize(
    ("score", "expected_label"),
    [
        (0.20, "Low"),
        (0.30, "Medium"),
        (0.65, "High"),
    ],
)
def test_confidence_labels_single_context_answers_by_retrieval_support(
    score: float,
    expected_label: str,
) -> None:
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=20,
                qdrant_candidate_count=20,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=score,
                top3_avg_retrieval_score=score,
                top1_rerank_score=score,
            ),
            marker_count=1,
            unique_citation_count=1,
            selected_count=1,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score == 1.0
    assert result.confidence_label == expected_label


def test_confidence_keeps_low_when_rerank_absent_and_retrieval_support_is_low() -> None:
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=20,
                qdrant_candidate_count=20,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.20,
                top3_avg_retrieval_score=0.20,
                top1_rerank_score=None,
            ),
            marker_count=1,
            unique_citation_count=1,
            selected_count=1,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score == 1.0
    assert result.confidence_label == "Low"
    assert result.answer_confidence < Settings(app_env="test").confidence_medium_threshold


def test_confidence_keeps_low_when_groundedness_is_low_despite_high_retrieval() -> None:
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=20,
                qdrant_candidate_count=20,
                post_filter_candidate_count=1,
                selected_count=1,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.95,
                top3_avg_retrieval_score=0.95,
                top1_rerank_score=None,
            ),
            marker_count=0,
            unique_citation_count=0,
            selected_count=1,
        ),
        Settings(app_env="test"),
    )

    assert result.groundedness_score < Settings(app_env="test").groundedness_medium_threshold
    assert result.confidence_label == "Low"


def test_confidence_with_all_scores_present_matches_flat_formula() -> None:
    # retrieval clamps 0.8, rerank clamps 0.6, groundedness 1.0, context strength 1.0.
    # All optional scores present -> identical to the legacy flat weighted formula:
    # 0.35*0.8 + 0.35*0.6 + 0.20*1.0 + 0.10*1.0 = 0.79.
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=3,
                qdrant_candidate_count=3,
                post_filter_candidate_count=3,
                selected_count=3,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.8,
                top3_avg_retrieval_score=0.7,
                top1_rerank_score=0.6,
            ),
            marker_count=3,
            unique_citation_count=3,
            selected_count=3,
        ),
        Settings(app_env="test"),
    )

    assert result.answer_confidence == pytest.approx(0.79)


def test_confidence_renormalizes_when_rerank_score_absent() -> None:
    # Reranking disabled -> top1_rerank_score is None. The 0.35 rerank weight is
    # dropped and the remaining weights (0.35 + 0.20 + 0.10 = 0.65) renormalize to
    # 1.0. With retrieval clamps 0.8, groundedness 1.0, context strength 1.0:
    # (0.35*0.8 + 0.20*1.0 + 0.10*1.0) / 0.65 = 0.58 / 0.65 = 0.892308...
    # The legacy formula would have yielded only 0.58 because the missing rerank
    # score was treated as 0.0; renormalization removes that silent penalty.
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=3,
                qdrant_candidate_count=3,
                post_filter_candidate_count=3,
                selected_count=3,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=0.8,
                top3_avg_retrieval_score=0.7,
                top1_rerank_score=None,
            ),
            marker_count=3,
            unique_citation_count=3,
            selected_count=3,
        ),
        Settings(app_env="test"),
    )

    assert result.answer_confidence == pytest.approx(0.58 / 0.65)
    assert result.answer_confidence > 0.58


def test_confidence_renormalizes_when_retrieval_and_rerank_absent() -> None:
    # Both retrieval signals absent -> only groundedness (0.20) and context
    # presence (0.10) remain. The raw weighted value would be high, but the
    # retrieval-support floor caps confidence below Medium so citations/context
    # alone cannot look confident without retrieval or rerank support.
    result = calculate_confidence(
        ConfidenceInputs(
            retrieval_score_summary=RetrievalScoreSummary(
                requested_top_k=3,
                qdrant_candidate_count=3,
                post_filter_candidate_count=3,
                selected_count=2,
                excluded_by_rdb_check_count=0,
                top1_retrieval_score=None,
                top3_avg_retrieval_score=None,
                top1_rerank_score=None,
            ),
            marker_count=2,
            unique_citation_count=2,
            selected_count=2,
        ),
        Settings(app_env="test"),
    )

    assert result.answer_confidence == pytest.approx(0.44)
    assert result.confidence_label == "Low"


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
