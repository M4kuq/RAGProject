from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from sqlalchemy.orm import Session

from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate
from app.repositories.graph_retrieval_repository import (
    GraphChunkRow,
    GraphEntityLookupResult,
    GraphRelationRow,
    GraphRetrievalRepository,
)
from app.schemas.graph import (
    GraphRetrievalPathCreate,
    validate_safe_graph_label,
    validate_safe_graph_metadata,
)

GRAPH_RETRIEVAL_SCHEMA_VERSION = "phase3.graph_retrieval.v1"
GRAPH_PATH_SCHEMA_VERSION = "phase3.graph_path.v2"
GRAPH_SCORE_SCHEMA_VERSION = "phase3.graph_score.v1"
# Match ASCII symbolic identifiers (e.g. C++, node.js) first so their punctuation
# is preserved, then fall back to runs of non-ASCII word characters (e.g. Japanese
# or other scripts) so non-ASCII queries still yield lookup terms.
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_][A-Za-z0-9_.:+#-]{0,79}|[^\W\dA-Za-z_][^\W_]{0,79}",
)
_TRAILING_TOKEN_PUNCTUATION = ".,;:!?"
_GRAPH_SIGNAL_RE = re.compile(
    r"(?i)\b(relation|relationship|related|depend|depends|dependency|connect|"
    r"connects|connected|use|uses|using|architecture|component|store|stores|"
    r"stored|link|links|linked|graph|path|multi[- ]?hop)\b"
)
_RELATION_MARKERS = {
    "use",
    "uses",
    "using",
    "depend",
    "depends",
    "dependency",
    "connect",
    "connects",
    "connected",
    "relation",
    "relationship",
    "architecture",
    "store",
    "stores",
    "stored",
    "link",
    "links",
    "linked",
}
# Japanese relation markers. Japanese has no word boundaries, so these are
# matched via substring containment against the raw query rather than against
# tokenized words (do NOT wrap them in \b). Each contributes the same weight as
# an English relation marker. ``使っ`` covers conjugations like 使って / 使った.
_JAPANESE_RELATION_MARKERS = (
    "関係",
    "関連",
    "依存",
    "使用",
    "使っ",
    "つながり",
    "接続",
    "連携",
    "構成",
)


class GraphStoreProvider(StrEnum):
    POSTGRES = "postgres"
    NEO4J = "neo4j"


@dataclass(frozen=True)
class GraphNodeRef:
    provider: GraphStoreProvider
    node_id: str
    entity_id: int | None
    safe_label: str
    entity_type: str | None = None
    score: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "node_id": self.node_id,
            "entity_id": self.entity_id,
            "safe_label": self.safe_label,
            "entity_type": self.entity_type,
            "score": self.score,
            "metadata": validate_safe_graph_metadata(dict(self.metadata)),
        }


@dataclass(frozen=True)
class GraphRelationRef:
    provider: GraphStoreProvider
    relation_id: str
    source_node_id: str | None
    target_node_id: str | None
    relation_type: str
    safe_label: str
    score: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "relation_id": self.relation_id,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "relation_type": self.relation_type,
            "safe_label": self.safe_label,
            "score": self.score,
            "metadata": validate_safe_graph_metadata(dict(self.metadata)),
        }


@dataclass(frozen=True)
class GraphEvidenceRef:
    provider: GraphStoreProvider
    source_chunk_ids: tuple[int, ...]
    document_chunk_ids: tuple[int, ...]
    retrieval_run_item_ids: tuple[int, ...] | None = None
    evidence_hashes: tuple[str, ...] | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "provider": self.provider.value,
            "source_chunk_ids": list(self.source_chunk_ids),
            "document_chunk_ids": list(self.document_chunk_ids),
            "metadata": validate_safe_graph_metadata(dict(self.metadata)),
        }
        if self.retrieval_run_item_ids is not None:
            payload["retrieval_run_item_ids"] = list(self.retrieval_run_item_ids)
        if self.evidence_hashes is not None:
            payload["evidence_hashes"] = list(self.evidence_hashes)
        return payload


@dataclass(frozen=True)
class GraphPath:
    provider: GraphStoreProvider
    path_id: str
    node_refs: tuple[GraphNodeRef, ...]
    relation_refs: tuple[GraphRelationRef, ...]
    evidence_refs: tuple[GraphEvidenceRef, ...]
    source_chunk_ids: tuple[int, ...]
    safe_entity_labels: tuple[str, ...]
    relation_types: tuple[str, ...]
    depth: int
    path_score: float
    score_breakdown: dict[str, object] = field(default_factory=dict)

    def path_json(self) -> dict[str, object]:
        return validate_safe_graph_metadata(
            {
                "schema_version": GRAPH_PATH_SCHEMA_VERSION,
                "strategy_type": "graph",
                "provider": self.provider.value,
                "path_id": self.path_id,
                "node_refs": [node.to_json() for node in self.node_refs],
                "relation_refs": [relation.to_json() for relation in self.relation_refs],
                "evidence_refs": [evidence.to_json() for evidence in self.evidence_refs],
                "source_chunk_ids": list(self.source_chunk_ids),
                "safe_entity_labels": list(self.safe_entity_labels),
                "relation_types": list(self.relation_types),
                "path_score": self.path_score,
                "depth": self.depth,
                "score_breakdown": dict(self.score_breakdown),
            }
        )


