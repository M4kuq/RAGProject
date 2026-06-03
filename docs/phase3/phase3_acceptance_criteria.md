# Phase3 Acceptance Criteria

This file defines the end-state checks for Phase3. PR-45 only establishes these criteria; it does not implement them.

## PR-45 Acceptance

| Check | Expected evidence |
|---|---|
| `docs/phase3/` exists | Phase3 docs are committed. |
| Roadmap exists | `phase3_roadmap.md` lists PR-45 through PR-59 or justified equivalent. |
| Graph-RAG architecture exists | `graph_rag_architecture.md` explains Phase2 relationship, Auto integration, and non-goals. |
| Schema draft exists | `graph_schema_draft.md` lists graph table candidates and migration plan. |
| Extraction/index/retrieval/router/citation/evaluation designs exist | Dedicated docs are present and cross-linked. |
| OCR/multimodal/production boundaries exist | Boundary docs explicitly defer implementation. |
| Security policy exists | `security_redaction_policy.md` states forbidden outputs and safe trace fields. |
| Normal CI is not broken | CI succeeds for docs-only PR. |

## Full Phase3 Acceptance

| Check | Status meaning | Evidence |
|---|---|---|
| Graph schema implemented | Required | Alembic migration, ORM/repository tests, rollback notes. |
| Entity / relation extraction implemented | Required | Worker job, extractor interface, deterministic tests, source mapping. |
| Graph index build job works | Required | `graph_index_runs` lifecycle and retry behavior verified. |
| Graph retrieval works | Required | `graph_entity_lookup`, relation traversal, and path search tests. |
| Graph-aware Router can select graph strategy | Required | Router tests for multi-hop, relation, entity comparison, and fallback. |
| Graph + vector hybrid works | Required | Hybrid strategy returns source chunk-backed evidence. |
| Graph citations map back to source chunks | Required | Node, edge, and path citations validate against retrieval run items. |
| Graph Debug UI works | Required | Admin-only safe graph panels show counts, path refs, scores, and warnings. |
| Graph evaluation works | Required | Metrics for extraction, relation accuracy, path relevance, and citation coverage. |
| OCR ingest works | Required by OCR milestone | Scanned PDF/image OCR creates bounded region metadata and source locators. |
| Image upload works | Required by multimodal milestone | Image lifecycle, validation, metadata, and admin review path work. |
| Multimodal citation UI works | Required by multimodal milestone | Viewer-safe citation panel can navigate OCR/image regions. |
| External LLM provider optional | Optional | Adapter works only when explicitly configured. |
| S3 storage adapter optional | Optional | Local storage remains default; S3 adapter is opt-in. |
| OIDC / OAuth optional | Optional | Local auth remains demo-capable; external identity is opt-in. |
| AWS deploy foundation documented | Required for production expansion | deploy/aws or Phase3 docs separate AWS from `k8s/local`. |
| Raw text / PII / secrets not exposed | Required | Tests and review show no unsafe docs, logs, traces, artifacts, UI, or MCP output. |
| Phase3 demo reproducible | Required | Demo docs, smoke path, acceptance checklist, and known limitations exist. |

## Evidence Quality

A check is not accepted by intent alone. It needs current evidence from code, migration files, tests, docs, CI, runtime behavior, or PR review. Narrow tests cannot prove broad behavior unless their coverage is explicit.
