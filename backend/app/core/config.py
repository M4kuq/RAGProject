from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "RAGProject"
    environment: str = "local"
    database_url: str = "sqlite:///./ragproject.db"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_cookie_name: str = "rag_session"
    csrf_cookie_name: str = "rag_csrf"
    session_secret: str = "dev-only-change-me"
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "lax"
    session_cookie_max_age_seconds: int = 28800
    storage_root: Path = Path("storage/uploads")
    qdrant_url: str = "http://qdrant:6333"
    ollama_url: str = "http://ollama:11434"
    use_fake_llm: bool = False
    model_name: str = "llama3.1"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