@dataclass(frozen=True)
class GraphStoreResultMetadata:
    entity_lookup_count: int = 0
    relation_count: int = 0
    path_count: int = 0
    source_candidate_count: int = 0
    reason_codes: tuple[str, ...] = ()
    elapsed_ms: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "entity_lookup_count": self.entity_lookup_count,
            "relation_count": self.relation_count,
            "path_count": self.path_count,
            "source_candidate_count": self.source_candidate_count,
            "reason_codes": list(self.reason_codes),
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(frozen=True)
class GraphRetrievalSettings:
    enabled: bool = False
    provider: GraphStoreProvider | str | None = None
    max_start_entities: int = 5
    max_depth: int = 2
    max_paths: int = 20
    max_relations_per_entity: int = 20
    max_source_chunks: int = 20
    timeout_ms: int = 3000
    fallback_strategy: str = "hybrid"
    min_entity_match_score: float = 0.5

    def bounded(self) -> GraphRetrievalSettings:
        fallback_strategy = (
            self.fallback_strategy if self.fallback_strategy in {"dense", "hybrid"} else "hybrid"
        )
        return GraphRetrievalSettings(
            enabled=self.enabled,
            provider=(
                _coerce_graph_store_provider(self.provider) if self.provider is not None else None
            ),
            max_start_entities=_bounded_int(self.max_start_entities, 1, 20),
            max_depth=_bounded_int(self.max_depth, 1, 4),
            max_paths=_bounded_int(self.max_paths, 1, 100),
            max_relations_per_entity=_bounded_int(
                self.max_relations_per_entity,
                1,
                100,
            ),
            max_source_chunks=_bounded_int(self.max_source_chunks, 1, 100),
            timeout_ms=_bounded_int(self.timeout_ms, 100, 30_000),
            fallback_strategy=fallback_strategy,
            min_entity_match_score=max(
                0.0,
                min(1.0, float(self.min_entity_match_score)),
            ),
        )


@dataclass(frozen=True)
class GraphPathCandidate:
    path_id: str
    entity_ids: tuple[int, ...]
    relation_ids: tuple[int, ...]
    safe_entity_labels: tuple[str, ...]
    relation_types: tuple[str, ...]
    source_chunk_ids: tuple[int, ...]
    depth: int
    path_score: float
    entity_match_score: float
    relation_score: float
    provider: GraphStoreProvider = GraphStoreProvider.POSTGRES
    entity_types: tuple[str | None, ...] = ()
    relation_scores: tuple[float | None, ...] = ()
    relation_node_id_pairs: tuple[tuple[int, int], ...] = ()
    evidence_hashes: tuple[str, ...] = ()

    def to_graph_path(self, *, source_chunk_ids: tuple[int, ...] | None = None) -> GraphPath:
        chunk_ids = source_chunk_ids if source_chunk_ids is not None else self.source_chunk_ids
        node_refs = tuple(
            GraphNodeRef(
                provider=self.provider,
                node_id=str(entity_id),
                entity_id=entity_id,
                safe_label=(
                    self.safe_entity_labels[index]
                    if index < len(self.safe_entity_labels)
                    else f"entity:{entity_id}"
                ),
                entity_type=self.entity_types[index] if index < len(self.entity_types) else None,
                score=self.entity_match_score if index == 0 else None,
            )
            for index, entity_id in enumerate(self.entity_ids)
        )
        relation_refs: list[GraphRelationRef] = []
        for index, relation_id in enumerate(self.relation_ids):
            source_node_id: str | None
            target_node_id: str | None
            if index < len(self.relation_node_id_pairs):
                source_node_id = str(self.relation_node_id_pairs[index][0])
                target_node_id = str(self.relation_node_id_pairs[index][1])
            else:
                source_node_id = (
                    str(self.entity_ids[index]) if index < len(self.entity_ids) else None
                )
                target_node_id = (
                    str(self.entity_ids[index + 1]) if index + 1 < len(self.entity_ids) else None
                )
            relation_refs.append(
                GraphRelationRef(
                    provider=self.provider,
                    relation_id=str(relation_id),
                    source_node_id=source_node_id,
                    target_node_id=target_node_id,
                    relation_type=(
                        self.relation_types[index]
                        if index < len(self.relation_types)
                        else f"relation:{relation_id}"
                    ),
                    safe_label=(
                        self.relation_types[index]
                        if index < len(self.relation_types)
                        else f"relation:{relation_id}"
                    ),
                    score=(
                        self.relation_scores[index]
                        if index < len(self.relation_scores)
                        else self.relation_score
                    ),
                )
            )
        evidence_refs: tuple[GraphEvidenceRef, ...] = ()
        if chunk_ids:
            evidence_refs = (
                GraphEvidenceRef(
                    provider=self.provider,
                    source_chunk_ids=chunk_ids,
                    document_chunk_ids=chunk_ids,
                    evidence_hashes=self.evidence_hashes or None,
                ),
            )
        score_breakdown = {
            "schema_version": GRAPH_SCORE_SCHEMA_VERSION,
            "retrieval_source": "graph",
            "entity_match_score": self.entity_match_score,
            "relation_score": self.relation_score,
            "path_score": self.path_score,
            "source_chunk_ids_count": len(chunk_ids),
            "path_depth": self.depth,
        }
        return GraphPath(
            provider=self.provider,
            path_id=self.path_id,
            node_refs=node_refs,
            relation_refs=tuple(relation_refs),
            evidence_refs=evidence_refs,
            source_chunk_ids=chunk_ids,
            safe_entity_labels=self.safe_entity_labels,
            relation_types=self.relation_types,
            depth=self.depth,
            path_score=self.path_score,
            score_breakdown=score_breakdown,
        )

    def path_json(self, *, source_chunk_ids: tuple[int, ...] | None = None) -> dict[str, object]:
        return self.to_graph_path(source_chunk_ids=source_chunk_ids).path_json()


