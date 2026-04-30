# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## ER図 / テーブル設計書 v1.4

---

## 1. 文書概要

### 1.1 目的

本書は、要件定義書 v1.1 および基本設計書 v1.2 をもとに、RAGシステムの ER 図観点と PostgreSQL / Qdrant のデータ設計を定義する。

本書の目的は以下である。

- 主要主語と責務を固定する
- テーブル責務、FK方向、UNIQUE 制約、CHECK 制約、索引方針を明確化する
- Phase1 で実装可能でありつつ、Phase2 / Phase3 で DB を作り直さず拡張できる構造を作る
- API設計、DDL作成、実装へスムーズに接続する

### 1.2 設計原則

- 1テーブル1責務を原則とする
- ライフサイクルを持つ主語を固定する
- Phase1 は **PostgreSQL + jobs テーブル + worker polling** を前提とする
- Phase2 / Phase3 は **列追加・テーブル追加・インデックス追加** により拡張し、DB 全面作り直しは前提としない
- 通常削除はアーカイブ、物理削除は管理者限定とする
- 監査、評価、citation、信頼度のトレーサビリティを最優先する

---

## 2. ER設計の主語

本システムでライフサイクルを持つ主要主語は以下とする。

- users
- roles
- auth_sessions
- chat_sessions
- chat_messages
- chat_tags
- summary_memories
- logical_documents
- document_versions
- document_chunks
- retrieval_runs
- retrieval_run_items
- citations
- evaluation_datasets
- evaluation_cases
- evaluation_runs
- evaluation_run_items
- evaluation_results
- jobs
- audit_logs
- user_settings
- system_settings
- pii_mapping_store_reference

### 2.1 主語ごとの責務

- `users`: 認証主体
- `roles`: 権限定義
- `auth_sessions`: ログインセッション
- `chat_sessions`: 会話の親
- `chat_messages`: 会話の子
- `chat_tags`: 会話タグ
- `summary_memories`: 会話要約メモリ
- `logical_documents`: 文書の論理単位
- `document_versions`: 版管理
- `document_chunks`: 検索最小単位
- `retrieval_runs`: 検索 / 回答生成の実行ヘッダ
- `retrieval_run_items`: 候補群 / 採用群
- `citations`: UI 表示用回答根拠
- `evaluation_datasets`: 評価データセット
- `evaluation_cases`: 評価ケース
- `evaluation_runs`: 評価実行ヘッダ
- `evaluation_run_items`: ケース別評価結果
- `evaluation_results`: metric別明細
- `jobs`: 非同期処理単位
- `audit_logs`: 監査記録
- `user_settings`: ユーザー個別設定
- `system_settings`: 運用設定
- `pii_mapping_store_reference`: 再識別用マッピング参照

---

## 3. ER図の関係定義

### 3.1 認証系

- `roles (1) - (N) users`
- `users (1) - (N) auth_sessions`

### 3.2 会話系

- `users (1) - (N) chat_sessions`
- `chat_sessions (1) - (N) chat_messages`
- `chat_sessions (1) - (N) chat_tags`
- `chat_sessions (1) - (N) summary_memories`
- `chat_messages.parent_message_id` は self reference
- `chat_messages.linked_retrieval_run_id` は assistant message が採用した最終 retrieval run を参照する
- `chat_messages.linked_retrieval_run_id` は同一 `chat_session_id` の `retrieval_runs` のみ参照可能とする（Phase1 はアプリケーション制御）

### 3.3 文書系

- `users (1) - (N) logical_documents` は Phase1 では **created_by** として保持し、所有権制御までは行わない
- `logical_documents (1) - (N) document_versions`
- `document_versions (1) - (N) document_chunks`

### 3.4 検索 / 根拠系

- chat 起源の `retrieval_runs` は `chat_sessions (1) - (N) retrieval_runs` とする
- `chat_messages (1) - (N) retrieval_runs` ※ `retrieval_runs.request_message_id` は検索要求元 message を指す
- `chat_messages (0..1) - (1) retrieval_runs` の採用結果参照は `chat_messages.linked_retrieval_run_id` で表現する
- `/rag/search` 起源の standalone retrieval run を許容し、この場合 `retrieval_runs.chat_session_id` および `request_message_id` は `NULL` とする
- `retrieval_runs (1) - (N) retrieval_run_items`
- `retrieval_runs (1) - (N) citations`
- `retrieval_run_items (N) - (1) document_chunks`
- `citations (N) - (1) document_chunks`

### 3.5 評価系

- `evaluation_datasets (1) - (N) evaluation_cases`
- `evaluation_datasets (1) - (N) evaluation_runs`
- `evaluation_runs (1) - (N) evaluation_run_items`
- `evaluation_cases (1) - (N) evaluation_run_items`
- `evaluation_run_items (1) - (N) evaluation_results`
- `evaluation_run_items (N) - (0..1) retrieval_runs`

