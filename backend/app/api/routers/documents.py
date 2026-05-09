from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.orm import Session
from starlette import status as http_status

from app.api.deps import pagination_params, require_admin, require_csrf
from app.api.responses import get_request_id, success_response
from app.core.config import get_settings
from app.core.errors import PayloadTooLarge
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import PaginationParams
from app.services.document_service import DocumentService

router = APIRouter()


def document_service() -> DocumentService:
    return DocumentService()


async def read_upload_bytes(file: UploadFile) -> bytes:
    max_bytes = get_settings().upload_max_bytes
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("")
def list_documents(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    display_status: str | None = None,
    _: User = Depends(require_admin),
    pagination: PaginationParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    items, page_meta = service.list_documents(
        db,
        status=status,
        query=q,
        display_status=display_status,
        pagination=pagination,
    )
    return success_response([item.model_dump(mode="json") for item in items], request, page_meta)


@router.post("", status_code=http_status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    document_name: str | None = Form(default=None),
    user: User = Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    content = await read_upload_bytes(file)
    result = service.upload_document(
        db,
        user=user,
        title=title if title is not None else document_name,
        filename=file.filename,
        content_type=file.content_type,
        content=content,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/{logical_document_id}")
def get_document_detail(
    logical_document_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    result = service.get_document_detail(db, logical_document_id=logical_document_id)
    return success_response(result.model_dump(mode="json"), request)


@router.post("/{logical_document_id}/versions", status_code=http_status.HTTP_201_CREATED)
async def add_document_version(
    logical_document_id: int,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    content = await read_upload_bytes(file)
    result, created = service.add_version(
        db,
        user=user,
        logical_document_id=logical_document_id,
        filename=file.filename,
        content_type=file.content_type,
        content=content,
        request_id=get_request_id(request),
    )
    response.status_code = http_status.HTTP_201_CREATED if created else http_status.HTTP_200_OK
    return success_response(result.model_dump(mode="json"), request)


@router.get("/{logical_document_id}/versions")
def list_document_versions(
    logical_document_id: int,
    request: Request,
    _: User = Depends(require_admin),
    pagination: PaginationParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    items, page_meta = service.list_versions(
        db,
        logical_document_id=logical_document_id,
        pagination=pagination,
    )
    return success_response([item.model_dump(mode="json") for item in items], request, page_meta)


@router.get("/{logical_document_id}/versions/{document_version_id}")
def get_document_version_detail(
    logical_document_id: int,
    document_version_id: int,
    request: Request,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    result = service.get_version_detail(
        db,
        logical_document_id=logical_document_id,
        document_version_id=document_version_id,
    )
    return success_response(result.model_dump(mode="json"), request)


@router.get("/{logical_document_id}/versions/{document_version_id}/chunks")
def list_document_chunks(
    logical_document_id: int,
    document_version_id: int,
    request: Request,
    _: User = Depends(require_admin),
    pagination: PaginationParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    items, page_meta = service.list_chunks(
        db,
        logical_document_id=logical_document_id,
        document_version_id=document_version_id,
        pagination=pagination,
    )
    return success_response([item.model_dump(mode="json") for item in items], request, page_meta)


@router.post("/{logical_document_id}/versions/{document_version_id}/approve")
def approve_document_version(
    logical_document_id: int,
    document_version_id: int,
    request: Request,
    user: User = Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    result = service.approve_version(
        db,
        user=user,
        logical_document_id=logical_document_id,
        document_version_id=document_version_id,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)


@router.post("/{logical_document_id}/archive")
def archive_document(
    logical_document_id: int,
    request: Request,
    user: User = Depends(require_admin),
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
    service: DocumentService = Depends(document_service),
) -> dict[str, object]:
    result = service.archive_document(
        db,
        user=user,
        logical_document_id=logical_document_id,
        request_id=get_request_id(request),
    )
    return success_response(result.model_dump(mode="json"), request)
