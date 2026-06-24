# Corpus-Grounded Multi-Hop Gold Dataset

This document describes `phase3_corpus_multi_hop`, a fixture-only evaluation
dataset for showing where GraphRAG should beat single-hop dense retrieval. It is
grounded in the committed demo corpora only:

- LLM paper seed corpus: `backend/app/seed_data/llm_paper_corpus.md`
- LLM corpus guide: `docs/demo/llm_paper_corpus.md`
- RAGProject self-doc manifest: `docs/demo/corpus_manifest.json`
- RAGProject GraphRAG and evaluation docs under `docs/phase3/`
- supporting Phase2 evaluation docs and `docs/DDL.md`

`data/demo/llm-research/*.md` is not present in this `origin/main` checkout. The
paper demo content currently lives in the seed corpus above and is loaded as the
`LLM Paper Corpus for RAG Demo` logical document.

## Scope

`phase3_corpus_multi_hop` adds only fixture data. It does not add metrics, API
fields, database migrations, retrieval logic, frontend behavior, or external
corpus text. `expected_document_ids` and `expected_chunk_ids` are intentionally
empty because ingest-time IDs are local database state. Use the runbook below to
create the live IDs for a local comparison.

The fixture has 14 active cases:

| Domain | Count | Tags |
|---|---:|---|
| LLM paper corpus | 8 | `paper`, `multi_hop` |
| RAGProject self docs | 6 | `system_docs`, `multi_hop` |

Every case includes:

- `required_citation=true`
- `metadata_json.expected_strategy=graph`
- `metadata_json.acceptable_strategies`
- `metadata_json.expected_entity_labels`
- `metadata_json.expected_relation_types`
- `metadata_json.required_hop_count`

## Design Intent

The cases are written so a single lexical or vector-nearest chunk is not enough
to collect every gold signal. A good GraphRAG run should follow shared entities,
themes, relation labels, or source-chunk-backed paths across at least two
documents or chunks.

| Case | Why it is multi-hop | Link axis | Dense miss pattern |
|---|---|---|---|
| `paper_cot_tot_react_reasoning_actions` | Requires CoT, ToT, and ReAct together. | reasoning method -> search -> action loop | One paper chunk can answer only part of the progression. |
| `paper_decomposition_sampling_reasoning` | Connects decomposition and voting. | reasoning decomposition -> diverse chains -> answer aggregation | Query terms may rank either Least-to-Most or Self-Consistency, not both. |
| `paper_tool_use_api_chain` | Needs Toolformer, Gorilla, and ToolBench. | tool use -> API grounding -> evaluation chains | API terms alone can overfocus on Gorilla. |
| `paper_agent_eval_interfaces` | Connects agent benchmarks and SWE-agent interface design. | interactive environments -> software interface | Software issue terms can miss WebArena and AgentBench. |
| `paper_rag_reliability_structure` | Joins Self-RAG, CRAG, and GraphRAG. | evidence need -> correction -> relational structure | RAG keyword overlap can retrieve one family member without the bridge. |
| `paper_global_multiscale_retrieval` | Contrasts RAPTOR summaries with GraphRAG relational summaries. | broad context -> tree/community summaries | Summary terms can miss the graph relation requirement. |
| `paper_prompt_adaptation_data` | Connects GPT-3 prompting, Many-Shot ICL, and Self-Instruct. | adaptation interface -> long context -> generated instruction data | Prompting terms may rank GPT-3 and miss synthetic data. |
| `paper_program_tool_reasoning` | Links executable reasoning, Python execution, and ReAct. | program execution -> tool use -> grounded acting | Code terms can miss action/observation loops. |
| `system_graph_source_truth_neo4j` | Needs architecture and Neo4j docs. | PostgreSQL source of truth -> optional read model -> source chunk IDs | Provider terms alone can miss the source-of-truth boundary. |
| `system_graph_retrieval_citation_bridge` | Needs retrieval strategy and citation validation docs. | graph path -> retrieval run item -> citation | Citation terms can miss graph path resolution. |
| `system_graph_index_worker_counts` | Needs graph indexing and redaction policy. | graph job -> count summary -> safe error handling | Worker terms can miss reporting safety. |
| `system_graph_evaluation_metadata` | Needs Phase2 strategy metadata plus Phase3 graph metrics. | expected strategy -> graph labels/types/hops | Metric terms can retrieve only one design doc. |
| `system_cache_provider_fingerprint` | Needs cache and provider fallback docs. | graph fingerprint -> provider key -> fallback reason | Cache terms can miss provider behavior. |
| `system_corpus_runbook_manifest` | Needs corpus manifest, README, and Neo4j runbook. | manifest -> self-doc corpus -> provider comparison | Runbook terms can miss the manifest source. |

The expected answers are short summaries. They do not copy full source passages
or generated context. The keyword list is made of short terms that exist in the
committed corpus and can be matched with case-insensitive substring checks.

## Reproduction Runbook

These steps use only repository-owned demo material and existing scripts.

1. Start the local stack.

   ```powershell
   $env:GRAPH_RETRIEVAL_ENABLED = "true"
   $env:GRAPH_ROUTER_ENABLED = "true"
   $env:GRAPH_STORE_PROVIDER = "postgres"
   docker compose up -d --build
   ```

2. Load the LLM paper seed corpus.

   ```powershell
   docker compose run --rm seed
   ```

3. Ingest the self-doc corpus from the committed manifest.

   ```powershell
   docker compose exec -T backend python -m app.scripts.ingest_demo_corpus `
     --repo-root /workspace `
     --manifest docs/demo/corpus_manifest.json `
     --base-url http://127.0.0.1:8000
   ```

4. Build graph indexes for active ready document versions.

   ```powershell
   docker compose exec -T backend python -m app.scripts.queue_graph_index_builds --dry-run
   docker compose exec -T backend python -m app.scripts.queue_graph_index_builds
   ```

   Wait for `graph_index_build` jobs to finish in the worker/admin job view. For
   a one-shot local demo that also runs optional Neo4j projection, use the
   existing `scripts\neo4j_demo.ps1` runbook instead.

5. Run the strategy comparison against this fixture.

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

6. Review results in the admin UI.

   - Open `/admin/evaluations`.
   - Select two completed runs, for example dense as base and graph as
     candidate.
   - Open the comparison page, or navigate directly to
     `/admin/evaluations/compare?base=<dense_run_id>&candidate=<graph_run_id>`.
   - Compare `faithfulness`, `context_precision`, `citation_coverage`,
     `graph_path_relevance`, `graph_citation_coverage`, and
     `multi_hop_answerability`.

Direction A for this dataset is dense or hybrid as base and `graph_postgres` as
candidate. A healthy demo should show graph gains on cases where the gold
signals span multiple paper entries or multiple self-docs. If graph indexing is
missing, unavailable, or unprojected, graph runs may show safe no-context or
fallback reason codes instead of quality gains.

## Safety Notes

- Do not commit uploaded files, extracted text dumps, chunks, Qdrant data,
  Neo4j data, database dumps, logs, artifacts, or environment files.
- Do not paste full source passages, generated context, raw graph evidence, or
  private operational values into reports or PR comments.
- Use live `retrieval_run_items`, citations, graph path refs, hashes, counts,
  provider labels, and aggregate scores for evidence.
- Use the live database to map document and chunk IDs after ingest. Keep fixture
  ID arrays empty.

## Next PR

C2 can add graph-specific or multi-hop scoring improvements. This C1 dataset
only supplies corpus-grounded gold cases and validation.