### 3.6 ジョブ / 監査 / 設定系

- `users (1) - (N) jobs` ※ created_by
- `jobs (1) - (N) jobs` ※ retry lineage は `retry_of_job_id` で表現する
- `users (1) - (N) audit_logs` ※ actor_user_id
- `users (1) - (1) user_settings`
- `users (1) - (N) system_settings` 更新主体は updated_by

---

## 4. 設計判断の確定事項

### 4.1 documents 独立概念は持たない

文書は以下の二層で管理する。

- `logical_documents`
- `document_versions`

`documents` テーブルは定義しない。

### 4.2 active version の表現

`logical_documents.active_document_version_id` は持たない。

採用方式:

- `document_versions.is_active`
- PostgreSQL の partial unique index により、1 logical_document につき active version は1件に制限する

### 4.3 document_chunks の親キー

`document_chunks` は **`document_version_id` のみ** を持つ。  
`logical_document_id` は持たない。  
必要な論理文書単位の情報は `document_versions` を経由して参照する。

### 4.4 会話ピン・TTL

`chat_pins` / `temporary_chat_ttls` は独立テーブルを持たず、`chat_sessions` の属性として管理する。

### 4.5 ジョブ配送方式

Phase1 は **PostgreSQL jobs テーブル + worker polling** を採用する。

### 4.6 ジョブロック回収方式

- job 取得時に `locked_by`, `locked_at`, `lease_expires_at` を設定する
- worker は処理継続中、必要に応じて lease を延長する
- `status = running` でも `lease_expires_at` を過ぎた job は回収候補とする
- 障害終了時は別 worker が再取得可能とする

### 4.7 評価結果の責務

- `evaluation_runs`: 実行ヘッダ
- `evaluation_run_items`: ケース別結果
- `evaluation_results`: metric別明細（縦持ち）

---

## 5. PostgreSQL テーブル定義

以下では、列、型、制約、索引、運用観点を定義する。

> 型は PostgreSQL を前提とする。

---

## 5.1 roles

### 役割

ユーザーロール定義。

### 列

- `role_id` BIGSERIAL PK
- `role_name` VARCHAR(50) NOT NULL
- `description` TEXT NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `UNIQUE(role_name)`
- `CHECK (role_name IN ('admin', 'viewer'))` は Phase1 では採用可

### 索引

- `UNIQUE(role_name)`

---

## 5.2 users

### 役割

認証主体。

### 列

- `user_id` BIGSERIAL PK
- `role_id` BIGINT NOT NULL FK -> roles.role_id
- `email` VARCHAR(255) NOT NULL
- `password_hash` TEXT NOT NULL
- `display_name` VARCHAR(100) NULL
- `is_active` BOOLEAN NOT NULL DEFAULT true
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `UNIQUE(email)`

### 索引

- `UNIQUE(email)`
- `INDEX(role_id)`

### 補足

- Phase1 は `users.role_id` の単純モデルを採用する
- `email` はアプリケーション層で **trim + lower** 正規化して保存する

---

## 5.3 auth_sessions

### 役割

サーバーサイドセッション管理。

### 列

- `auth_session_id` BIGSERIAL PK
- `user_id` BIGINT NOT NULL FK -> users.user_id
- `session_token_hash` TEXT NOT NULL
- `expires_at` TIMESTAMPTZ NOT NULL
- `last_accessed_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `revoked_at` TIMESTAMPTZ NULL
- `client_ip_masked` VARCHAR(128) NULL
- `user_agent` TEXT NULL

### 制約

- `UNIQUE(session_token_hash)`

### 索引

- `INDEX(user_id, expires_at)`
- `INDEX(expires_at)`

---

## 5.4 chat_sessions

### 役割

会話セッション親テーブル。

### 列

- `chat_session_id` BIGSERIAL PK
- `owner_user_id` BIGINT NOT NULL FK -> users.user_id
- `title` VARCHAR(255) NOT NULL
- `pinned` BOOLEAN NOT NULL DEFAULT false
- `temporary_flag` BOOLEAN NOT NULL DEFAULT false
- `ttl_expires_at` TIMESTAMPTZ NULL
- `status` VARCHAR(30) NOT NULL DEFAULT 'active'
- `last_message_at` TIMESTAMPTZ NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (status IN ('active','archived'))`
- `CHECK ((temporary_flag = true AND ttl_expires_at IS NOT NULL) OR (temporary_flag = false AND ttl_expires_at IS NULL))`

### 索引

