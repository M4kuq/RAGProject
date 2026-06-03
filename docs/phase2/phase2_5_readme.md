# Phase2.5 README

Phase2.5 is the final hardening layer between the completed Phase2 RAG stack and Phase3 planning. It does not add a new retrieval algorithm. It makes the PR-39 to PR-43 work understandable, demonstrable, and reviewable by a third party.

## Purpose

Phase2.5 turns the implemented Context Engineering and local Kubernetes baseline into an operator-facing handoff:

- show how Auto chooses and executes a RAG strategy
- show how Context Budget, Evidence Pack, and Tool Result Compression are inspected safely
- show how local MCP `rag_ask_auto` follows the same safe Auto path
- show how Docker Compose and local Kubernetes relate
- provide manual test cases, acceptance checks, smoke wrappers, known limitations, and Phase3/deploy handoff notes

## Difference From Phase2

Phase2 completed advanced retrieval, agentic routing, evaluation, observability, advanced import, citation navigation, MCP tools, and final Phase2 demo docs. Phase2.5 focuses on the post-Phase2 extension set:

| Area | Implemented before PR-44 | PR-44 responsibility |
|---|---|---|
| Auto retrieval | PR-39 LLM Tool-Calling Retrieval Orchestrator | Demo and acceptance path |
| Context Budget | PR-40 safe budget trace and admin debug panel | Operator docs and manual checks |
| Evidence Pack | PR-41 deterministic retrieved-context compression | Demo and citation mapping checks |
| Tool Result Compression | PR-42 planner-visible tool result guard | Demo and safety checks |
| MCP Auto | PR-42 local stdio `rag_ask_auto` | Local demo path |
| Local Kubernetes | PR-43 `k8s/local` kind/minikube baseline | Phase2.5 demo integration |

## Why Context Engineering Before Phase3

Phase3 will add Graph-RAG, OCR, multimodal evidence, external providers, and production deployment planning. Those features increase the amount and variety of evidence that can enter prompts, traces, debug views, and external integrations. Phase2.5 establishes the invariants first:

- budget decisions are recorded as safe counts and refs
- final evidence is packed with citation mapping preserved
- intermediate orchestrator tool outputs are compressed before planner visibility
- admin debug surfaces safe summaries only
- viewer UI does not expose internal debug traces
- local Kubernetes can run the existing stack without becoming production AWS/EKS

## Auto / LLM Tool-Calling Retrieval Orchestrator

Auto is exposed as `/rag/ask` with `strategy=llm_tool_orchestrator` and in Chat UI as the Auto/LLM orchestrator strategy. It is retrieval-only tool calling. The orchestrator can use bounded dense, sparse, hybrid, trace-inspection, and finalize-style retrieval tools, then returns an answer through the normal RAG answer path.

It intentionally does not expose upload, archive, approve, retry, admin mutation, external operation, remote MCP, or production deployment tools.

## Context Budget

Context Budget runs before answer generation and records a safe `phase2.context_budget.v1` trace in `retrieval_runs.context_budget_json`. It records candidate, selected, and dropped counts, estimated token usage, drop reasons, source summaries, and selected/dropped safe refs.

The token estimate is heuristic: `ceil(char_count / 4)`. It is not tokenizer-accurate and does not require model downloads or network calls.

## Evidence Pack

Evidence Pack runs after Context Budget and before answer generation. It builds deterministic compressed evidence while preserving the citation path:

```text
EvidenceItem -> retrieval_run_item -> document_chunk -> citation
```

The safe trace is stored in `retrieval_runs.context_compression_json`. It records compression ratios, evidence group counts, item counts, duplicate/drop reasons, hashes, and safe refs. It does not store generated evidence text, full context, or raw chunk text.

## Tool Result Compression

Tool Result Compression runs earlier than Evidence Pack. It bounds intermediate retrieval tool results before the LLM orchestrator planner sees them. The safe trace is stored in `retrieval_runs.tool_result_compression_json` and includes per-tool counts, estimated tokens, compression ratio, budget flags, drop reasons, and safe refs.

This is separate from final Evidence Pack compression.

## Retrieval Debug

Admin Retrieval Debug is the main verification surface for Phase2.5. Use it to confirm:

- requested `strategy_type`
- `selected_strategy` and `execution_strategy`
- `tools_used`
- `context_budget_json` selected/dropped/budget fields
- `context_compression_json` Evidence Pack fields
- `tool_result_compression_json` Tool Result Compression fields
- safe score, latency, settings, and decision metadata

Viewer Chat UI should show user-facing answer, citations, confidence, and Auto used strategy summary, but not internal Context Budget, Evidence Pack, or Tool Result Compression panels.

## MCP `rag_ask_auto`

The local stdio MCP server exposes:

```text
rag_ask_auto -> rag_ask(strategy=llm_tool_orchestrator)
```

