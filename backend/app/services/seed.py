from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
from app.ingest.embedding import EmbeddingAdapterError
from app.ingest.qdrant import (
    DocumentIndexingService,
    QdrantStoreError,
    create_document_indexing_service,
)
from app.rag.strategy import PHASE2_RETRIEVAL_SYSTEM_SETTINGS

DEMO_PASSWORD = "password"
DEMO_DOCUMENT_TITLE = "RAGProject Phase1 Seed Document"
DEMO_DOCUMENT_TEXT = (
    "RAGProject Phase1 validates a local Docker Compose RAG stack. "
    "The backend API is implemented with FastAPI, the frontend chat and admin UI are "
    "implemented with React, and PostgreSQL stores users, sessions, chat history, "
    "documents, jobs, retrieval runs, citations, confidence labels, and evaluation results. "
    "Qdrant is the vector database used for document chunk retrieval. "
    "A worker process handles document ingest, extraction, chunking, embedding, Qdrant "
    "indexing, and evaluation jobs. "
    "For local real-model testing, Phase1 can use LM Studio through OpenAI-compatible "
    "chat and embeddings endpoints. "
    "CI keeps deterministic CI adapters so GitHub Actions does not require API keys, "
    "large model downloads, or a local model server. "
    "The Phase1 demo also includes citation-aware answers, confidence labels, evaluation "
    "fixtures, and a local-only MCP stdio server."
)
SEED_DATA_DIR = Path(__file__).resolve().parents[1] / "seed_data"
LLM_PAPER_CORPUS_TEXT = (SEED_DATA_DIR / "llm_paper_corpus.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class DemoVersion:
    version_no: int
    file_name: str
    mime_type: str
    text: str
    section_title: str
    active: bool = True
    chunks: tuple[str, ...] = ()


@dataclass(frozen=True)
class DemoDocument:
    title: str
    versions: tuple[DemoVersion, ...]


def _split_seed_sections(text: str) -> tuple[str, ...]:
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("### P") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    return tuple(section for section in sections if section.startswith("### P"))


DEMO_DOCUMENTS: tuple[DemoDocument, ...] = (
    DemoDocument(
        title=DEMO_DOCUMENT_TITLE,
        versions=(
            DemoVersion(
                version_no=1,
                file_name="phase1-seed.md",
                mime_type="text/markdown",
                section_title="Phase1 seed",
                text=DEMO_DOCUMENT_TEXT,
            ),
        ),
    ),
    DemoDocument(
        title="Phase1 Design Memo",
        versions=(
            DemoVersion(
                version_no=1,
                file_name="phase1-design-v1.md",
                mime_type="text/markdown",
                section_title="Initial design memo",
                active=False,
                text=(
                    "Phase1 design memo version 1 records the first local RAG demo plan. "
                    "It uses PostgreSQL for relational state and Qdrant for vector search. "
                    "The old version is kept so the UI can show an inactive document version."
                ),
            ),
            DemoVersion(
                version_no=2,
                file_name="phase1-design-v2.md",
                mime_type="text/markdown",
                section_title="Updated design memo",
                text=(
                    "Phase1 design memo version 2 is the active demo document. "
                    "It explains that citations come from selected retrieval chunks, confidence "
                    "labels are stored with retrieval runs, and fake adapters keep CI predictable."
                ),
            ),
        ),
    ),
    DemoDocument(
        title="Phase1 Operations Policy Memo",
        versions=(
            DemoVersion(
                version_no=1,
                file_name="phase1-operations-policy.txt",
                mime_type="text/plain",
                section_title="Operations policy",
                text=(
                    "Phase1 operation policy keeps demo data local. Admin users can upload, "
                    "approve, archive, and retry jobs. Viewer users can use chat flows but do "
                    "not have document administration access."
                ),
            ),
        ),
    ),
    DemoDocument(
        title="Phase1 Metrics Sample CSV",
        versions=(
            DemoVersion(
                version_no=1,
                file_name="phase1-metrics.csv",
                mime_type="text/csv",
                section_title="Metrics sample",
                text=(
                    "metric,value,notes\n"
                    "dataset,phase1_smoke,default evaluation fixture\n"
                    "required_citation,true,evaluation expects cited answers\n"
                    "mcp_transport,stdio,local-only Phase1 server\n"
                ),
            ),
        ),
    ),
    DemoDocument(
        title="LLM Paper Corpus for RAG Demo",
        versions=(
            DemoVersion(
                version_no=1,
                file_name="llm-paper-corpus.md",
                mime_type="text/markdown",
                section_title="LLM paper corpus",
                text=LLM_PAPER_CORPUS_TEXT,
                chunks=_split_seed_sections(LLM_PAPER_CORPUS_TEXT),
            ),
        ),
    ),
)


def seed(db: Session, *, index_documents: bool = True) -> None:
    settings = get_settings()
    if settings.app_env.lower() not in {"local", "ci", "test"}:
        raise RuntimeError("Seed is only allowed in local, ci, or test environments.")

    roles = _seed_roles(db)
    _seed_users(db, roles)
    _seed_system_settings(db)

    admin = db.scalar(select(User).where(User.email == "admin@example.com"))
    if admin:
        for document in DEMO_DOCUMENTS:
            _seed_demo_document(
                db,
                owner_user_id=admin.user_id,
                document=document,
            )

    db.commit()
    if index_documents:
        _index_seed_documents(
            db,
            indexing_service=create_document_indexing_service(settings),
        )


def _seed_roles(db: Session) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    role_descriptions = {
        "admin": "Administrator role for Phase1 local validation.",
        "viewer": "Viewer role for Phase1 local validation.",
    }
    for name, description in role_descriptions.items():
        role = db.scalar(select(Role).where(Role.role_name == name))
        if not role:
            role = Role(
                role_name=name,
                description=description,
            )
            db.add(role)
            db.flush()
        roles[name] = role
    return roles


def _seed_users(db: Session, roles: dict[str, Role]) -> None:
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
                password_hash=hash_password(DEMO_PASSWORD),
                status="active",
            )
            db.add(user)
            db.flush()
        if not db.get(UserSetting, user.user_id):
            db.add(UserSetting(user_id=user.user_id))


