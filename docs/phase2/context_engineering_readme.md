# Context Engineering README

Context Engineering in Phase2.5 is the combined safety and operability layer around retrieved context and Auto tool results. It spans PR-40, PR-41, and PR-42.

## Components

| Component | PR | Runtime position | Safe trace |
|---|---:|---|---|
| Context Budget | PR-40 | before answer generation | `retrieval_runs.context_budget_json` |
| Evidence Pack | PR-41 | after Context Budget, before answer generation | `retrieval_runs.context_compression_json` |
| Tool Result Compression | PR-42 | before Auto planner sees tool output | `retrieval_runs.tool_result_compression_json` |

Auto from PR-39 and local MCP `rag_ask_auto` from PR-42 use these same safe boundaries.

## Flow

```text
Chat UI or MCP asks with Auto
 -> LLM tool-calling retrieval orchestrator runs retrieval-only tools
 -> Tool Result Compression bounds planner-visible tool results
 -> final retrieved item refs are persisted
 -> Context Budget selects refs for generation
 -> Evidence Pack compresses final retrieved context and preserves citations
 -> generation runs
 -> Chat UI and MCP return safe answer/citations/confidence/summary
 -> admin Retrieval Debug shows safe summaries
```

## Auto Demo Surface

Use Chat UI with strategy Auto/LLM Orchestrator. Confirm:

- answer is returned
- user-facing summary identifies the actual strategy used, such as Hybrid RAG
- `retrieval_summary.strategy_type` is `llm_tool_orchestrator`
- `selected_strategy`, `execution_strategy`, and `tools_used` are visible in safe admin debug or safe summaries

## Context Budget Demo Surface

In admin Retrieval Debug, open the run created by Auto and check the Context Budget panel:

- `enabled`
- `budget.max_context_tokens`
- `usage.estimated_context_tokens`
- `usage.remaining_context_tokens`
- `items.candidate_count`
- `items.selected_count`
- `items.dropped_count`
- `drop_reasons`
- safe selected and dropped refs

The panel must not show raw prompt, full context, raw chunk text, snippets, PII, or secrets.

## Evidence Pack Demo Surface

In the same run, check the Evidence Pack panel:

- `method = deterministic_evidence_pack`
- input selected item count
- output evidence item count
- evidence group count
- compression ratio
- duplicate/drop counts
- evidence item refs
- citation candidate count

Confirm that citation mapping is preserved through safe refs. Do not expose generated evidence text or raw chunk text.

## Tool Result Compression Demo Surface

For Auto runs, check the Tool Result Compression panel:

- tool call count
- search tool call count
- original/output/dropped item counts
- estimated tokens before and after
- compression ratio
- per-tool summary
- drop reasons
- budget exhausted and oversized rejected counts
- safe item refs

This panel is admin-only. Viewer Chat UI must not render it.

## MCP `rag_ask_auto`

Run the local stdio MCP server from `backend` and call `rag_ask_auto` with a demo question. The MCP output should include safe answer/citation/confidence fields and may include `auto_strategy_summary`.

It must not include raw trace payloads, raw tool payloads, full context, raw chunk text, token values, secrets, or local paths.

## Retrieval Debug Checklist

Use [context_engineering_acceptance_checklist.md](context_engineering_acceptance_checklist.md) for final acceptance. The minimum admin debug fields are:

- selected/execution strategy
- tools used
- Context Budget counts and budget state
- Evidence Pack compression ratio and groups
- Tool Result Compression counts and ratio
- redacted source labels and refs only

## Security Invariants

Forbidden in docs, logs, artifacts, UI, MCP output, and persisted safe traces:

- raw prompt
- full context
- raw chunk text
- raw tool payload
- PII
- tokens, secrets, credentials, cookies, sessions, passwords, or private keys
- `.env` values
- kubeconfig
- local storage paths or DB/Qdrant dumps

Allowed:

- counts
- booleans
- strategy labels
- tool names
- bounded IDs
- source labels already sanitized by the app
- hashes
- token estimates
- compression ratios
- drop reason counts

## Related Docs

- [phase2_5_readme.md](phase2_5_readme.md)
- [context_budget_trace_debug.md](context_budget_trace_debug.md)
- [evidence_pack_context_compression.md](evidence_pack_context_compression.md)
- [tool_result_compression_orchestrator_guard.md](tool_result_compression_orchestrator_guard.md)
- [context_engineering_demo_scenario.md](context_engineering_demo_scenario.md)
- [context_engineering_manual_test_cases.md](context_engineering_manual_test_cases.md)
- [context_engineering_acceptance_checklist.md](context_engineering_acceptance_checklist.md)
- [context_engineering_known_limitations.md](context_engineering_known_limitations.md)
