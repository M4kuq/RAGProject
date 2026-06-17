from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.db.graph_models import GraphRetrievalPath
from app.db.models import (
    Citation,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRunItem,
)
from app.rag.citations import CitationSource
from app.schemas.graph import validate_safe_graph_metadata
from app.services.source_locator_service import LocatedChunk, build_source_locator

GRAPH_CITATION_TRACE_SCHEMA_VERSION = "phase3.graph_citation_trace.v1"


@dataclass(frozen=True)
class GraphPathSourceMapping:
    source_chunk_id: int
    document_chunk_id: int
    retrieval_run_item_id: int
    selected_flag: bool
    old_version_flag: bool
    citation_ids: tuple[int, ...] = ()
    local_citation_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class LocatedGraphPathSource:
    source_chunk_id: int
    chunk_exists: bool
    active: bool
    retrieval_run_item: RetrievalRunItem | None = field(default=None, repr=False)
    chunk: DocumentChunk | None = field(default=None, repr=False)
    document_version: DocumentVersion | None = field(default=None, repr=False)
    logical_document: LogicalDocument | None = field(default=None, repr=False)
    citations: tuple[Citation, ...] = field(default_factory=tuple, repr=False)
    old_version_flag: bool = False

    @property
    def resolved(self) -> bool:
        return self.chunk_exists and self.active and self.retrieval_run_item is not None

    @property
    def citable(self) -> bool:
        return self.resolved and bool(
            self.retrieval_run_item and self.retrieval_run_item.selected_flag
        )

    def to_mapping(self) -> GraphPathSourceMapping | None:
        if not self.resolved or self.retrieval_run_item is None or self.chunk is None:
            return None
        return GraphPathSourceMapping(
            source_chunk_id=self.source_chunk_id,
            document_chunk_id=self.chunk.document_chunk_id,
            retrieval_run_item_id=self.retrieval_run_item.retrieval_run_item_id,
            selected_flag=self.retrieval_run_item.selected_flag,
            old_version_flag=self.old_version_flag,
            citation_ids=tuple(citation.citation_id for citation in self.citations),
            local_citation_ids=tuple(citation.rank_order for citation in self.citations),
        )


@dataclass(frozen=True)
class ValidatedGraphPath:
    path: GraphRetrievalPath = field(repr=False)
    path_id: str
    provider: str
    source_chunk_ids: tuple[int, ...]
    source_mappings: tuple[GraphPathSourceMapping, ...]
    validation_status: str
    reason_codes: tuple[str, ...]
    safe_metadata: dict[str, object]

    @property
    def valid(self) -> bool:
        return self.validation_status == "valid"

    @property
    def citable(self) -> bool:
        return self.valid and any(mapping.selected_flag for mapping in self.source_mappings)


@dataclass(frozen=True)
class GraphCitationCoverage:
    path_count: int
    valid_path_count: int
    citable_path_count: int
    excluded_path_count: int
    source_chunk_count: int
    resolved_source_chunk_count: int
    citable_source_chunk_count: int
    citation_source_count: int
    source_chunk_coverage_ratio: float
    citation_coverage_ratio: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class GraphCitationBuildResult:
    paths: tuple[ValidatedGraphPath, ...]
    coverage: GraphCitationCoverage
    citation_sources: tuple[CitationSource, ...]


