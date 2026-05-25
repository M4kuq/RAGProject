# Phase2 PR-20 Acceptance Criteria

## 必須条件

- GitHub main の `docs/Phase2_Phase3_RAG拡張実装計画書.md` を設計ソースとして確認している。
- Phase2 の中心方針を Advanced Retrieval / Agentic Control / Evaluation / Observability として扱う。
- `RetrievalStrategy` enum が定義され、DB CHECK と一致している。
- default strategy は `dense` である。
- 既存 `/rag/search` と `/rag/ask` は default dense として動作する。
- `retrieval_runs` に `strategy_type`, `query_plan_json`, `strategy_decision_json`, `latency_breakdown_json`, `retrieval_settings_json` がある。
- `retrieval_run_items` に `retrieval_source`, `score_breakdown_json` がある。
- ORM と Alembic migration が整合している。
- trace / score / settings / evaluation metric DTO が JSON serializable である。
- system_settings seed は idempotent で、既存値を上書きしない。
- raw prompt、raw chunk text、full context、PII、secret を保存しない方針が schema、docs、tests に現れている。

## セキュリティ条件

- `query_plan_json` に prompt全文を保存しない。
- `strategy_decision_json` に full context を保存しない。
- `score_breakdown_json` に chunk本文を保存しない。
- `retrieval_settings_json` に secret、token、credential を保存しない。
- logs に trace JSON の full dump を出さない。
- RAG context 内の命令を system instruction として扱わない。
- RAG応答から管理操作を直接実行しない。

## 対象外確認

PR-20では sparse retrieval、hybrid fusion、router、agentic loop、Debug UI、LangSmith、Graph-RAG、OCR、AWS/OIDC を実装しない。これらが未実装であることは PR-20 の失敗条件ではない。