@dataclass(frozen=True)
class GraphSourceCandidate:
    document_chunk_id: int
    retrieval_score: float
    rank_order: int
    payload: dict[str, object]
    score_breakdown_json: dict[str, object]
    path_refs: tuple[str, ...]
    graph_path_candidates: tuple[GraphPathCandidate, ...] = field(repr=False)

    def to_vector_candidate(self) -> VectorSearchCandidate:
        return VectorSearchCandidate(
            document_chunk_id=self.document_chunk_id,
            retrieval_score=self.retrieval_score,
            qdrant_order=self.rank_order,
            payload=dict(self.payload),
        )


@dataclass(frozen=True)
class GraphRetrievalResult:
    provider: GraphStoreProvider
    query_hash: str
    paths: tuple[GraphPath, ...]
    source_chunk_ids: tuple[int, ...]
    score_breakdown: dict[str, object]
    latency_ms: int
    fallback_used: bool
    metadata: GraphStoreResultMetadata
    entity_lookup_count: int
    relation_count: int
    path_count: int
    source_candidate_count: int
    graph_candidates: tuple[GraphSourceCandidate, ...]
    reason_codes: tuple[str, ...]
    elapsed_ms: int

    @property
    def no_context(self) -> bool:
        return not self.graph_candidates

    def summary_fields(self) -> dict[str, object]:
        return {
            "graph_schema_version": GRAPH_RETRIEVAL_SCHEMA_VERSION,
            "graph_path_schema_version": GRAPH_PATH_SCHEMA_VERSION,
            "graph_store_provider": self.provider.value,
            "graph_entity_lookup_count": self.entity_lookup_count,
            "graph_relation_count": self.relation_count,
            "graph_path_count": self.path_count,
            "graph_source_candidate_count": self.source_candidate_count,
            "graph_no_context": self.no_context,
            "graph_reason_codes": list(self.reason_codes),
            "graph_elapsed_ms": self.elapsed_ms,
            "graph_fallback_used": self.fallback_used,
        }


class GraphStore(Protocol):
    provider: GraphStoreProvider

    def search(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        settings: GraphRetrievalSettings,
    ) -> GraphRetrievalResult: ...


class GraphEntityLookupService:
    def __init__(self, repository: GraphRetrievalRepository | None = None) -> None:
        self.repository = repository or GraphRetrievalRepository()

    def query_terms(self, query: str) -> tuple[str, ...]:
        terms: list[str] = []
        seen: set[str] = set()
        for match in _TOKEN_RE.finditer(query):
            term = _normalize_query_term(match.group(0))
            if not term or term in seen:
                continue
            terms.append(term)
            seen.add(term)
            if len(terms) >= 32:
                break
        return tuple(terms)

    def lookup(
        self,
        db: Session,
        *,
        query: str,
        settings: GraphRetrievalSettings,
        filters: RetrievalFilters,
    ) -> list[GraphEntityLookupResult]:
        return self.repository.lookup_entities(
            db,
            query_terms=self.query_terms(query),
            limit=settings.max_start_entities,
            min_match_score=settings.min_entity_match_score,
            filters=filters,
        )


class GraphScoreCalculator:
    def score_path(
        self,
        *,
        entity_match_score: float,
        relation_confidences: tuple[float, ...],
        depth: int,
    ) -> tuple[float, float]:
        relation_score = (
            sum(relation_confidences) / len(relation_confidences)
            if relation_confidences
            else entity_match_score
        )
        depth_penalty = max(0.1, 1.0 - max(0, depth - 1) * 0.1)
        path_score = entity_match_score * 0.45 + relation_score * 0.45 + depth_penalty * 0.10
        return round(max(0.0, min(1.0, relation_score)), 6), round(
            max(0.0, min(1.0, path_score)),
            6,
        )

    def score_breakdown(
        self,
        *,
        candidate: GraphSourceCandidate,
        path_depth: int,
        path_rank: int,
        selected_flag: bool,
    ) -> dict[str, object]:
        path = candidate.graph_path_candidates[0]
        return {
            "schema_version": GRAPH_SCORE_SCHEMA_VERSION,
            "retrieval_source": "graph",
            "entity_match_score": path.entity_match_score,
            "relation_score": path.relation_score,
            "path_score": path.path_score,
            "source_chunk_score": candidate.retrieval_score,
            "path_depth": path_depth,
            "path_rank": path_rank,
            "source_chunk_ids_count": len(path.source_chunk_ids),
            "selected_flag": selected_flag,
        }


