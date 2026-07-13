from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.db.evaluation_models import EvaluationResult
from app.db.models import (
    DocumentChunk,
    DocumentVersion,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    EvaluationRunItem,
    LogicalDocument,
    RetrievalRun,
    RetrievalRunItem,
    Role,
    SystemSetting,
    User,
    UserSetting,
)
from app.rag.strategy import RETRIEVAL_SOURCE_VALUES, RETRIEVAL_STRATEGY_VALUES
from app.services.seed import DEMO_DOCUMENT_TITLE, seed


@pytest.fixture(scope="module")
def pg_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("PostgreSQL schema assertions require a PostgreSQL DATABASE_URL")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        engine.dispose()
        pytest.skip("PostgreSQL schema assertions require a reachable database")
    yield engine
    engine.dispose()


def scalar_set(engine: Engine, sql: str) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text(sql)).scalars())


def assert_rejected(engine: Engine, sql: str, params: dict[str, object] | None = None) -> None:
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            with pytest.raises(IntegrityError):
                conn.execute(text(sql), params or {})
        finally:
            transaction.rollback()


def test_migration_head_tables_constraints_and_indexes(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0020_graph_llm_extractor"

    expected_tables = {
        "roles",
        "users",
        "user_settings",
        "user_sessions",
        "chat_sessions",
        "chat_messages",
        "chat_tags",
        "summary_memories",
        "logical_documents",
        "document_versions",
        "document_chunks",
        "jobs",
        "retrieval_runs",
        "retrieval_run_items",
        "retrieval_cache_entries",
        "citations",
        "evaluation_datasets",
        "evaluation_cases",
        "evaluation_runs",
        "evaluation_run_items",
        "evaluation_results",
        "audit_logs",
        "system_settings",
    }
    actual_tables = scalar_set(
        pg_engine,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """,
    )
    assert expected_tables <= actual_tables

    expected_constraints = {
        "ck_users_status",
        "ck_users_email_normalized",
        "uq_users_email",
        "ck_document_versions_status",
        "ck_document_versions_active_ready_only",
        "uq_document_versions_content_hash",
        "ck_jobs_running_required_fields",
        "ck_jobs_failed_error_code",
        "fk_citations_retrieval_item",
        "uq_evaluation_results_item_metric",
        "fk_chat_messages_linked_retrieval_run_same_session",
        "ck_audit_logs_request_id_not_empty",
        "ck_retrieval_runs_strategy_type",
        "ck_retrieval_run_items_source",
        "ck_retrieval_cache_entries_cache_key_hash",
        "ck_retrieval_cache_entries_query_hash",
        "ck_retrieval_cache_entries_retrieval_hash",
        "ck_retrieval_cache_entries_rerank_hash",
        "ck_retrieval_cache_entries_document_fp",
        "ck_retrieval_cache_entries_graph_fp",
        "ck_retrieval_cache_entries_scope_hash",
        "uq_evaluation_datasets_name",
        "uq_evaluation_cases_dataset_key",
        "ck_evaluation_runs_strategy_type",
        "ck_evaluation_run_items_strategy_type",
        "ck_evaluation_run_items_generation_non_negative",
        "ck_evaluation_results_strategy_type",
    }
    actual_constraints = scalar_set(
        pg_engine,
        """
        SELECT conname
        FROM pg_constraint
        WHERE connamespace = 'public'::regnamespace
        """,
    )
    assert expected_constraints <= actual_constraints

    expected_indexes = {
        "ux_chat_messages_client_message_id",
        "ux_document_versions_one_active",
        "ix_document_versions_active",
        "ix_jobs_status_priority_created",
        "ix_jobs_lease_expires",
        "ux_jobs_active_retry_per_source",
        "ux_jobs_active_message_edit",
        "ux_retrieval_run_items_run_rerank_order",
        "ix_retrieval_cache_entries_expires",
        "ix_retrieval_cache_entries_namespace_strategy",
        "ix_evaluation_results_metric_score",
        "ix_evaluation_datasets_status_created",
        "ix_evaluation_cases_dataset_status",
        "ix_evaluation_runs_dataset_strategy",
        "ix_evaluation_run_items_case",
        "ix_document_chunks_content_fts",
        "ix_document_chunks_content_fts_english",
    }
    actual_indexes = scalar_set(
        pg_engine,
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
        """,
    )
    assert expected_indexes <= actual_indexes

    with pg_engine.connect() as conn:
        partial_index_defs = {
            row.indexname: row.indexdef.lower()
            for row in conn.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname IN (
                        'ux_document_versions_one_active',
                        'ix_jobs_lease_expires',
                        'ux_jobs_active_retry_per_source',
                        'ux_jobs_active_message_edit',
                        'ix_chat_sessions_user_status_created',
                        'ix_audit_logs_target',
                        'ix_document_chunks_content_fts',
                        'ix_document_chunks_content_fts_english'
                      )
                    """
                )
            )
        }
    assert "where" in partial_index_defs["ux_document_versions_one_active"]
    assert "is_active" in partial_index_defs["ux_document_versions_one_active"]
    assert "status" in partial_index_defs["ix_jobs_lease_expires"]
    assert "retry_of_job_id" in partial_index_defs["ux_jobs_active_retry_per_source"]
    assert "message_edit_regeneration" in partial_index_defs["ux_jobs_active_message_edit"]
    assert "created_at desc" in partial_index_defs["ix_chat_sessions_user_status_created"]
    assert "created_at desc" in partial_index_defs["ix_audit_logs_target"]
    assert "using gin" in partial_index_defs["ix_document_chunks_content_fts"]
    assert "to_tsvector('simple'" in partial_index_defs["ix_document_chunks_content_fts"]
    assert "using gin" in partial_index_defs["ix_document_chunks_content_fts_english"]
    assert "to_tsvector('english'" in partial_index_defs["ix_document_chunks_content_fts_english"]


def test_phase2_retrieval_trace_columns_and_constraints(pg_engine: Engine) -> None:
    suffix = uuid.uuid4().hex
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            default_strategy = conn.execute(
                text(
                    """
                    INSERT INTO retrieval_runs (status, top_k)
                    VALUES ('running', 5)
                    RETURNING retrieval_run_id, strategy_type, query_plan_json
                    """
                )
            ).one()
            assert default_strategy.strategy_type == "dense"
            assert default_strategy.query_plan_json is None

            assert_rejected(
                pg_engine,
                """
                INSERT INTO retrieval_runs (status, top_k, strategy_type)
                VALUES ('running', 5, 'graph_rag')
                """,
            )

            role_id = conn.execute(
                text(
                    """
                    INSERT INTO roles (role_name, description)
                    VALUES (:role_name, 'Phase2 constraint test')
                    RETURNING role_id
                    """
                ),
                {"role_name": f"phase2-{suffix}"},
            ).scalar_one()
            user_id = conn.execute(
                text(
                    """
                    INSERT INTO users (role_id, email, display_name, status)
                    VALUES (:role_id, :email, 'Phase2', 'active')
                    RETURNING user_id
                    """
                ),
                {"role_id": role_id, "email": f"phase2-{suffix}@example.com"},
            ).scalar_one()
            logical_id = conn.execute(
                text(
                    """
                    INSERT INTO logical_documents (owner_user_id, title, status)
                    VALUES (:owner_user_id, :title, 'active')
                    RETURNING logical_document_id
                    """
                ),
                {"owner_user_id": user_id, "title": f"Phase2 {suffix}"},
            ).scalar_one()
            version_id = conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        logical_document_id, version_no, content_hash, status, is_active,
                        file_name, mime_type, file_size_bytes, created_by
                    )
                    VALUES (
                        :logical_document_id, 1, :content_hash, 'ready', TRUE,
                        'phase2.txt', 'text/plain', 12, :created_by
                    )
                    RETURNING document_version_id
                    """
                ),
                {
                    "logical_document_id": logical_id,
                    "content_hash": suffix[:32].ljust(64, "a"),
                    "created_by": user_id,
                },
            ).scalar_one()
            chunk_id = conn.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        document_version_id, chunk_index, chunk_hash, content_text, modality
                    )
                    VALUES (:document_version_id, 0, :chunk_hash, 'phase2 chunk', 'text')
                    RETURNING document_chunk_id
                    """
                ),
                {
                    "document_version_id": version_id,
                    "chunk_hash": suffix[:32].ljust(64, "b"),
                },
            ).scalar_one()
            item = conn.execute(
                text(
                    """
                    INSERT INTO retrieval_run_items (
                        retrieval_run_id, document_chunk_id, retrieval_score, rerank_score,
                        rank_order, rerank_order, selected_flag, retrieval_source,
                        score_breakdown_json
                    )
                    VALUES (
                        :retrieval_run_id, :document_chunk_id, 0.9, 0.8,
                        1, 1, TRUE, 'dense', '{"dense_score": 0.9}'::jsonb
                    )
                    RETURNING retrieval_source, score_breakdown_json
                    """
                ),
                {
                    "retrieval_run_id": default_strategy.retrieval_run_id,
                    "document_chunk_id": chunk_id,
                },
            ).one()
            assert item.retrieval_source == "dense"
            assert item.score_breakdown_json == {"dense_score": 0.9}

            graph_chunk_id = conn.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        document_version_id, chunk_index, chunk_hash, content_text, modality
                    )
                    VALUES (:document_version_id, 1, :chunk_hash, 'graph chunk', 'text')
                    RETURNING document_chunk_id
                    """
                ),
                {
                    "document_version_id": version_id,
                    "chunk_hash": suffix[:32].ljust(64, "c"),
                },
            ).scalar_one()
            graph_item = conn.execute(
                text(
                    """
                    INSERT INTO retrieval_run_items (
                        retrieval_run_id, document_chunk_id, retrieval_score, rank_order,
                        retrieval_source
                    )
                    VALUES (:retrieval_run_id, :document_chunk_id, 0.9, 2, 'graph')
                    RETURNING retrieval_source
                    """
                ),
                {
                    "retrieval_run_id": default_strategy.retrieval_run_id,
                    "document_chunk_id": graph_chunk_id,
                },
            ).one()
            assert graph_item.retrieval_source == "graph"

            invalid_source_chunk_id = conn.execute(
                text(
                    """
                    INSERT INTO document_chunks (
                        document_version_id, chunk_index, chunk_hash, content_text, modality
                    )
                    VALUES (
                        :document_version_id, 2, :chunk_hash, 'invalid source chunk', 'text'
                    )
                    RETURNING document_chunk_id
                    """
                ),
                {
                    "document_version_id": version_id,
                    "chunk_hash": suffix[:32].ljust(64, "d"),
                },
            ).scalar_one()
            savepoint = conn.begin_nested()
            try:
                with pytest.raises(IntegrityError):
                    conn.execute(
                        text(
                            """
                            INSERT INTO retrieval_run_items (
                                retrieval_run_id, document_chunk_id, retrieval_score, rank_order,
                                retrieval_source
                            )
                            VALUES (:retrieval_run_id, :document_chunk_id, 0.9, 3, 'graph_rag')
                            """
                        ),
                        {
                            "retrieval_run_id": default_strategy.retrieval_run_id,
                            "document_chunk_id": invalid_source_chunk_id,
                        },
                    )
            finally:
                savepoint.rollback()
        finally:
            transaction.rollback()


