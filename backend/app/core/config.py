from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
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
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_max_age_seconds: int = 28800
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

    @field_validator("cors_allowed_origins", "upload_allowed_extensions", mode="before")
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

    @property
    def environment(self) -> str:
        return self.app_env

    @property
    def cors_origins(self) -> list[str]:
        return self.cors_allowed_origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
