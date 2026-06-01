# Phase2 Test Strategy

## PR-20

- Python retrieval enum values match Alembic CHECK values.
- Retrieval trace DTOs serialize to JSON.
- Trace DTOs reject sensitive keys.
- `/rag/search` and `/rag/ask` remain default dense.

## PR-21

- Latency spans are non-negative.
- Trace redaction removes forbidden keys, credential-like strings, URLs, and email-like values.
- `/rag/search` success / zero result / failure writes safe trace.
- `/rag/ask` success / no_context / generation failure / citation failure writes safe trace.
- Normal responses do not expose internal trace JSON.

## PR-22 Unit Tests

- `phase2_strategy_smoke.json` loads with the fixture loader.
- `EvaluationDatasetManifest` serializes and rejects secret-like or PII-like values.
- Metric specs include the required strategy comparison metrics.
- Metric detail DTOs do not include raw prompt, full context, raw chunk text, PII, or secrets.

## PR-22 DB / Migration Tests

- Alembic head is `0004_eval_dataset_metrics`.
- `evaluation_datasets` and `evaluation_cases` exist.
- `evaluation_runs` has dataset, strategy, trigger, retrieval settings, and strategy summary fields.
- `evaluation_run_items` has case, strategy, case key, latency, and metric summary fields.
- `evaluation_results` has metric value, metric detail, and strategy fields.
- Invalid strategy values are rejected by DB CHECK constraints.
- Upgrade and downgrade remain additive/reversible.

## PR-22 API Tests

- Unauthenticated dataset API access returns `401`.
- Viewer write access returns `403`.
- Admin can create/list/detail/archive datasets.
- Admin can create/list/detail/archive cases.
- Nested dataset/case mismatch returns `404`.
- JSON manifest import succeeds and is idempotent.
- Invalid manifest with secret-like values returns `422`.
- Export returns only a safe manifest.

## Regression Tests

- Existing fixture-based evaluation run still queues and runs with default `dense`.
- Persistent dataset cases can be evaluated by the existing dense runner.
- PR-21 retrieval trace tests still pass.
- `/rag/search` and `/rag/ask` tests still pass.

## PR-23 Sparse Retrieval Tests

- Alembic head is `0005_sparse_retrieval_fts`.
- PostgreSQL schema includes language-matched sparse FTS indexes.
- Sparse query normalization lowercases, deduplicates, and enforces the max term limit.
- Sparse score normalization ranks by raw score and tie-breaks by `document_chunk_id ASC`.
- `/rag/search strategy=sparse` writes `retrieval_runs.strategy_type = sparse`.
- Sparse items write `retrieval_source = sparse` and `score_breakdown_json.sparse_score`.
- Sparse no-result returns `200 OK` with `items=[]`.
- Sparse failure marks the run failed with safe trace.
- Dense `/rag/search` and `/rag/ask` remain default dense.
- Trace, response, and score breakdown do not include raw query, raw prompt, raw chunk text, full context, PII, or secrets.

## PR-24 Hybrid Retrieval Tests

- Hybrid fusion deduplicates by `document_chunk_id`.
- RRF and weighted fusion are deterministic.
- Fused scores normalize to `0.0..1.0`.
- `/rag/search strategy=hybrid` writes `retrieval_runs.strategy_type = hybrid`.
- Hybrid items write `retrieval_source = hybrid`.
- Hybrid score breakdown writes dense score, sparse score, fused score, fusion method, and rank metadata.
- Hybrid trace writes query plan, strategy decision, retrieval settings, `qdrant_search_ms`, `sparse_search_ms`, `fusion_ms`, and final-check latency.
- Hybrid disabled returns `strategy_not_enabled` without creating a retrieval run.
- Dense `/rag/search`, sparse `/rag/search`, `/rag/ask`, and evaluation dataset regressions remain green.
- Trace, response, and score breakdown do not include raw query, raw prompt, raw chunk text, full context, PII, or secrets.

## PR-25 Strategy Evaluation Runner Tests

