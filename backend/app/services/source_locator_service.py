from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ResourceNotFound
from app.db.models import (
    ChatSession,
    Citation,
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    User,
)
from app.rag.trace import TraceRedactor
from app.schemas.documents import DocumentChunkDiffSide, DocumentSourceLocator
from app.schemas.rag import RagCitationSourceResponse
from app.services.url_fetch_service import redact_url_for_display

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|\s)(?:export\s+)?"
    r"([A-Z0-9_.-]*(?:api[_-]?key|secret|password|token|credential)[A-Z0-9_.-]*)"
    r"\s*[:=]\s*\S+"
)
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s<>'\"]+")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


@dataclass(frozen=True)
class LocatedChunk:
    citation: Citation | None
    chunk: DocumentChunk
    version: DocumentVersion
    document: LogicalDocument


class SourceLocatorService:
    def get_citation_source(
        self,
        db: Session,
        *,
        citation_id: int,
        user: User,
        role_name: str,
    ) -> RagCitationSourceResponse:
        located = self._get_citation_chunk(
            db,
            citation_id=citation_id,
            user=user,
            role_name=role_name,
        )
        locator = build_source_locator(located)
        citation = located.citation
        if citation is None:
            raise ResourceNotFound()
        return RagCitationSourceResponse(
            **locator.model_dump(),
            citation_id=citation.citation_id,
            local_citation_id=citation.rank_order,
        )

    def _get_citation_chunk(
        self,
        db: Session,
        *,
        citation_id: int,
        user: User,
        role_name: str,
    ) -> LocatedChunk:
        statement = (
            select(Citation, DocumentChunk, DocumentVersion, LogicalDocument, RetrievalRun)
            .join(
                RetrievalRunItem,
                and_(
                    RetrievalRunItem.retrieval_run_id == Citation.retrieval_run_id,
                    RetrievalRunItem.document_chunk_id == Citation.document_chunk_id,
                ),
            )
            .join(RetrievalRun, RetrievalRun.retrieval_run_id == Citation.retrieval_run_id)
            .join(DocumentChunk, DocumentChunk.document_chunk_id == Citation.document_chunk_id)
            .join(
                DocumentVersion,
                DocumentVersion.document_version_id == DocumentChunk.document_version_id,
            )
            .join(
                LogicalDocument,
                LogicalDocument.logical_document_id == DocumentVersion.logical_document_id,
            )
            .where(Citation.citation_id == citation_id, RetrievalRunItem.selected_flag.is_(True))
        )
        if role_name != "admin":
            statement = statement.join(
                ChatSession,
                ChatSession.chat_session_id == RetrievalRun.chat_session_id,
            ).where(ChatSession.user_id == user.user_id)
        row = db.execute(statement).first()
        if row is None:
            raise ResourceNotFound()
        citation, chunk, version, document, _run = row
        return LocatedChunk(citation=citation, chunk=chunk, version=version, document=document)


def build_source_locator(
    located: LocatedChunk,
    *,
    preview_max_chars: int | None = None,
) -> DocumentSourceLocator:
    citation = located.citation
    metadata = safe_chunk_metadata(located.chunk.metadata_json)
    source_url = _source_url(citation, metadata)
    source_type = "external_url" if source_url else "upload"
    source_label = source_label_for_chunk(
        document=located.document,
        version=located.version,
        chunk=located.chunk,
        metadata=metadata,
    )
    display_label = _safe_label(
        citation.display_label if citation else source_label,
        max_length=255,
    )
    preview, preview_truncated = bounded_preview(
        located.chunk.content_text,
        max_chars=preview_max_chars or get_settings().citation_source_preview_max_chars,
    )
    return DocumentSourceLocator(
        logical_document_id=located.document.logical_document_id,
        document_version_id=located.version.document_version_id,
        document_chunk_id=located.chunk.document_chunk_id,
        chunk_index=located.chunk.chunk_index,
        version_no=located.version.version_no,
        document_title=_safe_label(located.document.title, max_length=255),
        file_name=_safe_label(located.version.file_name, max_length=255),
        source_type=source_type,  # type: ignore[arg-type]
        source_url=source_url,
        display_label=display_label,
        source_label=source_label,
        section_title=_safe_optional_label(located.chunk.section_title, max_length=160),
        page_from=located.chunk.page_from,
        page_to=located.chunk.page_to,
        sheet_name=_safe_optional_label(_metadata_str(metadata, "sheet_name"), max_length=120),
        row_from=_metadata_int(metadata, "row_from"),
        row_to=_metadata_int(metadata, "row_to"),
        slide_number=_metadata_int(metadata, "slide_number"),
        slide_title=_safe_optional_label(_metadata_str(metadata, "slide_title"), max_length=120),
        html_heading_path=_safe_optional_label(
            _metadata_str(metadata, "heading_path"), max_length=160
        ),
        xml_path=_safe_optional_label(_metadata_str(metadata, "xml_path"), max_length=160),
        structure_type=_safe_optional_label(
            _metadata_str(metadata, "structure_type"), max_length=80
        ),
        preview=preview,
        preview_truncated=preview_truncated,
        old_version_flag=old_version_flag(located.version, located.document),
    )


