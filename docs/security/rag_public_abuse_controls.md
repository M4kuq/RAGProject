# 認証済みRAG入口のresource/cost abuse controls

## 結論

`POST /api/v1/rag/ask` は、認証・CSRF・chat ownership・payload validationの後、retrieval/generationより前に共有admission gateを通る。PostgreSQL上でper-user/sharedのrate、concurrency、daily work unitsを短いtransactionとして原子的に判定する。

この変更は回答prompt、retrieval profile、chunk、Gold v2、通常精度設定を変更しない。RAG-31/62のprompt-injection worktree/PRとPII worktree/PRへstackせず、GitHub main `96e5fc82ad2c253d000633ff9e9431e88998f74a` から独立した `feature/rag-public-abuse-controls` で実装した。

## 実装した制御

| Control | Per user | Shared | Default |
|---|---:|---:|---:|
| Rolling accepted requests / 60s | 20 | 200 | server setting |
| Active concurrency lease | 2 | 20 | 900秒でcrash recovery |
| Accepted daily work units / UTC day | 500 | 10,000 | approximate admission budget |
| Denial audit retention | - | - | 8日 |
| Admission DB lock wait | - | - | 最大1秒 |

設定:

- `RAG_ABUSE_CONTROL_ENABLED`
- `RAG_ABUSE_USER_REQUESTS_PER_MINUTE`
- `RAG_ABUSE_GLOBAL_REQUESTS_PER_MINUTE`
- `RAG_ABUSE_USER_CONCURRENT_REQUESTS`
- `RAG_ABUSE_GLOBAL_CONCURRENT_REQUESTS`
- `RAG_ABUSE_USER_DAILY_WORK_UNITS`
- `RAG_ABUSE_GLOBAL_DAILY_WORK_UNITS`
- `RAG_ABUSE_LEASE_SECONDS`
- `RAG_ABUSE_AUDIT_RETENTION_DAYS`

本番相当環境で有効な場合はPostgreSQLを必須とする。global値はper-user値以上、leaseはすべてのAgentic timeout以上でなければ起動時validationに失敗する。

## Work-unit mapping

| Strategy | Unit |
|---|---:|
| dense | 1 |
| hybrid | 2 |
| graph / graph_postgres / graph_neo4j | 4 |
| agentic_router | 6 |
| llm_tool_orchestrator / langchain_agentic / langgraph_agentic | 8 |
| unknown future value | 8 |

このunitは実費ではない。受理前に使う保守的な相対コストである。mappingはサーバーコードに固定され、message、retrieved chunk、tool result、planner outputから変更できない。失敗とidempotent replayも入口・DB処理を消費するためunitに含める。

## 判定順とAPI契約

1. per-user rolling rate
2. shared rolling rate
3. per-user daily work units
4. shared daily work units
5. per-user active concurrency
6. shared active concurrency

| Reason code | HTTP | Retry-After |
|---|---:|---|
| `rag_user_rate_limited` | 429 | 最古の受理から60秒まで |
| `rag_capacity_rate_limited` | 503 | shared最古受理から60秒まで |
| `rag_user_daily_budget_exhausted` | 429 | 次のUTC日まで |
| `rag_capacity_daily_budget_exhausted` | 503 | 次のUTC日まで |
| `rag_user_concurrency_limited` | 429 | 最も早いlease expiryまで |
| `rag_capacity_concurrency_limited` | 503 | shared最早lease expiryまで |
| `rag_admission_unavailable` | 503 | 5秒 |

ユーザー固有gateを先に評価し、不要なshared capacity情報を返さない。DB障害または1秒を超えるadvisory lock競合はfail-closedで、retrieval/generationへ進まない。

## Data minimization and audit

`rag_request_admissions`:

- `user_id`やemailを保存しない。`SESSION_SECRET`をkeyにしたHMAC `subject_hash`だけを保存する。
- raw request IDを保存しない。HMAC `request_hash`だけを保存する。
- raw question、prompt、retrieved content、answer、token、secretを保存しない。
- strategy、work units、受理時刻、lease、terminal outcomeだけを保存する。

`rag_abuse_denial_buckets`:

- scope、HMAC subject、1分window、typed reason、件数、last seenだけを保存する。
- 同じscope/subject/window/reasonは1行へ集約し、拒否ログ自体のstorage amplificationを避ける。

retention削除は次のadmission transactionで進む。低traffic環境で厳密な期限削除が必要なら、同じ条件を使うscheduled maintenanceを追加する。

## Migration and rollback

Migration `0023_rag_abuse_controls` は新規2表と4 indexだけを作る。既存表、Qdrant、Docker volumeをresetしない。

通常の復帰:

1. `RAG_ABUSE_CONTROL_ENABLED=false` を設定してAPIを再起動する。
2. 新規表は残し、過去の安全な集約記録を保持する。
3. 原因を調査し、設定を修正後に再度有効化する。

Schema rollbackが必要な場合:

1. 先に旧コードへ戻すかcontrolを無効化する。
2. 必要な監査集約をrepository外の承認済み保管先へ退避する。
3. 明示的な変更承認後だけ `alembic downgrade 0022_eval_reliability` を行う。

downgradeは新規2表を削除するため、日常的なrollbackには使わない。

## Verification log

実装開始時:

- GitHub main: `96e5fc82ad2c253d000633ff9e9431e88998f74a`
- 独立worktree: `.worktrees/rag-public-abuse-controls`
- Jira: `RAG-63`
- baseline focused: `74 passed, 1 skipped, 3 warnings`

実装中:

- focused admission/API: `10 passed, 3 warnings`
- RAG/auth regression初回: `82 passed, 1 skipped`。既存productionテストが新しいPostgreSQL必須validationへ先に停止。
- テスト用production settingをcredentialなしPostgreSQL形式へ直し、対象テスト単体 `1 passed`。
- security reviewでshared daily budget不足とunbounded advisory-lock waitを発見し、追加。
- SQLite admission: `9 passed, 2 PostgreSQL-only skipped`
- mypy（CI同等、tests込み）: `Success: no issues found in 276 source files`
- GitHub Backend CI初回はtest helperの`dict[str, object]`展開を型エラーとして検出。`cast(Any, values)`へ限定修正し、上記full mypyと対象pytestで再検証した。
- backend full retry: `982 passed, 21 skipped, 3 known warnings`
- Ruff: `All checks passed`
- frontend Vitest: `16 files, 103 tests passed`
- frontend production build: TypeScript + Vite build成功

一時PostgreSQL 16、既存volumeなし:

1. 新規Docker network作成は `all predefined address pools have been fully subnetted` で失敗。既存networkを削除しなかった。
2. 既定bridgeと一時container限定linkへ切り替え。
3. `alembic upgrade head`: `0023_rag_abuse_controls` まで成功。
4. schema + two-session atomicity + bounded lock: 初回はlock計測にDB初回接続を含み7.34秒となり失敗。
5. contender connectionを計測前にwarm-upして、lock待ちだけを測るようテストを修正。
6. 再実行: schema + atomicity + bounded lock `3 passed`。
7. `alembic downgrade 0022_eval_reliability`: 成功。
8. `to_regclass`で新規2表が両方存在しないことを確認。
9. 再度`upgrade head`: 成功。
10. schema再確認: `1 passed`。
11. 一時PostgreSQL containerを削除。既存DB/Qdrant/volumeは未変更。

## Residual risks and next work

- `SESSION_SECRET` rotationでsubject hashが変わり、その時点のper-user履歴が継続しない。controlled rotation runbookが必要。
- work unitは固定近似で、実token/tool/provider費用ではない。provider別実測から定期校正する。
- replayもunitを使う。安全なidempotency判定をadmission前へ移す場合、cheap DB replay floodの別制限が必要。
- admission DB障害時は全RAGが停止する。PostgreSQL HA、alert、運用SLOが必要。
- auth/session検証のDB処理はadmissionより前にある。internet DDoSはedge/WAF/ingress rate limitで別途守る。
- 外部LLM送信時のPII masking、provider allowlist、Plus強制コスト攻撃は次のsecurity goalで扱う。
- Prompt injection、retrieved chunk poisoning、tool権限は [RAGProject threat model](./RAGProject-threat-model.md) のfocus pathとして別security gateを維持する。