def test_phase2_evaluation_dataset_strategy_columns_and_constraints(pg_engine: Engine) -> None:
    suffix = uuid.uuid4().hex
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            role_id = conn.execute(
                text(
                    """
                    INSERT INTO roles (role_name, description)
                    VALUES (:role_name, 'Phase2 evaluation constraint test')
                    RETURNING role_id
                    """
                ),
                {"role_name": f"eval-{suffix}"},
            ).scalar_one()
            user_id = conn.execute(
                text(
                    """
                    INSERT INTO users (role_id, email, display_name, status)
                    VALUES (:role_id, :email, 'Eval', 'active')
                    RETURNING user_id
                    """
                ),
                {"role_id": role_id, "email": f"eval-{suffix}@example.com"},
            ).scalar_one()
            dataset_id = conn.execute(
                text(
                    """
                    INSERT INTO evaluation_datasets (
                        dataset_name, description, source_type, created_by
                    )
                    VALUES (:dataset_name, 'Phase2 evaluation dataset', 'manual', :created_by)
                    RETURNING evaluation_dataset_id
                    """
                ),
                {"dataset_name": f"phase2_eval_{suffix[:8]}", "created_by": user_id},
            ).scalar_one()
            case_id = conn.execute(
                text(
                    """
                    INSERT INTO evaluation_cases (
                        evaluation_dataset_id, case_key, question, expected_keywords
                    )
                    VALUES (
                        :evaluation_dataset_id, 'case_a', 'What uses Qdrant?',
                        '["Qdrant"]'::jsonb
                    )
                    RETURNING evaluation_case_id
                    """
                ),
                {"evaluation_dataset_id": dataset_id},
            ).scalar_one()
            run = conn.execute(
                text(
                    """
                    INSERT INTO evaluation_runs (
                        created_by, evaluation_dataset_id, status, strategy_type,
                        retrieval_settings_json
                    )
                    VALUES (
                        :created_by, :evaluation_dataset_id, 'queued', 'hybrid',
                        '{"strategy_type": "hybrid"}'::jsonb
                    )
                    RETURNING evaluation_run_id, strategy_type, trigger_type
                    """
                ),
                {"created_by": user_id, "evaluation_dataset_id": dataset_id},
            ).one()
            assert run.strategy_type == "hybrid"
            assert run.trigger_type == "manual"
            item_id = conn.execute(
                text(
                    """
                    INSERT INTO evaluation_run_items (
                        evaluation_run_id, evaluation_case_id, strategy_type, case_key,
                        status, latency_ms, latency_breakdown_json, metric_summary_json
                    )
                    VALUES (
                        :evaluation_run_id, :evaluation_case_id, 'hybrid', 'case_a',
                        'succeeded', 12, '{"total_ms": 12}'::jsonb,
                        '{"metrics": {"recall_at_k": 1.0}}'::jsonb
                    )
                    RETURNING evaluation_run_item_id
                    """
                ),
                {"evaluation_run_id": run.evaluation_run_id, "evaluation_case_id": case_id},
            ).scalar_one()
            result = conn.execute(
                text(
                    """
                    INSERT INTO evaluation_results (
                        evaluation_run_item_id, metric_name, metric_value,
                        metric_detail_json, strategy_type
                    )
                    VALUES (
                        :evaluation_run_item_id, 'p95_latency', 12.0,
                        '{"unit": "ms"}'::jsonb, 'hybrid'
                    )
                    RETURNING metric_name, metric_value, strategy_type
                    """
                ),
                {"evaluation_run_item_id": item_id},
            ).one()
            assert result.metric_name == "p95_latency"
            assert result.strategy_type == "hybrid"

            savepoint = conn.begin_nested()
            try:
                with pytest.raises(IntegrityError):
                    conn.execute(
                        text(
                            """
                            INSERT INTO evaluation_runs (
                                created_by, evaluation_dataset_id, status, strategy_type
                            )
                            VALUES (:created_by, :evaluation_dataset_id, 'queued', 'graph_rag')
                            """
                        ),
                        {"created_by": user_id, "evaluation_dataset_id": dataset_id},
                    )
            finally:
                savepoint.rollback()
        finally:
            transaction.rollback()


