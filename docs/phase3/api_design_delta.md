# Phase3 API Design Delta

PR-45 records candidate API changes only. No API implementation is added in this PR.

## Strategy Values

Candidate future request values:

- `/api/v1/rag/search`: `graph`, `graph_hybrid`
- `/api/v1/rag/ask`: `graph`, `graph_hybrid`, and Auto-selected graph through `llm_tool_orchestrator`

Existing dense, hybrid, agentic router, and Auto behavior must remain backward compatible.

## Response Deltas

Candidate safe additions:

- `graph_summary`
- `graph_path_count`
- `selected_graph_path_count`
- `graph_citation_validation_status`
- `graph_score_summary`
- `fallback_reason`

Do not add raw graph path payload dumps or raw evidence text to user-facing responses.

## Admin Debug Deltas

Admin-only Retrieval Debug may include:

- graph entity counts
- graph relation counts
- graph path refs
- graph path validation summary
- stale graph warnings
- graph + vector merge score summary
- traversal budget summary

## New Admin Endpoints Candidate

Future PRs may add read-only admin endpoints:

- `GET /api/v1/admin/graph/entities`
- `GET /api/v1/admin/graph/relations`
- `GET /api/v1/admin/graph/index-runs`
- `GET /api/v1/admin/retrieval-runs/{id}/graph-paths`

These are candidate endpoints only. They need pagination, RBAC, safe fields, and redaction tests before implementation.

## MCP Delta

Candidate local read-only MCP tools:

- `rag_graph_search`
- `rag_graph_explain_path`

They must stay local-only unless remote MCP is separately designed and accepted.

## Compatibility

- Existing clients that omit graph fields must continue to work.
- Existing strategy values remain unchanged.
- Existing no-context behavior remains unchanged.
- Citation items remain source chunk-backed.
