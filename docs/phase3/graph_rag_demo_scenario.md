# GraphRAG Demo Scenario

This scenario is the PR-54 GraphRAG handoff demo. It is designed for a local
Docker Compose walkthrough. Use only seeded or presenter-owned synthetic demo
data. Do not paste private prompts, raw documents, raw chunks, raw graph
evidence, secrets, credentials, cookies, tokens, API keys, or `.env` values into
demo notes.

## Preconditions

- Docker Desktop or Docker Engine with Docker Compose is available.
- The repository is checked out locally.
- `.env.example` has been copied to `.env` for local development, but the demo
  must not show `.env` contents.
- The default PostgreSQL/Qdrant/Neo4j/backend/worker/frontend stack can start.
- The presenter has local admin credentials available in the shell or browser
  session. Do not display those values.
- Graph retrieval and Neo4j projection are enabled by the default Compose
  configuration.

## Safe Startup

Default Neo4j-backed GraphRAG:

```powershell
$env:RETRIEVAL_CACHE_ENABLED = "true"
docker compose config --quiet
docker compose up --build
```

```sh
export RETRIEVAL_CACHE_ENABLED=true
docker compose config --quiet
docker compose up --build
```

In another shell:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/ready
scripts\smoke_phase3_graph_rag.ps1
```

```sh
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
sh scripts/smoke_phase3_graph_rag.sh
```

## Prepare Graph Index

Queue graph index jobs for active ready local demo document versions:

```powershell
docker compose exec -T backend python -m app.scripts.queue_graph_index_builds
docker compose logs --tail 100 worker
```

```sh
docker compose exec -T backend python -m app.scripts.queue_graph_index_builds
docker compose logs --tail 100 worker
```

Expected result:

- the queue command prints safe JSON with document version IDs, job IDs, and counts
- worker logs show graph index jobs finishing or already being queued
- no raw chunk text, document text, prompts, graph evidence, credentials, or
  `.env` values appear in output

## Explicit Graph API Example

Use the local seeded demo admin account, but do not paste its password into
docs, PR comments, terminal transcripts, or screenshots. The examples below
read credentials from the shell and only send a safe synthetic query.

PowerShell:

```powershell
if (-not $env:DEMO_ADMIN_EMAIL) {
  $env:DEMO_ADMIN_EMAIL = Read-Host "Local admin email"
}
if (-not $env:DEMO_ADMIN_PASSWORD) {
  $securePassword = Read-Host "Local admin password" -AsSecureString
  $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
  try {
    $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPtr)
    $env:DEMO_ADMIN_PASSWORD = $plainPassword
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPtr)
  }
}
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$csrf = Invoke-RestMethod -WebSession $session `
  -Uri "http://localhost:8000/api/v1/auth/csrf"
$loginBody = @{
  email = $env:DEMO_ADMIN_EMAIL
  password = $env:DEMO_ADMIN_PASSWORD
} | ConvertTo-Json -Compress
$login = Invoke-RestMethod -WebSession $session `
  -Method Post `
  -Uri "http://localhost:8000/api/v1/auth/login" `
  -ContentType "application/json" `
  -Headers @{ "X-CSRF-Token" = $csrf.data.csrf_token } `
  -Body $loginBody
$searchBody = @{
  query = "How does FastAPI relate to PostgreSQL?"
  strategy = "graph"
  top_k = 5
  rerank_top_n = 2
} | ConvertTo-Json -Compress
Invoke-RestMethod -WebSession $session `
  -Method Post `
  -Uri "http://localhost:8000/api/v1/rag/search" `
  -ContentType "application/json" `
  -Headers @{ "X-CSRF-Token" = $login.data.csrf_token } `
  -Body $searchBody
```

POSIX shell:

```sh
: "${DEMO_ADMIN_EMAIL:?set DEMO_ADMIN_EMAIL in the shell}"
: "${DEMO_ADMIN_PASSWORD:?set DEMO_ADMIN_PASSWORD without printing it}"
cookie_file=$(mktemp)
csrf_json=$(curl -fsS -c "$cookie_file" http://localhost:8000/api/v1/auth/csrf)
csrf_token=$(printf '%s' "$csrf_json" |
  python -c 'import json,sys; print(json.load(sys.stdin)["data"]["csrf_token"])')
login_json=$(python -c 'import json, os; print(json.dumps({"email": os.environ["DEMO_ADMIN_EMAIL"], "password": os.environ["DEMO_ADMIN_PASSWORD"]}))' |
  curl -fsS -b "$cookie_file" -c "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  --data-binary @- \
  http://localhost:8000/api/v1/auth/login)
csrf_token=$(printf '%s' "$login_json" |
  python -c 'import json,sys; print(json.load(sys.stdin)["data"]["csrf_token"])')
curl -fsS -b "$cookie_file" \
  -H "Content-Type: application/json" \
  -H "X-CSRF-Token: $csrf_token" \
  -d '{"query":"How does FastAPI relate to PostgreSQL?","strategy":"graph_neo4j","top_k":5,"rerank_top_n":2}' \
  http://localhost:8000/api/v1/rag/search
rm -f "$cookie_file"
```

