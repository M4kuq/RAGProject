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

PR-24 extends this foundation to standalone hybrid retrieval:

- `retrieval_runs.strategy_type = "hybrid"`
- `query_plan_json` stores query hash, safe filter counts, normalized term count, and fusion method only
- `strategy_decision_json` records explicit hybrid selection
- `latency_breakdown_json` includes dense, sparse, and `fusion_ms` spans
- `retrieval_settings_json` includes fusion method, weights, RRF constant, and sparse provider metadata
- `retrieval_run_items.retrieval_source = "hybrid"`
- `score_breakdown_json` stores dense score, sparse score, fused score, rank metadata, and fusion method only

Hybrid trace does not expose the raw query, raw chunk text, full context, Qdrant raw payload, or full PostgreSQL result payload.

PR-27 extends `query_plan_json` with deterministic analyzer/planner metadata:

- intent
- ambiguity score and flags
- keyword-heavy score and signals
- version-specific and temporal signals
- safe rewrite hash/preview
- planned sub-query previews
- metadata filter candidates
- candidate strategies and recommended strategy

These fields are observability and future-router inputs only. They do not change the executed strategy in PR-27.

PR-28 records rule-based router decisions in `retrieval_runs.strategy_decision_json` using `phase2.router.v1`. Evaluation and debug tooling should treat `retrieval_runs.strategy_type = agentic_router` as the requested strategy and `strategy_decision_json.execution_strategy` as the executed strategy. Router decision details remain safe metadata only: reason codes, confidence, fallback flags, disabled candidates, and safety flags. Raw query, prompt, context, chunk text, PII, secrets, and raw exception messages are not stored.

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

PR-22 stores this shape. PR-23 makes sparse search executable for `/rag/search`, and PR-24 makes hybrid search executable for `/rag/search`.

PR-25 executes dataset-wide comparisons for dense / sparse / hybrid. Each case/strategy pair creates one `evaluation_run_items` row and one linked retrieval run. The aggregate comparison is stored in `evaluation_runs.strategy_metrics_summary_json` and exposed by the strategy comparison API.

`agentic_router` is not executed in PR-25. `strategy_selection_accuracy` remains not-applicable until PR-30.

## CI Direction

CI should use deterministic fixtures and fake adapters. Heavy model downloads, external API keys, LangSmith credentials, and external trace export must not be required for PR-25 validation.

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
