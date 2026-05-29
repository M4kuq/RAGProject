# Phase2 README

## Purpose

Phase2 extends the Phase1 dense RAG baseline with four central themes:

- Advanced Retrieval
- Agentic Control
- Evaluation
- Observability

PR-20 fixed the strategy and trace schema baseline. PR-21 connected safe trace recording to the existing dense `/rag/search` and `/rag/ask` flows. PR-22 adds dataset, case, and strategy metric schema management so later PRs can compare dense / sparse / hybrid / agentic_router on the same dataset. PR-23 adds standalone sparse lexical retrieval for `/rag/search`. PR-24 adds standalone hybrid dense+sparse retrieval and score fusion for `/rag/search`. PR-25 adds the deterministic strategy evaluation runner for dense / sparse / hybrid. PR-26 adds Retrieval Debug UI v2. PR-27 adds Query Analyzer / Query Planner. PR-28 adds explicit `agentic_router` routing for one retrieval call with safe dense fallback. PR-29 adds the bounded agentic retrieval loop, PR-30 adds agentic strategy evaluation plus failure dataset promotion, PR-31 adds lightweight CI retrieval evaluation smoke runs, PR-32 adds optional no-op-by-default external trace export, PR-33 adds a local opt-in SentenceTransformers experiment harness, PR-34 adds `.xlsx` / `.pptx` ingestion with metadata-only parent-child chunking, PR-35 adds `.html` / `.htm` / `.xml` file ingestion plus single-URL ingestion behind an SSRF guard, PR-36 adds safe document version compare plus citation source navigation, and PR-37 finalizes demo, acceptance, smoke, and Phase3 handoff documentation.

## PR Plan

| PR | Scope |
|---:|---|
| PR-20 | Phase2 Design Baseline / Strategy & Evaluation Schema |
| PR-21 | Retrieval Trace Foundation / Observability Schema |
| PR-22 | Evaluation Dataset Management / Strategy Metrics Schema |
| PR-23 | Sparse Retrieval / BM25 Index |
| PR-24 | Hybrid Retrieval / Score Fusion |
| PR-25 | Strategy Evaluation Runner |
| PR-26 | Retrieval Debug UI v2 |
| PR-27 | Query Analyzer / Query Planner |
| PR-28 | Strategy Router / Agentic Retrieval Control |
| PR-29 | Agentic Retrieval Loop / Context Sufficiency Check |
| PR-30 | Agentic Strategy Evaluation / Failure Dataset Promotion |
| PR-31 | CI Retrieval Evaluation / Scheduled Smoke |
| PR-32 | LangSmith Optional Adapter / Trace Export |
| PR-33 | SentenceTransformers Experiment Harness |
| PR-34 | Advanced Import: Excel / PowerPoint / Parent-child Chunk |
| PR-35 | Advanced Import: HTML / XML / URL + SSRF Guard |
| PR-36 | Document Diff / Citation Navigation / Version Compare |
| PR-37 | Phase2 Final Hardening / Demo / Docs |
| PR-38 | MCP Tools for Hybrid / Agentic RAG |

## Phase2 Final Docs

Use these files for final handoff and demo validation:

- [Phase2 demo scenario](phase2_demo_scenario.md)
- [Phase2 manual test cases](phase2_manual_test_cases.md)
- [Phase2 acceptance checklist](phase2_acceptance_checklist.md)
- [Phase2 known limitations](phase2_known_limitations.md)
- [Phase3 handoff](phase3_handoff.md)
- [Manual acceptance notes template](phase2_manual_acceptance_notes.md)

## Feature Docs Index

