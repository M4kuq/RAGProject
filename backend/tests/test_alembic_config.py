from __future__ import annotations

from configparser import ConfigParser

from app.db.alembic_config import escape_alembic_config_value


def test_escape_alembic_config_value_preserves_encoded_database_url() -> None:
    database_url = (
        "postgresql+psycopg://user:p%40ss%25word@example.test:5432/rag"
    )
    parser = ConfigParser()
    parser.add_section("alembic")
    parser.set(
        "alembic",
        "sqlalchemy.url",
        escape_alembic_config_value(database_url),
    )

    assert parser.get("alembic", "sqlalchemy.url") == database_url
