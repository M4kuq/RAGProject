# Phase2 / Phase3 RAG拡張実装計画書 改訂版

This revised note reflects the post-PR-44 repository state used by PR-45. `docs/Phase2_Phase3_RAG拡張実装計画書.md` remains as the older roadmap; this file records the Phase2.5 / Phase3 extension order currently implemented on `main`.

## Completed Baseline

- PR-20 to PR-21: retrieval strategy schema and safe retrieval trace foundation.
- PR-22 to PR-25: evaluation datasets, metrics, sparse retrieval, hybrid retrieval, and strategy evaluation runner.
- PR-26 to PR-30: Retrieval Debug UI v2, query analyzer/planner, `agentic_router`, bounded agentic retrieval loop, and agentic evaluation.
- PR-31 to PR-37: CI smoke, optional no-op trace export, local experiment harness, advanced Office/HTML/XML/URL ingestion, citation navigation, and Phase2 acceptance docs.
- PR-38: MCP hybrid / agentic RAG tools.
- PR-39: LLM Tool-Calling Retrieval Orchestrator for explicit ask mode.
- PR-40: Context Budget / Context Trace / Context Debug Foundation.
- PR-41: Retrieved Context Compression / Evidence Pack.
- PR-42: Tool Result Compression / Orchestrator Context Guard.
- PR-43: Kubernetes Baseline / Local K8s Deploy / Compose-to-K8s Hardening.
- PR-44: Phase2.5 Final Hardening / Context Engineering + Kubernetes Demo Docs.

## Current PR

PR-45 implements Phase3 Design Baseline / Graph-RAG Planning as documentation only:

- Phase3 roadmap and PR order
- Graph-RAG architecture
- graph schema draft and migration plan
- entity / relation extraction design
- graph indexing design
- graph retrieval strategy design
- Graph-aware Router design
- Graph + Vector Hybrid design
- Graph Citation and Graph Path Validation design
- Graph Debug UI design
- Graph Evaluation design
- OCR / Multimodal / Production Expansion boundaries
- API design delta
- acceptance criteria, risk register, test strategy, and security/redaction policy

PR-45 does not implement Graph-RAG runtime code, DB migration, graph tables, entity/relation extraction code, graph retrieval code, OCR, image upload, AWS/S3/OIDC, External LLM Provider, or online evaluation implementation.

## Completed PR-44

PR-44 added Phase2.5 final hardening and demo docs:

- Phase2.5 README
- Context Engineering README and demo scenario
- Context Engineering manual test cases and acceptance checklist
- Context Engineering known limitations
- local Kubernetes baseline Phase2.5 entrypoint
- Phase2.5 demo scenario
- Phase3 handoff and deploy/aws handoff
- safe Phase2.5 smoke wrappers

## Completed PR-43

PR-43 implemented Kubernetes Baseline / Local K8s Deploy / Compose-to-K8s Hardening:

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

PR-43 did not implement EKS, AWS, Terraform, S3, Bedrock, RDS, OIDC, production Ingress, WAF/NAT/private subnet design, production Secrets management, Graph-RAG, OCR, remote MCP, or external operation agents.

## Completed PR-42

PR-42 added Tool Result Compression / Orchestrator Context Guard:

- `ToolResultCompressor`
- `ToolResultBudgetManager`
- `OrchestratorContextGuard`
- bounded safe tool result schema for `dense_search`, `sparse_search`, `hybrid_search`, and `inspect_retrieval_trace`
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

## Phase3 Deferred Implementation

- PR-46 and later: Graph-RAG runtime implementation.
- PR-51 and later: OCR and multimodal ingestion/citation.
- PR-54 and later: external provider and storage adapters.
- PR-57 or deploy/aws branch: AWS production deployment foundation.
- PR-58 and later: online evaluation, A/B evaluation, and alerting.

## Safety Invariant

RAG trace, debug, logs, DB JSON, UI, MCP output, docs, and artifacts must not store or display raw prompt, raw document text, full context, raw chunk text, snippets inside context budget trace, raw tool outputs, raw tool result payloads, snippets in persisted compression trace, PII, credential values, session values, secrets, or local paths. Numeric estimates, counts, hashes, IDs, strategy labels, bounded source labels, and safe reason codes are allowed.