Expected result:

- HTTP 200 with `status=succeeded` and a `retrieval_run_id`, or a safe empty
  result when no chunk-backed graph evidence exists
- `retrieval_score_summary` and items use IDs, scores, snippets, and safe
  metadata already allowed by the RAG search response
- no raw graph evidence, full context, credential values, or `.env` values are
  printed

## Demo Flow

1. Start with the root README and open `docs/phase3/graph_rag_final_readme.md`.
   Explain that PostgreSQL is the source of truth and Neo4j is the default
   read model.

2. Open `http://localhost:5173` and sign in with the local demo admin account
   without showing credentials.

3. Open Admin Documents and confirm seeded demo documents are ready. Mention
   that graph indexing runs from ready active document versions.

4. Open Admin Retrieval Debug. Run `dense` and `hybrid` with safe synthetic
   questions such as:
   - `How does FastAPI connect to PostgreSQL in the demo architecture?`
   - `Which storage components support retrieval in RAGProject?`

5. Use the Chat GraphRAG option or the CSRF-authenticated API example above to
   run explicit `strategy=graph_neo4j` with a safe synthetic query. Use
   `graph_postgres` to compare the PostgreSQL source-of-truth graph path.

6. Return to Retrieval Debug and refresh the trace. Select the newest graph run.
   Show:
   - `strategy_type=graph`
   - graph score summary
   - Graph Trace path counts
   - source chunk IDs mapped to retrieval run item IDs
   - citation coverage ratios and reason codes
   - cache summary if cache was enabled

7. Run the same safe graph query again with cache enabled. Refresh Retrieval
   Debug and point out cache status. A hit depends on unchanged provider,
   settings, active corpus, graph fingerprint, and TTL.

8. Switch to Chat and run `Agentic Router` with a relation-style safe query.
   If `GRAPH_ROUTER_ENABLED=true` and graph signal is strong, the router can
   select graph. If graph yields no evidence, router-selected graph may fall
   back to dense or hybrid depending on settings.

9. Open Admin Evaluations. Select or import `phase3_graph_multi_hop` if needed.
   Run a small comparison with `dense`, `hybrid`, `agentic_router`,
   `graph_postgres`, and `graph_neo4j`. If Neo4j is unavailable or unprojected,
   the graph reason codes show whether PostgreSQL graph fallback was used.

10. Open the evaluation detail. Show graph path relevance, graph citation
    coverage, multi-hop answerability, cache metrics, provider comparison, and
    cache comparison. Do not open raw payload dumps.

11. Finish with `graph_rag_acceptance_checklist.md`,
    `graph_rag_known_limitations.md`, and `graph_rag_next_phase_handoff.md`.

## Neo4j Read Model Demo

Neo4j is part of the default demo stack. Use the local non-secret default
password only for local development, and override it for shared environments.

```powershell
docker compose up -d neo4j
docker compose config --services
```

```sh
docker compose up -d neo4j
docker compose config --services
```

Then rebuild or restart backend and worker if local env overrides changed:

```powershell
docker compose up --build backend worker frontend
```

```sh
docker compose up --build backend worker frontend
```

Expected result:

- default `docker compose config --services` lists Neo4j
- PostgreSQL graph retrieval still works through `strategy=graph_postgres`
- Neo4j graph retrieval maps results through `source_chunk_ids`
- citations still come from retrieval run items, not directly from graph nodes

## Safe Demo Queries

| Purpose | Query |
|---|---|
| dense baseline | `How does the demo architecture use FastAPI?` |
| hybrid baseline | `Which components mention PostgreSQL and Qdrant?` |
| graph relation | `How does FastAPI relate to PostgreSQL?` |
| graph multi-hop | `Which storage components are connected to FastAPI?` |
| router graph signal | `How does the worker relate to retrieval cache records?` |
| no-context | `Which production pager rotation owns this private incident?` |

## Presenter Safety Notes

- Use IDs, counts, hashes, safe labels, relation types, and scores as evidence.
- Do not show full database rows or raw JSON dumps from private data.
- Do not run `docker compose down -v` during the demo.
- Do not show `.env`, shell history, cookies, local browser storage, or provider keys.
- Do not claim Neo4j is required. It is an optional projection/read model.
