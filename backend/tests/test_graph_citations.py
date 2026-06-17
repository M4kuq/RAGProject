from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.graph_models import GraphRetrievalPath
from app.db.models import (
    Citation,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    User,
)
from app.rag.graph_citations import (
    GraphCitationBuilder,
    GraphPathSourceLocator,
    GraphPathValidator,
)
from app.services.graph_debug_service import GraphDebugTraceService


@pytest.fixture
def graph_citation_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def test_graph_citation_builder_maps_paths_through_retrieval_items(
    graph_citation_session_factory: sessionmaker[Session],
) -> None:
    with graph_citation_session_factory() as db:
        seed = _seed_graph_citation_run(db)
        paths = (
            db.query(GraphRetrievalPath).order_by(GraphRetrievalPath.graph_retrieval_path_id).all()
        )
        located = GraphPathSourceLocator().locate(
            db,
            retrieval_run_id=seed["retrieval_run_id"],
            paths=paths,
        )
        validated = GraphPathValidator().validate(paths=paths, located_sources=located)
        result = GraphCitationBuilder(snippet_max_chars=64).build(
            validated_paths=validated,
            located_sources=located,
        )

    assert result.coverage.path_count == 3
    assert result.coverage.valid_path_count == 2
    assert result.coverage.citable_path_count == 1
    assert result.coverage.excluded_path_count == 1
    assert result.coverage.source_chunk_coverage_ratio == pytest.approx(2 / 3, abs=0.000001)
    assert result.coverage.citation_coverage_ratio == pytest.approx(1 / 3, abs=0.000001)
    assert result.coverage.citation_source_count == 1
    assert result.citation_sources[0].retrieval_run_item_id == seed["selected_run_item_id"]
    assert result.citation_sources[0].document_chunk_id == seed["selected_chunk_id"]
    assert "missing_retrieval_run_item" in result.coverage.reason_codes
    assert any(path.reason_codes == ("no_selected_retrieval_run_items",) for path in result.paths)


def test_graph_debug_trace_returns_safe_path_summary_only(
    graph_citation_session_factory: sessionmaker[Session],
) -> None:
    with graph_citation_session_factory() as db:
        seed = _seed_graph_citation_run(db)
        response = GraphDebugTraceService().get_graph_trace(
            db,
            retrieval_run_id=seed["retrieval_run_id"],
        )

    assert response.schema_version == "phase3.graph_citation_trace.v1"
    assert response.retrieval_run_id == seed["retrieval_run_id"]
    assert response.graph_path_count == 3
    assert response.citable_path_count == 1
    assert response.coverage.citation_source_count == 1
    first_path = response.paths[0]
    assert first_path.provider == "postgres"
    assert first_path.safe_entity_labels == ["FastAPI", "PostgreSQL"]
    assert first_path.relation_types == ["uses"]
    assert first_path.source_mappings[0].retrieval_run_item_id == seed["selected_run_item_id"]
    serialized = response.model_dump_json()
    assert "raw graph evidence must not leak" not in serialized
    assert "FastAPI uses PostgreSQL for metadata." not in serialized
    assert "raw_evidence_text" not in serialized
    assert "content_text" not in serialized
    assert "prompt" not in serialized


def test_graph_locator_preserves_superseded_version_mappings(
    graph_citation_session_factory: sessionmaker[Session],
) -> None:
    with graph_citation_session_factory() as db:
        seed = _seed_graph_citation_run(db)
        version = db.get(DocumentVersion, seed["document_version_id"])
        assert version is not None
        version.is_active = False
        db.commit()
        paths = [
            path
            for path in db.query(GraphRetrievalPath).all()
            if path.path_json.get("path_id") == "gp_selected"
        ]
        located = GraphPathSourceLocator().locate(
            db,
            retrieval_run_id=seed["retrieval_run_id"],
            paths=paths,
        )
        validated = GraphPathValidator().validate(paths=paths, located_sources=located)
        result = GraphCitationBuilder(snippet_max_chars=64).build(
            validated_paths=validated,
            located_sources=located,
        )

    assert result.coverage.valid_path_count == 1
    assert result.coverage.citable_path_count == 1
    assert result.coverage.citation_source_count == 1
    assert result.paths[0].source_mappings[0].old_version_flag is True
    assert result.paths[0].reason_codes == ("old_version_source_chunk",)
    assert "inactive_source_chunk" not in result.coverage.reason_codes


def test_graph_coverage_counts_partial_source_resolution(
    graph_citation_session_factory: sessionmaker[Session],
) -> None:
    with graph_citation_session_factory() as db:
        seed = _seed_graph_citation_run(db)
        partial_path = _path(
            retrieval_run_id=seed["retrieval_run_id"],
            path_id="gp_partial_missing",
            source_chunk_ids=[seed["selected_chunk_id"], 999_999],
        )
        db.add(partial_path)
        db.commit()
        paths = [
            path
            for path in db.query(GraphRetrievalPath).all()
            if path.path_json.get("path_id") == "gp_partial_missing"
        ]
        located = GraphPathSourceLocator().locate(
            db,
            retrieval_run_id=seed["retrieval_run_id"],
            paths=paths,
        )
        validated = GraphPathValidator().validate(paths=paths, located_sources=located)
        result = GraphCitationBuilder(snippet_max_chars=64).build(
            validated_paths=validated,
            located_sources=located,
        )

    assert result.coverage.path_count == 1
    assert result.coverage.valid_path_count == 0
    assert result.coverage.resolved_source_chunk_count == 1
    assert result.coverage.citable_source_chunk_count == 1
    assert result.coverage.source_chunk_count == 2
    assert result.coverage.source_chunk_coverage_ratio == 0.5
    assert result.paths[0].validation_status == "excluded"
    assert result.paths[0].source_mappings[0].source_chunk_id == seed["selected_chunk_id"]
    assert result.paths[0].reason_codes == ("source_chunk_missing",)


