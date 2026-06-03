# Context Engineering Manual Test Cases

Use this table for Phase2.5 acceptance. Do not paste raw prompt, full context, raw chunk text, PII, `.env` values, token values, kubeconfig, secrets, logs, reports, or debug dumps into the Notes column.

| ID | Area | Scenario | Steps | Expected result | Notes |
|---|---|---|---|---|---|
| P25-TC-001 | Startup / migration / seed | Docker Compose config validates | Run `docker compose config` from repo root. | Compose config succeeds without printing `.env` values. | Safe local check. |
| P25-TC-002 | Startup / migration / seed | CI compose config validates | Run `docker compose -f docker-compose.ci.yml config --quiet`. | CI compose config succeeds. | No external API required. |
| P25-TC-003 | Startup / migration / seed | Migration reaches head | Run the existing migrate service or backend migration check. | Alembic upgrade succeeds. | Do not reset DB unless explicitly approved. |
| P25-TC-004 | Startup / migration / seed | Seed completes | Run the seed service or startup path. | Demo users, settings, and fixtures are available. | Do not print seeded credentials beyond docs-approved dummy labels. |
| P25-TC-100 | Auto / LLM Orchestrator | Auto answers a comparison query | In Chat UI, choose Auto and ask the comparison query. | Answer, citations, confidence, and retrieval summary are returned. | No raw context in UI. |
| P25-TC-101 | Auto / LLM Orchestrator | Auto handles keyword-heavy query | Ask the keyword-heavy sample question with Auto. | Auto uses a safe retrieval-only tool path and returns grounded answer or no-context. | Confirm no write/admin tools. |
| P25-TC-102 | Auto / LLM Orchestrator | Auto handles no-context query | Ask the no-context sample question. | Safe no-context answer or low-confidence response without fabricated citations. | No private data. |
| P25-TC-200 | Auto strategy display | Chat UI shows Auto used strategy | After an Auto answer, inspect the visible retrieval summary. | User-facing summary identifies the actual used strategy or safe Auto summary. | Viewer sees summary only. |
| P25-TC-201 | Auto strategy display | Admin debug shows strategy detail | Open latest run in Retrieval Debug. | `strategy_type`, selected/execution strategy, fallback, and `tools_used` are visible as safe metadata. | Admin-only. |
| P25-TC-300 | Context Budget | Budget trace exists | Open Auto run in Retrieval Debug. | Context Budget panel is present when `context_budget_json` exists. | Safe summary only. |
| P25-TC-301 | Context Budget | Selected/dropped counts visible | Inspect Context Budget panel. | Candidate, selected, dropped, source, citation candidate, and drop reason counts are visible. | No snippets. |
| P25-TC-302 | Context Budget | Budget estimate visible | Inspect usage section. | Estimated context tokens, total input tokens, remaining tokens, and exhausted flag are visible. | Heuristic estimate. |
| P25-TC-400 | Evidence Pack | Evidence Pack trace exists | Open same run in Retrieval Debug. | Evidence Pack panel is present when `context_compression_json` exists. | Safe summary only. |
| P25-TC-401 | Evidence Pack | Compression summary visible | Inspect Evidence Pack panel. | Compression ratio, evidence group count, output item count, and drop counts are visible. | No evidence text. |
| P25-TC-402 | Evidence Pack | Citation mapping preserved | Compare citations and evidence refs. | Evidence refs retain safe identifiers needed to map to retrieval items/chunks/citations. | Do not expose raw chunk text. |
| P25-TC-500 | Tool Result Compression | Tool result trace exists | Use an Auto run and open Retrieval Debug. | Tool Result Compression panel is present when `tool_result_compression_json` exists. | Auto only. |
| P25-TC-501 | Tool Result Compression | Per-tool summary visible | Inspect per-tool rows. | Tool call/search call counts, item counts, token estimates, compression ratio, and drop reasons are visible. | No raw tool payload. |
| P25-TC-502 | Tool Result Compression | Oversized/repeated guard visible | Trigger or inspect a run with repeated results if available. | Repeated or oversized counts are safe numeric fields. | Optional if fixture unavailable. |
| P25-TC-600 | Retrieval Debug | Admin can inspect safe summaries | Sign in as admin and open `/admin/retrieval-debug`. | Runs and panels load. | Admin-only route. |
| P25-TC-601 | Retrieval Debug | Viewer cannot inspect internal debug | Sign in as viewer and try admin debug route/API. | Access is denied or route is hidden. | Do not leak panel data. |
| P25-TC-602 | Retrieval Debug | Search debug still supports dense/sparse/hybrid/router | Run debug search for supported strategies. | Safe trace is recorded for each supported strategy. | Auto ask runs appear via history. |
| P25-TC-700 | MCP `rag_ask_auto` | Tool is listed | Start local stdio MCP server and list tools. | `rag_ask_auto` appears. | Local-only stdio. |
| P25-TC-701 | MCP `rag_ask_auto` | Safe Auto answer | Call `rag_ask_auto` with a safe demo question. | Output contains answer/citations/confidence and optional safe Auto summary. | No raw trace/tool payload. |
| P25-TC-702 | MCP `rag_ask_auto` | Trace summary optional safety | Call with trace summary enabled if supported. | Trace summary is safe metadata only. | No raw context. |
| P25-TC-800 | Kubernetes local deploy | Manifest validator passes | Run `python scripts/validate_k8s_manifests.py`. | Required local K8s components are present. | Local baseline only. |
| P25-TC-801 | Kubernetes local deploy | Kustomize renders | Run `kubectl kustomize k8s/local`. | Manifests render without kubeconfig output. | Does not apply. |
| P25-TC-802 | Kubernetes local deploy | Client dry-run applies | Run `kubectl apply --dry-run=client -k k8s/local` when kubectl is available. | Client validation succeeds. | Does not create resources. |
| P25-TC-803 | Kubernetes local deploy | Local cluster smoke | Build/load local images, apply `k8s/local`, run `scripts/k8s_smoke.*`. | Postgres, Qdrant, migrate, seed, backend, worker, frontend become ready. | Optional if kind/minikube unavailable. |
| P25-TC-900 | Security / Redaction | Raw prompt not exposed | Inspect docs, UI, debug panels, MCP output. | No raw prompt is displayed or persisted in safe summaries. | Field names may mention policy only. |
| P25-TC-901 | Security / Redaction | Full context and raw chunk text not exposed | Inspect Context Budget, Evidence Pack, Tool Result Compression panels. | Only safe refs, counts, hashes, labels, estimates, ratios. | No snippets in persisted traces. |
| P25-TC-902 | Security / Redaction | Secrets and kubeconfig absent | Inspect changed docs/scripts/manifests. | No API keys, tokens, cookies, passwords, kubeconfig, or `.env` values. | Placeholders only. |
| P25-TC-903 | Security / Redaction | Destructive commands warned | Inspect docs and scripts. | Cleanup commands are not run by default and destructive paths are explicitly warned. | No `down -v` automation. |
| P25-TC-1000 | Regression | Backend targeted tests | Run relevant backend tests for RAG, MCP, context budget, evidence pack, tool result compression. | Tests pass. | Use Docker CI if host env missing. |
| P25-TC-1001 | Regression | Frontend checks | Run lint/typecheck/test/build if frontend changed. | Checks pass. | PR-44 should not redesign UI. |
| P25-TC-1002 | Regression | Existing Phase2 smoke still works | Run `scripts/smoke_phase2.*`. | Existing Phase2 smoke remains valid. | No destructive cleanup. |
| P25-TC-1100 | Final Acceptance | Phase2.5 docs complete | Review Phase2.5 README, demo, manual cases, checklist, limitations, handoffs. | Third party can demo and accept Phase2.5. | PR-44 core deliverable. |
| P25-TC-1101 | Final Acceptance | PR ready for review | Run git hygiene, validations, and create PR. | PR includes only intended docs/scripts changes. | No generated artifacts. |
