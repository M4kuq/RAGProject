from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin, require_csrf
from app.core.config import get_settings
from app.db.models import DocumentVersion, Job, LogicalDocument, User
from app.db.session import get_db
from app.storage.extractors import validate_extension

router = APIRouter(dependencies=[Depends(require_csrf)])


@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        validate_extension(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    content = await file.read()
    content_hash = hashlib.sha256(content).hexdigest()
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    storage_key = f"{content_hash}_{Path(file.filename or 'upload').name}"
    (settings.storage_root / storage_key).write_bytes(content)
    logical = LogicalDocument(
        owner_user_id=user.user_id, title=file.filename or "Uploaded document"
    )
    db.add(logical)
    db.flush()
    version = DocumentVersion(
        logical_document_id=logical.logical_document_id,
        version_no=1,
        content_hash=content_hash,
        status="processing",
        is_active=False,
        file_name=file.filename or "upload",
        mime_type=file.content_type or "application/octet-stream",
        file_size_bytes=len(content),
        storage_key=storage_key,
        created_by=user.user_id,
    )
    db.add(version)
    db.flush()
    db.add(
        Job(
            job_type="document_ingest",
            payload={"document_version_id": version.document_version_id},
            created_by=user.user_id,
        )
    )
    db.commit()
    return {
        "data": {
            "logical_document_id": logical.logical_document_id,
            "document_version_id": version.document_version_id,
            "status": version.status,
        },
        "meta": {},
    }


@router.get("")
def list_documents(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    docs = db.scalars(select(LogicalDocument)).all()
    return {
        "data": [
            {"logical_document_id": d.logical_document_id, "title": d.title, "status": d.status}
            for d in docs
        ],
        "meta": {"pagination": {"page": 1, "page_size": 20, "total": len(docs)}},
    }
