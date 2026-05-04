# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## Alembic migration設計書 v1.0（Batch 3 完全版）

---

## 1. 文書概要

### 1.1 目的

本書は DDL草案 v1.8 を Alembic migration として安全に適用・rollback するため、
migration 分割、適用順序、制約追加順、seed 方針、downgrade 方針、migration test 方針を定義する。

### 1.2 対象範囲

- migration ファイル分割方針
- テーブル作成順
- 循環 FK の ALTER TABLE 追加順
- composite FK / partial unique index / CHECK 制約追加順
- seed data（roles / system settings 初期値）
- downgrade / rollback
- migration test
- ローカル初期化手順

### 1.3 非対象

- migration ファイル実装コード
- アプリケーションモデル実装
- OpenAPI YAML 実装

### 1.4 前提文書

- DDL草案 v1.8
- ER図 / テーブル設計書 v1.4
- API設計書 v1.9 最終版
- 状態遷移仕様書 v1.1
- バックエンド詳細設計書 v1.4 最終版
- RAG パイプライン詳細設計書 v1.4 最終版
- Worker / Job 詳細設計書 v1.4 最終版
- テスト設計書 v1.0（Batch 2 完全版）

---

## 2. migration 基本方針

### 2.1 原則

- 1 migration = 1責務（基盤・主語グループ・循環FK・index・seed を分離）
- 循環参照は CREATE TABLE で無理に閉じず、後段 ALTER TABLE で追加する
- 外部キー整合と CHECK 制約は DDL v1.8 と同一条件を維持する
- downgrade は「直前 revision に戻せること」を最低要件とする

### 2.2 命名規則

`<revision>_<phase>_<topic>.py`

例:
- `0001_base_extensions_and_auth.py`
- `0005_retrieval_and_citations.py`
- `0008_circular_fk_chat_messages_linked_run.py`

### 2.3 transaction 方針

- PostgreSQL で transactional DDL を前提
- ただし extension / index 作成の一部は個別実行単位を明確化

---

## 3. migration 分割と適用順序

## 3.1 Revision 一覧（Phase1 推奨）

1. `0001`: extensions + roles/users/user_settings/user_sessions
2. `0002`: chat_sessions/chat_tags/chat_messages/summary_memories
3. `0003`: logical_documents/document_versions/document_chunks
4. `0004`: jobs
5. `0005`: retrieval_runs/retrieval_run_items/citations（循環FK未追加）
6. `0006`: evaluation_runs/evaluation_run_items
7. `0007`: audit_logs/system_settings
8. `0008`: 循環 FK 追加（chat_messages.linked_retrieval_run_id）
9. `0009`: indexes（通常 index + partial unique index）
10. `0010`: seed（roles / 初期 system_settings）

## 3.2 テーブル作成順の理由

- users 系を先に作成し、作成者FKを持つテーブル群の親を先に確保する
- chat_messages は retrieval_runs 参照を後付けする前提で先に作成
- retrieval_run_items 作成後に citations の composite FK を閉じる
- jobs は多主語に依存しないため documents 後、retrieval 前後どちらでも成立するが、可読性のため documents 後に固定

---

## 4. 制約追加順序

## 4.1 循環 FK（必須）

対象:
- `chat_messages(chat_session_id, linked_retrieval_run_id)`
- `retrieval_runs(chat_session_id, retrieval_run_id)`

順序:
1. `chat_messages` を linked_retrieval_run_id nullable で作成
2. `retrieval_runs` を作成
3. `ALTER TABLE chat_messages ADD CONSTRAINT fk_chat_messages_linked_retrieval_run_same_session ... DEFERRABLE INITIALLY DEFERRED`



### 4.1.1 0005 と 0008 の責務分離（固定）

- `0005` で追加する FK:
  - `retrieval_runs.request_message_id` -> `chat_messages(chat_message_id)`（同時に同一 session 整合の複合FK）
  - `retrieval_run_items` / `citations` の各 FK
- `0008` で追加する FK:
  - `chat_messages.linked_retrieval_run_id` -> `retrieval_runs.retrieval_run_id`（循環FKのみ）

`0002` では `chat_messages.linked_retrieval_run_id` カラムを NULL 許容で作成し、FK は張らない。

## 4.2 composite FK（必須）

対象:
- `summary_memories(chat_session_id, source_message_upto_id)` -> `chat_messages(chat_session_id, chat_message_id)`
- `citations(retrieval_run_id, document_chunk_id)` -> `retrieval_run_items(retrieval_run_id, document_chunk_id)`

順序:
- 参照先テーブル作成後に同 migration 内で追加（作成時定義可）
- `citations` 複合FK成立に必要な `UNIQUE(retrieval_run_id, document_chunk_id)` は `0005` で `retrieval_run_items` 作成時に必ず定義する（`0009` へ遅延しない）

