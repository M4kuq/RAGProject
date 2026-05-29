# Phase2 Acceptance Checklist

Use this checklist for the Phase2 final review. `Status` should be updated by
the human reviewer during acceptance. Evidence should point to current files,
test runs, GitHub checks, screenshots, or manual notes, not to raw payload dumps.

| Check | Status | Evidence | Notes |
|---|---|---|---|
| dense retrieval works | Ready | `/api/v1/rag/search`, backend RAG tests | Default strategy remains dense. |
| sparse retrieval works | Ready | `docs/phase2/sparse_retrieval.md`, sparse tests | PostgreSQL FTS plus test fallback. |
| hybrid retrieval works | Ready | `docs/phase2/hybrid_retrieval.md`, hybrid tests | Dense+sparse fusion with score breakdown. |
| agentic_router works | Ready | `docs/phase2/strategy_router.md`, agentic tests | Explicit opt-in for search/ask. |
| context sufficiency works | Ready | `docs/phase2/agentic_retrieval_loop.md` | Rule-based, bounded checker. |
| fallback works | Ready | Router/agentic tests | Dense fallback is safe and deterministic. |
| query_plan_json saved | Ready | Retrieval trace/debug docs | Safe query analysis/planning only. |
| strategy_decision_json saved | Ready | Router/agentic docs | Includes selected/execution strategy and fallback fields. |
| latency_breakdown_json saved | Ready | Trace/debug docs | Includes retrieval/router/agentic spans. |
| score_breakdown_json saved | Ready | Hybrid/agentic docs | No raw chunk text. |
| Debug UI displays strategy / score / latency | Ready | `docs/phase2/retrieval_debug_ui_v2.md` | Admin-only. |
| evaluation compares strategies | Ready | `docs/phase2/strategy_evaluation_runner.md` | Dense/sparse/hybrid/agentic_router. |
| failure cases can be promoted | Ready | `docs/phase2/agentic_strategy_evaluation.md` | Idempotent promotion. |
| CI retrieval evaluation can run | Ready | `.github/workflows/retrieval-eval-smoke.yml` | Manual dispatch and optional schedule. |
| LangSmith optional adapter is no-op by default | Ready | `docs/phase2/langsmith_optional_adapter.md` | No secret required by default. |
| SentenceTransformers experiment dry-run works | Ready | `docs/phase2/sentence_transformers_experiment_harness.md` | No model download by default. |
| Excel / PowerPoint ingest works | Ready | `docs/phase2/advanced_import_office.md` | Metadata-only parent-child chunks. |
| HTML / XML / URL ingest works with SSRF guard | Ready | `docs/phase2/advanced_import_html_xml_url.md` | Local/mock tests; no crawler. |
| document diff works | Ready | `docs/phase2/document_diff_version_compare.md` | Admin-only bounded diff. |
| citation navigation works | Ready | `docs/phase2/citation_navigation.md` | Viewer-safe bounded source preview. |
| raw prompt / chunk / context not exposed | Ready | Security tests and redaction docs | Do not paste raw content into evidence. |
| destructive commands are not automatic | Ready | `scripts/smoke_phase2.*` | No `docker compose down -v` by default. |
| heavy model download is optional | Ready | PR-31/PR-33 docs | CI smoke blocks clearly if prerequisites missing. |
| external export is optional | Ready | PR-32 docs | Disabled/no-op unless explicitly configured. |
| Phase3 scope is clear | Ready | `docs/phase2/phase3_handoff.md` | Graph/OCR/multimodal/AWS/OIDC deferred. |