class GraphPathSearchService:
    def __init__(
        self,
        *,
        repository: GraphRetrievalRepository | None = None,
        score_calculator: GraphScoreCalculator | None = None,
    ) -> None:
        self.repository = repository or GraphRetrievalRepository()
        self.score_calculator = score_calculator or GraphScoreCalculator()

    def search_paths(
        self,
        db: Session,
        *,
        start_entities: list[GraphEntityLookupResult],
        filters: RetrievalFilters,
        settings: GraphRetrievalSettings,
        started_at: float,
    ) -> tuple[list[GraphPathCandidate], int, list[str]]:
        start_ids = {item.entity.graph_entity_id for item in start_entities}
        if not start_ids:
            return [], 0, ["no_start_entities"]

        paths: list[GraphPathCandidate] = []
        reason_codes: list[str] = []
        relation_count = 0
        lookup_by_id = {item.entity.graph_entity_id: item for item in start_entities}
        adjacency: dict[int, list[GraphRelationRow]] = {}
        loaded_entity_ids: set[int] = set()
        seen_relation_ids: set[int] = set()
        path_collection_limit = min(
            1000,
            max(
                settings.max_paths,
                settings.max_paths * settings.max_depth * settings.max_relations_per_entity,
            ),
        )
        frontier_limit = min(
            500,
            max(
                settings.max_paths,
                settings.max_paths * settings.max_relations_per_entity,
            ),
        )
        frontier: list[tuple[int, tuple[int, ...], tuple[GraphRelationRow, ...], float]] = [
            (entity_id, (entity_id,), (), lookup.match_score)
            for entity_id, lookup in lookup_by_id.items()
        ]

        for _depth in range(settings.max_depth):
            if not frontier:
                break
            if _elapsed_ms(started_at) > settings.timeout_ms:
                reason_codes.append("graph_timeout")
                break

            pending_entity_ids = {
                current_id for current_id, *_rest in frontier if current_id not in loaded_entity_ids
            }
            if pending_entity_ids:
                relation_rows = self.repository.list_relations_for_entity_ids(
                    db,
                    entity_ids=pending_entity_ids,
                    max_relations_per_entity=settings.max_relations_per_entity,
                    filters=filters,
                    exclude_relation_ids=seen_relation_ids,
                )
                loaded_entity_ids.update(pending_entity_ids)
                for relation_row in relation_rows:
                    relation_id = relation_row.relation.graph_relation_id
                    if relation_id in seen_relation_ids:
                        continue
                    seen_relation_ids.add(relation_id)
                    relation_count += 1
                    adjacency.setdefault(relation_row.relation.source_entity_id, []).append(
                        relation_row
                    )
                    adjacency.setdefault(relation_row.relation.target_entity_id, []).append(
                        relation_row
                    )

            next_frontier: list[
                tuple[int, tuple[int, ...], tuple[GraphRelationRow, ...], float]
            ] = []
            for current_id, entity_path, relation_path, entity_match_score in frontier:
                relations = adjacency.get(current_id, [])
                expanded_relation_count = 0
                for relation_row in relations:
                    relation = relation_row.relation
                    next_id = (
                        relation.target_entity_id
                        if relation.source_entity_id == current_id
                        else relation.source_entity_id
                    )
                    if next_id in entity_path:
                        continue
                    if expanded_relation_count >= settings.max_relations_per_entity:
                        break
                    expanded_relation_count += 1
                    next_entity_path = (*entity_path, next_id)
                    next_relation_path = (*relation_path, relation_row)
                    relation_confidences = tuple(
                        float(item.relation.confidence)
                        if item.relation.confidence is not None
                        else 0.5
                        for item in next_relation_path
                    )
                    relation_score, path_score = self.score_calculator.score_path(
                        entity_match_score=entity_match_score,
                        relation_confidences=relation_confidences,
                        depth=len(next_relation_path),
                    )
                    source_chunk_ids = _source_chunk_ids(
                        next_relation_path,
                        settings.max_source_chunks,
                    )
                    labels = _path_labels(
                        next_entity_path,
                        next_relation_path,
                        lookup_by_id,
                    )
                    entity_types = _path_entity_types(
                        next_entity_path,
                        next_relation_path,
                        lookup_by_id,
                    )
                    relation_types = tuple(
                        validate_safe_graph_label(
                            item.relation.relation_type,
                            field_name="relation_type",
                            max_length=120,
                        )
                        for item in next_relation_path
                    )
                    if len(paths) < path_collection_limit:
                        paths.append(
                            GraphPathCandidate(
                                path_id=f"gp_{len(paths) + 1}",
                                entity_ids=next_entity_path,
                                relation_ids=tuple(
                                    item.relation.graph_relation_id for item in next_relation_path
                                ),
                                safe_entity_labels=labels,
                                relation_types=relation_types,
                                source_chunk_ids=source_chunk_ids,
                                depth=len(next_relation_path),
                                path_score=path_score,
                                entity_match_score=round(entity_match_score, 6),
                                relation_score=relation_score,
                                entity_types=entity_types,
                                relation_scores=tuple(
                                    round(score, 6) for score in relation_confidences
                                ),
                                relation_node_id_pairs=tuple(
                                    (
                                        item.relation.source_entity_id,
                                        item.relation.target_entity_id,
                                    )
                                    for item in next_relation_path
                                ),
                                evidence_hashes=tuple(
                                    item.relation.evidence_text_hash
                                    for item in next_relation_path
                                    if item.relation.evidence_text_hash is not None
                                ),
                            )
                        )
                    elif "max_paths_reached" not in reason_codes:
                        reason_codes.append("max_paths_reached")
                    if (
                        len(next_relation_path) < settings.max_depth
                        and len(next_frontier) < frontier_limit
                    ):
                        next_frontier.append(
                            (
                                next_id,
                                next_entity_path,
                                next_relation_path,
                                entity_match_score,
                            )
                        )
            frontier = next_frontier

        selected = _select_paths_at_cap(paths, settings.max_paths, reason_codes)
        return selected, relation_count, _dedupe(reason_codes)


