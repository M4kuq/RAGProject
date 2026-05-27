# PR-29 Agentic Retrieval Loop / Context Sufficiency Check

## Purpose

PR-29 adds a bounded `AgenticRetrievalExecutor` on top of PR-28 `StrategyRouter`. The router still chooses the initial execution strategy, but `agentic_router` requests can now check whether the first retrieval result is sufficient and, when budget allows, run one deterministic fallback retrieval before final persistence.

This PR does not implement Graph-RAG, OCR, multi-agent orchestration, unbounded self-reflection, external operation agents, LangSmith export, or SentenceTransformers experiments.

## Execution Flow

`/api/v1/rag/search` and explicit `/api/v1/rag/ask strategy=agentic_router` use this flow:

1. Build safe query analysis and query plan.
2. Run `StrategyRouter` to get the initial execution strategy.
3. Execute the initial retrieval without persisting items.
4. Run `ContextSufficiencyChecker` on counts, scores, source diversity, and safe query intent.
5. If sufficient, merge/dedupe the initial result and rerank once before saving final items.
6. If insufficient and budget remains, run one fallback strategy, merge/dedupe by `document_chunk_id`, rerank the merged candidates, and check sufficiency again.
7. If still insufficient, search returns best-effort debug results while ask returns `422 no_context_found` without creating an assistant message.

## Budget

Seeded defaults:

- `rag.router.max_retrieval_calls = 2`
- `rag.router.max_fallback_calls = 1`
- `rag.router.sufficiency_min_candidates = 1`
- `rag.router.sufficiency_min_selected = 1`
- `rag.router.sufficiency_top_score_threshold = 0.2`
- `rag.router.enable_fallback_hybrid = true`
- `rag.router.enable_fallback_dense = true`
- `rag.router.no_context_after_budget_exhausted = true`

`max_retrieval_calls` is bounded to `1..3`. There is no unbounded loop and fallback recursion is not allowed.

## Sufficiency Rules

The checker is deterministic and does not read raw chunk text. It considers:

- post-final-check candidate count
- selected candidate count
- top retrieval score
- source diversity for comparison intent

Reason codes include `no_candidates`, `too_few_candidates`, `too_few_selected_candidates`, `low_top_score`, `insufficient_source_diversity_for_comparison`, and `sufficient_context`.

## Fallback

Fallback is deterministic:

- initial `dense` or `fallback_dense` tries `hybrid` first when enabled, then dense fallback.
- initial `sparse` tries `hybrid`, then dense fallback.
- initial `hybrid` tries dense fallback.

Unavailable fallback strategies are skipped. Router failures still fall back to dense through PR-28 behavior.

## Merge / Dedupe / Rerank

Candidates are merged by `document_chunk_id`. The merged candidate keeps safe score payload fields such as dense, sparse, and fused scores where available, and records source labels such as `initial_dense` and `fallback_hybrid` in `score_breakdown_json`. Raw chunk text is not stored in trace or score breakdown.

The final merged candidates are reranked once before persistence. `retrieval_run_items.retrieval_source` remains one of the existing DB-safe item sources, while `score_breakdown_json.retrieval_source = "agentic_router"` identifies the agentic merge context.

## Trace

`strategy_decision_json` adds safe fields:

- `agentic_schema_version`
- `agentic_fallback_used`
- `fallback_used`
- `fallback_strategy`
- `fallback_reason`
- `retrieval_call_count`
- `budget_exhausted`
- `sufficiency_score`
- `sufficiency_reason_codes`
- `sufficiency_decisions`
- candidate counts and final selected count
- `no_context`

`latency_breakdown_json` can include:

- `agentic_total_ms`
- `initial_retrieval_ms`
- `fallback_retrieval_ms`
- `sufficiency_check_ms`
- `merge_dedupe_ms`
- `rerank_after_merge_ms`

`retrieval_settings_json` records the budget and sufficiency thresholds.

## Security

The loop stores only counts, hashes, scores, durations, reason codes, strategy names, and safe item metadata. It does not store raw prompt, full context, raw chunk text, PII, secrets, raw exception messages, or external tool actions.

## PR-30 Handoff

PR-30 can add `agentic_router` to strategy evaluation and use these trace fields for `strategy_selection_accuracy`, failure dataset promotion, and no-context improvement analysis.
