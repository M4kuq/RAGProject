from __future__ import annotations

import math
from typing import Final

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import SystemSetting

GRAPH_INDEXING_ENABLED_SETTING = "rag.graph.indexing.enabled"
GRAPH_EXTRACTOR_DEFAULT_SETTING = "rag.graph.extractor.default"
_GRAPH_EXTRACTOR_TYPES = frozenset({"llm", "rule_based"})
_GRAPH_EXTRACTION_PROVIDERS = frozenset(
    {"fake", "ollama", "lmstudio", "openai", "anthropic", "gemini"}
)
_MISSING: Final = object()


def graph_indexing_enabled(db: Session) -> bool:
    setting = db.get(SystemSetting, GRAPH_INDEXING_ENABLED_SETTING)
    if setting is None:
        return True
    return setting.setting_value is True


def graph_extractor_type_override(db: Session) -> str | None:
    setting = db.get(SystemSetting, GRAPH_EXTRACTOR_DEFAULT_SETTING)
    value = setting.setting_value if setting is not None else None
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in _GRAPH_EXTRACTOR_TYPES else None


def graph_extraction_settings(db: Session, base: Settings) -> Settings:
    updates: dict[str, object] = {}
    _set_optional_provider(
        updates,
        "graph_extraction_provider",
        _setting_value(db, "rag.graph.extraction.provider"),
    )
    _set_optional_string(
        updates,
        "graph_extraction_model_name",
        _setting_value(db, "rag.graph.extraction.model_name"),
    )
    _set_float(
        updates,
        "graph_extraction_timeout_seconds",
        _setting_value(db, "rag.graph.extraction.timeout_seconds"),
        lower=0.0,
        upper=600.0,
        exclusive_lower=True,
    )
    _set_int(
        updates,
        "graph_extraction_max_output_chars",
        _setting_value(db, "rag.graph.extraction.max_output_chars"),
        lower=1000,
        upper=50000,
    )
    _set_int(
        updates,
        "graph_extraction_max_output_tokens",
        _setting_value(db, "rag.graph.extraction.max_output_tokens"),
        lower=128,
        upper=8192,
    )
    _set_float(
        updates,
        "graph_extraction_min_confidence",
        _setting_value(db, "rag.graph.extraction.min_confidence"),
        lower=0.0,
        upper=1.0,
    )
    _set_int(
        updates,
        "graph_extraction_max_entities_per_chunk",
        _setting_value(db, "rag.graph.max_entities_per_chunk"),
        lower=1,
        upper=100,
    )
    _set_int(
        updates,
        "graph_extraction_max_relations_per_chunk",
        _setting_value(db, "rag.graph.max_relations_per_chunk"),
        lower=1,
        upper=200,
    )
    if not updates:
        return base
    return base.model_copy(update=updates)


def _setting_value(db: Session, key: str) -> object:
    setting = db.get(SystemSetting, key)
    if setting is None:
        return _MISSING
    return setting.setting_value


def _set_optional_provider(
    updates: dict[str, object],
    field: str,
    value: object,
) -> None:
    if value is _MISSING:
        return
    if value is None:
        return
    if not isinstance(value, str):
        return
    normalized = value.strip().lower()
    if normalized in _GRAPH_EXTRACTION_PROVIDERS:
        updates[field] = normalized


def _set_optional_string(
    updates: dict[str, object],
    field: str,
    value: object,
) -> None:
    if value is _MISSING:
        return
    if value is None:
        return
    if not isinstance(value, str):
        return
    normalized = value.strip()
    if normalized:
        updates[field] = normalized


def _set_int(
    updates: dict[str, object],
    field: str,
    value: object,
    *,
    lower: int,
    upper: int,
) -> None:
    coerced = _coerce_int(value)
    if coerced is not None and lower <= coerced <= upper:
        updates[field] = coerced


def _set_float(
    updates: dict[str, object],
    field: str,
    value: object,
    *,
    lower: float,
    upper: float,
    exclusive_lower: bool = False,
) -> None:
    coerced = _coerce_float(value)
    if coerced is None:
        return
    lower_ok = coerced > lower if exclusive_lower else coerced >= lower
    if lower_ok and coerced <= upper:
        updates[field] = coerced


def _coerce_int(value: object) -> int | None:
    if value is _MISSING or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdecimal():
            return int(stripped)
    return None


def _coerce_float(value: object) -> float | None:
    if value is _MISSING or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, str)):
        try:
            coerced = float(value)
        except (TypeError, ValueError):
            return None
        return coerced if math.isfinite(coerced) else None
    return None