- `EvaluationRunCreateRequest` accepts `strategies=["dense", "sparse", "hybrid"]` and rejects `agentic_router`.
- Archived or empty persistent datasets cannot queue a run.
- Worker execution creates one item per case per strategy.
- Dense, sparse, and hybrid items link to strategy-specific `retrieval_runs`.
- `evaluation_results` stores `recall_at_k`, `mrr`, `citation_coverage`, `groundedness`, `faithfulness`, `no_context_rate`, `p95_latency`, and not-applicable `strategy_selection_accuracy`.
- Partial case failures leave the run succeeded with `failed_count` in the summary.
- All case/strategy failures mark the run failed.
- `strategy_metrics_summary_json` and the strategy comparison API aggregate metrics by strategy.
- Metric detail JSON contains counts, ranks, units, and reason codes only; it does not contain raw prompt, full context, raw chunk text, PII, or secrets.

## PR-26 Retrieval Debug UI v2 Tests

- `GET /api/v1/rag/retrieval-runs/{retrieval_run_id}` is admin-only, read-only, and returns redacted trace/items.
- The detail response excludes raw prompt, full context, raw chunk text, PII, token, secret, credential, API key, password, CSRF, session, and cookie values.
- `/admin/retrieval-debug` is protected by the admin route guard.
- The strategy selector exposes `dense`, `sparse`, and `hybrid`, while router and multi-query strategies are disabled as coming soon.
- The debug form calls `/api/v1/rag/search` with the selected strategy and CSRF token.
- The page displays run summary, query plan, strategy decision, retrieval settings, latency breakdown, score summary, score breakdown, retrieval run items, and latest strategy evaluation summary.
- Frontend redaction helpers remove forbidden trace keys and secret-like assignment values before rendering.

## PR-27 Query Analyzer / Query Planner Tests

- `QueryAnalyzer` intent classification is deterministic.
- Ambiguity, keyword-heavy, temporal, and version-specific signals are deterministic.
- `QueryPlanner` rewrite metadata is deterministic and does not apply to retrieval by default.
- Planned sub-query count respects `rag.query_planner.max_sub_queries`.
- Metadata filter candidates are structured and not raw SQL/Qdrant filter strings.
- Candidate strategies are proposals only; StrategyRouter is not executed.
- `/rag/search strategy=dense`, `sparse`, and `hybrid` write safe analyzer/planner fields to `query_plan_json`.
- `/rag/ask` writes safe analyzer/planner fields while preserving default dense behavior.
- The original user query preview is not persisted; derived query previews are redacted/truncated and do not expose raw prompt, full context, raw chunk text, PII, or secrets.
- Retrieval Debug UI renders analysis/planning summary and tolerates missing fields.

## PR-28 Strategy Router Tests

- `StrategyRouter` disabled or failing returns the configured fallback strategy (`fallback_dense` by default, `dense` when configured).
- Keyword-heavy and comparison queries select `hybrid` when available.
- Normal factual queries select `dense`.
- Version-specific queries record disabled `version_aware` candidates and execute an implemented strategy.
- Sparse/hybrid unavailable states fall back to `dense`.
- `/rag/search strategy=agentic_router` persists `retrieval_runs.strategy_type = agentic_router`.
- Router decisions persist requested, selected, and execution strategies in `strategy_decision_json` when enabled.
- `router_store_decision_trace=false` suppresses router decision persistence without changing execution.
- Router latency is recorded as `strategy_router_ms`.
- `/rag/ask strategy=agentic_router` is explicit opt-in; default ask remains dense.
- Retrieval Debug UI renders router decision, fallback, confidence, reason codes, disabled candidates, and safety flags.
- Router decision JSON does not contain raw query, raw prompt, full context, raw chunk text, PII, secrets, or raw exception messages.

## PR-29 Agentic Retrieval Loop Tests

- `ContextSufficiencyChecker` accepts enough candidates and rejects zero, low-score, and low-diversity comparison results.
- `AgenticRetrievalExecutor` respects `max_retrieval_calls` and `max_fallback_calls`.
- Initial insufficient context triggers one deterministic fallback when budget remains.
- Fallback results merge and dedupe by `document_chunk_id`.
- Merged candidates are reranked before final persistence.
- `/rag/search strategy=agentic_router` persists `retrieval_call_count`, fallback, sufficiency, and agentic latency fields.
- `/rag/ask strategy=agentic_router` returns `422 no_context_found` without an assistant message when budget is exhausted.
- Direct dense, sparse, and hybrid strategy regressions remain green.
- Agentic trace and score breakdown do not contain raw query, raw prompt, full context, raw chunk text, PII, secrets, or raw exception messages.