- `INDEX(owner_user_id, updated_at DESC)`
- `INDEX(owner_user_id, pinned, updated_at DESC)`
- `INDEX(temporary_flag, ttl_expires_at)`

### 削除方針

- 通常会話は論理管理
- 一時チャットは TTL 到達後に物理削除ジョブ対象

### 補足

- `title` は NOT NULL とし、未指定時は保存前にサーバーが仮タイトルを補完する
- `status` は `active` / `archived` のみを持ち、通常運用で `deleted` は使用しない

---

## 5.5 chat_messages

### 役割

会話メッセージ。

### 列

- `chat_message_id` BIGSERIAL PK
- `chat_session_id` BIGINT NOT NULL FK -> chat_sessions.chat_session_id
- `client_message_id` VARCHAR(255) NULL
- `role` VARCHAR(20) NOT NULL
- `body` TEXT NOT NULL
- `edited_flag` BOOLEAN NOT NULL DEFAULT false
- `parent_message_id` BIGINT NULL FK -> chat_messages.chat_message_id
- `lineage_group_id` UUID NOT NULL
- `linked_retrieval_run_id` BIGINT NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (role IN ('user','assistant','system'))`

### 索引

- `INDEX(chat_session_id, created_at)`
- `INDEX(lineage_group_id, created_at)`
- `INDEX(linked_retrieval_run_id)`
- `INDEX(parent_message_id)`
- `UNIQUE(chat_session_id, client_message_id) WHERE client_message_id IS NOT NULL` の partial unique index を採用

### 補足

- 編集後再生成のため `parent_message_id` と `lineage_group_id` を保持する
- `client_message_id` は `user` message の重複送信防止に利用する
- `client_message_id` は `role = 'user'` の message にのみ設定し、`assistant` / `system` message では `NULL` を基本とする（Phase1 はアプリケーション制御）
- `client_message_id` は最大長 255、空文字不可、使用可能文字は英数字・`-`・`_` とし、API/Pydantic で制御する
- duplicate 判定は `chat_session_id + client_message_id` で既存 `user` message を検索し、本文一致 / 不一致をアプリケーション側で判定する
- `linked_retrieval_run_id` は assistant message が最終的に採用した `retrieval_runs.retrieval_run_id` を指す
- `linked_retrieval_run_id` を持てるのは `role = 'assistant'` の message のみとし、`user` / `system` message では常に `NULL` とする（Phase1 はアプリケーション制御）
- `linked_retrieval_run_id` は同一 `chat_session_id` の `retrieval_runs` のみ参照可能とする（Phase1 はアプリケーション制御）
- 循環参照のため、DDL では `linked_retrieval_run_id -> retrieval_runs.retrieval_run_id` の FK は後段の `ALTER TABLE` または `DEFERRABLE` 制約で追加する想定とする

---

## 5.6 chat_tags

### 役割

会話タグ。

### 列

- `chat_tag_id` BIGSERIAL PK
- `chat_session_id` BIGINT NOT NULL FK -> chat_sessions.chat_session_id
- `tag_name` VARCHAR(100) NOT NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `UNIQUE(chat_session_id, tag_name)`

### 索引

- `INDEX(tag_name)`
- `INDEX(chat_session_id)`

---

## 5.7 summary_memories

### 役割

会話要約メモリ。

### 列

- `summary_memory_id` BIGSERIAL PK
- `chat_session_id` BIGINT NOT NULL FK -> chat_sessions.chat_session_id
- `summary_text` TEXT NOT NULL
- `source_message_upto_id` BIGINT NULL FK -> chat_messages.chat_message_id
- `version_no` INTEGER NOT NULL DEFAULT 1
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (version_no >= 1)`
- `UNIQUE(chat_session_id, version_no)`

### 索引

- `INDEX(chat_session_id, version_no DESC)`
- `INDEX(source_message_upto_id)`

### 補足

- Phase1 では最新1件のみ参照すれば十分だが、version を持たせてデバッグ性を確保する
- `source_message_upto_id` は同一 `chat_session_id` 内の `chat_messages` のみ参照可能とし、Phase1 はアプリケーション制御で保証する

---

## 5.8 logical_documents

### 役割

文書の論理単位。

### 列

