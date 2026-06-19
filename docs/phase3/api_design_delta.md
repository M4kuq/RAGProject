# Phase3 API Design Delta

PR-45 recorded candidate API changes. PR-48 through PR-53 implemented the
current `graph` path, graph citation/debug support, cache metadata, and
evaluation targets. PR-54 records the final demo boundary without adding new API
surface.

## Strategy Values

Implemented graph request value:

- `/api/v1/rag/search`: `graph`
- `/api/v1/rag/ask`: `graph`

Future candidate request value:

- `graph_hybrid`

Existing dense, hybrid, agentic router, and Auto behavior must remain backward compatible.

## Response Deltas

Safe additions used by graph/debug/cache/evaluation paths:

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
- graph score summary
- cache summary
- evaluation comparison target metadata
- traversal budget summary

## Admin Endpoints

Implemented admin graph trace endpoint:

- `GET /api/v1/rag/retrieval-runs/{retrieval_run_id}/graph-trace`

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
