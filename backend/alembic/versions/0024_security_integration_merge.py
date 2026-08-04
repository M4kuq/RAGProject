"""merge RAG security migration heads

Revision ID: 0024_security_merge
Revises: 0023_rag_abuse_controls, 0023_corpus_trust
Create Date: 2026-08-04
"""

from __future__ import annotations

revision = "0024_security_merge"
down_revision = ("0023_rag_abuse_controls", "0023_corpus_trust")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two independently validated security migration branches."""


def downgrade() -> None:
    """Split the migration graph back to the two security branch heads."""
