# GraphRAG Manual Test Cases

Use these cases for PR-54 manual acceptance. Evidence should point to current
files, commands, screenshots, CI checks, or safe admin summaries. Do not attach
raw payload dumps, raw prompts, raw chunks, raw documents, raw graph evidence,
PII, secrets, tokens, credentials, cookies, Neo4j credentials, or `.env` values.

| ID | Area | Setup | Action | Expected result | Evidence |
|---|---|---|---|---|---|
| G-TC-001 | README | Fresh checkout | Open root `README.md` and `docs/phase3/graph_rag_final_readme.md` | GraphRAG path can be followed from README links | README links |
| G-TC-002 | Compose defaults | No graph env set | Run `docker compose config --quiet` | Config succeeds and Neo4j is not required | command output |
| G-TC-003 | Graph env propagation | Set graph/cache env flags | Run `docker compose config` and inspect safe env names only | backend/worker receive graph/cache settings with default-off behavior preserved | redacted config notes |
| G-TC-004 | Graph index queue | Running stack | Run `python -m app.scripts.queue_graph_index_builds` in backend container | Active ready document versions get graph index jobs or are skipped if already queued | safe JSON counts |
| G-TC-005 | Graph index worker | Queued graph jobs | Wait for worker | Graph index jobs succeed or fail with safe error codes | Admin Jobs or worker status |
| G-TC-006 | Explicit graph search | Graph retrieval enabled and graph index built | Call `/api/v1/rag/search` with `strategy=graph` using a safe synthetic query | Response is chunk-backed or returns no-context/empty result without unsupported answer | retrieval run ID |
| G-TC-007 | Explicit graph ask | Graph retrieval enabled and graph index built | Call `/api/v1/rag/ask` with `strategy=graph` | Answer has citations or returns `no_context_found`; no unsupported answer is generated | chat run summary |
| G-TC-008 | Router graph selection | `GRAPH_ROUTER_ENABLED=true` | Ask relation/multi-hop query with `agentic_router` | Router may select graph when signal and index are available; otherwise base router behavior remains | strategy decision summary |
| G-TC-009 | Graph fallback | Router-selected graph with no evidence | Run relation query against no-match corpus | Router-selected graph records safe fallback reason and uses configured dense/hybrid fallback | reason codes |
| G-TC-010 | Graph trace | Graph run exists | Open Retrieval Debug and select graph run | Graph Trace shows path counts, safe labels, relation types, source chunk IDs, retrieval run item IDs, coverage ratios | screenshot or notes |
| G-TC-011 | Citation bridge | Graph ask run with citations | Open answer citation and source locator | Citation resolves through retrieval run item and document chunk, not raw graph edge | source locator view |
| G-TC-012 | Cache disabled default | No cache env set | Run same graph query twice | cache summary records disabled/not cacheable behavior, not unsafe payload | retrieval debug |
| G-TC-013 | Cache enabled | `RETRIEVAL_CACHE_ENABLED=true` | Run same graph query twice before TTL expiry | Second compatible request can hit cache; payload remains refs/hashes only | cache summary |
| G-TC-014 | Cache provider split | Compare Postgres and Neo4j providers | Run equivalent graph query per provider | Cache keys differ by provider | cache summary hashes |
| G-TC-015 | Evaluation graph_postgres | Graph index exists | Run small evaluation with `phase3_graph_multi_hop`, `graph_postgres` | Graph metrics are recorded as safe summaries | evaluation detail |
| G-TC-016 | Evaluation graph_neo4j optional | Neo4j not configured or unprojected | Include `graph_neo4j` target after PostgreSQL graph sources exist | Target records `neo4j_to_postgres_fallback` when PostgreSQL graph can answer, or safe reason codes when no graph source is usable; overall run continues | evaluation detail |
| G-TC-017 | Neo4j default optional | No `neo4j` profile | Run default stack | App starts without Neo4j and uses PostgreSQL graph store | compose services |
| G-TC-018 | Neo4j projection | `neo4j` profile enabled | Queue graph index after enabling projection | Neo4j projection writes safe refs only and does not block PostgreSQL graph index success | worker result summary |
| G-TC-019 | Redaction docs | Review PR docs | Search for raw/secret examples | Docs contain policy terms but no real secret values or raw private payloads | scan output |
| G-TC-020 | Smoke | Fresh shell | Run `scripts/smoke_phase3_graph_rag.*` | Smoke passes without destructive cleanup or external provider calls | command output |

## Minimum Acceptance Set

For a PR-54 portfolio handoff, run at least:

- G-TC-001 through G-TC-004
- G-TC-010
- G-TC-012 or G-TC-013
- G-TC-015
- G-TC-017
- G-TC-019
- G-TC-020

If local services are not running, document that G-TC-004 through G-TC-018 were
not executed locally and rely on unit tests/CI for runtime evidence.
