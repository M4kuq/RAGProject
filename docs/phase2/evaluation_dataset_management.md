# PR-22 Evaluation Dataset Management

## Purpose

PR-22 adds the dataset and metric schema foundation needed to compare retrieval strategies on the same evaluation set. It depends on PR-20 strategy enum / DB baseline and PR-21 safe retrieval trace recording.

This PR does not implement Sparse Retrieval, Hybrid Retrieval, Agentic Router, Strategy Evaluation Runner, CI evaluation workflow, LangSmith export, or SentenceTransformers experiments.

## Database Schema

PR-22 adds:

- `evaluation_datasets`
- `evaluation_cases`

PR-22 extends:

- `evaluation_runs.evaluation_dataset_id`
- `evaluation_runs.strategy_type`
- `evaluation_runs.trigger_type`
- `evaluation_runs.retrieval_settings_json`
- `evaluation_runs.strategy_metrics_summary_json`
- `evaluation_run_items.evaluation_case_id`
- `evaluation_run_items.strategy_type`
- `evaluation_run_items.case_key`
- `evaluation_run_items.latency_breakdown_json`
- `evaluation_run_items.metric_summary_json`
- `evaluation_results.metric_value`
- `evaluation_results.metric_detail_json`
- `evaluation_results.strategy_type`

Existing `evaluation_runs.metrics_config`, item score columns, and `evaluation_results.metric_score/details_json` remain for Phase1 compatibility.

## Dataset Schema

`evaluation_datasets` stores dataset identity and lifecycle metadata:

- `dataset_name`
- `description`
- `version`
- `source_type`: `manual`, `fixture`, `feedback_promoted`, `imported`
- `status`: `active`, `archived`
- `metadata_json`

`evaluation_cases` stores safe evaluation inputs:

- `case_key`
- `question`
- `expected_answer`
- `expected_keywords`
- `expected_document_ids`
- `expected_chunk_ids`
- `required_citation`
- `tags`
- `metadata_json`
- `status`

`question` is an evaluation input, not a raw prompt. Full prompts, full context, raw chunk text, PII, and secrets are not allowed in cases or metadata.

## Metric Schema

The minimum Phase2 metric names are:

- `recall_at_k`
- `mrr`
- `citation_coverage`
- `groundedness`
- `faithfulness`
- `no_context_rate`
- `p95_latency`
- `strategy_selection_accuracy`

`p95_latency` uses milliseconds. Most other metrics are ratios. `metric_value` is intentionally not constrained to `0..1` so latency metrics can be stored without abusing `metric_score`.

## Import / Export Manifest

PR-22 supports JSON manifests with:

```json
{
  "schema_version": "phase2.evaluation_dataset.v1",
  "dataset": {
    "dataset_name": "phase2_strategy_smoke",
    "version": "v1",
    "source_type": "fixture",
    "status": "active"
  },
  "cases": []
}
```

Import is idempotent by `dataset_name` and `(evaluation_dataset_id, case_key)`. Re-import updates safe metadata and cases without creating duplicates. Export returns the safe manifest only; it does not include raw context, chunk text, prompts, secrets, credentials, or retrieval trace internals.

## API

Admin-only APIs:

- `GET /api/v1/evaluations/datasets`
- `POST /api/v1/evaluations/datasets`
- `GET /api/v1/evaluations/datasets/{dataset_id}`
- `PATCH /api/v1/evaluations/datasets/{dataset_id}`
- `POST /api/v1/evaluations/datasets/{dataset_id}/archive`
- `GET /api/v1/evaluations/datasets/{dataset_id}/cases`
- `POST /api/v1/evaluations/datasets/{dataset_id}/cases`
- `GET /api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}`
- `PATCH /api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}`
- `POST /api/v1/evaluations/datasets/{dataset_id}/cases/{case_id}/archive`
- `POST /api/v1/evaluations/datasets/import`
- `GET /api/v1/evaluations/datasets/{dataset_id}/export`

Write APIs require CSRF. Viewer users receive `403`. Destructive delete is intentionally not added; archive is the lifecycle operation.

## Runner Boundary

The existing minimal evaluation runner remains default dense and Phase1-compatible. PR-22 stores requested `strategy_type` and safe retrieval settings, but non-dense strategy execution is left for PR-25 Strategy Evaluation Runner.

## Security Rules

Evaluation datasets, cases, metrics, logs, and responses must not store:

- raw prompt
- full context
- raw chunk text
- PII
- secret, token, credential, API key, password

`metric_detail_json` may store counts, labels, units, case keys, and safe reason codes. It must not store answer full text, retrieved chunk text, or trace payload dumps.

## Handoff

PR-25 will use these datasets and strategy fields to run dense / sparse / hybrid comparisons. PR-30 will use the same schema for agentic router evaluation and failure dataset promotion. PR-31 will add CI/scheduled evaluation workflows.