## 4.3 partial unique index（必須）

作成順:
1. `ux_document_versions_one_active`
2. `ux_jobs_active_retry_per_source`
3. `ux_jobs_active_message_edit`
4. `ux_retrieval_run_items_run_rerank_order`
5. `ux_citations_run_rank`

備考:
- evaluation active job 制約は DDL v1.8 未採用のため migration で強制追加しない
- 追随候補は Batch 5（実装分解）で「将来DDL追随候補」として管理
- 同一 evaluation_run_id の active evaluation job は Phase1 では service validation で制御し、DB partial unique index は Phase2 以降または race 顕在化時の追加候補とする

## 4.4 CHECK 制約（必須）

- status enum 系 CHECK をテーブル作成時に追加
- 時刻整合 CHECK（started_at/finished_at/lease_expires_at）を jobs/retrieval/evaluation に追加
- hash format / range CHECK を document_versions/document_chunks/retrieval_runs へ追加

---

## 5. seed data 方針

## 5.1 seed 対象

- roles: `admin`, `viewer`
- system_settings: 必須キーのみ（空で起動可能なら任意）

## 5.2 seed 実行方法

- migration `0010` で最小 seed を投入
- 冪等性のため `ON CONFLICT DO NOTHING` 方針


運用前提（必須）:

- アプリ起動前に `0001` から `0010` までを一括適用する。
- `roles` seed は `0010` で投入するため、`0001` のみ適用した状態でアプリを起動しない。

## 5.3 system user

- Phase1 では DB 内の専用 system user row は必須化しない
- 必要な `created_by` は API 実行ユーザーか `NULL許容` に従う

---

## 6. downgrade / rollback 方針

## 6.1 方針

- downgrade は revision 単位で逆順
- 依存順序: FK -> index -> table の順で削除
- seed rollback は「投入データのみ削除」を原則とする

## 6.2 注意点

- 循環 FK は先に DROP CONSTRAINT してから親子削除
- partial unique index を先に drop し、UNIQUE違反誤認を防止
- 既存業務データがある環境では full downgrade を運用手順で禁止可能（ローカル限定許可）



## 6.3 downgrade 実行順（固定）

1. `0010` seed rollback
2. `0009` indexes drop
3. `0008` circular FK drop
4. `0007` audit_logs/system_settings drop
5. `0006` evaluation tables drop
6. `0005` retrieval/citations tables drop
7. `0004` jobs drop
8. `0003` documents tables drop
9. `0002` chat tables drop
10. `0001` auth/users base drop

注意: `0008` の循環 FK は、関連 table drop 前に必ず先に drop する。

---

## 7. migration test 方針

## 7.1 必須テスト

1. empty DB から `upgrade head` 成功
2. `downgrade base` 成功
3. `upgrade head -> downgrade base -> upgrade head` 再適用成功
4. 主要制約テスト
   - user_settings 1:1
   - retrieval standalone CHECK
   - jobs status/timestamp CHECK
   - citations composite FK
   - document_versions one active
   - jobs active retry / message_edit partial unique

## 7.2 回帰テスト

- migration 後に API smoke（auth/login, document upload enqueue, rag/search）
- worker 起動時 startup check が migration 結果と矛盾しない

---

## 8. ローカル開発初期化手順（設計）

1. DB 作成
2. `alembic upgrade head`
3. seed 実行（0010 で自動または明示）
4. 最低限 smoke SQL 実行
5. backend/worker 起動

失敗時:
- schema mismatch は `alembic current/history` で調査
- 開発環境のみ `downgrade base` + `upgrade head` で再構築

---

## 9. 完了条件（Exit Criteria）

- migration 分割・順序・制約追加順が DDL v1.8 と矛盾しない
- 循環FK / composite FK / partial unique index の順序が明記済み
- seed / rollback / migration test 方針が定義済み
- Batch 4（OpenAPI契約設計）・Batch 5（実装分解）へ入力可能

---

## 10. 停止条件（要人間判断）

- DDL v1.8 と既存確定文書の矛盾が判明した場合
- downgrade 方針が運用制約と衝突する場合
- evaluation active job 制約を DDL へ即時反映する判断が必要な場合

---

## 11. 未対応事項・残リスク

- evaluation active job partial unique index は現状「候補」扱い
- migration 実装時に DB 方言差分（PostgreSQL限定事項）の実検証が必要
- seed の system_settings キーセットは Batch 7/8 のデモ要件で再確認する

---

## 12. 次に作成すべき設計書

- OpenAPI 3.1 契約設計書（Batch 4 完全版）

