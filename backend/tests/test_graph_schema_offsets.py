from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.graph import GraphEntityMentionCreate


def test_graph_entity_mention_offsets_reject_booleans() -> None:
    with pytest.raises(ValidationError):
        GraphEntityMentionCreate(
            graph_entity_id=1,
            document_chunk_id=1,
            document_version_id=1,
            mention_offset_start=True,
        )
    with pytest.raises(ValidationError):
        GraphEntityMentionCreate(
            graph_entity_id=1,
            document_chunk_id=1,
            document_version_id=1,
            mention_offset_end=False,
        )


def test_graph_entity_mention_offsets_accept_non_negative_integers() -> None:
    mention = GraphEntityMentionCreate(
        graph_entity_id=1,
        document_chunk_id=1,
        document_version_id=1,
        mention_offset_start=0,
        mention_offset_end=10,
    )

    assert mention.mention_offset_start == 0
    assert mention.mention_offset_end == 10
