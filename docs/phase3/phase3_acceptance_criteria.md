# Phase3 Acceptance Criteria

This file defines the PR-54 GraphRAG handoff checks and separates them from
later Phase3 expansion work. PR-45 established the original criteria; PR-54
uses the implemented GraphRAG scope as the portfolio-demo completion boundary.

## PR-45 Acceptance

| Check | Expected evidence |
|---|---|
| `docs/phase3/` exists | Phase3 docs are committed. |
| Roadmap exists | `phase3_roadmap.md` lists PR-45 through PR-54 and PR-55+ future candidates. |
| Graph-RAG architecture exists | `graph_rag_architecture.md` explains Phase2 relationship, Auto integration, and non-goals. |
| Schema draft exists | `graph_schema_draft.md` lists graph table candidates and migration plan. |
| Extraction/index/retrieval/router/citation/evaluation designs exist | Dedicated docs are present and cross-linked. |
| OCR/multimodal/production boundaries exist | Boundary docs explicitly defer implementation. |
| Security policy exists | `security_redaction_policy.md` states forbidden outputs and safe trace fields. |
| Normal CI is not broken | CI succeeds for docs-only PR. |

## PR-54 GraphRAG Acceptance

| Check | Status meaning | Evidence |
|---|---|---|
| GraphRAG README path is clear | Required | Root `README.md`, `docs/phase3/README.md`, and `graph_rag_final_readme.md` route a reviewer through setup, demo, security, limitations, and handoff. |
| Local PostgreSQL GraphRAG demo is reproducible | Required | `graph_rag_demo_scenario.md` gives concrete compose, env, graph index queue, query, debug, and expected-result steps. |
| Neo4j remains optional | Required | `neo4j_optional_backend.md` and demo docs show the `neo4j` profile, optional dependency extra, projection settings, and fallback to PostgreSQL when absent. |
| PostgreSQL source of truth is explicit | Required | Architecture docs explain that Neo4j is a rebuildable read model/projection only. |
| Graph index queue helper is safe | Required | Helper queues active ready document versions and prints IDs/counts only. |
| Cache behavior is explained | Required | PR-52 cache docs and final README explain query hashes, graph fingerprint, graph store provider split, disabled default, and payload exclusions. |
| Evaluation behavior is explained | Required | PR-53 evaluation docs and final README explain dense/hybrid/agentic_router/graph_postgres/graph_neo4j targets, optional Neo4j projection, visible Postgres graph fallback, and safe reason-code handling. |
| Debug/citation behavior is explained | Required | Graph citation/debug docs explain source chunk-backed citations and admin-safe graph trace fields. |
| Dense/hybrid/graph guidance exists | Required | Final README explains when to use each implemented strategy and states `graph_hybrid` is future work. |
| Manual test cases exist | Required | `graph_rag_manual_test_cases.md` lists reproducible checks and a minimum acceptance set. |
| Acceptance checklist exists | Required | `graph_rag_acceptance_checklist.md` marks the PR-54 readiness surface. |
| Known limitations and PR-55+ handoff exist | Required | `graph_rag_known_limitations.md` and `graph_rag_next_phase_handoff.md` separate future work from completed GraphRAG. |
| Raw text / PII / secrets not exposed | Required | Docs, tests, smoke, and self-review show no unsafe docs, logs, traces, artifacts, UI, or MCP output. |
| Lightweight smoke exists | Required | `scripts/smoke_phase3_graph_rag.*` checks compose/docs/helper/fixture expectations without destructive actions or secret output. |
| Existing behavior is not regressed | Required | Graph/cache/Neo4j remain opt-in and default compose does not require Neo4j. |

## Later Phase3 Expansion Acceptance

| Check | Status meaning | Evidence |
|---|---|---|
| Public `graph_hybrid` fusion strategy works | Future | Separate design and tests merge graph path evidence with dense/sparse/hybrid candidates. |
| OCR ingest works | Future OCR milestone | Scanned PDF/image OCR creates bounded region metadata and source locators. |
| Image upload works | Future multimodal milestone | Image lifecycle, validation, metadata, and admin review path work. |
| Multimodal citation UI works | Future multimodal milestone | Viewer-safe citation panel can navigate OCR/image regions. |
| External LLM provider optional | Future optional | Adapter works only when explicitly configured. |
| S3 storage adapter optional | Future optional | Local storage remains default; S3 adapter is opt-in. |
| OIDC / OAuth optional | Future optional | Local auth remains demo-capable; external identity is opt-in. |
| AWS deploy foundation documented | Future production expansion | deploy/aws or Phase3 docs separate AWS from `k8s/local`. |
| Online evaluation / A-B / alerting | Future production expansion | Production-like loops, budgets, alerting, and redacted reports are separately reviewed. |
| Redis retrieval cache | Future optional | In-memory/local DB cache remains sufficient until a separate Redis design is accepted. |

## Evidence Quality

A check is not accepted by intent alone. It needs current evidence from code, migration files, tests, docs, CI, runtime behavior, or PR review. Narrow tests cannot prove broad behavior unless their coverage is explicit.
