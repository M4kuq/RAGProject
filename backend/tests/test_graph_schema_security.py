from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.graph import GraphEntityCreate


def test_graph_labels_reject_bare_credential_values() -> None:
    fake_api_key = "sk-" + "x" * 16

    with pytest.raises(ValidationError):
        GraphEntityCreate(canonical_name=fake_api_key, entity_type="concept")

    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            aliases_json=[fake_api_key],
        )


@pytest.mark.parametrize(
    "metadata_json",
    [
        {"note": "sk-" + "x" * 16},
        {"owner": "alice@example.com"},
        {"contact": "090-1234-5678"},
        {"items": [{"owner": "alice@example.com"}]},
    ],
)
def test_graph_metadata_rejects_credential_and_pii_values(
    metadata_json: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            metadata_json=metadata_json,
        )


@pytest.mark.parametrize(
    "metadata_json",
    [
        {"rawChunkText": "copied source evidence"},
        {"chunkText": "copied source evidence"},
        {"items": [{"evidenceText": "copied source evidence"}]},
    ],
)
def test_graph_metadata_rejects_camel_case_unsafe_keys(
    metadata_json: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            metadata_json=metadata_json,
        )


def test_graph_metadata_allows_camel_case_hash_refs() -> None:
    GraphEntityCreate(
        canonical_name="Safe",
        entity_type="concept",
        metadata_json={"rawChunkTextHash": "a" * 64},
    )


def test_graph_metadata_allows_safe_token_counts_only() -> None:
    GraphEntityCreate(
        canonical_name="Safe",
        entity_type="concept",
        metadata_json={
            "graph_extraction_input_token_count": 12,
            "graph_extraction_output_token_count": 5,
            "graph_extraction_total_token_count": 17,
        },
    )

    with pytest.raises(ValidationError):
        GraphEntityCreate(
            canonical_name="Unsafe",
            entity_type="concept",
            metadata_json={"graph_extraction_input_token_count": "sk-" + "x" * 16},
        )
