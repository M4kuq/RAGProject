# Phase2.5 Demo Scenario

Target length: about 10 minutes. This demo proves that Phase2.5 is understandable, runnable, safe to inspect, and ready for Phase3 planning.

## Rules

- Do not paste `.env` values, kubeconfig, tokens, secrets, PII, raw prompt, full context, raw chunk text, raw tool payloads, logs, reports, or debug dumps into demo notes.
- Use local-only Docker Compose or local kind/minikube.
- Do not run destructive cleanup commands during the demo.
- External model downloads and external exports are optional and not required.

## Sample Questions

| Type | Sample question |
|---|---|
| keyword-heavy query | `Which Phase2 retrieval strategy uses sparse lexical matching and hybrid fusion?` |
| semantic query | `Explain how the system decides whether retrieved evidence is enough.` |
| comparison query | `Compare Context Budget and Evidence Pack in the RAG answer flow.` |
| version-specific query | `What changed in the PR-42 tool result compression guard?` |
| no_context query | `What is the deployment status of an unrelated private payroll system?` |
| Office document query | `What does the Phase2 strategy overview spreadsheet demonstrate?` |
| URL source query | `How are imported web sources represented in citations?` |

## 10 Minute Flow

| Time | Step | What to show | Expected result |
|---:|---|---|---|
| 0:00 | Start runtime | Docker Compose or local K8s is running. | Backend and frontend are reachable. |
| 0:45 | Admin login | Sign in as local admin. | Admin navigation is available. |
| 1:15 | Chat UI Auto | Select Auto / LLM Orchestrator. | Strategy selector accepts Auto. |
| 2:00 | Ask question | Send the comparison query. | Answer returns with citations/confidence. |
| 2:45 | Auto used display | Inspect user-facing retrieval summary. | Auto used strategy is visible, such as Hybrid RAG. |
| 3:30 | Retrieval Debug | Open admin Retrieval Debug and latest run. | `selected_strategy`, `execution_strategy`, and `tools_used` are safe metadata. |
| 4:20 | Context Budget | Open Context Budget panel. | selected/dropped/budget counts are visible. |
| 5:10 | Evidence Pack | Open Evidence Pack panel. | compression ratio and evidence groups are visible. |
| 6:00 | Tool Result Compression | Open Tool Result Compression panel. | tool result compression counts and ratio are visible. |
| 6:50 | MCP Auto | Run local stdio MCP `rag_ask_auto`. | Safe answer/citation/confidence/Auto summary output. |
| 7:40 | Kubernetes local deploy | Show `k8s/local` docs and smoke path. | It is clearly local kind/minikube, not EKS/AWS. |
| 8:30 | Redaction check | Inspect viewer UI and admin panels. | No raw prompt, raw chunk text, full context, PII, token, secret, or kubeconfig. |
| 9:15 | Handoff | Show Phase3 and deploy/aws handoff docs. | Boundaries are clear. |

## Docker Compose Startup

```powershell
docker compose config
docker compose up --build
scripts\smoke_phase2_5.ps1
```

```sh
docker compose config
docker compose up --build
sh scripts/smoke_phase2_5.sh
```

## Kubernetes Local Startup

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

## MCP `rag_ask_auto` Demo

Start the local stdio MCP server from `backend`:

```sh
python -m app.mcp.server --transport stdio
```

Call tool:

```json
{
  "name": "rag_ask_auto",
  "arguments": {
    "question": "Compare Context Budget and Evidence Pack in the RAG answer flow.",
    "top_k": 5,
    "rerank_top_n": 2,
    "include_citations": true,
    "include_confidence": true,
    "include_trace_summary": true
  }
}
```

Expected safe output:

- `status`
- `answer`
- `citations`
- `confidence`
- `retrieval_score_summary`
- optional `auto_strategy_summary`
- optional safe `trace_summary`

Forbidden output:

- raw prompt
- full context
- raw chunk text
- raw tool payload
- raw trace payload
- PII
- token or secret values
- kubeconfig
- local paths

## Acceptance Evidence To Capture

Capture only safe evidence:

- command names and pass/fail result
- PR check names and status
- retrieval run ID
- panel names and visible safe fields
- note that raw context and secrets were not displayed

Do not capture raw debug JSON dumps, logs, or screenshots containing sensitive content.
