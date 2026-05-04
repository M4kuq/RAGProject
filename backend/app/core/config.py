from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RAGProject"
    app_env: str = Field(default="local", validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"))
    database_url: str = "sqlite:///./ragproject.db"
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        validation_alias=AliasChoices("CORS_ALLOWED_ORIGINS", "CORS_ORIGINS"),
    )
    session_cookie_name: str = "rag_session"
    csrf_cookie_name: str = "rag_csrf"
    session_secret: str = "dev-only-change-me"
    session_token_bytes: int = Field(default=32, ge=32, le=128)
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_max_age_seconds: int = Field(default=28800, ge=60)
    csrf_header_name: str = "X-CSRF-Token"
    csrf_token_bytes: int = Field(default=32, ge=32, le=128)
    csrf_pre_auth_max_age_seconds: int = Field(default=600, ge=60)
    login_rate_limit_window_seconds: int = Field(default=300, ge=1)
    login_rate_limit_max_attempts: int = Field(default=5, ge=1)
    login_rate_limit_lock_seconds: int = Field(default=300, ge=1)
    login_rate_limit_max_keys: int = Field(default=10000, ge=100)
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    storage_root: Path = Path("storage/uploads")
    upload_max_bytes: int = 20 * 1024 * 1024
    upload_allowed_extensions: list[str] = Field(
        default_factory=lambda: [".pdf", ".docx", ".txt", ".md", ".csv"]
    )
    temp_chat_ttl_minutes: int = 24 * 60
    job_lease_seconds: int = 300
    log_level: str = "INFO"
    pii_masking_enabled: bool = True
    qdrant_url: str = "http://qdrant:6333"
    ollama_url: str = "http://ollama:11434"
    use_fake_llm: bool = False
    model_name: str = "llama3.1"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator(
        "cors_allowed_origins",
        "upload_allowed_extensions",
        "trusted_proxy_ips",
        mode="before",
    )
    @classmethod
    def parse_string_list(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            return json.loads(stripped)
        return [item.strip() for item in stripped.split(",") if item.strip()]

    @model_validator(mode="after")
    def validate_security_settings(self) -> Self:
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE=true is required when SameSite=None")

        if self.app_env.lower() not in {"local", "ci", "test"}:
            weak_values = {
                "dev-only-change-me",
                "change-me-in-local-env",
                "ci-only-change-me",
            }
            if self.session_secret in weak_values or len(self.session_secret) < 32:
                raise ValueError("SESSION_SECRET must be set to a strong random value")
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE=true is required outside local/ci/test")
        return self

    @property
    def environment(self) -> str:
        return self.app_env

    @property
    def cors_origins(self) -> list[str]:
        return self.cors_allowed_origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
