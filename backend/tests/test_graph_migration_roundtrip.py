from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
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
def pg_engine() -> Iterator[Engine]:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.skip("PostgreSQL migration assertions require a PostgreSQL DATABASE_URL")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        engine.dispose()
        pytest.skip("PostgreSQL migration assertions require a reachable database")
    yield engine
    engine.dispose()


def test_graph_migration_downgrade_upgrade_roundtrip(pg_engine: Engine) -> None:
    from alembic import command

    config = _alembic_config()
    try:
        command.downgrade(config, "0011_tool_result_compression")
        with pg_engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0011_tool_result_compression"
        assert GRAPH_TABLES.isdisjoint(set(inspect(pg_engine).get_table_names()))
    finally:
        command.upgrade(config, "head")

    with pg_engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0012_graph_schema_index"
    assert GRAPH_TABLES <= set(inspect(pg_engine).get_table_names())


def _alembic_config() -> Any:
    from alembic.config import Config

    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    return config
