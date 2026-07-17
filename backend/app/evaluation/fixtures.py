from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.evaluation.gold_v2 import GoldCaseV2


class EvaluationFixtureError(RuntimeError):
    pass


GOLD_V2_DATASET_NAME = "gold_answer_quality_v2"


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    question: str
    expected_keywords: tuple[str, ...]
    required_citation: bool
    expected_answer: str | None = None
    expected_document_ids: tuple[int, ...] = ()
    expected_chunk_ids: tuple[int, ...] = ()
    tags: tuple[str, ...] = ()
    metadata_json: dict[str, object] | None = None


def load_evaluation_cases(
    dataset_name: str,
    *,
    case_limit: int | None = None,
) -> list[EvaluationCase]:
    safe_name = _safe_dataset_name(dataset_name)
    if safe_name == GOLD_V2_DATASET_NAME:
        return _load_gold_v2_evaluation_cases(case_limit=case_limit)
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


def _load_gold_v2_evaluation_cases(*, case_limit: int | None) -> list[EvaluationCase]:
    from app.evaluation.gold_v2 import GoldV2ValidationError, load_gold_v2_bundle

    try:
        dataset, _, _ = load_gold_v2_bundle()
    except GoldV2ValidationError as exc:
        raise EvaluationFixtureError("evaluation_dataset_invalid") from exc

    loaded = [_gold_v2_evaluation_case(case) for case in dataset.cases]
    if case_limit is not None:
        loaded = loaded[:case_limit]
    if not loaded:
        raise EvaluationFixtureError("evaluation_dataset_empty")
    return loaded


def _gold_v2_evaluation_case(case: GoldCaseV2) -> EvaluationCase:
    expected_signals = tuple(fact.statement for fact in case.required_facts)
    if not expected_signals:
        expected_signals = (case.reference_answer,)
    return EvaluationCase(
        case_id=case.case_id,
        question=case.question,
        expected_keywords=expected_signals,
        required_citation=case.required_citation,
        expected_answer=case.reference_answer,
        tags=tuple(case.tags),
        metadata_json={
            "expected_strategy": case.expected_strategy,
            "acceptable_strategies": [case.expected_strategy],
            "expected_answer_slots": list(expected_signals),
            "required_hop_count": 2 if "multi_hop" in case.tags else 1,
        },
    )


def evaluation_case_question_hash(question: str | None) -> str:
    return hashlib.sha256((question or "").encode("utf-8")).hexdigest()


def evaluation_case_snapshot_hash(
    *,
    question: str | None,
    expected_answer: str | None,
    expected_keywords: list[str] | tuple[str, ...],
    expected_document_ids: list[int] | tuple[int, ...],
    expected_chunk_ids: list[int] | tuple[int, ...],
    required_citation: bool,
    metadata_json: dict[str, object] | None = None,
) -> str:
    snapshot = {
        "question_hash": evaluation_case_question_hash(question),
        "expected_answer_hash": evaluation_case_question_hash(expected_answer),
        "expected_keywords": list(expected_keywords),
        "expected_document_ids": list(expected_document_ids),
        "expected_chunk_ids": list(expected_chunk_ids),
        "required_citation": required_citation,
        "strategy_hints": evaluation_case_strategy_snapshot(metadata_json),
    }
    payload = json.dumps(snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluation_case_strategy_snapshot(
    metadata_json: dict[str, object] | None,
) -> dict[str, object]:
    if not isinstance(metadata_json, dict):
        return {}
    snapshot: dict[str, object] = {}
    expected = _safe_metadata_text_or_none(metadata_json.get("expected_strategy"), max_length=80)
    if expected is not None:
        snapshot["expected_strategy"] = expected
    raw_acceptable = metadata_json.get("acceptable_strategies")
    if isinstance(raw_acceptable, list):
        acceptable: list[str] = []
        for item in raw_acceptable:
            strategy = _safe_metadata_text_or_none(item, max_length=80)
            if strategy is not None and strategy not in acceptable:
                acceptable.append(strategy)
        if acceptable:
            snapshot["acceptable_strategies"] = acceptable
    for key in (
        "expected_entity_labels",
        "expected_relation_types",
        "expected_answer_slots",
    ):
        raw_values = metadata_json.get(key)
        if isinstance(raw_values, list):
            values: list[str] = []
            for item in raw_values:
                safe_value = _safe_metadata_text_or_none(item, max_length=120)
                if safe_value is not None and safe_value not in values:
                    values.append(safe_value)
            if values:
                snapshot[key] = values
    required_hop_count = metadata_json.get("required_hop_count")
    if (
        isinstance(required_hop_count, int)
        and not isinstance(required_hop_count, bool)
        and required_hop_count > 0
    ):
        snapshot["required_hop_count"] = required_hop_count
    return snapshot


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
    tags = _optional_tags(value)
    metadata_json = _optional_metadata_json(value)
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
        tags=tags,
        metadata_json=metadata_json,
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


def _optional_tags(value: dict[str, Any]) -> tuple[str, ...]:
    raw = value.get("tags", [])
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise EvaluationFixtureError("evaluation_case_invalid")
    tags: list[str] = []
    for item in raw:
        tag = _safe_metadata_text(item, max_length=80)
        if tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _optional_metadata_json(value: dict[str, Any]) -> dict[str, object] | None:
    raw = value.get("metadata_json")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise EvaluationFixtureError("evaluation_case_invalid")
    metadata: dict[str, object] = {}
    for key in ("expected_strategy", "expected_outcome"):
        raw_value = raw.get(key)
        if raw_value is not None:
            metadata[key] = _safe_metadata_text(raw_value, max_length=80)
    raw_acceptable = raw.get("acceptable_strategies")
    if raw_acceptable is not None:
        if not isinstance(raw_acceptable, list):
            raise EvaluationFixtureError("evaluation_case_invalid")
        acceptable: list[str] = []
        for item in raw_acceptable:
            strategy = _safe_metadata_text(item, max_length=80)
            if strategy not in acceptable:
                acceptable.append(strategy)
        metadata["acceptable_strategies"] = acceptable
    for key in (
        "expected_entity_labels",
        "expected_relation_types",
        "expected_answer_slots",
    ):
        raw_values = raw.get(key)
        if raw_values is None:
            continue
        if not isinstance(raw_values, list):
            raise EvaluationFixtureError("evaluation_case_invalid")
        values: list[str] = []
        for item in raw_values:
            metadata_text = _safe_metadata_text(item, max_length=120)
            if metadata_text not in values:
                values.append(metadata_text)
        metadata[key] = values
    required_hop_count = raw.get("required_hop_count")
    if required_hop_count is not None:
        if (
            isinstance(required_hop_count, bool)
            or not isinstance(required_hop_count, int)
            or required_hop_count < 1
            or required_hop_count > 10
        ):
            raise EvaluationFixtureError("evaluation_case_invalid")
        metadata["required_hop_count"] = required_hop_count
    return metadata or None


def _safe_metadata_text(value: Any, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise EvaluationFixtureError("evaluation_case_invalid")
    text = " ".join(value.replace("\x00", " ").split())
    if not text or len(text) > max_length or _contains_sensitive_word(text):
        raise EvaluationFixtureError("evaluation_case_invalid")
    return text


def _safe_metadata_text_or_none(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.replace("\x00", " ").split())
    if not text or len(text) > max_length or _contains_sensitive_word(text):
        return None
    return text


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
