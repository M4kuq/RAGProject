# AI/LLMエンジニア向けポートフォリオ提出用 RAGシステム

## Worker / Job 詳細設計書 v1.3

---

## 1. 文書概要

### 1.1 目的

本書は、RAGシステム Phase1 における非同期処理基盤である Worker / Job の詳細設計を定義する。

本書では、DDL草案 v1.8、API設計書 v1.9 最終版、RAG パイプライン詳細設計書 v1.4、バックエンド詳細設計書 v1.4、状態遷移仕様書 v1.1 と整合する形で、以下を実装可能な粒度まで具体化する。

* job lifecycle
* worker polling
* job acquisition
* lease / lock
* lease ownership validation
* terminal update safety
* running job reclaim
* retry
* handler 共通契約
* document ingest
* failed ingest retry
* reclaim 後の partial artifact cleanup
* 外部 I/O と DB transaction の分離
* Qdrant mirror update
* message edit regeneration
* stale retrieval_run 対策
* evaluation run
* evaluation retry / reclaim 時の partial item cleanup
* temporary chat cleanup
* failure handling
* idempotency
* partial failure recovery
* worker test 方針

### 1.2 本書の位置づけ

本書は API 設計や DDL を上書きしない。

本書は、既存設計で確定した DB 制約・API 契約・RAG パイプライン方針を前提として、Worker / Job 実装時に迷いやすい処理順序、transaction boundary、再実行方針、障害時の扱いを補完する。

### 1.3 前提文書

* 要件定義書 v1.1
* 基本設計書 v1.2
* ER図 / テーブル設計書 v1.4
* DDL草案 v1.8
* API設計書 v1.9 最終版
* RAG パイプライン詳細設計書 v1.4
* バックエンド詳細設計書 v1.4
* 状態遷移仕様書 v1.1
* 機能仕様補完書 v1.7

### 1.4 v1.3 の重点修正

v1.3 では、v1.2 レビューを踏まえて以下を反映する。

* `document_ingest` の Qdrant cleanup を DB transaction 内で実行しないと明記する
* `document_ingest` の cleanup transaction boundary を、DB row lock / cleanup 対象取得、external cleanup、RDB cleanup に分離する
* `document_versions.status = ready` の no-op success 時は Qdrant point 存在確認を行わないと固定する
* ready だが Qdrant point が不足する場合の修復は、`document_ingest` ではなく reindex または `qdrant_mirror_update` / repair job に委ねる
* `retrieval_runs.request_id` は Phase1 では UNIQUE 制約を持たない trace 用 field と明記する
* 同一 `job_trace_id` に複数 retrieval_run が存在し得ることを許容し、最新 run の判定規則を明記する
* stale retrieval_run 更新条件を `request_id = job_trace_id AND status = 'running'` に固定する
* stale 更新前に `succeeded retrieval_run + assistant_message` を確認する順序を明記する
* 同一 `job_trace_id` に複数 failed retrieval_run がある場合の debug 代表選択を定義する
* 同一 `evaluation_run_id` の queued / running evaluation job は Phase1 で 1 本に制限する
* evaluation active job 制限は service validation を基本とし、将来 partial unique index 候補を明記する
* failed retry / reclaim 開始時の Qdrant cleanup failure では、`document_versions.error_code = qdrant_cleanup_failed`、`jobs.error_code = qdrant_cleanup_failed` に固定する
* temporary cleanup audit に残す最小項目例を明記する
* startup check failure は Worker process 起動失敗であり、未取得 job の state を変更しないと明記する
* retry payload の `requested_by_user_id` は必須、`original_requested_by_user_id` は任意と整理する

### 1.5 本書で決めること

* job status の意味
* jobs の時刻項目の責務
* Worker process の構成
* polling interval / batch size / lease duration
* `SELECT ... FOR UPDATE SKIP LOCKED` による job acquisition
* queued job 取得ルール
* expired running job reclaim ルール
* lease 更新ルール
* lease ownership 喪失時の結果破棄ルール
* domain success no-op ルール
* retry job 作成ルール
* `retry_of_job_id` の扱い
* active retry 競合の判定
* handler の共通 interface
* handler 別の transaction boundary
* handler failure 時の job / domain state 更新
* partial failure recovery
* idempotency key / natural key の扱い
* temporary cleanup の削除順序
* worker test 観点

### 1.6 本書で決めないこと

* RAG prompt の最終文面
* embedding / rerank / generation model の最終チューニング
* Qdrant collection の物理作成手順
* OpenAPI YAML の完全定義
* Kubernetes / ECS など本番 worker orchestration
* Phase2 以降の分散 queue 製品導入

---

## 2. 設計原則

### 2.1 Deterministic 原則

同じ job state / payload / domain state に対して、Worker は同じ判断を行う。

optional wording は避け、以下は必ず固定する。

* job 取得条件
* retry 可否
* active retry 判定
* lease ownership 確認
* terminal update 条件
* lease lost 時の domain update 禁止
* reclaim 時の artifact cleanup
* 外部 I/O を DB transaction 内で実行しない方針
* handler success / failure 時の状態更新
* partial failure 時の再実行可否
* domain state と job state のどちらを正とするか

### 2.2 DB を job queue として使う原則

Phase1 では外部 queue 製品は導入せず、PostgreSQL の `jobs` テーブルを queue として利用する。

採用理由:

* ポートフォリオ用途として構成を過度に複雑化しない
* DDL / API / Worker の整合を示しやすい
* transaction により domain state と job state の整合を取りやすい
* `FOR UPDATE SKIP LOCKED` により複数 Worker でも安全に取得可能

### 2.3 Worker は at-least-once 実行を前提とする

Worker は完全な exactly-once 実行を保証しない。

Phase1 では以下を前提とする。

* job は障害時に再取得される可能性がある
* handler は可能な限り idempotent にする
* 外部副作用は natural key / upsert / cleanup により二重実行に耐える
* 途中失敗時は domain state を安全側に倒す
* lease ownership を失った Worker は handler result を採用しない
* domain state がすでに成功状態の場合、handler は no-op success できるようにする

### 2.4 job state と domain state の分離

`jobs.status` は非同期処理単位の状態を表す。

文書・評価・会話などの業務状態は、それぞれの domain table で表す。

例:

* job 状態: `jobs.status = failed`
* document version 状態: `document_versions.status = failed`
* evaluation 状態: `evaluation_runs.status = failed`

Worker は handler failure 時に、job state と domain state の両方を整合させる。

ただし、Worker が lease ownership を失っている場合、job state の terminal update は行わない。

### 2.5 domain final update と job terminal update の非原子的関係

handler が domain final update を完了した後に、job terminal update が lease lost により失敗する可能性がある。

例:

```text
Worker A:
  document_versions.status = ready に更新
  jobs.status = succeeded 更新前に lease lost

Worker B:
  同じ job を reclaim
```

この中間状態は完全には避けられない。

そのため各 handler は、開始時に domain state がすでに成功状態の場合、処理済みとして no-op success できるようにする。

代表例:

| handler                     | domain success state                                                | reclaim 時の扱い  |
| --------------------------- | ------------------------------------------------------------------- | ------------- |
| `document_ingest`           | `document_versions.status = ready`                                  | no-op success |
| `evaluation_run`            | `evaluation_runs.status = succeeded`                                | no-op success |
| `temporary_chat_cleanup`    | 対象 session が存在しない                                                   | no-op success |
| `message_edit_regeneration` | `job_trace_id` に紐づく succeeded retrieval_run + assistant_message が存在 | no-op success |

### 2.6 外部 I/O と DB transaction の分離原則

Qdrant cleanup / Qdrant upsert / embedding API / generation API などの外部 I/O は、DB transaction 内で実行しない。

理由:

* 長時間 DB transaction を避ける
* row lock の長時間保持を避ける
* DB rollback と外部副作用の不一致を局所化する
* reclaim 時の idempotent cleanup で復旧できるようにする

特に `document_ingest` の Qdrant cleanup は DB transaction 外で実行する。

### 2.7 RDB final check 優先原則

Qdrant payload は mirror であり、最終判定は RDB を正とする。

Worker が Qdrant mirror update に失敗した場合でも、RDB 上で retrieval 対象外になっていれば、RAG retrieval final check で除外される。

### 2.8 raw data 非ログ出力原則

Worker は以下を log / audit / job error_message に出さない。

* raw file content
* raw chunk text 全文
* prompt 全文
* PII
* secret
* local storage の絶対 path

ログには redacted / summarized value のみを出す。

### 2.9 Lease ownership 優先原則

Worker は job の実行権を `locked_by` によって判定する。

以下の場合、Worker は job の所有権を失ったとみなす。

* lease renewal の更新件数が 0
* terminal update の更新件数が 0
* terminal update 前の ownership check で `locked_by` が自 Worker ではない
* status が `running` ではなくなっている

所有権を失った Worker は、handler result を破棄し、`jobs.status` を更新しない。

`LeaseLostError` は job failure ではない。

`LeaseLostError` 発生時、handler は domain state を failed に更新しない。

---

## 3. jobs テーブル前提

### 3.1 status 一覧

Phase1 の `jobs.status` は以下を前提とする。

| status      | 意味                      | started_at | finished_at | Phase1 公開操作   |
| ----------- | ----------------------- | ---------- | ----------- | ------------- |
| `queued`    | 実行待ち                    | `NULL`     | `NULL`      | 作成・retry      |
| `running`   | Worker が lease を保持して実行中 | `NOT NULL` | `NULL`      | Worker 内部     |
| `succeeded` | 正常終了                    | `NOT NULL` | `NOT NULL`  | 参照のみ          |
| `failed`    | 失敗終了                    | `NOT NULL` | `NOT NULL`  | retry 可能      |
| `canceled`  | 中止済み                    | 状況による      | `NOT NULL`  | Phase1 では予約状態 |