- `logical_document_id` BIGSERIAL PK
- `created_by` BIGINT NULL FK -> users.user_id
- `document_name` VARCHAR(255) NOT NULL
- `source_type` VARCHAR(30) NOT NULL
- `status` VARCHAR(30) NOT NULL DEFAULT 'active'
- `archived_at` TIMESTAMPTZ NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (source_type IN ('upload','api','cli','folder_watch','url'))`
- `CHECK (status IN ('active','archived'))`

### 索引

- `INDEX(status)`
- `INDEX(updated_at DESC)`
- `INDEX(document_name)`

### 補足

- Phase1 は admin による共有コーパスとして扱い、owner ベースの厳密分離までは行わない
- `archived` は通常運用で非アクティブ化した状態を表す
- Phase1 では `deleted` 状態は持たず、物理削除は管理者限定操作として別途扱う
- `logical_documents` を archive した場合、Phase1 では関連する active version を無効化し、Qdrant payload の `is_active` mirror も更新して retrieval 対象外とする
- Phase1 の通常 API フローでは archive は logical document 単位で扱い、`document_versions.status = 'archived'` は原則使用しない

---

## 5.9 document_versions

### 役割

文書の版管理。

### 列

- `document_version_id` BIGSERIAL PK
- `logical_document_id` BIGINT NOT NULL FK -> logical_documents.logical_document_id
- `version_no` INTEGER NOT NULL
- `storage_path` TEXT NOT NULL
- `content_hash` VARCHAR(128) NOT NULL
- `mime_type` VARCHAR(255) NOT NULL
- `file_size_bytes` BIGINT NOT NULL
- `status` VARCHAR(30) NOT NULL
- `is_active` BOOLEAN NOT NULL DEFAULT false
- `ingested_at` TIMESTAMPTZ NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `created_by` BIGINT NULL FK -> users.user_id

### 制約

- `UNIQUE(logical_document_id, version_no)`
- `UNIQUE(logical_document_id, content_hash)`
- `CHECK (status IN ('uploaded','queued','parsing','parsed','chunking','embedding','indexing','ready','failed','pending_review','archived'))`

### 索引

- `INDEX(logical_document_id, version_no DESC)`
- `INDEX(logical_document_id, is_active)`
- `INDEX(status)`
- `UNIQUE(logical_document_id) WHERE is_active = true` の partial unique index を採用

### 補足

- `active version` は `document_versions.is_active` の一意制約で表現し、`logical_documents.active_document_version_id` は持たない
- `is_active = true` を許可するのは `status = 'ready'` の版のみとする
- Phase1 はアプリケーション制御でこれを保証し、将来必要に応じて DB 制約やトリガで強化可能とする
- `UNIQUE(logical_document_id, content_hash)` を正式採用し、同一 logical document 内で同一内容は新 version を作成せずスキップする
- revert により過去と同一内容へ戻した場合も、新 version は作成しない
- 重複スキップ時は `audit_logs` と `jobs` 実行結果で追跡可能とする
- Phase1 の `content_hash` は version 判定用の一意性判定キーとする
- Phase1 では原本ファイルの安定したバイト列に対する hash を採用する
- OCR や抽出器差分の影響を含めるかは Phase3 以降の詳細設計で拡張する
- `document_versions.status='archived'` は将来拡張余地として状態一覧には残すが、Phase1 の通常運用では遷移対象外とする

---

## 5.10 document_chunks

### 役割

検索最小単位。

### 列

- `document_chunk_id` BIGSERIAL PK
- `document_version_id` BIGINT NOT NULL FK -> document_versions.document_version_id
- `chunk_index` INTEGER NOT NULL
- `section_title` TEXT NULL
- `page_from` INTEGER NULL
- `page_to` INTEGER NULL
- `content_text` TEXT NOT NULL
- `modality` VARCHAR(30) NOT NULL DEFAULT 'text'
- `language` VARCHAR(20) NULL
- `token_count` INTEGER NULL
- `char_count` INTEGER NULL
- `content_hash` VARCHAR(128) NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `UNIQUE(document_version_id, chunk_index)`
- `CHECK (modality IN ('text','table','ocr_text','image_caption','metadata_summary'))`

### 索引

- `INDEX(document_version_id, chunk_index)`
- `INDEX(modality, language)`

### 補足

- `document_id` は持たない
- logical_document 単位の参照は `document_versions` 経由で行う
- RDB の active 状態の正は `document_versions.is_active` のみとする
- Qdrant payload 上の `is_active` は検索高速化のための denormalized mirror とする

---

## 5.11 retrieval_runs

### 定義

`retrieval_runs` は **chat 起源 run** と **standalone retrieval run** の両方を表す。

- chat 起源 run: `chat_session_id` / `request_message_id` を持つ
- standalone retrieval run: `/rag/search` 起源であり、`chat_session_id = NULL` かつ `request_message_id = NULL` とする

### 役割

検索 / 回答生成の実行ヘッダ。

### 列

- `retrieval_run_id` BIGSERIAL PK
- `chat_session_id` BIGINT NULL FK -> chat_sessions.chat_session_id
- `request_message_id` BIGINT NULL FK -> chat_messages.chat_message_id
- `normalized_query` TEXT NOT NULL
- `top_k_requested` INTEGER NOT NULL
- `filters_json` JSONB NULL
- `reranker_model_name` VARCHAR(255) NULL
- `embedding_model_name` VARCHAR(255) NULL
- `retrieval_score_summary` NUMERIC(10,6) NULL
- `rerank_score_top1` NUMERIC(10,6) NULL
- `answer_confidence` NUMERIC(10,6) NULL
- `groundedness_score` NUMERIC(10,6) NULL
- `confidence_label` VARCHAR(20) NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (top_k_requested > 0)`
- `CHECK (confidence_label IN ('High','Medium','Low'))`

