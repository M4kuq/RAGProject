# Phase3 Design Baseline

PR-45 starts Phase3 by fixing the design boundary for Graph-RAG and related expansion work. It builds on Phase2.5 Auto, Context Engineering, MCP `rag_ask_auto`, and the local Kubernetes baseline.

PR-45 is documentation only. It does not add Graph-RAG runtime code, database migrations, OCR, image upload, AWS infrastructure, OIDC, external LLM provider wiring, or online evaluation implementation.

## Entry Points

| Topic | Document |
|---|---|
| Roadmap and PR order | [phase3_roadmap.md](phase3_roadmap.md) |
| Phase3 acceptance criteria | [phase3_acceptance_criteria.md](phase3_acceptance_criteria.md) |
| Graph-RAG architecture | [graph_rag_architecture.md](graph_rag_architecture.md) |
| Graph schema draft and migration plan | [graph_schema_draft.md](graph_schema_draft.md) |
| Entity / relation extraction | [entity_relation_extraction_design.md](entity_relation_extraction_design.md) |
| Graph indexing | [graph_indexing_design.md](graph_indexing_design.md) |
| Retrieval strategy and graph + vector hybrid | [graph_retrieval_strategy.md](graph_retrieval_strategy.md) |
| Graph-aware Router | [graph_aware_router_design.md](graph_aware_router_design.md) |
| Graph Citation and path validation | [graph_citation_design.md](graph_citation_design.md) |
| Graph Debug UI | [graph_debug_ui_design.md](graph_debug_ui_design.md) |
| Graph Evaluation | [graph_evaluation_design.md](graph_evaluation_design.md) |
| OCR / Multimodal boundary | [ocr_multimodal_boundary.md](ocr_multimodal_boundary.md) |
| Production / deploy boundary | [production_expansion_boundary.md](production_expansion_boundary.md) |
| API design delta | [api_design_delta.md](api_design_delta.md) |
| Test strategy | [phase3_test_strategy.md](phase3_test_strategy.md) |
| Risk register | [phase3_risk_register.md](phase3_risk_register.md) |
| Security / redaction policy | [security_redaction_policy.md](security_redaction_policy.md) |

## Phase2.5 Foundation

Phase3 must extend, not bypass, the Phase2.5 safety path:

- Auto and `llm_tool_orchestrator` remain retrieval-only until a separate design approves more tool classes.
- Graph retrieval evidence must pass through Context Budget.
- Graph evidence must preserve Evidence Pack citation mapping.
- Graph tool outputs must be bounded by Tool Result Compression before planner visibility.
- Admin debug views may show safe graph summaries; viewer UI must not show internal trace details.
- Local Kubernetes remains `k8s/local` for kind/minikube and is not AWS/EKS production.

## PR-45 Non-Goals

- No Alembic migration.
- No graph tables in runtime database.
- No Graph-RAG backend implementation.
- No entity or relation extractor implementation.
- No graph database product integration.
- No OCR or image upload implementation.
- No multimodal UI implementation.
- No AWS, S3, Bedrock, RDS, ECS, EKS, OIDC, Terraform, or production secret manager work.
- No external LLM provider implementation.
- No online evaluation or A/B implementation.

## Safety Invariant

Docs, logs, artifacts, traces, debug output, and MCP output must not include raw document text, raw chunk text, raw prompt material, full context, PII, credential values, session values, or secret values. Graph design uses hashes, IDs, counts, scores, strategy labels, and source chunk references instead of raw evidence payloads.