### 3.2 canceled の Phase1 方針

`canceled` は DDL 上許容してよいが、Phase1 では公開 cancel API を実装しない。

Phase1 における位置づけ:

* 将来拡張用の terminal state
* manual operation / internal operation 用の予約状態
* retry 対象外
* 通常の Worker flow では作成しない

### 3.3 時刻項目の責務

| column             | 意味                           | 更新主体                             |
| ------------------ | ---------------------------- | -------------------------------- |
| `created_at`       | job 作成時刻                     | API / service                    |
| `started_at`       | Worker が初めて running に遷移させた時刻 | Worker                           |
| `finished_at`      | terminal state に到達した時刻       | Worker / internal cancel service |
| `locked_at`        | Worker が現在の lease を取得した時刻    | Worker                           |
| `lease_expires_at` | 現在の lease 期限                 | Worker                           |

重要方針:

* `created_at` は job 作成時刻である
* `started_at` は job 作成時刻ではない
* `queued` job の `started_at` は必ず `NULL`
* Worker が `running` にした時点で `started_at` を設定する
* reclaim / lease 更新では `started_at` を上書きしない
* `finished_at` は `succeeded` / `failed` / `canceled` への terminal 遷移時に設定する

### 3.4 lock / lease 項目

| column             | 意味                             |
| ------------------ | ------------------------------ |
| `locked_by`        | job を保持している Worker instance ID |
| `locked_at`        | 現在の lease を取得した時刻              |
| `lease_expires_at` | lease の期限                      |

`locked_by` は `worker_name + process_id + boot_uuid` のように、Worker instance を識別できる値とする。

例:

```text
worker-local-1:pid-1234:boot-01HZX...
```

### 3.5 retry 項目

| column / API field     | 意味                           |
| ---------------------- | ---------------------------- |
| `jobs.retry_of_job_id` | original source job を指す      |
| API `source_job_id`    | `jobs.retry_of_job_id` の外部表現 |

retry 方針:

* retry の retry でも、`retry_of_job_id` は直前 retry job ではなく original source job を指す
* active retry は original source job 単位で 1 本まで
* active retry の状態は `queued` / `running`
* active retry が存在する場合、API は `409 job_active_retry_exists` を返す

### 3.6 job_type 一覧

Phase1 の正式 job_type は以下とする。

| job_type                    | 目的                                    |
| --------------------------- | ------------------------------------- |
| `document_ingest`           | 文書抽出・chunking・embedding・Qdrant upsert |
| `qdrant_mirror_update`      | Qdrant payload mirror 更新              |
| `message_edit_regeneration` | user message 編集後の回答再生成                |
| `evaluation_run`            | 評価実行                                  |
| `temporary_chat_cleanup`    | temporary chat TTL 到達後の物理削除           |

Phase2 以降候補:

* `document_reindex_batch`
* `audit_log_retention_cleanup`
* `online_evaluation_sample`
* `ocr_ingest`

### 3.7 job target 方針

job は可能な限り target を明示する。

| job_type                    | target_type                               |                   target_id |
| --------------------------- | ----------------------------------------- | --------------------------: |
| `document_ingest`           | `document_version`                        |       `document_version_id` |
| `qdrant_mirror_update`      | `logical_document` または `document_version` |                       対象 ID |
| `message_edit_regeneration` | `chat_message`                            | 編集対象 user `chat_message_id` |
| `evaluation_run`            | `evaluation_run`                          |         `evaluation_run_id` |
| `temporary_chat_cleanup`    | `chat_session`                            |           `chat_session_id` |

`message_edit_regeneration` は target 必須とする。

### 3.8 payload_json 方針

`payload_json` は handler が必要とする最小情報のみ保存する。

禁止:

* raw file content
* raw chunk text
* prompt 全文
* PII
* secret
* local absolute path

payload 例:

```json
{
  "logical_document_id": 1000,
  "document_version_id": 2001,
  "requested_by_user_id": 1
}
```

---

## 4. Worker 全体構成

### 4.1 ディレクトリ構成

```text
backend/
  app/
    workers/
      worker_main.py
      worker_config.py
      job_dispatcher.py
      job_repository.py
      lease.py
      retry.py
      startup_checks.py
      handlers/
        base.py
        document_ingest_handler.py
        qdrant_mirror_update_handler.py
        message_edit_regeneration_handler.py
        evaluation_run_handler.py
        temporary_chat_cleanup_handler.py
    services/
      document_service.py
      rag_service.py
      evaluation_service.py
      chat_service.py
    db/
      session.py
      repositories/
```

### 4.2 Worker process 責務

Worker process は以下を担当する。

1. 起動時設定読み込み
2. Worker instance ID 生成
3. startup check
4. polling loop 開始
5. job acquisition
6. handler dispatch
7. lease renewal
8. lease ownership validation
9. success / failure terminal update
10. graceful shutdown
11. structured logging

### 4.3 Startup check

Worker は起動時、または最初の handler 実行前に必要な外部依存設定を検証する。

Startup check は enabled job_type に応じて必要最小限に分ける。

| enabled job_type            | 必須 check                                                                                                         |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `document_ingest`           | DB / storage / Qdrant connection / Qdrant collection / vector dimension / embedding config / embedding dimension |
| `qdrant_mirror_update`      | DB / Qdrant connection / Qdrant collection                                                                       |
| `message_edit_regeneration` | DB / retrieval client / reranker client / generation client / Qdrant connection                                  |
| `evaluation_run`            | DB / retrieval client / reranker client / generation client / evaluation fixture or case loader                  |
| `temporary_chat_cleanup`    | DB のみ                                                                                                            |

Phase1 方針:

* `document_ingest` を処理する Worker は Qdrant / embedding 設定検証を必須とする
* 検証失敗時、Worker process は起動失敗として終了する
* startup check failure は Worker process の起動失敗であり、未取得 job の status は変更しない
* 一時的外部依存障害の場合は retry with backoff してから終了してよい

### 4.4 Dispatcher 責務

`job_dispatcher.py` は `job_type` に応じて handler を選択する。

```text
document_ingest              -> DocumentIngestHandler
qdrant_mirror_update         -> QdrantMirrorUpdateHandler
message_edit_regeneration    -> MessageEditRegenerationHandler
evaluation_run               -> EvaluationRunHandler
temporary_chat_cleanup       -> TemporaryChatCleanupHandler
```

未知の `job_type` は acquisition 後に `running` になってから dispatcher が `failed` にする。

この場合、`started_at` は設定済みであるため、DDL 整合上問題ない。

### 4.5 Handler 責務

各 handler は以下を実装する。

* payload validation
* domain state validation
* domain success no-op 判定
* 処理本体
* domain state update
* idempotency handling
* partial failure cleanup
* error_code mapping

handler は `jobs` の generic state update を直接行わない。

job state update は worker runner / dispatcher 層で統一する。

### 4.6 repository 責務

`job_repository.py` は以下を担当する。

* acquire job
* check ownership
* renew lease
* mark succeeded
* mark failed
* mark canceled
* create retry job
* active retry existence check
* redacted payload 取得

---

## 5. Job lifecycle

### 5.1 状態遷移図

```text
          create
            |
            v
        +--------+
        | queued |
        +--------+
            |
            | acquire by worker
            v
       +---------+
       | running |
       +---------+
        /   |    \
       /    |     \
      v     v      v
succeeded failed canceled
```

running job の lease が期限切れになった場合、Worker は同じ job を reclaim して実行を継続または再実行する。

### 5.2 queued

queued は実行待ち状態である。

制約:

```text
status = 'queued'
started_at IS NULL
finished_at IS NULL
```

作成主体:

* API service
* domain service
* retry API
* scheduled internal process

### 5.3 running

running は Worker が lease を取得して処理中の状態である。

制約:

```text
status = 'running'
started_at IS NOT NULL
finished_at IS NULL
locked_by IS NOT NULL
locked_at IS NOT NULL
lease_expires_at IS NOT NULL
```

Worker は job acquisition 時に以下を更新する。

* `status = running`
* `started_at = COALESCE(started_at, now())`
* `locked_by = worker_instance_id`
* `locked_at = now()`
* `lease_expires_at = now() + lease_duration`

### 5.4 succeeded

succeeded は正常終了状態である。

制約:

```text
status = 'succeeded'
started_at IS NOT NULL
finished_at IS NOT NULL
error_code IS NULL
```

Worker は success 時に ownership を検証したうえで以下を更新する。

* `status = succeeded`
* `finished_at = now()`
* `error_code = NULL`
* `error_message = NULL`
* `lease_expires_at = NULL`

`locked_by` / `locked_at` はデバッグ目的で残してもよい。

### 5.5 failed

failed は失敗終了状態である。

制約:

```text
status = 'failed'
started_at IS NOT NULL
finished_at IS NOT NULL
error_code IS NOT NULL
```

Worker は failure 時に ownership を検証したうえで以下を更新する。

* `status = failed`
* `finished_at = now()`
* `error_code = mapped_error_code`
* `error_message = redacted safe message`
* `lease_expires_at = NULL`

### 5.6 canceled

canceled は明示的に中止された状態である。

Phase1 では公開 cancel API は実装しない。

cancel を将来実装する場合:

* queued job は即 canceled にできる
* running job は cooperative cancel を基本とする
* Worker は処理単位の境界で canceled を検知する
* canceled job は retry 対象外とする

---

## 6. Worker 設定

### 6.1 Phase1 正式設定項目

