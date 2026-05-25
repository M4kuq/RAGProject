# Phase2 Acceptance Criteria

## PR-20

- `RetrievalStrategy` enum exists and matches DB CHECK constraints.
- Default strategy is `dense`.
- Retrieval trace columns exist on `retrieval_runs`.
- Source and score columns exist on `retrieval_run_items`.
- Phase1 `/rag/search` and `/rag/ask` remain default dense.
- Trace DTOs are JSON serializable and reject sensitive keys.

## PR-21

- Existing dense `/rag/search` writes safe query plan, strategy decision, settings, latency, item source, and score breakdown.
- Existing dense `/rag/ask` writes safe trace including generation/citation/confidence latency where available.
- Failed retrieval runs preserve safe trace where possible.
- Responses do not expose internal trace JSON by default.

## PR-22

- `evaluation_datasets` and `evaluation_cases` exist.
- `evaluation_runs` can reference dataset, strategy, trigger type, retrieval settings, and strategy metric summary.
- `evaluation_run_items` can reference case, strategy, case key, latency breakdown, and metric summary.
- `evaluation_results` can store `metric_name`, `metric_value`, `metric_detail_json`, and `strategy_type`.
- Metric specs include `recall_at_k`, `mrr`, `citation_coverage`, `groundedness`, `faithfulness`, `no_context_rate`, `p95_latency`, and `strategy_selection_accuracy`.
- JSON fixture import/export is idempotent and safe.
- Admin dataset/case APIs are protected by admin auth and CSRF on writes.
- Viewer users receive `403`; unauthenticated users receive `401`.
- Existing minimal evaluation run still works with default `dense`.
- Minimal Evaluation UI can select datasets, show strategy, list cases, and export a manifest.

## Security

The following must not be stored in dataset, case, metric detail, logs, or normal responses:

- raw prompt
- full context
- raw chunk text
- PII
- secret, token, credential, API key, password

Evaluation case `question` is allowed only as a safe evaluation input. It must not be a full prompt or contain PII/secrets.

## Out of Scope for PR-22

- Strategy Evaluation Runner
- Sparse Retrieval / BM25
- Hybrid Retrieval / score fusion
- Agentic Router
- CI evaluation workflow
- LangSmith adapter
- SentenceTransformers experiment harness
- Graph-RAG / OCR / multimodal
- AWS / S3 / OIDC
