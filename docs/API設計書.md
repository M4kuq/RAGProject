# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## API設計書 v1.9 最終版

---

## 1. 文書概要

### 1.1 目的

本書は、要件定義書 v1.1、基本設計書 v1.2、ER図 / テーブル設計書 v1.4、DDL草案 v1.8、RAG パイプライン詳細設計書 v1.4、バックエンド詳細設計書 v1.4 をもとに、RAGシステムの API 仕様を定義する。

本書の目的は以下である。

- フロントエンド、CLI、Worker、管理機能が共通で利用できる API 契約を定義する
- 認証、認可、入力検証、監査、PII 保護を API レベルで明確化する
- Phase1 実装に必要な最小 API を確定し、Phase2 / Phase3 の拡張余地を残す
- 実装者が request / response / error / permission を迷わず実装できる状態にする
- RAG trace / citation / confidence / document version 表示状態の API 契約を固定する
- DDL と API の状態表現を分離し、UI 表示用状態を deterministic に導出できるようにする
- DDL v1.8 の jobs / retrieval_runs / retrieval_run_items / citations の制約と API response の命名を一致させる

### 1.2 対象範囲

本書では以下を対象とする。

- 認証 API
- ユーザー / セッション API
- 会話 API
- 文書 API
- RAG API
- 評価 API
- ジョブ API
- 監査 API
- 設定 API

### 1.3 非対象

本書では以下の詳細は対象外とする。

- OpenAPI の完全 YAML 出力
- 各フィールドの Pydantic 実装コード
- DB 実装詳細
- Worker 内部ロジック実装
- フロントエンド表示実装

### 1.4 v1.9 の重点修正

v1.9 では、v1.8 レビューを踏まえて以下を反映する。

- chat tag 重複追加時の挙動を `200 OK + result_code = already_exists` に固定する
- `active_version` は nullable と明記する
- approve 済み active version が存在しない場合、`active_version = null` を返す
- `latest_version` は logical document 配下で `version_no` が最大の document version と定義する
- `content_hash` の例を DDL 制約に合わせて `<sha256_hex_64>` に統一する
- Markdown code fence の崩れを修正する
- Phase1 の主要 ID と Phase2 以降候補 ID を分離する
- `old_version_flag` の導出条件を `CitationItem` schema 付近にも明記する
- v1.8 で確定した `/rag/search` 0件時、`retrieval_score_summary` key 統一、`scheduled_at` 廃止、replay citations 返却、`display_status` archived 優先を維持する

---

## 2. API 全体方針

### 2.1 プロトコル

- HTTP/1.1 または HTTP/2
- JSON ベース
- ファイルアップロードのみ `multipart/form-data`
- 文字コードは UTF-8

### 2.2 ベースパス

- `/api/v1`

例:

- `/api/v1/auth/login`
- `/api/v1/chat/sessions`
- `/api/v1/documents`

### 2.3 API バージョニング

- URL バージョニングを採用する
- Phase1 は `v1`
- 非互換変更は `v2` で提供する
- 互換的なフィールド追加は `v1` 内で許容する

### 2.4 設計原則

- REST を基本とし、RAG 質問実行など一部は action endpoint を許容する
- API request / response は Pydantic v2 schema で定義する
- ORM モデルを直接返却しない
- 一覧 API は pagination を前提とする
- 監査対象操作はすべて request_id 単位で追跡可能にする
- セキュリティ上危険な操作は idempotency / confirmation / permission を明示する
- optional behavior は原則持ち込まず、同条件では同一挙動を返す
- API response の命名は DDL と矛盾させない
- 内部 DB 状態と UI 表示用状態は混同しない

### 2.5 時刻表現

- すべての日時は **RFC3339 / ISO 8601 UTC (`Z`)** で返す
- クエリパラメータの `from` / `to` なども同形式で受ける

### 2.6 pagination 共通ルール

- 既定 `page=1`
- `page >= 1` を必須とする
- `page` は整数とする
- `page` の上限は設けず、範囲外は空配列で返す
- 既定 `page_size=20`
- 最大 `page_size=100`
- 上限超過は `422 validation_error`
- `page` / `page_size` を受ける一覧 API は必ず `meta.pagination` を返す

### 2.7 nested path 親子整合ルール

- 親子関係を持つ nested path は、必ず親 ID と子 ID の整合性をサーバー側で検証する
- 不整合時は **404** を返す

例:

- `/documents/{logical_document_id}/versions/{document_version_id}`
- `/documents/{logical_document_id}/versions/{document_version_id}/approve`
- `/chat/sessions/{chat_session_id}/messages`
- `/chat/sessions/{chat_session_id}/tags/{tag_name}`

### 2.8 共通 no-op 応答方針

- archive や重複 tag 追加などの冪等操作は、対象がすでに最終状態でも成功とみなす
- Phase1 の no-op は `200 OK` で返し、`data.result_code` に現在状態を返す
- 例: `already_archived`, `already_active`, `already_exists`

### 2.9 永続状態と表示状態の分離

API response では、DB 永続状態と UI 表示状態を明確に分離する。

#### 永続状態

永続状態は DB の `status` / `is_active` / `archived_at` 等の組み合わせをそのまま表す。

例:

- `logical_documents.status`
- `document_versions.status`
- `document_versions.is_active`
- `retrieval_runs.status`
- `jobs.status`

#### 表示状態

表示状態は UI / CLI が迷わず表示できるよう、API 側で導出した値を返す。

代表例:

- `document_versions.display_status`

Phase1 では、document version の承認待ちは DB status として `pending_review` を持たない。

承認待ちは以下で表現する。

```text
document_versions.status = 'ready'
AND document_versions.is_active = false
AND logical_documents.status = 'active'
```

API は必要に応じて、上記を `display_status = 'pending_review'` として返す。

`display_status` は DB column ではない。API service 層で DB status / `is_active` / logical document status から導出する。

#### logical document archived 優先ルール

`logical_documents.status = 'archived'` の場合、配下 version の `status` / `is_active` に関わらず、表示上は `display_status = 'archived'` を最優先する。

version 単位の response で `display_status` を返す場合も、必ず親 logical document の状態を考慮して導出する。

### 2.10 主要 ID 方針

#### Phase1 の主要 ID

Phase1 で API response / request に登場する主要 ID は以下とする。

- `user_id`
- `chat_session_id`
- `chat_message_id`
- `logical_document_id`
- `document_version_id`
- `document_chunk_id`
- `retrieval_run_id`
- `retrieval_run_item_id`
- `citation_id`
- `evaluation_run_id`
- `evaluation_run_item_id`
- `job_id`
- `audit_log_id`

#### Phase2 以降候補 ID

評価データセットを DB / API として独立管理する場合、Phase2 以降で以下を追加候補とする。

- `evaluation_dataset_id`
- `evaluation_case_id`

Phase1 では、評価ケース管理をファイルまたは固定 fixture として扱ってもよい。

---

## 3. セキュリティ設計方針

### 3.1 目標

本 API はポートフォリオ用途であるが、**実サービス相当の堅牢性を意識して設計する**。

### 3.2 認証方式

Phase1 では以下を採用する。

- HTTP-only Cookie
- Secure Cookie（HTTPS 前提環境では必須）
- SameSite=Lax を標準
- サーバーサイドセッション
- Cookie には session token の平文ではなく識別子のみを持たせ、DB では hash 管理する

### 3.3 CORS / credentials 方針

Phase1 は **同一 site 前提** を強く採用する。

推奨構成:

- フロントエンドとバックエンドは同一 site 配下
- 開発時は同一 site 別 port を許容
- cross-site cookie を前提としない

方針:

- `Access-Control-Allow-Credentials: true`
- `Access-Control-Allow-Origin` は **固定 allowlist のみ**
- `*` は使用禁止
- フロントエンド fetch は `credentials: include` を必須とする
- local 開発で HTTPS でない場合の Secure Cookie 挙動は環境別設定で制御する

### 3.4 認可方式

- RBAC を採用
- ロールは `admin` / `viewer`
- API ごとに必要ロールを明記する
- `admin` 専用操作はサーバー側で必ず検証する

### 3.5 入力検証

- 全入力は Pydantic でバリデーションする
- 文字列長、列挙値、数値範囲、UUID / 整数形式を厳密に検証する
- フロント側で検証していても、サーバー側で再検証する

### 3.6 CSRF 対策

Cookie 認証を使うため、状態変更系 API では CSRF 対策を必須とする。

採用方針:

- 専用 CSRF 発行 API を持つ
- CSRF token は `X-CSRF-Token` ヘッダで送信する
- SameSite Cookie を併用する
- Origin / Referer 検証を行う
- **login / logout を含む state-changing API 全体で CSRF を必須** とする

#### CSRF フロー

1. 初回ロード時に `GET /api/v1/auth/csrf` で取得
2. ログイン成功後に再取得
3. token を state-changing API に `X-CSRF-Token` で付与
4. logout 時に session と CSRF state を失効する

### 3.7 レート制限

- login API は IP / email 単位でレート制限する
- RAG API は user 単位でレート制限する
- upload API は user 単位でレート制限する
- 制限超過時は `429 rate_limit_exceeded`

### 3.8 request_id

- すべての API response に `request_id` を返す
- request_id は middleware で生成する
- 外部API呼び出し、監査ログ、アプリケーションログに伝播する
- クライアントから `X-Request-ID` が送られた場合でも、採用可否はサーバー側で検証する

### 3.9 PII 保護

- API response に不要な PII を含めない
- audit / logs / trace には PII をマスクして保存する
- raw file content はログに出さない
- prompt / context / retrieved chunks は必要最小限のみ外部 LLM に渡す
- 再識別が必要な場合は専用 mapping store に隔離する

### 3.10 アップロード防御

ファイルアップロード API では以下を行う。

- 拡張子検証
- MIME type 検証
- magic bytes / file signature 検証
- file size limit
- compression bomb 対策
- 実行可能ファイル拒否
- raw file name の sanitize
- storage path traversal 防止

### 3.11 prompt injection 防御

RAG context は信頼できない入力として扱う。

- retrieved context と system instruction を分離する
- context 内の命令を system instruction として扱わない
- 外部APIキーや内部設定を回答に含めない
- 管理系操作を RAG 応答から直接実行しない