- [Architecture delta](architecture_delta.md)
- [Retrieval strategy schema](retrieval_strategy_schema.md)
- [Retrieval trace foundation](retrieval_trace_foundation.md)
- [Evaluation dataset management](evaluation_dataset_management.md)
- [Sparse retrieval](sparse_retrieval.md)
- [Hybrid retrieval](hybrid_retrieval.md)
- [Strategy evaluation runner](strategy_evaluation_runner.md)
- [Retrieval Debug UI v2](retrieval_debug_ui_v2.md)
- [Query Analyzer / Planner](query_analyzer_planner.md)
- [Strategy Router](strategy_router.md)
- [Agentic retrieval loop](agentic_retrieval_loop.md)
- [Agentic strategy evaluation](agentic_strategy_evaluation.md)
- [CI retrieval evaluation](ci_retrieval_evaluation.md)
- [LangSmith optional adapter](langsmith_optional_adapter.md)
- [SentenceTransformers experiment harness](sentence_transformers_experiment_harness.md)
- [Advanced import: Office](advanced_import_office.md)
- [Parent-child chunking](parent_child_chunking.md)
- [Advanced import: HTML / XML / URL](advanced_import_html_xml_url.md)
- [SSRF guard](ssrf_guard.md)
- [Document diff / version compare](document_diff_version_compare.md)
- [Citation navigation](citation_navigation.md)
- [MCP advanced RAG tools](mcp_advanced_rag_tools.md)
- [Phase2 test strategy](test_strategy.md)
- [PR-by-PR acceptance criteria](acceptance_criteria.md)

## Local Setup And Smoke

Local setup follows the repository root README and Docker Compose files. For
Phase2-specific checks from the repository root:

```powershell
scripts/smoke_phase2.ps1
scripts/smoke_phase2.ps1 -RunExperimentDryRun
```

```sh
sh scripts/smoke_phase2.sh
sh scripts/smoke_phase2.sh --run-experiment-dry-run
```

The basic smoke validates compose configuration, Phase2 final docs, key
fixtures, and optional running health endpoints. `-Deep` / `--deep` additionally
requires running local services and performs safe admin searches across
`dense`, `sparse`, `hybrid`, and `agentic_router`; set
`SMOKE_ADMIN_EMAIL` and `SMOKE_ADMIN_PASSWORD` in your shell for that local-only
deep path. The smoke scripts do not run destructive cleanup, do not print
secrets, and do not require external API keys, LangSmith, GPU, or heavy model
downloads by default.

## PR-20 Baseline

PR-20 adds:

- `RetrievalStrategy` / `RetrievalSource` / `FusionMethod` / `RouterFallbackStrategy`
- retrieval trace columns on `retrieval_runs`
- source and score breakdown columns on `retrieval_run_items`
- redacted trace DTOs and retrieval settings DTOs
- Phase2 retrieval system settings

The default strategy remains `dense`.

## PR-21 Trace Foundation

PR-21 stores `phase2.trace.v1` safe trace metadata for existing dense retrieval:

- query plan hash and safe counts
- default dense strategy decision
- retrieval settings snapshot
- latency breakdown
- item source and score breakdown

Raw query, raw prompt, full context, raw chunk text, PII, and secrets are not stored or returned.

## PR-22 Evaluation Dataset Management

PR-22 adds:

- `evaluation_datasets`
- `evaluation_cases`
- strategy-aware fields on `evaluation_runs`, `evaluation_run_items`, and `evaluation_results`
- strategy metric specs
- JSON manifest import/export
- admin-only dataset/case API
- minimal Evaluation UI connection for dataset selection, case listing, strategy display, and export

PR-22 keeps the existing minimal evaluation runner default dense. Non-dense strategy execution is left for PR-25.

## PR-23 Sparse Retrieval

PR-23 adds:

- `SparseRetrievalStrategy`
- PostgreSQL full-text search over `document_chunks`
- `ix_document_chunks_content_fts` GIN expression index
- SQLite deterministic BM25 fallback for tests
- `/api/v1/rag/search` `strategy=sparse`
- sparse retrieval trace and score breakdown persistence

`/rag/ask` remains default dense in PR-23. Hybrid fusion is left for PR-24.

## PR-24 Hybrid Retrieval

PR-24 adds:

