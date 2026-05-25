# Phase2 Test Strategy

## PR-20

- Python retrieval enum values match Alembic CHECK values.
- Retrieval trace DTOs serialize to JSON.
- Trace DTOs reject sensitive keys.
- `/rag/search` and `/rag/ask` remain default dense.

## PR-21

- Latency spans are non-negative.
- Trace redaction removes forbidden keys, credential-like strings, URLs, and email-like values.
- `/rag/search` success / zero result / failure writes safe trace.
- `/rag/ask` success / no_context / generation failure / citation failure writes safe trace.
- Normal responses do not expose internal trace JSON.

## PR-22 Unit Tests

- `phase2_strategy_smoke.json` loads with the fixture loader.
- `EvaluationDatasetManifest` serializes and rejects secret-like or PII-like values.
- Metric specs include the required strategy comparison metrics.
- Metric detail DTOs do not include raw prompt, full context, raw chunk text, PII, or secrets.

## PR-22 DB / Migration Tests

- Alembic head is `0004_evaluation_dataset_strategy_metrics`.
- `evaluation_datasets` and `evaluation_cases` exist.
- `evaluation_runs` has dataset, strategy, trigger, retrieval settings, and strategy summary fields.
- `evaluation_run_items` has case, strategy, case key, latency, and metric summary fields.
- `evaluation_results` has metric value, metric detail, and strategy fields.
- Invalid strategy values are rejected by DB CHECK constraints.
- Upgrade and downgrade remain additive/reversible.

## PR-22 API Tests

- Unauthenticated dataset API access returns `401`.
- Viewer write access returns `403`.
- Admin can create/list/detail/archive datasets.
- Admin can create/list/detail/archive cases.
- Nested dataset/case mismatch returns `404`.
- JSON manifest import succeeds and is idempotent.
- Invalid manifest with secret-like values returns `422`.
- Export returns only a safe manifest.

## Regression Tests

- Existing fixture-based evaluation run still queues and runs with default `dense`.
- Persistent dataset cases can be evaluated by the existing dense runner.
- PR-21 retrieval trace tests still pass.
- `/rag/search` and `/rag/ask` tests still pass.

## Checks

- `ruff format --check .`
- `ruff check .`
- `mypy .`
- backend pytest
- frontend lint / typecheck / test / build when frontend files change
- Docker compose CI config and smoke when available
