from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_WEAK_SESSION_SECRETS = {
    "dev-only-change-me",
    "change-me-in-local-env",
    "ci-only-change-me",
}


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
    storage_backend: str = "local"
    documents_bucket_name: str | None = None
    documents_key_prefix: str = ""
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
    document_diff_preview_max_chars: int = Field(default=500, ge=40, le=2000)
    document_diff_max_items: int = Field(default=200, ge=1, le=500)
    document_diff_text_similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    citation_source_preview_max_chars: int = Field(default=500, ge=40, le=2000)
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
    aws_region: str = "ap-northeast-1"
    aws_sdk_connect_timeout_seconds: float = Field(default=3.0, gt=0.0, le=60.0)
    aws_sdk_read_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    aws_sdk_max_attempts: int = Field(default=3, ge=1, le=10)
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    retrieval_top_k_default: int = Field(default=20, ge=1, le=20)
    retrieval_top_k_max: int = Field(default=20, ge=1, le=20)
    retrieval_cache_enabled: bool = False
    retrieval_cache_namespace: str = "rag.retrieval"
    retrieval_cache_ttl_seconds: int = Field(default=300, ge=1, le=86400)
    hybrid_enabled: bool = True
    hybrid_fusion_method: str = "rrf"
    hybrid_rrf_k: int = Field(default=60, ge=1, le=1000)
    hybrid_dense_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    hybrid_sparse_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    hybrid_candidate_multiplier: int = Field(default=2, ge=1, le=5)
    graph_retrieval_enabled: bool = True
    graph_store_provider: str = "neo4j"
    graph_extractor_type: str = "llm"
    graph_extraction_provider: str | None = None
    graph_extraction_model_name: str | None = None
    graph_extraction_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    graph_extraction_max_output_chars: int = Field(default=12000, ge=1000, le=50000)
    graph_extraction_max_output_tokens: int = Field(default=2048, ge=128, le=8192)
    graph_extraction_max_entities_per_chunk: int = Field(default=20, ge=1, le=100)
    graph_extraction_max_relations_per_chunk: int = Field(default=40, ge=1, le=200)
    graph_extraction_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    graph_retrieval_max_start_entities: int = Field(default=5, ge=1, le=20)
    graph_retrieval_max_depth: int = Field(default=2, ge=1, le=4)
    graph_retrieval_max_paths: int = Field(default=20, ge=1, le=100)
    graph_retrieval_max_relations_per_entity: int = Field(default=20, ge=1, le=100)
    graph_retrieval_max_source_chunks: int = Field(default=20, ge=1, le=100)
    graph_retrieval_timeout_ms: int = Field(default=3000, ge=100, le=30000)
    graph_retrieval_fallback_strategy: str = "hybrid"
    graph_retrieval_min_entity_match_score: float = Field(default=0.5, ge=0.0, le=1.0)
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    neo4j_database: str = "neo4j"
    neo4j_connect_timeout_seconds: float = Field(default=3.0, gt=0.0, le=30.0)
    neo4j_health_check_enabled: bool = False
    neo4j_projection_enabled: bool = False
    neo4j_projection_connect_retry_attempts: int = Field(default=1, ge=1, le=60)
    neo4j_projection_connect_retry_delay_seconds: float = Field(default=1.0, ge=0.0, le=10.0)
    graph_router_enabled: bool = False
    graph_router_min_signal_score: float = Field(default=0.5, ge=0.0, le=1.0)
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
    router_llm_planner_model_name: str | None = None
    router_llm_planner_timeout_seconds: float = Field(default=30.0, gt=0.0, le=600.0)
    router_llm_planner_max_output_tokens: int = Field(default=256, ge=64, le=1024)
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
    llm_orchestrator_enabled: bool = True
    llm_orchestrator_max_tool_calls: int = Field(default=8, ge=1, le=10)
    llm_orchestrator_max_search_calls: int = Field(default=8, ge=1, le=10)
    llm_orchestrator_timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    llm_orchestrator_max_query_chars: int = Field(default=500, ge=1, le=1000)
    llm_orchestrator_max_tool_result_items: int = Field(default=10, ge=1, le=20)
    llm_orchestrator_max_snippet_chars: int = Field(default=500, ge=20, le=1000)
    llm_orchestrator_allow_trace_inspection: bool = True
    llm_orchestrator_allow_admin_tools: bool = False
    langchain_agentic_enabled: bool = True
    langchain_agentic_max_tool_calls: int = Field(default=8, ge=1, le=10)
    langchain_agentic_max_search_calls: int = Field(default=8, ge=1, le=10)
    langchain_agentic_timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    langchain_agentic_max_query_chars: int = Field(default=500, ge=1, le=1000)
    langchain_agentic_max_tool_result_items: int = Field(default=10, ge=1, le=20)
    langchain_agentic_max_snippet_chars: int = Field(default=500, ge=20, le=1000)
    langchain_agentic_allow_admin_tools: bool = False
    langgraph_agentic_enabled: bool = True
    langgraph_agentic_max_tool_calls: int = Field(default=8, ge=1, le=10)
    langgraph_agentic_max_search_calls: int = Field(default=8, ge=1, le=10)
    langgraph_agentic_timeout_seconds: float = Field(default=600.0, gt=0.0, le=600.0)
    langgraph_agentic_max_query_chars: int = Field(default=500, ge=1, le=1000)
    langgraph_agentic_max_tool_result_items: int = Field(default=10, ge=1, le=20)
    langgraph_agentic_max_snippet_chars: int = Field(default=500, ge=20, le=1000)
    langgraph_agentic_allow_admin_tools: bool = False
    tool_result_compression_enabled: bool = True
    tool_result_compression_max_items_per_tool: int = Field(default=8, ge=1, le=100)
    tool_result_compression_max_total_items_per_turn: int = Field(default=20, ge=1, le=200)
    tool_result_compression_max_snippet_chars: int = Field(default=500, ge=20, le=5000)
    tool_result_compression_max_tokens_per_tool: int = Field(default=1200, ge=1, le=200_000)
    tool_result_compression_max_total_tool_result_tokens: int = Field(
        default=3000,
        ge=1,
        le=200_000,
    )
    tool_result_compression_drop_low_score_first: bool = True
    tool_result_compression_group_by_source: bool = True
    tool_result_compression_reject_oversized_output: bool = True
    tool_result_compression_store_debug_trace: bool = True
    context_budget_enabled: bool = True
    context_budget_max_context_tokens: int = Field(default=6000, ge=1, le=200_000)
    context_budget_reserve_answer_tokens: int = Field(default=1000, ge=0, le=200_000)
    context_budget_max_context_items: int = Field(default=12, ge=1, le=100)
    context_budget_max_tokens_per_item: int = Field(default=1200, ge=1, le=200_000)
    context_budget_min_citation_candidates: int = Field(default=1, ge=0, le=100)
    context_budget_drop_low_score_first: bool = True
    context_budget_preserve_source_diversity: bool = True
    context_budget_token_estimator: str = "heuristic"
    context_budget_store_debug_trace: bool = True
    evidence_pack_enabled: bool = True
    evidence_pack_max_items: int = Field(default=12, ge=1, le=100)
    evidence_pack_max_items_per_source: int = Field(default=4, ge=1, le=100)
    evidence_pack_max_chars_per_item: int = Field(default=1200, ge=20, le=50_000)
    evidence_pack_max_total_chars: int = Field(default=12_000, ge=20, le=200_000)
    evidence_pack_near_duplicate_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    evidence_pack_preserve_citation_candidates: bool = True
    evidence_pack_group_by_source: bool = True
    evidence_pack_store_debug_trace: bool = True
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
    bedrock_rerank_model_id: str = "amazon.rerank-v1:0"
    search_snippet_max_chars: int = Field(default=240, ge=20, le=2000)
    ask_top_k_default: int = Field(default=20, ge=1, le=20)
    ask_rerank_top_n_default: int = Field(default=5, ge=1, le=20)
    generation_provider: str = "fake"
    generation_model_name: str = "fake-rag-answer"
    generation_max_context_chars: int = Field(default=6000, ge=100, le=50000)
    generation_max_output_chars: int = Field(default=8000, ge=20, le=20000)
    generation_max_output_tokens: int = Field(default=8192, ge=128, le=8192)
    bedrock_generation_model_id: str = "amazon.nova-lite-v1:0"
    generation_retry_on_insufficient_evidence: bool = True
    generation_pricing_overrides: object = Field(default={})
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
    mcp_http_api_key: str | None = None
    mcp_local_only: bool = True
    mcp_actor_mode: str = "mcp_local"
    mcp_snippet_max_chars: int = Field(default=240, ge=20, le=2000)
    mcp_tool_timeout_seconds: int = Field(default=30, ge=1, le=300)
    mcp_allow_write_tools: bool = False
    mcp_enable_advanced_rag_tools: bool = True
    mcp_allowed_strategies: list[str] = Field(
        default_factory=lambda: [
            "dense",
            "sparse",
            "hybrid",
            "agentic_router",
            "llm_tool_orchestrator",
            "langchain_agentic",
            "langgraph_agentic",
        ]
    )
    mcp_include_trace_summary_default: bool = False
    mcp_max_answer_chars: int = Field(default=4000, ge=20, le=8000)
    mcp_allow_evaluation_run_create: bool = False

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
        "mcp_allowed_strategies",
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

    @field_validator(
        "neo4j_uri",
        "neo4j_user",
        "neo4j_password",
        "graph_extraction_provider",
        "graph_extraction_model_name",
        mode="before",
    )
    @classmethod
    def blank_string_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("generation_pricing_overrides", mode="before")
    @classmethod
    def parse_generation_pricing_overrides(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return {}
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("Ignoring invalid GENERATION_PRICING_OVERRIDES JSON.")
                return {}
            if isinstance(parsed, dict):
                return parsed
            logger.warning("Ignoring non-object GENERATION_PRICING_OVERRIDES JSON.")
            return {}
        if isinstance(value, dict):
            return value
        return {}

    @model_validator(mode="after")
    def validate_security_settings(self) -> Self:
        if self.session_cookie_samesite == "none" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE=true is required when SameSite=None")
        if self.ingest_chunk_overlap_tokens >= self.ingest_chunk_size_tokens:
            raise ValueError(
                "INGEST_CHUNK_OVERLAP_TOKENS must be smaller than INGEST_CHUNK_SIZE_TOKENS"
            )
        self.storage_backend = self.storage_backend.strip().lower()
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("STORAGE_BACKEND must be local or s3")
        self.documents_bucket_name = (
            self.documents_bucket_name.strip() if self.documents_bucket_name else None
        )
        self.documents_key_prefix = self.documents_key_prefix.strip().strip("/")
        if self.documents_key_prefix and (
            "\\" in self.documents_key_prefix
            or any(part in {"", ".", ".."} for part in self.documents_key_prefix.split("/"))
        ):
            raise ValueError("DOCUMENTS_KEY_PREFIX must be a safe relative prefix")
        if self.storage_backend == "s3" and not self.documents_bucket_name:
            raise ValueError("DOCUMENTS_BUCKET_NAME is required when STORAGE_BACKEND=s3")
        self.aws_region = self.aws_region.strip()
        if not self.aws_region:
            raise ValueError("AWS_REGION must not be empty")
        self.bedrock_generation_model_id = self.bedrock_generation_model_id.strip()
        self.bedrock_embedding_model_id = self.bedrock_embedding_model_id.strip()
        self.bedrock_rerank_model_id = self.bedrock_rerank_model_id.strip()
        if (
            not self.bedrock_generation_model_id
            or not self.bedrock_embedding_model_id
            or not self.bedrock_rerank_model_id
        ):
            raise ValueError("Bedrock model IDs must not be empty")
        self.embedding_provider = self.embedding_provider.lower()
        if self.embedding_provider not in {"fake", "local", "lmstudio", "bedrock"}:
            raise ValueError("EMBEDDING_PROVIDER must be fake, local, lmstudio, or bedrock")
        if self.embedding_provider == "bedrock":
            if self.embedding_vector_dimension not in {256, 512, 1024}:
                raise ValueError(
                    "EMBEDDING_VECTOR_DIMENSION must be 256, 512, or 1024 for Bedrock Titan V2"
                )
            self.embedding_model = self.bedrock_embedding_model_id
        self.retrieval_cache_namespace = self.retrieval_cache_namespace.strip()
        if not self.retrieval_cache_namespace:
            raise ValueError("RETRIEVAL_CACHE_NAMESPACE must not be empty")
        self.rerank_provider = self.rerank_provider.lower()
        if self.rerank_provider not in {"none", "fake", "local", "bedrock"}:
            raise ValueError("RERANK_PROVIDER must be none, fake, local, or bedrock")
        if self.rerank_provider == "bedrock":
            self.reranker_model = self.bedrock_rerank_model_id
        if self.retrieval_top_k_default > self.retrieval_top_k_max:
            raise ValueError("RETRIEVAL_TOP_K_DEFAULT must be <= RETRIEVAL_TOP_K_MAX")
        self.hybrid_fusion_method = self.hybrid_fusion_method.lower()
        if self.hybrid_fusion_method not in {"rrf", "weighted"}:
            raise ValueError("HYBRID_FUSION_METHOD must be rrf or weighted")
        if self.hybrid_dense_weight + self.hybrid_sparse_weight <= 0:
            raise ValueError("At least one hybrid fusion weight must be positive")
        self.graph_retrieval_fallback_strategy = self.graph_retrieval_fallback_strategy.lower()
        if self.graph_retrieval_fallback_strategy not in {"dense", "hybrid"}:
            raise ValueError("GRAPH_RETRIEVAL_FALLBACK_STRATEGY must be dense or hybrid")
        self.graph_store_provider = self.graph_store_provider.strip().lower()
        if self.graph_store_provider not in {"postgres", "neo4j"}:
            raise ValueError("GRAPH_STORE_PROVIDER must be postgres or neo4j")
        self.graph_extractor_type = self.graph_extractor_type.strip().lower()
        if self.graph_extractor_type not in {"llm", "rule_based"}:
            raise ValueError("GRAPH_EXTRACTOR_TYPE must be llm or rule_based")
        self.graph_extraction_provider = (
            self.graph_extraction_provider.strip().lower()
            if self.graph_extraction_provider
            else None
        )
        if self.graph_extraction_provider is not None and self.graph_extraction_provider not in {
            "fake",
            "ollama",
            "lmstudio",
            "openai",
            "anthropic",
            "gemini",
            "bedrock",
        }:
            raise ValueError(
                "GRAPH_EXTRACTION_PROVIDER must be fake, ollama, lmstudio, openai, "
                "anthropic, gemini, bedrock, or unset"
            )
        self.graph_extraction_model_name = (
            self.graph_extraction_model_name.strip() if self.graph_extraction_model_name else None
        )
        self.neo4j_uri = self.neo4j_uri.strip() if self.neo4j_uri else None
        self.neo4j_user = self.neo4j_user.strip() if self.neo4j_user else None
        self.neo4j_password = self.neo4j_password.strip() if self.neo4j_password else None
        self.neo4j_database = self.neo4j_database.strip() or "neo4j"
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
        if self.router_mode not in {"rule_based", "llm"}:
            raise ValueError("ROUTER_MODE must be rule_based or llm")
        self.router_llm_planner_model_name = (
            self.router_llm_planner_model_name.strip()
            if self.router_llm_planner_model_name
            else None
        )
        self.router_fallback_strategy = self.router_fallback_strategy.lower()
        if self.router_fallback_strategy not in {"dense", "fallback_dense"}:
            raise ValueError("ROUTER_FALLBACK_STRATEGY must be dense or fallback_dense")
        if self.router_max_fallback_calls > self.router_max_retrieval_calls - 1:
            raise ValueError("ROUTER_MAX_FALLBACK_CALLS must be less than retrieval call budget")
        if self.router_sufficiency_min_selected > self.router_sufficiency_min_candidates:
            raise ValueError(
                "ROUTER_SUFFICIENCY_MIN_SELECTED must be <= ROUTER_SUFFICIENCY_MIN_CANDIDATES"
            )
        if self.llm_orchestrator_max_search_calls > self.llm_orchestrator_max_tool_calls:
            raise ValueError(
                "LLM_ORCHESTRATOR_MAX_SEARCH_CALLS must be <= LLM_ORCHESTRATOR_MAX_TOOL_CALLS"
            )
        if self.llm_orchestrator_allow_admin_tools:
            raise ValueError("LLM_ORCHESTRATOR_ALLOW_ADMIN_TOOLS must be false")
        if self.langchain_agentic_max_search_calls > self.langchain_agentic_max_tool_calls:
            raise ValueError(
                "LANGCHAIN_AGENTIC_MAX_SEARCH_CALLS must be <= LANGCHAIN_AGENTIC_MAX_TOOL_CALLS"
            )
        if self.langchain_agentic_allow_admin_tools:
            raise ValueError("LANGCHAIN_AGENTIC_ALLOW_ADMIN_TOOLS must be false")
        if self.langgraph_agentic_max_search_calls > self.langgraph_agentic_max_tool_calls:
            raise ValueError(
                "LANGGRAPH_AGENTIC_MAX_SEARCH_CALLS must be <= LANGGRAPH_AGENTIC_MAX_TOOL_CALLS"
            )
        if self.langgraph_agentic_allow_admin_tools:
            raise ValueError("LANGGRAPH_AGENTIC_ALLOW_ADMIN_TOOLS must be false")
        if (
            self.tool_result_compression_max_items_per_tool
            > self.tool_result_compression_max_total_items_per_turn
        ):
            raise ValueError(
                "TOOL_RESULT_COMPRESSION_MAX_ITEMS_PER_TOOL must be <= "
                "TOOL_RESULT_COMPRESSION_MAX_TOTAL_ITEMS_PER_TURN"
            )
        if (
            self.tool_result_compression_max_tokens_per_tool
            > self.tool_result_compression_max_total_tool_result_tokens
        ):
            raise ValueError(
                "TOOL_RESULT_COMPRESSION_MAX_TOKENS_PER_TOOL must be <= "
                "TOOL_RESULT_COMPRESSION_MAX_TOTAL_TOOL_RESULT_TOKENS"
            )
        self.context_budget_token_estimator = self.context_budget_token_estimator.lower()
        if self.context_budget_token_estimator != "heuristic":
            raise ValueError("CONTEXT_BUDGET_TOKEN_ESTIMATOR must be heuristic")
        if self.context_budget_reserve_answer_tokens >= self.context_budget_max_context_tokens:
            raise ValueError(
                "CONTEXT_BUDGET_RESERVE_ANSWER_TOKENS must be < CONTEXT_BUDGET_MAX_CONTEXT_TOKENS"
            )
        if self.context_budget_max_tokens_per_item > self.context_budget_max_context_tokens:
            raise ValueError(
                "CONTEXT_BUDGET_MAX_TOKENS_PER_ITEM must be <= CONTEXT_BUDGET_MAX_CONTEXT_TOKENS"
            )
        if self.context_budget_min_citation_candidates > self.context_budget_max_context_items:
            raise ValueError(
                "CONTEXT_BUDGET_MIN_CITATION_CANDIDATES must be <= CONTEXT_BUDGET_MAX_CONTEXT_ITEMS"
            )
        if self.evidence_pack_max_items_per_source > self.evidence_pack_max_items:
            raise ValueError(
                "EVIDENCE_PACK_MAX_ITEMS_PER_SOURCE must be <= EVIDENCE_PACK_MAX_ITEMS"
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
            "bedrock",
        }:
            raise ValueError(
                "GENERATION_PROVIDER must be fake, ollama, lmstudio, openai, anthropic, "
                "gemini, or bedrock"
            )
        if self.generation_provider == "bedrock":
            self.generation_model_name = self.bedrock_generation_model_id
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
        self.mcp_transport = self.mcp_transport.strip().lower()
        self.mcp_http_api_key = self.mcp_http_api_key.strip() if self.mcp_http_api_key else None
        if self.mcp_transport not in {"stdio", "http"}:
            raise ValueError("MCP_TRANSPORT must be stdio or http")
        if self.mcp_transport == "http" and not self.mcp_http_api_key:
            raise ValueError("MCP_HTTP_API_KEY is required when MCP_TRANSPORT=http")
        if not self.mcp_local_only:
            raise ValueError("MCP_LOCAL_ONLY must be true in Phase1")
        if self.mcp_actor_mode != "mcp_local":
            raise ValueError("MCP_ACTOR_MODE must be mcp_local in Phase1")
        if self.mcp_allow_write_tools:
            raise ValueError("MCP_ALLOW_WRITE_TOOLS must be false in Phase1")
        self.mcp_allowed_strategies = [item.lower() for item in self.mcp_allowed_strategies]
        allowed_mcp_strategies = {
            "dense",
            "sparse",
            "hybrid",
            "agentic_router",
            "llm_tool_orchestrator",
            "langchain_agentic",
            "langgraph_agentic",
        }
        if not self.mcp_allowed_strategies or any(
            item not in allowed_mcp_strategies for item in self.mcp_allowed_strategies
        ):
            raise ValueError(
                "MCP_ALLOWED_STRATEGIES must only include dense, sparse, hybrid, "
                "agentic_router, llm_tool_orchestrator, langchain_agentic, "
                "langgraph_agentic"
            )
        if self.mcp_allow_evaluation_run_create:
            raise ValueError("MCP_ALLOW_EVALUATION_RUN_CREATE must be false in PR-38")
        distance = self.qdrant_distance.strip().lower()
        if distance not in {"cosine", "dot", "euclid"}:
            raise ValueError("QDRANT_DISTANCE must be cosine, dot, or euclid")
        self.qdrant_distance = {"cosine": "Cosine", "dot": "Dot", "euclid": "Euclid"}[distance]

        if self.app_env.lower() not in {"local", "ci", "test"}:
            if self.session_secret in _WEAK_SESSION_SECRETS or len(self.session_secret) < 32:
                raise ValueError("SESSION_SECRET must be set to a strong random value")
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE=true is required outside local/ci/test")
        elif self.session_secret in _WEAK_SESSION_SECRETS:
            logger.warning(
                "SESSION_SECRET is using a weak default value in app_env=%s; "
                "set a strong random SESSION_SECRET before deploying.",
                self.app_env,
            )
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