## PR-30 Agentic Strategy Evaluation Tests

- `EvaluationRunCreateRequest` accepts `dense`, `sparse`, `hybrid`, and `agentic_router` in the same run.
- Agentic metric rows persist `strategy_selection_accuracy`, `fallback_rate`, `budget_exhausted_rate`, `sufficiency_score_avg`, and `retrieval_call_count_avg`.
- `strategy_selection_accuracy` is numeric only when a case defines `expected_strategy` or `acceptable_strategies`.
- Failure candidates are extracted for no-context, low-score, citation, strategy mismatch, budget, latency, and safe exception reasons.
- Promotion into an active dataset is idempotent; duplicate promotion returns an existing/skipped result.
- Failure candidate and promotion responses do not expose raw prompt, full context, raw chunk text, PII, or secrets.

## PR-31 CI Retrieval Evaluation Smoke Tests

- `retrieval-eval-smoke.yml` exposes `workflow_dispatch`, low-frequency `schedule`, artifact upload, and GitHub step summary output.
- The default path uses real local retrieval with PostgreSQL, Qdrant, and indexed demo documents.
- Fake embedding, fake reranker, and fake evaluator behavior are not used by the PR-31 smoke itself; answer generation is not exercised.
- Missing local model/cache prerequisites produce a safe blocked artifact instead of fake fallback.
- The workflow is not a required pull-request gate.
- The smoke script parses dataset, strategy, metric, threshold, and warn/fail options.
- Threshold violations and failed evaluation items are warnings in `warn` mode and non-zero exits in `fail` mode.
- `p95_latency_ms_max` is checked against the p95 latency value, not average latency.
- JSON and Markdown artifacts contain only aggregate metrics, thresholds, failure counts, and limitations.
- Artifacts and summaries redact raw prompt, full context, raw chunk text, PII, tokens, and secrets.
- Local wrappers call the same backend module as the workflow.

## PR-32 LangSmith Optional Adapter / Trace Export Tests

- `TRACE_EXPORT_PROVIDER` accepts `none` and `langsmith` only.
- Default settings select the no-op exporter and do not require secrets.
- Missing LangSmith SDK or API key skips export without failing search, ask, evaluation, or CI smoke.
- LangSmith export uses a minimized safe payload with hashes, counts, scores, status, and reason codes only.
- Retrieval export payloads exclude raw query, raw prompt, full context, raw chunk text, snippets, raw payload snapshots, PII, paths, tokens, cookies, sessions, credentials, API keys, and secrets.
- Evaluation and CI smoke export payloads include aggregate strategy metrics and failure counts only.
- Export failures return safe status codes and remain non-fatal.
- PR-31 JSON/Markdown artifacts record only safe trace export status.

## PR-33 SentenceTransformers Experiment Harness Tests

- Manifest parsing accepts `phase2.experiment.v1` and rejects unknown fields,
  unsupported strategies, unsupported metrics, local paths, and secret-like
  model ids.
- Model registry lookup distinguishes embedding and reranker candidates.
- Availability checks do not download models under `never` or `if-cached`.
- Missing optional models are `skipped`; missing required models are `blocked`.
- Dry-run validates the manifest, registry, availability status, artifact shape,
  and Markdown report without invoking heavy model evaluation.
- Local mode can call the existing Strategy Evaluation Runner through the PR-31
  retrieval smoke path when models are available.
- JSON and Markdown artifacts contain model ids, counts, aggregate metrics, and
  reason codes only.
- Artifacts and reports redact raw prompt, full context, raw chunk text, full
  answer text, PII, secrets, tokens, credentials, and local cache paths.
- Normal CI does not require SentenceTransformers, model downloads, GPU, or
  external API keys.

## PR-34 Advanced Import / Parent-child Chunk Tests

- Upload validation accepts `.xlsx` and `.pptx` with valid OOXML ZIP structure.
- Legacy `.xls` / `.ppt`, macro-enabled Office files, embedded object parts,
  encrypted ZIP entries, path traversal entries, and compression bombs are
  rejected.
