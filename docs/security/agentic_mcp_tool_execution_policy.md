# Agentic / MCP tool execution policy (RAG-67)

## Purpose and scope

This change closes the remaining RAG-31 tool-execution boundary without changing the RAG
accuracy profiles, public model selection, databases, Qdrant collections, or Docker volumes.
It applies to these server-side execution surfaces:

- MCP `tools/list` and `tools/call`
- `llm_tool_orchestrator`
- `langchain_agentic`
- `langgraph_agentic`

External write tools and their human-in-the-loop approval lifecycle are out of scope. MCP
remains local-only and authenticated by its existing transport policy. This work does not make
MCP internet-facing.

## Baseline retained

The baseline already had useful controls which remain authoritative:

- MCP input models use Pydantic `strict=True` and `extra="forbid"`.
- MCP write tools and evaluation-run creation are disabled.
- MCP HTTP requires its configured bearer credential and accepts only loopback origins.
- Agentic search loops have tool-call, search-call, timeout, query-size, result-size, and context
  budgets.
- Agentic tool registries contain retrieval and finalization functions, not arbitrary Python or
  shell execution.

The gap was that the capability/effect policy and execution audit were implicit. Internal LLM
tool output could also lose unknown arguments during parsing or fall back to the original query
when a search call had malformed arguments. That did not expose a write tool, but made the
execution boundary harder to prove and audit.

## Threat model

### Protected assets

- owner-scoped document and retrieval data
- evaluation and retrieval summaries
- model, retrieval, and Agentic compute budgets
- local service availability and audit integrity

### Attacker-controlled inputs

- an authenticated user's direct question
- instructions embedded in a retrieved or graph-derived chunk
- a compromised or malformed MCP client message
- malformed JSON emitted by an LLM planner

### Trust boundaries

1. User or retrieved text crossing into an LLM planner is untrusted data.
2. Planner output crossing into a callable tool is untrusted control data.
3. MCP JSON-RPC input crossing into a registered handler is untrusted control data.
4. A configured tool list is server-owned policy and cannot be expanded by prompt content.

### Residual risks

- A permitted read tool can still be abused for expensive but authorized work; RAG-63 tracks
  rate, concurrency, and daily-budget admission controls.
- Semantic prompt injection can influence which permitted retrieval strategy is requested. It
  cannot add a capability, but answer-level security remains covered by RAG-31/RAG-62 gates.
- Shared MCP bearer authentication does not provide per-user internet authorization. MCP remains
  local-only; making it remote requires a separate authenticated principal and owner-scope design.
- Write-tool HITL is not implemented or claimed as validated.

## Enforcement

`AGENT_TOOL_POLICY_MODE` defaults to `enforce`.

| Input or decision | Enforced result | Handler/tool invoked |
|---|---|---:|
| unknown or Unicode-confusable tool name | `tool_not_registered` | no |
| registered tool absent from server allowlist | `tool_not_allowed` or hidden MCP tool | no |
| write-effect tool while writes are disabled | `write_tool_denied` | no |
| non-object arguments | `tool_arguments_not_object` | no |
| unknown, missing, wrong-type, blank, or over-broad argument | `tool_arguments_invalid` | no |
| valid read tool and bounded typed arguments | `tool_allowed` | yes |

Search tools accept exactly one non-blank string `query`. Trace inspection accepts only an
optional positive `retrieval_run_id`. Finalization accepts only bounded call IDs for the current
orchestrator and the fixed `final_answer` intent. MCP continues to validate every tool with its
existing strict Pydantic model.

`MCP_ALLOWED_TOOLS` is a server-owned allowlist. It may be empty to disable all MCP tools. Unknown
names and duplicates fail configuration validation. The allowlist and write-tool denial remain
enforced even when the Agentic argument policy is temporarily set to `legacy`.

## Raw-free execution audit

Logger `app.security.tool_execution` emits schema `security.tool_execution.v1` with only:

- execution surface
- stable registered tool name, or `unknown`
- read/write effect
- allowed/denied/completed/failed decision
- controlled reason code
- policy mode
- bounded argument count and call index
- bounded duration in milliseconds when available

It never receives or emits the question, query, arguments, retrieved chunks, tool result, answer,
user identifier, request credential, PII, or the raw name of an unknown tool. Audit emission can
be disabled with `AGENT_TOOL_EXECUTION_AUDIT_ENABLED=false` without changing enforcement.

## Recovery

1. Immediate Agentic behavior rollback: set `AGENT_TOOL_POLICY_MODE=legacy`. This restores the
   previous internal argument/fallback behavior only. It does not enable writes or bypass the MCP
   allowlist.
2. Audit-only rollback: set `AGENT_TOOL_EXECUTION_AUDIT_ENABLED=false`.
3. Code rollback: return to parent commit
   `96e5fc82ad2c253d000633ff9e9431e88998f74a`.

No schema or stored-data rollback is required. Do not reset the database, Qdrant, Neo4j, or Docker
volumes for this feature.

## Evidence log

Date: 2026-08-04 (Asia/Tokyo)  
Issue: RAG-67  
Base: GitHub `main` at `96e5fc82ad2c253d000633ff9e9431e88998f74a`

| Check | Result |
|---|---|
| Focused MCP/Agentic regression subset | 78 passed, 2 known warnings |
| New authorization and audit fixtures | 17 passed, 1 known warning |
| Backend full suite | 989 passed, 19 skipped, 3 known warnings |
| Ruff on the full backend | passed |
| Ruff format check | 274 files formatted |
| mypy on the full backend | 251 files, no issues |
| Frontend Vitest | 103 passed |
| Frontend TypeScript/Vite production build | passed |
| `git diff --check` | passed |

The 17-case fixture includes unknown tools, a Cyrillic confusable tool name, extra fields, wrong
types, blank query, write-effect denial, restricted MCP listing/calling, all three Agentic
orchestrators, and an indirect escalation attempted after a poisoned retrieval result. The
retrieval callback was invoked zero times for every unauthorized or invalid direct call. In the
indirect case, the one authorized dense search ran and the attempted write tool did not. This is a
regression-fixture result, not a claim that every future tool or prompt attack is blocked.

The first full-suite run exposed one change-related reason-code regression and one unrelated
SQLite worker timestamp flake. The reason code was restored to the existing
`strategy_not_enabled` contract. The worker test then passed twice in isolation, and the final
full-suite rerun passed completely. Both the initial failure and the successful reruns are kept in
the task execution log rather than hidden.