def _seed_graph_citation_run(db: Session) -> dict[str, int]:
    role = Role(role_name="graph-citation-role", description="Graph citation")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email="graph-citation@example.com",
        display_name="Graph Citation",
        status="active",
    )
    db.add(user)
    db.flush()
    logical = LogicalDocument(owner_user_id=user.user_id, title="Graph Citation", status="active")
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash="1".zfill(64),
        status="ready",
        is_active=True,
        file_name="graph-citation.txt",
        mime_type="text/plain",
        file_size_bytes=100,
        created_by=user.user_id,
    )
    db.add(version)
    db.flush()
    chunks = [
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=0,
            chunk_hash="a" * 64,
            content_text="FastAPI uses PostgreSQL for metadata.",
            char_count=37,
            modality="text",
        ),
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=1,
            chunk_hash="b" * 64,
            content_text="PostgreSQL stores graph retrieval run items.",
            char_count=43,
            modality="text",
        ),
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=2,
            chunk_hash="c" * 64,
            content_text="Unselected graph support chunk.",
            char_count=31,
            modality="text",
        ),
    ]
    db.add_all(chunks)
    db.flush()
    run = RetrievalRun(
        status="succeeded",
        top_k=3,
        strategy_type="graph",
        query_hash="f" * 64,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    selected_item = RetrievalRunItem(
        retrieval_run_id=run.retrieval_run_id,
        document_chunk_id=chunks[0].document_chunk_id,
        retrieval_score=Decimal("0.900000"),
        rank_order=1,
        selected_flag=True,
        retrieval_source="graph",
    )
    unselected_item = RetrievalRunItem(
        retrieval_run_id=run.retrieval_run_id,
        document_chunk_id=chunks[2].document_chunk_id,
        retrieval_score=Decimal("0.500000"),
        rank_order=2,
        selected_flag=False,
        retrieval_source="graph",
    )
    db.add_all([selected_item, unselected_item])
    db.flush()
    db.add(
        Citation(
            retrieval_run_id=run.retrieval_run_id,
            document_chunk_id=chunks[0].document_chunk_id,
            snippet="FastAPI uses PostgreSQL for metadata.",
            display_label="graph-citation.txt",
            rank_order=1,
        )
    )
    db.add_all(
        [
            _path(
                retrieval_run_id=run.retrieval_run_id,
                path_id="gp_selected",
                source_chunk_ids=[chunks[0].document_chunk_id],
            ),
            _path(
                retrieval_run_id=run.retrieval_run_id,
                path_id="gp_missing_item",
                source_chunk_ids=[chunks[1].document_chunk_id],
            ),
            _path(
                retrieval_run_id=run.retrieval_run_id,
                path_id="gp_unselected",
                source_chunk_ids=[chunks[2].document_chunk_id],
            ),
        ]
    )
    db.commit()
    return {
        "retrieval_run_id": run.retrieval_run_id,
        "document_version_id": version.document_version_id,
        "selected_chunk_id": chunks[0].document_chunk_id,
        "selected_run_item_id": selected_item.retrieval_run_item_id,
    }


def _path(
    *,
    retrieval_run_id: int,
    path_id: str,
    source_chunk_ids: list[int],
) -> GraphRetrievalPath:
    return GraphRetrievalPath(
        retrieval_run_id=retrieval_run_id,
        path_json={
            "schema_version": "phase3.graph_path.v2",
            "strategy_type": "graph",
            "provider": "postgres",
            "path_id": path_id,
            "node_refs": [
                {
                    "provider": "postgres",
                    "node_id": "1",
                    "entity_id": 1,
                    "safe_label": "FastAPI",
                    "entity_type": "technology",
                },
                {
                    "provider": "postgres",
                    "node_id": "2",
                    "entity_id": 2,
                    "safe_label": "PostgreSQL",
                    "entity_type": "technology",
                },
                {
                    "provider": "postgres",
                    "node_id": "unsafe",
                    "entity_id": 3,
                    "safe_label": "raw graph evidence must not leak",
                    "entity_type": "unsafe",
                },
            ],
            "relation_refs": [
                {
                    "provider": "postgres",
                    "relation_id": "10",
                    "source_node_id": "1",
                    "target_node_id": "2",
                    "relation_type": "uses",
                    "safe_label": "uses",
                }
            ],
            "source_chunk_ids": source_chunk_ids,
            "safe_entity_labels": ["FastAPI", "PostgreSQL", "raw graph evidence must not leak"],
            "relation_types": ["uses", "raw graph evidence must not leak"],
            "depth": 1,
            "path_score": 0.9,
            "raw_evidence_text": "raw graph evidence must not leak",
        },
        score_breakdown_json={"retrieval_source": "graph", "path_score": 0.9},
        source_chunk_ids_json=source_chunk_ids,
    )
