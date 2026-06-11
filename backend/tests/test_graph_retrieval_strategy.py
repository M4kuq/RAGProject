from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.graph_models import (
    GraphEntity,
    GraphEntityMention,
    GraphRelation,
    GraphRetrievalPath,
)
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    User,
)
from app.rag.graph_retrieval import (
    GRAPH_PATH_SCHEMA_VERSION,
    GRAPH_SCORE_SCHEMA_VERSION,
    GraphEntityLookupService,
    GraphPathSearchService,
    GraphRetrievalSettings,
    GraphRetrievalStrategy,
    graph_query_signal_score,
)
from app.rag.retrieval import RetrievalFilters
from app.repositories.graph_retrieval_repository import (
    GraphEntityLookupResult,
    GraphRetrievalRepository,
)


@dataclass(frozen=True)
class SeedGraph:
    chunk_ids: set[int]
    logical_document_id: int
    document_version_id: int
    user_id: int
    fastapi_entity_id: int
    postgresql_entity_id: int
    qdrant_entity_id: int


@pytest.fixture
def graph_retrieval_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        engine.dispose()


def test_graph_retrieval_finds_bounded_paths_and_safe_scores(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        strategy = GraphRetrievalStrategy()

        result = strategy.search(
            db,
            query="How does FastAPI use PostgreSQL in the RAGProject architecture?",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(
                enabled=True,
                max_start_entities=5,
                max_depth=2,
                max_paths=8,
                max_relations_per_entity=2,
                max_source_chunks=10,
                min_entity_match_score=0.2,
            ),
        )

        assert result.entity_lookup_count >= 2
        assert result.relation_count <= 6
        assert result.path_count <= 8
        assert result.source_candidate_count >= 1
        assert result.graph_candidates[0].document_chunk_id in seed.chunk_ids
        assert result.graph_candidates[0].payload["retrieval_source"] == "graph"
        assert any(
            path.depth == 2
            for candidate in result.graph_candidates
            for path in candidate.graph_path_candidates
        )
        assert (
            result.graph_candidates[0].score_breakdown_json["schema_version"]
            == GRAPH_SCORE_SCHEMA_VERSION
        )
        assert result.graph_candidates[0].score_breakdown_json["retrieval_source"] == "graph"
        path_depth = result.graph_candidates[0].score_breakdown_json["path_depth"]
        assert isinstance(path_depth, int)
        assert path_depth <= 2
        assert result.graph_candidates[0].score_breakdown_json["selected_flag"] is True
        serialized = str(result).lower()
        assert "raw chunk text" not in serialized
        assert "secret" not in serialized
        assert "full_context" not in serialized


def test_graph_retrieval_returns_no_context_when_disabled_or_unmatched(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        _seed_graph(db)
        strategy = GraphRetrievalStrategy()

        disabled = strategy.search(
            db,
            query="FastAPI PostgreSQL",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=False),
        )
        unmatched = strategy.search(
            db,
            query="unmatched entity name",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=True),
        )

        assert disabled.no_context is True
        assert disabled.reason_codes == ("graph_disabled",)
        assert unmatched.no_context is True
        assert unmatched.reason_codes == ("no_entity_matches",)