- `HybridRetrievalStrategy`
- `rag/fusion.py` with RRF and weighted fusion
- dense+sparse candidate collection with deterministic dedupe
- `/api/v1/rag/search` `strategy=hybrid`
- `retrieval_runs.strategy_type = hybrid`
- `retrieval_run_items.retrieval_source = hybrid`
- `score_breakdown_json` with dense, sparse, fused score and rank metadata
- hybrid trace fields for query plan, decision, settings, latency, and score breakdown

`/rag/ask` remains default dense in PR-24. QueryAnalyzer, StrategyRouter, Agentic Retrieval Loop, Debug UI v2, Strategy Evaluation Runner, LangSmith export, and SentenceTransformers experiments remain later PRs.

## PR-25 Strategy Evaluation Runner

PR-25 adds:

- `POST /api/v1/evaluations/runs` strategy lists via `strategies`
- one evaluation item per case per strategy
- dense / sparse / hybrid execution through the existing safe retrieval path
- deterministic `recall_at_k`, `mrr`, `citation_coverage`, `groundedness`, `faithfulness`, `no_context_rate`, and `p95_latency`
- `strategy_selection_accuracy` as not-applicable until `agentic_router`
- `GET /api/v1/evaluations/runs/{evaluation_run_id}/strategy-comparison`
- minimal Evaluation UI display for selected strategies and strategy summaries

PR-25 does not implement QueryAnalyzer, StrategyRouter, Agentic Retrieval Loop, CI evaluation workflow, LangSmith export, SentenceTransformers experiments, or full dashboard charts.

## PR-26 Retrieval Debug UI v2

PR-26 adds:

- admin route `/admin/retrieval-debug`
- strategy selector for `dense`, `sparse`, and `hybrid`
- disabled coming-soon display for router and multi-query strategies
- safe trace display for query plan, strategy decision, retrieval settings, latency, and score summary
- score breakdown and retrieval-run item tables with dense / sparse / fused / rerank scores
- minimal strategy evaluation summary display from recent evaluation runs
- backend read-only retrieval run detail endpoint for safe admin trace inspection

PR-26 does not implement QueryAnalyzer, StrategyRouter, Agentic Retrieval Loop, LangSmith export, SentenceTransformers experiments, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

## PR-27 Query Analyzer / Query Planner

PR-27 adds:

- deterministic rule-based `QueryAnalyzer`
- deterministic rule-based `QueryPlanner`
- intent classification
- ambiguity, keyword-heavy, temporal, and version-specific signals
- safe query rewrite metadata
- planned sub-query previews
- structured metadata filter candidates
- candidate strategy and recommended strategy proposals
- query-plan trace integration for `/api/v1/rag/search` and `/api/v1/rag/ask`
- Retrieval Debug UI display for analysis and planning summary

PR-27 does not execute StrategyRouter, Agentic Retrieval Loop, context sufficiency checks, multi-query retrieval, metadata-filtered retrieval, version-aware retrieval, LLM planning, LangSmith export, Graph-RAG, OCR, or external operation agents.

## PR-28 Strategy Router / Agentic Retrieval Control

PR-28 adds:

- deterministic rule-based `StrategyRouter`
- `RouterDecisionTrace` persisted in `retrieval_runs.strategy_decision_json`
- `/api/v1/rag/search` `strategy=agentic_router`
- `/api/v1/rag/ask` explicit `strategy=agentic_router`
- one selected execution strategy per request: `dense`, `sparse`, `hybrid`, or `fallback_dense`
- safe fallback to dense when router is disabled, fails, or selects an unavailable strategy
- `strategy_router_ms` latency span
- Retrieval Debug UI display for requested strategy, selected strategy, execution strategy, fallback, confidence, reason codes, disabled candidates, and safety flags

PR-28 stores `retrieval_runs.strategy_type = agentic_router` for router-triggered runs and stores the actual execution strategy in `strategy_decision_json.execution_strategy`. It does not implement Agentic Retrieval Loop, context sufficiency checks, additional retrieval calls, multi-query execution, metadata-filtered execution, version-aware execution, LLM router execution, LangSmith export, Graph-RAG, OCR, or external operation agents.

