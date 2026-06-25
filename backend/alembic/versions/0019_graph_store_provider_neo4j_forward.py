"""forward migrate graph store provider default to neo4j

Revision ID: 0019_graph_store_provider_neo4j_forward
Revises: 0018_evaluation_generation_usage
Create Date: 2026-06-25
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0019_graph_store_provider_neo4j_forward"
down_revision = "0018_evaluation_generation_usage"
branch_labels = None
depends_on = None

_SETTING_KEY = "rag.graph.store.provider"
_OLD_SETTING_VALUE = "postgres"
_NEW_SETTING_VALUE = "neo4j"
_OLD_SETTING_DESCRIPTION = "GraphStore provider. Neo4j remains optional and disabled by default."
_NEW_SETTING_DESCRIPTION = (
    "GraphStore provider. Neo4j is the default read model; PostgreSQL remains source of truth."
)


def upgrade() -> None:
    bind = op.get_bind()
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
            "setting_key": _SETTING_KEY,
            "old_setting_value": json.dumps(_OLD_SETTING_VALUE),
            "new_setting_value": json.dumps(_NEW_SETTING_VALUE),
            "old_description": _OLD_SETTING_DESCRIPTION,
            "new_description": _NEW_SETTING_DESCRIPTION,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
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
              AND updated_at > created_at
            """
        ),
        {
            "setting_key": _SETTING_KEY,
            "old_setting_value": json.dumps(_OLD_SETTING_VALUE),
            "new_setting_value": json.dumps(_NEW_SETTING_VALUE),
            "old_description": _OLD_SETTING_DESCRIPTION,
            "new_description": _NEW_SETTING_DESCRIPTION,
        },
    )