def test_phase2_orm_fields_match_strategy_schema() -> None:
    version_columns = DocumentVersion.__table__.columns
    chunk_columns = DocumentChunk.__table__.columns
    run_columns = RetrievalRun.__table__.columns
    item_columns = RetrievalRunItem.__table__.columns
    run_constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in cast(Any, RetrievalRun.__table__).constraints
        if constraint.name == "ck_retrieval_runs_strategy_type"
    )
    item_constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in cast(Any, RetrievalRunItem.__table__).constraints
        if constraint.name == "ck_retrieval_run_items_source"
    )

    assert {
        "strategy_type",
        "query_plan_json",
        "strategy_decision_json",
        "latency_breakdown_json",
        "retrieval_settings_json",
        "context_budget_json",
        "context_compression_json",
        "tool_result_compression_json",
    } <= set(run_columns.keys())
    assert {"metadata_json"} <= set(version_columns.keys())
    assert {"metadata_json"} <= set(chunk_columns.keys())
    assert {"retrieval_source", "score_breakdown_json"} <= set(item_columns.keys())
    assert all(value in run_constraint_sql for value in RETRIEVAL_STRATEGY_VALUES)
    assert all(value in item_constraint_sql for value in RETRIEVAL_SOURCE_VALUES)


def test_phase2_evaluation_dataset_strategy_orm_fields() -> None:
    dataset_columns = EvaluationDataset.__table__.columns
    case_columns = EvaluationCase.__table__.columns
    run_columns = EvaluationRun.__table__.columns
    item_columns = EvaluationRunItem.__table__.columns
    result_columns = EvaluationResult.__table__.columns
    run_constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in cast(Any, EvaluationRun.__table__).constraints
        if constraint.name == "ck_evaluation_runs_strategy_type"
    )
    item_constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in cast(Any, EvaluationRunItem.__table__).constraints
        if constraint.name == "ck_evaluation_run_items_strategy_type"
    )
    result_constraint_sql = " ".join(
        str(constraint.sqltext)
        for constraint in cast(Any, EvaluationResult.__table__).constraints
        if constraint.name == "ck_evaluation_results_strategy_type"
    )

    assert {"evaluation_dataset_id", "dataset_name", "source_type", "status"} <= set(
        dataset_columns.keys()
    )
    assert {"evaluation_case_id", "evaluation_dataset_id", "case_key", "question"} <= set(
        case_columns.keys()
    )
    assert {
        "evaluation_dataset_id",
        "strategy_type",
        "trigger_type",
        "retrieval_settings_json",
        "strategy_metrics_summary_json",
    } <= set(run_columns.keys())
    assert {
        "evaluation_case_id",
        "strategy_type",
        "case_key",
        "generation_provider",
        "generation_model",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "generation_latency_ms",
        "latency_breakdown_json",
        "metric_summary_json",
    } <= set(item_columns.keys())
    assert {"metric_value", "metric_detail_json", "strategy_type"} <= set(result_columns.keys())
    assert all(value in run_constraint_sql for value in RETRIEVAL_STRATEGY_VALUES)
    assert all(value in item_constraint_sql for value in RETRIEVAL_STRATEGY_VALUES)
    assert all(value in result_constraint_sql for value in RETRIEVAL_STRATEGY_VALUES)