| key                                   | default | 説明                    |
| ------------------------------------- | ------: | --------------------- |
| `worker_poll_interval_ms`             |    1000 | polling interval      |
| `worker_batch_size`                   |       1 | 1 polling で取得する job 数 |
| `worker_lease_seconds`                |     300 | lease duration        |
| `worker_lease_renew_interval_seconds` |      60 | lease 更新間隔            |
| `worker_shutdown_grace_seconds`       |      30 | graceful shutdown 猶予  |
| `worker_enabled_job_types`            |     all | 実行対象 job_type         |

Phase1 では batch size は 1 を推奨する。

理由:

* 実装が単純
* デバッグしやすい
* ポートフォリオとして trace を説明しやすい

### 6.2 Phase2 以降候補設定

| key                              | 説明                    |
| -------------------------------- | --------------------- |
| `worker_max_reclaim_count`       | reclaim 回数上限          |
| `worker_reclaim_backoff_seconds` | reclaim job の backoff |

Phase1 では `reclaim_count` を DB に持たないため、`worker_max_reclaim_count` による実効制御は行わない。

`worker_max_reclaim_count` を導入する場合は、Phase2 以降で `jobs.reclaim_count INTEGER NOT NULL DEFAULT 0` を DDL に追加し、reclaim 時に `reclaim_count = reclaim_count + 1` を行う。

### 6.3 Worker instance ID

Worker 起動時に instance ID を生成する。

形式例:

```text
{hostname}:{process_id}:{boot_uuid}
```

例:

```text
local-worker-1:1234:01HZABCDEF...
```

### 6.4 対象 job_type の制御

Worker は環境変数で処理対象 job_type を制限できる。

例:

```text
WORKER_JOB_TYPES=document_ingest,evaluation_run
```

未指定時は全 job_type を処理する。

---

## 7. Job acquisition

### 7.1 取得対象

Worker は以下を取得対象とする。

1. `status = queued`
2. `status = running AND lease_expires_at IS NOT NULL AND lease_expires_at < now()`

### 7.2 優先順位

取得優先順位は以下とする。

1. `queued` の古い job
2. lease expired の `running` job

同一優先度では `created_at ASC, job_id ASC` とする。

### 7.3 SQL 方針

Worker は `SELECT ... FOR UPDATE SKIP LOCKED` を使用する。

概念 SQL:

```sql
WITH candidate AS (
    SELECT job_id
    FROM jobs
    WHERE
        job_type = ANY(:enabled_job_types)
        AND (
            status = 'queued'
            OR (
                status = 'running'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at < now()
            )
        )
    ORDER BY
        CASE WHEN status = 'queued' THEN 0 ELSE 1 END,
        created_at ASC,
        job_id ASC
    LIMIT :batch_size
    FOR UPDATE SKIP LOCKED
)
UPDATE jobs j
SET
    status = 'running',
    started_at = COALESCE(j.started_at, now()),
    locked_by = :worker_instance_id,
    locked_at = now(),
    lease_expires_at = now() + (:lease_seconds || ' seconds')::interval,
    updated_at = now()
FROM candidate
WHERE j.job_id = candidate.job_id
RETURNING j.*;
```

### 7.4 transaction boundary

job acquisition は 1 transaction で完了する。

取得後、handler 実行は別 transaction とする。

理由:

* 長時間 transaction を避ける
* `FOR UPDATE` lock を handler 実行中に保持しない
* DB 接続を占有しない

### 7.5 reclaim 判定

running job の lease が期限切れの場合、他 Worker は同じ job を reclaim できる。

reclaim 時:

* `started_at` は上書きしない
* `locked_by` は新 Worker に更新する
* `locked_at` は更新する
* `lease_expires_at` は更新する

Phase1 では reclaim 回数制限は行わない。

reclaim 回数制限は Phase2 以降で `jobs.reclaim_count` を追加した後に実装する。

---

## 8. Lease renewal / ownership

### 8.1 目的

長時間 job の実行中に lease が切れ、別 Worker が reclaim することを防ぐ。

### 8.2 lease renewal 条件

Worker は以下を満たす場合のみ lease 更新できる。

```text
jobs.job_id = target_job_id
AND jobs.status = 'running'
AND jobs.locked_by = current_worker_instance_id
```

### 8.3 lease renewal SQL

```sql
UPDATE jobs
SET
    locked_at = now(),
    lease_expires_at = now() + (:lease_seconds || ' seconds')::interval,
    updated_at = now()
WHERE
    job_id = :job_id
    AND status = 'running'
    AND locked_by = :worker_instance_id;
```

更新件数が 0 の場合、Worker は lease を失ったと判断する。

### 8.4 renewal timing

* default: 60 秒ごと
* job 処理の major step 境界でも更新してよい

例:

* extraction 完了後
* chunking 完了後
* embedding batch 完了後
* Qdrant upsert batch 完了後

### 8.5 ownership check

Worker は major step 境界および terminal update 前に ownership を確認する。

概念 SQL:

```sql
SELECT job_id
FROM jobs
WHERE
    job_id = :job_id
    AND status = 'running'
    AND locked_by = :worker_instance_id;
```

該当行が存在しない場合、Worker は lease ownership を失ったものとし、handler result を採用しない。

### 8.6 lease 喪失後の handler result 破棄

外部 API 呼び出しや Qdrant upsert は即時中断できない場合がある。

そのため、handler が完了した場合でも、terminal update 前に lease ownership を確認する。

ownership を失っている場合:

* handler result は破棄する
* `jobs.status` は更新しない
* success / failure の terminal update は行わない
* domain state を failed に更新しない
* warning log を出す
* 必要に応じて、後続 reclaim worker の処理に委ねる

### 8.7 terminal update safety

success / failure update は必ず `locked_by = current_worker_instance_id` を条件にする。

この条件により、Worker A が lease を失った後に遅れて terminal update してしまうことを防ぐ。

---

## 9. Terminal update

### 9.1 success update

Worker は handler 成功後、以下の SQL 条件で success update を行う。

```sql
UPDATE jobs
SET
    status = 'succeeded',
    finished_at = now(),
    error_code = NULL,
    error_message = NULL,
    lease_expires_at = NULL,
    updated_at = now()
WHERE
    job_id = :job_id
    AND status = 'running'
    AND locked_by = :worker_instance_id;
```

更新件数が 1 の場合のみ、job は succeeded になったとみなす。

更新件数が 0 の場合:

* Worker は lease ownership を失ったものとする
* handler result は破棄する
* succeeded への再更新は行わない
* warning log を出す

### 9.2 failure update

Worker は handler 失敗後、以下の SQL 条件で failure update を行う。

```sql
UPDATE jobs
SET
    status = 'failed',
    finished_at = now(),
    error_code = :error_code,
    error_message = :error_message,
    lease_expires_at = NULL,
    updated_at = now()
WHERE
    job_id = :job_id
    AND status = 'running'
    AND locked_by = :worker_instance_id;
```

更新件数が 1 の場合のみ、job は failed になったとみなす。

更新件数が 0 の場合:

* Worker は lease ownership を失ったものとする
* failure result は破棄する
* failed への再更新は行わない
* domain state も failed に更新しない
* warning log を出す

### 9.3 domain state update との関係

handler は処理途中で domain state を更新する場合がある。

ただし、lease ownership を失った Worker が domain state を確定更新することは避ける。

方針:

* major step 前後で ownership check を行う
* final domain state update 前にも ownership check を行う
* ownership を失っている場合、final domain state update を行わない
* すでに外部副作用が発生している場合は、後続 reclaim worker の idempotency / cleanup に委ねる

### 9.4 domain success no-op の必要性

以下の順序は起こり得る。

```text
domain final update succeeded
job terminal update failed due to lease lost
same job reclaimed by another worker
```

この場合、reclaim Worker は domain state を確認し、すでに成功状態なら no-op success で終える。

これにより、domain state と job terminal update の非原子的な隙間を安全に吸収する。

---

## 10. Handler 共通契約

### 10.1 Interface

handler は以下の interface を持つ。

```python
class JobHandler(Protocol):
    job_type: str

    def validate_payload(self, job: JobRecord) -> None:
        ...

    def handle(self, job: JobRecord, context: WorkerContext) -> HandlerResult:
        ...
```

### 10.2 HandlerResult

```python
@dataclass
class HandlerResult:
    status: Literal['succeeded']
    metadata: dict[str, Any] | None = None
```

handler が失敗する場合は、例外を送出する。

### 10.3 WorkerContext

`WorkerContext` は以下を提供する。

* `worker_instance_id`
* `job_repository`
* `lease_manager`
* `request_id` または `job_trace_id`
* `now_provider`
* DB session factory
* Qdrant client
* embedding client
* storage service

handler は major step 境界で `context.lease_manager.assert_owned(job_id)` を呼び出す。

### 10.4 例外分類

| exception                  | job error_code           | HTTP 対応   |
| -------------------------- | ------------------------ | --------- |
| `PayloadValidationError`   | `invalid_job_payload`    | 500 相当    |
| `DomainStateConflictError` | domain specific          | 409 相当    |
| `ExternalDependencyError`  | external dependency code | 503 相当    |
| `InternalPipelineError`    | internal pipeline code   | 500 相当    |
| `RetryableWorkerError`     | handler specific         | retry 対象  |
| `NonRetryableWorkerError`  | handler specific         | retry 非推奨 |
| `LeaseLostError`           | terminal update しない      | なし        |

`LeaseLostError` は job failure ではない。

Worker は `LeaseLostError` を捕捉しても `jobs.status` を更新しない。

handler も `LeaseLostError` 発生時に domain state を failed に更新しない。

### 10.5 Handler が直接行ってよいこと

handler は domain state の更新を行ってよい。

例:

* `document_versions.status = ready`
* `document_versions.status = failed`
* `evaluation_runs.status = running`
* `evaluation_runs.status = succeeded`
* `chat_messages` 作成

ただし、final domain state update 前に lease ownership を確認する。

### 10.6 Handler が直接行わないこと

handler は以下を直接行わない。

