"""seed graph store provider setting

Revision ID: 0016_graph_store_provider_seed
Revises: 0015_langgraph_agentic
Create Date: 2026-06-15
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0016_graph_store_provider_seed"
down_revision = "0015_langgraph_agentic"
branch_labels = None
depends_on = None

_SETTING_KEY = "rag.graph.store.provider"
_SETTING_VALUE = "postgres"
_SETTING_DESCRIPTION = "GraphStore provider. Neo4j remains optional and disabled by default."


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO system_settings (setting_key, setting_value, description)
            VALUES (:setting_key, CAST(:setting_value AS jsonb), :description)
            ON CONFLICT (setting_key) DO NOTHING
            """
        ),
        {
            "setting_key": _SETTING_KEY,
            "setting_value": json.dumps(_SETTING_VALUE),
            "description": _SETTING_DESCRIPTION,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM system_settings
            WHERE setting_key = :setting_key
              AND setting_value = CAST(:setting_value AS jsonb)
              AND description = :description
            """
        ),
        {
            "setting_key": _SETTING_KEY,
            "setting_value": json.dumps(_SETTING_VALUE),
            "description": _SETTING_DESCRIPTION,
        },
    )