The MCP output may include answer, citations, confidence, retrieval score summary, and safe `auto_strategy_summary`. It must not return raw tool payloads, raw trace payloads, full context, raw chunk text, token values, or secrets.

## Kubernetes Local Baseline

The Kubernetes baseline is under `k8s/local` and is documented in [kubernetes_local_baseline.md](kubernetes_local_baseline.md) and [kubernetes_baseline.md](kubernetes_baseline.md). It is local-only for kind/minikube.

It is not EKS, AWS production, Terraform, Helm production packaging, Ingress/TLS, autoscaling, S3, Bedrock, RDS, OIDC, WAF, NAT, private subnet, or production secret management.

## Docker Compose And Kubernetes

Docker Compose remains the default local development and CI-oriented path. Local Kubernetes is a second runtime baseline that mirrors the Compose-shaped stack for kind/minikube demos:

| Runtime | Use |
|---|---|
| Docker Compose | fastest local dev, existing smoke, CI compose validation |
| `k8s/local` | local cluster demo, manifest validation, port-forward based app access |

Both paths use local/fake-capable configuration. Neither path requires external API keys by default.

## Security / Redaction Policy

Do not put the following in docs, logs, artifacts, UI, MCP output, PR comments, or committed manifests:

- `.env` values
- kubeconfig
- API keys, tokens, passwords, cookies, sessions, credentials, private keys, or real secrets
- raw prompt
- full context
- raw chunk text
- raw tool payloads
- PII
- local DB, Qdrant, upload data, generated logs, reports, caches, or debug dumps

Admin debug may show safe metadata. Viewer UI must not show internal debug summaries. Local K8s NodePort or port-forward usage is local-only.

## Local Setup

Docker Compose path:

```powershell
Copy-Item .env.example .env
docker compose config
docker compose up --build
scripts\smoke_phase2_5.ps1
```

```sh
cp .env.example .env
docker compose config
docker compose up --build
sh scripts/smoke_phase2_5.sh
```

Local Kubernetes path:

```powershell
python scripts\validate_k8s_manifests.py
kubectl kustomize k8s/local
scripts\k8s_load_images.ps1 -Runtime kind
kubectl apply -k k8s/local
scripts\k8s_smoke.ps1
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

```sh
python scripts/validate_k8s_manifests.py
kubectl kustomize k8s/local
K8S_RUNTIME=kind sh scripts/k8s_load_images.sh
kubectl apply -k k8s/local
sh scripts/k8s_smoke.sh
kubectl -n ragproject-local port-forward svc/frontend 5173:5173
```

Destructive cleanup warning: do not run `docker compose down -v`, `kubectl delete namespace`, or `kubectl delete -k k8s/local` unless you explicitly accept local data deletion.

## Smoke Commands

Safe wrapper checks:

```powershell
scripts\smoke_phase2_5.ps1
scripts\smoke_phase2_5.ps1 -Deep
scripts\smoke_phase2_5.ps1 -K8sDryRun
```

```sh
sh scripts/smoke_phase2_5.sh
sh scripts/smoke_phase2_5.sh --deep
sh scripts/smoke_phase2_5.sh --k8s-dry-run
```

`Deep` requires running local services and local demo admin credentials provided through shell environment variables. It does not print those values.

## Demo Scenario

Use [phase2_5_demo_scenario.md](phase2_5_demo_scenario.md) for the full 10 minute path. It covers Docker Compose or local K8s startup, admin login, Chat UI Auto, Retrieval Debug, Context Budget, Evidence Pack, Tool Result Compression, MCP `rag_ask_auto`, local Kubernetes overview, redaction checks, and Phase3 handoff.

## Known Limitations

See [context_engineering_known_limitations.md](context_engineering_known_limitations.md). Key points:

- Auto is retrieval-only tool calling
- Context Budget token estimates are heuristic
- Evidence Pack is deterministic compression first
- LLM summarization is not required
- Tool Result Compression is for tool outputs, not final evidence compression
- Headroom, RTK, and LeanCTX are not integrated
- Kubernetes is local baseline only
- AWS and production hardening are separate deploy/aws work
- Graph-RAG, OCR, and multimodal are Phase3 or later

## Phase3 Handoff

See [phase3_handoff.md](phase3_handoff.md). Phase3 should keep Context Budget, Evidence Pack, Tool Result Compression, trace redaction, and viewer/admin debug boundaries when adding Graph-RAG, OCR, image upload, multimodal citations, external providers, and online evaluation.

## deploy/aws Handoff

See [deploy_aws_handoff.md](deploy_aws_handoff.md). AWS work should happen in a dedicated deploy/aws integration branch with explicit decisions for S3, Bedrock, RDS/Qdrant, ECS or EKS, OIDC/RBAC, Secrets Manager, networking, and WAF. PR-44 does not provision AWS.
