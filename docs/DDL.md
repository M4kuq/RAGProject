-- AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム
-- DDL草案 v1.8
-- Target: PostgreSQL 15+
-- Migration policy: Alembic 管理を前提とするため CREATE TABLE IF NOT EXISTS は使用しない。

-- ============================================================
-- v1.8 変更反映サマリ（v1.7 からの追随）
-- ============================================================
-- * retrieval_runs: status/error_code/started_at/finished_at の整合制約を明確化
-- * retrieval_runs.retrieval_score_summary を JSONB 前提で固定
-- * retrieval_run_items: retrieval_score 正式化、rerank_order/payload_snapshot を保持
-- * citations: (retrieval_run_id, document_chunk_id) -> retrieval_run_items 複合FKを採用
-- * jobs: running/terminal 時刻整合、retry lineage、message_edit active 制約を明確化
-- * chat_messages.linked_retrieval_run_id は循環参照回避のため ALTER TABLE 後付け
-- * document_versions: content_hash format / failed時error_code必須 / active-ready制約を明確化

BEGIN;

-- ============================================================
-- 0. Extensions
-- ============================================================
-- gen_random_uuid() を利用する場合に備える。
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- 1. Master / Users / Auth
-- ============================================================

CREATE TABLE roles (
    role_id BIGSERIAL PRIMARY KEY,
    role_name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE roles IS 'RBAC role master. Phase1 seed: admin, viewer. role_name は固定CHECKにしない。';

CREATE TABLE users (
    user_id BIGSERIAL PRIMARY KEY,
    role_id BIGINT NOT NULL REFERENCES roles(role_id) ON DELETE RESTRICT,
    email VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    password_hash TEXT,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    CONSTRAINT ck_users_status
        CHECK (status IN ('active', 'disabled')),
    CONSTRAINT ck_users_email_normalized
        CHECK (email = lower(email) AND email = btrim(email) AND email <> ''),
    CONSTRAINT ck_users_display_name_not_empty
        CHECK (btrim(display_name) <> '')
);

COMMENT ON TABLE users IS 'Application users. email は保存前に trim + lower する。DDLでも lower/trim 済みを保証する。';
COMMENT ON COLUMN users.password_hash IS 'Phase1 で local login を使う場合に利用。OIDC 導入時は nullable のままでもよい。';

CREATE TABLE user_settings (
    user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    ui_theme VARCHAR(30) NOT NULL DEFAULT 'system',
    memory_message_limit INTEGER NOT NULL DEFAULT 8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_user_settings_ui_theme
        CHECK (ui_theme IN ('light', 'dark', 'system')),
    CONSTRAINT ck_user_settings_memory_message_limit
        CHECK (memory_message_limit BETWEEN 1 AND 50)
);

COMMENT ON TABLE user_settings IS 'users と必須1:1。user 作成 transaction 内で同時 INSERT する eager create 前提。';

CREATE TABLE user_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_token_hash TEXT NOT NULL UNIQUE,
    csrf_state_hash TEXT,
    user_agent TEXT,
    ip_address INET,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT ck_user_sessions_expiry
        CHECK (expires_at > created_at)
);

COMMENT ON TABLE user_sessions IS 'Server-side session table. Cookie には raw session token を保持し、DB には hash のみ保存する。';
COMMENT ON COLUMN user_sessions.csrf_state_hash IS 'session-bound CSRF state。pre-auth CSRF state は login 成功時に失効する。';

-- ============================================================
-- 2. Chat
-- ============================================================

CREATE TABLE chat_sessions (
    chat_session_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    temporary_flag BOOLEAN NOT NULL DEFAULT FALSE,
    ttl_expires_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_chat_sessions_status
        CHECK (status IN ('active', 'archived')),
    CONSTRAINT ck_chat_sessions_title_not_empty
        CHECK (btrim(title) <> ''),
    CONSTRAINT ck_chat_sessions_temporary_ttl
        CHECK (
            (temporary_flag = TRUE AND ttl_expires_at IS NOT NULL)
            OR
            (temporary_flag = FALSE AND ttl_expires_at IS NULL)
        ),
    CONSTRAINT ck_chat_sessions_archived_at
        CHECK (
            (status = 'archived' AND archived_at IS NOT NULL)
            OR
            (status = 'active' AND archived_at IS NULL)
        )
);

COMMENT ON TABLE chat_sessions IS '通常 chat は active/archived。temporary chat は status ではなく temporary_flag + TTL 物理削除で扱う。';
COMMENT ON COLUMN chat_sessions.title IS 'API で未指定の場合、保存前にサーバーが仮タイトルを補完する。';

CREATE TABLE chat_tags (
    chat_tag_id BIGSERIAL PRIMARY KEY,
    chat_session_id BIGINT NOT NULL REFERENCES chat_sessions(chat_session_id) ON DELETE RESTRICT,
    tag_name VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_chat_tags_tag_name_not_empty
        CHECK (btrim(tag_name) <> ''),
    CONSTRAINT uq_chat_tags_session_name
        UNIQUE (chat_session_id, tag_name)
);

