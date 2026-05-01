from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import DocumentChunk, DocumentVersion, Job
from app.db.session import SessionLocal
from app.storage.extractors import chunk_text, extract_text


def handle_document_ingest(job: Job) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        version_id = int(job.payload["document_version_id"])
        version = db.get(DocumentVersion, version_id)
        if not version or not version.storage_key:
            job.status = "failed"
            job.error_code = "document_version_not_found"
            return
        text = extract_text(settings.storage_root / version.storage_key)
        chunks = chunk_text(text)
        if not chunks:
            version.status = "failed"
            version.error_code = "text_extraction_empty"
            job.status = "failed"
            job.error_code = "text_extraction_empty"
            return
        for index, chunk in enumerate(chunks):
            db.add(DocumentChunk(document_version_id=version.document_version_id, chunk_index=index, content=chunk))
        version.status = "ready"
        version.is_active = True
        job.status = "succeeded"
        job.finished_at = datetime.now(UTC)
        db.commit()


def run_once() -> bool:
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.status == "queued").order_by(Job.job_id).limit(1))
        if not job:
            return False
        job.status = "running"
        job.started_at = datetime.now(UTC)
        db.commit()
        if job.job_type == "document_ingest":
            handle_document_ingest(job)
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
