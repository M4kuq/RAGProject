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

## PR-25 Strategy Evaluation Runner Tests

- `EvaluationRunCreateRequest` accepts `strategies=["dense", "sparse", "hybrid"]` and rejects `agentic_router`.
- Archived or empty persistent datasets cannot queue a run.
- Worker execution creates one item per case per strategy.
- Dense, sparse, and hybrid items link to strategy-specific `retrieval_runs`.
- `evaluation_results` stores `recall_at_k`, `mrr`, `citation_coverage`, `groundedness`, `faithfulness`, `no_context_rate`, `p95_latency`, and not-applicable `strategy_selection_accuracy`.
- Partial case failures leave the run succeeded with `failed_count` in the summary.
- All case/strategy failures mark the run failed.
- `strategy_metrics_summary_json` and the strategy comparison API aggregate metrics by strategy.
- Metric detail JSON contains counts, ranks, units, and reason codes only; it does not contain raw prompt, full context, raw chunk text, PII, or secrets.

## PR-26 Retrieval Debug UI v2 Tests

- `GET /api/v1/rag/retrieval-runs/{retrieval_run_id}` is admin-only, read-only, and returns redacted trace/items.
- The detail response excludes raw prompt, full context, raw chunk text, PII, token, secret, credential, API key, password, CSRF, session, and cookie values.
- `/admin/retrieval-debug` is protected by the admin route guard.
- The strategy selector exposes `dense`, `sparse`, and `hybrid`, while router and multi-query strategies are disabled as coming soon.
- The debug form calls `/api/v1/rag/search` with the selected strategy and CSRF token.
- The page displays run summary, query plan, strategy decision, retrieval settings, latency breakdown, score summary, score breakdown, retrieval run items, and latest strategy evaluation summary.
- Frontend redaction helpers remove forbidden trace keys and secret-like assignment values before rendering.

## PR-27 Query Analyzer / Query Planner Tests

- `QueryAnalyzer` intent classification is deterministic.
- Ambiguity, keyword-heavy, temporal, and version-specific signals are deterministic.
- `QueryPlanner` rewrite metadata is deterministic and does not apply to retrieval by default.
- Planned sub-query count respects `rag.query_planner.max_sub_queries`.
- Metadata filter candidates are structured and not raw SQL/Qdrant filter strings.
- Candidate strategies are proposals only; StrategyRouter is not executed.
- `/rag/search strategy=dense`, `sparse`, and `hybrid` write safe analyzer/planner fields to `query_plan_json`.
- `/rag/ask` writes safe analyzer/planner fields while preserving default dense behavior.
- The original user query preview is not persisted; derived query previews are redacted/truncated and do not expose raw prompt, full context, raw chunk text, PII, or secrets.
- Retrieval Debug UI renders analysis/planning summary and tolerates missing fields.

## PR-28 Strategy Router Tests

- `StrategyRouter` disabled or failing returns the configured fallback strategy (`fallback_dense` by default, `dense` when configured).
- Keyword-heavy and comparison queries select `hybrid` when available.
- Normal factual queries select `dense`.
- Version-specific queries record disabled `version_aware` candidates and execute an implemented strategy.
- Sparse/hybrid unavailable states fall back to `dense`.
- `/rag/search strategy=agentic_router` persists `retrieval_runs.strategy_type = agentic_router`.
- Router decisions persist requested, selected, and execution strategies in `strategy_decision_json` when enabled.
- `router_store_decision_trace=false` suppresses router decision persistence without changing execution.
- Router latency is recorded as `strategy_router_ms`.
- `/rag/ask strategy=agentic_router` is explicit opt-in; default ask remains dense.
- Retrieval Debug UI renders router decision, fallback, confidence, reason codes, disabled candidates, and safety flags.
- Router decision JSON does not contain raw query, raw prompt, full context, raw chunk text, PII, secrets, or raw exception messages.

## PR-29 Agentic Retrieval Loop Tests

- `ContextSufficiencyChecker` accepts enough candidates and rejects zero, low-score, and low-diversity comparison results.
- `AgenticRetrievalExecutor` respects `max_retrieval_calls` and `max_fallback_calls`.
- Initial insufficient context triggers one deterministic fallback when budget remains.
- Fallback results merge and dedupe by `document_chunk_id`.
- Merged candidates are reranked before final persistence.
- `/rag/search strategy=agentic_router` persists `retrieval_call_count`, fallback, sufficiency, and agentic latency fields.
- `/rag/ask strategy=agentic_router` returns `422 no_context_found` without an assistant message when budget is exhausted.
- Direct dense, sparse, and hybrid strategy regressions remain green.
- Agentic trace and score breakdown do not contain raw query, raw prompt, full context, raw chunk text, PII, secrets, or raw exception messages.

## PR-30 Agentic Strategy Evaluation Tests

- `EvaluationRunCreateRequest` accepts `dense`, `sparse`, `hybrid`, and `agentic_router` in the same run.
- Agentic metric rows persist `strategy_selection_accuracy`, `fallback_rate`, `budget_exhausted_rate`, `sufficiency_score_avg`, and `retrieval_call_count_avg`.
- `strategy_selection_accuracy` is numeric only when a case defines `expected_strategy` or `acceptable_strategies`.
- Failure candidates are extracted for no-context, low-score, citation, strategy mismatch, budget, latency, and safe exception reasons.
- Promotion into an active dataset is idempotent; duplicate promotion returns an existing/skipped result.
- Failure candidate and promotion responses do not expose raw prompt, full context, raw chunk text, PII, or secrets.

## Checks

- `ruff format --check .`
- `ruff check .`
- `mypy .`
- backend pytest
- frontend lint / typecheck / test / build when frontend files change
- Docker compose CI config and smoke when available