def source_side_for_chunk(
    located: LocatedChunk,
    *,
    preview_max_chars: int,
) -> DocumentChunkDiffSide:
    locator = build_source_locator(located, preview_max_chars=preview_max_chars)
    return DocumentChunkDiffSide(
        document_chunk_id=locator.document_chunk_id,
        chunk_index=locator.chunk_index,
        source_label=locator.source_label,
        section_title=locator.section_title,
        page_from=locator.page_from,
        page_to=locator.page_to,
        sheet_name=locator.sheet_name,
        row_from=locator.row_from,
        row_to=locator.row_to,
        slide_number=locator.slide_number,
        html_heading_path=locator.html_heading_path,
        xml_path=locator.xml_path,
        preview=locator.preview,
        preview_truncated=locator.preview_truncated,
    )


def safe_chunk_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "parent_child_schema_version",
        "structure_type",
        "chunk_level",
        "parent_chunk_key",
        "child_chunk_key",
        "parent_title",
        "sheet_name",
        "row_from",
        "row_to",
        "column_from",
        "column_to",
        "table_index",
        "slide_number",
        "slide_title",
        "shape_count",
        "table_count",
        "html_title",
        "heading_path",
        "element_type",
        "element_index",
        "xml_root",
        "xml_path",
        "element_name",
        "source_type",
        "source_url",
    }
    safe: dict[str, object] = {}
    for key, item in value.items():
        if key not in allowed_keys:
            continue
        if isinstance(item, str):
            safe_value = (
                _safe_url(item)
                if key == "source_url"
                else _safe_optional_label(item, max_length=160)
            )
            if safe_value:
                safe[key] = safe_value
        elif isinstance(item, bool):
            safe[key] = item
        elif isinstance(item, int | float):
            safe[key] = item
    return safe


def source_label_for_chunk(
    *,
    document: LogicalDocument,
    version: DocumentVersion,
    chunk: DocumentChunk,
    metadata: dict[str, object] | None = None,
) -> str:
    safe_metadata = metadata if metadata is not None else safe_chunk_metadata(chunk.metadata_json)
    url = _source_url(None, safe_metadata)
    base = url or _safe_file_name(version.file_name) or _safe_label(document.title, max_length=120)
    suffix = _metadata_suffix(safe_metadata) or _safe_optional_label(
        chunk.section_title,
        max_length=120,
    )
    return f"{base} / {suffix}"[:255] if suffix else base[:255]


def old_version_flag(version: DocumentVersion, document: LogicalDocument) -> bool:
    return version.status != "ready" or not version.is_active or document.status != "active"


def bounded_preview(value: str, *, max_chars: int) -> tuple[str, bool]:
    cleaned = " ".join(value.replace("\x00", " ").split())
    redacted = _redact_sensitive_text(cleaned)
    if len(redacted) <= max_chars:
        return redacted, False
    return f"{redacted[: max_chars - 3]}...", True


def _redact_sensitive_text(value: str) -> str:
    without_assignments = _SECRET_ASSIGNMENT_RE.sub(" [redacted] ", value)
    without_urls = _URL_RE.sub(
        lambda match: redact_url_for_display(match.group(0)),
        without_assignments,
    )
    return _EMAIL_RE.sub("[redacted-email]", without_urls)


def _source_url(citation: Citation | None, metadata: dict[str, object]) -> str | None:
    if citation is not None and citation.source_type == "external_url" and citation.source_url:
        return _safe_url(citation.source_url)
    if metadata.get("source_type") != "url":
        return None
    source_url = metadata.get("source_url")
    if not isinstance(source_url, str):
        return None
    return _safe_url(source_url)


def _safe_url(value: str) -> str | None:
    redacted = redact_url_for_display(value)
    if not redacted or redacted == "redacted":
        return None
    if _SECRET_ASSIGNMENT_RE.search(redacted) or _EMAIL_RE.search(redacted):
        return None
    return redacted[:200]


def _metadata_suffix(metadata: dict[str, object]) -> str | None:
    structure_type = metadata.get("structure_type")
    if structure_type == "excel_sheet":
        parts: list[str] = []
        sheet_name = _metadata_str(metadata, "sheet_name")
        row_from = _metadata_int(metadata, "row_from")
        row_to = _metadata_int(metadata, "row_to")
        if sheet_name:
            parts.append(f"Sheet: {sheet_name}")
        if row_from is not None and row_to is not None:
            parts.append(f"Rows {row_from}-{row_to}" if row_from != row_to else f"Row {row_from}")
        return " / ".join(parts) or None
    if structure_type == "powerpoint_slide":
        parts = []
        slide_number = _metadata_int(metadata, "slide_number")
        slide_title = _metadata_str(metadata, "slide_title")
        if slide_number is not None:
            parts.append(f"Slide {slide_number}")
        if slide_title:
            parts.append(f"Title: {slide_title}")
        return " / ".join(parts) or None
    if structure_type == "html_section":
        return _metadata_str(metadata, "heading_path") or _metadata_str(metadata, "element_type")
    if structure_type == "xml_element":
        return _metadata_str(metadata, "xml_path") or _metadata_str(metadata, "element_name")
    return None


def _metadata_str(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def _metadata_int(metadata: dict[str, object], key: str) -> int | None:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _safe_file_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\\", "/").split("/")[-1]
    return _safe_optional_label(normalized, max_length=120)


def _safe_label(value: str | None, *, max_length: int) -> str:
    safe = _safe_optional_label(value, max_length=max_length)
    return safe or "source"


def _safe_optional_label(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    safe = TraceRedactor.safe_string(value, max_length=max_length)
    if not safe or safe == "redacted":
        return None
    return safe
