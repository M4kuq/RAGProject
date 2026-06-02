# Phase2 / Phase3 RAG拡張実装計画書 改訂版

This revised note reflects the post-PR-42 repository state used by PR-43.
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
- PR-40: Context Budget / Context Trace / Context Debug Foundation.
- PR-41: Retrieved Context Compression / Evidence Pack.
- PR-42: Tool Result Compression / Orchestrator Context Guard.

## Current PR

PR-43 implements Kubernetes Baseline / Local K8s Deploy /
Compose-to-K8s Hardening:

- local Kubernetes manifests under `k8s/local`
- frontend / backend / worker Deployments
- postgres / qdrant StatefulSets
- migration and seed Jobs
- ConfigMap and Secret template with local placeholders only
- PVCs for Postgres, Qdrant, and upload storage
- ClusterIP Services and port-forward-first access
- readiness/liveness probes and resource requests/limits
- kind/minikube local image loading scripts
- local K8s smoke scripts and manifest validation
- docs for deploy, smoke, cleanup, and secret handling

PR-43 does not implement EKS, AWS, Terraform, S3, Bedrock, RDS, OIDC,
production Ingress, WAF/NAT/private subnet design, production Secrets
management, Graph-RAG, OCR, remote MCP, or external operation agents.

## Completed PR-42

PR-42 added Tool Result Compression / Orchestrator Context Guard:

- `ToolResultCompressor`
- `ToolResultBudgetManager`
- `OrchestratorContextGuard`
- bounded safe tool result schema for `dense_search`, `sparse_search`,
  `hybrid_search`, and `inspect_retrieval_trace`
- per-tool and per-turn item/token budgets
- duplicate, same-chunk, repeated-result, and oversized-output handling
- safe `retrieval_runs.tool_result_compression_json`
- Auto / `llm_tool_orchestrator` integration before planner-visible tool results
- local MCP `rag_ask_auto` wrapper
- admin Retrieval Debug Tool Result Compression panel
- safe structured Tool Result Compression logs

## Completed PR-41

PR-41 added Retrieved Context Compression / Evidence Pack:

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

- PR-44: Phase2.5 Final Hardening / Context Engineering Demo Docs.
- Later Phase3: Graph-RAG, OCR, multimodal retrieval, AWS/S3/OIDC, remote MCP,
  and external operation agents.

## Safety Invariant

RAG trace, debug, logs, DB JSON, UI, and artifacts must not store or display raw
prompt, full context, raw chunk text, snippets inside context budget trace, raw
tool outputs, raw tool result payloads, snippets in persisted compression trace,
PII, credential values, token values, cookies, sessions, secrets, or local
paths. Numeric token and char estimates are allowed.
