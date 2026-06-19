# GraphRAG Acceptance Checklist

Use this checklist for PR-54 final review. `Status` should be updated by the
human reviewer during acceptance. Evidence must use safe summaries, file links,
command results, CI checks, screenshots, or review notes. Do not paste raw
payload dumps, private text, secrets, credentials, or `.env` values.

| Check | Status | Evidence | Notes |
|---|---|---|---|
| GraphRAG entry point is clear | Ready | `README.md`, `docs/phase3/README.md`, `graph_rag_final_readme.md` | README can route the demo. |
| Graph schema and index run lifecycle documented | Ready | `graph_schema_draft.md`, `graph_indexing_design.md` | PostgreSQL source-of-truth model. |
| Entity/relation extraction documented | Ready | `entity_relation_extraction_design.md` | Stores IDs, hashes, offsets, labels, counts. |
| Graph index build job can be queued for demo | Ready | `backend/app/scripts/queue_graph_index_builds.py` | Local helper only; no public API change. |
| Graph retrieval strategy documented | Ready | `graph_retrieval_strategy.md` | Explicit `graph`, bounded traversal, fallback boundary. |
| Dense/hybrid/graph usage guidance exists | Ready | `graph_rag_final_readme.md` | `graph_hybrid` remains future. |
| Graph-aware router boundary documented | Ready | `graph_aware_router_design.md`, final README | Graph router is opt-in. |
| Graph citation bridge documented | Ready | `graph_citation_debug_pr51.md` | Citations remain chunk-backed. |
| Graph debug surface documented | Ready | `graph_rag_demo_scenario.md`, `graph_citation_debug_pr51.md` | Admin-only safe graph trace. |
| PostgreSQL vs Neo4j architecture explained | Ready | final README, `neo4j_optional_backend.md` | Neo4j is read model/projection only. |
| Neo4j optional demo is reproducible | Ready | `graph_rag_demo_scenario.md`, `neo4j_optional_backend.md` | Requires local password in shell, not committed. |
| Cache behavior is explained | Ready | `retrieval_cache_foundation.md`, final README | refs/hashes only; provider and graph fingerprint in key. |
| Evaluation summary is explained | Ready | `graph_evaluation_design.md`, final README | PR-53 targets and safe metrics. |
| Manual test cases exist | Ready | `graph_rag_manual_test_cases.md` | Includes minimum acceptance set. |
| Known limitations are explicit | Ready | `graph_rag_known_limitations.md` | No OCR/multimodal/provider/S3/OIDC/AWS. |
| PR-55+ handoff exists | Ready | `graph_rag_next_phase_handoff.md` | Next work is separated from PR-54. |
| Smoke path exists | Ready | `scripts/smoke_phase3_graph_rag.*` | Non-destructive and external-provider-free. |
| Default behavior is not regressed | Ready | Compose defaults false, tests | Graph/cache/Neo4j remain opt-in. |
| Raw text and secret non-storage policy is clear | Ready | `security_redaction_policy.md`, final README | Safety invariant repeated in demo/checklist. |
| Docs do not include real secrets or raw private payloads | Ready | self-review and scans | Placeholder env names only. |
| PR scope does not add new retrieval strategy/metric | Ready | diff review | PR-54 is hardening/demo/docs. |

## Completion Rule

GraphRAG is considered portfolio-demo complete at PR-54 when:

- the local PostgreSQL GraphRAG path can be explained and smoke-checked
- graph index jobs can be prepared for demo data
- graph trace, cache, and evaluation behavior are documented consistently
- Neo4j optional setup is clearly non-default and non-blocking
- redaction and raw text non-storage rules are explicit
- PR-55+ items are separated as future work
