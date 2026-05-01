# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## OpenAPI 3.1 契約設計書 v1.1（Batch 4 最終版）

---

## 1. 文書概要

### 1.1 目的

本書は API設計書 v1.9 最終版を OpenAPI 3.1 で表現するための契約設計方針を定義する。
実装コードや YAML 本体を生成する前に、schema 粒度・命名・共通レスポンス・セキュリティ定義・型生成方針を固定する。

### 1.2 対象範囲

- OpenAPI version / ファイル構成
- operationId / tags 命名規則
- components/schemas
- common error response
- pagination / meta.request_id
- CSRF + Cookie session securitySchemes
- admin / viewer 権限制約表現
- nullable / required / enum / format
- examples
- multipart upload
- generated type / frontend 型生成方針
- schema lint / validation 方針

### 1.3 非対象

- OpenAPI YAML 本体実装
- FastAPI route 実装
- SDK 自動生成実行

### 1.4 前提文書

- API設計書 v1.9 最終版
- DDL草案 v1.8
- バックエンド詳細設計書 v1.4 最終版
- フロントエンド詳細設計書 v1.4 最終版
- RAG パイプライン詳細設計書 v1.4 最終版
- Worker / Job 詳細設計書 v1.4 最終版
- テスト設計書 v1.0
- Alembic migration設計書 v1.0

---

## 2. OpenAPI 全体方針

### 2.1 OpenAPI version

- OpenAPI: `3.1.0`
- JSON Schema dialect: OpenAPI 標準 dialect を採用

### 2.2 ベース情報

- `servers`: `/api/v1`
- `info.version`: `v1`
- 文書バージョンは API設計書 v1.9 と同期

### 2.3 ファイル構成方針

```text
openapi/
  openapi.yaml
  paths/
    auth.yaml
    users.yaml
    chat.yaml
    documents.yaml
    rag.yaml
    evaluations.yaml
    jobs.yaml
    audits.yaml
    settings.yaml
  components/
    schemas/
      common.yaml
      auth.yaml
      chat.yaml
      documents.yaml
      rag.yaml
      evaluations.yaml
      jobs.yaml
    responses.yaml
    parameters.yaml
    securitySchemes.yaml
```

> 本Batchでは設計のみ行い、上記ファイルは未作成。

---

## 3. operationId / tags 命名規則

### 3.1 operationId

`<resource><Action>` を lowerCamelCase で統一する。

例:
- `login`
- `getCsrfToken`
- `listChatSessions`
- `createDocumentUpload`
- `askRag`
- `searchRag`
- `retryJob`

### 3.2 tags

- `Auth`
- `Users`
- `Chat`
- `Documents`
- `RAG`
- `Evaluations`
- `Jobs`
- `Audit`
- `Settings`

---

## 4. Security 契約

### 4.1 securitySchemes

- `cookieSessionAuth`
  - type: `apiKey`
  - in: `cookie`
  - name: `session_token`
- `csrfHeader`
  - type: `apiKey`
  - in: `header`
  - name: `X-CSRF-Token`

### 4.2 endpoint 別適用方針

- 認証不要: login / csrf pre-auth
- 認証必要: 基本全API
- CSRF必要: state-changing API（POST/PUT/PATCH/DELETE）

### 4.3 admin / viewer 表現

- OpenAPI では role 自体を securityScheme に埋め込まない（securitySchemes は認証方式のみを表現）
- 権限制御が必要な operation では拡張属性を必須とする

```yaml
x-auth-required: true
x-required-roles:
  - admin
```

viewer / admin 両方許可の operation は以下とする。

```yaml
x-auth-required: true
x-required-roles:
  - viewer
  - admin
```

認証不要 operation は以下とする。

```yaml
x-auth-required: false
```

- 人間向け補足は `description` と `403` error example で明示する

---

## 5. 共通 schema / response 方針

### 5.1 共通 envelope

Phase1 は API設計書 v1.9 互換を維持しつつ、以下の統一度を必須化する。

- 一覧系: `data.items + meta.pagination` を必須
- error系: `error + meta` を必須
- 単一リソース取得系: API設計書 v1.9 互換のため直返しを許容
- 新規追加 endpoint: `data + meta` を標準

これにより型生成の一貫性を確保しつつ、既存契約との互換を維持する。

### 5.2 request_id