def test_graph_retrieval_applies_filters_to_relation_chunks(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        other_chunk_id = _seed_other_document_relation(db, seed)
        strategy = GraphRetrievalStrategy()

        result = strategy.search(
            db,
            query="How does FastAPI use Redis?",
            top_k=5,
            filters=RetrievalFilters(logical_document_ids=(seed.logical_document_id,)),
            settings=GraphRetrievalSettings(enabled=True, min_entity_match_score=0.2),
        )

        assert result.graph_candidates
        assert other_chunk_id not in {
            candidate.document_chunk_id for candidate in result.graph_candidates
        }
        assert all(
            other_chunk_id not in path.source_chunk_ids
            for candidate in result.graph_candidates
            for path in candidate.graph_path_candidates
        )


def test_graph_entity_lookup_scores_aliases_without_stopword_penalty(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        repository = GraphRetrievalRepository()

        results = repository.lookup_entities(
            db,
            query_terms=("how", "does", "pgsql", "connect"),
            limit=5,
            min_match_score=0.5,
        )

        assert any(result.entity.graph_entity_id == seed.postgresql_entity_id for result in results)


def test_graph_entity_lookup_ranks_name_matches_before_type_matches(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        db.add_all(
            [
                GraphEntity(
                    canonical_name=f"GenericTechnology{index}",
                    entity_type="technology",
                    aliases_json=[],
                )
                for index in range(80)
            ]
        )
        db.commit()
        repository = GraphRetrievalRepository()

        results = repository.lookup_entities(
            db,
            query_terms=("fastapi", "technology"),
            limit=5,
            min_match_score=0.2,
        )

        assert results
        assert results[0].entity.graph_entity_id == seed.fastapi_entity_id


def test_graph_entity_lookup_requires_phrase_boundaries_for_exact_matches(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        sql = GraphEntity(
            canonical_name="SQL",
            entity_type="technology",
            aliases_json=[],
        )
        db.add(sql)
        db.commit()
        repository = GraphRetrievalRepository()

        results = repository.lookup_entities(
            db,
            query_terms=("postgresql", "technology"),
            limit=10,
            min_match_score=0.5,
        )

        result_ids = {result.entity.graph_entity_id for result in results}
        assert seed.postgresql_entity_id in result_ids
        assert sql.graph_entity_id not in result_ids


def test_graph_entity_lookup_escapes_like_terms_before_row_limit(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        exact = GraphEntity(
            canonical_name="foo_bar",
            entity_type="technology",
            aliases_json=[],
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        noisy_entities = [
            GraphEntity(
                canonical_name=f"foo{index}bar",
                entity_type="technology",
                aliases_json=[],
                updated_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            for index in range(120)
        ]
        db.add_all([exact, *noisy_entities])
        db.commit()
        repository = GraphRetrievalRepository()

        results = repository.lookup_entities(
            db,
            query_terms=("foo_bar",),
            limit=1,
            min_match_score=0.5,
        )

        assert [result.entity.graph_entity_id for result in results] == [exact.graph_entity_id]


def test_graph_entity_lookup_applies_document_scope_before_start_limit(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        out_of_scope_entity_id = _seed_out_of_scope_fastapi_mention(db, seed)
        entity_lookup = GraphEntityLookupService()

        results = entity_lookup.lookup(
            db,
            query="FastAPI framework",
            filters=RetrievalFilters(logical_document_ids=(seed.logical_document_id,)),
            settings=GraphRetrievalSettings(
                enabled=True,
                max_start_entities=1,
                min_entity_match_score=0.2,
            ),
        )

        assert [result.entity.graph_entity_id for result in results] == [seed.fastapi_entity_id]
        assert out_of_scope_entity_id not in {result.entity.graph_entity_id for result in results}


def test_graph_query_terms_preserve_symbolic_entity_names() -> None:
    terms = GraphEntityLookupService().query_terms("How does C++ depend on C# and R?")

    assert "c++" in terms
    assert "c#" in terms
    assert "r" in terms


def test_graph_query_terms_strip_terminal_punctuation() -> None:
    terms = GraphEntityLookupService().query_terms("FastAPI. Node.js, C++ and C#.")

    assert "fastapi" in terms
    assert "node.js" in terms
    assert "c++" in terms
    assert "c#" in terms
    assert "fastapi." not in terms
    assert "c#." not in terms


def test_graph_relation_lookup_enforces_per_entity_cap(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        chunk_id = min(seed.chunk_ids)
        extra_entities = [
            GraphEntity(canonical_name=f"HubTarget{index}", entity_type="technology")
            for index in range(3)
        ]
        db.add_all(extra_entities)
        db.flush()
        db.add_all(
            [
                GraphRelation(
                    source_entity_id=seed.fastapi_entity_id,
                    target_entity_id=entity.graph_entity_id,
                    relation_type=f"links-{index}",
                    confidence=Decimal("0.70000"),
                    source_document_chunk_id=chunk_id,
                    evidence_text_hash=f"{index + 1}" * 64,
                    metadata_json={"rule_id": "test"},
                )
                for index, entity in enumerate(extra_entities)
            ]
        )
        db.commit()
        repository = GraphRetrievalRepository()

        rows = repository.list_relations_for_entity_ids(
            db,
            entity_ids={seed.fastapi_entity_id},
            max_relations_per_entity=1,
            filters=RetrievalFilters(),
        )

        assert len(rows) == 1


def test_graph_relation_lookup_filters_before_limiting(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        other_chunk_ids = {
            _seed_other_document_relation(db, seed),
            _seed_other_document_relation(db, seed),
            _seed_other_document_relation(db, seed),
        }
        for relation in db.scalars(select(GraphRelation)).all():
            if relation.source_document_chunk_id in other_chunk_ids:
                relation.confidence = Decimal("0.99000")
            else:
                relation.confidence = Decimal("0.10000")
        db.commit()
        repository = GraphRetrievalRepository()

        rows = repository.list_relations_for_entity_ids(
            db,
            entity_ids={seed.fastapi_entity_id},
            max_relations_per_entity=1,
            filters=RetrievalFilters(logical_document_ids=(seed.logical_document_id,)),
        )

        assert rows
        assert rows[0].relation.source_document_chunk_id in seed.chunk_ids


def test_graph_relation_lookup_fetches_each_frontier_entity_before_cap(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        db.execute(delete(GraphRelation))
        chunk_id = min(seed.chunk_ids)
        hub_neighbors = [
            GraphEntity(
                canonical_name=f"HubNeighbor{index}",
                entity_type="technology",
                aliases_json=[],
            )
            for index in range(6)
        ]
        qdrant_target = GraphEntity(
            canonical_name="QdrantSink",
            entity_type="technology",
            aliases_json=[],
        )
        db.add_all([*hub_neighbors, qdrant_target])
        db.flush()
        db.add_all(
            [
                GraphRelation(
                    source_entity_id=seed.fastapi_entity_id,
                    target_entity_id=entity.graph_entity_id,
                    relation_type=f"hub-link-{index}",
                    confidence=Decimal("0.99000"),
                    source_document_chunk_id=chunk_id,
                    evidence_text_hash=f"{index + 1}" * 64,
                    metadata_json={"rule_id": "test"},
                )
                for index, entity in enumerate(hub_neighbors)
            ]
        )
        db.add(
            GraphRelation(
                source_entity_id=seed.qdrant_entity_id,
                target_entity_id=qdrant_target.graph_entity_id,
                relation_type="stores",
                confidence=Decimal("0.10000"),
                source_document_chunk_id=chunk_id,
                evidence_text_hash="a" * 64,
                metadata_json={"rule_id": "test"},
            )
        )
        db.commit()
        repository = GraphRetrievalRepository()

        rows = repository.list_relations_for_entity_ids(
            db,
            entity_ids={seed.fastapi_entity_id, seed.qdrant_entity_id},
            max_relations_per_entity=1,
            filters=RetrievalFilters(),
        )

        assert any(
            seed.qdrant_entity_id in (row.relation.source_entity_id, row.relation.target_entity_id)
            for row in rows
        )


def test_graph_relation_lookup_does_not_double_count_shared_frontier_edge(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        db.execute(delete(GraphRelation))
        chunk_id = min(seed.chunk_ids)
        target = GraphEntity(
            canonical_name="SharedFrontierTarget",
            entity_type="technology",
            aliases_json=[],
        )
        db.add(target)
        db.flush()
        shared_relation = GraphRelation(
            source_entity_id=seed.fastapi_entity_id,
            target_entity_id=seed.qdrant_entity_id,
            relation_type="connects",
            confidence=Decimal("0.99000"),
            source_document_chunk_id=chunk_id,
            evidence_text_hash="a" * 64,
            metadata_json={"rule_id": "test"},
        )
        onward_relation = GraphRelation(
            source_entity_id=seed.qdrant_entity_id,
            target_entity_id=target.graph_entity_id,
            relation_type="stores",
            confidence=Decimal("0.10000"),
            source_document_chunk_id=chunk_id,
            evidence_text_hash="b" * 64,
            metadata_json={"rule_id": "test"},
        )
        db.add_all([shared_relation, onward_relation])
        db.commit()
        repository = GraphRetrievalRepository()

        rows = repository.list_relations_for_entity_ids(
            db,
            entity_ids=[seed.fastapi_entity_id, seed.qdrant_entity_id],
            max_relations_per_entity=1,
            filters=RetrievalFilters(),
        )

        relation_ids = {row.relation.graph_relation_id for row in rows}
        assert shared_relation.graph_relation_id in relation_ids
        assert onward_relation.graph_relation_id in relation_ids


def test_graph_path_search_expands_frontier_before_return_cap(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        db.execute(delete(GraphRelation))
        chunk_id = min(seed.chunk_ids)
        neighbors = [
            GraphEntity(canonical_name=f"Neighbor{index}", entity_type="technology")
            for index in range(3)
        ]
        deep_entity = GraphEntity(
            canonical_name="DeepStore",
            entity_type="technology",
        )
        db.add_all([*neighbors, deep_entity])
        db.flush()
        db.add_all(
            [
                GraphRelation(
                    source_entity_id=seed.fastapi_entity_id,
                    target_entity_id=entity.graph_entity_id,
                    relation_type=f"links-{index}",
                    confidence=Decimal("0.10000"),
                    source_document_chunk_id=chunk_id,
                    evidence_text_hash=f"{index + 3}" * 64,
                    metadata_json={"rule_id": "test"},
                )
                for index, entity in enumerate(neighbors)
            ]
        )
        db.add(
            GraphRelation(
                source_entity_id=neighbors[0].graph_entity_id,
                target_entity_id=deep_entity.graph_entity_id,
                relation_type="supports",
                confidence=Decimal("0.99000"),
                source_document_chunk_id=chunk_id,
                evidence_text_hash="8" * 64,
                metadata_json={"rule_id": "test"},
            )
        )
        db.commit()
        fastapi = db.get(GraphEntity, seed.fastapi_entity_id)
        assert fastapi is not None
        path_search = GraphPathSearchService()

        paths, relation_count, _reason_codes = path_search.search_paths(
            db,
            start_entities=[
                GraphEntityLookupResult(
                    entity=fastapi,
                    match_score=1.0,
                    matched_terms=("fastapi",),
                )
            ],
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(
                enabled=True,
                max_depth=2,
                max_paths=3,
                max_relations_per_entity=20,
                max_source_chunks=10,
            ),
            started_at=time.monotonic(),
        )

        assert relation_count >= 4
        assert any(path.depth == 2 for path in paths)


def test_graph_path_search_caps_relations_after_skipping_cycle_edges(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        db.execute(delete(GraphRelation))
        chunk_id = min(seed.chunk_ids)
        middle = GraphEntity(canonical_name="MiddleService", entity_type="technology")
        deep = GraphEntity(canonical_name="DeepService", entity_type="technology")
        db.add_all([middle, deep])
        db.flush()
        db.add_all(
            [
                GraphRelation(
                    source_entity_id=seed.fastapi_entity_id,
                    target_entity_id=middle.graph_entity_id,
                    relation_type="calls",
                    confidence=Decimal("0.99000"),
                    source_document_chunk_id=chunk_id,
                    evidence_text_hash="1" * 64,
                    metadata_json={"rule_id": "test"},
                ),
                GraphRelation(
                    source_entity_id=middle.graph_entity_id,
                    target_entity_id=deep.graph_entity_id,
                    relation_type="stores",
                    confidence=Decimal("0.10000"),
                    source_document_chunk_id=chunk_id,
                    evidence_text_hash="2" * 64,
                    metadata_json={"rule_id": "test"},
                ),
            ]
        )
        db.commit()
        fastapi = db.get(GraphEntity, seed.fastapi_entity_id)
        assert fastapi is not None
        path_search = GraphPathSearchService()

        paths, relation_count, _reason_codes = path_search.search_paths(
            db,
            start_entities=[
                GraphEntityLookupResult(
                    entity=fastapi,
                    match_score=1.0,
                    matched_terms=("fastapi",),
                )
            ],
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(
                enabled=True,
                max_depth=2,
                max_paths=3,
                max_relations_per_entity=1,
                max_source_chunks=10,
            ),
            started_at=time.monotonic(),
        )

        assert relation_count == 2
        assert any(path.depth == 2 and deep.graph_entity_id in path.entity_ids for path in paths)


def test_graph_retrieval_mention_only_paths_keep_entity_chunk_mapping(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        db.execute(delete(GraphRelation))
        db.commit()
        strategy = GraphRetrievalStrategy()

        result = strategy.search(
            db,
            query="FastAPI Qdrant",
            top_k=5,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=True, min_entity_match_score=0.2),
        )

        label_to_chunks = {
            path.safe_entity_labels[0]: set(path.source_chunk_ids)
            for candidate in result.graph_candidates
            for path in candidate.graph_path_candidates
        }
        assert "mention_only_paths" in result.reason_codes
        assert label_to_chunks["FastAPI"] == {min(seed.chunk_ids)}
        assert label_to_chunks["Qdrant"] == {max(seed.chunk_ids)}


def test_graph_mention_lookup_allocates_fallback_budget_per_entity(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        seed = _seed_graph(db)
        extra_chunks = [
            DocumentChunk(
                document_version_id=seed.document_version_id,
                chunk_index=10 + index,
                chunk_hash=f"{index + 4}" * 64,
                content_text=f"FastAPI fallback mention {index}.",
                char_count=30,
                modality="text",
            )
            for index in range(3)
        ]
        db.add_all(extra_chunks)
        db.flush()
        db.add_all(
            [
                GraphEntityMention(
                    graph_entity_id=seed.fastapi_entity_id,
                    document_chunk_id=chunk.document_chunk_id,
                    document_version_id=seed.document_version_id,
                    mention_text_hash=f"{index + 4}" * 64,
                    confidence=Decimal("0.90000"),
                )
                for index, chunk in enumerate(extra_chunks)
            ]
        )
        db.commit()
        repository = GraphRetrievalRepository()

        rows = repository.list_mentions_for_entity_ids(
            db,
            entity_ids={seed.fastapi_entity_id, seed.qdrant_entity_id},
            filters=RetrievalFilters(),
            max_source_chunks=2,
        )

        row_entity_ids = {row.graph_entity_id for row in rows if row.graph_entity_id is not None}
        assert seed.fastapi_entity_id in row_entity_ids
        assert seed.qdrant_entity_id in row_entity_ids

        capped_rows = repository.list_mentions_for_entity_ids(
            db,
            entity_ids=[
                seed.qdrant_entity_id,
                seed.fastapi_entity_id,
                seed.postgresql_entity_id,
            ],
            filters=RetrievalFilters(),
            max_source_chunks=1,
        )
        assert len(capped_rows) == 1
        assert capped_rows[0].graph_entity_id == seed.qdrant_entity_id


def test_graph_retrieval_falls_back_when_relation_paths_lack_source_chunks(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        _seed_graph(db)
        for relation in db.scalars(select(GraphRelation)).all():
            relation.source_document_chunk_id = None
        db.commit()
        strategy = GraphRetrievalStrategy()

        result = strategy.search(
            db,
            query="FastAPI uses PostgreSQL",
            top_k=3,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=True, min_entity_match_score=0.2),
        )

        assert result.graph_candidates
        assert "mention_only_paths" in result.reason_codes
        assert all(
            path.depth == 0
            for candidate in result.graph_candidates
            for path in candidate.graph_path_candidates
        )


def test_graph_retrieval_path_records_are_safe_and_link_to_retrieval_items(
    graph_retrieval_session_factory: sessionmaker[Session],
) -> None:
    with graph_retrieval_session_factory() as db:
        _seed_graph(db)
        strategy = GraphRetrievalStrategy()
        repository = GraphRetrievalRepository()
        result = strategy.search(
            db,
            query="FastAPI uses PostgreSQL",
            top_k=1,
            filters=RetrievalFilters(),
            settings=GraphRetrievalSettings(enabled=True, min_entity_match_score=0.2),
        )
        assert result.graph_candidates
        selected_chunk_ids = {candidate.document_chunk_id for candidate in result.graph_candidates}
        run = RetrievalRun(status="running", top_k=1, strategy_type="dense")
        db.add(run)
        db.flush()
        for rank, candidate in enumerate(result.graph_candidates, start=1):
            db.add(
                RetrievalRunItem(
                    retrieval_run_id=run.retrieval_run_id,
                    document_chunk_id=candidate.document_chunk_id,
                    retrieval_score=Decimal(str(candidate.retrieval_score)),
                    rank_order=rank,
                    selected_flag=True,
                    payload_snapshot=candidate.payload,
                    retrieval_source="dense",
                    score_breakdown_json=candidate.score_breakdown_json,
                )
            )
        db.flush()

        path_records = strategy.path_records(
            retrieval_run_id=run.retrieval_run_id,
            candidates=result.graph_candidates,
        )
        assert path_records
        assert {
            chunk_id for record in path_records for chunk_id in record.source_chunk_ids_json
        } <= selected_chunk_ids
        saved = repository.save_graph_retrieval_paths(
            db,
            retrieval_run_id=run.retrieval_run_id,
            paths=path_records,
        )
        db.commit()

        assert saved
        stored = db.scalars(select(GraphRetrievalPath)).all()
        assert len(stored) == len(saved)
        assert stored[0].path_json["schema_version"] == GRAPH_PATH_SCHEMA_VERSION
        assert stored[0].path_json["strategy_type"] == "graph"
        assert set(stored[0].source_chunk_ids_json) <= selected_chunk_ids
        payload_dump = str(stored[0].path_json).lower()
        assert "raw chunk" not in payload_dump
        assert "full context" not in payload_dump
        assert "prompt" not in payload_dump


def test_graph_query_signal_score_detects_relation_queries() -> None:
    assert graph_query_signal_score("How does FastAPI depend on PostgreSQL?") > 0.5
    assert graph_query_signal_score("simple keyword") < 0.5


def _seed_graph(db: Session) -> SeedGraph:
    role = Role(
        role_name=f"graph-role-{uuid.uuid4().hex[:8]}",
        description="Graph retrieval",
    )
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email=f"graph-retrieval-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Graph Retrieval",
        status="active",
    )
    db.add(user)
    db.flush()
    logical = LogicalDocument(
        owner_user_id=user.user_id,
        title="Graph Retrieval",
        status="active",
    )
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash="1".zfill(64),
        status="ready",
        is_active=True,
        file_name="graph-retrieval.txt",
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
            content_text="FastAPI uses PostgreSQL for RAGProject metadata.",
            char_count=49,
            modality="text",
        ),
        DocumentChunk(
            document_version_id=version.document_version_id,
            chunk_index=1,
            chunk_hash="b" * 64,
            content_text="RAGProject connects FastAPI and Qdrant for retrieval.",
            char_count=54,
            modality="text",
        ),
    ]
    db.add_all(chunks)
    db.flush()
    entities = {
        "FastAPI": GraphEntity(
            canonical_name="FastAPI",
            entity_type="technology",
            aliases_json=[],
        ),
        "PostgreSQL": GraphEntity(
            canonical_name="PostgreSQL",
            entity_type="technology",
            aliases_json=["PGSQL"],
        ),
        "RAGProject": GraphEntity(
            canonical_name="RAGProject",
            entity_type="artifact",
            aliases_json=[],
        ),
        "Qdrant": GraphEntity(
            canonical_name="Qdrant",
            entity_type="technology",
            aliases_json=[],
        ),
    }
    db.add_all(entities.values())
    db.flush()
    db.add_all(
        [
            GraphEntityMention(
                graph_entity_id=entities["FastAPI"].graph_entity_id,
                document_chunk_id=chunks[0].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash="c" * 64,
                confidence=Decimal("0.90000"),
            ),
            GraphEntityMention(
                graph_entity_id=entities["PostgreSQL"].graph_entity_id,
                document_chunk_id=chunks[0].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash="d" * 64,
                confidence=Decimal("0.90000"),
            ),
            GraphEntityMention(
                graph_entity_id=entities["Qdrant"].graph_entity_id,
                document_chunk_id=chunks[1].document_chunk_id,
                document_version_id=version.document_version_id,
                mention_text_hash="e" * 64,
                confidence=Decimal("0.90000"),
            ),
        ]
    )
    db.add_all(
        [
            GraphRelation(
                source_entity_id=entities["FastAPI"].graph_entity_id,
                target_entity_id=entities["PostgreSQL"].graph_entity_id,
                relation_type="uses",
                relation_label="uses",
                confidence=Decimal("0.85000"),
                source_document_chunk_id=chunks[0].document_chunk_id,
                evidence_text_hash="f" * 64,
                metadata_json={"rule_id": "test"},
            ),
            GraphRelation(
                source_entity_id=entities["PostgreSQL"].graph_entity_id,
                target_entity_id=entities["Qdrant"].graph_entity_id,
                relation_type="feeds",
                relation_label="feeds",
                confidence=Decimal("0.80000"),
                source_document_chunk_id=chunks[1].document_chunk_id,
                evidence_text_hash="9" * 64,
                metadata_json={"rule_id": "test"},
            ),
            GraphRelation(
                source_entity_id=entities["RAGProject"].graph_entity_id,
                target_entity_id=entities["Qdrant"].graph_entity_id,
                relation_type="uses",
                relation_label="uses",
                confidence=Decimal("0.75000"),
                source_document_chunk_id=chunks[1].document_chunk_id,
                evidence_text_hash="0" * 64,
                metadata_json={"rule_id": "test"},
            ),
        ]
    )
    db.commit()
    return SeedGraph(
        chunk_ids={chunk.document_chunk_id for chunk in chunks},
        logical_document_id=logical.logical_document_id,
        document_version_id=version.document_version_id,
        user_id=user.user_id,
        fastapi_entity_id=entities["FastAPI"].graph_entity_id,
        postgresql_entity_id=entities["PostgreSQL"].graph_entity_id,
        qdrant_entity_id=entities["Qdrant"].graph_entity_id,
    )


def _seed_out_of_scope_fastapi_mention(db: Session, seed: SeedGraph) -> int:
    logical = LogicalDocument(
        owner_user_id=seed.user_id,
        title="Out Of Scope Graph",
        status="active",
    )
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash="3".zfill(64),
        status="ready",
        is_active=True,
        file_name="out-of-scope-graph.txt",
        mime_type="text/plain",
        file_size_bytes=100,
        created_by=seed.user_id,
    )
    db.add(version)
    db.flush()
    chunk = DocumentChunk(
        document_version_id=version.document_version_id,
        chunk_index=0,
        chunk_hash="3" * 64,
        content_text="FastAPI is described as a framework in another document.",
        char_count=56,
        modality="text",
    )
    entity = GraphEntity(
        canonical_name="FastAPI",
        entity_type="framework",
        aliases_json=[],
    )
    db.add_all([chunk, entity])
    db.flush()
    db.add(
        GraphEntityMention(
            graph_entity_id=entity.graph_entity_id,
            document_chunk_id=chunk.document_chunk_id,
            document_version_id=version.document_version_id,
            mention_text_hash="8" * 64,
            confidence=Decimal("0.90000"),
        )
    )
    db.commit()
    return entity.graph_entity_id


def _seed_other_document_relation(db: Session, seed: SeedGraph) -> int:
    logical = LogicalDocument(
        owner_user_id=seed.user_id,
        title="Other Graph",
        status="active",
    )
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash="2".zfill(64),
        status="ready",
        is_active=True,
        file_name="other-graph.txt",
        mime_type="text/plain",
        file_size_bytes=100,
        created_by=seed.user_id,
    )
    db.add(version)
    db.flush()
    chunk = DocumentChunk(
        document_version_id=version.document_version_id,
        chunk_index=0,
        chunk_hash="2" * 64,
        content_text="FastAPI uses Redis in a different document.",
        char_count=43,
        modality="text",
    )
    redis = GraphEntity(
        canonical_name=f"Redis-{uuid.uuid4().hex[:8]}",
        entity_type="technology",
        aliases_json=[],
    )
    db.add_all([chunk, redis])
    db.flush()
    db.add(
        GraphRelation(
            source_entity_id=seed.fastapi_entity_id,
            target_entity_id=redis.graph_entity_id,
            relation_type="uses",
            relation_label="uses",
            confidence=Decimal("0.99000"),
            source_document_chunk_id=chunk.document_chunk_id,
            evidence_text_hash="7" * 64,
            metadata_json={"rule_id": "test"},
        )
    )
    db.commit()
    return chunk.document_chunk_id