* `jobs.status = succeeded`
* `jobs.status = failed`
* `jobs.finished_at` 更新
* retry job 作成

これらは Worker runner / service が統一して行う。

### 10.7 Handler idempotency

handler は少なくとも以下に耐える。

* 同一 job の再実行
* expired running job の reclaim
* 外部 upsert の二重実行
* domain state がすでに成功済みの状態

処理済み判定を domain state から行える場合、handler は no-op success として終了してよい。

---

## 11. Retry 設計

### 11.1 retry 対象

retry 可能なのは `status = failed` の job のみとする。

以下は retry 不可:

* `queued`
* `running`
* `succeeded`
* `canceled`

### 11.2 retry 作成 API の責務

API `POST /api/v1/jobs/{job_id}/retry` は以下を行う。

1. job 存在確認
2. 対象 job が failed であることを確認
3. original source job を解決
4. active retry の存在確認
5. retry job 作成
6. `202 Accepted` を返す

### 11.3 original source job 解決

```text
if failed_job.retry_of_job_id IS NULL:
    source_job_id = failed_job.job_id
else:
    source_job_id = failed_job.retry_of_job_id
```

新しい retry job は以下を持つ。

```text
retry_of_job_id = source_job_id
status = 'queued'
started_at = NULL
finished_at = NULL
```

### 11.4 retry job 複製方針

retry job は original job の以下を引き継ぐ。

* `job_type`
* `target_type`
* `target_id`
* `payload_json`

retry job が上書きする項目:

* `job_id`: 新規採番
* `retry_of_job_id`: original source job_id
* `status`: `queued`
* `started_at`: `NULL`
* `finished_at`: `NULL`
* `locked_by`: `NULL`
* `locked_at`: `NULL`
* `lease_expires_at`: `NULL`
* `error_code`: `NULL`
* `error_message`: `NULL`
* `created_at`: now()

特に `message_edit_regeneration` は target 必須であるため、retry job でも `target_type = chat_message` / `target_id = 編集対象 user message id` を引き継ぐ。

### 11.5 active retry 競合

active retry は以下とする。

```text
retry_of_job_id = source_job_id
AND status IN ('queued', 'running')
```

存在する場合、API は以下を返す。

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

### 11.6 retry payload

retry job の payload は original job の payload を複製する。

必須:

* `requested_by_user_id`: retry を要求した user_id に更新する

任意:

* `original_requested_by_user_id`: original job 作成者の user_id
* `retry_requested_at`: retry 要求時刻
* `retry_reason`: retry 理由

例:

```json
{
  "requested_by_user_id": 2,
  "original_requested_by_user_id": 1,
  "retry_requested_at": "2026-04-30T00:00:00Z",
  "retry_reason": "manual_retry"
}
```

raw data は payload に含めない。

### 11.7 retry と domain state

retry job 作成時には domain state を原則変更しない。

例外:

* failed document version retry では、Worker 実行開始時に `processing` へ戻す
* API retry 作成時点では戻さない

理由:

* queued のまま Worker が実行しない可能性がある
* domain state と実行状態のズレを避ける

---

## 12. document_ingest handler

### 12.1 目的

`document_ingest` は文書取り込み処理を行う。

処理対象:

* text extraction
* metadata extraction
* chunking
* document_chunks insert
* embedding
* Qdrant upsert
* document_versions status update

### 12.2 payload

```json
{
  "logical_document_id": 1000,
  "document_version_id": 2001,
  "requested_by_user_id": 1
}
```

### 12.3 前提 state

通常 ingest 開始時:

```text
document_versions.status = 'processing'
document_versions.error_code IS NULL
```

failed retry 開始時:

```text
document_versions.status = 'failed'
document_versions.error_code IS NOT NULL
```

reclaim 再実行時:

```text
document_versions.status = 'processing'
```

すでに処理済みの場合:

```text
document_versions.status = 'ready'
```

### 12.4 ready no-op success

handler 開始時に `document_versions.status = ready` の場合、対象 document version は取り込み済みとみなし、no-op success として終了する。

この場合:

* text extraction は再実行しない
* chunking は再実行しない
* embedding は再実行しない
* Qdrant upsert は再実行しない
* Qdrant point 存在確認は行わない
* `document_versions.is_active` は変更しない
* active 化は approve API の責務である

Phase1 では、`document_versions.status = ready` は Qdrant upsert 成功済みを含む完全成功状態とみなす。

ready だが Qdrant point 不足が検出された場合、`document_ingest` の no-op success では修復しない。

修復は以下のいずれかで扱う。

* reindex job
* `qdrant_mirror_update`
* Phase2 以降の repair job

### 12.5 reclaim / retry 開始時 artifact cleanup

`document_ingest` handler は、開始時に `document_versions` row を lock した後、対象 `document_version_id` に既存 artifacts が存在する場合、cleanup してから処理を開始する。

対象 artifacts:

* 同一 `document_version_id` の Qdrant points
* 同一 `document_version_id` の `document_chunks`

cleanup 対象ケース:

* failed retry
* lease expired reclaim による processing job 再実行
* 同一 job の再実行
* 前 Worker が partial artifact を作成した可能性がある場合

cleanup しないケース:

* `document_versions.status = ready` の no-op success

### 12.6 処理フロー

```text
validate payload
 -> assert lease ownership
 -> load logical_document / document_version
 -> transaction 1:
      lock document_version row
      if document_version.status = ready: no-op success
      validate logical_document.status = active
      collect existing document_chunk_ids / qdrant point ids for cleanup
      set document_versions.status = processing, error_code = null when needed
    commit
 -> external cleanup:
      cleanup Qdrant points outside DB transaction
 -> transaction 2:
      cleanup RDB document_chunks after Qdrant cleanup success
    commit
 -> assert lease ownership
 -> text extraction
 -> assert lease ownership
 -> metadata extraction
 -> chunking
 -> assert lease ownership
 -> transaction 3:
      document_chunks bulk insert
    commit
 -> embedding outside DB transaction
 -> assert lease ownership
 -> Qdrant upsert outside DB transaction
 -> assert lease ownership
 -> transaction 4:
      update document_versions.status = ready
    commit
 -> finish
```

### 12.7 transaction boundary

#### transaction 1: state preparation / cleanup target collection

* document_version row lock
* archived check
* `status = ready` の場合は no-op success 判定
* failed retry / processing reclaim / re-run の場合は cleanup 対象の `document_chunk_id` / Qdrant point id を取得
* 必要に応じて `status = processing`, `error_code = null`
* commit

この transaction 内では Qdrant cleanup を実行しない。

#### external cleanup

* transaction 1 で取得した point id をもとに Qdrant points を cleanup する
* Qdrant cleanup は DB transaction 外で実行する
* 対象 point が存在しない場合は cleanup success とする
* Qdrant cleanup failure は `qdrant_cleanup_failed` とする

#### transaction 2: RDB chunks cleanup

* Qdrant cleanup 成功後に、対象 `document_chunks` を削除する
* commit

#### transaction 3: chunk insert

* document_chunks bulk insert
* inserted `document_chunk_id` 一覧を保持する
* commit

#### external process

* embedding generation
* Qdrant upsert

#### transaction 4: success update

* terminal update 前 ownership check
* `document_versions.status = ready`
* `document_versions.error_code = null`
* `document_versions.is_active` は変更しない
* 取り込み直後は approve 待ちのため `is_active = false`
* commit

### 12.8 failed ingest retry

failed ingest retry は同一 `document_version_id` に対して再実行する。

開始時:

```text
collect cleanup targets in DB transaction
cleanup Qdrant points outside DB transaction
cleanup document_chunks in DB transaction
document_versions.status = 'processing'
document_versions.error_code = NULL
```

成功時:

```text
document_versions.status = 'ready'
document_versions.error_code = NULL
document_versions.is_active = false
```

失敗時:

```text
document_versions.status = 'failed'
document_versions.error_code = mapped_error_code
```

自動 active 化はしない。

active 化は approve API のみが行う。

### 12.9 failed retry / reclaim 開始時 cleanup

failed retry / reclaim 再実行開始時、以下を cleanup する。

1. DB transaction で cleanup 対象 `document_chunk_id` / Qdrant point id を取得する
2. DB transaction を commit する
3. Qdrant points cleanup を DB transaction 外で実行する
4. Qdrant cleanup 成功後、DB transaction で既存 `document_chunks` を削除する
5. 必要に応じて `document_versions.status = processing`, `error_code = null` へ更新する

Qdrant cleanup 方針:

* 対象 point が存在しない場合は cleanup 成功扱いとする
* Qdrant cleanup に失敗した場合は job failed とする
* 古い point が残ったまま新 ingest を進めない

開始時 Qdrant cleanup failure 時の状態更新:

```text
document_versions.status = 'failed'
document_versions.error_code = 'qdrant_cleanup_failed'
jobs.status = 'failed'
jobs.error_code = 'qdrant_cleanup_failed'
```

### 12.10 chunk insert 後の partial failure cleanup

`document_chunks` insert 後に embedding / Qdrant upsert / success update が失敗した場合、以下の順序で cleanup する。

1. 対象 `document_version_id` の `document_chunk_id` 一覧を取得する
2. DB transaction を閉じる
3. 対応する Qdrant point cleanup を DB transaction 外で試みる
4. Qdrant cleanup 成功後、RDB `document_chunks` を削除する
5. `document_versions.status = failed`, `error_code = mapped_error_code` に更新する
6. job を failed に更新する

Qdrant cleanup で対象 point が存在しない場合は cleanup 成功扱いとする。

Qdrant cleanup 自体が失敗した場合:

* job は failed とする
* `document_versions.status = failed` とする
* `document_versions.error_code` は original error を優先し、必要に応じて cleanup failure を log に残す
* RDB final check により failed version は retrieval 対象外となる

