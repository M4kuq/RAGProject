from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import SystemSetting

GRAPH_INDEXING_ENABLED_SETTING = "rag.graph.indexing.enabled"
GRAPH_EXTRACTOR_DEFAULT_SETTING = "rag.graph.extractor.default"
_GRAPH_EXTRACTOR_TYPES = frozenset({"llm", "rule_based"})


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
