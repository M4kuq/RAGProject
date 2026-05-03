from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import pagination_params, require_admin, require_csrf
from app.api.error_handlers import register_error_handlers
from app.api.middleware import RequestIdMiddleware, resolve_request_id
from app.api.responses import paginate, success_response
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.models import Job, User
from app.db.session import get_db
from app.main import create_app
from app.schemas.common import PaginationParams


def test_csrf_success_response_includes_meta_request_id() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/auth/csrf", headers={"X-Request-ID": "client.Trace-1"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "client.Trace-1"
    body = response.json()
    assert set(body) == {"data", "meta"}
    assert body["data"]["csrf_token"]
    assert body["meta"]["request_id"] == "client.Trace-1"


def test_error_response_uses_common_envelope_and_safe_details() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "not-an-email", "password": "secret"},
        headers={"X-Request-ID": "validation-1"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Invalid request."
    assert body["meta"]["request_id"] == "validation-1"
    assert body["error"]["details"][0]["field"] == "email"
    assert "email" in body["error"]["details"][0]["reason"].lower()
    assert "secret" not in response.text


def test_invalid_request_id_is_replaced() -> None:
    replaced = resolve_request_id("bad id")

    assert replaced.startswith("req_")
    assert replaced != "bad id"


def test_not_found_error_includes_generated_request_id() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/missing", headers={"X-Request-ID": "bad id"})

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "resource_not_found"
    assert body["meta"]["request_id"].startswith("req_")
    assert response.headers["X-Request-ID"] == body["meta"]["request_id"]


def test_pagination_helper_contract() -> None:
    page, meta = paginate([1, 2, 3, 4, 5], PaginationParams(page=2, page_size=2))

    assert page == [3, 4]
    assert meta.model_dump() == {"page": 2, "page_size": 2, "total": 5, "has_next": True}


def test_pagination_dependency_returns_validation_error() -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/items")
    def items(
        request: Request,
        pagination: Annotated[PaginationParams, Depends(pagination_params)],
    ) -> dict[str, object]:
        page, meta = paginate([1, 2, 3], pagination)
        return success_response(page, request, pagination=meta)

    client = TestClient(app)

    ok = client.get("/items?page=1&page_size=2", headers={"X-Request-ID": "page-1"})
    invalid = client.get("/items?page=1&page_size=101", headers={"X-Request-ID": "page-2"})

    assert ok.status_code == 200
    assert ok.json()["meta"] == {
        "request_id": "page-1",
        "pagination": {"page": 1, "page_size": 2, "total": 3, "has_next": True},
    }
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert invalid.json()["meta"]["request_id"] == "page-2"


def test_admin_jobs_pagination_is_not_pre_limited_to_first_50_rows() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as db:
        db.add_all(
            Job(job_id=index, job_type="test", status="queued", payload={})
            for index in range(1, 56)
        )
        db.commit()

    def override_db():
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_csrf] = lambda: None
    app.dependency_overrides[require_admin] = lambda: User(user_id=1, email="admin@example.com")
    try:
        response = TestClient(app).get(
            "/api/v1/jobs?page=3&page_size=20",
            headers={"X-Request-ID": "jobs-page-3"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert len(body["data"]) == 15
    assert body["meta"]["pagination"] == {
        "page": 3,
        "page_size": 20,
        "total": 55,
        "has_next": False,
    }


def test_unhandled_exception_is_logged_with_generic_error_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level("ERROR", logger="app.api.error_handlers"):
        response = client.get("/boom", headers={"X-Request-ID": "explode-1"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "Internal server error.",
            "details": {},
        },
        "meta": {"request_id": "explode-1"},
    }
    assert any(
        record.message == "Unhandled API exception"
        and record.__dict__.get("request_id") == "explode-1"
        and record.__dict__.get("exception_type") == "RuntimeError"
        for record in caplog.records
    )


def test_settings_accepts_canonical_and_legacy_env_names(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "ci")
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS",
        '["http://localhost:5173","http://127.0.0.1:5173"]',
    )
    settings = Settings()

    assert settings.app_env == "ci"
    assert settings.environment == "ci"
    assert settings.cors_allowed_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert settings.cors_origins == settings.cors_allowed_origins

    monkeypatch.delenv("APP_ENV")
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS")
    monkeypatch.setenv("ENVIRONMENT", "legacy")
    monkeypatch.setenv("CORS_ORIGINS", '["http://legacy.local"]')

    legacy_settings = Settings()
    assert legacy_settings.app_env == "legacy"
    assert legacy_settings.cors_allowed_origins == ["http://legacy.local"]


def test_cors_uses_formal_allowed_origins_env(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://frontend.local"]')
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        response = client.options(
            "/api/v1/auth/csrf",
            headers={
                "Origin": "http://frontend.local",
                "Access-Control-Request-Method": "GET",
            },
        )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://frontend.local"
    assert response.headers["access-control-allow-credentials"] == "true"