def _seed_system_settings(db: Session) -> None:
    defaults = {
        "rag.fake_mode": (
            {"enabled": True},
            "Use deterministic fake adapters in CI and local smoke tests.",
        ),
        "rag.allowed_file_extensions": (
            {"items": [".pdf", ".docx", ".txt", ".md", ".csv"]},
            "Phase1 upload allowlist.",
        ),
        "chat.memory_message_limit": (
            {"value": 8},
            "Default recent chat message memory size.",
        ),
        "chat.temporary_ttl_minutes": (
            {"value": 120},
            "Default temporary chat TTL in minutes.",
        ),
        "jobs.retry_max": (
            {"value": 3},
            "Default manual retry upper bound.",
        ),
        "rag.confidence_thresholds": (
            {"high": 0.75, "medium": 0.45},
            "Initial display thresholds for confidence labels.",
        ),
        "demo.sample_questions": (
            {
                "items": [
                    "What vector database is used by Phase1?",
                    "How does Phase1 keep CI deterministic?",
                    "Which MCP transport is used in Phase1?",
                    "What can an admin do with documents?",
                    "What is the core idea of Attention Is All You Need?",
                    "How did GPT-3 change few-shot learning?",
                    "What is the difference between GPT-3 and InstructGPT?",
                    "Which papers introduced RAG, Self-RAG, and GraphRAG?",
                    "How do DeepSeek-R1 and Kimi k1.5 use reinforcement learning for reasoning?",
                    "What does Qwen2.5-VL focus on?",
                ],
            },
            "Questions aligned with the Phase1 demo documents.",
        ),
        "evaluation.default_dataset": (
            {"dataset_name": "phase1_smoke", "case_limit": 5},
            "Default fixture for Phase1 demo evaluation.",
        ),
        "rag.evaluation.default_dataset": (
            {
                "dataset_name": "phase2_strategy_smoke",
                "strategy_type": "dense",
                "case_limit": 5,
            },
            "Default Phase2 strategy evaluation fixture metadata.",
        ),
        "rag.evaluation.ci_smoke_enabled": (
            {"enabled": True},
            "Enable deterministic CI retrieval evaluation smoke metadata.",
        ),
        "rag.evaluation.ci_smoke_defaults": (
            {
                "dataset_name": "phase2_strategy_smoke",
                "strategies": ["dense", "hybrid", "agentic_router"],
                "mode": "local",
                "case_limit": 5,
                "threshold_mode": "warn",
            },
            "Default options for PR-31 retrieval evaluation smoke workflow.",
        ),
        "evaluation.failure.low_recall_threshold": (
            {"value": 0.5},
            "Failure promotion threshold for low recall_at_k.",
        ),
        "evaluation.failure.low_mrr_threshold": (
            {"value": 0.5},
            "Failure promotion threshold for low MRR.",
        ),
        "evaluation.failure.low_citation_coverage_threshold": (
            {"value": 0.5},
            "Failure promotion threshold for low citation coverage.",
        ),
        "evaluation.failure.low_groundedness_threshold": (
            {"value": 0.5},
            "Failure promotion threshold for low groundedness.",
        ),
        "evaluation.failure.low_faithfulness_threshold": (
            {"value": 0.5},
            "Failure promotion threshold for low faithfulness.",
        ),
        "evaluation.failure.high_latency_ms": (
            {"value": 3000},
            "Failure promotion threshold for high evaluation latency.",
        ),
        "evaluation.failure.max_promotions_per_run": (
            {"value": 100},
            "Maximum failure cases promoted from one evaluation run.",
        ),
        "evaluation.agentic.expected_strategy_required_for_accuracy": (
            False,
            "Keep strategy selection accuracy not-applicable unless expected strategy exists.",
        ),
        **PHASE2_RETRIEVAL_SYSTEM_SETTINGS,
    }
    for key, (value, description) in defaults.items():
        if not db.get(SystemSetting, key):
            db.add(
                SystemSetting(
                    setting_key=key,
                    setting_value=value,
                    description=description,
                )
            )


