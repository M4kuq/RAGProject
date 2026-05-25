# Phase2 Design Baseline

## 目的

Phase2 は Phase1 の Core RAG を、検索戦略を比較・制御・観測できる RAG に拡張するフェーズである。中心方針は次の4点とする。

- Advanced Retrieval
- Agentic Control
- Evaluation
- Observability

PR-20 は Phase2 の最初のPRとして、後続PRが依存する strategy enum、trace schema、system_settings、evaluation/observability 方針を固定する。検索方式や UI の本体実装は行わない。

## PR計画

| PR | 目的 |
|---:|---|
| PR-20 | Phase2 Design Baseline / Strategy & Evaluation Schema |
| PR-21 | Retrieval Trace Foundation / Observability Schema |
| PR-22 | Evaluation Dataset Management / Strategy Metrics Schema |
| PR-23 | Sparse Retrieval / BM25 Index |
| PR-24 | Hybrid Retrieval / Score Fusion |
| PR-25 | Strategy Evaluation Runner |
| PR-26 | Retrieval Debug UI v2 |
| PR-27 | Query Analyzer / Query Planner |
| PR-28 | Strategy Router / Agentic Retrieval Control |
| PR-29 | Agentic Retrieval Loop / Context Sufficiency Check |
| PR-30 | Agentic Strategy Evaluation / Failure Dataset Promotion |
| PR-31 | CI Retrieval Evaluation / Scheduled Smoke |
| PR-32 | LangSmith Optional Adapter / Trace Export |
| PR-33 | SentenceTransformers Experiment Harness |
| PR-34 | Advanced Import: Excel / PowerPoint / Parent-child Chunk |
| PR-35 | Advanced Import: HTML / XML / URL + SSRF Guard |
| PR-36 | Document Diff / Citation Navigation / Version Compare |
| PR-37 | Phase2 Final Hardening / Demo / Docs |

## PR-21 Retrieval Trace Foundation

PR-21 では、PR-20 で追加した `retrieval_runs` / `retrieval_run_items` の trace columns に、既存 dense retrieval の safe trace を保存する。詳細は [retrieval_trace_foundation.md](./retrieval_trace_foundation.md) を参照する。

- `/rag/search` と `/rag/ask` は default `dense` の query plan / strategy decision / settings / latency を保存する。
- item ごとに `retrieval_source = dense` と score breakdown を保存する。
- failed run でも取得済み latency と safe metadata を保存する。
- Sparse / Hybrid / Router / Debug UI / LangSmith / external trace export は PR-21 では実装しない。

## PR-20で実装すること

- `RetrievalStrategy` / `RetrievalSource` / `FusionMethod` / `RouterFallbackStrategy`
- `retrieval_runs` の strategy/trace 用 column
- `retrieval_run_items` の source/score breakdown 用 column
- redacted trace DTO
- retrieval settings snapshot DTO
- strategy evaluation metric DTO
- Phase2 retrieval strategy system settings seed
- Phase2 docs と受け入れ基準

## PR-20で実装しないこと

Sparse Retrieval、Hybrid fusion、QueryAnalyzer、QueryPlanner、StrategyRouter、Agentic Retrieval Loop、Retrieval Debug UI、LangSmith adapter、Graph-RAG、OCR、AWS/OIDC は実装しない。

## 安全方針

trace、DTO、DB、docs のいずれでも raw prompt、raw chunk text、full context、PII、secret、token、credential を保存・表示しない。Phase1 の `/rag/search` と `/rag/ask` は default strategy `dense` として維持する。