class GraphPathSourceLocator:
    def locate(
        self,
        db: Session,
        *,
        retrieval_run_id: int,
        paths: Iterable[GraphRetrievalPath],
    ) -> dict[int, LocatedGraphPathSource]:
        source_chunk_ids = _ordered_positive_ids(
            chunk_id for path in paths for chunk_id in _source_chunk_ids_from_path(path)
        )
        if not source_chunk_ids:
            return {}

        statement = (
            select(DocumentChunk, DocumentVersion, LogicalDocument, RetrievalRunItem, Citation)
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .outerjoin(
                RetrievalRunItem,
                and_(
                    RetrievalRunItem.retrieval_run_id == retrieval_run_id,
                    RetrievalRunItem.document_chunk_id == DocumentChunk.document_chunk_id,
                ),
            )
            .outerjoin(
                Citation,
                and_(
                    Citation.retrieval_run_id == retrieval_run_id,
                    Citation.document_chunk_id == DocumentChunk.document_chunk_id,
                ),
            )
            .where(DocumentChunk.document_chunk_id.in_(source_chunk_ids))
            .order_by(
                DocumentChunk.document_chunk_id.asc(),
                Citation.rank_order.asc(),
                Citation.citation_id.asc(),
            )
        )
        located: dict[int, LocatedGraphPathSource] = {}
        citations_by_chunk_id: dict[int, list[Citation]] = {}
        for chunk, version, document, run_item, citation in db.execute(statement).all():
            active = version.status == "ready" and document.status == "active"
            old_version = _old_version_flag(version, document)
            source = located.get(chunk.document_chunk_id)
            if source is None:
                source = LocatedGraphPathSource(
                    source_chunk_id=chunk.document_chunk_id,
                    chunk_exists=True,
                    active=active,
                    retrieval_run_item=run_item,
                    chunk=chunk,
                    document_version=version,
                    logical_document=document,
                    old_version_flag=old_version,
                )
                located[chunk.document_chunk_id] = source
            if citation is not None:
                citations_by_chunk_id.setdefault(chunk.document_chunk_id, []).append(citation)

        for chunk_id, citations in citations_by_chunk_id.items():
            source = located[chunk_id]
            located[chunk_id] = LocatedGraphPathSource(
                source_chunk_id=source.source_chunk_id,
                chunk_exists=source.chunk_exists,
                active=source.active,
                retrieval_run_item=source.retrieval_run_item,
                chunk=source.chunk,
                document_version=source.document_version,
                logical_document=source.logical_document,
                citations=tuple(citations),
                old_version_flag=source.old_version_flag,
            )

        for chunk_id in source_chunk_ids:
            located.setdefault(
                chunk_id,
                LocatedGraphPathSource(
                    source_chunk_id=chunk_id,
                    chunk_exists=False,
                    active=False,
                ),
            )
        return located


class GraphPathValidator:
    def validate(
        self,
        *,
        paths: Iterable[GraphRetrievalPath],
        located_sources: dict[int, LocatedGraphPathSource],
    ) -> tuple[ValidatedGraphPath, ...]:
        validated: list[ValidatedGraphPath] = []
        for path in paths:
            source_chunk_ids = _source_chunk_ids_from_path(path)
            reason_codes: list[str] = []
            mappings: list[GraphPathSourceMapping] = []
            if not source_chunk_ids:
                reason_codes.append("graph_path_no_source_chunks")
            for chunk_id in source_chunk_ids:
                located = located_sources.get(chunk_id)
                if located is None or not located.chunk_exists:
                    reason_codes.append("source_chunk_missing")
                    continue
                if not located.active:
                    reason_codes.append("inactive_source_chunk")
                    continue
                if located.retrieval_run_item is None:
                    reason_codes.append("missing_retrieval_run_item")
                    continue
                mapping = located.to_mapping()
                if mapping is not None:
                    mappings.append(mapping)
            if mappings and not any(mapping.selected_flag for mapping in mappings):
                reason_codes.append("no_selected_retrieval_run_items")
            if any(mapping.old_version_flag for mapping in mappings):
                reason_codes.append("old_version_source_chunk")

            status = (
                "valid"
                if not reason_codes or set(reason_codes).issubset(_NON_EXCLUDING_REASON_CODES)
                else "excluded"
            )
            deduped_reasons = tuple(_dedupe_strings(reason_codes))
            safe_metadata = validate_safe_graph_metadata(
                {
                    "schema_version": GRAPH_CITATION_TRACE_SCHEMA_VERSION,
                    "validation_status": status,
                    "reason_codes": list(deduped_reasons),
                    "source_chunk_count": len(source_chunk_ids),
                    "resolved_source_chunk_count": len(mappings),
                    "citable_source_chunk_count": sum(
                        1 for mapping in mappings if mapping.selected_flag
                    ),
                    "old_version_source_chunk_count": sum(
                        1 for mapping in mappings if mapping.old_version_flag
                    ),
                }
            )
            validated.append(
                ValidatedGraphPath(
                    path=path,
                    path_id=_path_id(path),
                    provider=_provider(path),
                    source_chunk_ids=source_chunk_ids,
                    source_mappings=tuple(mappings),
                    validation_status=status,
                    reason_codes=deduped_reasons,
                    safe_metadata=safe_metadata,
                )
            )
        return tuple(validated)


