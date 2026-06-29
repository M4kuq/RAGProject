"""forward migrate graph extractor default to llm

Revision ID: 0020_graph_llm_extractor
Revises: 0019_graph_provider_neo4j
Create Date: 2026-06-29
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0020_graph_llm_extractor"
down_revision = "0019_graph_provider_neo4j"
branch_labels = None
depends_on = None

_GRAPH_EXTRACTOR_KEY = "rag.graph.extractor.default"
_OLD_GRAPH_EXTRACTOR_VALUE = "none"
_NEW_GRAPH_EXTRACTOR_VALUE = "llm"
_OLD_GRAPH_EXTRACTOR_DESCRIPTION = "Default graph extractor. PR-47 connects extractors."
_NEW_GRAPH_EXTRACTOR_DESCRIPTION = (
    "Default graph extractor. C2b uses LLM extraction with rule_based fallback."
)
_NEW_SETTINGS = {
    "rag.graph.extraction.provider": (
        None,
        "Optional graph extraction provider override; null reuses generation_provider.",
    ),
    "rag.graph.extraction.model_name": (
        None,
        "Optional graph extraction model override; null reuses generation_model_name.",
    ),
    "rag.graph.extraction.timeout_seconds": (
        60,
        "Timeout for one graph LLM extraction provider call.",
    ),
    "rag.graph.extraction.max_output_chars": (
        12000,
        "Maximum graph extraction LLM output characters per chunk.",
    ),
    "rag.graph.extraction.max_output_tokens": (
        2048,
        "Maximum graph extraction LLM output tokens per provider call.",
    ),
    "rag.graph.extraction.min_confidence": (
        0.5,
        "Minimum confidence for LLM graph extraction candidates.",
    ),
}


def upgrade() -> None:
    bind = op.get_bind()
    op.alter_column(
        "graph_index_runs",
        "extractor_type",
        server_default=sa.text("'llm'"),
    )
    _forward_default(
        bind,
        setting_key=_GRAPH_EXTRACTOR_KEY,
        old_value=_OLD_GRAPH_EXTRACTOR_VALUE,
        new_value=_NEW_GRAPH_EXTRACTOR_VALUE,
        old_description=_OLD_GRAPH_EXTRACTOR_DESCRIPTION,
        new_description=_NEW_GRAPH_EXTRACTOR_DESCRIPTION,
    )
    for setting_key, (setting_value, description) in _NEW_SETTINGS.items():
        bind.execute(
            sa.text(
                """
                INSERT INTO system_settings (setting_key, setting_value, description)
                VALUES (:setting_key, CAST(:setting_value AS jsonb), :description)
                ON CONFLICT (setting_key) DO NOTHING
                """
            ),
            {
                "setting_key": setting_key,
                "setting_value": json.dumps(setting_value),
                "description": description,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.alter_column(
        "graph_index_runs",
        "extractor_type",
        server_default=sa.text("'none'"),
    )
    for setting_key, (setting_value, description) in _NEW_SETTINGS.items():
        bind.execute(
            sa.text(
                """
                DELETE FROM system_settings
                WHERE setting_key = :setting_key
                  AND setting_value = CAST(:setting_value AS jsonb)
                  AND description = :description
                  AND updated_by IS NULL
                """
            ),
            {
                "setting_key": setting_key,
                "setting_value": json.dumps(setting_value),
                "description": description,
            },
        )
    _backward_default(
        bind,
        setting_key=_GRAPH_EXTRACTOR_KEY,
        old_value=_OLD_GRAPH_EXTRACTOR_VALUE,
        new_value=_NEW_GRAPH_EXTRACTOR_VALUE,
        old_description=_OLD_GRAPH_EXTRACTOR_DESCRIPTION,
        new_description=_NEW_GRAPH_EXTRACTOR_DESCRIPTION,
    )


def _forward_default(
    bind: sa.engine.Connection,
    *,
    setting_key: str,
    old_value: object,
    new_value: object,
    old_description: str,
    new_description: str,
) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE system_settings
            SET setting_value = CAST(:new_setting_value AS jsonb),
                description = :new_description,
                updated_at = now()
            WHERE setting_key = :setting_key
              AND setting_value = CAST(:old_setting_value AS jsonb)
              AND description = :old_description
              AND updated_by IS NULL
            """
        ),
        {
            "setting_key": setting_key,
            "old_setting_value": json.dumps(old_value),
            "new_setting_value": json.dumps(new_value),
            "old_description": old_description,
            "new_description": new_description,
        },
    )


def _backward_default(
    bind: sa.engine.Connection,
    *,
    setting_key: str,
    old_value: object,
    new_value: object,
    old_description: str,
    new_description: str,
) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE system_settings
            SET setting_value = CAST(:old_setting_value AS jsonb),
                description = :old_description,
                updated_at = created_at
            WHERE setting_key = :setting_key
              AND setting_value = CAST(:new_setting_value AS jsonb)
              AND description = :new_description
              AND updated_by IS NULL
            """
        ),
        {
            "setting_key": setting_key,
            "old_setting_value": json.dumps(old_value),
            "new_setting_value": json.dumps(new_value),
            "old_description": old_description,
            "new_description": new_description,
        },
    )
