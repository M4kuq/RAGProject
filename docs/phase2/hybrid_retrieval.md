# Hybrid Retrieval

PR-24 adds standalone hybrid dense+sparse retrieval for `/api/v1/rag/search`.

It consumes the PR-20 strategy schema, PR-21 safe trace fields, PR-22 strategy metric schema, and PR-23 sparse retrieval foundation. The goal is to return a single ranked result list from dense vector candidates and sparse lexical candidates so PR-25 can compare dense / sparse / hybrid on the same evaluation dataset.

## Strategy

Request:

```json
{
  "query": "keyword heavy policy",
  "top_k": 20,
  "rerank_top_n": 5,
  "strategy": "hybrid"
}
```

Behavior:

1. Create a standalone `retrieval_runs` row with `strategy_type = "hybrid"`.
2. Store safe query plan, strategy decision, retrieval settings, and latency trace.
3. Run dense vector retrieval with the existing Qdrant path.
4. Run sparse lexical retrieval with the PR-23 sparse strategy.
5. Fuse candidates with RRF or weighted score fusion.
6. Deduplicate by `document_chunk_id`.
7. Reuse the existing RDB final check.
8. Persist `retrieval_run_items` with `retrieval_source = "hybrid"`.
9. Persist `score_breakdown_json` with score/rank metadata only.

`/rag/ask` remains default dense in PR-24.

## Fusion Methods

Supported methods:

- `rrf`
- `weighted`

Default settings:

- `rag.hybrid.enabled = true`
- `rag.hybrid.fusion_method = "rrf"`
- `rag.hybrid.rrf_k = 60`
- `rag.hybrid.dense_weight = 0.5`
- `rag.hybrid.sparse_weight = 0.5`
- `rag.hybrid.candidate_multiplier = 2`

RRF uses source rank positions and normalizes the fused score to `0.0..1.0` within the candidate set. Weighted fusion uses max-normalized dense and sparse scores. Ties are deterministic: fused score desc, dense rank asc, sparse rank asc, `document_chunk_id` asc.

## Trace

Hybrid query plan stores:

- `strategy_type = "hybrid"`
- `query_mode = "dense_sparse_single_query"`
- `query_hash`
- metadata filter counts
- reason codes such as `phase2_hybrid_dense_sparse` and `fusion_method:<method>`

Hybrid strategy decision stores:

- `selected_strategy = "hybrid"`
- `decision_source = "request"`
- `router_enabled = false`
- reason code `explicit_strategy_hybrid`

Hybrid latency stores:

- `query_embedding_ms`
- `qdrant_search_ms`
- `sparse_search_ms`
- `fusion_ms`
- `rdb_final_check_ms`
- `retrieval_items_persist_ms`

## Score Breakdown

Example:

```json
{
  "schema_version": "phase2.trace.v1",
  "retrieval_source": "hybrid",
  "dense_score": 0.91,
  "sparse_score": 0.82,
  "fused_score": 1.0,
  "rank_order": 1,
  "final_rank": 1,
  "selected_flag": true,
  "fusion_method": "rrf",
  "dense_rank": 1,
  "sparse_rank": 2
}
```

`rerank_score` and `rerank_order` remain null for hybrid PR-24. Optional rerank-after-fusion is left for later hardening.

## RDB Final Check

Hybrid retrieval reuses the existing final check:

- chunk exists
- `document_versions.status = ready`
- `document_versions.is_active = true`
- `logical_documents.status = active`
- modality matches request filter

Dense and sparse candidates are overfetched by a bounded multiplier before fusion to reduce stale-candidate hiding. Raw candidate payloads are not persisted.

## Security

Hybrid retrieval must not store or log:

- raw user query
- raw prompt
- raw chunk text
- full context
- Qdrant raw payload dumps
- PII
- secret / token / credential / API key / password

Trace and score JSON store only hashes, safe counts, provider/method names, scores, ranks, durations, and reason codes. Response snippets remain bounded display snippets.

## Out of Scope

PR-24 does not implement QueryAnalyzer, StrategyRouter, Agentic Retrieval Loop, Retrieval Debug UI v2, Strategy Evaluation Runner, LangSmith export, SentenceTransformers experiments, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

## PR-25 Handoff

PR-25 can use:

- `strategy=hybrid`
- persisted `retrieval_runs.strategy_type = hybrid`
- persisted `retrieval_run_items.retrieval_source = hybrid`
- per-item dense / sparse / fused score metadata
- PR-22 evaluation dataset and metric schema
