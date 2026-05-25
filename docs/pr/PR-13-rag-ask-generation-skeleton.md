# PR-13 /rag/ask skeleton

## Scope

- Implements authenticated and CSRF-protected `POST /api/v1/rag/ask`.
- Persists one user message per `chat_session_id + client_message_id`.
- Creates chat-origin `retrieval_runs` with `chat_session_id` and `request_message_id`.
- Reuses the PR-12 embedding, vector retrieval, RDB final check, rerank, and retrieval item persistence path.
- Assembles a bounded, untrusted generation context from selected reranked chunks.
- Uses a deterministic fake answer generator by default so CI does not require an external LLM or API key.
- Saves an assistant message only after generation succeeds, with `linked_retrieval_run_id` set to the same-session retrieval run.

## Duplicate / Replay

- Same `client_message_id` with different message text returns `409 client_message_conflict`.
- Same message while the existing retrieval run is running returns `409 request_in_progress`.
- Same message with a failed retrieval run returns `409 conflict`.
- Same message with a succeeded retrieval run and assistant message replays the stored result with `meta.replayed=true`.

## Explicitly Deferred

- Citation table inserts.
- Confidence and groundedness calculation.
- Evaluation and feedback.
- Streaming, frontend integration, OCR, GraphRAG, and agentic RAG.
