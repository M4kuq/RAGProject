# Retrieval Strategy Schema

## RetrievalStrategy

| value | PR-23 behavior | future owner |
|---|---|---|
| `dense` | default dense retrieval and `/rag/ask` strategy | PR-20/21/22 |
| `sparse` | implemented for `/rag/search` standalone lexical retrieval | PR-23/25 |
| `hybrid` | schema-only value for future evaluation comparison | PR-24/25 |
| `multi_query_dense` | schema-only value | PR-27+ |
| `multi_query_hybrid` | schema-only value | PR-27+ |
| `metadata_filtered` | schema-only value | PR-27+ |
| `version_aware` | schema-only value | PR-27+ |
| `agentic_router` | schema-only value | PR-28+ |
| `fallback_dense` | schema-only value | PR-28+ |

Python enum values and DB CHECK constraints must stay aligned.

## RetrievalSource

`retrieval_run_items.retrieval_source` is item-level provenance. PR-21 stores `dense` metadata for the existing dense flow. PR-23 stores `sparse` for standalone lexical retrieval. `hybrid`, `fallback_dense`, and `metadata_filter` are reserved for later PRs.

## Trace DTO

The trace DTOs use `phase2.trace.v1`:

- `QueryPlanTrace`
- `StrategyDecisionTrace`
- `LatencyBreakdown`
- `RetrievalSettingsSnapshot`
- `ScoreBreakdown`

They are JSON serializable and must not carry raw prompt, raw query, full context, raw chunk text, PII, secrets, tokens, or credentials.

PR-23 sparse trace adds:

- `query_plan_json.reason_codes = ["phase2_sparse_lexical", "normalized_terms:<count>"]`
- `strategy_decision_json.selected_strategy = "sparse"`
- `latency_breakdown_json.sparse_search_ms`
- `retrieval_settings_json.sparse_provider`
- `retrieval_settings_json.sparse_language`
- `retrieval_settings_json.sparse_score_normalization`
- `score_breakdown_json.sparse_score`

## Evaluation DTO

PR-22 adds `phase2.evaluation.v1` and `phase2.evaluation_dataset.v1` DTOs:

- `MetricSpec`
- `MetricValue`
- `MetricSummary`
- `StrategyMetricSummary`
- `EvaluationCaseSpec`
- `EvaluationDatasetManifest`

These DTOs are strategy-aware and redaction-aware. Metric detail is limited to safe counts, units, labels, case keys, and reason codes.

## Strategy Evaluation Boundary

PR-22 stores strategy metadata but does not implement non-dense execution. If a non-dense run reaches the existing minimal runner, it is recorded as not implemented rather than pretending dense results are sparse/hybrid results. PR-25 owns strategy execution.