### 12.11 success criteria

成功条件:

* text extraction 成功
* chunking 成功
* `document_chunks` 保存成功
* embedding 成功
* Qdrant upsert 成功
* lease ownership が維持されている
* `document_versions.status = ready` 更新成功
* job success terminal update 成功

または、開始時に `document_versions.status = ready` であるため no-op success した場合。

### 12.12 failure handling

失敗時:

* lease ownership がある場合のみ domain failed update を行う
* `document_versions.status = failed`
* `document_versions.error_code = mapped_error_code`
* job は `failed`
* partial chunks は削除する
* Qdrant upsert 済み point は削除を試みる

`LeaseLostError` の場合:

* `document_versions.status = failed` へ更新しない
* job state を更新しない
* 後続 reclaim Worker に処理を委ねる

代表 error_code:

| error_code               | 説明                             |
| ------------------------ | ------------------------------ |
| `file_not_found`         | storage 上の対象ファイルが存在しない         |
| `text_extraction_failed` | text extraction 失敗             |
| `chunking_failed`        | chunking 失敗                    |
| `embedding_failed`       | embedding 失敗                   |
| `qdrant_upsert_failed`   | Qdrant upsert 失敗               |
| `qdrant_cleanup_failed`  | Qdrant cleanup 失敗              |
| `document_archived`      | 対象 logical document が archived |
| `invalid_job_payload`    | payload 不正                     |

---

## 13. qdrant_mirror_update handler

### 13.1 目的

Qdrant payload mirror を RDB 状態に追随させる。

主な用途:

* document archive 後の `is_active=false` mirror
* active version 切替後の version mirror 更新
* manual repair

### 13.2 payload

logical document 単位:

```json
{
  "logical_document_id": 1000,
  "mirror_action": "mark_inactive"
}
```

version 単位:

```json
{
  "document_version_id": 2001,
  "mirror_action": "sync_payload"
}
```

### 13.3 処理フロー

```text
validate payload
 -> assert lease ownership
 -> load RDB state
 -> calculate expected Qdrant payload
 -> update Qdrant points outside DB transaction
 -> assert lease ownership
 -> finish
```

### 13.4 missing point 方針

Phase1 では、`qdrant_mirror_update` で対象 point が存在しない場合は `qdrant_point_not_found` として failed にする。

これは retrieval correctness のためではなく、運用検知重視のためである。

archive 後の retrieval eligibility は RDB final check を正とするため、missing point / mirror update failure によって archived document が retrieval 対象に戻ることはない。

### 13.5 failure handling

Qdrant mirror update が失敗しても、RDB final check を正とする。

ただし、job としては failed にする。

理由:

* mirror update の失敗は運用上検知すべき
* retrieval correctness は RDB final check で守られる

代表 error_code:

* `qdrant_payload_update_failed`
* `qdrant_point_not_found`
* `invalid_job_payload`

`LeaseLostError` の場合、job state / domain state は更新しない。

---

## 14. message_edit_regeneration handler

### 14.1 目的

user message 編集後に、RAG 回答を再生成する。

### 14.2 payload

```json
{
  "chat_session_id": 10,
  "chat_message_id": 101,
  "requested_by_user_id": 1
}
```

### 14.3 target 必須

`message_edit_regeneration` job は以下を必須とする。

```text
target_type = 'chat_message'
target_id = chat_message_id
```

### 14.4 job_trace_id

`message_edit_regeneration` は job_id から deterministic な `job_trace_id` を生成する。

形式:

```text
job:{job_id}
```

例:

```text
job:400
```

この `job_trace_id` を RAG pipeline の `request_id` として渡す。

`retrieval_runs.request_id` にはこの `job_trace_id` を保存する。

### 14.5 retrieval_runs.request_id の一意性方針

`retrieval_runs.request_id` は trace 用 field であり、Phase1 では UNIQUE 制約を持たない。

同一 `job_trace_id` に複数の retrieval_run が存在し得る。

理由:

* reclaim / re-run 時に stale running run を failed にした後、新しい retrieval_run を作成するため
* failed retrieval_run を再利用せず、新規 retrieval_run を作る方針のため

同一 `job_trace_id` に複数 retrieval_run が存在する場合、判定には `status`, `started_at`, `retrieval_run_id` を用いる。

もし将来 request_id を一意にしたい場合は、以下のような attempt 付き trace id を検討する。

```text
job:{job_id}:attempt:{n}
```

Phase1 では attempt 付き ID は採用しない。

### 14.6 前提条件

* 対象 message が存在する
* 対象 message は user message
* 対象 session が active
* 対象 session が temporary expired ではない
* owner が一致する

### 14.7 retrieval_run 判定優先順位

handler 開始時に、同一 `job_trace_id` を持つ retrieval_run を確認する。

同一 `job_trace_id` に複数 retrieval_run がある場合、判定優先順位は以下とする。

1. `succeeded` retrieval_run + assistant_message が存在する
2. `running` retrieval_run が存在する
3. `failed` retrieval_run のみ存在する
4. retrieval_run が存在しない

複数 failed retrieval_run がある場合、debug 表示上の代表は以下で選ぶ。

```text
started_at DESC, retrieval_run_id DESC
```

### 14.8 stale retrieval_run 処理

#### succeeded retrieval_run + assistant_message が存在する場合

以下を満たす場合、handler は no-op success とする。

```text
retrieval_runs.request_id = job_trace_id
AND retrieval_runs.status = 'succeeded'
AND 対応する assistant_message が存在する
```

#### running retrieval_run が存在する場合

同一 `job_trace_id` の running retrieval_run が残っている場合、stale run とみなす。

Phase1 では以下の順序で処理する。

1. 同一 `job_trace_id` の succeeded retrieval_run + assistant_message が存在しないことを確認する
2. running retrieval_run のみを stale failed に更新する
3. 新しい retrieval_run を作成して再実行する

stale 更新 SQL 条件:

```sql
UPDATE retrieval_runs
SET
    status = 'failed',
    error_code = 'stale_retrieval_run_reclaimed',
    finished_at = now(),
    updated_at = now()
WHERE
    request_id = :job_trace_id
    AND status = 'running';
```

この SQL では `status = 'running'` を必須条件とし、すでに `succeeded` になった retrieval_run を誤って failed にしない。

#### failed retrieval_run が存在する場合

同一 `job_trace_id` の failed retrieval_run が存在する場合、既存 run は再利用しない。

Phase1 では、新しい retrieval_run を作成して再実行する。

### 14.9 処理フロー

```text
validate payload
 -> assert lease ownership
 -> generate job_trace_id = job:{job_id}
 -> lock chat_session row
 -> lock target chat_message row
 -> validate editable state
 -> check retrieval_run by job_trace_id
 -> if succeeded run + assistant exists: no-op success
 -> if running run exists: mark stale run failed with request_id = job_trace_id AND status = running
 -> if failed run exists: do not reuse
 -> check latest lineage assistant existence
 -> create new retrieval_run with request_id = job_trace_id
 -> run RAG pipeline with request_id = job_trace_id
 -> assert lease ownership
 -> final transaction:
      create new assistant_message
      link assistant_message.linked_retrieval_run_id
      update edit lineage visibility
 -> finish
```

### 14.10 old lineage 方針

* 編集前の assistant message は物理削除しない
* 通常 UI では最新 lineage のみ表示する
* debug mode では旧 lineage を表示可能とする

### 14.11 idempotency / 二重生成防止

Phase1 では、同一 `chat_message_id` の active edit job を DB 制約または service validation により 1 本に制限する。

reclaim による同一 job 再実行時は、lease ownership validation と terminal update safety により、複数 Worker が同じ job を同時に成功確定することを防ぐ。

retrieval_run の重複防止は `job_trace_id` により行う。

assistant message の重複防止は final transaction 前に以下を確認して行う。

* 対象 user message の最新 lineage に、すでに有効な assistant message が存在するか
* 同一 `job_trace_id` の succeeded retrieval_run に対応する assistant message が存在するか
* 対象 user message の編集後 content に対応する assistant response がすでに確定しているか

存在する場合、handler は no-op success として終了してよい。

Phase2 以降では、`edit_operation_id` または `origin_job_id` を retrieval_run / assistant message metadata に持たせる拡張を検討する。

### 14.12 failure handling

RAG pipeline 失敗時:

* lease ownership がある場合のみ failed domain update を行う
* job は failed
* retrieval_run は failed として残す
* assistant_message は作成しない
* user message の編集内容は維持する
* UI は再生成失敗として表示してよい

`LeaseLostError` の場合:

* job state は更新しない
* retrieval_run を failed にできるとは限らない
* reclaim Worker が同一 `job_trace_id` の running retrieval_run を stale として処理する

代表 error_code:

* `message_not_found`
* `message_not_editable`
* `archived_session_readonly`
* `retrieval_failed`
* `rerank_failed`
* `generation_failed`
* `citation_build_failed`
* `stale_retrieval_run_reclaimed`

---

## 15. evaluation_run handler

### 15.1 目的

評価ケースに対して RAG pipeline を実行し、評価結果を保存する。

### 15.2 payload

```json
{
  "evaluation_run_id": 900,
  "evaluation_scope": "baseline",
  "requested_by_user_id": 1
}
```

### 15.3 active evaluation job 制限

同一 `evaluation_run_id` の `queued` / `running` evaluation job は Phase1 で 1 本に制限する。

制御方針:

* Phase1 では service validation を基本とする
* evaluation job 作成時に、同一 `target_type = 'evaluation_run'` かつ同一 `target_id = evaluation_run_id` の active job がないことを確認する
* active job は `status IN ('queued', 'running')` とする
* active job が存在する場合、新規 job を作成せず conflict とする

