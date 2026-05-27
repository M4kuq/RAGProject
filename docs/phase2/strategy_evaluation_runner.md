# PR-25 Strategy Evaluation Runner

PR-25 runs the same evaluation dataset across the standalone retrieval strategies that exist after PR-24. PR-30 extends the same runner to include `agentic_router`:

- `dense`
- `sparse`
- `hybrid`
- `agentic_router` (PR-30)

It depends on the PR-20 strategy schema, PR-21 safe retrieval trace fields, PR-22 dataset and metric schema, PR-23 sparse retrieval, and PR-24 hybrid score fusion.

## Request Shape

Admin users can create a run with a strategy list:

```json
{
  "evaluation_dataset_id": 1,
  "strategies": ["dense", "sparse", "hybrid"],
  "metrics": [
    "recall_at_k",
    "mrr",
    "citation_coverage",
    "groundedness",
    "faithfulness",
    "no_context_rate",
    "p95_latency"
  ],
  "top_k": 20,
  "rerank_top_n": 5,
  "case_limit": 20,
  "trigger_type": "manual"
}
```

If `strategies` is omitted, the runner uses `["dense"]` to preserve the Phase1-compatible default. After PR-30, admin-created runs may include `dense`, `sparse`, `hybrid`, and `agentic_router`.

## Execution Flow

The worker `evaluation_run` handler delegates to `EvaluationService.run_job()`:

1. Load the queued `evaluation_runs` row.
2. Load active cases from the persistent dataset, or fixture cases for legacy runs.
3. Create one `evaluation_run_items` row per case per strategy.
4. Execute the existing `/rag/search` retrieval path with the selected strategy.
5. Link the resulting `retrieval_runs` row to the item.
6. Save deterministic per-case metric rows in `evaluation_results`.
7. Aggregate strategy summaries into `evaluation_runs.strategy_metrics_summary_json`.

Case-level failures are stored on the item with a safe `error_code`. If every item fails, the run is marked `failed`. Partial failures keep the run `succeeded` and are reflected in the summary `failed_count`.

## Metric Rules

PR-25 avoids LLM-as-a-Judge and external model dependencies. Metrics are deterministic:

- `recall_at_k`: expected chunk IDs, expected document IDs, or expected keywords matched by retrieved snippets.
- `mrr`: reciprocal rank of the first relevant retrieved item.
- `citation_coverage`: retrieval-only proxy based on selected safe citation snippets.
- `groundedness`: existing confidence score when available, otherwise retrieval presence.
- `faithfulness`: expected keyword or expected answer signal found in safe snippets or answer text.
- `no_context_rate`: `1.0` for no selected context, otherwise `0.0`; averaged by strategy.
- `p95_latency`: percentile of item-level evaluation latency in milliseconds.
- `strategy_selection_accuracy`: calculated for `agentic_router` when a case defines `metadata_json.expected_strategy` or `metadata_json.acceptable_strategies`; otherwise `not_applicable`.
- `fallback_rate`: averaged from safe router decision trace.
- `budget_exhausted_rate`: averaged from bounded retrieval budget trace.
- `sufficiency_score_avg`: average safe sufficiency score from PR-29.
- `retrieval_call_count_avg`: average bounded retrieval calls from PR-29.

Metric details store only counts, ranks, labels, units, and reason codes. They do not store retrieved text, full answers, prompts, or trace payload dumps.

## API

PR-25 extends:

- `POST /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs`
- `GET /api/v1/evaluations/runs/{evaluation_run_id}`

PR-25 adds:

- `GET /api/v1/evaluations/runs/{evaluation_run_id}/strategy-comparison`

PR-30 adds:

- `GET /api/v1/evaluations/runs/{evaluation_run_id}/failure-candidates`
- `POST /api/v1/evaluations/runs/{evaluation_run_id}/promote-failures`

The comparison response returns strategy, metric name, average, p50, p95, count, failed count, and not-applicable count.

## Security

The runner must not store or return:

- raw prompt
- full context
- raw chunk text
- PII
- secret, token, credential, API key, or password

Evaluation cases keep safe questions and expectations only. Retrieval snippets may be used in-memory for deterministic metric calculation, but metric detail JSON and logs store only safe metadata.

## Handoff

PR-30 adds `agentic_router` execution, agentic metrics, failure candidate extraction, and idempotent failure promotion. PR-31 can schedule CI evaluation runs without changing the metric persistence contract.