- `meta.request_id` を共通化
- error response にも `request_id` を保持する

### 5.3 common error response

- `validation_error` (422)
- `unauthorized` (401)
- `forbidden` (403)
- `not_found` (404)
- `conflict` (409)
- `internal_error` (500)

`error_code`, `message`, `request_id`, `details?` を共通化する。

### 5.4 pagination schema

```yaml
PaginationMeta:
  type: object
  required: [page, page_size, total, total_pages]
```

---

## 6. 重点契約（API v1.9 追随）

### 6.1 nullable / required / enum / format

- `active_version`: nullable
- `display_status`: enum（active/archived 等、API設計に準拠）
- `retrieval_score_summary`: object（JSON）
- `failed run` の `answer_confidence/groundedness/confidence_label`: nullable
- `content_hash`: `^[0-9a-f]{64}$` 相当

### 6.2 `/rag/search`

- 0件は `200 OK + items=[]`

### 6.3 `/rag/ask`

- `no_context_found`: `422`
- `citation_build_failed`: `500`
- replay response は初回成功時と同等の citations を返す

### 6.4 CitationItem

- `document_version_id` は返さない
- `payload_snapshot`（retrieval_run_items 保存項目）と CitationItem（UI表示根拠）は別schemaで管理

### 6.5 Jobs

- job response 時刻は `created_at` を正とし、`scheduled_at` は使わない

---

## 7. multipart upload 契約

- `multipart/form-data`
- `file`（binary）必須
- メタ項目（title 等）は API設計書に合わせ required 判定
- サイズ超過・MIME不正は `422`（または設計準拠エラー）

---

## 8. examples 方針

### 8.1 共通ルール

- 全 operation に最低 1 success example を必須
- error example は重要 endpoint で個別必須、単純 GET detail は共通 error response 参照を許容

### 8.2 重点 endpoint（個別 error example 必須）

- Auth / RBAC 系
- RAG 系
- Documents の upload / approve / archive
- Jobs retry 系
- Admin endpoint

### 8.3 RAG 必須 examples

- `/rag/ask`: success / replay / request_in_progress / no_context_found / citation_build_failed
- `/rag/search`: hit / zero-hit

### 8.4 推奨追加 examples

- `/auth/csrf`: success
- `/auth/login`: success / invalid_credentials / csrf_missing_or_invalid
- `/documents` upload: accepted / duplicate_content_skipped / validation_error
- `/documents/{id}/approve`: success / conflict
- `/jobs/{id}`: queued / running / succeeded / failed
- `/jobs/{id}/retry`: accepted / job_active_retry_exists

---

## 9. generated type / frontend 型生成方針

### 9.1 生成元

- OpenAPI 3.1 YAML を唯一の契約ソースにする

### 9.2 frontend 連携

- 型生成はフロントエンド詳細設計の fetch hook / TanStack Query で利用
- enum は string literal union として生成
- nullable は `T | null` へ反映

### 9.3 互換性ルール

- 破壊的変更は `v2`
- `v1` では後方互換なフィールド追加のみ許可

---

## 10. schema lint / validation 方針

- OpenAPI lint（3.1 準拠）を CI で実施
- examples の schema 適合チェックを実施
- `operationId` 重複禁止
- `$ref` 解決失敗禁止

---

## 11. Batch 5 への追随事項

- 実装分解書で OpenAPI 作成タスクを分割し、以下を明記する:
  - components 先行定義
  - RAG/Jobs の重点エラーケース優先
  - 型生成の差分レビュー手順

---

## 12. 完了条件

- API設計書 v1.9 の重点契約が OpenAPI 観点で一意に定義されている
- security / error / pagination / examples 方針が定義済み
- nullable/enum/format の曖昧さがない
- Batch 5 で実装分解可能

---

## 13. 停止条件（要人間判断）

- API設計書の記述だけでは schema を確定できない箇所がある
- RAG エラー契約（422/500）に矛盾がある
- role 制約の表現方法（403のみか拡張属性併用か）で運用方針が合意できない

---

## 14. 未対応事項・残リスク

- OpenAPI 3.1 ツールチェーンの一部が 3.0 系前提の場合、実装時に互換調整が必要
- examples の最終文面は Batch 7（seed/demo）と同期調整が必要

---

## 15. 次に作成すべき設計書

- 実装分解書（Batch 5 完全版）

