# Phase2 Acceptance Criteria

## PR-20

- `RetrievalStrategy` enum exists and matches DB CHECK constraints.
- Default strategy is `dense`.
- Retrieval trace columns exist on `retrieval_runs`.
- Source and score columns exist on `retrieval_run_items`.
- Phase1 `/rag/search` and `/rag/ask` remain default dense.
- Trace DTOs are JSON serializable and reject sensitive keys.

## PR-21

- Existing dense `/rag/search` writes safe query plan, strategy decision, settings, latency, item source, and score breakdown.
- Existing dense `/rag/ask` writes safe trace including generation/citation/confidence latency where available.
- Failed retrieval runs preserve safe trace where possible.
- Responses do not expose internal trace JSON by default.

## PR-22

- `evaluation_datasets` and `evaluation_cases` exist.
- `evaluation_runs` can reference dataset, strategy, trigger type, retrieval settings, and strategy metric summary.
- `evaluation_run_items` can reference case, strategy, case key, latency breakdown, and metric summary.
- `evaluation_results` can store `metric_name`, `metric_value`, `metric_detail_json`, and `strategy_type`.
- Metric specs include `recall_at_k`, `mrr`, `citation_coverage`, `groundedness`, `faithfulness`, `no_context_rate`, `p95_latency`, and `strategy_selection_accuracy`.
- JSON fixture import/export is idempotent and safe.
- Admin dataset/case APIs are protected by admin auth and CSRF on writes.
- Viewer users receive `403`; unauthenticated users receive `401`.
- Existing minimal evaluation run still works with default `dense`.
- Minimal Evaluation UI can select datasets, show strategy, list cases, and export a manifest.

## PR-23

- `SparseRetrievalStrategy` exists.
- `/api/v1/rag/search` accepts `strategy=sparse`.
- `strategy` omitted remains default `dense`.
- PostgreSQL full-text search is available through language-matched FTS indexes.
- SQLite tests use deterministic lightweight BM25 fallback without new production dependencies.
- Sparse retrieval runs save `retrieval_runs.strategy_type = sparse`.
- Sparse run items save `retrieval_run_items.retrieval_source = sparse`.
- Sparse score breakdown saves `sparse_score`, rank metadata, and selection flag only.
- Sparse ranking/limit pre-filters active logical documents and ready active versions; the existing RDB final check remains a defense-in-depth guard for archived logical documents, inactive versions, failed versions, missing chunks, and wrong modality.
- Sparse 0-result returns `200 OK` with `items=[]`.
- Sparse failure marks the run failed with safe trace.
- Dense `/rag/search` and `/rag/ask` regressions still pass.

## PR-24

- `HybridRetrievalStrategy` exists.
- `/api/v1/rag/search` accepts `strategy=hybrid`.
- `strategy` omitted remains default `dense`.
- Hybrid retrieval runs dense vector retrieval and sparse lexical retrieval for the same safe query hash.
- Candidate dedupe is by `document_chunk_id`.
- RRF and weighted fusion are available without new production dependencies.
- Fused scores are normalized to `0.0..1.0` and ranking is deterministic.
- Hybrid retrieval runs save `retrieval_runs.strategy_type = hybrid`.
- Hybrid run items save `retrieval_run_items.retrieval_source = hybrid`.
- Hybrid score breakdown saves dense score, sparse score, fused score, rank metadata, fusion method, and selection flag only.
- Hybrid trace records safe query plan, strategy decision, retrieval settings, latency, and score metadata.
- Hybrid reuses the existing RDB final check.
- Hybrid 0-result returns `200 OK` with `items=[]`.
- Hybrid disabled returns `strategy_not_enabled` without creating a retrieval run.
- Dense `/rag/search`, sparse `/rag/search`, `/rag/ask`, and evaluation regressions still pass.

## PR-25

- Admin can queue evaluation runs with `strategies=["dense", "sparse", "hybrid"]`.
- `agentic_router` is rejected or marked disabled for PR-25 execution.
- The runner creates one item per active case per strategy.
- Each item links to a strategy-specific retrieval run.
- Results store `recall_at_k`, `mrr`, `citation_coverage`, `groundedness`, `faithfulness`, `no_context_rate`, `p95_latency`, and not-applicable `strategy_selection_accuracy`.
- `strategy_metrics_summary_json` stores per-strategy aggregates.
- The strategy comparison API returns per-strategy metric summaries.
- Existing default dense evaluation runs still work.
- External LLM judge, heavy model downloads, CI workflow scheduling, QueryAnalyzer, StrategyRouter, and Agentic Retrieval Loop remain out of scope.

## PR-34

- Upload validation accepts `.xlsx` and `.pptx`.
- Legacy `.xls` / `.ppt` and macro-enabled Office files are rejected.
- Excel extraction reads visible sheets and records sheet / row / column metadata.
- PowerPoint extraction reads slide text and records slide metadata.
- Hidden sheets, speaker notes, embedded objects, images, and OCR are not ingested.
- Parent-child chunk v1 is represented with safe metadata in `document_chunks.metadata_json`.
- Search and citation source labels include sheet or slide information when available.

## PR-35

- Upload validation accepts `.html`, `.htm`, and `.xml`.
- SVG, XML DTD/entity declarations, unsupported binary content, and dangerous file names are rejected.
- HTML extraction removes active/non-visible elements and records title / heading metadata.
- XML extraction rejects XXE/entity inputs and records root / element path metadata.
- `POST /api/v1/documents/url` creates a document version and ingest job for a single safe URL.
- URL fetch validates scheme, userinfo, DNS-resolved IPs, redirects, timeout, max bytes, and content type.
- Localhost, private IPs, link-local IPs, metadata IPs, metadata hostnames, and `.local` hosts are rejected.
- URL metadata stores safe source/final URLs without query strings or fragments.
- HTML/XML/URL chunks can be searched and cited with safe source labels.
- CI validation uses fixtures/mock HTTP transport and does not require external internet access.
- Raw HTML/XML, full fetched body, raw chunk text, PII, tokens, and secrets are not logged, traced, or returned.
- Existing PDF / DOCX / TXT / Markdown / CSV ingest remains compatible.
- Raw file content, raw chunk text, PII, tokens, and secrets are not logged, traced, or returned.

## Security

The following must not be stored in dataset, case, metric detail, trace, score breakdown, logs, or normal responses:

- raw prompt
- full context
- raw chunk text
- PII
- secret, token, credential, API key, password

Evaluation case `question` is allowed only as a safe evaluation input. It must not be a full prompt or contain PII/secrets.
Sparse and hybrid response snippets are bounded display snippets. Raw chunk text is never stored in trace or score breakdown.

## Out of Scope for PR-25

- QueryAnalyzer / QueryPlanner
- Agentic Router
- Agentic Retrieval Loop
- Retrieval Debug UI v2
- CI evaluation workflow
- LangSmith adapter
- SentenceTransformers experiment harness
- Graph-RAG / OCR / multimodal
- AWS / S3 / OIDC
