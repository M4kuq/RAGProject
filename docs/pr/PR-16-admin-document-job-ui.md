# PR-16 Admin Document / Job UI

PR-16 adds the admin-only React UI for operating the Phase1 document and job APIs.

Implemented scope:

- `/admin/documents` document list, filters, pagination, upload, archive
- `/admin/documents/:logicalDocumentId` document metadata, versions, upload new version, chunk preview, archive
- `/admin/documents/:logicalDocumentId/versions/:documentVersionId` version detail, chunk preview, approve
- `/admin/documents/review` pending review list and approve action
- `/admin/jobs` job list, filters, safe payload preview, retry
- `/admin/jobs/:jobId` job detail, safe payload, failed-job retry

Security notes:

- Admin routes are wrapped with the existing `/api/v1/auth/me` role check and show Forbidden for non-admin users.
- Mutations use `apiFetch`, so POST upload/approve/archive/retry requests receive `X-CSRF-Token` from the existing CSRF token handling.
- Raw file content, full chunk text, raw job payload dumps, token/secret/password/csrf/session values, and storage paths are not rendered.
- Chunk previews, filenames, labels, and error text are rendered through React text nodes and truncated before display.

Out of scope:

- Backend API additions
- DB migrations
- RAG pipeline changes
- evaluation/audit/settings/retrieval-debug UI expansion
- OCR, GraphRAG, Agentic RAG, deploy work
