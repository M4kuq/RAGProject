from __future__ import annotations


def escape_alembic_config_value(value: str) -> str:
    """Escape percent signs consumed by ConfigParser interpolation."""
    return value.replace("%", "%%")
