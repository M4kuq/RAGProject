# Phase2 Manual Test Cases

These cases are for acceptance walkthroughs and release sign-off. They describe
what to verify without requiring real secrets, real customer data, external API
keys, or heavy model downloads.

| ID | Area | Scenario | Steps | Expected result | Notes |
|---|---|---|---|---|---|
| P2-TC-001 | Start / migration / seed | Compose configuration is valid | Run `docker compose config` and `docker compose -f docker-compose.ci.yml config --quiet`. | Both commands succeed. | No destructive cleanup. |
| P2-TC-002 | Start / migration / seed | Services become ready | Start services, then check `/health` and `/ready`. | Backend is healthy and ready. | Do not print env values. |
| P2-TC-003 | Start / migration / seed | Seeded data exists | Sign in as local demo admin and list documents/evaluation datasets. | Seeded documents and `phase2_strategy_smoke` are visible. | Local demo only. |
| P2-TC-100 | Sparse Retrieval | Sparse strategy returns results | Run `/rag/search` with `strategy=sparse` for a keyword-heavy query. | `200 OK`; results or safe empty list; run trace records sparse. | No raw query persisted beyond safe trace policy. |
| P2-TC-101 | Sparse Retrieval | Sparse no-result path | Search an intentionally absent term. | `200 OK` with `items=[]`; safe trace. | No exception payload leaks. |
| P2-TC-200 | Hybrid Retrieval | Hybrid returns fused scores | Run `/rag/search` with `strategy=hybrid`. | Items include safe dense/sparse/fusion score summary. | Verify deterministic ordering where possible. |
| P2-TC-201 | Hybrid Retrieval | Hybrid remains optional | Disable hybrid through settings in a controlled local test. | Request fails safely with strategy-not-enabled behavior. | Restore settings afterward. |
| P2-TC-300 | Query Analyzer / Planner | Query analysis is recorded | Search with a comparison or version-specific query. | Debug UI shows intent, flags, and safe query plan summary. | No raw full prompt/context. |
| P2-TC-301 | Query Analyzer / Planner | Metadata filter candidates are safe | Use a query with `section:`-style hint. | Plan shows structured candidates, not raw SQL/Qdrant filters. | Redacted previews only. |
| P2-TC-400 | Strategy Router | Agentic router selects execution strategy | Run `/rag/search strategy=agentic_router`. | Strategy decision shows selected/execution strategy and reason codes. | Default ask remains unchanged unless opt-in. |
| P2-TC-401 | Strategy Router | Router fallback is safe | Simulate unavailable sparse/hybrid path in local test settings. | Router uses dense/fallback_dense and records fallback reason. | No raw exception message. |
| P2-TC-500 | Agentic Retrieval Loop | Sufficient first result avoids fallback | Run agentic search for an easy seeded query. | `retrieval_call_count=1`; fallback false when sufficient. | Debug UI confirms sufficiency. |
| P2-TC-501 | Agentic Retrieval Loop | Insufficient context triggers bounded fallback | Run a query likely to be low score or low diversity. | At most configured calls; fallback trace is safe. | No unbounded loop. |
| P2-TC-502 | Agentic Retrieval Loop | Ask no-context behavior | Use `/rag/ask strategy=agentic_router` for a no-context query. | `422 no_context_found`; no assistant message is created. | User message behavior follows API contract. |
| P2-TC-600 | Retrieval Debug UI v2 | Trace sections render | Open `/admin/retrieval-debug` and a run detail. | Query plan, decision, settings, score, latency, items render safely. | Admin only. |
| P2-TC-601 | Retrieval Debug UI v2 | Redaction holds in UI | Inspect debug detail for forbidden strings. | No raw prompt, full context, raw chunk text, token, or secret. | Use browser devtools only for safe payloads. |
| P2-TC-700 | Strategy Evaluation | Multi-strategy run queues | Create evaluation with `dense,hybrid,agentic_router`. | Run creates one item per case per strategy and completes/partially completes. | Admin only; CSRF required. |
| P2-TC-701 | Strategy Evaluation | Agentic metrics aggregate | Open strategy comparison. | fallback rate, budget exhaustion, sufficiency score, call count are shown. | Expected strategy accuracy may be N/A. |
| P2-TC-702 | Strategy Evaluation | Failure promotion is idempotent | Promote selected failure candidates twice. | First creates or links cases; second reports skipped/already exists. | Target dataset must be active. |
| P2-TC-800 | CI Retrieval Evaluation | Manual workflow is available | Inspect GitHub Actions workflow dispatch inputs. | Dataset, strategies, threshold mode, case limit, thresholds are available. | Not required PR gate. |
| P2-TC-801 | CI Retrieval Evaluation | Local smoke wrapper runs or blocks safely | Run `scripts/run_retrieval_eval_smoke.ps1` in a prepared local env. | Success or safe blocked artifact with reason codes. | No fake retrieval fallback. |
| P2-TC-900 | LangSmith Optional Adapter | Default no-op | Run search/ask without LangSmith settings. | RAG succeeds; export status is skipped/no-op. | No secret required. |
| P2-TC-901 | LangSmith Optional Adapter | Export failure is non-fatal | Configure invalid optional exporter endpoint in a safe local test. | Search/ask/evaluation still succeeds; warning is safe. | Do not use real secret in test notes. |
| P2-TC-1000 | SentenceTransformers Experiment | Dry-run validates manifest | Run `scripts/run_retrieval_model_experiment.ps1 -Mode dry-run -DownloadPolicy never`. | JSON/Markdown artifact skeleton is written. | No model download. |
| P2-TC-1001 | SentenceTransformers Experiment | Missing model is skipped/blocked | Run local mode without cached optional model. | Optional model skipped or required model blocked with safe reason. | No GPU required. |
| P2-TC-1100 | Advanced Import | Office ingest | Upload `.xlsx` and `.pptx` fixtures. | Versions become ready; chunks include sheet/slide metadata. | Legacy and macro files rejected. |
| P2-TC-1101 | Advanced Import | HTML/XML ingest | Upload `.html`, `.htm`, `.xml` fixtures. | Versions become ready; chunks include heading/XML path metadata. | SVG/DTD/entity rejected. |
| P2-TC-1102 | Advanced Import | URL ingest SSRF guard | Ingest from a local mock-safe URL and try localhost/private IP redirects. | Safe URL creates version/job; blocked URLs return validation errors. | No real internet dependency in CI. |
| P2-TC-1200 | Document Diff / Citation Navigation | Version compare | Compare two versions under one logical document. | Summary shows added/removed/changed/unchanged and bounded previews. | Admin only. |
| P2-TC-1201 | Document Diff / Citation Navigation | Citation source preview | Open a chat citation and click View source. | Safe locator preview opens; old-version/source URL indicators are correct. | Viewer sees preview only. |
| P2-TC-1300 | Security / Redaction | Forbidden data absent | Inspect API/UI artifacts and docs. | No raw prompt, full context, raw chunk text, PII, token, secret, or storage path. | Use safe synthetic test data. |
| P2-TC-1301 | Security / Redaction | Destructive operations are explicit | Review smoke and docs commands. | No automatic `down -v`, DB reset, or force push command. | Warnings are present. |
| P2-TC-1400 | Final Acceptance | End-to-end demo complete | Run the demo scenario from start to citation source navigation. | Phase2 can be demonstrated and known limitations are clear. | Record evidence in the checklist. |
| P2-TC-1500 | MCP Advanced RAG | Strategy-aware MCP search | Call `rag_search` with `strategy=hybrid` and `strategy=agentic_router`, or use the wrapper tools. | Results include bounded snippets and optional safe trace summary. | No raw chunk text or full context. |
| P2-TC-1501 | MCP Advanced RAG | Agentic ask opt-in | Call `rag_ask_agentic` for a safe synthetic question. | Answer includes citations/confidence when available; no-context is structured. | No raw prompt or generated context payload. |
| P2-TC-1502 | MCP Advanced RAG | Trace and evaluation resources | Read `rag://retrieval-runs/{id}`, `rag://evaluations/{id}/summary`, and `rag://strategies`. | Resources return safe summaries only. | MCP remains local-only and read-mostly. |
| P2-TC-1600 | LLM Tool Orchestrator | Chat UI LLM Agentic RAG | Select `LLM Agentic RAG` in Chat and send a safe synthetic question. | `/rag/ask` sends `strategy=llm_tool_orchestrator`; answer uses citations/confidence or returns `422 no_context_found` safely. | No internal tool trace in viewer UI. |
| P2-TC-1601 | LLM Tool Orchestrator | Tool loop is bounded | Run with low local `LLM_ORCHESTRATOR_MAX_TOOL_CALLS` in a controlled test. | Budget exhaustion returns no-context and no assistant placeholder. | No unbounded self-reflection loop. |
| P2-TC-1602 | LLM Tool Orchestrator | Safe trace | Inspect Retrieval Debug for an `llm_tool_orchestrator` run. | Trace shows tool counts, tools used, finalize/budget flags, and latency only. | No raw prompt, full context, raw chunk text, token, or secret. |
| P2-TC-1603 | LangChain Agentic RAG | Chat UI LangChain Agentic | Select `LangChain Agentic` in Chat and send a safe synthetic question. | `/rag/ask` sends `strategy=langchain_agentic`; answer uses citations/confidence or returns `422 no_context_found` safely. | Same retrieval-only tool boundary as Auto. |
| P2-TC-1604 | LangChain Agentic RAG | Safe trace | Inspect Retrieval Debug for a `langchain_agentic` run. | Trace shows provider, tool counts, finalize/budget flags, and LangChain latency fields only. | No raw prompt, full context, raw chunk text, token, or secret. |