class PostgresGraphStore:
    provider = GraphStoreProvider.POSTGRES

    def __init__(
        self,
        *,
        repository: GraphRetrievalRepository | None = None,
        entity_lookup: GraphEntityLookupService | None = None,
        path_search: GraphPathSearchService | None = None,
    ) -> None:
        self.repository = repository or GraphRetrievalRepository()
        self.entity_lookup = entity_lookup or GraphEntityLookupService(self.repository)
        self.path_search = path_search or GraphPathSearchService(repository=self.repository)

    def search(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        settings: GraphRetrievalSettings,
    ) -> GraphRetrievalResult:
        started_at = time.monotonic()
        bounded_settings = settings.bounded()
        reason_codes: list[str] = []
        if not bounded_settings.enabled:
            return _graph_retrieval_result(
                provider=self.provider,
                query=query,
                graph_candidates=(),
                paths=(),
                entity_lookup_count=0,
                relation_count=0,
                path_count=0,
                source_candidate_count=0,
                reason_codes=("graph_disabled",),
                elapsed_ms=_elapsed_ms(started_at),
            )
        if not self.repository.has_active_graph_sources(db, filters=filters):
            return _graph_retrieval_result(
                provider=self.provider,
                query=query,
                graph_candidates=(),
                paths=(),
                entity_lookup_count=0,
                relation_count=0,
                path_count=0,
                source_candidate_count=0,
                reason_codes=("graph_unavailable",),
                elapsed_ms=_elapsed_ms(started_at),
            )

        start_entities = self.entity_lookup.lookup(
            db,
            query=query,
            settings=bounded_settings,
            filters=filters,
        )
        if not start_entities:
            return _graph_retrieval_result(
                provider=self.provider,
                query=query,
                graph_candidates=(),
                paths=(),
                entity_lookup_count=0,
                relation_count=0,
                path_count=0,
                source_candidate_count=0,
                reason_codes=("no_entity_matches",),
                elapsed_ms=_elapsed_ms(started_at),
            )
        paths, relation_count, path_reasons = self.path_search.search_paths(
            db,
            start_entities=start_entities,
            filters=filters,
            settings=bounded_settings,
            started_at=started_at,
        )
        reason_codes.extend(path_reasons)
        if not paths:
            reason_codes.append("no_relation_paths")
        graph_candidates = _source_candidates(
            paths,
            top_k=max(1, top_k),
            max_source_chunks=bounded_settings.max_source_chunks,
        )
        if not graph_candidates:
            mention_paths = self._mention_only_paths_for_entities(
                db,
                start_entities=start_entities,
                filters=filters,
                settings=bounded_settings,
            )
            if mention_paths:
                paths = mention_paths
                graph_candidates = _source_candidates(
                    paths,
                    top_k=max(1, top_k),
                    max_source_chunks=bounded_settings.max_source_chunks,
                )
                reason_codes.append("mention_only_paths")
            else:
                reason_codes.append("no_chunk_backed_paths")
        return _graph_retrieval_result(
            provider=self.provider,
            query=query,
            graph_candidates=tuple(graph_candidates),
            paths=tuple(path.to_graph_path() for path in paths),
            entity_lookup_count=len(start_entities),
            relation_count=relation_count,
            path_count=len(paths),
            source_candidate_count=len(graph_candidates),
            reason_codes=tuple(_dedupe(reason_codes or ["graph_search_completed"])),
            elapsed_ms=_elapsed_ms(started_at),
        )

    def _mention_only_paths_for_entities(
        self,
        db: Session,
        *,
        start_entities: list[GraphEntityLookupResult],
        filters: RetrievalFilters,
        settings: GraphRetrievalSettings,
    ) -> list[GraphPathCandidate]:
        source_rows = self.repository.list_mentions_for_entity_ids(
            db,
            entity_ids=[item.entity.graph_entity_id for item in start_entities],
            filters=filters,
            max_source_chunks=settings.max_source_chunks,
        )
        return _mention_only_paths(start_entities, source_rows, settings)

    def path_records(
        self,
        *,
        retrieval_run_id: int,
        candidates: tuple[GraphSourceCandidate, ...],
    ) -> list[GraphRetrievalPathCreate]:
        return _path_records(retrieval_run_id=retrieval_run_id, candidates=candidates)


class Neo4jGraphStore:
    provider = GraphStoreProvider.NEO4J

    def search(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        settings: GraphRetrievalSettings,
    ) -> GraphRetrievalResult:
        started_at = time.monotonic()
        return _graph_retrieval_result(
            provider=self.provider,
            query=query,
            graph_candidates=(),
            paths=(),
            entity_lookup_count=0,
            relation_count=0,
            path_count=0,
            source_candidate_count=0,
            reason_codes=("graph_store_provider_unavailable", "neo4j_not_configured"),
            elapsed_ms=_elapsed_ms(started_at),
            fallback_used=True,
        )


