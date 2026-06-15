from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Neo4jConnectionConfig:
    uri: str | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = "neo4j"
    connect_timeout_seconds: float = 3.0

    @property
    def is_configured(self) -> bool:
        return bool(self.uri and self.user and self.password)

    @classmethod
    def from_settings(cls, settings: object) -> Neo4jConnectionConfig:
        return cls(
            uri=_optional_str(getattr(settings, "neo4j_uri", None)),
            user=_optional_str(getattr(settings, "neo4j_user", None)),
            password=_optional_str(getattr(settings, "neo4j_password", None)),
            database=_optional_str(getattr(settings, "neo4j_database", "neo4j")) or "neo4j",
            connect_timeout_seconds=float(getattr(settings, "neo4j_connect_timeout_seconds", 3.0)),
        )


class Neo4jUnavailable(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class Neo4jClient:
    def __init__(
        self,
        *,
        config: Neo4jConnectionConfig,
        driver: Any | None = None,
    ) -> None:
        self.config = config
        self._driver = driver
        self._owns_driver = driver is None

    def unavailable_reason(self) -> str | None:
        if not self.config.is_configured:
            return "neo4j_not_configured"
        if self._driver is None and _load_graph_database() is None:
            return "neo4j_driver_unavailable"
        return None

    def execute(
        self, query: str, parameters: dict[str, object] | None = None
    ) -> list[dict[str, Any]]:
        driver = self._ensure_driver()
        kwargs: dict[str, object] = dict(parameters or {})
        if self.config.database:
            kwargs["database_"] = self.config.database
        kwargs["result_transformer_"] = _records_to_dicts
        try:
            result = driver.execute_query(query, **kwargs)
        except Neo4jUnavailable:
            raise
        except Exception as exc:
            raise Neo4jUnavailable("neo4j_connection_failed") from exc
        if isinstance(result, list):
            return [dict(item) for item in result]
        return list(result)

    def verify_connectivity(self) -> str | None:
        try:
            driver = self._ensure_driver()
            verify = getattr(driver, "verify_connectivity", None)
            if callable(verify):
                verify()
        except Neo4jUnavailable as exc:
            return exc.reason_code
        except Exception:
            return "neo4j_connection_failed"
        return None

    def close(self) -> None:
        if self._driver is None or not self._owns_driver:
            return
        close = getattr(self._driver, "close", None)
        if callable(close):
            close()
        self._driver = None

    def _ensure_driver(self) -> Any:
        reason = self.unavailable_reason()
        if reason is not None:
            raise Neo4jUnavailable(reason)
        if self._driver is not None:
            return self._driver
        graph_database = _load_graph_database()
        if graph_database is None:
            raise Neo4jUnavailable("neo4j_driver_unavailable")
        try:
            self._driver = graph_database.driver(
                self.config.uri,
                auth=(self.config.user, self.config.password),
                connection_timeout=self.config.connect_timeout_seconds,
            )
        except Exception as exc:
            raise Neo4jUnavailable("neo4j_connection_failed") from exc
        return self._driver


def neo4j_health_status(settings: object) -> dict[str, object]:
    config = Neo4jConnectionConfig.from_settings(settings)
    if not bool(getattr(settings, "neo4j_health_check_enabled", False)):
        return {
            "enabled": False,
            "configured": config.is_configured,
            "status": "disabled",
        }
    client = Neo4jClient(config=config)
    reason = client.verify_connectivity()
    client.close()
    return {
        "enabled": True,
        "configured": config.is_configured,
        "status": "ok" if reason is None else "degraded",
        "reason_code": reason,
    }


def _load_graph_database() -> Any | None:
    try:
        from neo4j import GraphDatabase  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return None
    return GraphDatabase


def _records_to_dicts(result: Any) -> list[dict[str, Any]]:
    return [record.data() for record in result]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
