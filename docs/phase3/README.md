# Phase3 GraphRAG Handoff

PR-54 is the final hardening and demo-docs handoff for the local text GraphRAG
path delivered through PR-46 to PR-53. It builds on Phase2.5 Auto, Context
Engineering, MCP `rag_ask_auto`, and the local Kubernetes baseline.

This directory still contains the earlier design docs because they explain how
the implementation arrived here. Use the PR-54 entry points first for demo and
acceptance work.

## Entry Points

| Topic | Document |
|---|---|
| GraphRAG final operator README | [graph_rag_final_readme.md](graph_rag_final_readme.md) |
| Local GraphRAG demo scenario | [graph_rag_demo_scenario.md](graph_rag_demo_scenario.md) |
| Manual GraphRAG test cases | [graph_rag_manual_test_cases.md](graph_rag_manual_test_cases.md) |
| GraphRAG acceptance checklist | [graph_rag_acceptance_checklist.md](graph_rag_acceptance_checklist.md) |
| GraphRAG known limitations | [graph_rag_known_limitations.md](graph_rag_known_limitations.md) |
| GraphRAG next phase handoff | [graph_rag_next_phase_handoff.md](graph_rag_next_phase_handoff.md) |
| Roadmap and PR order | [phase3_roadmap.md](phase3_roadmap.md) |
| Phase3 acceptance criteria | [phase3_acceptance_criteria.md](phase3_acceptance_criteria.md) |
| Graph-RAG architecture | [graph_rag_architecture.md](graph_rag_architecture.md) |
| Graph schema draft and migration plan | [graph_schema_draft.md](graph_schema_draft.md) |
| Entity / relation extraction | [entity_relation_extraction_design.md](entity_relation_extraction_design.md) |
| Graph indexing | [graph_indexing_design.md](graph_indexing_design.md) |
| Retrieval strategy and graph + vector hybrid | [graph_retrieval_strategy.md](graph_retrieval_strategy.md) |
| Graph-aware Router | [graph_aware_router_design.md](graph_aware_router_design.md) |
| Graph Citation and path validation | [graph_citation_design.md](graph_citation_design.md) |
| PR-51 Graph citation debug implementation | [graph_citation_debug_pr51.md](graph_citation_debug_pr51.md) |
| PR-52 Retrieval result cache foundation | [retrieval_cache_foundation.md](retrieval_cache_foundation.md) |
| Graph Debug UI | [graph_debug_ui_design.md](graph_debug_ui_design.md) |
| Graph Evaluation | [graph_evaluation_design.md](graph_evaluation_design.md) |
| Corpus-grounded multi-hop gold dataset | [corpus_multi_hop_gold_dataset.md](corpus_multi_hop_gold_dataset.md) |
| OCR / Multimodal boundary | [ocr_multimodal_boundary.md](ocr_multimodal_boundary.md) |
| Production / deploy boundary | [production_expansion_boundary.md](production_expansion_boundary.md) |
| API design delta | [api_design_delta.md](api_design_delta.md) |
| Test strategy | [phase3_test_strategy.md](phase3_test_strategy.md) |
| Risk register | [phase3_risk_register.md](phase3_risk_register.md) |
| Security / redaction policy | [security_redaction_policy.md](security_redaction_policy.md) |

## PR-54 Completion Scope

PR-54 does not add a new retrieval strategy, new evaluation metric, OCR,
multimodal input, external LLM provider, S3, OIDC, AWS deploy, Redis
implementation, or production alerting. It completes the portfolio-demo
handoff with:

- root README and Phase3 README links that route directly to GraphRAG usage
- local PostgreSQL GraphRAG demo steps
- optional Neo4j read-model/projection demo steps
- manual tests and acceptance checklist
- PostgreSQL source-of-truth versus Neo4j read-model architecture explanation
- retrieval cache and evaluation summaries for PR-52/PR-53
- security/redaction checklist for docs, debug traces, cache, evaluation, and
  graph paths
- troubleshooting, known limitations, and PR-55+ handoff
- non-destructive GraphRAG smoke scripts

## Phase2.5 Foundation

Phase3 must extend, not bypass, the Phase2.5 safety path:

- Auto and `llm_tool_orchestrator` remain retrieval-only until a separate design approves more tool classes.
- Graph retrieval evidence passes through Context Budget.
- Graph evidence preserves Evidence Pack citation mapping through source chunk
  backed retrieval run items.
- Graph tool outputs must be bounded by Tool Result Compression before planner visibility.
- Admin debug views may show safe graph summaries; viewer UI must not show internal trace details.
- Local Kubernetes remains `k8s/local` for kind/minikube and is not AWS/EKS production.

## Current Non-Goals

- No OCR or image upload implementation.
- No multimodal UI implementation.
- No AWS, S3, Bedrock, RDS, ECS, EKS, OIDC, Terraform, or production secret
  manager work.
- No external LLM provider implementation.
- No online evaluation, A/B implementation, or production alerting.
- No public `graph_hybrid` strategy; it remains a PR-55+ candidate.
- No requirement that Neo4j be installed or enabled.

## Safety Invariant

Docs, logs, artifacts, traces, debug output, and MCP output must not include raw document text, raw chunk text, raw prompt material, full context, PII, credential values, session values, or secret values. Graph design uses hashes, IDs, counts, scores, strategy labels, and source chunk references instead of raw evidence payloads.
