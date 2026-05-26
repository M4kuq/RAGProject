# PR-27 Query Analyzer / Query Planner

PR-27 adds a deterministic rule-based query analysis and planning layer before future agentic retrieval control.

## Purpose

The analyzer and planner produce safe metadata for `retrieval_runs.query_plan_json` so PR-28 Strategy Router and PR-29 Agentic Retrieval Loop can decide how to route or expand retrieval without changing the existing PR-27 execution path.

PR-27 does not implement automatic strategy routing. Explicit `/api/v1/rag/search` `strategy` values still determine the executed retrieval strategy, and `/api/v1/rag/ask` remains default dense.

## Analyzer Signals

`QueryAnalyzer` classifies and stores:

- `intent`: `factual_lookup`, `procedural`, `comparison`, `summarization`, `troubleshooting`, `definition`, `version_specific`, or `unknown`
- `ambiguity_score`
- `ambiguity_flags`
- `needs_clarification_candidate`
- `keyword_heavy_score`
- `keyword_signals`
- `version_specific_flag`
- `version_hints`
- `temporal_reference_flag`
- safe metadata filter hints
- candidate strategies for later router use

The implementation is deterministic and rule-based. It uses token shape, keyword sets, endpoint/file-extension/error-code signals, version terms, and ambiguity references. It does not call an LLM or external API.

## Planner Output

`QueryPlanner` stores:

- `rewrite_applied`
- `rewritten_query_hash`
- bounded `rewritten_query_preview`
- up to `rag.query_planner.max_sub_queries` planned sub-query previews
- structured `metadata_filter_candidates`
- `candidate_strategies`
- `recommended_strategy`
- `disabled_reason = strategy_router_not_implemented`
- `safety_flags` including `planned_only`

Sub-queries, metadata filters, `multi_query_*`, `metadata_filtered`, and `version_aware` are only planned in PR-27. They are not executed.

## Query Plan Trace

`query_plan_json` remains `phase2.trace.v1` and includes the previous strategy-specific fields:

- `strategy_type`
- `query_mode`
- `query_hash`
- `metadata_filter_applied`
- `reason_codes`

PR-27 adds nested `analysis` and `planner` payloads plus top-level summary fields that the Retrieval Debug UI can render. The executed strategy remains the explicit request strategy for `/rag/search` or dense for `/rag/ask`.

## Rewrite Policy

`rag.query_planner.apply_rewrite_to_retrieval` defaults to `false`.

The planner can compute a normalized rewrite hash and preview, but PR-27 does not apply rewritten queries to retrieval unless the setting is explicitly enabled. This preserves dense / sparse / hybrid regression behavior.

## Redaction Policy

The trace stores a SHA-256 `query_hash`. It does not persist the original user query preview. Derived previews such as rewritten query previews and sub-query previews are stored only when available, and they pass through redaction and truncation:

- no raw prompt
- no full context
- no raw chunk text
- no original raw user query preview
- no secret, token, credential, session, cookie, CSRF, or private key
- no email, URL, phone-number-like PII, or secret assignment value

If `rag.query_planner.redact_pii = false`, PR-27 does not store unredacted previews. It disables derived preview persistence and keeps hashes / counts / structured reason codes only.

The backend redactor is still applied again when returning retrieval-run detail to the Debug UI.

## Settings

Seeded `system_settings` defaults:

- `rag.query_analyzer.enabled = true`
- `rag.query_planner.enabled = true`
- `rag.query_planner.apply_rewrite_to_retrieval = false`
- `rag.query_planner.max_sub_queries = 3`
- `rag.query_planner.max_preview_chars = 160`
- `rag.query_planner.store_query_preview = true`
- `rag.query_planner.redact_pii = true`

Preview persistence requires both `store_query_preview` and `redact_pii` to be true.

The seed remains idempotent and does not overwrite existing values.

## PR-28 / PR-29 Handoff

PR-28 can consume `candidate_strategies`, `recommended_strategy`, `intent`, `keyword_heavy_score`, `version_specific_flag`, and `metadata_filter_candidates` to implement Strategy Router.

PR-29 can consume planned `sub_queries`, ambiguity flags, and `needs_clarification_candidate` for agentic retrieval loop and context sufficiency handling.