class GraphStoreResolver:
    def __init__(
        self,
        *,
        provider: GraphStoreProvider | str = GraphStoreProvider.POSTGRES,
        postgres_store: GraphStore | None = None,
        neo4j_store: GraphStore | None = None,
    ) -> None:
        self.provider = _coerce_graph_store_provider(provider)
        self.postgres_store = postgres_store or PostgresGraphStore()
        self.neo4j_store = neo4j_store or Neo4jGraphStore()

    def resolve(self, provider: GraphStoreProvider | str | None = None) -> GraphStore:
        resolved_provider = (
            self.provider if provider is None else _coerce_graph_store_provider(provider)
        )
        if resolved_provider == GraphStoreProvider.NEO4J:
            return self.neo4j_store
        return self.postgres_store


class GraphRetrievalStrategy:
    def __init__(
        self,
        *,
        resolver: GraphStoreResolver | None = None,
        graph_store: GraphStore | None = None,
        provider: GraphStoreProvider | str = GraphStoreProvider.POSTGRES,
    ) -> None:
        self.graph_store: GraphStore | None
        if graph_store is not None:
            self.resolver = GraphStoreResolver(
                provider=graph_store.provider,
                postgres_store=graph_store,
            )
            self.graph_store = graph_store
            return
        self.resolver = resolver or GraphStoreResolver(
            provider=provider,
        )
        self.graph_store = None

    def search(
        self,
        db: Session,
        *,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
        settings: GraphRetrievalSettings,
    ) -> GraphRetrievalResult:
        bounded_settings = settings.bounded()
        store = self.graph_store or self.resolver.resolve(bounded_settings.provider)
        return store.search(
            db,
            query=query,
            top_k=top_k,
            filters=filters,
            settings=bounded_settings,
        )

    def path_records(
        self,
        *,
        retrieval_run_id: int,
        candidates: tuple[GraphSourceCandidate, ...],
    ) -> list[GraphRetrievalPathCreate]:
        return _path_records(retrieval_run_id=retrieval_run_id, candidates=candidates)


def graph_query_signal_score(query: str) -> float:
    tokens = [match.group(0).lower() for match in _TOKEN_RE.finditer(query)]
    if not tokens:
        return 0.0
    # Japanese has no word boundaries, so match relation markers via substring
    # containment against the raw query (English markers stay tokenized via the
    # boundary-aware signal regex / marker set). A Japanese marker counts as both
    # a graph signal hit and a relation marker, mirroring how English relation
    # words appear in both ``_GRAPH_SIGNAL_RE`` and ``_RELATION_MARKERS``.
    japanese_relation_markers = sum(1 for marker in _JAPANESE_RELATION_MARKERS if marker in query)
    signal_hits = 1 if (_GRAPH_SIGNAL_RE.search(query) or japanese_relation_markers) else 0
    relation_markers = sum(1 for token in tokens if token in _RELATION_MARKERS)
    relation_markers += japanese_relation_markers
    multi_entity_hint = 1 if sum(1 for token in tokens if token[:1].isalpha()) >= 3 else 0
    return round(
        min(
            1.0,
            signal_hits * 0.45 + relation_markers * 0.15 + multi_entity_hint * 0.25,
        ),
        6,
    )


def _path_records(
    *,
    retrieval_run_id: int,
    candidates: tuple[GraphSourceCandidate, ...],
) -> list[GraphRetrievalPathCreate]:
    paths_by_id: dict[str, GraphPathCandidate] = {}
    selected_chunk_ids_by_path: dict[str, list[int]] = {}
    for candidate in candidates:
        for path in candidate.graph_path_candidates:
            if candidate.document_chunk_id not in path.source_chunk_ids:
                continue
            paths_by_id.setdefault(path.path_id, path)
            selected_chunk_ids = selected_chunk_ids_by_path.setdefault(path.path_id, [])
            if candidate.document_chunk_id not in selected_chunk_ids:
                selected_chunk_ids.append(candidate.document_chunk_id)

    records: list[GraphRetrievalPathCreate] = []
    for path_id, path in paths_by_id.items():
        selected_chunk_ids = selected_chunk_ids_by_path.get(path_id, [])
        if not selected_chunk_ids:
            continue
        selected_chunk_ids_tuple = tuple(selected_chunk_ids)
        graph_path = path.to_graph_path(source_chunk_ids=selected_chunk_ids_tuple)
        records.append(
            GraphRetrievalPathCreate(
                retrieval_run_id=retrieval_run_id,
                path_json=graph_path.path_json(),
                score_breakdown_json=graph_path.score_breakdown,
                source_chunk_ids_json=list(selected_chunk_ids_tuple),
            )
        )
    return records


