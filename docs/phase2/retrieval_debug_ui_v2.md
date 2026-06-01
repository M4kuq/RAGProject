# PR-26 Retrieval Debug UI v2

PR-26 adds an admin-only debug surface for the Phase2 retrieval trace and score schema.

## Purpose

The page at `/admin/retrieval-debug` lets admins run the existing standalone `/api/v1/rag/search` path with:

- `dense`
- `sparse`
- `hybrid`
- `agentic_router`

The page visualizes the safe trace produced by PR-21 and the sparse/hybrid score metadata added by PR-23 and PR-24. It also shows the strategy comparison summary produced by PR-25 when recent evaluation runs are available.

## Out of Scope

PR-26 does not implement QueryAnalyzer, QueryPlanner, StrategyRouter, Agentic Retrieval Loop, LangSmith export, SentenceTransformers experiments, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

Future strategies are shown as disabled or coming soon:

- `multi_query_dense`
- `multi_query_hybrid`
- `metadata_filtered`
- `version_aware`

## API

PR-26 uses:

- `POST /api/v1/rag/search`
- `GET /api/v1/rag/retrieval-runs/{retrieval_run_id}`
- `GET /api/v1/evaluations/runs`

`POST /api/v1/rag/search` remains admin-only and CSRF-protected. The retrieval run detail endpoint is admin-only and does not require CSRF because it is read-only.

## Displayed Data

The debug page displays:

- strategy and run status
- `retrieval_score_summary`
- `query_plan_json`
- `strategy_decision_json`
- `retrieval_settings_json`
- `latency_breakdown_json`
- `retrieval_run_items`
- `retrieval_run_items.score_breakdown_json`
- dense, sparse, fused/fusion, and rerank scores
- rank, rerank order, selected flag, source label, page, and bounded snippet
- RDB final check exclusion count
- latest available strategy evaluation summary

Query analysis, rewritten-query preview, planned sub-query preview, metadata filter candidates, and candidate strategy proposals are populated by PR-27. PR-28 populates router decision and fallback fields when admins choose `agentic_router`.

## Redaction Rules

The backend endpoint and frontend renderer both redact defensively. The UI must not display:

- raw prompt
- full context
- raw chunk text
- raw document payload dumps
- PII
- secret, token, credential, API key, password, CSRF, session, or cookie values

The UI renders text through React text nodes and does not inject HTML. Snippets and source labels are truncated for display. Trace JSON is summarized first, with a collapsible safe-field view for unknown future fields.

## PR-27 Integration

The Query Plan panel displays:

- intent
- ambiguity score and flags
- keyword-heavy score
- version-specific flag
- rewritten query preview
- planned sub-query previews
- metadata filter candidates
- candidate strategies
- recommended strategy
- `planned_only` safety flags

These fields are for inspection only in PR-27. PR-28 can populate router decision metadata in `strategy_decision_json` and decide whether to execute the recommended strategy.

## PR-28 Integration

The Strategy Decision panel displays:

- requested strategy
- selected strategy
- execution strategy
- decision source
- router enabled
- fallback used and fallback reason
- confidence
- reason codes
- disabled candidates
- safety flags

`agentic_router` runs a single selected retrieval strategy in PR-28. PR-29 adds a bounded retrieval loop and the Debug UI displays the additional safe fields when present:

- `retrieval_call_count`
- `fallback_used`, `fallback_strategy`, and `fallback_reason`
- `budget_exhausted`
- `sufficiency_score` and `sufficiency_reason_codes`
- initial, merged, deduped, and final selected candidate counts
- `agentic_total_ms`, `initial_retrieval_ms`, `fallback_retrieval_ms`,
  `sufficiency_check_ms`, `merge_dedupe_ms`, and `rerank_after_merge_ms`

Multi-query execution, metadata-filtered execution, version-aware retrieval, Graph-RAG, OCR, and external observability export remain out of scope.

## PR-40 Context Budget Panel

PR-40 adds a Context Budget panel to the same admin Retrieval Debug surface. The
panel reads safe `retrieval_runs.context_budget_json` metadata when present.
Search-only runs can show an empty state because context budget is applied to
`/rag/ask` immediately before generation.

Displayed fields:

- max context tokens
- estimated context tokens
- remaining context tokens
- selected and dropped context item counts
- drop reason counts
- citation candidate count
- source count and source breakdown
- selected and dropped safe item refs
- budget exhausted flag

The panel does not display raw prompt, full context, raw chunk text, snippets,
raw tool output, PII, token values, secrets, credentials, sessions, cookies, or
local paths. Numeric token estimates are safe bounded counts.

## PR-41 Evidence Pack Panel

PR-41 adds an Evidence Pack panel to the same admin Retrieval Debug surface. The
panel reads safe `retrieval_runs.context_compression_json` metadata when present.
Search-only runs can show an empty state because Evidence Pack construction is
applied to `/rag/ask` after context budget selection and before generation.

Displayed fields:

- enabled
- compression method
- input selected context item count
- output evidence item count
- evidence group count
- compression ratio
- duplicate and source-limit drop counts
- max items per source
- evidence groups
- evidence item refs
- dropped evidence refs
- citation candidate count

The panel does not display raw prompt, raw query, full context, raw chunk text,
`evidence_text_for_generation`, raw tool output, snippets, PII, token values,
secrets, credentials, sessions, cookies, or local paths. Viewer chat UI does not
render Evidence Pack debug internals.