可能であれば、将来以下のような partial unique index を DB 防衛線として追加する。

```sql
CREATE UNIQUE INDEX ux_jobs_active_evaluation_run_target
ON jobs (target_type, target_id)
WHERE job_type = 'evaluation_run'
  AND status IN ('queued', 'running');
```

DDL v1.8 にこの制約がない場合、Phase1 では service validation + transaction で防御する。

### 15.4 状態遷移

評価 run は以下の順に遷移する。

```text
queued -> running -> succeeded
queued -> running -> failed
```

Worker は evaluation job 実行開始時に以下を更新する。

```text
evaluation_runs.status = 'running'
evaluation_runs.started_at = now()
evaluation_runs.finished_at = NULL
evaluation_runs.error_code = NULL
```

成功時:

```text
evaluation_runs.status = 'succeeded'
evaluation_runs.finished_at = now()
```

失敗時:

```text
evaluation_runs.status = 'failed'
evaluation_runs.finished_at = now()
evaluation_runs.error_code = mapped_error_code
```

### 15.5 handler 開始時の no-op / cleanup 方針

Phase1 では、evaluation handler 開始時に必ず `evaluation_runs` row を lock して状態を確認する。

#### succeeded の場合

`evaluation_runs.status = succeeded` の場合、評価は完了済みとみなし no-op success とする。

既存 `evaluation_run_items` / `evaluation_results` は削除しない。

#### queued / running / failed の場合

`evaluation_runs.status IN ('queued', 'running', 'failed')` の場合、retry / reclaim / re-run のいずれであっても、既存 `evaluation_results` / `evaluation_run_items` を削除してから再評価する。

これにより、lease lost reclaim 時の partial items による重複や集計ズレを防ぐ。

### 15.6 開始時 transaction boundary

通常実行、retry、reclaim のいずれでも、handler 開始時に以下を 1 transaction で実行する。

1. `evaluation_runs` row を lock
2. `status = succeeded` なら no-op success 判定して終了
3. `status IN ('queued', 'running', 'failed')` なら `status = running` に更新
4. `started_at = now()` に更新
5. `finished_at = null` に更新
6. `error_code = null` に更新
7. 既存 `evaluation_results` を削除
8. 既存 `evaluation_run_items` を削除
9. commit

その後、評価 case 実行に入る。

長時間 transaction を避けるため、case 実行中は evaluation_run row lock を保持しない。

`started_at` は「今回の評価開始時刻」として扱い、retry / reclaim では `now()` に更新してよい。

### 15.7 通常処理フロー

```text
validate payload
 -> assert lease ownership
 -> start transaction:
      lock evaluation_run row
      if status = succeeded: no-op success
      set status = running
      set started_at = now()
      set finished_at = null
      set error_code = null
      delete existing evaluation_results
      delete existing evaluation_run_items
    commit
 -> load evaluation cases
 -> for each case:
      assert lease ownership
      run retrieval / generation / scoring
      save evaluation_run_item
      save evaluation_results
 -> aggregate metrics
 -> assert lease ownership
 -> mark evaluation_run succeeded
```

### 15.8 partial failure 方針

Phase1 では、1 case の失敗で evaluation run 全体を failed にしてよい。

ただし、失敗前に保存済みの `evaluation_run_items` は削除しない。

理由:

* 失敗箇所のデバッグに使える
* 評価品質改善の trace として有用

次回 retry / reclaim 開始時には、開始時 transaction で既存 items / results を削除して再評価する。

### 15.9 temporary chat 起源 retrieval_run との関係

temporary chat 起源の retrieval_run は evaluation_run_items から参照しない。

そのため、temporary cleanup は evaluation 系 FK と衝突しない前提とする。

評価対象にする retrieval_run は、永続 chat または standalone evaluation 用 trace に限定する。

### 15.10 failure handling

失敗時:

* lease ownership がある場合のみ `evaluation_runs.status = failed` に更新する
* `evaluation_runs.finished_at = now()`
* `evaluation_runs.error_code = mapped_error_code`
* job は failed

`LeaseLostError` の場合:

* evaluation_run を failed に更新しない
* job state を更新しない
* 後続 reclaim Worker が既存 partial items を削除して再評価する

---

## 16. temporary_chat_cleanup handler

### 16.1 目的

temporary chat の TTL 到達後に、関連データを物理削除する。

### 16.2 payload

```json
{
  "chat_session_id": 10
}
```

### 16.3 削除対象

削除対象:

* temporary chat session
* chat messages
* summary memories
* chat tags
* retrieval runs
* retrieval run items
* citations

削除しない対象:

* audit_logs
* users
* documents
* document_versions
* document_chunks
* evaluation results

### 16.4 lock 方針

cleanup 開始時に `chat_sessions` row を lock する。

```sql
SELECT *
FROM chat_sessions
WHERE chat_session_id = :chat_session_id
FOR UPDATE;
```

対象 session が以下を満たすことを確認する。

```text
temporary_flag = true
AND ttl_expires_at <= now()
```

満たさない場合は no-op success とする。

対象 session が存在しない場合も no-op success とする。

### 16.5 linked_retrieval_run_id NULL 化

`chat_messages.linked_retrieval_run_id` が循環 FK に関わる場合、削除前に NULL 化する。

```text
chat_messages.linked_retrieval_run_id = NULL
```

### 16.6 削除 transaction 直前 ownership check

temporary cleanup は削除 transaction 開始直前に lease ownership を確認する。

削除 transaction は短時間で完了させ、途中で lease renewal を必要としない単位にする。

対象件数が多く長時間化する場合は、Phase2 以降で batch cleanup を検討する。

### 16.7 削除順序

推奨削除順序:

```text
assert lease ownership
 -> lock chat_session
 -> validate temporary ttl expired
 -> assert lease ownership immediately before delete transaction
 -> set chat_messages.linked_retrieval_run_id = NULL
 -> delete citations for retrieval_runs in session
 -> delete retrieval_run_items for retrieval_runs in session
 -> delete retrieval_runs
 -> delete summary_memories
 -> delete chat_tags
 -> delete chat_messages
 -> delete chat_sessions
```

FK cascade が定義されている場合でも、Phase1 実装では削除順序を明示し、意図しない cascade に依存しすぎない。

### 16.8 audit log 方針

cleanup 自体は audit_logs に記録してよい。

ただし、対象 temporary chat の内容そのものは audit_logs に保存しない。

audit_logs は削除しない。

保存してよい audit 項目例:

```json
{
  "action_type": "temporary_chat_cleanup",
  "target_type": "chat_session",
  "target_id": 10,
  "actor_user_id": null,
  "details": {
    "deleted_message_count": 12,
    "deleted_retrieval_run_count": 3,
    "deleted_citation_count": 8,
    "deleted_summary_memory_count": 1
  }
}
```

保存禁止:

* message content
* prompt
* answer content
* chunk text
* raw citation snippet
* PII

`actor_user_id` は system user を用意する場合は system user id、用意しない場合は `NULL` とする。

### 16.9 failure handling

cleanup 失敗時:

* job は failed
* 削除 transaction は rollback
* 次回 retry で再実行可能

`LeaseLostError` の場合:

* job state は更新しない
* 削除 transaction 前なら何もしない
* 削除 transaction 中の失敗は rollback する

---

## 17. Failure handling 共通方針

### 17.1 error_code mapping

Worker は handler exception を job error_code に変換する。

| category            | error_code 例                                                                      |
| ------------------- | --------------------------------------------------------------------------------- |
| payload 不正          | `invalid_job_payload`                                                             |
| domain state 不整合    | `invalid_domain_state`                                                            |
| document ingest     | `text_extraction_failed`, `embedding_failed`, `qdrant_upsert_failed`              |
| RAG                 | `retrieval_failed`, `rerank_failed`, `generation_failed`, `citation_build_failed` |
| evaluation          | `evaluation_case_failed`, `evaluation_scoring_failed`                             |
| cleanup             | `cleanup_failed`                                                                  |
| external dependency | `external_dependency_unavailable`, `external_dependency_timeout`                  |
| lease lost          | terminal update なし                                                                |

### 17.2 error_message

`error_message` は user safe / operator safe な短文にする。

禁止:

* stack trace 全文
* raw text
* PII
* prompt 全文
* secret
* local absolute path

詳細な stack trace は application log に出すが、PII / secret は redaction する。

### 17.3 terminal update

Worker は failure 時に ownership がある場合のみ以下を更新する。

```text
jobs.status = 'failed'
jobs.finished_at = now()
jobs.error_code = mapped_error_code
jobs.error_message = redacted_message
jobs.lease_expires_at = NULL
```

terminal update の SQL には必ず以下を含める。

```text
status = 'running'
AND locked_by = current_worker_instance_id
```

### 17.4 domain state update の責務

handler failure 時の domain state update は handler が行う。

例:

* document ingest failure: `document_versions.status = failed`
* evaluation failure: `evaluation_runs.status = failed`
* message regeneration failure: retrieval_run を failed にする

Worker runner は domain state の詳細を知らない。

ただし、handler は final domain state update 前に lease ownership を確認する。

### 17.5 LeaseLostError の扱い

`LeaseLostError` は failure ではなく ownership 喪失である。

`LeaseLostError` 発生時:

* handler は domain state を failed に更新しない
* Worker runner は `jobs.status` を更新しない
* terminal update は行わない
* 後続 reclaim Worker が domain state / artifacts / stale trace を確認して処理する

---

## 18. Idempotency / partial failure

### 18.1 基本方針

Worker は at-least-once 実行を前提とし、handler は再実行に耐える設計とする。

### 18.2 domain success no-op

handler 開始時に domain state がすでに成功状態の場合、handler は no-op success として終了してよい。

これにより、domain final update 後に job terminal update が失敗したケースを吸収する。

### 18.3 document_ingest

