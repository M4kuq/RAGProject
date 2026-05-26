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

- Alembic head is `0004_eval_dataset_metrics`.
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

## PR-23 Sparse Retrieval Tests

- Alembic head is `0005_sparse_retrieval_fts`.
- PostgreSQL schema includes language-matched sparse FTS indexes.
- Sparse query normalization lowercases, deduplicates, and enforces the max term limit.
- Sparse score normalization ranks by raw score and tie-breaks by `document_chunk_id ASC`.
- `/rag/search strategy=sparse` writes `retrieval_runs.strategy_type = sparse`.
- Sparse items write `retrieval_source = sparse` and `score_breakdown_json.sparse_score`.
- Sparse no-result returns `200 OK` with `items=[]`.
- Sparse failure marks the run failed with safe trace.
- Dense `/rag/search` and `/rag/ask` remain default dense.
- Trace, response, and score breakdown do not include raw query, raw prompt, raw chunk text, full context, PII, or secrets.

## PR-24 Hybrid Retrieval Tests

- Hybrid fusion deduplicates by `document_chunk_id`.
- RRF and weighted fusion are deterministic.
- Fused scores normalize to `0.0..1.0`.
- `/rag/search strategy=hybrid` writes `retrieval_runs.strategy_type = hybrid`.
- Hybrid items write `retrieval_source = hybrid`.
- Hybrid score breakdown writes dense score, sparse score, fused score, fusion method, and rank metadata.
- Hybrid trace writes query plan, strategy decision, retrieval settings, `qdrant_search_ms`, `sparse_search_ms`, `fusion_ms`, and final-check latency.
- Hybrid disabled returns `strategy_not_enabled` without creating a retrieval run.
- Dense `/rag/search`, sparse `/rag/search`, `/rag/ask`, and evaluation dataset regressions remain green.
- Trace, response, and score breakdown do not include raw query, raw prompt, raw chunk text, full context, PII, or secrets.

## Checks

- `ruff format --check .`
- `ruff check .`
- `mypy .`
- backend pytest
- frontend lint / typecheck / test / build when frontend files change
- Docker compose CI config and smoke when available
