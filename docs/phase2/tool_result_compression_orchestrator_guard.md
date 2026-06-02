# PR-42 Tool Result Compression / Orchestrator Context Guard

PR-42 adds a guardrail between the LLM tool-calling retrieval orchestrator and
the retrieval tool results returned to the planner. It is separate from PR-41
Evidence Pack construction: Evidence Pack shapes the final context for answer
generation, while Tool Result Compression bounds the intermediate retrieval
tool output that the orchestrator LLM sees.

## Scope

Implemented:

- `ToolResultCompressor`
- `ToolResultBudgetManager`
- `OrchestratorContextGuard`
- bounded `ToolResultItem` planner payloads
- per-tool item/token budget
- per-turn item/token budget
- duplicate and same-chunk result reduction
- repeated tool result detection
- oversized compressed output rejection
- safe `retrieval_runs.tool_result_compression_json`
- admin Retrieval Debug Tool Result Compression panel
- local MCP `rag_ask_auto` wrapper for `llm_tool_orchestrator`

Not implemented:

- Evidence Pack reimplementation
- LLM summarization for tool result compression
- external summarizers or model downloads
- Graph-RAG, OCR, multimodal, AWS/S3/OIDC
- write/admin/external-operation tools
- remote MCP transports

## Flow

```text
LLM requests retrieval tool
 -> execute dense/sparse/hybrid retrieval internally
 -> ToolResultCompressor bounds snippets and removes duplicates
 -> ToolResultBudgetManager enforces per-tool/per-turn budgets
 -> OrchestratorContextGuard returns safe tool result payload to the planner
 -> final selected compressed refs feed retrieval item persistence
 -> ContextBudgetManager and EvidencePackBuilder run before generation
```

Only compressed tool result items can become final orchestrator context. If a
tool output is rejected or all items are dropped, the tool result is represented
as a safe empty/error result and cannot be selected by `finalize_answer`.

## Budgets

Defaults are intentionally loose enough for existing Auto behavior:

```text
rag.tool_result_compression.enabled = true
rag.tool_result_compression.max_items_per_tool = 8
rag.tool_result_compression.max_total_items_per_turn = 20
rag.tool_result_compression.max_snippet_chars = 500
rag.tool_result_compression.max_tokens_per_tool = 1200
rag.tool_result_compression.max_total_tool_result_tokens = 3000
rag.tool_result_compression.drop_low_score_first = true
rag.tool_result_compression.group_by_source = true
rag.tool_result_compression.reject_oversized_output = true
rag.tool_result_compression.store_debug_trace = true
```

Token counts use the same deterministic heuristic as PR-40:

```text
estimated_tokens = ceil(char_count / 4)
```

This is an operational estimate, not a tokenizer-accurate count.

## Safe Tool Result Item

The planner may receive:

- `tool_call_id`
- `document_chunk_id`
- safe `source_label`
- safe `section_title`
- page range
- bounded/redacted `snippet`
- score/rank metadata
- `citation_candidate`
- `estimated_tokens`
- `source_group_key`

The planner never receives full chunk text, full context, raw prompt, raw tool
payload, local paths, tokens, secrets, or credentials.

## Trace Schema

Safe trace is stored in `retrieval_runs.tool_result_compression_json`:

```json
{
  "schema_version": "phase2.tool_result_compression.v1",
  "enabled": true,
  "budget": {
    "max_items_per_tool": 8,
    "max_total_items_per_turn": 20,
    "max_snippet_chars": 500,
    "max_tokens_per_tool": 1200,
    "max_total_tool_result_tokens": 3000
  },
  "summary": {
    "tool_call_count": 2,
    "search_tool_call_count": 2,
    "original_item_count": 12,
    "output_item_count": 6,
    "dropped_item_count": 6,
    "estimated_tokens_before": 4200,
    "estimated_tokens_after": 760,
    "compression_ratio": 0.181,
    "budget_exhausted": false,
    "repeated_result_count": 0,
    "oversized_rejected_count": 0
  },
  "drop_reasons": {
    "max_items_limit": 3,
    "same_chunk_deduped": 2
  },
  "by_tool": [],
  "item_refs": [],
  "dropped_item_refs": []
}
```

Trace refs may include `retrieval_run_item_id` after retrieval item persistence
when the compressed item maps to the final saved item. Trace does not persist
snippets or raw tool payloads.

## Drop Reasons

Enum-like reasons:

- `max_items_limit`
- `max_total_items_limit`
- `max_tokens_limit`
- `max_total_tokens_limit`
- `exact_duplicate_removed`
- `same_chunk_deduped`
- `same_source_grouped`
- `low_score_dropped`
- `oversized_rejected`
- `unsafe_redacted`
- `repeated_result`
- `missing_text`
- `unknown`

## Debug UI

Admin Retrieval Debug displays:

- enabled flag
- tool call/search call counts
- original/output/dropped item counts
- compression ratio
- estimated tokens before/after
- max items per tool
- max total tool result tokens
- drop reason counts
- per-tool summary
- budget exhausted and oversized rejected counts
- safe item refs

Viewer chat UI does not display this internal debug panel.

## MCP `rag_ask_auto`

PR-42 exposes a local-only stdio MCP wrapper:

```text
rag_ask_auto -> rag_ask(strategy=llm_tool_orchestrator)
```

The MCP answer output remains bounded and safe. It can include answer,
citations, confidence, retrieval score summary, and a safe
`auto_strategy_summary`. It does not return raw tool result payloads or raw
trace payloads.

## Security Rules

Forbidden in DB, logs, trace, UI, and MCP output:

- raw prompt
- full context
- raw chunk text
- raw tool payload
- persisted snippets
- PII
- token, secret, password, credential, cookie, session values
- local paths

Structured logs use only counts, IDs, strategy/tool names, budget flags, token
estimates, compression ratios, and drop reason counts.

## PR-43 Handoff

PR-43 should harden the full Phase2.5 context engineering stack end-to-end:

- smoke fixtures for dense/hybrid/Auto/MCP
- demo documentation
- CI hardening
- operator-facing examples of Context Budget + Evidence Pack + Tool Result Compression
- additional redaction regression cases
