# Sparse Retrieval

## Purpose

PR-23 adds standalone sparse lexical retrieval for `document_chunks`.

This is the first Advanced Retrieval implementation after the PR-20 strategy schema, PR-21 trace foundation, and PR-22 evaluation dataset schema. The goal is to let `/api/v1/rag/search` run `strategy=sparse` independently so PR-24 can later fuse dense and sparse candidates.

## Implementation

The production path uses PostgreSQL full-text search:

- `to_tsvector(<language>, document_chunks.content_text)` where `<language>` is `simple` or `english`
- `plainto_tsquery(<language>, :normalized_query)`
- `ts_rank_cd(...)`
- `GIN` expression indexes: `ix_document_chunks_content_fts` and `ix_document_chunks_content_fts_english`

The SQLite test path uses a deterministic lightweight BM25 fallback inside the repository. It is for CI/unit coverage only and does not introduce a production dependency.

## Query Normalization

The sparse strategy lowercases the input and keeps unique lexical terms matching `[A-Za-z0-9_]+`.

Defaults:

- `rag.sparse.enabled = true`
- `rag.sparse.provider = "postgres_fts"`
- `rag.sparse.language = "simple"`
- `rag.sparse.min_query_terms = 1`
- `rag.sparse.max_query_terms = 32`
- `rag.sparse.score_normalization = "max"`

Advanced Japanese morphological tokenization is not included in PR-23. Keyword-heavy, English, numeric, and code-like queries are the intended baseline. Japanese sparse retrieval improvement is left for later Phase2 hardening or Phase3.

## Retrieval Flow

`/api/v1/rag/search` now accepts:

```json
{
  "query": "keyword heavy query",
  "top_k": 20,
  "rerank_top_n": 5,
  "strategy": "sparse"
}
```

Flow:

1. Create a standalone `retrieval_runs` row with `strategy_type = "sparse"`.
2. Store a safe query plan trace with `query_hash` and normalized term count.
3. Run sparse lexical search over `document_chunks`.
4. Reuse the existing RDB final check.
5. Persist `retrieval_run_items` with `retrieval_source = "sparse"`.
6. Persist `score_breakdown_json` with `sparse_score`, `rank_order`, `final_rank`, and `selected_flag`.
7. Mark the run `succeeded`, including safe latency and settings trace.

Sparse 0-result searches return `200 OK` with `items=[]`.

## RDB Final Check

PR-23 reuses the existing final check. Only candidates that satisfy all conditions are returned:

- chunk exists
- `document_versions.status = 'ready'`
- `document_versions.is_active = true`
- `logical_documents.status = 'active'`
- requested modality matches
- requested logical document filter matches, when provided

Archived documents, inactive versions, failed versions, and wrong-modality chunks are excluded after lexical candidate retrieval.

## Score Semantics

Sparse score is normalized to `0.0..1.0` using max-score normalization within a result set. Ranking uses the unrounded raw sparse score; the normalized score is rounded only for persisted/displayed score metadata.

Tie-breaks are deterministic:

```text
raw_score DESC, document_chunk_id ASC
```

`rerank_score` and `rerank_order` remain null for sparse PR-23. Hybrid fusion and optional rerank-on-sparse are not implemented here.

Example `score_breakdown_json`:

```json
{
  "schema_version": "phase2.trace.v1",
  "retrieval_source": "sparse",
  "sparse_score": 0.873421,
  "rank_order": 1,
  "final_rank": 1,
  "selected_flag": true
}
```

## Security

Sparse retrieval must not store or log:

- raw user query
- raw prompt
- full context
- raw chunk text in trace or score breakdown
- PII
- secret, token, credential, API key, password
- full DB payload dumps

PostgreSQL query text is passed as a bind value through SQLAlchemy. The text search language is restricted to a small allow-list before it is used as a SQL literal.

Response snippets remain bounded display snippets. `payload_snapshot` stores only safe metadata and never stores `content_text`.

## Non-goals

PR-23 does not implement:

- Hybrid Retrieval
- RRF or weighted fusion
- QueryAnalyzer / QueryPlanner
- StrategyRouter
- Agentic Retrieval Loop
- Retrieval Debug UI v2
- Strategy Evaluation Runner
- LangSmith export
- SentenceTransformers experiment harness

## PR-24 Handoff

PR-24 can build hybrid retrieval by consuming:

- dense candidates from the existing dense retrieval path
- sparse candidates from `SparseRetrievalStrategy`
- item provenance via `retrieval_source`
- per-item score details via `score_breakdown_json`
- safe run-level trace via `query_plan_json`, `strategy_decision_json`, `latency_breakdown_json`, and `retrieval_settings_json`