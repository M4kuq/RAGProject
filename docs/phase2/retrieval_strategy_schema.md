# Retrieval Strategy Schema

## RetrievalStrategy

| value | PR-24 behavior | future owner |
|---|---|---|
| `dense` | default dense retrieval and `/rag/ask` strategy | PR-20/21/22 |
| `sparse` | implemented for `/rag/search` standalone lexical retrieval | PR-23/25 |
| `hybrid` | implemented for `/rag/search` dense+sparse fusion | PR-24/25 |
| `multi_query_dense` | schema-only value | PR-27+ |
| `multi_query_hybrid` | schema-only value | PR-27+ |
| `metadata_filtered` | schema-only value | PR-27+ |
| `version_aware` | schema-only value | PR-27+ |
| `agentic_router` | schema-only value | PR-28+ |
| `fallback_dense` | schema-only value | PR-28+ |

Python enum values and DB CHECK constraints must stay aligned.

## RetrievalSource

`retrieval_run_items.retrieval_source` is item-level provenance. PR-21 stores `dense` metadata for the existing dense flow. PR-23 stores `sparse` for standalone lexical retrieval. PR-24 stores `hybrid` for dense+sparse fused retrieval. `fallback_dense` and `metadata_filter` are reserved for later PRs.

## Trace DTO

The trace DTOs use `phase2.trace.v1`:

- `QueryPlanTrace`
- `StrategyDecisionTrace`
- `LatencyBreakdown`
- `RetrievalSettingsSnapshot`
- `ScoreBreakdown`

They are JSON serializable and must not carry raw prompt, raw query, full context, raw chunk text, PII, secrets, tokens, or credentials.

PR-27 query analysis and planning extends `QueryPlanTrace` with safe planned-only fields:

- `analysis.intent`
- `analysis.ambiguity_score`
- `analysis.keyword_heavy_score`
- `analysis.version_specific_flag`
- `planner.rewrite_applied`
- `planner.rewritten_query_hash`
- bounded `planner.rewritten_query_preview`
- `planner.sub_queries`
- `planner.metadata_filter_candidates`
- `planner.candidate_strategies`
- `planner.recommended_strategy`
- `planner.safety_flags`

Top-level summary copies are stored for Retrieval Debug UI rendering. The original user query preview is not persisted; derived previews are redacted and truncated. Candidate strategies are not executed until the Strategy Router PR.

PR-23 sparse trace adds:

- `query_plan_json.reason_codes = ["phase2_sparse_lexical", "normalized_terms:<count>"]`
- `strategy_decision_json.selected_strategy = "sparse"`
- `latency_breakdown_json.sparse_search_ms`
- `retrieval_settings_json.sparse_provider`
- `retrieval_settings_json.sparse_language`
- `retrieval_settings_json.sparse_score_normalization`
- `score_breakdown_json.sparse_score`

PR-24 hybrid trace adds:

- `query_plan_json.reason_codes = ["phase2_hybrid_dense_sparse", "fusion_method:<method>", "normalized_terms:<count>"]`
- `strategy_decision_json.selected_strategy = "hybrid"`
- `latency_breakdown_json.fusion_ms`
- `retrieval_settings_json.fusion_method`
- `retrieval_settings_json.hybrid_rrf_k`
- `retrieval_settings_json.hybrid_dense_weight`
- `retrieval_settings_json.hybrid_sparse_weight`
- `retrieval_settings_json.hybrid_candidate_multiplier`
- `score_breakdown_json.dense_score`
- `score_breakdown_json.sparse_score`
- `score_breakdown_json.fused_score`
- `score_breakdown_json.fusion_method`

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

PR-22 stores strategy metadata but does not implement non-dense evaluation execution. PR-23 and PR-24 make sparse and hybrid executable for `/rag/search`; PR-25 owns dataset-wide Strategy Evaluation Runner execution.
