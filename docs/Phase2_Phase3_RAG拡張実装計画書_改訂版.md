# Phase2 / Phase3 RAG拡張実装計画書 改訂版

This revised note reflects the post-PR-40 repository state used by PR-41.
`docs/Phase2_Phase3_RAG拡張実装計画書.md` remains as the older roadmap; this file
records the Phase2.5 / pre-Phase3 extension order currently implemented on
`main`.

## Completed Baseline

- PR-20 to PR-21: retrieval strategy schema and safe retrieval trace foundation.
- PR-22 to PR-25: evaluation datasets, metrics, sparse retrieval, hybrid
  retrieval, and strategy evaluation runner.
- PR-26 to PR-30: Retrieval Debug UI v2, query analyzer/planner,
  `agentic_router`, bounded agentic retrieval loop, and agentic evaluation.
- PR-31 to PR-37: CI smoke, optional no-op trace export, local experiment
  harness, advanced Office/HTML/XML/URL ingestion, citation navigation, and
  Phase2 acceptance docs.
- PR-38: MCP hybrid / agentic RAG tools.
- PR-39: LLM Tool-Calling Retrieval Orchestrator for explicit ask mode.

## Current PR

PR-41 implements Retrieved Context Compression / Evidence Pack:

- `EvidencePackBuilder`
- `ContextCompressor`
- `EvidenceItem`, `EvidenceGroup`, and `EvidencePackTrace`
- deterministic exact / normalized / near-duplicate reduction
- bounded evidence text for generation
- safe `retrieval_runs.context_compression_json`
- citation source mapping from evidence item to retrieval run item and chunk
- `/rag/ask` integration after PR-40 context budget and before generation
- dense / hybrid / `agentic_router` / `llm_tool_orchestrator` ask coverage
- admin Retrieval Debug Evidence Pack panel
- safe structured Evidence Pack logs

PR-41 compresses retrieved context. It does not compress LLM tool results.

## Completed PR-40

PR-40 added Context Budget / Context Trace / Context Debug Foundation:

- `ContextBudgetPolicy`
- `ContextBudgetManager`
- `ContextItem`, `ContextBudgetDecision`, and `ContextBudgetTrace`
- deterministic token estimate using `ceil(char_count / 4)`
- safe `retrieval_runs.context_budget_json`
- `/rag/ask` integration before generation
- dense / hybrid / `agentic_router` / `llm_tool_orchestrator` ask coverage
- admin Retrieval Debug Context Budget panel
- safe structured context budget logs

## Explicitly Deferred

- PR-42: Tool Result Compression.
- Later Phase3: Graph-RAG, OCR, multimodal retrieval, AWS/S3/OIDC, remote MCP,
  and external operation agents.

## Safety Invariant

RAG trace, debug, logs, DB JSON, UI, and artifacts must not store or display raw
prompt, full context, raw chunk text, snippets inside context budget trace, raw
tool outputs, PII, credential values, token values, cookies, sessions, secrets,
or local paths. Numeric token and char estimates are allowed.