class GraphCitationBuilder:
    def __init__(self, *, snippet_max_chars: int) -> None:
        self.snippet_max_chars = max(1, int(snippet_max_chars))

    def build(
        self,
        *,
        validated_paths: Iterable[ValidatedGraphPath],
        located_sources: dict[int, LocatedGraphPathSource],
    ) -> GraphCitationBuildResult:
        paths = tuple(validated_paths)
        citation_sources: list[CitationSource] = []
        seen_run_item_ids: set[int] = set()
        for path in paths:
            if not path.valid:
                continue
            for mapping in path.source_mappings:
                if not mapping.selected_flag or mapping.retrieval_run_item_id in seen_run_item_ids:
                    continue
                located = located_sources.get(mapping.source_chunk_id)
                if located is None or not located.citable:
                    continue
                source = self._citation_source(
                    local_citation_id=len(citation_sources) + 1,
                    located=located,
                )
                if source is None:
                    continue
                citation_sources.append(source)
                seen_run_item_ids.add(mapping.retrieval_run_item_id)
        coverage = calculate_graph_citation_coverage(
            validated_paths=paths,
            citation_source_count=len(citation_sources),
        )
        return GraphCitationBuildResult(
            paths=paths,
            coverage=coverage,
            citation_sources=tuple(citation_sources),
        )

    def _citation_source(
        self,
        *,
        local_citation_id: int,
        located: LocatedGraphPathSource,
    ) -> CitationSource | None:
        if (
            located.retrieval_run_item is None
            or located.chunk is None
            or located.document_version is None
            or located.logical_document is None
        ):
            return None
        locator = build_source_locator(
            LocatedChunk(
                citation=None,
                chunk=located.chunk,
                version=located.document_version,
                document=located.logical_document,
            ),
            preview_max_chars=self.snippet_max_chars,
        )
        return CitationSource(
            local_citation_id=local_citation_id,
            retrieval_run_item_id=located.retrieval_run_item.retrieval_run_item_id,
            document_chunk_id=located.chunk.document_chunk_id,
            source_label=locator.source_label,
            snippet=locator.preview,
            page_from=locator.page_from,
            page_to=locator.page_to,
            section_title=locator.section_title,
            source_type=locator.source_type,
            source_url=locator.source_url,
        )


def calculate_graph_citation_coverage(
    *,
    validated_paths: Iterable[ValidatedGraphPath],
    citation_source_count: int,
) -> GraphCitationCoverage:
    paths = tuple(validated_paths)
    source_chunk_ids = {
        chunk_id for path in paths for chunk_id in path.source_chunk_ids if chunk_id > 0
    }
    resolved_source_chunk_ids = {
        mapping.source_chunk_id for path in paths for mapping in path.source_mappings
    }
    citable_source_chunk_ids = {
        mapping.source_chunk_id
        for path in paths
        for mapping in path.source_mappings
        if mapping.selected_flag
    }
    valid_path_count = sum(1 for path in paths if path.valid)
    citable_path_count = sum(1 for path in paths if path.citable)
    reason_codes = tuple(_dedupe_strings(code for path in paths for code in path.reason_codes))
    return GraphCitationCoverage(
        path_count=len(paths),
        valid_path_count=valid_path_count,
        citable_path_count=citable_path_count,
        excluded_path_count=max(0, len(paths) - valid_path_count),
        source_chunk_count=len(source_chunk_ids),
        resolved_source_chunk_count=len(resolved_source_chunk_ids),
        citable_source_chunk_count=len(citable_source_chunk_ids),
        citation_source_count=max(0, citation_source_count),
        source_chunk_coverage_ratio=_ratio(len(resolved_source_chunk_ids), len(source_chunk_ids)),
        citation_coverage_ratio=_ratio(citable_path_count, len(paths)),
        reason_codes=reason_codes,
    )


def _source_chunk_ids_from_path(path: GraphRetrievalPath) -> tuple[int, ...]:
    return tuple(_ordered_positive_ids(path.source_chunk_ids_json))


def _path_id(path: GraphRetrievalPath) -> str:
    value = path.path_json.get("path_id")
    return (
        str(value)[:120]
        if isinstance(value, str) and value
        else f"graph_path:{path.graph_retrieval_path_id}"
    )


def _provider(path: GraphRetrievalPath) -> str:
    value = path.path_json.get("provider")
    return str(value)[:40] if isinstance(value, str) and value else "unknown"


_NON_EXCLUDING_REASON_CODES = frozenset(
    {
        "no_selected_retrieval_run_items",
        "old_version_source_chunk",
    }
)


def _old_version_flag(version: DocumentVersion, document: LogicalDocument) -> bool:
    return version.status != "ready" or not version.is_active or document.status != "active"


def _ordered_positive_ids(values: Iterable[int]) -> tuple[int, ...]:
    ordered: list[int] = []
    seen: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number <= 0 or number in seen:
            continue
        ordered.append(number)
        seen.add(number)
    return tuple(ordered)


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        safe = str(value).strip()
        if not safe or safe in seen:
            continue
        deduped.append(safe)
        seen.add(safe)
    return deduped


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(max(0.0, min(1.0, numerator / denominator)), 6)
