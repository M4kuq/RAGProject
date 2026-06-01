# PR-39 / PR-40 LLM Tool Orchestrator Smoke

This smoke note covers the PR-39 `llm_tool_orchestrator` ask path after PR-40
context budget tracing.

## Scope

- `/api/v1/rag/ask strategy=llm_tool_orchestrator`
- bounded retrieval-only tool calls
- final selected evidence passed through `ContextBudgetManager`
- safe `retrieval_runs.context_budget_json`
- admin Retrieval Debug context budget display

Not in scope:

- Retrieved Context Compression / Evidence Pack
- Tool Result Compression
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
7. Confirm citations still use selected context candidates.
8. Confirm admin Retrieval Debug renders the Context Budget panel.
9. Confirm viewer chat does not render context budget internals.

## Redaction

The smoke must not print or persist raw prompt, full context, raw chunk text,
snippets, raw tool outputs, PII, token values, secrets, local paths, cookies, or
session values. Numeric token estimates and bounded safe IDs are allowed.
