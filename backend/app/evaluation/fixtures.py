from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class EvaluationFixtureError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    expected_keywords: tuple[str, ...]
    required_citation: bool
    expected_answer: str | None = None
    expected_document_ids: tuple[int, ...] = ()
    expected_chunk_ids: tuple[int, ...] = ()


def load_evaluation_cases(
    dataset_name: str,
    *,
    case_limit: int | None = None,
) -> list[EvaluationCase]:
    safe_name = _safe_dataset_name(dataset_name)
    path = Path(__file__).with_name("fixtures") / f"{safe_name}.json"
    if not path.exists():
        raise EvaluationFixtureError("evaluation_dataset_not_found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationFixtureError("evaluation_dataset_invalid") from exc

    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list) or not cases:
        raise EvaluationFixtureError("evaluation_dataset_empty")

    loaded = [_case_from_payload(item) for item in cases]
    if case_limit is not None:
        loaded = loaded[:case_limit]
    if not loaded:
        raise EvaluationFixtureError("evaluation_dataset_empty")
    return loaded


def _safe_dataset_name(value: str) -> str:
    name = value.strip()
    if not name or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in name):
        raise EvaluationFixtureError("evaluation_dataset_invalid")
    return name


def _case_from_payload(value: Any) -> EvaluationCase:
    if not isinstance(value, dict):
        raise EvaluationFixtureError("evaluation_case_invalid")
    case_id = _required_text(value, "case_id", max_length=100, fallback_key="case_key")
    question = _required_text(value, "question", max_length=8000)
    raw_keywords = value.get("expected_keywords")
    if not isinstance(raw_keywords, list):
        raise EvaluationFixtureError("evaluation_case_invalid")
    keywords = tuple(_keyword(item) for item in raw_keywords)
    expected_answer = _optional_text(value, "expected_answer", max_length=8000)
    expected_document_ids = _optional_positive_ids(value, "expected_document_ids")
    expected_chunk_ids = _optional_positive_ids(value, "expected_chunk_ids")
    if not keywords and expected_answer is None:
        raise EvaluationFixtureError("evaluation_case_invalid")
    return EvaluationCase(
        case_id=case_id,
        question=question,
        expected_keywords=keywords,
        required_citation=bool(value.get("required_citation", True)),
        expected_answer=expected_answer,
        expected_document_ids=expected_document_ids,
        expected_chunk_ids=expected_chunk_ids,
    )


def _required_text(
    value: dict[str, Any],
    key: str,
    *,
    max_length: int,
    fallback_key: str | None = None,
) -> str:
    raw = value.get(key)
    if raw is None and fallback_key is not None:
        raw = value.get(fallback_key)
    if not isinstance(raw, str):
        raise EvaluationFixtureError("evaluation_case_invalid")
    text = " ".join(raw.replace("\x00", " ").split())
    if not text or len(text) > max_length or _contains_sensitive_word(text):
        raise EvaluationFixtureError("evaluation_case_invalid")
    return text


def _optional_text(value: dict[str, Any], key: str, *, max_length: int) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise EvaluationFixtureError("evaluation_case_invalid")
    text = " ".join(raw.replace("\x00", " ").split())
    if not text or len(text) > max_length or _contains_sensitive_word(text):
        raise EvaluationFixtureError("evaluation_case_invalid")
    return text


def _keyword(value: Any) -> str:
    if not isinstance(value, str):
        raise EvaluationFixtureError("evaluation_case_invalid")
    keyword = " ".join(value.replace("\x00", " ").split())
    if not keyword or len(keyword) > 100 or _contains_sensitive_word(keyword):
        raise EvaluationFixtureError("evaluation_case_invalid")
    return keyword


def _optional_positive_ids(value: dict[str, Any], key: str) -> tuple[int, ...]:
    raw = value.get(key, [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise EvaluationFixtureError("evaluation_case_invalid")
    ids: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise EvaluationFixtureError("evaluation_case_invalid")
        ids.append(item)
    return tuple(ids)


def _contains_sensitive_word(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in ("api_key", "apikey", "secret", "password", "token"))