### 索引

- `INDEX(chat_session_id, created_at DESC)`
- `INDEX(request_message_id)`
- `INDEX(created_at DESC)`

### 補足

- `request_message_id` は chat 起源 run の場合のみ使用し、同一 `chat_session_id` 内の `chat_messages` のみ参照可能とする
- `/rag/search` 起源の standalone retrieval run では `chat_session_id = NULL`、`request_message_id = NULL` とする
- 一時チャット起源の `retrieval_runs` は評価系から参照しない
- `retrieval_runs` は Phase1 では retrieval trace を主とし、generation trace を強化する場合は `generation_model_name` と `prompt_version` を将来拡張列として追加できる構造とする

---

## 5.12 retrieval_run_items

### 役割

検索候補群 / 採用群の明細。

### 列

- `retrieval_run_item_id` BIGSERIAL PK
- `retrieval_run_id` BIGINT NOT NULL FK -> retrieval_runs.retrieval_run_id
- `document_chunk_id` BIGINT NOT NULL FK -> document_chunks.document_chunk_id
- `initial_score` NUMERIC(10,6) NULL
- `rerank_score` NUMERIC(10,6) NULL
- `selected_flag` BOOLEAN NOT NULL DEFAULT false
- `selected_reason` TEXT NULL
- `rank_order` INTEGER NOT NULL
- `page_from` INTEGER NULL
- `page_to` INTEGER NULL

### 制約

- `UNIQUE(retrieval_run_id, rank_order)`
- `UNIQUE(retrieval_run_id, document_chunk_id)`

### 索引

- `INDEX(retrieval_run_id, selected_flag, rank_order)`
- `INDEX(document_chunk_id)`

### 補足

- `document_version_id` は冗長保持せず、`document_chunk_id` を正とする
- version 情報は `document_chunks -> document_versions` 経由で解決する

---

## 5.13 citations

### 役割

UI 表示用回答根拠。

### 列