## PR-29 Agentic Retrieval Loop / Context Sufficiency Check

PR-29 adds:

- bounded `AgenticRetrievalExecutor`
- deterministic `ContextSufficiencyChecker`
- retrieval budget settings with default `max_retrieval_calls = 2`
- fallback retrieval when the first router-selected result is insufficient
- merge and dedupe by `document_chunk_id`
- rerank after merged candidates
- safe sufficiency, fallback, retrieval-call-count, budget, and latency trace fields
- `/api/v1/rag/search strategy=agentic_router` loop execution
- `/api/v1/rag/ask strategy=agentic_router` opt-in loop execution with `no_context_found` after budget exhaustion
- Retrieval Debug UI display for fallback, sufficiency, and loop latency fields

PR-29 still does not implement multi-query execution, metadata-filtered execution, version-aware retrieval execution, Graph-RAG, OCR, multi-agent architecture, external operation agents, LangSmith export, or SentenceTransformers experiments.

## PR-30 Agentic Strategy Evaluation / Failure Dataset Promotion

PR-30 adds:

- `agentic_router` as an evaluation strategy beside `dense`, `sparse`, and `hybrid`
- agentic metrics: `strategy_selection_accuracy`, `fallback_rate`, `budget_exhausted_rate`, `sufficiency_score_avg`, and `retrieval_call_count_avg`
- failure candidate extraction from no-context, low score, citation, strategy mismatch, budget, latency, and safe exception signals
- idempotent failure promotion back into active evaluation datasets
- minimal Evaluation UI display for agentic summary, failure candidates, and promotion results

PR-30 does not implement CI evaluation workflow, LangSmith export, SentenceTransformers experiments, online evaluation, LLM-as-a-Judge, Graph-RAG, OCR, or external operation agents.

## PR-31 CI Retrieval Evaluation / Scheduled Smoke

PR-31 adds:

- `.github/workflows/retrieval-eval-smoke.yml`
- manual `workflow_dispatch` inputs for dataset, strategies, mode, threshold behavior, and case limit
- weekly low-frequency scheduled smoke execution
- real local retrieval execution using the existing Strategy Evaluation Runner
- `backend/app/scripts/retrieval_eval_smoke.py`
- local wrapper scripts for PowerShell and Unix-like shells
- JSON and Markdown artifacts
- GitHub step summary output
- configurable warn/fail threshold checks

The default strategy set is `dense,hybrid,agentic_router` to keep the smoke short. `sparse` can be included manually. The default workflow does not require GitHub secrets, external LLM/API keys, BAAI/heavyweight model downloads, GPU, LangSmith, online evaluation, Graph-RAG, or OCR.
The workflow caches a small local embedding model, does not exercise answer generation, and does not fall back to fake embedding, reranker, or evaluator behavior; missing local retrieval prerequisites are reported as a safe `blocked` artifact.

## PR-32 LangSmith Optional Adapter / Trace Export

PR-32 adds:

- provider-neutral `TraceExporter` interface
- `NoOpTraceExporter` as the default behavior
- optional lazy LangSmith adapter
- minimized retrieval trace payloads for `/api/v1/rag/search` and `/api/v1/rag/ask`
- minimized strategy evaluation summary export
- optional PR-31 CI retrieval smoke summary export hook
- export redaction for raw prompts, full context, raw chunk text, PII, secrets, tokens, paths, and raw payload dumps
- non-fatal export failure handling

PR-32 does not require LangSmith credentials, external trace export, heavy model downloads, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth. Normal CI remains secret-free and external-export-free.

## Non-goals

PR-32 does not implement SentenceTransformers experiments, online evaluation, production trace sampling, external LLM/API-required evaluation, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

## PR-33 SentenceTransformers Experiment Harness

PR-33 adds:

