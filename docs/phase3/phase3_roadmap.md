# Phase3 Roadmap

Phase3 first stabilizes text GraphRAG on the local stack, then leaves OCR,
multimodal input, provider expansion, production deployment, and online
evaluation as explicit follow-up work. PR-54 is the GraphRAG final
hardening/demo-docs boundary.

## PR Plan

| PR | Title | Primary outcome |
|---|---|---|
| PR-45 | Phase3 Design Baseline / Graph-RAG Planning | Fixed architecture, schema draft, boundaries, acceptance, risk, and test plan. |
| PR-46 | Graph Schema / Graph Index Foundation | Add graph schema migration, ORM, DTOs, repository, index run lifecycle skeleton, job type constant, settings, tests, and docs. |
| PR-47 | Entity / Relation Extraction Pipeline | Add extractor interface, worker job handler, deterministic test extractor, and safe source mapping. |
| PR-48 | Graph Retrieval Strategy / Graph-aware Router | Add graph lookup/traversal strategy and route graph-shaped queries. |
| PR-49 | GraphStore Boundary / Neo4j Migration Prep | Wrap PostgreSQL graph retrieval behind provider-neutral DTOs and prepare optional Neo4j without making it required. |
| PR-50 | Neo4j Optional Backend | Add Neo4j as an optional read model/projection while keeping PostgreSQL as source of truth. |
| PR-51 | Graph Citation / Debug UI | Map graph paths back to source chunk-backed citations and expose admin-safe graph trace details. |
| PR-52 | Retrieval Cache Foundation | Add strategy-agnostic safe retrieval result caching for dense, sparse, hybrid, and graph retrieval. |
| PR-53 | Graph Evaluation / Strategy Comparison | Compare dense, hybrid, agentic, graph_postgres, and graph_neo4j with cache-aware safe reports. |
| PR-54 | GraphRAG Final Hardening / Demo Docs | Finalize README routing, reproducible demos, Neo4j optional steps, manual tests, acceptance checklist, smoke, security review, limitations, and PR-55+ handoff. |
| PR-55+ | Future Extensions | Consider graph/vector fusion, OCR, multimodal citation, external providers, S3, OIDC, AWS deploy, online evaluation, Redis, and production observability only after separate design approval. |

## PR-46 Exit Shape

PR-46 is complete when graph persistence and index run lifecycle can be tested without implementing graph extraction or graph retrieval.

Required evidence:

- Alembic upgrade/downgrade path exists.
- Graph tables have FK/CHECK/index coverage.
- ORM/DTO/repository/service skeletons exist.
- `graph_index_runs` lifecycle works in tests.
- `graph_index_build` is reserved for PR-47 worker wiring and not active at the PR-46 boundary.
- Graph settings default to disabled.
- Docs preserve raw text and secret non-storage policy.

## PR-47 Exit Shape

PR-47 is complete when ready document versions can be converted into safe graph
entities, mentions, and relations through the worker pipeline without exposing
raw evidence text.

Required evidence:

- `graph_index_build` is registered in worker configuration and dispatch.
- Entity/relation extraction stores IDs, hashes, offsets, labels, counts, and safe metadata only.
- Rebuilding a document version is idempotent for mentions and relations.
- Retrying a failed graph index run creates a new succeeded run.
- Tests cover success, idempotency, worker registration, retry, and safe failure.

## PR-48 Through PR-53 Exit Shape

PR-48 through PR-53 are complete when the graph path can run through the
existing retrieval, citation, cache, debug, and evaluation boundaries without
turning Neo4j into a required service.

Required evidence:

- explicit `graph` retrieval resolves graph paths back to source chunks.
- graph-aware routing can select graph only when graph is enabled and signals
  are strong enough.
- provider-neutral GraphStore DTOs support PostgreSQL and Neo4j.
- graph citations remain `retrieval_run_item -> document_chunk` backed.
- admin graph debug exposes safe refs, counts, reason codes, and scores only.
- retrieval cache keys include graph fingerprint and graph store provider.
- evaluation can compare dense, hybrid, agentic_router, graph_postgres, and
  optional graph_neo4j without storing raw payloads.

## PR-54 Exit Shape

PR-54 is complete when a reviewer can start from README, reproduce the local
PostgreSQL GraphRAG demo path, optionally enable Neo4j as a read model, run the
lightweight smoke checks, and confirm the acceptance/security checklist without
needing private payloads or secrets.

Required evidence:

- [`graph_rag_final_readme.md`](graph_rag_final_readme.md) is the operator
  entry point.
- [`graph_rag_demo_scenario.md`](graph_rag_demo_scenario.md) contains concrete
  local and Neo4j demo steps.
- [`graph_rag_manual_test_cases.md`](graph_rag_manual_test_cases.md) and
  [`graph_rag_acceptance_checklist.md`](graph_rag_acceptance_checklist.md)
  define the acceptance surface.
- [`graph_rag_known_limitations.md`](graph_rag_known_limitations.md) keeps
  scope limits separate from implemented behavior.
- [`graph_rag_next_phase_handoff.md`](graph_rag_next_phase_handoff.md) lists
  PR-55+ candidates without making them PR-54 requirements.
- `scripts/smoke_phase3_graph_rag.*` run non-destructive docs/config checks.

## Sequencing Rules

1. Graph schema and graph index state come before extraction or retrieval.
2. Extraction must preserve source chunk mapping before graph retrieval is exposed.
3. Graph retrieval must be available before Graph-aware Router can select it.
4. Graph Citation must validate node/edge/path mapping before user-facing graph evidence appears.
5. Graph Debug UI must show safe summaries only after backend trace contracts exist.
6. OCR and image work starts after text GraphRAG has a stable citation model.
7. Production expansion stays optional and separate until local behavior is stable.
8. External providers, S3, OIDC, AWS deploy, Redis, and alerting require future
   PRs with separate export and secret-handling review.
