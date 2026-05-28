# Phase2 README

## Purpose

Phase2 extends the Phase1 dense RAG baseline with four central themes:

- Advanced Retrieval
- Agentic Control
- Evaluation
- Observability

PR-20 fixed the strategy and trace schema baseline. PR-21 connected safe trace recording to the existing dense `/rag/search` and `/rag/ask` flows. PR-22 adds dataset, case, and strategy metric schema management so later PRs can compare dense / sparse / hybrid / agentic_router on the same dataset. PR-23 adds standalone sparse lexical retrieval for `/rag/search`. PR-24 adds standalone hybrid dense+sparse retrieval and score fusion for `/rag/search`. PR-25 adds the deterministic strategy evaluation runner for dense / sparse / hybrid. PR-28 adds explicit `agentic_router` routing for one retrieval call with safe dense fallback. PR-29 adds the bounded agentic retrieval loop, PR-30 adds agentic strategy evaluation plus failure dataset promotion, and PR-31 adds lightweight CI retrieval evaluation smoke runs.

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
- deterministic fake-mode execution using the existing Strategy Evaluation Runner
- `backend/app/scripts/retrieval_eval_smoke.py`
- local wrapper scripts for PowerShell and Unix-like shells
- JSON and Markdown artifacts
- GitHub step summary output
- configurable warn/fail threshold checks

The default strategy set is `dense,hybrid,agentic_router` to keep the smoke short. `sparse` can be included manually. The default workflow does not require GitHub secrets, external LLM/API keys, BAAI model downloads, GPU, LangSmith, online evaluation, Graph-RAG, or OCR.

## Non-goals

PR-31 does not implement LangSmith export, SentenceTransformers experiments, online evaluation, production trace sampling, external LLM/API-required evaluation, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

## Security

Phase2 docs, DB schema, DTOs, API responses, and UI must not store or display raw prompt, full context, raw chunk text, PII, secret, token, credential, API key, or password. Dataset import validation rejects secret-like and PII-like values.