- `backend/app/experiments` with manifest schema, model registry, availability checks, runner, and report generation
- `backend/app/experiments/manifests/phase2_retrieval_models.example.json`
- local wrapper scripts for dry-run and local opt-in execution
- JSON and Markdown experiment artifacts under `artifacts/experiments`
- optional `backend[experiments]` dependency extra for SentenceTransformers

The default command is dry-run and does not download models. Local mode uses
cached public SentenceTransformers models by default, can opt into downloads only
with `DownloadPolicy=opt-in-download`, and indexes deterministic seed documents
into experiment-specific Qdrant collections before calling the existing Strategy
Evaluation Runner through the PR-31 retrieval smoke path.

PR-33 does not implement fine-tuning, production model cutover, required CI heavy
model downloads, GPU-required evaluation, external API-required evaluation,
Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

## PR-34 Advanced Import: Excel / PowerPoint / Parent-child Chunk

PR-34 adds:

- upload validation for `.xlsx` and `.pptx`
- rejection of legacy, macro-enabled, embedded-object, encrypted, and unsafe
  Office files
- Excel extraction with visible-sheet, row, column, table, and parent metadata
- PowerPoint extraction with slide, title, shape/table, and parent metadata
- metadata-only parent-child chunking v1
- source labels for sheet/row and slide/title citations

PR-34 does not implement legacy `.xls` / `.ppt`, OCR, speaker-note extraction,
visual slide rendering, embedded object extraction, HTML/XML/URL ingest,
Graph-RAG, AWS, S3, or OIDC/OAuth.

## PR-35 HTML / XML / URL Ingest

PR-35 adds:

- upload validation and extraction for `.html`, `.htm`, and `.xml`
- safe HTML extraction that ignores script, style, iframe, object, embed, comments, and SVG
- safe XML extraction that rejects DTD / entity declarations and SVG
- `POST /api/v1/documents/url` for admin-only single URL ingestion
- SSRF guard checks for scheme, userinfo, DNS-resolved IPs, localhost, private/link-local/metadata IPs, redirects, timeout, max bytes, and content type
- safe `source_url` / `final_url` metadata on `document_versions.metadata_json`
- heading / XML path metadata on `document_chunks.metadata_json`

PR-35 does not implement crawling, recursive web ingest, authenticated URL fetch,
cookies, JavaScript rendering, headless browsing, OCR, image upload, multimodal
retrieval, Graph-RAG, AWS, S3, or OIDC/OAuth. CI tests use fixtures and mock HTTP
transports, not real external network access.

## PR-36 Document Diff / Citation Navigation

PR-36 adds:

- admin-only document version compare API and UI
- metadata diff over safe version fields only
- chunk diff summary with added / removed / changed / unchanged counts
- bounded chunk diff previews
- citation source locator API for authenticated users
- Chat citation "View source" preview
- admin-only deep link from source preview to document detail
- source URL redaction and old-version warning preservation

PR-36 does not implement unbounded full-text diff, raw chunk exposure, PDF page
image rendering, DOCX/PPTX visual rendering, OCR region navigation, Graph-RAG,
AWS, S3, or OIDC/OAuth.

## PR-37 Final Hardening / Demo / Docs

PR-37 adds final Phase2 handoff material rather than a new retrieval feature:

- demo scenario for Advanced Retrieval, Agentic-RAG, evaluation, debug,
  observability, advanced import, document diff, and citation navigation
- manual test cases grouped by Phase2 area
- acceptance checklist with safe evidence guidance
- known limitations and Phase3 handoff
- safe Phase2 smoke wrappers for Windows and Unix-like shells

The final docs and smoke scripts must not include `.env` values, real secrets,
raw prompts, full context, raw chunk text, private document text, PII, or
destructive cleanup commands. External trace export and model downloads remain
explicit opt-in.

## Security

Phase2 docs, DB schema, DTOs, API responses, and UI must not store or display raw prompt, full context, raw chunk text, PII, secret, token, credential, API key, or password. Dataset import validation rejects secret-like and PII-like values.
