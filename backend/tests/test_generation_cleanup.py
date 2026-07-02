from __future__ import annotations

from app.rag.citations import CitationSource, parse_generation_output
from app.rag.generation import (
    GenerationContextItem,
    GenerationRequest,
    _final_answer_text,
    _generation_output_text,
    _truncate_output,
)
from app.rag.insufficient import is_insufficient_evidence_answer
from app.services.rag_service import (
    _validated_generation_or_fallback,
)


def test_final_answer_text_strips_model_self_check_tail() -> None:
    raw = (
        "Phase1 uses FastAPI and Qdrant [4]\u3002 "
        "Wait, I need to check if Citation [1] adds specific stack info. "
        "Constraint: Return only the final answer text."
    )

    assert _final_answer_text(raw) == "Phase1 uses FastAPI and Qdrant [4]\u3002"


def test_final_answer_text_formats_japanese_sentence_breaks() -> None:
    raw = "Phase1 uses FastAPI [1]\u3002 React handles UI [1]\u3002"

    assert _final_answer_text(raw) == ("Phase1 uses FastAPI [1]\u3002\nReact handles UI [1]\u3002")


def test_parse_generation_output_preserves_readable_line_breaks() -> None:
    parsed = parse_generation_output("Phase1 uses FastAPI [1]\nReact handles UI [2]")

    assert parsed.answer_text == "Phase1 uses FastAPI [1]\nReact handles UI [2]"
    assert parsed.unique_marker_ids == [1, 2]


def test_generation_rewrites_insufficient_answer_to_non_error_phrase() -> None:
    raw = "検索された文書には、この質問に答えるための十分な根拠がありません。 [1]"

    rewritten = _truncate_output(raw, max_chars=200)

    assert rewritten == "検索された引用では、この質問への回答を確定できません [1]。"
    assert "十分な根拠" not in rewritten


def test_generation_rewritten_insufficient_answer_remains_detectable() -> None:
    raw = "検索された文書には、この質問に答えるための十分な根拠がありません。 [1]"

    rewritten = _truncate_output(raw, max_chars=200)

    assert is_insufficient_evidence_answer(rewritten)


def test_wrapped_standalone_insufficient_answers_use_safe_fallback() -> None:
    for content in (
        "Final answer: Insufficient evidence [1].",
        "There is insufficient evidence in the retrieved documents to answer the question [1].",
    ):
        parsed, cited_sources, used_fallback = _validated_generation_or_fallback(
            content,
            context_items=_context_items(),
            prompt_citation_sources=_citation_sources(),
            allow_insufficient_evidence_fallback=True,
        )

        assert (
            parsed.answer_text
            == "検索された文書には、この質問に直接答えるための十分な根拠がありません [1]。"
        )
        assert [source.local_citation_id for source in cited_sources] == [1]
        assert used_fallback
        assert is_insufficient_evidence_answer(content)


def test_plain_standalone_template_remains_insufficient() -> None:
    assert is_insufficient_evidence_answer("Insufficient evidence [1].")
    assert is_insufficient_evidence_answer(
        "検索された文書には、この質問に答えるための十分な根拠がありません。"
    )


def test_supported_answer_is_not_insufficient() -> None:
    assert not is_insufficient_evidence_answer("Alpha policy requires owner approval [1].")


def test_generation_output_keeps_supported_answer_with_insufficient_caveat() -> None:
    expected = (
        "Alpha policy requires owner approval [1]. "
        "There is insufficient evidence for the requested launch number."
    )
    content = _generation_output_text(
        f"Final answer: {expected}",
        _generation_request(),
        cleanup_final_answer=True,
    )

    parsed, cited_sources, used_fallback = _validated_generation_or_fallback(
        content,
        context_items=_context_items(),
        prompt_citation_sources=_citation_sources(),
        allow_insufficient_evidence_fallback=True,
    )

    assert parsed.answer_text == content
    assert content == expected
    assert [source.local_citation_id for source in cited_sources] == [1]
    assert not used_fallback
    assert not is_insufficient_evidence_answer(content)


def test_generation_output_standalone_insufficient_still_uses_safe_fallback() -> None:
    content = _generation_output_text(
        "Final answer: 検索された文書には、この質問に答えるための十分な根拠がありません。 [1]",
        _generation_request(),
        cleanup_final_answer=True,
    )

    parsed, cited_sources, used_fallback = _validated_generation_or_fallback(
        content,
        context_items=_context_items(),
        prompt_citation_sources=_citation_sources(),
        allow_insufficient_evidence_fallback=True,
    )

    assert (
        parsed.answer_text
        == "検索された文書には、この質問に直接答えるための十分な根拠がありません [1]。"
    )
    assert [source.local_citation_id for source in cited_sources] == [1]
    assert used_fallback


def _generation_request() -> GenerationRequest:
    return GenerationRequest(
        message="What does the Alpha policy require before launch?",
        context_items=_context_items(),
        max_output_chars=500,
    )


def _context_items() -> list[GenerationContextItem]:
    return [
        GenerationContextItem(
            document_chunk_id=100,
            source_label="alpha-policy.md",
            text="Alpha policy requires owner approval before launch.",
            local_citation_id=1,
        )
    ]


def _citation_sources() -> list[CitationSource]:
    return [
        CitationSource(
            local_citation_id=1,
            retrieval_run_item_id=10,
            document_chunk_id=100,
            source_label="alpha-policy.md",
            snippet="Alpha policy requires owner approval before launch.",
            page_from=None,
            page_to=None,
            section_title=None,
        )
    ]