- `citation_id` BIGSERIAL PK
- `retrieval_run_id` BIGINT NOT NULL FK -> retrieval_runs.retrieval_run_id
- `document_chunk_id` BIGINT NOT NULL FK -> document_chunks.document_chunk_id
- `snippet` TEXT NOT NULL
- `page_from` INTEGER NULL
- `page_to` INTEGER NULL
- `source_type` VARCHAR(20) NOT NULL
- `source_url` TEXT NULL
- `display_label` VARCHAR(255) NOT NULL
- `rank_order` INTEGER NOT NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (source_type IN ('private','public'))`
- `UNIQUE(retrieval_run_id, rank_order)`

### 索引

- `INDEX(retrieval_run_id, rank_order)`
- `INDEX(document_chunk_id)`

### 補足

- `citations` は `retrieval_run_items` の単なる再利用ではなく、UI表示用の snippet / label / url を固定するため別テーブルで保持する
- `document_version_id` は冗長保持せず、`document_chunk_id` を正とする

---

## 5.14 evaluation_datasets

### 役割

評価データセット定義。

### 列

- `evaluation_dataset_id` BIGSERIAL PK
- `dataset_name` VARCHAR(255) NOT NULL
- `dataset_version` VARCHAR(50) NOT NULL
- `description` TEXT NULL
- `source_revision` VARCHAR(255) NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `created_by` BIGINT NULL FK -> users.user_id

### 制約

- `UNIQUE(dataset_name, dataset_version)`

### 索引

- `INDEX(dataset_name)`

---

## 5.15 evaluation_cases

### 役割

評価ケース。

### 列

- `evaluation_case_id` BIGSERIAL PK
- `evaluation_dataset_id` BIGINT NOT NULL FK -> evaluation_datasets.evaluation_dataset_id
- `case_key` VARCHAR(255) NOT NULL
- `question_text` TEXT NOT NULL
- `expected_answer` TEXT NULL
- `expected_citations_json` JSONB NULL
- `language` VARCHAR(20) NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `UNIQUE(evaluation_dataset_id, case_key)`

### 索引

- `INDEX(evaluation_dataset_id)`

---

## 5.16 evaluation_runs

### 役割

評価実行ヘッダ。

### 列

- `evaluation_run_id` BIGSERIAL PK
- `evaluation_dataset_id` BIGINT NOT NULL FK -> evaluation_datasets.evaluation_dataset_id
- `trigger_type` VARCHAR(50) NOT NULL
- `model_version` VARCHAR(255) NULL
- `prompt_version` VARCHAR(255) NULL
- `retrieval_settings_json` JSONB NULL
- `evaluator_type` VARCHAR(100) NULL
- `started_at` TIMESTAMPTZ NULL
- `finished_at` TIMESTAMPTZ NULL
- `status` VARCHAR(30) NOT NULL DEFAULT 'queued'
- `created_by` BIGINT NULL FK -> users.user_id

### 制約

- `CHECK (trigger_type IN ('manual','ci','scheduled','post_deploy','online_sampled_trace'))`
- `CHECK (status IN ('queued','running','succeeded','failed','canceled'))`

### 索引

- `INDEX(evaluation_dataset_id, started_at DESC)`
- `INDEX(trigger_type, started_at DESC)`
- `INDEX(status)`

### 補足

- `started_at` は queued 作成時ではなく running 遷移時に設定する
- `canceled` は内部中止または将来の管理操作用の予約状態であり、Phase1 の公開 API では cancel endpoint を持たない

---

## 5.17 evaluation_run_items

### 役割

ケース別評価結果。

### 列

- `evaluation_run_item_id` BIGSERIAL PK
- `evaluation_run_id` BIGINT NOT NULL FK -> evaluation_runs.evaluation_run_id
- `evaluation_case_id` BIGINT NOT NULL FK -> evaluation_cases.evaluation_case_id
- `retrieval_run_id` BIGINT NULL FK -> retrieval_runs.retrieval_run_id
- `status` VARCHAR(30) NOT NULL DEFAULT 'queued'
- `latency_ms` INTEGER NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `UNIQUE(evaluation_run_id, evaluation_case_id)`
- `CHECK (status IN ('queued','running','succeeded','failed','canceled'))`

### 索引

- `INDEX(evaluation_run_id)`
- `INDEX(evaluation_case_id)`
- `INDEX(retrieval_run_id)`

### 補足

- Phase1 では temporary chat 起源の `retrieval_run_id` を評価系で参照しない
- `canceled` は内部中止または将来の管理操作用の予約状態であり、Phase1 の公開 API では item 単位の cancel endpoint を持たない

---

## 5.18 evaluation_results

### 役割

metric別明細。

### 列

- `evaluation_result_id` BIGSERIAL PK
- `evaluation_run_item_id` BIGINT NOT NULL FK -> evaluation_run_items.evaluation_run_item_id
- `metric_name` VARCHAR(100) NOT NULL
- `metric_score` NUMERIC(10,6) NULL
- `metric_label` VARCHAR(100) NULL
- `details_json` JSONB NULL

### 制約

- `UNIQUE(evaluation_run_item_id, metric_name)`

### 索引

- `INDEX(metric_name, metric_score)`
- `INDEX(evaluation_run_item_id)`

### 補足

Phase1 は metric master を別テーブル化せず、`metric_name` 文字列直持ちとする。

---

## 5.19 jobs

### 役割

非同期処理管理。

### 列

- `job_id` BIGSERIAL PK
- `retry_of_job_id` BIGINT NULL FK -> jobs.job_id
- `job_type` VARCHAR(50) NOT NULL
- `payload_json` JSONB NOT NULL
- `status` VARCHAR(30) NOT NULL DEFAULT 'queued'
- `retry_count` INTEGER NOT NULL DEFAULT 0
- `scheduled_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `started_at` TIMESTAMPTZ NULL
- `finished_at` TIMESTAMPTZ NULL
- `error_message` TEXT NULL
- `created_by` BIGINT NULL FK -> users.user_id
- `locked_by` VARCHAR(100) NULL
- `locked_at` TIMESTAMPTZ NULL
- `lease_expires_at` TIMESTAMPTZ NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `CHECK (status IN ('queued','running','succeeded','failed','canceled'))`
- `CHECK (retry_count >= 0)`
- `CHECK (retry_of_job_id IS NULL OR retry_of_job_id <> job_id)`

### 索引

- `INDEX(status, scheduled_at)`
- `INDEX(status, lease_expires_at, scheduled_at)`
- `INDEX(job_type, status)`
- `INDEX(created_by)`
- `INDEX(retry_of_job_id)`
- `INDEX(locked_at)`
- `INDEX(lease_expires_at)`

### 補足

