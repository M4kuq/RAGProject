from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DocumentChunk, DocumentVersion, Job
from app.db.session import SessionLocal
from app.storage.extractors import chunk_text, extract_text


def handle_document_ingest(db: Session, job: Job) -> None:
    settings = get_settings()
    payload = job.payload_json or {}
    version_id = int(payload["document_version_id"])
    version = db.get(DocumentVersion, version_id)
    if not version or not version.storage_key:
        job.status = "failed"
        job.error_code = "document_version_not_found"
        job.finished_at = datetime.now(UTC)
        return
    text = extract_text(settings.storage_root / version.storage_key)
    chunks = chunk_text(text)
    if not chunks:
        version.status = "failed"
        version.error_code = "text_extraction_empty"
        job.status = "failed"
        job.error_code = "text_extraction_empty"
        job.finished_at = datetime.now(UTC)
        return
    for index, chunk in enumerate(chunks):
        chunk_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        db.add(
            DocumentChunk(
                document_version_id=version.document_version_id,
                chunk_index=index,
                chunk_hash=chunk_hash,
                content_text=chunk,
                token_count=len(chunk.split()),
                char_count=len(chunk),
            )
        )
    version.status = "ready"
    version.is_active = False
    job.status = "succeeded"
    job.finished_at = datetime.now(UTC)


def run_once() -> bool:
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.status == "queued").order_by(Job.job_id).limit(1))
        if not job:
            return False
        settings = get_settings()
        now = datetime.now(UTC)
        job.status = "running"
        job.locked_by = "local-worker"
        job.locked_at = now
        job.lease_expires_at = now + timedelta(seconds=getattr(settings, "job_lease_seconds", 300))
        job.started_at = now
        db.commit()
        if job.job_type == "document_ingest":
            handle_document_ingest(db, job)
        else:
            job.status = "succeeded"
            job.finished_at = datetime.now(UTC)
        db.commit()
        return True


def main() -> None:
    while True:
        did_work = run_once()
        if not did_work:
            time.sleep(2)


if __name__ == "__main__":
    main()
