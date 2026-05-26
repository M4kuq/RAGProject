# Evaluation and Observability Strategy

## Direction

Phase2 treats evaluation and observability as central architecture, not support tooling. Retrieval strategies must be observable and comparable before new retrieval algorithms are added.

## Trace Foundation

PR-21 stores safe retrieval trace for the existing dense flow:

- query plan hash and counts
- strategy decision metadata
- retrieval settings snapshot
- latency breakdown
- item source and score breakdown

Trace payloads never store raw query, raw prompt, full context, raw chunk text, PII, secrets, credentials, tokens, or external raw request/response bodies.

PR-23 extends this foundation to standalone sparse retrieval:

- `retrieval_runs.strategy_type = "sparse"`
- `query_plan_json` stores query hash, safe filter counts, and normalized term count only
- `strategy_decision_json` records explicit sparse selection
- `latency_breakdown_json` includes `sparse_search_ms`
- `retrieval_settings_json` includes sparse provider/language/normalization
- `retrieval_run_items.retrieval_source = "sparse"`
- `score_breakdown_json` stores `sparse_score` and rank metadata only

Sparse trace does not expose the raw query, `content_text`, full context, or full PostgreSQL result payload.

## Dataset Foundation

PR-22 stores evaluation datasets and cases in DB so the same dataset can be reused across strategies:

- manual datasets
- imported fixture datasets
- future feedback-promoted datasets
- archived datasets retained for auditability

Dataset/case APIs are admin-only. Writes require CSRF. Delete is intentionally not exposed; archive is used instead.

## Strategy Metrics

The Phase2 strategy metric schema includes:

- `recall_at_k`
- `mrr`
- `citation_coverage`
- `groundedness`
- `faithfulness`
- `no_context_rate`
- `p95_latency`
- `strategy_selection_accuracy`

`evaluation_results.metric_value` stores the canonical metric value. `metric_score` remains for Phase1 ratio metrics. `metric_detail_json` stores safe details such as counts, units, labels, case key, and reason codes.

## Comparison Model

The comparison key is:

```text
evaluation_dataset_id + strategy_type + metric_name
```

PR-22 stores this shape. PR-23 makes sparse search executable for `/rag/search`, but PR-25 still owns dataset-wide Strategy Evaluation Runner execution across dense/sparse/hybrid/agentic_router.

## CI Direction

CI should use deterministic fixtures and fake adapters. Heavy model downloads, external API keys, LangSmith credentials, and external trace export must not be required for PR-22 validation.

## Redaction Rules

Do not store or display:

- raw prompt
- full context
- raw chunk text
- PII
- secret, token, credential, API key, password
- raw external API request/response
- full trace JSON dumps in logs

Evaluation case `question` is saved only after validation rejects PII-like and secret-like values.
