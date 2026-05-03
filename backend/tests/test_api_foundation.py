from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.api.deps import pagination_params
from app.api.error_handlers import register_error_handlers
from app.api.middleware import RequestIdMiddleware, resolve_request_id
from app.api.responses import paginate, success_response
from app.core.config import Settings, get_settings
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
