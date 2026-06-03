# Context Engineering Acceptance Checklist

Status values: `Pass`, `Fail`, `Not run`, or `N/A`. Evidence should be a safe command name, PR check, screenshot description, or retrieval run ID only. Do not paste raw prompt, full context, raw chunk text, PII, `.env` values, tokens, secrets, kubeconfig, logs, reports, or debug dumps.

| Check | Status | Evidence | Notes |
|---|---|---|---|
| Auto can answer | Not run |  | Chat UI Auto or MCP Auto returns answer/no-context safely. |
| Auto used strategy is visible in Chat UI | Not run |  | Viewer-facing summary only. |
| Auto selected/execution strategy is visible in safe logs | Not run |  | Safe structured labels and counts only. |
| Auto trace is visible in admin Retrieval Debug | Not run |  | Admin-only. |
| Context Budget is recorded | Not run |  | `context_budget_json` exists for ask run. |
| Context Budget selected/dropped counts are visible | Not run |  | Safe counts and refs. |
| Evidence Pack is built | Not run |  | `context_compression_json` exists. |
| Evidence Pack preserves citation mapping | Not run |  | Safe item refs map to retrieval item/chunk/citation. |
| Tool Result Compression is applied | Not run |  | `tool_result_compression_json` exists for Auto run. |
| MCP `rag_ask_auto` works | Not run |  | Local stdio tool call succeeds or safe no-context. |
| Local Kubernetes manifests exist | Pass | `k8s/local` directory | PR-43 local baseline. |
| Local Kubernetes smoke steps exist | Pass | `scripts/k8s_smoke.*` | Existing PR-43 smoke plus Phase2.5 docs. |
| Docker Compose path still works | Not run |  | Validate with `docker compose config`. |
| raw prompt is not exposed | Not run |  | Inspect docs/UI/MCP/debug output. |
| full context is not exposed | Not run |  | Inspect safe traces and panels. |
| raw chunk text is not exposed | Not run |  | Inspect safe traces and panels. |
| PII/secret is not exposed | Not run |  | Review changed docs/scripts and UI outputs. |
| viewer cannot see internal debug | Not run |  | Viewer access denied or route hidden. |
| admin can see safe debug summaries | Not run |  | Admin Retrieval Debug panels. |
| Phase3 handoff is clear | Pass | `docs/phase2/phase3_handoff.md` | Updated by PR-44. |
| deploy/aws handoff is clear | Pass | `docs/phase2/deploy_aws_handoff.md` | New PR-44 handoff. |
| Kubernetes is local baseline only | Pass | `docs/phase2/kubernetes_baseline.md` | Explicit non-EKS scope. |
| Destructive cleanup is not automatic | Pass | `scripts/smoke_phase2_5.*` | Wrapper does not delete volumes or namespace. |
| External model download is optional | Pass | `docs/phase2/phase2_5_readme.md` | No required download path. |
| External export is optional | Pass | `docs/phase2/phase2_5_readme.md` | No default external export. |
