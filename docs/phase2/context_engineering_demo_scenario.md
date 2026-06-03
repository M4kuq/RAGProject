# Context Engineering Demo Scenario

This is the focused Context Engineering demo used inside the broader [Phase2.5 demo scenario](phase2_5_demo_scenario.md).

## Prerequisites

- Docker Compose or local Kubernetes stack is running.
- Seed and migration completed.
- A local admin user can sign in.
- Demo documents are approved and indexed.
- No real secrets, `.env` values, kubeconfig, PII, raw prompt, full context, or raw chunk text are copied into demo notes.

## Demo Questions

Use safe questions that do not contain PII or secret-like values:

| Type | Sample question |
|---|---|
| keyword-heavy query | `Which Phase2 retrieval strategy uses sparse lexical matching and hybrid fusion?` |
| semantic query | `Explain how the system decides whether retrieved evidence is enough.` |
| comparison query | `Compare Context Budget and Evidence Pack in the RAG answer flow.` |
| version-specific query | `What changed in the PR-42 tool result compression guard?` |
| no_context query | `What is the deployment status of an unrelated private payroll system?` |
| Office document query | `What does the Phase2 strategy overview spreadsheet demonstrate?` |
| URL source query | `How are imported web sources represented in citations?` |

## Steps

1. Sign in as admin in the local app.
2. Open Chat UI.
3. Select Auto / LLM Orchestrator strategy.
4. Send the comparison query.
5. Confirm the answer returns with citations and confidence.
6. Confirm the Chat UI shows the user-facing Auto used strategy summary.
7. Open admin Retrieval Debug.
8. Select the latest Auto run.
9. Confirm `strategy_type` is `llm_tool_orchestrator`.
10. Confirm selected/execution strategy and `tools_used` are visible as safe labels.
11. Open Context Budget panel.
12. Verify candidate, selected, dropped, budget, and drop reason counts.
13. Open Evidence Pack panel.
14. Verify compression ratio, evidence group count, item refs, and citation candidate count.
15. Open Tool Result Compression panel.
16. Verify original/output/dropped item counts, per-tool summary, token estimates, and compression ratio.
17. Run MCP `rag_ask_auto` through the local stdio MCP server.
18. Verify MCP output contains safe answer, citations, confidence, and optional `auto_strategy_summary` only.
19. Confirm viewer Chat UI does not show internal debug panels.
20. Confirm admin debug does not show raw prompt, full context, raw chunk text, PII, token values, secrets, kubeconfig, or `.env` values.

## Expected Result

- Auto can answer with citations.
- The actual strategy selected/executed by Auto is visible in safe UI/log/debug metadata.
- Context Budget, Evidence Pack, and Tool Result Compression are visible as safe admin summaries.
- MCP `rag_ask_auto` follows the same safe Auto path.
- No raw context or secret-like data is displayed.

## Failure Handling

If no context is found, treat it as a valid no-context path when:

- answer status is safe
- no fabricated citation is returned
- debug traces still show safe counts and no raw payloads

If the LLM provider is unavailable, rerun with fake/local configuration or document the provider dependency as a manual limitation. Do not paste provider keys or `.env` content into notes.