CREATE TABLE chat_messages (
    chat_message_id BIGSERIAL PRIMARY KEY,
    chat_session_id BIGINT NOT NULL REFERENCES chat_sessions(chat_session_id) ON DELETE RESTRICT,
    role VARCHAR(30) NOT NULL,
    content TEXT NOT NULL,
    client_message_id VARCHAR(255),
    linked_retrieval_run_id BIGINT,
    edited_flag BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_chat_messages_session_message
        UNIQUE (chat_session_id, chat_message_id),
    CONSTRAINT ck_chat_messages_role
        CHECK (role IN ('user', 'assistant', 'system')),
    CONSTRAINT ck_chat_messages_content_not_empty
        CHECK (btrim(content) <> ''),
    CONSTRAINT ck_chat_messages_client_message_user_only
        CHECK (client_message_id IS NULL OR role = 'user'),
    CONSTRAINT ck_chat_messages_client_message_not_empty
        CHECK (client_message_id IS NULL OR client_message_id <> ''),
    CONSTRAINT ck_chat_messages_linked_retrieval_assistant_only
        CHECK (linked_retrieval_run_id IS NULL OR role = 'assistant')
);

COMMENT ON TABLE chat_messages IS 'client_message_id は user message の idempotency key。linked_retrieval_run_id は assistant message の採用 retrieval trace。';
COMMENT ON COLUMN chat_messages.client_message_id IS 'API/Pydantic で最大255、英数字・-・_、空文字不可を検証する。DDLでは空文字不可と user role 限定を保証する。';

