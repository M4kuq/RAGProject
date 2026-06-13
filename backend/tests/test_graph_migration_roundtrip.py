from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.engine.url import URL
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings

GRAPH_TABLES = {
    "graph_entities",
    "graph_relations",
    "graph_entity_mentions",
    "graph_index_runs",
    "graph_retrieval_paths",
}


@pytest.fixture(scope="module")
def isolated_pg_database() -> Iterator[tuple[Engine, str]]:
    configured_url = make_url(get_settings().database_url)
    if configured_url.get_backend_name() != "postgresql":
        pytest.skip("PostgreSQL migration assertions require a PostgreSQL DATABASE_URL")

    admin_url = configured_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    temp_database = f"rag_pr46_graph_migration_{uuid.uuid4().hex}"
    temp_url = configured_url.set(database=temp_database)
    try:
        with admin_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.execute(text(f'CREATE DATABASE "{temp_database}"'))
    except OperationalError:
        admin_engine.dispose()
        pytest.skip("PostgreSQL migration assertions require CREATE DATABASE permission")

    engine = create_engine(temp_url, pool_pre_ping=True)
    try:
        yield engine, _render_url(temp_url)
    finally:
        engine.dispose()
        try:
            with admin_engine.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{temp_database}" WITH (FORCE)'))
        finally:
            admin_engine.dispose()


def test_graph_migration_downgrade_upgrade_roundtrip(
    isolated_pg_database: tuple[Engine, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from alembic import command
    from app.core.config import get_settings

    pg_engine, database_url = isolated_pg_database
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = _alembic_config()
    try:
        command.upgrade(config, "head")
        with pg_engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0015_langgraph_agentic"
        assert GRAPH_TABLES <= set(inspect(pg_engine).get_table_names())

        command.downgrade(config, "0011_tool_result_compression")
        with pg_engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0011_tool_result_compression"
        assert GRAPH_TABLES.isdisjoint(set(inspect(pg_engine).get_table_names()))

        command.upgrade(config, "head")
        with pg_engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0015_langgraph_agentic"
        assert GRAPH_TABLES <= set(inspect(pg_engine).get_table_names())
    finally:
        get_settings.cache_clear()


def _alembic_config() -> Any:
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option("prepend_sys_path", str(backend_dir))
    return config


def _render_url(url: URL) -> str:
    return url.render_as_string(hide_password=False)