- `retry_of_job_id` により、再試行 job の lineage を追跡可能とする
- `retry_count` は **各 job 自身の実行試行回数** を表し、retry lineage 全体の回数は `retry_of_job_id` を辿って把握する
- `locked_by / locked_at / lease_expires_at` を持たせ、将来の複数 worker 化にも耐えられるようにする
- `lease_expires_at` を過ぎた running job は回収候補とする
- 実際の dequeue / reclaim クエリは詳細設計で固定する
- 状態遷移仕様上、`queued` では lock 系・started_at・finished_at は `NULL`、`running` では `locked_by / locked_at / lease_expires_at / started_at` 必須、terminal 状態では `finished_at` 必須とする
- 同一 source job に対して `queued/running` の active retry は 1件までとする

---

## 5.20 audit_logs

### 役割

監査証跡。

### 列

- `audit_log_id` BIGSERIAL PK
- `actor_user_id` BIGINT NULL FK -> users.user_id
- `action_type` VARCHAR(100) NOT NULL
- `target_type` VARCHAR(100) NOT NULL
- `target_id` VARCHAR(255) NULL
- `request_id` VARCHAR(255) NULL
- `details_json` JSONB NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 索引

- `INDEX(actor_user_id, created_at DESC)`
- `INDEX(action_type, created_at DESC)`
- `INDEX(target_type, target_id)`
- `INDEX(request_id)`

### 補足

監査対象:

- 認証
- 文書操作
- 評価実行
- 設定変更
- 外部API利用
- 将来のマスク解除操作

---

## 5.21 user_settings

### 役割

ユーザー個別設定。

### 列

- `user_id` BIGINT PK FK -> users.user_id
- `memory_message_limit` INTEGER NOT NULL DEFAULT 8
- `ui_theme` VARCHAR(30) NOT NULL DEFAULT 'light'
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `PRIMARY KEY(user_id)`
- `CHECK (memory_message_limit BETWEEN 1 AND 20)`
- `CHECK (ui_theme IN ('light','dark','system'))`

### 索引

- 原則不要

### 補足

- `/users/me/settings` の保存先として利用する
- `system_settings` とは分離し、ユーザーごとの表示・操作設定のみを保持する
- `users` 作成 transaction 内で `user_settings` を同時 INSERT する eager create 方針とする
- Phase1 の正式値は `memory_message_limit: 1..20`、`ui_theme: light / dark / system` とする

---

## 5.22 system_settings

### 役割

運用設定。

### 列

- `setting_key` VARCHAR(100) PK
- `setting_value_json` JSONB NOT NULL
- `updated_by` BIGINT NULL FK -> users.user_id
- `updated_at` TIMESTAMPTZ NOT NULL DEFAULT now()

### 制約

- `PRIMARY KEY(setting_key)`

### 索引

- 原則不要

### 補足

Phase1 は confidence 閾値、メモリ件数 N などをここへ一元配置する。

---

## 5.23 pii_mapping_store_reference

### 役割

再識別が必要な項目のみを外部安全領域へマッピングするための参照。

### 列

- `pii_mapping_ref_id` BIGSERIAL PK
- `tokenized_value` VARCHAR(255) NOT NULL
- `mapping_store_key` VARCHAR(255) NOT NULL
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `expires_at` TIMESTAMPTZ NULL

### 制約

- `UNIQUE(tokenized_value)`

### 索引

- `INDEX(expires_at)`

### 補足

Phase1 はテーブル先行定義のみでも可。

---

## 6. Qdrant 設計

### 6.1 collection 方針

- collection は原則 1つを採用
- payload filtering により version / active / modality / language を制御する

### 6.2 point payload

- `document_version_id`
- `logical_document_id` ※ RDB には持たないが payload には持つ
- `document_chunk_id`
- `chunk_index`
- `section_title`
- `page_from`
- `page_to`
- `modality`
- `language`
- `is_active` ※ `document_versions.is_active` の denormalized mirror
- `source_type`

### 補足

- Phase1 では document tags を正式スコープ外とし、Qdrant payload に `tags` は持たない
- chunk indexing 時に `document_versions` を参照して payload を構築する
- active version 切替時は、該当 points の payload を更新する

### 6.3 vector

- dense vector: `bge-m3`
- sparse / multimodal vector は Phase2 / Phase3 以降の拡張余地

---

## 7. FK / 削除方針

### 7.1 基本方針

- 通常削除は論理削除 / アーカイブを優先する
- FK は安易に CASCADE しない
- 履歴保持が必要な関係は RESTRICT / NO ACTION を基本とする

### 7.2 物理削除を許容する対象

- 一時チャット関連
- 管理者限定の document physical delete 対象

### 7.3 一時チャット削除方針

一時チャット TTL 到達時は、以下を一括削除対象とする。

- `chat_sessions`
- `chat_messages`
- `chat_tags`
- `summary_memories`
- `retrieval_runs`
- `retrieval_run_items`
- `citations`