def _graph_retrieval_result(
    *,
    provider: GraphStoreProvider,
    query: str,
    graph_candidates: tuple[GraphSourceCandidate, ...],
    paths: tuple[GraphPath, ...],
    entity_lookup_count: int,
    relation_count: int,
    path_count: int,
    source_candidate_count: int,
    reason_codes: tuple[str, ...],
    elapsed_ms: int,
    fallback_used: bool = False,
) -> GraphRetrievalResult:
    path_source_chunk_ids = tuple(
        _dedupe_ints(chunk_id for path in paths for chunk_id in path.source_chunk_ids)
    )
    source_chunk_ids = tuple(
        _dedupe_ints(
            [
                *(candidate.document_chunk_id for candidate in graph_candidates),
                *path_source_chunk_ids,
            ]
        )
    )
    score_breakdown = validate_safe_graph_metadata(
        {
            "schema_version": GRAPH_SCORE_SCHEMA_VERSION,
            "retrieval_source": "graph",
            "provider": provider.value,
            "path_count": path_count,
            "source_candidate_count": source_candidate_count,
        }
    )
    metadata = GraphStoreResultMetadata(
        entity_lookup_count=entity_lookup_count,
        relation_count=relation_count,
        path_count=path_count,
        source_candidate_count=source_candidate_count,
        reason_codes=reason_codes,
        elapsed_ms=elapsed_ms,
    )
    return GraphRetrievalResult(
        provider=provider,
        query_hash=_safe_query_hash(query),
        paths=paths,
        source_chunk_ids=source_chunk_ids,
        score_breakdown=score_breakdown,
        latency_ms=elapsed_ms,
        fallback_used=fallback_used,
        metadata=metadata,
        entity_lookup_count=entity_lookup_count,
        relation_count=relation_count,
        path_count=path_count,
        source_candidate_count=source_candidate_count,
        graph_candidates=graph_candidates,
        reason_codes=reason_codes,
        elapsed_ms=elapsed_ms,
    )


def _source_candidates(
    paths: list[GraphPathCandidate],
    *,
    top_k: int,
    max_source_chunks: int,
) -> list[GraphSourceCandidate]:
    by_chunk_id: dict[int, list[GraphPathCandidate]] = {}
    for path in paths:
        for chunk_id in path.source_chunk_ids:
            by_chunk_id.setdefault(chunk_id, []).append(path)
    candidates: list[GraphSourceCandidate] = []
    for chunk_id, chunk_paths in by_chunk_id.items():
        ranked_paths = sorted(
            chunk_paths,
            key=lambda path: path.path_score,
            reverse=True,
        )
        score = round(
            sum(path.path_score for path in ranked_paths[:3]) / min(3, len(ranked_paths)),
            6,
        )
        path_refs = tuple(path.path_id for path in ranked_paths[:5])
        payload = {
            "retrieval_source": "graph",
            "graph_path_refs": list(path_refs),
            "source_chunk_ids_json": [chunk_id],
            "path_count": len(ranked_paths),
        }
        candidates.append(
            GraphSourceCandidate(
                document_chunk_id=chunk_id,
                retrieval_score=score,
                rank_order=0,
                payload=payload,
                score_breakdown_json={},
                path_refs=path_refs,
                graph_path_candidates=tuple(ranked_paths[:5]),
            )
        )
    candidates.sort(
        key=lambda item: (item.retrieval_score, -item.document_chunk_id),
        reverse=True,
    )
    # Honor the configured graph source budget as a global cap across all paths:
    # relation-backed paths can surface up to ``top_k`` distinct chunks, but the
    # configured ``max_source_chunks`` must bound the aggregated source set too.
    # Candidates are already sorted best-first, so slicing preserves ordering.
    distinct_chunk_cap = max(1, min(top_k, max_source_chunks))
    calculator = GraphScoreCalculator()
    ranked: list[GraphSourceCandidate] = []
    for rank, candidate in enumerate(candidates[:distinct_chunk_cap], start=1):
        ranked_candidate = GraphSourceCandidate(
            document_chunk_id=candidate.document_chunk_id,
            retrieval_score=candidate.retrieval_score,
            rank_order=rank,
            payload={**candidate.payload, "rank_order": rank},
            score_breakdown_json={},
            path_refs=candidate.path_refs,
            graph_path_candidates=candidate.graph_path_candidates,
        )
        ranked.append(
            GraphSourceCandidate(
                document_chunk_id=ranked_candidate.document_chunk_id,
                retrieval_score=ranked_candidate.retrieval_score,
                rank_order=ranked_candidate.rank_order,
                payload=ranked_candidate.payload,
                score_breakdown_json=calculator.score_breakdown(
                    candidate=ranked_candidate,
                    path_depth=ranked_candidate.graph_path_candidates[0].depth,
                    path_rank=rank,
                    selected_flag=True,
                ),
                path_refs=ranked_candidate.path_refs,
                graph_path_candidates=ranked_candidate.graph_path_candidates,
            )
        )
    return ranked