### 3.12 ユーザー列挙耐性

login / password 関連 API では、email 存在有無を推測できない応答にする。

### 3.13 セッション再生成と失効

- login 成功時に session identifier を再生成する
- logout 時に DB session を失効する
- Cookie を削除する
- session fixation を防ぐ

---

## 4. 認証・認可共通仕様

### 4.1 認証ヘッダ / Cookie

- Cookie 認証を標準とする
- API client 用 token は Phase1 では正式対象外
- 将来 CLI 用 token を追加可能とする

### 4.2 共通レスポンスヘッダ

- `X-Request-ID`
- `Cache-Control`
- 必要に応じて `X-CSRF-Token-Required`

### 4.3 認証エラー

未認証の場合:

```json
{
  "error": {
    "code": "auth_required",
    "message": "Authentication required."
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 4.4 403 / 404 の使い分け

所有物秘匿のため、以下を原則とする。

- admin 専用 API に viewer がアクセスした場合: `403 permission_denied`
- viewer が他人の chat session を指定した場合: `404 resource_not_found`
- viewer が他人の message を指定した場合: `404 resource_not_found`
- viewer が standalone retrieval run を指定した場合: `404 resource_not_found`
- 存在しない resource: `404 resource_not_found`

### 4.5 same-session 横断ルール

以下は同一 `chat_session_id` 内に属していることを API service 層で必ず検証する。

- `chat_messages`
- `retrieval_runs`
- `assistant_message.linked_retrieval_run_id`
- edit lineage
- summary memory source message

DB で composite FK を持つ場合も、API service 層の検証を省略しない。

---

## 5. 共通レスポンス形式

### 5.1 成功レスポンス

```json
{
  "data": {},
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 5.2 一覧レスポンス

```json
{
  "data": [],
  "meta": {
    "request_id": "req_xxx",
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 100,
      "has_next": true
    }
  }
}
```

### 5.3 エラーレスポンス

```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid request.",
    "details": [
      {
        "field": "title",
        "reason": "must not be empty"
      }
    ]
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 5.4 エラーコード方針

代表的な error code は以下とする。

| error_code | HTTP | 用途 |
|---|---:|---|
| `authentication_failed` | 401 | 認証失敗 |
| `auth_required` | 401 | 未認証 |
| `permission_denied` | 403 | 権限不足 |
| `csrf_missing` | 403 | CSRF token 未送信 |
| `csrf_invalid` | 403 | CSRF token 不正 |
| `resource_not_found` | 404 | 対象なし、または所有物秘匿 |
| `validation_error` | 422 | 入力値不正 |
| `business_validation_error` | 422 | 汎用的な業務条件不成立 |
| `conflict` | 409 | リソース状態競合 |
| `request_in_progress` | 409 | 同一 client_message_id の処理中 |
| `client_message_conflict` | 409 | 同一 client_message_id に異なる request body |
| `archived_session_readonly` | 409 | archived session への更新操作 |
| `document_archived` | 409 | archived document への更新操作 |
| `document_version_not_approvable` | 409 | 承認可能状態ではない version の approve |
| `active_version_conflict` | 409 | active version 切替競合 |
| `job_not_ready` | 409 | job が操作可能状態ではない |
| `job_active_retry_exists` | 409 | 同一 source job に active retry が存在 |
| `unsafe_file_rejected` | 415 | 危険ファイル拒否 |
| `payload_too_large` | 413 | ファイルサイズ超過 |
| `no_context_found` | 422 | RDB final check 後に回答根拠が存在しない |
| `citation_build_failed` | 500 | citation 生成不能。InternalPipelineError として扱う |
| `retrieval_failed` | 503 | retrieval pipeline failure |
| `rerank_failed` | 503 | rerank pipeline failure |
| `generation_failed` | 503 | generation pipeline failure |
| `external_dependency_unavailable` | 503 | 外部依存障害 |
| `external_dependency_timeout` | 503 | 外部依存 timeout |
| `rate_limit_exceeded` | 429 | レート制限 |
| `internal_error` | 500 | 予期しない内部エラー |

### 5.5 エラー分類ルール

#### business_validation_error

`business_validation_error` は汎用 fallback とする。

既知の業務エラーでは、可能な限り個別 error code を優先する。

例:

- approve 不能: `document_version_not_approvable`
- 根拠不足: `no_context_found`
- active retry 競合: `job_active_retry_exists`

#### pipeline failure

`retrieval_failed` / `rerank_failed` / `generation_failed` は、外部依存または一時的処理不能を含む pipeline failure として扱い、Phase1 では HTTP 503 に寄せる。

`citation_build_failed` は、LLM 出力の citation marker validation や citation 生成処理の内部検証失敗であるため、外部依存障害ではなく `InternalPipelineError` として HTTP 500 を返す。

### 5.6 成功系の特殊ステータス

重複スキップや no-op など、正常系だが通常作成・更新が発生しないケースは成功レスポンスで返す。

| status / result_code | HTTP | 用途 |
|---|---:|---|
| `duplicate_content_skipped` | 200 | 同一 content_hash の version 作成スキップ |
| `already_archived` | 200 | 既に archived の対象に archive を再実行 |
| `already_active` | 200 | 既に active の document version に approve を再実行 |
| `already_exists` | 200 | 既に同一 tag 等が存在する no-op success |
| `no_op` | 200 | 状態変更不要 |

`document_already_archived` のような archive no-op 用 error code は使用しない。

---

## 6. ステータスコード方針

- `200 OK`: 取得・更新成功、または正常系スキップ
- `201 Created`: 新規作成成功
- `202 Accepted`: 非同期処理受理
- `204 No Content`: 削除成功など
- `400 Bad Request`: リクエスト不正
- `401 Unauthorized`: 未認証
- `403 Forbidden`: 権限不足
- `404 Not Found`: 対象なし、または所有物秘匿
- `409 Conflict`: 競合
- `413 Payload Too Large`: ファイルサイズ超過
- `415 Unsupported Media Type`: MIME 不正
- `422 Unprocessable Entity`: バリデーション失敗、または業務条件不成立
- `429 Too Many Requests`: レート制限
- `500 Internal Server Error`: 予期しないエラー、または内部 pipeline 検証失敗
- `503 Service Unavailable`: 外部依存利用不可、または一時的 pipeline failure

---

## 7. リソース識別子方針

### 7.1 外部公開 ID

外部公開用 ID は内部 BIGINT をそのまま出しても Phase1 ではよいが、将来的には ULID / UUID 化可能な設計とする。

Phase1 方針:

- JSON では整数 ID を返却してよい
- 外部公開 API の汎用性向上が必要な場合は Phase2 以降で opaque id 化を検討する

### 7.2 ID の秘匿方針

- ID が推測可能であっても、認可チェックで必ず保護する
- 所有物不一致は 404 を返す
- admin 専用 API では 403 と 404 の使い分けを明確にする

---

## 8. 認証 API

## 8.1 GET /api/v1/auth/csrf

### 目的

CSRF token を発行または再発行する。

### 認証

不要。

ただしログイン済みの場合は、現在の session に紐づく CSRF state を返す。

### レスポンス

```json
{
  "data": {
    "csrf_token": "csrf_xxx"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 補足

- response には `Cache-Control: no-store` を付与する
- pre-auth CSRF state は login 成功時に失効する
- login 成功後は session-bound CSRF state に切り替える

---

## 8.2 POST /api/v1/auth/login

### 目的

email / password によりログインする。

### 認証

不要。

### CSRF

必須。

### リクエスト

```json
{
  "email": "user@example.com",
  "password": "password"
}
```

### バリデーション

- `email`: 必須、lowercase / trim 後に検証
- `password`: 必須、空文字不可

### レスポンス

```json
{
  "data": {
    "user": {
      "user_id": 1,
      "email": "user@example.com",
      "display_name": "User",
      "role": "admin"
    },
    "csrf_token": "csrf_xxx"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### Cookie

- session cookie を HTTP-only で発行する
- Secure / SameSite=Lax を標準とする

### 補足

- login 成功時に session identifier を再生成する
- pre-auth CSRF state / cookie を失効する
- login 成功後に session-bound CSRF state を発行し、response body の `csrf_token` で返す
- last_login_at を更新する

### 監査

- 成功 / 失敗とも監査対象とする
- 失敗理由は user enumeration につながらない粒度で保存する

### エラー

- `401 authentication_failed`
- `403 csrf_invalid`
- `429 rate_limit_exceeded`

---

## 8.3 POST /api/v1/auth/logout

### 目的

現在の session を失効する。

### 認証

必要。

### CSRF

必須。

### レスポンス

```json
{
  "data": {
    "status": "logged_out"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 補足

- DB session を失効する
- session cookie を削除する
- CSRF state を失効する

### 監査

- 監査対象

---

## 8.4 GET /api/v1/auth/me

### 目的

現在ログイン中のユーザー情報を取得する。

### 認証

必要。

### レスポンス

```json
{
  "data": {
    "user_id": 1,
    "email": "user@example.com",
    "display_name": "User",
    "role": "admin"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 9. ユーザー / 設定 API

### 9.0 保存先方針

ユーザー個別設定は `user_settings` に保存する。

### 9.0.1 Phase1 の正式設定項目

- `ui_theme`
- `memory_message_limit`

## 9.1 GET /api/v1/users/me/settings

### 目的

ログインユーザー自身の設定を取得する。

### 認証

必要。

### 応答方針

ログインユーザー本人の設定のみ返す。

### レスポンス

```json
{
  "data": {
    "ui_theme": "system",
    "memory_message_limit": 8
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 9.2 PATCH /api/v1/users/me/settings

### 目的

ログインユーザー自身の設定を更新する。

### 認証

必要。

### CSRF

必須。

### リクエスト

```json
{
  "ui_theme": "dark",
  "memory_message_limit": 8
}
```

### バリデーション

- `ui_theme`: `light` / `dark` / `system`
- `memory_message_limit`: 1〜50

---

## 10. 会話 API

## 10.1 POST /api/v1/chat/sessions

### 目的

新しい会話 session を作成する。

### 認証

必要。

### CSRF

必須。

### リクエスト

```json
{
  "title": "新しい会話",
  "temporary_flag": false
}
```

### バリデーション

- `title`: 省略可。省略時はサーバーが仮タイトルを補完
- `temporary_flag`: 省略時 false

### 補足

- temporary chat の場合は `ttl_expires_at` を設定する
- temporary chat は履歴一覧に表示しない

### レスポンス

```json
{
  "data": {
    "chat_session_id": 10,
    "title": "新しい会話",
    "status": "active",
    "display_status": "active",
    "mode": "active",
    "temporary_flag": false,
    "ttl_expires_at": null,
    "created_at": "2026-04-30T00:00:00Z",
    "updated_at": "2026-04-30T00:00:00Z",
    "tags": []
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 10.2 GET /api/v1/chat/sessions

### 目的

会話 session 一覧を取得する。

### 認証

必要。

### クエリ

| name | required | 説明 |
|---|---:|---|
| `status` | no | `active` / `archived` |
| `q` | no | title 部分一致 |
| `page` | no | 既定 1 |
| `page_size` | no | 既定 20、最大 100 |

### レスポンス

```json
{
  "data": [
    {
      "chat_session_id": 10,
      "title": "新しい会話",
      "status": "active",
      "display_status": "active",
      "mode": "active",
      "temporary_flag": false,
      "ttl_expires_at": null,
      "created_at": "2026-04-30T00:00:00Z",
      "updated_at": "2026-04-30T00:00:00Z"
    }
  ],
  "meta": {
    "request_id": "req_xxx",
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 1,
      "has_next": false
    }
  }
}
```

### セキュリティ

- owner の session のみ返す
- admin であっても通常一覧では他ユーザーの session は返さない

---

## 10.3 GET /api/v1/chat/sessions/{chat_session_id}

### 目的

会話 session 詳細を取得する。

### 認証

必要。

### 取得ルール

- owner のみ取得可能
- owner 不一致は 404
- archived session も参照可能
- temporary session は TTL 切れでも参照可能とし、`display_status = temporary_expired` を返す

### レスポンス

```json
{
  "data": {
    "chat_session_id": 10,
    "title": "新しい会話",
    "status": "active",
    "display_status": "active",
    "mode": "active",
    "temporary_flag": false,
    "ttl_expires_at": null,
    "created_at": "2026-04-30T00:00:00Z",
    "updated_at": "2026-04-30T00:00:00Z",
    "tags": []
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 10.4 GET /api/v1/chat/sessions/{chat_session_id}/messages

### 目的

会話 message 一覧を取得する。

### 認証

必要。

### 取得ルール

- owner の session のみ取得可能
- owner 不一致は 404
- archived session も参照可能
- temporary session は TTL 切れでも参照可能。ただし更新系 API は `409 temporary_session_expired`

### クエリ

| name | required | 説明 |
|---|---:|---|
| `page` | no | 既定 1 |
| `page_size` | no | 既定 20、最大 100 |
| `include_internal_lineage` | no | 既定 false。true は admin debug 用 |

### レスポンス

```json
{
  "data": [
    {
      "chat_message_id": 101,
      "role": "user",
      "content": "RAGの評価方針を教えてください",
      "client_message_id": "msg_cli_001",
      "edited_flag": false,
      "created_at": "2026-04-30T00:00:00Z"
    },
    {
      "chat_message_id": 102,
      "role": "assistant",
      "content": "評価方針は...",
      "client_message_id": null,
      "edited_flag": false,
      "created_at": "2026-04-30T00:00:05Z"
    }
  ],
  "meta": {
    "request_id": "req_xxx",
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 2,
      "has_next": false
    }
  }
}
```

### 補足

- user message のみ `client_message_id` を持つ
- 通常レスポンスでは `linked_retrieval_run_id` などの internal lineage は返さない
- `include_internal_lineage=true` は admin debug 用とし、通常 UI では利用しない
- viewer が `include_internal_lineage=true` を指定した場合は `403 permission_denied`
- failure 時の assistant placeholder は Phase1 では保存しない

---

## 10.5 PATCH /api/v1/chat/sessions/{chat_session_id}

### 目的

会話 session のタイトル等を更新する。

### 認証

必要。

### CSRF

必須。

### リクエスト

```json
{
  "title": "更新後タイトル"
}
```

### バリデーション

- title は空文字不可
- archived session は更新不可

---

## 10.6 POST /api/v1/chat/sessions/{chat_session_id}/tags

### 目的

会話 session に tag を追加する。

### 認証

必要。

### CSRF

必須。

### リクエスト

```json
{
  "tag_name": "portfolio"
}
```

### 新規作成レスポンス

`201 Created`

```json
{
  "data": {
    "chat_session_id": 10,
    "tag_name": "portfolio",
    "result_code": "created"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 重複追加レスポンス

同一 `chat_session_id + tag_name` が既に存在する場合、Phase1 では no-op success とする。

`200 OK`

```json
{
  "data": {
    "chat_session_id": 10,
    "tag_name": "portfolio",
    "result_code": "already_exists"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- `tag_name` は trim 後に空文字不可
- `tag_name` は `/` と `\` を含めない。削除 API が path parameter であるため、path separator は禁止する
- 重複 tag 追加は `409` にしない
- archived session への tag 追加は `409 archived_session_readonly`

---

## 10.7 DELETE /api/v1/chat/sessions/{chat_session_id}/tags/{tag_name}

### 目的

会話 session から tag を削除する。

### 認証

必要。

### CSRF

必須。

### ルール

- 存在しない tag の削除は no-op success とする
- archived session への tag 削除は `409 archived_session_readonly`

### レスポンス

`200 OK`

```json
{
  "data": {
    "chat_session_id": 10,
    "tag_name": "portfolio",
    "result_code": "deleted"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 10.8 POST /api/v1/chat/sessions/{chat_session_id}/archive

### 目的

会話 session を archived にする。

### 認証

必要。

### CSRF

必須。

### レスポンス

```json
{
  "data": {
    "chat_session_id": 10,
    "status": "archived",
    "result_code": "archived"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 冪等性

- すでに archived の場合も `200 OK`
- `result_code = already_archived` を返す

### 補足

- temporary session は archive 対象外
- temporary session は TTL によって物理削除する

---

## 10.9 POST /api/v1/chat/messages/{chat_message_id}/edit

### 目的

既存 user message を編集し、回答再生成 job を作成する。

### 認証

必要。

### CSRF

必須。

### リクエスト

```json
{
  "content": "修正後の質問本文"
}
```

### ルール

- user message のみ編集可能
- assistant message は編集不可
- archived session の message は編集不可
- 編集後の再生成は async job とする
- old lineage は内部的に保持し、通常 UI では非表示
- debug view では lineage を表示可能にしてよい

### 実行方式

- API は `202 Accepted` を返す
- job_type は `message_edit_regeneration`
- job payload には対象 `chat_message_id` と `chat_session_id` を含める
- job 成功後に新 assistant message を保存する

### レスポンス

```json
{
  "data": {
    "chat_message_id": 101,
    "status": "edit_accepted",
    "job_id": 400
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### クライアント挙動

- user message は即時更新表示してよい
- 再生成中 banner を表示してよい
- job 成功後に assistant response を差し替える

### 監査

- 編集操作は監査対象

---

## 11. 文書 API

## 11.1 POST /api/v1/documents

### 目的

新しい logical document と最初の document version を作成し、ingest job を登録する。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### Content-Type

- `multipart/form-data`

### フィールド

| field | required | 説明 |
|---|---:|---|
| `file` | yes | アップロード対象ファイル |
| `document_name` | no | 未指定の場合はサーバー側で file name から補完 |

### Phase1 対象拡張子

| extension | Phase1 | 備考 |
|---|---:|---|
| `.pdf` | yes | text layer 抽出対象 |
| `.txt` | yes | plain text |
| `.md` | yes | markdown text |
| `.png` / `.jpg` / `.jpeg` | no | OCR は Phase3 |
| `.docx` | yes | Word text |
| `.xlsx` | yes | Excel sheet / row text, macro-enabled files are rejected |
| `.pptx` | yes | PowerPoint slide text, OCR is not performed |

### バリデーション

- MIME type 検証
- 拡張子検証
- file signature / magic bytes 検証
- サイズ上限検証
- 許可形式のみ受理
- dangerous file rejection
- compression bomb 対策
- content_hash 計算

### 通常レスポンス

`202 Accepted`

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_version_id": 2000,
    "job_id": 200,
    "ingest_status": "queued",
    "version_status": "processing",
    "display_status": "processing"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- `source_type` は API 入力で受けず、サーバー側で `upload` に固定する
- URL 取込などは将来別 endpoint として追加する
- file validation / content_hash / duplicate check 成功後に `document_versions.status = processing` を作成する
- ingest job は `queued` として作成する
- queued job 作成時、`jobs.started_at` は `null` とする
- `pending_review` は DB status として作成しない
- 取り込み完了後、worker が `document_versions.status = ready` に更新する
- 取り込み直後の ready version は `is_active = false` とし、承認待ち扱いにする
- API response では `display_status = pending_review` として表示可能にする

---

## 11.2 POST /api/v1/documents/{logical_document_id}/versions

### 目的

既存 logical document に対して新しい document version を追加し、ingest job を登録する。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### Content-Type

- `multipart/form-data`

### フィールド

| field | required | 説明 |
|---|---:|---|
| `file` | yes | 新 version として取り込むファイル |

### 通常レスポンス

`202 Accepted`

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_version_id": 2001,
    "job_id": 201,
    "ingest_status": "queued",
    "version_status": "processing",
    "display_status": "processing"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### duplicate content レスポンス

同一 `logical_document_id + content_hash` が既に存在する場合、新しい document version と ingest job は作成しない。

`200 OK`

```json
{
  "data": {
    "status": "duplicate_content_skipped",
    "reason": "duplicate_content",
    "logical_document_id": 1000,
    "matched_document_version_id": 2000,
    "matched_version_no": 1
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- 同一内容への revert は Phase1 では新 version として扱わない
- duplicate skip は error ではなく成功系 status とする
- archived logical document への version 追加は `409 conflict`
- file validation failure では document_version / ingest job を作成しない

---

## 11.3 GET /api/v1/documents

### 目的

logical document 一覧を取得する。

### 認証

必要

### 権限

- `admin`

### クエリ

| name | required | 説明 |
|---|---:|---|
| `status` | no | `active` / `archived` |
| `display_status` | no | `processing` / `pending_review` / `active` / `failed` / `archived` |
| `q` | no | document_name 部分一致 |
| `page` | no | 既定 1 |
| `page_size` | no | 既定 20、最大 100 |

### レスポンス

```json
{
  "data": [
    {
      "logical_document_id": 1000,
      "document_name": "rag_design.md",
      "status": "active",
      "latest_version": {
        "document_version_id": 2001,
        "version_no": 2,
        "status": "ready",
        "is_active": false,
        "display_status": "pending_review",
        "created_at": "2026-04-30T00:00:00Z",
        "updated_at": "2026-04-30T00:10:00Z"
      },
      "active_version": null,
      "updated_at": "2026-04-30T00:10:00Z"
    }
  ],
  "meta": {
    "request_id": "req_xxx",
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 1,
      "has_next": false
    }
  }
}
```

### latest_version 定義

`latest_version` は、同一 `logical_document_id` 配下で `version_no` が最大の `document_versions` を指す。

`created_at` が最新の version ではなく、`version_no` 最大を正とする。

### active_version nullability

`active_version` は nullable とする。

まだ approve 済み version が存在しない場合、`active_version = null` を返す。

例:

```json
{
  "latest_version": {
    "status": "ready",
    "is_active": false,
    "display_status": "pending_review"
  },
  "active_version": null
}
```

active version が存在する場合は以下のように返す。

```json
{
  "active_version": {
    "document_version_id": 2000,
    "version_no": 1,
    "status": "ready",
    "is_active": true,
    "display_status": "active"
  }
}
```

### display_status 導出規則

`display_status` は以下の優先順位で導出する。

| 優先 | 条件 | display_status |
|---:|---|---|
| 1 | `logical_documents.status = 'archived'` | `archived` |
| 2 | `document_versions.status = 'failed'` | `failed` |
| 3 | `document_versions.status = 'processing'` | `processing` |
| 4 | `document_versions.status = 'ready' AND document_versions.is_active = true` | `active` |
| 5 | `document_versions.status = 'ready' AND document_versions.is_active = false` | `pending_review` |

logical document が archived の場合、配下 version が failed / processing / ready のいずれであっても、表示上は `archived` を優先する。

### display_status filter 条件変換

`display_status` は DB column ではないため、API service 層で DB 条件に変換して適用する。

| display_status filter | 条件変換 |
|---|---|
| `archived` | `logical_documents.status = 'archived'` |
| `failed` | `logical_documents.status = 'active' AND document_versions.status = 'failed'` |
| `processing` | `logical_documents.status = 'active' AND document_versions.status = 'processing'` |
| `active` | `logical_documents.status = 'active' AND document_versions.status = 'ready' AND document_versions.is_active = true` |
| `pending_review` | `logical_documents.status = 'active' AND document_versions.status = 'ready' AND document_versions.is_active = false` |

---

## 11.4 GET /api/v1/documents/{logical_document_id}

### 目的

logical document 詳細を取得する。

### 認証

必要

### 権限

- `admin`

### レスポンス

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_name": "rag_design.md",
    "status": "active",
    "created_at": "2026-04-30T00:00:00Z",
    "updated_at": "2026-04-30T00:10:00Z",
    "active_version": null,
    "versions": [
      {
        "document_version_id": 2001,
        "version_no": 2,
        "status": "ready",
        "is_active": false,
        "display_status": "pending_review",
        "file_name": "rag_design.md",
        "mime_type": "text/markdown",
        "file_size_bytes": 12345,
        "content_hash": "<sha256_hex_64>",
        "page_count": null,
        "chunk_count": 20,
        "error_code": null,
        "created_at": "2026-04-30T00:05:00Z",
        "updated_at": "2026-04-30T00:10:00Z"
      }
    ]
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- `pending_review` は response の `display_status` としてのみ返す
- `status` には DB 永続状態のみ返す
- version 単位の `display_status` も logical document の archived 状態を考慮して導出する
- `active_version` は nullable とする
- approve 済み version が存在しない場合、`active_version = null` を返す
- chunk の raw text はこの endpoint では返さない
- `content_hash` を返す場合は admin 限定とする
- `content_hash` は lowercase hex 64文字を想定し、仕様例では `<sha256_hex_64>` と表記する

---

## 11.5 GET /api/v1/documents/{logical_document_id}/versions/{document_version_id}

### 目的

document version 詳細を取得する。

### 認証

必要

### 権限

- `admin`

### レスポンス

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_version_id": 2001,
    "version_no": 2,
    "status": "ready",
    "is_active": false,
    "display_status": "pending_review",
    "file_name": "rag_design.md",
    "mime_type": "text/markdown",
    "file_size_bytes": 12345,
    "content_hash": "<sha256_hex_64>",
    "page_count": null,
    "chunk_count": 20,
    "error_code": null,
    "created_at": "2026-04-30T00:05:00Z",
    "updated_at": "2026-04-30T00:10:00Z"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- nested path の `logical_document_id` と `document_version_id` の親子整合を検証する
- 不整合時は 404
- failed version の場合は `error_code` を返す
- failed 以外では `error_code = null` とする
- 親 logical document が archived の場合は `display_status = archived` を返す

---

## 11.6 POST /api/v1/documents/{logical_document_id}/versions/{document_version_id}/approve

### 目的

承認待ち document version を active version に切り替える。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### 承認可能条件

承認可能な version は以下をすべて満たす。

```text
logical_documents.status = 'active'
AND document_versions.status = 'ready'
AND document_versions.is_active = false
```

### 成功レスポンス

`200 OK`

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_version_id": 2001,
    "version_no": 2,
    "status": "ready",
    "is_active": true,
    "display_status": "active",
    "previous_active_document_version_id": null,
    "result_code": "approved"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### already active レスポンス

対象 version が既に active の場合は no-op success とする。

`200 OK`

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_version_id": 2001,
    "version_no": 2,
    "status": "ready",
    "is_active": true,
    "display_status": "active",
    "result_code": "already_active"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### エラー

| 条件 | HTTP | error.code |
|---|---:|---|
| logical document が存在しない | 404 | `resource_not_found` |
| version が logical document 配下ではない | 404 | `resource_not_found` |
| logical document が archived | 409 | `document_archived` |
| version が processing | 409 | `document_version_not_approvable` |
| version が failed | 409 | `document_version_not_approvable` |
| active version 切替競合 | 409 | `active_version_conflict` |

### ルール

- 承認時は同一 logical document 配下の既存 active version を `is_active = false` にする
- 対象 version を `is_active = true` にする
- active version は 1 logical document あたり 1 件のみ
- 初回 approve では `previous_active_document_version_id = null` になり得る
- failed version は approve 対象外
- processing version は approve 対象外
- `pending_review` という DB status への更新は行わない

---

## 11.7 POST /api/v1/documents/{logical_document_id}/reindex

### 目的

active version または指定 version を再インデックスする。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### リクエスト

```json
{
  "document_version_id": 2001
}
```

### レスポンス

`202 Accepted`

```json
{
  "data": {
    "logical_document_id": 1000,
    "document_version_id": 2001,
    "job_id": 250,
    "status": "queued"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 対象 version ごとの挙動

#### ready version の reindex

ready version の reindex は、同一 `document_version_id` に対して embedding / Qdrant upsert を再実行する。

成功後も `document_versions.status = ready` を維持する。

active version の reindex であっても、reindex 中に検索対象から外すかどうかは Worker / Job 詳細設計で定義する。ただし Phase1 では、既存 Qdrant point を維持しつつ、成功時に upsert で置き換える方針を推奨する。

#### failed version の retry

failed version の reindex retry は、同一 `document_version_id` に対して再実行する。

開始時に以下へ戻す。

```text
document_versions.status = 'processing'
document_versions.error_code = null
```

成功時は以下へ更新する。

```text
document_versions.status = 'ready'
document_versions.error_code = null
document_versions.is_active = false
```

failed retry 成功後は、自動 active 化せず、approve API により active version へ昇格する。

### ルール

- `document_version_id` 未指定時は active version を対象にする
- active version が存在しない場合、`document_version_id` 未指定 reindex は `409 document_version_not_approvable` とする
- failed version の retry は同一 `document_version_id` に対して再実行する
- archived logical document の reindex は `409 conflict`
- queued job 作成時、`jobs.started_at` は `null` とする

---

## 11.8 POST /api/v1/documents/{logical_document_id}/archive

### 目的

logical document を archive し、retrieval 対象外にする。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### 成功レスポンス

`200 OK`

```json
{
  "data": {
    "logical_document_id": 1000,
    "status": "archived",
    "result_code": "archived",
    "retrieval_eligible": false
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### already archived レスポンス

`200 OK`

```json
{
  "data": {
    "logical_document_id": 1000,
    "status": "archived",
    "result_code": "already_archived",
    "retrieval_eligible": false
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- archive は logical document 単位を正とする
- archive 後は関連 active version を `is_active = false` にする
- archive 後の文書は retrieval 対象外とする
- Qdrant payload の `is_active` は mirror として更新を試みる
- Qdrant mirror 更新に失敗しても、retrieval final check では RDB 判定を正とし、archived document を除外する
- archive no-op は error ではなく success とする

### 監査

- 監査対象

---

## 11.9 DELETE /api/v1/documents/{logical_document_id}

### 目的

文書を物理削除する。

### 認証

必要

### 権限

- `admin`

### 条件

- `retrieval_run_items` / `citations` / 評価系から未参照であること
- 一度でも回答根拠や評価に使われた文書は原則削除不可

### レスポンス

- 成功時 `204 No Content`
- 参照済みで削除不可なら `409 conflict`

### 監査

- 強い操作として必ず監査対象

---

## 12. RAG API

## 12.1 POST /api/v1/rag/ask

### 目的

chat session に紐づく RAG 質問を実行し、回答、citation、confidence、retrieval trace を保存する。

### 認証

必要

### 権限

- `viewer`
- `admin`

### CSRF

必須

### リクエスト

```json
{
  "chat_session_id": 10,
  "message": "この設計書でRAGの評価方針はどうなっていますか？",
  "client_message_id": "uuid-or-client-generated-id",
  "top_k": 10,
  "filters": {
    "logical_document_ids": [1000],
    "modality": "text"
  }
}
```

### バリデーション

| field | rule |
|---|---|
| `chat_session_id` | 必須 |
| `message` | 必須、空文字不可、最大 8000 文字 |
| `client_message_id` | 必須、最大長 255、空文字不可、同一 session 内 user message で一意 |
| `top_k` | 省略可、1〜20 |
| `filters.logical_document_ids` | 省略可 |
| `filters.modality` | Phase1 は `text` のみ |

### 重複送信対策

- `client_message_id` により同一 session 内の重複送信を防ぐ
- duplicate 判定は **user message 保存前** に行う
- duplicate request では **新しい user message を作らない**
- **成功済み + 同一本文** の場合は `200 OK` で既存結果を返す
- **実行中** の場合は `409 request_in_progress` を返す
- **失敗済み + 同一本文** の場合は、同じ `client_message_id` では再実行せず `409 conflict` を返し、クライアントは **新しい `client_message_id`** で再送する
- **同じ `client_message_id` だが本文が異なる** 場合は `409 conflict` を返す

### 処理概要

1. request validation
2. chat/session validation
3. duplicate/replay check
4. user message + retrieval_run creation
5. query preprocessing
6. vector retrieval
7. RDB active/archive final check
8. no_context check
9. rerank
10. context selection
11. PII masking / context minimization
12. generation
13. response parsing / citation marker validation
14. citation generation
15. confidence calculation
16. final transaction
17. response

### partial failure 方針

- user message は先に保存する
- retrieval_run は失敗時も監査 / デバッグ用に残す
- assistant message は **成功時のみ** 保存する
- 失敗時は `503` / `500` / `422` を返し、クライアントは user message のみ表示する
- failed assistant placeholder は Phase1 では保存しない

### 失敗時の会話表示ルール

- 失敗した request の user message は **通常の message として保持し、`/messages` に通常表示する**
- Phase1 では user message 自体に失敗状態列は持たず、UI は直近の ask API 応答・request_id・必要に応じたジョブ/監査情報に基づいて失敗表示してよい
- 再送時は **新しい `client_message_id` を持つ新しい user message** が追加される
- 旧 failed request と新 request を自動結合する API は Phase1 では持たない

### 成功レスポンス

`200 OK`

```json
{
  "data": {
    "chat_session_id": 10,
    "user_message": {
      "chat_message_id": 101,
      "role": "user",
      "content": "この設計書でRAGの評価方針はどうなっていますか？",
      "client_message_id": "uuid-or-client-generated-id",
      "created_at": "2026-04-30T00:00:00Z"
    },
    "assistant_message": {
      "chat_message_id": 102,
      "role": "assistant",
      "content": "評価方針は、faithfulness / groundedness と citation coverage を中心に確認します...",
      "linked_retrieval_run_id": 500,
      "created_at": "2026-04-30T00:00:05Z"
    },
    "retrieval_run": {
      "retrieval_run_id": 500,
      "status": "succeeded",
      "retrieval_score_summary": {
        "candidate_count": 10,
        "post_final_check_count": 8,
        "selected_count": 4,
        "excluded_count": 2,
        "top1_retrieval_score": 0.912345,
        "top3_avg_retrieval_score": 0.876543,
        "top1_rerank_score": 0.834567
      },
      "rerank_score_top1": 0.834567,
      "answer_confidence": 0.78,
      "groundedness_score": 0.82,
      "confidence_label": "High"
    },
    "citations": [
      {
        "citation_id": 1,
        "document_chunk_id": 3001,
        "rank_order": 1,
        "snippet": "評価では faithfulness / groundedness と citation coverage を確認する...",
        "page_from": 12,
        "page_to": 12,
        "source_type": "upload",
        "source_url": null,
        "display_label": "RAGパイプライン詳細設計書 p.12",
        "old_version_flag": false
      }
    ],
    "meta": {
      "replayed": false
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### replay レスポンス

同一 `chat_session_id + client_message_id` かつ同一 request body の成功済み request が存在する場合、保存済みの既存結果を返す。

replay response は、初回成功時と同等の `assistant_message` / `retrieval_run` / `citations` を返す。

初回成功時との差分は `data.meta.replayed = true` のみとする。

`200 OK`

```json
{
  "data": {
    "chat_session_id": 10,
    "user_message": {
      "chat_message_id": 101,
      "role": "user",
      "content": "この設計書でRAGの評価方針はどうなっていますか？",
      "client_message_id": "uuid-or-client-generated-id",
      "created_at": "2026-04-30T00:00:00Z"
    },
    "assistant_message": {
      "chat_message_id": 102,
      "role": "assistant",
      "content": "評価方針は、faithfulness / groundedness と citation coverage を中心に確認します...",
      "linked_retrieval_run_id": 500,
      "created_at": "2026-04-30T00:00:05Z"
    },
    "retrieval_run": {
      "retrieval_run_id": 500,
      "status": "succeeded",
      "retrieval_score_summary": {
        "candidate_count": 10,
        "post_final_check_count": 8,
        "selected_count": 4,
        "excluded_count": 2,
        "top1_retrieval_score": 0.912345,
        "top3_avg_retrieval_score": 0.876543,
        "top1_rerank_score": 0.834567
      },
      "rerank_score_top1": 0.834567,
      "answer_confidence": 0.78,
      "groundedness_score": 0.82,
      "confidence_label": "High"
    },
    "citations": [
      {
        "citation_id": 1,
        "document_chunk_id": 3001,
        "rank_order": 1,
        "snippet": "評価では faithfulness / groundedness と citation coverage を確認する...",
        "page_from": 12,
        "page_to": 12,
        "source_type": "upload",
        "source_url": null,
        "display_label": "RAGパイプライン詳細設計書 p.12",
        "old_version_flag": false
      }
    ],
    "meta": {
      "replayed": true
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### no_context_found

RDB final check 後に回答に使える候補が 0 件の場合は、HTTP 422 とする。

`422 Unprocessable Entity`

```json
{
  "error": {
    "code": "no_context_found",
    "message": "回答に必要な根拠が見つかりませんでした。",
    "details": {
      "chat_session_id": 10,
      "retrieval_run_id": 501
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### no_context_found 時の保存方針

- user message は保存する
- retrieval_run は保存する
- retrieval_run.status は `failed` とする
- retrieval_run.error_code は `no_context_found` とする
- retrieval_run.finished_at を保存する
- assistant message は作成しない
- citations は作成しない
- retrieval_run_items は作成しない
- 保存可能な範囲で `retrieval_score_summary` を保存してよい
- confidence 系は `null` とする

### citation_build_failed

citation marker validation / citation 生成に失敗した場合は、HTTP 500 とする。

`citation_build_failed` は `InternalPipelineError` として扱う。

外部依存障害ではないため、`external_dependency_unavailable` / `external_dependency_timeout` / `generation_failed` とは分ける。

`500 Internal Server Error`

```json
{
  "error": {
    "code": "citation_build_failed",
    "message": "回答根拠の生成に失敗しました。",
    "details": {
      "retrieval_run_id": 502
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### citation_build_failed 時の保存方針

- user message は保存済み
- retrieval_run_items は保存済みの場合、そのまま残す
- retrieval_run.status は `failed`
- retrieval_run.error_code は `citation_build_failed`
- retrieval_run.finished_at を保存する
- assistant message は作成しない
- citations は作成しない
- confidence 系は `null` とする

### failed run confidence 方針

failed retrieval run では、以下は必ず `null` とする。

```json
{
  "answer_confidence": null,
  "groundedness_score": null,
  "confidence_label": null
}
```

### セキュリティ

- 文書全文を外部APIへ送らない
- retrieval 後の最小コンテキストのみ送る
- 外部API利用時は PII マスキング
- prompt injection 防御として system instruction / developer instruction / retrieved context / user question の境界を明確化する

### エラー

| 条件 | HTTP | error.code |
|---|---:|---|
| 未認証 | 401 | `auth_required` |
| chat session 不存在 / owner 不一致 | 404 | `resource_not_found` |
| archived session への投稿 | 409 | `archived_session_readonly` |
| temporary session 期限切れ | 404 | `resource_not_found` |
| 同一 client_message_id が処理中 | 409 | `request_in_progress` |
| 同一 client_message_id で異なる body | 409 | `client_message_conflict` |
| RDB final check 後 0 件 | 422 | `no_context_found` |
| retrieval 失敗 | 503 | `retrieval_failed` |
| rerank 失敗 | 503 | `rerank_failed` |
| generation 失敗 | 503 | `generation_failed` |
| citation 生成失敗 | 500 | `citation_build_failed` |
| 外部依存 timeout | 503 | `external_dependency_timeout` |

---

## 12.2 POST /api/v1/rag/search

### 目的

admin が retrieval 単体を検証するための standalone retrieval debug API。

回答生成は行わず、検索結果と retrieval trace を確認する。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### リクエスト

```json
{
  "query": "RAG評価方針",
  "top_k": 10,
  "strategy": "dense",
  "filters": {
    "logical_document_ids": [1000],
    "modality": "text"
  }
}
```

### バリデーション

| field | rule |
|---|---|
| `query` | 必須、空文字不可、最大 8000 文字 |
| `top_k` | 省略可、1〜20 |
| `strategy` | 省略可、既定 `dense`。PR-24 では `dense` / `sparse` / `hybrid` を実行可能。router/agentic系は後続PRまで `strategy_not_enabled` |
| `filters.logical_document_ids` | 省略可 |
| `filters.modality` | Phase1 は `text` のみ |

### 保存方針

- standalone retrieval_run を作成する
- `retrieval_runs.chat_session_id = null`
- `retrieval_runs.request_message_id = null`
- 作成時は `status = running`
- `started_at` は retrieval_run 作成時刻
- 成功時は `status = succeeded`
- 失敗時は `status = failed`
- RDB final check 後の候補が 1 件以上ある場合のみ `retrieval_run_items` を作成する
- RDB final check 後の候補が 0 件の場合、`retrieval_run_items` は作成しない
- candidates 0 件でも retrieval_run は `succeeded` とする
- candidates 0 件でも `retrieval_score_summary` に 0 件サマリを保存する
- PR-23 `strategy=sparse` では `retrieval_runs.strategy_type = sparse` を保存する
- PR-23 `strategy=sparse` では `retrieval_run_items.retrieval_source = sparse` と `score_breakdown_json.sparse_score` を保存する
- PR-24 `strategy=hybrid` では `retrieval_runs.strategy_type = hybrid` を保存する
- PR-24 `strategy=hybrid` では `retrieval_run_items.retrieval_source = hybrid` と dense/sparse/fused score breakdown を保存する
- `/rag/search` では chat_messages を作成しない
- `/rag/search` では citations テーブルを作成しない
- `/rag/search` は evaluation 対象外とする

### 成功レスポンス

`200 OK`

```json
{
  "data": {
    "retrieval_run_id": 600,
    "status": "succeeded",
    "query": "RAG評価方針",
    "retrieval_score_summary": {
      "candidate_count": 10,
      "post_final_check_count": 8,
      "selected_count": 4,
      "excluded_count": 2,
      "top1_retrieval_score": 0.912345,
      "top3_avg_retrieval_score": 0.876543,
      "top1_rerank_score": 0.834567
    },
    "items": [
      {
        "retrieval_run_item_id": 9001,
        "document_chunk_id": 3001,
        "retrieval_score": 0.912345,
        "rerank_score": 0.834567,
        "rank_order": 1,
        "rerank_order": 1,
        "selected_flag": true,
        "snippet": "評価では faithfulness / groundedness と citation coverage を確認する...",
        "display_label": "RAGパイプライン詳細設計書 p.12",
        "source_type": "upload",
        "source_url": null,
        "page_from": 12,
        "page_to": 12,
        "payload_snapshot": {
          "logical_document_id": 1000,
          "document_version_id": 2001,
          "document_name": "RAGパイプライン詳細設計書",
          "version_no": 2,
          "section_title": "evaluation",
          "modality": "text"
        }
      }
    ]
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 0件レスポンス

RDB final check 後の候補が 0 件でも HTTP 200 を返す。

この場合、`retrieval_run_items` は作成しない。

`200 OK`

```json
{
  "data": {
    "retrieval_run_id": 601,
    "status": "succeeded",
    "query": "存在しない内容",
    "retrieval_score_summary": {
      "candidate_count": 0,
      "post_final_check_count": 0,
      "selected_count": 0,
      "excluded_count": 0,
      "top1_retrieval_score": null,
      "top3_avg_retrieval_score": null,
      "top1_rerank_score": null
    },
    "items": []
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- response order は `rerank_order` 昇順を正とする
- rerank 未実行または rerank 失敗時の fallback 表示は Phase1 では行わない
- `snippet` は masking 後 chunk text から生成する
- `payload_snapshot` に raw chunk text は含めない
- `score` という曖昧な field は使わず、`retrieval_score` / `rerank_score` を明示する
- Qdrant payload の `is_active` は mirror として扱い、最終採用可否は RDB final check で判断する
- archived logical document、inactive version、failed version の chunk は除外する
- `CitationItem` では `document_version_id` を直接返さない
- 一方、`RetrievalRunItem.payload_snapshot` は debug 表示用 snapshot として `document_version_id` を含めてよい
- `payload_snapshot.document_version_id` は citations の DB 冗長保持とは別物である

### エラー

| 条件 | HTTP | error.code |
|---|---:|---|
| viewer が実行 | 403 | `permission_denied` |
| query 空文字 | 422 | `validation_error` |
| top_k 範囲外 | 422 | `validation_error` |
| retrieval 失敗 | 503 | `retrieval_failed` |
| rerank 失敗 | 503 | `rerank_failed` |
| retrieval_run_items 保存失敗 | 500 | `internal_error` |

---

## 12.3 GET /api/v1/rag/retrieval-runs/{retrieval_run_id}

### 目的

retrieval trace を取得する。

### 認証

必要

### 権限

- chat-origin run: owner または admin
- standalone run: admin のみ

### ルート追加理由

`GET /api/v1/rag/citations/{retrieval_run_id}` は citation 取得に特化した endpoint とし、retrieval trace 全体取得は本 endpoint に分離する。

### レスポンス

```json
{
  "data": {
    "retrieval_run": {
      "retrieval_run_id": 600,
      "origin_type": "standalone",
      "chat_session_id": null,
      "request_message_id": null,
      "status": "succeeded",
      "error_code": null,
      "query_hash": "<sha256_hex_64>",
      "top_k": 10,
      "retrieval_score_summary": {
        "candidate_count": 10,
        "post_final_check_count": 8,
        "selected_count": 4,
        "excluded_count": 2,
        "top1_retrieval_score": 0.912345,
        "top3_avg_retrieval_score": 0.876543,
        "top1_rerank_score": 0.834567
      },
      "rerank_score_top1": 0.834567,
      "answer_confidence": null,
      "groundedness_score": null,
      "confidence_label": null,
      "started_at": "2026-04-30T00:00:00Z",
      "finished_at": "2026-04-30T00:00:02Z"
    },
    "items": [
      {
        "retrieval_run_item_id": 9001,
        "document_chunk_id": 3001,
        "retrieval_score": 0.912345,
        "rerank_score": 0.834567,
        "rank_order": 1,
        "rerank_order": 1,
        "selected_flag": true,
        "payload_snapshot": {
          "logical_document_id": 1000,
          "document_version_id": 2001,
          "document_name": "RAGパイプライン詳細設計書",
          "version_no": 2,
          "page_from": 12,
          "page_to": 12,
          "section_title": "evaluation",
          "modality": "text"
        }
      }
    ],
    "citations": []
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### origin_type 導出規則

PR-26 では同じ endpoint を Retrieval Debug UI v2 が利用する。`retrieval_run` には `strategy_type`, `query_plan_json`, `strategy_decision_json`, `latency_breakdown_json`, `retrieval_settings_json` を含めてよい。PR-27 以降の `query_plan_json` には `analysis` / `planner` / `intent` / `candidate_strategies` / `recommended_strategy` などの safe query-plan metadata を含めてよい。`items` には `retrieval_source` と `score_breakdown_json` を含めてよい。ただし raw prompt / full context / raw chunk text / PII / secret-like value は backend response と frontend display の両方で redaction する。

| 条件 | origin_type |
|---|---|
| `chat_session_id IS NOT NULL AND request_message_id IS NOT NULL` | `chat` |
| `chat_session_id IS NULL AND request_message_id IS NULL` | `standalone` |

中間状態は DB / application の不整合として扱う。

### 権限ルール

- chat-origin run は `retrieval_runs.chat_session_id` から owner 判定する
- owner 不一致は 404
- standalone run は admin のみ取得可能
- viewer が standalone run を指定した場合は 404
- admin はすべての retrieval_run を取得可能

### failed run response

failed run の場合、confidence 系は `null` とする。

```json
{
  "data": {
    "retrieval_run": {
      "retrieval_run_id": 602,
      "origin_type": "chat",
      "status": "failed",
      "error_code": "citation_build_failed",
      "retrieval_score_summary": {
        "candidate_count": 10,
        "post_final_check_count": 8,
        "selected_count": 4,
        "excluded_count": 2,
        "top1_retrieval_score": 0.912345,
        "top3_avg_retrieval_score": 0.876543,
        "top1_rerank_score": 0.834567
      },
      "rerank_score_top1": 0.834567,
      "answer_confidence": null,
      "groundedness_score": null,
      "confidence_label": null,
      "started_at": "2026-04-30T00:00:00Z",
      "finished_at": "2026-04-30T00:00:05Z"
    },
    "items": [],
    "citations": []
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 12.4 GET /api/v1/rag/citations/{retrieval_run_id}

### 目的

`/rag/ask` 成功時に保存された citations を取得する。

### 認証

必要

### 権限

- chat-origin run: owner または admin
- standalone run: admin のみ

### レスポンス

```json
{
  "data": {
    "retrieval_run_id": 500,
    "citations": [
      {
        "citation_id": 1,
        "document_chunk_id": 3001,
        "rank_order": 1,
        "snippet": "評価では faithfulness / groundedness と citation coverage を確認する...",
        "page_from": 12,
        "page_to": 12,
        "source_type": "upload",
        "source_url": null,
        "display_label": "RAGパイプライン詳細設計書 p.12",
        "old_version_flag": false
      }
    ]
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- citations response では `document_version_id` を返さない
- version 情報が必要な場合は、server 側で `document_chunk_id` から解決した表示情報として返す
- `old_version_flag` は citation が指す `document_chunk` の version が、現在の active version ではない場合に true とする
- standalone run では citations テーブルを作成しないため、admin から取得した場合は空配列を返す
- viewer が standalone run を指定した場合は 404

### old_version_flag の発生条件

Phase1 の通常 `/rag/ask` では active version のみ retrieval 対象であるため、citation 作成直後の `old_version_flag` は通常 `false` となる。

ただし、過去の retrieval_run を後から表示する場合、citation 作成当時は active だった version が現在 active ではなくなっている可能性がある。この場合、`old_version_flag = true` になり得る。

将来の version 指定検索や admin debug 拡張でも、旧版参照を明示する目的で `old_version_flag = true` になり得る。

### standalone run の citations response

`200 OK`

```json
{
  "data": {
    "retrieval_run_id": 600,
    "citations": []
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 12.5 RAG response schema 共通定義

### RetrievalScoreSummary

`retrieval_score_summary` は JSON object として返す。

Phase1 では、API response key と DB の `retrieval_runs.retrieval_score_summary` JSONB key を同名に統一する。

PR-23 では `strategy=sparse` の場合も同じ response schema を使う。`qdrant_candidate_count` は 0、`sparse_candidate_count` は sparse lexical candidate 数、`top1_rerank_score` は null とする。dense では `sparse_candidate_count` は null。PR-24 `strategy=hybrid` では dense/sparse/fused candidate metadata を安全な summary と score breakdown に保存し、`top1_rerank_score` は null とする。

以下の key を source of truth とする。

| key | nullable | 説明 |
|---|---:|---|
| `candidate_count` | no | vector retrieval 直後、または pipeline 上の候補総数 |
| `post_final_check_count` | no | RDB final check 後に残った候補数 |
| `selected_count` | no | context / response に採用された候補数 |
| `excluded_count` | no | RDB final check や selection で除外された候補数 |
| `top1_retrieval_score` | yes | retrieval_score の top1 |
| `top3_avg_retrieval_score` | yes | retrieval_score top3 平均 |
| `top1_rerank_score` | yes | rerank_score の top1 |

```json
{
  "candidate_count": 10,
  "post_final_check_count": 8,
  "selected_count": 4,
  "excluded_count": 2,
  "top1_retrieval_score": 0.912345,
  "top3_avg_retrieval_score": 0.876543,
  "top1_rerank_score": 0.834567
}
```

候補 0 件の場合は以下とする。

```json
{
  "candidate_count": 0,
  "post_final_check_count": 0,
  "selected_count": 0,
  "excluded_count": 0,
  "top1_retrieval_score": null,
  "top3_avg_retrieval_score": null,
  "top1_rerank_score": null
}
```

### RetrievalRunItem

```json
{
  "retrieval_run_item_id": 9001,
  "document_chunk_id": 3001,
  "retrieval_score": 0.912345,
  "rerank_score": 0.834567,
  "rank_order": 1,
  "rerank_order": 1,
  "selected_flag": true,
  "payload_snapshot": {
    "logical_document_id": 1000,
    "document_version_id": 2001,
    "document_name": "RAGパイプライン詳細設計書",
    "version_no": 2,
    "page_from": 12,
    "page_to": 12,
    "section_title": "evaluation",
    "modality": "text"
  }
}
```

### CitationItem

```json
{
  "citation_id": 1,
  "document_chunk_id": 3001,
  "rank_order": 1,
  "snippet": "評価では faithfulness / groundedness と citation coverage を確認する...",
  "page_from": 12,
  "page_to": 12,
  "source_type": "upload",
  "source_url": null,
  "display_label": "RAGパイプライン詳細設計書 p.12",
  "old_version_flag": false
}
```

`CitationItem` では `document_version_id` を返さない。

`old_version_flag` は、citation が指す `document_chunk` の version が現在の active version ではない場合に true とする。

`RetrievalRunItem.payload_snapshot` は debug trace 用 snapshot であるため、`document_version_id` を含めてよい。これは citations テーブルに `document_version_id` を冗長保持することとは別である。

### ConfidenceInfo

成功 run のみ confidence を返す。

```json
{
  "answer_confidence": 0.78,
  "groundedness_score": 0.82,
  "confidence_label": "High"
}
```

failed run では以下とする。

```json
{
  "answer_confidence": null,
  "groundedness_score": null,
  "confidence_label": null
}
```

---

## 13. 評価 API

## 13.1 POST /api/v1/evaluations/runs

### 目的

評価実行を開始する。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### リクエスト

Phase1 では、評価ケースは固定 fixture またはアプリケーション管理ファイルとして扱う。

```json
{
  "trigger_type": "manual",
  "evaluation_scope": "baseline"
}
```

### ルール

- Phase1 では `trigger_type=manual` のみ許可
- Phase2 以降で `ci` / `scheduled` / `post_deploy` / `online_sampled_trace` を解放可能
- evaluation job は必ず `queued -> running -> succeeded/failed` を経由する
- `evaluation_runs` は queued では `started_at = null` / `finished_at = null`
- running 遷移時に `started_at` を設定する
- terminal 遷移時に `finished_at` を設定する

### レスポンス

`202 Accepted`

```json
{
  "data": {
    "evaluation_run_id": 900,
    "job_id": 300,
    "status": "queued"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

---

## 13.2 GET /api/v1/evaluations/runs

### 目的

評価実行一覧を取得する。

### 認証

必要

### 権限

- `admin`

### クエリ

- `status`
- `trigger_type`
- `page`
- `page_size`

---

## 13.3 GET /api/v1/evaluations/runs/{evaluation_run_id}

### 目的

評価実行詳細を取得する。

### 認証

必要

### 権限

- `admin`

### レスポンス

- run header
- run items
- metrics
- latency
- summary

### Phase2 以降候補

評価データセットを永続化する場合、Phase2 以降で以下 API を追加候補とする。

- `GET /api/v1/evaluations/datasets`
- `POST /api/v1/evaluations/datasets`
- `POST /api/v1/evaluations/datasets/{evaluation_dataset_id}/cases`

---

## 14. ジョブ API

## 14.1 GET /api/v1/jobs

### 目的

ジョブ一覧を取得する。

### 認証

必要

### 権限

- `admin`

### クエリ

- `status`
- `job_type`
- `page`
- `page_size`
- `from`
- `to`

### 時刻範囲 validation

- `from <= to`
- 最大検索期間は **31日** とする
- 不正形式は `422 validation_error`

---

## 14.2 GET /api/v1/jobs/{job_id}

### 目的

ジョブ詳細を取得する。

### 認証

必要

### 権限

- `admin`

### レスポンス項目

job 詳細では以下を返す。

`scheduled_at` は使用しない。

DDL v1.8 の `jobs.created_at` に合わせ、API response でも `created_at` を返す。

```json
{
  "data": {
    "job_id": 300,
    "job_type": "document_ingest",
    "status": "failed",
    "source_job_id": null,
    "retry_count": 0,
    "created_at": "2026-04-30T00:00:00Z",
    "started_at": "2026-04-30T00:01:00Z",
    "finished_at": "2026-04-30T00:02:00Z",
    "error_code": "embedding_failed",
    "error_message": "Embedding generation failed.",
    "redacted_payload": {
      "logical_document_id": 1000,
      "document_version_id": 2001
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### 時刻項目の意味

| field | 意味 |
|---|---|
| `created_at` | job 作成時刻 |
| `started_at` | worker が running に遷移させた時刻。queued では null |
| `finished_at` | terminal state に到達した時刻。queued / running では null |

### payload 応答方針

- `payload_json` はデフォルトでは返さない
- 必要な場合でも `redacted_payload` のみ返す
- 文書パス、元ファイル名、PII を含む生 payload は返さない

### 命名対応

- API の `source_job_id` は DDL 上の `jobs.retry_of_job_id` に対応する
- retry の retry でも、`source_job_id` は直前 retry job ではなく original source job を指す
- active retry 判定は original source job 単位で行う

---

## 14.3 POST /api/v1/jobs/{job_id}/retry

### 目的

失敗 job を再試行する。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### retry 対象条件

- 対象 job の `status = failed`
- canceled / succeeded / running / queued は retry 対象外
- active retry が既に存在する場合は 409

### 成功レスポンス

`201 Created`

```json
{
  "data": {
    "job_id": 301,
    "source_job_id": 300,
    "status": "queued",
    "result_code": "retry_created"
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### active retry 競合

`409 Conflict`

```json
{
  "error": {
    "code": "job_active_retry_exists",
    "message": "An active retry already exists for this source job.",
    "details": {
      "source_job_id": 300,
      "active_retry_job_id": 301
    }
  },
  "meta": {
    "request_id": "req_xxx"
  }
}
```

### ルール

- retry 元 job は failed のまま変更しない
- 新 job は queued として作成する
- `created_at` は job 作成時刻である
- `started_at` は worker が running にした時刻であり、queued 作成時は null とする
- API 上の `source_job_id` は DDL の `retry_of_job_id` に対応する
- retry chain は original source job に集約する
- retry の retry でも `source_job_id` には original source job_id を返す

---

## 15. 監査 API

## 15.1 GET /api/v1/audit-logs

### 目的

監査ログ一覧を取得する。

### 認証

必要

### 権限

- `admin`

### クエリ

- `actor_user_id`
- `action_type`
- `target_type`
- `target_id`
- `page`
- `page_size`
- `from`
- `to`

### 時刻範囲 validation

- `from <= to`
- 最大検索期間は **31日** とする
- 不正形式は `422 validation_error`

### セキュリティ

- PII を直接返さない
- details_json はマスク済みで返す

---

## 16. システム設定 API

## 16.1 GET /api/v1/system/settings

### 目的

システム設定を取得する。

### 認証

必要

### 権限

- `admin`

### 対象例

- memory_message_limit
- confidence thresholds
- duplicate handling policy
- temporary chat ttl
- job retry upper bound

---

## 16.2 PATCH /api/v1/system/settings

### 目的

システム設定を更新する。

### 認証

必要

### 権限

- `admin`

### CSRF

必須

### リクエスト例

```json
{
  "temporary_chat_ttl_minutes": 120,
  "memory_message_limit": 8,
  "job_retry_max": 3
}
```

### 監査

- 必ず監査対象

---

## 17. Worker 内部利用 API / 非公開 API 方針

Phase1 では worker は主に DB polling で動作するが、必要に応じて内部専用 API を持ってよい。

原則:

- 外部公開しない
- internal network のみ
- 認証不要にしない
- service token 等で保護する

---

## 18. Archive / Retrieval 整合ルール

### 18.1 RDB final check を正とする

retrieval 対象判定では、Qdrant payload ではなく RDB を最終的な正とする。

retrieval 採用可能な chunk は以下をすべて満たす。

```text
logical_documents.status = 'active'
AND document_versions.status = 'ready'
AND document_versions.is_active = true
AND document_chunks.document_version_id = document_versions.document_version_id
```

### 18.2 Qdrant payload の位置づけ

Qdrant payload の `is_active` / `logical_document_id` / `document_version_id` / `modality` 等は検索効率化のための mirror である。

Qdrant payload が古い場合でも、RDB final check で不適格な chunk は除外する。

### 18.3 archive 後の扱い

logical document archive 後は以下とする。

- API 上の document status は `archived`
- 関連 active version は `is_active = false`
- retrieval 対象外
- Qdrant payload の `is_active=false` 更新を試みる
- Qdrant 更新に失敗しても、RDB final check で除外する
- `/rag/search` でも `/rag/ask` でも archived document は採用しない

---

## 19. 監査対象操作一覧

以下は必ず監査対象とする。

- ログイン / ログアウト
- 文書アップロード
- 文書アーカイブ
- 文書物理削除
- 文書承認
- 再インデックス
- 評価実行
- システム設定変更
- 外部API利用
- 将来のマスク解除操作

---

## 20. PII / 機微情報 API 応答ルール

### 20.1 応答原則

- 不要な PII は返さない
- ログには生の PII を出さない
- details_json は必要に応じてマスク済みにする

### 20.2 例

- `auth_sessions.session_token_hash` は返さない
- `jobs.payload_json` は管理者でも必要最小限のみ返す
- `audit_logs.details_json` は PII 除去済みを返す

---

## 21. 非機能 / 運用 API 観点

### 21.1 タイムアウト

- 文書アップロードは同期完了を待たず `202 Accepted`
- 評価実行も `202 Accepted`
- RAG 質問は同期応答だが、将来 streaming 拡張可能な設計とする

### 21.2 冪等性

以下は可能なら idempotency key 対応を検討する。

- 文書アップロード
- 評価実行
- ジョブ再試行

ただし Phase1 では、`/rag/ask` の冪等性は `client_message_id` を正式な重複送信対策として扱う。

### 21.3 将来拡張

- streaming answer
- SSE / WebSocket
- OIDC
- AWS 連携
- online evaluation trigger
- URL 取込 API
- evaluation dataset API

---

## 22. OpenAPI 化の前提ルール

OpenAPI 化する際は以下を守る。

- operationId を一意にする
- request / response schema を共通化する
- error schema を統一する
- 認証必須 API に security scheme を明記する
- 管理者専用 API は description に権限を明記する
- archive no-op 応答は chat/document で共通 schema 名を使用する
- tag 重複追加 no-op は `already_exists` として定義する
- `document_versions.status` と `display_status` を混同しない
- `display_status=pending_review` は API 表示用状態であり、DB status ではないことを schema description に明記する
- `display_status` filter は DB column ではなく service 層で条件変換することを schema description または実装メモに明記する
- `latest_version` は `version_no` 最大と定義する
- `active_version` は nullable として定義する
- `content_hash` は `<sha256_hex_64>` 相当の lowercase hex 64文字として schema description に明記する
- `RetrievalScoreSummary` は object schema として定義する
- `RetrievalScoreSummary` の key は API response / DB JSONB で同名に統一する
- `RetrievalRunItem` では `score` を使わず、`retrieval_score` / `rerank_score` を明示する
- `CitationItem` では `document_version_id` を返さない
- `CitationItem.old_version_flag` は現在 active version ではない version を指す場合に true と説明する
- `RetrievalRunItem.payload_snapshot` では debug snapshot として `document_version_id` を含めてよい
- failed retrieval run の confidence fields は nullable とする
- `/rag/search` 0件 response は success schema として定義する
- `/rag/search` 0件時は `retrieval_run_items` を作成しない
- `/rag/ask` replay response は初回成功時と同じ response schema を使用し、`data.meta.replayed` のみ差分にする
- `citation_build_failed` は 500 error response として定義する
- `jobs` response では `scheduled_at` を使わず `created_at` を使う
- Phase1 の評価 API では `evaluation_dataset_id` / `evaluation_case_id` を必須にしない

## Phase2 PR-22 評価 dataset / case API

PR-22 では strategy 比較評価のため、以下の admin-only API を追加する。GET は CSRF 不要、POST/PATCH は CSRF 必須。viewer は 403、未認証は 401。

### Dataset

- `GET /api/v1/evaluations/datasets`
- `POST /api/v1/evaluations/datasets`
- `GET /api/v1/evaluations/datasets/{evaluation_dataset_id}`
- `PATCH /api/v1/evaluations/datasets/{evaluation_dataset_id}`
- `POST /api/v1/evaluations/datasets/{evaluation_dataset_id}/archive`

削除 API は持たない。archive は冪等な lifecycle 操作として扱う。

### Case

- `GET /api/v1/evaluations/datasets/{evaluation_dataset_id}/cases`
- `POST /api/v1/evaluations/datasets/{evaluation_dataset_id}/cases`
- `GET /api/v1/evaluations/datasets/{evaluation_dataset_id}/cases/{evaluation_case_id}`
- `PATCH /api/v1/evaluations/datasets/{evaluation_dataset_id}/cases/{evaluation_case_id}`
- `POST /api/v1/evaluations/datasets/{evaluation_dataset_id}/cases/{evaluation_case_id}/archive`

case は親 dataset と整合しない場合 404 を返す。

### Import / Export

- `POST /api/v1/evaluations/datasets/import`
- `GET /api/v1/evaluations/datasets/{evaluation_dataset_id}/export`

manifest schema は `phase2.evaluation_dataset.v1`。import は `dataset_name` と `case_key` で冪等に upsert する。export は safe manifest のみを返す。

### Evaluation run request extension

既存 `POST /api/v1/evaluations/runs` は後方互換を維持しつつ、以下を受け取れる。

```json
{
  "evaluation_dataset_id": 1,
  "dataset_name": "phase2_strategy_smoke",
  "strategy_type": "dense",
  "strategies": ["dense", "sparse", "hybrid"],
  "metrics": [
    "recall_at_k",
    "mrr",
    "citation_coverage",
    "groundedness",
    "faithfulness",
    "no_context_rate",
    "p95_latency"
  ],
  "top_k": 20,
  "rerank_top_n": 5,
  "case_limit": 20,
  "trigger_type": "manual"
}
```

PR-25 では `strategies` に `dense` / `sparse` / `hybrid` を指定できる。省略時は `["dense"]`。`agentic_router` は PR-30 まで実行不可。

Strategy comparison は以下で取得する。

- `GET /api/v1/evaluations/runs/{evaluation_run_id}/strategy-comparison`

### Security

dataset / case / metric / response には raw prompt、full context、raw chunk text、PII、secret、token、credential、API key、password を保存・表示しない。case の `question` は safe evaluation input として保存できるが、full prompt ではない。

---

## 23. 実装優先順位

### Phase1 で優先実装する API

1. auth
2. chat sessions / messages / tags
3. documents create / add version / list / detail / approve / archive
4. rag ask
5. rag search
6. retrieval trace
7. citations取得
8. evaluations run / list / detail
9. jobs / retry
10. audit logs
11. user settings / system settings

### Phase1 の API 実装順序補足

- chat tag duplicate は `200 OK + already_exists` になることをテストする
- document API 実装時点で `display_status` 導出を入れる
- `display_status` filter は DB column 前提にせず、service 層で条件変換する
- `latest_version = max(version_no)` をテストする
- approve 前 document の `active_version = null` をテストする
- `/rag/ask` 実装時点で `no_context_found = 422` を固定する
- `/rag/ask` replay は初回成功時と同等の citations を返すことをテストする
- `/rag/search` 実装時点で 0件 success をテストする
- `/rag/search` 0件時は `retrieval_run_items` が作成されないことをテストする
- retrieval trace 実装時点で failed run confidence null をテストする
- citations 実装時点で `document_version_id` 非返却をテストする
- `old_version_flag` が現在 active version と異なる version で true になることをテストする
- standalone retrieval_run_id を viewer が trace / citations API に指定した場合は 404 になることをテストする
- standalone retrieval_run_id を admin が trace / citations API に指定した場合は 200 になることをテストする
- archive 実装時点で RDB final check による retrieval 対象外化をテストする
- job retry 実装時点で original source job 方針をテストする
- job response で `scheduled_at` が出ず、`created_at` が返ることをテストする
- ready version reindex と failed version retry の状態遷移差をテストする

### Phase2 以降で強化する API

- evaluation dataset API
- online evaluation
- production trace ingestion
- alert rules
- OCR 管理
- AWS deploy 管理
- streaming answer
- SSE / WebSocket
- URL 取込 API

---

## 24. 総括

本 API 設計は、Phase1 の実装可能性と、実サービス級の堅牢性を意識した安全設計を両立することを目的としている。

特に以下を重視している。

- 認証 / 認可の明確化
- CSRF / CORS / Cookie 運用の明確化
- セッション再生成と明示的失効
- 同一 session 保証などの横断ルールの明示
- PII / 監査 / 外部API境界の厳格化
- 文書アップロード防御
- confidence / citation / retrieval trace の返却整合
- archive と retrieval 対象外の関係の固定
- admin / viewer の責務分離
- 将来の online eval / OIDC / AWS 拡張余地

v1.9 では、DDL草案 v1.8、RAG パイプライン詳細設計書 v1.4、バックエンド詳細設計書 v1.4、および API設計書 v1.8 レビューを踏まえ、特に以下を固定した。

- `pending_review` を DB status として使わない
- `ready + is_active=false` を承認待ちとして扱う
- API response では `display_status` を返す
- logical document archived を `display_status` 導出の最優先条件とする
- `display_status` filter は service 層で条件変換する
- chat tag duplicate は `200 OK + result_code = already_exists` とする
- `latest_version` は `version_no` 最大の version とする
- `active_version` は nullable とする
- approve 済み version がない場合は `active_version = null` を返す
- `content_hash` の例は `<sha256_hex_64>` とし、DDL 制約と矛盾させない
- `/rag/ask` の `no_context_found` は HTTP 422
- `/rag/ask` replay response は保存済み citations を含めて返す
- `/rag/search` の 0件は HTTP 200 + `items=[]`
- `/rag/search` 0件時は `retrieval_run_items` を作成しない
- `/rag/search` は standalone retrieval run を保存する
- `/rag/search` は `strategy` 省略時 `dense`、PR-24 では `strategy=sparse` / `strategy=hybrid` も許可する
- `/rag/search` では citations テーブルを作成しない
- `citation_build_failed` は HTTP 500 / InternalPipelineError
- failed retrieval run では confidence 系を null とする
- `retrieval_score_summary` は JSON object として返す
- `retrieval_score_summary` の key は API response / DB JSONB で同名に統一する
- retrieval trace は `retrieval_score / rerank_score / rank_order / rerank_order / selected_flag / payload_snapshot` を返す
- citations response では `document_version_id` を返さない
- `CitationItem.old_version_flag` は citation が指す version が現在 active version ではない場合に true とする
- `RetrievalRunItem.payload_snapshot` には debug snapshot として `document_version_id` を含めてよい
- archive 後は RDB final check を正として retrieval 対象外にする
- Qdrant payload は mirror として扱う
- job retry は original source job に集約する
- job response では `scheduled_at` ではなく `created_at` を返す
- ready version reindex と failed version retry の状態遷移差を明確化する
- Phase1 の評価 API では `evaluation_dataset_id` / `evaluation_case_id` を必須にしない

以上をもって、API設計書 v1.9 最終版とする。
