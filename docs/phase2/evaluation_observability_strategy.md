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
- `fallback_rate`
- `budget_exhausted_rate`
- `sufficiency_score_avg`
- `retrieval_call_count_avg`

`evaluation_results.metric_value` stores the canonical metric value. `metric_score` remains for Phase1 ratio metrics. `metric_detail_json` stores safe details such as counts, units, labels, case key, and reason codes.

## Comparison Model

The comparison key is:

```text
evaluation_dataset_id + strategy_type + metric_name
```

PR-22 stores this shape. PR-23 makes sparse search executable for `/rag/search`, and PR-24 makes hybrid search executable for `/rag/search`.

PR-25 executes dataset-wide comparisons for dense / sparse / hybrid. Each case/strategy pair creates one `evaluation_run_items` row and one linked retrieval run. The aggregate comparison is stored in `evaluation_runs.strategy_metrics_summary_json` and exposed by the strategy comparison API.

PR-30 executes `agentic_router` in the same comparison shape. Agentic summaries are derived from safe router/loop trace fields such as `fallback_used`, `budget_exhausted`, `sufficiency_score`, and `retrieval_call_count`.

## Failure Feedback Loop

PR-30 extracts failure candidates from item status and metric rows, then allows admins to promote selected failures into an active evaluation dataset. Candidate payloads store hashes, failure types, strategy names, numeric metric snapshots, and reason codes only. Promotion metadata links back to the source evaluation run item and remains idempotent by promotion key.

## CI Direction

CI should use deterministic fixtures and fake adapters for normal PR validation. Heavy model downloads, external API keys, LangSmith credentials, and external trace export must not be required for PR-30 validation.

PR-31 adds `retrieval-eval-smoke.yml` as a lightweight manual/scheduled smoke layer. It runs the existing strategy evaluation runner with real local retrieval settings, cached small local embeddings, and no answer-generation dependency; writes only safe aggregate JSON/Markdown artifacts; supports manual strategy and threshold inputs; and keeps threshold failures configurable as warn-only or hard-fail. If local retrieval prerequisites are unavailable, the smoke reports a safe `blocked` artifact instead of falling back to fake adapters.

## External Trace Export

PR-32 adds an optional external trace export layer on top of the safe Phase2
trace and evaluation summaries.

- The default exporter is no-op.
- `TRACE_EXPORT_PROVIDER=langsmith` is optional and requires explicit
  `TRACE_EXPORT_ENABLED=true`, `LANGSMITH_TRACING_ENABLED=true`, and an
  out-of-repository `LANGSMITH_API_KEY`.
- Search, ask, evaluation, and CI smoke flows keep working when the exporter is
  disabled, missing dependencies, missing secrets, or the external provider
  fails.
- Export payloads are minimized summaries: run ids, strategy names, hashes,
  counts, numeric scores, safe reason codes, latency, status, and safe error
  codes.
- Export payloads never include raw query, raw prompt, full context, raw chunk
  text, full answer text, raw external request/response bodies, paths, PII,
  tokens, cookies, sessions, credentials, API keys, or secrets.

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
