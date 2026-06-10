from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.rag.retrieval import RetrievalFilters, VectorSearchCandidate
from app.repositories.graph_retrieval_repository import (
    GraphChunkRow,
    GraphEntityLookupResult,
    GraphRelationRow,
    GraphRetrievalRepository,
)
from app.schemas.graph import GraphRetrievalPathCreate, validate_safe_graph_label

GRAPH_RETRIEVAL_SCHEMA_VERSION = "phase3.graph_retrieval.v1"
GRAPH_PATH_SCHEMA_VERSION = "phase3.graph_path.v1"
GRAPH_SCORE_SCHEMA_VERSION = "phase3.graph_score.v1"
_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{1,79}")
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


@dataclass(frozen=True)
class GraphRetrievalSettings:
    enabled: bool = False
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
            self.fallback_strategy
            if self.fallback_strategy in {"dense", "hybrid"}
            else "hybrid"
        )
        return GraphRetrievalSettings(
            enabled=self.enabled,
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

    def path_json(self) -> dict[str, object]:
        return {
            "schema_version": GRAPH_PATH_SCHEMA_VERSION,
            "strategy_type": "graph",
            "path_id": self.path_id,
            "entity_ids": list(self.entity_ids),
            "relation_ids": list(self.relation_ids),
            "safe_entity_labels": list(self.safe_entity_labels),
            "relation_types": list(self.relation_types),
            "source_chunk_ids": list(self.source_chunk_ids),
            "path_score": self.path_score,
            "depth": self.depth,
        }


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
            "graph_entity_lookup_count": self.entity_lookup_count,
            "graph_relation_count": self.relation_count,
            "graph_path_count": self.path_count,
            "graph_source_candidate_count": self.source_candidate_count,
            "graph_no_context": self.no_context,
            "graph_reason_codes": list(self.reason_codes),
            "graph_elapsed_ms": self.elapsed_ms,
        }


class GraphEntityLookupService:
    def __init__(self, repository: GraphRetrievalRepository | None = None) -> None:
        self.repository = repository or GraphRetrievalRepository()

    def query_terms(self, query: str) -> tuple[str, ...]:
        terms: list[str] = []
        seen: set[str] = set()
        for match in _TOKEN_RE.finditer(query):
            term = match.group(0).strip().lower()
            if term in seen or len(term) < 2:
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
    ) -> list[GraphEntityLookupResult]:
        return self.repository.lookup_entities(
            db,
            query_terms=self.query_terms(query),
            limit=settings.max_start_entities,
            min_match_score=settings.min_entity_match_score,
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
        path_score = (
            entity_match_score * 0.45
            + relation_score * 0.45
            + depth_penalty * 0.10
        )
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
                current_id
                for current_id, *_rest in frontier
                if current_id not in loaded_entity_ids
            }
            if pending_entity_ids:
                relation_rows = self.repository.list_relations_for_entity_ids(
                    db,
                    entity_ids=pending_entity_ids,
                    max_relations_per_entity=settings.max_relations_per_entity,
                    filters=filters,
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
                for relation_row in adjacency.get(current_id, [])[ : settings.max_relations_per_entity]:
                    relation = relation_row.relation
                    next_id = (
                        relation.target_entity_id
                        if relation.source_entity_id == current_id
                        else relation.source_entity_id
                    )
                    if next_id in entity_path:
                        continue
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
                    relation_types = tuple(
                        validate_safe_graph_label(
                            item.relation.relation_type,
                            field_name="relation_type",
                            max_length=120,
                        )
                        for item in next_relation_path
                    )
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
                        )
                    )
                    if len(paths) >= settings.max_paths:
                        reason_codes.append("max_paths_reached")
                        break
                    if len(next_relation_path) < settings.max_depth:
                        next_frontier.append(
                            (
                                next_id,
                                next_entity_path,
                                next_relation_path,
                                entity_match_score,
                            )
                        )
                if len(paths) >= settings.max_paths:
                    break
            if len(paths) >= settings.max_paths:
                break
            frontier = next_frontier

        paths.sort(
            key=lambda path: (path.path_score, -path.depth, path.path_id),
            reverse=True,
        )
        return paths[: settings.max_paths], relation_count, _dedupe(reason_codes)


