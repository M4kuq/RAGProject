from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.error_handlers import register_error_handlers
from app.api.middleware import RequestIdMiddleware
from app.api.routers import admin, auth, chat, documents, health, jobs, rag
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", settings.csrf_header_name, "X-Request-ID"],
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
    app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"])
    app.include_router(rag.router, prefix="/api/v1/rag", tags=["rag"])
    app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
    return app


app = create_app()
