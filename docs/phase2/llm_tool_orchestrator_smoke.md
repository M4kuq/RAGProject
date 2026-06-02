# PR-39 / PR-42 LLM Tool Orchestrator Smoke

This smoke note covers the PR-39 `llm_tool_orchestrator` ask path after PR-40
context budget tracing, PR-41 Evidence Pack construction, and PR-42 tool result
compression.

## Scope

- `/api/v1/rag/ask strategy=llm_tool_orchestrator`
- bounded retrieval-only tool calls
- final selected evidence passed through `ContextBudgetManager`
- final selected retrieved context passed through `EvidencePackBuilder`
- retrieval tool results passed through `ToolResultCompressor`
- safe `retrieval_runs.context_budget_json`
- safe `retrieval_runs.context_compression_json`
- safe `retrieval_runs.tool_result_compression_json`
- admin Retrieval Debug context budget display
- admin Retrieval Debug Evidence Pack display
- admin Retrieval Debug Tool Result Compression display

Not in scope:

- Graph-RAG
- OCR or multimodal retrieval
- external operation agents
- remote MCP

## Expected Checks

1. Send an ask request with `strategy=llm_tool_orchestrator`.
2. Confirm `retrieval_runs.strategy_type = llm_tool_orchestrator`.
3. Confirm `strategy_decision_json` keeps bounded tool counts and safe tool names.
4. Confirm `context_budget_json.schema_version = phase2.context_budget.v1`.
5. Confirm selected / dropped item counts and drop reasons are present.
6. Confirm `context_budget_json.strategy.selected_strategy = llm_tool_orchestrator`.
7. Confirm `context_compression_json.schema_version = phase2.context_compression.v1`.
8. Confirm compression ratio, evidence item count, group count, and duplicate drops are present.
9. Confirm `tool_result_compression_json.schema_version = phase2.tool_result_compression.v1`.
10. Confirm tool result output item counts, drop reasons, and budget flags are present.
11. Confirm citations still use selected Evidence Pack candidates.
12. Confirm admin Retrieval Debug renders Context Budget, Evidence Pack, and Tool Result Compression panels.
13. Confirm viewer chat does not render context budget, Evidence Pack, or tool result compression internals.

## Redaction

The smoke must not print or persist raw prompt, full context, raw chunk text,
`evidence_text_for_generation`, snippets in trace, raw tool outputs, PII, token
values, secrets, local paths, cookies, or session values. Numeric token
estimates, bounded safe IDs, and text hashes are allowed.