class GraphRetrievalStrategy:
    def __init__(
        self,
        *,
        repository: GraphRetrievalRepository | None = None,
        entity_lookup: GraphEntityLookupService | None = None,
        path_search: GraphPathSearchService | None = None,
    ) -> None:
        self.repository = repository or GraphRetrievalRepository()
        self.entity_lookup = entity_lookup or GraphEntityLookupService(self.repository)
        self.path_search = path_search or GraphPathSearchService(
            repository=self.repository,
        )

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
            return GraphRetrievalResult(
                0,
                0,
                0,
                0,
                (),
                ("graph_disabled",),
                _elapsed_ms(started_at),
            )
        if not self.repository.has_active_graph_sources(db, filters=filters):
            return GraphRetrievalResult(
                0,
                0,
                0,
                0,
                (),
                ("graph_unavailable",),
                _elapsed_ms(started_at),
            )

        start_entities = self.entity_lookup.lookup(
            db,
            query=query,
            settings=bounded_settings,
        )
        if not start_entities:
            return GraphRetrievalResult(
                0,
                0,
                0,
                0,
                (),
                ("no_entity_matches",),
                _elapsed_ms(started_at),
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
        graph_candidates = _source_candidates(paths, top_k=max(1, top_k))
        if not graph_candidates:
            mention_paths = self._mention_only_paths_for_entities(
                db,
                start_entities=start_entities,
                filters=filters,
                settings=bounded_settings,
            )
            if mention_paths:
                paths = mention_paths
                graph_candidates = _source_candidates(paths, top_k=max(1, top_k))
                reason_codes.append("mention_only_paths")
            else:
                reason_codes.append("no_chunk_backed_paths")
        return GraphRetrievalResult(
            entity_lookup_count=len(start_entities),
            relation_count=relation_count,
            path_count=len(paths),
            source_candidate_count=len(graph_candidates),
            graph_candidates=tuple(graph_candidates),
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
            entity_ids={item.entity.graph_entity_id for item in start_entities},
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
        paths_by_id: dict[str, GraphPathCandidate] = {}
        selected_chunk_ids_by_path: dict[str, list[int]] = {}
        for candidate in candidates:
            for path in candidate.graph_path_candidates:
                if candidate.document_chunk_id not in path.source_chunk_ids:
                    continue
                paths_by_id.setdefault(path.path_id, path)
                selected_chunk_ids = selected_chunk_ids_by_path.setdefault(
                    path.path_id,
                    [],
                )
                if candidate.document_chunk_id not in selected_chunk_ids:
                    selected_chunk_ids.append(candidate.document_chunk_id)

        records: list[GraphRetrievalPathCreate] = []
        for path_id, path in paths_by_id.items():
            selected_chunk_ids = selected_chunk_ids_by_path.get(path_id, [])
            if not selected_chunk_ids:
                continue
            path_json = path.path_json()
            path_json["source_chunk_ids"] = list(selected_chunk_ids)
            records.append(
                GraphRetrievalPathCreate(
                    retrieval_run_id=retrieval_run_id,
                    path_json=path_json,
                    score_breakdown_json={
                        "schema_version": GRAPH_SCORE_SCHEMA_VERSION,
                        "retrieval_source": "graph",
                        "entity_match_score": path.entity_match_score,
                        "relation_score": path.relation_score,
                        "path_score": path.path_score,
                        "source_chunk_ids_count": len(selected_chunk_ids),
                        "path_depth": path.depth,
                    },
                    source_chunk_ids_json=list(selected_chunk_ids),
                )
            )
        return records


def graph_query_signal_score(query: str) -> float:
    tokens = [match.group(0).lower() for match in _TOKEN_RE.finditer(query)]
    if not tokens:
        return 0.0
    signal_hits = 1 if _GRAPH_SIGNAL_RE.search(query) else 0
    relation_markers = sum(1 for token in tokens if token in _RELATION_MARKERS)
    multi_entity_hint = (
        1 if sum(1 for token in tokens if token[:1].isalpha()) >= 3 else 0
    )
    return round(
        min(
            1.0,
            signal_hits * 0.45 + relation_markers * 0.15 + multi_entity_hint * 0.25,
        ),
        6,
    )


def _source_candidates(
    paths: list[GraphPathCandidate],
    *,
    top_k: int,
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
    calculator = GraphScoreCalculator()
    ranked: list[GraphSourceCandidate] = []
    for rank, candidate in enumerate(candidates[:top_k], start=1):
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


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped
