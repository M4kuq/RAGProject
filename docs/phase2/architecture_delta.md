# Phase2 Architecture Delta

## Phase1 Baseline

Phase1 includes dense retrieval, rerank, citation, confidence, manual evaluation, MCP, and Web UI. Evaluation cases were fixture-oriented and the retrieval path was dense by default.

## Phase2 Direction

Phase2 adds observable, comparable retrieval strategy foundations before adding new retrieval algorithms. The core themes are:

- Advanced Retrieval
- Agentic Control
- Evaluation
- Observability

## PR-20 Delta

PR-20 adds retrieval strategy enums, retrieval trace columns, score/source columns, and Phase2 system settings. Runtime behavior stays default `dense`.

## PR-21 Delta

PR-21 writes safe trace payloads for the existing dense retrieval path. It records metadata, scores, settings, and latency without raw prompt, raw query, full context, raw chunk text, PII, or secrets.

## PR-22 Delta

PR-22 adds evaluation dataset and case management:

- persistent evaluation datasets
- persistent evaluation cases
- strategy-aware evaluation runs
- strategy-aware evaluation run items
- metric value/detail schema for strategy comparison
- fixture import/export
- admin dataset/case API
- minimal UI connection

The existing evaluation runner remains dense-compatible. Non-dense strategy execution belongs to PR-25.

## PR-23 Delta

PR-23 adds standalone sparse retrieval:

- PostgreSQL full-text search over `document_chunks`
- `SparseRetrievalStrategy`
- `strategy=sparse` for `/api/v1/rag/search`
- sparse run trace and item score breakdown
- existing RDB final check reuse

`/rag/ask` remains dense by default. Hybrid fusion and strategy routing remain downstream work.

## Downstream Dependencies

PR-24 will consume PR-23 sparse candidates and existing dense candidates for hybrid retrieval. PR-25 will use PR-22 datasets and strategy metric schema to compare retrieval strategies. PR-30 will reuse the schema for agentic router evaluation and failure dataset promotion.

## Phase3 Boundary

Graph-RAG, OCR, multimodal, AWS, S3, OIDC/OAuth, A/B evaluation, and production online evaluation are Phase3 topics.