def test_deployed_seed_users_omit_known_local_accounts() -> None:
    from app.services.seed import _seed_users

    class FakeSession:
        def __init__(self, scalar_results: list[User | None]) -> None:
            self.users: list[User] = []
            self.scalar_results = scalar_results

        def scalar(self, statement: object) -> User | None:
            del statement
            return self.scalar_results.pop(0)

        def add(self, item: object) -> None:
            if isinstance(item, User):
                self.users.append(item)

        def flush(self) -> None:
            return None

        def get(self, model: object, key: object) -> object:
            del model, key
            return object()

    old_admin = User(
        role_id=1,
        email="admin@example.com",
        display_name="Local Admin",
        password_hash=hash_password("password"),
        status="active",
    )
    old_viewer = User(
        role_id=2,
        email="viewer@example.com",
        display_name="Local Viewer",
        password_hash=hash_password("password"),
        status="active",
    )
    fake_db = FakeSession([old_admin, old_viewer, None])
    roles = {
        "admin": Role(role_id=1, role_name="admin", description="Admin"),
        "viewer": Role(role_id=2, role_name="viewer", description="Viewer"),
    }

    _seed_users(
        cast(Any, fake_db),
        roles,
        deployed_admin_email="aws-admin@example.com",
        deployed_admin_password="strong-deployed-password",
    )

    assert [user.email for user in fake_db.users] == ["aws-admin@example.com"]
    assert old_admin.status == "disabled"
    assert old_viewer.status == "disabled"
    assert not verify_password("password", old_admin.password_hash)
    assert not verify_password("password", old_viewer.password_hash)
    assert verify_password("strong-deployed-password", fake_db.users[0].password_hash)
    assert not verify_password("password", fake_db.users[0].password_hash)