idempotency 方針:

* `document_versions.status = ready` なら no-op success
* ready no-op success では Qdrant point 存在確認をしない
* Qdrant repair は reindex / mirror update / repair job の責務とする
* `document_chunks` は `document_version_id` 単位で再作成可能
* Qdrant point は deterministic point id を使う
* Qdrant upsert は二重実行可能
* failed retry / reclaim / re-run では既存 artifacts を cleanup してから再実行する
* Qdrant cleanup は DB transaction 外で実行する
* cleanup failure は job failed とする

### 18.4 qdrant_mirror_update

idempotency 方針:

* payload update は同じ値で何度実行してもよい
* 対象 point が存在しない場合、Phase1 では運用検知重視で `qdrant_point_not_found` として failed にする
* archive 後の retrieval eligibility は RDB final check を正とする

### 18.5 message_edit_regeneration

idempotency 方針:

* `job_trace_id = job:{job_id}` を使う
* `retrieval_runs.request_id` は UNIQUE ではない
* 同一 `job_trace_id` の succeeded retrieval_run + assistant_message があれば no-op success
* 同一 `job_trace_id` の running retrieval_run があれば stale として failed にする
* stale 更新条件は `request_id = job_trace_id AND status = running`
* 同一 `job_trace_id` の failed retrieval_run は再利用せず新規 run を作る
* 複数 failed retrieval_run がある場合、debug 代表は `started_at DESC, retrieval_run_id DESC` で選ぶ
* 同一 `chat_message_id` の active edit job を 1 本に制限する
* final transaction 前に最新 assistant existence を確認する
* reclaim による同一 job 再実行時は lease ownership により二重 terminal update を防ぐ
* assistant の重複防止は lineage / latest assistant / job_trace_id 確認で行う

Phase2 以降候補:

* `edit_operation_id`
* `origin_job_id`
* retrieval_run / assistant_message metadata への job trace 紐づけ
* `job:{job_id}:attempt:{n}` 形式の attempt trace

### 18.6 evaluation_run

idempotency 方針:

* `evaluation_runs.status = succeeded` なら no-op success
* queued / running / failed なら開始時に既存 item / result を削除して再作成する
* retry / reclaim / re-run の挙動を統一する
* 同一 `evaluation_run_id` の active evaluation job は 1 本に制限する

### 18.7 temporary_chat_cleanup

idempotency 方針:

* 対象 session が存在しない場合は no-op success
* temporary 条件を満たさない場合は no-op success
* 削除順序は transaction で実行する

---

## 19. Concurrency 設計

### 19.1 複数 Worker

複数 Worker は同時起動可能とする。

安全性は以下で担保する。

* `FOR UPDATE SKIP LOCKED`
* lease
* `locked_by`
* terminal update ownership condition
* domain row lock
* unique constraint
* idempotent handler
* domain success no-op
* reclaim cleanup

### 19.2 job acquisition race

同じ queued job を複数 Worker が取得しようとしても、`FOR UPDATE SKIP LOCKED` により 1 Worker のみが取得する。

### 19.3 lease lost race

Worker A が lease を失い、Worker B が reclaim した場合、Worker A は terminal update に失敗する。

理由:

```text
WHERE job_id = :job_id
AND status = 'running'
AND locked_by = :worker_a_id
```

この条件に一致しなくなるためである。

Worker A は handler result を破棄する。

### 19.4 active retry race

retry API は active retry check と retry job insert を同一 transaction で実行する。

DB の partial unique index がある場合は DB 制約を最終防衛線とする。

### 19.5 document version race

同一 document version に対する ingest / reindex は同時実行しない。

handler 開始時に `document_versions` row を lock する。

reclaim / retry / re-run では、同一 `document_version_id` の existing artifacts を cleanup してから再実行する。

Qdrant cleanup は DB transaction 外で実行する。

### 19.6 message edit race

同一 `chat_message_id` の active edit job は 1 本に制限する。

同一 job の reclaim では `job_trace_id` により retrieval_run の重複・stale running run を検出する。

`retrieval_runs.request_id` は UNIQUE ではないため、同一 `job_trace_id` の複数 run は `status`, `started_at`, `retrieval_run_id` により扱う。

### 19.7 evaluation run race

同一 `evaluation_run_id` の active job は 1 本に制限する。

Phase1 では service validation を基本とし、可能であれば将来 partial unique index で防衛する。

reclaim / retry / re-run では、開始時 transaction により既存 item / result を削除して再評価する。

### 19.8 temporary cleanup race

temporary cleanup は対象 `chat_sessions` row を lock する。

同じ session の投稿・編集・cleanup が競合する場合、cleanup が優先される。

TTL 到達後の temporary session への API 操作は 404 とする。

---

## 20. Logging / Audit / Observability

### 20.1 structured logging

Worker log は structured log とする。

含める項目:

* `request_id` または `job_id`
* `job_trace_id`
* `job_type`
* `job_id`
* `worker_instance_id`
* `status`
* `error_code`
* `duration_ms`
* `lease_lost`
* `reclaimed`
* `stale_retrieval_run_id`

### 20.2 log redaction

以下は log に出さない。

* raw content
* PII
* secrets
* prompt 全文
* document chunk 全文

### 20.3 audit_logs

以下は audit 対象とする。

* document ingest request
* document approve
* document archive
* job retry request
* evaluation run request
* system settings update
* security relevant external API event
* 管理上必要な external API event
* temporary cleanup summary

すべての embedding / rerank / generation 呼び出しを audit_logs に記録しない。

external API usage は application log / metrics を基本とし、security relevant または管理上必要なイベントのみ audit_logs に記録する。

Worker 内部の細かい step すべてを audit に保存する必要はない。

### 20.4 metrics

Phase1 では application log ベースでよい。

将来 metrics 候補:

* job queue depth
* job success count
* job failure count
* job duration p50 / p95
* reclaim count
* lease renewal failure count
* terminal update lost ownership count
* stale retrieval_run count
* qdrant_cleanup_failed count
* handler 別 failure count

---

## 21. API 連携

### 21.1 jobs API

API v1.9 と以下を揃える。

* `source_job_id` は `jobs.retry_of_job_id`
* `created_at` は job 作成時刻
* `started_at` は worker が running にした時刻
* `finished_at` は terminal state 到達時刻
* `scheduled_at` は使用しない

### 21.2 retry API

`POST /api/v1/jobs/{job_id}/retry` は Worker ではなく API service が retry job を作成する。

Worker は retry job を通常 queued job と同じように処理する。

### 21.3 document API

文書 upload API は `document_ingest` job を作成し、`202 Accepted` を返す。

Worker 成功後:

```text
document_versions.status = 'ready'
document_versions.is_active = false
```

approve API により active 化する。

### 21.4 RAG API

`/rag/ask` 自体は同期 API であり、通常 job 化しない。

ただし、message edit regeneration は job 化する。

`message_edit_regeneration` では `job_trace_id = job:{job_id}` を RAG pipeline の request_id として渡し、`retrieval_runs.request_id` に保存する。

`retrieval_runs.request_id` は Phase1 では UNIQUE ではない。

### 21.5 evaluation API

評価実行 API は `evaluation_run` job を作成し、`202 Accepted` を返す。

同一 `evaluation_run_id` の active evaluation job は 1 本に制限する。

---

## 22. Security

### 22.1 Worker 権限

Worker は DB と Qdrant にアクセスする。

Worker 用 DB user は Phase1 では backend と同一でもよいが、将来的には分離可能にする。

### 22.2 secret 管理

Worker が使う外部 API key は環境変数または secret manager から読み込む。

secret は job payload に保存しない。

### 22.3 file access

Worker は storage service 経由でファイルにアクセスする。

local path を payload に直接保存しない。

### 22.4 external API

外部 API 呼び出し時は以下を守る。

* request_id / job_id / job_trace_id を trace に含める
* PII masking
* timeout 設定
* retry with backoff
* raw prompt logging 禁止

---

## 23. Test 方針

### 23.1 unit test

対象:

* job acquisition query builder
* lease ownership check
* terminal update SQL condition
* retry source job resolution
* retry job target/payload copy
* retry payload requested_by_user_id update
* active retry check
* job_trace_id generation
* retrieval_runs.request_id non-unique 前提の選択ロジック
* stale retrieval_run update SQL condition
* error_code mapping
* handler payload validation
* display-safe error message generation

### 23.2 integration test

対象:

* queued job acquisition
* running job lease renewal
* expired running job reclaim
* multiple worker acquisition race
* lease lost terminal update failure
* job succeeded transition
* job failed transition
* retry job creation
* active retry conflict
* evaluation active job conflict

### 23.3 handler test

#### document_ingest

* normal ingest success
* `document_versions.status = ready` の no-op success
* ready no-op success で Qdrant point check を行わない
* extraction failure
* embedding failure
* Qdrant upsert failure
* Qdrant cleanup が DB transaction 外で実行される
* cleanup 対象 id 取得後に transaction が閉じる
* chunk insert 後 failure cleanup
* reclaim 再実行時 existing artifacts cleanup
* failed retry success
* failed retry failure
* failed retry Qdrant cleanup failure で `qdrant_cleanup_failed`
* failed retry missing Qdrant point success
* existing artifacts cleanup
* lease lost result discard

#### qdrant_mirror_update

* archive mirror success
* Qdrant failure
* missing point failed
* missing point failed が retrieval correctness に影響しないこと

#### message_edit_regeneration

* success
* job_trace_id が `retrieval_runs.request_id` に保存される
* `retrieval_runs.request_id` が UNIQUE でない前提で複数 run を扱える
* same job_trace_id succeeded run + assistant exists の no-op success
* same job_trace_id running run を stale failed にして新規 run 作成
* stale update が `request_id = job_trace_id AND status = running` のみを対象にする
* same job_trace_id failed run を再利用せず新規 run 作成
* multiple failed run の debug 代表を `started_at DESC, retrieval_run_id DESC` で選ぶ
* archived session failure
* generation failure
* citation_build_failed
* no duplicate assistant on retry
* active edit job conflict
* lease lost result discard

