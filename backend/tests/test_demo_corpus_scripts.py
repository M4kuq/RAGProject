from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DocumentVersion, LogicalDocument, Role, User
from app.scripts.build_demo_graph_index import build_demo_graph_indexes
from app.scripts.ingest_demo_corpus import (
    DemoCorpusApiClient,
    DemoCorpusError,
    ingest_demo_corpus,
    load_manifest,
)


def test_demo_corpus_manifest_loads_repo_self_docs() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    entries = load_manifest(repo_root / "docs/demo/corpus_manifest.json", repo_root=repo_root)

    assert entries
    assert any(entry.source_path == "README.md" for entry in entries)
    assert any(entry.source_path == "docs/phase3/neo4j_optional_backend.md" for entry in entries)
    assert all(len(entry.content_hash) == 64 for entry in entries)
    assert all(not entry.source_path.startswith("docs/prompts/") for entry in entries)
    assert all(entry.absolute_path.is_file() for entry in entries)


def test_demo_corpus_manifest_rejects_paths_outside_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    manifest = repo_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "title": "Outside",
                        "source_path": "../outside.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    try:
        load_manifest(manifest, repo_root=repo_root)
    except DemoCorpusError as exc:
        assert "manifest_path_outside_repo" in str(exc)
    else:
        raise AssertionError("outside repo paths must be rejected")


def test_demo_corpus_client_applies_basic_auth_header() -> None:
    client = DemoCorpusApiClient(
        base_url="https://example.test",
        admin_email="admin@example.com",
        admin_password="not-used",
        origin="https://example.test",
        timeout_seconds=0.1,
        basic_auth_header="Basic ZGVtby11c2VyOnBhc3N3b3Jk",
    )
    try:
        assert client.client.headers["Authorization"] == "Basic ZGVtby11c2VyOnBhc3N3b3Jk"
    finally:
        client.close()


def test_demo_corpus_client_rejects_non_basic_authorization() -> None:
    with pytest.raises(DemoCorpusError, match="basic_auth_header_must_use_basic_scheme"):
        DemoCorpusApiClient(
            base_url="https://example.test",
            admin_email="admin@example.com",
            admin_password="not-used",
            origin="https://example.test",
            timeout_seconds=0.1,
            basic_auth_header="Bearer must-not-be-used",
        )


def test_ingest_demo_corpus_dry_run_does_not_require_api_login() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    entries = load_manifest(repo_root / "docs/demo/corpus_manifest.json", repo_root=repo_root)[:2]
    client = DemoCorpusApiClient(
        base_url="http://127.0.0.1:9",
        admin_email="admin@example.com",
        admin_password="not-used",
        origin="http://localhost:5173",
        timeout_seconds=0.1,
    )
    try:
        summary = ingest_demo_corpus(
            entries,
            client=client,
            manifest_path=repo_root / "docs/demo/corpus_manifest.json",
            wait=True,
            approve=True,
            wait_timeout_seconds=1,
            poll_seconds=0.01,
            dry_run=True,
        )
    finally:
        client.close()

    assert summary.dry_run is True
    assert summary.item_count == 2
    assert {item.action for item in summary.items} == {"would_upload_or_skip"}


def test_demo_graph_index_build_dry_run_lists_active_ready_versions(
    demo_graph_session_factory: sessionmaker[Session],
) -> None:
    with demo_graph_session_factory() as db:
        _seed_ready_version(db)

        summary = build_demo_graph_indexes(db, dry_run=True)

        assert summary.built_count == 0
        assert summary.would_build_count == 1
        assert summary.items[0].action == "would_build"


def _seed_ready_version(db: Session) -> None:
    role = Role(role_name="admin", description="Admin")
    db.add(role)
    db.flush()
    user = User(
        role_id=role.role_id,
        email="admin@example.com",
        display_name="Admin",
        status="active",
    )
    db.add(user)
    db.flush()
    logical = LogicalDocument(owner_user_id=user.user_id, title="Demo Corpus")
    db.add(logical)
    db.flush()
    db.add(
        DocumentVersion(
            logical_document_id=logical.logical_document_id,
            version_no=1,
            content_hash="a" * 64,
            status="ready",
            is_active=True,
            file_name="demo.md",
            mime_type="text/markdown",
            file_size_bytes=10,
            created_by=user.user_id,
        )
    )
    db.commit()


@pytest.fixture
def demo_graph_session_factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield factory
    finally:
        engine.dispose()
