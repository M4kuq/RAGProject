from __future__ import annotations

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    LogicalDocument,
    Role,
    SystemSetting,
    User,
    UserSetting,
)

DEMO_DOCUMENT_TITLE = "RAGProject Phase1 Seed Document"
DEMO_DOCUMENT_TEXT = (
    "RAGProject Phase1 validates a local Docker Compose RAG stack with PostgreSQL, "
    "Qdrant, deterministic fake adapters for CI, citation-aware retrieval traces, "
    "and idempotent seed data."
)


def seed(db: Session) -> None:
    roles: dict[str, Role] = {}
    role_descriptions = {
        "admin": "Administrator role for Phase1 local validation.",
        "viewer": "Viewer role for Phase1 local validation.",
    }
    for name, description in role_descriptions.items():
        role = db.scalar(select(Role).where(Role.role_name == name))
        if not role:
            role = Role(role_name=name, description=description)
            db.add(role)
            db.flush()
        roles[name] = role

    users = [
        ("admin@example.com", "Admin", "admin"),
        ("viewer@example.com", "Viewer", "viewer"),
    ]
    for email, display_name, role_name in users:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(
                role_id=roles[role_name].role_id,
                email=email,
                display_name=display_name,
                password_hash=hash_password("password"),
                status="active",
            )
            db.add(user)
            db.flush()
        if not db.get(UserSetting, user.user_id):
            db.add(UserSetting(user_id=user.user_id))

    defaults = {
        "rag.fake_mode": (
            {"enabled": True},
            "Use deterministic fake adapters in CI and local smoke tests.",
        ),
        "rag.allowed_file_extensions": (
            {"items": [".pdf", ".docx", ".txt", ".md", ".csv"]},
            "Phase1 upload allowlist.",
        ),
        "chat.memory_message_limit": ({"value": 8}, "Default recent chat message memory size."),
        "chat.temporary_ttl_minutes": ({"value": 120}, "Default temporary chat TTL in minutes."),
        "jobs.retry_max": ({"value": 3}, "Default manual retry upper bound."),
        "rag.confidence_thresholds": (
            {"high": 0.75, "medium": 0.45},
            "Initial display thresholds for confidence labels.",
        ),
    }
    for key, (value, description) in defaults.items():
        if not db.get(SystemSetting, key):
            db.add(SystemSetting(setting_key=key, setting_value=value, description=description))

    admin = db.scalar(select(User).where(User.email == "admin@example.com"))
    if admin:
        logical = db.scalar(
            select(LogicalDocument).where(LogicalDocument.title == DEMO_DOCUMENT_TITLE)
        )
        if not logical:
            logical = LogicalDocument(
                owner_user_id=admin.user_id,
                title=DEMO_DOCUMENT_TITLE,
                status="active",
            )
            db.add(logical)
            db.flush()

        content_hash = hashlib.sha256(DEMO_DOCUMENT_TEXT.encode("utf-8")).hexdigest()
        version = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.logical_document_id == logical.logical_document_id,
                DocumentVersion.content_hash == content_hash,
            )
        )
        if not version:
            active_exists = db.scalar(
                select(DocumentVersion.document_version_id).where(
                    DocumentVersion.logical_document_id == logical.logical_document_id,
                    DocumentVersion.is_active.is_(True),
                )
            )
            next_version_no = (
                db.scalar(
                    select(func.max(DocumentVersion.version_no)).where(
                        DocumentVersion.logical_document_id == logical.logical_document_id
                    )
                )
                or 0
            ) + 1
            version = DocumentVersion(
                logical_document_id=logical.logical_document_id,
                version_no=next_version_no,
                content_hash=content_hash,
                status="ready",
                is_active=active_exists is None,
                file_name="phase1-seed.md",
                mime_type="text/markdown",
                file_size_bytes=len(DEMO_DOCUMENT_TEXT.encode("utf-8")),
                page_count=1,
                extractor_name="seed",
                extractor_version="1",
                created_by=admin.user_id,
            )
            db.add(version)
            db.flush()
        if not db.scalar(
            select(DocumentChunk).where(
                DocumentChunk.document_version_id == version.document_version_id,
                DocumentChunk.chunk_index == 0,
            )
        ):
            db.add(
                DocumentChunk(
                    document_version_id=version.document_version_id,
                    chunk_index=0,
                    chunk_hash=hashlib.sha256(DEMO_DOCUMENT_TEXT.encode("utf-8")).hexdigest(),
                    content_text=DEMO_DOCUMENT_TEXT,
                    token_count=len(DEMO_DOCUMENT_TEXT.split()),
                    char_count=len(DEMO_DOCUMENT_TEXT),
                    page_from=1,
                    page_to=1,
                    section_title="Phase1 seed",
                    modality="text",
                )
            )
    db.commit()
