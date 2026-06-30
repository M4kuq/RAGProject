# Corpus-Grounded Multi-Hop Gold Dataset

This document describes `phase3_corpus_multi_hop`, a fixture-only evaluation
dataset for GraphRAG strategy comparisons. It is grounded in repository-owned
demo material only:

- LLM paper seed corpus: `backend/app/seed_data/llm_paper_corpus.md`
- RAGProject self-doc manifest: `docs/demo/corpus_manifest.json`
- RAGProject GraphRAG, evaluation, retrieval, and storage docs under `docs/phase3/`
- supporting Phase2 docs plus `README.md`, `docs/DDL.md`, and design docs listed
  by the manifest

`data/demo/llm-research/*.md` is not present in this `origin/main` checkout. The
paper demo content currently lives in the seed corpus and is loaded as the
`LLM Paper Corpus for RAG Demo` logical document.

## Scope

`phase3_corpus_multi_hop` adds only fixture data. It does not add metrics, API
fields, database migrations, retrieval logic, frontend behavior, or external
corpus text. `expected_document_ids` and `expected_chunk_ids` are intentionally
empty because ingest-time IDs are local database state. Use the runbook below to
create the live IDs for a local comparison.

The fixture has 14 active cases:

| Domain | Count | Support shape |
|---|---:|---|
| LLM paper corpus | 6 | LLM-observed relations plus extracted canonical entity hubs |
| RAGProject self docs | 8 | extracted relations plus canonical entity hubs |

Every case includes:

- `required_citation=true`
- `metadata_json.expected_strategy=graph`
- `metadata_json.acceptable_strategies`
- `metadata_json.expected_entity_labels`
- `metadata_json.expected_relation_types`

Paper cases now use non-empty `expected_relation_types` based on the #82 LLM
graph extraction validation. The old deterministic rule-based extractor still
emits 0 paper relations, but structured-output LLM extraction produced paper
relations in the live corpus run. Self-doc cases continue to use relation types
only when the deterministic extractor emits the relation.

The relation types expected by this fixture are safe labels only. Self-doc
relation cases use the rule-based outputs `supports`, `uses`, `depends_on`,
`includes`, and `connects`. Paper cases use the LLM-observed safe type set
`describes`, `evaluates`, `implements`, `improves`, `includes`, `organizes`,
`supports`, and `uses`, with only 1-3 conservative types per case.

Only relation-backed cases set `metadata_json.required_hop_count`. Hub-only
system-doc cases omit it because the current multi-hop answerability metric
measures relation path depth, not same-entity hub fanout. All present
`required_hop_count` values are 1, within the default
`GRAPH_RETRIEVAL_MAX_DEPTH` runbook setting.

## Extracted Graph Grounding

The original fixture was aligned against the deterministic rule-based graph
extractor: `EntityExtractionService`, `RelationExtractionService`, and
`GraphEntityNormalizer`. Validation uses the same `FixedTokenChunker`
configuration as document ingest: 512-unit windows with 128-unit overlap.

Extraction summary, without source text:

| Corpus | Chunks | Mentions | Canonical labels | Relations |
|---|---:|---:|---:|---:|
| Paper seed corpus | 110 | 121 | 32 | 0 |
| Self-doc manifest corpus | 113 | 1567 | 322 | 72 |

#82 then validated structured-output LLM graph extraction against the live
active-ready corpus without adding raw evidence to the fixture. The latest
observed aggregate was:

| Scope | Relations |
|---|---:|
| Full active corpus | 22 |
| Paper seed corpus | 9 |

This means the paper baseline moved from rule-based `0` relations to LLM
`9` relations for the paper logical document. Paper `expected_relation_types`
are therefore grounded from the LLM-observed safe type set, but remain
conservative because LLM extraction is non-deterministic.

`graph_path_relevance` uses intersection scoring: expected entity labels and
expected relation types receive credit when the observed graph path contains a
matching safe label or type. The corpus eval runbook remains a manual/demo
workflow with warn thresholds so LLM non-determinism does not make CI flaky. CI
continues to use Fake paths and fixture/schema/metric-logic tests; it does not
require a live LLM graph index or the full corpus index.

Paper multi-source hubs used by the fixture:

| Canonical label | Source count | Mention count |
|---|---:|---:|
| `RAG` | 8 | 17 |
| `Retrieval` | 11 | 13 |
| `LoRA` | 2 | 3 |
| `LLM` | 18 | 25 |
| `API` | 2 | 4 |
| `DeepSeek` | 4 | 8 |

Paper relation types used by the fixture:

| Case | Conservative LLM-observed relation types |
|---|---|
| `paper_rag_retrieval_hub` | `uses`, `organizes` |
| `paper_lora_quantization_hub` | `uses` |
| `paper_deepseek_llm_hub` | `describes` |
| `paper_api_tool_hub` | `evaluates`, `uses` |
| `paper_graphrag_global_summarization_relation` | `organizes`, `supports` |
| `paper_react_reasoning_action_relation` | `uses` |

Self-doc relation edges used by the fixture:

| Source | Relation | Target |
|---|---|---|
| `Graph` | `uses` | `graph path relevance` |
| `graph path relevance` | `uses` | `graph citation coverage` |
| `Graph` | `connects` | `Citation` |
| `GraphRAG` | `connects` | `PostgreSQL` |
| `GraphIndexService` | `connects` | `graph index build` |

Self-doc multi-source hubs used by the fixture:

| Canonical label | Source count | Mention count |
|---|---:|---:|
| `GraphRAG` | 7 | 44 |
| `GraphStore` | 4 | 10 |
| `GraphRetrievalStrategy` | 2 | 4 |
| `GraphIndexService` | 3 | 3 |
| `GraphRepository` | 2 | 2 |
| `GraphPath` | 3 | 4 |
| `PostgreSQL` | 13 | 57 |
| `retrieval run items` | 9 | 23 |
| `source chunk ids` | 4 | 6 |
| `document chunk id` | 6 | 20 |
| `document version id` | 5 | 44 |
| `graph index build` | 4 | 8 |
| `graph path relevance` | 2 | 2 |
| `graph citation coverage` | 2 | 2 |

Graph advantage is clearest in technical and structural content where extracted
entities and relation edges reflect implementation boundaries. With LLM
extraction enabled, the paper corpus is also useful for lightweight relation
scoring, but the expectations stay intentionally sparse to avoid overfitting to
one non-deterministic extraction run.

## Case Design

| Case | Domain | Actual graph support | Why dense can miss |
|---|---|---|---|
| `paper_rag_retrieval_hub` | paper | `RAG` and `Retrieval` hubs span multiple paper blocks; LLM extraction adds `uses` / `organizes` relation types. | A lexical hit can stop at one RAG entry and miss later Self-RAG, CRAG, RAPTOR, or GraphRAG entries. |
| `paper_lora_quantization_hub` | paper | `LoRA` spans LoRA and QLoRA entries; `LLM` links the broader model corpus; LLM extraction adds `uses`. | Quantization terms can rank QLoRA without bringing the earlier LoRA adaptation entry. |
| `paper_deepseek_llm_hub` | paper | `DeepSeek` and `LLM` span scaling, V3, reasoning, and code-focused entries; LLM extraction adds `describes`. | A query about one DeepSeek variant can miss the adjacent DeepSeek entries. |
| `paper_api_tool_hub` | paper | `API` spans Gorilla and ToolBench; LLM extraction adds `evaluates` / `uses`. | API terms can overfocus on Gorilla and miss the tool-use benchmark entry. |
| `paper_graphrag_global_summarization_relation` | paper | `GraphRAG`, `RAG`, and `Retrieval` labels connect to `organizes` / `supports` relation types around graph indexing and summaries. | GraphRAG terms can retrieve the title entry without following the relation to summary-oriented evidence. |
| `paper_react_reasoning_action_relation` | paper | `ReAct` and `LLM` labels connect through a `uses` relation type around reasoning and action traces. | Agent terms can retrieve benchmark or tool entries without the reasoning-action paper. |
| `system_graph_metric_chain_relation` | system docs | `Graph uses graph path relevance`; `graph path relevance uses graph citation coverage`. | Dense retrieval can find one metric term while missing the metric chain. |
| `system_graph_citation_relation` | system docs | `Graph connects Citation`; retrieval trace hubs attach citation evidence to source chunks. | Citation terms can miss graph path and retrieval-run evidence. |
| `system_graphrag_postgresql_relation` | system docs | `GraphRAG connects PostgreSQL`; both labels are multi-source hubs. | GraphRAG terms can retrieve user-facing docs without the storage boundary. |
| `system_graph_index_service_relation` | system docs | `GraphIndexService connects graph index build`; GraphRepository and document version id remain adjacent hubs. | Indexing terms can retrieve worker behavior without the service/job link. |
| `system_graphstore_provider_hub` | system docs | `GraphStore`, `PostgresGraphStore`, `Neo4jGraphStore`, and `GraphPath` are extracted hubs/endpoints. | Provider terms can retrieve one backend doc without the shared path-evidence contract. |
| `system_mcp_langchain_langgraph_hub` | system docs | `MCP`, `LangChain`, `LangGraph`, and `Qdrant` are extracted hubs across Phase2 self docs. | Tooling terms can retrieve one strategy doc without the cross-tool boundary. |
| `system_graph_evaluation_metric_hub` | system docs | `GraphRAG`, `graph path relevance`, `graph citation coverage`, and `Evaluation` are extracted hubs. | Metric terms can retrieve only the evaluation design and miss strategy comparison docs. |
| `system_retrieval_trace_source_hub` | system docs | `GraphRetrievalStrategy`, `retrieval run items`, `source chunk ids`, `document chunk id`, and `document version id` are extracted hubs. | Trace terms can retrieve the SQL/API record without the graph path source mapping. |

Expected answers are short summaries. They do not copy full source passages or
generated context. Expected keywords are short terms that exist in the committed
corpus and can be matched with case-insensitive substring checks.

## Reproduction Runbook

These steps use only repository-owned demo material and existing scripts.