CREATE UNIQUE INDEX ux_chat_messages_client_message_id
    ON chat_messages(chat_session_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE TABLE summary_memories (
    summary_memory_id BIGSERIAL PRIMARY KEY,
    chat_session_id BIGINT NOT NULL REFERENCES chat_sessions(chat_session_id) ON DELETE RESTRICT,
    source_message_upto_id BIGINT NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_summary_memories_same_session_message
        FOREIGN KEY (chat_session_id, source_message_upto_id)
        REFERENCES chat_messages(chat_session_id, chat_message_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_summary_memories_summary_text_not_empty
        CHECK (btrim(summary_text) <> '')
);

COMMENT ON TABLE summary_memories IS '要約メモリ。source_message_upto_id は同一 session の message であることを複合FKで保証する。';

-- ============================================================
-- 3. Documents
-- ============================================================

CREATE TABLE logical_documents (
    logical_document_id BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    title VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_logical_documents_status
        CHECK (status IN ('active', 'archived')),
    CONSTRAINT ck_logical_documents_title_not_empty
        CHECK (btrim(title) <> ''),
    CONSTRAINT ck_logical_documents_archived_at
        CHECK (
            (status = 'archived' AND archived_at IS NOT NULL)
            OR
            (status = 'active' AND archived_at IS NULL)
        )
);

COMMENT ON TABLE logical_documents IS '論理文書。通常削除は archive。archive 時は active version を無効化し、Qdrant mirror 更新 job を作成する。';

CREATE TABLE document_versions (
    document_version_id BIGSERIAL PRIMARY KEY,
    logical_document_id BIGINT NOT NULL REFERENCES logical_documents(logical_document_id) ON DELETE RESTRICT,
    version_no INTEGER NOT NULL,
    content_hash CHAR(64) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'processing',
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    error_code VARCHAR(100),
    file_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    storage_key TEXT,
    page_count INTEGER,
    extractor_name VARCHAR(100),
    extractor_version VARCHAR(100),
    created_by BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_document_versions_version_no
        UNIQUE (logical_document_id, version_no),
    CONSTRAINT uq_document_versions_content_hash
        UNIQUE (logical_document_id, content_hash),
    CONSTRAINT ck_document_versions_version_no
        CHECK (version_no >= 1),
    CONSTRAINT ck_document_versions_status
        CHECK (status IN ('processing', 'ready', 'failed', 'archived')),
    CONSTRAINT ck_document_versions_active_ready_only
        CHECK (is_active = FALSE OR status = 'ready'),
    CONSTRAINT ck_document_versions_content_hash_format
        CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_document_versions_file_name_not_empty
        CHECK (btrim(file_name) <> ''),
    CONSTRAINT ck_document_versions_mime_type_not_empty
        CHECK (btrim(mime_type) <> ''),
    CONSTRAINT ck_document_versions_file_size
        CHECK (file_size_bytes >= 0),
    CONSTRAINT ck_document_versions_page_count
        CHECK (page_count IS NULL OR page_count >= 0),
    CONSTRAINT ck_document_versions_error_code_by_status
        CHECK (
            (status = 'failed' AND error_code IS NOT NULL)
            OR
            (status <> 'failed' AND error_code IS NULL)
        )
);

COMMENT ON TABLE document_versions IS '文書版。Phase1 では pending_review status は使わず、status=ready AND is_active=false を承認待ちとして扱う。';
COMMENT ON COLUMN document_versions.status IS 'Phase1 values: processing, ready, failed. archived は将来拡張・内部整理用で、通常 archive は logical_documents 単位。';
COMMENT ON COLUMN document_versions.is_active IS 'RDB 上の検索対象 version の正。true にできるのは status=ready のみ。';
COMMENT ON COLUMN document_versions.content_hash IS 'upload file byte sequence に対する SHA-256。logical_document_id 単位で重複 version を作らない。';
COMMENT ON COLUMN document_versions.error_code IS 'status=failed の場合のみ設定する。failed ingest retry で processing に戻す際は必ず NULL に戻す。';

CREATE UNIQUE INDEX ux_document_versions_one_active
    ON document_versions(logical_document_id)
    WHERE is_active = TRUE;

CREATE TABLE document_chunks (
    document_chunk_id BIGSERIAL PRIMARY KEY,
    document_version_id BIGINT NOT NULL REFERENCES document_versions(document_version_id) ON DELETE RESTRICT,
    chunk_index INTEGER NOT NULL,
    chunk_hash CHAR(64) NOT NULL,
    content_text TEXT NOT NULL,
    token_count INTEGER,
    char_count INTEGER,
    page_from INTEGER,
    page_to INTEGER,
    section_title TEXT,
    modality VARCHAR(30) NOT NULL DEFAULT 'text',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_document_chunks_version_index
        UNIQUE (document_version_id, chunk_index),
    CONSTRAINT ck_document_chunks_chunk_index
        CHECK (chunk_index >= 0),
    CONSTRAINT ck_document_chunks_chunk_hash_format
        CHECK (chunk_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_document_chunks_content_not_empty
        CHECK (btrim(content_text) <> ''),
    CONSTRAINT ck_document_chunks_token_count
        CHECK (token_count IS NULL OR token_count >= 0),
    CONSTRAINT ck_document_chunks_char_count
        CHECK (char_count IS NULL OR char_count >= 0),
    CONSTRAINT ck_document_chunks_page_range
        CHECK (page_from IS NULL OR page_to IS NULL OR page_from <= page_to),
    CONSTRAINT ck_document_chunks_page_positive
        CHECK ((page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1)),
    CONSTRAINT ck_document_chunks_modality
        CHECK (modality IN ('text'))
);

COMMENT ON TABLE document_chunks IS 'document_version 配下の chunk。Phase1 では chunk 単位 is_active は持たず、version active と logical document status を正とする。';
COMMENT ON COLUMN document_chunks.content_text IS 'RAG retrieval/generation/sparse retrieval 用の chunk text。application log / audit log / trace JSON / score_breakdown JSON には出さない。';

-- ============================================================
-- 4. Jobs
-- ============================================================

CREATE TABLE jobs (
    job_id BIGSERIAL PRIMARY KEY,
    job_type VARCHAR(80) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 100,
    target_type VARCHAR(80),
    target_id BIGINT,
    payload_json JSONB,
    result_json JSONB,
    error_code VARCHAR(100),
    error_message TEXT,
    locked_by VARCHAR(100),
    locked_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    retry_of_job_id BIGINT REFERENCES jobs(job_id) ON DELETE RESTRICT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_jobs_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    CONSTRAINT ck_jobs_priority
        CHECK (priority >= 0),
    CONSTRAINT ck_jobs_retry_count
        CHECK (retry_count >= 0),
    CONSTRAINT ck_jobs_no_self_retry
        CHECK (retry_of_job_id IS NULL OR retry_of_job_id <> job_id),
    CONSTRAINT ck_jobs_queued_times
        CHECK (status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)),
    CONSTRAINT ck_jobs_running_required_fields
        CHECK (
            status <> 'running'
            OR (
                locked_by IS NOT NULL
                AND locked_at IS NOT NULL
                AND lease_expires_at IS NOT NULL
                AND started_at IS NOT NULL
                AND finished_at IS NULL
            )
        ),
    CONSTRAINT ck_jobs_terminal_finished
        CHECK (status NOT IN ('succeeded', 'failed', 'canceled') OR finished_at IS NOT NULL),
    CONSTRAINT ck_jobs_success_failed_started
        CHECK (status NOT IN ('succeeded', 'failed') OR started_at IS NOT NULL),
    CONSTRAINT ck_jobs_failed_error_code
        CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CONSTRAINT ck_jobs_message_edit_target_required
        CHECK (
            job_type <> 'message_edit_regeneration'
            OR (target_type = 'chat_message' AND target_id IS NOT NULL)
        ),
    CONSTRAINT ck_jobs_lease_order
        CHECK (
            lease_expires_at IS NULL
            OR locked_at IS NULL
            OR lease_expires_at > locked_at
        ),
    CONSTRAINT ck_jobs_finished_after_started
        CHECK (
            finished_at IS NULL
            OR started_at IS NULL
            OR finished_at >= started_at
        )
);

COMMENT ON TABLE jobs IS '非同期 job。retry は元 job を戻さず新 job を作成し、retry_of_job_id で lineage を追う。reclaim は新 job を作らない。';
COMMENT ON COLUMN jobs.target_type IS 'soft reference。temporary chat cleanup 後の deleted target 参照を許容する。';
COMMENT ON COLUMN jobs.payload_json IS 'UI には raw 表示しない。必要時は redacted summary のみ表示する。';
COMMENT ON COLUMN jobs.retry_of_job_id IS 'retry lineage の original source job_id を保持する。retry の retry でも直前 job ではなく original source job_id を入れる。active retry は source job 単位で1本のみ許可する。';

CREATE UNIQUE INDEX ux_jobs_active_retry_per_source
    ON jobs(retry_of_job_id)
    WHERE retry_of_job_id IS NOT NULL AND status IN ('queued', 'running');

CREATE UNIQUE INDEX ux_jobs_active_message_edit
    ON jobs(target_type, target_id)
    WHERE job_type = 'message_edit_regeneration'
      AND target_type = 'chat_message'
      AND status IN ('queued', 'running');

-- ============================================================
-- 5. Retrieval / Citations
-- ============================================================

CREATE TABLE retrieval_runs (
    retrieval_run_id BIGSERIAL PRIMARY KEY,
    chat_session_id BIGINT REFERENCES chat_sessions(chat_session_id) ON DELETE RESTRICT,
    request_message_id BIGINT,
    status VARCHAR(30) NOT NULL DEFAULT 'running',
    error_code VARCHAR(100),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    top_k INTEGER,
    strategy_type VARCHAR(50) NOT NULL DEFAULT 'dense',
    query_hash CHAR(64),
    retrieval_score_summary JSONB,
    query_plan_json JSONB,
    strategy_decision_json JSONB,
    latency_breakdown_json JSONB,
    retrieval_settings_json JSONB,
    rerank_score_top1 NUMERIC(10,6),
    answer_confidence NUMERIC(10,6),
    groundedness_score NUMERIC(10,6),
    confidence_label VARCHAR(30),
    request_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_retrieval_runs_session_run
        UNIQUE (chat_session_id, retrieval_run_id),
    CONSTRAINT fk_retrieval_runs_request_message_same_session
        FOREIGN KEY (chat_session_id, request_message_id)
        REFERENCES chat_messages(chat_session_id, chat_message_id)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT ck_retrieval_runs_origin
        CHECK (
            (chat_session_id IS NULL AND request_message_id IS NULL)
            OR
            (chat_session_id IS NOT NULL AND request_message_id IS NOT NULL)
        ),
    CONSTRAINT ck_retrieval_runs_status
        CHECK (status IN ('running', 'succeeded', 'failed')),
    CONSTRAINT ck_retrieval_runs_running_times
        CHECK (
            status <> 'running'
            OR (
                started_at IS NOT NULL
                AND finished_at IS NULL
                AND error_code IS NULL
            )
        ),
    CONSTRAINT ck_retrieval_runs_terminal_times
        CHECK (
            status NOT IN ('succeeded', 'failed')
            OR (started_at IS NOT NULL AND finished_at IS NOT NULL)
        ),
    CONSTRAINT ck_retrieval_runs_failed_error
        CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CONSTRAINT ck_retrieval_runs_succeeded_error_null
        CHECK (status <> 'succeeded' OR error_code IS NULL),
    CONSTRAINT ck_retrieval_runs_finished_after_started
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT ck_retrieval_runs_failed_confidence_null
        CHECK (
            status <> 'failed'
            OR (
                answer_confidence IS NULL
                AND groundedness_score IS NULL
                AND confidence_label IS NULL
            )
        ),
    CONSTRAINT ck_retrieval_runs_confidence_range
        CHECK (answer_confidence IS NULL OR (answer_confidence >= 0 AND answer_confidence <= 1)),
    CONSTRAINT ck_retrieval_runs_groundedness_range
        CHECK (groundedness_score IS NULL OR (groundedness_score >= 0 AND groundedness_score <= 1)),
    CONSTRAINT ck_retrieval_runs_confidence_label
        CHECK (confidence_label IS NULL OR confidence_label IN ('High', 'Medium', 'Low')),
    CONSTRAINT ck_retrieval_runs_top_k
        CHECK (top_k IS NULL OR (top_k BETWEEN 1 AND 20)),
    CONSTRAINT ck_retrieval_runs_strategy_type
        CHECK (
            strategy_type IN (
                'dense',
                'sparse',
                'hybrid',
                'multi_query_dense',
                'multi_query_hybrid',
                'metadata_filtered',
                'version_aware',
                'agentic_router',
                'fallback_dense'
            )
        ),
    CONSTRAINT ck_retrieval_runs_query_hash_format
        CHECK (query_hash IS NULL OR query_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_retrieval_runs_request_id_not_empty
        CHECK (request_id IS NULL OR btrim(request_id) <> '')
);

COMMENT ON TABLE retrieval_runs IS 'chat 起源 run と /rag/search 起源 standalone run の両方を表す。standalone は chat_session_id/request_message_id が両方 NULL。';
COMMENT ON COLUMN retrieval_runs.retrieval_score_summary IS 'RAG v1.4 に合わせ JSONB。top1/top3/count/excluded/selected など複合 summary を保存する。';
COMMENT ON COLUMN retrieval_runs.strategy_type IS 'Phase2 retrieval strategy。既存Phase1 runは dense として扱う。';
COMMENT ON COLUMN retrieval_runs.query_plan_json IS 'Phase2 trace用のredacted query plan。raw prompt/full context/PII/secretは保存しない。';
COMMENT ON COLUMN retrieval_runs.strategy_decision_json IS 'Phase2 trace用のredacted strategy decision。router判断理由のみを保存し、raw prompt/full contextは保存しない。';
COMMENT ON COLUMN retrieval_runs.latency_breakdown_json IS 'Phase2 trace用のlatency breakdown。raw request/responseは保存しない。';
COMMENT ON COLUMN retrieval_runs.retrieval_settings_json IS 'run時点のretrieval設定snapshot。secret、raw prompt、raw chunk textは保存しない。';
COMMENT ON COLUMN retrieval_runs.answer_confidence IS '成功 run のみ保存。failed run では NULL。正解確率ではなく補助指標。';
COMMENT ON COLUMN retrieval_runs.started_at IS 'retrieval_run 作成時刻。status は running/succeeded/failed のみであり、DDL上も NOT NULL DEFAULT now() とする。';
COMMENT ON COLUMN retrieval_runs.error_code IS 'running/succeeded では NULL、failed では NOT NULL。';

CREATE TABLE retrieval_run_items (
    retrieval_run_item_id BIGSERIAL PRIMARY KEY,
    retrieval_run_id BIGINT NOT NULL REFERENCES retrieval_runs(retrieval_run_id) ON DELETE RESTRICT,
    document_chunk_id BIGINT NOT NULL REFERENCES document_chunks(document_chunk_id) ON DELETE RESTRICT,
    retrieval_score NUMERIC(10,6) NOT NULL,
    rerank_score NUMERIC(10,6),
    rank_order INTEGER NOT NULL,
    rerank_order INTEGER,
    selected_flag BOOLEAN NOT NULL DEFAULT FALSE,
    payload_snapshot JSONB,
    retrieval_source VARCHAR(50),
    score_breakdown_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_retrieval_run_items_run_chunk
        UNIQUE (retrieval_run_id, document_chunk_id),
    CONSTRAINT ck_retrieval_run_items_rank_order
        CHECK (rank_order >= 1),
    CONSTRAINT ck_retrieval_run_items_rerank_order
        CHECK (rerank_order IS NULL OR rerank_order >= 1),
    CONSTRAINT ck_retrieval_run_items_source
        CHECK (
            retrieval_source IS NULL
            OR retrieval_source IN (
                'dense',
                'sparse',
                'hybrid',
                'rerank',
                'fallback_dense',
                'metadata_filter'
            )
        )
);

COMMENT ON TABLE retrieval_run_items IS 'RDB final check を通過した post-final-check candidates。Qdrant raw candidates は保存しない。';
COMMENT ON COLUMN retrieval_run_items.retrieval_score IS 'vector search score。initial_score という列名は使用しない。';
COMMENT ON COLUMN retrieval_run_items.payload_snapshot IS 'source_label, page_from, page_to, modality 等の表示用 snapshot。raw chunk text は保存しない。';
COMMENT ON COLUMN retrieval_run_items.retrieval_source IS 'Phase2 retrieval source。dense/sparse/hybrid/fallback等のitem provenanceを保存する。';
COMMENT ON COLUMN retrieval_run_items.score_breakdown_json IS 'Phase2 score breakdown。dense/sparse/fused/rerank等のscoreのみを保存し、raw chunk text/prompt/PII/secretは保存しない。';

CREATE TABLE citations (
    citation_id BIGSERIAL PRIMARY KEY,
    retrieval_run_id BIGINT NOT NULL,
    document_chunk_id BIGINT NOT NULL,
    snippet TEXT NOT NULL,
    page_from INTEGER,
    page_to INTEGER,
    source_type VARCHAR(50) NOT NULL DEFAULT 'upload',
    source_url TEXT,
    display_label TEXT NOT NULL,
    rank_order INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_citations_retrieval_item
        FOREIGN KEY (retrieval_run_id, document_chunk_id)
        REFERENCES retrieval_run_items(retrieval_run_id, document_chunk_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_citations_snippet_not_empty
        CHECK (btrim(snippet) <> ''),
    CONSTRAINT ck_citations_display_label_not_empty
        CHECK (btrim(display_label) <> ''),
    CONSTRAINT ck_citations_rank_order
        CHECK (rank_order >= 1),
    CONSTRAINT ck_citations_page_range
        CHECK (page_from IS NULL OR page_to IS NULL OR page_from <= page_to),
    CONSTRAINT ck_citations_page_positive
        CHECK ((page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1)),
    CONSTRAINT ck_citations_source_type
        CHECK (source_type IN ('upload', 'external_url'))
);

COMMENT ON TABLE citations IS 'citations は retrieval_run_items に含まれる chunk からのみ作成可能。document_version_id は冗長保持しない。';
COMMENT ON COLUMN citations.source_url IS 'Phase1 upload 文書では NULL 許容。外部URL source は Phase2 以降。';

-- chat_messages.linked_retrieval_run_id の循環参照を後付けする。
ALTER TABLE chat_messages
    ADD CONSTRAINT fk_chat_messages_linked_retrieval_run_same_session
    FOREIGN KEY (chat_session_id, linked_retrieval_run_id)
    REFERENCES retrieval_runs(chat_session_id, retrieval_run_id)
    ON DELETE RESTRICT
    DEFERRABLE INITIALLY DEFERRED;

-- ============================================================
-- 6. Evaluation
-- ============================================================

CREATE TABLE evaluation_datasets (
    evaluation_dataset_id BIGSERIAL PRIMARY KEY,
    dataset_name VARCHAR(120) NOT NULL,
    description TEXT,
    version VARCHAR(50) NOT NULL DEFAULT 'v1',
    source_type VARCHAR(50) NOT NULL DEFAULT 'manual',
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    metadata_json JSONB,
    created_by BIGINT REFERENCES users(user_id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_evaluation_datasets_name
        UNIQUE (dataset_name),
    CONSTRAINT ck_evaluation_datasets_source_type
        CHECK (source_type IN ('manual', 'fixture', 'feedback_promoted', 'imported')),
    CONSTRAINT ck_evaluation_datasets_status
        CHECK (status IN ('active', 'archived')),
    CONSTRAINT ck_evaluation_datasets_name_not_empty
        CHECK (btrim(dataset_name) <> ''),
    CONSTRAINT ck_evaluation_datasets_version_not_empty
        CHECK (btrim(version) <> '')
);

CREATE TABLE evaluation_cases (
    evaluation_case_id BIGSERIAL PRIMARY KEY,
    evaluation_dataset_id BIGINT NOT NULL
        REFERENCES evaluation_datasets(evaluation_dataset_id) ON DELETE RESTRICT,
    case_key VARCHAR(120) NOT NULL,
    question TEXT NOT NULL,
    expected_answer TEXT,
    expected_keywords JSONB,
    expected_document_ids JSONB,
    expected_chunk_ids JSONB,
    required_citation BOOLEAN NOT NULL DEFAULT TRUE,
    tags JSONB,
    metadata_json JSONB,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_evaluation_cases_dataset_key
        UNIQUE (evaluation_dataset_id, case_key),
    CONSTRAINT ck_evaluation_cases_status
        CHECK (status IN ('active', 'archived')),
    CONSTRAINT ck_evaluation_cases_key_not_empty
        CHECK (btrim(case_key) <> ''),
    CONSTRAINT ck_evaluation_cases_question_not_empty
        CHECK (btrim(question) <> '')
);

CREATE TABLE evaluation_runs (
    evaluation_run_id BIGSERIAL PRIMARY KEY,
    created_by BIGINT NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
    evaluation_dataset_id BIGINT REFERENCES evaluation_datasets(evaluation_dataset_id) ON DELETE RESTRICT,
    status VARCHAR(30) NOT NULL DEFAULT 'queued',
    target_type VARCHAR(80),
    target_id BIGINT,
    metrics_config JSONB,
    strategy_type VARCHAR(50) NOT NULL DEFAULT 'dense',
    trigger_type VARCHAR(50) NOT NULL DEFAULT 'manual',
    retrieval_settings_json JSONB,
    strategy_metrics_summary_json JSONB,
    error_code VARCHAR(100),
    error_message TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_evaluation_runs_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    CONSTRAINT ck_evaluation_runs_queued_times
        CHECK (status <> 'queued' OR (started_at IS NULL AND finished_at IS NULL)),
    CONSTRAINT ck_evaluation_runs_running_times
        CHECK (status <> 'running' OR (started_at IS NOT NULL AND finished_at IS NULL)),
    CONSTRAINT ck_evaluation_runs_terminal_finished
        CHECK (status NOT IN ('succeeded', 'failed', 'canceled') OR finished_at IS NOT NULL),
    CONSTRAINT ck_evaluation_runs_failed_error_code
        CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CONSTRAINT ck_evaluation_runs_strategy_type
        CHECK (strategy_type IN (
            'dense',
            'sparse',
            'hybrid',
            'multi_query_dense',
            'multi_query_hybrid',
            'metadata_filtered',
            'version_aware',
            'agentic_router',
            'fallback_dense'
        )),
    CONSTRAINT ck_evaluation_runs_trigger_type
        CHECK (trigger_type IN ('manual', 'ci', 'scheduled', 'post_deploy', 'online_sampled_trace'))
);

COMMENT ON TABLE evaluation_runs IS 'Phase1 は admin manual evaluation。PR-22以降は dataset / strategy / trigger を保存する。';

CREATE TABLE evaluation_run_items (
    evaluation_run_item_id BIGSERIAL PRIMARY KEY,
    evaluation_run_id BIGINT NOT NULL REFERENCES evaluation_runs(evaluation_run_id) ON DELETE RESTRICT,
    evaluation_case_id BIGINT REFERENCES evaluation_cases(evaluation_case_id) ON DELETE RESTRICT,
    retrieval_run_id BIGINT REFERENCES retrieval_runs(retrieval_run_id) ON DELETE RESTRICT,
    strategy_type VARCHAR(50) NOT NULL DEFAULT 'dense',
    case_key VARCHAR(120),
    status VARCHAR(30) NOT NULL DEFAULT 'queued',
    faithfulness_score NUMERIC(10,6),
    groundedness_score NUMERIC(10,6),
    citation_coverage NUMERIC(10,6),
    latency_ms INTEGER,
    latency_breakdown_json JSONB,
    metric_summary_json JSONB,
    error_code VARCHAR(100),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_evaluation_run_items_status
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')),
    CONSTRAINT ck_evaluation_run_items_scores
        CHECK (
            (faithfulness_score IS NULL OR (faithfulness_score >= 0 AND faithfulness_score <= 1))
            AND (groundedness_score IS NULL OR (groundedness_score >= 0 AND groundedness_score <= 1))
            AND (citation_coverage IS NULL OR (citation_coverage >= 0 AND citation_coverage <= 1))
        ),
    CONSTRAINT ck_evaluation_run_items_latency
        CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT ck_evaluation_run_items_failed_error_code
        CHECK (status <> 'failed' OR error_code IS NOT NULL),
    CONSTRAINT ck_evaluation_run_items_strategy_type
        CHECK (strategy_type IN (
            'dense',
            'sparse',
            'hybrid',
            'multi_query_dense',
            'multi_query_hybrid',
            'metadata_filtered',
            'version_aware',
            'agentic_router',
            'fallback_dense'
        ))
);

CREATE TABLE evaluation_results (
    evaluation_result_id BIGSERIAL PRIMARY KEY,
    evaluation_run_item_id BIGINT NOT NULL
        REFERENCES evaluation_run_items(evaluation_run_item_id) ON DELETE RESTRICT,
    metric_name VARCHAR(100) NOT NULL,
    metric_score NUMERIC(10,6),
    metric_value NUMERIC(12,6),
    metric_label VARCHAR(100),
    details_json JSONB,
    metric_detail_json JSONB,
    strategy_type VARCHAR(50) NOT NULL DEFAULT 'dense',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_evaluation_results_item_metric
        UNIQUE (evaluation_run_item_id, metric_name),
    CONSTRAINT ck_evaluation_results_metric_name
        CHECK (btrim(metric_name) <> ''),
    CONSTRAINT ck_evaluation_results_score
        CHECK (metric_score IS NULL OR (metric_score >= 0 AND metric_score <= 1)),
    CONSTRAINT ck_evaluation_results_strategy_type
        CHECK (strategy_type IN (
            'dense',
            'sparse',
            'hybrid',
            'multi_query_dense',
            'multi_query_hybrid',
            'metadata_filtered',
            'version_aware',
            'agentic_router',
            'fallback_dense'
        ))
);

-- ============================================================
-- 7. Audit / Settings
-- ============================================================

CREATE TABLE audit_logs (
    audit_log_id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(100) NOT NULL,
    actor_user_id BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    action_type VARCHAR(100) NOT NULL,
    target_type VARCHAR(100) NOT NULL,
    target_id BIGINT,
    metadata_json JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_logs_action_type_not_empty
        CHECK (btrim(action_type) <> ''),
    CONSTRAINT ck_audit_logs_target_type_not_empty
        CHECK (btrim(target_type) <> ''),
    CONSTRAINT ck_audit_logs_request_id_not_empty
        CHECK (btrim(request_id) <> '')
);

COMMENT ON TABLE audit_logs IS '監査ログ。target_id は login failure 等の対象リソース不在イベントで NULL 許容。raw prompt/raw document/raw token は保存しない。';

CREATE TABLE system_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    setting_value JSONB NOT NULL,
    description TEXT,
    updated_by BIGINT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_system_settings_key_not_empty
        CHECK (btrim(setting_key) <> '')
);

COMMENT ON TABLE system_settings IS 'システム設定。未知 key は API で 404、値不正は 422。';

-- ============================================================
-- 8. Indexes
-- ============================================================

CREATE INDEX ix_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX ix_user_sessions_expires_at ON user_sessions(expires_at);

CREATE INDEX ix_chat_sessions_user_status_created
    ON chat_sessions(user_id, status, created_at DESC);
CREATE INDEX ix_chat_sessions_ttl
    ON chat_sessions(ttl_expires_at)
    WHERE temporary_flag = TRUE;

CREATE INDEX ix_chat_messages_session_created
    ON chat_messages(chat_session_id, created_at ASC, chat_message_id ASC);

CREATE INDEX ix_summary_memories_session_created
    ON summary_memories(chat_session_id, created_at DESC);

CREATE INDEX ix_logical_documents_owner_status_created
    ON logical_documents(owner_user_id, status, created_at DESC);

CREATE INDEX ix_document_versions_logical_status
    ON document_versions(logical_document_id, status, created_at DESC);
CREATE INDEX ix_document_versions_active
    ON document_versions(logical_document_id, is_active)
    WHERE is_active = TRUE;

CREATE INDEX ix_document_chunks_version_index
    ON document_chunks(document_version_id, chunk_index ASC);
CREATE INDEX ix_document_chunks_content_fts
    ON document_chunks
    USING GIN (to_tsvector('simple', content_text));
CREATE INDEX ix_document_chunks_content_fts_english
    ON document_chunks
    USING GIN (to_tsvector('english', content_text));

CREATE INDEX ix_jobs_status_priority_created
    ON jobs(status, priority ASC, created_at ASC);
CREATE INDEX ix_jobs_lease_expires
    ON jobs(lease_expires_at)
    WHERE status = 'running';
CREATE INDEX ix_jobs_target
    ON jobs(target_type, target_id);

CREATE INDEX ix_retrieval_runs_chat_created
    ON retrieval_runs(chat_session_id, created_at DESC)
    WHERE chat_session_id IS NOT NULL;
CREATE INDEX ix_retrieval_runs_status_created
    ON retrieval_runs(status, created_at DESC);
CREATE INDEX ix_retrieval_runs_request_message
    ON retrieval_runs(request_message_id)
    WHERE request_message_id IS NOT NULL;

CREATE UNIQUE INDEX ux_retrieval_run_items_run_rank
    ON retrieval_run_items(retrieval_run_id, rank_order);
CREATE UNIQUE INDEX ux_retrieval_run_items_run_rerank_order
    ON retrieval_run_items(retrieval_run_id, rerank_order)
    WHERE rerank_order IS NOT NULL;
CREATE INDEX ix_retrieval_run_items_chunk
    ON retrieval_run_items(document_chunk_id);

CREATE UNIQUE INDEX ux_citations_run_rank
    ON citations(retrieval_run_id, rank_order);
CREATE INDEX ix_citations_chunk
    ON citations(document_chunk_id);

CREATE INDEX ix_evaluation_runs_status_created
    ON evaluation_runs(status, created_at DESC);
CREATE INDEX ix_evaluation_runs_dataset_strategy
    ON evaluation_runs(evaluation_dataset_id, strategy_type, created_at DESC);
CREATE INDEX ix_evaluation_run_items_run_status
    ON evaluation_run_items(evaluation_run_id, status);
CREATE INDEX ix_evaluation_datasets_status_created
    ON evaluation_datasets(status, created_at DESC);
CREATE INDEX ix_evaluation_cases_dataset_status
    ON evaluation_cases(evaluation_dataset_id, status);
CREATE INDEX ix_evaluation_run_items_case
    ON evaluation_run_items(evaluation_case_id);
CREATE INDEX ix_evaluation_results_item
    ON evaluation_results(evaluation_run_item_id);
CREATE INDEX ix_evaluation_results_metric_score
    ON evaluation_results(metric_name, metric_score);

CREATE INDEX ix_audit_logs_created
    ON audit_logs(created_at DESC);
CREATE INDEX ix_audit_logs_action_created
    ON audit_logs(action_type, created_at DESC);
CREATE INDEX ix_audit_logs_target
    ON audit_logs(target_type, target_id, created_at DESC);

-- ============================================================
-- 9. Seed guidance
-- ============================================================
-- 実 migration では Alembic seed または app bootstrap で投入する。
-- INSERT INTO roles(role_name, description) VALUES
--   ('admin', 'Administrator'),
--   ('viewer', 'Standard viewer');

COMMIT;