def test_seed_can_run_twice_without_duplicates(
    pg_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indexing_service = _CapturingIndexingService()
    monkeypatch.setattr(
        "app.services.seed.create_document_indexing_service",
        lambda settings: indexing_service,
    )
    Session = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    with Session() as db:
        seed(db)
    with Session() as db:
        seed(db)

    with Session() as db:
        assert db.query(Role).filter(Role.role_name == "admin").count() == 1
        assert db.query(Role).filter(Role.role_name == "viewer").count() == 1
        assert db.query(User).filter(User.email == "admin@example.com").count() == 1
        assert db.query(User).filter(User.email == "viewer@example.com").count() == 1
        seed_user_ids = [
            user.user_id
            for user in db.query(User)
            .filter(User.email.in_(["admin@example.com", "viewer@example.com"]))
            .all()
        ]
        assert db.query(UserSetting).filter(UserSetting.user_id.in_(seed_user_ids)).count() == 2
        assert (
            db.query(SystemSetting).filter(SystemSetting.setting_key == "rag.fake_mode").count()
            == 1
        )
        assert (
            db.query(SystemSetting).filter(SystemSetting.setting_key == "jobs.retry_max").count()
            == 1
        )
        assert (
            db.query(SystemSetting)
            .filter(SystemSetting.setting_key == "rag.default_strategy")
            .count()
            == 1
        )
        assert _setting_value(db, "rag.default_strategy") == "dense"
        assert _setting_value(db, "rag.hybrid.enabled") is True
        assert _setting_value(db, "rag.hybrid.fusion_method") == "rrf"
        assert _setting_value(db, "rag.hybrid.rrf_k") == 60
        assert _setting_value(db, "rag.hybrid.dense_weight") == 0.5
        assert _setting_value(db, "rag.hybrid.sparse_weight") == 0.5
        assert _setting_value(db, "rag.hybrid.candidate_multiplier") == 2
        assert _setting_value(db, "rag.router.enabled") is True
        assert _setting_value(db, "rag.router.mode") == "rule_based"
        assert _setting_value(db, "rag.router.allow_agentic_search") is True
        assert _setting_value(db, "rag.router.allow_agentic_ask") is True
        assert _setting_value(db, "rag.router.keyword_heavy_threshold") == 0.65
        assert _setting_value(db, "rag.router.ambiguity_threshold") == 0.75
        assert _setting_value(db, "rag.router.max_retrieval_calls") == 2
        assert _setting_value(db, "rag.router.max_fallback_calls") == 1
        assert _setting_value(db, "rag.router.sufficiency_min_candidates") == 1
        assert _setting_value(db, "rag.router.sufficiency_min_selected") == 1
        assert _setting_value(db, "rag.router.sufficiency_top_score_threshold") == 0.2
        assert _setting_value(db, "rag.router.enable_fallback_hybrid") is True
        assert _setting_value(db, "rag.router.enable_fallback_dense") is True
        assert _setting_value(db, "rag.router.no_context_after_budget_exhausted") is True
        assert _setting_value(db, "rag.router.fallback_strategy") == "fallback_dense"
        assert _setting_value(db, "rag.router.store_decision_trace") is True
        assert _setting_value(db, "rag.graph.extractor.default") == "llm"
        assert _setting_value(db, "rag.graph.extraction.provider") is None
        assert _setting_value(db, "rag.graph.extraction.model_name") is None
        assert _setting_value(db, "rag.graph.extraction.timeout_seconds") == 60
        assert _setting_value(db, "rag.graph.extraction.max_output_chars") == 12000
        assert _setting_value(db, "rag.graph.extraction.max_output_tokens") == 2048
        assert _setting_value(db, "rag.graph.extraction.min_confidence") == 0.5
        assert _setting_value(db, "rag.graph.max_entities_per_chunk") == 20
        assert _setting_value(db, "rag.graph.max_relations_per_chunk") == 40
        assert _setting_value(db, "rag.tool_result_compression.enabled") is True
        assert _setting_value(db, "rag.tool_result_compression.max_items_per_tool") == 8
        assert _setting_value(db, "rag.tool_result_compression.max_total_items_per_turn") == 20
        assert _setting_value(db, "rag.tool_result_compression.max_snippet_chars") == 500
        assert _setting_value(db, "rag.tool_result_compression.max_tokens_per_tool") == 1200
        assert (
            _setting_value(db, "rag.tool_result_compression.max_total_tool_result_tokens") == 3000
        )
        assert _setting_value(db, "rag.tool_result_compression.drop_low_score_first") is True
        assert _setting_value(db, "rag.tool_result_compression.group_by_source") is True
        assert _setting_value(db, "rag.tool_result_compression.reject_oversized_output") is True
        assert _setting_value(db, "rag.tool_result_compression.store_debug_trace") is True
        assert _setting_value(db, "rag.context_budget.enabled") is True
        assert _setting_value(db, "rag.context_budget.max_context_tokens") == 6000
        assert _setting_value(db, "rag.context_budget.reserve_answer_tokens") == 1000
        assert _setting_value(db, "rag.context_budget.max_context_items") == 12
        assert _setting_value(db, "rag.context_budget.max_tokens_per_item") == 1200
        assert _setting_value(db, "rag.context_budget.min_citation_candidates") == 1
        assert _setting_value(db, "rag.context_budget.drop_low_score_first") is True
        assert _setting_value(db, "rag.context_budget.preserve_source_diversity") is True
        assert _setting_value(db, "rag.context_budget.token_estimator") == "heuristic"
        assert _setting_value(db, "rag.context_budget.store_debug_trace") is True
        assert _setting_value(db, "rag.evidence_pack.enabled") is True
        assert _setting_value(db, "rag.evidence_pack.max_items") == 12
        assert _setting_value(db, "rag.evidence_pack.max_items_per_source") == 4
        assert _setting_value(db, "rag.evidence_pack.max_chars_per_item") == 1200
        assert _setting_value(db, "rag.evidence_pack.max_total_chars") == 12000
        assert _setting_value(db, "rag.evidence_pack.near_duplicate_threshold") == 0.85
        assert _setting_value(db, "rag.evidence_pack.preserve_citation_candidates") is True
        assert _setting_value(db, "rag.evidence_pack.group_by_source") is True
        assert _setting_value(db, "rag.evidence_pack.store_debug_trace") is True
        assert _setting_value(db, "rag.trace.enabled") is True
        assert _setting_value(db, "rag.sparse.enabled") is True
        assert _setting_value(db, "rag.sparse.provider") == "postgres_fts"
        assert _setting_value(db, "rag.sparse.language") == "simple"
        assert _setting_value(db, "rag.sparse.min_query_terms") == 1
        assert _setting_value(db, "rag.sparse.max_query_terms") == 32
        assert _setting_value(db, "rag.sparse.score_normalization") == "max"
        assert _setting_value(db, "rag.query_analyzer.enabled") is True
        assert _setting_value(db, "rag.query_planner.enabled") is True
        assert _setting_value(db, "rag.query_planner.apply_rewrite_to_retrieval") is False
        assert _setting_value(db, "rag.query_planner.max_sub_queries") == 3
        assert _setting_value(db, "rag.query_planner.max_preview_chars") == 160
        assert _setting_value(db, "rag.query_planner.store_query_preview") is True
        assert _setting_value(db, "rag.query_planner.redact_pii") is True
        assert _setting_value(db, "rag.evaluation.default_dataset") == {
            "dataset_name": "phase2_strategy_smoke",
            "strategy_type": "dense",
            "case_limit": 5,
        }
        assert _setting_value(db, "rag.evaluation.ci_smoke_enabled") == {"enabled": True}
        assert _setting_value(db, "rag.evaluation.ci_smoke_defaults") == {
            "dataset_name": "phase2_strategy_smoke",
            "strategies": ["dense", "hybrid", "agentic_router"],
            "mode": "local",
            "case_limit": 5,
            "threshold_mode": "warn",
        }
        logical = db.query(LogicalDocument).filter_by(title=DEMO_DOCUMENT_TITLE).one()
        versions = (
            db.query(DocumentVersion)
            .filter_by(logical_document_id=logical.logical_document_id)
            .all()
        )
        assert versions
        active_versions = [version for version in versions if version.is_active]
        assert len(active_versions) == 1
        version = active_versions[0]
        assert version.status == "ready"
        assert (
            db.query(DocumentChunk)
            .filter_by(document_version_id=version.document_version_id)
            .count()
            == 1
        )

    indexed_titles = {call.title for call in indexing_service.calls}
    assert DEMO_DOCUMENT_TITLE in indexed_titles
    assert "LLM Paper Corpus for RAG Demo" in indexed_titles
    assert all(call.version_status == "ready" for call in indexing_service.calls)
    assert all(call.is_active is True for call in indexing_service.calls)
    assert any(
        call.title == "LLM Paper Corpus for RAG Demo" and call.chunk_count >= 100
        for call in indexing_service.calls
    )
    assert any(
        call.document_version_id > 0 and call.chunk_ids for call in indexing_service.cleanup_calls
    )
    assert all(
        call.document_version_id
        not in {indexed_call.document_version_id for indexed_call in indexing_service.calls}
        for call in indexing_service.cleanup_calls
    )


def test_seed_preserves_existing_phase2_strategy_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    indexing_service = _CapturingIndexingService()
    monkeypatch.setattr(
        "app.services.seed.create_document_indexing_service",
        lambda settings: indexing_service,
    )
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        Base.metadata.create_all(engine)
        with Session() as db:
            db.add(
                SystemSetting(
                    setting_key="rag.default_strategy",
                    setting_value="hybrid",
                    description="Operator override",
                )
            )
            db.add(
                SystemSetting(
                    setting_key="rag.router.enabled",
                    setting_value=False,
                    description="Operator router override",
                )
            )
            db.add(
                SystemSetting(
                    setting_key="rag.router.fallback_strategy",
                    setting_value="dense",
                    description="Operator fallback override",
                )
            )
            db.add(
                SystemSetting(
                    setting_key="rag.context_budget.max_context_tokens",
                    setting_value=4096,
                    description="Operator context budget override",
                )
            )
            db.commit()

        with Session() as db:
            seed(db)
        with Session() as db:
            seed(db)

        with Session() as db:
            assert _setting_value(db, "rag.default_strategy") == "hybrid"
            assert _setting_value(db, "rag.hybrid.enabled") is True
            assert _setting_value(db, "rag.hybrid.fusion_method") == "rrf"
            assert _setting_value(db, "rag.router.enabled") is False
            assert _setting_value(db, "rag.router.fallback_strategy") == "dense"
            assert _setting_value(db, "rag.router.allow_agentic_search") is True
            assert _setting_value(db, "rag.router.allow_agentic_ask") is True
            assert _setting_value(db, "rag.context_budget.max_context_tokens") == 4096
            assert _setting_value(db, "rag.context_budget.max_context_items") == 12
            assert _setting_value(db, "rag.sparse.enabled") is True
            assert _setting_value(db, "rag.sparse.provider") == "postgres_fts"
            assert _setting_value(db, "rag.query_analyzer.enabled") is True
            assert _setting_value(db, "rag.query_planner.enabled") is True
            assert _setting_value(db, "rag.evaluation.default_dataset") == {
                "dataset_name": "phase2_strategy_smoke",
                "strategy_type": "dense",
                "case_limit": 5,
            }
    finally:
        engine.dispose()


def _setting_value(db: Any, key: str) -> object:
    setting = db.get(SystemSetting, key)
    assert setting is not None
    return setting.setting_value


def test_major_db_constraints_reject_invalid_data(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        role_id = conn.execute(
            text("SELECT role_id FROM roles WHERE role_name = 'viewer'")
        ).scalar_one()
        admin_user_id = conn.execute(
            text("SELECT user_id FROM users WHERE email = 'admin@example.com'")
        ).scalar_one()

    suffix = uuid.uuid4().hex
    assert_rejected(
        pg_engine,
        """
        INSERT INTO users (role_id, email, display_name, status)
        VALUES (:role_id, :email, 'Invalid Status', 'locked')
        """,
        {"role_id": role_id, "email": f"invalid-{suffix}@example.com"},
    )
    assert_rejected(
        pg_engine,
        """
        INSERT INTO users (role_id, email, display_name, status)
        VALUES (:role_id, 'admin@example.com', 'Duplicate Admin', 'active')
        """,
        {"role_id": role_id},
    )
    assert_rejected(
        pg_engine,
        """
        INSERT INTO jobs (job_type, status)
        VALUES ('document_ingest', 'paused')
        """,
    )

    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            logical_id = conn.execute(
                text(
                    """
                    INSERT INTO logical_documents (owner_user_id, title, status)
                    VALUES (:owner_user_id, :title, 'active')
                    RETURNING logical_document_id
                    """
                ),
                {"owner_user_id": admin_user_id, "title": f"constraint-doc-{suffix}"},
            ).scalar_one()
            common = {
                "logical_document_id": logical_id,
                "created_by": admin_user_id,
                "mime_type": "text/plain",
                "file_size_bytes": 1,
            }
            conn.execute(
                text(
                    """
                    INSERT INTO document_versions (
                        logical_document_id, version_no, content_hash, status, is_active,
                        file_name, mime_type, file_size_bytes, created_by
                    )
                    VALUES (
                        :logical_document_id, 1, :content_hash, 'ready', TRUE,
                        'v1.txt', :mime_type, :file_size_bytes, :created_by
                    )
                    """
                ),
                common | {"content_hash": "a" * 64},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO document_versions (
                            logical_document_id, version_no, content_hash, status, is_active,
                            file_name, mime_type, file_size_bytes, created_by
                        )
                        VALUES (
                            :logical_document_id, 2, :content_hash, 'ready', TRUE,
                            'v2.txt', :mime_type, :file_size_bytes, :created_by
                        )
                        """
                    ),
                    common | {"content_hash": "b" * 64},
                )
        finally:
            transaction.rollback()


def test_jobs_active_retry_partial_unique_index(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            source_job_id = conn.execute(
                text(
                    """
                    INSERT INTO jobs (
                        job_type, status, started_at, finished_at, error_code
                    )
                    VALUES ('document_ingest', 'failed', now(), now(), 'seed_test_failure')
                    RETURNING job_id
                    """
                )
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (job_type, status, retry_of_job_id)
                    VALUES ('document_ingest', 'queued', :source_job_id)
                    """
                ),
                {"source_job_id": source_job_id},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO jobs (job_type, status, retry_of_job_id)
                        VALUES ('document_ingest', 'queued', :source_job_id)
                        """
                    ),
                    {"source_job_id": source_job_id},
                )
        finally:
            transaction.rollback()


def test_jobs_message_edit_active_partial_unique_index(pg_engine: Engine) -> None:
    with pg_engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (job_type, status, target_type, target_id)
                    VALUES ('message_edit_regeneration', 'queued', 'chat_message', 100)
                    """
                )
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        """
                        INSERT INTO jobs (job_type, status, target_type, target_id)
                        VALUES ('message_edit_regeneration', 'queued', 'chat_message', 100)
                        """
                    )
                )
        finally:
            transaction.rollback()

        transaction = conn.begin()
        try:
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (
                        job_type, status, target_type, target_id, started_at, finished_at
                    )
                    VALUES (
                        'message_edit_regeneration', 'succeeded', 'chat_message', 100,
                        now(), now()
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO jobs (job_type, status, target_type, target_id)
                    VALUES ('message_edit_regeneration', 'queued', 'chat_message', 100)
                    """
                )
            )
        finally:
            transaction.rollback()


@dataclass(frozen=True)
class _IndexCall:
    document_version_id: int
    title: str
    version_status: str
    is_active: bool
    chunk_count: int
    chunk_ids: list[int]


class _CapturingIndexingService:
    def __init__(self) -> None:
        self.calls: list[_IndexCall] = []
        self.cleanup_calls: list[_CleanupCall] = []

    def index_chunks(
        self,
        *,
        logical_document: Any,
        document_version: Any,
        chunks: list[Any],
    ) -> None:
        self.calls.append(
            _IndexCall(
                document_version_id=document_version.document_version_id,
                title=logical_document.title,
                version_status=document_version.status,
                is_active=document_version.is_active,
                chunk_count=len(chunks),
                chunk_ids=[chunk.document_chunk_id for chunk in chunks],
            )
        )

    def cleanup_document_points(
        self,
        *,
        document_version_id: int,
        document_chunk_ids: list[int],
    ) -> None:
        self.cleanup_calls.append(
            _CleanupCall(
                document_version_id=document_version_id,
                chunk_ids=document_chunk_ids,
            )
        )


@dataclass(frozen=True)
class _CleanupCall:
    document_version_id: int
    chunk_ids: list[int]


def test_deployed_seed_omits_unindexed_demo_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.seed as seed_module

    class FakeSession:
        def __init__(self) -> None:
            self.committed = False

        def commit(self) -> None:
            self.committed = True

    fake_db = FakeSession()
    seeded_documents: list[object] = []
    monkeypatch.setattr(
        seed_module,
        "get_settings",
        lambda: type("FakeSettings", (), {"app_env": "local"})(),
    )
    monkeypatch.setattr(seed_module, "_seed_roles", lambda db: {})
    monkeypatch.setattr(seed_module, "_seed_users", lambda db, roles, **kwargs: None)
    monkeypatch.setattr(seed_module, "_seed_system_settings", lambda db: None)
    monkeypatch.setattr(
        seed_module,
        "_seed_demo_document",
        lambda db, **kwargs: seeded_documents.append(kwargs),
    )

    seed_module.seed(
        cast(Any, fake_db),
        index_documents=False,
        deployed_admin_email="aws-admin@example.com",
        deployed_admin_password="strong-deployed-password",
    )

    assert fake_db.committed is True
    assert seeded_documents == []
