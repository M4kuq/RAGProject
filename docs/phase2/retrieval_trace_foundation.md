# PR-21 Retrieval Trace Foundation

## 目的

PR-21 は PR-20 の strategy/trace schema を前提に、既存の dense retrieval、`/rag/search`、`/rag/ask` へ safe trace recording を接続する。検索戦略を増やす PR ではなく、後続の Evaluation、Sparse/Hybrid Retrieval、Strategy Router、Debug UI が参照できる観測基盤を作る。

## 実装する範囲

- `query_plan_json` に default dense の query plan metadata を保存する。
- `strategy_decision_json` に router disabled / default dense の decision metadata を保存する。
- `latency_breakdown_json` に retrieval、qdrant、RDB final check、rerank、generation、citation、confidence の duration を保存する。
- `retrieval_settings_json` に run 時点の safe settings snapshot を保存する。
- `retrieval_run_items.retrieval_source` に `dense` を保存する。
- `retrieval_run_items.score_breakdown_json` に dense/rerank score と rank metadata を保存する。
- failed run でも、作成済み run に対して保存可能な safe trace を残す。

## 実装しない範囲

Sparse Retrieval、BM25、Hybrid Retrieval、fusion、QueryAnalyzer、QueryPlanner、StrategyRouter、Agentic Retrieval Loop、Debug UI、LangSmith、外部 trace export は実装しない。

## Trace schema v1

すべての PR-21 trace payload は `schema_version = "phase2.trace.v1"` を持つ。保存対象は safe metadata のみとし、raw query、raw prompt、raw chunk text、full context、PII、secret は保存しない。

### query_plan_json

保存する代表項目:

- `strategy_type = "dense"`
- `query_mode = "single_query"`
- `query_hash = sha256(raw query)`
- `rewrite_applied = false`
- `sub_query_count = 0`
- `metadata_filter_applied`
- `metadata_filter_count`
- `logical_document_filter_count`
- `reason_codes = ["phase1_compat_default_dense"]`

raw query は保存しない。必要な照合は `retrieval_runs.query_hash` と同じ hash を使う。

### strategy_decision_json

保存する代表項目:

- `selected_strategy = "dense"`
- `decision_source = "default"`
- `decision_policy = "static_dense"`
- `router_enabled = false`
- `fallback_used = false`
- `fallback_strategy = "dense"`
- `reason_codes = ["phase1_compat_default_dense"]`

router prompt、LLM decision prompt、raw query、full context は保存しない。

### latency_breakdown_json

保存する代表項目:

- `total_ms`
- `retrieval_ms`
- `query_embedding_ms`
- `qdrant_search_ms`
- `rdb_final_check_ms`
- `rerank_ms`
- `retrieval_items_persist_ms`
- `/rag/ask` のみ: `context_assembly_ms`, `generation_ms`, `citation_build_ms`, `confidence_ms`

timer は monotonic clock を使う。値は non-negative integer milliseconds とし、例外時も取得済み span を保存する。

### retrieval_settings_json

保存する代表項目:

- `strategy_type = "dense"`
- `top_k`
- `rerank_top_n`
- `embedding_provider`
- `rerank_provider`
- `generation_provider`
- `qdrant_collection`
- `rdb_final_check_enabled`
- `hybrid_enabled = false`
- `router_enabled = false`
- `trace_enabled = true`

provider は mode/name のみ保存し、URL credential、API key、token は保存しない。Qdrant URL は保存しない。

### score_breakdown_json

保存する代表項目:

- `retrieval_source = "dense"`
- `dense_score`
- `rerank_score`
- `rank_order`
- `rerank_order`
- `final_rank`
- `selected_flag`

chunk text、payload full dump、prompt、query は保存しない。

## `/rag/search` trace

`/rag/search` は standalone `retrieval_runs` を作成し、default dense の query plan、strategy decision、settings snapshot を保存する。成功時は retrieval/rerank/persist latency と item score breakdown を保存する。zero result の場合も run は `succeeded` とし、items は空、trace は保存する。

## `/rag/ask` trace

`/rag/ask` は chat linked `retrieval_runs` を作成し、default dense trace を保存する。成功時は retrieval、context assembly、generation、citation build、confidence の latency を保存する。generation/citation failure では assistant placeholder を作らず、可能な範囲で retrieval items と failed trace を残す。

## failed run trace

run 作成後の failure では `mark_failed` と同時に `latency_breakdown_json` を保存する。`answer_confidence`、`groundedness_score`、`confidence_label` は failed run では NULL のままにする。

## Redaction policy

trace builder は forbidden key を除外し、secret/token/credential 形式、URL userinfo、email-like value を redacted にする。通常 API response には internal trace JSON を返さない。logs に trace full dump を出さない。

## PR-22 以降への引き継ぎ

- PR-22 は trace と retrieval_run を evaluation dataset / metrics に結びつける。
- PR-23/24 は `retrieval_source` と `score_breakdown_json` に sparse/fusion score を追加する。
- PR-26 は Debug UI でこの safe trace を表示する。ただし raw prompt / raw chunk text / full context は表示しない。
- PR-28/29 は `strategy_decision_json` と `query_plan_json` に router / agentic loop metadata を追加する。
