from __future__ import annotations

import math
import re
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ResourceNotFound
from app.rag.graph_citations import (
    GRAPH_CITATION_TRACE_SCHEMA_VERSION,
    GraphCitationBuilder,
    GraphPathSourceLocator,
    GraphPathValidator,
    ValidatedGraphPath,
)
from app.rag.trace import TraceRedactor
from app.repositories.graph_retrieval_repository import GraphRetrievalRepository
from app.repositories.retrieval_repository import RetrievalRepository
from app.schemas.graph import validate_safe_graph_label
from app.schemas.rag import (
    GraphCitationCoverageResponse,
    GraphDebugNodeRef,
    GraphDebugRelationRef,
    GraphDebugSourceMapping,
    GraphPathDebugTrace,
    GraphRunDebugTraceResponse,
)

_UNSAFE_DEBUG_TEXT_RE = re.compile(
    r"(raw[_ -]?(graph[_ -]?)?evidence|raw[_ -]?(document|chunk|prompt)|"
    r"full[_ -]?context|chunk[_ -]?text|document[_ -]?text|content[_ -]?text|"
    r"api[_ -]?key|secret|token|credential|password)",
    re.IGNORECASE,
)


class GraphDebugTraceService:
    def __init__(
        self,
        *,
        retrieval_repository: RetrievalRepository | None = None,
        graph_repository: GraphRetrievalRepository | None = None,
        source_locator: GraphPathSourceLocator | None = None,
        path_validator: GraphPathValidator | None = None,
        citation_builder: GraphCitationBuilder | None = None,
    ) -> None:
        self.retrieval_repository = retrieval_repository or RetrievalRepository()
        self.graph_repository = graph_repository or GraphRetrievalRepository()
        self.source_locator = source_locator or GraphPathSourceLocator()
        self.path_validator = path_validator or GraphPathValidator()
        self.citation_builder = citation_builder or GraphCitationBuilder(
            snippet_max_chars=get_settings().citation_preview_max_chars
        )

    def get_graph_trace(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
    ) -> GraphRunDebugTraceResponse:
        run = self.retrieval_repository.get_run(db, retrieval_run_id=retrieval_run_id)
        if run is None:
            raise ResourceNotFound()
        paths = self.graph_repository.list_graph_retrieval_paths(
            db,
            retrieval_run_id=retrieval_run_id,
        )
        located_sources = self.source_locator.locate(
            db,
            retrieval_run_id=retrieval_run_id,
            paths=paths,
        )
        validated_paths = self.path_validator.validate(
            paths=paths,
            located_sources=located_sources,
        )
        build_result = self.citation_builder.build(
            validated_paths=validated_paths,
            located_sources=located_sources,
        )
        coverage = build_result.coverage
        return GraphRunDebugTraceResponse(
            schema_version=GRAPH_CITATION_TRACE_SCHEMA_VERSION,
            retrieval_run_id=retrieval_run_id,
            graph_path_count=coverage.path_count,
            valid_path_count=coverage.valid_path_count,
            citable_path_count=coverage.citable_path_count,
            excluded_path_count=coverage.excluded_path_count,
            citation_source_count=coverage.citation_source_count,
            coverage=GraphCitationCoverageResponse(
                path_count=coverage.path_count,
                valid_path_count=coverage.valid_path_count,
                citable_path_count=coverage.citable_path_count,
                excluded_path_count=coverage.excluded_path_count,
                source_chunk_count=coverage.source_chunk_count,
                resolved_source_chunk_count=coverage.resolved_source_chunk_count,
                citable_source_chunk_count=coverage.citable_source_chunk_count,
                citation_source_count=coverage.citation_source_count,
                source_chunk_coverage_ratio=coverage.source_chunk_coverage_ratio,
                citation_coverage_ratio=coverage.citation_coverage_ratio,
                reason_codes=list(coverage.reason_codes),
            ),
            paths=[_graph_path_debug_trace(path) for path in build_result.paths],
        )


def _graph_path_debug_trace(path: ValidatedGraphPath) -> GraphPathDebugTrace:
    path_json = TraceRedactor.safe_dict(path.path.path_json)
    return GraphPathDebugTrace(
        graph_retrieval_path_id=path.path.graph_retrieval_path_id,
        path_id=path.path_id,
        provider=path.provider,
        validation_status=path.validation_status,
        reason_codes=list(path.reason_codes),
        safe_metadata=path.safe_metadata,
        source_chunk_ids=list(path.source_chunk_ids),
        depth=_safe_int(path_json.get("depth")),
        path_score=_safe_score(path_json.get("path_score")),
        safe_entity_labels=_safe_string_list(path_json.get("safe_entity_labels"), max_length=255),
        relation_types=_safe_string_list(path_json.get("relation_types"), max_length=120),
        node_refs=_node_refs(path_json.get("node_refs")),
        relation_refs=_relation_refs(path_json.get("relation_refs")),
        source_mappings=[
            GraphDebugSourceMapping(
                source_chunk_id=mapping.source_chunk_id,
                document_chunk_id=mapping.document_chunk_id,
                retrieval_run_item_id=mapping.retrieval_run_item_id,
                selected_flag=mapping.selected_flag,
                citation_ids=list(mapping.citation_ids),
                local_citation_ids=list(mapping.local_citation_ids),
            )
            for mapping in path.source_mappings
        ],
    )


def _node_refs(value: object) -> list[GraphDebugNodeRef]:
    if not isinstance(value, list):
        return []
    refs: list[GraphDebugNodeRef] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        safe_label = _safe_string(item.get("safe_label"), max_length=255)
        node_id = _safe_string(item.get("node_id"), max_length=120)
        if safe_label is None or node_id is None:
            continue
        refs.append(
            GraphDebugNodeRef(
                provider=_safe_string(item.get("provider"), max_length=40) or "unknown",
                node_id=node_id,
                entity_id=_safe_int(item.get("entity_id")),
                safe_label=safe_label,
                entity_type=_safe_string(item.get("entity_type"), max_length=80),
            )
        )
    return refs


def _relation_refs(value: object) -> list[GraphDebugRelationRef]:
    if not isinstance(value, list):
        return []
    refs: list[GraphDebugRelationRef] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        relation_id = _safe_string(item.get("relation_id"), max_length=120)
        relation_type = _safe_string(item.get("relation_type"), max_length=120)
        if relation_id is None or relation_type is None:
            continue
        refs.append(
            GraphDebugRelationRef(
                provider=_safe_string(item.get("provider"), max_length=40) or "unknown",
                relation_id=relation_id,
                source_node_id=_safe_string(item.get("source_node_id"), max_length=120),
                target_node_id=_safe_string(item.get("target_node_id"), max_length=120),
                relation_type=relation_type,
                safe_label=_safe_string(item.get("safe_label"), max_length=120) or relation_type,
            )
        )
    return refs


def _safe_string_list(value: object, *, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    safe: list[str] = []
    for item in value:
        text = _safe_string(item, max_length=max_length)
        if text is not None:
            safe.append(text)
    return safe


def _safe_string(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    if _UNSAFE_DEBUG_TEXT_RE.search(safe):
        return None
    try:
        safe = validate_safe_graph_label(
            safe,
            field_name="graph_debug_trace",
            max_length=max_length,
        )
    except ValueError:
        return None
    return safe or None


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, Decimal):
        number = int(value)
        return number if number >= 0 else None
    if not isinstance(value, str):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if number >= 0 else None


def _safe_score(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, int | float | Decimal):
        return None
    score = float(value)
    if not math.isfinite(score):
        return None
    if score < 0.0 or score > 1.0:
        return None
    return round(score, 6)