def _seed_demo_document(
    db: Session,
    *,
    owner_user_id: int,
    document: DemoDocument,
) -> None:
    logical = db.scalar(select(LogicalDocument).where(LogicalDocument.title == document.title))
    if not logical:
        logical = LogicalDocument(
            owner_user_id=owner_user_id,
            title=document.title,
            status="active",
        )
        db.add(logical)
        db.flush()

    active_version: DocumentVersion | None = None
    for demo_version in document.versions:
        version = _seed_document_version(
            db,
            logical_document_id=logical.logical_document_id,
            created_by=owner_user_id,
            demo_version=demo_version,
        )
        _seed_document_chunks(
            db,
            version=version,
            demo_version=demo_version,
        )
        if demo_version.active:
            active_version = version

    if active_version is None:
        active_version = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.logical_document_id == logical.logical_document_id)
            .order_by(DocumentVersion.version_no.desc())
        )
    if active_version is None:
        return

    versions = list(
        db.scalars(
            select(DocumentVersion).where(
                DocumentVersion.logical_document_id == logical.logical_document_id
            )
        )
    )
    for version in versions:
        version.is_active = False
    db.flush()

    for version in versions:
        if version.document_version_id != active_version.document_version_id:
            _clear_failed_document_version_fields(version)
            version.status = "archived"
    _clear_failed_document_version_fields(active_version)
    active_version.status = "ready"
    active_version.is_active = True
    db.flush()