- Excel extraction reads visible sheets, skips hidden sheets, renders rows
  deterministically, and records sheet / row / column metadata.
- PowerPoint extraction reads slides in presentation order, extracts shape/table
  text, excludes speaker notes and OCR, and records slide metadata.
- Parent-child chunk v1 stores metadata-only child relationships in
  `document_chunks.metadata_json`.
- `payload_snapshot`, Qdrant payloads, API chunk preview, source labels, search
  results, and citations expose only allowlisted structural metadata.
- Ingest worker processes `.xlsx` and `.pptx` into ready document versions and
  document chunks.
- Existing PDF/DOCX/TXT/Markdown/CSV ingest regressions remain green.
- Dense, sparse, hybrid, agentic_router search and ask regressions remain green.
- Logs, traces, responses, and artifacts do not expose raw file content, raw
  chunk text, PII, tokens, or secrets.

## PR-35 HTML / XML / URL Ingest Tests

- Upload validation accepts `.html`, `.htm`, and `.xml` with matching MIME types.
- Upload validation rejects SVG, NUL bytes, XML DTD/entity declarations, and
  unsupported/binary content.
- HTML extraction removes `script`, `style`, `noscript`, `iframe`, `object`,
  `embed`, and comments while preserving headings, paragraphs, lists, and table
  text.
- XML extraction rejects external/entity declarations and records safe root/path
  metadata.
- `POST /api/v1/documents/url` is admin-only and CSRF-protected.
- URL fetch rejects non-HTTP schemes, userinfo/auth URLs, localhost, private IP,
  link-local, metadata IP, metadata hostnames, unsafe redirects, too many
  redirects, unsupported content types, timeout, and over-size responses.
- URL fetch tests use mock HTTP transports and resolver stubs; CI must not
  require external internet access.
- URL ingest stores only safe `source_url` / `final_url` metadata without query
  string, fragment, fetched body, raw HTML/XML, raw chunk text, PII, tokens, or
  secrets.
- Ingest worker processes uploaded HTML/XML and URL-derived documents into ready
  document versions and chunks.
- Search and citation source labels can include safe heading/XML path/URL
  metadata.
- Existing PDF/DOCX/TXT/Markdown/CSV/Excel/PowerPoint ingest regressions remain
  green.

## PR-36 Document Diff / Citation Navigation Tests

- Admin document version compare succeeds for two versions under one logical document.
- Viewer access to version compare is rejected.
- Mismatched logical document/version ids return not found.
- Metadata diff includes only safe version fields and redacted URL metadata.
- Chunk diff reports added, removed, changed, and unchanged counts.
- Diff items return bounded previews only; raw full chunk text, storage keys,
  secret-like assignments, URL query strings, and email addresses are not exposed.
- Citation source lookup succeeds for a citation that belongs to the requesting
  viewer's chat.
- Citation source lookup returns not found for another viewer.
- Admin can inspect citation source locators.
- Citation source locators include Office/Web metadata when present and retain
  old-version warning state.
- Chat CitationPanel opens a bounded source preview and shows admin deep links
  only for admin users.
- Admin DocumentDetail shows version compare summary, metadata diff, and bounded
  chunk previews.

## PR-37 Phase2 Final Hardening / Demo / Docs Tests

- Phase2 README links to final demo, manual test, acceptance, limitations, and
  Phase3 handoff documents.
- Phase2 README links to each PR-20 through PR-36 feature document that exists
  in `docs/phase2`.
- `phase2_demo_scenario.md` covers Advanced Retrieval, Agentic-RAG, Retrieval
  Debug UI, Strategy Evaluation, CI Retrieval Evaluation, LangSmith optional
  export, SentenceTransformers dry-run, advanced import, document diff, and
  citation navigation.
- `phase2_manual_test_cases.md` includes categories P2-TC-001 through
  P2-TC-1400 for startup, retrieval, router, agentic loop, debug UI,
  evaluation, CI smoke, observability, experiments, import, document diff,
  security, and final acceptance.
- `phase2_acceptance_checklist.md` covers the required Phase2 completion checks
  and uses safe evidence pointers instead of raw payload dumps.
