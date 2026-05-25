# Phase2 DB Migration Plan

## Migration

PR-20 は `0003_phase2_strategy_trace` を追加する。既存列の削除や意味変更は行わず、追加 column と CHECK constraint のみで構成する。

## retrieval_runs

追加 column:

| column | type | null | default | purpose |
|---|---|---:|---|---|
| `strategy_type` | `VARCHAR(50)` | no | `dense` | runの検索戦略 |
| `query_plan_json` | `JSONB` | yes | none | redacted query plan |
| `strategy_decision_json` | `JSONB` | yes | none | redacted strategy decision |
| `latency_breakdown_json` | `JSONB` | yes | none | retrieval/rerank/generation latency |
| `retrieval_settings_json` | `JSONB` | yes | none | run時点の安全な設定snapshot |

`strategy_type` は `dense`, `sparse`, `hybrid`, `multi_query_dense`, `multi_query_hybrid`, `metadata_filtered`, `version_aware`, `agentic_router`, `fallback_dense` のみ許可する。既存 run は DB default により `dense` として扱う。

## retrieval_run_items

追加 column:

| column | type | null | default | purpose |
|---|---|---:|---|---|
| `retrieval_source` | `VARCHAR(50)` | yes | none | itemの取得元 |
| `score_breakdown_json` | `JSONB` | yes | none | dense/sparse/fused/rerank scoreの内訳 |

`retrieval_source` は `dense`, `sparse`, `hybrid`, `rerank`, `fallback_dense`, `metadata_filter` のみ許可する。PR-20 では dense の保存土台だけを使う。

## downgrade

downgrade は追加 CHECK constraint を削除してから追加 column を削除する。既存 Phase1 schema へ戻せるが、downgrade により Phase2 trace metadata は失われる。

## 保存禁止

JSONB column には raw prompt、raw chunk text、full context、PII、secret、token、credential を保存しない。
