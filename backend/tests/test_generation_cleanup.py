from __future__ import annotations

from app.rag.citations import parse_generation_output
from app.rag.generation import _final_answer_text, _truncate_output
from app.services.rag_service import _is_insufficient_evidence_answer


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

    assert _is_insufficient_evidence_answer(rewritten)