Phase1 では、一時チャットは恒久保存対象ではないため、関連 retrieval / citation も保持しない方針を採用する。

### 7.4 一時チャット物理削除手順

1. 対象 session の assistant messages の `linked_retrieval_run_id` を `NULL` に更新する
2. 対象 session に紐づく `retrieval_run_items` と `citations` を削除する
3. 対象 session に紐づく `retrieval_runs` を削除する
4. 対象 session に紐づく `summary_memories` を削除する
5. 対象 session に紐づく `chat_tags` を削除する
6. 対象 session に紐づく `chat_messages` を削除する
7. 対象 `chat_sessions` を削除する

### 7.5 文書物理削除方針

- 通常運用での削除はアーカイブを標準とする
- 物理削除は管理者限定とする
- 物理削除できるのは、`retrieval_run_items`・`citations`・評価系から未参照の文書 version / chunk に限定する
- 一度でも回答根拠や評価に使用された文書は、以後は原則アーカイブのみとする

### 7.6 代表方針

- `chat_sessions -> chat_messages`: 一時チャット削除時にアプリ側制御で削除
- `chat_messages -> retrieval_runs`: 一時チャット削除時にアプリ側制御で削除
- `retrieval_runs -> retrieval_run_items / citations`: 一時チャット削除時にアプリ側制御で削除
- `logical_documents -> document_versions`: 通常はアーカイブ、物理削除は管理者限定
- `document_versions -> document_chunks`: 物理削除時のみアプリ側制御で削除
- `evaluation_runs`: 原則履歴保持

---

## 8. 命名規約

### 8.1 規約

- テーブル名は複数形
- 概念説明は単数形
- PK は `<table_singular>_id`
- FK は参照先概念に基づいて命名する

### 8.2 注意点

- `documents` は使わない
- `document_id` は使わない
- `logical_document_id` と `document_version_id` を厳密に使い分ける
- `role_name` や `modality` などの CHECK 制約は Phase1 の説明性のために採用可能だが、将来拡張時は migration による追加を前提とする

---

## 9. API逆引き観点

DDL 化前に以下を逆引き確認する。

- 会話一覧取得に必要な列があるか
- 会話再開に必要な summary memory の最新取得が速いか
- 一時チャット TTL 削除が索引だけで引けるか
- 文書一覧 / 文書詳細に必要な status / version 情報があるか
- retrieval / citation / confidence が回答単位で追跡できるか
- 評価結果一覧 / 詳細画面に必要な run / item / result の関係があるか
- 監査ログの target 追跡が可能か
- `request_message_id` と `linked_retrieval_run_id` の役割が API 上で混同されないか
- `client_message_id` による重複送信防止が実装可能か
- retry job の lineage が `retry_of_job_id` で追跡可能か
- standalone retrieval run が chat history と混同されないか

---

## 10. Phase2 / Phase3 拡張方針

### 10.1 Phase2

- `online_eval_samples` テーブル追加
- `evaluation_alert_rules` テーブル追加
- `diff_snapshots` テーブル追加
- OCR 前提メタデータ列追加
- hybrid retrieval 関連列追加
- document tags 関連テーブル追加
- LangSmith 連携用の trace 可視化 / observability 拡張
- SentenceTransformers を用いた評価 / 比較実験の管理拡張

### 10.2 Phase3

- `ocr_results` テーブル追加
- 画像関連メタデータ追加
- OIDC 関連テーブル追加
- AWS 連携用設定項目追加

### 10.3 方針

Phase2 / Phase3 は **既存テーブルの全面再設計ではなく、拡張的追加** を原則とする。

---

## 11. 先に固定すべき最重要事項

1. `document_chunks.document_id` は採用しない
2. active version は `document_versions.is_active` + partial unique index で統一
3. `evaluation_results` は metric別明細で固定
4. jobs は `locked_by / locked_at / lease_expires_at / retry_of_job_id` を含める
5. 通常削除はアーカイブ、物理削除は管理者限定
6. `chat_messages.client_message_id` で user message の重複送信防止を支える
7. `user_settings` は `system_settings` と分離し、eager create とする
8. `retrieval_runs` は chat 起源 run と standalone retrieval run の両方を表す

---

## 12. 総括

本設計は、Phase1 の実装に必要な最小構成を満たしつつ、Phase2 / Phase3 で DB 全面作り直しを避けるための前方互換性を重視している。

特に以下を満たすことを重視する。

- 主語の固定
- 1テーブル1責務
- トレーサビリティ
- バージョン管理の一貫性
- 評価拡張のしやすさ
- ジョブ / 監査 / PII の実務性
- standalone retrieval run を含む retrieval trace の一貫性

以上をもって、ER図 / テーブル設計書 v1.4 とする。