def _seed_document_version(
    db: Session,
    *,
    logical_document_id: int,
    created_by: int,
    demo_version: DemoVersion,
) -> DocumentVersion:
    content_hash = _content_hash(demo_version.text)
    version = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.logical_document_id == logical_document_id,
            DocumentVersion.content_hash == content_hash,
        )
    )
    if not version:
        next_version_no = (
            db.scalar(
                select(func.max(DocumentVersion.version_no)).where(
                    DocumentVersion.logical_document_id == logical_document_id
                )
            )
            or 0
        ) + 1
        version = DocumentVersion(
            logical_document_id=logical_document_id,
            version_no=max(demo_version.version_no, next_version_no),
            content_hash=content_hash,
            status="ready",
            is_active=False,
            file_name=demo_version.file_name,
            mime_type=demo_version.mime_type,
            file_size_bytes=len(demo_version.text.encode("utf-8")),
            page_count=1,
            extractor_name="seed",
            extractor_version="1",
            created_by=created_by,
        )
        db.add(version)
        db.flush()
    return version


def _clear_failed_document_version_fields(version: DocumentVersion) -> None:
    version.error_code = None


def _seed_document_chunks(
    db: Session,
    *,
    version: DocumentVersion,
    demo_version: DemoVersion,
) -> None:
    chunks = demo_version.chunks or (demo_version.text,)
    for chunk_index, chunk_text in enumerate(chunks):
        exists = db.scalar(
            select(DocumentChunk.document_chunk_id).where(
                DocumentChunk.document_version_id == version.document_version_id,
                DocumentChunk.chunk_index == chunk_index,
            )
        )
        if exists:
            continue
        db.add(
            DocumentChunk(
                document_version_id=version.document_version_id,
                chunk_index=chunk_index,
                chunk_hash=_content_hash(chunk_text),
                content_text=chunk_text,
                token_count=len(chunk_text.split()),
                char_count=len(chunk_text),
                page_from=1,
                page_to=1,
                section_title=_section_title_for_chunk(
                    chunk_text,
                    fallback=demo_version.section_title,
                ),
                modality="text",
            )
        )


def _index_seed_documents(
    db: Session,
    *,
    indexing_service: DocumentIndexingService,
) -> None:
    try:
        seed_versions = _iter_seed_versions(db)
        for logical, version, chunks in seed_versions:
            if _is_indexable_seed_version(logical, version) and chunks:
                indexing_service.index_chunks(
                    logical_document=logical,
                    document_version=version,
                    chunks=chunks,
                )
        for logical, version, chunks in seed_versions:
            if _is_indexable_seed_version(logical, version):
                continue
            indexing_service.cleanup_document_points(
                document_version_id=version.document_version_id,
                document_chunk_ids=[chunk.document_chunk_id for chunk in chunks],
            )
    except (EmbeddingAdapterError, QdrantStoreError) as exc:
        raise RuntimeError("Seed document indexing failed.") from exc


def _iter_seed_versions(
    db: Session,
) -> list[tuple[LogicalDocument, DocumentVersion, list[DocumentChunk]]]:
    demo_titles = [document.title for document in DEMO_DOCUMENTS]
    logical_documents = list(
        db.scalars(
            select(LogicalDocument)
            .where(LogicalDocument.title.in_(demo_titles))
            .order_by(LogicalDocument.logical_document_id.asc())
        )
    )
    result: list[tuple[LogicalDocument, DocumentVersion, list[DocumentChunk]]] = []
    for logical in logical_documents:
        versions = list(
            db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.logical_document_id == logical.logical_document_id)
                .order_by(DocumentVersion.version_no.asc())
            )
        )
        for version in versions:
            chunks = list(
                db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_version_id == version.document_version_id)
                    .order_by(DocumentChunk.chunk_index.asc())
                )
            )
            result.append((logical, version, chunks))
    return result


def _is_indexable_seed_version(logical: LogicalDocument, version: DocumentVersion) -> bool:
    return logical.status == "active" and version.status == "ready" and version.is_active


def _section_title_for_chunk(chunk_text: str, *, fallback: str) -> str:
    chunk_lines = chunk_text.splitlines()
    first_line = chunk_lines[0].strip() if chunk_lines else ""
    if first_line.startswith("### "):
        return first_line.removeprefix("### ")[:200]
    return fallback


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