- `phase2_known_limitations.md` and `phase3_handoff.md` clearly separate
  Phase2 limitations from Graph-RAG, OCR, multimodal, AWS/S3, OIDC, and online
  evaluation Phase3 scope.
- `scripts/smoke_phase2.ps1` and `scripts/smoke_phase2.sh` validate key Phase2
  artifacts without destructive cleanup by default.
- Phase2 smoke scripts do not read `.env`, print secrets, run
  `docker compose down -v`, require external APIs, or force heavy model
  downloads.
- Phase2 docs do not include raw prompts, full context, raw chunk text, private
  document text, PII, real API keys, tokens, cookies, or credentials.

## PR-38 MCP Hybrid / Agentic RAG Tools Tests

- `rag_search` preserves default dense compatibility and accepts `dense`,
  `sparse`, `hybrid`, and `agentic_router`.
- `rag_search_hybrid` and `rag_search_agentic` are thin wrappers over
  strategy-aware `rag_search`.
- `rag_ask` preserves default dense compatibility and allows explicit
  `strategy=agentic_router`; `rag_ask_agentic` is a wrapper.
- `rag_get_retrieval_trace` returns safe query-plan, strategy-decision, score,
  latency, and count summaries without raw query, prompt, context, or chunk
  text.
- `rag_compare_strategies` reads existing latest evaluation results and does
  not create new runs from MCP.
- `rag_get_evaluation_summary` returns safe strategy, failure, promotion, and
  agentic metric summaries.
- MCP resources include `rag://retrieval-runs/{retrieval_run_id}`,
  `rag://evaluations/{evaluation_run_id}/summary`, and `rag://strategies`.
- MCP prompts include hybrid debug, agentic answer-with-citations, and strategy
  comparison review templates, and they avoid destructive/admin write
  instructions.
- Upload, archive, approve, retry, remote MCP, OAuth, raw chunk text, full
  context, tokens, secrets, and local paths remain absent from MCP output.

## PR-40 Context Budget / Trace / Debug Tests

- `ContextBudgetPolicy` validates enabled flag, max context tokens, answer
  reserve, max context items, max tokens per item, minimum citation candidates,
  source-diversity toggle, and heuristic estimator.
- Token estimation is deterministic and uses `ceil(char_count / 4)` without
  model downloads or heavyweight tokenizer dependencies.
- `ContextBudgetManager` selects within budget, drops over-budget items, drops
  items beyond max item count, preserves source diversity when enabled, counts
  citation candidates, counts sources, and records drop reason counts.
- `reserve_answer_tokens` is subtracted from the effective context token limit.
- After generation context assembly applies the existing char cap, persisted
  selected refs match the context actually passed to generation.
- `min_citation_candidates` promotes additional candidates only when budget and
  max item limits allow.
- `context_budget_json` stores only safe refs and bounded count metadata. It
  does not store raw prompt, full context, raw chunk text, snippets, raw tool
  results, PII, token values, secrets, credentials, cookies, sessions, or local
  paths.
- `/rag/ask` dense, hybrid, `agentic_router`, and `llm_tool_orchestrator` runs
  persist context budget trace before generation.
- If retrieval completed but budget selection leaves no context, the failed
  `no_context_found` run still stores safe context budget metadata.
- Generation and citation failure paths retain safe context budget metadata when
  retrieval and budget selection completed.
- Persisted top retrieval/rerank scores are recomputed from final budget-selected
  context refs rather than pre-budget candidates.
- Admin retrieval-run detail includes safe `context_budget_json`; viewer access
  remains `403`, and missing run remains `404`.
- Admin Retrieval Debug renders the Context Budget panel, selected/dropped
  counts, drop reasons, budget exhausted state, source breakdown, and selected /
  dropped safe refs.
- Viewer chat UI does not render internal context budget debug.
- Safe structured logs use only allowed fields: request ID, retrieval run ID,
  strategy labels, candidate/selected/dropped counts, estimated context tokens,
  remaining context tokens, exhausted flag, and drop reason counts.

## Checks

- `ruff format --check .`
- `ruff check .`
- `mypy .`
- backend pytest
- frontend lint / typecheck / test / build when frontend files change
- Docker compose CI config and smoke when available