#### evaluation_run

* success
* `evaluation_runs.status = succeeded` の no-op success
* one case failure
* retry clears previous items in initial transaction
* reclaim clears previous partial items in initial transaction
* 同一 evaluation_run_id の active job が 2 本作られない
* temporary chat origin retrieval_run is not evaluation target

#### temporary_chat_cleanup

* TTL expired deletion
* TTL not expired no-op
* session missing no-op
* deletion transaction 直前 ownership check
* linked_retrieval_run_id NULL 化
* child table deletion order
* audit_logs not deleted
* audit に本文や chunk text を保存しない
* audit に deleted count のみ保存する

### 23.4 concurrency test

* 2 Worker が同じ queued job を取得しない
* expired lease の job を reclaim できる
* lease を失った Worker が succeeded / failed に更新できない
* reclaim 後 document_ingest が existing artifacts を cleanup する
* active retry が同時に 2 本作られない
* stale retrieval_run が running のまま残らない
* evaluation active job が同時に 2 本作られない
* evaluation reclaim で partial items が重複しない
* temporary cleanup と message fetch が競合しても破綻しない

### 23.5 idempotency test

* same job re-run after reclaim
* domain success no-op
* Qdrant upsert repeated
* cleanup repeated
* failed retry repeated
* terminal update ownership lost

### 23.6 API integration test

* job response に `scheduled_at` が出ない
* job response に `created_at` が出る
* retry response の `source_job_id` が original source job を指す
* retry job が `job_type` / `target_type` / `target_id` / `payload_json` を引き継ぐ
* retry payload の `requested_by_user_id` が retry 実行者になる
* active retry exists で `409 job_active_retry_exists`
* same evaluation_run active job conflict が発生する

---

## 24. 実装順序

### 24.1 Phase1 実装順序

1. job model / repository 実装
2. job acquisition 実装
3. lease renewal 実装
4. lease ownership check 実装
5. terminal update safety 実装
6. dispatcher 実装
7. handler base 実装
8. startup check 実装
9. document_ingest handler 実装
10. retry API 接続
11. qdrant_mirror_update handler 実装
12. message_edit_regeneration handler 実装
13. evaluation_run handler 実装
14. temporary_chat_cleanup handler 実装
15. worker integration test
16. failure / reclaim / retry test

### 24.2 最初に通すべき最小シナリオ

```text
POST /documents
 -> document_ingest job queued
 -> worker acquire
 -> document ingest succeeded
 -> document_versions.status = ready
 -> approve API
 -> /rag/ask で retrieval 対象になる
```

### 24.3 次に通すべき failure scenario

```text
POST /documents
 -> document_ingest job queued
 -> worker acquire
 -> embedding failed
 -> collect cleanup ids in DB transaction
 -> Qdrant cleanup outside DB transaction
 -> RDB chunks cleanup in DB transaction
 -> jobs.status = failed
 -> document_versions.status = failed
 -> retry API
 -> retry job queued
 -> worker acquire
 -> Qdrant cleanup outside DB transaction
 -> document_versions.status = processing
 -> retry success
 -> document_versions.status = ready
```

### 24.4 lease lost scenario

```text
Worker A acquires job
 -> Worker A lease expires
 -> Worker B reclaims job
 -> Worker A finishes late
 -> Worker A terminal update WHERE locked_by = A returns 0
 -> Worker A discards result
 -> Worker B result becomes source of truth
```

### 24.5 document_ingest reclaim artifact scenario

```text
Worker A:
  document_chunks insert success
  Qdrant upsert success
  lease lost before success update

Worker B:
  reclaim same job
  lock document_version
  status = processing
  collect cleanup ids
  commit DB transaction
  cleanup existing Qdrant points outside DB transaction
  cleanup existing document_chunks in DB transaction
  re-run ingest
```

### 24.6 document_ingest ready no-op scenario

```text
Worker A:
  Qdrant upsert success
  document_versions.status = ready
  lease lost before job terminal update

Worker B:
  reclaim same job
  lock document_version
  status = ready
  no-op success
  Qdrant point existence is not checked
```

### 24.7 message_edit_regeneration stale retrieval_run scenario

```text
Worker A:
  create retrieval_run request_id = job:400
  lease lost while RAG pipeline running

Worker B:
  reclaim same job
  find no succeeded retrieval_run + assistant for job:400
  update retrieval_runs
    set status = failed
    where request_id = 'job:400'
      and status = 'running'
  create new retrieval_run
  regenerate answer
```

### 24.8 evaluation_run reclaim partial item scenario

```text
Worker A:
  save evaluation_run_items for case 1 and case 2
  lease lost

Worker B:
  reclaim same job
  lock evaluation_run
  delete existing evaluation_results / evaluation_run_items
  re-run all cases
```

---

## 25. 総括

本書では、RAGシステム Phase1 の Worker / Job 実装に必要な以下を確定した。

* jobs table を PostgreSQL queue として利用する
* Worker は at-least-once 実行を前提とする
* job handler は idempotent に設計する
* `queued` job の `started_at` は `NULL`
* Worker が `running` にした時点で `started_at` を設定する
* reclaim / lease renewal では `started_at` を上書きしない
* `created_at` は job 作成時刻である
* `finished_at` は terminal state 到達時刻である
* job acquisition は `SELECT ... FOR UPDATE SKIP LOCKED` を用いる
* expired running の取得条件は `lease_expires_at IS NOT NULL AND lease_expires_at < now()` とする
* running job は lease expired 時に reclaim 可能とする
* Phase1 では `reclaim_count` を持たず、`worker_max_reclaim_count` は Phase2 以降候補とする
* lease renewal は `locked_by = current_worker_instance_id` を条件にする
* success / failure terminal update は `locked_by = current_worker_instance_id` を条件にする
* terminal update 更新件数が 0 の場合、Worker は lease を失ったものとして handler result を破棄する
* lease を失った Worker は job state を更新しない
* `LeaseLostError` では domain state も failed にしない
* domain final update 後に job terminal update が失敗し得るため、handler 開始時の domain success no-op を必須方針にする
* 外部 I/O は DB transaction 内で実行しない
* Qdrant cleanup は DB transaction 外で実行する
* retry は failed job のみ可能とする
* retry job は original job の `job_type` / `target_type` / `target_id` / `payload_json` を引き継ぐ
* retry payload の `requested_by_user_id` は retry 実行者に更新する
* `original_requested_by_user_id` は任意で保持してよい
* `retry_of_job_id` は original source job を指す
* retry の retry でも original source job を指す
* active retry は original source job 単位で 1 本までとする
* `canceled` は Phase1 では公開 cancel API なしの予約 terminal state とする
* `document_ingest` は `document_versions.status = ready` の場合 no-op success とする
* ready no-op success では Qdrant point 存在確認を行わない
* ready 状態の Qdrant repair は reindex / mirror update / repair job に委ねる
* `document_ingest` は failed retry / reclaim / re-run 時に同一 `document_version_id` の existing chunks / Qdrant points を cleanup してから再実行する
* failed ingest retry 開始時の Qdrant cleanup failure は `qdrant_cleanup_failed` として job / document_version を failed にする
* cleanup 対象 point が存在しない場合は cleanup success とする
* chunk insert 後に失敗した場合、cleanup id 取得、Qdrant cleanup、RDB chunks cleanup、document_version failed update の順で処理する
* failed retry 成功後は `ready + is_active=false` とし、自動 active 化しない
* Qdrant payload は mirror であり、RDB final check を正とする
* `qdrant_mirror_update` の missing point は Phase1 では運用検知重視で failed とする
* `message_edit_regeneration` は target 必須とする
* `message_edit_regeneration` は `job_trace_id = job:{job_id}` を `retrieval_runs.request_id` に保存する
* `retrieval_runs.request_id` は Phase1 では UNIQUE ではない
* stale running retrieval_run は `request_id = job_trace_id AND status = running` の条件で failed にしてから新規 retrieval_run を作成する
* 同一 `job_trace_id` の succeeded retrieval_run + assistant_message が存在する場合は no-op success とする
* 同一 `job_trace_id` の failed retrieval_run は再利用しない
* 複数 failed retrieval_run がある場合、debug 代表は `started_at DESC, retrieval_run_id DESC` で選ぶ
* `message_edit_regeneration` の二重生成防止は active edit job 制限、job_trace_id、latest assistant existence check で行う
* `evaluation_run` は succeeded の場合 no-op success とする
* 同一 `evaluation_run_id` の active evaluation job は Phase1 で 1 本に制限する
* `evaluation_run` は retry / reclaim / re-run 時に既存 evaluation_results / evaluation_run_items を削除して再評価する
* `temporary_chat_cleanup` は session row lock 後、削除 transaction 直前に ownership check し、子テーブルを明示順序で削除する
* temporary cleanup audit には deleted count 等の最小 summary のみ保存し、本文や chunk text は保存しない
* temporary chat 起源 retrieval_run は evaluation から参照しない
* audit_logs は temporary cleanup で削除しない
* external API usage は application log / metrics を基本とし、必要なものだけ audit_logs に記録する
* Worker startup check は enabled job_type ごとに必要最小限を実行する
* startup check failure では未取得 job の state を変更しない
* Worker test では lease 競合、reclaim、lease lost terminal update、partial artifact cleanup、stale retrieval_run、evaluation partial item cleanup、active retry、active evaluation job、handler failure、idempotency を重点的に検証する

以上をもって、Worker / Job 詳細設計書 v1.3 とする。
