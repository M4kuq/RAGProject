# PR-12 rag search debug API

## Scope

PR-12 implements `POST /api/v1/rag/search` as an admin-only standalone retrieval
debug API. It runs query embedding, Qdrant vector search, RDB final check,
rerank, and retrieval trace persistence.

The endpoint does not create chat messages, assistant messages, citations,
answers, confidence, frontend views, or full evaluation records.

## Request

```json
{
  "query": "RAG evaluation policy",
  "top_k": 20,
  "rerank_top_n": 5,
  "filters": {
    "logical_document_ids": [1000],
    "modality": "text"
  }
}
```

`top_k` follows the existing DDL constraint and is capped at `20`.
`rerank_top_n` controls how many reranked candidates are marked selected.

## Trace persistence

`/rag/search` creates a standalone `retrieval_runs` row:

- `chat_session_id = NULL`
- `request_message_id = NULL`
- `status = running | succeeded | failed`
- `query_hash` stores the query hash, not the raw query

Only candidates that pass the RDB final check are saved to
`retrieval_run_items`. Raw Qdrant candidates and raw chunk text are not saved.

`retrieval_score_summary` stores:

- `requested_top_k`
- `qdrant_candidate_count`
- `post_filter_candidate_count`
- `selected_count`
- `excluded_by_rdb_check_count`
- `top1_retrieval_score`
- `top3_avg_retrieval_score`
- `top1_rerank_score`

## Empty result

If no candidate remains after RDB final check, the endpoint returns `200 OK`
with `items=[]`, marks the retrieval run `succeeded`, saves a zero-count summary,
and does not create `retrieval_run_items`.

## CI behavior

CI uses fake embedding, fake Qdrant clients in tests, and the fake reranker.
No reranker model download or external API key is required for deterministic
test coverage.
