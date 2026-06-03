# Phase2 README

## Phase2.5 Final Handoff

Phase2.5 is the final hardening and demo handoff after PR-39 to PR-43. Use these files first when reviewing or demoing the Context Engineering and local Kubernetes work:

- [Phase2.5 README](phase2_5_readme.md)
- [Phase2.5 demo scenario](phase2_5_demo_scenario.md)
- [Context Engineering README](context_engineering_readme.md)
- [Context Engineering demo scenario](context_engineering_demo_scenario.md)
- [Context Engineering manual test cases](context_engineering_manual_test_cases.md)
- [Context Engineering acceptance checklist](context_engineering_acceptance_checklist.md)
- [Context Engineering known limitations](context_engineering_known_limitations.md)
- [Kubernetes baseline entrypoint](kubernetes_baseline.md)
- [Local Kubernetes baseline details](kubernetes_local_baseline.md)
- [Phase3 handoff](phase3_handoff.md)
- [deploy/aws handoff](deploy_aws_handoff.md)

Safe Phase2.5 smoke:

```powershell
scripts\smoke_phase2_5.ps1
```

```sh
sh scripts/smoke_phase2_5.sh
```

## Purpose

Phase2 extends the Phase1 dense RAG baseline with four central themes:

- Advanced Retrieval
- Agentic Control
- Evaluation
- Observability

PR-20 fixed the strategy and trace schema baseline. PR-21 connected safe trace recording to the existing dense `/rag/search` and `/rag/ask` flows. PR-22 adds dataset, case, and strategy metric schema management so later PRs can compare dense / sparse / hybrid / agentic_router on the same dataset. PR-23 adds standalone sparse lexical retrieval for `/rag/search`. PR-24 adds standalone hybrid dense+sparse retrieval and score fusion for `/rag/search`. PR-25 adds the deterministic strategy evaluation runner for dense / sparse / hybrid. PR-26 adds Retrieval Debug UI v2. PR-27 adds Query Analyzer / Query Planner. PR-28 adds explicit `agentic_router` routing for one retrieval call with safe dense fallback. PR-29 adds the bounded agentic retrieval loop, PR-30 adds agentic strategy evaluation plus failure dataset promotion, PR-31 adds lightweight CI retrieval evaluation smoke runs, PR-32 adds optional no-op-by-default external trace export, PR-33 adds a local opt-in SentenceTransformers experiment harness, PR-34 adds `.xlsx` / `.pptx` ingestion with metadata-only parent-child chunking, PR-35 adds `.html` / `.htm` / `.xml` file ingestion plus single-URL ingestion behind an SSRF guard, PR-36 adds safe document version compare plus citation source navigation, PR-37 finalizes demo, acceptance, smoke, and Phase3 handoff documentation, PR-38 adds MCP hybrid / agentic tools, PR-39 adds the LLM tool-calling retrieval orchestrator, PR-40 adds safe context budget / trace / debug foundation before generation, PR-41 adds deterministic Evidence Pack construction for retrieved context compression, PR-42 adds safe Tool Result Compression / Orchestrator Context Guard for Auto tool outputs, PR-43 adds a local Kubernetes baseline for kind/minikube, and PR-44 adds the Phase2.5 final hardening, demo, manual acceptance, smoke, known limitation, Phase3, and deploy/aws handoff docs.

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
| PR-39 | LLM Tool-Calling Retrieval Orchestrator |
| PR-40 | Context Budget / Context Trace / Context Debug Foundation |
| PR-41 | Retrieved Context Compression / Evidence Pack |
| PR-42 | Tool Result Compression / Orchestrator Context Guard |
| PR-43 | Kubernetes Baseline / Local K8s Deploy / Compose-to-K8s Hardening |
| PR-44 | Phase2.5 Final Hardening / Context Engineering + Kubernetes Demo Docs |

## Phase2 Final Docs

Use these files for final handoff and demo validation:

- [Phase2 demo scenario](phase2_demo_scenario.md)
- [Phase2 demo scenario Japanese](phase2_demo_scenario.ja.md)
- [Phase2 manual test cases](phase2_manual_test_cases.md)
- [Phase2 manual test cases Japanese](phase2_manual_test_cases.ja.md)
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
- [LLM tool-calling retrieval orchestrator](llm_tool_calling_retrieval_orchestrator.md)
- [LLM tool-calling retrieval orchestrator Japanese](llm_tool_calling_retrieval_orchestrator.ja.md)
- [LLM tool orchestrator smoke](llm_tool_orchestrator_smoke.md)
- [Context budget trace debug](context_budget_trace_debug.md)
- [Evidence Pack context compression](evidence_pack_context_compression.md)
- [Tool Result Compression / Orchestrator Guard](tool_result_compression_orchestrator_guard.md)
- [Local Kubernetes baseline](kubernetes_local_baseline.md)
- [Phase2 test strategy](test_strategy.md)
- [PR-by-PR acceptance criteria](acceptance_criteria.md)

## Local Setup And Smoke

Local setup follows the repository root README and Docker Compose files. For Phase2-specific checks from the repository root:

```powershell
scripts/smoke_phase2.ps1
scripts/smoke_phase2.ps1 -RunExperimentDryRun
scripts/smoke_phase2_5.ps1
```

```sh
sh scripts/smoke_phase2.sh
sh scripts/smoke_phase2.sh --run-experiment-dry-run
sh scripts/smoke_phase2_5.sh
```

The basic smoke validates compose configuration, Phase2 final docs, Phase2.5 docs, key fixtures, and optional running health endpoints. `-Deep` / `--deep` additionally requires running local services and local demo admin credentials in shell environment variables. The smoke scripts do not run destructive cleanup, do not print secrets, do not print kubeconfig, and do not require external API keys, LangSmith, GPU, or heavy model downloads by default.

## Security Invariant

RAG trace, debug, logs, DB JSON, UI, MCP output, and artifacts must not store or display raw prompt, full context, raw chunk text, snippets inside context budget trace, raw tool outputs, raw tool result payloads, snippets in persisted compression trace, PII, credential values, token values, cookies, sessions, secrets, kubeconfig, or local paths. Numeric token and char estimates are allowed.
