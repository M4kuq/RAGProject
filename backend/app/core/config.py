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
        default_factory=lambda: [
            ".pdf",
            ".docx",
            ".txt",
            ".md",
            ".markdown",
            ".csv",
            ".xlsx",
            ".pptx",
            ".html",
            ".htm",
            ".xml",
        ]
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
    ingest_office_max_pages: int = Field(default=200, ge=1, le=1000)
    ingest_office_max_rows_per_sheet: int = Field(default=5000, ge=1, le=100000)
    ingest_office_max_cells: int = Field(default=100000, ge=1, le=1000000)
    ingest_office_rows_per_chunk: int = Field(default=25, ge=1, le=200)
    ingest_office_max_slides: int = Field(default=300, ge=1, le=1000)
    ingest_html_max_elements: int = Field(default=5000, ge=1, le=100000)
    ingest_xml_max_elements: int = Field(default=5000, ge=1, le=100000)
    document_url_fetch_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    document_url_fetch_max_redirects: int = Field(default=3, ge=0, le=10)
    document_url_fetch_max_bytes: int = Field(default=5_000_000, ge=1024, le=20_000_000)
    document_url_fetch_allowed_schemes: list[str] = Field(default_factory=lambda: ["http", "https"])
    document_url_fetch_allowed_content_types: list[str] = Field(
        default_factory=lambda: [
            "text/html",
            "application/xhtml+xml",
            "text/xml",
            "application/xml",
            "application/rss+xml",
            "application/atom+xml",
        ]
    )
    document_url_fetch_block_private_ips: bool = True
    document_url_fetch_user_agent: str = "RAGProjectBot/Phase2"
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
    ollama_timeout_seconds: float = Field(default=180.0, gt=0)
    use_fake_llm: bool = False
    model_name: str = "llama3.1"
    embedding_provider: str = "fake"
    embedding_model: str = "BAAI/bge-m3"
    embedding_vector_dimension: int = Field(default=1024, ge=1)
    embedding_fake_dimension: int = Field(default=8, ge=1)
    embedding_batch_size: int = Field(default=32, ge=1)
    retrieval_top_k_default: int = Field(default=20, ge=1, le=20)
    retrieval_top_k_max: int = Field(default=20, ge=1, le=20)
    hybrid_enabled: bool = True
    hybrid_fusion_method: str = "rrf"
    hybrid_rrf_k: int = Field(default=60, ge=1, le=1000)
    hybrid_dense_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    hybrid_sparse_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    hybrid_candidate_multiplier: int = Field(default=2, ge=1, le=5)
    sparse_enabled: bool = True
    sparse_provider: str = "postgres_fts"
    sparse_language: str = "simple"
    sparse_min_query_terms: int = Field(default=1, ge=1, le=32)
    sparse_max_query_terms: int = Field(default=32, ge=1, le=64)
    sparse_score_normalization: str = "max"
    query_analyzer_enabled: bool = True
    query_planner_enabled: bool = True
    query_planner_apply_rewrite_to_retrieval: bool = False
    query_planner_max_sub_queries: int = Field(default=3, ge=0, le=3)
    query_planner_max_preview_chars: int = Field(default=160, ge=20, le=240)
    query_planner_store_query_preview: bool = True
    query_planner_redact_pii: bool = True
    router_enabled: bool = True
    router_mode: str = "rule_based"
    router_allow_agentic_search: bool = True
    router_allow_agentic_ask: bool = True
    router_keyword_heavy_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    router_ambiguity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    router_max_retrieval_calls: int = Field(default=2, ge=1, le=3)
    router_max_fallback_calls: int = Field(default=1, ge=0, le=2)
    router_sufficiency_min_candidates: int = Field(default=1, ge=1, le=20)
    router_sufficiency_min_selected: int = Field(default=1, ge=1, le=20)
    router_sufficiency_top_score_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    router_enable_fallback_hybrid: bool = True
    router_enable_fallback_dense: bool = True
    router_no_context_after_budget_exhausted: bool = True
    router_fallback_strategy: str = "fallback_dense"
    router_store_decision_trace: bool = True
    evaluation_failure_low_recall_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation_failure_low_mrr_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation_failure_low_citation_coverage_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation_failure_low_groundedness_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation_failure_low_faithfulness_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    evaluation_failure_high_latency_ms: int = Field(default=3000, ge=1, le=600000)
    evaluation_failure_max_promotions_per_run: int = Field(default=100, ge=1, le=100)
    evaluation_agentic_expected_strategy_required_for_accuracy: bool = False
    trace_export_enabled: bool = False
    trace_export_provider: str = "none"
    trace_export_timeout_seconds: float = Field(default=3.0, gt=0.0, le=30.0)
    trace_export_include_retrieval: bool = True
    trace_export_include_evaluation: bool = True
    trace_export_include_ci_summary: bool = True
    trace_export_include_previews: bool = False
    trace_export_preview_max_chars: int = Field(default=0, ge=0, le=240)
    langsmith_tracing_enabled: bool = False
    langsmith_project: str = "ragproject-phase2"
    langsmith_endpoint: str = ""
    langsmith_api_key: str | None = None
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
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = Field(default=30.0, gt=0)
    anthropic_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_version: str = "2023-06-01"
    anthropic_timeout_seconds: float = Field(default=30.0, gt=0)
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: float = Field(default=30.0, gt=0)
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
        "document_url_fetch_allowed_schemes",
        "document_url_fetch_allowed_content_types",
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
        self.hybrid_fusion_method = self.hybrid_fusion_method.lower()
        if self.hybrid_fusion_method not in {"rrf", "weighted"}:
            raise ValueError("HYBRID_FUSION_METHOD must be rrf or weighted")
        if self.hybrid_dense_weight + self.hybrid_sparse_weight <= 0:
            raise ValueError("At least one hybrid fusion weight must be positive")
        self.sparse_provider = self.sparse_provider.lower()
        if self.sparse_provider != "postgres_fts":
            raise ValueError("SPARSE_PROVIDER must be postgres_fts")
        self.sparse_language = self.sparse_language.lower()
        if self.sparse_language not in {"simple", "english"}:
            raise ValueError("SPARSE_LANGUAGE must be simple or english")
        if self.sparse_min_query_terms > self.sparse_max_query_terms:
            raise ValueError("SPARSE_MIN_QUERY_TERMS must be <= SPARSE_MAX_QUERY_TERMS")
        self.sparse_score_normalization = self.sparse_score_normalization.lower()
        if self.sparse_score_normalization != "max":
            raise ValueError("SPARSE_SCORE_NORMALIZATION must be max")
        self.router_mode = self.router_mode.lower()
        if self.router_mode != "rule_based":
            raise ValueError("ROUTER_MODE must be rule_based")
        self.router_fallback_strategy = self.router_fallback_strategy.lower()
        if self.router_fallback_strategy not in {"dense", "fallback_dense"}:
            raise ValueError("ROUTER_FALLBACK_STRATEGY must be dense or fallback_dense")
        if self.router_max_fallback_calls > self.router_max_retrieval_calls - 1:
            raise ValueError("ROUTER_MAX_FALLBACK_CALLS must be less than retrieval call budget")
        if self.router_sufficiency_min_selected > self.router_sufficiency_min_candidates:
            raise ValueError(
                "ROUTER_SUFFICIENCY_MIN_SELECTED must be <= ROUTER_SUFFICIENCY_MIN_CANDIDATES"
            )
        self.document_url_fetch_allowed_schemes = [
            item.lower() for item in self.document_url_fetch_allowed_schemes
        ]
        if not self.document_url_fetch_allowed_schemes or any(
            item not in {"http", "https"} for item in self.document_url_fetch_allowed_schemes
        ):
            raise ValueError("DOCUMENT_URL_FETCH_ALLOWED_SCHEMES must only include http/https")
        self.document_url_fetch_allowed_content_types = [
            item.lower() for item in self.document_url_fetch_allowed_content_types
        ]
        if not self.document_url_fetch_allowed_content_types:
            raise ValueError("DOCUMENT_URL_FETCH_ALLOWED_CONTENT_TYPES must not be empty")
        self.document_url_fetch_user_agent = (
            self.document_url_fetch_user_agent.strip() or "RAGProjectBot/Phase2"
        )
        self.trace_export_provider = self.trace_export_provider.lower()
        if self.trace_export_provider not in {"none", "langsmith"}:
            raise ValueError("TRACE_EXPORT_PROVIDER must be none or langsmith")
        if self.trace_export_preview_max_chars > 0 and not self.trace_export_include_previews:
            raise ValueError(
                "TRACE_EXPORT_INCLUDE_PREVIEWS=true is required when preview chars are enabled"
            )
        self.langsmith_endpoint = self.langsmith_endpoint.rstrip("/")
        self.langsmith_api_key = self.langsmith_api_key.strip() if self.langsmith_api_key else None
        self.langsmith_project = self.langsmith_project.strip() or "ragproject-phase2"
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
        if self.generation_provider not in {
            "fake",
            "ollama",
            "lmstudio",
            "openai",
            "anthropic",
            "gemini",
        }:
            raise ValueError(
                "GENERATION_PROVIDER must be fake, ollama, lmstudio, openai, anthropic, or gemini"
            )
        self.lmstudio_base_url = self.lmstudio_base_url.rstrip("/")
        self.lmstudio_api_key = self.lmstudio_api_key.strip() or "lm-studio"
        self.openai_api_key = self.openai_api_key.strip() if self.openai_api_key else None
        self.openai_base_url = self.openai_base_url.rstrip("/")
        self.anthropic_api_key = self.anthropic_api_key.strip() if self.anthropic_api_key else None
        self.anthropic_base_url = self.anthropic_base_url.rstrip("/")
        self.gemini_api_key = self.gemini_api_key.strip() if self.gemini_api_key else None
        self.gemini_base_url = self.gemini_base_url.rstrip("/")
        if self.generation_provider == "openai" and not self.openai_base_url:
            raise ValueError("OPENAI_BASE_URL is required when GENERATION_PROVIDER=openai")
        if self.generation_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when GENERATION_PROVIDER=openai")
        if self.generation_provider == "anthropic" and not self.anthropic_base_url:
            raise ValueError("ANTHROPIC_BASE_URL is required when GENERATION_PROVIDER=anthropic")
        if self.generation_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when GENERATION_PROVIDER=anthropic")
        if self.generation_provider == "gemini" and not self.gemini_base_url:
            raise ValueError("GEMINI_BASE_URL is required when GENERATION_PROVIDER=gemini")
        if self.generation_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when GENERATION_PROVIDER=gemini")
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