1. Create a temporary untracked compose override so the running backend
   container can read the repository self-doc files at `/workspace`.

   ```powershell
   Set-Content .\docker-compose.selfdocs.override.yml -Encoding utf8 -Value @(
     "services:",
     "  backend:",
     "    volumes:",
     "      - ./:/workspace:ro"
   )
   ```

   This file is for local reproduction only. Do not commit it. The plain
   `docker compose up -d --build` path is not enough for this runbook because
   the default backend container has no repository checkout at `/workspace`.

2. Start the local stack with graph retrieval enabled and the temporary
   override. Keep the same `-f docker-compose.selfdocs.override.yml` argument on
   every later compose command in this runbook.

   ```powershell
   $env:GRAPH_RETRIEVAL_ENABLED = "true"
   $env:GRAPH_ROUTER_ENABLED = "true"
   $env:GRAPH_STORE_PROVIDER = "postgres"
   docker compose `
     -f docker-compose.yml `
     -f docker-compose.selfdocs.override.yml `
     up -d --build
   ```

   If your local setup uses a demo compose profile, enable that profile before
   starting the stack.

3. Load the LLM paper seed corpus.

   ```powershell
   docker compose `
     -f docker-compose.yml `
     -f docker-compose.selfdocs.override.yml `
     run --rm seed
   ```

4. Ingest the self-doc corpus from the committed manifest.

   The plain compose backend image does not contain the repository root, so the
   override above is required before using `/workspace` here.

   ```powershell
   docker compose `
     -f docker-compose.yml `
     -f docker-compose.selfdocs.override.yml `
     exec -T backend python -m app.scripts.ingest_demo_corpus `
     --repo-root /workspace `
     --manifest docs/demo/corpus_manifest.json `
     --base-url http://127.0.0.1:8000
   ```

5. Build graph indexes for active ready document versions.

   ```powershell
   docker compose `
     -f docker-compose.yml `
     -f docker-compose.selfdocs.override.yml `
     exec -T backend python -m app.scripts.queue_graph_index_builds --dry-run
   docker compose `
     -f docker-compose.yml `
     -f docker-compose.selfdocs.override.yml `
     exec -T backend python -m app.scripts.queue_graph_index_builds
   ```

   Wait for `graph_index_build` jobs to finish in the worker/admin job view. For
   a one-shot local demo that also runs Neo4j projection, use the
   existing `scripts\neo4j_demo.ps1` runbook instead.

6. Run the strategy comparison against this fixture.

   ```powershell
   .\scripts\run_retrieval_eval_smoke.ps1 `
     -Dataset phase3_corpus_multi_hop `
     -Strategies dense,hybrid,graph_postgres `
     -CaseLimit 14 `
     -ThresholdMode warn
   ```

   Optional provider comparison:

   ```powershell
   .\scripts\run_retrieval_eval_smoke.ps1 `
     -Dataset phase3_corpus_multi_hop `
     -Strategies graph_postgres,graph_neo4j `
     -CaseLimit 14 `
     -ThresholdMode warn
   ```

7. Review results in the admin UI.

   - Open `/admin/evaluations`.
   - Open the completed `phase3_corpus_multi_hop` run created by the smoke
     command above.
   - Use the run detail strategy comparison table to compare `dense`, `hybrid`,
     and `graph_postgres` inside that single run.
   - Compare `faithfulness`, `context_precision`, `citation_coverage`,
     `graph_path_relevance`, `graph_citation_coverage`, and
     `multi_hop_answerability`.

For the separate two-run comparison page, create separate single-strategy runs
instead of using the multi-strategy smoke command:

   ```powershell
   .\scripts\run_retrieval_eval_smoke.ps1 `
     -Dataset phase3_corpus_multi_hop `
     -Strategies dense `
     -CaseLimit 14 `
     -ThresholdMode warn
   .\scripts\run_retrieval_eval_smoke.ps1 `
     -Dataset phase3_corpus_multi_hop `
     -Strategies graph_postgres `
     -CaseLimit 14 `
     -ThresholdMode warn
   ```

Direction A for this dataset is dense or hybrid as base and `graph_postgres` as
candidate. A healthy demo should show graph gains most clearly on the self-doc
cases where entity and relation extraction matches implementation structure. If
graph indexing is missing, unavailable, or unprojected, graph runs may show safe
no-context or fallback reason codes instead of quality gains.

## Safety Notes

- Do not commit uploaded files, extracted text dumps, chunks, Qdrant data,
  Neo4j data, database dumps, logs, artifacts, or environment files.
- Do not paste full source passages, generated context, raw graph evidence, or
  private operational values into reports or PR comments.
- Use live `retrieval_run_items`, citations, graph path refs, hashes, counts,
  provider labels, and aggregate scores for evidence.
- Use the live database to map document and chunk IDs after ingest. Keep fixture
  ID arrays empty.

## Next Handoff

This C2 dataset update records the LLM-observed paper relation behavior without
making CI depend on a live LLM, full corpus indexing, or a specific local DB
state. Future scoring changes should keep the same redaction boundary: safe
labels, relation types, hashes, IDs, counts, and aggregate metrics only.