def _mention_only_paths(
    start_entities: list[GraphEntityLookupResult],
    source_rows: list[GraphChunkRow],
    settings: GraphRetrievalSettings,
) -> list[GraphPathCandidate]:
    lookup_by_id = {lookup.entity.graph_entity_id: lookup for lookup in start_entities}
    paths: list[GraphPathCandidate] = []
    seen: set[tuple[int, int]] = set()
    for source_row in source_rows:
        if source_row.graph_entity_id is None:
            continue
        lookup = lookup_by_id.get(source_row.graph_entity_id)
        if lookup is None:
            continue
        dedupe_key = (
            lookup.entity.graph_entity_id,
            source_row.chunk.document_chunk_id,
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        label = validate_safe_graph_label(
            lookup.entity.canonical_name,
            field_name="canonical_name",
            max_length=255,
        )
        paths.append(
            GraphPathCandidate(
                path_id=f"gp_{len(paths) + 1}",
                entity_ids=(lookup.entity.graph_entity_id,),
                relation_ids=(),
                safe_entity_labels=(label,),
                relation_types=(),
                source_chunk_ids=(source_row.chunk.document_chunk_id,),
                depth=0,
                path_score=round(max(0.0, min(1.0, lookup.match_score)), 6),
                entity_match_score=round(lookup.match_score, 6),
                relation_score=round(lookup.match_score, 6),
                entity_types=(lookup.entity.entity_type,),
            )
        )
        if len(paths) >= settings.max_paths:
            break
    return paths


def _path_labels(
    entity_path: tuple[int, ...],
    relation_path: tuple[GraphRelationRow, ...],
    lookup_by_id: dict[int, GraphEntityLookupResult],
) -> tuple[str, ...]:
    entities_by_id = {
        lookup.entity.graph_entity_id: lookup.entity for lookup in lookup_by_id.values()
    }
    for relation in relation_path:
        entities_by_id[relation.source_entity.graph_entity_id] = relation.source_entity
        entities_by_id[relation.target_entity.graph_entity_id] = relation.target_entity
    labels: list[str] = []
    for entity_id in entity_path:
        entity = entities_by_id.get(entity_id)
        if entity is None:
            continue
        labels.append(
            validate_safe_graph_label(
                entity.canonical_name,
                field_name="canonical_name",
                max_length=255,
            )
        )
    return tuple(labels)


def _path_entity_types(
    entity_path: tuple[int, ...],
    relation_path: tuple[GraphRelationRow, ...],
    lookup_by_id: dict[int, GraphEntityLookupResult],
) -> tuple[str | None, ...]:
    entities_by_id = {
        lookup.entity.graph_entity_id: lookup.entity for lookup in lookup_by_id.values()
    }
    for relation in relation_path:
        entities_by_id[relation.source_entity.graph_entity_id] = relation.source_entity
        entities_by_id[relation.target_entity.graph_entity_id] = relation.target_entity
    entity_types: list[str | None] = []
    for entity_id in entity_path:
        entity = entities_by_id.get(entity_id)
        if entity is None or entity.entity_type is None:
            entity_types.append(None)
            continue
        entity_types.append(
            validate_safe_graph_label(
                entity.entity_type,
                field_name="entity_type",
                max_length=80,
            )
        )
    return tuple(entity_types)


def _source_chunk_ids(
    relation_path: tuple[GraphRelationRow, ...],
    max_source_chunks: int,
) -> tuple[int, ...]:
    chunk_ids: list[int] = []
    seen: set[int] = set()
    for relation in relation_path:
        chunk_id = relation.relation.source_document_chunk_id
        if chunk_id is None or chunk_id in seen:
            continue
        chunk_ids.append(chunk_id)
        seen.add(chunk_id)
        if len(chunk_ids) >= max_source_chunks:
            break
    return tuple(chunk_ids)


def _bounded_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _coerce_graph_store_provider(provider: GraphStoreProvider | str) -> GraphStoreProvider:
    if isinstance(provider, GraphStoreProvider):
        return provider
    normalized = str(provider).strip().lower()
    if normalized == GraphStoreProvider.NEO4J.value:
        return GraphStoreProvider.NEO4J
    return GraphStoreProvider.POSTGRES


def _normalize_query_term(term: str) -> str:
    return term.strip().lower().rstrip(_TRAILING_TOKEN_PUNCTUATION)


def _safe_query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def _path_sort_key(path: GraphPathCandidate) -> tuple[float, int, str]:
    return (path.path_score, -path.depth, path.path_id)


def _select_paths_at_cap(
    paths: list[GraphPathCandidate],
    max_paths: int,
    reason_codes: list[str],
) -> list[GraphPathCandidate]:
    """Select up to ``max_paths`` paths, preferring chunk-backed ones at the cap.

    A path whose ``source_chunk_ids`` is empty cannot contribute source-backed
    evidence to ``_source_candidates()``. If more than ``max_paths`` higher-scoring
    source-less paths exist, a naive top-N slice would drop chunk-backed paths and
    starve relation-backed evidence, forcing the mention-only fallback. Two-pass
    selection fills the cap with chunk-backed paths first, then source-less paths,
    both in sorted order, then re-sorts the result with the same key so downstream
    ordering semantics are unchanged. When this preference actually displaces a
    source-less path, the ``chunk_backed_paths_preferred`` reason code is appended.
    """
    ordered = sorted(paths, key=_path_sort_key, reverse=True)
    naive_selection = ordered[:max_paths]
    chunk_backed = [path for path in ordered if path.source_chunk_ids]
    source_less = [path for path in ordered if not path.source_chunk_ids]
    selected = (chunk_backed + source_less)[:max_paths]
    if {id(path) for path in selected} != {id(path) for path in naive_selection}:
        reason_codes.append("chunk_backed_paths_preferred")
    selected.sort(key=_path_sort_key, reverse=True)
    return selected


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def _dedupe_ints(values: Iterable[int]) -> list[int]:
    deduped: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value <= 0 or value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped
