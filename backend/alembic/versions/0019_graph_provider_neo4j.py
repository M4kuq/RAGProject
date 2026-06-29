"""forward migrate graph store provider default to neo4j

Revision ID: 0019_graph_provider_neo4j
Revises: 0018_evaluation_generation_usage
Create Date: 2026-06-25
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "0019_graph_provider_neo4j"
down_revision = "0018_evaluation_generation_usage"
branch_labels = None
depends_on = None

_GRAPH_STORE_PROVIDER_KEY = "rag.graph.store.provider"
_OLD_GRAPH_STORE_PROVIDER_VALUE = "postgres"
_NEW_GRAPH_STORE_PROVIDER_VALUE = "neo4j"
_OLD_GRAPH_STORE_PROVIDER_DESCRIPTION = (
    "GraphStore provider. Neo4j remains optional and disabled by default."
)
_NEW_GRAPH_STORE_PROVIDER_DESCRIPTION = (
    "GraphStore provider. Neo4j is the default read model; PostgreSQL remains source of truth."
)
_GRAPH_RETRIEVAL_ENABLED_KEY = "rag.graph.retrieval.enabled"
_OLD_GRAPH_RETRIEVAL_ENABLED_VALUE = False
_NEW_GRAPH_RETRIEVAL_ENABLED_VALUE = True
_OLD_GRAPH_RETRIEVAL_ENABLED_DESCRIPTION = (
    "Enable graph retrieval strategies. PR-48 connects retrieval."
)
_NEW_GRAPH_RETRIEVAL_ENABLED_DESCRIPTION = (
    "Enable explicit strategy=graph graph retrieval requests by default."
)


def upgrade() -> None:
    bind = op.get_bind()
    _forward_default(
        bind,
        setting_key=_GRAPH_STORE_PROVIDER_KEY,
        old_value=_OLD_GRAPH_STORE_PROVIDER_VALUE,
        new_value=_NEW_GRAPH_STORE_PROVIDER_VALUE,
        old_description=_OLD_GRAPH_STORE_PROVIDER_DESCRIPTION,
        new_description=_NEW_GRAPH_STORE_PROVIDER_DESCRIPTION,
    )
    _forward_default(
        bind,
        setting_key=_GRAPH_RETRIEVAL_ENABLED_KEY,
        old_value=_OLD_GRAPH_RETRIEVAL_ENABLED_VALUE,
        new_value=_NEW_GRAPH_RETRIEVAL_ENABLED_VALUE,
        old_description=_OLD_GRAPH_RETRIEVAL_ENABLED_DESCRIPTION,
        new_description=_NEW_GRAPH_RETRIEVAL_ENABLED_DESCRIPTION,
    )


def downgrade() -> None:
    bind = op.get_bind()
    _backward_default(
        bind,
        setting_key=_GRAPH_STORE_PROVIDER_KEY,
        old_value=_OLD_GRAPH_STORE_PROVIDER_VALUE,
        new_value=_NEW_GRAPH_STORE_PROVIDER_VALUE,
        old_description=_OLD_GRAPH_STORE_PROVIDER_DESCRIPTION,
        new_description=_NEW_GRAPH_STORE_PROVIDER_DESCRIPTION,
    )
    _backward_default(
        bind,
        setting_key=_GRAPH_RETRIEVAL_ENABLED_KEY,
        old_value=_OLD_GRAPH_RETRIEVAL_ENABLED_VALUE,
        new_value=_NEW_GRAPH_RETRIEVAL_ENABLED_VALUE,
        old_description=_OLD_GRAPH_RETRIEVAL_ENABLED_DESCRIPTION,
        new_description=_NEW_GRAPH_RETRIEVAL_ENABLED_DESCRIPTION,
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
