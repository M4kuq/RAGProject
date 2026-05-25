# Phase2 README

## Purpose

Phase2 extends the Phase1 dense RAG baseline with four central themes:

- Advanced Retrieval
- Agentic Control
- Evaluation
- Observability

PR-20 fixed the strategy and trace schema baseline. PR-21 connected safe trace recording to the existing dense `/rag/search` and `/rag/ask` flows. PR-22 adds dataset, case, and strategy metric schema management so later PRs can compare dense / sparse / hybrid / agentic_router on the same dataset.

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

## Non-goals

PR-22 does not implement Sparse Retrieval, Hybrid Retrieval, Strategy Evaluation Runner, Agentic Router, CI evaluation workflow, LangSmith export, SentenceTransformers experiments, Graph-RAG, OCR, AWS, S3, or OIDC/OAuth.

## Security

Phase2 docs, DB schema, DTOs, API responses, and UI must not store or display raw prompt, full context, raw chunk text, PII, secret, token, credential, API key, or password. Dataset import validation rejects secret-like and PII-like values.
