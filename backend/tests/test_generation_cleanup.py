from __future__ import annotations

from app.rag.citations import parse_generation_output
from app.rag.generation import _final_answer_text


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
