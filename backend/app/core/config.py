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
        default_factory=lambda: [".pdf", ".docx", ".txt", ".md", ".markdown", ".csv"]
    )
    temp_chat_ttl_minutes: int = 120
    job_lease_seconds: int = 300
    worker_poll_interval_ms: int = Field(default=1000, ge=100)
    worker_batch_size: int = Field(default=1, ge=1, le=100)
    worker_lease_seconds: int = Field(default=300, ge=1)
    worker_lease_renew_interval_seconds: int = Field(default=60, ge=1)
    worker_shutdown_grace_seconds: int = Field(default=30, ge=1)
    worker_enabled_job_types: str = "all"
    worker_instance_name: str | None = None
    ingest_chunk_size_tokens: int = Field(default=512, ge=1)
    ingest_chunk_overlap_tokens: int = Field(default=128, ge=0)
    ingest_max_extracted_text_chars: int = Field(default=5_000_000, ge=1)
    ingest_chunk_preview_chars: int = Field(default=200, ge=1, le=2000)
    log_level: str = "INFO"
    pii_masking_enabled: bool = True
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection_name: str = "document_chunks"
    qdrant_distance: str = "Cosine"
    qdrant_create_collection: bool = True
    qdrant_required: bool = False
    qdrant_upsert_batch_size: int = Field(default=64, ge=1)
    qdrant_timeout_seconds: float = Field(default=5.0, gt=0)
    ollama_url: str = "http://ollama:11434"
    use_fake_llm: bool = False
    model_name: str = "llama3.1"
    embedding_provider: str = "fake"
    embedding_model: str = "BAAI/bge-m3"
    embedding_vector_dimension: int = Field(default=1024, ge=1)
    embedding_fake_dimension: int = Field(default=8, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)
    retrieval_top_k_default: int = Field(default=20, ge=1, le=20)
    retrieval_top_k_max: int = Field(default=20, ge=1, le=20)
    rerank_provider: str = "fake"
    rerank_top_n_default: int = Field(default=5, ge=1, le=20)
    rerank_top_n_max: int = Field(default=5, ge=1, le=20)
    rerank_score_min: float = 0.0
    rerank_score_max: float = 1.0
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    search_snippet_max_chars: int = Field(default=240, ge=20, le=2000)
    ask_top_k_default: int = Field(default=20, ge=1, le=20)
    ask_rerank_top_n_default: int = Field(default=5, ge=1, le=20)
    generation_provider: str = "fake"
    generation_model_name: str = "fake-rag-answer"
    generation_max_context_chars: int = Field(default=6000, ge=100, le=50000)
    generation_max_output_chars: int = Field(default=8000, ge=20, le=20000)
    generation_max_output_tokens: int = Field(default=8192, ge=128, le=8192)
    lmstudio_base_url: str = "http://host.docker.internal:1234/v1"
    lmstudio_api_key: str = "lm-studio"
    lmstudio_timeout_seconds: float = Field(default=180.0, gt=0)
    citation_preview_max_chars: int = Field(default=240, ge=20, le=2000)
    confidence_high_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    confidence_medium_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    groundedness_high_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    groundedness_medium_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    mcp_enabled: bool = True
    mcp_transport: str = "stdio"
    mcp_local_only: bool = True
    mcp_actor_mode: str = "mcp_local"
    mcp_snippet_max_chars: int = Field(default=240, ge=20, le=2000)
    mcp_tool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    mcp_allow_write_tools: bool = False

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
        if self.ingest_chunk_overlap_tokens >= self.ingest_chunk_size_tokens:
            raise ValueError(
                "INGEST_CHUNK_OVERLAP_TOKENS must be smaller than INGEST_CHUNK_SIZE_TOKENS"
            )
        self.embedding_provider = self.embedding_provider.lower()
        if self.embedding_provider not in {"fake", "local", "lmstudio"}:
            raise ValueError("EMBEDDING_PROVIDER must be fake, local, or lmstudio")
        self.rerank_provider = self.rerank_provider.lower()
        if self.rerank_provider not in {"none", "fake", "local"}:
            raise ValueError("RERANK_PROVIDER must be none, fake, or local")
        if self.retrieval_top_k_default > self.retrieval_top_k_max:
            raise ValueError("RETRIEVAL_TOP_K_DEFAULT must be <= RETRIEVAL_TOP_K_MAX")
        if self.rerank_top_n_default > self.rerank_top_n_max:
            raise ValueError("RERANK_TOP_N_DEFAULT must be <= RERANK_TOP_N_MAX")
        if self.rerank_score_max <= self.rerank_score_min:
            raise ValueError("RERANK_SCORE_MAX must be greater than RERANK_SCORE_MIN")
        if self.confidence_high_threshold <= self.confidence_medium_threshold:
            raise ValueError(
                "CONFIDENCE_HIGH_THRESHOLD must be greater than CONFIDENCE_MEDIUM_THRESHOLD"
            )
        if self.groundedness_high_threshold <= self.groundedness_medium_threshold:
            raise ValueError(
                "GROUNDEDNESS_HIGH_THRESHOLD must be greater than GROUNDEDNESS_MEDIUM_THRESHOLD"
            )
        if (
            "ask_top_k_default" in self.model_fields_set
            and self.ask_top_k_default > self.retrieval_top_k_max
        ):
            raise ValueError("ASK_TOP_K_DEFAULT must be <= RETRIEVAL_TOP_K_MAX")
        if (
            "ask_rerank_top_n_default" in self.model_fields_set
            and self.ask_rerank_top_n_default > self.rerank_top_n_max
        ):
            raise ValueError("ASK_RERANK_TOP_N_DEFAULT must be <= RERANK_TOP_N_MAX")
        self.generation_provider = self.generation_provider.lower()
        if self.generation_provider not in {"fake", "ollama", "lmstudio"}:
            raise ValueError("GENERATION_PROVIDER must be fake, ollama, or lmstudio")
        self.lmstudio_base_url = self.lmstudio_base_url.rstrip("/")
        self.lmstudio_api_key = self.lmstudio_api_key.strip() or "lm-studio"
        self.mcp_transport = self.mcp_transport.lower()
        if self.mcp_transport != "stdio":
            raise ValueError("MCP_TRANSPORT must be stdio in Phase1")
        if not self.mcp_local_only:
            raise ValueError("MCP_LOCAL_ONLY must be true in Phase1")
        if self.mcp_actor_mode != "mcp_local":
            raise ValueError("MCP_ACTOR_MODE must be mcp_local in Phase1")
        if self.mcp_allow_write_tools:
            raise ValueError("MCP_ALLOW_WRITE_TOOLS must be false in Phase1")
        distance = self.qdrant_distance.strip().lower()
        if distance not in {"cosine", "dot", "euclid"}:
            raise ValueError("QDRANT_DISTANCE must be cosine, dot, or euclid")
        self.qdrant_distance = {"cosine": "Cosine", "dot": "Dot", "euclid": "Euclid"}[distance]

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

    @property
    def effective_embedding_dimension(self) -> int:
        if self.embedding_provider == "fake":
            return self.embedding_fake_dimension
        return self.embedding_vector_dimension


@lru_cache
def get_settings() -> Settings:
    return Settings()
