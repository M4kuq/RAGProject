# Strategy Router / Agentic Retrieval Control

## Purpose

PR-28 adds the first Agentic Control execution step after PR-27 query planning. It takes the safe query analysis and query plan, chooses one retrieval strategy, executes it once, and stores the redacted router decision for observability when enabled.

This PR does not add a retrieval loop. Context sufficiency checks, fallback loops, multi-query execution, metadata-filtered execution, and version-aware execution are deferred to PR-29 and later.

## Inputs

The router reads only safe planning metadata:

- `QueryAnalysisTrace.intent`
- `ambiguity_score`
- `keyword_heavy_score`
- `version_specific_flag`
- `QueryPlannerTrace.candidate_strategies`
- current safe settings such as sparse/hybrid availability

It does not receive raw prompt, full context, raw chunk text, document payload dumps, credentials, or external tool permissions.

## Output

Router decisions are stored as `phase2.router.v1` in `retrieval_runs.strategy_decision_json` when `ROUTER_STORE_DECISION_TRACE=true`. Setting `ROUTER_STORE_DECISION_TRACE=false` keeps the router execution behavior but suppresses `strategy_decision_json` persistence.

```json
{
  "schema_version": "phase2.router.v1",
  "requested_strategy": "agentic_router",
  "selected_strategy": "hybrid",
  "execution_strategy": "hybrid",
  "decision_source": "rule_based",
  "fallback_used": false,
  "router_enabled": true,
  "confidence": 0.72,
  "reason_codes": ["keyword_heavy", "hybrid_available"],
  "disabled_candidates": ["version_aware"],
  "safety_flags": ["single_retrieval_call", "no_agentic_loop", "no_external_action"]
}
```

`retrieval_runs.strategy_type` remains `agentic_router` when the router was requested. The executed strategy is recorded in `execution_strategy` when decision trace persistence is enabled.

## Routing Rules

PR-28 uses deterministic rule-based routing:

- router disabled or unavailable: configured fallback strategy (`fallback_dense` by default, or `dense` with `ROUTER_FALLBACK_STRATEGY=dense`)
- version-specific query: prefer `hybrid`; `version_aware` remains disabled/planned
- keyword-heavy query: prefer `hybrid`, then `sparse`, then `dense`
- comparison intent: prefer `hybrid`
- high ambiguity: prefer `hybrid`, then the configured fallback strategy
- normal factual lookup: `dense`

Only `dense`, `sparse`, `hybrid`, and `fallback_dense` are executable in PR-28. `multi_query_dense`, `multi_query_hybrid`, `metadata_filtered`, and `version_aware` are recorded as disabled candidates and reduced to a safe executable strategy.

## API Behavior

`/api/v1/rag/search` accepts:

```json
{
  "query": "HTTP 500 API_ERROR /api/v1/rag/search",
  "strategy": "agentic_router",
  "top_k": 20,
  "rerank_top_n": 5
}
```

`/api/v1/rag/ask` keeps the default dense behavior. It uses the router only when the request explicitly sets `strategy=agentic_router`.

Router failures never expose exception messages. If routing fails, the service records `fallback_reason=router_error` and executes the configured fallback strategy.

## Trace and Debug UI

PR-28 adds `strategy_router_ms` to latency breakdowns. Retrieval Debug UI displays requested, selected, and execution strategy, fallback status, confidence, reason codes, disabled candidates, and safety flags when router decision trace persistence is enabled.

The UI and API continue to redact sensitive keys and values. Raw query text is not included in router decision JSON.

## Security Rules

The router may only choose retrieval strategy. It cannot execute admin actions, external tools, writes, shell commands, or network exports.

Decision traces must not include:

- raw prompt
- full context
- raw chunk text
- PII
- secrets, tokens, credentials, cookies, sessions, or CSRF values
- raw router exception messages

## PR-29 Handoff

PR-29 should add the Agentic Retrieval Loop on top of this single-call router:

- context sufficiency check
- bounded additional retrieval calls
- fallback and merge/dedupe behavior
- budget control
- loop-level trace fields
