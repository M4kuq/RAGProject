from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import Select, and_, delete, func, insert, or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models import DocumentChunk, DocumentVersion, LogicalDocument
from app.schemas.common import PaginationParams


class DocumentRepository:
    def create_logical_document(
        self,
        db: Session,
        *,
        owner_user_id: int,
        title: str,
    ) -> LogicalDocument:
        document = LogicalDocument(owner_user_id=owner_user_id, title=title, status="active")
        db.add(document)
        db.flush()
        return document

    def create_version(
        self,
        db: Session,
        *,
        logical_document_id: int,
        version_no: int,
        content_hash: str,
        file_name: str,
        mime_type: str,
        file_size_bytes: int,
        storage_key: str,
        created_by: int,
    ) -> DocumentVersion:
        version = DocumentVersion(
            logical_document_id=logical_document_id,
            version_no=version_no,
            content_hash=content_hash,
            status="processing",
            is_active=False,
            file_name=file_name,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            storage_key=storage_key,
            created_by=created_by,
        )
        db.add(version)
        db.flush()
        return version

    def get_document(
        self,
        db: Session,
        *,
        logical_document_id: int,
        for_update: bool = False,
    ) -> LogicalDocument | None:
        statement = select(LogicalDocument).where(
            LogicalDocument.logical_document_id == logical_document_id
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def get_version(
        self,
        db: Session,
        *,
        logical_document_id: int,
        document_version_id: int,
        for_update: bool = False,
    ) -> DocumentVersion | None:
        statement = select(DocumentVersion).where(
            DocumentVersion.logical_document_id == logical_document_id,
            DocumentVersion.document_version_id == document_version_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def get_version_by_hash(
        self,
        db: Session,
        *,
        logical_document_id: int,
        content_hash: str,
    ) -> DocumentVersion | None:
        return db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.logical_document_id == logical_document_id,
                DocumentVersion.content_hash == content_hash,
            )
        )

    def get_version_by_id(
        self,
        db: Session,
        *,
        document_version_id: int,
        for_update: bool = False,
    ) -> DocumentVersion | None:
        statement = select(DocumentVersion).where(
            DocumentVersion.document_version_id == document_version_id
        )
        if for_update:
            statement = statement.with_for_update()
        return db.scalar(statement)

    def max_version_no(self, db: Session, *, logical_document_id: int) -> int:
        return (
            db.scalar(
                select(func.max(DocumentVersion.version_no)).where(
                    DocumentVersion.logical_document_id == logical_document_id
                )
            )
            or 0
        )

    def list_documents(
        self,
        db: Session,
        *,
        status: str,
        query: str | None,
        latest_version_filter: tuple[str | None, bool | None],
        pagination: PaginationParams,
    ) -> tuple[list[LogicalDocument], int]:
        base = self._document_list_statement(
            status=status,
            query=query,
            latest_version_filter=latest_version_filter,
        )
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(
                LogicalDocument.updated_at.desc(),
                LogicalDocument.logical_document_id.desc(),
            )
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
        return list(rows), total

    def active_versions_by_document_ids(
        self, db: Session, *, logical_document_ids: list[int]
    ) -> dict[int, DocumentVersion]:
        if not logical_document_ids:
            return {}
        rows = db.scalars(
            select(DocumentVersion).where(
                DocumentVersion.logical_document_id.in_(logical_document_ids),
                DocumentVersion.is_active.is_(True),
            )
        ).all()
        return {row.logical_document_id: row for row in rows}

    def latest_versions_by_document_ids(
        self, db: Session, *, logical_document_ids: list[int]
    ) -> dict[int, DocumentVersion]:
        if not logical_document_ids:
            return {}
        latest = (
            select(
                DocumentVersion.logical_document_id.label("logical_document_id"),
                func.max(DocumentVersion.version_no).label("version_no"),
            )
            .where(DocumentVersion.logical_document_id.in_(logical_document_ids))
            .group_by(DocumentVersion.logical_document_id)
            .subquery()
        )
        rows = db.scalars(
            select(DocumentVersion).join(
                latest,
                and_(
                    DocumentVersion.logical_document_id == latest.c.logical_document_id,
                    DocumentVersion.version_no == latest.c.version_no,
                ),
            )
        ).all()
        return {row.logical_document_id: row for row in rows}

    def list_versions(
        self,
        db: Session,
        *,
        logical_document_id: int,
        pagination: PaginationParams | None = None,
    ) -> tuple[list[DocumentVersion], int]:
        base = select(DocumentVersion).where(
            DocumentVersion.logical_document_id == logical_document_id
        )
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        statement = base.order_by(DocumentVersion.version_no.desc())
        if pagination is not None:
            statement = statement.offset(pagination.offset).limit(pagination.page_size)
        return list(db.scalars(statement).all()), total

    def count_chunks(self, db: Session, *, document_version_id: int) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_version_id == document_version_id)
            )
            or 0
        )

    def delete_chunks(self, db: Session, *, document_version_id: int) -> int:
        result = db.execute(
            delete(DocumentChunk).where(DocumentChunk.document_version_id == document_version_id)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    def bulk_insert_chunks(
        self,
        db: Session,
        *,
        chunks: Sequence[Mapping[str, object]],
    ) -> None:
        if not chunks:
            return
        db.execute(insert(DocumentChunk), list(chunks))
        db.flush()

    def update_ingest_metadata(
        self,
        db: Session,
        *,
        version: DocumentVersion,
        page_count: int | None,
        extractor_name: str,
        extractor_version: str,
        updated_at: datetime,
    ) -> None:
        version.page_count = page_count
        version.extractor_name = extractor_name
        version.extractor_version = extractor_version
        version.status = "processing"
        version.error_code = None
        version.updated_at = updated_at
        db.flush()

    def reset_version_for_ingest(
        self,
        db: Session,
        *,
        version: DocumentVersion,
        updated_at: datetime,
    ) -> None:
        version.status = "processing"
        version.error_code = None
        version.page_count = None
        version.extractor_name = None
        version.extractor_version = None
        version.updated_at = updated_at
        db.flush()

    def mark_version_failed(
        self,
        db: Session,
        *,
        version: DocumentVersion,
        error_code: str,
        updated_at: datetime,
    ) -> None:
        version.status = "failed"
        version.error_code = error_code
        version.page_count = None
        version.extractor_name = None
        version.extractor_version = None
        version.updated_at = updated_at
        db.flush()

    def chunk_counts_by_version_ids(
        self, db: Session, *, document_version_ids: list[int]
    ) -> dict[int, int]:
        if not document_version_ids:
            return {}
        rows = db.execute(
            select(DocumentChunk.document_version_id, func.count())
            .where(DocumentChunk.document_version_id.in_(document_version_ids))
            .group_by(DocumentChunk.document_version_id)
        ).all()
        return {int(version_id): int(count) for version_id, count in rows}

    def list_chunks(
        self,
        db: Session,
        *,
        document_version_id: int,
        pagination: PaginationParams,
    ) -> tuple[list[DocumentChunk], int]:
        base = select(DocumentChunk).where(DocumentChunk.document_version_id == document_version_id)
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = db.scalars(
            base.order_by(DocumentChunk.chunk_index.asc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        ).all()
        return list(rows), total

    def set_active_version(
        self,
        db: Session,
        *,
        logical_document_id: int,
        version: DocumentVersion,
        updated_at: datetime,
    ) -> int | None:
        active = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.logical_document_id == logical_document_id,
                DocumentVersion.is_active.is_(True),
            )
        )
        previous_id = active.document_version_id if active else None
        if active and active.document_version_id != version.document_version_id:
            active.is_active = False
            active.updated_at = updated_at
        version.is_active = True
        version.updated_at = updated_at
        db.flush()
        return previous_id

    def touch_document(
        self,
        db: Session,
        *,
        document: LogicalDocument,
        updated_at: datetime,
    ) -> None:
        document.updated_at = updated_at
        db.flush()

    def archive_document(
        self,
        db: Session,
        *,
        document: LogicalDocument,
        archived_at: datetime,
    ) -> None:
        document.status = "archived"
        document.archived_at = archived_at
        document.updated_at = archived_at
        active_versions = db.scalars(
            select(DocumentVersion).where(
                DocumentVersion.logical_document_id == document.logical_document_id,
                DocumentVersion.is_active.is_(True),
            )
        ).all()
        for version in active_versions:
            version.is_active = False
            version.updated_at = archived_at
        db.flush()

    def _document_list_statement(
        self,
        *,
        status: str,
        query: str | None,
        latest_version_filter: tuple[str | None, bool | None],
    ) -> Select[tuple[LogicalDocument]]:
        latest = (
            select(
                DocumentVersion.logical_document_id.label("logical_document_id"),
                func.max(DocumentVersion.version_no).label("version_no"),
            )
            .group_by(DocumentVersion.logical_document_id)
            .subquery()
        )
        latest_version = aliased(DocumentVersion)
        statement = (
            select(LogicalDocument)
            .outerjoin(latest, LogicalDocument.logical_document_id == latest.c.logical_document_id)
            .outerjoin(
                latest_version,
                and_(
                    latest_version.logical_document_id == latest.c.logical_document_id,
                    latest_version.version_no == latest.c.version_no,
                ),
            )
            .where(LogicalDocument.status == status)
        )
        if query:
            file_name_match = (
                select(DocumentVersion.document_version_id)
                .where(
                    DocumentVersion.logical_document_id == LogicalDocument.logical_document_id,
                    DocumentVersion.file_name.ilike(f"%{query}%"),
                )
                .exists()
            )
            statement = statement.where(
                or_(LogicalDocument.title.ilike(f"%{query}%"), file_name_match)
            )
        latest_version_status, latest_version_is_active = latest_version_filter
        if latest_version_status is not None:
            statement = statement.where(latest_version.status == latest_version_status)
        if latest_version_is_active is not None:
            statement = statement.where(latest_version.is_active.is_(latest_version_is_active))
        return statement
